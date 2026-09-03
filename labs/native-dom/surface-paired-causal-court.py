#!/usr/bin/env python3
"""Paired causal G3 court harness (`surface-paired-causal-court-0.0.1.md`).

Two arms with an identical operation and input sequence on the same live
target: A, the real surface child (visual), and B, the headless
counterfactual (`replay:<script>` child mode, no AppKit). This revision
runs arm B only. Arm A is not implemented here and is refused: it needs
`--visual`, `MINICON_SURF_ALLOW_VISIBLE_COURT=1` and the OWNER's explicit
permission for that run, and no such run has been authorized. The court
refuses to start at all when the visible-court variable is set.

Without arm A the paired differential D cannot be computed and is not
computed: the receipt's status is `unverified-headless-counterfactual`,
arm A's fields are `not_observed`, the thresholds' evaluation is
`pending-owner-authorized-visual`, and the frozen court's absolute S2 and
S3 readings are quoted by reference. Nothing here is a pass.

Sequence per round (both arms by design; here B): surface.show → the
`shown` event → the replayed click bound to frame 1 (`input_applied`,
`repainted`) → target.inspect → the replayed scroll bound to frame 2 →
target.inspect → the replayed scroll back bound to frame 3 →
target.inspect → memory.report → surface.hide → `hidden` → target.inspect
→ memory.report. The replay script's coordinates come from the layout of
a probe run's `shown` event on the same page, and every measured run
checks that its own `shown` layout has the same rows.
"""

import argparse
import hashlib
import importlib.util
import json
import os
import signal
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
HEADLESS = load_module("surface_headless_court", Path(__file__).with_name("surface-headless-court.py"))
PROFILE = SURFACE.PROFILE
NETWORK = SURFACE.NETWORK
RETENTION = SURFACE.RETENTION
FIXTURE_ROOT = SURFACE.FIXTURE_ROOT
VISIBLE_ENV = "MINICON_SURF_ALLOW_VISIBLE_COURT"
OWNER = "native-dom-surface"
ROUNDS = 3
SEQUENCE = ["surface.show", "input:click(frame 1)", "target.inspect", "input:scroll(frame 2)", "target.inspect", "input:scroll-back(frame 3)",
            "target.inspect", "memory.report", "surface.hide", "target.inspect", "memory.report"]
SHOW_STAGES = ["show_entry", "after_snapshot", "after_painter", "after_command_spawn", "after_reader_thread", "after_hello_ready",
               "after_first_frame_ack", "shown", "hide_entry", "after_close_reap_join", "after_frame_drop"]
THRESHOLDS = {"D_post_hide_footprint_bytes_max": 65536, "D_slope_footprint_bytes_max": 32768, "D_owner_bytes": 0,
              "binds": "this court only; the frozen court's S2 and S3 are unchanged and quoted by reference"}


class Host:
    def __init__(self, binary, directory, allocator, origin, surface_binary, court_file, child_mode):
        environment = dict(os.environ)
        for knob in ("MINICON_SURF_NATIVE_REALM_ZONE", "MINICON_SURF_NATIVE_REALM_ARENA", "MINICON_SURF_PROFILE_STORE", VISIBLE_ENV,
                     "http_proxy", "https_proxy", "all_proxy"):
            environment.pop(knob, None)
        if allocator == "arena":
            environment["MINICON_SURF_NATIVE_REALM_ARENA"] = "1"
        command = [binary, "serve", "--stdio", "--fixture-root", str(FIXTURE_ROOT), "--config-dir", str(Path(directory) / "config"),
                   "--allow-origin", origin, "--surface-binary", str(surface_binary), "--surface-child-mode", child_mode,
                   "--surface-court-file", str(court_file), "--surface-court-stages", "1"]
        self.process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, env=environment)
        self.counter = 0

    def call(self, operation, arguments, deadline_ms=15000):
        self.counter += 1
        request = {"protocol": "minicon-surf.control", "version": "0.0.1", "request_id": f"req_paired_{self.counter}",
                   "deadline_ms": deadline_ms, "operation": operation, "arguments": arguments}
        check_contract.validate_request(request)
        self.process.stdin.write(json.dumps(request) + "\n")
        self.process.stdin.flush()
        line = self.process.stdout.readline()
        if not line:
            raise RuntimeError(f"host exited during {operation}")
        response = json.loads(line)
        check_contract.validate_response(response)
        return response

    def ok(self, operation, arguments, deadline_ms=15000):
        response = self.call(operation, arguments, deadline_ms)
        if not response["ok"]:
            raise RuntimeError(f"{operation} failed: {response['error']}")
        return response["result"]

    def finish(self):
        self.process.stdin.close()
        return self.process.wait(timeout=30)


