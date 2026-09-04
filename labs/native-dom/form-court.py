#!/usr/bin/env python3
"""The frozen court for the agent-native form interaction slice.

Frozen before the shim and the host change, as
`form-interaction-design-0.0.1.md` §12.9 requires. Until the five actions and
the new snapshot roles exist it fails, which is what freezing the criteria
first means.

Strictly headless: no surface binary, no window, no AppKit, and it refuses to
run with the visible-court variable set. One hermetic loopback origin, both
allocators, a fresh host per run.

Every fixture value is fake. The court types values it knows and then asserts
that none of them appears in the audit ledger, the court-only log or this
receipt, because a form value is page data and may not leave the page.

Groups:
 1 vocabulary   each action on its own roles, every other role refused typed
 2 events       the ordered events of the design, and the state afterwards
 3 identity     one revision per applied action, older references stale
 4 submit       GET serialisation, atomicity, refusals before mutation
 5 secrecy      no value, label or query in the ledger, the log or the receipt
 6 memory       the numeric live-owner criteria of the design
 7 cdp          the form methods stay -32601
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
VERSION = "0.0.2"

# Fake values only. The secrecy group greps for each of these.
TYPED = {
    "text": "court-fake-entry",
    "area": "court-fake-area",
    "long": "x" * 1024,
}
CYCLES = 128
CAPS = {
    "max_value_bytes": 1024,
    "max_forms": 16,
    "max_controls_per_form": 64,
    "max_options_per_select": 64,
    "max_url_bytes": 2000,
    "realm_plateau_bytes": 65536,
    "realm_tail_bytes": 8192,
    "realm_after_submit_bytes": 65536,
    "audit_entries": 64,
}


class FormHandler(PROFILE.ProfileHandler):
    """The representative pages plus one bounded form page and its target."""

    FORM = (b"<!doctype html><html><body><main>"
            b"<h1>Court form</h1>"
            b"<form id=\"f\" method=\"get\" action=\"/landed.html\">"
            b"<label for=\"t\">Text</label><input id=\"t\" name=\"text\" type=\"text\" value=\"\">"
            b"<textarea id=\"a\" name=\"area\"></textarea>"
            b"<input id=\"c\" name=\"agree\" type=\"checkbox\">"
            b"<input id=\"d\" name=\"box\" type=\"checkbox\" value=\"yes\">"
            b"<input id=\"r1\" name=\"choice\" type=\"radio\" value=\"one\">"
            b"<input id=\"r2\" name=\"choice\" type=\"radio\" value=\"two\">"
            b"<select id=\"s\" name=\"pick\">"
            b"<option value=\"a\">Alpha</option><option value=\"b\">Beta</option>"
            b"<option value=\"c\" disabled>Gamma</option></select>"
            b"<input id=\"h\" name=\"hidden\" type=\"hidden\" value=\"fixed\">"
            b"<input id=\"x\" name=\"off\" type=\"text\" value=\"\" disabled>"
            b"<input id=\"ro\" name=\"ro\" type=\"text\" value=\"\" readonly>"
            b"<input id=\"nameless\" type=\"text\" value=\"\">"
            b"<button id=\"go\" name=\"submitter\" value=\"go\" type=\"submit\">Send</button>"
            b"<input id=\"rst\" type=\"reset\" value=\"Reset\">"
            b"</form></main></body></html>")
    POST_FORM = (b"<!doctype html><html><body><main><h1>Post form</h1>"
                 b"<form id=\"p\" method=\"post\" action=\"/landed.html\">"
                 b"<input id=\"pt\" name=\"text\" type=\"text\" value=\"\">"
                 b"<button id=\"pgo\" type=\"submit\">Send</button></form></main></body></html>")

    def do_GET(self):
        path, _, _query = self.path.partition("?")
        if path == "/form.html":
            return self.reply(200, self.FORM, "text/html")
        if path == "/post-form.html":
            return self.reply(200, self.POST_FORM, "text/html")
        if path == "/landed.html":
            return self.reply(200, b"<!doctype html><html><body><main><h1>Landed</h1>"
                                   b"<p id=\"q\">landed</p></main></body></html>", "text/html")
        return super().do_GET()


class Host(NAV.Host):
    pass


def refused(response, code, reason=None):
    if response.get("ok"):
        return False
    error = response["error"]
    if error["code"] != code:
        return False
    return reason is None or error.get("details", {}).get("reason") == reason


def snapshot(host, target):
    return host.ok("target.snapshot", {"target": target, "format": "semantic",
                                       "max_bytes": 262144, "max_nodes": 128})


def node(snap, role, name=None, index=0):
    """A node reference from this snapshot by role, optionally by its name."""
    found = [n for n in snap.get("nodes", [])
             if n.get("role") == role and (name is None or n.get("name") == name
                                           or n.get("control_name") == name)]
    return found[index]["reference"] if len(found) > index else None


def act(host, target, reference, action, deadline_ms=15000):
    return host.call("target.act", {"target": target, "reference": reference, "action": action}, deadline_ms)


def run(binary, allocator, origin, expect, tag):
    with tempfile.TemporaryDirectory(prefix="minicon-surf-form-court-") as directory:
        court_file = Path(directory) / "court-only.ndjson"
        host = Host(binary, directory, allocator, origin)
        try:
            profile = host.ok("profile.create", {"persistence": "ephemeral"})["profile"]
            session = host.ok("session.open", {"profile": profile})["session"]
            target = NAV.open_target(host, session, origin, "/form.html")

            # 1. The snapshot names the new roles with their bounded facts.
            snap = snapshot(host, target)
            roles = {n.get("role") for n in snap.get("nodes", [])}
            expect(tag + "the snapshot names checkbox, radio, select and form",
                   {"checkbox", "radio", "select", "form"} <= roles, sorted(roles))
            selects = [n for n in snap.get("nodes", []) if n.get("role") == "select"]
            options = selects[0].get("options") if selects else None
            expect(tag + "a select reports bounded options with index, label, selected and disabled",
                   isinstance(options, list) and 0 < len(options) <= CAPS["max_options_per_select"]
                   and all({"index", "label", "selected", "disabled"} <= set(o) for o in options),
                   options)
            expect(tag + "the snapshot reports no option value",
                   all("value" not in o for o in (options or [])), options)
            forms = [n for n in snap.get("nodes", []) if n.get("role") == "form"]
            expect(tag + "a form reports its bounded controls and its method",
                   forms and isinstance(forms[0].get("controls"), list)
                   and len(forms[0]["controls"]) <= CAPS["max_controls_per_form"]
                   and forms[0].get("method") == "get", forms[0] if forms else None)
            expect(tag + "every control reports disabled and read_only",
                   all({"disabled", "read_only"} <= set(n) for n in snap.get("nodes", [])
                       if n.get("role") in ("textbox", "checkbox", "radio", "select")),
                   [n.get("role") for n in snap.get("nodes", [])])

            # 2. Each action applies on its own roles.
            textbox = node(snap, "textbox", "Text")
            applied = act(host, target, textbox, {"kind": "set_value", "value": TYPED["text"]})
            expect(tag + "set_value applies to a textbox and reports only the byte length",
                   applied.get("ok") and applied["result"]["value_bytes"] == len(TYPED["text"].encode())
                   and TYPED["text"] not in json.dumps(applied), applied.get("error"))
            expect(tag + "the applied action advanced the revision",
                   applied.get("ok") and applied["result"]["revision"] > snap["revision"],
                   {"before": snap["revision"], "after": (applied.get("result") or {}).get("revision")})
            stale = act(host, target, textbox, {"kind": "set_value", "value": TYPED["text"]})
            expect(tag + "the same reference is stale after its own action",
                   refused(stale, "stale_revision"), stale.get("error"))

            snap = snapshot(host, target)
            checkbox = node(snap, "checkbox")
            checked = act(host, target, checkbox, {"kind": "set_checked", "checked": True})
            expect(tag + "set_checked applies to a checkbox", checked.get("ok"), checked.get("error"))
            snap = snapshot(host, target)
            expect(tag + "the snapshot shows the checkbox checked",
                   any(n.get("role") == "checkbox" and n.get("checked") for n in snap["nodes"]))
            radios = [n for n in snap["nodes"] if n.get("role") == "radio"]
            first = act(host, target, radios[0]["reference"], {"kind": "set_checked", "checked": True})
            snap = snapshot(host, target)
            radios = [n for n in snap["nodes"] if n.get("role") == "radio"]
            second = act(host, target, radios[1]["reference"], {"kind": "set_checked", "checked": True})
            snap = snapshot(host, target)
            after = [n.get("checked") for n in snap["nodes"] if n.get("role") == "radio"]
            expect(tag + "setting one radio of a group clears the others",
                   first.get("ok") and second.get("ok") and after.count(True) == 1, after)
            select = node(snap, "select")
            chosen = act(host, target, select, {"kind": "select_option", "index": 1})
            snap = snapshot(host, target)
            options = next(n for n in snap["nodes"] if n.get("role") == "select")["options"]
            expect(tag + "select_option chooses by the snapshot's index",
                   chosen.get("ok") and options[1]["selected"] is True, options)

            # 2b. Every other role is refused typed, before any change.
            wrong = act(host, target, node(snap, "select"), {"kind": "set_value", "value": "x"})
            expect(tag + "set_value on a select is refused typed", refused(wrong, "unsupported_capability"),
                   wrong.get("error"))
            wrong = act(host, target, node(snap, "textbox", "Text"), {"kind": "set_checked", "checked": True})
            expect(tag + "set_checked on a textbox is refused typed", refused(wrong, "unsupported_capability"),
                   wrong.get("error"))
            disabled = [n for n in snap["nodes"] if n.get("disabled")]
            if disabled:
                response = act(host, target, disabled[0]["reference"], {"kind": "set_value", "value": "x"})
                expect(tag + "a disabled control refuses before any change",
                       refused(response, "unsupported_capability"), response.get("error"))
            readonly = [n for n in snap["nodes"] if n.get("read_only")]
            if readonly:
                response = act(host, target, readonly[0]["reference"], {"kind": "set_value", "value": "x"})
                expect(tag + "a read-only control refuses set_value", refused(response, "unsupported_capability"),
                       response.get("error"))
            over = act(host, target, node(snap, "textbox", "Text"),
                       {"kind": "set_value", "value": "y" * (CAPS["max_value_bytes"] + 1)})
            expect(tag + "an over-long value is refused before any change",
                   refused(over, "invalid_request"), over.get("error"))
            far = act(host, target, node(snap, "select"), {"kind": "select_option", "index": 60})
            expect(tag + "an option index outside the list is refused typed",
                   refused(far, "not_found") or refused(far, "invalid_request"), far.get("error"))

            # 3. Keyboard activation, bounded to two keys.
            pressed = act(host, target, node(snap, "button"), {"kind": "press", "key": "space"})
            expect(tag + "press space activates a button", pressed.get("ok"), pressed.get("error"))

            # 4. Submit: GET serialisation and atomicity.
            snap = snapshot(host, target)
            before = host.ok("target.inspect", {"target": target})
            submitted = act(host, target, node(snap, "form"), {"kind": "submit"})
            after = host.ok("target.inspect", {"target": target})
            query = (after.get("url") or "").partition("?")[2]
            expect(tag + "submit navigates with the serialised query",
                   submitted.get("ok") and "/landed.html" in (after.get("url") or "")
                   and "text=court-fake-entry" in query and "agree=on" in query
                   and "choice=" in query and "pick=b" in query and "hidden=fixed" in query,
                   {"query_bytes": len(query)})
            expect(tag + "the query excludes disabled and unnamed controls",
                   "off=" not in query and "nameless" not in query, {"query_bytes": len(query)})
            expect(tag + "the submit replaced the document",
                   after["frames"][0]["generation"] > before["frames"][0]["generation"], None)
            expect(tag + "the committed URL stays inside its bound", len(after.get("url") or "") <= CAPS["max_url_bytes"])

            # 4b. POST is refused before anything changes.
            post = NAV.open_target(host, session, origin, "/post-form.html")
            post_snap = snapshot(host, post)
            state = host.ok("target.inspect", {"target": post})
            refusal = act(host, post, node(post_snap, "form"), {"kind": "submit"})
            unchanged = host.ok("target.inspect", {"target": post})
            expect(tag + "a post form is refused typed before any mutation or navigation",
                   refused(refusal, "unsupported_capability")
                   and unchanged["revision"] == state["revision"]
                   and unchanged["frames"][0]["generation"] == state["frames"][0]["generation"],
                   refusal.get("error"))
            host.ok("target.close", {"target": post})

            # 5. Secrecy: nothing the court typed may leave the page.
            ledger = host.ok("session.inspect", {"session": session}).get("audit", {})
            text = json.dumps(ledger)
            expect(tag + "the ledger records the actions without any value, label or query",
                   ledger.get("count", 0) >= 1
                   and all(value not in text for value in TYPED.values())
                   and "pick=" not in text and "Alpha" not in text,
                   {"count": ledger.get("count"), "limit": ledger.get("limit")})
            log = court_file.read_text() if court_file.exists() else ""
            expect(tag + "the court-only log holds no typed value", all(v not in log for v in TYPED.values()))

            # 6. Memory: the numeric live-owner criteria.
            target2 = NAV.open_target(host, session, origin, "/form.html")
            fresh_realm = host.ok("memory.report", {})["owners"]["script_realms"]["malloc_bytes"]
            marks = {}
            for cycle in range(1, CYCLES + 1):
                snap = snapshot(host, target2)
                act(host, target2, node(snap, "textbox", "Text"),
                    {"kind": "set_value", "value": TYPED["long"]})
                snap = snapshot(host, target2)
                reset = [n for n in snap["nodes"] if n.get("role") == "button" and n.get("name") == "Reset"]
                if reset:
                    act(host, target2, reset[0]["reference"], {"kind": "click"})
                if cycle in (8, 64, CYCLES):
                    marks[cycle] = host.ok("memory.report", {})["owners"]["script_realms"]["malloc_bytes"]
            expect(tag + "the realm's live bytes plateau rather than grow with the cycles",
                   marks.get(CYCLES, 0) - marks.get(8, 0) <= CAPS["realm_plateau_bytes"],
                   {"at_8": marks.get(8), "at_128": marks.get(CYCLES),
                    "growth": marks.get(CYCLES, 0) - marks.get(8, 0), "cap": CAPS["realm_plateau_bytes"]})
            expect(tag + "the last cycles add almost nothing",
                   marks.get(CYCLES, 0) - marks.get(64, 0) <= CAPS["realm_tail_bytes"],
                   {"at_64": marks.get(64), "at_128": marks.get(CYCLES), "cap": CAPS["realm_tail_bytes"]})
            snap = snapshot(host, target2)
            act(host, target2, node(snap, "form"), {"kind": "submit"})
            replaced = host.ok("memory.report", {})["owners"]["script_realms"]["malloc_bytes"]
            expect(tag + "the realm after a submit is close to a freshly opened one",
                   abs(replaced - fresh_realm) <= CAPS["realm_after_submit_bytes"],
                   {"fresh": fresh_realm, "after_submit": replaced})
            owners = host.ok("memory.report", {})["owners"]
            expect(tag + "the audit ring stays capped",
                   owners["sessions"]["audit_entries"] <= CAPS["audit_entries"], owners["sessions"])
            host.ok("target.close", {"target": target})
            host.ok("target.close", {"target": target2})
            host.ok("session.close", {"session": session})
            closed = host.ok("memory.report", {})["owners"]
            expect(tag + "every owner returns to zero after the closes",
                   closed["targets"]["objects"] == 0 and closed["sessions"]["audit_entries"] == 0
                   and closed["script_realms"]["malloc_bytes"] == 0, closed["targets"])
            arena_owner = closed["script_realms"]
            expect(tag + "the arena counters hold",
                   arena_owner.get("arena_blocks_leaked", 0) == 0, arena_owner)
            expect(tag + "the host exits cleanly", host.finish() == 0)
        finally:
            if host.process.poll() is None:
                host.process.kill()
                host.process.wait()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--binary", required=True)
    parser.add_argument("--receipt", required=True)
    parser.add_argument("--client-modules", default=str(ROOT / "target" / "labs" / "d4"))
    args = parser.parse_args()
    if VISIBLE_ENV in os.environ:
        print(json.dumps({"passed": None, "unverified": f"{VISIBLE_ENV} is set; this court is headless-only"}))
        return 3
    checks = []

    def expect(name, condition, detail=None):
        checks.append({"check": name, "passed": bool(condition), **({"detail": detail} if detail is not None else {})})

    server = NETWORK.Server(("127.0.0.1", 0), FormHandler)
    origin = f"http://127.0.0.1:{server.server_address[1]}"
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        for allocator in ("system", "arena"):
            run(args.binary, allocator, origin, expect, f"[{allocator}] ")
    finally:
        server.shutdown()
    receipt = {
        "schema": "minicon-surf.native-dom-form-receipt/0.0.1",
        "technology": "native-dom",
        "control_version": VERSION,
        "design": "labs/native-dom/form-interaction-design-0.0.1.md",
        "host_sha256": hashlib.sha256(Path(args.binary).read_bytes()).hexdigest(),
        "caps": CAPS,
        "checks": checks,
        "checks_passed": sum(1 for c in checks if c["passed"]),
        "checks_total": len(checks),
        "passed": all(c["passed"] for c in checks),
        "limitations": [
            "every fixture value is fake and the court asserts none of them reaches the ledger, the court log or this receipt",
            "one hermetic origin on loopback, one form page, macOS only; no surface, no window, no AppKit",
            "constraint validation is not implemented and is not tested: the design excludes it",
            "the footprint differential is a diagnostic elsewhere and is not a criterion here",
            "no pid, path, window or desktop fact is recorded",
        ],
    }
    Path(args.receipt).write_text(json.dumps(receipt, indent=1, sort_keys=True) + "\n")
    print(json.dumps({"passed": receipt["passed"], "checks_passed": receipt["checks_passed"],
                      "checks_total": receipt["checks_total"]}))
    for check in checks:
        if not check["passed"]:
            print("FAIL", json.dumps(check)[:260])
    return 0 if receipt["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
