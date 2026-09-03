#!/usr/bin/env python3
"""Read-only attribution of the surface's snapshot and serde path (G3).

After the frame region, the surface court's post-hide residual (0.2 to
0.5 MB after three rounds) is small-block churn that precedes the child.
cdx-k68's ruling asks where in the snapshot path it is born and where it
stops being released, before any second candidate. Nothing here is a fix.

Per cell a fresh host process, one warm-up plus seven measured runs, three
show/hide rounds each, default allocator and arena. The host samples
itself (`--surface-court-stages 1`) at every stage of the path:
show_entry → before_realm_eval → after_realm_eval (the realm produced its
value: realm-side allocations done) → after_string_crossing (the value
became a host String) → after_js_value_drop → after_jobs_drained →
after_network_pump → before_serde_parse → after_serde_parse (Value beside
the String) → after_string_drop → after_rows_extract (rows beside the
Value) → after_value_drop → [after_gc] → after_snapshot (rows and hit map
alive) → after_painter (the mmap written) → spawn stages → shown →
hide_entry → after_close_reap_join → after_frame_drop. Each sample carries
the kernel footprint and RSS, libmalloc in-use and allocated, the thread
count, and the realm's arena statistics (used, blocks, high water,
decommitted-from) when an arena serves it. The court samples footprint,
RSS, libmalloc and the owners from outside as a cross-check.

Shapes (the product court is unchanged): the surface court's fixture
(`semantic-interactive.html`), a small static fixture
(`semantic-static.html`), and the representative page over the hermetic
loopback server. Arms (court-only `--surface-court-snapshot-arm`): the
product path (`full`: evaluate, parse, extract rows, drop the Value,
paint), `evaluate_only` (the same script, its text dropped unparsed),
`parse_drop` (parsed, the Value dropped before any row exists); the
product path plus an explicit realm GC (`--surface-court-gc 1`, a
diagnostic, never a fix); and two lab-only microbench arms that produce
equal-byte JSON of two shapes inside the realm (flat: one padding string;
nested: object-heavy entries) — a microbench, not a browser result. A
`plateau` cell takes seven consecutive `target.snapshot` calls with no
surface to see whether repeated snapshots of one target reuse or grow;
`plateau-inspect` (seven revision reads, no snapshot path) and
`plateau-idle` (the outside samples alone) bound the control plane's own
churn between stages, which the in-process stages never see.
"""

import argparse
import hashlib
import importlib.util
import json
import os
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


SURFACE = load_module("surface_court", Path(__file__).with_name("surface-court.py"))
PROFILE = SURFACE.PROFILE
NETWORK = SURFACE.NETWORK
RETENTION = SURFACE.RETENTION
FIXTURE_ROOT = SURFACE.FIXTURE_ROOT

STAGES = ["show_entry", "before_realm_eval", "after_realm_eval", "after_string_crossing", "after_js_value_drop", "after_jobs_drained",
          "after_network_pump", "before_serde_parse", "after_serde_parse", "after_string_drop", "after_rows_extract", "after_value_drop",
          "after_gc", "after_snapshot", "after_painter", "after_command_spawn", "after_reader_thread", "after_hello_ready",
          "after_first_frame_ack", "shown", "hide_entry", "after_close_reap_join", "after_frame_drop"]
