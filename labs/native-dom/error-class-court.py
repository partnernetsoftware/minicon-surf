#!/usr/bin/env python3
"""The frozen court for the base's own error classes.

Frozen from `error-class-audit-0.0.1.md` §9 before the base changes, and
failing until the capture and the throw sites exist.

What it holds the host to: the selector engine's four page entry points and
`removeChild` throw the engine's own `DOMException` with the standard name,
the legacy code and the `[object DOMException]` tag; the constructor is
captured at base load, so a page that replaces `globalThis.DOMException` or
`globalThis.Error` first changes nothing; `classList`, `cloneNode` and the
timers keep exactly the shapes they have, because the scope was ruled to stop
at two sites; the redaction still holds, so a class change cannot be mistaken
for a leak repair; and a child realm still runs no scripts, which is why it
pays for this slice without being able to observe it.

Three of its criteria read the shipped sources rather than the binary, so they
fail on an old binary for the right reason and pass on a new one before it is
even run. The runtime criteria are the discriminating half.

Strictly headless: no surface binary, no window, no AppKit, and it refuses to
run with the visible-court variable set. One hermetic loopback origin, both
allocators.

Groups: sources, selector, removeChild, capture, unchanged, message, child,
redaction.
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
# The two the slice serves, and the codes the engine keeps for them.
SELECTOR_NAME, SELECTOR_CODE = "SyntaxError", "12"
REMOVE_NAME, REMOVE_CODE = "NotFoundError", "8"
DOM_TAG = "[object DOMException]"
ERROR_TAG = "[object Error]"
# An opaque token, for the same reason the redaction court's are opaque: a
# readable one collides with the vocabulary of the thing scanning for it.
LEAK_TOKEN = "hzt4-62qwvm-3081"


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


def caught(expression):
    """Report a throw as name|code|tag|isError, or the fact that it did not."""
    return ("(function(){try{" + expression + ";return 'no throw';}catch(e){"
            "return e.name+'|'+String(e.code)+'|'+Object.prototype.toString.call(e)"
            "+'|'+(e instanceof Error);}})()")


PROBES = [
    ("selector_query", caught("document.querySelector('div:hover')")),
    ("selector_all", caught("document.querySelectorAll('div:hover')")),
    ("selector_closest", caught("document.body.closest('div:hover')")),
    ("selector_matches", caught("document.body.matches('div:hover')")),
    ("remove_child", caught("document.body.removeChild(document.createElement('p'))")),
    # The capture: the page installs its own constructor first, and the base
    # must not be using the global it just replaced.
    ("capture_domexception",
     "(function(){var real=DOMException;function Fake(){}"
     "globalThis.DOMException=Fake;var out;"
     "try{document.querySelector('div:hover');}catch(e){"
     "out=e.name+'|'+String(e.code)+'|'+Object.prototype.toString.call(e)"
     "+'|isFake:'+(e instanceof Fake);}"
     "globalThis.DOMException=real;return out;})()"),
    ("capture_error",
     "(function(){var real=Error;function Fake(){}"
     "globalThis.Error=Fake;var out;"
     "try{document.querySelector('div:hover');}catch(e){"
     "out=e.name+'|'+String(e.code)+'|'+Object.prototype.toString.call(e);}"
     "globalThis.Error=real;return out;})()"),
    # Scope stops at two sites: these keep exactly what they have.
    ("classlist_empty", caught("document.body.classList.add('')")),
    ("classlist_space", caught("document.body.classList.add('a b')")),
    ("clone_unmodelled", caught("document.body.cloneNode.call({nodeType:7},false)")),
    ("timer_string", caught("setTimeout('1+1',0)")),
    # The name is the name now, not a prefix inside the message.
    ("message_prefix",
     "(function(){try{document.querySelector('div:hover');}catch(e){"
     "return String(e.message.indexOf('SyntaxError'));}})()"),
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
    PROBE_PAGE = ("<!doctype html><html><body><main>" + slots + "</main><script>"
                  + script + "</script></body></html>").encode()
    # A child whose script would announce itself if it ran.
    CHILD = ("<!doctype html><html><body><main><p id=c>embedded static</p><script>"
             "document.getElementById('c').textContent='embedded dynamic';"
             "</script></main></body></html>").encode()
    PARENT = b"<!doctype html><html><body><main><iframe src='/child.html'></iframe></main></body></html>"
    # An uncaught selector refusal built from a value the page holds.
    CRASH = ("<!doctype html><html><body><main><input id=h value='" + LEAK_TOKEN + "'>"
             "</main><script>document.querySelector('[value=\"'"
             "+document.getElementById('h').value+'\"]:x');</script></body></html>").encode()

    class Handler(network.Handler):
        def do_GET(self):
            path, _, _query = self.path.partition("?")
            network.Handler.hits.append(path)
            pages = {"/probe.html": PROBE_PAGE, "/child.html": CHILD,
                     "/parent.html": PARENT, "/crash.html": CRASH}
            if path in pages:
                return self.reply(200, pages[path])
            return super().do_GET()

    server = network.Server(("127.0.0.1", 0), Handler)
    origin = f"http://127.0.0.1:{server.server_address[1]}"
    threading.Thread(target=server.serve_forever, daemon=True).start()
    checks = []
    killed_hosts = []

    def expect(name, condition, detail=None):
        checks.append({"check": name, "passed": bool(condition),
                       **({"detail": detail} if detail is not None else {})})

    # S1-S3: read out of the shipped sources, so drift in the base is caught
    # even by a run that never opens a document.
    base = BASE_JS.read_text()
    main_source = MAIN_JS.read_text()
    captures = len(re.findall(r"const\s+DOMExceptionCtor\s*=\s*DOMException\s*;", base))
    expect("S1: the base captures the constructor exactly once, at load",
           captures == 1, {"captures": captures})
    stale = (base.count('new Error("SyntaxError: selector')
             + base.count('new Error("NotFoundError")'))
    expect("S2: no throw site in the base still spells a name inside a message",
           stale == 0, {"stale_sites": stale})
    expect("S3: the slice is base-only, so the main extension captures nothing",
           "DOMExceptionCtor" not in main_source,
           {"named_in_main": "DOMExceptionCtor" in main_source})

    try:
        for allocator in ("system", "arena"):
            tag = f"[{allocator}] "
            with tempfile.TemporaryDirectory(prefix="minicon-surf-errclass-") as directory:
                host = JOBS.Supervised(args.binary, directory, origin, allocator)
                try:
                    profile = host.ok("profile.create", {"persistence": "ephemeral"})["profile"]
                    session = host.ok("session.open", {"profile": profile})["session"]
                    target = host.ok("target.open",
                                     {"session": session,
                                      "url": origin + "/probe.html"})["target"]
                    snapshot = host.ok("target.snapshot",
                                       {"target": target, "format": "semantic",
                                        "max_bytes": 131072, "max_nodes": 128})
                    said = {}
                    for node in snapshot["nodes"]:
                        text = node.get("name") or ""
                        if node.get("role") == "text" and "=" in text:
                            key, _, value = text.partition("=")
                            said[key] = value
                    want_selector = f"{SELECTOR_NAME}|{SELECTOR_CODE}|{DOM_TAG}|true"

                    # E1, E2: every selector entry point, one answer.
                    for name in ("selector_query", "selector_all",
                                 "selector_closest", "selector_matches"):
                        expect(tag + f"E1/E2: {name} throws the standard selector refusal",
                               said.get(name) == want_selector, {"said": said.get(name)})

                    # E3: removeChild.
                    expect(tag + "E3: removeChild throws NotFoundError with its legacy code",
                           said.get("remove_child")
                           == f"{REMOVE_NAME}|{REMOVE_CODE}|{DOM_TAG}|true",
                           {"said": said.get("remove_child")})

                    # E4, E5: the capture, against a page that moved first.
                    expect(tag + "E4: a page that replaces DOMException does not change the throw",
                           said.get("capture_domexception")
                           == f"{SELECTOR_NAME}|{SELECTOR_CODE}|{DOM_TAG}|isFake:false",
                           {"said": said.get("capture_domexception")})
                    expect(tag + "E5: nor does a page that replaces Error",
                           said.get("capture_error")
                           == f"{SELECTOR_NAME}|{SELECTOR_CODE}|{DOM_TAG}",
                           {"said": said.get("capture_error")})

                    # E6, E7: the scope stops where it was ruled to stop.
                    expect(tag + "E6: classList keeps the vocabulary it has",
                           said.get("classlist_empty") == f"SyntaxError|undefined|{ERROR_TAG}|true"
                           and said.get("classlist_space")
                           == f"InvalidCharacterError|undefined|{ERROR_TAG}|true",
                           {"empty": said.get("classlist_empty"),
                            "space": said.get("classlist_space")})
                    expect(tag + "E7: an unmodelled node kind and a string timer stay TypeErrors",
                           said.get("clone_unmodelled", "").startswith("TypeError|undefined|")
                           and said.get("timer_string", "").startswith("TypeError|undefined|"),
                           {"clone": said.get("clone_unmodelled"),
                            "timer": said.get("timer_string")})

                    # E8: the name is the name, not a prefix in the message.
                    expect(tag + "E8: the message no longer carries the name as a prefix",
                           said.get("message_prefix") == "-1",
                           {"said": said.get("message_prefix")})

                    # E9: the child pays for this slice and cannot observe it.
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
                    expect(tag + "E9: a child still runs no scripts, so it cannot observe the class",
                           child.get("ok")
                           and any("embedded static" in (n or "") for n in names)
                           and not any("embedded dynamic" in (n or "") for n in names),
                           {"nodes": len(names)})

                    # E10: the redaction still holds over the new class.
                    crashed = host.call("target.open",
                                        {"session": session, "url": origin + "/crash.html"})
                    body = json.dumps(crashed, sort_keys=True)
                    error = (crashed.get("error") or {})
                    details = error.get("details") or {}
                    expect(tag + "E10: an uncaught refusal still says the host's fixed word only",
                           LEAK_TOKEN not in body
                           and details.get("engine_error") == "a script threw"
                           and error.get("code") == "target_crashed",
                           {"token_present": LEAK_TOKEN in body,
                            "engine_error": details.get("engine_error"),
                            "code": error.get("code")})
                finally:
                    if host.killed:
                        killed_hosts.append({"allocator": allocator})
                    host.finish()
                    killed_hosts.extend({"allocator": allocator, **t} for t in host.timeouts)
    finally:
        server.shutdown()

    receipt = {
        "court": "native-dom base error classes (control 0.0.2)",
        "host_sha256": hashlib.sha256(Path(args.binary).read_bytes()).hexdigest(),
        "expected": {"selector": f"{SELECTOR_NAME}/{SELECTOR_CODE}",
                     "remove_child": f"{REMOVE_NAME}/{REMOVE_CODE}",
                     "unchanged": ["classList", "dispatch", "localStorage", "cloneNode",
                                   "setTimeout"]},
        "checks": checks,
        "checks_passed": sum(1 for c in checks if c["passed"]),
        "checks_total": len(checks),
        "passed": all(c["passed"] for c in checks) and not killed_hosts,
        "hosts_killed": killed_hosts,
        "limitations": [
            "design-frozen court: it fails until the base captures the constructor and throws it",
            "three criteria read the shipped sources beside this court rather than the binary, so they are repo-local by design",
            "the M1 and M2 floors and the main-only slack are measured by the child-frame and shim-footprint courts on the same binary",
            "the child-divergence criterion shows a child cannot observe the class; the bytes it pays are measured elsewhere",
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
