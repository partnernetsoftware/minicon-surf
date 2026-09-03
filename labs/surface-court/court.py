#!/usr/bin/env python3
"""Frozen candidate court for the native route's G3 surface design (macOS).

Measures standalone `surface-probe` builds (plain-buffer control, direct
Cocoa through objc2, winit + softbuffer) against the pre-registered criteria
S1–S9 and stages of `labs/native-dom/surface-design-0.0.1.md`. Nothing here
touches the native host. A fresh probe process per run, one warm-up plus
seven measured, driven over stdio: headless → three rounds of show → pump →
capture → hide → sample. Footprint and RSS are sampled from outside over the
complete process tree (host plus descendants) with the helper court's
sampler; the WindowServer footprint is recorded before and after as a
diagnostic that is not attributed to the probe. The window shows a colour-bar
test pattern only; the capture reads back the own window and never the
desktop.
"""

import argparse
import hashlib
import importlib.util
import json
import statistics
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LAB = Path(__file__).resolve().parent
CANDIDATES = {"plain": [], "cocoa": ["cocoa"], "winit-softbuffer": ["winit-softbuffer"]}
BACKING_BYTES = 320 * 200 * 4
CRITERIA = {
    "s2_post_hide_over_headless_bytes": 262144,
    "s3_slope_round3_minus_round1_bytes": 65536,
    "s4_in_use_after_hide_over_headless_bytes": 65536,
    "s9_show_hide_wall_ms_gate": 1000.0,
    "s9_show_hide_wall_ms_target": 200.0,
}
ROUNDS = 3
SETTLE_SECONDS = 0.05


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


HELPER = load_module("profile_helper_court", ROOT / "labs" / "native-dom" / "profile-helper-court.py")
RETENTION = HELPER.RETENTION
TreeSampler = HELPER.TreeSampler
descendants_of = HELPER.descendants_of


def window_server_footprint():
    pid = subprocess.run(["pgrep", "-x", "WindowServer"], capture_output=True, text=True).stdout.split()
    if not pid:
        return None
    try:
        return RETENTION.sample_process(int(pid[0]))["physical_footprint_bytes"]
    except Exception:  # noqa: BLE001 - the diagnostic is optional
        return None


def build(candidate, skip_build):
    target = LAB / "target" / candidate
    binary = target / "release" / "surface-probe"
    features = CANDIDATES[candidate]
    command = ["cargo", "build", "--release", "--locked", "--offline", "--target-dir", str(target)]
    if features:
        command += ["--features", ",".join(features)]
    record = {"candidate": candidate, "features": features}
    if not skip_build or not binary.exists():
        started = time.monotonic()
        result = subprocess.run(command, cwd=LAB, capture_output=True, text=True)
        record["build_seconds"] = round(time.monotonic() - started, 1)
        if result.returncode != 0:
            tail = [line for line in result.stderr.splitlines() if line.startswith(("error", "warning: build failed"))][:6]
            record.update({"built": False, "error": " | ".join(tail)[:600]})
            return None, record
    record["built"] = True
    record["binary_bytes"] = binary.stat().st_size
    record["binary_sha256"] = hashlib.sha256(binary.read_bytes()).hexdigest()
    tree = ["cargo", "tree", "--offline", "--edges", "normal", "--prefix", "none"] + (["--features", ",".join(features)] if features else [])
    listing = subprocess.run(tree, cwd=LAB, capture_output=True, text=True).stdout
    crates = sorted({" ".join(line.split(" ")[:2]) for line in listing.splitlines() if " v" in line})
    record["crates"] = len(crates)
    record["crate_list"] = crates
    libraries = subprocess.run(["otool", "-L", str(binary)], capture_output=True, text=True).stdout.splitlines()[1:]
    record["dynamic_libraries"] = sorted(line.strip().split(" (")[0] for line in libraries)
    return binary, record


