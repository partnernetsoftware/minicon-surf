#!/usr/bin/env python3
"""A measurement probe, not a court: is the round-D attribute store actually
the host's, or only stored by the host?

`a0482ed` moved each element's tag and attribute map into a closure-owned
`WeakMap` read through non-writable globals, and closed F1 and F2 against a
page that patches intrinsics. But `Element.prototype.__attrs` is a
page-observable accessor whose getter returns **the record's own Map object**.
`fail-open-triage-probe.py` already records that a page can reach it
(`attrs=map`); no arm has ever asked whether a page can *write through* it.

Six arms, each an honest control beside the write it is paired with:

  A1  no patch                      the declared POST submits as a POST
  A2  __attrs.set('method','get')   does the host submit it as a GET?
  A3  __attrs.delete('method')      does removing the fact reach the default?
  A4  __attrs.set('action', ...)    does the form submit somewhere else?
  A5  __attrs.delete('target')      is a named-target link then activated?
  A6  __attrs.set('href', ...)      is a plain link then activated elsewhere?

A2/A3 are F1's shape, A5/A6 are F2's, reached by a plain method call on an
object the page is handed rather than by patching an intrinsic. The server
records the **method and path** it was asked for, which is the only thing that
distinguishes a host that read its own fact from one that read the page's.

It scores nothing and freezes nothing. Strictly headless: no surface binary,
no window, no AppKit, and it refuses to run with the visible-court variable
set. One hermetic loopback origin; nothing is fetched from outside this file.
Form values and built query strings are page data: only whether a path was
asked for is recorded, never a value.
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

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "protocol"))
import check_contract  # noqa: E402

VISIBLE_ENV = "MINICON_SURF_ALLOW_VISIBLE_COURT"
FIXTURE_ROOT = ROOT / "labs" / "court" / "fixtures"


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

# Every patch is an ordinary method call on an object the page was handed. None
# of them replaces an intrinsic, which is the point: the round-D defence is
# aimed at intrinsic patching.
PATCHES = {
    "none": "",
    "set_method": "document.getElementById('postform').__attrs.set('method','get');",
    "delete_method": "document.getElementById('postform').__attrs.delete('method');",
    "set_action": "document.getElementById('postform').__attrs.set('action','/moved.html');",
    "delete_target": "document.getElementById('named').__attrs.delete('target');",
    "set_href": "document.getElementById('plain').__attrs.set('href','/moved.html');",
    # The honest controls. A page is entitled to mutate its own DOM through the
    # ordinary API; if these produce the same outcomes as the writes above, the
    # store is not being defeated at all and the host is simply reading live
    # DOM state. Pairing every arm above with the honest route it imitates is
    # the only thing that separates a fail-open from correct behaviour.
    "honest_set_method": "document.getElementById('postform').setAttribute('method','get');",
    "honest_remove_method": "document.getElementById('postform').removeAttribute('method');",
    "honest_remove_target": "document.getElementById('named').removeAttribute('target');",
    "honest_set_href": "document.getElementById('plain').setAttribute('href','/moved.html');",
}


def page(patch):
    """One document for every arm, so a difference in a recorded column is the
    arm's difference and not the fixture's."""
    return (
        "<!doctype html><html><body><main>"
        "<p id='mark'>marker</p>"
        "<p id='echo'>unset</p>"
        "<form id='postform' method='post' action='/landed.html'>"
        "<input id='p' type='text' name='p' value='typed'>"
        "<button id='postgo' type='submit'>send</button></form>"
        "<a id='named' href='/landed.html' target='somewhere'>named</a>"
        "<a id='plain' href='/landed.html'>plain</a>"
        "</main><script>(function(){"
        # Anti-vacuity: the page reports whether its write ran and what the
        # store reads back as. Without this, a refusal below could pass on a
        # page whose script never executed at all.
        "var note = 'ran';"
        "try { " + patch + " } catch (e) { note = 'threw:' + (e && e.name); }"
        "try { var f = document.getElementById('postform');"
        " note += '|method=' + f.getAttribute('method');"
        " note += '|action=' + f.getAttribute('action');"
        " document.getElementById('echo').textContent = note;"
        "} catch (e2) { try { document.getElementById('echo').textContent ="
        " 'echo-threw'; } catch (e3) {} }"
        "})();</script></body></html>"
    ).encode()


class Host:
    def __init__(self, binary, directory, origin):
        Path(directory).mkdir(parents=True, exist_ok=True)
        environment = dict(os.environ)
        for knob in ("MINICON_SURF_NATIVE_REALM_ZONE", "MINICON_SURF_NATIVE_REALM_ARENA",
                     "MINICON_SURF_PROFILE_STORE", VISIBLE_ENV):
            environment.pop(knob, None)
        command = [binary, "serve", "--stdio", "--fixture-root", str(FIXTURE_ROOT),
                   "--config-dir", str(Path(directory) / "config"), "--allow-origin", origin]
        self.process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                        stderr=subprocess.DEVNULL, text=True, env=environment)
        self.counter = 0

    def raw(self, operation, arguments, validate=True):
        self.counter += 1
        request = {"protocol": "minicon-surf.control", "version": "0.0.2",
                   "request_id": f"req_as_{self.counter}", "deadline_ms": 30000,
                   "operation": operation, "arguments": arguments}
        if validate:
            check_contract.validate_request(request)
        self.process.stdin.write(json.dumps(request) + "\n")
        self.process.stdin.flush()
        line = self.process.stdout.readline()
        if not line:
            return {"ok": False, "error": {"code": "host_exited"}}
        return json.loads(line)

    def ok(self, operation, arguments):
        answer = self.raw(operation, arguments)
        if not answer.get("ok"):
            raise RuntimeError(f"{operation}: {answer.get('error')}")
        return answer["result"]

    def finish(self):
        try:
            self.process.stdin.close()
            self.process.wait(timeout=15)
        except Exception:
            self.process.kill()


def outcome(answer):
    if not answer.get("ok"):
        error = answer.get("error") or {}
        reason = (error.get("details") or {}).get("reason")
        return error.get("code", "?") + (f"/{reason}" if reason else "")
    result = answer.get("result") or {}
    for key in ("applied", "revision"):
        if key in result:
            return f"ok:{key}={result[key]}"
    return "ok"


def run_arm(binary, origin, seen, patch_name, workspace):
    """One host per act: a committed navigation replaces the document the
    references point into, and a stale reference is a refusal for a reason
    that has nothing to do with the rule."""
    record = {"patch": patch_name}

    def one_act(tag, dom_id, action):
        host = Host(binary, Path(workspace) / f"{patch_name}-{tag}", origin)
        profile = host.ok("profile.create", {"persistence": "ephemeral"})["profile"]
        session = host.ok("session.open", {"profile": profile})["session"]
        target = host.ok("target.open", {"session": session,
                                         "url": origin + "/page.html"})["target"]
        snapshot = host.raw("target.snapshot", {"target": target, "format": "semantic",
                                               "max_bytes": 65536, "max_nodes": 64})
        nodes = (snapshot.get("result") or {}).get("nodes") or []
        by_id = {n.get("dom_id"): n for n in nodes if n.get("dom_id")}
        # The page's own report, so a silent no-op arm is visible as one.
        echo = next((n.get("name") for n in nodes if n.get("dom_id") == "echo"), None)
        result = {"in_snapshot": dom_id in by_id, "page_echo": echo}
        if dom_id in by_id:
            del seen[:]
            answer = host.raw("target.act", {"target": target,
                                             "reference": by_id[dom_id]["reference"],
                                             "action": action}, validate=False)
            result["outcome"] = outcome(answer)
            result["server_saw"] = list(seen)
        host.finish()
        return result

    record["submit_postform"] = one_act("submit", "postgo", {"kind": "submit"})
    record["activate_named"] = one_act("named", "named", {"kind": "click"})
    record["activate_plain"] = one_act("plain", "plain", {"kind": "click"})
    return record


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--binary", required=True)
    parser.add_argument("--receipt", required=True)
    args = parser.parse_args()
    if os.environ.get(VISIBLE_ENV):
        print(json.dumps({"ran": False, "reason": "the visible-court variable is set"}))
        return 1

    network = RETENTION.load_network_module()
    seen = []
    current = {"patch": "none"}

    with tempfile.TemporaryDirectory(prefix="minicon-surf-as-") as workspace:

        class Handler(network.Handler):
            def _note(self, verb):
                path, _, _query = self.path.partition("?")
                # The query is page data. Only the verb and the path are kept.
                seen.append(f"{verb} {path}")
                return path

            def do_GET(self):
                path = self._note("GET")
                if path in ("/landed.html", "/moved.html"):
                    return self.reply(200, b"<!doctype html><html><body><p>landed</p></body></html>",
                                      "text/html")
                return self.reply(200, page(PATCHES[current["patch"]]), "text/html")

            def do_POST(self):
                length = int(self.headers.get("Content-Length") or 0)
                if length:
                    self.rfile.read(length)
                self._note("POST")
                return self.reply(200, b"<!doctype html><html><body><p>landed</p></body></html>",
                                  "text/html")

        server = network.Server(("127.0.0.1", 0), Handler)
        origin = f"http://127.0.0.1:{server.server_address[1]}"
        threading.Thread(target=server.serve_forever, daemon=True).start()

        rows = []
        try:
            for name in PATCHES:
                current["patch"] = name
                rows.append(run_arm(args.binary, origin, seen, name, workspace))
        finally:
            server.shutdown()

    receipt = {
        "probe": "attribute store: can a page write through the __attrs accessor?",
        "binary_sha256": hashlib.sha256(Path(args.binary).read_bytes()).hexdigest(),
        "rows": rows,
    }
    Path(args.receipt).write_text(json.dumps(receipt, indent=2) + "\n")
    print(json.dumps({"ran": True, "arms": len(rows), "receipt": args.receipt}))
    return 0


sys.exit(main())
