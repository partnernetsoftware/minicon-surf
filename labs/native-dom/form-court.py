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

Court amendment (mechanism, recorded when the host was implemented): the court
validates its own requests against the contract before sending them, which
refused the deliberately over-long value before the host could. That one
request now skips the court's own check so the host's typed refusal is what is
measured; every other request is still validated both ways. No criterion moved.

Second amendment (mechanism): the keyboard group pressed space on the form's
submit button, which submits and navigates, so the group that follows found no
form. Space is now pressed on a checkbox, which is the other activation the
design defines and changes nothing else, and the submit group starts from a
freshly opened form page. No criterion moved.

Completion, not a movement: group 7 was frozen in the design and missing from
the first implementation of this file. It is implemented now against the
pinned client.

Third amendment (mechanism): three checks put whole option objects into their
details, so the fixture's labels reached the receipt. A label is page text and
a receipt carries none, so the details now report the shape of the options and
not their labels. No criterion moved.
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
    "wide": "courtfake-\u00e9\u4f8b\u00fc",
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
            b"</form><a id=\"lnk\" href=\"/landed.html\">Go</a></main></body></html>")
    POST_FORM = (b"<!doctype html><html><body><main><h1>Post form</h1>"
                 b"<form id=\"p\" method=\"post\" action=\"/landed.html\">"
                 b"<input id=\"pt\" name=\"text\" type=\"text\" value=\"\">"
                 b"<button id=\"pgo\" type=\"submit\">Send</button></form></main></body></html>")

    CANCEL_FORM = (b"<!doctype html><html><body><main><h1>Cancel form</h1>"
                   b"<form id=\"cf\" method=\"get\" action=\"/landed.html\">"
                   b"<input id=\"cc\" name=\"agree\" type=\"checkbox\">"
                   b"<button id=\"cgo\" type=\"submit\">Send</button></form></main>"
                   b"<script>document.getElementById('cc').addEventListener('click',(e)=>e.preventDefault());"
                   b"document.getElementById('cf').addEventListener('submit',(e)=>e.preventDefault());</script>"
                   b"</body></html>")
    HANDLER_FORM = (b"<!doctype html><html><body><main><h1>Handler form</h1>"
                    b"<form id=\"hf\" method=\"get\" action=\"/absent.html\">"
                    b"<input id=\"ht\" name=\"text\" type=\"text\" value=\"\">"
                    b"<button id=\"hgo\" type=\"submit\">Send</button></form>"
                    b"<p id=\"mark\">before</p></main>"
                    b"<script>document.getElementById('hf').addEventListener('submit',()=>{"
                    b"document.getElementById('mark').textContent='handler ran';});</script>"
                    b"</body></html>")
    DENIED_FORM = (b"<!doctype html><html><body><main><h1>Denied form</h1>"
                   b"<form id=\"df\" method=\"get\" action=\"http://10.0.0.1/x.html\">"
                   b"<input id=\"dt\" name=\"text\" type=\"text\" value=\"\">"
                   b"<button id=\"dgo\" type=\"submit\">Send</button></form></main></body></html>")
    SCHEME_FORM = (b"<!doctype html><html><body><main><h1>Scheme form</h1>"
                   b"<form id=\"sf\" method=\"get\" action=\"https://127.0.0.1:1/x.html\">"
                   b"<input id=\"st\" name=\"text\" type=\"text\" value=\"\">"
                   b"<button id=\"sgo\" type=\"submit\">Send</button></form></main></body></html>")

    def do_GET(self):
        path, _, _query = self.path.partition("?")
        if path == "/cancel-form.html":
            return self.reply(200, self.CANCEL_FORM, "text/html")
        if path == "/handler-form.html":
            return self.reply(200, self.HANDLER_FORM, "text/html")
        if path == "/denied-form.html":
            return self.reply(200, self.DENIED_FORM, "text/html")
        if path == "/scheme-form.html":
            return self.reply(200, self.SCHEME_FORM, "text/html")
        if path == "/form.html":
            return self.reply(200, self.FORM, "text/html")
        if path == "/post-form.html":
            return self.reply(200, self.POST_FORM, "text/html")
        if path == "/landed.html":
            return self.reply(200, b"<!doctype html><html><body><main><h1>Landed</h1>"
                                   b"<p id=\"q\">landed</p></main></body></html>", "text/html")
        return super().do_GET()


