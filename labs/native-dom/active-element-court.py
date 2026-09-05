#!/usr/bin/env python3
"""Focus belongs to the agent's actions, and to nothing a page can say.

Frozen before the code. On the build it was written on there is no
`document.activeElement` at all, so most of this fails there.

The narrow model it holds: a host-driven action moves focus, a page's own
`focus()` moves nothing, the state is unforgeable from page script, the four
focus events go through the trusted dispatcher in the standard's order, and no
focused element outlives its document.

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

# The page records what it sees into its own elements; the court reads them
# back. Every fixture keeps its own references, taken before any attack, so
# what is measured is what happened and not whether the page can still look.
PAGE = (
    "<!doctype html><html><body><main><p id=\"m\">start</p>"
    "<div id=\"host\"><input id=\"a\" value=\"a\"><input id=\"b\" value=\"b\"></div>"
    "<p id=\"log\"></p><p id=\"who\"></p><p id=\"forge\"></p></main><script>"
    "var mark=document.getElementById('m');"
    "var log=document.getElementById('log');"
    "var who=document.getElementById('who');"
    "var forge=document.getElementById('forge');"
    "var host=document.getElementById('host');"
    "var a=document.getElementById('a');var b=document.getElementById('b');"
    "var seen=[];"
    "['focus','blur'].forEach(function(type){"
    "  [a,b].forEach(function(el){ el.addEventListener(type, function(ev){"
    "    seen.push(type+':'+ev.target.id); }); }); });"
    "['focusin','focusout'].forEach(function(type){"
    "  host.addEventListener(type, function(ev){ seen.push(type+':'+ev.target.id); }); });"
    "var report=function(){"
    "  var el=document.activeElement;"
    "  who.textContent='active='+(el?(el.id||el.localName):'none');"
    "  log.textContent='events='+(seen.join(',')||'none'); };"
    "report();"
    "a.addEventListener('click', report);"
    "b.addEventListener('click', report);"
    "mark.textContent='page ran';"
    "</script></body></html>"
)

# A second page that attacks the state before the host acts on it.
FORGE_PAGE = (
    "<!doctype html><html><body><main><p id=\"m\">start</p>"
    "<input id=\"a\" value=\"a\"><input id=\"b\" value=\"b\">"
    "<p id=\"who\"></p></main><script>"
    "var who=document.getElementById('who');"
    "var a=document.getElementById('a');var b=document.getElementById('b');"
    "var tried=[];"
    "try { document.__focused = b; tried.push('own-property'); } catch (e) {}"
    "try { document.activeElement = b; tried.push('assign'); } catch (e) {}"
    "try { b.focus(); tried.push('page-focus'); } catch (e) {}"
    "a.addEventListener('click', function(){"
    "  var el=document.activeElement;"
    "  who.textContent='after='+(el?(el.id||el.localName):'none')+'|tried='+tried.join('+');"
    "});"
    "document.getElementById('m').textContent='page ran';"
    "</script></body></html>"
)

NEXT_PAGE = (
    "<!doctype html><html><body><main><p id=\"m\">next</p>"
    "<p id=\"who\"></p></main><script>"
    "var el=document.activeElement;"
    "document.getElementById('who').textContent="
    "'active='+(el?(el.id||el.localName):'none');"
    "</script></body></html>"
)


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
            pages = {"/page.html": PAGE, "/forge.html": FORGE_PAGE, "/next.html": NEXT_PAGE}
            if path in pages:
                return self.reply(200, pages[path].encode())
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
            directory = tempfile.TemporaryDirectory(prefix="minicon-surf-focus-")
            host = JOBS.Supervised(args.binary, directory.name, origin, allocator)
            try:
                def owners():
                    answer = host.call("memory.report", {})
                    if not answer.get("ok"):
                        return None
                    owned = answer["result"]["owners"]
                    return owned["script_realms"]["malloc_bytes"] + owned["targets"]["fixture_bytes"]

                def texts(target):
                    answer = host.call("target.snapshot",
                                       {"target": target, "format": "semantic",
                                        "max_bytes": 65536, "max_nodes": 64})
                    if not answer.get("ok"):
                        return []
                    return [n.get("name") or "" for n in answer["result"]["nodes"]
                            if n.get("role") == "text"]

                def field(target, key):
                    for text in texts(target):
                        if text.startswith(key + "="):
                            return text[len(key) + 1:]
                    return None

                def boxes(target):
                    answer = host.call("target.snapshot",
                                       {"target": target, "format": "semantic",
                                        "max_bytes": 65536, "max_nodes": 64})
                    if not answer.get("ok"):
                        return []
                    return [n for n in answer["result"]["nodes"] if n.get("role") == "textbox"]

                def click(target, node):
                    return host.call("target.act",
                                     {"target": target, "reference": node["reference"],
                                      "action": {"kind": "click"}}, deadline_ms=8000)

                profile = host.ok("profile.create", {"persistence": "ephemeral"})["profile"]
                session = host.ok("session.open", {"profile": profile})["session"]
                empty = owners()

                opened = host.call("target.open",
                                   {"session": session, "url": f"{origin}/page.html"},
                                   deadline_ms=8000)
                target = opened["result"]["target"] if opened.get("ok") else None
                expect(tag + "before anything is activated, the body is what is active",
                       field(target, "active") == "body" if target else False,
                       {"active": field(target, "active") if target else None})

                fields = boxes(target) if target else []
                first = click(target, fields[0]) if len(fields) > 0 else None
                expect(tag + "a host click focuses what it clicked",
                       field(target, "active") == "a" if first and first.get("ok") else False,
                       {"active": field(target, "active") if target else None})

                fields = boxes(target) if target else []
                second = click(target, fields[1]) if len(fields) > 1 else None
                expect(tag + "and a second click moves it",
                       field(target, "active") == "b" if second and second.get("ok") else False,
                       {"active": field(target, "active") if target else None})
                expect(tag + "the four events fire in the standard's order, and only focusin "
                       "and focusout reach an ancestor",
                       # Focus moves before the click is dispatched, so the
                       # page's click listener already sees the new value: the
                       # first click raises focus and focusin at a, the second
                       # blur and focusout at a, then focus and focusin at b.
                       field(target, "events")
                       == "focus:a,focusin:a,blur:a,focusout:a,focus:b,focusin:b",
                       {"events": field(target, "events")})
                if target:
                    host.ok("target.close", {"target": target})

                # A page that tries to move focus itself, before a host click.
                opened = host.call("target.open",
                                   {"session": session, "url": f"{origin}/forge.html"},
                                   deadline_ms=8000)
                target = opened["result"]["target"] if opened.get("ok") else None
                fields = boxes(target) if target else []
                if fields:
                    click(target, fields[0])
                after = field(target, "after") if target else None
                expect(tag + "a page cannot forge focus, and the host's click still decides it",
                       after is not None and after.startswith("a|")
                       and "page-focus" in after,
                       {"after": after})
                if target:
                    host.ok("target.close", {"target": target})

                # Nothing outlives its document.
                opened = host.call("target.open",
                                   {"session": session, "url": f"{origin}/page.html"},
                                   deadline_ms=8000)
                target = opened["result"]["target"] if opened.get("ok") else None
                fields = boxes(target) if target else []
                if fields:
                    click(target, fields[0])
                navigated = host.call("target.navigate",
                                      {"target": target, "url": f"{origin}/next.html"},
                                      deadline_ms=8000) if target else {}
                expect(tag + "a new document starts with nothing focused",
                       navigated.get("ok") is True and field(target, "active") == "body",
                       {"active": field(target, "active") if target else None})
                if target:
                    host.ok("target.close", {"target": target})

                closed = owners()
                expect(tag + "closing every target returns the owners exactly",
                       closed is not None and closed == empty,
                       {"closed": closed, "empty": empty})
            finally:
                if host.timeouts:
                    killed_hosts.append({"group": f"focus-{allocator}",
                                         "allocator": allocator, "timeouts": host.timeouts})
                host.finish()
                directory.cleanup()
    finally:
        server.shutdown()

    receipt = {
        "court": "native-dom host-driven focus (control 0.0.2)",
        "host_sha256": hashlib.sha256(Path(args.binary).read_bytes()).hexdigest(),
        "checks": checks,
        "checks_passed": sum(1 for c in checks if c["passed"]),
        "checks_total": len(checks),
        "passed": all(c["passed"] for c in checks) and not killed_hosts,
        "hosts_killed": killed_hosts,
        "limitations": [
            "design-frozen court: it fails until a host action moves focus",
            "the narrow model by ruling: a page's own focus() moves nothing, so a page that focuses itself and reads activeElement sees what the agent last activated",
            "no tabindex, contenteditable, autofocus, Tab navigation, :focus, hasFocus, cross-frame focus or relatedTarget; no focus field in the snapshot",
            "the M1 and M2 floors and the main-only slack, including the handle identifier's cost, are measured by the child-frame and shim-footprint courts on the same binary",
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
