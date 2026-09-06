#!/usr/bin/env python3
"""The frozen court for `getElementsByClassName`.

Frozen from `browser-gap-triage-0.0.3.md` §7 before the extension changes, and
failing until the method exists.

One member has to serve both call surfaces — a page calls it on `document` and
on an element — because two members would not fit the slack this slice was
given. `querySelectorAll` lives on the base's `Node` and both `Element` and
`Document` extend it, so the court checks both surfaces and, from the sources,
that only one definition exists.

The rest is the shape the ruling fixed: a space-separated class list means all
of them, the argument is trimmed, an empty one is an empty result and not a
throw, matching is case-sensitive, the result is a plain array in document
order like `querySelectorAll` and just as non-live, and a class name this
host's selector engine cannot express comes back empty rather than throwing.

The method adds no authority, no reentrancy and no lifetime semantics, so
there is nothing of that kind to pin — which the receipt says out loud rather
than leaving as an absence.

Strictly headless: no surface binary, no window, no AppKit, and it refuses to
run with the visible-court variable set. One hermetic loopback origin, both
allocators.

Groups: sources, surfaces, matching, shape, losses, realm, owners.
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
    ("document_surface",
     "document.getElementsByClassName('alpha').map(function(e){return e.id;}).join(',')"),
    ("element_surface",
     "document.getElementById('box').getElementsByClassName('alpha')"
     ".map(function(e){return e.id;}).join(',')"),
    ("document_order",
     "document.getElementsByClassName('ordered').map(function(e){return e.id;}).join(',')"),
    ("case_sensitive",
     "String(document.getElementsByClassName('Alpha').length)"),
    ("multi_class_is_and",
     "document.getElementsByClassName('alpha beta').map(function(e){return e.id;}).join(',')"),
    ("whitespace_trimmed",
     "document.getElementsByClassName('   alpha   ').map(function(e){return e.id;}).join(',')"),
    ("duplicate_names",
     "document.getElementsByClassName('alpha alpha').map(function(e){return e.id;}).join(',')"),
    ("empty_argument",
     "(function(){try{var r=document.getElementsByClassName('');"
     "return 'length '+r.length;}catch(e){return 'threw:'+e.name;}})()"),
    ("whitespace_argument",
     "(function(){try{var r=document.getElementsByClassName('   ');"
     "return 'length '+r.length;}catch(e){return 'threw:'+e.name;}})()"),
    ("no_match",
     "String(document.getElementsByClassName('nothing-here').length)"),
    ("detached_subtree",
     "(function(){var root=document.createElement('div');"
     "var kid=document.createElement('p');kid.className='alpha';root.append(kid);"
     "return 'found '+root.getElementsByClassName('alpha').length;})()"),
    ("plain_array_like_qsa",
     "(function(){var r=document.getElementsByClassName('alpha');"
     "var q=document.querySelectorAll('.alpha');"
     "return String(Array.isArray(r))+'|'+String(Array.isArray(q))"
     "+'|'+String(r.length===q.length);})()"),
    ("not_live",
     "(function(){var r=document.getElementsByClassName('alpha');var before=r.length;"
     "var extra=document.createElement('p');extra.className='alpha';"
     "document.getElementById('box').append(extra);"
     "return 'held '+before+'|still '+r.length"
     "+'|fresh '+document.getElementsByClassName('alpha').length;})()"),
    ("inexpressible_name",
     "(function(){try{var r=document.getElementsByClassName('a.b');"
     "return 'length '+r.length;}catch(e){return 'threw:'+e.name;}})()"),
    # It reads the tree and does nothing else: no listener runs, no event is
    # minted, and calling it inside a dispatch changes nothing.
    ("inert_during_dispatch",
     "(function(){var bus=document.getElementById('box');var seen=0;"
     "bus.addEventListener('probe',function(){seen+=1;"
     "document.getElementsByClassName('alpha');});"
     "bus.dispatchEvent(new Event('probe'));"
     "return 'ran '+seen+'|still '+document.getElementsByClassName('alpha').length;})()"),
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
    # `first` and `second` carry both classes; `third` only one; `outside`
    # sits beyond the element surface's scope.
    PAGE = ("<!doctype html><html><body>"
            "<p id=\"outside\" class=\"alpha ordered\">outside</p>"
            "<main><div id=\"box\">"
            "<p id=\"first\" class=\"alpha beta ordered\">one</p>"
            "<p id=\"second\" class=\"beta alpha ordered\">two</p>"
            "<p id=\"third\" class=\"alpha\">three</p>"
            "<p id=\"fourth\" class=\"Alpha\">four</p>"
            "</div>" + slots + "</main><script>" + script + "</script></body></html>").encode()
    MANY = ("<!doctype html><html><body><main><p id=m class=alpha>many</p></main><script>"
            # Guarded so a host without the method fails the criterion
            # instead of crashing the court on the fixture.
            "var n=0;try{for(var i=0;i<200;i+=1){"
            "n+=document.getElementsByClassName('alpha').length;}"
            "document.getElementById('m').textContent='counted '+n;}catch(e){}"
            "</script></body></html>").encode()
    CHILD = ("<!doctype html><html><body><main><p id=c class=alpha>embedded static</p>"
             "</main></body></html>").encode()
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
    definitions = len(re.findall(r"getElementsByClassName\s*=", main_source))
    expect("H1: one definition serves both surfaces, and it is in the extension",
           definitions == 1 and "getElementsByClassName" not in base,
           {"definitions_in_main": definitions,
            "in_base": "getElementsByClassName" in base})
    expect("H2: the handle's key set is unchanged by this slice",
           handle_keys(base) == HANDLE_KEYS,
           {"unexpected": sorted(handle_keys(base) - HANDLE_KEYS),
            "missing": sorted(HANDLE_KEYS - handle_keys(base))})

    try:
        for allocator in ("system", "arena"):
            tag = f"[{allocator}] "
            with tempfile.TemporaryDirectory(prefix="minicon-surf-classname-") as directory:
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

                    expect(tag + "C1: the document surface finds every match, in document order",
                           said.get("document_surface") == "outside,first,second,third",
                           {"said": said.get("document_surface")})
                    expect(tag + "C2: the element surface finds only its own descendants",
                           said.get("element_surface") == "first,second,third",
                           {"said": said.get("element_surface")})
                    expect(tag + "C3: order is the document's, not the query's",
                           said.get("document_order") == "outside,first,second",
                           {"said": said.get("document_order")})
                    expect(tag + "C4: matching is case-sensitive",
                           said.get("case_sensitive") == "1",
                           {"said": said.get("case_sensitive")})
                    expect(tag + "C5: a class list means all of them, not any",
                           said.get("multi_class_is_and") == "first,second",
                           {"said": said.get("multi_class_is_and")})
                    expect(tag + "C6: the argument is trimmed",
                           said.get("whitespace_trimmed") == "outside,first,second,third",
                           {"said": said.get("whitespace_trimmed")})
                    expect(tag + "C7: a repeated name is not a different query",
                           said.get("duplicate_names") == "outside,first,second,third",
                           {"said": said.get("duplicate_names")})
                    expect(tag + "C8: an empty or blank argument is an empty result, not a throw",
                           said.get("empty_argument") == "length 0"
                           and said.get("whitespace_argument") == "length 0",
                           {"empty": said.get("empty_argument"),
                            "blank": said.get("whitespace_argument")})
                    expect(tag + "C9: no match is an empty result",
                           said.get("no_match") == "0", {"said": said.get("no_match")})
                    expect(tag + "C10: it works inside a detached subtree",
                           said.get("detached_subtree") == "found 1",
                           {"said": said.get("detached_subtree")})
                    expect(tag + "C11: the result is a plain array, like querySelectorAll's",
                           said.get("plain_array_like_qsa") == "true|true|true",
                           {"said": said.get("plain_array_like_qsa")})
                    expect(tag + "C12: and it is not live — the recorded loss, pinned",
                           said.get("not_live") == "held 4|still 4|fresh 5",
                           {"said": said.get("not_live")})
                    expect(tag + "C13: a name the engine cannot express is empty, not a throw",
                           said.get("inexpressible_name") == "length 0",
                           {"said": said.get("inexpressible_name")})
                    expect(tag + "C14: it adds no authority — inert inside a dispatch",
                           said.get("inert_during_dispatch") == "ran 1|still 4",
                           {"said": said.get("inert_during_dispatch")})

                    # A child realm does not get it, and cannot: it runs no scripts.
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
                    expect(tag + "C15: the child realm is untouched by this slice",
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
                    expect(tag + "C16: two hundred calls answer the same way every time",
                           counted == ["counted 200"], {"said": counted})
                    host.ok("target.close", {"target": many})
                    host.ok("target.close", {"target": parent})
                    host.ok("target.close", {"target": target})
                    host.ok("session.close", {"session": session})
                    owners = host.ok("memory.report", {})["owners"]
                    live = (owners["script_realms"]["malloc_bytes"]
                            + owners["targets"]["fixture_bytes"])
                    expect(tag + "C17: the owners come back when the targets close",
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
        "court": "native-dom getElementsByClassName (control 0.0.2)",
        "host_sha256": hashlib.sha256(Path(args.binary).read_bytes()).hexdigest(),
        "adds": {"authority": None, "reentrancy": None, "lifecycle": None,
                 "note": "it reads the tree through the selector engine a page can already call"},
        "checks": checks,
        "checks_passed": sum(1 for c in checks if c["passed"]),
        "checks_total": len(checks),
        "passed": all(c["passed"] for c in checks) and not killed_hosts,
        "hosts_killed": killed_hosts,
        "limitations": [
            "design-frozen court: it fails until the method exists",
            "the live HTMLCollection is a loss inherited from querySelectorAll, pinned here as a plain non-live array rather than newly introduced",
            "a class name the selector engine cannot express answers empty; the standard method would match it, and that is a recorded loss",
            "two criteria read the shipped sources beside this court rather than the binary, so they are repo-local by design",
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
