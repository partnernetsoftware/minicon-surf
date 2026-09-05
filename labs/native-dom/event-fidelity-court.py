#!/usr/bin/env python3
"""Bounded `Event` fidelity: the dispatch an agent's action and a page's own
handler both go through.

Frozen before the code, and it fails on the build it was written on. Every
page writes what it learned into its own elements and the court reads them
back through `target.snapshot`, except the authority group, which measures
what the host itself decided.

Strictly headless: no surface, no window, no AppKit, one hermetic loopback
origin, both allocators, supervised hosts with the wall-clock kill.
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

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "protocol"))
import check_contract  # noqa: E402,F401

VISIBLE_ENV = "MINICON_SURF_ALLOW_VISIBLE_COURT"


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

# Each probe writes into its own slot, because one long string is truncated by
# the snapshot's byte bound and a truncated answer is not an answer.
SLOTS = 28


def page(script, extra=""):
    slots = "".join(f'<p id="r{i}"></p>' for i in range(SLOTS))
    return ("<!doctype html><html><body><main>"
            '<div id="outer"><div id="inner"><p id="t">t</p></div></div>'
            + extra + slots + "</main><script>"
            "var t=document.getElementById('t');"
            "var inner=document.getElementById('inner');"
            "var outer=document.getElementById('outer');"
            "var __n=0;"
            "function probe(name, fn){var v;try{v=fn();}catch(e){v='threw:'+e.name;}"
            "var slot=document.getElementById('r'+(__n++));if(slot)slot.textContent=name+'='+v;}"
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

    FIDELITY = r"""
