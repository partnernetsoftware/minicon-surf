#!/usr/bin/env python3
"""Long-cycle soak and fragmentation court for the native realm allocators.

Rules and adoption criteria are fixed in RULES and CRITERIA below and copied
into the receipt; they were committed before the first full run and are not
moved afterwards. Two browser workloads (the interactive fixture and the
hermetic representative page) run 128 open → use → close cycles in one host
process per run, sampling physical footprint, RSS, libmalloc, realm bytes and
arena statistics at fixed cycles. A separate allocator-stress section opens
an adversarial allocation fixture; it is an allocator microbenchmark and is
judged apart from the browser workloads. Every close is followed by a
teardown check: any live owner, arena, block, mapping or target after a close
fails the run.
"""

import argparse
import hashlib
import importlib.util
import json
import statistics
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "protocol"))
import check_contract  # noqa: E402

RULES = {
    "cycles": 128,
    "targets_per_cycle": 1,
    "warmup_runs": 1,
    "measured_runs": 7,
    "settle_ms": 200,
    "sample_cycles": [1, 2, 4, 8, 16, 24, 32, 48, 64, 80, 96, 112, 128],
    "use_per_cycle": "target.snapshot (semantic, 64 nodes); on the interactive fixture also one revision-scoped click and a revision wait",
    "stages_per_sample_cycle": ["live (target open, after use)", "post_close (after target.close)"],
    "teardown_check_after_every_close": [
        "owners.targets.objects == 0", "owners.script_realms.objects == 0",
        "owners.script_realms.dedicated_arenas == []", "owners.network.fetches == 0",
        "allocator.arena_blocks_leaked_total == 0", "allocator.zone_blocks_leaked_total == 0",
        "arena arm: allocator.arenas_unmapped == cycles closed so far",
    ],
    "slope": "least-squares slope of post_close physical footprint over sample cycles >= 8, bytes per cycle",
    "late_growth": "post_close footprint at cycle 128 minus at cycle 64",
    "reopen_cost": "live footprint at a sample cycle minus post_close footprint at the previous sample cycle",
    "retained": "post_close footprint minus the empty footprint of the same run",
    "capacity": "capacity-growth.html: realm live bytes at first OOM divided by 16 MiB, 1 warm-up + 7 runs",
    "stress": "allocator-stress.html opened once per run (1 warm-up + 7 runs) and eight open/close cycles in one process; judged separately from the browser workloads",
    "trim": "memory.trim on a live stress realm; the arena path only marks the free tail reusable and is reported as tail-only, never as close recovery",
}

CRITERIA = {
    "C1_retained_128": "arena median retained at cycle 128 < system median, exact two-sided Mann-Whitney p < 0.05, both workloads",
    "C2_first_open_live": "arena median (live at cycle 1 - empty) <= 1.10 x system median, both workloads",
    "C3_slope_plateau": "arena slope <= 4096 bytes/cycle and median late growth <= 524288 bytes, and arena slope <= system slope + 1024, both workloads",
    "C4_reopen_stable": "arena median reopen cost at cycle 128 <= 1.5 x median reopen cost at cycle 8, both workloads",
    "C5_rss_post_close_128": "arena median RSS after the close at cycle 128 <= system median, both workloads",
    "C6_teardown": "zero teardown-check violations in every run of every arm",
    "C7_capacity": "arena median dense-array capacity ratio >= 0.90 x system median",
    "verdict": "court-eligible only if C1..C7 all hold; the default allocator stays unchanged either way and the arena stays opt-in",
}

FIXTURE_ROOT = ROOT / "labs" / "court" / "fixtures"
REALM_LIMIT_BYTES = 16 * 1024 * 1024


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


RETENTION = load_module("retention_court", Path(__file__).with_name("retention-court.py"))


def report_metrics(report, arena_arm):
    owners = report["owners"]
    arenas = owners["script_realms"]["dedicated_arenas"]
    return {
        "targets": owners["targets"]["objects"],
        "realms": owners["script_realms"]["objects"],
        "realm_bytes": owners["script_realms"]["malloc_bytes"],
        "network_fetches": owners["network"]["fetches"],
        "libmalloc_in_use": report["libmalloc"]["size_in_use"],
        "libmalloc_allocated": report["libmalloc"]["size_allocated"],
        "arena_used": sum(a["used_bytes"] for a in arenas),
        "arena_blocks": sum(a["blocks"] for a in arenas),
        "arena_high_water": sum(a["high_water_bytes"] for a in arenas),
        "arena_decommitted_from": [a["decommitted_from"] for a in arenas],
        "arenas_live": len(arenas),
        "arenas_unmapped": report["allocator"]["arenas_unmapped"],
        "arena_leaked": report["allocator"]["arena_blocks_leaked_total"],
        "zone_leaked": report["allocator"]["zone_blocks_leaked_total"],
        "arena_arm": arena_arm,
    }


