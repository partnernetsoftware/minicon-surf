#!/usr/bin/env python3
"""Frozen court for the bounded Keychain helper experiment (P6, arena cell).

The attribution court showed that the store's footprint is dominated by a
one-time cost at the host's first Keychain call. The approved candidate moves
that call into a short-lived helper process of the same signed binary: the
host generates the data key, the helper wraps or unwraps it with the master
key over an anonymous pipe, and the wrapped key is stored once so committed
mutations never touch the Keychain again.

This court compares the helper build against the in-process build that the
attribution court measured (`--baseline-binary`), on the attribution court's
`store-data` timeline and its `off-equal-churn` control, in fresh processes,
one warm-up plus seven measured runs, under the default allocator and the
arena. A sampler thread reads the complete process tree (host plus every
descendant) at about one kilohertz for the whole run, so the transient peak
while a helper is alive is recorded, not only the host's steady state after
the helper exits. Every control operation boundary checks that no descendant
remains.

Pre-registered criteria (all must hold under both allocators):

- C1 the `profiles_created` step of the helper build costs at most 524,288
  bytes over the feature-off arm of the same build;
- C2 libmalloc in-use after every close is within 65,536 bytes of the
  feature-off arm of the same build;
- C3 the churned total-live point falls by at least 1,048,576 bytes against
  the baseline build on the same timeline;
- C4 the complete-tree footprint peak while a helper is alive is not higher
  than the baseline build's in-process peak over the same create/open
  operations;
- C5 no descendant exists after any operation returns, no helper is ever
  killed on timeout, no helper fails, and the host's helper counters agree
  with the sampler's sightings;
- C6 every host exits cleanly with every owner at zero.

The court records the transient peak and the recovered steady state side by
side, each helper's pid, role, parent and lifetime, and the baseline numbers.
The verdict boundary is fixed: success can only move the arena cell of the
P6 slice from failed to an observed/keep candidate; the default cell stays
failed because feature-off churn alone crosses the line. Fake values only.
"""

import argparse
import ctypes
import hashlib
import importlib.util
import json
import statistics
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "protocol"))


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


ATTR = load_module("profile_attribution_court", Path(__file__).with_name("profile-attribution-court.py"))
PROFILE = ATTR.PROFILE
RETENTION = ATTR.RETENTION
NETWORK = ATTR.NETWORK

CRITERIA = {
    "c1_profiles_created_step_over_feature_off_bytes": 524288,
    "c2_in_use_after_closes_over_feature_off_bytes": 65536,
    "c3_churned_total_live_drop_bytes": 1048576,
    "c4_tree_peak_not_above_in_process_peak": True,
    "c5_no_descendant_after_any_operation": True,
}
MEASURED_OPERATIONS = ("profile.create", "session.open")

_LIBPROC = ctypes.CDLL("/usr/lib/libproc.dylib")
_LIBPROC.proc_listchildpids.argtypes = [ctypes.c_int, ctypes.c_void_p, ctypes.c_int]
_LIBPROC.proc_listchildpids.restype = ctypes.c_int
_LIBPROC.proc_pidpath.argtypes = [ctypes.c_int, ctypes.c_void_p, ctypes.c_uint32]
_LIBPROC.proc_pidpath.restype = ctypes.c_int


def children_of(pid):
    buffer = (ctypes.c_int * 256)()
    count = _LIBPROC.proc_listchildpids(pid, buffer, ctypes.sizeof(buffer))
    if count <= 0:
        return []
    return [p for p in buffer[:count] if p > 0]


def descendants_of(pid):
    found, frontier = [], [pid]
    while frontier:
        parent = frontier.pop()
        for child in children_of(parent):
            found.append((child, parent))
            frontier.append(child)
    return found


def executable_of(pid):
    buffer = ctypes.create_string_buffer(4096)
    if _LIBPROC.proc_pidpath(pid, buffer, 4096) <= 0:
        return None
    return buffer.value.decode(errors="replace")


