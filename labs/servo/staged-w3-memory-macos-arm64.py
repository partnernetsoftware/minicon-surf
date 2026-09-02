#!/usr/bin/env python3
"""Servo W3 attribution-closure and per-cycle-slope court on macOS arm64.

One long-lived engine instance builds, semantically verifies and drops N
WebViews. Every stage samples complete-tree RSS and Apple physical footprint
from outside the process while the runtime reports jemalloc and libmalloc
statistics from inside it. Control cells vary N to separate one-time warm-up
from per-cycle growth; pressure cells compare allocator recovery actions.
"""

import argparse
import ctypes
import ctypes.util
import hashlib
import json
import platform
import statistics
import subprocess
import tempfile
import time
from pathlib import Path


STAGES = (
    "empty", "one_target", "post_one_close", "last_target",
    "post_all_closes", "post_action",
)
ACTIONS = {
    "control": "control_wait",
    "jemalloc-purge": "jemalloc_all_arenas_purge",
    "libmalloc-relief": "libmalloc_zone_pressure_relief",
    "both": "jemalloc_purge_then_libmalloc_relief",
}
JEMALLOC_KEYS = ("allocated", "active", "metadata", "resident", "mapped", "retained")
LIBMALLOC_KEYS = ("blocks_in_use", "size_in_use", "max_size_in_use", "size_allocated")
RUSAGE_INFO_V4 = 4


def stage_order(cycles):
    """Emission order: with one cycle the live windows precede both close windows."""
    if cycles == 1:
        return ("empty", "one_target", "last_target", "post_one_close",
                "post_all_closes", "post_action")
    return STAGES


class RusageInfoV4(ctypes.Structure):
    _fields_ = [
        ("ri_uuid", ctypes.c_uint8 * 16),
        ("ri_user_time", ctypes.c_uint64),
        ("ri_system_time", ctypes.c_uint64),
        ("ri_pkg_idle_wkups", ctypes.c_uint64),
        ("ri_interrupt_wkups", ctypes.c_uint64),
        ("ri_pageins", ctypes.c_uint64),
        ("ri_wired_size", ctypes.c_uint64),
        ("ri_resident_size", ctypes.c_uint64),
        ("ri_phys_footprint", ctypes.c_uint64),
        ("ri_proc_start_abstime", ctypes.c_uint64),
        ("ri_proc_exit_abstime", ctypes.c_uint64),
        ("ri_child_user_time", ctypes.c_uint64),
        ("ri_child_system_time", ctypes.c_uint64),
        ("ri_child_pkg_idle_wkups", ctypes.c_uint64),
        ("ri_child_interrupt_wkups", ctypes.c_uint64),
        ("ri_child_pageins", ctypes.c_uint64),
        ("ri_child_elapsed_abstime", ctypes.c_uint64),
        ("ri_diskio_bytesread", ctypes.c_uint64),
        ("ri_diskio_byteswritten", ctypes.c_uint64),
        ("ri_cpu_time_qos_default", ctypes.c_uint64),
        ("ri_cpu_time_qos_maintenance", ctypes.c_uint64),
        ("ri_cpu_time_qos_background", ctypes.c_uint64),
        ("ri_cpu_time_qos_utility", ctypes.c_uint64),
        ("ri_cpu_time_qos_legacy", ctypes.c_uint64),
        ("ri_cpu_time_qos_user_initiated", ctypes.c_uint64),
        ("ri_cpu_time_qos_user_interactive", ctypes.c_uint64),
        ("ri_billed_system_time", ctypes.c_uint64),
        ("ri_serviced_system_time", ctypes.c_uint64),
        ("ri_logical_writes", ctypes.c_uint64),
        ("ri_lifetime_max_phys_footprint", ctypes.c_uint64),
        ("ri_instructions", ctypes.c_uint64),
        ("ri_cycles", ctypes.c_uint64),
        ("ri_billed_energy", ctypes.c_uint64),
        ("ri_serviced_energy", ctypes.c_uint64),
        ("ri_interval_max_phys_footprint", ctypes.c_uint64),
        ("ri_runnable_time", ctypes.c_uint64),
    ]


