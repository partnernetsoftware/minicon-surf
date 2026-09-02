#!/usr/bin/env python3
"""Same-process staged RSS and physical-footprint court for synthetic control."""

import argparse
import hashlib
import json
import platform
import re
import statistics
import subprocess
import tempfile
import time
from pathlib import Path


STAGES = ("empty", "one_live", "one_headed", "one_post_hide", "maximum_headed", "post_release", "post_trim")
PHYS = re.compile(r"^\s*phys_footprint:\s+(\d+) B$", re.MULTILINE)
PHYS_PEAK = re.compile(r"^\s*phys_footprint_peak:\s+(\d+) B$", re.MULTILINE)


class Host:
    def __init__(self, binary, profile_root):
        self.process = subprocess.Popen(
            [binary, "serve", "--stdio", "--profile-root", profile_root],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        self.next_id = 0

    def call(self, operation, arguments, expect_ok=True):
        self.next_id += 1
        request_id = f"req_capacity_court_{self.next_id}"
        request = {"protocol": "minicon-surf.control", "version": "0.0.1",
                   "request_id": request_id, "deadline_ms": 100,
                   "operation": operation, "arguments": arguments}
        self.process.stdin.write(json.dumps(request, separators=(",", ":")) + "\n")
        self.process.stdin.flush()
        line = self.process.stdout.readline()
        if not line:
            raise RuntimeError(f"host exited before response: {self.process.stderr.read()}")
        response = json.loads(line)
        assert response["request_id"] == request_id
        assert response["ok"] is expect_ok, response
        return response.get("result") if expect_ok else response["error"]

    def memory(self):
        return self.call("memory.report", {})

    def finish(self):
        self.process.stdin.close()
        assert self.process.wait(timeout=5) == 0, self.process.stderr.read()


def process_tree(root):
    output = subprocess.run(
        ["ps", "-axo", "pid=,ppid=,rss="], check=True, capture_output=True, text=True
    ).stdout
    rows = {}
    children = {}
    for line in output.splitlines():
        fields = line.split()
        if len(fields) != 3:
            continue
        pid, ppid, rss_kib = map(int, fields)
        rows[pid] = rss_kib
        children.setdefault(ppid, []).append(pid)
    pending = [root]
    seen = set()
    while pending:
        pid = pending.pop()
        if pid in seen:
            continue
        seen.add(pid)
        pending.extend(children.get(pid, ()))
    return [(pid, rows[pid]) for pid in seen if pid in rows]


def footprint(pid):
    output = subprocess.run(
        ["footprint", "-p", str(pid), "-f", "bytes", "--noCategories"],
        check=True, capture_output=True, text=True,
    ).stdout
    current = PHYS.search(output)
    peak = PHYS_PEAK.search(output)
    if not current or not peak:
        raise RuntimeError("footprint output did not contain byte-valued auxiliary data")
    return int(current.group(1)), int(peak.group(1))


def sample_stage(host, samples, settle_ms):
    memory = host.memory()
    time.sleep(settle_ms / 1000)
    rss_values = []
    physical_values = []
    reported_peaks = []
    process_counts = []
    for _ in range(samples):
        tree = process_tree(host.process.pid)
        if not tree:
            raise RuntimeError("host tree disappeared while sampling")
        rss_values.append(sum(rss_kib * 1024 for _, rss_kib in tree))
        physical = [footprint(pid) for pid, _ in tree]
        physical_values.append(sum(item[0] for item in physical))
        reported_peaks.append(sum(item[1] for item in physical))
        process_counts.append(len(tree))
        time.sleep(0.02)
    return {
        "peak_tree_resident_bytes": max(rss_values),
        "peak_tree_physical_footprint_bytes": max(physical_values),
        "reported_process_peak_physical_footprint_bytes": max(reported_peaks),
        "peak_process_count": max(process_counts),
        "logical_accounted_bytes": memory["total_accounted_bytes"],
        "owners": {name: value["objects"] for name, value in memory["owners"].items()},
    }


def create_profile(host, index):
    return host.call("profile.create", {
        "persistence": "ephemeral", "name": f"capacity-{index}",
        "policy": {"network": "online", "permissions": "deny_by_default"},
    })["profile"]


def run_once(binary, samples, settle_ms):
    with tempfile.TemporaryDirectory() as directory:
        host = Host(binary, str(Path(directory) / "profiles"))
        profiles = []
        sessions = []
        targets = []
        surfaces = []
        result = {"empty": sample_stage(host, samples, settle_ms)}

        profiles.append(create_profile(host, 0))
        sessions.append(host.call("session.open", {"profile": profiles[0]})["session"])
        targets.append(host.call("target.open", {"session": sessions[0]})["target"])
        result["one_live"] = sample_stage(host, samples, settle_ms)
        surfaces.append(host.call("surface.show", {"target": targets[0]})["surface"])
        result["one_headed"] = sample_stage(host, samples, settle_ms)
        host.call("surface.hide", {"surface": surfaces.pop()})
        result["one_post_hide"] = sample_stage(host, samples, settle_ms)

        for index in range(1, 8):
            profiles.append(create_profile(host, index))
        while len(sessions) < 16:
            profile = profiles[len(sessions) % len(profiles)]
            sessions.append(host.call("session.open", {"profile": profile})["session"])
        while len(targets) < 32:
            session = sessions[len(targets) % len(sessions)]
            targets.append(host.call("target.open", {"session": session})["target"])
        for profile_index, profile in enumerate(profiles):
            session = sessions[profile_index]
            for kind in ("cookie", "local_storage"):
                for entry in range(32):
                    host.call("profile.storage.put", {
                        "session": session, "kind": kind, "key": f"key-{entry}",
                        "value": chr(97 + profile_index) * 1024,
                    })
        for target in targets[:8]:
            surfaces.append(host.call("surface.show", {"target": target})["surface"])
        overflow = {
            "profile": host.call("profile.create", {"persistence": "ephemeral", "name": "overflow"}, False)["code"],
            "session": host.call("session.open", {"profile": profiles[0]}, False)["code"],
            "target": host.call("target.open", {"session": sessions[0]}, False)["code"],
            "surface": host.call("surface.show", {"target": targets[8]}, False)["code"],
            "storage": host.call("profile.storage.put", {
                "session": sessions[0], "kind": "cookie", "key": "overflow", "value": "value"
            }, False)["code"],
        }
        assert set(overflow.values()) == {"resource_limit"}, overflow
        result["maximum_headed"] = sample_stage(host, samples, settle_ms)

        for surface in surfaces:
            host.call("surface.hide", {"surface": surface})
        for session in sessions:
            host.call("session.close", {"session": session})
        for profile in profiles:
            host.call("profile.delete", {"profile": profile})
        result["post_release"] = sample_stage(host, samples, settle_ms)
        assert all(value == 0 for value in result["post_release"]["owners"].values())
        trim = host.call("memory.trim", {})
        result["post_trim"] = sample_stage(host, samples, settle_ms)
        result["post_trim"]["allocator_trim_strategy"] = trim["strategy"]
        result["post_trim"]["allocator_release_reporting"] = trim["release_reporting"]
        if "released_bytes" in trim:
            result["post_trim"]["allocator_reported_released_bytes"] = trim["released_bytes"]
        host.finish()
        return result, overflow


def aggregate(runs):
    states = {}
    for stage in STAGES:
        rows = [run[stage] for run in runs]
        state = {}
        for key in ("peak_tree_resident_bytes", "peak_tree_physical_footprint_bytes",
                    "reported_process_peak_physical_footprint_bytes", "peak_process_count",
                    "logical_accounted_bytes"):
            values = [row[key] for row in rows]
            state[key] = values
            state[f"median_{key}"] = int(statistics.median(values))
            state[f"maximum_{key}"] = max(values)
        state["owners"] = rows[0]["owners"]
        if stage == "post_trim":
            state["allocator_trim_strategy"] = rows[0]["allocator_trim_strategy"]
            state["allocator_release_reporting"] = rows[0]["allocator_release_reporting"]
            released = [row.get("allocator_reported_released_bytes") for row in rows]
            if all(value is not None for value in released):
                state["allocator_reported_released_bytes"] = released
                state["median_allocator_reported_released_bytes"] = int(statistics.median(released))
        states[stage] = state
    return states


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--binary", required=True)
    parser.add_argument("--repetitions", type=int, default=7)
    parser.add_argument("--samples-per-stage", type=int, default=3)
    parser.add_argument("--settle-ms", type=int, default=100)
    parser.add_argument("--allocator-label", default="system")
    parser.add_argument("--receipt")
    args = parser.parse_args()
    if platform.system() != "Darwin" or platform.machine() != "arm64":
        parser.error("this court requires macOS arm64")
    if min(args.repetitions, args.samples_per_stage, args.settle_ms) <= 0:
        parser.error("repetitions, samples, and settle time must be positive")
    run_once(args.binary, args.samples_per_stage, args.settle_ms)  # warmup
    runs = []
    overflows = []
    for _ in range(args.repetitions):
        run, overflow = run_once(args.binary, args.samples_per_stage, args.settle_ms)
        runs.append(run)
        overflows.append(overflow)
    states = aggregate(runs)
    release_rss = [run["post_release"]["peak_tree_resident_bytes"] - run["empty"]["peak_tree_resident_bytes"] for run in runs]
    release_physical = [run["post_release"]["peak_tree_physical_footprint_bytes"] - run["empty"]["peak_tree_physical_footprint_bytes"] for run in runs]
    trim_rss = [run["post_trim"]["peak_tree_resident_bytes"] - run["empty"]["peak_tree_resident_bytes"] for run in runs]
    trim_physical = [run["post_trim"]["peak_tree_physical_footprint_bytes"] - run["empty"]["peak_tree_physical_footprint_bytes"] for run in runs]
    trim_vs_release_rss = [run["post_trim"]["peak_tree_resident_bytes"] - run["post_release"]["peak_tree_resident_bytes"] for run in runs]
    trim_vs_release_physical = [run["post_trim"]["peak_tree_physical_footprint_bytes"] - run["post_release"]["peak_tree_physical_footprint_bytes"] for run in runs]
    median_trim_physical = int(statistics.median(trim_vs_release_physical))
    trim_effective = median_trim_physical <= -16384
    trim_state = states["post_trim"]
    receipt = {
        "schema": "minicon-surf.synthetic-staged-memory-receipt/0.0.1",
        "status": "incomplete",
        "binary_sha256": hashlib.sha256(Path(args.binary).read_bytes()).hexdigest(),
        "allocator_label": args.allocator_label,
        "platform": {"os": "macos", "architecture": "arm64"},
        "workload": {"stage_order": list(STAGES), "warmups": 1,
                     "measured_repetitions": args.repetitions,
                     "samples_per_stage": args.samples_per_stage,
                     "settle_ms": args.settle_ms,
                     "maximum_state": {"profiles": 8, "sessions": 16, "targets": 32,
                                       "surfaces": 8, "cookie_entries": 256,
                                       "local_storage_entries": 256,
                                       "value_bytes_per_entry": 1024}},
        "measurement": {"states": states,
                        "post_release_minus_empty_resident_bytes": release_rss,
                        "median_post_release_minus_empty_resident_bytes": int(statistics.median(release_rss)),
                        "post_release_minus_empty_physical_footprint_bytes": release_physical,
                        "median_post_release_minus_empty_physical_footprint_bytes": int(statistics.median(release_physical)),
                        "post_trim_minus_empty_resident_bytes": trim_rss,
                        "median_post_trim_minus_empty_resident_bytes": int(statistics.median(trim_rss)),
                        "post_trim_minus_empty_physical_footprint_bytes": trim_physical,
                        "median_post_trim_minus_empty_physical_footprint_bytes": int(statistics.median(trim_physical)),
                        "post_trim_minus_post_release_resident_bytes": trim_vs_release_rss,
                        "median_post_trim_minus_post_release_resident_bytes": int(statistics.median(trim_vs_release_rss)),
                        "post_trim_minus_post_release_physical_footprint_bytes": trim_vs_release_physical,
                        "median_post_trim_minus_post_release_physical_footprint_bytes": median_trim_physical,
                        "trim_verdict": ("effective" if trim_effective else "ineffective"),
                        "trim_effectiveness_threshold_bytes": -16384},
        "capacity_rejections": overflows,
        "limitations": ["synthetic state is not an HTML/browser-engine workload",
                        "footprint physical footprint is Apple platform-specific",
                        "20ms gaps and command overhead exist between stage samples",
                        ("allocator release reporting is unavailable; verdict uses measured footprint"
                         if trim_state["allocator_release_reporting"] == "unavailable"
                         else "allocator-reported bytes are retained separately from the measured verdict"),
                        "one process was observed; future engine routes require complete descendant attribution",
                        "no external browser baseline is meaningful for this engine-neutral state"],
    }
    encoded = json.dumps(receipt, indent=2, sort_keys=True) + "\n"
    if args.receipt:
        Path(args.receipt).write_text(encoded, encoding="utf-8")
    print(encoded, end="")


if __name__ == "__main__":
    main()