RELEASE_STAGES = ["after_js_value_drop", "after_string_drop", "after_value_drop", "after_gc", "after_close_reap_join", "after_frame_drop"]
KEYS = ("footprint", "rss", "virtual", "in_use", "allocated", "threads", "arena_used", "arena_blocks", "arena_high_water", "arena_touched")
ROUNDS = 3
PLATEAU_CALLS = 7
CELLS = [
    {"name": "current-full", "shape": "fixture:semantic-interactive.html", "arm": None, "gc": False, "mode": "surface"},
    {"name": "current-evaluate_only", "shape": "fixture:semantic-interactive.html", "arm": "evaluate_only", "gc": False, "mode": "surface"},
    {"name": "current-parse_drop", "shape": "fixture:semantic-interactive.html", "arm": "parse_drop", "gc": False, "mode": "surface"},
    {"name": "current-full-gc", "shape": "fixture:semantic-interactive.html", "arm": None, "gc": True, "mode": "surface"},
    {"name": "static-full", "shape": "fixture:semantic-static.html", "arm": None, "gc": False, "mode": "surface"},
    {"name": "static-evaluate_only", "shape": "fixture:semantic-static.html", "arm": "evaluate_only", "gc": False, "mode": "surface"},
    {"name": "static-parse_drop", "shape": "fixture:semantic-static.html", "arm": "parse_drop", "gc": False, "mode": "surface"},
    {"name": "representative-full", "shape": "url:/index.html", "arm": None, "gc": False, "mode": "surface"},
    {"name": "representative-evaluate_only", "shape": "url:/index.html", "arm": "evaluate_only", "gc": False, "mode": "surface"},
    {"name": "representative-parse_drop", "shape": "url:/index.html", "arm": "parse_drop", "gc": False, "mode": "surface"},
    {"name": "microbench-flat", "shape": "fixture:semantic-static.html", "arm": "microbench_flat", "gc": False, "mode": "surface"},
    {"name": "microbench-nested", "shape": "fixture:semantic-static.html", "arm": "microbench_nested", "gc": False, "mode": "surface"},
    {"name": "plateau-current", "shape": "fixture:semantic-interactive.html", "arm": None, "gc": False, "mode": "plateau"},
    {"name": "plateau-inspect", "shape": "fixture:semantic-interactive.html", "arm": None, "gc": False, "mode": "plateau-inspect"},
    {"name": "plateau-idle", "shape": "fixture:semantic-interactive.html", "arm": None, "gc": False, "mode": "plateau-idle"},
]


class Host(SURFACE.CDP.Host):
    def __init__(self, binary, directory, allocator, origin, surface_binary, court_file, arm, gc, visual=False):
        environment = dict(os.environ)
        for knob in ("MINICON_SURF_NATIVE_REALM_ZONE", "MINICON_SURF_NATIVE_REALM_ARENA", "MINICON_SURF_PROFILE_STORE"):
            environment.pop(knob, None)
        if allocator == "arena":
            environment["MINICON_SURF_NATIVE_REALM_ARENA"] = "1"
        command = [binary, "serve", "--stdio", "--fixture-root", str(FIXTURE_ROOT), "--config-dir", str(Path(directory) / "config"),
                   "--allow-origin", origin, "--surface-binary", str(surface_binary), "--surface-court-file", str(court_file),
                   "--surface-court-stages", "1"]
        if arm:
            command += ["--surface-court-snapshot-arm", arm]
        if gc:
            command += ["--surface-court-gc", "1"]
        if visual:
            command += ["--visual", "1"]
        else:
            # Headless by default: the surface binary in its no-AppKit `drain`
            # mode (the paired attribution showed the child mode does not
            # change the host's retention); no window is ever created.
            command += ["--surface-child-mode", "drain"]
        self.process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, env=environment)
        self.counter = 0

    def finish(self):
        self.process.stdin.close()
        return self.process.wait(timeout=30)


def outside(host):
    time.sleep(0.05)
    sample = RETENTION.sample_process(host.process.pid)
    report = host.ok("memory.report", {})
    owners = report["owners"]
    return {"footprint": sample["physical_footprint_bytes"], "rss": sample["resident_bytes"],
            "in_use": report["libmalloc"]["size_in_use"], "allocated": report["libmalloc"]["size_allocated"],
            "realm_malloc": owners["script_realms"]["malloc_bytes"], "surface_bytes": owners["surfaces"]["bytes"],
            "frame_reserved": owners["surfaces"]["frame"]["reserved_bytes"]}


def read_stages(court_file):
    stages = []
    for line in Path(court_file).read_text().splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if event.get("event") == "stage":
            arena = event.get("arena") or {}
            touched = None
            if arena:
                touched = min(arena.get("high_water_bytes", 0), arena.get("decommitted_from") or arena.get("reserved_bytes", 0))
            stages.append({"stage": event["stage"], "footprint": event.get("footprint"), "rss": event.get("rss"), "virtual": event.get("virtual"),
                           "in_use": event.get("in_use"), "allocated": event.get("allocated"), "threads": event.get("threads"),
                           "arena_used": arena.get("used_bytes"), "arena_blocks": arena.get("blocks"),
                           "arena_high_water": arena.get("high_water_bytes"), "arena_touched": touched})
    return stages