def sample(host, arena_arm):
    time.sleep(RULES["settle_ms"] / 1000.0)
    outside = RETENTION.sample_process(host.process.pid)
    report = host.call("memory.report", {})
    return {**outside, **report_metrics(report, arena_arm)}


def teardown_violations(host, cycles_closed, arena_arm):
    report = host.call("memory.report", {})
    m = report_metrics(report, arena_arm)
    violations = []
    if m["targets"] != 0 or m["realms"] != 0:
        violations.append("owner_not_zero")
    if m["arenas_live"] != 0:
        violations.append("arena_still_live")
    if m["network_fetches"] != 0:
        violations.append("network_owner_not_zero")
    if m["arena_leaked"] != 0 or m["zone_leaked"] != 0:
        violations.append("blocks_leaked")
    if arena_arm and m["arenas_unmapped"] != cycles_closed:
        violations.append("mapping_count_differs")
    return violations


def open_and_use(host, session, workload, origin):
    if workload == "representative-url":
        target = host.call("target.open", {"session": session, "url": f"{origin}/index.html"})["target"]
    else:
        target = host.call("target.open", {"session": session, "fixture": "semantic-interactive.html"})["target"]
    snapshot = host.call("target.snapshot", {"target": target, "format": "semantic", "max_bytes": 65536, "max_nodes": 64})
    if workload == "fixture-interactive":
        button = next(n for n in snapshot["nodes"] if n["role"] == "button")
        host.call("target.act", {"target": target, "reference": button["reference"], "action": {"kind": "click"}})
        host.call("target.wait", {"target": target, "condition": {"kind": "revision_at_least", "revision": 1}}, 2000)
    return target


def soak_once(binary, workload, allocator, origin):
    arena_arm = allocator == "arena"
    with tempfile.TemporaryDirectory(prefix="minicon-surf-arena-soak-") as directory:
        host = RETENTION.Host(binary, directory, origin if workload == "representative-url" else None, allocator)
        try:
            profile = host.call("profile.create", {"persistence": "ephemeral"})["profile"]
            session = host.call("session.open", {"profile": profile})["session"]
            empty = sample(host, arena_arm)
            live, post_close, violations = {}, {}, {}
            for cycle in range(1, RULES["cycles"] + 1):
                target = open_and_use(host, session, workload, origin)
                if cycle in RULES["sample_cycles"]:
                    live[cycle] = sample(host, arena_arm)
                host.call("target.close", {"target": target})
                found = teardown_violations(host, cycle, arena_arm)
                if found:
                    violations[cycle] = found
                if cycle in RULES["sample_cycles"]:
                    post_close[cycle] = sample(host, arena_arm)
            host.call("session.close", {"session": session})
            if host.finish() != 0:
                raise RuntimeError("host exited with failure")
            return {"empty": empty, "live": live, "post_close": post_close, "teardown_violations": violations}
        finally:
            if host.process.poll() is None:
                host.process.kill()
                host.process.wait()


def stress_once(binary, allocator):
    arena_arm = allocator == "arena"
    with tempfile.TemporaryDirectory(prefix="minicon-surf-arena-stress-") as directory:
        host = RETENTION.Host(binary, directory, None, allocator)
        try:
            profile = host.call("profile.create", {"persistence": "ephemeral"})["profile"]
            session = host.call("session.open", {"profile": profile})["session"]
            empty = sample(host, arena_arm)
            target = host.call("target.open", {"session": session, "fixture": "allocator-stress.html"}, 120000)["target"]
            snapshot = host.call("target.snapshot", {"target": target, "format": "semantic", "max_bytes": 65536, "max_nodes": 16})
            text = next((n["name"] for n in snapshot["nodes"] if n["role"] == "text"), "")
            live = sample(host, arena_arm)
            trim = host.call("memory.trim", {})
            post_trim = sample(host, arena_arm)
            host.call("target.close", {"target": target})
            violations = teardown_violations(host, 1, arena_arm)
            post_close = sample(host, arena_arm)
            cycle_post_close = []
            for cycle in range(2, 9):
                target = host.call("target.open", {"session": session, "fixture": "allocator-stress.html"}, 120000)["target"]
                host.call("target.close", {"target": target})
                violations += teardown_violations(host, cycle, arena_arm)
                cycle_post_close.append(sample(host, arena_arm)["physical_footprint_bytes"])
            host.call("session.close", {"session": session})
            host.finish()
            return {
                "page_report": text,
                "empty": empty,
                "live": live,
                "trim": {"released_bytes": trim["released_bytes"], "arena_released_bytes": trim.get("arena_released_bytes", 0)},
                "post_trim": post_trim,
                "post_close": post_close,
                "cycle_post_close_footprint": cycle_post_close,
                "teardown_violations": violations,
            }
        finally:
            if host.process.poll() is None:
                host.process.kill()
                host.process.wait()


