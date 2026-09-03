#!/usr/bin/env python3
"""Reconcile the Rust host's attributable process metrics with the shared
process-tree sampler (X9 micro-experiment ME3, macOS arm64).

At each stage (empty, one target, eight targets, post-close) the court takes an
independent sample of the host's process tree (`ps` pid/ppid/rss plus
`proc_pid_rusage` physical footprint, the same sampler the shared retention
court uses), asks the host for `memory.report`, and samples again. The host's
report must name exactly the pids the sampler saw, and every per-process
value must fall inside the bracket of the two independent samples widened by
a tolerance fixed before the run. A missing child, an incomplete report, or a
value outside the bracket is a recorded failure, never a pass. The report is
diagnostics only: the court never asks the host to change anything to make
the numbers agree, and the host never terminates a child while reporting.
"""

import argparse
import hashlib
import importlib.util
import json
import os
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "protocol"))
import check_contract  # noqa: E402

FIXTURE_ROOT = ROOT / "labs" / "court" / "fixtures"
STAGES = ("empty", "one_target", "eight_targets", "post_close")
TARGETS = 8
# Tolerances fixed before the run: a value must lie inside the bracket of the
# two independent samples widened by the larger of these.
TOLERANCE_BYTES = 1_048_576
TOLERANCE_RATIO = 0.05
FORBIDDEN_KEYS = {"cmdline", "command", "argv", "env", "environment", "path", "executable", "cwd"}


def load_sampler():
    spec = importlib.util.spec_from_file_location(
        "retention_court", ROOT / "labs" / "court" / "cdp-target-retention-macos-arm64.py")
    module = importlib.util.module_from_spec(spec)
    saved = sys.argv
    sys.argv = ["retention-court"]
    try:
        spec.loader.exec_module(module)
    finally:
        sys.argv = saved
    return module


class Host:
    def __init__(self, binary, engine, directory):
        environment = dict(os.environ, MINICON_SURF_LIGHTPANDA=engine)
        self.process = subprocess.Popen(
            [binary, "serve", "--stdio", "--fixture-root", str(FIXTURE_ROOT),
             "--config-dir", str(Path(directory) / "config")],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, env=environment)
        self.counter = 0

    def call(self, operation, arguments, deadline_ms=30000):
        self.counter += 1
        request = {"protocol": "minicon-surf.control", "version": "0.0.1",
                   "request_id": f"req_pm_{self.counter}", "deadline_ms": deadline_ms,
                   "operation": operation, "arguments": arguments}
        check_contract.validate_request(request)
        self.process.stdin.write(json.dumps(request) + "\n")
        self.process.stdin.flush()
        line = self.process.stdout.readline()
        if not line:
            raise RuntimeError(f"host exited during {operation}")
        response = json.loads(line)
        check_contract.validate_response(response)
        if not response["ok"]:
            raise RuntimeError(f"{operation} failed: {response['error']['code']}")
        return response["result"]

    def finish(self):
        self.process.stdin.close()
        return self.process.wait(timeout=30)


def independent_sample(sampler, root_pid):
    """One pass of the shared sampler: {pid: (rss_bytes, footprint_bytes)}."""
    tree = sampler.process_tree(root_pid)
    return {pid: (rss_kib * 1024, sampler.physical_footprint(pid)) for pid, rss_kib in tree}


def within(value, before, after):
    low, high = min(before, after), max(before, after)
    slack = max(TOLERANCE_BYTES, int(TOLERANCE_RATIO * high))
    return low - slack <= value <= high + slack


def strings_of(value):
    if isinstance(value, dict):
        for key, child in value.items():
            yield key
            yield from strings_of(child)
    elif isinstance(value, list):
        for child in value:
            yield from strings_of(child)
    elif isinstance(value, str):
        yield value


