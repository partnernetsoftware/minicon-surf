#!/usr/bin/env python3
"""The frozen court for the first listener-options rung.

Frozen from `listener-options-audit-0.0.1.md` §9 before the shim changes, and
failing until the options are forwarded, `once` is honoured and an object with
`handleEvent` is accepted.

What it holds the host to: `once` runs a listener exactly once on a page's own
dispatch **and** on the host's, and removes the registration so a later
`removeEventListener` is a no-op and a re-add is a new registration; a
listener that throws is still removed; an object with `handleEvent` is called
with itself as the receiver and is its own identity for removal; the handler
is resolved **at registration**, so a page that swaps the method afterwards
keeps what it registered and the host never reads a page property while it
dispatches; `window` and `EventTarget` honour the options exactly as a node
does; and the owners come back when the target closes.

Deliberately not pinned: `capture`, `passive` and `signal`, which are deferred
rungs. This court must not have to be amended when they land.

Strictly headless: no surface binary, no window, no AppKit, and it refuses to
run with the visible-court variable set. One hermetic loopback origin, both
allocators.

Groups: sources, once, host-driven, identity, throw, handleEvent, forwarding,
owners.
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
# Owners return to the empty-host level within this after the target closes.
OWNER_RETURN_BYTES = 65536

PROBES = [
    # once, on a page's own dispatch.
    ("once_page",
     "(function(){var b=document.createElement('span');var n=0;"
     "b.addEventListener('t',function(){n++;},{once:true});"
     "b.dispatchEvent(new Event('t'));b.dispatchEvent(new Event('t'));"
     "return 'ran '+n;})()"),
    # once through the boolean-free object form and through no options at all.
    ("no_options_still_repeats",
     "(function(){var b=document.createElement('span');var n=0;"
     "b.addEventListener('t',function(){n++;});"
     "b.dispatchEvent(new Event('t'));b.dispatchEvent(new Event('t'));"
     "return 'ran '+n;})()"),
    # After once has fired: removing is a no-op, and a re-add is a new one.
    ("once_then_readd",
     "(function(){var b=document.createElement('span');var n=0;"
     "var f=function(){n++;};b.addEventListener('t',f,{once:true});"
     "b.dispatchEvent(new Event('t'));"
     "b.removeEventListener('t',f);"
     "b.addEventListener('t',f);"
     "b.dispatchEvent(new Event('t'));b.dispatchEvent(new Event('t'));"
     "return 'ran '+n;})()"),
    # A once listener that throws is still gone afterwards.
    ("once_throws",
     "(function(){var b=document.createElement('span');var n=0;"
     "b.addEventListener('t',function(){n++;throw new Error('x');},{once:true});"
     "b.dispatchEvent(new Event('t'));b.dispatchEvent(new Event('t'));"
     "return 'ran '+n;})()"),
    # handleEvent: called, with the object as the receiver.
    ("handle_event",
     "(function(){var b=document.createElement('span');var n=0;var self=null;"
     "var o={handleEvent:function(){n++;self=this;}};"
     "b.addEventListener('t',o);b.dispatchEvent(new Event('t'));"
     "return 'ran '+n+'|this is the object '+String(self===o);})()"),
    # The object is its own identity for removal.
    ("handle_event_remove",
     "(function(){var b=document.createElement('span');var n=0;"
     "var o={handleEvent:function(){n++;}};"
     "b.addEventListener('t',o);b.removeEventListener('t',o);"
     "b.dispatchEvent(new Event('t'));return 'ran '+n;})()"),
    # The handler is resolved at registration: the host reads no page property
    # while it dispatches, so a later swap does not take effect.
    ("handle_event_frozen",
     "(function(){var b=document.createElement('span');var first=0,second=0;"
     "var o={handleEvent:function(){first++;}};b.addEventListener('t',o);"
     "o.handleEvent=function(){second++;};b.dispatchEvent(new Event('t'));"
     "return 'registered '+first+'|swapped '+second;})()"),
    # An object with no callable handleEvent registers nothing and does not throw.
    ("handle_event_absent",
     "(function(){try{var b=document.createElement('span');"
     "b.addEventListener('t',{});b.dispatchEvent(new Event('t'));"
     "return 'ignored quietly';}catch(e){return 'threw:'+e.name;}})()"),
    # once and handleEvent together.
    ("handle_event_once",
     "(function(){var b=document.createElement('span');var n=0;"
     "b.addEventListener('t',{handleEvent:function(){n++;}},{once:true});"
     "b.dispatchEvent(new Event('t'));b.dispatchEvent(new Event('t'));"
     "return 'ran '+n;})()"),
    # The other two call sites forward options too.
    ("window_once",
     "(function(){var n=0;window.addEventListener('wo',function(){n++;},{once:true});"
     "window.dispatchEvent(new Event('wo'));window.dispatchEvent(new Event('wo'));"
     "return 'ran '+n;})()"),
    ("eventtarget_once",
     "(function(){try{var t=new EventTarget();var n=0;"
     "t.addEventListener('x',function(){n++;},{once:true});"
     "t.dispatchEvent(new Event('x'));t.dispatchEvent(new Event('x'));"
     "return 'ran '+n;}catch(e){return 'threw:'+e.name;}})()"),
    # Dispatch semantics that must not shift with this rung.
    ("cancel_unchanged",
     "(function(){var b=document.createElement('span');"
     "b.addEventListener('c',function(e){e.preventDefault();});"
     "var ev=new Event('c',{cancelable:true});var r=b.dispatchEvent(ev);"
     "return 'returned '+r+'|prevented '+ev.defaultPrevented;})()"),
    # The host's own clicks: a once listener counts them for the page to read.
    ("host_once_registered",
     "(function(){var b=document.getElementById('go');var n=0;"
     "b.addEventListener('click',function(){n++;"
     "document.getElementById('count').textContent='clicks '+n;},{once:true});"
     "return 'registered';})()"),
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
    PAGE = ("<!doctype html><html><body><main><button id=go type=button>go</button>"
            "<p id=\"count\">clicks 0</p>" + slots + "</main><script>"
            + script + "</script></body></html>").encode()
    # A page that registers many listeners, so the owners have something to
    # return when the target closes.
    MANY = ("<!doctype html><html><body><main><p id=m>many</p></main><script>"
            "var m=document.getElementById('m');"
            "for(var i=0;i<200;i+=1){"
            "m.addEventListener('t'+i,function(){},{once:true});"
            "m.addEventListener('h'+i,{handleEvent:function(){}});}"
            "</script></body></html>").encode()

    class Handler(network.Handler):
        def do_GET(self):
            path, _, _query = self.path.partition("?")
            network.Handler.hits.append(path)
            return self.reply(200, MANY if path == "/many.html" else PAGE)

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
    forwarding = (
        len(re.findall(r"addEventListener\(type, fn, options\)", base))
        + len(re.findall(r"addEventListener\(type, fn, options\)", main_source))
        + len(re.findall(r"g\.addEventListener = \(type, fn, options\)", main_source)))
    expect("S1: all three call sites forward the options",
           forwarding == 3, {"forwarding_sites": forwarding})
    # The handler is resolved where registration happens and nowhere else.
    # Sliced, not searched: a comment that merely mentions the name must not
    # be able to pass this. The resolution belongs in the registration and
    # nowhere near the walk.
    dispatch_body = base[base.index("function dispatchOn("):] if "function dispatchOn(" in base else ""
    add_body = ""
    if "function addListener(" in base and "function removeListener(" in base:
        add_body = base[base.index("function addListener("):base.index("function removeListener(")]
    expect("S2: handleEvent is resolved in the registration and never read by the walk",
           "handleEvent" in add_body and "handleEvent" not in dispatch_body,
           {"in_registration": "handleEvent" in add_body,
            "in_dispatch": "handleEvent" in dispatch_body})

    try:
        for allocator in ("system", "arena"):
            tag = f"[{allocator}] "
            with tempfile.TemporaryDirectory(prefix="minicon-surf-listopt-") as directory:
                host = JOBS.Supervised(args.binary, directory, origin, allocator)
                try:
                    empty = host.ok("memory.report", {})["owners"]
                    baseline = (empty["script_realms"]["malloc_bytes"]
                                + empty["targets"]["fixture_bytes"])
                    profile = host.ok("profile.create", {"persistence": "ephemeral"})["profile"]
                    session = host.ok("session.open", {"profile": profile})["session"]
                    target = host.ok("target.open",
                                     {"session": session,
                                      "url": origin + "/page.html"})["target"]
                    snapshot = host.ok("target.snapshot",
                                       {"target": target, "format": "semantic",
                                        "max_bytes": 131072, "max_nodes": 128})
                    said = {}
                    for node in snapshot["nodes"]:
                        text = node.get("name") or ""
                        if node.get("role") == "text" and "=" in text:
                            key, _, value = text.partition("=")
                            said[key] = value

                    expect(tag + "L1: a once listener runs once on a page's dispatch",
                           said.get("once_page") == "ran 1", {"said": said.get("once_page")})
                    expect(tag + "L1: and a listener without options still runs every time",
                           said.get("no_options_still_repeats") == "ran 2",
                           {"said": said.get("no_options_still_repeats")})
                    expect(tag + "L1: after it fires, removing is a no-op and a re-add is a new registration",
                           said.get("once_then_readd") == "ran 3",
                           {"said": said.get("once_then_readd")})
                    expect(tag + "L1: a once listener that throws is still removed",
                           said.get("once_throws") == "ran 1",
                           {"said": said.get("once_throws")})
                    expect(tag + "L2: an object with handleEvent is called with itself as the receiver",
                           said.get("handle_event") == "ran 1|this is the object true",
                           {"said": said.get("handle_event")})
                    expect(tag + "L2: the object is its own identity for removal",
                           said.get("handle_event_remove") == "ran 0",
                           {"said": said.get("handle_event_remove")})
                    expect(tag + "L2: the handler is resolved at registration, not read during dispatch",
                           said.get("handle_event_frozen") == "registered 1|swapped 0",
                           {"said": said.get("handle_event_frozen")})
                    expect(tag + "L2: an object without a callable handleEvent is ignored quietly",
                           said.get("handle_event_absent") == "ignored quietly",
                           {"said": said.get("handle_event_absent")})
                    expect(tag + "L1+L2: they compose",
                           said.get("handle_event_once") == "ran 1",
                           {"said": said.get("handle_event_once")})
                    expect(tag + "L0: window and EventTarget honour the options like a node",
                           said.get("window_once") == "ran 1"
                           and said.get("eventtarget_once") == "ran 1",
                           {"window": said.get("window_once"),
                            "eventtarget": said.get("eventtarget_once")})
                    expect(tag + "cancelling is unchanged by this rung",
                           said.get("cancel_unchanged") == "returned false|prevented true",
                           {"said": said.get("cancel_unchanged")})

                    # The host's own dispatch: two clicks, one run.
                    expect(tag + "the page could register for the host's click",
                           said.get("host_once_registered") == "registered",
                           {"said": said.get("host_once_registered")})
                    counted = None
                    for _ in range(2):
                        seen = host.ok("target.snapshot",
                                       {"target": target, "format": "semantic",
                                        "max_bytes": 131072, "max_nodes": 128})
                        button = next((n for n in seen["nodes"]
                                       if n.get("role") == "button"), None)
                        if button:
                            host.call("target.act",
                                      {"target": target, "reference": button["reference"],
                                       "action": {"kind": "click"}})
                    seen = host.ok("target.snapshot",
                                   {"target": target, "format": "semantic",
                                    "max_bytes": 131072, "max_nodes": 128})
                    for node in seen["nodes"]:
                        text = node.get("name") or ""
                        if node.get("role") == "text" and text.startswith("clicks "):
                            counted = text
                    expect(tag + "L1: two of the agent's own clicks run a once listener once",
                           counted == "clicks 1", {"said": counted})

                    # Owners come back when the target closes.
                    many = host.ok("target.open",
                                   {"session": session,
                                    "url": origin + "/many.html"})["target"]
                    host.ok("target.close", {"target": many})
                    host.ok("target.close", {"target": target})
                    host.ok("session.close", {"session": session})
                    after = host.ok("memory.report", {})["owners"]
                    live = (after["script_realms"]["malloc_bytes"]
                            + after["targets"]["fixture_bytes"])
                    expect(tag + "the owners come back when the targets close",
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
        "court": "native-dom listener options, first rung (control 0.0.2)",
        "host_sha256": hashlib.sha256(Path(args.binary).read_bytes()).hexdigest(),
        "thresholds": {"owner_return_bytes": OWNER_RETURN_BYTES},
        "checks": checks,
        "checks_passed": sum(1 for c in checks if c["passed"]),
        "checks_total": len(checks),
        "passed": all(c["passed"] for c in checks) and not killed_hosts,
        "hosts_killed": killed_hosts,
        "limitations": [
            "design-frozen court: it fails until the options are forwarded and once and handleEvent are honoured",
            "capture, passive and signal are deferred rungs and are deliberately not pinned here, so this court needs no amendment when they land",
            "two criteria read the shipped sources beside this court rather than the binary, so they are repo-local by design",
            "the M1 and M2 floors and the main-only slack are measured by the child-frame and shim-footprint courts on the same binary; a failure there stops the rung",
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
