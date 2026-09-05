#!/usr/bin/env python3
"""The shim-split court: does moving main-only page surface out of every
realm's shim actually return memory, and does it return it in the process and
not merely in the ledger?

Frozen before the product code exists, and it fails against the build it was
written on. Two binaries run the same arms in the same process order — the
candidate and the exact baseline it must beat — because a recovery is a
difference between builds, not a number read once.

Strictly headless: no surface, no window, no AppKit, one hermetic loopback
origin, both allocators, supervised hosts with the wall-clock kill.

  shim-footprint-court.py --binary CANDIDATE --baseline BASELINE --receipt OUT
      [--candidate-build-seconds S] [--baseline-build-seconds S]
"""

import argparse
import hashlib
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "protocol"))
import check_contract  # noqa: E402,F401

VISIBLE_ENV = "MINICON_SURF_ALLOW_VISIBLE_COURT"

# The child-frame caps, for the record. They are proven by the child-frame
# court, on its fixtures and in its order, not restated here (see §21).
CAP_M1 = 262144
CAP_M2 = 1835008
# The recovered margin, frozen by ruling: at least 16 KiB of M1 headroom is a
# standing floor, not a budget to spend, and seven children keep seven times
# that.
FLOOR_M1 = 245760
FLOOR_M2 = 1720320
# Bytes must leave the process, not just the accounting. The figure is
# page-granular and varies between runs of one binary, so it is read over
# many realms, in fresh hosts, and every candidate host must sit below every
# baseline host by this much.
FOOTPRINT_RECOVERY = 16384
FOOTPRINT_TARGETS = 4
FOOTPRINT_HOSTS = 3
# What the main realm may pay for the split, against the same baseline.
MAIN_SLACK = 65536


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
JOBS = load_module("job_deadline_court", Path(__file__).with_name("job-deadline-court.py"))