def open_target(host, session, shape, origin):
    kind, _, what = shape.partition(":")
    if kind == "url":
        target = host.ok("target.open", {"session": session, "url": f"{origin}{what}"})["target"]
        # The representative page's load-time fetch settles on the first
        # evaluations after open (surface court amendment); two inspections.
        host.ok("target.inspect", {"target": target})
        host.ok("target.inspect", {"target": target})
    else:
        target = host.ok("target.open", {"session": session, "fixture": what})["target"]
        host.ok("target.inspect", {"target": target})
    return target


def run_once(binary, surface_binary, allocator, origin, cell, visual=False, live_hosts=None):
    with tempfile.TemporaryDirectory(prefix="minicon-surf-snapshot-attribution-") as directory:
        court_file = Path(directory) / "court-only.ndjson"
        host = Host(binary, directory, allocator, origin, surface_binary, court_file, cell["arm"], cell["gc"], visual=visual)
        if live_hosts is not None:
            live_hosts.append(host)
        try:
            profile = host.ok("profile.create", {"persistence": "ephemeral"})["profile"]
            session = host.ok("session.open", {"profile": profile})["session"]
            target = open_target(host, session, cell["shape"], origin)
            headless = outside(host)
            rounds = []
            if cell["mode"].startswith("plateau"):
                # plateau: seven snapshots; plateau-inspect: seven revision reads
                # (no snapshot path); plateau-idle: the outside samples alone
                # (the control plane's own churn: request, response, memory.report).
                for index in range(1, PLATEAU_CALLS + 1):
                    nodes = None
                    if cell["mode"] == "plateau":
                        snapshot = host.ok("target.snapshot", {"target": target, "format": "semantic", "max_bytes": 65536, "max_nodes": 64}, 15000)
                        nodes = len(snapshot.get("nodes", []))
                    elif cell["mode"] == "plateau-inspect":
                        host.ok("target.inspect", {"target": target})
                    rounds.append({"round": index, "nodes": nodes, "after": outside(host)})
            else:
                for index in range(1, ROUNDS + 1):
                    shown = host.call("surface.show", {"target": target}, 15000)
                    shown_sample = outside(host)
                    hidden = None
                    if shown["ok"]:
                        hidden = host.ok("surface.hide", {"surface": shown["result"]["surface"]}, 15000)
                    post_hide = outside(host)
                    rounds.append({"round": index, "show_ok": shown["ok"],
                                   "show_error": (shown.get("error") or {}).get("details", {}).get("reason") if not shown["ok"] else None,
                                   "shown": shown_sample, "post_hide": post_hide, "teardown": (hidden or {}).get("teardown"),
                                   "latency": (shown.get("result") or {}).get("latency")})
            host.ok("target.close", {"target": target})
            post_target_close = outside(host)
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
    per_round = []
    current = None
    for event in stages:
        if event["stage"] == "show_entry":
            current = {}
            per_round.append(current)
        if current is not None:
            current[event["stage"]] = {k: event.get(k) for k in KEYS}
    return {"headless": headless, "rounds": rounds, "stage_rounds": per_round, "post_target_close": post_target_close,
            "post_close": post_close, "post_trim": post_trim, "trim_released": trimmed.get("released_bytes"), "exit_code": exit_code}


def median(values):
    values = [v for v in values if v is not None]
    return int(statistics.median(values)) if values else None


def summarize(values):
    values = [v for v in values if v is not None]
    if not values:
        return None
    return {"median": int(statistics.median(values)), "minimum": min(values), "maximum": max(values)}


