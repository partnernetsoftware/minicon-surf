#!/usr/bin/env python3
"""Headless, read-only attribution of the control plane's per-request churn.

After the frame region and the snapshot attribution, what the surface
court's post-hide gate still sees is mostly the host growing a little on
every control request, with or without a surface. This court measures
that growth per operation, from outside (proc_pid_rusage footprint, no
observer effect) and from inside (court-only request stages, an observer
effect this court also measures), on a fresh host per run, one warm-up
plus seven runs, default allocator and arena. Nothing is fixed here.

Strictly headless: no --visual, no MINICON_SURF_ALLOW_VISIBLE_COURT; the
one arm that exercises surface.show/hide uses the surface binary's
no-AppKit drain child (a separate process that maps no AppKit), and the
court checks before and after that no surface child and no window owned
by it exist.

Arms (one operation repeated on one live target; the same request every
time): memory.report (the self-measuring request: its own perturbation is
an arm, not a probe), target.inspect (revision read: request parse,
profile working-copy sync and commit, response), target.snapshot (the
shared snapshot path plus the largest response), profile.list (not
target-scoped: no working-copy sync or commit), surface.show+hide with
the drain child (the gate's own pair), and, when the CDP client modules
are present, a CDP Page.getFrameTree over an attached session (the CDP
message tables). Each run issues 128 requests; the footprint over
headless is sampled from outside after each of the first seven and then
after every eighth request, so the seven-request slope and the
128-request plateau are both read. With stages on, the host samples
itself at request_read, request_parsed, after_sync_io, after_dispatch,
after_commit_io, after_execute, response_serialized, request_dropped,
response_written and response_dropped; the stages-off cell of the same
arm gives the observer effect (the difference of the outside readings).

Reported per arm: footprint over headless after requests 1..7 and at
8,16,...,128 (medians of seven runs), the slope over the first seven and
over 8..128, the first request after which the outside footprint stopped
returning to its pre-request value, the per-stage deltas (stages on),
the observer effect, response bytes, and memory.report's in-use, arena
and owner figures taken once at the end.
"""

import argparse
import ctypes
import hashlib
import importlib.util
import json
import os
import signal
import statistics
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "protocol"))
import check_contract  # noqa: E402


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    saved = sys.argv
    sys.argv = [name]
    try:
        spec.loader.exec_module(module)
    finally:
        sys.argv = saved
    return module


HEADLESS = load_module("surface_headless_court", Path(__file__).with_name("surface-headless-court.py"))
CDP = load_module("cdp_frame_tree_court", Path(__file__).with_name("cdp-frame-tree-court.py"))
PROFILE = load_module("profile_court", Path(__file__).with_name("profile-court.py"))
RETENTION = PROFILE.RETENTION
NETWORK = PROFILE.NETWORK
FIXTURE_ROOT = PROFILE.FIXTURE_ROOT
VISIBLE_ENV = "MINICON_SURF_ALLOW_VISIBLE_COURT"
OWNER = "native-dom-surface"
CALLS = 128
FIRST = 7
STAGES = ["request_read", "request_parsed", "after_sync_io", "after_dispatch", "after_commit_io", "after_execute", "response_serialized",
          "request_dropped", "response_written", "response_dropped"]
KEYS = ("footprint", "rss", "in_use", "allocated", "threads")
ARMS = ["memory.report", "target.inspect", "target.snapshot", "profile.list", "surface.pair", "cdp.frame_tree"]


def footprint(pid):
    info = RETENTION.RusageInfoV4()
    if RETENTION._LIBPROC.proc_pid_rusage(pid, 4, ctypes.byref(info)) != 0:
        raise RuntimeError("proc_pid_rusage failed")
    return int(info.ri_phys_footprint)


