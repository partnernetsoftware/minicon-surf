#!/usr/bin/env python3
"""Post-close retention attribution court for the native route (macOS arm64).

Every run is a fresh host process, so its `empty` and `live` stages are the
fresh-process control; `post_close`, `post_action`, `reopen_live` and
`post_reclose` are the same-instance stages. Each stage records physical
footprint and RSS from outside, and `memory.report` from inside: logical
owners (targets, script realms and their byte count, network fetches and
bytes) and libmalloc's `size_in_use` versus `size_allocated`, which splits
retained memory into bytes still allocated and bytes freed but reserved.

Cells are workload × allocator. Workloads: a static fixture, an interactive
fixture, and the hermetic representative page over the bounded network.
Allocators: the system allocator (default) and, on request, one dedicated
libmalloc zone per QuickJS realm destroyed at close, or one reserved mapping
per realm served by a boundary-tag heap and unmapped at close. The action
stage runs `memory.trim` (malloc_zone_pressure_relief plus, for live arena
realms, madvise of the free tail). One warm-up plus seven measured runs per
cell; the receipt keeps every sample and exact two-sided Mann-Whitney U tests
between every pair of allocators for retained footprint, first-open live
footprint and post-close RSS.
"""

import argparse
import ctypes
import ctypes.util
import hashlib
import http.server
import importlib.util
import itertools
import json
import os
import socketserver
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

FIXTURE_ROOT = ROOT / "labs" / "court" / "fixtures"
STAGES = ("empty", "live", "post_close", "post_action", "reopen_live", "post_reclose")
TARGETS = 8


class RusageInfoV4(ctypes.Structure):
    _fields_ = [("ri_uuid", ctypes.c_uint8 * 16)] + [
        (name, ctypes.c_uint64) for name in (
            "ri_user_time", "ri_system_time", "ri_pkg_idle_wkups", "ri_interrupt_wkups", "ri_pageins",
            "ri_wired_size", "ri_resident_size", "ri_phys_footprint", "ri_proc_start_abstime",
            "ri_proc_exit_abstime", "ri_child_user_time", "ri_child_system_time", "ri_child_pkg_idle_wkups",
            "ri_child_interrupt_wkups", "ri_child_pageins", "ri_child_elapsed_abstime", "ri_diskio_bytesread",
            "ri_diskio_byteswritten", "ri_cpu_time_qos_default", "ri_cpu_time_qos_maintenance",
            "ri_cpu_time_qos_background", "ri_cpu_time_qos_utility", "ri_cpu_time_qos_legacy",
            "ri_cpu_time_qos_user_initiated", "ri_cpu_time_qos_user_interactive", "ri_billed_system_time",
            "ri_serviced_system_time", "ri_logical_writes", "ri_lifetime_max_phys_footprint", "ri_instructions",
            "ri_cycles", "ri_billed_energy", "ri_serviced_energy", "ri_interval_max_phys_footprint",
            "ri_runnable_time",
        )
    ]


_LIBPROC = ctypes.CDLL(ctypes.util.find_library("proc"))
_LIBPROC.proc_pid_rusage.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_void_p]
_LIBPROC.proc_pid_rusage.restype = ctypes.c_int


def sample_process(pid):
    info = RusageInfoV4()
    if _LIBPROC.proc_pid_rusage(pid, 4, ctypes.byref(info)) != 0:
        raise RuntimeError("proc_pid_rusage failed")
    rss = subprocess.run(["ps", "-o", "rss=", "-p", str(pid)], capture_output=True, text=True).stdout.strip()
    return {"physical_footprint_bytes": int(info.ri_phys_footprint),
            "resident_bytes": int(rss or 0) * 1024,
            "lifetime_max_physical_footprint_bytes": int(info.ri_lifetime_max_phys_footprint)}


def load_network_module():
    spec = importlib.util.spec_from_file_location("network_journey", Path(__file__).with_name("network-journey.py"))
    module = importlib.util.module_from_spec(spec)
    saved = sys.argv
    sys.argv = ["network-journey"]
    try:
        spec.loader.exec_module(module)
    finally:
        sys.argv = saved
    return module


