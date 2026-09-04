#!/usr/bin/env python3
"""Read-only attribution of the navigation differential.

Frozen first in `navigation-attribution-0.0.1.md`. One question: where in a
navigation the differential bytes appear, and how much of the excess is the
navigation's own live or retained state rather than the allocator's and the
control plane's general churn.

This court produces no pass and no fail. It replaces no receipt, moves no
cap, and is not followed by an optimisation. Strictly headless: no surface
binary, no window, no AppKit, and it refuses to run with the visible-court
variable set.

Two arms with the same request count, deadline and target, exactly as the
soak defines them: one navigating, one reading the revision instead. Each arm
runs with the court-only in-process stage sampling on and off, so the
observer effect is measured rather than assumed. Fresh host per run, one
warm-up plus seven measured runs, both allocators, per-run values reported.
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


NAV = load_module("navigation_court", Path(__file__).with_name("navigation-court.py"))
PROFILE = NAV.PROFILE
NETWORK = NAV.NETWORK
RETENTION = NAV.RETENTION
FIXTURE_ROOT = NAV.FIXTURE_ROOT
VISIBLE_ENV = "MINICON_SURF_ALLOW_VISIBLE_COURT"
NAVIGATIONS = 128
STAGES = ["navigation_entry", "candidate_fetched", "candidate_built", "after_swap",
          "after_history_audit", "result_built"]
OWNER_KEYS = ("history_bytes", "audit_bytes", "audit_capacity_bytes", "realm_malloc_bytes",
              "document_bytes", "document_fetches")


class Host(NAV.Host):
    def __init__(self, binary, directory, allocator, origin, court_file=None):
        environment = dict(os.environ)
        for knob in ("MINICON_SURF_NATIVE_REALM_ZONE", "MINICON_SURF_NATIVE_REALM_ARENA",
                     "MINICON_SURF_PROFILE_STORE", VISIBLE_ENV, "http_proxy", "https_proxy", "all_proxy"):
            environment.pop(knob, None)
        if allocator == "arena":
            environment["MINICON_SURF_NATIVE_REALM_ARENA"] = "1"
        command = [binary, "serve", "--stdio", "--fixture-root", str(FIXTURE_ROOT),
                   "--config-dir", str(Path(directory) / "config"), "--allow-origin", origin]
        if court_file:
            # Court-only and off unless asked for: no visual path exists here.
            command += ["--surface-court-file", str(court_file), "--surface-court-stages", "1"]
        self.process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                        stderr=subprocess.DEVNULL, text=True, env=environment)
        self.counter = 0


def stages_of(court_file):
    events = []
    path = Path(court_file)
    if not path.exists():
        return events
    for line in path.read_text().splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if event.get("event") == "stage" and event.get("stage") in STAGES:
            events.append(event)
    return events


def one_run(binary, allocator, origin, navigating, sampling):
    with tempfile.TemporaryDirectory(prefix="minicon-surf-navigation-attribution-") as directory:
        court_file = Path(directory) / "court-only.ndjson" if sampling else None
        host = Host(binary, directory, allocator, origin, court_file)
        try:
            profile = host.ok("profile.create", {"persistence": "ephemeral"})["profile"]
            session = host.ok("session.open", {"profile": profile})["session"]
            target = NAV.open_target(host, session, origin, "/index.html")
            base = host.footprint()
            owners_before = host.ok("memory.report", {})["owners"]
            for index in range(1, NAVIGATIONS + 1):
                if navigating:
                    page = "/about.html" if index % 2 else "/index.html"
                    host.ok("target.navigate", {"target": target, "url": f"{origin}{page}"})
                else:
                    host.ok("target.inspect", {"target": target})
            outside = host.footprint() - base
            owners_after = host.ok("memory.report", {})["owners"]
            events = stages_of(court_file) if sampling else []
            host.ok("target.close", {"target": target})
            host.ok("session.close", {"session": session})
            owners_closed = host.ok("memory.report", {})["owners"]
            after_close = host.footprint() - base
            code = host.finish()
        finally:
            if host.process.poll() is None:
                host.process.kill()
                host.process.wait()
    # Per-stage deltas over one navigation, from the last complete round.
    rounds, current = [], None
    for event in events:
        if event["stage"] == STAGES[0]:
            current = {}
            rounds.append(current)
        if current is not None:
            current[event["stage"]] = event
    complete = [r for r in rounds if all(stage in r for stage in STAGES)]
    deltas = {}
    if complete:
        for earlier, later in zip(STAGES, STAGES[1:]):
            values = [r[later]["footprint"] - r[earlier]["footprint"] for r in complete]
            in_use = [r[later]["in_use"] - r[earlier]["in_use"] for r in complete]
            deltas[f"{earlier}->{later}"] = {
                "footprint_sum": sum(values),
                "footprint_grew_navigations": sum(1 for v in values if v > 0),
                "in_use_sum": sum(in_use),
            }
    owner_growth = {}
    for key in OWNER_KEYS:
        first = complete[0][STAGES[0]]["owners"].get(key) if complete else None
        last = complete[-1][STAGES[-1]]["owners"].get(key) if complete else None
        if isinstance(first, int) and isinstance(last, int):
            owner_growth[key] = last - first
    return {
        "outside_over_base": outside,
        "after_close_over_base": after_close,
        "navigations_sampled": len(complete),
        "stage_deltas": deltas,
        "owner_growth_across_run": owner_growth,
        "owners_live_after": {
            "history_bytes": owners_after["targets"].get("history_bytes"),
            "audit_bytes": owners_after["sessions"].get("audit_bytes"),
            "audit_capacity_bytes": owners_after["sessions"].get("audit_capacity_bytes"),
            "realm_malloc_bytes": owners_after["script_realms"].get("malloc_bytes"),
        },
        "owners_after_close": {
            "history_bytes": owners_closed["targets"].get("history_bytes"),
            "audit_bytes": owners_closed["sessions"].get("audit_bytes"),
            "realm_malloc_bytes": owners_closed["script_realms"].get("malloc_bytes"),
        },
        "owners_before": {
            "realm_malloc_bytes": owners_before["script_realms"].get("malloc_bytes"),
        },
        "exit_code": code,
    }


def median(values):
    return int(statistics.median(values)) if values else None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--binary", required=True)
    parser.add_argument("--receipt", required=True)
    parser.add_argument("--repetitions", type=int, default=7)
    parser.add_argument("--warmup", type=int, default=1)
    args = parser.parse_args()
    if VISIBLE_ENV in os.environ:
        print(json.dumps({"status": "refused", "reason": f"{VISIBLE_ENV} is set; this court is headless-only"}))
        return 3
    server = NETWORK.Server(("127.0.0.1", 0), PROFILE.ProfileHandler)
    origin = f"http://127.0.0.1:{server.server_address[1]}"
    threading.Thread(target=server.serve_forever, daemon=True).start()
    results = {}
    try:
        for allocator in ("system", "arena"):
            cells = {}
            for navigating in (True, False):
                for sampling in (True, False):
                    runs = []
                    for repetition in range(args.warmup + args.repetitions):
                        outcome = one_run(args.binary, allocator, origin, navigating, sampling)
                        if repetition >= args.warmup:
                            runs.append(outcome)
                    name = ("navigating" if navigating else "control") + ("+stages" if sampling else "")
                    cells[name] = {
                        "outside_over_base": {
                            "per_run": [r["outside_over_base"] for r in runs],
                            "median": median([r["outside_over_base"] for r in runs]),
                        },
                        "after_close_over_base": {
                            "per_run": [r["after_close_over_base"] for r in runs],
                            "median": median([r["after_close_over_base"] for r in runs]),
                        },
                        "navigations_sampled": median([r["navigations_sampled"] for r in runs]),
                        "stage_deltas_median": {
                            key: {
                                inner: median([r["stage_deltas"][key][inner] for r in runs if key in r["stage_deltas"]])
                                for inner in ("footprint_sum", "footprint_grew_navigations", "in_use_sum")
                            }
                            for key in (runs[0]["stage_deltas"] if runs and runs[0]["stage_deltas"] else {})
                        },
                        "owner_growth_across_run_median": {
                            key: median([r["owner_growth_across_run"].get(key) for r in runs
                                         if isinstance(r["owner_growth_across_run"].get(key), int)])
                            for key in OWNER_KEYS
                        },
                        "owners_live_after_median": {
                            key: median([r["owners_live_after"][key] for r in runs
                                         if isinstance(r["owners_live_after"][key], int)])
                            for key in ("history_bytes", "audit_bytes", "audit_capacity_bytes", "realm_malloc_bytes")
                        },
                        "owners_after_close_median": {
                            key: median([r["owners_after_close"][key] for r in runs
                                         if isinstance(r["owners_after_close"][key], int)])
                            for key in ("history_bytes", "audit_bytes", "realm_malloc_bytes")
                        },
                        "exit_codes": sorted({r["exit_code"] for r in runs}),
                    }
                    print(allocator, name, "outside median", cells[name]["outside_over_base"]["median"],
                          "| per run", cells[name]["outside_over_base"]["per_run"])
            differential = {
                "with_stages": cells["navigating+stages"]["outside_over_base"]["median"]
                - cells["control+stages"]["outside_over_base"]["median"],
                "without_stages": cells["navigating"]["outside_over_base"]["median"]
                - cells["control"]["outside_over_base"]["median"],
            }
            differential["observer_effect"] = differential["with_stages"] - differential["without_stages"]
            results[allocator] = {"cells": cells, "differential": differential}
    finally:
        server.shutdown()
    receipt = {
        "schema": "minicon-surf/native-dom-navigation-attribution/0.0.1",
        "design": "labs/native-dom/navigation-attribution-0.0.1.md",
        "status": "attribution-only",
        "verdict": "none: this court has no pass and no fail, moves no cap and replaces no receipt",
        "of": ["native-dom-control-0.0.2-navigation",
               "native-dom-control-0.0.2-navigation-replication",
               "native-dom-control-0.0.2-navigation-repair"],
        "host_sha256": hashlib.sha256(Path(args.binary).read_bytes()).hexdigest(),
        "navigations": NAVIGATIONS, "repetitions": args.repetitions, "warmup": args.warmup,
        "stages": STAGES, "results": results,
        "limitations": [
            "the in-process stage samples exist only with the court-only file and stage flags, which are off by default; their cost is reported as the observer effect rather than assumed away",
            "outside readings are proc_pid_rusage physical footprint on the host, taken between requests, never from the host's own report",
            "the two arms differ in the operation under test, which is the point; the request count, deadline and target are identical",
            "one hermetic origin on loopback, one page pair, macOS only; no surface, no window, no AppKit",
            "no pid, path, window or desktop fact is recorded",
        ],
    }
    Path(args.receipt).write_text(json.dumps(receipt, indent=1, sort_keys=True) + "\n")
    print(json.dumps({"status": receipt["status"],
                      "differential": {a: r["differential"] for a, r in results.items()}}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
