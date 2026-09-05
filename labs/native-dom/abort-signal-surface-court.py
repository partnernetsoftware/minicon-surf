#!/usr/bin/env python3
"""The frozen court for the rest of the AbortSignal surface.

Frozen from `abort-signal-surface-audit-0.0.1.md` §8 before the extension
changes, and failing until R1 through R5 exist.

Two of its criteria exist because the gaps they cover **fail silently** today:
`abort(reason)` takes an argument and discards it, and `onabort` accepts a
handler and never calls it. A page cannot tell, which is why they are pinned
by value rather than by presence.

It also holds the ruled shape: `abort()` is a dispatch **because the page
called it**, an abort listener's exception stays swallowed, the state lives in
closure-owned maps a page cannot reach, the handle's key set does not grow, and
the slice stays in the main extension so no child pays for it.

`timeout()` is a deferred candidate and is pinned **absent**, so taking it
later is a ruling that amends this court rather than a diff that slips in.

Strictly headless: no surface binary, no window, no AppKit, and it refuses to
run with the visible-court variable set. One hermetic loopback origin, both
allocators.

Groups: sources, silent failures, event, reentrancy, reason, statics, onabort,
L5 regression.
"""

import argparse
import hashlib
import importlib.util
import json
import os
import re
import sys
import tempfile
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "protocol"))
import check_contract  # noqa: E402,F401

VISIBLE_ENV = "MINICON_SURF_ALLOW_VISIBLE_COURT"
BASE_JS = Path(__file__).with_name("src") / "dom_shim_base.js"
MAIN_JS = Path(__file__).with_name("src") / "dom_shim_main.js"
HANDLE_KEYS = {
    "g", "document", "Document", "Element", "Node", "Event",
    "addListener", "removeListener", "dispatchOn", "contains",
    "focusedElement", "eventStateOf", "signals",
}

PROBES = [
    # The two silent failures, pinned by value.
    ("reason_respected",
     "(function(){var c=new AbortController();c.abort('because');"
     "return 'reason '+String(c.signal.reason);})()"),
    ("onabort_fires",
     "(function(){var c=new AbortController();var fired=0;"
     "c.signal.onabort=function(){fired++;};c.abort();"
     "return 'fired '+fired+'|held '+(typeof c.signal.onabort);})()"),
    # R1: the signal is an EventTarget and abort fires once.
    ("signal_is_event_target",
     "(function(){var s=new AbortController().signal;"
     "return typeof s.addEventListener+'|'+String(s instanceof EventTarget);})()"),
    ("abort_event_once",
     "(function(){var c=new AbortController();var fired=0;"
     "c.signal.addEventListener('abort',function(){fired++;});"
     "c.abort();c.abort();return 'fired '+fired+'|aborted '+String(c.signal.aborted);})()"),
    # The ruled reentrancy shape: the page's own abort dispatches in place.
    ("abort_dispatches_in_place",
     "(function(){var c=new AbortController();var b=document.createElement('span');"
     "var seq=[];c.signal.addEventListener('abort',function(){seq.push('abort listener');});"
     "b.addEventListener('r',function(){seq.push('first');c.abort();seq.push('back');});"
     "b.addEventListener('r',function(){seq.push('third');});"
     "b.dispatchEvent(new Event('r'));return seq.join('>');})()"),
    ("abort_listener_throw_is_local",
     "(function(){var c=new AbortController();"
     "c.signal.addEventListener('abort',function(){throw new Error('x');});"
     "var out;try{c.abort();out='abort returned';}catch(e){out='abort threw:'+e.name;}"
     "return out+'|aborted '+String(c.signal.aborted);})()"),
    # R2: the default reason and a custom one.
    ("default_reason",
     "(function(){var c=new AbortController();var before=String(c.signal.reason);c.abort();"
     "var r=c.signal.reason;"
     "return before+'|'+(r&&r.name)+'|'+String(r instanceof DOMException);})()"),
    ("reason_not_writable",
     "(function(){var c=new AbortController();c.abort('kept');"
     "try{c.signal.reason='forged';}catch(e){return 'read-only:'+e.name;}"
     "return 'now '+String(c.signal.reason);})()"),
    # R3.
    ("throw_if_aborted",
     "(function(){var c=new AbortController();var quiet='did not throw';"
     "try{c.signal.throwIfAborted();}catch(e){quiet='threw early';}"
     "c.abort('why');var thrown='did not throw';"
     "try{c.signal.throwIfAborted();}catch(e){thrown=String(e);}"
     "return quiet+'|'+thrown;})()"),
    # R4.
    ("static_abort",
     "(function(){var s=AbortSignal.abort('static reason');"
     "return String(s.aborted)+'|'+String(s.reason)+'|'"
     "+String(s instanceof AbortSignal);})()"),
    # R5, beyond firing: replacing and clearing.
    ("onabort_replaced",
     "(function(){var c=new AbortController();var a=0,b=0;"
     "c.signal.onabort=function(){a++;};c.signal.onabort=function(){b++;};"
     "c.abort();return 'first '+a+'|second '+b;})()"),
    ("onabort_cleared",
     "(function(){var c=new AbortController();var n=0;"
     "c.signal.onabort=function(){n++;};c.signal.onabort=null;"
     "c.abort();return 'fired '+n+'|held '+String(c.signal.onabort);})()"),
    # The deferred candidate stays absent.
    ("timeout_absent", "typeof AbortSignal.timeout"),
    # The L5 guarantees this slice must not weaken.
    ("hostile_object_still_refused",
     "(function(){try{var b=document.createElement('span');"
     "b.addEventListener('t',function(){},{signal:{aborted:false}});return 'accepted';}"
     "catch(e){return 'threw:'+e.name;}})()"),
    ("hostile_getter_still_refused",
     "(function(){var ran='getter did not run';"
     "var s={get aborted(){ran='getter ran';return false;}};var out;"
     "try{var b=document.createElement('span');"
     "b.addEventListener('t',function(){},{signal:s});out='accepted';}"
     "catch(e){out='threw:'+e.name;}return out+'|'+ran;})()"),
    ("abort_still_removes",
     "(function(){var c=new AbortController();var b=document.createElement('span');var n=0;"
     "b.addEventListener('t',function(){n++;},{signal:c.signal});"
     "b.dispatchEvent(new Event('t'));c.abort();b.dispatchEvent(new Event('t'));"
     "return 'ran '+n;})()"),
    ("aborted_still_not_writable",
     "(function(){var c=new AbortController();"
     "try{c.signal.aborted=true;}catch(e){return 'read-only:'+e.name;}"
     "return 'now '+String(c.signal.aborted);})()"),
]


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


