#!/usr/bin/env python3
"""The frozen court for passive listeners.

Frozen from `passive-listener-audit-0.0.1.md` §9 before the base changes, and
failing until a passive listener stops being able to cancel.

This is the rung that takes power away from the page, so the criteria that
matter most are the authority pair run through a real `target.act`: today a
page can refuse an agent's navigation by declaring the listener that refuses it
passive, and after this rung it cannot, while a page's *legitimate*
cancellation from a non-passive listener still works exactly as before.

It also pins what the ruling decided not to change: a late `preventDefault`
after the dispatch has returned still writes the flag — the host has already
read its answer, so the page only misleads itself — and no event type is
passive unless the page says so.

Strictly headless: no surface binary, no window, no AppKit, and it refuses to
run with the visible-court variable set. One hermetic loopback origin, both
allocators.

Groups: inert, mixed, nesting, window, composition, defaults, authority.
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
    ("inert",
     "(function(){var b=document.createElement('span');"
     "b.addEventListener('t',function(e){e.preventDefault();},{passive:true});"
     "var ev=new Event('t',{cancelable:true});var r=b.dispatchEvent(ev);"
     "return 'returned '+r+'|prevented '+ev.defaultPrevented;})()"),
    ("mixed_passive_first",
     "(function(){var b=document.createElement('span');"
     "b.addEventListener('m',function(e){e.preventDefault();},{passive:true});"
     "b.addEventListener('m',function(e){e.preventDefault();});"
     "var ev=new Event('m',{cancelable:true});var r=b.dispatchEvent(ev);"
     "return 'returned '+r+'|prevented '+ev.defaultPrevented;})()"),
    ("mixed_passive_second",
     "(function(){var b=document.createElement('span');"
     "b.addEventListener('o',function(e){e.preventDefault();});"
     "b.addEventListener('o',function(e){e.preventDefault();},{passive:true});"
     "var ev=new Event('o',{cancelable:true});var r=b.dispatchEvent(ev);"
     "return 'returned '+r+'|prevented '+ev.defaultPrevented;})()"),
    ("nested_event_cancellable",
     "(function(){var b=document.createElement('span');var inner=null;"
     "b.addEventListener('outer',function(){var ev2=new Event('inner',{cancelable:true});"
     "b.dispatchEvent(ev2);inner=ev2.defaultPrevented;},{passive:true});"
     "b.addEventListener('inner',function(e){e.preventDefault();});"
     "b.dispatchEvent(new Event('outer',{cancelable:true}));"
     "return 'inner prevented '+inner;})()"),
    ("nested_cannot_cancel_outer",
     "(function(){var b=document.createElement('span');"
     "b.addEventListener('outer2',function(e){var outer=e;"
     "b.addEventListener('inner2',function(){outer.preventDefault();});"
     "b.dispatchEvent(new Event('inner2'));},{passive:true});"
     "var ev=new Event('outer2',{cancelable:true});var r=b.dispatchEvent(ev);"
     "return 'returned '+r+'|outer prevented '+ev.defaultPrevented;})()"),
    ("window_closes_after",
     "(function(){var a=document.createElement('span');"
     "a.addEventListener('p1',function(e){e.preventDefault();},{passive:true});"
     "a.dispatchEvent(new Event('p1',{cancelable:true}));"
     "var b=document.createElement('span');"
     "b.addEventListener('p2',function(e){e.preventDefault();});"
     "var ev=new Event('p2',{cancelable:true});var r=b.dispatchEvent(ev);"
     "return 'returned '+r;})()"),
    ("window_closes_within",
     "(function(){var b=document.createElement('span');var seq=[];"
     "b.addEventListener('w',function(e){seq.push('passive');e.preventDefault();},{passive:true});"
     "b.addEventListener('w',function(e){seq.push('plain');e.preventDefault();});"
     "var ev=new Event('w',{cancelable:true});b.dispatchEvent(ev);"
     "return seq.join('>')+'|prevented '+ev.defaultPrevented;})()"),
    ("passive_capture",
     "(function(){var p=document.getElementById('box');var c=document.getElementById('go');"
     "p.addEventListener('pc',function(e){e.preventDefault();},{capture:true,passive:true});"
     "var ev=new Event('pc',{bubbles:true,cancelable:true});var r=c.dispatchEvent(ev);"
     "return 'returned '+r+'|prevented '+ev.defaultPrevented;})()"),
    ("passive_once",
     "(function(){var b=document.createElement('span');var n=0;"
     "b.addEventListener('po',function(e){n++;e.preventDefault();},{passive:true,once:true});"
     "var ev=new Event('po',{cancelable:true});b.dispatchEvent(ev);"
     "b.dispatchEvent(new Event('po',{cancelable:true}));"
     "return 'ran '+n+'|prevented '+ev.defaultPrevented;})()"),
    ("passive_handle_event",
     "(function(){var b=document.createElement('span');var n=0;"
     "b.addEventListener('ph',{handleEvent:function(e){n++;e.preventDefault();}},{passive:true});"
     "var ev=new Event('ph',{cancelable:true});var r=b.dispatchEvent(ev);"
     "return 'ran '+n+'|returned '+r;})()"),
    ("passive_window",
     "(function(){var n=0;"
     "window.addEventListener('pw',function(e){n++;e.preventDefault();},{passive:true});"
     "var ev=new Event('pw',{cancelable:true});var r=window.dispatchEvent(ev);"
     "return 'ran '+n+'|returned '+r;})()"),
    ("non_cancelable",
     "(function(){var b=document.createElement('span');"
     "b.addEventListener('pn',function(e){e.preventDefault();},{passive:true});"
     "var ev=new Event('pn');var r=b.dispatchEvent(ev);"
     "return 'returned '+r+'|prevented '+ev.defaultPrevented;})()"),
    # Ruled: the late write stands, and it changes nothing the host reads.
    ("late_prevent",
     "(function(){var b=document.createElement('span');var kept=null;"
     "b.addEventListener('lp',function(e){kept=e;},{passive:true});"
     "var ev=new Event('lp',{cancelable:true});var r=b.dispatchEvent(ev);"
     "kept.preventDefault();return 'dispatch returned '+r+'|after '+ev.defaultPrevented;})()"),
    # Ruled: no type is passive unless the page says so.
    ("no_default_passivity",
     "(function(){var out=[];var types=['wheel','touchstart','touchmove','scroll'];"
     "for(var i=0;i<types.length;i+=1){var b=document.createElement('span');"
     "b.addEventListener(types[i],function(e){e.preventDefault();});"
     "var ev=new Event(types[i],{cancelable:true});b.dispatchEvent(ev);"
     "out.push(ev.defaultPrevented);}return out.join(',');})()"),
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
            + slots + "</main><script>" + script + "</script></body></html>").encode()

    def link_page(options):
        return ("<!doctype html><html><body><main>"
                "<a id=\"go\" href=\"/next.html\">next</a></main><script>"
                "document.getElementById('go').addEventListener('click',"
                "function(e){e.preventDefault();}," + options + ");"
                "</script></body></html>").encode()
    PASSIVE_LINK = link_page("{passive:true}")
    PLAIN_LINK = link_page("{}")
    NEXT = b"<!doctype html><html><body><main><p id=a>second page</p></main></body></html>"

    class Handler(network.Handler):
        def do_GET(self):
            path, _, _query = self.path.partition("?")
            network.Handler.hits.append(path)
            pages = {"/passive.html": PASSIVE_LINK, "/plain.html": PLAIN_LINK,
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
            with tempfile.TemporaryDirectory(prefix="minicon-surf-passive-") as directory:
                host = JOBS.Supervised(args.binary, directory, origin, allocator)
                try:
                    profile = host.ok("profile.create", {"persistence": "ephemeral"})["profile"]
                    session = host.ok("session.open", {"profile": profile})["session"]

                    def look(target):
                        return host.ok("target.snapshot",
                                       {"target": target, "format": "semantic",
                                        "max_bytes": 131072, "max_nodes": 128})

                    target = host.ok("target.open",
                                     {"session": session,
                                      "url": origin + "/page.html"})["target"]
                    said = {}
                    for node in look(target)["nodes"]:
                        text = node.get("name") or ""
                        if node.get("role") == "text" and "=" in text:
                            key, _, value = text.partition("=")
                            said[key] = value

                    expect(tag + "P1: a passive listener cannot cancel its own event",
                           said.get("inert") == "returned true|prevented false",
                           {"said": said.get("inert")})
                    expect(tag + "P2: a non-passive listener still can, in either order",
                           said.get("mixed_passive_first") == "returned false|prevented true"
                           and said.get("mixed_passive_second") == "returned false|prevented true",
                           {"first": said.get("mixed_passive_first"),
                            "second": said.get("mixed_passive_second")})
                    expect(tag + "P3: a nested event is cancellable in its own right",
                           said.get("nested_event_cancellable") == "inner prevented true",
                           {"said": said.get("nested_event_cancellable")})
                    expect(tag + "P4: and cannot cancel the outer event from inside",
                           said.get("nested_cannot_cancel_outer")
                           == "returned true|outer prevented false",
                           {"said": said.get("nested_cannot_cancel_outer")})
                    expect(tag + "P5: the window closes when the dispatch ends",
                           said.get("window_closes_after") == "returned false",
                           {"said": said.get("window_closes_after")})
                    expect(tag + "P6: and between two listeners in the same dispatch",
                           said.get("window_closes_within") == "passive>plain|prevented true",
                           {"said": said.get("window_closes_within")})
                    expect(tag + "P7: passive composes with capture",
                           said.get("passive_capture") == "returned true|prevented false",
                           {"said": said.get("passive_capture")})
                    expect(tag + "P8: with once",
                           said.get("passive_once") == "ran 1|prevented false",
                           {"said": said.get("passive_once")})
                    expect(tag + "P9: with a handleEvent object",
                           said.get("passive_handle_event") == "ran 1|returned true",
                           {"said": said.get("passive_handle_event")})
                    expect(tag + "P10: and on the window",
                           said.get("passive_window") == "ran 1|returned true",
                           {"said": said.get("passive_window")})
                    expect(tag + "P11: a non-cancelable event is unchanged",
                           said.get("non_cancelable") == "returned true|prevented false",
                           {"said": said.get("non_cancelable")})
                    expect(tag + "P12: a late preventDefault still writes the flag, as ruled",
                           said.get("late_prevent") == "dispatch returned true|after true",
                           {"said": said.get("late_prevent")})
                    expect(tag + "P13: no event type is passive unless the page says so",
                           said.get("no_default_passivity") == "true,true,true,true",
                           {"said": said.get("no_default_passivity")})

                    # The authority pair, through the agent's own action.
                    passive_target = host.ok("target.open",
                                             {"session": session,
                                              "url": origin + "/passive.html"})["target"]
                    link = next((n for n in look(passive_target)["nodes"]
                                 if n.get("role") == "link"), None)
                    passive_act = host.call("target.act",
                                            {"target": passive_target,
                                             "reference": link["reference"],
                                             "action": {"kind": "click"}}) if link else {}
                    passive_url = (host.ok("target.inspect", {"target": passive_target})
                                   .get("url") or "")
                    expect(tag + "A1: a passive listener cannot refuse the agent's navigation",
                           passive_act.get("ok") and passive_url.endswith("/next.html"),
                           {"ok": passive_act.get("ok"), "url_tail": passive_url[-14:]})

                    plain_target = host.ok("target.open",
                                           {"session": session,
                                            "url": origin + "/plain.html"})["target"]
                    link2 = next((n for n in look(plain_target)["nodes"]
                                  if n.get("role") == "link"), None)
                    plain_act = host.call("target.act",
                                          {"target": plain_target,
                                           "reference": link2["reference"],
                                           "action": {"kind": "click"}}) if link2 else {}
                    plain_url = (host.ok("target.inspect", {"target": plain_target})
                                 .get("url") or "")
                    expect(tag + "A2: a plain listener still can, so exactly one route closed",
                           plain_act.get("ok") and plain_url.endswith("/plain.html"),
                           {"ok": plain_act.get("ok"), "url_tail": plain_url[-14:]})
                finally:
                    if host.killed:
                        killed_hosts.append({"allocator": allocator})
                    host.finish()
                    killed_hosts.extend({"allocator": allocator, **t} for t in host.timeouts)
    finally:
        server.shutdown()

    receipt = {
        "court": "native-dom passive listeners (control 0.0.2)",
        "host_sha256": hashlib.sha256(Path(args.binary).read_bytes()).hexdigest(),
        "ruled": {"late_prevent_default": "writes the flag; the host has already answered",
                  "type_based_default_passivity": False},
        "checks": checks,
        "checks_passed": sum(1 for c in checks if c["passed"]),
        "checks_total": len(checks),
        "passed": all(c["passed"] for c in checks) and not killed_hosts,
        "hosts_killed": killed_hosts,
        "limitations": [
            "design-frozen court: it fails until a passive listener stops being able to cancel",
            "signal and AbortController are a deferred rung and are deliberately not pinned here",
            "the M1 and M2 floors and the main-only slack are measured by the child-frame and shim-footprint courts on the same binary; a failure there stops the rung",
            "the CDP-dependent suites need the pinned client present under the ignored target/labs/d4; this court needs none of it",
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
