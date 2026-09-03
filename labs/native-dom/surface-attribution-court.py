#!/usr/bin/env python3
"""Paired attribution of the host's post-hide retention (G3 surface process).

cdx-k68's verdict keeps the surface court at 106 of 110 (mechanics pass, the
host's post-hide footprint and slope fail) and asks for attribution before
any fix. This court is read-only: no cap moves, nothing is optimized.

Per cell a fresh host process, one warm-up plus seven measured runs, three
show/hide rounds each, under the default allocator and the arena. The host
writes court-only stage samples (`--surface-court-file`) taken inside the
process at: show_entry → after_snapshot → after_painter →
after_command_spawn (pipes and child) → after_reader_thread (channel and
reader thread) → after_hello_ready → after_first_frame_ack → shown →
hide_entry → after_close_reap_join (CLOSE, CLOSED, exit reaped, reader
joined) → after_frame_drop (frame buffer dropped). Each sample carries the
kernel's physical footprint and RSS, libmalloc in-use and allocated bytes,
and the thread count. The court samples footprint and RSS from outside at
headless, shown and post-hide as a cross-check.

Cells (child modes are lab-local modes of the real surface binary, chosen
through the court-only `--surface-child-mode`; the frame ladder through
`--surface-court-frame`; stage sampling through `--surface-court-stages 1`):
  appkit 640x400 (the product path), appkit 256x256, appkit 128x128,
  protocol 640x400 (HELLO/READY/ACK/CLOSED, no AppKit, frames discarded),
  drain 640x400 (no AppKit, the latest frame kept), and
  exit (the child leaves at once: the failed protocol path, reported apart).

Reported per cell: stage medians, consecutive-stage deltas, post-hide over
headless per round, the first stage of the hide sequence that stops
releasing, thread recovery, and whether libmalloc in-use returns; plus
memory.trim after the run as a diagnostic that is not a fix.
"""

import argparse
import hashlib
import importlib.util
import json
import statistics
import sys
import tempfile
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


SURFACE = load_module("surface_court", Path(__file__).with_name("surface-court.py"))
RETENTION = SURFACE.RETENTION
STAGES = ["show_entry", "after_snapshot", "after_painter", "after_command_spawn", "after_reader_thread", "after_hello_ready",
          "after_first_frame_ack", "shown", "hide_entry", "after_close_reap_join", "after_frame_drop"]
HIDE_SEQUENCE = ["shown", "hide_entry", "after_close_reap_join", "after_frame_drop"]
# Default (headless): the frame ladder on the no-AppKit `drain` child; the
# real window cells run only under the double opt-in (`--visual` and
# MINICON_SURF_ALLOW_VISIBLE_COURT=1) and show windows.
CELLS = [("drain-640x400", "drain", "640x400"), ("drain-256x256", "drain", "256x256"), ("drain-128x128", "drain", "128x128"),
         ("protocol-640x400", "protocol", "640x400"), ("exit-640x400", "exit", "640x400")]
VISUAL_CELLS = [("appkit-640x400", None, "640x400"), ("appkit-256x256", None, "256x256"), ("appkit-128x128", None, "128x128")]
ROUNDS = 3
KEYS = ("footprint", "rss", "virtual", "in_use", "allocated", "threads")


class Host(SURFACE.CDP.Host):
    def __init__(self, binary, directory, allocator, surface_binary, court_file, child_mode, frame, visual=False):
        import os
        import subprocess
        environment = dict(os.environ)
        for knob in ("MINICON_SURF_NATIVE_REALM_ZONE", "MINICON_SURF_NATIVE_REALM_ARENA", "MINICON_SURF_PROFILE_STORE"):
            environment.pop(knob, None)
        if allocator == "arena":
            environment["MINICON_SURF_NATIVE_REALM_ARENA"] = "1"
        command = [binary, "serve", "--stdio", "--fixture-root", str(SURFACE.FIXTURE_ROOT), "--config-dir", str(Path(directory) / "config"),
                   "--surface-binary", str(surface_binary), "--surface-court-file", str(court_file), "--surface-court-frame", frame, "--surface-court-stages", "1"]
        if child_mode:
            command += ["--surface-child-mode", child_mode]
        if visual:
            command += ["--visual", "1"]
        self.process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, env=environment)
        self.counter = 0

    def finish(self):
        self.process.stdin.close()
        return self.process.wait(timeout=30)


def outside(host):
    time.sleep(0.05)
    sample = RETENTION.sample_process(host.process.pid)
    report = host.ok("memory.report", {})
    return {"footprint": sample["physical_footprint_bytes"], "rss": sample["resident_bytes"],
            "in_use": report["libmalloc"]["size_in_use"], "allocated": report["libmalloc"]["size_allocated"]}