def events(court_file):
    out = []
    path = Path(court_file)
    if not path.exists():
        return out
    for line in path.read_text().splitlines():
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    return out


def wait_count(court_file, predicate, count, seconds=5.0):
    """Wait until at least `count` events satisfy `predicate`; return (ok, ms)."""
    started = time.monotonic()
    while time.monotonic() - started < seconds:
        if sum(1 for e in events(court_file) if predicate(e)) >= count:
            return True, (time.monotonic() - started) * 1000
        time.sleep(0.005)
    return False, (time.monotonic() - started) * 1000


def footprint(pid):
    return RETENTION.sample_process(pid)["physical_footprint_bytes"]


def open_page(host, origin):
    profile = host.ok("profile.create", {"persistence": "ephemeral"})["profile"]
    session = host.ok("session.open", {"profile": profile})["session"]
    target = host.ok("target.open", {"session": session, "url": f"{origin}/index.html"})["target"]
    # The representative page's load-time fetch settles on the first
    # evaluations after open (frozen court amendment): inspect twice.
    host.ok("target.inspect", {"target": target})
    host.ok("target.inspect", {"target": target})
    return session, target


def probe_layout(binary, surface_binary, origin):
    """One headless drain run to read the page's layout and derive the script."""
    with tempfile.TemporaryDirectory(prefix="minicon-surf-paired-probe-") as directory:
        court_file = Path(directory) / "court-only.ndjson"
        host = Host(binary, directory, "system", origin, surface_binary, court_file, "drain")
        try:
            session, target = open_page(host, origin)
            shown = host.ok("surface.show", {"target": target})
            ok, _ = wait_count(court_file, lambda e: e.get("event") == "shown", 1)
            layout = next(e for e in events(court_file) if e.get("event") == "shown")["layout"]
            host.ok("surface.hide", {"surface": shown["surface"]})
            host.ok("target.close", {"target": target})
            host.ok("session.close", {"session": session})
        finally:
            host.finish() if host.process.poll() is None else None
    rows = layout["rows"]
    button = next(r for r in rows if r["role"] == "button")
    x, y = 60, button["y"] + button["height"] // 2
    row_height = layout["frame"]["row_height"]
    script = f"1:click:{x}:{y}:0;2:scroll:{x}:{y}:{row_height};3:scroll:{x}:{y}:-{row_height}"
    return script, [(r["node"], r["role"], r["y"]) for r in rows]


