#!/usr/bin/env python3
"""The frozen court for round D (candidate E): the attributes a decision reads
are the host's, and so is the tag, out of one record per element.

Frozen from `attribute-fact-design-0.0.1.md` §11 and the ruling that chose
candidate E, **before** the host's scripts stop asking the element for its
attributes, and failing until they do.

Under test: a page cannot make the host read a declared `method="post"` as a
GET (F1) or a declared `target="somewhere"` as the current frame (F2), whether
it lies through `String.prototype.toLowerCase`, through `Map.prototype.get`
inside `getAttribute`, by writing `el.__attrs` directly, or by trying to take
the host's readers away. Round C's answer must not regress while its store is
rewritten, the page must keep everything it had, and both construction doors --
the parser's own seeding and `cloneNode` -- must reach the store, because the
round-D audit measured a candidate that missed the first one and reported every
form as a GET.

Not under test: node names, which still come from the page's `textContent`.

Half of this court exists to stop the other half passing for the wrong reason.
Every refusal is paired, in the same document and under the same patch, with
honest markup that must still work: a `method="get"` form that must submit, an
untargeted link that must navigate, and a real `<a download>` that must deliver
its bytes.

Strictly headless: no surface binary, no window, no AppKit, and it refuses to
run with the visible-court variable set. Two hermetic loopback origins and one
temporary local file; nothing is fetched from outside this file.

Groups: method, target, page, doors, tag, unmoved.
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

# Frozen before the code, against round C's measured tree (base 33,290;
# system M1 235,658 / M2 1,648,172; arena M1 227,850 / M2 1,592,540). The
# audit's E candidate cost +518, +2,896/+20,272 and +3,024/+21,904, so each
# ceiling has margin and none is vacuous. The main shim is not touched.
BASE_BYTES_AFTER_C = 33290
BASE_BYTES_CEILING = BASE_BYTES_AFTER_C + 600
MAIN_BYTES = 26485
M1_AFTER_C = {"system": 235658, "arena": 227850}
M2_AFTER_C = {"system": 1648172, "arena": 1592540}
M1_CEILING = 3200
M2_CEILING = 23000

DECLARED_CAPTURES = [
    "reflectApply", "StringOf", "MapOf", "weakMapGet", "weakMapSet", "mapGet",
    "mapSet", "mapHas", "arrayIndexOf", "arraySplice", "arraySlice", "arrayPush",
    "weakSetAdd", "weakSetHas", "jsonStringify",
]
# The internals handle's key set, pinned elsewhere and repeated here because
# this round installs a second reader and must not widen the handle: the
# readers are separate globals, never new keys on the handle.
HANDLE_KEYS = ["g", "document", "Document", "Element", "Node", "Event", "addListener",
               "removeListener", "dispatchOn", "signals", "contains", "focusedElement",
               "eventStateOf"]

ASKED_FOR = b"the bytes the agent asked for"
LOCAL_FILE = b"a local file the agent never named"

PATCHES = {
    "unpatched": "",
    # F1, through the normalisation.
    "lower_post_get": (
        "var L = String.prototype.toLowerCase;"
        "String.prototype.toLowerCase = function () {"
        " return String(this) === 'post' ? 'get' : L.call(this); };"
    ),
    # F2, through the normalisation.
    "lower_target_self": (
        "var L2 = String.prototype.toLowerCase;"
        "String.prototype.toLowerCase = function () {"
        " return String(this) === 'somewhere' ? '_self' : L2.call(this); };"
    ),
    # Both, through the accessor `getAttribute` is built from.
    "map_get_lies": (
        "var MG = Map.prototype.get;"
        "Map.prototype.get = function (k) {"
        " var v = MG.call(this, k);"
        " if (k === 'method' && v === 'post') return 'get';"
        " if (k === 'target' && v === 'somewhere') return '_self';"
        " return v; };"
    ),
    # Both, by writing the field the host used to read.
    "write_attrs": (
        "var f = document.getElementById('postform');"
        "f.__attrs = new Map([['method', 'get'], ['action', '/landed.html'],"
        " ['id', 'postform']]);"
        "var a = document.getElementById('named');"
        "a.__attrs = new Map([['href', '/landed.html'], ['id', 'named']]);"
    ),
    # Both, by trying to take the host's readers away. A `div` renamed at the
    # same time keeps round C under the same patch.
    "take_the_readers": (
        "var f = document.getElementById('postform');"
        "f.__attrs = new Map([['method', 'get'], ['action', '/landed.html'],"
        " ['id', 'postform']]);"
        "var d = document.getElementById('fakedl');"
        "d.tagName = 'A'; d.localName = 'a';"
        "try { window.__mcsAttr = function () { return 'get'; }; } catch (e) {}"
        "try { window.__mcsTag = function () { return 'a'; }; } catch (e) {}"
        "try { delete window.__mcsAttr; } catch (e) {}"
        "try { delete window.__mcsTag; } catch (e) {}"
        "try { Object.defineProperty(window, '__mcsAttr',"
        " { value: function () { return 'get'; }, configurable: true }); } catch (e) {}"
    ),
    # Round C's own routes, so rewriting its store cannot regress it.
    "lower_div_anchor": (
        "var L3 = String.prototype.toLowerCase;"
        "String.prototype.toLowerCase = function () {"
        " return String(this) === 'DIV' ? 'a' : L3.call(this); };"
    ),
    "write_tag_name": (
        "var d = document.getElementById('fakedl');"
        "d.tagName = 'A'; d.localName = 'a';"
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
    """One document for every arm: the dishonest markup, the honest markup that
    must keep working, and both construction doors the store has to reach."""
    return (
        "<!doctype html><html><body><main>"
        "<p id='mark'>marker</p>"
        "<p id='echo'>unset</p>"
        # F1: the declared POST and the honest GET.
        "<form id='postform' method='post' action='/landed.html'>"
        "<button id='postgo' type='submit'>send</button></form>"
        "<form id='getform' method='get' action='/landed.html'>"
        "<button id='getgo' type='submit'>go</button></form>"
        # F2: the named target and the honest untargeted link.
        "<a id='named' href='/landed.html' target='somewhere'>named</a>"
        "<a id='plain' href='/landed.html'>plain</a>"
        # Round C, kept under the same patches.
        "<div id='fakedl' href='/asked.bin' download='x.bin'>not a link</div>"
        "<a id='realdl' href='/asked.bin' download='x.bin'>a real link</a>"
        # No authority expansion: three addresses an honest anchor also carries.
        "<a id='ajs' href='javascript:void(0)' download='x.bin'>anchor js</a>"
        "<a id='afile' href='file://" + local_file + "' download='x.bin'>anchor file</a>"
        "<a id='across' href='" + second_origin + "/asked.bin' download='x.bin'>anchor cross</a>"
        "</main><script>(function(){"
        # The clone door and the createElement door, both built before the
        # patch, because a store that misses either leaves an element the host
        # cannot classify -- the defect the round-D audit measured in a
        # candidate that validated only at write time.
        "try { var src = document.getElementById('realdl');"
        " var copy = src.cloneNode(true); copy.setAttribute('id', 'clonedl');"
        " document.querySelector('main').append(copy);"
        " var pf = document.getElementById('postform');"
        " var pc = pf.cloneNode(true); pc.setAttribute('id', 'cloneform');"
        " document.querySelector('main').append(pc);"
        " var made = document.createElement('a');"
        " made.setAttribute('href', '/asked.bin');"
        " made.setAttribute('download', 'x.bin');"
        " made.setAttribute('id', 'madedl');"
        " made.textContent = 'made by script';"
        " document.querySelector('main').append(made); } catch (e) {}"
        + patch +
        # The page reports what its own `__attrs` write did: whether it threw,
        # and whether the field still reads back as the Map page code expects.
        "try { var f2 = document.getElementById('postform');"
        " var kind = (f2.__attrs && typeof f2.__attrs.get === 'function') ? 'map' : 'other';"
        " var names = f2.getAttributeNames().length;"
        " document.getElementById('echo').textContent ="
        " 'attrs=' + kind + ' names=' + (names > 0 ? 'some' : 'none');"
        "} catch (e) { try { document.getElementById('echo').textContent = 'threw';"
        " } catch (e2) {} }"
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
                   "request_id": f"req_af_{self.counter}", "deadline_ms": 30000,
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
    parser.add_argument("--m1", type=int, default=None)
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

    with tempfile.TemporaryDirectory(prefix="minicon-surf-af-") as workspace:
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
            return host, session, target, by_id, nodes

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

                # Each act gets its own host: a committed navigation replaces
                # the document, and a stale reference would refuse for a reason
                # that has nothing to do with the rule under test.
                host, session, target, by_id, nodes = open_arm(f"m-{label}")
                marker = "mark" in by_id
                echo = (by_id.get("echo") or {}).get("name")
                methods = {n.get("dom_id"): n.get("method") for n in nodes
                           if n.get("role") == "form"}
                roles = {n.get("dom_id"): n.get("role") for n in nodes if n.get("dom_id")}
                # Both submit doors, because the audit measured an arm where
                # the lie reached the form's own activation and not the
                # submitter's: a court that asked only one of them would have
                # passed on a page that still submits its POST.
                post = act(host, target, by_id, "postform", {"kind": "submit"})
                post_button = act(host, target, by_id, "postgo", {"kind": "submit"})
                activations = {n.get("dom_id"): n.get("activation") for n in nodes
                               if n.get("activation")}
                audit = host.raw("session.inspect", {"session": session})
                ledger = json.dumps(audit)
                host.finish()

                expect(f"A: with `{label}`, a declared POST is refused `form_method_unsupported`",
                       marker and post["present"] and post.get("ok") is False
                       and post.get("reason") == "form_method_unsupported",
                       {"marker": marker, "reason": post.get("reason")})
                expect(f"A: with `{label}`, a declared POST is refused through its submitter too",
                       post_button["present"] and post_button.get("ok") is False
                       and post_button.get("reason") == "form_method_unsupported",
                       {"reason": post_button.get("reason")})
                expect(f"A: with `{label}`, a declared POST reaches the server not at all",
                       marker and post["fetched"] == [] and post_button["fetched"] == [],
                       {"server_saw": post["fetched"] + post_button["fetched"]})
                expect(f"A: with `{label}`, both submit doors read the same refusal",
                       activations.get("postform") == "form_method_unsupported"
                       and activations.get("postgo") == "form_method_unsupported",
                       {"activations": {k: activations.get(k)
                                        for k in ("postform", "postgo", "getform", "getgo")}})
                expect(f"A: with `{label}`, the snapshot reports the method the page declared",
                       methods.get("postform") == "post" and methods.get("getform") == "get",
                       {"methods": methods})
                expect(f"A: with `{label}`, the ledger records no method and no form value",
                       '"method"' not in ledger and "somewhere" not in ledger,
                       {"mentions_method": '"method"' in ledger})

                host, _, target, by_id, _ = open_arm(f"g-{label}")
                honest_form = act(host, target, by_id, "getgo", {"kind": "submit"})
                host.finish()
                expect(f"A: with `{label}`, an honest GET form still submits and is fetched",
                       honest_form["present"] and honest_form.get("ok") is True
                       and "/landed.html" in honest_form["fetched"],
                       {"ok": honest_form.get("ok"), "server_saw": honest_form["fetched"]})

                host, _, target, by_id, _ = open_arm(f"t-{label}")
                named = act(host, target, by_id, "named", {"kind": "click"})
                host.finish()
                expect(f"B: with `{label}`, a named target is refused `target_named`",
                       named["present"] and named.get("ok") is False
                       and named.get("reason") == "target_named"
                       and named["fetched"] == [],
                       {"reason": named.get("reason"), "server_saw": named["fetched"]})

                host, _, target, by_id, _ = open_arm(f"p-{label}")
                plain = act(host, target, by_id, "plain", {"kind": "click"})
                host.finish()
                expect(f"B: with `{label}`, an untargeted link still navigates",
                       plain["present"] and plain.get("ok") is True
                       and "/landed.html" in plain["fetched"],
                       {"ok": plain.get("ok"), "server_saw": plain["fetched"]})

                # C: the page keeps what it had.
                expect(f"C: with `{label}`, the page's write lands and its script completes",
                       echo == "attrs=map names=some", {"echo": echo})

                # D: both construction doors, and E: round C unbroken.
                host, _, target, by_id, _ = open_arm(f"d-{label}")
                cloned = act(host, target, by_id, "clonedl", {"kind": "download"})
                host.finish()
                host, _, target, by_id2, _ = open_arm(f"d2-{label}")
                cloneform = act(host, target, by_id2, "cloneform", {"kind": "submit"})
                host.finish()
                host, _, target, by_id3, _ = open_arm(f"d3-{label}")
                made = act(host, target, by_id3, "madedl", {"kind": "download"})
                host.finish()
                expect(f"D: with `{label}`, a cloned anchor is a link and downloads",
                       roles.get("clonedl") == "link" and cloned.get("ok") is True
                       and cloned["payload"] == ASKED_FOR,
                       {"role": roles.get("clonedl"), "ok": cloned.get("ok")})
                expect(f"D: with `{label}`, a cloned POST form keeps its method and is refused",
                       cloneform["present"] and cloneform.get("ok") is False
                       and cloneform.get("reason") == "form_method_unsupported",
                       {"reason": cloneform.get("reason"),
                        "method": methods.get("cloneform")})
                expect(f"D: with `{label}`, an anchor built by script downloads",
                       roles.get("madedl") == "link" and made.get("ok") is True
                       and made["payload"] == ASKED_FOR,
                       {"role": roles.get("madedl"), "ok": made.get("ok")})

                host, _, target, by_id, _ = open_arm(f"c-{label}")
                div = act(host, target, by_id, "fakedl", {"kind": "download"})
                honest = act(host, target, by_id, "realdl", {"kind": "download"})
                host.finish()
                expect(f"E: with `{label}`, round C still holds -- the div is not offered",
                       "fakedl" not in by_id and div["present"] is False
                       and div["fetched"] == [],
                       {"offered": "fakedl" in by_id, "role": roles.get("fakedl")})
                expect(f"E: with `{label}`, the honest link still delivers its bytes",
                       honest.get("ok") is True and honest["payload"] == ASKED_FOR,
                       {"ok": honest.get("ok"), "bytes": len(honest["payload"])})

                host, _, target, by_id, _ = open_arm(f"x-{label}")
                for dom_id, want, name in (("ajs", "scheme_unsupported", "a javascript: href"),
                                           ("afile", "scheme_unsupported", "a file:// href"),
                                           ("across", "address", "an origin never allowed")):
                    got = act(host, target, by_id, dom_id, {"kind": "download"})
                    expect(f"F: with `{label}`, an anchor with {name} is still refused",
                           got["present"] and got.get("ok") is False
                           and got.get("reason") == want and got["payload"] != LOCAL_FILE,
                           {"reason": got.get("reason"),
                            "got_local_file": got["payload"] == LOCAL_FILE})
                host.finish()

            # ------------------------------------------ F: nothing else moved
            base = SHIM_BASE.read_text()
            found = re.findall(r"^\s*const (\w+) = (?:Reflect|String|Map|WeakMap|WeakSet"
                               r"|Array|JSON|Object)[.\w]*;", base, re.M)
            expect("F: the fifteen declared captures are unchanged and there is no sixteenth",
                   sorted(found) == sorted(DECLARED_CAPTURES), {"found": found})

            for reader in ("__mcsTag", "__mcsAttr"):
                where = base.find(f'"{reader}"')
                window = base[where:where + 320] if where >= 0 else ""
                expect(f"F: `{reader}` is installed non-writable and non-configurable",
                       where >= 0 and "writable: false" in window
                       and "configurable: false" in window,
                       {"found": where >= 0})

            handle = base.find('"__mcsInternals"')
            body = base[handle:handle + 1200] if handle >= 0 else ""
            missing = [key for key in HANDLE_KEYS if key not in body]
            expect("F: the internals handle carries its thirteen keys and no reader",
                   handle >= 0 and not missing
                   and "__mcsAttr" not in body and "__mcsTag" not in body,
                   {"found": handle >= 0, "missing": missing,
                    "reader_in_handle": "__mcsAttr" in body or "__mcsTag" in body})

            expect("F: `__attrs` has a landing setter, so a page's write is ignored not fatal",
                   "set __attrs(" in base, {"has_setter": "set __attrs(" in base})

            main_bytes = len(SHIM_MAIN.read_bytes())
            base_bytes = len(SHIM_BASE.read_bytes())
            expect("F: the base shim grows by at most 600 bytes and the main shim not at all",
                   BASE_BYTES_AFTER_C < base_bytes <= BASE_BYTES_CEILING
                   and main_bytes == MAIN_BYTES,
                   {"base": base_bytes, "ceiling": BASE_BYTES_CEILING,
                    "after_round_c": BASE_BYTES_AFTER_C, "main": main_bytes})

            measured = {"system": (args.m1, args.m2), "arena": (args.m1_arena, args.m2_arena)}
            for arm, (m1, m2) in measured.items():
                expect(f"F: [{arm}] one child costs at most {M1_CEILING} more owner bytes",
                       m1 is not None and (m1 - M1_AFTER_C[arm]) <= M1_CEILING,
                       {"measured": m1, "after_round_c": M1_AFTER_C[arm],
                        "delta": None if m1 is None else m1 - M1_AFTER_C[arm]})
                expect(f"F: [{arm}] seven children cost at most {M2_CEILING} more owner bytes",
                       m2 is not None and (m2 - M2_AFTER_C[arm]) <= M2_CEILING,
                       {"measured": m2, "after_round_c": M2_AFTER_C[arm],
                        "delta": None if m2 is None else m2 - M2_AFTER_C[arm]})
        finally:
            server.shutdown()
            other.shutdown()

    passed = sum(1 for check in checks if check["passed"])
    receipt = {
        "court": "native-dom round D: the attributes a decision reads are the host's",
        "frozen_from": "labs/native-dom/attribute-fact-design-0.0.1.md §11, candidate E",
        "binary_sha256": hashlib.sha256(Path(args.binary).read_bytes()).hexdigest(),
        "passed": passed == len(checks),
        "score": f"{passed}/{len(checks)}",
        "checks": checks,
        "not_under_test": ["node names, which still come from the page's textContent"],
        "headless": "no surface binary, no window, no AppKit; two hermetic loopback origins",
    }
    Path(args.receipt).write_text(json.dumps(receipt, indent=2) + "\n")
    print(json.dumps({"passed": receipt["passed"], "score": receipt["score"],
                      "receipt": args.receipt}))
    return 0 if receipt["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