def read_stages(court_file):
    stages = []
    for line in Path(court_file).read_text().splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if event.get("event") == "stage":
            stages.append(event)
    return stages


def run_once(binary, surface_binary, allocator, child_mode, frame, visual=False):
    with tempfile.TemporaryDirectory(prefix="minicon-surf-surface-attribution-") as directory:
        court_file = Path(directory) / "court-only.ndjson"
        host = Host(binary, directory, allocator, surface_binary, court_file, child_mode, frame, visual=visual)
        try:
            profile = host.ok("profile.create", {"persistence": "ephemeral"})["profile"]
            session = host.ok("session.open", {"profile": profile})["session"]
            target = host.ok("target.open", {"session": session, "fixture": "semantic-interactive.html"})["target"]
            host.ok("target.inspect", {"target": target})
            headless = outside(host)
            rounds = []
            for index in range(1, ROUNDS + 1):
                shown = host.call("surface.show", {"target": target}, 15000)
                shown_sample = outside(host)
                hidden = None
                if shown["ok"]:
                    hidden = host.ok("surface.hide", {"surface": shown["result"]["surface"]}, 15000)
                post_hide = outside(host)
                rounds.append({"round": index, "show_ok": shown["ok"], "show_error": (shown.get("error") or {}).get("details", {}).get("reason") if not shown["ok"] else None,
                               "shown": shown_sample, "post_hide": post_hide, "teardown": (hidden or {}).get("teardown"),
                               "latency": (shown.get("result") or {}).get("latency")})
            host.ok("target.close", {"target": target})
            host.ok("session.close", {"session": session})
            post_close = outside(host)
            trimmed = host.ok("memory.trim", {})
            post_trim = outside(host)
            stages = read_stages(court_file)
            exit_code = host.finish()
        finally:
            if host.process.poll() is None:
                host.process.kill()
                host.process.wait()
    # Split the stage samples into rounds by show_entry markers.
    per_round = []
    current = None
    for event in stages:
        if event["stage"] == "show_entry":
            current = {}
            per_round.append(current)
        if current is not None:
            current[event["stage"]] = {k: event.get(k) for k in KEYS}
    return {"headless": headless, "rounds": rounds, "stage_rounds": per_round, "post_close": post_close, "post_trim": post_trim,
            "trim_released": trimmed.get("released_bytes"), "exit_code": exit_code}


def median(values):
    return int(statistics.median(values))


def summarize(values):
    return {"median": median(values), "minimum": min(values), "maximum": max(values)}


