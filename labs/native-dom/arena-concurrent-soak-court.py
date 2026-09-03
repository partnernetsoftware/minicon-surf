#!/usr/bin/env python3
"""Concurrent multi-target soak court for the native realm allocators.

Rules and criteria are fixed in RULES and CRITERIA below, copied into the
receipt, and were committed before the first full run. Each run keeps one
host process and repeats a round that mirrors an Agent's concurrent shape:
targets are opened up the ladder 1 → 2 → 4 → 8, alternating the interactive
fixture and the hermetic representative page by slot; every target is used
(snapshot; click plus revision wait on the interactive ones); half of them
are closed in a deliberately interleaved order that changes from round to
round; the survivors are checked for unchanged state; the closed slots are
refilled; then every target is closed. Physical footprint, RSS, virtual size,
owners, libmalloc and arena statistics are sampled at each stage of every
round. Any partial close that does not remove exactly its owners and arenas,
any survivor whose revision changes, and any all-close that leaves an owner,
arena, block or mapping behind fails the run. Nothing is recovered by
restarting the host.
"""

import argparse
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
import check_contract  # noqa: E402

RULES = {
    "rounds": 32,
    "ladder": [1, 2, 4, 8],
    "slots": 8,
    "slot_workload": "even slots open semantic-interactive.html, odd slots open the representative page over the bounded network",
    "use": "every target: semantic snapshot (64 nodes); interactive targets also one revision-scoped click and a revision_at_least wait",
    "partial_close_order": {
        "round % 4 == 0": [0, 2, 4, 6],
        "round % 4 == 1": [1, 3, 5, 7],
        "round % 4 == 2": [7, 5, 3, 1],
        "round % 4 == 3": [2, 3, 4, 5],
    },
    "refill": "the closed slots are reopened with the same slot workload and used again",
    "stages_per_round": ["ladder_1", "ladder_2", "ladder_4", "peak_8", "post_partial_close", "refilled_8", "post_all_close"],
    "warmup_runs": 1,
    "measured_runs": 7,
    "settle_ms": 150,
    "sample": "physical footprint, RSS and virtual size (ps vsz) of the host from outside; memory.report owners, libmalloc, realm bytes and arena used/blocks/high-water/reserved/decommit/unmapped/leaked from inside",
    "partial_close_check": "owners.targets and script_realms equal the survivors; arena arm: dedicated_arenas equals the survivors and arenas_unmapped grew by exactly the closed count",
    "survivor_check": "target.inspect revision equals the revision recorded before the partial close and a semantic snapshot returns the same node count",
    "all_close_check": "owners.targets, script_realms, network.fetches all zero; dedicated_arenas empty; leaked blocks zero; arena arm: arenas_unmapped equals every target opened so far in the run",
    "marginal_cost": "(peak_8 footprint - ladder_1 footprint) / 7 within a round",
    "reopen_cost": "refilled_8 footprint - post_partial_close footprint",
    "retained": "post_all_close footprint minus the run's empty footprint",
    "slope": "least-squares slope of post_all_close footprint over rounds >= 4, bytes per round; late growth = last round minus round 24",
    "virtual": "arena arm reserves 32 MiB of address space per live realm; the receipt records host virtual size, the sum of arena high-water (touched) and the physical footprint side by side",
    "interior_hole_signal": "at peak_8 the arena's summed high-water minus summed used; interior trimming stays deferred unless this exceeds both 25% of summed used and 1 MiB in median",
    "capacity": "capacity-growth.html dense-array ratio, 1 warm-up + 7 runs per arm",
}

CRITERIA = {
    "K1_peak_live": "arena median peak_8 footprint (round 1 and final round) <= 1.10 x default median",
    "K2_marginal": "arena median marginal cost per target at the final round <= 1 MiB, <= 1.25 x its own round-1 marginal, and <= 1.10 x the default's round-1 (cold) marginal; the default's later-round marginal is libmalloc page reuse, not a per-target cost, and is recorded but not a bound",
    "K3_partial_close_exact": "every partial close in every run removes exactly its owners, realms and arenas (arena arm: unmapped grows by the closed count)",
    "K4_survivors": "every survivor keeps its revision and snapshot node count across every partial close",
    "K5_all_close_zero": "every all-close leaves zero targets, realms, fetches, live arenas and leaked blocks; arena arm: unmapped equals every target opened so far",
    "K6_slope_plateau": "arena post_all_close slope <= 8192 bytes/round, late growth <= 524288 bytes, and slope <= default slope + 2048",
    "K7_retained_final": "arena median retained after the final all-close < default median with exact Mann-Whitney p < 0.05",
    "K8_capacity": "arena median dense-array capacity ratio >= 0.90 x default median",
    "K9_journeys": "control 27/27 and network 35/35 pass on the same binary under default and arena (checked outside this script and recorded in the README)",
    "verdict": "concurrent-court-eligible only if K1..K8 hold here and K9 is recorded; the default stays unchanged and the arena opt-in either way; interior trimming follows the interior_hole_signal rule, not the verdict",
}

