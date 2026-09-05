#!/usr/bin/env python3
"""C1: the page-only members of `Node`, `Text` and `Element` belong to the
realm that can call them.

Frozen before the code. On the build it was written on, every child realm
carries all ten, and the two criteria that read the split fail there.

The first criterion is not a runtime check at all: it re-derives the audit
from the shipped sources, so the inventory cannot go stale in silence. If a
host script grows a call to a member that has left the base, or if a member a
child-capable script names is missing from the base, this court says so before
any host is started.

Strictly headless: no surface, no window, no AppKit, one hermetic loopback
origin, both allocators, supervised hosts with the wall-clock kill.
"""

import argparse
import hashlib
import importlib.util
import json
import os
import re
import subprocess
import sys
import tempfile
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "protocol"))
import check_contract  # noqa: E402,F401

VISIBLE_ENV = "MINICON_SURF_ALLOW_VISIBLE_COURT"
HERE = Path(__file__).resolve().parent
# Exactly the ten the ruling moves. Nothing else, and no more.
MOVED = ("firstChild", "lastChild", "parentElement", "appendChild", "remove",
         "innerText", "defaultValue", "focus", "blur", "submit",
         # C2a: the method moves and calls the base's own helper through the
         # handle, so the walk exists once and cannot drift.
         "contains",
         # A compatibility fix, never in the base: a page that calls it should
         # not die where a browser would have answered.
         "closest",
         # Reads the element's own attribute map, and claims to be nothing
         # more than a new array of names.
         "getAttributeNames")
# The scripts that run in a child realm as well as a main one.
CHILD_SCRIPTS = ("snapshot_script", "preflight_script", "act_script", "form_action_script",
                 "SERIALIZE_JS", "ACTIVATION_JS", "INSTALL_JS", "REVISION_JS",
                 "SCROLL_REVISION_JS", "dispatch_arm_script")


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