def summarize_cell(cell, runs):
    out = {"exit_codes": sorted({r["exit_code"] for r in runs}),
           "headless": {k: summarize([r["headless"][k] for r in runs]) for k in runs[0]["headless"]},
           "post_target_close_over_headless": summarize([r["post_target_close"]["footprint"] - r["headless"]["footprint"] for r in runs]),
           "post_close_over_headless": summarize([r["post_close"]["footprint"] - r["headless"]["footprint"] for r in runs]),
           "post_trim_over_headless": summarize([r["post_trim"]["footprint"] - r["headless"]["footprint"] for r in runs]),
           "realm_malloc_after_target_close": summarize([r["post_target_close"]["realm_malloc"] for r in runs]),
           "trim_released": summarize([r["trim_released"] for r in runs])}
    if cell["mode"].startswith("plateau"):
        out["plateau"] = [{"call": i + 1,
                           "footprint_over_headless": summarize([r["rounds"][i]["after"]["footprint"] - r["headless"]["footprint"] for r in runs]),
                           "in_use_over_headless": summarize([r["rounds"][i]["after"]["in_use"] - r["headless"]["in_use"] for r in runs]),
                           "realm_malloc": summarize([r["rounds"][i]["after"]["realm_malloc"] for r in runs]),
                           "nodes": median([r["rounds"][i]["nodes"] for r in runs])}
                          for i in range(PLATEAU_CALLS)]
        return out
    rounds = []
    for index in range(ROUNDS):
        stage_rounds = [r["stage_rounds"][index] for r in runs if len(r["stage_rounds"]) > index]
        present = [s for s in STAGES if all(s in sr for sr in stage_rounds)] if stage_rounds else []
        medians = {s: {k: summarize([sr[s][k] for sr in stage_rounds]) for k in KEYS} for s in present}
        deltas = {}
        for a, b in zip(present, present[1:]):
            deltas[f"{a}->{b}"] = {k: median([sr[b][k] - sr[a][k] for sr in stage_rounds if sr[a][k] is not None and sr[b][k] is not None]) for k in KEYS}
        releases = {}
        for s in RELEASE_STAGES:
            if s in present:
                before = present[present.index(s) - 1]
                releases[s] = {"footprint_fell_runs": sum(1 for sr in stage_rounds if sr[s]["footprint"] < sr[before]["footprint"]),
                               "in_use_fell_runs": sum(1 for sr in stage_rounds if sr[s]["in_use"] < sr[before]["in_use"]),
                               "runs": len(stage_rounds)}
        retained = summarize([sr["after_frame_drop"]["footprint"] - sr["show_entry"]["footprint"] for sr in stage_rounds
                              if "after_frame_drop" in sr and "show_entry" in sr])
        realm_growth = summarize([sr["after_realm_eval"]["footprint"] - sr["before_realm_eval"]["footprint"] for sr in stage_rounds
                                  if "after_realm_eval" in sr and "before_realm_eval" in sr])
        host_growth = summarize([sr["after_value_drop" if "after_value_drop" in sr else "after_string_drop"]["footprint"] - sr["after_realm_eval"]["footprint"]
                                 for sr in stage_rounds if "after_realm_eval" in sr and ("after_value_drop" in sr or "after_string_drop" in sr)])
        rounds.append({"round": index + 1,
                       "show_ok_runs": sum(1 for r in runs if r["rounds"][index]["show_ok"]),
                       "latency": {k: summarize([(r["rounds"][index]["latency"] or {}).get(k) for r in runs]) for k in ("ready_ms", "first_frame_ms", "show_ms")},
                       "outside_shown_over_headless": summarize([r["rounds"][index]["shown"]["footprint"] - r["headless"]["footprint"] for r in runs]),
                       "outside_post_hide_over_headless": summarize([r["rounds"][index]["post_hide"]["footprint"] - r["headless"]["footprint"] for r in runs]),
                       "post_hide_in_use_over_headless": summarize([r["rounds"][index]["post_hide"]["in_use"] - r["headless"]["in_use"] for r in runs]),
                       "post_hide_realm_malloc": summarize([r["rounds"][index]["post_hide"]["realm_malloc"] for r in runs]),
                       "stage_retained_over_show_entry": retained,
                       "realm_side_growth": realm_growth, "host_side_growth_after_realm": host_growth,
                       "stages_present": present, "stage_medians": medians, "stage_deltas": deltas, "release_stages": releases})
    out["rounds"] = rounds
    return out


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--binary", required=True)
    parser.add_argument("--surface-binary", required=True)
    parser.add_argument("--receipt", required=True)
    parser.add_argument("--repetitions", type=int, default=7)
    parser.add_argument("--warmup", type=int, default=1)
    parser.add_argument("--cells", default="")
    parser.add_argument("--visual", action="store_true", help="real-window child instead of the headless drain child (shows windows); needs MINICON_SURF_ALLOW_VISIBLE_COURT=1")
    args = parser.parse_args()
    if args.visual and not SURFACE.visual_opt_in(args):
        print(json.dumps({"passed": None, "unverified": "--visual needs MINICON_SURF_ALLOW_VISIBLE_COURT=1; nothing was started"}))
        return 3
    visual = SURFACE.visual_opt_in(args)
    cells = [c for c in CELLS if not args.cells or c["name"] in args.cells.split(",")]
    live_hosts = []
    SURFACE.install_cleanup(lambda: live_hosts)
    server = NETWORK.Server(("127.0.0.1", 0), PROFILE.ProfileHandler)
    origin = f"http://127.0.0.1:{server.server_address[1]}"
    threading.Thread(target=server.serve_forever, daemon=True).start()
    results = {}
    try:
        for allocator in ("system", "arena"):
            results[allocator] = {}
            for cell in cells:
                for _ in range(args.warmup):
                    run_once(args.binary, args.surface_binary, allocator, origin, cell, visual, live_hosts)
                runs = [run_once(args.binary, args.surface_binary, allocator, origin, cell, visual, live_hosts) for _ in range(args.repetitions)]
                results[allocator][cell["name"]] = summarize_cell(cell, runs)
                summary = results[allocator][cell["name"]]
                if cell["mode"].startswith("plateau"):
                    print(allocator, cell["name"], "plateau footprint", [p["footprint_over_headless"]["median"] for p in summary["plateau"]])
                else:
                    print(allocator, cell["name"], "post-hide", [r["outside_post_hide_over_headless"]["median"] for r in summary["rounds"]],
                          "retained", [r["stage_retained_over_show_entry"]["median"] if r["stage_retained_over_show_entry"] else None for r in summary["rounds"]],
                          "realm", [r["realm_side_growth"]["median"] if r["realm_side_growth"] else None for r in summary["rounds"]],
                          "host", [r["host_side_growth_after_realm"]["median"] if r["host_side_growth_after_realm"] else None for r in summary["rounds"]])
    finally:
        server.shutdown()
    receipt = {
        "schema": "minicon-surf/native-dom-surface-snapshot-attribution/0.0.1",
        "purpose": "read-only attribution of the surface's snapshot and serde path after the frame region; no cap moves, nothing fixed",
        "technology": "native-dom host with a court-only stage log and snapshot arms; direct-Cocoa surface child; outside samples from libproc",
        "host_sha256": hashlib.sha256(Path(args.binary).read_bytes()).hexdigest(),
        "surface_sha256": hashlib.sha256(Path(args.surface_binary).read_bytes()).hexdigest(),
        "repetitions": args.repetitions, "warmup": args.warmup, "rounds": ROUNDS, "plateau_calls": PLATEAU_CALLS,
        "visual": visual, "child_mode": "appkit" if visual else "drain",
        "stages": STAGES, "cells": cells, "results": results,
        "shared_path": "CDP DOM snapshot, the Agent's target.snapshot and the surface's rows all run snapshot_script through the same realm eval, String crossing and serde_json parse (eval_json); surface input uses act_script through the same eval; target.inspect reads only the revision",
        "limitations": ["stage samples are the host's own (proc_pid_rusage, proc_pidinfo, malloc_zone_statistics, the realm's arena statistics) only with --surface-court-file and --surface-court-stages 1; outside samples cross-check them",
                        "the microbench arms are lab-only equal-byte JSON shapes produced inside the realm: not a browser result",
                        "the explicit realm GC is a diagnostic, not a fix; closing the target is recorded but never counts against the hide gate",
                        "no pid, path or command line is recorded",
                        "headless by default: the surface binary runs in its no-AppKit drain mode; --visual with MINICON_SURF_ALLOW_VISIBLE_COURT=1 uses the real window child and shows windows"],
    }
    Path(args.receipt).write_text(json.dumps(receipt, indent=1, sort_keys=True) + "\n")
    print("receipt", args.receipt)
    return 0


if __name__ == "__main__":
    sys.exit(main())
