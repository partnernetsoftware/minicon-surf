#!/usr/bin/env python3
"""The frozen court for the capture phase.

Frozen from `capture-phase-audit-0.0.1.md` §8 before the base changes, and
failing until the walk has three phases.

What it holds the host to: capture, then the target, then bubbling, with the
window outermost and `eventPhase` reading 1, 2, 3; a **non-bubbling** event
that still builds the whole path and still runs the ancestors' capture
listeners without bubbling afterwards; `capture` as part of listener identity,
so a removal with the wrong flag removes nothing and the two flags are two
registrations; the stop flags honoured in each phase; `once` and `handleEvent`
composing with capture; and — the pair that matters — a page's capture
listener able to keep the target's own listener from seeing a synthesized
action, while the host's decision still comes from `defaultPrevented` through
the bridge and never from propagation.

The floors and the main-only slack are measured by the child-frame and
shim-footprint courts on the same binary, and a failure there stops the rung.

Strictly headless: no surface binary, no window, no AppKit, and it refuses to
run with the visible-court variable set. One hermetic loopback origin, both
allocators.

Groups: order, phases, non-bubbling, identity, stops, composition, authority.
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

PROBES = [
    ("order",
     "(function(){var p=document.getElementById('box');var c=document.getElementById('go');"
     "var seq=[];document.addEventListener('t',function(){seq.push('doc-cap');},true);"
     "p.addEventListener('t',function(){seq.push('box-cap');},true);"
     "c.addEventListener('t',function(){seq.push('target');});"
     "p.addEventListener('t',function(){seq.push('box-bub');});"
     "window.addEventListener('t',function(){seq.push('win-bub');});"
     "c.dispatchEvent(new Event('t',{bubbles:true}));return seq.join('>');})()"),
    ("window_outermost",
     "(function(){var c=document.getElementById('go');var seq=[];"
     "window.addEventListener('wc',function(){seq.push('win-cap');},true);"
     "document.addEventListener('wc',function(){seq.push('doc-cap');},true);"
     "c.addEventListener('wc',function(){seq.push('target');});"
     "c.dispatchEvent(new Event('wc',{bubbles:true}));return seq.join('>');})()"),
    ("phases",
     "(function(){var p=document.getElementById('box');var c=document.getElementById('go');"
     "var seen=[];p.addEventListener('ph',function(e){seen.push(e.eventPhase);},true);"
     "c.addEventListener('ph',function(e){seen.push(e.eventPhase);});"
     "p.addEventListener('ph',function(e){seen.push(e.eventPhase);});"
     "c.dispatchEvent(new Event('ph',{bubbles:true}));return seen.join(',');})()"),
    # A non-bubbling event: the capture side still runs, the bubble side does not.
    ("non_bubbling",
     "(function(){var p=document.getElementById('box');var c=document.getElementById('go');"
     "var seq=[];p.addEventListener('nb',function(){seq.push('box-cap');},true);"
     "c.addEventListener('nb',function(){seq.push('target');});"
     "p.addEventListener('nb',function(){seq.push('box-bub');});"
     "window.addEventListener('nb',function(){seq.push('win-cap');},true);"
     "c.dispatchEvent(new Event('nb'));return seq.join('>');})()"),
    # Identity: the wrong flag removes nothing, and two flags are two records.
    ("identity_wrong_flag",
     "(function(){var b=document.createElement('span');var n=0;var f=function(){n++;};"
     "b.addEventListener('id',f,true);b.removeEventListener('id',f,false);"
     "b.dispatchEvent(new Event('id'));return 'ran '+n;})()"),
    ("identity_right_flag",
     "(function(){var b=document.createElement('span');var n=0;var f=function(){n++;};"
     "b.addEventListener('id2',f,true);b.removeEventListener('id2',f,true);"
     "b.dispatchEvent(new Event('id2'));return 'ran '+n;})()"),
    ("two_registrations",
     "(function(){var p=document.getElementById('box');var c=document.getElementById('go');"
     "var n=0;var f=function(){n++;};p.addEventListener('tr',f,true);"
     "p.addEventListener('tr',f,false);c.dispatchEvent(new Event('tr',{bubbles:true}));"
     "return 'ran '+n;})()"),
    ("target_runs_both",
     "(function(){var c=document.getElementById('go');var seq=[];"
     "c.addEventListener('tb',function(){seq.push('cap-reg');},true);"
     "c.addEventListener('tb',function(){seq.push('bub-reg');});"
     "c.dispatchEvent(new Event('tb',{bubbles:true}));return seq.join('>');})()"),
    # Stop flags, in each phase.
    ("stop_in_capture",
     "(function(){var p=document.getElementById('box');var c=document.getElementById('go');"
     "var seq=[];p.addEventListener('sc',function(e){seq.push('box-cap');e.stopPropagation();},true);"
     "c.addEventListener('sc',function(){seq.push('target');});"
     "p.addEventListener('sc',function(){seq.push('box-bub');});"
     "c.dispatchEvent(new Event('sc',{bubbles:true}));return seq.join('>');})()"),
    ("stop_immediate_in_capture",
     "(function(){var p=document.getElementById('box');var c=document.getElementById('go');"
     "var seq=[];p.addEventListener('si',function(e){seq.push('cap1');e.stopImmediatePropagation();},true);"
     "p.addEventListener('si',function(){seq.push('cap2');},true);"
     "c.addEventListener('si',function(){seq.push('target');});"
     "c.dispatchEvent(new Event('si',{bubbles:true}));return seq.join('>');})()"),
    ("stop_in_bubble",
     "(function(){var p=document.getElementById('box');var c=document.getElementById('go');"
     "var seq=[];p.addEventListener('sb',function(e){seq.push('box-cap');},true);"
     "c.addEventListener('sb',function(e){seq.push('target');e.stopPropagation();});"
     "p.addEventListener('sb',function(){seq.push('box-bub');});"
     "c.dispatchEvent(new Event('sb',{bubbles:true}));return seq.join('>');})()"),
    # Composition with the rung already built.
    ("once_capture",
     "(function(){var p=document.getElementById('box');var c=document.getElementById('go');"
     "var n=0;p.addEventListener('oc',function(){n++;},{capture:true,once:true});"
     "c.dispatchEvent(new Event('oc',{bubbles:true}));"
     "c.dispatchEvent(new Event('oc',{bubbles:true}));return 'ran '+n;})()"),
    ("once_capture_keeps_the_bubble_twin",
     "(function(){var p=document.getElementById('box');var c=document.getElementById('go');"
     "var cap=0,bub=0;var f=function(){cap++;};var g2=function(){bub++;};"
     "p.addEventListener('ok',f,{capture:true,once:true});p.addEventListener('ok',g2);"
     "c.dispatchEvent(new Event('ok',{bubbles:true}));"
     "c.dispatchEvent(new Event('ok',{bubbles:true}));"
     "return 'capture '+cap+'|bubble '+bub;})()"),
    ("handle_event_capture",
     "(function(){var p=document.getElementById('box');var c=document.getElementById('go');"
     "var n=0;var o={handleEvent:function(){n++;}};p.addEventListener('hc',o,true);"
     "c.dispatchEvent(new Event('hc',{bubbles:true}));p.removeEventListener('hc',o,true);"
     "c.dispatchEvent(new Event('hc',{bubbles:true}));return 'ran '+n;})()"),
    # The page half of the authority pair, armed here and read after the act.
    ("authority_armed",
     "(function(){document.addEventListener('click',function(e){e.stopPropagation();"
     "document.getElementById('doc').textContent='doc ran';},true);"
     "document.getElementById('go').addEventListener('click',function(){"
     "document.getElementById('tgt').textContent='target ran';});return 'armed';})()"),
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
    PAGE = ("<!doctype html><html><body><main><div id=\"box\">"
            "<button id=\"go\" type=\"button\">go</button></div>"
            "<p id=\"doc\">doc quiet</p><p id=\"tgt\">target quiet</p>"
            + slots + "</main><script>" + script + "</script></body></html>").encode()
    # The host half of the pair: a link, and a page that stops propagation or
    # prevents the default from a capture listener at the document.
    def link_page(call):
        return ("<!doctype html><html><body><main><div id=\"box\">"
                "<a id=\"go\" href=\"/next.html\">next</a></div></main><script>"
                "document.addEventListener('click',function(e){e." + call + "();},true);"
                "</script></body></html>").encode()
    STOPPING = link_page("stopPropagation")
    PREVENTING = link_page("preventDefault")
    NEXT = b"<!doctype html><html><body><main><p id=a>second page</p></main></body></html>"

    class Handler(network.Handler):
        def do_GET(self):
            path, _, _query = self.path.partition("?")
            network.Handler.hits.append(path)
            pages = {"/stopping.html": STOPPING, "/preventing.html": PREVENTING,
                     "/next.html": NEXT}
            return self.reply(200, pages.get(path, PAGE))

    server = network.Server(("127.0.0.1", 0), Handler)
    origin = f"http://127.0.0.1:{server.server_address[1]}"
    threading.Thread(target=server.serve_forever, daemon=True).start()
    checks = []
    killed_hosts = []

    def expect(name, condition, detail=None):
        checks.append({"check": name, "passed": bool(condition),
                       **({"detail": detail} if detail is not None else {})})

    try:
        for allocator in ("system", "arena"):
            tag = f"[{allocator}] "
            with tempfile.TemporaryDirectory(prefix="minicon-surf-capture-") as directory:
                host = JOBS.Supervised(args.binary, directory, origin, allocator)
                try:
                    profile = host.ok("profile.create", {"persistence": "ephemeral"})["profile"]
                    session = host.ok("session.open", {"profile": profile})["session"]

                    def open_at(path):
                        return host.ok("target.open",
                                       {"session": session,
                                        "url": origin + path})["target"]

                    def look(target):
                        return host.ok("target.snapshot",
                                       {"target": target, "format": "semantic",
                                        "max_bytes": 131072, "max_nodes": 128})

                    target = open_at("/page.html")
                    snapshot = look(target)
                    said = {}
                    for node in snapshot["nodes"]:
                        text = node.get("name") or ""
                        if node.get("role") == "text" and "=" in text:
                            key, _, value = text.partition("=")
                            said[key] = value

                    expect(tag + "C1: capture runs down, then the target, then bubbling",
                           said.get("order") == "doc-cap>box-cap>target>box-bub>win-bub",
                           {"said": said.get("order")})
                    expect(tag + "C2: the window is the outermost capture target",
                           said.get("window_outermost") == "win-cap>doc-cap>target",
                           {"said": said.get("window_outermost")})
                    expect(tag + "C3: eventPhase reads 1, 2, 3",
                           said.get("phases") == "1,2,3", {"said": said.get("phases")})
                    expect(tag + "C4: a non-bubbling event still captures down and does not bubble",
                           said.get("non_bubbling") == "win-cap>box-cap>target",
                           {"said": said.get("non_bubbling")})
                    expect(tag + "C5: a removal with the wrong flag removes nothing",
                           said.get("identity_wrong_flag") == "ran 1",
                           {"said": said.get("identity_wrong_flag")})
                    expect(tag + "C6: and one with the right flag still removes",
                           said.get("identity_right_flag") == "ran 0",
                           {"said": said.get("identity_right_flag")})
                    expect(tag + "C7: the two flags are two registrations",
                           said.get("two_registrations") == "ran 2",
                           {"said": said.get("two_registrations")})
                    expect(tag + "C8: at the target both kinds run, in registration order",
                           said.get("target_runs_both") == "cap-reg>bub-reg",
                           {"said": said.get("target_runs_both")})
                    expect(tag + "C9: stopPropagation in capture ends the dispatch there",
                           said.get("stop_in_capture") == "box-cap",
                           {"said": said.get("stop_in_capture")})
                    expect(tag + "C10: stopImmediatePropagation stops the rest of that hop too",
                           said.get("stop_immediate_in_capture") == "cap1",
                           {"said": said.get("stop_immediate_in_capture")})
                    expect(tag + "C11: stopPropagation at the target still leaves capture done",
                           said.get("stop_in_bubble") == "box-cap>target",
                           {"said": said.get("stop_in_bubble")})
                    expect(tag + "C12: once and capture compose",
                           said.get("once_capture") == "ran 1",
                           {"said": said.get("once_capture")})
                    expect(tag + "C13: a spent once capture record leaves its bubble twin alone",
                           said.get("once_capture_keeps_the_bubble_twin") == "capture 1|bubble 2",
                           {"said": said.get("once_capture_keeps_the_bubble_twin")})
                    expect(tag + "C14: handleEvent and capture compose, and remove by the object",
                           said.get("handle_event_capture") == "ran 1",
                           {"said": said.get("handle_event_capture")})

                    # A1: the page's capture listener suppresses its own target
                    # listener on the host's synthesized click.
                    expect(tag + "A1a: the page could arm both listeners",
                           said.get("authority_armed") == "armed",
                           {"said": said.get("authority_armed")})
                    button = next((n for n in snapshot["nodes"]
                                   if n.get("role") == "button"), None)
                    if button:
                        host.call("target.act",
                                  {"target": target, "reference": button["reference"],
                                   "action": {"kind": "click"}})
                    texts = [n.get("name") for n in look(target)["nodes"]
                             if n.get("role") == "text"]
                    expect(tag + "A1b: a capture listener stops the page's own target listener",
                           "doc ran" in texts and "target quiet" in texts,
                           {"doc": "doc ran" in texts, "target_quiet": "target quiet" in texts})

                    # A2: the host's decision is read from defaultPrevented and
                    # never from propagation.
                    stopping = open_at("/stopping.html")
                    link = next((n for n in look(stopping)["nodes"]
                                 if n.get("role") == "link"), None)
                    stopped_act = host.call("target.act",
                                            {"target": stopping,
                                             "reference": link["reference"],
                                             "action": {"kind": "click"}}) if link else {}
                    stopped_url = host.ok("target.inspect", {"target": stopping}).get("url") or ""
                    expect(tag + "A2a: stopping propagation does not stop the host's navigation",
                           stopped_act.get("ok") and stopped_url.endswith("/next.html"),
                           {"ok": stopped_act.get("ok"), "url_tail": stopped_url[-12:]})

                    preventing = open_at("/preventing.html")
                    link2 = next((n for n in look(preventing)["nodes"]
                                  if n.get("role") == "link"), None)
                    prevented_act = host.call("target.act",
                                              {"target": preventing,
                                               "reference": link2["reference"],
                                               "action": {"kind": "click"}}) if link2 else {}
                    prevented_url = (host.ok("target.inspect", {"target": preventing})
                                     .get("url") or "")
                    expect(tag + "A2b: preventing the default does, from the capture phase too",
                           prevented_act.get("ok")
                           and prevented_url.endswith("/preventing.html"),
                           {"ok": prevented_act.get("ok"), "url_tail": prevented_url[-16:]})
                finally:
                    if host.killed:
                        killed_hosts.append({"allocator": allocator})
                    host.finish()
                    killed_hosts.extend({"allocator": allocator, **t} for t in host.timeouts)
    finally:
        server.shutdown()

    receipt = {
        "court": "native-dom capture phase (control 0.0.2)",
        "host_sha256": hashlib.sha256(Path(args.binary).read_bytes()).hexdigest(),
        "expected": {"order": "doc-cap>box-cap>target>box-bub>win-bub",
                     "phases": "1,2,3",
                     "non_bubbling": "win-cap>box-cap>target"},
        "checks": checks,
        "checks_passed": sum(1 for c in checks if c["passed"]),
        "checks_total": len(checks),
        "passed": all(c["passed"] for c in checks) and not killed_hosts,
        "hosts_killed": killed_hosts,
        "limitations": [
            "design-frozen court: it fails until the walk has three phases",
            "passive and signal are deferred rungs and are deliberately not pinned here",
            "the M1 and M2 floors and the main-only slack are measured by the child-frame and shim-footprint courts on the same binary; a failure there stops the rung",
            "composedPath and shadow retargeting are not modelled by this host and are not tested",
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