class Host(NAV.Host):
    """The navigation court's host plus the court-only log, so the secrecy
    group greps something real rather than an absent file."""

    def __init__(self, binary, directory, allocator, origin, court_file=None):
        super().__init__(binary, directory, allocator, origin)
        if court_file is None:
            return
        self.process.stdin.close()
        self.process.wait(timeout=15)
        environment = dict(os.environ)
        for knob in ("MINICON_SURF_NATIVE_REALM_ZONE", "MINICON_SURF_NATIVE_REALM_ARENA",
                     "MINICON_SURF_PROFILE_STORE", VISIBLE_ENV, "http_proxy", "https_proxy", "all_proxy"):
            environment.pop(knob, None)
        if allocator == "arena":
            environment["MINICON_SURF_NATIVE_REALM_ARENA"] = "1"
        command = [binary, "serve", "--stdio", "--fixture-root", str(FIXTURE_ROOT),
                   "--config-dir", str(Path(directory) / "config2"), "--allow-origin", origin,
                   "--surface-court-file", str(court_file)]
        self.process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                        stderr=subprocess.DEVNULL, text=True, env=environment)
        self.counter = 0


def encodings(value):
    """A value as it could appear: plain, percent-encoded, and with + for space."""
    import urllib.parse
    return {value, urllib.parse.quote(value), urllib.parse.quote_plus(value),
            urllib.parse.quote(value, safe=""), value.replace(" ", "+")}


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


def shape(options):
    """What a check may say about options: their shape, never their text."""
    if not isinstance(options, list):
        return {"options": type(options).__name__}
    return {"count": len(options),
            "fields": sorted({key for option in options for key in option}),
            "selected_index": next((o.get("index") for o in options if o.get("selected")), None),
            "disabled_count": sum(1 for o in options if o.get("disabled"))}


def act(host, target, reference, action, deadline_ms=15000, validate=True):
    return host.call("target.act", {"target": target, "reference": reference, "action": action},
                     deadline_ms, validate=validate)


def qualify_cdp(binary, origin, client_modules, expect, tag):
    """Group 7: the form methods stay unprojected, proven with the client."""
    if not (Path(client_modules) / "node_modules").exists():
        expect(tag + "the form methods stay -32601", False,
               {"reason": "the pinned client package is absent from the ignored lab directory"})
        return
    with tempfile.TemporaryDirectory(prefix="minicon-surf-form-cdp-") as directory:
        host = NAV.Host(binary, directory, "system", origin, cdp=True)
        client = None
        try:
            profile = host.ok("profile.create", {"persistence": "ephemeral"})["profile"]
            session = host.ok("session.open", {"profile": profile})["session"]
            target = NAV.open_target(host, session, origin, "/form.html")
            client = NAV.CDP.Client(client_modules)
            client.command("connect", endpoint=host.endpoint())
            client.command("waitForTarget", id=target)
            client.command("attach", name="A", id=target)
            for method, params in (("DOM.setNodeValue", {"nodeId": 1, "value": "x"}),
                                   ("Input.dispatchKeyEvent", {"type": "keyDown", "key": "Enter"}),
                                   ("Input.insertText", {"text": "x"})):
                answer = client.send("A", method, params)
                expect(tag + f"{method} stays an explicit -32601: the slice adds no CDP form surface",
                       NAV.cdp_failed(answer, -32601), answer)
            host.ok("target.close", {"target": target})
            host.ok("session.close", {"session": session})
        finally:
            if client is not None:
                try:
                    client.command("disconnect")
                except Exception:  # noqa: BLE001
                    pass
                client.process.wait(timeout=10)
            if host.process.poll() is None:
                host.finish()


