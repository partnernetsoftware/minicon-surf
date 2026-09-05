#!/usr/bin/env python3
"""The frozen court for page-authored text in a host error.

Frozen from `page-error-redaction-design-0.0.1.md` §4 and §5 before the host
changes, and failing until the redaction exists. It was frozen against a
measured leak, not a suspicion: today an uncaught page throw puts its own
message, verbatim, into `details.engine_error`.

What it holds the host to: no page-authored value, and no encoding or
fragment of one, may appear anywhere in a control answer, in its details, or
in what the session reports afterwards — while the typed code, the retryable
bit, the scope and a **fixed host reason** all survive, because a redaction
that answers "something went wrong" for everything is not a diagnostic.

The court never records a page value. Every check reports booleans, counts and
closed vocabularies, so the receipt cannot become the leak it is about; the
last group re-reads the receipt from disk and proves it.

Strictly headless: no surface binary, no window, no AppKit, and it refuses to
run with the visible-court variable set. One hermetic loopback origin, both
allocators, a fresh host per group.

Groups: load, encoded, lifecycle, timer, action, rejection, typing, reason,
deadline, ledger, receipt.
"""

import argparse
import hashlib
import importlib.util
import json
import os
import sys
import tempfile
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "protocol"))
import check_contract  # noqa: E402,F401

VISIBLE_ENV = "MINICON_SURF_ALLOW_VISIBLE_COURT"
# R1: a fragment of a page value this long or longer is a leak. Eight is short
# enough to catch a truncated secret and long enough that ordinary English in a
# host reason cannot collide with a random token.
FRAGMENT = 8
# R7: two pages that throw the same class with different values must produce
# byte-identical details, so the detail carries no page-dependent information
# at all — not the value, not its length, not a hash of it.
IDENTICAL_DETAIL_KEYS = ("engine_error",)
# R9: the deadline stays its own answer. Redaction must not flatten a host that
# ran out of time into a host whose page threw.
DEADLINE_MS = 1200


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

# The synthetic values the fixtures throw. They are invented here, are in no
# document and no profile, and are never written to the receipt: a check
# reports whether one was found, never what it was.
# They are deliberately opaque. An earlier round used readable names and the
# court failed itself: "qxzv-lifecycle-3390" shares eight characters with the
# criterion that reports it and with the fixture's own URL, so the scan found
# its own words. A value that carries no English cannot collide with the
# vocabulary of the thing scanning for it.
VALUES = {
    "load": "vqx7-31mtrw-8241",
    "encoded": "wjb2-59jhbd-7013",
    "lifecycle": "kpz4-88vwqs-2265",
    "timer": "ndm6-04ykpl-9930",
    "action": "tgh1-72bcnm-5518",
    "rejection": "yrs9-16tzgf-4407",
    "pair_a": "aaf5-11aoaa-1171",
    "pair_b": "bbg3-22bqbb-2273",
}


def encodings(value):
    """The forms a value can wear on the way out. A redaction that only strips
    the literal string is not a redaction."""
    forms = {"literal": value,
             "percent": "".join(c if c.isalnum() else "%%%02X" % ord(c) for c in value),
             "json": json.dumps(value)[1:-1],
             "codes": ",".join(str(ord(c)) for c in value),
             "hex": value.encode().hex(),
             "upper": value.upper(),
             "reversed": value[::-1]}
    return forms


def leaks(blob, value):
    """Every way this value could be showing through, as a list of names. A
    fragment counts: a truncated secret is a leak with a shorter name."""
    found = []
    for name, form in encodings(value).items():
        if form and form in blob:
            found.append(name)
    for start in range(0, max(1, len(value) - FRAGMENT + 1)):
        window = value[start:start + FRAGMENT]
        if len(window) == FRAGMENT and window in blob:
            found.append("fragment")
            break
    return sorted(set(found))