def aggregate(runs):
    out = {"headless": {k: summarize([r["headless"][k] for r in runs]) for k in ("footprint", "rss", "in_use", "allocated")},
           "rounds": [], "exit_codes": sorted(set(r["exit_code"] for r in runs)), "show_ok": all(rd["show_ok"] for r in runs for rd in r["rounds"]),
           "show_errors": sorted({rd["show_error"] for r in runs for rd in r["rounds"] if rd["show_error"]}),
           "post_close_over_headless": summarize([r["post_close"]["footprint"] - r["headless"]["footprint"] for r in runs]),
           "post_trim_over_headless": summarize([r["post_trim"]["footprint"] - r["headless"]["footprint"] for r in runs]),
           "trim_released": summarize([r["trim_released"] or 0 for r in runs])}
    for index in range(ROUNDS):
        rows = [r["rounds"][index] for r in runs]
        stage_rows = [r["stage_rounds"][index] for r in runs if len(r["stage_rounds"]) > index]
        stages = {}
        for stage in STAGES:
            present = [s[stage] for s in stage_rows if stage in s]
            if present:
                stages[stage] = {k: summarize([p[k] for p in present]) for k in KEYS if all(p.get(k) is not None for p in present)}
        deltas = {}
        previous = None
        for stage in STAGES:
            if stage in stages:
                if previous:
                    deltas[f"{previous}->{stage}"] = {k: stages[stage][k]["median"] - stages[previous][k]["median"] for k in ("footprint", "in_use", "allocated", "threads") if k in stages[stage] and k in stages[previous]}
                previous = stage
        # The first stage of the hide sequence whose footprint does not fall below the previous one, per run, then the mode.
        firsts = []
        for s in stage_rows:
            sequence = [(st, s[st]["footprint"]) for st in HIDE_SEQUENCE if st in s]
            first = None
            for (a, fa), (b, fb) in zip(sequence, sequence[1:]):
                if fb >= fa:
                    first = b
                    break
            firsts.append(first or "none")
        out["rounds"].append({
            "round": index + 1,
            "stages": stages,
            "stage_deltas": deltas,
            "outside_shown_over_headless": summarize([rd["shown"]["footprint"] - r["headless"]["footprint"] for r, rd in zip(runs, rows)]),
            "outside_post_hide_over_headless": summarize([rd["post_hide"]["footprint"] - r["headless"]["footprint"] for r, rd in zip(runs, rows)]),
            "post_hide_in_use_over_headless": summarize([rd["post_hide"]["in_use"] - r["headless"]["in_use"] for r, rd in zip(runs, rows)]),
            "first_non_releasing_stage": {f: firsts.count(f) for f in sorted(set(firsts))},
            "threads_show_entry_vs_after_frame_drop": ((stages.get("after_frame_drop", {}).get("threads", {}).get("median"), stages.get("show_entry", {}).get("threads", {}).get("median"))),
            "teardown_exits": sorted({(rd["teardown"] or {}).get("exit", "n/a") for rd in rows}),
            "latency": {k: summarize([rd["latency"][k] for rd in rows if rd["latency"]]) for k in ("ready_ms", "first_frame_ms", "show_ms") if any(rd["latency"] for rd in rows)},
        })
    return out


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--binary", required=True)
    parser.add_argument("--surface-binary", required=True)
    parser.add_argument("--repetitions", type=int, default=7)
    parser.add_argument("--warmup", type=int, default=1)
    parser.add_argument("--receipt", required=True)
    parser.add_argument("--cells", default="")
    parser.add_argument("--visual", action="store_true", help="also run the real-window cells (shows windows); needs MINICON_SURF_ALLOW_VISIBLE_COURT=1")
    args = parser.parse_args()
    if args.visual and not SURFACE.visual_opt_in(args):
        print(json.dumps({"passed": None, "unverified": "--visual needs MINICON_SURF_ALLOW_VISIBLE_COURT=1; nothing was started"}))
        return 3
    cells = list(CELLS) + (list(VISUAL_CELLS) if SURFACE.visual_opt_in(args) else [])
    wanted = set(args.cells.split(",")) if args.cells else {c[0] for c in cells}
    live_hosts = []
    SURFACE.install_cleanup(lambda: live_hosts)
    results = {}
    for allocator in ("system", "arena"):
        results[allocator] = {}
        for label, mode, frame in cells:
            if label not in wanted:
                continue
            runs = []
            for repetition in range(args.warmup + args.repetitions):
                run = run_once(args.binary, args.surface_binary, allocator, mode, frame, visual=mode is None)
                if repetition >= args.warmup:
                    runs.append(run)
            results[allocator][label] = aggregate(runs)
    receipt = {
        "schema": "minicon-surf.native-dom-surface-attribution-receipt/0.0.1",
        "technology": "native-dom",
        "host_sha256": hashlib.sha256(Path(args.binary).read_bytes()).hexdigest(),
        "surface_sha256": hashlib.sha256(Path(args.surface_binary).read_bytes()).hexdigest(),
        "purpose": "read-only paired attribution of the host's post-hide retention; no cap moves; the exit cell is the failed protocol path and stands apart",
        "stages": STAGES,
        "cells": [{"label": c[0], "child_mode": c[1] or "appkit", "frame": c[2]} for c in cells if c[0] in wanted],
        "visual": SURFACE.visual_opt_in(args),
        "repetitions": args.repetitions,
        "warmup": args.warmup,
        "rounds": ROUNDS,
        "results": results,
        "limitations": [
            "stage samples are taken by the host itself (proc_pid_rusage, proc_pidinfo, malloc_zone_statistics) only when --surface-court-file and --surface-court-stages 1 are given; the outside samples cross-check them",
            "the fixture page is a court fixture file, not the representative page over the network, so the painter's rows differ from the surface court's",
            "memory.trim after the run is a diagnostic, not a fix; no cap moves here",
            "no pid, path or command line is recorded",
            "headless by default: the child runs in its no-AppKit modes; the appkit cells exist only under --visual with MINICON_SURF_ALLOW_VISIBLE_COURT=1 and show windows",
        ],
    }
    Path(args.receipt).write_text(json.dumps(receipt, indent=1, sort_keys=True) + "\n")
    summary = {}
    for allocator, cells in results.items():
        summary[allocator] = {label: {"post_hide_over_headless_r1_r3": [c["rounds"][0]["outside_post_hide_over_headless"]["median"], c["rounds"][-1]["outside_post_hide_over_headless"]["median"]],
                                      "first_non_releasing": c["rounds"][0]["first_non_releasing_stage"], "show_ok": c["show_ok"]} for label, c in cells.items()}
    print(json.dumps(summary, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
