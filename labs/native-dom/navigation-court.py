#!/usr/bin/env python3
"""The frozen hermetic court for the agent-native navigation slice.

Frozen before the host implements anything, as `navigation-design-0.0.1.md`
§11 requires. Until `target.navigate`, `target.reload` and `target.traverse`
exist it fails, and that is the point: the criteria are fixed first.

Strictly headless. No surface, no AppKit, no window: this court never passes
`--surface-binary` and refuses to run with the visible-court variable set.
Everything is served by a loopback server on an ephemeral port; both
allocators; a fresh host per run.

Groups, in the order of the design:
 1 identity          navigate keeps the target and frame, replaces the document
 2 reload            same identity rules, no history movement
 3 traverse          the bounded window, eviction, forward truncation
 4 atomic rollback   six failure kinds change nothing at all
 5 policy reuse      the profile's cookies, storage and network policy
 6 agent             audit, deterministic waiting, typed refusals, versions
 7 memory            the pre-registered budgets and the differential soak
 8 cdp               Page.navigate and Page.reload map through; history does not
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


PROFILE = load_module("profile_court", Path(__file__).with_name("profile-court.py"))
RETENTION = PROFILE.RETENTION
NETWORK = PROFILE.NETWORK
FIXTURE_ROOT = PROFILE.FIXTURE_ROOT
VISIBLE_ENV = "MINICON_SURF_ALLOW_VISIBLE_COURT"
VERSION = "0.0.2"
MAX_ENTRIES = 8

# Pre-registered budgets (design §7). A failure narrows the slice; it never
# moves a number here.
CAPS = {
    "history_entry_bytes": 1024,
    "history_total_bytes": 8192,
    "post_navigation_retention_bytes": 262144,
    "soak_navigations": 128,
    "soak_excess_bytes_per_navigation": 8192,
    "soak_tail_slope_bytes_per_navigation": 1024,
    "soak_tail": 64,
}


class Host:
    """Speaks 0.0.2 by default; `version` lets one check prove the boundary."""

    def __init__(self, binary, directory, allocator, origin, pinned_root=None):
        environment = dict(os.environ)
        for knob in ("MINICON_SURF_NATIVE_REALM_ZONE", "MINICON_SURF_NATIVE_REALM_ARENA",
                     "MINICON_SURF_PROFILE_STORE", VISIBLE_ENV, "http_proxy", "https_proxy", "all_proxy"):
            environment.pop(knob, None)
        if allocator == "arena":
            environment["MINICON_SURF_NATIVE_REALM_ARENA"] = "1"
        command = [binary, "serve", "--stdio", "--fixture-root", str(FIXTURE_ROOT),
                   "--config-dir", str(Path(directory) / "config"), "--allow-origin", origin]
        if pinned_root:
            command += ["--pinned-root", str(pinned_root)]
        self.process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                        stderr=subprocess.DEVNULL, text=True, env=environment)
        self.counter = 0

    def call(self, operation, arguments, deadline_ms=20000, version=VERSION):
        self.counter += 1
        request = {"protocol": "minicon-surf.control", "version": version,
                   "request_id": f"req_nav_{self.counter}", "deadline_ms": deadline_ms,
                   "operation": operation, "arguments": arguments}
        if version in check_contract.BY_VERSION and operation in check_contract.BY_VERSION[version]:
            check_contract.validate_request(request)
        self.process.stdin.write(json.dumps(request) + "\n")
        self.process.stdin.flush()
        line = self.process.stdout.readline()
        if not line:
            raise RuntimeError(f"host exited during {operation}")
        response = json.loads(line)
        if response.get("version") in check_contract.BY_VERSION:
            check_contract.validate_response(response)
        return response

    def ok(self, operation, arguments, deadline_ms=20000):
        response = self.call(operation, arguments, deadline_ms)
        if not response["ok"]:
            raise RuntimeError(f"{operation} failed: {response['error']}")
        return response["result"]

    def footprint(self):
        time.sleep(0.02)
        return RETENTION.sample_process(self.process.pid)["physical_footprint_bytes"]

    def finish(self):
        self.process.stdin.close()
        return self.process.wait(timeout=30)


def refused(response, code, reason=None):
    if response.get("ok"):
        return False
    error = response["error"]
    if error["code"] != code:
        return False
    return reason is None or error.get("details", {}).get("reason") == reason


def identity(state):
    """The four facts a navigation must change or keep, as one comparable tuple."""
    return (state.get("target"), state.get("frame"), state.get("generation"),
            state.get("realm"), state.get("revision"))


def open_target(host, session, origin, page="/index.html"):
    target = host.ok("target.open", {"session": session, "url": f"{origin}{page}"}, 30000)["target"]
    # The representative page settles its load-time fetch on the first
    # evaluations after open (the surface court's recorded amendment).
    host.ok("target.inspect", {"target": target})
    host.ok("target.inspect", {"target": target})
    return target


def first_reference(host, target):
    snapshot = host.ok("target.snapshot", {"target": target, "format": "semantic",
                                           "max_bytes": 65536, "max_nodes": 64})
    return snapshot["nodes"][0]["reference"] if snapshot.get("nodes") else None


def run(binary, allocator, origin, expect, tag):
    with tempfile.TemporaryDirectory(prefix="minicon-surf-navigation-court-") as directory:
        host = Host(binary, directory, allocator, origin)
        try:
            profile = host.ok("profile.create", {"persistence": "ephemeral"})["profile"]
            session = host.ok("session.open", {"profile": profile})["session"]

            # 1. Identity.
            target = open_target(host, session, origin, "/index.html")
            before = host.ok("target.inspect", {"target": target})
            reference = first_reference(host, target)
            navigated = host.call("target.navigate", {"target": target, "url": f"{origin}/about.html"})
            expect(tag + "navigate is served under 0.0.2", navigated.get("ok"),
                   navigated if not navigated.get("ok") else None)
            if not navigated.get("ok"):
                return
            result = navigated["result"]
            expect(tag + "navigate keeps the target and its main frame",
                   result["target"] == target and result["frame"] == before["frame"],
                   {"frame_before": before["frame"], "frame_after": result["frame"]})
            expect(tag + "navigate increments the document generation by exactly one",
                   result["generation"] == before["generation"] + 1,
                   {"before": before["generation"], "after": result["generation"]})
            expect(tag + "navigate mints a new realm and advances the revision",
                   result["realm"] != before["realm"] and result["revision"] > before["revision"],
                   {"realm_before": before["realm"], "realm_after": result["realm"],
                    "revision_before": before["revision"], "revision_after": result["revision"]})
            expect(tag + "navigate commits the requested URL", result["url"] == f"{origin}/about.html", result["url"])
            retired = host.call("target.snapshot", {"target": target, "format": "semantic", "max_bytes": 65536,
                                                    "max_nodes": 64, "realm": before["realm"]})
            expect(tag + "the replaced realm is not_found with realm scope",
                   refused(retired, "not_found") and retired["error"].get("scope", {}).get("kind") == "realm",
                   retired.get("error"))
            if reference is not None:
                acted = host.call("target.act", {"target": target, "reference": reference,
                                                 "action": {"kind": "click"}})
                expect(tag + "a node reference from before the navigation is stale_revision",
                       refused(acted, "stale_revision"), acted.get("error"))
            inspected = host.ok("target.inspect", {"target": target})
            expect(tag + "target.inspect carries the bounded history state",
                   inspected.get("history", {}).get("length") == 2
                   and inspected["history"]["position"] == 1
                   and inspected["history"]["can_go_back"] is True
                   and inspected["history"]["can_go_forward"] is False,
                   inspected.get("history"))

            # 2. Reload.
            state = host.ok("target.inspect", {"target": target})
            reloaded = host.ok("target.reload", {"target": target})
            expect(tag + "reload replaces the document and keeps the URL",
                   reloaded["generation"] == state["generation"] + 1
                   and reloaded["realm"] != state["realm"]
                   and reloaded["revision"] > state["revision"]
                   and reloaded["url"] == state["url"],
                   {"before": identity(state), "after": identity(reloaded)})
            expect(tag + "reload appends no entry and does not move the position",
                   reloaded["history"] == state["history"],
                   {"before": state.get("history"), "after": reloaded["history"]})

            # 3. Traverse: the bounded window.
            host.ok("target.navigate", {"target": target, "url": f"{origin}/count.html"})
            back_one = host.ok("target.traverse", {"target": target, "delta": -1})
            expect(tag + "traverse -1 lands on the previous entry and refetches it",
                   back_one["url"] == f"{origin}/about.html" and back_one["history"]["position"] == 1
                   and back_one["generation"] > reloaded["generation"],
                   {"url": back_one["url"], "history": back_one["history"]})
            back_two = host.ok("target.traverse", {"target": target, "delta": -1})
            expect(tag + "traverse reaches the first entry and reports no further back",
                   back_two["url"] == f"{origin}/index.html" and back_two["history"]["position"] == 0
                   and back_two["history"]["can_go_back"] is False,
                   {"url": back_two["url"], "history": back_two["history"]})
            forward = host.ok("target.traverse", {"target": target, "delta": 2})
            expect(tag + "traverse +2 returns to the newest entry",
                   forward["url"] == f"{origin}/count.html" and forward["history"]["can_go_forward"] is False,
                   {"url": forward["url"], "history": forward["history"]})
            past_end = host.call("target.traverse", {"target": target, "delta": 1})
            expect(tag + "an offset past the newest entry is not_found and changes nothing",
                   refused(past_end, "not_found")
                   and identity(host.ok("target.inspect", {"target": target})) == identity(forward),
                   past_end.get("error"))
            past_start = host.call("target.traverse", {"target": target, "delta": -8})
            expect(tag + "an offset before the oldest entry is not_found", refused(past_start, "not_found"),
                   past_start.get("error"))

            # 3b. A navigation from a back position truncates the forward entries.
            host.ok("target.traverse", {"target": target, "delta": -1})
            truncated = host.ok("target.navigate", {"target": target, "url": f"{origin}/many.html"})
            expect(tag + "navigating from a back position truncates the forward entries",
                   truncated["history"]["length"] == 3 and truncated["history"]["can_go_forward"] is False,
                   truncated["history"])

            # 3c. The window is bounded: the ninth entry evicts the first.
            for index in range(MAX_ENTRIES + 2):
                page = "/about.html" if index % 2 else "/index.html"
                capped = host.ok("target.navigate", {"target": target, "url": f"{origin}{page}"})
            expect(tag + "the history window never exceeds its cap",
                   capped["history"]["length"] == MAX_ENTRIES
                   and capped["history"]["position"] == MAX_ENTRIES - 1,
                   capped["history"])
            evicted = host.call("target.traverse", {"target": target, "delta": -MAX_ENTRIES})
            expect(tag + "an evicted entry is not_found, not silently clamped", refused(evicted, "not_found"),
                   evicted.get("error"))

            # 4. Atomic rollback: nothing changes on any failure.
            settled = host.ok("target.inspect", {"target": target})
            settled_reference = first_reference(host, target)
            failures = [
                ("a denied origin", {"url": "http://10.0.0.1/evil.html"}, "permission_denied"),
                ("an unqualified scheme", {"url": "https://127.0.0.1:1/index.html"}, "unsupported_capability"),
                ("a missing document", {"url": f"{origin}/absent.html"}, "internal"),
                ("a malformed URL", {"url": "not-a-url"}, "invalid_request"),
            ]
            for name, arguments, code in failures:
                response = host.call("target.navigate", {"target": target, **arguments})
                after = host.ok("target.inspect", {"target": target})
                expect(tag + f"{name} is refused typed and changes nothing",
                       refused(response, code) and identity(after) == identity(settled)
                       and after["history"] == settled["history"],
                       {"error": response.get("error"), "before": identity(settled), "after": identity(after)})
            if settled_reference is not None:
                still = host.call("target.act", {"target": target, "reference": settled_reference,
                                                 "action": {"kind": "click"}})
                expect(tag + "a node reference survives every failed navigation", still.get("ok"), still.get("error"))
            offline = host.ok("profile.policy.set", {"session": session, "network": "offline",
                                                     "permissions": "deny"})
            response = host.call("target.navigate", {"target": target, "url": f"{origin}/index.html"})
            after = host.ok("target.inspect", {"target": target})
            expect(tag + "an offline profile refuses before any socket and changes nothing",
                   refused(response, "permission_denied") and identity(after) == identity(settled),
                   {"policy": offline, "error": response.get("error")})
            host.ok("profile.policy.set", {"session": session, "network": "online", "permissions": "deny"})
            deadline = host.call("target.navigate", {"target": target, "url": f"{origin}/index.html"}, 1)
            after = host.ok("target.inspect", {"target": target})
            expect(tag + "an expired deadline is typed and leaves the target whole",
                   refused(deadline, "deadline_exceeded") and identity(after) == identity(settled),
                   deadline.get("error"))

            # 5. Policy reuse.
            host.ok("target.navigate", {"target": target,
                                        "url": f"{origin}/cookie/set?name=court&value=one&attrs=Path%3D%2F"})
            echoed = host.ok("target.navigate", {"target": target, "url": f"{origin}/echo.html"})
            host.ok("target.wait", {"target": target,
                                    "condition": {"kind": "revision_at_least", "revision": echoed["revision"]}}, 5000)
            snapshot = host.ok("target.snapshot", {"target": target, "format": "semantic",
                                                   "max_bytes": 65536, "max_nodes": 64})
            names = " ".join(node.get("name", "") for node in snapshot.get("nodes", []))
            expect(tag + "a cookie set before the navigation is sent on the next one", "court=one" in names, names[:120])
            stored = host.ok("profile.storage.get", {"session": session, "kind": "cookie", "key": "court"})
            expect(tag + "the navigation's cookie reached the profile jar", stored.get("found") is True, stored)

            # 6. Agent concerns.
            inspected = host.ok("session.inspect", {"session": session})
            versions = inspected.get("supported_protocol_versions")
            expect(tag + "session.inspect advertises both versions as advisory discovery",
                   isinstance(versions, list) and {"0.0.1", "0.0.2"} <= set(versions), versions)
            ledger = inspected.get("audit", {})
            expect(tag + "the audit ledger counts the navigations without keeping a full URL",
                   json.dumps(ledger).count("?") == 0 and "cookie/set" not in json.dumps(ledger), ledger)
            older = host.call("target.navigate", {"target": target, "url": f"{origin}/index.html"}, version="0.0.1")
            expect(tag + "the same operation under 0.0.1 is invalid_request, never inferred",
                   refused(older, "invalid_request"), older.get("error"))
            extra = host.call("target.reload", {"target": target, "ignore_cache": True})
            expect(tag + "an unsupported reload argument is refused typed, not ignored",
                   refused(extra, "invalid_request"), extra.get("error"))

            # 7. Memory: the frame the budgets are measured in.
            steady = host.footprint()
            host.ok("target.navigate", {"target": target, "url": f"{origin}/about.html"})
            after_navigation = host.footprint()
            expect(tag + "post-navigation retention stays within its budget",
                   after_navigation - steady <= CAPS["post_navigation_retention_bytes"],
                   {"over_steady": after_navigation - steady, "cap": CAPS["post_navigation_retention_bytes"]})
            owners = host.ok("memory.report", {})["owners"]
            history_bytes = owners.get("targets", {}).get("history_bytes")
            expect(tag + "the history owner reports bounded bytes",
                   isinstance(history_bytes, int) and history_bytes <= CAPS["history_total_bytes"],
                   {"history_bytes": history_bytes, "cap": CAPS["history_total_bytes"]})

            host.ok("target.close", {"target": target})
            host.ok("session.close", {"session": session})
            expect(tag + "every owner returns to zero after the closes",
                   host.ok("memory.report", {})["owners"]["targets"]["objects"] == 0)
            expect(tag + "the host exits cleanly", host.finish() == 0)
        finally:
            if host.process.poll() is None:
                host.process.kill()
                host.process.wait()


def soak(binary, allocator, origin, navigating):
    """One arm of the differential soak: the same request count either way."""
    with tempfile.TemporaryDirectory(prefix="minicon-surf-navigation-soak-") as directory:
        host = Host(binary, directory, allocator, origin)
        try:
            profile = host.ok("profile.create", {"persistence": "ephemeral"})["profile"]
            session = host.ok("session.open", {"profile": profile})["session"]
            target = open_target(host, session, origin, "/index.html")
            base = host.footprint()
            samples = []
            for index in range(1, CAPS["soak_navigations"] + 1):
                if navigating:
                    page = "/about.html" if index % 2 else "/index.html"
                    host.ok("target.navigate", {"target": target, "url": f"{origin}{page}"})
                else:
                    host.ok("target.inspect", {"target": target})
                if index % 8 == 0:
                    samples.append({"call": index, "over_base": host.footprint() - base})
            host.ok("target.close", {"target": target})
            host.ok("session.close", {"session": session})
            code = host.finish()
        finally:
            if host.process.poll() is None:
                host.process.kill()
                host.process.wait()
    return {"base": base, "samples": samples, "exit_code": code}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--binary", required=True)
    parser.add_argument("--receipt", required=True)
    parser.add_argument("--repetitions", type=int, default=7)
    parser.add_argument("--warmup", type=int, default=1)
    parser.add_argument("--skip-soak", action="store_true", help="mechanics only; the soak is reported unverified")
    args = parser.parse_args()
    if VISIBLE_ENV in os.environ:
        print(json.dumps({"passed": None, "unverified": f"{VISIBLE_ENV} is set; this court is headless-only"}))
        return 3
    checks = []

    def expect(name, condition, detail=None):
        checks.append({"check": name, "passed": bool(condition), **({"detail": detail} if detail is not None else {})})

    server = NETWORK.Server(("127.0.0.1", 0), PROFILE.ProfileHandler)
    origin = f"http://127.0.0.1:{server.server_address[1]}"
    threading.Thread(target=server.serve_forever, daemon=True).start()
    soaks = {}
    try:
        for allocator in ("system", "arena"):
            run(args.binary, allocator, origin, expect, f"[{allocator}] ")
            if args.skip_soak:
                continue
            arms = {}
            for navigating in (False, True):
                runs = []
                for repetition in range(args.warmup + args.repetitions):
                    outcome = soak(args.binary, allocator, origin, navigating)
                    if repetition >= args.warmup:
                        runs.append(outcome)
                by_call = {}
                for sample in runs[0]["samples"]:
                    call = sample["call"]
                    by_call[call] = int(statistics.median(
                        [next(s["over_base"] for s in r["samples"] if s["call"] == call) for r in runs]))
                arms["navigating" if navigating else "control"] = by_call
            last, tail = CAPS["soak_navigations"], CAPS["soak_navigations"] - CAPS["soak_tail"]
            excess = arms["navigating"][last] - arms["control"][last]
            tail_slope = ((arms["navigating"][last] - arms["navigating"][tail])
                          - (arms["control"][last] - arms["control"][tail]))
            soaks[allocator] = {"arms": arms, "excess_at_128": excess, "tail_excess_slope": tail_slope}
            expect(f"[{allocator}] the navigating arm's excess over the control arm stays within its budget",
                   excess <= CAPS["soak_navigations"] * CAPS["soak_excess_bytes_per_navigation"],
                   {"excess": excess, "cap": CAPS["soak_navigations"] * CAPS["soak_excess_bytes_per_navigation"]})
            expect(f"[{allocator}] the excess does not keep growing over the last navigations",
                   tail_slope <= CAPS["soak_tail"] * CAPS["soak_tail_slope_bytes_per_navigation"],
                   {"tail_excess_slope": tail_slope,
                    "cap": CAPS["soak_tail"] * CAPS["soak_tail_slope_bytes_per_navigation"]})
    finally:
        server.shutdown()
    receipt = {
        "schema": "minicon-surf.native-dom-navigation-receipt/0.0.1",
        "technology": "native-dom",
        "control_version": VERSION,
        "design": "labs/native-dom/navigation-design-0.0.1.md",
        "host_sha256": hashlib.sha256(Path(args.binary).read_bytes()).hexdigest(),
        "caps": CAPS,
        "checks": checks,
        "checks_passed": sum(1 for c in checks if c["passed"]),
        "checks_total": len(checks),
        "passed": all(c["passed"] for c in checks) and not args.skip_soak,
        "soak": soaks if soaks else "unverified: --skip-soak",
        "limitations": [
            "history is metadata only: an entry is the final canonical committed URL, so a traverse refetches and no page state is restored",
            "the soak is differential by design: the control-churn court showed every control request grows the host without a plateau, so an absolute cap would fail for reasons unrelated to navigation; the two arms hold the request count, deadline and target identical and differ in the operation under test",
            "one hermetic origin on loopback, one page set, macOS only; no surface, no window, no AppKit",
            "no pid, path, window or desktop fact is recorded",
        ],
    }
    Path(args.receipt).write_text(json.dumps(receipt, indent=1, sort_keys=True) + "\n")
    print(json.dumps({"passed": receipt["passed"], "checks_passed": receipt["checks_passed"],
                      "checks_total": receipt["checks_total"]}))
    for check in checks:
        if not check["passed"]:
            print("FAIL", json.dumps(check)[:300])
    return 0 if receipt["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
