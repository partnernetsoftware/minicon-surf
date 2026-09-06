#!/usr/bin/env python3
"""The frozen court for round C: an element's tag is the host's, not the page's.

Frozen from `element-tag-design-0.0.1.md` §4, **before** the host's scripts stop
asking the element for its tag, and failing until they do.

One thing is under test and two are deliberately not. Under test: a page cannot
make the host treat a `<div>` as a link, whether it lies through
`String.prototype.toLowerCase` or writes `tagName` directly, and cannot take the
host's reader away. Not under test: F1 `methodOf` and F2 `targetOf`, which stay
open after this round and are named here so nobody reads a pass as covering
them. Every criterion below says `tag` where it means tag.

Half of this court exists to stop the other half passing for the wrong reason.
Every refusal is paired with the honest `<a download>` in the same document,
carrying the same patch, which must still deliver its bytes; the page's own
script must be proved to have run; and ordinary markup must still classify.

Strictly headless: no surface binary, no window, no AppKit, and it refuses to
run with the visible-court variable set. Two hermetic loopback origins and one
temporary local file; nothing is fetched from outside this file.

Groups: tag, page, technique.
"""

import argparse
import base64
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

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "protocol"))
import check_contract  # noqa: E402

VISIBLE_ENV = "MINICON_SURF_ALLOW_VISIBLE_COURT"
FIXTURE_ROOT = ROOT / "labs" / "court" / "fixtures"
HOST_SOURCE = ROOT / "labs" / "native-dom" / "src" / "main.rs"
SHIM_BASE = ROOT / "labs" / "native-dom" / "src" / "dom_shim_base.js"
SHIM_MAIN = ROOT / "labs" / "native-dom" / "src" / "dom_shim_main.js"

# REBASED, 2026-09-06, ruled after the round-D audit. **The original form of
# this group expired.** It was frozen before round C as deltas from the
# pre-round-C tree -- base 32,898, M1 233,962/225,898, M2 1,636,236/1,579,500,
# with ceilings of +400, +2,048 and +14,336 -- and round C came in at +392,
# +1,696/+1,952 and +11,936/+13,040, inside every one. That was the right shape
# while round C was the last thing to land, and meaningless the moment anything
# landed on top: measured against a round-D candidate the group failed all five
# criteria, reporting round C's cost **plus** round D's as if it were round
# C's. A cost ceiling written as a delta from a fixed prior baseline expires
# when the next slice lands.
#
# So the group is rebased to equalities at what the tree actually costs, which
# is a live regression guard rather than a statement about one slice: any change
# to these five numbers must now be ruled and re-frozen, exactly as
# `signature-integrity-court.py`'s base-byte pin already works. The old deltas
# and their reasons are kept above so the movement can be read off the file.
BASE_BYTES = 33290
MAIN_BYTES = 26485
M1_PINNED = {"system": 235658, "arena": 227850}
M2_PINNED = {"system": 1648172, "arena": 1592540}

DECLARED_CAPTURES = [
    "reflectApply", "StringOf", "MapOf", "weakMapGet", "weakMapSet", "mapGet",
    "mapSet", "mapHas", "arraySlice", "arrayPush", "arrayIndexOf",
    "arraySplice", "weakSetAdd", "weakSetHas", "jsonStringify",
]

ASKED_FOR = b"the bytes the agent asked for"
LOCAL_FILE = b"a local file the agent never named"

# Each patch lies about the tag and leaves the document working. A blanket
# replacement collapses the page's own snapshot and proves nothing.
PATCHES = {
    "unpatched": "",
    "lower_div_anchor": (
        "var L = String.prototype.toLowerCase;"
        "String.prototype.toLowerCase = function () {"
        " return String(this) === 'DIV' ? 'a' : L.call(this); };"
    ),
    "write_tag_name": (
        "var d = document.getElementById('fakedl');"
        "d.tagName = 'A'; d.localName = 'a';"
    ),
    "take_the_reader": (
        "var d = document.getElementById('fakedl');"
        "d.tagName = 'A'; d.localName = 'a';"
        "try { window.__mcsTag = function () { return 'a'; }; } catch (e) {}"
        "try { delete window.__mcsTag; } catch (e) {}"
        "try { Object.defineProperty(window, '__mcsTag',"
        " { value: function () { return 'a'; }, configurable: true }); } catch (e) {}"
    ),
}


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


