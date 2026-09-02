#!/usr/bin/env python3
"""Attribute Servo W3 per-cycle system-heap growth to allocation-site families.

Runs the W3 runtime under MallocStackLogging for a low and a high cycle count,
takes `malloc_history -allBySize` at the settled empty and post-all-closes
stages, groups live allocations by primitive (libmalloc, mmap, thread, GPU)
and by owner family, and reports the per-cycle growth of every family.
"""

import argparse
import hashlib
import json
import os
import platform
import re
import statistics
import subprocess
import tempfile
import time
from pathlib import Path


ENTRY = re.compile(r"^(\d+) calls? for (\d+) bytes: (.*)$", re.M)
RUST_CRATE = re.compile(r"Cs[0-9A-Za-z]+_(\d+)([A-Za-z_][A-Za-z0-9_]*)")
SNAPSHOT_STAGES = ("empty", "post_all_closes")

PRIMITIVES = (
    ("libmalloc", re.compile(r"libsystem_malloc")),
    ("mmap", re.compile(r"\bmmap\b|mach_vm_map|mach_vm_allocate|vm_allocate|vm_remap")),
    ("thread", re.compile(r"_pthread_create")),
    ("gpu", re.compile(r"IOGPU|IOSurface|IOAccel")),
)
ALLOCATOR_FRAME = re.compile(
    r"libsystem_malloc|operator_new_impl|operator new|libc\+\+abi|_objc_rootAllocWithZone|"
    r"libsystem_kernel|_rjem_|os_pages_map|pac_alloc|arena_slab_alloc|base_block_alloc|"
    r"base_alloc|extent_alloc|large_palloc|arena_malloc|tcache_alloc|"
    r"_5alloc\b|_4core\b|_3std\b|__rust_alloc|allocator_api|hashbrown"
)
GENERIC_FAMILY = ("objc-foundation", "thread-tls")
FAMILIES = (
    ("spidermonkey", re.compile(r"\bjs::|\bJS::|\bJS_|\bglue::|mozilla::|\bmozjs\b|jsglue")),
    ("apple-gl-metal", re.compile(
        r"AppleMetalOpenGLRenderer|GLEngine|com\.apple\.opengl|com\.apple\.Metal|IOGPU|"
        r"AGXMetal|IOSurface|libGFXShared|SkyLight|CoreVideo|IOAccel|\bGLD|\bgld|\bgle|\bgli"
    )),
    ("sqlite", re.compile(r"sqlite3|rusqlite")),
    ("aws-lc", re.compile(r"aws_lc|jent_")),
    ("fonts-text", re.compile(r"\bFT_|\bhb_|CoreText|FontParser|freetype|harfbuzz|libFont")),
    ("thread-tls", re.compile(r"_pthread|ThreadLocalVariables|_tlv_|libsystem_pthread|libdyld")),
    ("objc-foundation", re.compile(r"libobjc|CoreFoundation|Foundation|libdispatch")),
)
IMAGE = re.compile(r"^\(([^)]+)\)")


def rust_crate(frame):
    match = RUST_CRATE.search(frame)
    if not match:
        return None
    length = int(match.group(1))
    name = match.group(2)[:length]
    return name if len(name) == length else None


def classify(frames):
    """Return (primitive, family, owner_frames) for one malloc_history stack."""
    primitive = "other"
    for name, pattern in PRIMITIVES:
        if pattern.search(frames[-1]):
            primitive = name
            break
    owner = []
    family = None
    fallback = None
    for frame in reversed(frames):
        if ALLOCATOR_FRAME.search(frame):
            continue
        owner.append(frame)
        if family is None:
            candidate = None
            for name, pattern in FAMILIES:
                if pattern.search(frame):
                    candidate = name
                    break
            if candidate is None:
                crate = rust_crate(frame)
                if crate:
                    candidate = f"rust:{crate}"
                elif frame.startswith("(") and "servo-w3-runtime" not in frame:
                    candidate = f"image:{IMAGE.match(frame).group(1)}"
            # Generic runtime layers (dispatch, ObjC, TLS, libc++) name the
            # caller that sits above them; keep them only as a fallback.
            if candidate is not None and (
                candidate in GENERIC_FAMILY or candidate.startswith("image:lib")
            ):
                fallback = fallback or candidate
            elif candidate is not None:
                family = candidate
        if len(owner) >= 8:
            break
    return primitive, family or fallback or "unclassified", owner


def strip_frame(frame):
    return re.sub(r"^0x[0-9a-f]+ ", "", frame.strip())[:160]