def slope(points):
    xs = [x for x, _ in points]
    ys = [y for _, y in points]
    n = len(points)
    mean_x = sum(xs) / n
    mean_y = sum(ys) / n
    denominator = sum((x - mean_x) ** 2 for x in xs)
    return sum((x - mean_x) * (y - mean_y) for x, y in points) / denominator if denominator else 0.0


def derive(run):
    empty_fp = run["empty"]["physical_footprint_bytes"]
    cycles = RULES["sample_cycles"]
    post = run["post_close"]
    live = run["live"]
    retained = {c: post[c]["physical_footprint_bytes"] - empty_fp for c in cycles}
    reopen = {}
    for previous, current in zip(cycles, cycles[1:]):
        reopen[current] = live[current]["physical_footprint_bytes"] - post[previous]["physical_footprint_bytes"]
    last = cycles[-1]
    half = min(cycles, key=lambda c: abs(c - last / 2))
    early = min(c for c in cycles if c >= 8) if any(c >= 8 for c in cycles) else cycles[min(1, len(cycles) - 1)]
    return {
        "empty_footprint": empty_fp,
        "first_open_live_minus_empty": live[1]["physical_footprint_bytes"] - empty_fp,
        "retained_by_cycle": retained,
        "retained_128": retained[last],
        "slope_bytes_per_cycle": slope([(c, post[c]["physical_footprint_bytes"]) for c in cycles if c >= 8] or [(c, post[c]["physical_footprint_bytes"]) for c in cycles]),
        "late_growth_64_to_128": post[last]["physical_footprint_bytes"] - post[half]["physical_footprint_bytes"],
        "reopen_cost_by_cycle": reopen,
        "reopen_cost_8": reopen.get(early, 0),
        "reopen_cost_128": reopen.get(last, 0),
        "rss_post_close_128": post[last]["resident_bytes"],
        "rss_live_128": live[last]["resident_bytes"],
        "libmalloc_allocated_post_close_128": post[last]["libmalloc_allocated"],
        "libmalloc_in_use_post_close_128": post[last]["libmalloc_in_use"],
        "arena_high_water_live_128": live[last]["arena_high_water"],
        "arena_used_live_128": live[last]["arena_used"],
        "arenas_unmapped_128": post[last]["arenas_unmapped"],
        "teardown_violations": run["teardown_violations"],
    }