_LIBPROC = ctypes.CDLL(ctypes.util.find_library("proc"))
_LIBPROC.proc_pid_rusage.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_void_p]
_LIBPROC.proc_pid_rusage.restype = ctypes.c_int


def physical_footprint(pid):
    """Return (current, lifetime maximum) physical footprint in bytes."""
    info = RusageInfoV4()
    if _LIBPROC.proc_pid_rusage(pid, RUSAGE_INFO_V4, ctypes.byref(info)) != 0:
        raise RuntimeError("proc_pid_rusage failed for a court process")
    return int(info.ri_phys_footprint), int(info.ri_lifetime_max_phys_footprint)


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


def thread_count(pid):
    output = subprocess.run(
        ["ps", "-M", "-p", str(pid)], check=True, capture_output=True, text=True
    ).stdout
    return max(len(output.splitlines()) - 1, 0)


def launch(binary, fixture, stage_ms, cycles, mode, directory):
    return subprocess.Popen(
        [binary, fixture, str(Path(directory) / "config"), str(stage_ms), str(cycles), mode],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
    )


def read_json_line(process, what):
    line = process.stdout.readline()
    if not line:
        raise RuntimeError(f"Servo exited before {what}")
    return json.loads(line)


def check_action(marker, stage, action):
    reported = marker.get("action", {})
    if stage != "post_action":
        if reported.get("name") != "none":
            raise RuntimeError(f"unexpected action before post_action: {marker}")
        return
    if reported.get("name") != ACTIONS[action]:
        raise RuntimeError(f"unexpected action name: {marker}")
    if action in ("jemalloc-purge", "both") and reported.get("jemalloc_purge_result_code") != 0:
        raise RuntimeError(f"jemalloc purge did not succeed: {marker}")
    if action in ("libmalloc-relief", "both") and not isinstance(
        reported.get("libmalloc_released_bytes"), int
    ):
        raise RuntimeError(f"libmalloc relief did not report bytes: {marker}")


def check_allocators(end):
    allocators = end.get("allocators", {})
    jemalloc = allocators.get("jemalloc", {})
    libmalloc = allocators.get("libmalloc", {})
    for key in JEMALLOC_KEYS:
        if not isinstance(jemalloc.get(key), int):
            raise RuntimeError(f"jemalloc statistic {key} missing: {end}")
    for key in LIBMALLOC_KEYS:
        if not isinstance(libmalloc.get(key), int):
            raise RuntimeError(f"libmalloc statistic {key} missing: {end}")
    return allocators


def run_once(binary, fixture, cycles, action, report, samples, settle_ms, stage_ms):
    with tempfile.TemporaryDirectory(prefix="minicon-surf-servo-w3-") as directory:
        process = launch(binary, fixture, stage_ms, cycles, f"{report}-{action}", directory)
        states = {}
        try:
            for stage in stage_order(cycles):
                marker = read_json_line(process, stage)
                if marker.get("stage") != stage:
                    raise RuntimeError(f"unexpected Servo stage marker: {marker}")
                check_action(marker, stage, action)
                state = {}
                if report == "rss":
                    time.sleep(settle_ms / 1000.0)
                    rss_values, footprint_values, lifetime_values, process_counts = [], [], [], []
                    for _ in range(samples):
                        tree = process_tree(process.pid)
                        if not tree:
                            raise RuntimeError("Servo process tree disappeared during measurement")
                        footprints = [physical_footprint(pid) for pid, _ in tree]
                        rss_values.append(sum(rss_kib * 1024 for _, rss_kib in tree))
                        footprint_values.append(sum(current for current, _ in footprints))
                        lifetime_values.append(sum(maximum for _, maximum in footprints))
                        process_counts.append(len(tree))
                        time.sleep(0.02)
                    state = {
                        "peak_tree_resident_bytes": max(rss_values),
                        "peak_tree_physical_footprint_bytes": max(footprint_values),
                        "lifetime_max_physical_footprint_bytes": max(lifetime_values),
                        "peak_process_count": max(process_counts),
                        "thread_count": thread_count(process.pid),
                    }
                end = read_json_line(process, f"{stage} end")
                if end.get("stage_end") != stage:
                    raise RuntimeError(f"unexpected Servo stage end: {end}")
                state["allocators"] = check_allocators(end)
                if stage == "post_action":
                    state["action"] = marker["action"]
                if report == "internal":
                    if "internal_memory" not in end:
                        raise RuntimeError(f"internal report missing: {end}")
                    state["internal_memory"] = end["internal_memory"]
                states[stage] = state
            if process.wait(timeout=15) != 0:
                raise RuntimeError("Servo W3 runtime exited with failure")
            return states
        finally:
            if process.poll() is None:
                process.kill()
                process.wait()


