#!/usr/bin/env python3
"""Measure Servo W3 stages emitted by one long-lived engine instance."""

import argparse
import hashlib
import json
import platform
import statistics
import subprocess
import tempfile
import time
from pathlib import Path


STAGES = ("empty", "one_target", "post_one_close", "eighth_target", "post_eight_closes")


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


def run_once(binary, fixture, samples, settle_ms, stage_ms):
    with tempfile.TemporaryDirectory(prefix="minicon-surf-servo-w3-") as directory:
        process = subprocess.Popen(
            [binary, fixture, str(Path(directory) / "config"), str(stage_ms)],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        states = {}
        try:
            for expected_stage in STAGES:
                line = process.stdout.readline()
                if not line:
                    raise RuntimeError(f"Servo exited before {expected_stage}: {process.stderr.read()}")
                marker = json.loads(line)
                if marker != {"stage": expected_stage}:
                    raise RuntimeError(f"unexpected Servo stage marker: {marker}")
                time.sleep(settle_ms / 1000.0)
                rss_values = []
                process_counts = []
                for _ in range(samples):
                    tree = process_tree(process.pid)
                    if not tree:
                        raise RuntimeError("Servo process tree disappeared during measurement")
                    rss_values.append(sum(rss_kib * 1024 for _, rss_kib in tree))
                    process_counts.append(len(tree))
                    time.sleep(0.02)
                states[expected_stage] = {
                    "peak_tree_resident_bytes": max(rss_values),
                    "peak_process_count": max(process_counts),
                }
            if process.wait(timeout=15) != 0:
                raise RuntimeError(f"Servo W3 failed: {process.stderr.read()}")
            return states
        finally:
            if process.poll() is None:
                process.kill()
                process.wait()


def aggregate(runs):
    states = {}
    for stage in STAGES:
        state = {}
        for key in ("peak_tree_resident_bytes", "peak_process_count"):
            values = [run[stage][key] for run in runs]
            state[key] = values
            state[f"median_{key}"] = int(statistics.median(values))
            state[f"maximum_{key}"] = max(values)
        states[stage] = state
    first_delta = [
        run["one_target"]["peak_tree_resident_bytes"]
        - run["empty"]["peak_tree_resident_bytes"]
        for run in runs
    ]
    post_one_retained = [
        run["post_one_close"]["peak_tree_resident_bytes"]
        - run["empty"]["peak_tree_resident_bytes"]
        for run in runs
    ]
    eighth_delta = [
        run["eighth_target"]["peak_tree_resident_bytes"]
        - run["post_one_close"]["peak_tree_resident_bytes"]
        for run in runs
    ]
    retained = [
        run["post_eight_closes"]["peak_tree_resident_bytes"]
        - run["empty"]["peak_tree_resident_bytes"]
        for run in runs
    ]
    return {
        "states": states,
        "first_target_minus_empty_resident_bytes": first_delta,
        "median_first_target_minus_empty_resident_bytes": int(statistics.median(first_delta)),
        "post_one_close_minus_empty_resident_bytes": post_one_retained,
        "median_post_one_close_minus_empty_resident_bytes": int(statistics.median(post_one_retained)),
        "eighth_target_minus_post_one_close_resident_bytes": eighth_delta,
        "median_eighth_target_minus_post_one_close_resident_bytes": int(statistics.median(eighth_delta)),
        "post_eight_closes_minus_empty_resident_bytes": retained,
        "median_post_eight_closes_minus_empty_resident_bytes": int(statistics.median(retained)),
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--binary", required=True)
    parser.add_argument("--fixture", required=True)
    parser.add_argument("--repetitions", type=int, default=7)
    parser.add_argument("--samples-per-stage", type=int, default=3)
    parser.add_argument("--settle-ms", type=int, default=200)
    parser.add_argument("--stage-ms", type=int, default=1000)
    parser.add_argument("--receipt")
    args = parser.parse_args()
    if platform.system() != "Darwin" or platform.machine() != "arm64":
        parser.error("this court requires macOS arm64")
    if min(args.repetitions, args.samples_per_stage, args.settle_ms, args.stage_ms) <= 0:
        parser.error("repetitions, samples, settle time, and stage time must be positive")
    required_window = args.settle_ms + (args.samples_per_stage * 20)
    if required_window >= args.stage_ms:
        parser.error("stage time must exceed settle plus sampling window")

    fixture = Path(args.fixture)
    binary = Path(args.binary)
    binary_sha = hashlib.sha256(binary.read_bytes()).hexdigest()
    run_once(str(binary), str(fixture), args.samples_per_stage, args.settle_ms, args.stage_ms)
    runs = [
        run_once(str(binary), str(fixture), args.samples_per_stage, args.settle_ms, args.stage_ms)
        for _ in range(args.repetitions)
    ]
    if hashlib.sha256(binary.read_bytes()).hexdigest() != binary_sha:
        raise RuntimeError("Servo binary changed during the measured court")
    receipt = {
        "schema": "minicon-surf.servo-staged-w3-memory-receipt/0.0.1",
        "status": "incomplete",
        "technology": "servo",
        "technology_version": "0.5.0",
        "crate_sha256": "331e15df72165ca15b3945970c6870c4b7367be116ded058fda4f41190b265b8",
        "binary_sha256": binary_sha,
        "platform": {"os": "macos", "architecture": "arm64"},
        "workload": {
            "id": "W3",
            "fixture_sha256": hashlib.sha256(fixture.read_bytes()).hexdigest(),
            "transport": "percent-encoded-data-url",
            "rendering_context": "one shared SoftwareRenderingContext-800x600",
            "engine_lifecycle": "one Servo instance; eight sequential WebView build/drop cycles",
            "close_semantic": "WebViewInner drop sends CloseWebView and removes its paint webview",
            "stage_order": list(STAGES),
            "warmups": 1,
            "measured_repetitions": args.repetitions,
            "samples_per_stage": args.samples_per_stage,
            "settle_ms": args.settle_ms,
            "stage_ms": args.stage_ms,
            "profile": "fresh temporary config and temporary_storage per repetition",
        },
        "measurement": {
            "semantic": "peak of sampled sum of BSD ps RSS over Servo root and recursively observed descendants per stage",
            **aggregate(runs),
        },
        "agent_observation": {
            "interface": "direct Rust WebView evaluate_javascript callback",
            "semantic_expected_present_on_all_eight_targets": True,
        },
        "limitations": [
            "summed RSS is neither private memory nor PSS and can double-count shared pages",
            "process-table sampling can miss short-lived or reparented processes",
            "one synthetic data URL, one operating system and one architecture only",
            "software rendering is not a headed or GPU-offscreen comparison",
            "the rendering context is reused, so this isolates WebView churn rather than surface allocation churn",
            "direct Rust callbacks do not prove native CLI, stable semantic nodes or CDP compatibility",
            "no like-for-like external browser uses this direct embedding and software-rendering path",
            "no explicit allocator trim or memory-pressure signal is exercised",
        ],
    }
    encoded = json.dumps(receipt, indent=2, sort_keys=True) + "\n"
    if args.receipt:
        Path(args.receipt).write_text(encoded, encoding="utf-8")
    print(encoded, end="")


if __name__ == "__main__":
    main()