def run_b(binary, surface_binary, allocator, origin, script, probe_rows):
    with tempfile.TemporaryDirectory(prefix="minicon-surf-paired-b-") as directory:
        court_file = Path(directory) / "court-only.ndjson"
        host = Host(binary, directory, allocator, origin, surface_binary, court_file, f"replay:{script}")
        rounds = []
        valid = True
        try:
            session, target = open_page(host, origin)
            time.sleep(0.05)
            headless = footprint(host.process.pid)
            requests = {"target.inspect": 0, "memory.report": 0, "surface.show": 0, "surface.hide": 0}
            for index in range(1, ROUNDS + 1):
                round_info = {"round": index}
                shown = host.call("surface.show", {"target": target}, 15000)
                requests["surface.show"] += 1
                if not shown["ok"]:
                    valid = False
                    round_info["show_error"] = shown["error"]["code"]
                    rounds.append(round_info)
                    break
                round_info["latency"] = shown["result"]["latency"]
                ok_shown, _ = wait_count(court_file, lambda e: e.get("event") == "shown", index)
                layout = [e for e in events(court_file) if e.get("event") == "shown"][-1]["layout"]
                round_info["layout_matches_probe"] = [(r["node"], r["role"], r["y"]) for r in layout["rows"]] == probe_rows
                applied = lambda e: e.get("event") == "input_applied"  # noqa: E731
                repainted = lambda e: e.get("event") == "repainted"  # noqa: E731
                base = 3 * (index - 1)
                inputs_ok = []
                revisions = []
                for step in (1, 2, 3):
                    ok_in, ms_in = wait_count(court_file, applied, base + step)
                    ok_re, _ = wait_count(court_file, repainted, base + step)
                    inputs_ok.append(ok_in and ok_re)
                    inspected = host.ok("target.inspect", {"target": target})
                    requests["target.inspect"] += 1
                    revisions.append(inspected["revision"])
                    if step == 1:
                        round_info["first_input_ms"] = round(ms_in, 1)
                time.sleep(0.02)
                round_info["shown_over_headless"] = footprint(host.process.pid) - headless
                report = host.ok("memory.report", {})
                requests["memory.report"] += 1
                round_info["owners_shown"] = {"bytes": report["owners"]["surfaces"]["bytes"], "frame_touched": report["owners"]["surfaces"]["frame"]["touched_bytes"]}
                hidden = host.ok("surface.hide", {"surface": shown["result"]["surface"]}, 15000)
                requests["surface.hide"] += 1
                round_info["teardown"] = hidden["teardown"]
                wait_count(court_file, lambda e: e.get("event") == "hidden", index)
                inspected = host.ok("target.inspect", {"target": target})
                requests["target.inspect"] += 1
                time.sleep(0.02)
                round_info["post_hide_over_headless"] = footprint(host.process.pid) - headless
                report = host.ok("memory.report", {})
                requests["memory.report"] += 1
                round_info["owners_post_hide"] = {"objects": report["owners"]["surfaces"]["objects"], "bytes": report["owners"]["surfaces"]["bytes"]}
                round_info["in_use_post_hide"] = report["libmalloc"]["size_in_use"]
                round_info["inputs_applied"] = inputs_ok
                round_info["revisions_after_inputs"] = revisions
                round_info["revision_after_hide"] = inspected["revision"]
                round_info["surface_after_hide"] = inspected.get("surface")
                valid = valid and all(inputs_ok) and round_info["layout_matches_probe"] and ok_shown
                rounds.append(round_info)
            host.ok("target.close", {"target": target})
            host.ok("session.close", {"session": session})
            stage_events = [e for e in events(court_file) if e.get("event") == "stage"]
            exit_code = host.finish()
        finally:
            if host.process.poll() is None:
                host.process.kill()
                host.process.wait()
    stage_rounds, current = [], None
    for event in stage_events:
        if event["stage"] == "show_entry":
            current = {}
            stage_rounds.append(current)
        if current is not None and event["stage"] in SHOW_STAGES:
            current[event["stage"]] = {"footprint": event.get("footprint"), "in_use": event.get("in_use")}
    return {"headless": headless, "rounds": rounds, "requests": requests, "stage_rounds": stage_rounds, "valid": valid, "exit_code": exit_code}


def summarize(values):
    values = [v for v in values if v is not None]
    return {"median": int(statistics.median(values)), "minimum": min(values), "maximum": max(values)} if values else None


