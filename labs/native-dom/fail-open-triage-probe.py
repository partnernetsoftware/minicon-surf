#!/usr/bin/env python3
"""A measurement probe, not a court: what F1, F2 and F5 actually buy a page.

`uncaptured-intrinsic-audit-0.0.1.md` §3.1 named five fail-opens. Three were
closed at `7b9e11a`; these three were kept out deliberately, and this probe
asks each of them the questions a ruling needs, separately:

  F1  `methodOf` (`main.rs:591`)          a POST form submitted as a GET
  F2  `targetOf` (`main.rs:572`)          a named-target link activated in frame
  F5  `download_probe_script` (`:775`)    a non-link div downloaded as a link

Three arms per defect, not one. The **selective patch** shows the defect. The
**honest control** shows whether a page could have had the same outcome by
writing different markup, which is what separates a broken refusal vocabulary
from a real escalation. The **escalation probes** ask whether the defect
reaches anything the honest markup could not: a scheme, an origin, or a file.

A fourth arm asks whether the obvious host-side fix would even work: the realm
reading a raw attribute goes through `getAttribute`, which is built from
`String`, `toLowerCase` and `Map.prototype.get` -- all the page's.

It scores nothing and freezes nothing. Strictly headless: no surface binary, no
window, no AppKit, and it refuses to run with the visible-court variable set.
Two hermetic loopback origins and one temporary local file; nothing is fetched
from outside this file.
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

ASKED_FOR = b"the bytes the agent asked for"
LOCAL_SECRET = b"a local file the agent never named"

# Each patch lies about exactly one comparison and leaves the document
# working. A blanket replacement collapses the page's own snapshot and proves
# nothing; that was measured in the audit this probe follows.
PATCHES = {
    "none": "",
    # F1: the form's declared method reads back as the one the host models.
    "lower_post_get": (
        "var L = String.prototype.toLowerCase;"
        "String.prototype.toLowerCase = function () {"
        " return String(this) === 'post' ? 'get' : L.call(this); };"
    ),
    # F2: the link's declared target reads back as the current frame.
    "lower_target_self": (
        "var L2 = String.prototype.toLowerCase;"
        "String.prototype.toLowerCase = function () {"
        " return String(this) === 'somewhere' ? '_self' : L2.call(this); };"
    ),
    # F5: a div reads back as an anchor.
    "lower_div_anchor": (
        "var L3 = String.prototype.toLowerCase;"
        "String.prototype.toLowerCase = function () {"
        " return String(this) === 'DIV' ? 'a' : L3.call(this); };"
    ),
    # The dependency arm: `getAttribute` reads through `Map.prototype.get`, so
    # even the raw attribute a host-side check would want is the page's.
    "map_get_method": (
        "var MG = Map.prototype.get;"
        "Map.prototype.get = function (k) {"
        " var v = MG.call(this, k);"
        " return (k === 'method' && v === 'post') ? 'get' : v; };"
    ),
    # No intrinsic is replaced at all in these three. The base shim keeps an
    # element's tag and its attribute map as ordinary own properties, so the
    # question is whether the fact the host trusts is the page's to write
    # directly -- which would put these defects out of reach of any intrinsic
    # hardening.
    "prop_tag_name": (
        "var d = document.getElementById('fakedl');"
        "d.tagName = 'A'; d.localName = 'a';"
    ),
    "prop_attrs_method": (
        "var f = document.getElementById('postform');"
        "f.__attrs = new Map([['method', 'get'], ['action', '/landed.html'],"
        " ['id', 'postform']]);"
    ),
    "prop_attrs_target": (
        "var a = document.getElementById('named');"
        "a.__attrs = new Map([['href', '/landed.html'], ['id', 'named']]);"
    ),
    # Whether a host-owned reader, once it exists, is the page's to replace or
    # delete. On a build without the readers these are no-ops, which is what
    # makes them a fair before/after comparison.
    "override_readers": (
        "var d = document.getElementById('fakedl');"
        "d.tagName = 'A'; d.localName = 'a';"
        "var f = document.getElementById('postform');"
        "f.__attrs = new Map([['method', 'get'], ['action', '/landed.html'], ['id', 'postform']]);"
        "try { window.__mcsTag = function () { return 'a'; }; } catch (e) {}"
        "try { window.__mcsAttr = function () { return 'get'; }; } catch (e) {}"
        "try { delete window.__mcsTag; } catch (e) {}"
        "try { delete window.__mcsAttr; } catch (e) {}"
        "try { Object.defineProperty(window, '__mcsTag',"
        " { value: function () { return 'a'; } }); } catch (e) {}"
    ),
    "map_get_target": (
        "var MG2 = Map.prototype.get;"
        "Map.prototype.get = function (k) {"
        " var v = MG2.call(this, k);"
        " return (k === 'target' && v === 'somewhere') ? '_self' : v; };"
    ),
}


def page(patch, second_origin, local_file):
    """One document for every arm, so a difference in a recorded column is the
    arm's difference and not the fixture's. The honest controls sit beside the
    dishonest ones in the same document."""
    return (
        "<!doctype html><html><body><main>"
        "<p id='mark'>marker</p>"
        "<p id='echo'>unset</p>"
        # F1: the declared POST, and the honest GET beside it.
        "<form id='postform' method='post' action='/landed.html'>"
        "<input id='p' type='text' name='p' value='typed'>"
        "<button id='postgo' type='submit'>send</button></form>"
        "<form id='getform' method='get' action='/landed.html'>"
        "<input id='q' type='text' name='q' value='typed'>"
        "<button id='getgo' type='submit'>go</button></form>"
        # F2: the named target, and the honest untargeted link beside it.
        "<a id='named' href='/landed.html' target='somewhere'>named</a>"
        "<a id='plain' href='/landed.html'>plain</a>"
        # F5: the div that is not a link, and the honest anchor beside it.
        "<div id='fakedl' href='/asked.bin' download='x.bin'>not a link</div>"
        "<a id='realdl' href='/asked.bin' download='x.bin'>a real link</a>"
        # F5 escalation: addresses an honest anchor would also carry.
        "<div id='divjs' href='javascript:void(0)' download='x.bin'>div js</div>"
        "<a id='ajs' href='javascript:void(0)' download='x.bin'>anchor js</a>"
        "<div id='divfile' href='file://" + local_file + "' download='x.bin'>div file</div>"
        "<a id='afile' href='file://" + local_file + "' download='x.bin'>anchor file</a>"
        "<div id='divcross' href='" + second_origin + "/asked.bin' download='x.bin'>div cross</div>"
        "<a id='across' href='" + second_origin + "/asked.bin' download='x.bin'>anchor cross</a>"
        "</main><script>(function(){" + patch +
        # The page reports what its own write did: whether it threw, what the
        # field reads back as, and whether the value survived. A slice that
        # moves an internal field must not turn a page's harmless mistake into
        # a dead document.
        "try { var f2 = document.getElementById('postform');"
        " var kind = (f2.__attrs && typeof f2.__attrs.get === 'function') ? 'map' : 'other';"
        " document.getElementById('echo').textContent = 'attrs=' + kind;"
        "} catch (e) { try { document.getElementById('echo').textContent ="
        " 'threw'; } catch (e2) {} }"
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
                   "request_id": f"req_ft_{self.counter}", "deadline_ms": 30000,
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
    for key in ("byte_count", "applied", "revision"):
        if key in result:
            return f"ok:{key}={result[key]}"
    return "ok"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--binary", required=True)
    parser.add_argument("--receipt", required=True)
    args = parser.parse_args()
    if os.environ.get(VISIBLE_ENV):
        print(json.dumps({"ran": False, "reason": "the visible-court variable is set"}))
        return 1

    network = RETENTION.load_network_module()
    served = []
    current = {"patch": "none"}

    with tempfile.TemporaryDirectory(prefix="minicon-surf-ft-") as workspace:
        local_file = str(Path(workspace) / "local.bin")
        Path(local_file).write_bytes(LOCAL_SECRET)

        # A second loopback origin the host is never told to allow, so a
        # cross-origin download has somewhere real to be refused from.
        class Other(network.Handler):
            def do_GET(self):
                served.append("OTHER" + self.path)
                return self.reply(200, ASKED_FOR, "application/octet-stream")

        other = network.Server(("127.0.0.1", 0), Other)
        second_origin = f"http://127.0.0.1:{other.server_address[1]}"
        threading.Thread(target=other.serve_forever, daemon=True).start()

        class Handler(network.Handler):
            def do_GET(self):
                path, _, _query = self.path.partition("?")
                network.Handler.hits.append(path)
                served.append(path)
                if path == "/asked.bin":
                    return self.reply(200, ASKED_FOR, "application/octet-stream",
                                      [("Content-Disposition", 'attachment; filename="asked.bin"')])
                if path == "/landed.html":
                    return self.reply(200, b"<!doctype html><html><body><p>landed</p></body></html>",
                                      "text/html")
                return self.reply(200, page(PATCHES[current["patch"]], second_origin, local_file),
                                  "text/html")

        server = network.Server(("127.0.0.1", 0), Handler)
        origin = f"http://127.0.0.1:{server.server_address[1]}"
        threading.Thread(target=server.serve_forever, daemon=True).start()

        rows = []
        try:
            for name in PATCHES:
                current["patch"] = name
                rows.append(run_arm(args.binary, origin, served, name, workspace, local_file))
        finally:
            server.shutdown()
            other.shutdown()

    receipt = {
        "probe": "fail-open triage: F1 methodOf, F2 targetOf, F5 download node kind",
        "binary_sha256": hashlib.sha256(Path(args.binary).read_bytes()).hexdigest(),
        "local_file_bytes": len(LOCAL_SECRET),
        "rows": rows,
    }
    Path(args.receipt).write_text(json.dumps(receipt, indent=2) + "\n")
    print(json.dumps({"ran": True, "arms": len(rows), "receipt": args.receipt}))
    return 0


def run_arm(binary, origin, served, patch_name, workspace, local_file):
    """One host per arm. Each act gets a fresh host, because a committed
    navigation replaces the document the references point into and a stale
    reference is a refusal for a reason that has nothing to do with the rule."""
    record = {"patch": patch_name}

    def fresh(tag):
        host = Host(binary, Path(workspace) / f"{patch_name}-{tag}", origin)
        profile = host.ok("profile.create", {"persistence": "ephemeral"})["profile"]
        session = host.ok("session.open", {"profile": profile})["session"]
        target = host.ok("target.open", {"session": session,
                                         "url": origin + "/page.html"})["target"]
        snapshot = host.raw("target.snapshot", {"target": target, "format": "semantic",
                                                "max_bytes": 65536, "max_nodes": 64})
        nodes = (snapshot.get("result") or {}).get("nodes") or []
        by_id = {n.get("dom_id"): n for n in nodes if n.get("dom_id")}
        return host, session, target, by_id, nodes

    def one_act(tag, dom_id, action, want_ledger=False):
        host, session, target, by_id, nodes = fresh(tag)
        result = {"in_snapshot": dom_id in by_id}
        if dom_id in by_id:
            del served[:]
            answer = host.raw("target.act", {"target": target,
                                             "reference": by_id[dom_id]["reference"],
                                             "action": action}, validate=False)
            result["outcome"] = outcome(answer)
            result["fetched"] = list(served)
            if answer.get("ok"):
                blob = (answer.get("result") or {}).get("bytes_base64")
                if blob:
                    import base64
                    payload = base64.b64decode(blob)
                    result["got_asked_for"] = payload == ASKED_FOR
                    result["got_local_file"] = payload == LOCAL_SECRET
            if want_ledger:
                audit = host.raw("session.inspect", {"session": session})
                text = json.dumps(audit)
                # Form values and built queries are page data: only whether
                # they are present is recorded here, never a value.
                result["ledger_mentions_method"] = "method" in text
                result["ledger_leaks_value"] = "typed" in text
                result["ledger_entries"] = [
                    entry for entry in (audit.get("result") or {}).get("audit", {})
                    .get("entries", []) if "act" in json.dumps(entry)
                ][-2:]
        host.finish()
        return result, by_id, nodes

    # ------------------------------------------------------------ F1 methodOf
    post, by_id, nodes = one_act("f1-post", "postgo", {"kind": "submit"}, want_ledger=True)
    # Anti-vacuity: the page's own script ran before the host looked. Without
    # this every refusal below could pass on a page that installed nothing.
    record["marker_in_snapshot"] = "mark" in by_id
    # Page compatibility: whether the page's own `__attrs` write threw, what it
    # read back, and whether the field is still a Map to page code.
    record["page_echo"] = (by_id.get("echo") or {}).get("name")
    record["div_role"] = (by_id.get("fakedl") or {}).get("role")
    record["anchor_role"] = (by_id.get("realdl") or {}).get("role")
    record["f1_post_submit"] = post
    record["f1_snapshot_method"] = {n.get("dom_id"): n.get("method") for n in nodes
                                    if n.get("role") == "form"}
    record["f1_activation"] = {k: v.get("activation") for k, v in by_id.items()
                               if v.get("activation")}
    honest, _, _ = one_act("f1-get", "getgo", {"kind": "submit"}, want_ledger=True)
    record["f1_honest_get_submit"] = honest

    # ------------------------------------------------------------ F2 targetOf
    named, _, _ = one_act("f2-named", "named", {"kind": "click"})
    record["f2_named_link"] = named
    plain, _, _ = one_act("f2-plain", "plain", {"kind": "click"})
    record["f2_honest_plain_link"] = plain

    # --------------------------------------------------- F5 download node kind
    for dom_id, key in (("fakedl", "f5_div"), ("realdl", "f5_honest_anchor"),
                        ("divjs", "f5_div_javascript"), ("ajs", "f5_anchor_javascript"),
                        ("divfile", "f5_div_file"), ("afile", "f5_anchor_file"),
                        ("divcross", "f5_div_cross_origin"),
                        ("across", "f5_anchor_cross_origin")):
        result, _, _ = one_act(f"f5-{dom_id}", dom_id, {"kind": "download"})
        record[key] = result
    return record


if __name__ == "__main__":
    sys.exit(main())
