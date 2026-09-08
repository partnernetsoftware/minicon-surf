#!/usr/bin/env python3
"""The frozen court for profile history disclosure.

Frozen from `history-persistence-audit-0.0.1.md` §13 and the ruling that
followed it, **before the host changes, and failing until the capability
exists**. Nothing here is an implementation and nothing here is a proposal:
the numbers and the criteria are fixed now so that they cannot be chosen later
to fit whatever the code turns out to do.

The ruled shape, in one line: the profile record gains one bounded list of
committed entries, read through an **opt-in** argument on the existing
`profile.inspect`, entries carry **origin and path only**, ordered
**most-recent-first**, with **no timestamps**, and the default response stays
**byte-identical to today's**.

Two things make this court non-vacuous, and they matter more than the count:

  * **Every criterion is scored on every run.** A court that only grows its
    criteria once the capability exists cannot be said to cover them, and its
    early receipts would flatter the work. The capability criteria below fail
    today, by design, and the baseline receipt records exactly which.
  * **The privacy detector is proved to work before it is trusted.** A "no
    query leaked" check passes trivially while nothing is disclosed at all.
    So the P group plants a query, a fragment and a bare path and requires the
    detector to catch the first two and clear the third. If the detector
    stopped detecting, P fails even though D would still look clean.

Strictly headless: no surface binary, no window, no AppKit, and it refuses to
run with the visible-court variable set. One hermetic loopback origin.

Groups: G ground (must pass now and after), P detector control (must pass now),
D disclosure (must fail until the capability exists).
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

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "protocol"))
import check_contract  # noqa: E402

VISIBLE_ENV = "MINICON_SURF_ALLOW_VISIBLE_COURT"
FIXTURE_ROOT = ROOT / "labs" / "court" / "fixtures"

# Frozen before the host changes. A failure narrows the slice; it never moves
# a number here. Both come from the host's own constants (MAX_HISTORY_ENTRIES
# 8, MAX_URL_BYTES 2000) and the ruling's "8 entries / 16 KiB".
HISTORY_ENTRY_LIMIT = 8
HISTORY_TOTAL_BYTES = 16384

# Planted in a navigated URL. If any disclosure ever carries a query, this
# string is what proves it, and the P group proves the detector can see it.
PLANTED_QUERY_KEY = "mcs-court-query-must-not-leak"
PLANTED_FRAGMENT = "mcs-court-fragment-must-not-leak"


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


def discloses_more_than_origin_and_path(value):
    """The detector the D group depends on, and the P group proves.

    True when a disclosed entry carries anything the ruling forbids: a query,
    a fragment, or either planted marker. Applied only to disclosed history
    entries -- never to `target.inspect`'s current `url`, which the ruling
    leaves alone as existing browser state.
    """
    text = value if isinstance(value, str) else json.dumps(value, sort_keys=True)
    return ("?" in text or "#" in text
            or PLANTED_QUERY_KEY in text or PLANTED_FRAGMENT in text)


class Host:
    def __init__(self, binary, directory, origin, profile_root):
        environment = dict(os.environ)
        for knob in ("MINICON_SURF_NATIVE_REALM_ZONE", "MINICON_SURF_NATIVE_REALM_ARENA"):
            environment.pop(knob, None)
        environment.pop(VISIBLE_ENV, None)
        environment["MINICON_SURF_PROFILE_STORE"] = "envelope-keyfile-experiment"
        command = [binary, "serve", "--stdio", "--fixture-root", str(FIXTURE_ROOT),
                   "--config-dir", str(Path(directory) / "config"),
                   "--allow-origin", origin, "--profile-root", str(profile_root)]
        self.process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                        stderr=subprocess.DEVNULL, text=True, env=environment)
        self.counter = 0

    def raw(self, operation, arguments, validate=True, deadline=30000, version="0.0.1"):
        self.counter += 1
        request = {"protocol": "minicon-surf.control", "version": version,
                   "request_id": f"req_hd_{self.counter}", "deadline_ms": deadline,
                   "operation": operation, "arguments": arguments}
        if validate:
            try:
                check_contract.validate_request(request)
            except Exception as error:
                return {"ok": False, "error": {"code": "contract_rejected",
                                               "details": {"reason": str(error)[:120]}}}
        self.process.stdin.write(json.dumps(request) + "\n")
        self.process.stdin.flush()
        line = self.process.stdout.readline()
        if not line:
            return {"ok": False, "error": {"code": "host_exited"}}
        return json.loads(line)

    def call(self, operation, arguments, deadline=30000, version="0.0.1"):
        return self.raw(operation, arguments, validate=False, deadline=deadline, version=version)

    def ok(self, operation, arguments, deadline=30000, version="0.0.1"):
        answer = self.call(operation, arguments, deadline, version)
        if not answer.get("ok"):
            raise RuntimeError(f"{operation}: {answer.get('error')}")
        return answer["result"]

    def finish(self):
        try:
            self.process.stdin.close()
            self.process.wait(timeout=15)
        except Exception:
            self.process.kill()


def refused(answer, code):
    return (not answer.get("ok")) and answer.get("error", {}).get("code") == code


def entries_of(answer):
    """Whatever the disclosure returns, normalised. None when absent."""
    if not answer.get("ok"):
        return None
    result = answer["result"]
    for key in ("history", "history_entries", "entries"):
        value = result.get(key)
        if isinstance(value, list):
            return value
    return None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--binary", required=True)
    parser.add_argument("--receipt", required=True)
    args = parser.parse_args()
    if os.environ.get(VISIBLE_ENV):
        print(json.dumps({"passed": False, "reason": "the visible-court variable is set"}))
        return 1

    network = RETENTION.load_network_module()

    class Handler(network.Handler):
        def do_GET(self):
            path, _, _query = self.path.partition("?")
            network.Handler.hits.append(path)
            body = (b"<!doctype html><html><body><main><p id=\"s\">page</p>"
                    b"<a id=\"l\" href=\"/b.html\">go</a></main></body></html>")
            return self.reply(200, body, "text/html")

    server = network.Server(("127.0.0.1", 0), Handler)
    origin = f"http://127.0.0.1:{server.server_address[1]}"
    threading.Thread(target=server.serve_forever, daemon=True).start()
    checks = []

    def expect(name, condition, detail=None):
        checks.append({"check": name, "passed": bool(condition),
                       **({"detail": detail} if detail is not None else {})})

    # ---- P: the detector is proved before the D group leans on it. ----
    expect("P1: the detector catches a planted query",
           discloses_more_than_origin_and_path(f"http://127.0.0.1:1/a.html?{PLANTED_QUERY_KEY}=1"))
    expect("P2: the detector catches a planted fragment",
           discloses_more_than_origin_and_path(f"http://127.0.0.1:1/a.html#{PLANTED_FRAGMENT}"))
    expect("P3: the detector clears a bare origin and path",
           not discloses_more_than_origin_and_path("http://127.0.0.1:1/a.html"))
    expect("P4: the detector reads structured entries, not only strings",
           discloses_more_than_origin_and_path({"url": f"http://x/a?{PLANTED_QUERY_KEY}=1"}))

    try:
        with tempfile.TemporaryDirectory(prefix="minicon-surf-hd-") as directory:
            profile_root = Path(directory) / "profiles"
            profile_root.mkdir()
            host = Host(args.binary, directory, origin, profile_root)
            disclosure = {}
            try:
                profile = host.ok("profile.create", {"persistence": "persistent", "name": "alpha"})["profile"]
                session = host.ok("session.open", {"profile": profile})["session"]
                noisy = f"{origin}/a.html?{PLANTED_QUERY_KEY}=1#{PLANTED_FRAGMENT}"
                target = host.ok("target.open", {"session": session, "url": noisy})["target"]
                host.ok("target.inspect", {"target": target})

                # Fill past the bound: eviction must never refuse a navigation.
                refusals = []
                for index in range(HISTORY_ENTRY_LIMIT + 4):
                    answer = host.call("target.navigate",
                                       {"target": target, "url": f"{origin}/p{index}.html"},
                                       version="0.0.2")
                    if not answer.get("ok"):
                        refusals.append(answer["error"].get("code"))
                inspected = host.ok("target.inspect", {"target": target})
                ring = inspected.get("history") or {}

                expect("G1: the ring bounds at the frozen entry limit",
                       ring.get("length") == HISTORY_ENTRY_LIMIT,
                       {"length": ring.get("length"), "limit": HISTORY_ENTRY_LIMIT})
                expect("G2: eviction never refuses a navigation",
                       refusals == [], {"refusals": refusals})
                expect("G3: the ring exposes shape only, never a URL",
                       set(ring) <= {"position", "length", "can_go_back", "can_go_forward"}
                       and not discloses_more_than_origin_and_path(ring),
                       {"keys": sorted(ring)})

                default = host.call("profile.inspect", {"profile": profile})
                default_keys = sorted(default.get("result", {}))
                expect("G4: the default profile.inspect carries no history and no URL",
                       default.get("ok") and not any("hist" in k for k in default_keys)
                       and not discloses_more_than_origin_and_path(default.get("result", {})),
                       {"keys": default_keys})

                memory = host.ok("memory.report", {})
                expect("G5: memory.report names the entry limit and discloses no URL",
                       json.dumps(memory).count("history_entry_limit") == 1
                       and PLANTED_QUERY_KEY not in json.dumps(memory),
                       {"limit_named": "history_entry_limit" in json.dumps(memory)})

                # ---- D: the capability. Every one is scored, and fails today. ----
                asked = host.call("profile.inspect", {"profile": profile, "history": True})
                disclosure["asked"] = asked.get("error", {}).get("code") if not asked.get("ok") else "ok"
                shown = entries_of(asked)
                expect("D1: profile.inspect accepts the opt-in history argument",
                       asked.get("ok"), asked.get("error"))
                expect("D2: it returns a list bounded by the frozen entry limit",
                       isinstance(shown, list) and len(shown) <= HISTORY_ENTRY_LIMIT,
                       {"entries": None if shown is None else len(shown)})
                expect("D3: the disclosed list is bounded by the frozen byte budget",
                       shown is not None and len(json.dumps(shown).encode()) <= HISTORY_TOTAL_BYTES,
                       {"bytes": None if shown is None else len(json.dumps(shown).encode()),
                        "budget": HISTORY_TOTAL_BYTES})
                expect("D4: no disclosed entry carries a query or a fragment",
                       shown is not None and not any(
                           discloses_more_than_origin_and_path(entry) for entry in shown),
                       {"entries": shown if shown is not None else "absent"})
                expect("D5: the disclosed list is most-recent-first",
                       shown is not None and len(shown) >= 2
                       and json.dumps(shown[0]).find("p11") >= 0,
                       {"first": None if not shown else shown[0]})
                expect("D6: no disclosed entry carries a timestamp",
                       shown is not None and not any(
                           any("time" in k or "stamp" in k for k in entry)
                           for entry in shown if isinstance(entry, dict)),
                       {"entries": shown if shown is not None else "absent"})
                expect("D7: the absent argument and history:true are distinguishable",
                       shown is not None and not any("hist" in k for k in default_keys),
                       {"default_has_history": any("hist" in k for k in default_keys),
                        "asked_has_entries": shown is not None})

                # readonly: the ring moves, the record must not.
                host.call("target.close", {"target": target})
                host.call("session.close", {"session": session})
                ro = host.call("session.open", {"profile": profile, "mode": "readonly"})
                ro_grew = None
                before = after = None
                if ro.get("ok"):
                    ro_session = ro["result"]["session"]
                    before = entries_of(host.call("profile.inspect", {"profile": profile, "history": True}))
                    ro_target = host.call("target.open", {"session": ro_session, "url": f"{origin}/ro.html"})
                    if ro_target.get("ok"):
                        host.call("target.close", {"target": ro_target["result"]["target"]})
                    after = entries_of(host.call("profile.inspect", {"profile": profile, "history": True}))
                    ro_grew = (before is not None and after is not None and after != before)
                    host.call("session.close", {"session": ro_session})
                # A disclosure that does not exist cannot be observed not to
                # grow. Requiring the list on both sides is what stops this
                # criterion passing vacuously before the capability lands.
                expect("D8: a readonly session navigates without growing the disclosure",
                       isinstance(before, list) and isinstance(after, list) and ro_grew is False,
                       {"grew": ro_grew, "readonly_opened": ro.get("ok"),
                        "observable": isinstance(before, list) and isinstance(after, list)})

                # ephemeral: an explicit no-op, never a bare empty list.
                eph = host.ok("profile.create", {"persistence": "ephemeral"})["profile"]
                eph_answer = host.call("profile.inspect", {"profile": eph, "history": True})
                eph_entries = entries_of(eph_answer)
                eph_said = json.dumps(eph_answer.get("result", {}))
                expect("D9: an ephemeral profile says nothing is persisted, not a bare empty list",
                       eph_entries == [] and ("ephemeral" in eph_said or "persist" in eph_said),
                       {"result": eph_answer.get("result") if eph_answer.get("ok") else eph_answer.get("error")})

                # fork: inherits cookies, storage and policy -- never history.
                fork = host.call("profile.create",
                                 {"persistence": "persistent", "name": "delta", "from": profile})
                fork_entries = None
                if fork.get("ok"):
                    fork_entries = entries_of(host.call(
                        "profile.inspect", {"profile": fork["result"]["profile"], "history": True}))
                expect("D10: a fork inherits none of the parent's history",
                       fork_entries == [], {"fork_entries": fork_entries})
                expect("D11: a download never enters the disclosure",
                       shown is not None and not any("download" in json.dumps(e) for e in shown),
                       {"entries": shown if shown is not None else "absent"})
            finally:
                host.finish()

            # persistence and the corrupt-record path, across a restart.
            second = Host(args.binary, directory, origin, profile_root)
            try:
                adopted = None
                for record in second.ok("profile.list", {}).get("profiles", []):
                    if record.get("name") == "alpha":
                        adopted = record.get("profile")
                expect("G6: the persistent profile is still adopted at startup",
                       adopted is not None, {"adopted": adopted})
                restored = None
                if adopted is not None:
                    restored = entries_of(second.call(
                        "profile.inspect", {"profile": adopted, "history": True}))
                expect("D12: the disclosure survives a restart",
                       isinstance(restored, list) and len(restored) > 0,
                       {"restored": None if restored is None else len(restored)})
                expect("D13: nothing restored carries a query or a fragment",
                       isinstance(restored, list) and restored
                       and not any(discloses_more_than_origin_and_path(e) for e in restored),
                       {"restored": restored})
            finally:
                second.finish()

            # A corrupt persisted list must be refused at adoption, with no
            # softer path than a corrupt record already gets.
            for record in profile_root.rglob("*"):
                if record.is_file():
                    record.write_bytes(b"corrupt-not-a-sealed-record")
            third = Host(args.binary, directory, origin, profile_root)
            try:
                listed = third.call("profile.list", {})
                available = [p for p in (listed.get("result", {}).get("profiles") or [])
                             if p.get("available")]
                expect("G7: a corrupt record is refused at adoption, not softened",
                       listed.get("ok") and available == [],
                       {"available": available})
            finally:
                third.finish()
    finally:
        server.shutdown()

    receipt = {
        "court": "native-dom profile history disclosure (control 0.0.1 arguments)",
        "host_sha256": hashlib.sha256(Path(args.binary).read_bytes()).hexdigest(),
        "frozen": {
            "entry_limit": HISTORY_ENTRY_LIMIT,
            "total_bytes": HISTORY_TOTAL_BYTES,
            "shape": "opt-in history argument on the existing profile.inspect; no new operation",
            "entries": "origin and path only; never a query, a fragment or a timestamp",
            "order": "most-recent-first",
            "default_response": "byte-identical to today's profile.inspect",
            "refused": "reopen-target restoration; target ids are reused across restarts",
        },
        "checks": checks,
        "checks_passed": sum(1 for c in checks if c["passed"]),
        "checks_total": len(checks),
        "passed": all(c["passed"] for c in checks),
        "limitations": [
            "design-frozen court: the D group fails until profile.inspect accepts the opt-in history argument, and the baseline receipt records exactly which criteria that is",
            "every criterion is scored on every run, so the criterion count does not grow when the capability lands; only the pass count does",
            "the P group is the honest control for the D group: while nothing is disclosed, a query check passes vacuously, so the detector is proved against a planted query, a planted fragment and a bare path before D relies on it",
            "D8's readonly arm can only observe what the disclosure returns; until D1 passes it cannot distinguish 'did not grow' from 'nothing to grow'. It is scored FAILING on that ambiguity, and that is a repair: as first written it compared two absent lists, found them equal, and PASSED vacuously -- the defect the P group exists to prevent, inside the court that carries the P group. It now requires the list on both sides",
            "the G group is not vacuous by construction, and this was observed rather than argued: while this court was being written its navigations were sent under 0.0.1, where target.navigate does not exist, and G1 and G2 failed (ring length 1, twelve invalid_request refusals). The ground criteria can fail, and did",
            "the atomic-commit criterion is covered by G7 and D12 together (a corrupt record is refused, and what survives a restart is what the record holds); forcing a mid-commit failure needs the profile court's fault injection and is out of this court's scope",
            "target.inspect's current url still carries its query by the ruling that leaves existing browser state alone; the detector is never applied to it",
            "one hermetic loopback origin, macOS only; no surface, no window, no AppKit",
        ],
    }
    Path(args.receipt).write_text(json.dumps(receipt, indent=1, sort_keys=True) + "\n")
    print(json.dumps({"passed": receipt["passed"], "checks_passed": receipt["checks_passed"],
                      "checks_total": receipt["checks_total"]}))
    for check in checks:
        if not check["passed"]:
            print("FAIL", json.dumps(check)[:160])
    return 0 if receipt["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