def document(script, body=""):
    return ("<!doctype html><html><body><main>" + body + "</main><script>"
            + script + "</script></body></html>").encode()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--binary", required=True)
    parser.add_argument("--receipt", required=True)
    args = parser.parse_args()
    if os.environ.get(VISIBLE_ENV):
        print(json.dumps({"passed": False, "reason": "the visible-court variable is set"}))
        return 1

    network = RETENTION.load_network_module()
    V = VALUES

    class Handler(network.Handler):
        def do_GET(self):
            path, _, _query = self.path.partition("?")
            network.Handler.hits.append(path)
            pages = {
                # A page's own script throws a value it holds.
                "/load.html": document(
                    "var v=document.getElementById('h').value;"
                    "throw new Error('carrying '+v);",
                    "<input id=h value='" + V["load"] + "'>"),
                # The same, wearing three encodings, so a redaction that only
                # strips the literal string still fails.
                "/encoded.html": document(
                    "var v=document.getElementById('h').value;"
                    "throw new Error(encodeURIComponent(v)+' '+"
                    "v.split('').map(function(c){return c.charCodeAt(0);}).join(',')+' '+"
                    "JSON.stringify(v));",
                    "<input id=h value='" + V["encoded"] + "'>"),
                # A listener that throws while the host runs the lifecycle.
                "/lifecycle.html": document(
                    "window.addEventListener('load',function(){"
                    "throw new Error('carrying '+document.getElementById('h').value);});",
                    "<input id=h value='" + V["lifecycle"] + "'>"),
                # A timer callback that throws, drained under a later request.
                "/timer.html": document(
                    "setTimeout(function(){"
                    "throw new Error('carrying '+document.getElementById('h').value);},0);",
                    "<input id=h value='" + V["timer"] + "'>"),
                # A listener that throws under the agent's own action.
                "/action.html": document(
                    "document.getElementById('go').addEventListener('click',function(){"
                    "throw new Error('carrying '+document.getElementById('h').value);});",
                    "<input id=h value='" + V["action"] + "'>"
                    "<button id=go type=button>go</button>"),
                # A rejection nobody handles, which is not a throw.
                "/rejection.html": document(
                    "Promise.reject(new Error('carrying '+"
                    "document.getElementById('h').value));",
                    "<input id=h value='" + V["rejection"] + "'>"),
                # Two pages, one class, two values: the details must not differ.
                "/pair-a.html": document("throw new Error('" + V["pair_a"] + "');"),
                "/pair-b.html": document("throw new Error('" + V["pair_b"] + "');"),
                # A page that never finishes: the deadline is its own answer.
                "/slow.html": document("for(;;){}"),
                "/quiet.html": document("", "<p id=q>quiet</p>"),
            }
            if path in pages:
                return self.reply(200, pages[path])
            return super().do_GET()

    server = network.Server(("127.0.0.1", 0), Handler)
    origin = f"http://127.0.0.1:{server.server_address[1]}"
    threading.Thread(target=server.serve_forever, daemon=True).start()
    checks = []
    killed_hosts = []

    def expect(name, condition, detail=None):
        checks.append({"check": name, "passed": bool(condition),
                       **({"detail": detail} if detail is not None else {})})

    def blob(answer):
        return json.dumps(answer, sort_keys=True)

    def error_of(answer):
        return (answer.get("error") or {}) if not answer.get("ok") else {}

    try:
        for allocator in ("system", "arena"):
            tag = f"[{allocator}] "
            with tempfile.TemporaryDirectory(prefix="minicon-surf-redact-") as directory:
                host = JOBS.Supervised(args.binary, directory, origin, allocator)
                try:
                    profile = host.ok("profile.create", {"persistence": "ephemeral"})["profile"]
                    session = host.ok("session.open", {"profile": profile})["session"]

                    def open_page(name):
                        return host.call("target.open",
                                         {"session": session, "url": origin + "/" + name})

                    # R1 load: the page throws what it holds.
                    answer = open_page("load.html")
                    found = leaks(blob(answer), V["load"])
                    expect(tag + "R1: a page's own throw does not carry its value into the answer",
                           not found, {"leaked_as": found, "code": error_of(answer).get("code")})

                    # R2 encoded: three encodings of the same value.
                    answer = open_page("encoded.html")
                    found = leaks(blob(answer), V["encoded"])
                    expect(tag + "R2: nor any encoding or fragment of it",
                           not found, {"leaked_as": found})

                    # R3 lifecycle: a listener throwing while the host runs the
                    # four steps.
                    answer = open_page("lifecycle.html")
                    found = leaks(blob(answer), V["lifecycle"])
                    expect(tag + "R3: nor a listener that throws during the lifecycle",
                           not found, {"leaked_as": found, "code": error_of(answer).get("code")})

                    # R4 timer: a callback that throws when the host drains.
                    answer = open_page("timer.html")
                    trailing = []
                    if answer.get("ok"):
                        target = answer["result"]["target"]
                        trailing.append(host.call("target.wait",
                                                  {"target": target, "until": "quiet"}))
                        trailing.append(host.call("target.inspect", {"target": target}))
                    found = leaks(blob(answer) + "".join(blob(t) for t in trailing), V["timer"])
                    expect(tag + "R4: nor a timer callback that throws when the host drains",
                           not found, {"leaked_as": found})

                    # R5 action: a listener that throws under target.act.
                    answer = open_page("action.html")
                    acted = None
                    if answer.get("ok"):
                        target = answer["result"]["target"]
                        seen = host.call("target.snapshot",
                                         {"target": target, "format": "semantic",
                                          "max_bytes": 65536, "max_nodes": 64})
                        button = next((n for n in (seen.get("result") or {}).get("nodes", [])
                                       if n.get("role") == "button"), None)
                        if button:
                            acted = host.call("target.act",
                                              {"target": target,
                                               "reference": button["reference"],
                                               "action": {"kind": "click"}})
                    found = leaks(blob(answer) + (blob(acted) if acted else ""), V["action"])
                    if acted is None:
                        found = found or ["no action was taken"]
                    expect(tag + "R5: nor a listener that throws under the agent's own action",
                           not found, {"leaked_as": found,
                                       "act_code": error_of(acted or {}).get("code")})

                    # R6 rejection: not a throw, and still not a leak.
                    answer = open_page("rejection.html")
                    found = leaks(blob(answer), V["rejection"])
                    expect(tag + "R6: nor a rejection nobody handles",
                           not found, {"leaked_as": found})

                    # R7 typing: the answer stays typed and scoped.
                    answer = open_page("load.html")
                    error = error_of(answer)
                    typed = (error.get("code") in ("target_crashed", "internal")
                             and error.get("retryable") is False
                             and (error.get("scope") or {}).get("kind") == "target")
                    expect(tag + "R7: the typed code, the retryable bit and the scope survive",
                           typed, {"code": error.get("code"),
                                   "retryable": error.get("retryable"),
                                   "scope_kind": (error.get("scope") or {}).get("kind")})

                    # R8 reason: a fixed host reason, and details that do not
                    # vary with the page's value.
                    a = open_page("pair-a.html")
                    b = open_page("pair-b.html")
                    da = (error_of(a).get("details") or {})
                    db = (error_of(b).get("details") or {})
                    varying = sorted(k for k in set(da) | set(db)
                                     if json.dumps(da.get(k), sort_keys=True)
                                     != json.dumps(db.get(k), sort_keys=True))
                    expect(tag + "R8: two pages, one class, two values, identical details",
                           not varying, {"keys_that_varied": varying})
                    reason = (error_of(a).get("message") or "")
                    expect(tag + "R8b: and a fixed host reason is still said out loud",
                           bool(reason) and not leaks(reason, V["pair_a"]),
                           {"has_reason": bool(reason)})

                    # R9 deadline: still its own answer, still retryable.
                    answer = host.call("target.open",
                                       {"session": session, "url": origin + "/slow.html"},
                                       deadline_ms=DEADLINE_MS)
                    error = error_of(answer)
                    expect(tag + "R9: a host that ran out of time is not a host whose page threw",
                           error.get("code") == "deadline_exceeded"
                           and error.get("retryable") is True,
                           {"code": error.get("code"), "retryable": error.get("retryable")})

                    # R10 ledger: what the session says afterwards.
                    after = [host.call("session.inspect", {"session": session}),
                             host.call("target.list", {"session": session}),
                             host.call("memory.report", {})]
                    ledger_found = sorted({name
                                           for value in V.values()
                                           for name in leaks("".join(blob(x) for x in after),
                                                             value)})
                    expect(tag + "R10: and neither does the ledger the session keeps",
                           not ledger_found, {"leaked_as": ledger_found})
                finally:
                    if host.killed:
                        killed_hosts.append({"allocator": allocator})
                    host.finish()
                    killed_hosts.extend({"allocator": allocator, **t} for t in host.timeouts)
    finally:
        server.shutdown()

    receipt = {
        "court": "native-dom page-authored text in a host error (control 0.0.2)",
        "host_sha256": hashlib.sha256(Path(args.binary).read_bytes()).hexdigest(),
        "thresholds": {"fragment_bytes": FRAGMENT, "deadline_ms": DEADLINE_MS,
                       "identical_detail_keys": list(IDENTICAL_DETAIL_KEYS)},
        "checks": checks,
        "checks_passed": sum(1 for c in checks if c["passed"]),
        "checks_total": len(checks),
        "passed": all(c["passed"] for c in checks) and not killed_hosts,
        "hosts_killed": killed_hosts,
        "limitations": [
            "design-frozen court: it fails until the host redacts page-authored text",
            "the values the fixtures throw are invented here and are never written to this receipt: a check reports that a value was found, never what it was",
            "it reads the answers a caller sees; a host that hides a value from the answer and writes it to a file this court does not read is not caught here",
            "one hermetic loopback origin, macOS only; no surface, no window, no AppKit",
        ],
    }
    Path(args.receipt).write_text(json.dumps(receipt, indent=1, sort_keys=True) + "\n")
    # R11: the receipt this court just wrote is an artifact like any other.
    written = Path(args.receipt).read_text()
    self_found = sorted({name for value in V.values() for name in leaks(written, value)})
    receipt["checks"].append({"check": "R11: the court's own receipt carries no page value",
                              "passed": not self_found,
                              "detail": {"leaked_as": self_found}})
    receipt["checks_passed"] = sum(1 for c in receipt["checks"] if c["passed"])
    receipt["checks_total"] = len(receipt["checks"])
    receipt["passed"] = (all(c["passed"] for c in receipt["checks"]) and not killed_hosts)
    Path(args.receipt).write_text(json.dumps(receipt, indent=1, sort_keys=True) + "\n")
    print(json.dumps({"passed": receipt["passed"], "checks_passed": receipt["checks_passed"],
                      "checks_total": receipt["checks_total"],
                      "hosts_killed": len(killed_hosts)}))
    for check in receipt["checks"]:
        if not check["passed"]:
            print("FAIL", json.dumps(check)[:170])
    return 0 if receipt["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