class Host:
    def __init__(self, binary, directory, allocator, surface_binary, court_file, stages, cdp):
        environment = dict(os.environ)
        for knob in ("MINICON_SURF_NATIVE_REALM_ZONE", "MINICON_SURF_NATIVE_REALM_ARENA", "MINICON_SURF_PROFILE_STORE", VISIBLE_ENV,
                     "http_proxy", "https_proxy", "all_proxy"):
            environment.pop(knob, None)
        if allocator == "arena":
            environment["MINICON_SURF_NATIVE_REALM_ARENA"] = "1"
        self.ready = Path(directory) / "ready.json"
        command = [binary, "serve", "--stdio", "--fixture-root", str(FIXTURE_ROOT), "--config-dir", str(Path(directory) / "config"),
                   "--surface-binary", str(surface_binary), "--surface-child-mode", "drain", "--surface-court-file", str(court_file)]
        if stages:
            command += ["--surface-court-stages", "1"]
        if cdp:
            command += ["--cdp-port", "0", "--ready-file", str(self.ready)]
        self.process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, env=environment)
        self.counter = 0
        self.response_bytes = 0

    def call(self, operation, arguments, deadline_ms=15000):
        self.counter += 1
        request = {"protocol": "minicon-surf.control", "version": "0.0.1", "request_id": f"req_churn_{self.counter}",
                   "deadline_ms": deadline_ms, "operation": operation, "arguments": arguments}
        check_contract.validate_request(request)
        self.process.stdin.write(json.dumps(request) + "\n")
        self.process.stdin.flush()
        line = self.process.stdout.readline()
        if not line:
            raise RuntimeError(f"host exited during {operation}")
        self.response_bytes = len(line.encode())
        response = json.loads(line)
        check_contract.validate_response(response)
        return response

    def ok(self, operation, arguments, deadline_ms=15000):
        response = self.call(operation, arguments, deadline_ms)
        if not response["ok"]:
            raise RuntimeError(f"{operation} failed: {response['error']}")
        return response["result"]

    def endpoint(self):
        deadline = time.time() + 10
        while time.time() < deadline and not self.ready.exists():
            time.sleep(0.01)
        return json.loads(self.ready.read_text())

    def finish(self):
        self.process.stdin.close()
        return self.process.wait(timeout=30)


def read_stages(court_file):
    stages = []
    for line in Path(court_file).read_text().splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if event.get("event") == "stage" and event.get("stage") in STAGES:
            stages.append(event)
    return stages


def one_request(host, arm, target, client):
    if arm == "memory.report":
        host.ok("memory.report", {})
    elif arm == "target.inspect":
        host.ok("target.inspect", {"target": target})
    elif arm == "target.snapshot":
        host.ok("target.snapshot", {"target": target, "format": "semantic", "max_bytes": 65536, "max_nodes": 64})
    elif arm == "profile.list":
        host.ok("profile.list", {})
    elif arm == "surface.pair":
        shown = host.ok("surface.show", {"target": target})
        host.ok("surface.hide", {"surface": shown["surface"]})
    elif arm == "cdp.frame_tree":
        client.send("A", "Page.getFrameTree")


def run_once(binary, surface_binary, allocator, arm, stages, client_modules):
    cdp = arm == "cdp.frame_tree"
    with tempfile.TemporaryDirectory(prefix="minicon-surf-control-churn-") as directory:
        court_file = Path(directory) / "court-only.ndjson"
        host = Host(binary, directory, allocator, surface_binary, court_file, stages, cdp)
        client = None
        try:
            profile = host.ok("profile.create", {"persistence": "ephemeral"})["profile"]
            session = host.ok("session.open", {"profile": profile})["session"]
            target = host.ok("target.open", {"session": session, "fixture": "semantic-interactive.html"})["target"]
            host.ok("target.inspect", {"target": target})
            if cdp:
                client = CDP.Client(client_modules)
                client.command("connect", endpoint=host.endpoint()["browser_websocket_url"])
                client.command("waitForTarget", id=target)
                client.command("attach", name="A", id=target)
                client.send("A", "Page.getFrameTree")
            time.sleep(0.05)
            headless = footprint(host.process.pid)
            samples = []
            response_bytes = []
            for index in range(1, CALLS + 1):
                one_request(host, arm, target, client)
                response_bytes.append(host.response_bytes)
                if index <= FIRST or index % 8 == 0:
                    time.sleep(0.02)
                    samples.append({"call": index, "over_headless": footprint(host.process.pid) - headless})
            time.sleep(0.05)
            final_outside = footprint(host.process.pid) - headless
            report = host.ok("memory.report", {})
            stage_events = read_stages(court_file) if stages else []
        finally:
            if client is not None:
                try:
                    client.command("disconnect")
                except Exception:  # noqa: BLE001
                    pass
                client.process.wait(timeout=10)
            exit_code = host.finish() if host.process.poll() is None else host.process.returncode
            if host.process.poll() is None:
                host.process.kill()
                host.process.wait()
    # Per-request stage rounds: split on request_read for the arm's operation.
    rounds = []
    current = None
    for event in stage_events:
        if event["stage"] == "request_read":
            current = {}
            rounds.append(current)
        if current is not None:
            current[event["stage"]] = {k: event.get(k) for k in KEYS}
            if event.get("operation"):
                current["operation"] = event["operation"]
    wanted = {"memory.report": {"memory.report"}, "target.inspect": {"target.inspect"}, "target.snapshot": {"target.snapshot"},
              "profile.list": {"profile.list"}, "surface.pair": {"surface.show", "surface.hide"}, "cdp.frame_tree": set()}[arm]
    rounds = [r for r in rounds if r.get("operation") in wanted]
    return {"headless": headless, "samples": samples, "final_outside": final_outside, "response_bytes": response_bytes,
            "in_use": report["libmalloc"]["size_in_use"], "allocated": report["libmalloc"]["size_allocated"],
            "realm_malloc": report["owners"]["script_realms"]["malloc_bytes"], "arenas": report["owners"]["script_realms"].get("dedicated_arenas"),
            "stage_rounds": rounds, "exit_code": exit_code}