def source_inventory():
    """What the shipped sources say, re-derived rather than remembered."""
    base = (HERE / "src" / "dom_shim_base.js").read_text()
    extension = (HERE / "src" / "dom_shim_main.js").read_text()
    rust = (HERE / "src" / "main.rs").read_text()
    declared = set()
    inside = False
    for line in base.split("\n"):
        if re.match(r"  class (Node|Text|Element)\b", line):
            inside = True
            continue
        if inside and re.match(r"^  \}", line):
            inside = False
            continue
        if inside:
            m = re.match(r"\s{4}(?:get |set )?(\w+)\s*[({]", line)
            if m and m.group(1) != "constructor":
                declared.add(m.group(1))
    # Every member a child-capable host script names. The body is the script's
    # own string and nothing after it: a fixed window would run off the end of
    # the literal and read the Rust around it, which is how an early version of
    # this criterion "found" a host script calling .remove (§10.1).
    named = set()
    for name in CHILD_SCRIPTS:
        for pattern in (r"fn " + name + r"\(", r"const " + name + r"\s*:"):
            start = re.search(pattern, rust)
            if not start:
                continue
            tail = rust[start.end():]
            raw = re.search(r'r(#+)"(.*?)"\1', tail, re.S)
            plain = re.search(r'=\s*"((?:[^"\\]|\\.)*)"', tail)
            body = None
            if raw and (not plain or raw.start() < plain.start()):
                body = raw.group(2)
            elif plain:
                body = plain.group(1)
            if body is None:
                continue
            for member in re.findall(r"\.([A-Za-z_][A-Za-z0-9_]*)\b", body):
                named.add(member)
    installed = set(re.findall(r"(?:Node|Element)\.prototype\.(\w+)\s*=", extension))
    installed |= set(re.findall(r'Object\.defineProperty\((?:Node|Element)\.prototype,\s*"(\w+)"',
                                extension))
    return declared, named, installed


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--binary", required=True)
    parser.add_argument("--receipt", required=True)
    args = parser.parse_args()
    if os.environ.get(VISIBLE_ENV):
        print(json.dumps({"passed": False, "reason": "the visible-court variable is set"}))
        return 1

    checks = []
    killed_hosts = []

    def expect(name, condition, detail=None):
        checks.append({"check": name, "passed": bool(condition),
                       **({"detail": detail} if detail is not None else {})})

    declared, named, installed = source_inventory()
    still_in_base = sorted(m for m in MOVED if m in declared)
    expect("every moved member is declared nowhere in the base",
           not still_in_base, {"still_in_base": still_in_base})
    expect("and every one of them is installed by the main extension",
           all(m in installed for m in MOVED),
           {"missing": sorted(m for m in MOVED if m not in installed)})
    reached = sorted(m for m in MOVED if m in named)
    expect("no child-capable host script names a member that left the base",
           not reached, {"named_by_host": reached})
    missing = sorted(m for m in (named & {*declared, *MOVED}) if m not in declared and m not in MOVED)
    expect("and every member those scripts do name is still in the base",
           not missing, {"missing_from_base": missing})

    network = RETENTION.load_network_module()

    class Handler(network.Handler):
        def do_GET(self):
            path, _, _query = self.path.partition("?")
            network.Handler.hits.append(path)
            if path == "/page.html":
                # A main realm calls every moved member and writes what it got.
                return self.reply(200, (
                    "<!doctype html><html><body><main><p id=\"m\">start</p>"
                    "<div id=\"host\"><p id=\"a\">alpha</p><p id=\"b\">beta</p></div>"
                    "<form id=\"f\"><input id=\"v\" value=\"kept\"></form>"
                    "<p id=\"attrs\">attrs</p></main><script>"
                    "var host=document.getElementById('host');"
                    "var a=document.getElementById('a');"
                    "var out=[];"
                    "out.push(host.firstChild===a);"
                    "out.push(host.lastChild===document.getElementById('b'));"
                    "out.push(a.parentElement===host);"
                    "var made=document.createElement('p');"
                    "out.push(host.appendChild(made)===made);"
                    "made.remove();"
                    "out.push(host.lastChild!==made);"
                    "out.push(a.innerText==='alpha');"
                    "out.push(document.getElementById('v').defaultValue==='kept');"
                    "a.focus(); a.blur();"
                    "var submitted=false;"
                    "document.getElementById('f').addEventListener('submit', function(ev){"
                    "  submitted=true; ev.preventDefault(); });"
                    "document.getElementById('f').submit();"
                    "out.push(submitted);"
                    # getAttributeNames: empty, repeated, mixed case, order
                    # after a removal, and a new array every call.
                    "var bare=document.createElement('p');"
                    "out.push(bare.getAttributeNames().length===0);"
                    "var at=document.getElementById('attrs');"
                    "at.setAttribute('data-one','1');"
                    "at.setAttribute('data-one','2');"
                    "at.setAttribute('MixedCase','3');"
                    "out.push(at.getAttributeNames().join(',')==='id,data-one,mixedcase');"
                    "at.removeAttribute('data-one');"
                    "out.push(at.getAttributeNames().join(',')==='id,mixedcase');"
                    "out.push(at.getAttributeNames()!==at.getAttributeNames());"
                    "out.push(Array.isArray(at.getAttributeNames()));"
                    # closest: what it answers, where it stops, and that it
                    # refuses exactly what matches refuses.
                    "out.push(a.closest('#host')===host);"
                    "out.push(a.closest('p')===a);"
                    "out.push(a.closest('#nothing-here')===null);"
                    "out.push(document.getElementById('v').closest('form')"
                    "===document.getElementById('f'));"
                    "var loose=document.createElement('div');"
                    "var inner=document.createElement('span');"
                    "loose.append(inner);"
                    "out.push(inner.closest('div')===loose);"
                    "var refused=0;"
                    "['a > b','a, b','p:first-child'].forEach(function(sel){"
                    "  try { a.closest(sel); } catch (e) { refused += 1; } });"
                    "out.push(refused===3);"
                    "out.push(host.contains(a));"
                    "out.push(host.contains(host));"
                    "out.push(!a.contains(host));"
                    "out.push(!host.contains(document.createElement('p')));"
                    "document.getElementById('m').textContent=out.join(',');"
                    "</script></body></html>").encode())
            # A mutation deep in the tree: the revision only moves if the
            # observer's subtree scope still works, which is the base helper
            # doing the job it kept (§12.1).
            if path == "/deep.html":
                return self.reply(200, (
                    "<!doctype html><html><body><main><p id=\"m\">start</p>"
                    "<div><div><div id=\"deep\"><p id=\"leaf\">leaf</p></div></div></div>"
                    "</main><script>"
                    "setTimeout(function(){"
                    "document.getElementById('leaf').setAttribute('data-court','moved');"
                    "document.getElementById('m').textContent='mutated';},0);"
                    "</script></body></html>").encode())
            if path == "/parent.html":
                return self.reply(200, (
                    "<!doctype html><html><body><main><p>parent</p>"
                    "<iframe src=\"/child.html\"></iframe></main></body></html>").encode())
            if path == "/child.html":
                return self.reply(200, (
                    "<!doctype html><html><body><main>"
                    "<form><input id=\"c\" type=\"checkbox\" checked>"
                    "<input type=\"reset\" value=\"undo\"></form>"
                    "<p id=\"cm\">child text</p></main></body></html>").encode())
            return self.reply(404, b"gone")

    server = network.Server(("127.0.0.1", 0), Handler)
    origin = f"http://127.0.0.1:{server.server_address[1]}"
    threading.Thread(target=server.serve_forever, daemon=True).start()

    try:
        for allocator in ("system", "arena"):
            tag = f"[{allocator}] "
            directory = tempfile.TemporaryDirectory(prefix="minicon-surf-element-view-")
            host = JOBS.Supervised(args.binary, directory.name, origin, allocator)
            try:
                def owners():
                    answer = host.call("memory.report", {})
                    if not answer.get("ok"):
                        return None
                    owned = answer["result"]["owners"]
                    return owned["script_realms"]["malloc_bytes"] + owned["targets"]["fixture_bytes"]

                def snapshot(target, **extra):
                    answer = host.call("target.snapshot",
                                       {"target": target, "format": "semantic",
                                        "max_bytes": 65536, "max_nodes": 64, **extra})
                    return answer["result"] if answer.get("ok") else None

                profile = host.ok("profile.create", {"persistence": "ephemeral"})["profile"]
                session = host.ok("session.open", {"profile": profile})["session"]
                empty = owners()

                opened = host.call("target.open",
                                   {"session": session, "url": f"{origin}/page.html"},
                                   deadline_ms=8000)
                said = None
                if opened.get("ok"):
                    shot = snapshot(opened["result"]["target"])
                    texts = [n.get("name") for n in (shot or {}).get("nodes", [])
                             if n.get("role") == "text"]
                    said = texts[0] if texts else None
                    host.ok("target.close", {"target": opened["result"]["target"]})
                expect(tag + "a main realm calls every moved member and each answers as it did",
                       said == ("true,true,true,true,true,true,true,true"
                                ",true,true,true,true,true"
                                ",true,true,true,true,true,true,true,true,true,true"),
                       {"said": said})

                opened = host.call("target.open",
                                   {"session": session, "url": f"{origin}/deep.html"},
                                   deadline_ms=8000)
                before = opened["result"]["revision"] if opened.get("ok") else None
                deep = opened["result"]["target"] if opened.get("ok") else None
                after = None
                if deep:
                    answer = host.call("target.inspect", {"target": deep})
                    after = (answer.get("result") or {}).get("revision")
                    host.ok("target.close", {"target": deep})
                expect(tag + "and the base's helper still gives the observer its subtree scope",
                       before is not None and after is not None and after > before,
                       {"revision": [before, after]})

                opened = host.call("target.open",
                                   {"session": session, "url": f"{origin}/parent.html"},
                                   deadline_ms=8000)
                target = opened["result"]["target"] if opened.get("ok") else None
                inspected = host.call("target.inspect", {"target": target}) if target else {}
                frames = (inspected.get("result") or {}).get("frames") or []
                frame = frames[1]["frame"] if len(frames) > 1 else None
                shot = snapshot(target, frame=frame) if frame else None
                nodes = (shot or {}).get("nodes") or []
                expect(tag + "a child realm still answers a snapshot, selectors and all",
                       bool(nodes) and any(n.get("role") == "text" for n in nodes),
                       {"nodes": len(nodes)})

                boxes = [n for n in nodes if n.get("role") in ("checkbox", "switch")]
                acted = None
                if boxes:
                    answer = host.call("target.act",
                                       {"target": target, "reference": boxes[0]["reference"],
                                        "action": {"kind": "set_checked", "checked": False}},
                                       deadline_ms=8000)
                    acted = (answer.get("result") or {}).get("applied") if answer.get("ok") \
                        else (answer.get("error") or {}).get("code")
                expect(tag + "a child's host action still applies through the bridge",
                       acted is True, {"acted": acted})

                undone = None
                if frame:
                    shot = snapshot(target, frame=frame)
                    buttons = [n for n in ((shot or {}).get("nodes") or [])
                               if n.get("role") == "button"]
                    if buttons:
                        answer = host.call("target.act",
                                           {"target": target, "reference": buttons[0]["reference"],
                                            "action": {"kind": "click"}}, deadline_ms=8000)
                        if answer.get("ok"):
                            after = snapshot(target, frame=frame)
                            boxes_after = [n for n in ((after or {}).get("nodes") or [])
                                           if n.get("role") in ("checkbox", "switch")]
                            undone = boxes_after[0].get("checked") if boxes_after else None
                expect(tag + "and the DOM's own reset still runs in a child realm",
                       undone is True, {"checked_after_reset": undone})
                if target:
                    host.ok("target.close", {"target": target})
                closed = owners()
                expect(tag + "closing every target returns the owners exactly",
                       closed is not None and closed == empty,
                       {"closed": closed, "empty": empty})
            finally:
                if host.timeouts:
                    killed_hosts.append({"group": f"element-view-{allocator}",
                                         "allocator": allocator, "timeouts": host.timeouts})
                host.finish()
                directory.cleanup()

        # The divergence itself, through the court-only realm probe.
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
               {"code": closed.returncode})
        directory = tempfile.TemporaryDirectory(prefix="minicon-surf-element-divergence-")
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
                expect("the main realm has the moved members and every child realm has none",
                       opened.get("ok") is True
                       and probe.get("main_element_view") is True
                       and probe.get("children_element_view") == 0
                       and probe.get("realms_probed", 0) >= 2,
                       {"probe": probe})
                if opened.get("ok"):
                    host.ok("target.close", {"target": opened["result"]["target"]})
        finally:
            if host.timeouts:
                killed_hosts.append({"group": "divergence", "allocator": "system",
                                     "timeouts": host.timeouts})
            host.finish()
            directory.cleanup()
            expect("the probe seam's court file is gone when the host is",
                   not probe_file.exists(), {"court_file": probe_file.exists()})
            probe_directory.cleanup()
    finally:
        server.shutdown()

    receipt = {
        "court": "native-dom page-only Element members (control 0.0.2)",
        "host_sha256": hashlib.sha256(Path(args.binary).read_bytes()).hexdigest(),
        "moved": list(MOVED),
        "checks": checks,
        "checks_passed": sum(1 for c in checks if c["passed"]),
        "checks_total": len(checks),
        "passed": all(c["passed"] for c in checks) and not killed_hosts,
        "hosts_killed": killed_hosts,
        "limitations": [
            "design-frozen court: the source-inventory and divergence criteria fail until the ten members move",
            "the inventory is re-derived from the shipped sources beside this court, so it is repo-local by design",
            "the M1 and M2 floors and the main-only slack are measured by the child-frame and shim-footprint courts on the same binary",
            "control-churn is not part of this evidence: it requires a surface binary and this slice runs no surface path",
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