probe('phase', function(){
  var e=new Event('a',{bubbles:true}); var seen=[String(e.eventPhase)];
  t.addEventListener('a', function(ev){ seen.push(String(ev.eventPhase)); });
  inner.addEventListener('a', function(ev){ seen.push(String(ev.eventPhase)); });
  t.dispatchEvent(e); seen.push(String(e.eventPhase)); return seen.join(',');
});
probe('stop_vs_immediate', function(){
  var hit=[]; var e=new Event('b',{bubbles:true});
  t.addEventListener('b', function(ev){ hit.push('one'); ev.stopPropagation(); });
  t.addEventListener('b', function(ev){ hit.push('two'); });
  inner.addEventListener('b', function(ev){ hit.push('up'); });
  t.dispatchEvent(e);
  var hit2=[]; var e2=new Event('c',{bubbles:true});
  t.addEventListener('c', function(ev){ hit2.push('one'); ev.stopImmediatePropagation(); });
  t.addEventListener('c', function(ev){ hit2.push('two'); });
  inner.addEventListener('c', function(ev){ hit2.push('up'); });
  t.dispatchEvent(e2);
  return hit.join('')+'/'+hit2.join('');
});
probe('add_remove_during', function(){
  var hit=[]; var late=function(){ hit.push('late'); };
  var second=function(){ hit.push('second'); };
  t.addEventListener('d', function(){ hit.push('first'); t.addEventListener('d', late); t.removeEventListener('d', second); });
  t.addEventListener('d', second);
  t.dispatchEvent(new Event('d',{})); return hit.join(',');
});
probe('readd_during', function(){
  var hit=[]; var second=function(){ hit.push('second'); };
  t.addEventListener('r1', function(){ hit.push('first');
    t.removeEventListener('r1', second); t.addEventListener('r1', second); });
  t.addEventListener('r1', second);
  t.dispatchEvent(new Event('r1',{}));
  var inThisOne = hit.join(',');
  hit = [];
  t.dispatchEvent(new Event('r1',{}));
  return inThisOne + '/' + hit.join(',');
});
probe('preset_stop', function(){
  var hit=[]; t.addEventListener('r2', function(){ hit.push('ran'); });
  var e=new Event('r2',{}); e.stopPropagation();
  t.dispatchEvent(e);
  var first = hit.join(',') || 'none';
  hit = [];
  t.dispatchEvent(e);
  return first + '/' + (hit.join(',') || 'none');
});
probe('non_event', function(){
  var ran='no'; t.addEventListener('r3', function(){ ran='yes'; });
  var thrown='none';
  try { t.dispatchEvent({ type: 'r3' }); thrown='returned'; }
  catch (e) { thrown = e.name; }
  return thrown + ',' + ran;
});
probe('type_is_a_string', function(){
  var hit=[];
  t.addEventListener(1, function(){ hit.push('numeric'); });
  t.dispatchEvent(new Event('1',{}));
  t.addEventListener('2', function(){ hit.push('string'); });
  t.dispatchEvent(new Event(2,{}));
  return hit.join(',') || 'none';
});
probe('nested_redispatch', function(){
  var hit=[]; var e=new Event('f',{bubbles:true}); var inner_err='none';
  t.addEventListener('f', function(ev){ hit.push('one');
    try { t.dispatchEvent(ev); } catch(err){ inner_err=err.name; } });
  t.addEventListener('f', function(ev){ hit.push('two'); });
  inner.addEventListener('f', function(ev){ hit.push('up'); });
  t.dispatchEvent(e);
  var again=0; t.addEventListener('g', function(){ again++; });
  var e2=new Event('g',{}); t.dispatchEvent(e2); t.dispatchEvent(e2);
  return inner_err+'/'+hit.join('')+'/'+String(e.target===t)+'/again='+again;
});
probe('read_only', function(){
  var e=new Event('h',{bubbles:true,cancelable:true}); var bad=[];
  var tries={type:'z',bubbles:false,cancelable:false,target:'forged',
             currentTarget:'forged',defaultPrevented:true,eventPhase:9,dispatching:true};
  for (var k in tries){ var was=e[k]; try{ e[k]=tries[k]; }catch(err){}
    if (e[k]!==was && !(was===undefined && e[k]===undefined)) bad.push(k); }
  return bad.length ? 'writable:'+bad.join(',') : 'all-read-only';
});
probe('cancel_and_return', function(){
  var e=new Event('i',{}); var r1;
  t.addEventListener('i', function(ev){ ev.preventDefault(); });
  r1=t.dispatchEvent(e);
  var e2=new Event('j',{cancelable:true}); var r2;
  t.addEventListener('j', function(ev){ ev.preventDefault(); });
  r2=t.dispatchEvent(e2);
  return String(e.defaultPrevented)+','+String(r1)+'/'+String(e2.defaultPrevented)+','+String(r2);
});
probe('cleanup_after_throw', function(){
  var e=new Event('k',{bubbles:true}); var reached=[];
  t.addEventListener('k', function(){ reached.push('one'); throw new Error('boom'); });
  t.addEventListener('k', function(){ reached.push('two'); });
  inner.addEventListener('k', function(){ reached.push('up'); });
  var ret=t.dispatchEvent(e);
  return reached.join('')+'/'+String(e.currentTarget)+'/'+String(e.eventPhase)
    +'/'+String(e.target===t)+'/'+String(e.dispatching)+'/'+String(ret);
});
probe('trusted_and_clock', function(){
  var a=new Event('l',{}); var b=new Event('l',{});
  return String(a.isTrusted)+'/'+(typeof a.timeStamp)+'/'+String(b.timeStamp>=a.timeStamp);
});
probe('composed', function(){
  return String((new Event('m',{})).composed)+','+String((new Event('m',{composed:true})).composed);
});
probe('custom_regressions', function(){
  var e=new CustomEvent('n',{bubbles:true,detail:{which:'alpha'}});
  var seen='none';
  inner.addEventListener('n', function(ev){ seen=ev.detail.which+','+String(ev===e)
    +','+String(ev.target===t)+','+String(ev.currentTarget===inner); });
  t.dispatchEvent(e);
  var nul=new CustomEvent('o',null);
  return seen+'/'+String(nul.detail)+'/'+String(nul.bubbles);
});
probe('window_hop', function(){
  var got=[]; var e=new Event('p',{bubbles:true});
  window.addEventListener('p', function(ev){ got.push(String(ev.currentTarget===window)); });
  t.dispatchEvent(e);
  var loose=document.createElement('div');
  var e2=new Event('q',{bubbles:true});
  window.addEventListener('q', function(){ got.push('detached-reached-window'); });
  loose.dispatchEvent(e2);
  return got.join(',') || 'none';
});
"""

    class Handler(network.Handler):
        def do_GET(self):
            path, _, _query = self.path.partition("?")
            network.Handler.hits.append(path)
            if path == "/fidelity.html":
                return self.reply(200, page(FIDELITY))
            # The authority group: one line apart, and the host decides.
            if path.startswith("/link-"):
                how = path[len("/link-"):-len(".html")]
                line = {"forge": "ev.defaultPrevented = true;",
                        "prevent": "ev.preventDefault();",
                        "plain": ""}[how]
                return self.reply(200, (
                    "<!doctype html><html><body><main><p id=\"m\">start</p>"
                    "<a id=\"b\" href=\"/next.html\">go</a></main><script>"
                    "document.getElementById('b').addEventListener('click', function(ev){"
                    + line +
                    "document.getElementById('m').textContent='handler ran';});"
                    "</script></body></html>").encode())
            if path == "/trusted-click.html":
                return self.reply(200, (
                    "<!doctype html><html><body><main><p id=\"m\">start</p>"
                    "<button id=\"b\">press</button></main><script>"
                    "document.getElementById('b').addEventListener('click', function(ev){"
                    "document.getElementById('m').textContent='isTrusted='+String(ev.isTrusted)"
                    "+',phase='+String(ev.eventPhase)+',cancelable='+String(ev.cancelable);});"
                    "</script></body></html>").encode())
            if path == "/next.html":
                return self.reply(200, b"<!doctype html><html><body><p>next</p></body></html>")
            return self.reply(404, b"gone")

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
            directory = tempfile.TemporaryDirectory(prefix="minicon-surf-event-")
            host = JOBS.Supervised(args.binary, directory.name, origin, allocator)
            try:
                def snapshot(target, nodes=128):
                    answer = host.call("target.snapshot",
                                       {"target": target, "format": "semantic",
                                        "max_bytes": 65536, "max_nodes": nodes})
                    return answer["result"] if answer.get("ok") else None

                def results(target):
                    shot = snapshot(target)
                    if not shot:
                        return {}
                    out = {}
                    for node in shot["nodes"]:
                        text = node.get("name") or ""
                        if node.get("role") == "text" and "=" in text:
                            key, _, value = text.partition("=")
                            out[key] = value
                    return out

                profile = host.ok("profile.create", {"persistence": "ephemeral"})["profile"]
                session = host.ok("session.open", {"profile": profile})["session"]
                opened = host.call("target.open",
                                   {"session": session, "url": f"{origin}/fidelity.html"},
                                   deadline_ms=8000)
                said = results(opened["result"]["target"]) if opened.get("ok") else {}

                expect(tag + "the phase is NONE, AT_TARGET, BUBBLING, then NONE again",
                       said.get("phase") == "0,2,3,0", {"said": said.get("phase")})
                expect(tag + "stopPropagation spares the rest of the target's listeners; "
                       "stopImmediatePropagation does not",
                       said.get("stop_vs_immediate") == "onetwo/one",
                       {"said": said.get("stop_vs_immediate")})
                expect(tag + "a listener added during a dispatch does not run in it, "
                       "and one removed during it does not run",
                       said.get("add_remove_during") == "first",
                       {"said": said.get("add_remove_during")})
                expect(tag + "a re-entrant dispatch is refused without corrupting the one in flight",
                       said.get("nested_redispatch") == "InvalidStateError/onetwoup/true/again=2",
                       {"said": said.get("nested_redispatch")})
                expect(tag + "a listener removed and re-added during a dispatch waits for the next one",
                       said.get("readd_during") == "first/first,second",
                       {"said": said.get("readd_during")})
                expect(tag + "a stop flag set before a dispatch holds for it, and is cleared by its end",
                       said.get("preset_stop") == "none/ran", {"said": said.get("preset_stop")})
                expect(tag + "dispatching something that is not an event is refused, not reported",
                       said.get("non_event") == "TypeError,no", {"said": said.get("non_event")})
                expect(tag + "a listener type is a string, whatever the page passed",
                       said.get("type_is_a_string") == "numeric,string",
                       {"said": said.get("type_is_a_string")})
                expect(tag + "nothing about an event is page-writable",
                       said.get("read_only") == "all-read-only", {"said": said.get("read_only")})
                expect(tag + "preventDefault is ignored unless the event is cancelable, "
                       "and the return value follows",
                       said.get("cancel_and_return") == "false,true/true,false",
                       {"said": said.get("cancel_and_return")})
                expect(tag + "a listener that throws leaves the dispatch clean and does not stop it",
                       said.get("cleanup_after_throw") == "onetwoup/null/0/true/false/true",
                       {"said": said.get("cleanup_after_throw")})
                expect(tag + "isTrusted is false and timeStamp is a clock that does not go back",
                       said.get("trusted_and_clock") == "false/number/true",
                       {"said": said.get("trusted_and_clock")})
                expect(tag + "composed is false by default and follows the dictionary",
                       said.get("composed") == "false,true", {"said": said.get("composed")})
                expect(tag + "CustomEvent keeps its detail, its identity and its null dictionary",
                       said.get("custom_regressions") == "alpha,true,true,true/null/false",
                       {"said": said.get("custom_regressions")})
                expect(tag + "the window is the last hop, and only for a path that reached the document",
                       said.get("window_hop") == "true", {"said": said.get("window_hop")})
                if opened.get("ok"):
                    host.ok("target.close", {"target": opened["result"]["target"]})

                # The authority group: three pages one line apart, and what
                # the host decided about each.
                outcomes = {}
                for how in ("plain", "prevent", "forge"):
                    answer = host.call("target.open",
                                       {"session": session, "url": f"{origin}/link-{how}.html"},
                                       deadline_ms=8000)
                    if not answer.get("ok"):
                        outcomes[how] = "open_failed"
                        continue
                    target = answer["result"]["target"]
                    shot = snapshot(target, nodes=40)
                    link = [n for n in (shot or {}).get("nodes", []) if n.get("role") == "link"]
                    if not link:
                        outcomes[how] = "no_link"
                    else:
                        act = host.call("target.act",
                                        {"target": target, "reference": link[0]["reference"],
                                         "action": {"kind": "click"}}, deadline_ms=8000)
                        result = act.get("result") or {}
                        outcomes[how] = "navigated" if result.get("navigated") else "not_navigated"
                    host.ok("target.close", {"target": target})
                expect(tag + "a page that writes defaultPrevented cancels nothing the host does",
                       outcomes.get("plain") == "navigated"
                       and outcomes.get("forge") == "navigated",
                       {"outcomes": outcomes})
                expect(tag + "and preventDefault still cancels the host's activation",
                       outcomes.get("prevent") == "not_navigated", {"outcomes": outcomes})

                # isTrusted and the phase of an event the host itself raises.
                answer = host.call("target.open",
                                   {"session": session, "url": f"{origin}/trusted-click.html"},
                                   deadline_ms=8000)
                heard = None
                if answer.get("ok"):
                    target = answer["result"]["target"]
                    shot = snapshot(target, nodes=40)
                    button = [n for n in (shot or {}).get("nodes", []) if n.get("role") == "button"]
                    if button:
                        host.call("target.act",
                                  {"target": target, "reference": button[0]["reference"],
                                   "action": {"kind": "click"}}, deadline_ms=8000)
                        shot = snapshot(target, nodes=40)
                        texts = [n.get("name") for n in (shot or {}).get("nodes", [])
                                 if n.get("role") == "text"]
                        heard = texts[0] if texts else None
                    host.ok("target.close", {"target": target})
                expect(tag + "an event the host raises is not trusted, and reaches its target at phase 2",
                       heard == "isTrusted=false,phase=2,cancelable=true", {"heard": heard})
            finally:
                if host.timeouts:
                    killed_hosts.append({"group": f"event-{allocator}",
                                         "allocator": allocator, "timeouts": host.timeouts})
                host.finish()
                directory.cleanup()
    finally:
        server.shutdown()

    receipt = {
        "court": "native-dom bounded Event fidelity (control 0.0.2)",
        "host_sha256": hashlib.sha256(Path(args.binary).read_bytes()).hexdigest(),
        "checks": checks,
        "checks_passed": sum(1 for c in checks if c["passed"]),
        "checks_total": len(checks),
        "passed": all(c["passed"] for c in checks) and not killed_hosts,
        "hosts_killed": killed_hosts,
        "limitations": [
            "design-frozen court: it fails until the base Event and dispatcher are faithful",
            "no listener options (capture, once, passive, signal), no handleEvent objects, no capture phase and so no eventPhase 1",
            "no composedPath, no interface constants, no relatedTarget, no typed event interfaces beyond Event and CustomEvent",
            "an exception a listener throws is swallowed: nothing is reported to the page or the host",
            "timeStamp reads the clock this realm already inherited; this court asserts monotonicity between two constructions and nothing more",
            "the unchanged M1 and M2 floors are measured by the child-frame and shim-footprint courts on the same binary",
            "one hermetic loopback origin, macOS only; no surface, no window, no AppKit",
        ],
    }
    Path(args.receipt).write_text(json.dumps(receipt, indent=1, sort_keys=True) + "\n")
    print(json.dumps({"passed": receipt["passed"], "checks_passed": receipt["checks_passed"],
                      "checks_total": receipt["checks_total"],
                      "hosts_killed": len(killed_hosts)}))
    for check in checks:
        if not check["passed"]:
            print("FAIL " + json.dumps(check))
    return 0 if receipt["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