def median_int(values):
    return int(statistics.median(values))


def series(runs, stage, getter):
    return [getter(run[stage]) for run in runs]


def summarize(values):
    return {"values": values, "median": median_int(values), "maximum": max(values)}


def delta(runs, later, earlier, getter):
    return summarize([getter(run[later]) - getter(run[earlier]) for run in runs])


EXTERNAL_METRICS = {
    "resident": lambda state: state["peak_tree_resident_bytes"],
    "physical_footprint": lambda state: state["peak_tree_physical_footprint_bytes"],
    "lifetime_max_physical_footprint": lambda state: state["lifetime_max_physical_footprint_bytes"],
    "threads": lambda state: state["thread_count"],
}
ALLOCATOR_METRICS = {
    f"jemalloc_{key}": (lambda key: lambda state: state["allocators"]["jemalloc"][key])(key)
    for key in JEMALLOC_KEYS
} | {
    f"libmalloc_{key}": (lambda key: lambda state: state["allocators"]["libmalloc"][key])(key)
    for key in LIBMALLOC_KEYS
}


def aggregate_rss_cell(runs):
    metrics = EXTERNAL_METRICS | ALLOCATOR_METRICS
    states = {
        stage: {
            **{name: summarize(series(runs, stage, getter)) for name, getter in metrics.items()},
            "peak_process_count": max(run[stage]["peak_process_count"] for run in runs),
        }
        for stage in STAGES
    }
    deltas = {}
    for name, getter in metrics.items():
        deltas[name] = {
            "one_target_minus_empty": delta(runs, "one_target", "empty", getter),
            "post_one_close_minus_empty": delta(runs, "post_one_close", "empty", getter),
            "post_all_closes_minus_post_one_close": delta(
                runs, "post_all_closes", "post_one_close", getter
            ),
            "post_all_closes_minus_empty": delta(runs, "post_all_closes", "empty", getter),
            "post_action_minus_post_all_closes": delta(
                runs, "post_action", "post_all_closes", getter
            ),
            "post_action_minus_empty": delta(runs, "post_action", "empty", getter),
        }
    closure = {}
    for external in ("resident", "physical_footprint"):
        for stage_pair in (("post_all_closes", "empty"), ("post_action", "empty")):
            later, earlier = stage_pair
            external_delta = [
                EXTERNAL_METRICS[external](run[later]) - EXTERNAL_METRICS[external](run[earlier])
                for run in runs
            ]
            owned = [
                (ALLOCATOR_METRICS["jemalloc_resident"](run[later])
                 - ALLOCATOR_METRICS["jemalloc_resident"](run[earlier]))
                + (ALLOCATOR_METRICS["libmalloc_size_allocated"](run[later])
                   - ALLOCATOR_METRICS["libmalloc_size_allocated"](run[earlier]))
                for run in runs
            ]
            ratios = [
                (owned_bytes / external_bytes) if external_bytes else None
                for owned_bytes, external_bytes in zip(owned, external_delta)
            ]
            closure[f"{external}:{later}_minus_{earlier}"] = {
                "external_delta_bytes": summarize(external_delta),
                "jemalloc_resident_plus_libmalloc_reserved_delta_bytes": summarize(owned),
                "owned_over_external_ratio": ratios,
                "median_owned_over_external_ratio": (
                    statistics.median([ratio for ratio in ratios if ratio is not None])
                    if any(ratio is not None for ratio in ratios) else None
                ),
            }
    action_reports = [run["post_action"]["action"] for run in runs]
    return {
        "states": states,
        "deltas": deltas,
        "attribution_closure": closure,
        "action_reports": action_reports,
    }