def reconcile(stage, report, before, after, expected_children):
    """Typed findings for one stage; an empty list is agreement."""
    findings = []
    host_pid = report["host"]["pid"]
    reported = {host_pid: report["host"]["metrics"]}
    for child in report["children"]:
        if child["metrics"] is not None:
            reported[child["pid"]] = child["metrics"]
    for descendant in report["unattributed_descendants"]:
        if descendant["metrics"] is not None:
            reported[descendant["pid"]] = descendant["metrics"]
    for name, sample in (("before", before), ("after", after)):
        if set(sample) != set(reported):
            findings.append({"reason": "pid_set_differs", "sample": name,
                             "only_in_sampler": sorted(set(sample) - set(reported)),
                             "only_in_report": sorted(set(reported) - set(sample))})
    if not report["tree"]["complete"]:
        findings.append({"reason": "report_incomplete", "incomplete": report["tree"]["incomplete"]})
    if len(report["children"]) != expected_children:
        findings.append({"reason": "child_count_differs", "expected": expected_children,
                         "reported": len(report["children"])})
    for child in report["children"]:
        if child["state"] != "running" or not child["identity_verified"]:
            findings.append({"reason": "child_state_not_running", "child": child["child"], "state": child["state"]})
    if report["unattributed_descendants"]:
        findings.append({"reason": "unattributed_descendants",
                         "count": len(report["unattributed_descendants"])})
    for pid, metrics in reported.items():
        if pid in before and pid in after:
            if not within(metrics["resident_bytes"], before[pid][0], after[pid][0]):
                findings.append({"reason": "resident_outside_bracket", "pid_role": "host" if pid == host_pid else "child",
                                 "reported": metrics["resident_bytes"], "before": before[pid][0], "after": after[pid][0]})
            if not within(metrics["physical_footprint_bytes"], before[pid][1], after[pid][1]):
                findings.append({"reason": "footprint_outside_bracket", "pid_role": "host" if pid == host_pid else "child",
                                 "reported": metrics["physical_footprint_bytes"], "before": before[pid][1], "after": after[pid][1]})
    summed_before = sum(v[0] for v in before.values())
    summed_after = sum(v[0] for v in after.values())
    if not within(report["tree"]["summed_resident_bytes"], summed_before, summed_after):
        findings.append({"reason": "summed_resident_outside_bracket"})
    footprint_before = sum(v[1] for v in before.values())
    footprint_after = sum(v[1] for v in after.values())
    if not within(report["tree"]["summed_physical_footprint_bytes"], footprint_before, footprint_after):
        findings.append({"reason": "summed_footprint_outside_bracket"})
    if report["private_bytes"]["available"] is not False:
        findings.append({"reason": "private_bytes_claimed"})
    leaked = [s for s in strings_of(report) if "/" in s or "=" in s]
    forbidden = [k for k in strings_of(report) if k in FORBIDDEN_KEYS]
    if leaked or forbidden:
        findings.append({"reason": "identity_leak", "strings": leaked[:4], "keys": forbidden})
    return findings


def run_once(binary, engine, sampler, settle_ms):
    with tempfile.TemporaryDirectory(prefix="minicon-surf-process-metrics-") as directory:
        host = Host(binary, engine, directory)
        stages = {}
        try:
            profile = host.call("profile.create", {"persistence": "ephemeral"})["profile"]
            session = host.call("session.open", {"profile": profile})["session"]
            targets = []

            def stage(name, expected_children):
                time.sleep(settle_ms / 1000.0)
                before = independent_sample(sampler, host.process.pid)
                report = host.call("memory.report", {})
                after = independent_sample(sampler, host.process.pid)
                findings = reconcile(name, report, before, after, expected_children)
                stages[name] = {
                    "generation": report["generation"],
                    "children": [{"child": c["child"], "target": c["target"], "state": c["state"],
                                  "spawned_generation": c["spawned_generation"]} for c in report["children"]],
                    "report": {
                        "processes": report["tree"]["processes"],
                        "summed_resident_bytes": report["tree"]["summed_resident_bytes"],
                        "summed_physical_footprint_bytes": report["tree"]["summed_physical_footprint_bytes"],
                        "host_resident_bytes": report["host"]["metrics"]["resident_bytes"],
                        "host_physical_footprint_bytes": report["host"]["metrics"]["physical_footprint_bytes"],
                        "children_physical_footprint_bytes": sum(c["metrics"]["physical_footprint_bytes"] for c in report["children"] if c["metrics"]),
                        "complete": report["tree"]["complete"],
                    },
                    "sampler": {
                        "before": {"processes": len(before), "summed_resident_bytes": sum(v[0] for v in before.values()),
                                   "summed_physical_footprint_bytes": sum(v[1] for v in before.values())},
                        "after": {"processes": len(after), "summed_resident_bytes": sum(v[0] for v in after.values()),
                                  "summed_physical_footprint_bytes": sum(v[1] for v in after.values())},
                    },
                    "findings": findings,
                    "agrees": not findings,
                }

            stage("empty", 0)
            targets.append(host.call("target.open", {"session": session, "fixture": "semantic-static.html"})["target"])
            stage("one_target", 1)
            first_report_children = stages["one_target"]["children"]
            while len(targets) < TARGETS:
                targets.append(host.call("target.open", {"session": session, "fixture": "semantic-static.html"})["target"])
            stage("eight_targets", TARGETS)
            closed_first = host.call("target.close", {"target": targets[0]})
            for target in targets[1:]:
                host.call("target.close", {"target": target})
            stage("post_close", 0)
            # The closed child must be gone from the report and from the sampler.
            closed_pid = closed_first["child"]["pid"]
            post = stages["post_close"]
            post["closed_child_absent"] = (
                closed_first["child"]["child"] == first_report_children[0]["child"]
                and all(c["target"] != targets[0] for c in post["children"])
                and closed_pid not in independent_sample(sampler, host.process.pid))
            if not post["closed_child_absent"]:
                post["findings"].append({"reason": "closed_child_still_present"})
                post["agrees"] = False
            counters = host.call("memory.report", {})["counters"]
            post["counters"] = counters
            if counters != {"children_spawned_total": TARGETS, "children_reaped_total": TARGETS}:
                post["findings"].append({"reason": "counters_differ", "counters": counters})
                post["agrees"] = False
            host.call("session.close", {"session": session})
            if host.finish() != 0:
                raise RuntimeError("host exited with failure")
            return stages
        finally:
            if host.process.poll() is None:
                host.process.kill()
                host.process.wait()


