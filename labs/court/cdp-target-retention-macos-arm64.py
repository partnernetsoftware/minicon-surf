#!/usr/bin/env python3
"""Same-server CDP target churn, capacity, and post-close retention court."""

import argparse
import hashlib
import importlib.util
import json
import pathlib
import platform
import statistics
import subprocess
import tempfile
import time
import urllib.parse


STAGES = ("empty", "one_target", "post_one_close", "eighth_target", "post_eight_closes")
TARGET_COUNT = 8


def load_cdp_support():
    path = pathlib.Path(__file__).with_name("cdp-live-target.py")
    spec = importlib.util.spec_from_file_location("minicon_surf_cdp_support", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


CDP_SUPPORT = load_cdp_support()


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


def sample_stage(process, samples, settle_ms):
    time.sleep(settle_ms / 1000.0)
    rss_values = []
    process_counts = []
    for _ in range(samples):
        tree = process_tree(process.pid)
        if not tree:
            raise RuntimeError("browser process tree disappeared during measurement")
        rss_values.append(sum(rss_kib * 1024 for _, rss_kib in tree))
        process_counts.append(len(tree))
        time.sleep(0.02)
    return {
        "peak_tree_resident_bytes": max(rss_values),
        "peak_process_count": max(process_counts),
    }


def open_ready_target(cdp, fixture_url):
    target_id = cdp.call("Target.createTarget", {"url": "about:blank"})["targetId"]
    session_id = cdp.call(
        "Target.attachToTarget", {"targetId": target_id, "flatten": True}
    )["sessionId"]
    cdp.call("Page.enable", session_id=session_id)
    cdp.call("Runtime.enable", session_id=session_id)
    cdp.call("Page.navigate", {"url": fixture_url}, session_id)
    expression = "document.querySelector('h1')?.textContent"
    deadline = time.monotonic() + 5.0
    while True:
        result = cdp.call(
            "Runtime.evaluate",
            {"expression": expression, "returnByValue": True},
            session_id,
        )
        if result.get("result", {}).get("value") == "Memory and Agent Court":
            return target_id
        if time.monotonic() >= deadline:
            raise AssertionError("target semantic state did not become ready")
        time.sleep(0.025)


def wait_targets_closed(cdp, target_ids):
    deadline = time.monotonic() + 5.0
    while True:
        live = {
            item["targetId"]
            for item in cdp.call("Target.getTargets").get("targetInfos", [])
        }
        if not live.intersection(target_ids):
            return
        if time.monotonic() >= deadline:
            raise AssertionError("closed targets remained in Target.getTargets")
        time.sleep(0.025)


def close_target(cdp, target_id):
    closed = cdp.call("Target.closeTarget", {"targetId": target_id})
    if closed.get("success") is False:
        raise AssertionError(f"Target.closeTarget rejected {target_id}")
    wait_targets_closed(cdp, [target_id])


def run_once(engine, browser, fixture, samples, settle_ms):
    fixture_url = "data:text/html," + urllib.parse.quote_from_bytes(fixture, safe="")
    with tempfile.TemporaryDirectory(prefix="minicon-surf-retention-") as directory:
        process = None
        websocket = None
        targets = []
        try:
            launch_args = argparse.Namespace(engine=engine, browser=browser)
            process, endpoint = CDP_SUPPORT.launch(launch_args, directory)
            websocket = CDP_SUPPORT.WebSocket(endpoint)
            cdp = CDP_SUPPORT.CDP(websocket)
            result = {"empty": sample_stage(process, samples, settle_ms)}
            targets.append(open_ready_target(cdp, fixture_url))
            result["one_target"] = sample_stage(process, samples, settle_ms)
            close_target(cdp, targets.pop())
            result["post_one_close"] = sample_stage(process, samples, settle_ms)
            for _ in range(2, TARGET_COUNT):
                target_id = open_ready_target(cdp, fixture_url)
                close_target(cdp, target_id)
            targets.append(open_ready_target(cdp, fixture_url))
            result["eighth_target"] = sample_stage(process, samples, settle_ms)
            close_target(cdp, targets.pop())
            result["post_eight_closes"] = sample_stage(process, samples, settle_ms)

            capacity_error = None
            while len(targets) < TARGET_COUNT:
                try:
                    targets.append(open_ready_target(cdp, fixture_url))
                except RuntimeError as error:
                    capacity_error = str(error)
                    break
            capacity = {
                "observed_concurrent_targets_at_probe_stop": len(targets),
                "attempted_limit": TARGET_COUNT,
                "probe_reached_limit": len(targets) == TARGET_COUNT,
                "next_create_error": capacity_error,
                "state": sample_stage(process, samples, settle_ms),
            }
            for target_id in targets:
                close_target(cdp, target_id)
            return result, capacity
        finally:
            if websocket is not None:
                websocket.close()
            if process is not None and process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()


def aggregate(run_results):
    runs = [item[0] for item in run_results]
    capacities = [item[1] for item in run_results]
    stages = {}
    for stage in STAGES:
        stage_runs = [run[stage] for run in runs]
        stages[stage] = {}
        for key in ("peak_tree_resident_bytes", "peak_process_count"):
            values = [row[key] for row in stage_runs]
            stages[stage][key] = values
            stages[stage][f"median_{key}"] = int(statistics.median(values))
            stages[stage][f"maximum_{key}"] = max(values)
    one_delta = [
        run["one_target"]["peak_tree_resident_bytes"]
        - run["empty"]["peak_tree_resident_bytes"]
        for run in runs
    ]
    reused_delta = [
        run["eighth_target"]["peak_tree_resident_bytes"]
        - run["post_one_close"]["peak_tree_resident_bytes"]
        for run in runs
    ]
    retained = [
        run["post_eight_closes"]["peak_tree_resident_bytes"]
        - run["empty"]["peak_tree_resident_bytes"]
        for run in runs
    ]
    capacity_rss = [item["state"]["peak_tree_resident_bytes"] for item in capacities]
    observed = [item["observed_concurrent_targets_at_probe_stop"] for item in capacities]
    reached_limit = [item["probe_reached_limit"] for item in capacities]
    errors = [item["next_create_error"] for item in capacities]
    return {
        "states": stages,
        "first_target_delta_resident_bytes": one_delta,
        "median_first_target_delta_resident_bytes": int(statistics.median(one_delta)),
        "eighth_target_minus_post_one_close_resident_bytes": reused_delta,
        "median_eighth_target_minus_post_one_close_resident_bytes": int(statistics.median(reused_delta)),
        "post_eight_closes_minus_empty_resident_bytes": retained,
        "median_post_eight_closes_minus_empty_resident_bytes": int(statistics.median(retained)),
        "concurrent_capacity_probe": {
            "observed_concurrent_targets_at_probe_stop": observed,
            "probe_reached_limit": reached_limit,
            "next_create_errors": errors,
            "peak_tree_resident_bytes": capacity_rss,
            "median_peak_tree_resident_bytes": int(statistics.median(capacity_rss)),
        },
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--lightpanda", required=True)
    parser.add_argument("--chrome", required=True)
    parser.add_argument("--fixture", required=True)
    parser.add_argument("--lightpanda-sha256", required=True)
    parser.add_argument("--repetitions", type=int, default=7)
    parser.add_argument("--samples-per-stage", type=int, default=3)
    parser.add_argument("--settle-ms", type=int, default=500)
    parser.add_argument("--receipt")
    args = parser.parse_args()
    if platform.system() != "Darwin" or platform.machine() != "arm64":
        parser.error("this court requires macOS arm64")
    if min(args.repetitions, args.samples_per_stage, args.settle_ms) <= 0:
        parser.error("repetitions, samples, and settle time must be positive")

    fixture = pathlib.Path(args.fixture).read_bytes()
    lightpanda_sha = hashlib.sha256(pathlib.Path(args.lightpanda).read_bytes()).hexdigest()
    if lightpanda_sha != args.lightpanda_sha256:
        parser.error("Lightpanda artifact digest does not match --lightpanda-sha256")
    chrome_path = pathlib.Path(args.chrome)
    chrome_sha = hashlib.sha256(chrome_path.read_bytes()).hexdigest()
    chrome_version = subprocess.check_output([args.chrome, "--version"], text=True).strip()
    candidates = {"lightpanda": [], "google_chrome": []}
    paths = {"lightpanda": args.lightpanda, "google_chrome": args.chrome}
    engine_names = {"lightpanda": "lightpanda", "google_chrome": "chrome"}
    for candidate in candidates:
        run_once(engine_names[candidate], paths[candidate], fixture,
                 args.samples_per_stage, args.settle_ms)
    for repetition in range(args.repetitions):
        order = list(candidates)
        if repetition % 2:
            order.reverse()
        for candidate in order:
            candidates[candidate].append(
                run_once(engine_names[candidate], paths[candidate], fixture,
                         args.samples_per_stage, args.settle_ms)
            )

    if hashlib.sha256(pathlib.Path(args.lightpanda).read_bytes()).hexdigest() != lightpanda_sha:
        raise RuntimeError("Lightpanda artifact changed during the measured court")
    if hashlib.sha256(chrome_path.read_bytes()).hexdigest() != chrome_sha:
        raise RuntimeError("Chrome executable changed during the measured court")
    if subprocess.check_output([args.chrome, "--version"], text=True).strip() != chrome_version:
        raise RuntimeError("Chrome version changed during the measured court")
    receipt = {
        "schema": "minicon-surf.cdp-target-retention-receipt/0.0.1",
        "status": "incomplete",
        "court": "same-server-cdp-target-churn-capacity-and-retention",
        "platform": {"os": "macos", "architecture": "arm64"},
        "workload": {
            "fixture_sha256": hashlib.sha256(fixture).hexdigest(),
            "stage_order": list(STAGES),
            "sequential_target_cycles": TARGET_COUNT,
            "concurrent_target_probe_limit": TARGET_COUNT,
            "warmups_per_candidate": 1,
            "measured_repetitions": args.repetitions,
            "samples_per_stage": args.samples_per_stage,
            "settle_ms": args.settle_ms,
            "order": "alternating by repetition",
            "target_ready_condition": "named semantic heading observed through Runtime.evaluate for every sequential target",
            "profile": "fresh temporary Chrome profile per repetition; Lightpanda 0.4.0 has no equivalent profile flag",
        },
        "measurement": {
            "semantic": "peak of sampled sum of BSD ps RSS over browser root and recursively observed descendants per stage",
            "candidates": {
                "lightpanda": {
                    "version": "0.4.0",
                    "artifact_sha256": lightpanda_sha,
                    **aggregate(candidates["lightpanda"]),
                },
                "google_chrome": {
                    "version": chrome_version.removeprefix("Google Chrome "),
                    "executable_sha256": chrome_sha,
                    **aggregate(candidates["google_chrome"]),
                },
            },
        },
        "limitations": [
            "summed RSS is neither private memory nor PSS and can double-count shared pages",
            "process-table sampling can miss short-lived or reparented processes",
            "one synthetic data URL, one operating system and one architecture only",
            "installed Chrome is digest-identified but is not a pinned downloadable artifact",
            "browser feature sets and engine capabilities are not equivalent",
            "stage samples are maxima and transitions occur between measured windows",
            "concurrent capacity is reported per candidate rather than forced into a like-for-like target count",
            "Lightpanda has no product-equivalent profile isolation in this court",
            "no allocator trim or memory-pressure signal is available through the shared CDP journey",
        ],
    }
    encoded = json.dumps(receipt, indent=2, sort_keys=True) + "\n"
    if args.receipt:
        pathlib.Path(args.receipt).write_text(encoded, encoding="utf-8")
    print(encoded, end="")


if __name__ == "__main__":
    main()