def aggregate_internal_cell(runs):
    stages = {}
    for stage in STAGES:
        rows = [run[stage]["internal_memory"] for run in runs]
        explicit = [row["explicit_reported_bytes"] for row in rows]
        kinds = sorted({kind for row in rows for kind in row["bytes_by_kind"]})
        paths = sorted({
            item["path"] for row in rows for item in row["largest_sanitized_explicit_path_prefixes"]
        })
        path_rows = [
            {item["path"]: item["bytes"] for item in row["largest_sanitized_explicit_path_prefixes"]}
            for row in rows
        ]
        non_explicit_paths = sorted({
            item["path"] for row in rows for item in row["sanitized_non_explicit_reports"]
        })
        non_explicit_rows = [
            {item["path"]: item["bytes"] for item in row["sanitized_non_explicit_reports"]}
            for row in rows
        ]
        stages[stage] = {
            "explicit_reported_bytes": summarize(explicit),
            "process_report_count": [row["process_report_count"] for row in rows],
            "report_count": [row["report_count"] for row in rows],
            "bytes_by_kind": {
                kind: summarize([row["bytes_by_kind"].get(kind, 0) for row in rows])
                for kind in kinds
            },
            "largest_sanitized_explicit_path_prefixes": {
                path: summarize([row.get(path, 0) for row in path_rows]) for path in paths
            },
            "sanitized_non_explicit_reports": {
                path: summarize([row.get(path, 0) for row in non_explicit_rows])
                for path in non_explicit_paths
            },
            "allocators": {
                name: summarize(series(runs, stage, getter))
                for name, getter in ALLOCATOR_METRICS.items()
            },
        }
    explicit = lambda state: state["internal_memory"]["explicit_reported_bytes"]
    return {
        "semantic": "Servo memory reporter; explicit kinds are summed, each non-explicit report remains separate because resident/vsize and other global measures may overlap",
        "states": stages,
        "post_all_closes_minus_empty_explicit_reported_bytes": delta(
            runs, "post_all_closes", "empty", explicit
        ),
        "post_action_minus_post_all_closes_explicit_reported_bytes": delta(
            runs, "post_action", "post_all_closes", explicit
        ),
        "post_action_minus_empty_explicit_reported_bytes": delta(
            runs, "post_action", "empty", explicit
        ),
    }


def least_squares(points):
    """Return (intercept, slope) for (x, y) points; None if fewer than two x values."""
    xs = [x for x, _ in points]
    if len(set(xs)) < 2:
        return None
    mean_x = statistics.fmean(xs)
    mean_y = statistics.fmean(y for _, y in points)
    denominator = sum((x - mean_x) ** 2 for x in xs)
    slope = sum((x - mean_x) * (y - mean_y) for x, y in points) / denominator
    return {"intercept_bytes": mean_y - slope * mean_x, "slope_bytes_per_cycle": slope}


