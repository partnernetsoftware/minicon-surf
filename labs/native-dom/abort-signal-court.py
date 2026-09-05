#!/usr/bin/env python3
"""The frozen court for branded abort signals.

Frozen from `abort-signal-audit-0.0.1.md` §9 before the shim changes, and
failing until the branded model exists.

Two things it holds the host to that are not about `AbortController` at all:
the **handle's exact key set**, so widening the one-shot handle is a decision
that fails a criterion rather than a diff nobody notices, and the placement of
the model — classes in the extension, brand and flags in the base.

The rest is the ruled minimum: a signal the host did not mint is a
`TypeError`, so no page object and no page getter ever reaches a record; abort
removes the listener; a signal already aborted registers nothing; an abort
during a dispatch stops a listener that has not run yet and does not unwind
one that has; it composes with `capture`, `once` and a `handleEvent` object;
`aborted` answers the host's own state and a page cannot write it. Owners come
back when the targets close, and a child realm still runs no scripts, so it
pays for this and cannot use it.

Strictly headless: no surface binary, no window, no AppKit, and it refuses to
run with the visible-court variable set. One hermetic loopback origin, both
allocators.

Groups: handle, placement, hostile, semantics, composition, owners, child.
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
# The handle, exactly. Frozen here so that adding to it is a ruling and not a
# diff: `signals` is the one entry this slice is allowed to add.
HANDLE_KEYS = {
    "g", "document", "Document", "Element", "Node", "Event",
    "addListener", "removeListener", "dispatchOn", "contains",
    "focusedElement", "eventStateOf", "signals",
}
OWNER_RETURN_BYTES = 65536

PROBES = [
    ("constructors", "typeof AbortController+'|'+typeof AbortSignal"),
    # A page object is not a signal.
    ("hostile_object",
     "(function(){try{var b=document.createElement('span');"
     "b.addEventListener('t',function(){},{signal:{aborted:false}});"
     "return 'accepted';}catch(e){return 'threw:'+e.name;}})()"),
    # Nor is one with a getter, and the getter must never run.
    ("hostile_getter",
     "(function(){var ran='getter did not run';"
     "var s={get aborted(){ran='getter ran';return false;}};"
     "var out;try{var b=document.createElement('span');"
     "b.addEventListener('t',function(){},{signal:s});out='accepted';}"
     "catch(e){out='threw:'+e.name;}return out+'|'+ran;})()"),
    ("abort_removes",
     "(function(){var c=new AbortController();var b=document.createElement('span');"
     "var n=0;b.addEventListener('t',function(){n++;},{signal:c.signal});"
     "b.dispatchEvent(new Event('t'));c.abort();b.dispatchEvent(new Event('t'));"
     "return 'ran '+n;})()"),
    ("pre_aborted",
     "(function(){var c=new AbortController();c.abort();"
     "var b=document.createElement('span');var n=0;"
     "b.addEventListener('t2',function(){n++;},{signal:c.signal});"
     "b.dispatchEvent(new Event('t2'));return 'ran '+n;})()"),
    ("abort_mid_dispatch",
     "(function(){var c=new AbortController();var b=document.createElement('span');"
     "var seq=[];b.addEventListener('t3',function(){seq.push('first');c.abort();});"
     "b.addEventListener('t3',function(){seq.push('second');},{signal:c.signal});"
     "b.dispatchEvent(new Event('t3'));return seq.join('>');})()"),
    ("abort_after_running",
     "(function(){var c=new AbortController();var b=document.createElement('span');"
     "var seq=[];b.addEventListener('t4',function(){seq.push('signalled');},{signal:c.signal});"
     "b.addEventListener('t4',function(){seq.push('plain');c.abort();});"
     "b.dispatchEvent(new Event('t4'));b.dispatchEvent(new Event('t4'));"
     "return seq.join('>');})()"),
    ("with_capture",
     "(function(){var c=new AbortController();var p=document.getElementById('box');"
     "var t=document.getElementById('go');var n=0;"
     "p.addEventListener('t5',function(){n++;},{capture:true,signal:c.signal});"
     "t.dispatchEvent(new Event('t5',{bubbles:true}));c.abort();"
     "t.dispatchEvent(new Event('t5',{bubbles:true}));return 'ran '+n;})()"),
    ("with_once",
     "(function(){var c=new AbortController();var b=document.createElement('span');"
     "var n=0;b.addEventListener('t6',function(){n++;},{once:true,signal:c.signal});"
     "b.dispatchEvent(new Event('t6'));b.dispatchEvent(new Event('t6'));"
     "return 'ran '+n;})()"),
    ("with_handle_event",
     "(function(){var c=new AbortController();var b=document.createElement('span');"
     "var n=0;b.addEventListener('t7',{handleEvent:function(){n++;}},{signal:c.signal});"
     "b.dispatchEvent(new Event('t7'));c.abort();b.dispatchEvent(new Event('t7'));"
     "return 'ran '+n;})()"),
    # aborted answers the host, and a page cannot write it.
    ("aborted_is_the_hosts",
     "(function(){var c=new AbortController();var before=c.signal.aborted;"
     "try{c.signal.aborted=true;}catch(e){}var forged=c.signal.aborted;"
     "c.abort();return String(before)+'|'+String(forged)+'|'+String(c.signal.aborted);})()"),
    # A signal the page builds by hand, even shaped correctly, is not minted.
    ("forged_shape",
     "(function(){try{var fake=Object.create(AbortSignal.prototype);"
     "var b=document.createElement('span');"
     "b.addEventListener('t8',function(){},{signal:fake});return 'accepted';}"
     "catch(e){return 'threw:'+e.name;}})()"),
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
    """The keys the base hands the extension, read out of the shipped source."""
    start = base.index("return take({")
    end = base.index("});", start)
    body = base[start + len("return take({"):end]
    body = re.sub(r"//[^\n]*", "", body)
    keys = set()
    depth = 0
    field = ""
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
    PAGE = ("<!doctype html><html><body><main><div id=\"box\">"
            "<button id=\"go\" type=\"button\">go</button></div>"
            + slots + "</main><script>" + script + "</script></body></html>").encode()
    MANY = ("<!doctype html><html><body><main><p id=m>many</p></main><script>"
            "var m=document.getElementById('m');"
            "for(var i=0;i<200;i+=1){var c=new AbortController();"
            "m.addEventListener('s'+i,function(){},{signal:c.signal});}"
            "</script></body></html>").encode()
    CHILD = ("<!doctype html><html><body><main><p id=c>embedded static</p><script>"
             "document.getElementById('c').textContent='embedded dynamic';"
             "</script></main></body></html>").encode()
    PARENT = b"<!doctype html><html><body><main><iframe src='/child.html'></iframe></main></body></html>"

    class Handler(network.Handler):
        def do_GET(self):
            path, _, _query = self.path.partition("?")
            network.Handler.hits.append(path)
            pages = {"/many.html": MANY, "/child.html": CHILD, "/parent.html": PARENT}
            return self.reply(200, pages.get(path, PAGE))

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
    keys = handle_keys(base)
    expect("H1: the one-shot handle hands over exactly these names",
           keys == HANDLE_KEYS,
           {"unexpected": sorted(keys - HANDLE_KEYS),
            "missing": sorted(HANDLE_KEYS - keys)})
    expect("H2: the brand lives in the base and the classes do not",
           "hostSignals" in base and "abortedSignals" in base
           and "class AbortController" not in base,
           {"brand_in_base": "hostSignals" in base,
            "classes_in_base": "class AbortController" in base})
    expect("H3: the classes live in the extension and the brand does not",
           "class AbortController" in main_source and "class AbortSignal" in main_source
           and "new WeakSet()" not in main_source,
           {"classes_in_main": "class AbortController" in main_source,
            "weakset_in_main": "new WeakSet()" in main_source})

    try:
        for allocator in ("system", "arena"):
            tag = f"[{allocator}] "
            with tempfile.TemporaryDirectory(prefix="minicon-surf-signal-") as directory:
                host = JOBS.Supervised(args.binary, directory, origin, allocator)
                try:
                    empty = host.ok("memory.report", {})["owners"]
                    baseline = (empty["script_realms"]["malloc_bytes"]
                                + empty["targets"]["fixture_bytes"])
                    profile = host.ok("profile.create", {"persistence": "ephemeral"})["profile"]
                    session = host.ok("session.open", {"profile": profile})["session"]

                    def look(target, **extra):
                        return host.call("target.snapshot",
                                         {"target": target, "format": "semantic",
                                          "max_bytes": 131072, "max_nodes": 128, **extra})

                    target = host.ok("target.open",
                                     {"session": session,
                                      "url": origin + "/page.html"})["target"]
                    said = {}
                    for node in look(target)["result"]["nodes"]:
                        text = node.get("name") or ""
                        if node.get("role") == "text" and "=" in text:
                            key, _, value = text.partition("=")
                            said[key] = value

                    expect(tag + "S1: the constructors exist",
                           said.get("constructors") == "function|function",
                           {"said": said.get("constructors")})
                    expect(tag + "S2: a page object is not a signal",
                           said.get("hostile_object") == "threw:TypeError",
                           {"said": said.get("hostile_object")})
                    expect(tag + "S3: nor one with a getter, and the getter never runs",
                           said.get("hostile_getter") == "threw:TypeError|getter did not run",
                           {"said": said.get("hostile_getter")})
                    expect(tag + "S4: an object shaped like a signal is still not one",
                           said.get("forged_shape") == "threw:TypeError",
                           {"said": said.get("forged_shape")})
                    expect(tag + "S5: abort removes the listener",
                           said.get("abort_removes") == "ran 1",
                           {"said": said.get("abort_removes")})
                    expect(tag + "S6: a signal already aborted registers nothing",
                           said.get("pre_aborted") == "ran 0",
                           {"said": said.get("pre_aborted")})
                    expect(tag + "S7: an abort during a dispatch stops a listener not yet run",
                           said.get("abort_mid_dispatch") == "first",
                           {"said": said.get("abort_mid_dispatch")})
                    expect(tag + "S8: and does not unwind one that already ran",
                           said.get("abort_after_running") == "signalled>plain",
                           {"said": said.get("abort_after_running")})
                    expect(tag + "S9: it composes with capture, once and handleEvent",
                           said.get("with_capture") == "ran 1"
                           and said.get("with_once") == "ran 1"
                           and said.get("with_handle_event") == "ran 1",
                           {"capture": said.get("with_capture"),
                            "once": said.get("with_once"),
                            "handle_event": said.get("with_handle_event")})
                    expect(tag + "S10: aborted answers the host, and a page cannot write it",
                           said.get("aborted_is_the_hosts") == "false|false|true",
                           {"said": said.get("aborted_is_the_hosts")})

                    # A child realm pays for this and cannot use it.
                    parent = host.ok("target.open",
                                     {"session": session,
                                      "url": origin + "/parent.html"})["target"]
                    frames = (host.ok("target.inspect", {"target": parent}).get("frames") or [])
                    child_frame = frames[1]["frame"] if len(frames) > 1 else "frame_absent"
                    child = look(parent, frame=child_frame)
                    names = ([n.get("name") for n in child["result"]["nodes"]]
                             if child.get("ok") else [])
                    expect(tag + "S11: a child realm still runs no scripts",
                           child.get("ok")
                           and any("embedded static" in (n or "") for n in names)
                           and not any("embedded dynamic" in (n or "") for n in names),
                           {"nodes": len(names)})

                    # Owners come back.
                    # On a build without the model this page's own script
                    # throws, which is a failed criterion elsewhere and must
                    # not abort the court: the owners still have to come back.
                    opened = host.call("target.open",
                                       {"session": session,
                                        "url": origin + "/many.html"})
                    if opened.get("ok"):
                        host.ok("target.close", {"target": opened["result"]["target"]})
                    host.ok("target.close", {"target": parent})
                    host.ok("target.close", {"target": target})
                    host.ok("session.close", {"session": session})
                    after = host.ok("memory.report", {})["owners"]
                    live = (after["script_realms"]["malloc_bytes"]
                            + after["targets"]["fixture_bytes"])
                    expect(tag + "S12: the owners come back when the targets close",
                           live <= baseline + OWNER_RETURN_BYTES,
                           {"baseline": baseline, "after": live,
                            "bound": OWNER_RETURN_BYTES})
                finally:
                    if host.killed:
                        killed_hosts.append({"allocator": allocator})
                    host.finish()
                    killed_hosts.extend({"allocator": allocator, **t} for t in host.timeouts)
    finally:
        server.shutdown()

    receipt = {
        "court": "native-dom branded abort signals (control 0.0.2)",
        "host_sha256": hashlib.sha256(Path(args.binary).read_bytes()).hexdigest(),
        "handle_keys": sorted(HANDLE_KEYS),
        "checks": checks,
        "checks_passed": sum(1 for c in checks if c["passed"]),
        "checks_total": len(checks),
        "passed": all(c["passed"] for c in checks) and not killed_hosts,
        "hosts_killed": killed_hosts,
        "limitations": [
            "design-frozen court: it fails until the branded model exists",
            "H1 pins the handle's exact key set by design, so any future widening fails this criterion until it is ruled and the set is amended here",
            "three criteria read the shipped sources beside this court rather than the binary, so they are repo-local by design",
            "the abort event on a signal, reason, throwIfAborted, the statics and onabort are separate candidates and are deliberately not tested",
            "the M1 and M2 floors and the main-only slack are measured by the child-frame and shim-footprint courts on the same binary",
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
