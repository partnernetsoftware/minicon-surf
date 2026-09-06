#!/usr/bin/env python3
"""The frozen court for copy-on-write profiles.

Frozen from `copy-on-write-audit-0.0.1.md` §11 and the ruling that produced it,
**before the host changes, and failing until `profile.create` accepts `from`**.

The ruled shape: a fork is `profile.create` with a `from` argument, so the
operation enum stays at 26. It opens the source record inside the host --
nothing can be copied by hand, because the profile id is sealed into the record
and the DEK is wrapped under the host's own key account -- takes the source's
writer lock, re-seals the plaintext under the child's own id and a fresh DEK,
and leaves the parent's file byte-for-byte unchanged.

The criterion that matters most is not the copy. It is **F3 plus F10**: the
parent is only ever read, so its digest is identical afterwards, and a failed
re-seal leaves no child directory and does not latch the parent. A fork that
damaged the profile it copied from would pass every other criterion here.

Strictly headless: no surface binary, no window, no AppKit, and it refuses to
run with the visible-court variable set. Nothing is downloaded from outside the
fixture server in this file, and no profile outside a temporary root is touched.

Groups: contract, content, identity, isolation, locks, ceiling, failure,
inheritance, budgets, retention, redaction.
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
MAX_PROFILES = 8
FAKE_VALUE = "court-fake-storage-value"
FAKE_COOKIE = "court-fake-cookie-value"


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
    """One host over a profile root that outlives it. Headless, one origin."""

    def __init__(self, binary, directory, root, origin=None):
        environment = dict(os.environ)
        for knob in ("MINICON_SURF_NATIVE_REALM_ZONE", "MINICON_SURF_NATIVE_REALM_ARENA",
                     "MINICON_SURF_PROFILE_STORE", VISIBLE_ENV):
            environment.pop(knob, None)
        command = [binary, "serve", "--stdio", "--fixture-root", str(FIXTURE_ROOT),
                   "--config-dir", str(Path(directory) / "config"),
                   "--profile-root", str(root)]
        if origin:
            command += ["--allow-origin", origin]
        self.process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                        stderr=subprocess.DEVNULL, text=True, env=environment)
        self.counter = 0

    def raw(self, operation, arguments, validate=True):
        """`from` is not in the contract yet, so the court must be able to
        reach the host without it."""
        self.counter += 1
        request = {"protocol": "minicon-surf.control", "version": "0.0.2",
                   "request_id": f"req_cow_{self.counter}", "deadline_ms": 30000,
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

    def fork(self, name, source, persistence="persistent"):
        return self.raw("profile.create",
                        {"persistence": persistence, "name": name, "from": source},
                        validate=False)

    def finish(self):
        try:
            self.process.stdin.close()
            self.process.wait(timeout=15)
        except Exception:
            self.process.kill()


def refused(answer, code):
    return (not answer.get("ok")) and answer.get("error", {}).get("code") == code


def digest_of(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest() if Path(path).exists() else None


def record_of(root, name):
    path = Path(root) / name / "profile.v1.sealed"
    return path if path.exists() else None


def tree(root):
    root = Path(root)
    return {str(p.relative_to(root)) for p in root.rglob("*")} if root.exists() else set()


def node_of(host, target, selector_id):
    """The node reference for an element, through the snapshot an agent has."""
    snapshot = host.ok("target.snapshot", {"target": target, "format": "semantic",
                                           "max_bytes": 65536, "max_nodes": 128})
    for entry in snapshot["nodes"]:
        if entry.get("dom_id") == selector_id:
            reference = entry.get("reference") or {}
            return reference.get("node"), reference.get("revision", snapshot["revision"])
    return None, snapshot["revision"]


def fill(host, session):
    host.ok("profile.storage.put", {"session": session, "kind": "local_storage",
                                    "key": "k1", "value": FAKE_VALUE})
    host.ok("profile.storage.put", {"session": session, "kind": "local_storage",
                                    "key": "k2", "value": FAKE_VALUE + "-2"})
    host.ok("profile.storage.put", {"session": session, "kind": "cookie",
                                    "key": "c1", "value": FAKE_COOKIE})


def counts(host, profile):
    inspected = host.ok("profile.inspect", {"profile": profile})
    return {"cookies": inspected["cookies"]["objects"],
            "cookie_bytes": inspected["cookies"]["bytes"],
            "storage": inspected.get("storage", {}).get("keys"),
            "policy": inspected.get("policy")}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--binary", required=True)
    parser.add_argument("--receipt", required=True)
    args = parser.parse_args()
    if os.environ.get(VISIBLE_ENV):
        print(json.dumps({"passed": False, "reason": "the visible-court variable is set"}))
        return 1

    checks = []

    def expect(name, condition, detail=None):
        checks.append({"check": name, "passed": bool(condition),
                       **({"detail": detail} if detail is not None else {})})

    # ---- F1: the contract, read from the repo -----------------------------
    expect("F1: the contract takes {persistence, name, from} and the enum is still 26",
           contract_takes_from() and len(check_contract.OPERATIONS_NEXT) == 26
           and (EXAMPLES / "profile-create-from.request.json").exists(),
           {"operations": len(check_contract.OPERATIONS_NEXT),
            "example": (EXAMPLES / "profile-create-from.request.json").exists()})

    with tempfile.TemporaryDirectory(prefix="minicon-surf-cow-") as directory:
        root = Path(directory) / "profiles"
        root.mkdir(mode=0o700)
        host = Host(args.binary, directory, root)
        try:
            parent = host.ok("profile.create", {"persistence": "persistent",
                                                "name": "alpha"})["profile"]
            session = host.ok("session.open", {"profile": parent})["session"]
            fill(host, session)

            # ---- F7: a live session on the source stops the fork ---------
            live = host.fork("beta", parent)
            expect("F7: a source with a live session is refused, and nothing is created",
                   refused(live, "resource_limit") and record_of(root, "beta") is None,
                   {"code": (live.get("error") or {}).get("code"),
                    "child_on_disk": record_of(root, "beta") is not None})
            host.ok("session.close", {"session": session})

            before = counts(host, parent)
            parent_record = record_of(root, "alpha")
            parent_digest = digest_of(parent_record) if parent_record else None

            # ---- the fork itself -----------------------------------------
            forked = host.fork("beta", parent)
            child = (forked.get("result") or {}).get("profile")
            # Every criterion below has to be *exercised* to pass. Without
            # this gate they all pass on a host that cannot fork at all: the
            # parent is unchanged because nothing happened, the child's record
            # names no parent because there is no record, and a failed fork
            # leaves no child because no fork ever lands. That is how a court
            # comes to measure its own fixture, which has happened five times
            # in this directory already.
            made_a_child = bool(child) and record_of(root, "beta") is not None
            expect("F3: the parent's record is byte-for-byte unchanged by the fork",
                   made_a_child and parent_digest is not None
                   and digest_of(parent_record) == parent_digest,
                   {"forked": made_a_child,
                    "unchanged": digest_of(parent_record) == parent_digest})
            expect("F3b: the child is sealed under its own identity",
                   child is not None and record_of(root, "beta") is not None
                   and json.loads(record_of(root, "beta").read_text()).get("profile")
                   == "profile_beta" if record_of(root, "beta") else False,
                   {"child": child})

            child_counts = counts(host, child) if child else {}
            expect("F2: the child reproduces the cookies and storage it was copied from",
                   child_counts.get("cookies") == before["cookies"]
                   and child_counts.get("cookie_bytes") == before["cookie_bytes"]
                   and child_counts.get("storage") == before["storage"],
                   {"parent": before, "child": child_counts})

            expect("F11: the policy is inherited",
                   child_counts.get("policy") == before["policy"],
                   {"parent_policy": before["policy"], "child_policy": child_counts.get("policy")})

            # ---- F4/F5: the two directions of isolation -------------------
            child_session = host.ok("session.open", {"profile": child})["session"] if child else None
            if child_session:
                host.ok("profile.storage.put", {"session": child_session, "kind": "local_storage",
                                                "key": "child-only", "value": FAKE_VALUE})
                host.ok("session.close", {"session": child_session})
            expect("F4: writing in the child changes nothing in the parent",
                   made_a_child and counts(host, parent) == before
                   and digest_of(parent_record) == parent_digest,
                   {"parent_now": counts(host, parent)})

            child_before = counts(host, child) if child else {}
            child_record = record_of(root, "beta")
            child_digest = digest_of(child_record) if child_record else None
            parent_session = host.ok("session.open", {"profile": parent})["session"]
            host.ok("profile.storage.put", {"session": parent_session, "kind": "local_storage",
                                            "key": "parent-only", "value": FAKE_VALUE})
            host.ok("session.close", {"session": parent_session})
            expect("F5: writing in the parent changes nothing in the child",
                   made_a_child and counts(host, child) == child_before
                   and digest_of(child_record) == child_digest,
                   {"child_now": counts(host, child) if child else None})

            # ---- F6: two locks, two live sessions -------------------------
            first = host.call("session.open", {"profile": parent})
            second = host.call("session.open", {"profile": child}) if child else {}
            expect("F6: parent and child hold their own locks at the same time",
                   first.get("ok") and second.get("ok"),
                   {"parent": first.get("ok"), "child": second.get("ok")})
            for opened in (first, second):
                if opened.get("ok"):
                    host.call("session.close", {"session": opened["result"]["session"]})

            # ---- F13: an ephemeral source has no record to copy -----------
            ephemeral = host.ok("profile.create", {"persistence": "ephemeral"})["profile"]
            from_ephemeral = host.fork("gamma", ephemeral)
            # The refusal has to be about the source, not about an argument
            # the host has never heard of, so it must carry its own reason.
            expect("F13: an ephemeral source is refused, typed, and creates nothing",
                   made_a_child and refused(from_ephemeral, "invalid_request")
                   and ((from_ephemeral.get("error") or {}).get("details") or {}).get("reason")
                   == "ephemeral_source"
                   and record_of(root, "gamma") is None,
                   {"code": (from_ephemeral.get("error") or {}).get("code"),
                    "reason": ((from_ephemeral.get("error") or {}).get("details")
                               or {}).get("reason")})

            # ---- F14: no provenance anywhere ------------------------------
            child_bytes = record_of(root, "beta").read_bytes() if record_of(root, "beta") else b""
            expect("F14: neither the child's record nor the answer names the parent",
                   made_a_child and b"alpha" not in child_bytes
                   and "alpha" not in json.dumps(forked),
                   {"record_bytes": len(child_bytes)})

            # ---- F17: the child's key material is its own -----------------
            parent_json = json.loads(parent_record.read_text()) if parent_record else {}
            child_json = json.loads(record_of(root, "beta").read_text()) if record_of(root, "beta") else {}
            expect("F17: the child seals under its own DEK",
                   bool(child_json) and child_json.get("dek_sealed") != parent_json.get("dek_sealed")
                   and child_json.get("record_nonce") != parent_json.get("record_nonce"),
                   {"distinct": bool(child_json)
                    and child_json.get("dek_sealed") != parent_json.get("dek_sealed")})

            # ---- F10: a re-seal that cannot land --------------------------
            blocked = Path(root) / "delta"
            blocked.write_text("not a directory")
            failed = host.fork("delta", parent)
            still_writes = False
            probe = host.call("session.open", {"profile": parent})
            if probe.get("ok"):
                wrote = host.call("profile.storage.put",
                                  {"session": probe["result"]["session"], "kind": "local_storage",
                                   "key": "after-failure", "value": FAKE_VALUE})
                still_writes = bool(wrote.get("ok"))
                host.call("session.close", {"session": probe["result"]["session"]})
            expect("F10: a failed fork leaves no child and does not latch the parent",
                   made_a_child and (not failed.get("ok")) and still_writes and blocked.is_file(),
                   {"code": (failed.get("error") or {}).get("code"),
                    "parent_still_writes": still_writes})
            blocked.unlink()

            # ---- F9: the ceiling --------------------------------------
            while True:
                made = host.call("profile.create", {"persistence": "ephemeral"})
                if not made.get("ok"):
                    break
            at_ceiling = host.fork("omega", parent)
            expect("F9: at the profile ceiling the fork is refused and creates nothing",
                   refused(at_ceiling, "resource_limit") and record_of(root, "omega") is None,
                   {"code": (at_ceiling.get("error") or {}).get("code")})

            # ---- F15: the ledger and the answers stay clean ---------------
            blob = json.dumps(host.ok("session.list", {}))
            expect("F15: no value, key or cookie reaches an answer or the ledger",
                   made_a_child and FAKE_VALUE not in blob and FAKE_COOKIE not in blob
                   and FAKE_VALUE not in json.dumps(forked),
                   {"answer_bytes": len(blob)})
        finally:
            host.finish()

        # ---- F16: deleting the parent leaves the child ---------------
        after = Host(args.binary, directory, root)
        try:
            listed = {p["name"]: p for p in after.ok("profile.list", {})["profiles"]}
            deleted = after.call("profile.delete", {"profile": "profile_alpha"})
            survived = after.call("profile.inspect", {"profile": "profile_beta"})
            expect("F16: deleting the parent leaves the child intact and readable",
                   deleted.get("ok") and survived.get("ok")
                   and record_of(root, "beta") is not None,
                   {"deleted": deleted.get("ok"), "child_readable": survived.get("ok"),
                    "listed": sorted(listed)})
        finally:
            after.finish()

    # ---- F8: another host holds the lock ------------------------------
    with tempfile.TemporaryDirectory(prefix="minicon-surf-cow-lock-") as directory:
        root = Path(directory) / "profiles"
        root.mkdir(mode=0o700)
        owner = Host(args.binary, directory, root)
        try:
            source = owner.ok("profile.create", {"persistence": "persistent",
                                                 "name": "alpha"})["profile"]
            held = owner.ok("session.open", {"profile": source})["session"]
            other = Host(args.binary, str(Path(directory) / "second"), root)
            try:
                locked = other.fork("beta", "profile_alpha")
                expect("F8: a source locked by another host is refused, and creates nothing",
                       refused(locked, "profile_locked") and record_of(root, "beta") is None,
                       {"code": (locked.get("error") or {}).get("code")})
            finally:
                other.finish()
            owner.call("session.close", {"session": held})
        finally:
            owner.finish()

    # F18, added 2026-09-06 by the supplementary ruling: a persistent source
    # may be forked into an ephemeral child, which inherits in memory and
    # leaves nothing behind. Added rather than assumed, because §11.6 had left
    # it open and the court tested it in neither direction.
    with tempfile.TemporaryDirectory(prefix="minicon-surf-cow-mem-") as directory:
        root = Path(directory) / "profiles"
        root.mkdir(mode=0o700)
        host = Host(args.binary, directory, root)
        try:
            parent = host.ok("profile.create", {"persistence": "persistent",
                                                "name": "alpha"})["profile"]
            session = host.ok("session.open", {"profile": parent})["session"]
            fill(host, session)
            source_counts = counts(host, parent)
            host.ok("session.close", {"session": session})
            before_disk = tree(root)
            memory_child = host.fork("in-memory", parent, persistence="ephemeral")
            child = (memory_child.get("result") or {}).get("profile")
            child_counts = counts(host, child) if child else {}
            expect("F18: a persistent source forks into an ephemeral child that leaves no disk",
                   bool(child)
                   and child_counts.get("cookies") == source_counts["cookies"]
                   and child_counts.get("storage") == source_counts["storage"]
                   and child_counts.get("policy") == source_counts["policy"]
                   and tree(root) == before_disk,
                   {"child": child, "disk_unchanged": tree(root) == before_disk,
                    "source": source_counts, "child_counts": child_counts})
            expect("F19: an ephemeral source is refused with its own reason, either persistence",
                   all(((host.fork(f"x{index}", child or "profile_missing",
                                   persistence=persistence).get("error") or {}).get("details")
                        or {}).get("reason") == "ephemeral_source"
                       for index, persistence in enumerate(("persistent", "ephemeral")))
                   if child else False,
                   {"source": child})
        finally:
            host.finish()

    # F20 and F21, added 2026-09-06 after review caught a gap this court did
    # not cover: the first implementation, when it could not re-read the
    # source's record under the lock, fell back to whatever this host had
    # loaded at startup. Every criterion above passed on that build, because
    # none of them ever made the re-read fail, and none of them proved where
    # the copied bytes came from.
    drive_reread(args.binary, expect)

    # F12, driven now that the fork exists. The download counters are live
    # state that is never persisted, so a child starts full however much its
    # source has spent -- which is exactly why the ruling had to say so out
    # loud: a fork is a way to buy another allowance, at the price of one of
    # the eight profile slots.
    drive_download_budget(args.binary, RETENTION.load_network_module(), expect)

    receipt = {
        "court": "native-dom copy-on-write profiles (profile.create from)",
        "host_sha256": hashlib.sha256(Path(args.binary).read_bytes()).hexdigest(),
        "ruled": {"shape": "profile.create gains from; the operation enum stays at 26",
                  "lock": "the fork takes the source's writer lock; any live session refuses",
                  "parent": "read only, byte-for-byte unchanged",
                  "inherits": "cookies, storage and the policy",
                  "resets": "download count and bytes",
                  "forbidden_source": "ephemeral",
                  "provenance": "the child's record does not name its parent"},
        "checks": checks,
        "checks_passed": sum(1 for c in checks if c["passed"]),
        "checks_total": len(checks),
        "passed": all(c["passed"] for c in checks),
        "limitations": [
            "design-frozen court: it fails until profile.create accepts from",
            "F3 and F10 are the criteria that matter: a fork that damaged the profile it copied from would pass every other one",
            "F12 is stated but not driven; it needs a network arm against a live parent and child once the fork exists",
            "one criterion reads the contract source and its examples rather than the binary, so it is repo-local by design",
            "the keychain-backed store is the default mode, as the profile court runs it",
            "no profile outside a temporary root is touched, and nothing is fetched from outside this file",
        ],
    }
    Path(args.receipt).write_text(json.dumps(receipt, indent=1, sort_keys=True) + "\n")
    print(json.dumps({"passed": receipt["passed"], "checks_passed": receipt["checks_passed"],
                      "checks_total": receipt["checks_total"]}))
    for check in checks:
        if not check["passed"]:
            print("FAIL", json.dumps(check)[:170])
    return 0 if receipt["passed"] else 1


def drive_reread(binary, expect):
    """Where do the child's bytes come from, and what happens when they cannot
    be read at all?"""
    # F21: another host commits to the source after this host adopted it. A
    # fork that copied memory would miss the write; one that re-reads the
    # record under the lock carries it.
    with tempfile.TemporaryDirectory(prefix="minicon-surf-cow-reread-") as directory:
        root = Path(directory) / "profiles"
        root.mkdir(mode=0o700)
        adopter = Host(binary, directory, root)
        try:
            parent = adopter.ok("profile.create", {"persistence": "persistent",
                                                   "name": "alpha"})["profile"]
            session = adopter.ok("session.open", {"profile": parent})["session"]
            fill(adopter, session)
            adopter.ok("session.close", {"session": session})

            # A second host writes a key this one has never seen, then leaves.
            writer = Host(binary, str(Path(directory) / "second"), root)
            try:
                other = writer.ok("session.open", {"profile": "profile_alpha"})["session"]
                writer.ok("profile.storage.put", {"session": other, "kind": "local_storage",
                                                  "key": "written-elsewhere",
                                                  "value": FAKE_VALUE + "-elsewhere"})
                writer.ok("session.close", {"session": other})
            finally:
                writer.finish()

            forked = adopter.fork("beta", parent)
            child = (forked.get("result") or {}).get("profile")
            carried = None
            if child:
                child_session = adopter.ok("session.open", {"profile": child})["session"]
                got = adopter.call("profile.storage.get", {"session": child_session,
                                                           "kind": "local_storage",
                                                           "key": "written-elsewhere"})
                carried = bool(got.get("ok")) and bool((got.get("result") or {}).get("found", True))
                adopter.call("session.close", {"session": child_session})
            expect("F21: the child carries the committed record, not this host's startup state",
                   bool(child) and carried is True,
                   {"child": child, "carried_other_hosts_write": carried})
        finally:
            adopter.finish()

    # F20: a record that cannot be read is a refusal, never a fallback copy.
    with tempfile.TemporaryDirectory(prefix="minicon-surf-cow-unreadable-") as directory:
        root = Path(directory) / "profiles"
        root.mkdir(mode=0o700)
        host = Host(binary, directory, root)
        try:
            parent = host.ok("profile.create", {"persistence": "persistent",
                                                "name": "alpha"})["profile"]
            session = host.ok("session.open", {"profile": parent})["session"]
            fill(host, session)
            host.ok("session.close", {"session": session})
            before = counts(host, parent)

            # The record on disk stops being readable while the host holds the
            # profile in memory -- exactly the case a fallback would paper over.
            record = record_of(root, "alpha")
            original = record.read_bytes()
            record.write_bytes(b"{\"format\":\"broken\"}")
            refused_answer = host.fork("beta", parent)
            child_made = record_of(root, "beta") is not None
            # The store's own vocabulary, not a new one: a corrupt record is
            # already `not_found` everywhere else in this host, an unreadable
            # one `internal`, a missing key `unsupported_capability`. What the
            # criterion insists on is that the fork answers with one of them
            # and a reason, rather than quietly copying something else.
            expect("F20: an unreadable source record is a typed refusal, and no child appears",
                   (not refused_answer.get("ok"))
                   and (refused_answer.get("error") or {}).get("code") in ("not_found", "internal",
                                                                          "unsupported_capability")
                   and ((refused_answer.get("error") or {}).get("details") or {}).get("reason")
                   and not child_made,
                   {"code": (refused_answer.get("error") or {}).get("code"),
                    "child_on_disk": child_made})

            # The parent is untouched by the refusal: its bytes are what the
            # court broke, not what the host wrote, and it is not latched.
            record.write_bytes(original)
            probe = host.call("session.open", {"profile": parent})
            wrote = False
            if probe.get("ok"):
                answer = host.call("profile.storage.put",
                                   {"session": probe["result"]["session"],
                                    "kind": "local_storage", "key": "after-refusal",
                                    "value": FAKE_VALUE})
                wrote = bool(answer.get("ok"))
                host.call("session.close", {"session": probe["result"]["session"]})
            expect("F20b: a refused fork does not latch the parent",
                   wrote and counts(host, parent)["cookies"] == before["cookies"],
                   {"parent_still_writes": wrote})
        finally:
            host.finish()


def drive_download_budget(binary, network, expect):
    """Spend the source's whole download allowance, then fork and download."""
    body = b"court-fixture-payload" * 8

    class Handler(network.Handler):
        def do_GET(self):
            path, _, _query = self.path.partition("?")
            network.Handler.hits.append(path)
            if path == "/f.bin":
                return self.reply(200, body, "application/octet-stream",
                                  [("Content-Disposition", 'attachment; filename="f.bin"')])
            return self.reply(200, b"<!doctype html><html><body><main>"
                                   b"<a id=\"dl\" href=\"/f.bin\" download=\"f.bin\">f</a>"
                                   b"</main></body></html>", "text/html")

    server = network.Server(("127.0.0.1", 0), Handler)
    origin = f"http://127.0.0.1:{server.server_address[1]}"
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        with tempfile.TemporaryDirectory(prefix="minicon-surf-cow-budget-") as directory:
            root = Path(directory) / "profiles"
            root.mkdir(mode=0o700)
            host = Host(binary, directory, root, origin)
            try:
                parent = host.ok("profile.create", {"persistence": "persistent",
                                                    "name": "alpha"})["profile"]
                session = host.ok("session.open", {"profile": parent})["session"]
                target = host.ok("target.open", {"session": session,
                                                 "url": origin + "/a.html"})["target"]
                node, revision = node_of(host, target, "dl")
                spent = 0
                while True:
                    answer = host.raw("target.act", {
                        "target": target,
                        "reference": {"target": target, "revision": revision,
                                      "node": node or "node_1"},
                        "action": {"kind": "download"}}, validate=False)
                    if not answer.get("ok"):
                        break
                    spent += 1
                host.ok("target.close", {"target": target})
                host.ok("session.close", {"session": session})

                forked = host.fork("beta", parent)
                child = (forked.get("result") or {}).get("profile")
                served = None
                if child:
                    child_session = host.ok("session.open", {"profile": child})["session"]
                    child_target = host.ok("target.open", {"session": child_session,
                                                           "url": origin + "/a.html"})["target"]
                    child_node, child_revision = node_of(host, child_target, "dl")
                    answer = host.raw("target.act", {
                        "target": child_target,
                        "reference": {"target": child_target, "revision": child_revision,
                                      "node": child_node or "node_1"},
                        "action": {"kind": "download"}}, validate=False)
                    served = bool(answer.get("ok"))
                expect("F12: the child's download allowance is full even when the parent's is spent",
                       spent > 0 and served is True,
                       {"parent_spent": spent, "child_served": served})
            finally:
                host.finish()
    finally:
        server.shutdown()


def node_of_download(host, target, selector_id):
    return node_of(host, target, selector_id)


def contract_takes_from():
    request = {"protocol": "minicon-surf.control", "version": "0.0.2",
               "request_id": "req_1", "deadline_ms": 5000, "operation": "profile.create",
               "arguments": {"persistence": "persistent", "name": "beta", "from": "profile_alpha"}}
    try:
        check_contract.validate_request(request)
    except Exception:
        return False
    hostile = json.loads(json.dumps(request))
    hostile["arguments"]["path"] = "/tmp/x"
    try:
        check_contract.validate_request(hostile)
    except Exception:
        return True
    return False


if __name__ == "__main__":
    sys.exit(main())
