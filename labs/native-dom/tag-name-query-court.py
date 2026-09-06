#!/usr/bin/env python3
"""The frozen court for `getElementsByTagName`.

Frozen from `smallest-method-audit-0.0.1.md` §7 before the extension changes,
and failing until the method exists.

One member on `Node` serves both call surfaces, because `querySelectorAll`
lives there and `Element` and `Document` extend it — a second definition would
be a second member, and the slack this slice was given fits exactly one.

Two divergences are pinned as losses rather than left to be met as surprises:
the result is a plain array and not a live `HTMLCollection`, and a
namespace-ish name such as `a:b` answers empty where a browser matches an
element literally called that.

Every counting criterion is written to be order-independent — it compares an
answer with `querySelectorAll` taken at the same moment, or with itself across
an action — because a probe that names an absolute count in a shared document
measures the probes before it. That has cost this batch three amended
criteria; this court is written not to repeat it.

Strictly headless: no surface binary, no window, no AppKit, and it refuses to
run with the visible-court variable set. One hermetic loopback origin, both
allocators.

Groups: sources, scope, matching, shape, losses, inertness, realm, owners.
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
OWNER_RETURN_BYTES = 65536

PROBES = [
    # Scope, order, and the element's own exclusion — by identity against the
    # selector engine, so no absolute count is named.
    ("document_scope",
     "(function(){var a=document.getElementsByTagName('p');"
     "var b=document.querySelectorAll('p');"
     "return String(a.length===b.length)+'|'"
     "+String(a.every(function(e,i){return e===b[i];}));})()"),
    ("element_scope",
     "(function(){var box=document.getElementById('box');"
     "var a=box.getElementsByTagName('p');var b=box.querySelectorAll('p');"
     "return String(a.length===b.length)+'|'"
     "+String(a.every(function(e,i){return e===b[i];}))+'|'"
     "+String(a.indexOf(document.getElementById('outside'))<0);})()"),
    ("self_excluded",
     "(function(){var box=document.getElementById('box');"
     "return String(box.getElementsByTagName('div').indexOf(box)<0);})()"),
    ("document_order",
     "(function(){var a=document.getElementsByTagName('p');"
     "var ids=[];for(var i=0;i<a.length;i+=1)if(a[i].id)ids.push(a[i].id);"
     "return ids.slice(0,3).join(',');})()"),
    ("case_insensitive",
     "(function(){var lower=document.getElementsByTagName('p');"
     "var upper=document.getElementsByTagName('P');"
     "return String(lower.length===upper.length)+'|'+String(upper.length>0);})()"),
    ("star",
     "(function(){var a=document.getElementsByTagName('*');"
     "var b=document.querySelectorAll('*');"
     "return String(a.length===b.length)+'|'+String(a.length>0);})()"),
    ("detached",
     "(function(){var root=document.createElement('div');"
     "var kid=document.createElement('p');root.append(kid);"
     "return String(root.getElementsByTagName('p').length===1)+'|'"
     "+String(root.getElementsByTagName('p')[0]===kid);})()"),
    ("no_match", "String(document.getElementsByTagName('nosuchtag').length)"),
    ("empty_argument",
     "(function(){try{return 'length '+document.getElementsByTagName('').length;}"
     "catch(e){return 'threw:'+e.name;}})()"),
    ("unparseable_argument",
     "(function(){try{return 'length '+document.getElementsByTagName('a:b').length;}"
     "catch(e){return 'threw:'+e.name;}})()"),
    ("plain_array",
     "(function(){var a=document.getElementsByTagName('p');"
     "return String(Array.isArray(a))+'|'+typeof a.item+'|'+typeof a.namedItem;})()"),
    ("fresh_each_call",
     "(function(){var a=document.getElementsByTagName('p');"
     "var b=document.getElementsByTagName('p');"
     "return String(a!==b)+'|'+String(a.length===b.length);})()"),
    ("not_live",
     "(function(){var held=document.getElementsByTagName('p');var before=held.length;"
     "var extra=document.createElement('p');document.getElementById('box').append(extra);"
     "var after=held.length;var fresh=document.getElementsByTagName('p').length;"
     "return String(before===after)+'|'+String(fresh===before+1);})()"),
    ("inert_during_dispatch",
     "(function(){var bus=document.getElementById('box');var seen=0;"
     "var before=document.getElementsByTagName('p').length;"
     "bus.addEventListener('probe',function(){seen+=1;"
     "document.getElementsByTagName('p');});"
     "bus.dispatchEvent(new Event('probe'));"
     "return 'ran '+seen+'|unchanged '"
     "+String(before===document.getElementsByTagName('p').length);})()"),
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
    PAGE = ("<!doctype html><html><body><p id=\"outside\">out</p>"
            "<main><div id=\"box\"><p id=\"first\">1</p><p id=\"second\">2</p></div>"
            + slots + "</main><script>" + script + "</script></body></html>").encode()
    MANY = ("<!doctype html><html><body><main><p id=m>many</p></main><script>"
            "var n=0;try{for(var i=0;i<200;i+=1){"
            "n+=document.getElementsByTagName('p').length;}"
            "document.getElementById('m').textContent='counted '+n;}catch(e){}"
            "</script></body></html>").encode()
    CHILD = b"<!doctype html><html><body><main><p id=c>embedded static</p></main></body></html>"
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
    definitions = len(re.findall(r"getElementsByTagName\s*=", main_source))
    expect("H1: one definition serves both surfaces, and it is in the extension",
           definitions == 1 and "getElementsByTagName" not in base,
           {"definitions_in_main": definitions,
            "in_base": "getElementsByTagName" in base})
    expect("H2: the handle's key set is unchanged by this slice",
           handle_keys(base) == HANDLE_KEYS,
           {"unexpected": sorted(handle_keys(base) - HANDLE_KEYS),
            "missing": sorted(HANDLE_KEYS - handle_keys(base))})
    expect("H3: getElementsByClassName is still not here — it stays on hold",
           "getElementsByClassName" not in main_source
           and "getElementsByClassName" not in base,
           {"in_main": "getElementsByClassName" in main_source})

    try:
        for allocator in ("system", "arena"):
            tag = f"[{allocator}] "
            with tempfile.TemporaryDirectory(prefix="minicon-surf-tagname-") as directory:
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
                    said = {}
                    for node in host.ok("target.snapshot",
                                        {"target": target, "format": "semantic",
                                         "max_bytes": 131072,
                                         "max_nodes": 128})["nodes"]:
                        text = node.get("name") or ""
                        if node.get("role") == "text" and "=" in text:
                            key, _, value = text.partition("=")
                            said[key] = value

                    expect(tag + "T1: the document surface answers as the selector engine does",
                           said.get("document_scope") == "true|true",
                           {"said": said.get("document_scope")})
                    expect(tag + "T2: the element surface is scoped to its own descendants",
                           said.get("element_scope") == "true|true|true",
                           {"said": said.get("element_scope")})
                    expect(tag + "T3: an element does not find itself",
                           said.get("self_excluded") == "true",
                           {"said": said.get("self_excluded")})
                    expect(tag + "T4: results come in document order",
                           said.get("document_order") == "outside,first,second",
                           {"said": said.get("document_order")})
                    expect(tag + "T5: the name is matched case-insensitively",
                           said.get("case_insensitive") == "true|true",
                           {"said": said.get("case_insensitive")})
                    expect(tag + "T6: '*' means everything, as the engine has it",
                           said.get("star") == "true|true", {"said": said.get("star")})
                    expect(tag + "T7: it works inside a detached subtree",
                           said.get("detached") == "true|true",
                           {"said": said.get("detached")})
                    expect(tag + "T8: no match is an empty result",
                           said.get("no_match") == "0", {"said": said.get("no_match")})
                    expect(tag + "T9: an empty name answers empty and does not throw",
                           said.get("empty_argument") == "length 0",
                           {"said": said.get("empty_argument")})
                    expect(tag + "T10: and so does a name the engine cannot parse",
                           said.get("unparseable_argument") == "length 0",
                           {"said": said.get("unparseable_argument")})
                    expect(tag + "L1: the result is a plain array, not an HTMLCollection",
                           said.get("plain_array") == "true|undefined|undefined",
                           {"said": said.get("plain_array")})
                    expect(tag + "T11: each call allocates its own array",
                           said.get("fresh_each_call") == "true|true",
                           {"said": said.get("fresh_each_call")})
                    expect(tag + "L2: and that array is not live",
                           said.get("not_live") == "true|true",
                           {"said": said.get("not_live")})
                    expect(tag + "T12: it adds no authority — inert inside a dispatch",
                           said.get("inert_during_dispatch") == "ran 1|unchanged true",
                           {"said": said.get("inert_during_dispatch")})

                    parent = host.ok("target.open",
                                     {"session": session,
                                      "url": origin + "/parent.html"})["target"]
                    frames = (host.ok("target.inspect", {"target": parent}).get("frames") or [])
                    child_frame = frames[1]["frame"] if len(frames) > 1 else "frame_absent"
                    child = host.call("target.snapshot",
                                      {"target": parent, "format": "semantic",
                                       "max_bytes": 65536, "max_nodes": 64,
                                       "frame": child_frame})
                    names = ([n.get("name") for n in child["result"]["nodes"]]
                             if child.get("ok") else [])
                    expect(tag + "T13: the child realm is untouched by this slice",
                           child.get("ok")
                           and any("embedded static" in (n or "") for n in names),
                           {"nodes": len(names)})

                    many = host.ok("target.open",
                                   {"session": session,
                                    "url": origin + "/many.html"})["target"]
                    counted = [n.get("name") for n in host.ok(
                        "target.snapshot",
                        {"target": many, "format": "semantic",
                         "max_bytes": 65536, "max_nodes": 64})["nodes"]
                        if (n.get("name") or "").startswith("counted ")]
                    expect(tag + "T14: two hundred calls answer the same way every time",
                           counted == ["counted 200"], {"said": counted})
                    host.ok("target.close", {"target": many})
                    host.ok("target.close", {"target": parent})
                    host.ok("target.close", {"target": target})
                    host.ok("session.close", {"session": session})
                    owners = host.ok("memory.report", {})["owners"]
                    live = (owners["script_realms"]["malloc_bytes"]
                            + owners["targets"]["fixture_bytes"])
                    expect(tag + "T15: the owners come back when the targets close",
                           live <= baseline + OWNER_RETURN_BYTES,
                           {"baseline": baseline, "after": live})
                finally:
                    if host.killed:
                        killed_hosts.append({"allocator": allocator})
                    host.finish()
                    killed_hosts.extend({"allocator": allocator, **t} for t in host.timeouts)
    finally:
        server.shutdown()

    receipt = {
        "court": "native-dom getElementsByTagName (control 0.0.2)",
        "host_sha256": hashlib.sha256(Path(args.binary).read_bytes()).hexdigest(),
        "adds": {"authority": None, "reentrancy": None, "lifecycle": None,
                 "note": "it delegates to querySelectorAll, which a page can already call"},
        "recorded_losses": ["a plain array rather than a live HTMLCollection",
                            "a namespace-ish name such as a:b answers empty"],
        "checks": checks,
        "checks_passed": sum(1 for c in checks if c["passed"]),
        "checks_total": len(checks),
        "passed": all(c["passed"] for c in checks) and not killed_hosts,
        "hosts_killed": killed_hosts,
        "limitations": [
            "design-frozen court: it fails until the method exists",
            "getElementsByClassName stays on hold and its own frozen court stays failing; H3 keeps it out of this slice",
            "three criteria read the shipped sources beside this court rather than the binary, so they are repo-local by design",
            "the main-only slack bound and the M1/M2 floors are measured by the shim-footprint and child-frame courts on the same binary",
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