ALLOCATOR_KNOBS = {"system": None, "zone": "MINICON_SURF_NATIVE_REALM_ZONE", "arena": "MINICON_SURF_NATIVE_REALM_ARENA"}


class Host:
    def __init__(self, binary, directory, origin, allocator):
        command = [binary, "serve", "--stdio", "--fixture-root", str(FIXTURE_ROOT),
                   "--config-dir", str(Path(directory) / "config")]
        if origin:
            command += ["--allow-origin", origin]
        environment = dict(os.environ)
        for knob in ALLOCATOR_KNOBS.values():
            if knob:
                environment.pop(knob, None)
        knob = ALLOCATOR_KNOBS[allocator]
        if knob:
            environment[knob] = "1"
        self.process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                        stderr=subprocess.DEVNULL, text=True, env=environment)
        self.counter = 0

    def call(self, operation, arguments, deadline_ms=30000):
        self.counter += 1
        request = {"protocol": "minicon-surf.control", "version": "0.0.1",
                   "request_id": f"req_ret_{self.counter}", "deadline_ms": deadline_ms,
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


def stage_sample(host, settle_ms):
    time.sleep(settle_ms / 1000.0)
    outside = sample_process(host.process.pid)
    report = host.call("memory.report", {})
    owners = report["owners"]
    return {
        **outside,
        "owners": {
            "targets": owners["targets"]["objects"],
            "script_realms": owners["script_realms"]["objects"],
            "script_realm_bytes": owners["script_realms"]["malloc_bytes"],
            "network_fetches": owners["network"]["fetches"],
            "network_bytes": owners["network"]["bytes"],
        },
        "libmalloc_size_in_use": report["libmalloc"]["size_in_use"],
        "libmalloc_size_allocated": report["libmalloc"]["size_allocated"],
        "zones_destroyed": report["allocator"]["zones_destroyed"],
        "zone_blocks_leaked_total": report["allocator"]["zone_blocks_leaked_total"],
        "arenas_unmapped": report["allocator"]["arenas_unmapped"],
        "arena_blocks_leaked_total": report["allocator"]["arena_blocks_leaked_total"],
        "arena_used_bytes": sum(a["used_bytes"] for a in owners["script_realms"]["dedicated_arenas"]),
        "arena_high_water_bytes": sum(a["high_water_bytes"] for a in owners["script_realms"]["dedicated_arenas"]),
    }


def open_targets(host, session, workload, origin):
    ids = []
    for _ in range(TARGETS):
        if workload == "representative-url":
            result = host.call("target.open", {"session": session, "url": f"{origin}/index.html"})
        else:
            fixture = {"fixture-static": "semantic-static.html", "fixture-interactive": "semantic-interactive.html"}[workload]
            result = host.call("target.open", {"session": session, "fixture": fixture})
        ids.append(result["target"])
    for target in ids:
        # Force the realm to hold a snapshot's node table, as an Agent would.
        host.call("target.snapshot", {"target": target, "format": "semantic", "max_bytes": 65536, "max_nodes": 64})
    return ids


def run_once(binary, workload, allocator, origin, settle_ms):
    with tempfile.TemporaryDirectory(prefix="minicon-surf-retention-") as directory:
        host = Host(binary, directory, origin if workload == "representative-url" else None, allocator)
        try:
            profile = host.call("profile.create", {"persistence": "ephemeral"})["profile"]
            session = host.call("session.open", {"profile": profile})["session"]
            stages = {"empty": stage_sample(host, settle_ms)}
            ids = open_targets(host, session, workload, origin)
            stages["live"] = stage_sample(host, settle_ms)
            for target in ids:
                host.call("target.close", {"target": target})
            stages["post_close"] = stage_sample(host, settle_ms)
            trim = host.call("memory.trim", {})
            stages["post_action"] = stage_sample(host, settle_ms)
            stages["post_action"]["trim_released_bytes"] = trim["released_bytes"]
            stages["post_action"]["trim_arena_released_bytes"] = trim.get("arena_released_bytes", 0)
            ids = open_targets(host, session, workload, origin)
            stages["reopen_live"] = stage_sample(host, settle_ms)
            for target in ids:
                host.call("target.close", {"target": target})
            stages["post_reclose"] = stage_sample(host, settle_ms)
            host.call("session.close", {"session": session})
            if host.finish() != 0:
                raise RuntimeError("host exited with failure")
            return stages
        finally:
            if host.process.poll() is None:
                host.process.kill()
                host.process.wait()


REALM_LIMIT_BYTES = 16 * 1024 * 1024


def capacity_once(binary, allocator, settle_ms):
    """Open the growth fixture and read the realm's live bytes at first OOM."""
    with tempfile.TemporaryDirectory(prefix="minicon-surf-capacity-") as directory:
        host = Host(binary, directory, None, allocator)
        try:
            profile = host.call("profile.create", {"persistence": "ephemeral"})["profile"]
            session = host.call("session.open", {"profile": profile})["session"]
            target = host.call("target.open", {"session": session, "fixture": "capacity-growth.html"}, 120000)["target"]
            time.sleep(settle_ms / 1000.0)
            report = host.call("memory.report", {})
            snapshot = host.call("target.snapshot", {"target": target, "format": "semantic", "max_bytes": 65536, "max_nodes": 16})
            text = next((n["name"] for n in snapshot["nodes"] if n["role"] == "text"), "")
            live = report["owners"]["script_realms"]["malloc_bytes"]
            arenas = report["owners"]["script_realms"]["dedicated_arenas"]
            host.call("target.close", {"target": target})
            host.call("session.close", {"session": session})
            host.finish()
            return {"realm_live_bytes_after_first_oom": live,
                    "ratio_of_hard_cap": live / REALM_LIMIT_BYTES,
                    "arena_high_water_bytes": sum(a["high_water_bytes"] for a in arenas),
                    "page_report": text}
        finally:
            if host.process.poll() is None:
                host.process.kill()
                host.process.wait()


def median_int(values):
    return int(statistics.median(values))


def summarize(values):
    return {"values": values, "median": median_int(values), "minimum": min(values), "maximum": max(values)}


def mann_whitney_exact(a, b):
    """Exact two-sided Mann-Whitney U p-value by enumerating rank splits."""
    pooled = sorted(a + b)
    ranks = {}
    index = 0
    while index < len(pooled):
        end = index
        while end + 1 < len(pooled) and pooled[end + 1] == pooled[index]:
            end += 1
        for k in range(index, end + 1):
            ranks[k] = (index + end + 2) / 2
        index = end + 1
    value_ranks = []
    for k, value in enumerate(pooled):
        value_ranks.append((value, ranks[k]))
    rank_of = {}
    for value, rank in value_ranks:
        rank_of.setdefault(value, rank)
    n_a, n_b = len(a), len(b)
    u_a = sum(rank_of[v] for v in a) - n_a * (n_a + 1) / 2
    u_stat = min(u_a, n_a * n_b - u_a)
    all_ranks = [rank for _, rank in value_ranks]
    total = 0
    extreme = 0
    for combo in itertools.combinations(range(len(all_ranks)), n_a):
        total += 1
        u = sum(all_ranks[i] for i in combo) - n_a * (n_a + 1) / 2
        if min(u, n_a * n_b - u) <= u_stat + 1e-9:
            extreme += 1
    return {"u": u_stat, "p_two_sided": extreme / total, "n_a": n_a, "n_b": n_b}


def aggregate(runs):
    stages = {}
    for stage in STAGES:
        rows = [run[stage] for run in runs]
        stages[stage] = {
            "physical_footprint_bytes": summarize([r["physical_footprint_bytes"] for r in rows]),
            "resident_bytes": summarize([r["resident_bytes"] for r in rows]),
            "libmalloc_size_in_use": summarize([r["libmalloc_size_in_use"] for r in rows]),
            "libmalloc_size_allocated": summarize([r["libmalloc_size_allocated"] for r in rows]),
            "owners": {key: summarize([r["owners"][key] for r in rows]) for key in rows[0]["owners"]},
            "zone_blocks_leaked_total": summarize([r["zone_blocks_leaked_total"] for r in rows]),
            "arena_blocks_leaked_total": summarize([r["arena_blocks_leaked_total"] for r in rows]),
            "arenas_unmapped": summarize([r["arenas_unmapped"] for r in rows]),
            "arena_used_bytes": summarize([r["arena_used_bytes"] for r in rows]),
            "arena_high_water_bytes": summarize([r["arena_high_water_bytes"] for r in rows]),
        }
        if stage == "post_action":
            stages[stage]["trim_released_bytes"] = summarize([r["trim_released_bytes"] for r in rows])
            stages[stage]["trim_arena_released_bytes"] = summarize([r["trim_arena_released_bytes"] for r in rows])

    def delta(later, earlier, key):
        return summarize([run[later][key] - run[earlier][key] for run in runs])

    return {
        "states": stages,
        "retained_post_close_minus_empty": {
            "physical_footprint_bytes": delta("post_close", "empty", "physical_footprint_bytes"),
            "resident_bytes": delta("post_close", "empty", "resident_bytes"),
            "libmalloc_size_in_use": delta("post_close", "empty", "libmalloc_size_in_use"),
            "libmalloc_size_allocated": delta("post_close", "empty", "libmalloc_size_allocated"),
        },
        "live_minus_empty": {
            "physical_footprint_bytes": delta("live", "empty", "physical_footprint_bytes"),
            "resident_bytes": delta("live", "empty", "resident_bytes"),
            "libmalloc_size_in_use": delta("live", "empty", "libmalloc_size_in_use"),
        },
        "action_post_action_minus_post_close": {
            "physical_footprint_bytes": delta("post_action", "post_close", "physical_footprint_bytes"),
            "resident_bytes": delta("post_action", "post_close", "resident_bytes"),
        },
        "reuse_reopen_live_minus_live": {
            "physical_footprint_bytes": delta("reopen_live", "live", "physical_footprint_bytes"),
        },
        "second_close_post_reclose_minus_post_close": {
            "physical_footprint_bytes": delta("post_reclose", "post_close", "physical_footprint_bytes"),
        },
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--binary", required=True)
    parser.add_argument("--repetitions", type=int, default=7)
    parser.add_argument("--settle-ms", type=int, default=500)
    parser.add_argument("--workloads", default="fixture-static,fixture-interactive,representative-url")
    parser.add_argument("--allocators", default="system,zone,arena")
    parser.add_argument("--receipt")
    args = parser.parse_args()
    binary = Path(args.binary)
    binary_sha = hashlib.sha256(binary.read_bytes()).hexdigest()
    network = load_network_module()
    server = network.Server(("127.0.0.1", 0), network.Handler)
    origin = f"http://127.0.0.1:{server.server_address[1]}"
    threading.Thread(target=server.serve_forever, daemon=True).start()
    workloads = [w for w in args.workloads.split(",") if w]
    allocators = [a for a in args.allocators.split(",") if a]
    cells = {}
    try:
        for allocator in allocators:
            for workload in workloads:
                run_once(str(binary), workload, allocator, origin, args.settle_ms)
                runs = [run_once(str(binary), workload, allocator, origin, args.settle_ms)
                        for _ in range(args.repetitions)]
                cells[f"{workload}/{allocator}"] = {"workload": workload, "allocator": allocator,
                                                    **aggregate(runs)}
    finally:
        server.shutdown()
        server.server_close()
    if hashlib.sha256(binary.read_bytes()).hexdigest() != binary_sha:
        raise RuntimeError("host binary changed during the court")

    capacity = {}
    for allocator in allocators:
        capacity_once(str(binary), allocator, args.settle_ms)
        runs = [capacity_once(str(binary), allocator, args.settle_ms) for _ in range(args.repetitions)]
        capacity[allocator] = {
            "realm_live_bytes_after_first_oom": summarize([r["realm_live_bytes_after_first_oom"] for r in runs]),
            "ratio_of_hard_cap": {"values": [round(r["ratio_of_hard_cap"], 4) for r in runs],
                                  "median": round(statistics.median(r["ratio_of_hard_cap"] for r in runs), 4)},
            "arena_high_water_bytes": summarize([r["arena_high_water_bytes"] for r in runs]),
            "page_reports": [r["page_report"] for r in runs],
        }
    measures = {
        "retained_footprint": ("retained_post_close_minus_empty", "physical_footprint_bytes"),
        "live_footprint_minus_empty": ("live_minus_empty", "physical_footprint_bytes"),
        "post_close_resident": ("states", "post_close"),
    }

    def samples(cell, measure):
        section, key = measures[measure]
        if measure == "post_close_resident":
            return cell["states"]["post_close"]["resident_bytes"]["values"]
        return cell[section][key]["values"]

    tests = {}
    for first, second in itertools.combinations(allocators, 2):
        for workload in workloads:
            entry = {}
            for measure in measures:
                a = samples(cells[f"{workload}/{first}"], measure)
                b = samples(cells[f"{workload}/{second}"], measure)
                entry[measure] = {first: a, second: b, **mann_whitney_exact(a, b)}
            tests[f"{workload}/{first}-vs-{second}"] = entry
    receipt = {
        "schema": "minicon-surf.native-dom-retention-attribution-receipt/0.0.2",
        "status": "observed",
        "technology": "native-dom",
        "technology_version": "0.0.2",
        "host_sha256": binary_sha,
        "platform": {"os": "macos", "architecture": "arm64"},
        "design": {
            "stages": list(STAGES),
            "targets_per_stage": TARGETS,
            "fresh_process_control": "every run is a fresh host process; empty and live are the fresh-process control, later stages are same-instance",
            "action": "memory.trim = malloc_zone_pressure_relief(NULL, 0); for live arena realms also madvise(MADV_FREE_REUSABLE) on the free tail",
            "reuse": "reopen_live opens the same targets again after the action; post_reclose closes them again",
            "allocators": {"system": "Rust global system allocator; QuickJS via rquickjs default allocator into the default libmalloc zone",
                           "zone": "MINICON_SURF_NATIVE_REALM_ZONE=1: one libmalloc zone per QuickJS realm, destroyed after the runtime drops; macOS only",
                           "arena": "MINICON_SURF_NATIVE_REALM_ARENA=1: one 32 MiB private anonymous mapping per QuickJS realm served by a boundary-tag heap, unmapped when the runtime and its allocator are both dropped; QuickJS's own 16 MiB limit binds; macOS only"},
            "warmups_per_cell": 1,
            "measured_repetitions_per_cell": args.repetitions,
            "settle_ms": args.settle_ms,
            "workloads": workloads,
        },
        "measurement": {
            "semantic": "physical footprint and RSS of the single host process per stage; memory.report logical owners and libmalloc size_in_use/size_allocated per stage; retained = post_close - empty",
            "cells": cells,
            "allocator_tests": tests,
            "capacity_at_first_oom": {
                "semantic": "capacity-growth.html grows one dense array until the realm throws; the realm's live bytes are read afterwards and divided by the 16 MiB hard cap",
                "hard_cap_bytes": REALM_LIMIT_BYTES,
                "note": "the hard cap bounds the realm; it is not a guaranteed usable capacity. Under the zone allocator a reallocation is charged for the replacement before the old block is released, so large growth can fail below the cap; the arena grows in place when the free tail follows the block and otherwise holds old and new inside its reservation while QuickJS's own delta check decides",
                "allocators": capacity,
            },
        },
        "limitations": [
            "single process on one macOS arm64 machine; footprint is the kernel's accounting, RSS counts shared pages",
            "libmalloc size_in_use covers the default zone plus dedicated zones; dedicated zones vanish at close, which is the point of the zone cell",
            "the dedicated-zone and arena cells are macOS mechanisms (libmalloc zones; mmap/madvise/munmap) and say nothing about other platforms",
            "arena high-water is the heap's own touched extent, not a kernel page count",
            "settle windows are 500 ms; slower background reclamation is not measured",
        ],
    }
    encoded = json.dumps(receipt, indent=2, sort_keys=True) + "\n"
    if args.receipt:
        Path(args.receipt).write_text(encoded, encoding="utf-8")
    print(encoded, end="")


if __name__ == "__main__":
    main()