class TreeSampler:
    """Samples host plus descendants until stopped; records peaks and sightings."""

    def __init__(self, host_pid, host_binary):
        self.host_pid = host_pid
        self.host_binary = str(Path(host_binary).resolve())
        self.samples = []
        self.sightings = {}
        self.max_descendants = 0
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def start(self):
        self._thread.start()

    def stop(self):
        self._stop.set()
        self._thread.join()

    def _run(self):
        while not self._stop.is_set():
            now = time.monotonic()
            try:
                host = RETENTION.sample_process(self.host_pid)
            except RuntimeError:
                break
            tree_fp, tree_rss = host["physical_footprint_bytes"], host["resident_bytes"]
            descendants = descendants_of(self.host_pid)
            self.max_descendants = max(self.max_descendants, len(descendants))
            for pid, parent in descendants:
                record = self.sightings.get(pid)
                if record is None:
                    executable = executable_of(pid)
                    args = subprocess.run(["ps", "-o", "args=", "-p", str(pid)], capture_output=True, text=True).stdout.strip()
                    record = {"pid": pid, "parent": parent, "first_seen": now, "last_seen": now, "samples": 0, "peak_footprint": 0,
                              "peak_rss": 0, "same_binary": executable == self.host_binary,
                              "role": "keychain-helper" if "keychain-helper" in args else (args.split(" ")[-1] if args else "exited before inspection")}
                    self.sightings[pid] = record
                try:
                    child = RETENTION.sample_process(pid)
                except RuntimeError:
                    continue
                record["last_seen"] = now
                record["samples"] += 1
                record["peak_footprint"] = max(record["peak_footprint"], child["physical_footprint_bytes"])
                record["peak_rss"] = max(record["peak_rss"], child["resident_bytes"])
                tree_fp += child["physical_footprint_bytes"]
                tree_rss += child["resident_bytes"]
            self.samples.append((now, host["physical_footprint_bytes"], tree_fp, tree_rss, len(descendants)))
            time.sleep(0.0005)


class TreeHost(PROFILE.Host):
    """The profile court's host with operation windows and descendant checks."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.ops = []
        self.descendants_after_ops = 0
        self.sampler = TreeSampler(self.process.pid, args[0])
        self.sampler.start()

    def call(self, operation, arguments, deadline_ms=30000):
        started = time.monotonic()
        response = super().call(operation, arguments, deadline_ms)
        ended = time.monotonic()
        left = descendants_of(self.process.pid)
        if left:
            self.descendants_after_ops += 1
        self.ops.append({"operation": operation, "start": started, "end": ended, "descendants_after": len(left)})
        return response

    def finish(self):
        code = super().finish()
        self.sampler.stop()
        return code


def window_peaks(host):
    """Peaks of host-only and complete-tree footprint inside the measured operations, and overall."""
    windows = [(o["start"], o["end"]) for o in host.ops if o["operation"] in MEASURED_OPERATIONS]
    in_window = [s for s in host.sampler.samples if any(a <= s[0] <= b for a, b in windows)]
    overall = host.sampler.samples
    return {
        "samples_total": len(overall),
        "samples_in_measured_operations": len(in_window),
        "host_peak_in_measured_operations": max((s[1] for s in in_window), default=0),
        "tree_peak_in_measured_operations": max((s[2] for s in in_window), default=0),
        "tree_rss_peak_in_measured_operations": max((s[3] for s in in_window), default=0),
        "host_peak_overall": max((s[1] for s in overall), default=0),
        "tree_peak_overall": max((s[2] for s in overall), default=0),
        "max_descendants": host.sampler.max_descendants,
        "descendants_after_ops": host.descendants_after_ops,
        "helpers": [
            {**{k: v for k, v in r.items() if k not in ("first_seen", "last_seen")},
             "lifetime_ms": round((r["last_seen"] - r["first_seen"]) * 1000, 3)}
            for r in host.sampler.sightings.values()
        ],
    }


def run_once(binary, arm, allocator, origin):
    hosts = []
    original = ATTR.Host

    def make_host(*args, **kwargs):
        host = TreeHost(*args, **kwargs)
        hosts.append(host)
        return host

    ATTR.Host = make_host
    try:
        with tempfile.TemporaryDirectory(prefix="minicon-surf-profile-helper-") as directory:
            root = Path(directory) / "profiles"
            try:
                run = ATTR.run_arm(binary, arm, allocator, origin, directory)
            finally:
                if arm != "off-equal-churn":
                    ATTR.delete_keychain_item(root)
    finally:
        ATTR.Host = original
    measured = hosts[-1]
    run["tree"] = window_peaks(measured)
    final = ATTR.stage_map(run)["post_close_all"]
    run["helper_counters"] = final.get("keychain_helper")
    return run


def sample_with_helper(host, label):
    """The attribution sampler plus the host's helper counters when it reports them."""
    row = ATTR_SAMPLE(host, label)
    report = host.ok("memory.report", {})
    row["keychain_helper"] = report["owners"]["profiles"].get("keychain_helper")
    return row


