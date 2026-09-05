#!/usr/bin/env python3
"""`classList` and `CustomEvent`: the last of the triaged gaps.

Frozen before the code. It fails on the build it was written on, where both
are undefined. Everything a page learns is written into its own document and
read back through `target.snapshot`, so the court never needs an eval surface
the host does not have.

Strictly headless: no surface, no window, no AppKit, one hermetic loopback
origin, both allocators, supervised hosts with the wall-clock kill.
"""

import argparse
import hashlib
import importlib.util
import json
import os
import subprocess
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


def page(script, body='<p id="m">start</p>'):
    """One fixture shape: a marked element, a writer, and a script that puts
    what it learned where a snapshot can read it."""
    return ("<!doctype html><html><body><main>" + body + "</main>"
            "<script>var mark=document.getElementById('m');"
            "var write=function(t){mark.textContent=String(t);};"
            "var say=function(fn){try{write(fn());}catch(e){write('threw:'+e.name);}};"
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

    class Handler(network.Handler):
        def do_GET(self):
            path, _, _query = self.path.partition("?")
            network.Handler.hits.append(path)
            pages = {
                # 1: what the page adds is what the attribute, the selector
                # and className all say.
                "/reflect.html": page(
                    "var t=document.getElementById('t');"
                    "t.classList.add('a');t.classList.add('b');"
                    "say(function(){return t.getAttribute('class')+'|'"
                    "+(document.querySelector('.b')===t)+'|'+t.className;});",
                    '<p id="m">start</p><p id="t">target</p>'),
                # 2: removal and the four shapes of toggle.
                "/toggle.html": page(
                    "var t=document.getElementById('t');"
                    "t.className='a b';"
                    "var out=[];"
                    "t.classList.remove('a');out.push(t.getAttribute('class'));"
                    "out.push(t.classList.toggle('b'));out.push(t.getAttribute('class'));"
                    "out.push(t.classList.toggle('c',true));out.push(t.getAttribute('class'));"
                    "out.push(t.classList.toggle('c',false));out.push(t.getAttribute('class'));"
                    "write(out.join('|'));",
                    '<p id="m">start</p><p id="t">target</p>'),
                # 3: contains and length read the attribute the page wrote,
                # ragged whitespace and all.
                # The token reads are parsed; `value` and `toString` are the
                # attribute's own string, raggedness and all (§9.3).
                "/read.html": page(
                    "var t=document.getElementById('t');"
                    "t.setAttribute('class','  one   two  ');"
                    "say(function(){return t.classList.contains('two')+'|'"
                    "+t.classList.contains('three')+'|'+t.classList.length"
                    "+'|['+t.classList.value+']|['+t.classList.toString()+']';});",
                    '<p id="m">start</p><p id="t">target</p>'),
                # 9.1 and 9.2: a held list is the same object and a live view.
                "/live.html": page(
                    "var t=document.getElementById('t');"
                    "var u=document.getElementById('u');"
                    "var list=t.classList;"
                    "var same=(t.classList===t.classList)+','+(list===t.classList)"
                    "+','+(list!==u.classList);"
                    "t.className='x y';"
                    "var afterClassName=list.contains('x')+','+list.length+','+list.value;"
                    "t.setAttribute('class','p q');"
                    "var afterAttribute=list.contains('q')+','+list.toString();"
                    "list.add('r');"
                    "var afterAdd=t.getAttribute('class');"
                    "var identityKept=(list===t.classList);"
                    "write([same,afterClassName,afterAttribute,afterAdd,identityKept].join('|'));",
                    '<p id="m">start</p><p id="t">target</p><p id="u">other</p>'),
                # 9.3: the setter writes the attribute it is given, verbatim,
                # and the token reads over it stay parsed.
                "/value.html": page(
                    "var t=document.getElementById('t');"
                    "t.className='a';"
                    "t.classList.value='  b   c ';"
                    "write('['+t.getAttribute('class')+']|'+t.classList.length"
                    "+'|'+t.classList.contains('c')+'|['+t.classList.value+']');",
                    '<p id="m">start</p><p id="t">target</p>'),
                # 9.4: an explicit null dictionary is not a crash.
                "/null-init.html": page(
                    "say(function(){var e=new CustomEvent('court:none',null);"
                    "return e.type+'|'+String(e.detail)+'|'+e.bubbles;});"),
                # 4: a turn that changes nothing must not advance the
                # revision; the same page changes something on demand.
                # The no-op turn must touch nothing at all, so what it
                # learned is written on the turn after it: the first boundary
                # is pure no-op calls, and the text that arrives on the second
                # is what proves they ran rather than threw.
                "/quiet.html": page(
                    "var t=document.getElementById('t');"
                    "t.className='keep';"
                    "setTimeout(function(){"
                    "var seen;"
                    "try{t.classList.remove('absent');t.classList.add('keep');"
                    "seen='quiet:'+t.getAttribute('class');}"
                    "catch(e){seen='threw:'+e.name;}"
                    "setTimeout(function(){write(seen);},0);},0);",
                    '<p id="m">start</p><p id="t">target</p>'),
                "/loud.html": page(
                    "var t=document.getElementById('t');"
                    "t.className='keep';"
                    "setTimeout(function(){t.classList.add('added');"
                    "write(t.getAttribute('class'));},0);",
                    '<p id="m">start</p><p id="t">target</p>'),
                # 5: the two token errors, and the attribute left alone.
                "/errors.html": page(
                    "var t=document.getElementById('t');"
                    "t.className='a';"
                    "var out=[];"
                    "try{t.classList.add('');}catch(e){out.push(e.name);}"
                    "try{t.classList.add('a b');}catch(e){out.push(e.name);}"
                    "out.push(t.getAttribute('class'));"
                    "write(out.join('|'));",
                    '<p id="m">start</p><p id="t">target</p>'),
                # 6: a CustomEvent's detail crosses a bubbling dispatch.
                "/custom.html": page(
                    "var t=document.getElementById('t');"
                    "var seen='none';"
                    "document.addEventListener('court:pick',function(e){"
                    "seen=e.type+'|'+(e.detail&&e.detail.which)+'|'"
                    "+(e.detail&&e.detail.deep&&e.detail.deep.n)+'|'+(e.target===t);});"
                    "t.dispatchEvent(new CustomEvent('court:pick',"
                    "{bubbles:true,detail:{which:'alpha',deep:{n:7}}}));"
                    "write(seen);",
                    '<p id="m">start</p><p id="t">target</p>'),
                # 7: a parent whose child realm must have neither.
                "/parent.html": page("write('parent');",
                                     '<p id="m">start</p><iframe src="/child.html"></iframe>'),
                "/child.html": ("<!doctype html><html><body><p id=\"c\">child</p>"
                                "</body></html>").encode(),
            }
            if path in pages:
                return self.reply(200, pages[path])
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
            directory = tempfile.TemporaryDirectory(prefix="minicon-surf-element-")
            host = JOBS.Supervised(args.binary, directory.name, origin, allocator)
            try:
                def open_page(session, path):
                    return host.call("target.open", {"session": session, "url": f"{origin}{path}"},
                                     deadline_ms=8000)

                def mark(target):
                    """What the page wrote into its marked element."""
                    answer = host.call("target.snapshot",
                                       {"target": target, "format": "semantic",
                                        "max_bytes": 65536, "max_nodes": 32})
                    if not answer.get("ok"):
                        return None
                    texts = [n.get("name") for n in answer["result"]["nodes"]
                             if n.get("role") == "text"]
                    return texts[0] if texts else None

                def revision(target):
                    answer = host.call("target.inspect", {"target": target})
                    return answer["result"]["revision"] if answer.get("ok") else None

                def read(path):
                    opened = open_page(session, path)
                    if not opened.get("ok"):
                        return None, None
                    target = opened["result"]["target"]
                    return target, mark(target)

                profile = host.ok("profile.create", {"persistence": "ephemeral"})["profile"]
                session = host.ok("session.open", {"profile": profile})["session"]

                target, said = read("/reflect.html")
                expect(tag + "what the page adds is what the attribute, the selector and className say",
                       said == "a b|true|a b", {"said": said})
                if target:
                    host.ok("target.close", {"target": target})

                target, said = read("/toggle.html")
                expect(tag + "remove leaves the rest, and toggle answers and acts in all four shapes",
                       said == "b|false||true|c|false|", {"said": said})
                if target:
                    host.ok("target.close", {"target": target})

                target, said = read("/read.html")
                expect(tag + "token reads are parsed while value and toString are the attribute itself",
                       said == "true|false|2|[  one   two  ]|[  one   two  ]", {"said": said})
                if target:
                    host.ok("target.close", {"target": target})

                target, said = read("/live.html")
                expect(tag + "the list is the same object each time, and never shared between elements",
                       said is not None and said.split("|")[0] == "true,true,true"
                       and said.split("|")[-1] == "true",
                       {"said": said})
                expect(tag + "and a held list answers about the attribute as it is now, not as it was",
                       said is not None and said.split("|")[1] == "true,2,x y"
                       and said.split("|")[2] == "true,p q",
                       {"said": said})
                expect(tag + "and adding through a held list keeps what was written directly",
                       said is not None and said.split("|")[3] == "p q r", {"said": said})
                if target:
                    host.ok("target.close", {"target": target})

                target, said = read("/value.html")
                expect(tag + "the value setter writes what it is given and the token reads stay parsed",
                       said == "[  b   c ]|2|true|[  b   c ]", {"said": said})
                if target:
                    host.ok("target.close", {"target": target})

                target, said = read("/null-init.html")
                expect(tag + "an explicit null dictionary is an empty one, not a crash",
                       said == "court:none|null|false", {"said": said})
                if target:
                    host.ok("target.close", {"target": target})

                # A turn that changes nothing must not advance the revision.
                # The "before" is the revision the open itself reports, because
                # an observation is the boundary that runs the due timer.
                opened = open_page(session, "/quiet.html")
                quiet = opened["result"]["target"] if opened.get("ok") else None
                before = opened["result"]["revision"] if opened.get("ok") else None
                # This boundary runs the no-op turn and nothing else.
                after = revision(quiet) if quiet else None
                said = mark(quiet) if quiet else None
                expect(tag + "a turn whose calls change nothing does not advance the revision",
                       said == "quiet:keep" and before is not None and before == after,
                       {"said": said, "revision": [before, after]})
                if quiet:
                    host.ok("target.close", {"target": quiet})
                opened = open_page(session, "/loud.html")
                loud = opened["result"]["target"] if opened.get("ok") else None
                before = opened["result"]["revision"] if opened.get("ok") else None
                after = revision(loud) if loud else None
                said = mark(loud) if loud else None
                expect(tag + "and a turn that changes the attribute does advance it",
                       said == "keep added" and before is not None and after is not None
                       and after > before,
                       {"said": said, "revision": [before, after]})
                if loud:
                    host.ok("target.close", {"target": loud})

                target, said = read("/errors.html")
                expect(tag + "an empty token and a token with a space throw their own names, "
                       "and neither changes the attribute",
                       said == "SyntaxError|InvalidCharacterError|a", {"said": said})
                if target:
                    host.ok("target.close", {"target": target})

                target, said = read("/custom.html")
                expect(tag + "a CustomEvent carries its detail through a bubbling dispatch",
                       said == "court:pick|alpha|7|true", {"said": said})
                if target:
                    host.ok("target.close", {"target": target})
            finally:
                if host.timeouts:
                    killed_hosts.append({"group": f"element-{allocator}",
                                         "allocator": allocator, "timeouts": host.timeouts})
                host.finish()
                directory.cleanup()

        # 7: main-only means a child realm has neither. There is deliberately
        # no eval surface in a child realm, so this is the court-only realm
        # probe, refused before the host serves without the private court file.
        probe_directory = tempfile.TemporaryDirectory(prefix="minicon-surf-element-probe-")
        probe_file = Path(probe_directory.name) / "court.ndjson"
        closed = subprocess.run(
            [args.binary, "serve", "--stdio", "--fixture-root", str(RETENTION.FIXTURE_ROOT),
             "--config-dir", str(Path(probe_directory.name) / "closed"),
             "--allow-origin", origin, "--court-realm-probe", "1"],
            input="", capture_output=True, text=True, timeout=30,
            env={k: v for k, v in os.environ.items() if k != VISIBLE_ENV})
        expect("the realm probe is refused without the private court file",
               closed.returncode != 0 and not closed.stdout.strip(),
               {"code": closed.returncode, "answered": len(closed.stdout)})
        directory = tempfile.TemporaryDirectory(prefix="minicon-surf-element-main-only-")
        host = JOBS.Supervised(args.binary, directory.name, origin, "system",
                               extra=("--court-realm-probe", "1",
                                      "--surface-court-file", str(probe_file)))
        try:
            answer = host.call("profile.create", {"persistence": "ephemeral"})
            if not answer.get("ok"):
                expect("the host accepts the court-only realm probe", False,
                       {"reason": (answer.get("error") or {}).get("code", "refused")})
            else:
                profile = answer["result"]["profile"]
                session = host.ok("session.open", {"profile": profile})["session"]
                opened = host.call("target.open",
                                   {"session": session, "url": f"{origin}/parent.html"},
                                   deadline_ms=8000)
                report = host.call("memory.report", {})
                probe = ((report.get("result") or {}).get("owners") or {}).get("realm_probe") or {}
                expect("the main realm has classList and CustomEvent",
                       opened.get("ok") is True
                       and probe.get("main_class_list") is True
                       and probe.get("main_custom_event") is True,
                       {"probe": probe})
                expect("and no child realm has either, which is what main-only has to mean",
                       probe.get("realms_probed", 0) >= 2
                       and probe.get("children_class_list") == 0
                       and probe.get("children_custom_event") == 0,
                       {"probe": probe})
                if opened.get("ok"):
                    host.ok("target.close", {"target": opened["result"]["target"]})
        finally:
            if host.timeouts:
                killed_hosts.append({"group": "realm-probe", "allocator": "system",
                                     "timeouts": host.timeouts})
            host.finish()
            directory.cleanup()
            expect("the probe seam's court file is gone when the host is",
                   not probe_file.exists(), {"court_file": probe_file.exists()})
            probe_directory.cleanup()
    finally:
        server.shutdown()

    receipt = {
        "court": "native-dom classList and CustomEvent (control 0.0.2)",
        "host_sha256": hashlib.sha256(Path(args.binary).read_bytes()).hexdigest(),
        "checks": checks,
        "checks_passed": sum(1 for c in checks if c["passed"]),
        "checks_total": len(checks),
        "passed": all(c["passed"] for c in checks) and not killed_hosts,
        "hosts_killed": killed_hosts,
        "limitations": [
            "design-frozen court: it fails until classList and CustomEvent exist",
            "no iteration, index access, item, replace or supports on the list, and no DOMTokenList global",
            "a call that changes nothing writes nothing: a recorded divergence, proven in both directions",
            "the revision advances per mutation flush, not per call, which is the host's existing batching",
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