def summarize(values):
    return {"values": values, "median": int(statistics.median(values)), "minimum": min(values), "maximum": max(values)}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--binary", required=True)
    parser.add_argument("--engine", required=True)
    parser.add_argument("--engine-sha256", required=True)
    parser.add_argument("--repetitions", type=int, default=7)
    parser.add_argument("--settle-ms", type=int, default=500)
    parser.add_argument("--receipt")
    args = parser.parse_args()
    engine_sha = hashlib.sha256(Path(args.engine).read_bytes()).hexdigest()
    if engine_sha != args.engine_sha256:
        raise SystemExit("engine digest differs from the pinned artifact")
    sampler = load_sampler()
    run_once(args.binary, args.engine, sampler, args.settle_ms)  # warm-up
    runs = [run_once(args.binary, args.engine, sampler, args.settle_ms) for _ in range(args.repetitions)]
    stages = {}
    for name in STAGES:
        rows = [run[name] for run in runs]
        stages[name] = {
            "agreements": sum(1 for row in rows if row["agrees"]),
            "findings": [f for row in rows for f in row["findings"]],
            "report_summed_physical_footprint_bytes": summarize([r["report"]["summed_physical_footprint_bytes"] for r in rows]),
            "sampler_before_summed_physical_footprint_bytes": summarize([r["sampler"]["before"]["summed_physical_footprint_bytes"] for r in rows]),
            "report_summed_resident_bytes": summarize([r["report"]["summed_resident_bytes"] for r in rows]),
            "sampler_before_summed_resident_bytes": summarize([r["sampler"]["before"]["summed_resident_bytes"] for r in rows]),
            "report_host_physical_footprint_bytes": summarize([r["report"]["host_physical_footprint_bytes"] for r in rows]),
            "report_children_physical_footprint_bytes": summarize([r["report"]["children_physical_footprint_bytes"] for r in rows]),
            "processes": summarize([r["report"]["processes"] for r in rows]),
        }
    all_agree = all(row["agrees"] for run in runs for row in run.values())
    receipt = {
        "schema": "minicon-surf.lightpanda-process-metrics-receipt/0.0.1",
        "status": "observed" if all_agree else "disagreement-recorded",
        "technology": "lightpanda",
        "technology_version": "0.4.0",
        "host": "labs/lightpanda/host (process-per-target Rust host)",
        "host_sha256": hashlib.sha256(Path(args.binary).read_bytes()).hexdigest(),
        "engine_sha256": engine_sha,
        "platform": {"os": "macos", "architecture": "arm64"},
        "design": {
            "stages": list(STAGES),
            "targets": TARGETS,
            "warmups": 1,
            "measured_repetitions": args.repetitions,
            "settle_ms": args.settle_ms,
            "independent_sampler": "labs/court/cdp-target-retention-macos-arm64.py process_tree (ps pid/ppid/rss) and physical_footprint (proc_pid_rusage RUSAGE_INFO_V4)",
            "bracket": "each host value must lie within [min(before, after) - slack, max(before, after) + slack] where slack = max(1 MiB, 5% of the larger sample)",
            "rules": ["pid sets equal to both samples", "report complete", "child count equals open targets", "every child running with identity verified",
                      "no unattributed descendants", "per-process resident and footprint inside the bracket", "summed values inside the bracket",
                      "private bytes declared unavailable", "no path, command line or environment strings", "closed child absent from report and sampler",
                      "spawn/reap counters equal eight after the closes"],
        },
        "measurement": {
            "semantic": "host memory.report per-process resident (pti_resident_size) and physical footprint (ri_phys_footprint) versus the sampler's ps rss and proc_pid_rusage footprint; summed resident double counts shared pages and is not total memory",
            "stages": stages,
            "runs": runs,
        },
        "passed": all_agree,
        "limitations": [
            "one macOS arm64 machine, one static fixture, eight targets; agreement is within a fixed bracket, not identity, because the samples are taken at different instants",
            "private/shared bytes are unavailable to both the host and the sampler",
            "the report attributes children by the host's spawn; the engine exposes no in-process owner ledger",
            "the report is diagnostics only and grants nothing",
        ],
    }
    encoded = json.dumps(receipt, indent=2, sort_keys=True) + "\n"
    if args.receipt:
        Path(args.receipt).write_text(encoded, encoding="utf-8")
    print(json.dumps({k: receipt[k] for k in ("status", "passed")}))
    for name in STAGES:
        print(name, "agreements", stages[name]["agreements"], "/", args.repetitions,
              "report fp", stages[name]["report_summed_physical_footprint_bytes"]["median"],
              "sampler fp", stages[name]["sampler_before_summed_physical_footprint_bytes"]["median"],
              "findings", len(stages[name]["findings"]))
    return 0 if all_agree else 1


if __name__ == "__main__":
    sys.exit(main())