def parse_history(text):
    """Aggregate bytes by (primitive, family) and keep every stack's bytes."""
    totals = {}
    stacks = {}
    for match in ENTRY.finditer(text):
        count, size = int(match.group(1)), int(match.group(2))
        frames = [strip_frame(frame) for frame in match.group(3).split(" | ")]
        primitive, family, owner = classify(frames)
        key = (primitive, family)
        totals[key] = totals.get(key, 0) + size
        stack_key = (primitive, family, " < ".join(owner[:4]))
        stacks[stack_key] = stacks.get(stack_key, 0) + size
    return totals, stacks


def snapshot(pid):
    result = subprocess.run(
        ["malloc_history", str(pid), "-allBySize"], capture_output=True, text=True
    )
    if result.returncode != 0 or "calls for" not in result.stdout and "call for" not in result.stdout:
        raise RuntimeError("malloc_history did not produce an allocation report")
    return parse_history(result.stdout)


def run_once(binary, fixture, cycles, settle_ms, stage_ms):
    env = dict(os.environ, MallocStackLogging="1", MallocStackLoggingNoCompact="1")
    with tempfile.TemporaryDirectory(prefix="minicon-surf-servo-owner-") as directory:
        process = subprocess.Popen(
            [binary, fixture, str(Path(directory) / "config"), str(stage_ms), str(cycles),
             "rss-control"],
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            text=True, env=env,
        )
        snapshots = {}
        try:
            while True:
                line = process.stdout.readline()
                if not line:
                    raise RuntimeError("Servo exited before the last snapshot")
                marker = json.loads(line)
                stage = marker.get("stage")
                if stage in SNAPSHOT_STAGES:
                    time.sleep(settle_ms / 1000.0)
                    snapshots[stage] = snapshot(process.pid)
                end = marker.get("stage_end")
                if end in SNAPSHOT_STAGES:
                    snapshots[end] = (*snapshots[end], marker["allocators"]["libmalloc"])
                if end == "post_action":
                    break
            if process.wait(timeout=15) != 0:
                raise RuntimeError("Servo W3 runtime exited with failure")
            return snapshots
        finally:
            if process.poll() is None:
                process.kill()
                process.wait()


def median_int(values):
    return int(statistics.median(values))


def summarize(values):
    return {"values": values, "median": median_int(values), "minimum": min(values),
            "maximum": max(values)}


def family_table(low_runs, high_runs, low_cycles, high_cycles, primitive):
    """Per-cycle growth by family between the two cycle counts, run-paired."""
    span = high_cycles - low_cycles
    keys = set()
    for run in low_runs + high_runs:
        keys.update(k for k in run["post_all_closes"][0] if k[0] == primitive)
    rows = {}
    per_run_total = []
    for low, high in zip(low_runs, high_runs):
        total = 0
        for key in keys:
            total += high["post_all_closes"][0].get(key, 0) - low["post_all_closes"][0].get(key, 0)
        per_run_total.append(total)
    for key in sorted(keys):
        growth = [
            (high["post_all_closes"][0].get(key, 0) - low["post_all_closes"][0].get(key, 0)) / span
            for low, high in zip(low_runs, high_runs)
        ]
        shares = [
            (g * span / t) if t else None for g, t in zip(growth, per_run_total)
        ]
        rows[key[1]] = {
            "per_cycle_bytes": summarize([int(g) for g in growth]),
            "share_of_primitive_growth": shares,
            "median_share": (
                statistics.median([s for s in shares if s is not None])
                if any(s is not None for s in shares) else None
            ),
            "warm_up_bytes": summarize([
                run["post_all_closes"][0].get(key, 0) - run["empty"][0].get(key, 0)
                for run in low_runs
            ]),
        }
    return {
        "primitive": primitive,
        "per_cycle_total_bytes": summarize([int(t / span) for t in per_run_total]),
        "families": dict(sorted(rows.items(), key=lambda item: -item[1]["per_cycle_bytes"]["median"])),
    }