class Probe:
    def __init__(self, binary):
        self.process = subprocess.Popen([str(binary)], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
        self.sampler = TreeSampler(self.process.pid, str(binary))
        self.sampler.start()
        self.descendants_seen = 0

    def call(self, **command):
        self.process.stdin.write(json.dumps(command) + "\n")
        self.process.stdin.flush()
        line = self.process.stdout.readline()
        if not line:
            raise RuntimeError("probe exited")
        if descendants_of(self.process.pid):
            self.descendants_seen += 1
        return json.loads(line)

    def sample(self, label):
        time.sleep(SETTLE_SECONDS)
        outside = RETENTION.sample_process(self.process.pid)
        report = self.call(op="report")
        return {"stage": label, "physical_footprint_bytes": outside["physical_footprint_bytes"], "resident_bytes": outside["resident_bytes"],
                "libmalloc_size_in_use": report["libmalloc"]["size_in_use"], "libmalloc_size_allocated": report["libmalloc"]["size_allocated"],
                "surfaces": report["surfaces"], "backing_bytes": report["backing_bytes"], "window_numbers": report["window_numbers"], "images": report["images"]}

    def finish(self):
        try:
            self.call(op="exit")
        except Exception:  # noqa: BLE001
            pass
        code = self.process.wait(timeout=30)
        self.sampler.stop()
        return code


def run_once(binary):
    probe = Probe(binary)
    stages, rounds = [], []
    try:
        headless = probe.call(op="headless")
        stages.append(probe.sample("headless"))
        for index in range(1, ROUNDS + 1):
            shown = probe.call(op="show")
            if not shown["ok"]:
                raise RuntimeError(f"show: {shown.get('error')}")
            probe.call(op="pump")
            time.sleep(0.15)
            probe.call(op="pump")
            shown_sample = probe.sample(f"shown_{index}")
            capture = probe.call(op="capture")
            hidden = probe.call(op="hide")
            if not hidden["ok"]:
                raise RuntimeError(f"hide: {hidden.get('error')}")
            probe.call(op="pump")
            hidden_sample = probe.sample(f"post_hide_{index}")
            stages += [shown_sample, hidden_sample]
            rounds.append({"round": index, "window_number": shown.get("window_number"), "real_window": shown.get("real_window"),
                           "show_ms": shown.get("show_ms"), "hide_ms": hidden.get("hide_ms"), "activation_policy": shown.get("activation_policy"),
                           "capture": capture, "shown": shown_sample, "post_hide": hidden_sample})
        exit_code = probe.finish()
    finally:
        if probe.process.poll() is None:
            probe.process.kill()
            probe.process.wait()
            probe.sampler.stop()
    return {"candidate": headless.get("candidate"), "stages": stages, "rounds": rounds, "exit_code": exit_code, "descendants_seen": probe.descendants_seen,
            "max_descendants": probe.sampler.max_descendants, "tree_peak": max((s[2] for s in probe.sampler.samples), default=0),
            "samples": len(probe.sampler.samples)}


def median(values):
    return int(statistics.median(values))


def summarize(values):
    return {"median": median(values), "minimum": min(values), "maximum": max(values), "values": values}


def aggregate(runs):
    stages = {}
    for stage in [s["stage"] for s in runs[0]["stages"]]:
        rows = [next(s for s in r["stages"] if s["stage"] == stage) for r in runs]
        stages[stage] = {key: summarize([row[key] for row in rows]) for key in ("physical_footprint_bytes", "resident_bytes", "libmalloc_size_in_use", "libmalloc_size_allocated", "surfaces", "backing_bytes")}
        stages[stage]["images"] = rows[0]["images"]
        stages[stage]["window_numbers_count"] = summarize([len(row["window_numbers"]) for row in rows])
    return {
        "candidate": runs[0]["candidate"],
        "stages": stages,
        "show_ms": summarize([round(rd["show_ms"], 2) for r in runs for rd in r["rounds"]]),
        "hide_ms": summarize([round(rd["hide_ms"], 2) for r in runs for rd in r["rounds"]]),
        "real_window": all(rd["real_window"] for r in runs for rd in r["rounds"]),
        "activation_policy": runs[0]["rounds"][0]["activation_policy"],
        "capture": {"verified_rounds": sum(1 for r in runs for rd in r["rounds"] if rd["capture"].get("verified")), "of": sum(len(r["rounds"]) for r in runs),
                    "example": runs[0]["rounds"][0]["capture"]},
        "tree": {"max_descendants": max(r["max_descendants"] for r in runs), "descendants_seen_at_calls": sum(r["descendants_seen"] for r in runs),
                 "tree_peak": summarize([r["tree_peak"] for r in runs]), "samples": summarize([r["samples"] for r in runs])},
        "exit_codes": [r["exit_code"] for r in runs],
        "post_hide_over_headless": [[rd["post_hide"]["physical_footprint_bytes"] - r["stages"][0]["physical_footprint_bytes"] for rd in r["rounds"]] for r in runs],
        "slope_round3_minus_round1": [r["rounds"][-1]["post_hide"]["physical_footprint_bytes"] - r["rounds"][0]["post_hide"]["physical_footprint_bytes"] for r in runs],
        "in_use_after_hide_over_headless": [[rd["post_hide"]["libmalloc_size_in_use"] - r["stages"][0]["libmalloc_size_in_use"] for rd in r["rounds"]] for r in runs],
        "owner_sequence_exact": all(r["stages"][0]["surfaces"] == 0 and r["stages"][0]["backing_bytes"] == 0
                                    and all(rd["shown"]["surfaces"] == 1 and rd["shown"]["backing_bytes"] == BACKING_BYTES and rd["post_hide"]["surfaces"] == 0 and rd["post_hide"]["backing_bytes"] == 0 for rd in r["rounds"])
                                    for r in runs),
        "windows_on_screen_exact": all(all((len(rd["shown"]["window_numbers"]) >= 1) and (len(rd["post_hide"]["window_numbers"]) == 0) for rd in r["rounds"]) for r in runs),
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--candidates", default="plain,cocoa,winit-softbuffer")
    parser.add_argument("--repetitions", type=int, default=7)
    parser.add_argument("--warmup", type=int, default=1)
    parser.add_argument("--receipt", required=True)
    parser.add_argument("--skip-build", action="store_true")
    args = parser.parse_args()
    checks, results, builds = [], {}, {}

    def expect(name, condition, detail=None):
        checks.append({"check": name, "passed": bool(condition), **({"detail": detail} if detail is not None else {})})

    window_server_before = window_server_footprint()
    for candidate in [c.strip() for c in args.candidates.split(",") if c.strip()]:
        binary, record = build(candidate, args.skip_build)
        builds[candidate] = record
        if binary is None:
            expect(f"[{candidate}] probe builds offline", False, record.get("error"))
            continue
        runs = []
        for repetition in range(args.warmup + args.repetitions):
            run = run_once(binary)
            if repetition >= args.warmup:
                runs.append(run)
            expect(f"[{candidate}] run {repetition}: probe exits cleanly with no surface and no descendant", run["exit_code"] == 0 and run["stages"][-1]["surfaces"] == 0 and run["max_descendants"] == 0)
        results[candidate] = aggregate(runs)
    window_server_after = window_server_footprint()

    for candidate, agg in results.items():
        if candidate == "plain":
            continue
        tag = f"[{candidate}] "
        expect(tag + "S1 surface owner and backing bytes go 0 → 1 (256,000 bytes) → 0 in every round", agg["owner_sequence_exact"])
        worst = max(v for run in agg["post_hide_over_headless"] for v in run)
        expect(tag + "S2 post-hide footprint over headless, every round", worst <= CRITERIA["s2_post_hide_over_headless_bytes"], {"worst": worst, "per_run": agg["post_hide_over_headless"]})
        slope = max(agg["slope_round3_minus_round1"])
        expect(tag + "S3 no slope: post-hide round 3 minus round 1", slope <= CRITERIA["s3_slope_round3_minus_round1_bytes"], {"worst": slope, "per_run": agg["slope_round3_minus_round1"]})
        worst_in_use = max(v for run in agg["in_use_after_hide_over_headless"] for v in run)
        expect(tag + "S4 libmalloc in-use after hide over headless", worst_in_use <= CRITERIA["s4_in_use_after_hide_over_headless_bytes"], {"worst": worst_in_use})
        expect(tag + "S5 one process, no descendant at any stage", agg["tree"]["max_descendants"] == 0 and agg["tree"]["descendants_seen_at_calls"] == 0)
        loaded = {stage: (s["images"].get("Metal"), s["images"].get("OpenGL")) for stage, s in agg["stages"].items()}
        expect(tag + "S6 no Metal or OpenGL image loaded at any stage", not any(m or o for m, o in loaded.values()), {stage: {"Metal": m, "OpenGL": o} for stage, (m, o) in loaded.items()})
        expect(tag + "S7 a real OS window with a window number is on screen while shown and gone after hide", agg["real_window"] and agg["windows_on_screen_exact"],
               {"real_window": agg["real_window"], "on_screen": {s: agg["stages"][s]["window_numbers_count"]["median"] for s in agg["stages"]}})
        expect(tag + "S8 the own window's pixels match the pattern (or the OS refused the capture: recorded)",
               agg["capture"]["verified_rounds"] == agg["capture"]["of"] or (agg["capture"]["verified_rounds"] == 0 and "reason" in agg["capture"]["example"]), agg["capture"])
        expect(tag + "S9 show and hide within the 1,000 ms gate (200 ms target recorded)",
               agg["show_ms"]["maximum"] <= CRITERIA["s9_show_hide_wall_ms_gate"] and agg["hide_ms"]["maximum"] <= CRITERIA["s9_show_hide_wall_ms_gate"],
               {"show_ms": agg["show_ms"]["median"], "hide_ms": agg["hide_ms"]["median"], "show_max": agg["show_ms"]["maximum"], "hide_max": agg["hide_ms"]["maximum"]})

    receipt = {
        "schema": "minicon-surf.surface-court-receipt/0.0.1",
        "design": "labs/native-dom/surface-design-0.0.1.md",
        "platform": "macOS only",
        "criteria": CRITERIA,
        "rounds": ROUNDS,
        "repetitions": args.repetitions,
        "warmup": args.warmup,
        "builds": builds,
        "results": results,
        "window_server_footprint_diagnostic": {"before": window_server_before, "after": window_server_after, "note": "a system process outside the probe's tree; recorded, not attributed"},
        "checks": checks,
        "passed": all(c["passed"] for c in checks),
        "limitations": [
            "the window server, the GPU and any compositor memory live outside the probe's process tree and are not attributed; the WindowServer footprint delta is a system-wide diagnostic",
            "the pattern is a colour-bar fixture read back from the own window only; no desktop content is captured",
            "one platform, one window size, no input events",
        ],
    }
    Path(args.receipt).write_text(json.dumps(receipt, indent=1, sort_keys=True) + "\n")
    failed = [c for c in checks if not c["passed"]]
    print(json.dumps({"passed": receipt["passed"], "checks_passed": len(checks) - len(failed), "checks_total": len(checks), "builds": {k: v.get("built") for k, v in builds.items()}}, indent=1))
    for check in failed:
        print("FAIL", json.dumps(check)[:600])
    return 0 if receipt["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
