#!/usr/bin/env python3
"""The frozen court for readonly profiles.

Frozen from `readonly-profile-audit-0.0.1.md` §6 and the ruling that followed
it, before the host changes, and failing until the mode exists.

The design's whole difficulty is that `read_only` is already taken: it is the
**fail-closed latch** set when a write cannot be committed, pinned by
`profile-court.py:326`. So this court's most important criterion is not about
the new mode at all — **R5 proves the latch still behaves**, with its own
distinct refusal, on the same binary. A mode that ate the old meaning would
pass every other criterion here and fail that one.

The ruled shape: `mode` is an argument of `profile.create`, belongs to the open
and is not persisted, `ephemeral` with `readonly` is refused, the writer lock
is still taken, and the asked-for refusal is typed differently from the latch's.

Strictly headless: no surface binary, no window, no AppKit, and it refuses to
run with the visible-court variable set. One hermetic loopback origin, both
allocators, the sealed store enabled.

Groups: contract, mode, refusals, reads, isolation, latch, lifetime, lock.
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
EXAMPLES = ROOT / "protocol" / "examples"


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


class Host:
    """One host with the sealed store on, over a profile root that outlives it."""

    def __init__(self, binary, directory, allocator, origin, profile_root):
        environment = dict(os.environ)
        for knob in ("MINICON_SURF_NATIVE_REALM_ZONE", "MINICON_SURF_NATIVE_REALM_ARENA",
                     "MINICON_SURF_PROFILE_STORE"):
            environment.pop(knob, None)
        if allocator == "arena":
            environment["MINICON_SURF_NATIVE_REALM_ARENA"] = "1"
        # The default mode is the keychain envelope; the court does not ask
        # for the keyfile experiment.
        environment.pop("MINICON_SURF_PROFILE_STORE", None)
        environment.pop(VISIBLE_ENV, None)
        # The usage line is positional: --fixture-root then --config-dir.
        command = [binary, "serve", "--stdio", "--fixture-root", str(FIXTURE_ROOT),
                   "--config-dir", str(Path(directory) / "config"),
                   "--allow-origin", origin, "--profile-root", str(profile_root)]
        self.process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                        stderr=subprocess.DEVNULL, text=True, env=environment)
        self.counter = 0

    def raw(self, operation, arguments, validate=True):
        """A request the contract may itself refuse: R7 needs to reach the host."""
        self.counter += 1
        request = {"protocol": "minicon-surf.control", "version": "0.0.1",
                   "request_id": f"req_ro_{self.counter}", "deadline_ms": 30000,
                   "operation": operation, "arguments": arguments}
        if validate:
            check_contract.validate_request(request)
        self.process.stdin.write(json.dumps(request) + "\n")
        self.process.stdin.flush()
        line = self.process.stdout.readline()
        if not line:
            return {"ok": False, "error": {"code": "host_exited"}}
        return json.loads(line)

    def call(self, operation, arguments):
        return self.raw(operation, arguments)

    def ok(self, operation, arguments):
        answer = self.call(operation, arguments)
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


def reason(answer):
    return ((answer.get("error") or {}).get("details") or {}).get("reason")


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
            body = (b"<!doctype html><html><body><main><p id=\"s\">page</p></main><script>"
                    b"try{localStorage.setItem('page','written');}catch(e){}"
                    b"document.getElementById('s').textContent='ls='"
                    b"+String(localStorage.getItem('page'));</script></body></html>")
            return self.reply(200, body, "text/html")

    server = network.Server(("127.0.0.1", 0), Handler)
    origin = f"http://127.0.0.1:{server.server_address[1]}"
    threading.Thread(target=server.serve_forever, daemon=True).start()
    checks = []

    def expect(name, condition, detail=None):
        checks.append({"check": name, "passed": bool(condition),
                       **({"detail": detail} if detail is not None else {})})

    # C1/C2: the contract carries the mode, its example and its refusals.
    contract_source = (ROOT / "protocol" / "check_contract.py").read_text()
    expect("C1: the contract puts the mode on session.open and keeps it off profile.create",
           "session mode differs" in contract_source
           and "profile mode differs" not in contract_source,
           {"session_rule": "session mode differs" in contract_source,
            "create_rule_gone": "profile mode differs" not in contract_source})
    expect("C2: an example pair carries the mode on the session and leaves read_only alone",
           (EXAMPLES / "session-open-readonly.request.json").exists()
           and (EXAMPLES / "session-open-readonly.success.json").exists()
           and json.loads((EXAMPLES / "session-open-readonly.success.json").read_text())
           ["result"]["read_only"] is False,
           {"request": (EXAMPLES / "session-open-readonly.request.json").exists()})

    try:
        for allocator in ("system", "arena"):
            tag = f"[{allocator}] "
            with tempfile.TemporaryDirectory(prefix="minicon-surf-readonly-") as directory:
                root = Path(directory) / "profiles"
                root.mkdir()
                # Round one: a writable host that leaves something to read.
                host = Host(args.binary, directory, allocator, origin, root)
                try:
                    made = host.call("profile.create",
                                     {"persistence": "persistent", "name": "alpha"})
                    if not made.get("ok"):
                        expect(tag + "the sealed store is available for this court",
                               False, {"error": made.get("error")})
                        continue
                    alpha = made["result"]["profile"]
                    session = host.ok("session.open", {"profile": alpha})["session"]
                    host.ok("profile.storage.put",
                            {"session": session, "kind": "local_storage",
                             "key": "seed", "value": "written-once"})
                    host.ok("session.close", {"session": session})
                finally:
                    host.finish()

                # Round two: the same identity, opened readonly.
                host = Host(args.binary, directory, allocator, origin, root)
                try:
                    # The profile was adopted at startup: it is already listed,
                    # and create would answer conflict. The open is the session.
                    listed = host.ok("profile.list", {})["profiles"]
                    adopted = next((p["profile"] for p in listed
                                    if p.get("name") == "alpha"), None)
                    expect(tag + "R0: the profile is adopted at startup, not created again",
                           adopted is not None, {"listed": listed})
                    if adopted is None:
                        continue
                    opened = host.call("session.open",
                                       {"profile": adopted, "mode": "readonly"})
                    expect(tag + "R1: an adopted profile opens readonly and says so, without the latch",
                           opened.get("ok")
                           and opened["result"].get("mode") == "readonly"
                           and opened["result"].get("read_only") is False,
                           {"answer": opened.get("result") or opened.get("error")})
                    if not opened.get("ok"):
                        continue
                    ro = adopted
                    ro_session = opened["result"]["session"]

                    read = host.call("profile.storage.get",
                                     {"session": ro_session, "kind": "local_storage",
                                      "key": "seed"})
                    expect(tag + "R3: reads still answer from the record",
                           read.get("ok") and read["result"].get("found") is True
                           and read["result"].get("value") == "written-once",
                           {"answer": read.get("result") or read.get("error")})

                    put = host.call("profile.storage.put",
                                    {"session": ro_session, "kind": "local_storage",
                                     "key": "nope", "value": "must-not-write"})
                    policy = host.call("profile.policy.set",
                                       {"session": ro_session, "network": "offline",
                                        "permissions": "allow_by_default"})
                    expect(tag + "R2: every write is refused, and not as a failed commit",
                           (not put.get("ok")) and (not policy.get("ok"))
                           and reason(put) != "storage_commit_failed"
                           and reason(policy) != "storage_commit_failed"
                           and not refused(put, "internal"),
                           {"put": put.get("error"), "policy": policy.get("error")})

                    target = host.call("target.open",
                                       {"session": ro_session, "url": origin + "/page.html"})
                    expect(tag + "R3b: a page still opens and runs against a readonly profile",
                           target.get("ok"), {"answer": target.get("error")})
                    if target.get("ok"):
                        host.call("target.close", {"target": target["result"]["target"]})

                    # R7: an ephemeral profile has nothing to read, and the
                    # contract cannot see a profile's persistence through an
                    # opaque id, so this refusal is the host's and is pinned here.
                    scratch = host.call("profile.create", {"persistence": "ephemeral"})
                    if scratch.get("ok"):
                        ephemeral = host.call("session.open",
                                              {"profile": scratch["result"]["profile"],
                                               "mode": "readonly"})
                        expect(tag + "R7: an ephemeral profile cannot be opened readonly",
                               refused(ephemeral, "invalid_request"),
                               {"answer": ephemeral.get("error")})
                    else:
                        expect(tag + "R7: an ephemeral profile cannot be opened readonly",
                               False, {"answer": scratch.get("error")})

                    unknown = host.raw("session.open",
                                       {"profile": adopted, "mode": "sometimes"},
                                       validate=False)
                    expect(tag + "R9: an unknown mode is refused",
                           refused(unknown, "invalid_request"),
                           {"answer": unknown.get("error")})

                    # And the mode does not join profile.create, by ruling.
                    on_create = host.raw("profile.create",
                                         {"persistence": "persistent", "name": "gamma",
                                          "mode": "readonly"}, validate=False)
                    expect(tag + "R9b: profile.create does not take a mode",
                           refused(on_create, "invalid_request"),
                           {"answer": on_create.get("error")})

                    # R4: a writable sibling in the same host still writes.
                    beta = host.call("profile.create",
                                     {"persistence": "persistent", "name": "beta"})
                    if beta.get("ok"):
                        beta_session = host.ok("session.open",
                                               {"profile": beta["result"]["profile"]})["session"]
                        wrote = host.call("profile.storage.put",
                                          {"session": beta_session, "kind": "local_storage",
                                           "key": "sibling", "value": "still-writes"})
                        expect(tag + "R4: a writable sibling is unaffected",
                               wrote.get("ok"), {"answer": wrote.get("error")})

                        # R5: the latch still behaves on a writable profile, and its
                        # refusal is the one it always was.
                        os.chmod(root / "beta", 0o500)
                        try:
                            failed = host.call("profile.storage.put",
                                               {"session": beta_session,
                                                "kind": "local_storage",
                                                "key": "fault", "value": "court-fault"})
                            again = host.call("profile.storage.put",
                                              {"session": beta_session,
                                               "kind": "local_storage",
                                               "key": "after", "value": "court-after"})
                            inspected = host.call("profile.inspect",
                                                  {"profile": beta["result"]["profile"]})
                            expect(tag + "R5: a failed commit still latches, with its own reason",
                                   refused(failed, "internal")
                                   and reason(failed) == "storage_commit_failed"
                                   and (not again.get("ok"))
                                   and inspected.get("ok")
                                   and inspected["result"].get("read_only") is True,
                                   {"failed": failed.get("error"),
                                    "again": again.get("error"),
                                    "read_only": (inspected.get("result") or {}).get("read_only")})
                        finally:
                            os.chmod(root / "beta", 0o700)
                    else:
                        expect(tag + "R4: a writable sibling is unaffected", False,
                               {"answer": beta.get("error")})

                    # R8: the writer lock is still taken by a readonly holder.
                    with tempfile.TemporaryDirectory(prefix="minicon-surf-readonly-2-") as other:
                        rival = Host(args.binary, other, allocator, origin, root)
                        try:
                            # The rival adopts the same root, so the profile is
                            # already listed; the lock is taken at the open.
                            rival_list = rival.call("profile.list", {})
                            rival_id = None
                            if rival_list.get("ok"):
                                rival_id = next((p["profile"] for p
                                                 in rival_list["result"]["profiles"]
                                                 if p.get("name") == "alpha"), None)
                            contended = rival.call("session.open",
                                                   {"profile": rival_id,
                                                    "mode": "readonly"}) if rival_id else {}
                            expect(tag + "R8: a second host is still locked out, readonly or not",
                                   refused(contended, "profile_locked"),
                                   {"answer": contended.get("error"), "seen": rival_id})
                        finally:
                            rival.finish()
                finally:
                    host.finish()

                # R6: a SECOND session on the same profile, without the mode,
                # still writes — the mode belongs to the session, not to the
                # profile and not to the record.
                host = Host(args.binary, directory, allocator, origin, root)
                try:
                    # Amended before the code, on a second measured lifecycle
                    # fact: this host allows ONE LIVE SESSION PER PROFILE —
                    # a second open answers resource_limit — so "two sessions
                    # at once" cannot be the test. The sequence proves the same
                    # thing: the mode dies with the session that carried it.
                    listed = host.ok("profile.list", {})["profiles"]
                    adopted = next((p["profile"] for p in listed
                                    if p.get("name") == "alpha"), None)
                    writable = False
                    second_refused = False
                    if adopted is not None:
                        ro = host.call("session.open", {"profile": adopted, "mode": "readonly"})
                        if ro.get("ok"):
                            rival = host.call("session.open", {"profile": adopted})
                            second_refused = refused(rival, "resource_limit")
                            host.call("session.close",
                                      {"session": ro["result"]["session"]})
                        rw = host.call("session.open", {"profile": adopted})
                        if rw.get("ok"):
                            wrote = host.call("profile.storage.put",
                                              {"session": rw["result"]["session"],
                                               "kind": "local_storage",
                                               "key": "after-readonly",
                                               "value": "writable-again"})
                            writable = bool(wrote.get("ok"))
                    expect(tag + "R6: after the readonly session closes, a plain one writes",
                           writable, {"writable": writable})
                    expect(tag + "R6b: one live session per profile still holds, readonly or not",
                           second_refused, {"second_refused": second_refused})
                finally:
                    host.finish()
    finally:
        server.shutdown()

    receipt = {
        "court": "native-dom readonly profiles (control 0.0.1 arguments)",
        "host_sha256": hashlib.sha256(Path(args.binary).read_bytes()).hexdigest(),
        "ruled": {"shape": "mode argument on session.open, per session, not persisted",
                  "read_only": "unchanged: the failed-commit latch",
                  "ephemeral_readonly": "refused",
                  "writer_lock": "still taken"},
        "checks": checks,
        "checks_passed": sum(1 for c in checks if c["passed"]),
        "checks_total": len(checks),
        "passed": all(c["passed"] for c in checks),
        "limitations": [
            "design-frozen court: it fails until session.open accepts the mode",
            "on a host without the mode the run stops at R1 in each arm, so the criterion count is small until the capability exists and grows to the full set once R1 passes",
            "R5 is the criterion that matters: it proves the new mode did not eat read_only's meaning, and profile-court.py's own latch criterion must keep passing on the same binary",
            "two criteria read the contract source and its examples rather than the binary, so they are repo-local by design",
            "the sealed store is enabled through the profile-store environment knob, as the profile court does",
            "one hermetic loopback origin, macOS only; no surface, no window, no AppKit",
        ],
    }
    Path(args.receipt).write_text(json.dumps(receipt, indent=1, sort_keys=True) + "\n")
    print(json.dumps({"passed": receipt["passed"], "checks_passed": receipt["checks_passed"],
                      "checks_total": receipt["checks_total"]}))
    for check in checks:
        if not check["passed"]:
            print("FAIL", json.dumps(check)[:170])
    return 0 if receipt["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