FIXTURE_ROOT = ROOT / "labs" / "court" / "fixtures"


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


def virtual_bytes(pid):
    out = subprocess.run(["ps", "-o", "vsz=", "-p", str(pid)], capture_output=True, text=True).stdout.strip()
    return int(out or 0) * 1024


def inside(report):
    owners = report["owners"]
    arenas = owners["script_realms"]["dedicated_arenas"]
    return {
        "targets": owners["targets"]["objects"],
        "realms": owners["script_realms"]["objects"],
        "realm_bytes": owners["script_realms"]["malloc_bytes"],
        "network_fetches": owners["network"]["fetches"],
        "libmalloc_in_use": report["libmalloc"]["size_in_use"],
        "libmalloc_allocated": report["libmalloc"]["size_allocated"],
        "arenas_live": len(arenas),
        "arena_used": sum(a["used_bytes"] for a in arenas),
        "arena_blocks": sum(a["blocks"] for a in arenas),
        "arena_high_water": sum(a["high_water_bytes"] for a in arenas),
        "arena_reserved": sum(a["reserved_bytes"] for a in arenas),
        "arena_decommitted": sum(1 for a in arenas if a["decommitted_from"] is not None),
        "arenas_unmapped": report["allocator"]["arenas_unmapped"],
        "arena_leaked": report["allocator"]["arena_blocks_leaked_total"],
        "zone_leaked": report["allocator"]["zone_blocks_leaked_total"],
    }


def sample(host):
    time.sleep(RULES["settle_ms"] / 1000.0)
    outside = RETENTION.sample_process(host.process.pid)
    outside["virtual_bytes"] = virtual_bytes(host.process.pid)
    return {**outside, **inside(host.call("memory.report", {}))}


def open_slot(host, session, slot, origin):
    if slot % 2 == 0:
        target = host.call("target.open", {"session": session, "fixture": "semantic-interactive.html"})["target"]
    else:
        target = host.call("target.open", {"session": session, "url": f"{origin}/index.html"})["target"]
    return target


def use_target(host, target, slot):
    snapshot = host.call("target.snapshot", {"target": target, "format": "semantic", "max_bytes": 65536, "max_nodes": 64})
    if slot % 2 == 0:
        button = next(n for n in snapshot["nodes"] if n["role"] == "button")
        host.call("target.act", {"target": target, "reference": button["reference"], "action": {"kind": "click"}})
        host.call("target.wait", {"target": target, "condition": {"kind": "revision_at_least", "revision": 1}}, 2000)
    inspect = host.call("target.inspect", {"target": target})
    snapshot = host.call("target.snapshot", {"target": target, "format": "semantic", "max_bytes": 65536, "max_nodes": 64})
    return {"revision": inspect["revision"], "nodes": len(snapshot["nodes"])}


def close_order(round_index):
    return {0: [0, 2, 4, 6], 1: [1, 3, 5, 7], 2: [7, 5, 3, 1], 3: [2, 3, 4, 5]}[round_index % 4]