ATTR_SAMPLE = ATTR.sample
ATTR.sample = sample_with_helper


def median(values):
    return int(statistics.median(values))


def summarize(values):
    return {"median": median(values), "minimum": min(values), "maximum": max(values), "values": values}


def aggregate(runs):
    base = ATTR.aggregate(runs)
    trees = [r["tree"] for r in runs]
    base["tree"] = {
        key: summarize([t[key] for t in trees])
        for key in ("host_peak_in_measured_operations", "tree_peak_in_measured_operations", "tree_rss_peak_in_measured_operations",
                    "host_peak_overall", "tree_peak_overall", "max_descendants", "descendants_after_ops", "samples_total",
                    "samples_in_measured_operations")
    }
    base["tree"]["helpers_per_run"] = summarize([len(t["helpers"]) for t in trees])
    base["tree"]["helper_lifetime_ms"] = summarize([h["lifetime_ms"] for t in trees for h in t["helpers"]] or [0])
    base["tree"]["helper_peak_footprint"] = summarize([h["peak_footprint"] for t in trees for h in t["helpers"]] or [0])
    base["tree"]["helper_roles"] = sorted({h["role"] for t in trees for h in t["helpers"]})
    base["tree"]["helpers_same_binary"] = all(h["same_binary"] for t in trees for h in t["helpers"])
    base["tree"]["example_helpers"] = trees[0]["helpers"][:4]
    counters = [r["helper_counters"] for r in runs if r["helper_counters"]]
    base["helper_counters_after_run"] = counters[0] if counters else None
    base["helper_counters_consistent"] = all(
        c["spawns_total"] == len(t["helpers"]) and c["failures_total"] == 0 and c["timeout_kills_total"] == 0 and c["live"] == 0
        for c, t in zip(counters, trees)
    ) if counters else None
    return base


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--binary", required=True, help="the helper build under test")
    parser.add_argument("--baseline-binary", required=True, help="the in-process keychain build the attribution court measured")
    parser.add_argument("--repetitions", type=int, default=7)
    parser.add_argument("--warmup", type=int, default=1)
    parser.add_argument("--receipt", required=True)
    args = parser.parse_args()

    server = NETWORK.Server(("127.0.0.1", 0), PROFILE.ProfileHandler)
    origin = f"http://127.0.0.1:{server.server_address[1]}"
    threading.Thread(target=server.serve_forever, daemon=True).start()
    cells = (("helper", args.binary, "off-equal-churn"), ("helper", args.binary, "store-data"), ("baseline", args.baseline_binary, "store-data"))
    results, checks = {}, []

    def expect(name, condition, detail=None):
        checks.append({"check": name, "passed": bool(condition), **({"detail": detail} if detail is not None else {})})

    try:
        for allocator in ("system", "arena"):
            results[allocator] = {}
            for build, binary, arm in cells:
                runs = []
                for repetition in range(args.warmup + args.repetitions):
                    run = run_once(binary, arm, allocator, origin)
                    if repetition >= args.warmup:
                        runs.append(run)
                    final = ATTR.stage_map(run)["post_close_all"]["owners"]
                    expect(f"[{allocator}] {build} {arm} run {repetition}: C6 host exits cleanly with every owner at zero",
                           run["exit_code"] == 0 and final["targets"] == 0 and final["script_realms"] == 0 and final["sessions"] == 0
                           and run["stages"][-1]["arena_blocks_leaked_total"] == 0)
                results[allocator][f"{build}:{arm}"] = aggregate(runs)
    finally:
        server.shutdown()

    for allocator in ("system", "arena"):
        tag = f"[{allocator}] "
        cell = results[allocator]
        helper_data, helper_off, base_data = cell["helper:store-data"], cell["helper:off-equal-churn"], cell["baseline:store-data"]

        def fp(agg, stage):
            return agg["stages"][stage]["physical_footprint_bytes"]["median"]

        c1 = fp(helper_data, "profiles_created") - fp(helper_off, "profiles_created")
        expect(tag + "C1 the profiles_created step costs at most 524,288 over feature-off", c1 <= CRITERIA["c1_profiles_created_step_over_feature_off_bytes"], {"delta": c1})
        c2 = helper_data["stages"]["post_close_all"]["libmalloc_size_in_use"]["median"] - helper_off["stages"]["post_close_all"]["libmalloc_size_in_use"]["median"]
        expect(tag + "C2 libmalloc in-use after every close is within 65,536 of feature-off", c2 <= CRITERIA["c2_in_use_after_closes_over_feature_off_bytes"], {"delta": c2})
        c3 = fp(base_data, "churned_final") - fp(helper_data, "churned_final")
        expect(tag + "C3 the churned total-live point falls by at least 1,048,576 against the baseline build",
               c3 >= CRITERIA["c3_churned_total_live_drop_bytes"], {"baseline": fp(base_data, "churned_final"), "helper": fp(helper_data, "churned_final"), "drop": c3})
        tree_peak = helper_data["tree"]["tree_peak_in_measured_operations"]["median"]
        base_peak = base_data["tree"]["host_peak_in_measured_operations"]["median"]
        expect(tag + "C4 the complete-tree peak while a helper is alive is not above the in-process peak over the same operations",
               tree_peak <= base_peak, {"helper_tree_peak": tree_peak, "in_process_peak": base_peak,
                                        "helper_host_only_peak": helper_data["tree"]["host_peak_in_measured_operations"]["median"]})
        expect(tag + "C5 no descendant after any operation, no timeout kill, no failure, counters agree with sightings",
               helper_data["tree"]["descendants_after_ops"]["maximum"] == 0 and helper_off["tree"]["descendants_after_ops"]["maximum"] == 0
               and helper_data["helper_counters_consistent"] is True and helper_data["tree"]["helpers_same_binary"]
               and helper_data["tree"]["helper_roles"] == ["keychain-helper"],
               {"descendants_after_ops": helper_data["tree"]["descendants_after_ops"], "counters": helper_data["helper_counters_after_run"],
                "roles": helper_data["tree"]["helper_roles"], "helpers_per_run": helper_data["tree"]["helpers_per_run"]})
        expect(tag + "the baseline build spawned nothing", base_data["tree"]["max_descendants"]["maximum"] == 0)

    receipt = {
        "schema": "minicon-surf.native-dom-profile-helper-receipt/0.0.1",
        "technology": "native-dom",
        "technology_version": "0.0.2",
        "host_sha256": hashlib.sha256(Path(args.binary).read_bytes()).hexdigest(),
        "baseline_sha256": hashlib.sha256(Path(args.baseline_binary).read_bytes()).hexdigest(),
        "criteria": CRITERIA,
        "measured_operations": list(MEASURED_OPERATIONS),
        "repetitions": args.repetitions,
        "warmup": args.warmup,
        "checks": checks,
        "passed": all(c["passed"] for c in checks),
        "results": results,
        "verdict_boundary": "success moves only the arena cell of the P6 slice to an observed/keep candidate on this macOS cell; the default cell stays failed because feature-off churn alone crosses the frozen line",
        "limitations": [
            "the sampler sees the tree at about one kilohertz; a helper shorter than one sample interval is still recorded by the host's counters and the descendant checks, but its peak may be under-sampled",
            "physical footprint of the helper counts its private pages; framework code pages shared with the host are counted once by neither",
            "one platform, one fixture set, fake values only; no leak-absence claim",
        ],
    }
    Path(args.receipt).write_text(json.dumps(receipt, indent=1, sort_keys=True) + "\n")
    failed = [c for c in checks if not c["passed"]]
    print(json.dumps({"passed": receipt["passed"], "checks_passed": len(checks) - len(failed), "checks_total": len(checks)}, indent=1))
    for check in failed:
        print("FAIL", json.dumps(check)[:600])
    return 0 if receipt["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