def summarize_b(runs):
    valid = [r for r in runs if r["valid"] and len(r["rounds"]) == ROUNDS]
    out = {"runs": len(runs), "valid_runs": len(valid), "exit_codes": sorted({r["exit_code"] for r in runs}),
           "requests_per_run": runs[0]["requests"] if runs else None, "rounds": []}
    for index in range(ROUNDS):
        rs = [r["rounds"][index] for r in valid]
        stage = [r["stage_rounds"][index] for r in valid if len(r["stage_rounds"]) > index]
        present = [s for s in SHOW_STAGES if all(s in sr for sr in stage)] if stage else []
        out["rounds"].append({
            "round": index + 1,
            "shown_over_headless": summarize([r["shown_over_headless"] for r in rs]),
            "post_hide_over_headless": summarize([r["post_hide_over_headless"] for r in rs]),
            "stage_retained_over_show_entry": summarize([sr["after_frame_drop"]["footprint"] - sr["show_entry"]["footprint"] for sr in stage if "after_frame_drop" in sr and "show_entry" in sr]),
            "stage_deltas": {f"{a}->{b}": summarize([sr[b]["footprint"] - sr[a]["footprint"] for sr in stage])["median"] for a, b in zip(present, present[1:])},
            "owners_post_hide_bytes": summarize([r["owners_post_hide"]["bytes"] for r in rs]),
            "frame_touched_shown": summarize([r["owners_shown"]["frame_touched"] for r in rs]),
            "inputs_applied_runs": sum(1 for r in rs if all(r["inputs_applied"])),
            "revision_advanced_by_inputs_runs": sum(1 for r in rs if r["revisions_after_inputs"][0] < r["revisions_after_inputs"][-1] or r["revisions_after_inputs"][0] >= 1),
            "teardown_exits": sorted({r["teardown"]["exit"] for r in rs}),
            "latency": {k: summarize([r["latency"][k] for r in rs]) for k in ("ready_ms", "first_frame_ms", "show_ms")},
        })
    if len(out["rounds"]) == ROUNDS and out["rounds"][0]["post_hide_over_headless"] and out["rounds"][2]["post_hide_over_headless"]:
        out["slope_post_hide"] = out["rounds"][2]["post_hide_over_headless"]["median"] - out["rounds"][0]["post_hide_over_headless"]["median"]
    return out