def median(values):
    values = [v for v in values if v is not None]
    return int(statistics.median(values)) if values else None


def summarize(values):
    values = [v for v in values if v is not None]
    return {"median": int(statistics.median(values)), "minimum": min(values), "maximum": max(values)} if values else None


def first_non_returning(samples_runs):
    """The first sampled request after which the footprint over headless never
    fell back to 0 in the majority of runs."""
    votes = {}
    for samples in samples_runs:
        chosen = None
        for index, sample in enumerate(samples):
            if sample["over_headless"] > 0 and all(s["over_headless"] > 0 for s in samples[index:]):
                chosen = sample["call"]
                break
        votes[chosen] = votes.get(chosen, 0) + 1
    return {str(k): v for k, v in votes.items()}


def summarize_arm(runs):
    calls = [s["call"] for s in runs[0]["samples"]]
    over = [{"call": c, "over_headless": summarize([r["samples"][i]["over_headless"] for r in runs])} for i, c in enumerate(calls)]
    by_call = {c: median([r["samples"][i]["over_headless"] for r in runs]) for i, c in enumerate(calls)}
    out = {"exit_codes": sorted({r["exit_code"] for r in runs}), "headless": summarize([r["headless"] for r in runs]),
           "over_headless_by_call": over, "final_outside_over_headless": summarize([r["final_outside"] for r in runs]),
           "slope_first_seven": by_call.get(7, 0) - by_call.get(1, 0), "slope_8_to_128": by_call.get(128, 0) - by_call.get(8, 0),
           "per_request_first_seven": (by_call.get(7, 0) - by_call.get(1, 0)) / 6.0, "per_request_8_to_128": (by_call.get(128, 0) - by_call.get(8, 0)) / 120.0,
           "first_non_returning_call": first_non_returning([r["samples"] for r in runs]),
           "response_bytes": summarize([b for r in runs for b in r["response_bytes"]]),
           "end_in_use": summarize([r["in_use"] for r in runs]), "end_allocated": summarize([r["allocated"] for r in runs]),
           "end_realm_malloc": summarize([r["realm_malloc"] for r in runs])}
    stage_runs = [r["stage_rounds"] for r in runs if r["stage_rounds"]]
    if stage_runs:
        by_operation = {}
        for operation in sorted({rd["operation"] for sr in stage_runs for rd in sr}):
            rounds = [rd for sr in stage_runs for rd in sr if rd["operation"] == operation]
            present = [s for s in STAGES if all(s in rd for rd in rounds)]
            deltas = {}
            for a, b in zip(present, present[1:]):
                pairs = [(rd[a], rd[b]) for rd in rounds if rd[a]["footprint"] is not None and rd[b]["footprint"] is not None]
                deltas[f"{a}->{b}"] = {"footprint_median": median([y["footprint"] - x["footprint"] for x, y in pairs]),
                                       "in_use_median": median([y["in_use"] - x["in_use"] for x, y in pairs]),
                                       # Sums over every request of every run: where the rare page growth is born.
                                       "footprint_sum": sum(y["footprint"] - x["footprint"] for x, y in pairs),
                                       "footprint_grew_requests": sum(1 for x, y in pairs if y["footprint"] > x["footprint"]),
                                       "footprint_fell_requests": sum(1 for x, y in pairs if y["footprint"] < x["footprint"]),
                                       "in_use_sum": sum(y["in_use"] - x["in_use"] for x, y in pairs)}
            # Growth per request that stays: the mean footprint delta from
            # request_read to response_dropped over the run (medians hide a
            # growth of one page every few requests).
            growth = [rd["response_dropped"]["footprint"] - rd["request_read"]["footprint"] for rd in rounds if "response_dropped" in rd and "request_read" in rd]
            by_operation[operation] = {"rounds": len(rounds), "stage_deltas_median": deltas,
                                       "footprint_growth_per_request_mean": (sum(growth) / len(growth)) if growth else None,
                                       "requests_with_growth": sum(1 for g in growth if g > 0), "requests_with_release": sum(1 for g in growth if g < 0)}
        out["stages_by_operation"] = by_operation
    return out