def slope_summary(rss_cells):
    """Fit retained-above-empty against cycle count across the control cells."""
    result = {}
    control_cells = {
        cycles: cell for (cycles, action), cell in rss_cells.items() if action == "control"
    }
    if len(control_cells) < 2:
        return {"semantic": "fewer than two control cycle counts; no slope fitted"}
    for metric in ("resident", "physical_footprint", "jemalloc_resident",
                   "libmalloc_size_allocated", "libmalloc_size_in_use", "threads"):
        run_points = []
        median_points = []
        for cycles, cell in sorted(control_cells.items()):
            retained = cell["deltas"][metric]["post_all_closes_minus_empty"]
            run_points.extend((cycles, value) for value in retained["values"])
            median_points.append((cycles, retained["median"]))
        fit_runs = least_squares(run_points)
        fit_medians = least_squares(median_points)
        result[metric] = {
            "retained_median_by_cycles": {str(c): value for c, value in median_points},
            "least_squares_over_runs": fit_runs,
            "least_squares_over_medians": fit_medians,
        }
    result["semantic"] = (
        "retained = intercept + slope * cycles over control cells; intercept approximates "
        "one-time warm-up, slope approximates per-cycle accumulation"
    )
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--binary", required=True)
    parser.add_argument("--fixture", required=True)
    parser.add_argument("--repetitions", type=int, default=7)
    parser.add_argument("--samples-per-stage", type=int, default=3)
    parser.add_argument("--settle-ms", type=int, default=3000)
    parser.add_argument("--stage-ms", type=int, default=4000)
    parser.add_argument("--control-cycles", default="1,8,32",
                        help="comma-separated cycle counts for control cells")
    parser.add_argument("--pressure-cycles", type=int, default=8,
                        help="cycle count for the pressure-action cells")
    parser.add_argument("--actions", default="jemalloc-purge,libmalloc-relief,both")
    parser.add_argument("--receipt")
    args = parser.parse_args()
    if platform.system() != "Darwin" or platform.machine() != "arm64":
        parser.error("this court requires macOS arm64")
    if min(args.repetitions, args.samples_per_stage, args.settle_ms, args.stage_ms) <= 0:
        parser.error("repetitions, samples, settle time, and stage time must be positive")
    required_window = args.settle_ms + (args.samples_per_stage * 40)
    if required_window >= args.stage_ms:
        parser.error("stage time must exceed settle plus sampling window")
    control_cycles = sorted({int(value) for value in args.control_cycles.split(",")})
    actions = [action for action in args.actions.split(",") if action]
    for action in actions:
        if action not in ACTIONS or action == "control":
            parser.error(f"unknown pressure action {action}")

    fixture = Path(args.fixture)
    binary = Path(args.binary)
    binary_sha = hashlib.sha256(binary.read_bytes()).hexdigest()

    cells = [(cycles, "control") for cycles in control_cycles]
    cells.extend((args.pressure_cycles, action) for action in actions)
    if (args.pressure_cycles, "control") not in cells:
        cells.append((args.pressure_cycles, "control"))
    internal_cells = [(args.pressure_cycles, "control")] + [
        (args.pressure_cycles, action) for action in actions
    ]

    rss_cells = {}
    for cycles, action in cells:
        run_once(str(binary), str(fixture), cycles, action, "rss",
                 args.samples_per_stage, args.settle_ms, args.stage_ms)
        runs = [
            run_once(str(binary), str(fixture), cycles, action, "rss",
                     args.samples_per_stage, args.settle_ms, args.stage_ms)
            for _ in range(args.repetitions)
        ]
        rss_cells[(cycles, action)] = aggregate_rss_cell(runs)
    internal = {}
    for cycles, action in internal_cells:
        run_once(str(binary), str(fixture), cycles, action, "internal",
                 args.samples_per_stage, args.settle_ms, args.stage_ms)
        runs = [
            run_once(str(binary), str(fixture), cycles, action, "internal",
                     args.samples_per_stage, args.settle_ms, args.stage_ms)
            for _ in range(args.repetitions)
        ]
        internal[f"cycles={cycles},action={action}"] = aggregate_internal_cell(runs)
    if hashlib.sha256(binary.read_bytes()).hexdigest() != binary_sha:
        raise RuntimeError("Servo binary changed during the measured court")

    receipt = {
        "schema": "minicon-surf.servo-staged-w3-memory-receipt/0.0.4",
        "status": "incomplete",
        "technology": "servo",
        "technology_version": "0.5.0",
        "crate_sha256": "331e15df72165ca15b3945970c6870c4b7367be116ded058fda4f41190b265b8",
        "allocator": {
            "rust_global_allocator": "tikv-jemallocator 0.6.1 via servo-allocator; symbol prefix _rjem_, stats feature enabled by this lab",
            "jemalloc_crate_version": "0.6.1+5.3.0-1-ge13ca993e8ccb9ba9847cc330696e02839f328f7",
            "jemalloc_crate_sha256": "cd8aa5b2ab86a2cefa406d889139c162cbb230092f7d1d7cbc1716405d852a3b",
            "system_heap": "Apple libmalloc; SpiderMonkey (mozjs_sys --disable-jemalloc), swgl, FreeType and HarfBuzz allocate here",
            "jemalloc_purge_mallctl": "arena.4096.purge",
            "libmalloc_relief_call": "malloc_zone_pressure_relief(NULL, 0)",
        },
        "binary_sha256": binary_sha,
        "platform": {"os": "macos", "architecture": "arm64"},
        "workload": {
            "id": "W3",
            "fixture_sha256": hashlib.sha256(fixture.read_bytes()).hexdigest(),
            "transport": "percent-encoded-data-url",
            "rendering_context": "one shared SoftwareRenderingContext-800x600",
            "engine_lifecycle": "one Servo instance; N sequential WebView build/drop cycles",
            "close_semantic": "WebViewInner drop sends CloseWebView and removes its paint webview; each WebView is its own browsing-context group, so every cycle starts and exits one script thread",
            "stage_order": list(STAGES),
            "stage_note": "with one cycle, one_target/last_target and post_one_close/post_all_closes are consecutive windows over the same state",
            "cells": [{"cycles": cycles, "action": action} for cycles, action in cells],
            "internal_report_cells": [
                {"cycles": cycles, "action": action} for cycles, action in internal_cells
            ],
            "warmups_per_cell": 1,
            "measured_repetitions_per_cell": args.repetitions,
            "samples_per_stage": args.samples_per_stage,
            "settle_ms": args.settle_ms,
            "stage_ms": args.stage_ms,
            "profile": "fresh temporary config and temporary_storage per repetition",
        },
        "measurement": {
            "semantic": {
                "resident": "peak of sampled sum of BSD ps RSS over Servo root and recursively observed descendants per stage",
                "physical_footprint": "peak of sampled sum of proc_pid_rusage RUSAGE_INFO_V4 ri_phys_footprint over the same tree",
                "lifetime_max_physical_footprint": "sum of ri_lifetime_max_phys_footprint over the same tree; the kernel's own peak since process start, so it captures transient spikes between samples",
                "threads": "ps -M thread rows of the root process once per stage",
                "jemalloc": "mallctl stats.* after an epoch refresh, read in-process at the end of each stage window",
                "libmalloc": "malloc_zone_statistics(NULL) size_in_use/size_allocated across all zones, read in-process at the end of each stage window",
                "attribution_closure": "(delta jemalloc stats.resident + delta libmalloc size_allocated) / delta external measure",
                "process_separation": "rss and internal-report cells use separate fresh processes",
            },
            "rss_cells": {
                f"cycles={cycles},action={action}": cell
                for (cycles, action), cell in rss_cells.items()
            },
            "per_cycle_slope": slope_summary(rss_cells),
            "internal_reports": internal,
        },
        "agent_observation": {
            "interface": "direct Rust WebView evaluate_javascript callback",
            "semantic_expected_present_on_all_targets": True,
        },
        "limitations": [
            "summed RSS is neither private memory nor PSS and can double-count shared pages",
            "physical footprint is the kernel's per-process accounting and excludes pages already marked reusable",
            "each WebView close raises footprint transiently by roughly 200 MB of graphics-owned memory for about one second; the settle window is chosen to sample after that spike and the lifetime maximum records it",
            "process-table sampling can miss short-lived or reparented processes",
            "one synthetic data URL, one operating system and one architecture only",
            "software rendering is not a headed or GPU-offscreen comparison",
            "the rendering context is reused, so this isolates WebView churn rather than surface allocation churn",
            "direct Rust callbacks do not prove native CLI, stable semantic nodes or CDP compatibility",
            "no like-for-like external browser uses this direct embedding and software-rendering path",
            "allocator statistics are read at the end of each stage window, after the external samples",
            "jemalloc stats.resident and libmalloc size_allocated do not cover thread stacks, JIT code, GC chunks or other direct mmap owners",
            "the slope fit uses three cycle counts and assumes linear accumulation",
            "internal reports run in separate processes so reporter allocations do not contaminate RSS runs",
            "within internal-report runs, an earlier report may affect later report state",
            "sanitized path-prefix medians cover only each run's twelve largest explicit and non-explicit prefixes",
        ],
    }
    encoded = json.dumps(receipt, indent=2, sort_keys=True) + "\n"
    if args.receipt:
        Path(args.receipt).write_text(encoded, encoding="utf-8")
    print(encoded, end="")


if __name__ == "__main__":
    main()