def page(patch, second_origin, local_file):
    """One document for every arm. The honest markup sits beside the dishonest
    markup, so an arm that refuses everything fails as loudly as one that
    allows everything."""
    return (
        "<!doctype html><html><body><main>"
        "<p id='mark'>marker</p>"
        "<p id='echo'>unset</p>"
        "<div id='fakedl' href='/asked.bin' download='x.bin'>not a link</div>"
        "<a id='realdl' href='/asked.bin' download='x.bin'>a real link</a>"
        "<a id='ajs' href='javascript:void(0)' download='x.bin'>anchor js</a>"
        "<a id='afile' href='file://" + local_file + "' download='x.bin'>anchor file</a>"
        "<a id='across' href='" + second_origin + "/asked.bin' download='x.bin'>anchor cross</a>"
        "<form id='f' method='get' action='/landed.html'>"
        "<input id='q' type='text' name='q' value='typed'>"
        "<button id='go' type='submit'>go</button></form>"
        "</main><script>(function(){"
        # A cloned anchor, built before the patch runs, because `cloneNode`
        # constructs an element by a different door than the parser and a store
        # that missed it would leave the copy classless.
        "try { var src = document.getElementById('realdl');"
        " var copy = src.cloneNode(true); copy.setAttribute('id', 'clonedl');"
        " document.querySelector('main').append(copy); } catch (e) {}"
        + patch +
        # The page reads its own write back and puts it where the snapshot can
        # be asked about it, which is how the court sees that nothing was taken
        # away from the page.
        "try { document.getElementById('echo').textContent ="
        " 'tag=' + document.getElementById('fakedl').tagName; } catch (e) {}"
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
                   "request_id": f"req_tg_{self.counter}", "deadline_ms": 30000,
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


def reason_of(answer):
    if answer.get("ok"):
        return None
    return ((answer.get("error") or {}).get("details") or {}).get("reason")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--binary", required=True)
    parser.add_argument("--receipt", required=True)
    parser.add_argument("--m1", type=int, default=None,
                        help="measured child-frame M1 for the cost group, system arm")
    parser.add_argument("--m1-arena", type=int, default=None)
    parser.add_argument("--m2", type=int, default=None)
    parser.add_argument("--m2-arena", type=int, default=None)
    args = parser.parse_args()
    if os.environ.get(VISIBLE_ENV):
        print(json.dumps({"passed": False, "reason": "the visible-court variable is set"}))
        return 1

    network = RETENTION.load_network_module()
    served = []
    current = {"patch": "unpatched"}
    checks = []

    def expect(name, condition, detail=None):
        checks.append({"check": name, "passed": bool(condition),
                       **({"detail": detail} if detail is not None else {})})

    with tempfile.TemporaryDirectory(prefix="minicon-surf-tag-") as workspace:
        local_file = str(Path(workspace) / "local.bin")
        Path(local_file).write_bytes(LOCAL_FILE)

        class Other(network.Handler):
            def do_GET(self):
                served.append("OTHER")
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
                                      [("Content-Disposition", 'attachment; filename="a.bin"')])
                if path == "/landed.html":
                    return self.reply(200, b"<!doctype html><html><body><p>landed</p></body></html>",
                                      "text/html")
                return self.reply(200, page(PATCHES[current["patch"]], second_origin, local_file),
                                  "text/html")

        server = network.Server(("127.0.0.1", 0), Handler)
        origin = f"http://127.0.0.1:{server.server_address[1]}"
        threading.Thread(target=server.serve_forever, daemon=True).start()

        def open_arm(tag):
            host = Host(args.binary, Path(workspace) / tag, origin)
            profile = host.ok("profile.create", {"persistence": "ephemeral"})["profile"]
            session = host.ok("session.open", {"profile": profile})["session"]
            target = host.ok("target.open", {"session": session,
                                             "url": origin + "/page.html"})["target"]
            snapshot = host.raw("target.snapshot", {"target": target, "format": "semantic",
                                                    "max_bytes": 65536, "max_nodes": 64})
            nodes = (snapshot.get("result") or {}).get("nodes") or []
            by_id = {n.get("dom_id"): n for n in nodes if n.get("dom_id")}
            return host, target, by_id, nodes

        def act(host, target, by_id, dom_id, action):
            del served[:]
            if dom_id not in by_id:
                return {"present": False, "fetched": []}
            answer = host.raw("target.act", {"target": target,
                                             "reference": by_id[dom_id]["reference"],
                                             "action": action}, validate=False)
            payload = b""
            if answer.get("ok"):
                blob = (answer.get("result") or {}).get("bytes_base64")
                if blob:
                    payload = base64.b64decode(blob)
            return {"present": True, "ok": bool(answer.get("ok")), "reason": reason_of(answer),
                    "fetched": list(served), "payload": payload}

        try:
            for label, patch in PATCHES.items():
                current["patch"] = label
                host, target, by_id, nodes = open_arm(f"tag-{label}")
                marker = "mark" in by_id
                div = act(host, target, by_id, "fakedl", {"kind": "download"})
                host2, target2, by_id2, _ = open_arm(f"honest-{label}")
                honest = act(host2, target2, by_id2, "realdl", {"kind": "download"})
                text = act(host2, target2, by_id2, "mark", {"kind": "download"})

                expect(f"A: with `{label}`, the div is never offered as a link and never downloads",
                       marker and "fakedl" not in by_id and div["present"] is False,
                       {"marker": marker, "offered": "fakedl" in by_id,
                        "role": (by_id.get("fakedl") or {}).get("role")})
                expect(f"A: with `{label}`, nothing was fetched for the div",
                       marker and div["fetched"] == [], {"server_saw": div["fetched"]})
                expect(f"A: with `{label}`, the honest link still delivers its bytes",
                       honest["present"] and honest.get("ok") is True
                       and honest["payload"] == ASKED_FOR,
                       {"ok": honest.get("ok"), "bytes": len(honest["payload"])})
                expect(f"A: with `{label}`, a paragraph is still refused `not_a_link`",
                       text["present"] and text.get("ok") is False
                       and text.get("reason") == "not_a_link",
                       {"reason": text.get("reason")})

                # The escalation pairs, unchanged by this round.
                for dom_id, want, name in (("ajs", "scheme_unsupported", "a javascript: href"),
                                           ("afile", "scheme_unsupported", "a file:// href"),
                                           ("across", "address", "an origin never allowed")):
                    got = act(host2, target2, by_id2, dom_id, {"kind": "download"})
                    expect(f"A: with `{label}`, an anchor with {name} is still refused",
                           got["present"] and got.get("ok") is False
                           and got.get("reason") == want
                           and got["payload"] != LOCAL_FILE,
                           {"reason": got.get("reason"), "got_local_file":
                            got["payload"] == LOCAL_FILE})

                # B: the page keeps what it had.
                echo = (by_id.get("echo") or {}).get("name")
                expect(f"B: with `{label}`, the page's own script ran to completion",
                       marker and echo is not None and echo.startswith("tag="),
                       {"echo": echo, "marker": marker})
                if label in ("write_tag_name", "take_the_reader"):
                    expect(f"B: with `{label}`, the page reads back the tag it wrote",
                           echo == "tag=A", {"echo": echo})
                roles = {n.get("dom_id"): n.get("role") for n in nodes if n.get("dom_id")}
                expect(f"B: with `{label}`, ordinary markup still classifies",
                       roles.get("realdl") == "link" and roles.get("f") == "form"
                       and roles.get("q") == "textbox" and roles.get("go") == "button",
                       {"roles": roles})
                host.finish()
                host2.finish()

            # B: a cloned anchor is still an anchor to the host. `cloneNode`
            # constructs by a different door than the parser, and a store that
            # missed it would answer "" for the copy and leave it classless.
            current["patch"] = "unpatched"
            host, target, by_id, nodes = open_arm("clone")
            roles = {n.get("dom_id"): n.get("role") for n in nodes if n.get("dom_id")}
            cloned = act(host, target, by_id, "clonedl", {"kind": "download"})
            host.finish()
            expect("B: a cloned anchor is still a link to the host and still downloads",
                   roles.get("clonedl") == "link" and cloned["present"]
                   and cloned.get("ok") is True and cloned["payload"] == ASKED_FOR,
                   {"role": roles.get("clonedl"), "ok": cloned.get("ok"),
                    "bytes": len(cloned["payload"])})

            # ------------------------------------------- C: technique and cost
            source = HOST_SOURCE.read_text()
            base = SHIM_BASE.read_text()

            found = re.findall(r"^\s*const (\w+) = (?:Reflect|String|Map|WeakMap|WeakSet"
                               r"|Array|JSON|Object)[.\w]*;", base, re.M)
            expect("C: the fifteen declared captures are unchanged and there is no sixteenth",
                   found == DECLARED_CAPTURES, {"found": found})

            def region(start_marker, end_marker):
                begin = source.find(start_marker)
                if begin < 0:
                    return None
                end = source.find(end_marker, begin + len(start_marker))
                return source[begin:end] if end > begin else None

            for start, end, name in (
                ("const SERIALIZE_JS", "const ACTIVATION_JS", "SERIALIZE_JS"),
                ("const ACTIVATION_JS", "fn activation_js", "ACTIVATION_JS"),
                ("fn snapshot_script", "fn microbench_script", "snapshot_script"),
                ("fn download_probe_script", "fn download_name", "download_probe_script"),
                ("fn form_action_script", "fn act_script", "form_action_script"),
                ("fn act_script", "// ------", "act_script"),
            ):
                body = region(start, end)
                expect(f"C: `{name}` no longer asks the element for its tag",
                       body is not None and len(body) > 100
                       and ".tagName.toLowerCase()" not in body,
                       {"found": body is not None, "length": len(body or ""),
                        "still_asks": body is not None and ".tagName.toLowerCase()" in body})

            reader = base.find('"__mcsTag"')
            window = base[reader:reader + 260] if reader >= 0 else ""
            expect("C: the reader is installed non-writable and non-configurable",
                   reader >= 0 and "writable: false" in window and "configurable: false" in window,
                   {"found": reader >= 0})

            base_bytes = len(SHIM_BASE.read_bytes())
            main_bytes = len(SHIM_MAIN.read_bytes())
            expect("C: the shims cost exactly what they are pinned at",
                   base_bytes == BASE_BYTES and main_bytes == MAIN_BYTES,
                   {"base": base_bytes, "pinned": BASE_BYTES,
                    "main": main_bytes, "main_pinned": MAIN_BYTES})

            measured = {"system": (args.m1, args.m2), "arena": (args.m1_arena, args.m2_arena)}
            for arm, (m1, m2) in measured.items():
                expect(f"C: [{arm}] one child costs exactly {M1_PINNED[arm]} owner bytes",
                       m1 == M1_PINNED[arm],
                       {"measured": m1, "pinned": M1_PINNED[arm],
                        "delta": None if m1 is None else m1 - M1_PINNED[arm]})
                expect(f"C: [{arm}] seven children cost exactly {M2_PINNED[arm]} owner bytes",
                       m2 == M2_PINNED[arm],
                       {"measured": m2, "pinned": M2_PINNED[arm],
                        "delta": None if m2 is None else m2 - M2_PINNED[arm]})
        finally:
            server.shutdown()
            other.shutdown()

    passed = sum(1 for check in checks if check["passed"])
    receipt = {
        "court": "native-dom round C: an element's tag is the host's",
        "frozen_from": "labs/native-dom/element-tag-design-0.0.1.md §4",
        "binary_sha256": hashlib.sha256(Path(args.binary).read_bytes()).hexdigest(),
        "passed": passed == len(checks),
        "score": f"{passed}/{len(checks)}",
        "checks": checks,
        "not_under_test": [
            "F1 methodOf: a declared POST submitted as a GET -- still open after this round",
            "F2 targetOf: a named-target link activated -- still open after this round",
            "node names, which still come from the page's textContent",
        ],
        "headless": "no surface binary, no window, no AppKit; two hermetic loopback origins",
    }
    Path(args.receipt).write_text(json.dumps(receipt, indent=2) + "\n")
    print(json.dumps({"passed": receipt["passed"], "score": receipt["score"],
                      "receipt": args.receipt}))
    return 0 if receipt["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