def top_growing_stacks(low_runs, high_runs, low_cycles, high_cycles, limit=20):
    span = high_cycles - low_cycles
    keys = set()
    for run in low_runs + high_runs:
        keys.update(run["post_all_closes"][1])
    rows = []
    for key in keys:
        growth = [
            (high["post_all_closes"][1].get(key, 0) - low["post_all_closes"][1].get(key, 0)) / span
            for low, high in zip(low_runs, high_runs)
        ]
        rows.append((median_int(growth), key, growth))
    rows.sort(key=lambda row: -row[0])
    return [
        {
            "primitive": key[0], "family": key[1], "owner_frames": key[2],
            "median_per_cycle_bytes": median, "per_cycle_bytes": [int(g) for g in growth],
        }
        for median, key, growth in rows[:limit] if median > 0
    ]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--binary", required=True)
    parser.add_argument("--fixture", required=True)
    parser.add_argument("--repetitions", type=int, default=7)
    parser.add_argument("--low-cycles", type=int, default=1)
    parser.add_argument("--high-cycles", type=int, default=17)
    parser.add_argument("--settle-ms", type=int, default=3500)
    parser.add_argument("--stage-ms", type=int, default=7000)
    parser.add_argument("--receipt")
    args = parser.parse_args()
    if platform.system() != "Darwin" or platform.machine() != "arm64":
        parser.error("this court requires macOS arm64")
    if args.high_cycles <= args.low_cycles or args.settle_ms + 2500 >= args.stage_ms:
        parser.error("high cycles must exceed low cycles and stage must exceed settle plus tool time")

    binary = Path(args.binary)
    fixture = Path(args.fixture)
    binary_sha = hashlib.sha256(binary.read_bytes()).hexdigest()
    cells = {}
    for cycles in (args.low_cycles, args.high_cycles):
        run_once(str(binary), str(fixture), cycles, args.settle_ms, args.stage_ms)
        cells[cycles] = [
            run_once(str(binary), str(fixture), cycles, args.settle_ms, args.stage_ms)
            for _ in range(args.repetitions)
        ]
    if hashlib.sha256(binary.read_bytes()).hexdigest() != binary_sha:
        raise RuntimeError("Servo binary changed during the measured court")
    low_runs, high_runs = cells[args.low_cycles], cells[args.high_cycles]

    tables = {
        primitive: family_table(low_runs, high_runs, args.low_cycles, args.high_cycles, primitive)
        for primitive in ("libmalloc", "mmap", "thread", "gpu", "other")
    }
    libmalloc = tables["libmalloc"]
    leading = next(iter(libmalloc["families"].items()), None)
    gate = {
        "criterion": "leading libmalloc family holds at least 0.70 of per-cycle libmalloc growth in every run pair",
        "leading_family": leading[0] if leading else None,
        "shares": leading[1]["share_of_primitive_growth"] if leading else [],
        "passed": bool(leading) and all(
            share is not None and share >= 0.70 for share in leading[1]["share_of_primitive_growth"]
        ),
    }
    tracked_versus_zone = [
        {
            "cycles": cycles,
            "tracked_libmalloc_bytes": summarize([
                sum(v for k, v in run["post_all_closes"][0].items() if k[0] == "libmalloc")
                for run in runs
            ]),
            "malloc_zone_size_in_use": summarize([
                run["post_all_closes"][2]["size_in_use"] for run in runs
            ]),
        }
        for cycles, runs in cells.items()
    ]
    receipt = {
        "schema": "minicon-surf.servo-libmalloc-growth-owner-receipt/0.0.1",
        "status": "incomplete",
        "technology": "servo",
        "technology_version": "0.5.0",
        "crate_sha256": "331e15df72165ca15b3945970c6870c4b7367be116ded058fda4f41190b265b8",
        "binary_sha256": binary_sha,
        "platform": {"os": "macos", "architecture": "arm64"},
        "workload": {
            "id": "W3",
            "fixture_sha256": hashlib.sha256(fixture.read_bytes()).hexdigest(),
            "engine_lifecycle": "one Servo instance; N sequential WebView build/drop cycles under MallocStackLogging",
            "cells": [{"cycles": args.low_cycles}, {"cycles": args.high_cycles}],
            "warmups_per_cell": 1,
            "measured_repetitions_per_cell": args.repetitions,
            "snapshot_stages": list(SNAPSHOT_STAGES),
            "settle_ms": args.settle_ms,
            "stage_ms": args.stage_ms,
        },
        "measurement": {
            "semantic": {
                "tool": "malloc_history -allBySize on the live process; live allocations grouped by allocation primitive and by the innermost non-allocator frame's family",
                "per_cycle": "(high-cycle bytes - low-cycle bytes) / (high cycles - low cycles), paired by run index",
                "warm_up": "post_all_closes minus empty in the low-cycle cell",
                "families": [name for name, _ in FAMILIES] + ["rust:<crate>", "image:<library>", "unclassified"],
            },
            "growth_by_primitive_and_family": tables,
            "top_growing_stacks": top_growing_stacks(low_runs, high_runs, args.low_cycles, args.high_cycles),
            "tracked_versus_zone_statistics": tracked_versus_zone,
            "gate": gate,
        },
        "limitations": [
            "MallocStackLogging records libmalloc and VM allocations; Rust allocations reach it only as jemalloc extent mmaps",
            "family classification is regex-based on symbol names; unclassified and image:* rows are reported rather than guessed",
            "malloc_history itself perturbs the process and the settled state is sampled once per stage",
            "one synthetic data URL, one operating system, one architecture and one Servo release",
            "run pairing across cells is by index, not by shared process",
        ],
    }
    encoded = json.dumps(receipt, indent=2, sort_keys=True) + "\n"
    if args.receipt:
        Path(args.receipt).write_text(encoded, encoding="utf-8")
    print(encoded, end="")


if __name__ == "__main__":
    main()