def handle_keys(base):
    start = base.index("return take({")
    end = base.index("});", start)
    body = re.sub(r"//[^\n]*", "", base[start + len("return take({"):end])
    keys, depth, field = set(), 0, ""
    for character in body:
        if character in "([{":
            depth += 1
        elif character in ")]}":
            depth -= 1
        if character == "," and depth == 0:
            keys.add(field.split(":")[0].strip())
            field = ""
        else:
            field += character
    if field.strip():
        keys.add(field.split(":")[0].strip())
    return {key for key in keys if key}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--binary", required=True)
    parser.add_argument("--receipt", required=True)
    args = parser.parse_args()
    if os.environ.get(VISIBLE_ENV):
        print(json.dumps({"passed": False, "reason": "the visible-court variable is set"}))
        return 1

    network = RETENTION.load_network_module()
    slots = "".join("<p id=r%d></p>" % i for i in range(len(PROBES)))
    script = "".join(
        "try{var v%d=String(%s);}catch(e){var v%d='probe-threw:'+e.name;}"
        "document.getElementById('r%d').textContent='%s='+v%d;"
        % (i, expression, i, i, name, i)
        for i, (name, expression) in enumerate(PROBES))
    PAGE = ("<!doctype html><html><body><main>" + slots + "</main><script>"
            + script + "</script></body></html>").encode()

    class Handler(network.Handler):
        def do_GET(self):
            network.Handler.hits.append(self.path.partition("?")[0])
            return self.reply(200, PAGE)

    server = network.Server(("127.0.0.1", 0), Handler)
    origin = f"http://127.0.0.1:{server.server_address[1]}"
    threading.Thread(target=server.serve_forever, daemon=True).start()
    checks = []
    killed_hosts = []

    def expect(name, condition, detail=None):
        checks.append({"check": name, "passed": bool(condition),
                       **({"detail": detail} if detail is not None else {})})

    base = BASE_JS.read_text()
    main_source = MAIN_JS.read_text()
    expect("H1: the handle's key set does not grow for this slice",
           handle_keys(base) == HANDLE_KEYS,
           {"unexpected": sorted(handle_keys(base) - HANDLE_KEYS),
            "missing": sorted(HANDLE_KEYS - handle_keys(base))})
    expect("H2: the slice is main-only, so no child pays for it",
           "throwIfAborted" in main_source and "throwIfAborted" not in base
           and "onabort" in main_source and "onabort" not in base,
           {"in_main": "throwIfAborted" in main_source,
            "in_base": "throwIfAborted" in base})
    expect("H3: the state stays in closure-owned maps, not on the signal",
           "signalReason" in main_source and "signalHandler" in main_source,
           {"reason_map": "signalReason" in main_source,
            "handler_map": "signalHandler" in main_source})

    try:
        for allocator in ("system", "arena"):
            tag = f"[{allocator}] "
            with tempfile.TemporaryDirectory(prefix="minicon-surf-signalsurf-") as directory:
                host = JOBS.Supervised(args.binary, directory, origin, allocator)
                try:
                    profile = host.ok("profile.create", {"persistence": "ephemeral"})["profile"]
                    session = host.ok("session.open", {"profile": profile})["session"]
                    target = host.ok("target.open",
                                     {"session": session,
                                      "url": origin + "/page.html"})["target"]
                    said = {}
                    for node in host.ok("target.snapshot",
                                        {"target": target, "format": "semantic",
                                         "max_bytes": 131072,
                                         "max_nodes": 128})["nodes"]:
                        text = node.get("name") or ""
                        if node.get("role") == "text" and "=" in text:
                            key, _, value = text.partition("=")
                            said[key] = value

                    expect(tag + "F1: abort(reason) keeps the reason it was given",
                           said.get("reason_respected") == "reason because",
                           {"said": said.get("reason_respected")})
                    expect(tag + "F2: onabort is called, and reads back as a function",
                           said.get("onabort_fires") == "fired 1|held function",
                           {"said": said.get("onabort_fires")})
                    expect(tag + "R1a: the signal is an EventTarget",
                           said.get("signal_is_event_target") == "function|true",
                           {"said": said.get("signal_is_event_target")})
                    expect(tag + "R1b: abort fires once, and a second abort is inert",
                           said.get("abort_event_once") == "fired 1|aborted true",
                           {"said": said.get("abort_event_once")})
                    expect(tag + "R1c: the page's own abort dispatches where it was called",
                           said.get("abort_dispatches_in_place")
                           == "first>abort listener>back>third",
                           {"said": said.get("abort_dispatches_in_place")})
                    expect(tag + "R1d: an abort listener's exception stays local",
                           said.get("abort_listener_throw_is_local")
                           == "abort returned|aborted true",
                           {"said": said.get("abort_listener_throw_is_local")})
                    expect(tag + "R2a: the default reason is an AbortError DOMException",
                           said.get("default_reason") == "undefined|AbortError|true",
                           {"said": said.get("default_reason")})
                    expect(tag + "R2b: and a page cannot write it",
                           said.get("reason_not_writable", "").startswith("read-only:")
                           or said.get("reason_not_writable") == "now kept",
                           {"said": said.get("reason_not_writable")})
                    expect(tag + "R3: throwIfAborted is quiet until it is not",
                           (said.get("throw_if_aborted") or "").startswith("did not throw|")
                           and "why" in (said.get("throw_if_aborted") or ""),
                           {"said": said.get("throw_if_aborted")})
                    expect(tag + "R4: the static mints an already-aborted signal",
                           said.get("static_abort") == "true|static reason|true",
                           {"said": said.get("static_abort")})
                    expect(tag + "R5a: setting onabort twice keeps only the second",
                           said.get("onabort_replaced") == "first 0|second 1",
                           {"said": said.get("onabort_replaced")})
                    expect(tag + "R5b: and clearing it removes the handler",
                           said.get("onabort_cleared") == "fired 0|held null",
                           {"said": said.get("onabort_cleared")})
                    expect(tag + "D1: timeout stays absent, as the deferred candidate",
                           said.get("timeout_absent") == "undefined",
                           {"said": said.get("timeout_absent")})
                    expect(tag + "L5a: a page object is still not a signal",
                           said.get("hostile_object_still_refused") == "threw:TypeError",
                           {"said": said.get("hostile_object_still_refused")})
                    expect(tag + "L5b: nor one with a getter, which still never runs",
                           said.get("hostile_getter_still_refused")
                           == "threw:TypeError|getter did not run",
                           {"said": said.get("hostile_getter_still_refused")})
                    expect(tag + "L5c: abort still removes the listener",
                           said.get("abort_still_removes") == "ran 1",
                           {"said": said.get("abort_still_removes")})
                    expect(tag + "L5d: and aborted is still not writable by a page",
                           (said.get("aborted_still_not_writable") or "")
                           .startswith("read-only:"),
                           {"said": said.get("aborted_still_not_writable")})
                finally:
                    if host.killed:
                        killed_hosts.append({"allocator": allocator})
                    host.finish()
                    killed_hosts.extend({"allocator": allocator, **t} for t in host.timeouts)
    finally:
        server.shutdown()

    receipt = {
        "court": "native-dom AbortSignal surface, R1-R5 (control 0.0.2)",
        "host_sha256": hashlib.sha256(Path(args.binary).read_bytes()).hexdigest(),
        "deferred": ["AbortSignal.timeout", "AbortSignal.any"],
        "checks": checks,
        "checks_passed": sum(1 for c in checks if c["passed"]),
        "checks_total": len(checks),
        "passed": all(c["passed"] for c in checks) and not killed_hosts,
        "hosts_killed": killed_hosts,
        "limitations": [
            "design-frozen court: it fails until R1 through R5 exist",
            "timeout() is pinned absent by ruling, so taking it later amends this court rather than slipping past it",
            "three criteria read the shipped sources beside this court rather than the binary, so they are repo-local by design",
            "the main-only slack and the M1/M2 floors are measured by the shim-footprint and child-frame courts on the same binary; H2 is what keeps this slice out of the base, which is what protects the child floors",
            "one hermetic loopback origin, macOS only; no surface, no window, no AppKit",
        ],
    }
    Path(args.receipt).write_text(json.dumps(receipt, indent=1, sort_keys=True) + "\n")
    print(json.dumps({"passed": receipt["passed"], "checks_passed": receipt["checks_passed"],
                      "checks_total": receipt["checks_total"],
                      "hosts_killed": len(killed_hosts)}))
    for check in checks:
        if not check["passed"]:
            print("FAIL", json.dumps(check)[:170])
    return 0 if receipt["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