def surface_hygiene():
    return {"owner_windows": HEADLESS.surface_windows(), "surface_processes": len(HEADLESS.processes(OWNER)),
            "host_processes": len(HEADLESS.processes("native-dom-control"))}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--binary", required=True)
    parser.add_argument("--surface-binary", required=True)
    parser.add_argument("--receipt", required=True)
    parser.add_argument("--client-modules", default=str(ROOT / "target" / "labs" / "d4"))
    parser.add_argument("--repetitions", type=int, default=7)
    parser.add_argument("--warmup", type=int, default=1)
    parser.add_argument("--arms", default="")
    args = parser.parse_args()
    if VISIBLE_ENV in os.environ:
        print(json.dumps({"passed": None, "unverified": f"{VISIBLE_ENV} is set; this court is headless-only and refuses to run with it"}))
        return 3
    arms = [a for a in ARMS if not args.arms or a in args.arms.split(",")]
    if "cdp.frame_tree" in arms and not (Path(args.client_modules) / "node_modules").exists():
        arms.remove("cdp.frame_tree")
        cdp_note = "cdp.frame_tree skipped: client modules absent (unverified)"
    else:
        cdp_note = None
    before = surface_hygiene()
    live = []

    def handler(signum, _frame):
        for host in live:
            try:
                if host.process.poll() is None:
                    host.process.kill()
                    host.process.wait(timeout=5)
            except Exception:  # noqa: BLE001
                pass
        sys.exit(128 + signum)
    signal.signal(signal.SIGINT, handler)
    signal.signal(signal.SIGTERM, handler)
    results = {}
    for allocator in ("system", "arena"):
        results[allocator] = {}
        for arm in arms:
            for stages in (False, True):
                label = f"{arm}{'+stages' if stages else ''}"
                runs = []
                for repetition in range(args.warmup + args.repetitions):
                    run = run_once(args.binary, args.surface_binary, allocator, arm, stages, args.client_modules)
                    if repetition >= args.warmup:
                        runs.append(run)
                results[allocator][label] = summarize_arm(runs)
                s = results[allocator][label]
                print(allocator, label, "by_call", [o["over_headless"]["median"] for o in s["over_headless_by_call"]][:7], "…128:", s["over_headless_by_call"][-1]["over_headless"]["median"],
                      "slope7", s["slope_first_seven"], "per_req_8_128", round(s["per_request_8_to_128"]), "first_non_returning", s["first_non_returning_call"], "resp_bytes", s["response_bytes"]["median"])
        for arm in arms:
            on, off = results[allocator].get(f"{arm}+stages"), results[allocator].get(arm)
            if on and off:
                on["observer_effect_at_128"] = on["over_headless_by_call"][-1]["over_headless"]["median"] - off["over_headless_by_call"][-1]["over_headless"]["median"]
    after = surface_hygiene()
    receipt = {
        "schema": "minicon-surf/native-dom-control-churn/0.0.1",
        "purpose": "headless read-only attribution of the control plane's per-request churn; nothing fixed, no cap moves",
        "technology": "native-dom host with court-only request stages; drain surface child (no AppKit) for the surface arm; external proc_pid_rusage footprint",
        "host_sha256": hashlib.sha256(Path(args.binary).read_bytes()).hexdigest(),
        "surface_sha256": hashlib.sha256(Path(args.surface_binary).read_bytes()).hexdigest(),
        "calls": CALLS, "first": FIRST, "repetitions": args.repetitions, "warmup": args.warmup, "stages": STAGES, "arms": arms,
        "hygiene": {"before": before, "after": after, "headless": before["owner_windows"] == 0 and after["owner_windows"] == 0 and after["surface_processes"] == 0},
        "results": results,
        "notes": [n for n in [cdp_note] if n],
        "limitations": ["outside samples are proc_pid_rusage physical footprint with a 20 ms settle; in-process stage samples exist only in the +stages cells and perturb the host (the observer effect is reported as the difference of the outside readings at request 128)",
                        "memory.report at the end of a run is one more request; in-use, allocated and realm malloc are read once there, never between samples",
                        "the surface arm uses the drain child of the surface binary: a separate process that maps no AppKit; no window exists at any time",
                        "one fixture page, one operation per arm, one host per run; macOS only; no pid, path or command line is recorded"],
    }
    Path(args.receipt).write_text(json.dumps(receipt, indent=1, sort_keys=True) + "\n")
    print("hygiene", json.dumps(receipt["hygiene"]))
    print("receipt", args.receipt)
    return 0 if receipt["hygiene"]["headless"] else 1


if __name__ == "__main__":
    sys.exit(main())