def page(title, bodies, script=""):
    return ("<!doctype html><html><body><main><h1>" + title + "</h1>"
            + "".join(bodies) + "</main>"
            + (f"<script>{script}</script>" if script else "")
            + "</body></html>").encode()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--binary", required=True)
    parser.add_argument("--baseline", required=True)
    parser.add_argument("--receipt", required=True)
    parser.add_argument("--candidate-build-seconds", type=float, default=None)
    parser.add_argument("--baseline-build-seconds", type=float, default=None)
    args = parser.parse_args()
    if os.environ.get(VISIBLE_ENV):
        print(json.dumps({"passed": False, "reason": "the visible-court variable is set"}))
        return 1

    network = RETENTION.load_network_module()

    class Handler(network.Handler):
        def do_GET(self):
            path, _, _query = self.path.partition("?")
            network.Handler.hits.append(path)
            # A child that carries a script: the script must still not run,
            # split or not, because this slice changes what a realm compiles
            # and never what it may run.
            if path == "/child-script.html":
                return self.reply(200, page("Child script", ['<p id="cs">embedded script</p>'],
                                            "window.__courtRan = 1;"))
            pages = {
                # These four are the child-frame court's fixtures byte for
                # byte, so M1 and M2 here are the same measurement its frozen
                # caps were written against.
                "/child-a.html": page("Child A", ['<p id="ca">embedded alpha</p>']),
                "/parent-none.html": page("Parent none", []),
                "/parent-one.html": page("Parent one", ['<iframe src="/child-a.html"></iframe>']),
                "/parent-seven.html": page(
                    "Parent seven",
                    [f'<iframe src="/child-{n}.html"></iframe>' for n in range(7)]),
                **{f"/child-{n}.html": page(f"Child {n}", [f'<p>embedded {n}</p>'])
                   for n in range(7)},
                "/parent-script-child.html": page(
                    "Parent script child", ['<iframe src="/child-script.html"></iframe>']),
                # The main-only arm: page script, timers, storage and a
                # location read, so the extension's whole surface is live.
                "/main-only.html": page(
                    "Main only", ["<p id='m'>start</p>"],
                    "var t = setTimeout(function(){}, 100000);"
                    "try { localStorage.setItem('k', 'v'); } catch (e) {}"
                    "document.getElementById('m').textContent = String(location.pathname).length;"
                    "window.addEventListener('load', function(){});"),
            }
            if path in pages:
                return self.reply(200, pages[path])
            return self.reply(404, b"gone")

    server = network.Server(("127.0.0.1", 0), Handler)
    origin = f"http://127.0.0.1:{server.server_address[1]}"
    threading.Thread(target=server.serve_forever, daemon=True).start()
    checks = []
    killed_hosts = []
    measured = {}

    def expect(name, condition, detail=None):
        checks.append({"check": name, "passed": bool(condition),
                       **({"detail": detail} if detail is not None else {})})

    def footprint_arm(binary, allocator, label):
        """The first growth of `FOOTPRINT_TARGETS` parents of seven children in
        a host that has done nothing else.

        A process footprint does not shrink when memory is freed, so a second
        cycle in a warmed host measures the allocator's reuse and not the
        cost. And the growth of a single child sits inside the 16 KiB page the
        figure moves in: measured that way, two runs of the *same binary*
        differ by more than the threshold, which is a criterion that cannot
        fail. So the signal is made large — twenty-eight child realms — and
        read once per fresh host."""
        directory = tempfile.TemporaryDirectory(prefix="minicon-surf-shim-fp-")
        host = JOBS.Supervised(binary, directory.name, origin, allocator)
        try:
            profile = host.ok("profile.create", {"persistence": "ephemeral"})["profile"]
            session = host.ok("session.open", {"profile": profile})["session"]
            before = RETENTION.sample_process(host.pid)["physical_footprint_bytes"]
            opened = []
            for _ in range(FOOTPRINT_TARGETS):
                answer = host.call("target.open",
                                   {"session": session, "url": f"{origin}/parent-seven.html"},
                                   deadline_ms=8000)
                if not answer.get("ok"):
                    return None
                opened.append(answer["result"]["target"])
            after = RETENTION.sample_process(host.pid)["physical_footprint_bytes"]
            for target in opened:
                host.ok("target.close", {"target": target})
            return after - before
        finally:
            if host.timeouts:
                killed_hosts.append({"arm": label, "allocator": allocator,
                                     "timeouts": host.timeouts})
            host.finish()
            directory.cleanup()

    def arms(binary, allocator, label):
        """Every measurement one build owes, in one host, in one order."""
        directory = tempfile.TemporaryDirectory(prefix="minicon-surf-shim-")
        host = JOBS.Supervised(binary, directory.name, origin, allocator)
        out = {}
        try:
            def owners():
                report = host.call("memory.report", {})
                if not report.get("ok"):
                    return None
                owned = report["result"]["owners"]
                return owned["script_realms"]["malloc_bytes"] + owned["targets"]["fixture_bytes"]

            def footprint():
                return RETENTION.sample_process(host.pid)["physical_footprint_bytes"]

            def open_page(session, path):
                return host.call("target.open", {"session": session, "url": f"{origin}{path}"},
                                 deadline_ms=8000)

            profile = host.ok("profile.create", {"persistence": "ephemeral"})["profile"]
            session = host.ok("session.open", {"profile": profile})["session"]
            out["empty_owners"] = owners()
            # The parent without children is the base every child cost is
            # measured against, exactly as the child-frame court does it.
            none = open_page(session, "/parent-none.html")
            out["base_owners"] = owners()
            out["base_footprint"] = footprint()
            if none.get("ok"):
                host.ok("target.close", {"target": none["result"]["target"]})
            one = open_page(session, "/parent-one.html")
            out["m1"] = (owners() or 0) - (out["base_owners"] or 0)
            out["m1_footprint"] = footprint() - out["base_footprint"]
            if one.get("ok"):
                host.ok("target.close", {"target": one["result"]["target"]})
            seven = open_page(session, "/parent-seven.html")
            out["m2"] = (owners() or 0) - (out["base_owners"] or 0)
            if seven.get("ok"):
                host.ok("target.close", {"target": seven["result"]["target"]})

            # The main-only arm: one target, no children, the extension's
            # whole surface exercised by the page itself.
            main_page = open_page(session, "/main-only.html")
            out["main_live"] = (owners() or 0) - (out["empty_owners"] or 0)
            if main_page.get("ok"):
                host.ok("target.close", {"target": main_page["result"]["target"]})
            out["closed_owners"] = owners()
            # A child that carries a script is refused, split or not.
            scripted = open_page(session, "/parent-script-child.html")
            if scripted.get("ok"):
                inspected = host.call("target.inspect",
                                      {"target": scripted["result"]["target"]})
                result = inspected.get("result") or {}
                out["child_frames"] = len(result.get("frames") or [])
                out["frames_skipped"] = result.get("frames_skipped")
                out["scripts_skipped"] = result.get("scripts_skipped")
                out["script_count"] = result.get("script_count")
                host.ok("target.close", {"target": scripted["result"]["target"]})
        finally:
            if host.timeouts:
                killed_hosts.append({"arm": label, "allocator": allocator,
                                     "timeouts": host.timeouts})
            host.finish()
            directory.cleanup()
        return out

    try:
        for allocator in ("system", "arena"):
            tag = f"[{allocator}] "
            base = arms(args.baseline, allocator, f"baseline-{allocator}")
            cand = arms(args.binary, allocator, f"candidate-{allocator}")
            base["footprints"] = [footprint_arm(args.baseline, allocator,
                                                 f"baseline-footprint-{allocator}")
                                  for _ in range(FOOTPRINT_HOSTS)]
            cand["footprints"] = [footprint_arm(args.binary, allocator,
                                                f"candidate-footprint-{allocator}")
                                  for _ in range(FOOTPRINT_HOSTS)]
            measured[allocator] = {"baseline": base, "candidate": cand}

            # The unmoved caps themselves are the child-frame court's, and
            # they are proven there, on its fixtures and in its order: this
            # court reads about a kilobyte higher on the same build because it
            # measures in a fresh host, so asserting its numbers against those
            # caps would compare two different measurements. What this court
            # owns is the recovery.

            # 2: the recovered margin, frozen. A floor, not a budget.
            expect(tag + f"one child leaves at least 16 KiB of M1 headroom (<= {FLOOR_M1})",
                   0 < cand.get("m1", 0) <= FLOOR_M1,
                   {"m1": cand.get("m1"), "baseline": base.get("m1")})
            expect(tag + f"seven children leave seven times it (<= {FLOOR_M2})",
                   0 < cand.get("m2", 0) <= FLOOR_M2,
                   {"m2": cand.get("m2"), "baseline": base.get("m2")})
            expect(tag + "and the recovery is a real difference from the baseline build",
                   cand.get("m1", 0) < base.get("m1", 0)
                   and cand.get("m2", 0) < base.get("m2", 0),
                   {"m1_delta": base.get("m1", 0) - cand.get("m1", 0),
                    "m2_delta": base.get("m2", 0) - cand.get("m2", 0)})

            # 3: the bytes leave the process, not the ledger.
            # Every candidate host must sit below every baseline host by the
            # frozen recovery: ranges that overlap are noise, not a recovery,
            # and two builds of the same source fail this.
            base_prints = [f for f in base.get("footprints") or [] if f]
            cand_prints = [f for f in cand.get("footprints") or [] if f]
            recovered = (min(base_prints) - max(cand_prints)) if base_prints and cand_prints else 0
            expect(tag + f"{FOOTPRINT_TARGETS * 7} child realms cost at least {FOOTPRINT_RECOVERY} "
                   "fewer bytes of process footprint, every host below every baseline host",
                   len(base_prints) == FOOTPRINT_HOSTS and len(cand_prints) == FOOTPRINT_HOSTS
                   and recovered >= FOOTPRINT_RECOVERY,
                   {"recovered": recovered, "baseline": base_prints, "candidate": cand_prints})

            # 4: the main realm does not pay for it.
            slack = cand.get("main_live", 0) - base.get("main_live", 0)
            expect(tag + f"a main-only page costs no more than {MAIN_SLACK} bytes above the baseline",
                   slack <= MAIN_SLACK,
                   {"slack": slack, "baseline": base.get("main_live"),
                    "candidate": cand.get("main_live")})

            # 5: exact release.
            expect(tag + "closing every target returns the owners exactly",
                   cand.get("closed_owners") is not None
                   and cand.get("closed_owners") == cand.get("empty_owners"),
                   {"closed": cand.get("closed_owners"), "empty": cand.get("empty_owners")})

            # 6: a child still cannot run script.
            # A child is built and its script is not run: what the candidate
            # reports about a script-bearing child is what the baseline does,
            # field for field. A split that quietly enabled child script would
            # move one of these.
            expect(tag + "a script-bearing child is built exactly as before and still runs nothing",
                   cand.get("child_frames") == base.get("child_frames")
                   and cand.get("frames_skipped") == base.get("frames_skipped")
                   and cand.get("scripts_skipped") == base.get("scripts_skipped")
                   and cand.get("script_count") == base.get("script_count"),
                   {"frames": cand.get("child_frames"),
                    "scripts_skipped": cand.get("scripts_skipped"),
                    "script_count": cand.get("script_count")})

        # 7: the internals handle, on both paths. There is deliberately no
        # arbitrary eval surface in a child realm, so the only honest way to
        # ask whether a capability is still reachable there is a court-only
        # probe, constrained like every other court seam: refused before the
        # host serves anything unless the private court file is given.
        probe_directory = tempfile.TemporaryDirectory(prefix="minicon-surf-shim-probe-")
        probe_file = Path(probe_directory.name) / "court.ndjson"
        closed = subprocess.run(
            [args.binary, "serve", "--stdio", "--fixture-root", str(RETENTION.FIXTURE_ROOT),
             "--config-dir", str(Path(probe_directory.name) / "closed"),
             "--allow-origin", origin, "--court-realm-probe", "1"],
            input="", capture_output=True, text=True, timeout=30,
            env={k: v for k, v in os.environ.items() if k != VISIBLE_ENV})
        expect("the realm probe is refused without the private court file",
               closed.returncode != 0 and not closed.stdout.strip(),
               {"code": closed.returncode, "answered": len(closed.stdout)})
        directory = tempfile.TemporaryDirectory(prefix="minicon-surf-shim-sealed-")
        host = JOBS.Supervised(args.binary, directory.name, origin, "system",
                               extra=("--court-realm-probe", "1",
                                      "--surface-court-file", str(probe_file)))
        try:
            answer = host.call("profile.create", {"persistence": "ephemeral"})
            if not answer.get("ok"):
                expect("the host accepts the court-only realm probe", False,
                       {"reason": (answer.get("error") or {}).get("code", "refused")})
            else:
                profile = answer["result"]["profile"]
                session = host.ok("session.open", {"profile": profile})["session"]
                opened = host.call("target.open",
                                   {"session": session, "url": f"{origin}/parent-seven.html"},
                                   deadline_ms=8000)
                report = host.call("memory.report", {})
                probe = ((report.get("result") or {}).get("owners") or {}).get("realm_probe") or {}
                expect("the internals handle is gone from the main realm, and not enumerable",
                       opened.get("ok") is True
                       and probe.get("main_present") is False
                       and probe.get("main_enumerable") is False,
                       {"probe": probe})
                expect("and it is gone from every child realm, which runs no script to consume it",
                       probe.get("realms_probed", 0) >= 8
                       and probe.get("children_present") == 0
                       and probe.get("children_enumerable") == 0,
                       {"probe": probe})
                if opened.get("ok"):
                    host.ok("target.close", {"target": opened["result"]["target"]})
        finally:
            if host.timeouts:
                killed_hosts.append({"arm": "realm-probe", "allocator": "system",
                                     "timeouts": host.timeouts})
            host.finish()
            directory.cleanup()
            expect("the probe seam's court file is gone when the host is",
                   not probe_file.exists(), {"court_file": probe_file.exists()})
            probe_directory.cleanup()
    finally:
        server.shutdown()

    here = Path(__file__).parent
    def source_bytes(name):
        path = here / "src" / name
        return path.stat().st_size if path.exists() else None

    receipt = {
        "court": "native-dom per-realm shim split (control 0.0.2)",
        "host_sha256": hashlib.sha256(Path(args.binary).read_bytes()).hexdigest(),
        "baseline_sha256": hashlib.sha256(Path(args.baseline).read_bytes()).hexdigest(),
        "thresholds": {"cap_m1": CAP_M1, "cap_m2": CAP_M2, "floor_m1": FLOOR_M1,
                       "floor_m2": FLOOR_M2, "footprint_recovery": FOOTPRINT_RECOVERY,
                       "main_slack": MAIN_SLACK},
        "attribution": {
            "source_bytes": {"dom_shim.js": source_bytes("dom_shim.js"),
                             "dom_shim_base.js": source_bytes("dom_shim_base.js"),
                             "dom_shim_main.js": source_bytes("dom_shim_main.js")},
            "binary_bytes": {"candidate": Path(args.binary).stat().st_size,
                             "baseline": Path(args.baseline).stat().st_size},
            "build_seconds": {"candidate": args.candidate_build_seconds,
                              "baseline": args.baseline_build_seconds},
            "measured": measured,
        },
        "checks": checks,
        "checks_passed": sum(1 for c in checks if c["passed"]),
        "checks_total": len(checks),
        "passed": all(c["passed"] for c in checks) and not killed_hosts,
        "hosts_killed": killed_hosts,
        "limitations": [
            "design-frozen court: it fails until the shim is split",
            "the recovered margin is a floor to hold, not a budget to spend",
            "one hermetic loopback origin, macOS only; no surface, no window, no AppKit",
            "a host that does not answer inside its wall-clock bound is killed by pid and reaped",
            "footprint is a process figure and is page-granular; the criterion asks for one page",
        ],
    }
    Path(args.receipt).write_text(json.dumps(receipt, indent=1, sort_keys=True) + "\n")
    print(json.dumps({"passed": receipt["passed"], "checks_passed": receipt["checks_passed"],
                      "checks_total": receipt["checks_total"],
                      "hosts_killed": len(killed_hosts)}))
    for check in checks:
        if not check["passed"]:
            print("FAIL " + json.dumps(check))
    return 0 if receipt["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