def run(binary, allocator, origin, expect, tag):
    with tempfile.TemporaryDirectory(prefix="minicon-surf-form-court-") as directory:
        court_file = Path(directory) / "court-only.ndjson"
        host = Host(binary, directory, allocator, origin, court_file)
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
                   shape(options))
            expect(tag + "the snapshot reports no option value",
                   all("value" not in o for o in (options or [])), shape(options))
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
                   chosen.get("ok") and options[1]["selected"] is True, shape(options))

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
            # The court's own contract check would refuse this one first.
            over = act(host, target, node(snap, "textbox", "Text"),
                       {"kind": "set_value", "value": "y" * (CAPS["max_value_bytes"] + 1)},
                       validate=False)
            expect(tag + "an over-long value is refused before any change",
                   refused(over, "invalid_request"), over.get("error"))
            far = act(host, target, node(snap, "select"), {"kind": "select_option", "index": 60})
            expect(tag + "an option index outside the list is refused typed",
                   refused(far, "not_found") or refused(far, "invalid_request"), far.get("error"))

            # 3. Keyboard activation, bounded to two keys. Space is pressed on
            # a checkbox: on a submit button it would submit and navigate, and
            # the group below needs the form still there.
            snap = snapshot(host, target)
            before_press = [n.get("checked") for n in snap["nodes"] if n.get("role") == "checkbox"]
            pressed = act(host, target, node(snap, "checkbox"), {"kind": "press", "key": "space"})
            snap = snapshot(host, target)
            after_press = [n.get("checked") for n in snap["nodes"] if n.get("role") == "checkbox"]
            expect(tag + "press space toggles a checkbox",
                   pressed.get("ok") and after_press[0] != before_press[0],
                   {"before": before_press, "after": after_press, "error": pressed.get("error")})

            # 4. Submit: GET serialisation and atomicity, from a fresh form.
            host.ok("target.navigate", {"target": target, "url": f"{origin}/form.html"})
            snap = snapshot(host, target)
            act(host, target, node(snap, "textbox", "Text"), {"kind": "set_value", "value": TYPED["text"]})
            snap = snapshot(host, target)
            act(host, target, node(snap, "checkbox"), {"kind": "set_checked", "checked": True})
            snap = snapshot(host, target)
            act(host, target, node(snap, "radio"), {"kind": "set_checked", "checked": True})
            snap = snapshot(host, target)
            act(host, target, node(snap, "select"), {"kind": "select_option", "index": 1})
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

            # 4c. The activation matrix, in full. Every pair outside it is
            # refused typed, and a refusal never moves the revision.
            host.ok("target.navigate", {"target": target, "url": f"{origin}/form.html"})
            allowed = {("enter", "link"), ("enter", "button"), ("enter", "textbox"),
                       ("space", "button"), ("space", "checkbox"), ("space", "radio")}
            for key in ("enter", "space"):
                for role in ("textbox", "checkbox", "radio", "select", "form", "button", "link"):
                    snap = snapshot(host, target)
                    reference = node(snap, role)
                    if reference is None:
                        continue
                    before = host.ok("target.inspect", {"target": target})["revision"]
                    answer = act(host, target, reference, {"kind": "press", "key": key})
                    after = host.ok("target.inspect", {"target": target})["revision"]
                    if (key, role) in allowed:
                        expect(tag + f"press {key} on a {role} is served",
                               answer.get("ok"), answer.get("error"))
                        if role in ("textbox", "button", "link"):
                            # Those three navigate; start over.
                            host.ok("target.navigate", {"target": target, "url": f"{origin}/form.html"})
                    else:
                        expect(tag + f"press {key} on a {role} is refused typed and moves nothing",
                               refused(answer, "unsupported_capability", "key_role_unsupported")
                               and after == before,
                               {"error": answer.get("error"), "revision": [before, after]})

            # 4d. Cancellation: the page's preventDefault is respected.
            cancel = NAV.open_target(host, session, origin, "/cancel-form.html")
            snap = snapshot(host, cancel)
            answer = act(host, cancel, node(snap, "checkbox"), {"kind": "set_checked", "checked": True})
            snap = snapshot(host, cancel)
            still = [n.get("checked") for n in snap["nodes"] if n.get("role") == "checkbox"]
            expect(tag + "a canceled click leaves the checkbox as it was and is not applied",
                   answer.get("ok") and answer["result"]["applied"] is False
                   and answer["result"]["default_prevented"] is True and still == [False],
                   {"result": answer.get("result"), "checked": still})
            state = host.ok("target.inspect", {"target": cancel})
            answer = act(host, cancel, node(snap, "form"), {"kind": "submit"})
            after = host.ok("target.inspect", {"target": cancel})
            expect(tag + "a canceled submit does not navigate and is not applied",
                   answer.get("ok") and answer["result"]["applied"] is False
                   and answer["result"]["default_prevented"] is True
                   and after["url"] == state["url"]
                   and after["frames"][0]["generation"] == state["frames"][0]["generation"],
                   answer.get("result"))
            host.ok("target.close", {"target": cancel})

            # 4e. A failed submit keeps identity and history; the handler's own
            # effect stays, because no browser rolls that back.
            handler = NAV.open_target(host, session, origin, "/handler-form.html")
            snap = snapshot(host, handler)
            act(host, handler, node(snap, "textbox"), {"kind": "set_value", "value": TYPED["text"]})
            before = host.ok("target.inspect", {"target": handler})
            snap = snapshot(host, handler)
            failed = act(host, handler, node(snap, "form"), {"kind": "submit"})
            after = host.ok("target.inspect", {"target": handler})
            marks = [n.get("name") for n in snapshot(host, handler)["nodes"] if n.get("role") == "text"]
            expect(tag + "a failed submit keeps the target, frame, generation, realm and URL",
                   not failed.get("ok")
                   and after["frames"][0]["generation"] == before["frames"][0]["generation"]
                   and after["frames"][0]["realm"] == before["frames"][0]["realm"]
                   and after["url"] == before["url"] and after["history"] == before["history"],
                   {"error": failed.get("error"), "generation": after["frames"][0]["generation"]})
            expect(tag + "the submit handler's own effect stays and the revision shows it",
                   any("handler ran" in (m or "") for m in marks) and after["revision"] > before["revision"],
                   {"marks": marks, "revision": [before["revision"], after["revision"]]})

            # 4f. Every submit failure is diagnosed without its address.
            failures = [("a denied origin", "/denied-form.html", None),
                        ("a missing document", "/handler-form.html", None),
                        ("an unqualified scheme", "/scheme-form.html", None)]
            responses = []
            for name, page, _ in failures:
                probe = NAV.open_target(host, session, origin, page)
                snap = snapshot(host, probe)
                textbox = node(snap, "textbox")
                if textbox is not None:
                    act(host, probe, textbox, {"kind": "set_value", "value": TYPED["text"]})
                    snap = snapshot(host, probe)
                answer = act(host, probe, node(snap, "form"), {"kind": "submit"})
                responses.append(json.dumps(answer))
                expect(tag + f"{name} refuses the submit and says so without its address",
                       not answer.get("ok")
                       and answer["error"].get("details", {}).get("redacted") is True
                       and "href" not in json.dumps(answer["error"])
                       and "10.0.0.1" not in json.dumps(answer)
                       and "127.0.0.1" not in json.dumps(answer),
                       answer.get("error"))
                host.ok("target.close", {"target": probe})
            offline_probe = NAV.open_target(host, session, origin, "/form.html")
            host.ok("profile.policy.set", {"session": session, "network": "offline",
                                           "permissions": "allow_by_default"})
            snap = snapshot(host, offline_probe)
            answer = act(host, offline_probe, node(snap, "form"), {"kind": "submit"})
            responses.append(json.dumps(answer))
            expect(tag + "an offline profile refuses the submit without its address",
                   not answer.get("ok") and answer["error"].get("details", {}).get("redacted") is True
                   and "?" not in json.dumps(answer), answer.get("error"))
            host.ok("profile.policy.set", {"session": session, "network": "online",
                                           "permissions": "allow_by_default"})
            snap = snapshot(host, offline_probe)
            answer = act(host, offline_probe, node(snap, "form"), {"kind": "submit"}, deadline_ms=1)
            responses.append(json.dumps(answer))
            expect(tag + "an expired deadline refuses the submit without its address",
                   not answer.get("ok") and "?" not in json.dumps(answer), answer.get("error"))
            host.ok("target.close", {"target": offline_probe})

            # 4g. A non-ASCII value is audited by its UTF-8 byte length.
            wide = NAV.open_target(host, session, origin, "/form.html")
            snap = snapshot(host, wide)
            answer = act(host, wide, node(snap, "textbox", "Text"),
                         {"kind": "set_value", "value": TYPED["wide"]})
            expect(tag + "a non-ASCII value is counted in UTF-8 bytes, not code units",
                   answer.get("ok")
                   and answer["result"]["value_bytes"] == len(TYPED["wide"].encode())
                   and answer["result"]["value_bytes"] != len(TYPED["wide"]),
                   {"reported": (answer.get("result") or {}).get("value_bytes"),
                    "utf8": len(TYPED["wide"].encode()), "code_units": len(TYPED["wide"])})
            host.ok("target.close", {"target": wide})

            # 5. Secrecy: nothing the court typed may leave the page.
            ledger = host.ok("session.inspect", {"session": session}).get("audit", {})
            text = json.dumps(ledger)
            expect(tag + "the ledger records the actions without any value, label or query",
                   ledger.get("count", 0) >= 1
                   and all(value not in text for value in TYPED.values())
                   and "pick=" not in text and "Alpha" not in text,
                   {"count": ledger.get("count"), "limit": ledger.get("limit")})
            log = court_file.read_text() if court_file.exists() else ""
            expect(tag + "the court-only log exists and holds no typed value",
                   court_file.exists() and not any(form in log for v in TYPED.values() for form in encodings(v)),
                   {"log_bytes": len(log)})
            everything = " ".join(responses) + text + log
            leaked = sorted({form for v in TYPED.values() for form in encodings(v) if form in everything})
            expect(tag + "no typed value reaches a refusal, the ledger or the log, encoded or not",
                   not leaked, {"leaked": leaked[:3]})

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
        qualify_cdp(args.binary, origin, args.client_modules, expect, "[cdp] ")
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