def summarize(values):
    return {"values": values, "median": statistics.median(values), "minimum": min(values), "maximum": max(values)}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--binary", required=True)
    parser.add_argument("--allocators", default="system,arena")
    parser.add_argument("--workloads", default="fixture-interactive,representative-url")
    parser.add_argument("--repetitions", type=int, default=RULES["measured_runs"])
    parser.add_argument("--cycles", type=int, default=RULES["cycles"], help="smoke runs only; the receipt records deviations from RULES")
    parser.add_argument("--receipt")
    args = parser.parse_args()
    if args.cycles != RULES["cycles"]:
        RULES["sample_cycles"] = sorted({c for c in RULES["sample_cycles"] if c <= args.cycles} | {args.cycles})
        RULES["cycles"] = args.cycles
    binary = Path(args.binary)
    binary_sha = hashlib.sha256(binary.read_bytes()).hexdigest()
    network = RETENTION.load_network_module()
    server = network.Server(("127.0.0.1", 0), network.Handler)
    origin = f"http://127.0.0.1:{server.server_address[1]}"
    threading.Thread(target=server.serve_forever, daemon=True).start()
    allocators = [a for a in args.allocators.split(",") if a]
    workloads = [w for w in args.workloads.split(",") if w]
    cells = {}
    try:
        for allocator in allocators:
            for workload in workloads:
                soak_once(str(binary), workload, allocator, origin)
                runs = [derive(soak_once(str(binary), workload, allocator, origin)) for _ in range(args.repetitions)]
                cells[f"{workload}/{allocator}"] = {
                    "workload": workload,
                    "allocator": allocator,
                    "runs": runs,
                    "first_open_live_minus_empty": summarize([r["first_open_live_minus_empty"] for r in runs]),
                    "retained_128": summarize([r["retained_128"] for r in runs]),
                    "retained_by_cycle_median": {c: statistics.median(r["retained_by_cycle"][c] for r in runs) for c in RULES["sample_cycles"]},
                    "slope_bytes_per_cycle": summarize([round(r["slope_bytes_per_cycle"], 1) for r in runs]),
                    "late_growth_64_to_128": summarize([r["late_growth_64_to_128"] for r in runs]),
                    "reopen_cost_8": summarize([r["reopen_cost_8"] for r in runs]),
                    "reopen_cost_128": summarize([r["reopen_cost_128"] for r in runs]),
                    "rss_post_close_128": summarize([r["rss_post_close_128"] for r in runs]),
                    "rss_live_128": summarize([r["rss_live_128"] for r in runs]),
                    "libmalloc_allocated_post_close_128": summarize([r["libmalloc_allocated_post_close_128"] for r in runs]),
                    "libmalloc_in_use_post_close_128": summarize([r["libmalloc_in_use_post_close_128"] for r in runs]),
                    "arena_high_water_live_128": summarize([r["arena_high_water_live_128"] for r in runs]),
                    "arena_used_live_128": summarize([r["arena_used_live_128"] for r in runs]),
                    "teardown_violation_runs": sum(1 for r in runs if r["teardown_violations"]),
                }
    finally:
        server.shutdown()
        server.server_close()

    capacity = {}
    stress = {}
    for allocator in allocators:
        RETENTION.capacity_once(str(binary), allocator, RULES["settle_ms"])
        runs = [RETENTION.capacity_once(str(binary), allocator, RULES["settle_ms"]) for _ in range(args.repetitions)]
        capacity[allocator] = {
            "ratio_of_hard_cap": summarize([round(r["ratio_of_hard_cap"], 4) for r in runs]),
            "realm_live_bytes_after_first_oom": summarize([r["realm_live_bytes_after_first_oom"] for r in runs]),
        }
        stress_once(str(binary), allocator)
        runs = [stress_once(str(binary), allocator) for _ in range(args.repetitions)]
        stress[allocator] = {
            "page_reports": sorted({r["page_report"] for r in runs}),
            "live_minus_empty_footprint": summarize([r["live"]["physical_footprint_bytes"] - r["empty"]["physical_footprint_bytes"] for r in runs]),
            "live_realm_bytes": summarize([r["live"]["realm_bytes"] for r in runs]),
            "arena_used_live": summarize([r["live"]["arena_used"] for r in runs]),
            "arena_high_water_live": summarize([r["live"]["arena_high_water"] for r in runs]),
            "arena_blocks_live": summarize([r["live"]["arena_blocks"] for r in runs]),
            "fragmentation_high_water_over_used": summarize([round(r["live"]["arena_high_water"] / r["live"]["arena_used"], 4) if r["live"]["arena_used"] else 0 for r in runs]),
            "libmalloc_allocated_minus_in_use_live": summarize([r["live"]["libmalloc_allocated"] - r["live"]["libmalloc_in_use"] for r in runs]),
            "trim_released_bytes": summarize([r["trim"]["released_bytes"] for r in runs]),
            "trim_arena_released_bytes_tail_only": summarize([r["trim"]["arena_released_bytes"] for r in runs]),
            "post_trim_minus_live_footprint": summarize([r["post_trim"]["physical_footprint_bytes"] - r["live"]["physical_footprint_bytes"] for r in runs]),
            "post_close_minus_empty_footprint": summarize([r["post_close"]["physical_footprint_bytes"] - r["empty"]["physical_footprint_bytes"] for r in runs]),
            "eighth_cycle_post_close_minus_first": summarize([r["cycle_post_close_footprint"][-1] - r["post_close"]["physical_footprint_bytes"] for r in runs]),
            "teardown_violation_runs": sum(1 for r in runs if r["teardown_violations"]),
        }

    evaluation = {}
    if "system" in allocators and "arena" in allocators:
        def cell(workload, allocator):
            return cells[f"{workload}/{allocator}"]
        c1 = all(
            cell(w, "arena")["retained_128"]["median"] < cell(w, "system")["retained_128"]["median"]
            and RETENTION.mann_whitney_exact(cell(w, "system")["retained_128"]["values"], cell(w, "arena")["retained_128"]["values"])["p_two_sided"] < 0.05
            for w in workloads)
        c2 = all(cell(w, "arena")["first_open_live_minus_empty"]["median"] <= 1.10 * cell(w, "system")["first_open_live_minus_empty"]["median"] for w in workloads)
        c3 = all(
            cell(w, "arena")["slope_bytes_per_cycle"]["median"] <= 4096
            and cell(w, "arena")["late_growth_64_to_128"]["median"] <= 524288
            and cell(w, "arena")["slope_bytes_per_cycle"]["median"] <= cell(w, "system")["slope_bytes_per_cycle"]["median"] + 1024
            for w in workloads)
        c4 = all(cell(w, "arena")["reopen_cost_128"]["median"] <= 1.5 * cell(w, "arena")["reopen_cost_8"]["median"] for w in workloads)
        c5 = all(cell(w, "arena")["rss_post_close_128"]["median"] <= cell(w, "system")["rss_post_close_128"]["median"] for w in workloads)
        c6 = all(cells[k]["teardown_violation_runs"] == 0 for k in cells) and all(s["teardown_violation_runs"] == 0 for s in stress.values())
        c7 = capacity["arena"]["ratio_of_hard_cap"]["median"] >= 0.90 * capacity["system"]["ratio_of_hard_cap"]["median"]
        evaluation = {"C1_retained_128": c1, "C2_first_open_live": c2, "C3_slope_plateau": c3, "C4_reopen_stable": c4,
                      "C5_rss_post_close_128": c5, "C6_teardown": c6, "C7_capacity": c7}
        evaluation["court_eligible"] = all(evaluation.values())
        evaluation["tests"] = {
            w: RETENTION.mann_whitney_exact(cell(w, "system")["retained_128"]["values"], cell(w, "arena")["retained_128"]["values"])
            for w in workloads
        }
    receipt = {
        "schema": "minicon-surf.native-dom-arena-soak-receipt/0.0.1",
        "status": "observed",
        "technology": "native-dom",
        "technology_version": "0.0.2",
        "host_sha256": binary_sha,
        "platform": {"os": "macos", "architecture": "arm64"},
        "rules": RULES,
        "criteria": CRITERIA,
        "measurement": {
            "semantic": "physical footprint and RSS of one host process sampled from outside; memory.report owners, libmalloc and arena statistics from inside; one target per cycle, 128 cycles per run",
            "cells": cells,
            "capacity_at_first_oom": capacity,
            "allocator_stress": stress,
            "stress_semantic": "allocator-stress.html is an allocator microbenchmark, not a page; its rows describe the allocator under that script and are judged apart from the browser workloads",
        },
        "evaluation": evaluation,
        "limitations": [
            "one macOS arm64 machine; one target per cycle; two browser workloads plus one synthetic allocator script",
            "the arena's memory.trim marks only the free tail of a live realm reusable; it is not a whole-realm close recovery and is reported as tail-only",
            "the default allocator's post-close retention is libmalloc reservation; leak absence is not claimed for any arm",
            "the arena is a macOS mmap prototype; nothing here is a cross-platform result",
        ],
    }
    encoded = json.dumps(receipt, indent=2, sort_keys=True, default=str) + "\n"
    if args.receipt:
        Path(args.receipt).write_text(encoded, encoding="utf-8")
    print(json.dumps(evaluation, indent=1))
    for key, cell in cells.items():
        print(key, "first-open", cell["first_open_live_minus_empty"]["median"], "retained128", cell["retained_128"]["median"],
              "slope", cell["slope_bytes_per_cycle"]["median"], "late", cell["late_growth_64_to_128"]["median"],
              "reopen8/128", cell["reopen_cost_8"]["median"], cell["reopen_cost_128"]["median"],
              "rss128", cell["rss_post_close_128"]["median"], "violations", cell["teardown_violation_runs"])
    for key, row in stress.items():
        print("stress", key, "live-empty", row["live_minus_empty_footprint"]["median"], "frag", row["fragmentation_high_water_over_used"]["median"],
              "trim tail", row["trim_arena_released_bytes_tail_only"]["median"], "post-close", row["post_close_minus_empty_footprint"]["median"],
              "8th-1st", row["eighth_cycle_post_close_minus_first"]["median"], row["page_reports"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