def frozen_reference():
    path = Path(__file__).with_name("evidence") / "native-dom-control-0.0.2-surface.json"
    if not path.exists():
        return None
    receipt = json.loads(path.read_text())
    return {"host_sha256_prefix": receipt["host_sha256"][:16], "checks_passed": receipt["checks_passed"], "checks_total": receipt["checks_total"],
            "absolute_failures": [{"check": c["check"], "detail": c.get("detail")} for c in receipt["checks"] if not c["passed"]]}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--binary", required=True)
    parser.add_argument("--surface-binary", required=True)
    parser.add_argument("--receipt", required=True)
    parser.add_argument("--repetitions", type=int, default=7)
    parser.add_argument("--warmup", type=int, default=1)
    parser.add_argument("--visual", action="store_true", help="arm A (real window): not implemented in this revision; needs the owner's permission for the run")
    args = parser.parse_args()
    if VISIBLE_ENV in os.environ:
        print(json.dumps({"passed": None, "unverified": f"{VISIBLE_ENV} is set; this revision runs the headless counterfactual only and refuses to start"}))
        return 3
    if args.visual:
        print(json.dumps({"passed": None, "unverified": "arm A (real window) is not implemented in this revision and needs the owner's permission for the run; nothing was started"}))
        return 3
    before = HEADLESS.windows_by_owner().get(OWNER, 0), len(HEADLESS.processes(OWNER))
    live = []

    def handler(signum, _frame):
        for host in live:
            try:
                if host.process.poll() is None:
                    host.process.kill()
                    host.process.wait(timeout=5)
            except Exception:  # noqa: BLE001
                pass
        sys.exit(128 + signum)
    signal.signal(signal.SIGINT, handler)
    signal.signal(signal.SIGTERM, handler)
    server = NETWORK.Server(("127.0.0.1", 0), PROFILE.ProfileHandler)
    origin = f"http://127.0.0.1:{server.server_address[1]}"
    threading.Thread(target=server.serve_forever, daemon=True).start()
    results = {}
    try:
        script, probe_rows = probe_layout(args.binary, args.surface_binary, origin)
        for allocator in ("system", "arena"):
            runs = []
            for repetition in range(args.warmup + args.repetitions):
                run = run_b(args.binary, args.surface_binary, allocator, origin, script, probe_rows)
                if repetition >= args.warmup:
                    runs.append(run)
            results[allocator] = summarize_b(runs)
            s = results[allocator]
            print(allocator, "B valid", s["valid_runs"], "/", s["runs"], "post-hide", [r["post_hide_over_headless"]["median"] if r["post_hide_over_headless"] else None for r in s["rounds"]],
                  "slope", s.get("slope_post_hide"), "retained", [r["stage_retained_over_show_entry"]["median"] if r["stage_retained_over_show_entry"] else None for r in s["rounds"]],
                  "inputs ok runs", [r["inputs_applied_runs"] for r in s["rounds"]])
    finally:
        server.shutdown()
    after = HEADLESS.windows_by_owner().get(OWNER, 0), len(HEADLESS.processes(OWNER))
    receipt = {
        "schema": "minicon-surf/native-dom-surface-paired-causal/0.0.1",
        "design": "labs/native-dom/surface-paired-causal-court-0.0.1.md",
        "status": "unverified-headless-counterfactual",
        "evaluation": "pending-owner-authorized-visual",
        "purpose": "paired causal evidence for the presentation's own retention (A real minus B counterfactual); this revision observes B only; nothing here is a pass and no cap moves",
        "host_sha256": hashlib.sha256(Path(args.binary).read_bytes()).hexdigest(),
        "surface_sha256": hashlib.sha256(Path(args.surface_binary).read_bytes()).hexdigest(),
        "sequence_per_round": SEQUENCE, "rounds": ROUNDS, "repetitions": args.repetitions, "warmup": args.warmup,
        "replay_script_bytes": len(script), "replay_events": script.count(":") // 4,
        "arms": {"A": {"status": "not_observed", "reason": "real surface child (visual): requires --visual, MINICON_SURF_ALLOW_VISIBLE_COURT=1 and the owner's explicit permission for the run; not run in this revision"},
                 "B": {"status": "observed", "child_mode": "replay (no AppKit)", "results": results}},
        "differential": {"status": "not_computable", "reason": "arm A not observed", "D_post_hide_footprint": None, "D_slope_footprint": None, "D_owner_bytes": None},
        "thresholds": THRESHOLDS,
        "frozen_court_reference": frozen_reference(),
        "hygiene": {"before": {"owner_windows": before[0], "surface_processes": before[1]}, "after": {"owner_windows": after[0], "surface_processes": after[1]},
                    "headless": before[0] == 0 and after[0] == 0 and after[1] == 0},
        "limitations": ["arm B is the surface binary's replay mode: a separate process that maps no AppKit; its tree peak is not the visual child's",
                        "the replay events are bound to frame acknowledgements, so arm A must post its real events at the same points (after shown and after each repainted) to keep the sequence identical",
                        "no pid, path, window number, coordinates beyond the replay script's row-derived values, capture or desktop content is recorded"],
    }
    Path(args.receipt).write_text(json.dumps(receipt, indent=1, sort_keys=True) + "\n")
    print(json.dumps({"status": receipt["status"], "evaluation": receipt["evaluation"], "hygiene": receipt["hygiene"]}))
    return 0 if receipt["hygiene"]["headless"] else 1


if __name__ == "__main__":
    sys.exit(main())
