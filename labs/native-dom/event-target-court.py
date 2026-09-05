#!/usr/bin/env python3
"""The frozen court for the `EventTarget` constructor.

Frozen from `event-target-audit-0.0.1.md` §9 before the extension changes, and
failing until the class exists.

What it holds the host to: the name, the chain between `Node` and `Object`,
`new EventTarget()` as a working bus, subclassing, and the borrowed
plain-object bus that already worked before the slice — while the three
authority properties measured in the audit still hold, the window keeps its
ruled divergence, and dispatch on a real node behaves exactly as it did.

The slice is main-only by ruling, so two criteria read the shipped sources: the
class is declared in the extension and nowhere in the base, and the base's
one-shot handle is not asked for anything new.

The floors and the main-only slack are not measured here — the child-frame and
shim-footprint courts measure them on the same binary, and a failure there
stops the slice.

Strictly headless: no surface binary, no window, no AppKit, and it refuses to
run with the visible-court variable set. One hermetic loopback origin, both
allocators.

Groups: sources, name, chain, bus, subclass, borrowed, containment, window,
dispatch.
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
CHAIN = "Element>Element>Node>EventTarget>Object"


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

PROBES = [
    ("name", "typeof EventTarget"),
    ("node_instance",
     "(function(){try{return String(document.body instanceof EventTarget);}"
     "catch(e){return 'threw:'+e.name;}})()"),
    ("chain",
     "(function(){var p=[],o=document.body;while(o){var c=o.constructor;"
     "p.push(c&&c.name?c.name:'?');o=Object.getPrototypeOf(o);}return p.join('>');})()"),
    # A standalone bus: delivered, then removed and not delivered.
    ("bus",
     "(function(){try{var t=new EventTarget();var n=0;var f=function(){n++;};"
     "t.addEventListener('ping',f);t.dispatchEvent(new Event('ping'));"
     "t.removeEventListener('ping',f);t.dispatchEvent(new Event('ping'));"
     "return 'delivered '+n;}catch(e){return 'threw:'+e.name;}})()"),
    ("subclass",
     "(function(){try{var Bus=function(){};"
     "Bus.prototype=Object.create(EventTarget.prototype);"
     "Bus.prototype.constructor=Bus;var b=new Bus();var n=0;"
     "b.addEventListener('x',function(){n++;});b.dispatchEvent(new Event('x'));"
     "return String(b instanceof EventTarget)+'|'+n;}catch(e){return 'threw:'+e.name;}})()"),
    # The bus a page could already build by borrowing, which must keep working.
    ("borrowed",
     "(function(){try{var o={};o.addEventListener=document.body.addEventListener;"
     "var n=0;o.addEventListener('z',function(){n++;});"
     "var d=document.body.dispatchEvent;d.call(o,new Event('z'));"
     "return 'delivered '+n;}catch(e){return 'threw:'+e.name;}})()"),
    # C1: a forged parent must not reach the tree.
    ("forged_parent",
     "(function(){try{var real=document.getElementById('go');var seen=0;"
     "real.addEventListener('ping',function(){seen++;});"
     "var fake={parentNode:real};fake.addEventListener=document.body.addEventListener;"
     "fake.dispatchEvent=document.body.dispatchEvent;"
     "fake.dispatchEvent(new Event('ping',{bubbles:true}));"
     "return 'real listeners ran '+seen;}catch(e){return 'threw:'+e.name;}})()"),
    # C2: the reserved focus type is inert from a page.
    ("reserved_focus",
     "(function(){try{var b=document.getElementById('go');"
     "var before=document.activeElement?document.activeElement.localName:'none';"
     "b.dispatchEvent(new Event('__mcsFocus'));"
     "var after=document.activeElement?document.activeElement.localName:'none';"
     "return before+'->'+after;}catch(e){return 'threw:'+e.name;}})()"),
    # C3 is measured through a real action, below, not from the page.
    ("spy_installed",
     "(function(){try{EventTarget.prototype.dispatchEvent=function(){"
     "document.getElementById('spy').textContent='spy called';return true;};"
     "return 'installed';}catch(e){return 'threw:'+e.name;}})()"),
    # The window keeps its ruled divergence and its own working methods.
    ("window_divergence",
     "(function(){try{var i=String(window instanceof EventTarget);var n=0;"
     "window.addEventListener('w',function(){n++;});"
     "window.dispatchEvent(new Event('w'));return i+'|'+n;}"
     "catch(e){return 'threw:'+e.name;}})()"),
    # Node dispatch semantics must not shift.
    ("node_dispatch",
     "(function(){try{var b=document.createElement('span');var n=0;"
     "b.addEventListener('c',function(e){n++;e.preventDefault();});"
     "var ev=new Event('c',{cancelable:true});var r=b.dispatchEvent(ev);"
     "return 'ran '+n+'|returned '+r+'|prevented '+ev.defaultPrevented;}"
     "catch(e){return 'threw:'+e.name;}})()"),
]


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
    # The click listener is registered before the spy is installed, so the
    # host's action has something real to deliver to.
    PAGE = ("<!doctype html><html><body><main><button id=go type=button>go</button>"
            "<p id=\"hit\">not clicked</p><p id=\"spy\">spy not called</p>"
            + slots + "</main><script>"
            "document.getElementById('go').addEventListener('click',function(){"
            "document.getElementById('hit').textContent='host click delivered';});"
            + script + "</script></body></html>").encode()

    class Handler(network.Handler):
        def do_GET(self):
            path, _, _query = self.path.partition("?")
            network.Handler.hits.append(path)
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
    declared = len(re.findall(r"class\s+EventTarget\s*\{", main_source))
    expect("S1: the extension declares the class once and the base never names it",
           declared == 1 and "EventTarget" not in base,
           {"declared_in_main": declared, "named_in_base": "EventTarget" in base})
    handle = base[base.index("return take({"):] if "return take({" in base else ""
    handle = handle[:handle.index("});")] if "});" in handle else handle
    expect("S2: the one-shot handle is not asked for anything new",
           "EventTarget" not in handle and "addListener" in handle
           and "dispatchOn" in handle,
           {"handle_names_eventtarget": "EventTarget" in handle})

    try:
        for allocator in ("system", "arena"):
            tag = f"[{allocator}] "
            with tempfile.TemporaryDirectory(prefix="minicon-surf-eventtarget-") as directory:
                host = JOBS.Supervised(args.binary, directory, origin, allocator)
                try:
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

                    expect(tag + "E1: the name exists",
                           said.get("name") == "function", {"said": said.get("name")})
                    expect(tag + "E2: a node is an EventTarget, and the chain says where",
                           said.get("node_instance") == "true"
                           and said.get("chain") == CHAIN,
                           {"instance": said.get("node_instance"),
                            "chain": said.get("chain")})
                    expect(tag + "E3: new EventTarget() is a bus that delivers and stops",
                           said.get("bus") == "delivered 1", {"said": said.get("bus")})
                    expect(tag + "E4: a subclass is an EventTarget and receives",
                           said.get("subclass") == "true|1", {"said": said.get("subclass")})
                    expect(tag + "E5: the borrowed plain-object bus still works",
                           said.get("borrowed") == "delivered 1",
                           {"said": said.get("borrowed")})

                    # C1 and C2: containment, from the page's own side.
                    expect(tag + "C1: a forged parent does not reach the real element",
                           said.get("forged_parent") == "real listeners ran 0",
                           {"said": said.get("forged_parent")})
                    expect(tag + "C2: the reserved focus type moves nothing from a page",
                           said.get("reserved_focus") in ("body->body", "none->none"),
                           {"said": said.get("reserved_focus")})

                    # C3: the page has replaced EventTarget.prototype.dispatchEvent;
                    # the host's own action must be untouched by that.
                    expect(tag + "C3a: the page could install its spy",
                           said.get("spy_installed") == "installed",
                           {"said": said.get("spy_installed")})
                    button = next((n for n in snapshot["nodes"]
                                   if n.get("role") == "button"), None)
                    acted = host.call("target.act",
                                      {"target": target,
                                       "reference": button["reference"],
                                       "action": {"kind": "click"}}) if button else {}
                    after = host.ok("target.snapshot",
                                    {"target": target, "format": "semantic",
                                     "max_bytes": 131072, "max_nodes": 128})
                    texts = [n.get("name") for n in after["nodes"]
                             if n.get("role") == "text"]
                    expect(tag + "C3b: a page's spy on the prototype does not take the host's dispatch",
                           acted.get("ok")
                           and any("host click delivered" == t for t in texts)
                           and any("spy not called" == t for t in texts),
                           {"acted": acted.get("ok"),
                            "delivered": any("host click delivered" == t for t in texts),
                            "spy_quiet": any("spy not called" == t for t in texts)})

                    # The ruled divergence, and the semantics that must not shift.
                    expect(tag + "E6: window stays outside the chain and keeps its own three",
                           said.get("window_divergence") == "false|1",
                           {"said": said.get("window_divergence")})
                    expect(tag + "E7: dispatch on a node behaves exactly as it did",
                           said.get("node_dispatch") == "ran 1|returned false|prevented true",
                           {"said": said.get("node_dispatch")})
                finally:
                    if host.killed:
                        killed_hosts.append({"allocator": allocator})
                    host.finish()
                    killed_hosts.extend({"allocator": allocator, **t} for t in host.timeouts)
    finally:
        server.shutdown()

    receipt = {
        "court": "native-dom EventTarget constructor (control 0.0.2)",
        "host_sha256": hashlib.sha256(Path(args.binary).read_bytes()).hexdigest(),
        "expected": {"chain": CHAIN, "window_instanceof": False},
        "checks": checks,
        "checks_passed": sum(1 for c in checks if c["passed"]),
        "checks_total": len(checks),
        "passed": all(c["passed"] for c in checks) and not killed_hosts,
        "hosts_killed": killed_hosts,
        "limitations": [
            "design-frozen court: it fails until the main extension declares the class",
            "two criteria read the shipped sources beside this court rather than the binary, so they are repo-local by design",
            "the M1 and M2 floors and the main-only slack are measured by the child-frame and shim-footprint courts on the same binary; a failure there stops the slice",
            "listener options, handleEvent objects and AbortController are a separate candidate and are deliberately not pinned here",
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