def run_once(binary, allocator, origin):
    arena_arm = allocator == "arena"
    with tempfile.TemporaryDirectory(prefix="minicon-surf-concurrent-soak-") as directory:
        host = RETENTION.Host(binary, directory, origin, allocator)
        try:
            profile = host.call("profile.create", {"persistence": "ephemeral"})["profile"]
            session = host.call("session.open", {"profile": profile})["session"]
            empty = sample(host)
            rounds = []
            violations = []
            opened_total = 0
            for round_index in range(RULES["rounds"]):
                slots = {}
                state = {}
                stages = {}
                for level in RULES["ladder"]:
                    while len(slots) < level:
                        slot = len(slots)
                        slots[slot] = open_slot(host, session, slot, origin)
                        opened_total += 1
                    if level < RULES["slots"]:
                        stages[f"ladder_{level}"] = sample(host)
                for slot, target in slots.items():
                    state[slot] = use_target(host, target, slot)
                stages["peak_8"] = sample(host)
                if arena_arm and stages["peak_8"]["arenas_live"] != RULES["slots"]:
                    violations.append({"round": round_index, "stage": "peak_8", "reason": "arena_count_differs"})
                order = close_order(round_index)
                before_unmapped = stages["peak_8"]["arenas_unmapped"]
                for slot in order:
                    host.call("target.close", {"target": slots.pop(slot)})
                stages["post_partial_close"] = sample(host)
                partial = stages["post_partial_close"]
                survivors = len(slots)
                if partial["targets"] != survivors or partial["realms"] != survivors:
                    violations.append({"round": round_index, "stage": "post_partial_close", "reason": "owners_differ_from_survivors"})
                if arena_arm and (partial["arenas_live"] != survivors or partial["arenas_unmapped"] != before_unmapped + len(order)):
                    violations.append({"round": round_index, "stage": "post_partial_close", "reason": "arenas_differ_from_survivors"})
                for slot, target in slots.items():
                    inspect = host.call("target.inspect", {"target": target})
                    snapshot = host.call("target.snapshot", {"target": target, "format": "semantic", "max_bytes": 65536, "max_nodes": 64})
                    if inspect["revision"] != state[slot]["revision"] or len(snapshot["nodes"]) != state[slot]["nodes"]:
                        violations.append({"round": round_index, "stage": "survivors", "slot": slot, "reason": "survivor_state_changed"})
                for slot in order:
                    slots[slot] = open_slot(host, session, slot, origin)
                    opened_total += 1
                    state[slot] = use_target(host, slots[slot], slot)
                stages["refilled_8"] = sample(host)
                for slot in sorted(slots):
                    host.call("target.close", {"target": slots[slot]})
                slots.clear()
                stages["post_all_close"] = sample(host)
                final = stages["post_all_close"]
                if final["targets"] or final["realms"] or final["network_fetches"] or final["arenas_live"] or final["arena_leaked"] or final["zone_leaked"]:
                    violations.append({"round": round_index, "stage": "post_all_close", "reason": "owner_or_arena_survived"})
                if arena_arm and final["arenas_unmapped"] != opened_total:
                    violations.append({"round": round_index, "stage": "post_all_close", "reason": "mapping_count_differs"})
                rounds.append({"close_order": order, "stages": stages})
            host.call("session.close", {"session": session})
            if host.finish() != 0:
                raise RuntimeError("host exited with failure")
            return {"empty": empty, "rounds": rounds, "violations": violations, "opened_total": opened_total}
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
    rounds = run["rounds"]
    fp = lambda r, stage: rounds[r]["stages"][stage]["physical_footprint_bytes"]  # noqa: E731
    first, last = 0, len(rounds) - 1
    quarter = max(0, len(rounds) - len(rounds) // 4 - 1)
    post_all = [fp(r, "post_all_close") - empty_fp for r in range(len(rounds))]
    return {
        "empty_footprint": empty_fp,
        "empty_virtual": run["empty"]["virtual_bytes"],
        "peak_first": fp(first, "peak_8") - empty_fp,
        "peak_final": fp(last, "peak_8") - empty_fp,
        "peak_rss_final": rounds[last]["stages"]["peak_8"]["resident_bytes"],
        "peak_virtual_final": rounds[last]["stages"]["peak_8"]["virtual_bytes"],
        "peak_arena_reserved_final": rounds[last]["stages"]["peak_8"]["arena_reserved"],
        "peak_arena_high_water_final": rounds[last]["stages"]["peak_8"]["arena_high_water"],
        "peak_arena_used_final": rounds[last]["stages"]["peak_8"]["arena_used"],
        "peak_arena_blocks_final": rounds[last]["stages"]["peak_8"]["arena_blocks"],
        "peak_realm_bytes_final": rounds[last]["stages"]["peak_8"]["realm_bytes"],
        "peak_libmalloc_in_use_final": rounds[last]["stages"]["peak_8"]["libmalloc_in_use"],
        "ladder_first": [fp(first, f"ladder_{k}") - empty_fp for k in (1, 2, 4)] + [fp(first, "peak_8") - empty_fp],
        "ladder_final": [fp(last, f"ladder_{k}") - empty_fp for k in (1, 2, 4)] + [fp(last, "peak_8") - empty_fp],
        "marginal_first": (fp(first, "peak_8") - fp(first, "ladder_1")) / 7,
        "marginal_final": (fp(last, "peak_8") - fp(last, "ladder_1")) / 7,
        "post_partial_final": fp(last, "post_partial_close") - empty_fp,
        "reopen_cost_first": fp(first, "refilled_8") - fp(first, "post_partial_close"),
        "reopen_cost_final": fp(last, "refilled_8") - fp(last, "post_partial_close"),
        "retained_by_round": post_all,
        "retained_final": post_all[last],
        "rss_post_all_close_final": rounds[last]["stages"]["post_all_close"]["resident_bytes"],
        "libmalloc_in_use_post_all_close_final": rounds[last]["stages"]["post_all_close"]["libmalloc_in_use"],
        "libmalloc_allocated_post_all_close_final": rounds[last]["stages"]["post_all_close"]["libmalloc_allocated"],
        "slope_bytes_per_round": slope([(r, fp(r, "post_all_close")) for r in range(len(rounds)) if r >= 4] or [(r, fp(r, "post_all_close")) for r in range(len(rounds))]),
        "late_growth": fp(last, "post_all_close") - fp(quarter, "post_all_close"),
        "interior_hole_peak_final": rounds[last]["stages"]["peak_8"]["arena_high_water"] - rounds[last]["stages"]["peak_8"]["arena_used"],
        "opened_total": run["opened_total"],
        "arenas_unmapped_final": rounds[last]["stages"]["post_all_close"]["arenas_unmapped"],
        "violations": run["violations"],
    }


def summarize(values):
    return {"values": values, "median": statistics.median(values), "minimum": min(values), "maximum": max(values)}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--binary", required=True)
    parser.add_argument("--allocators", default="system,arena")
    parser.add_argument("--repetitions", type=int, default=RULES["measured_runs"])
    parser.add_argument("--rounds", type=int, default=RULES["rounds"], help="smoke runs only; the receipt records deviations from RULES")
    parser.add_argument("--receipt")
    args = parser.parse_args()
    RULES["rounds"] = args.rounds
    binary = Path(args.binary)
    binary_sha = hashlib.sha256(binary.read_bytes()).hexdigest()
    network = RETENTION.load_network_module()
    server = network.Server(("127.0.0.1", 0), network.Handler)
    origin = f"http://127.0.0.1:{server.server_address[1]}"
    threading.Thread(target=server.serve_forever, daemon=True).start()
    allocators = [a for a in args.allocators.split(",") if a]
    cells = {}
    try:
        for allocator in allocators:
            run_once(str(binary), allocator, origin)
            runs = [derive(run_once(str(binary), allocator, origin)) for _ in range(args.repetitions)]
            keys = ["peak_first", "peak_final", "peak_rss_final", "peak_virtual_final", "peak_arena_reserved_final",
                    "peak_arena_high_water_final", "peak_arena_used_final", "peak_arena_blocks_final", "peak_realm_bytes_final",
                    "peak_libmalloc_in_use_final", "marginal_first", "marginal_final", "post_partial_final", "reopen_cost_first",
                    "reopen_cost_final", "retained_final", "rss_post_all_close_final", "libmalloc_in_use_post_all_close_final",
                    "libmalloc_allocated_post_all_close_final", "slope_bytes_per_round", "late_growth", "interior_hole_peak_final",
                    "empty_virtual"]
            cells[allocator] = {
                "allocator": allocator,
                "runs": runs,
                **{key: summarize([round(r[key], 1) if isinstance(r[key], float) else r[key] for r in runs]) for key in keys},
                "ladder_first_median": [statistics.median(r["ladder_first"][i] for r in runs) for i in range(4)],
                "ladder_final_median": [statistics.median(r["ladder_final"][i] for r in runs) for i in range(4)],
                "retained_by_round_median": [statistics.median(r["retained_by_round"][i] for r in runs) for i in range(RULES["rounds"])],
                "violation_runs": sum(1 for r in runs if r["violations"]),
                "violations": [v for r in runs for v in r["violations"]],
            }
    finally:
        server.shutdown()
        server.server_close()
    capacity = {}
    for allocator in allocators:
        RETENTION.capacity_once(str(binary), allocator, RULES["settle_ms"])
        runs = [RETENTION.capacity_once(str(binary), allocator, RULES["settle_ms"]) for _ in range(args.repetitions)]
        capacity[allocator] = {"ratio_of_hard_cap": summarize([round(r["ratio_of_hard_cap"], 4) for r in runs])}

    evaluation = {}
    if "system" in allocators and "arena" in allocators:
        s, a = cells["system"], cells["arena"]
        k1 = a["peak_first"]["median"] <= 1.10 * s["peak_first"]["median"] and a["peak_final"]["median"] <= 1.10 * s["peak_final"]["median"]
        k2 = (a["marginal_final"]["median"] <= 1_048_576
              and a["marginal_final"]["median"] <= 1.25 * a["marginal_first"]["median"]
              and a["marginal_final"]["median"] <= 1.10 * s["marginal_first"]["median"])
        partial_ok = all(v["reason"] not in ("owners_differ_from_survivors", "arenas_differ_from_survivors", "arena_count_differs") for c in cells.values() for v in c["violations"])
        survivors_ok = all(v["reason"] != "survivor_state_changed" for c in cells.values() for v in c["violations"])
        all_close_ok = all(v["reason"] not in ("owner_or_arena_survived", "mapping_count_differs") for c in cells.values() for v in c["violations"])
        k6 = a["slope_bytes_per_round"]["median"] <= 8192 and a["late_growth"]["median"] <= 524288 and a["slope_bytes_per_round"]["median"] <= s["slope_bytes_per_round"]["median"] + 2048
        test = RETENTION.mann_whitney_exact(s["retained_final"]["values"], a["retained_final"]["values"])
        k7 = a["retained_final"]["median"] < s["retained_final"]["median"] and test["p_two_sided"] < 0.05
        k8 = capacity["arena"]["ratio_of_hard_cap"]["median"] >= 0.90 * capacity["system"]["ratio_of_hard_cap"]["median"]
        hole = a["interior_hole_peak_final"]["median"]
        used = a["peak_arena_used_final"]["median"]
        evaluation = {
            "K1_peak_live": k1, "K2_marginal": k2, "K3_partial_close_exact": partial_ok, "K4_survivors": survivors_ok,
            "K5_all_close_zero": all_close_ok, "K6_slope_plateau": k6, "K7_retained_final": k7, "K8_capacity": k8,
            "K9_journeys": "recorded outside this script",
            "concurrent_court_eligible_K1_to_K8": all([k1, k2, partial_ok, survivors_ok, all_close_ok, k6, k7, k8]),
            "retained_final_test": test,
            "interior_hole_signal": {"summed_high_water_minus_used_at_peak": hole, "summed_used_at_peak": used,
                                     "exceeds_25_percent_and_1_mib": bool(hole > 0.25 * used and hole > 1_048_576),
                                     "decision": "interior trimming stays deferred" if not (hole > 0.25 * used and hole > 1_048_576) else "interior trimming needs a safety design and a benefit threshold before any implementation"},
            "virtual_reservation": {"arena_reserved_at_peak": a["peak_arena_reserved_final"]["median"],
                                    "arena_touched_at_peak": a["peak_arena_high_water_final"]["median"],
                                    "host_virtual_at_peak_arena": a["peak_virtual_final"]["median"],
                                    "host_virtual_at_peak_default": s["peak_virtual_final"]["median"],
                                    "host_virtual_empty_arena": a["empty_virtual"]["median"],
                                    "host_virtual_empty_default": s["empty_virtual"]["median"]},
        }
    receipt = {
        "schema": "minicon-surf.native-dom-arena-concurrent-soak-receipt/0.0.1",
        "status": "observed",
        "technology": "native-dom",
        "technology_version": "0.0.2",
        "host_sha256": binary_sha,
        "platform": {"os": "macos", "architecture": "arm64"},
        "rules": RULES,
        "criteria": CRITERIA,
        "measurement": {
            "semantic": "one host process per run; 32 rounds of ladder 1/2/4/8 mixed targets, use, interleaved partial close, survivor check, refill, all-close; physical footprint, RSS and virtual size from outside, owners and allocator statistics from inside",
            "cells": cells,
            "capacity_at_first_oom": capacity,
        },
        "evaluation": evaluation,
        "limitations": [
            "one macOS arm64 machine; eight targets; two page workloads; no layout, images or storage",
            "virtual size is the host's whole address space as ps reports it; the arena's reservations are the dominant difference between arms",
            "the default allocator's retention is libmalloc reservation; leak absence is not claimed for any arm",
            "the arena is a macOS mmap prototype; nothing here is a cross-platform result",
        ],
    }
    encoded = json.dumps(receipt, indent=2, sort_keys=True, default=str) + "\n"
    if args.receipt:
        Path(args.receipt).write_text(encoded, encoding="utf-8")
    print(json.dumps({k: v for k, v in evaluation.items() if k not in ("retained_final_test",)}, indent=1, default=str))
    for key, cell in cells.items():
        print(key, "peak first/final", cell["peak_first"]["median"], cell["peak_final"]["median"], "marginal", cell["marginal_first"]["median"], cell["marginal_final"]["median"],
              "partial", cell["post_partial_final"]["median"], "reopen", cell["reopen_cost_first"]["median"], cell["reopen_cost_final"]["median"],
              "retained", cell["retained_final"]["median"], "slope", cell["slope_bytes_per_round"]["median"], "late", cell["late_growth"]["median"],
              "rss peak/post", cell["peak_rss_final"]["median"], cell["rss_post_all_close_final"]["median"], "virt", cell["peak_virtual_final"]["median"],
              "violations", cell["violation_runs"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
