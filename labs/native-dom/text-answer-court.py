#!/usr/bin/env python3
"""The frozen court for the text cut: an answer is never lost to half a
character.

Frozen from `text-answer-audit-0.0.1.md` §12 and the ruling that followed it,
**before** `snapshot_script` stops cutting with `String.prototype.slice`, and
failing until it does.

The audit measured that a node's text reaches nothing it was feared to reach --
not node identity, not a reference, not a navigation, a download or a typed
refusal -- but that the 256-unit cut can lose the **whole** snapshot: a
surrogate pair straddling the cut leaves a lone surrogate, `serde_json` refuses
the string, and the host answers a bare `internal`. That is ordinary content at
an unlucky offset, not an attack, and it reaches all six places the snapshot
cuts a page-derived string.

Under test: the answer survives its own text at every one of those six cuts, a
deliberately written lone surrogate does not lose it either, the bound still
binds when the page owns `slice`, and none of the authority the audit measured
as unreachable becomes reachable.

Not under test, and deliberately: an option's `label` and the value that would
be submitted are two different strings, and the snapshot shows only the first.
That is conformant HTML and a question about what the answer should say; it is
named in this court's receipt so a pass cannot be read as covering it.

Strictly headless: no surface binary, no window, no AppKit, and it refuses to
run with the visible-court variable set. One hermetic loopback origin; nothing
is fetched from outside this file.

Groups: cuts, surrogates, bound, unmoved.
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
HOST_SOURCE = ROOT / "labs" / "native-dom" / "src" / "main.rs"
SHIM_BASE = ROOT / "labs" / "native-dom" / "src" / "dom_shim_base.js"
SHIM_MAIN = ROOT / "labs" / "native-dom" / "src" / "dom_shim_main.js"

# Nothing about the shims may move: the whole change is inside a host script,
# which is compiled per evaluation and is not resident per realm. These are the
# values the round-D implementation left, and this round must not touch them.
BASE_BYTES = 33886
MAIN_BYTES = 26485

EMOJI = "\U0001F600"

# One document per cut site, each placing a surrogate pair so that the pair
# straddles the boundary that site is cut at: 256 for a name, a value and an
# option label, 64 for a `dom_id`, a control's name and a radio's group.
CUTS = {
    "name_pair_straddles": ("<p id='p'>" + "a" * 255 + EMOJI + "tail</p>", 256),
    "dom_id_pair_straddles": ("<p id='" + "i" * 63 + EMOJI + "tail'>a node</p>", 64),
    "value_pair_straddles": ("<form><input id='v' type='text' name='v' value='"
                             + "a" * 255 + EMOJI + "tail'></form>", 256),
    "option_pair_straddles": ("<form><select id='s' name='s'><option>"
                              + "a" * 255 + EMOJI + "tail</option></select></form>", 256),
    "control_name_pair_straddles": ("<form><input id='c' type='text' name='"
                                    + "n" * 63 + EMOJI + "tail' value='v'></form>", 64),
    "group_pair_straddles": ("<form><input id='r' type='radio' name='"
                             + "g" * 63 + EMOJI + "tail'></form>", 64),
}
# The controls. A court whose every criterion is a surrogate would pass on a
# host that answered nothing, so each shape that must keep working is here.
CONTROLS = {
    "plain": "<p id='p'>hello</p>",
    "long_plain": "<p id='p'>" + "B" * 2000 + "</p>",
    "pair_ends_at_the_cut": "<p id='p'>" + "a" * 254 + EMOJI + "tail</p>",
    "pair_after_the_cut": "<p id='p'>" + "a" * 300 + EMOJI + "</p>",
    "emoji_in_short_text": "<p id='p'>" + EMOJI + " short</p>",
}
SURROGATES = {
    "written_lone_surrogate":
        "<p id='p'>x</p><script>document.getElementById('p').textContent ="
        " 'A' + String.fromCharCode(0xD800) + 'B';</script>",
    "written_lone_low_surrogate":
        "<p id='p'>x</p><script>document.getElementById('p').textContent ="
        " 'A' + String.fromCharCode(0xDC00) + 'B';</script>",
    "written_pair":
        "<p id='p'>x</p><script>document.getElementById('p').textContent ="
        " 'A' + String.fromCharCode(0xD83D, 0xDE00) + 'B';</script>",
}
# The page owns `String.prototype.slice`; after the change the cut must not.
BOUND = {
    "slice_identity":
        "<p id='p'>" + "B" * 2000 + "</p>"
        "<script>String.prototype.slice = function () { return String(this); };</script>",
    "slice_empty":
        "<p id='p'>" + "B" * 2000 + "</p>"
        "<script>String.prototype.slice = function () { return ''; };</script>",
}
# Nothing the audit measured as unreachable may become reachable.
UNMOVED = ("<main><p id='p'>plain</p>"
           "<a id='link' href='/landed.html'>a link</a>"
           "<a id='dl' href='/asked.bin' download='x.bin'>a download</a>"
           "<div id='notlink' href='/asked.bin' download='x.bin'>not a link</div>"
           "<a id='js' href='javascript:void(0)'>a script link</a>"
           "<form id='f' method='post' action='/landed.html'>"
           "<button id='go' type='submit'>go</button></form>"
           "<a id='named' href='/landed.html' target='somewhere'>named</a>"
           "</main><script>(function(){"
           " var l = document.getElementById('link');"
           " l.addEventListener('focus', function () {"
           "  document.getElementById('p').textContent = 'MOVED-DURING-THE-ACT'; });"
           "})();</script>")

ASKED_FOR = b"the bytes the agent asked for"


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


def document(body):
    return ("<!doctype html><html><body>" + body + "</body></html>").encode()


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
                   "request_id": f"req_ta_{self.counter}", "deadline_ms": 30000,
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


def has_lone_surrogate(text):
    if not text:
        return False
    units = text.encode("utf-16-le", "surrogatepass")
    i = 0
    while i + 1 < len(units):
        unit = units[i] | (units[i + 1] << 8)
        if 0xD800 <= unit <= 0xDBFF:
            if i + 3 >= len(units):
                return True
            nxt = units[i + 2] | (units[i + 3] << 8)
            if not 0xDC00 <= nxt <= 0xDFFF:
                return True
            i += 4
            continue
        if 0xDC00 <= unit <= 0xDFFF:
            return True
        i += 2
    return False


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--binary", required=True)
    parser.add_argument("--receipt", required=True)
    args = parser.parse_args()
    if os.environ.get(VISIBLE_ENV):
        print(json.dumps({"passed": False, "reason": "the visible-court variable is set"}))
        return 1

    network = RETENTION.load_network_module()
    served = []
    current = {"body": ""}
    checks = []

    def expect(name, condition, detail=None):
        checks.append({"check": name, "passed": bool(condition),
                       **({"detail": detail} if detail is not None else {})})

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
            return self.reply(200, document(current["body"]), "text/html")

    server = network.Server(("127.0.0.1", 0), Handler)
    origin = f"http://127.0.0.1:{server.server_address[1]}"
    threading.Thread(target=server.serve_forever, daemon=True).start()

    try:
        with tempfile.TemporaryDirectory(prefix="minicon-surf-ta-") as workspace:

            def snapshot(tag, body, max_nodes=64):
                """Each document on its own host: a page that loses its own
                answer must not be able to explain another's."""
                current["body"] = body
                host = Host(args.binary, Path(workspace) / tag, origin)
                try:
                    profile = host.ok("profile.create", {"persistence": "ephemeral"})["profile"]
                    session = host.ok("session.open", {"profile": profile})["session"]
                    target = host.ok("target.open", {"session": session,
                                                     "url": origin + "/page.html"})["target"]
                    answer = host.raw("target.snapshot",
                                      {"target": target, "format": "semantic",
                                       "max_bytes": 65536, "max_nodes": max_nodes})
                    return host, target, answer
                except Exception as error:
                    host.finish()
                    raise error

            # ------------------------------------------------- A: the six cuts
            for label, (body, limit) in CUTS.items():
                host, _target, answer = snapshot("cut-" + label, body)
                host.finish()
                result = answer.get("result") or {}
                nodes = result.get("nodes") or []
                blob = json.dumps(nodes)
                fields = [n.get(key) for n in nodes
                          for key in ("name", "value", "dom_id", "control_name", "group")
                          if n.get(key)]
                fields += [o.get("label") for n in nodes for o in (n.get("options") or [])
                           if o.get("label")]
                expect(f"A: `{label}` still returns an answer",
                       bool(answer.get("ok")) and nodes,
                       {"ok": bool(answer.get("ok")),
                        "code": (answer.get("error") or {}).get("code"),
                        "nodes": len(nodes)})
                expect(f"A: `{label}` carries no half character and stays inside its bound",
                       bool(answer.get("ok"))
                       and not any(has_lone_surrogate(f) for f in fields)
                       and all(len(f) <= limit for f in fields),
                       {"lone_surrogate": any(has_lone_surrogate(f) for f in fields),
                        "longest": max((len(f) for f in fields), default=0), "limit": limit,
                        "blob_bytes": len(blob)})

            # ------------------------------- the controls that must keep working
            for label, body in CONTROLS.items():
                host, _target, answer = snapshot("ctl-" + label, body)
                host.finish()
                nodes = (answer.get("result") or {}).get("nodes") or []
                names = [n.get("name") for n in nodes if n.get("name")]
                expect(f"A: the control `{label}` answers with a node and a name",
                       bool(answer.get("ok")) and len(nodes) == 1 and names
                       and not has_lone_surrogate(names[0]) and len(names[0]) <= 256,
                       {"ok": bool(answer.get("ok")), "nodes": len(nodes),
                        "name_len": len(names[0]) if names else 0})

            # ------------------------------------------------ B: the surrogates
            for label, body in SURROGATES.items():
                host, _target, answer = snapshot("sur-" + label, body)
                host.finish()
                nodes = (answer.get("result") or {}).get("nodes") or []
                names = [n.get("name") for n in nodes if n.get("name")]
                expect(f"B: `{label}` does not lose the answer",
                       bool(answer.get("ok")) and len(nodes) == 1 and names
                       and len(names[0]) == 3 and not has_lone_surrogate(names[0]),
                       {"ok": bool(answer.get("ok")), "nodes": len(nodes),
                        "name_len": len(names[0]) if names else 0,
                        "lone": has_lone_surrogate(names[0]) if names else None})

            # ------------------------------------------------------ C: the bound
            for label, body in BOUND.items():
                host, _target, answer = snapshot("bnd-" + label, body)
                host.finish()
                result = answer.get("result") or {}
                nodes = result.get("nodes") or []
                names = [n.get("name") or "" for n in nodes]
                expect(f"C: with `{label}` the cut is still the host's",
                       bool(answer.get("ok")) and len(nodes) == 1
                       and all(len(n) <= 256 for n in names)
                       and result.get("truncated") is False,
                       {"ok": bool(answer.get("ok")), "nodes": len(nodes),
                        "longest": max((len(n) for n in names), default=0),
                        "truncated": result.get("truncated")})

            # ------------------------------------------------- D: nothing moved
            host, target, answer = snapshot("unmoved", UNMOVED)
            result = answer.get("result") or {}
            nodes = result.get("nodes") or []
            by_id = {n.get("dom_id"): n for n in nodes if n.get("dom_id")}
            ids = {k: (v.get("reference") or {}).get("node") for k, v in by_id.items()}
            activations = {k: v.get("activation") for k, v in by_id.items() if v.get("activation")}
            expect("D: a node's id is the host's, and a div is still not a link",
                   ids.get("p") == "node_1" and "notlink" not in by_id
                   and by_id.get("link", {}).get("role") == "link",
                   {"ids": ids, "div_offered": "notlink" in by_id})
            expect("D: the typed refusals a page's text cannot reach are unchanged",
                   activations.get("js") == "scheme_unsupported"
                   and activations.get("named") == "target_named"
                   and activations.get("go") == "form_method_unsupported"
                   and activations.get("dl") == "download_available",
                   {"activations": activations})

            del served[:]
            act = host.raw("target.act", {"target": target,
                                          "reference": by_id["link"]["reference"],
                                          "action": {"kind": "click"}}, validate=False)
            fetched = list(served)
            after = host.raw("target.snapshot", {"target": target, "format": "semantic",
                                                 "max_bytes": 65536, "max_nodes": 64})
            stale = host.raw("target.act", {"target": target,
                                            "reference": by_id["go"]["reference"],
                                            "action": {"kind": "submit"}}, validate=False)
            host.finish()
            expect("D: a link still navigates, and to nowhere else",
                   bool(act.get("ok")) and fetched == ["/landed.html"],
                   {"ok": bool(act.get("ok")), "server_saw": fetched})
            expect("D: text written during an act moves the revision and staleness still bites",
                   (after.get("result") or {}).get("revision", 0) > result.get("revision", 0)
                   and stale.get("ok") is False
                   and (stale.get("error") or {}).get("code") == "stale_revision",
                   {"before": result.get("revision"),
                    "after": (after.get("result") or {}).get("revision"),
                    "stale_code": (stale.get("error") or {}).get("code")})

            host, target, answer = snapshot("download", UNMOVED)
            by_id = {n.get("dom_id"): n for n in ((answer.get("result") or {}).get("nodes") or [])
                     if n.get("dom_id")}
            del served[:]
            got = host.raw("target.act", {"target": target,
                                          "reference": by_id["dl"]["reference"],
                                          "action": {"kind": "download"}}, validate=False)
            host.finish()
            expect("D: an honest download still delivers its bytes",
                   bool(got.get("ok"))
                   and (got.get("result") or {}).get("byte_count") == len(ASKED_FOR),
                   {"ok": bool(got.get("ok")),
                    "bytes": (got.get("result") or {}).get("byte_count")})

            # ------------------------------------------- the technique and cost
            source = HOST_SOURCE.read_text()
            begin = source.find("fn snapshot_script")
            body = source[begin:source.find("fn microbench_script", begin)] if begin >= 0 else ""
            expect("D: the snapshot cuts without `slice` and without the global `String`",
                   len(body) > 500 and ".slice(0," not in body and "String(" not in body,
                   {"found": begin >= 0, "length": len(body),
                    "has_slice": ".slice(0," in body, "has_String": "String(" in body})
            expect("D: no shim byte moved -- the whole change is in a host script",
                   len(SHIM_BASE.read_bytes()) == BASE_BYTES
                   and len(SHIM_MAIN.read_bytes()) == MAIN_BYTES,
                   {"base": len(SHIM_BASE.read_bytes()), "base_pinned": BASE_BYTES,
                    "main": len(SHIM_MAIN.read_bytes()), "main_pinned": MAIN_BYTES})
    finally:
        server.shutdown()

    passed = sum(1 for check in checks if check["passed"])
    receipt = {
        "court": "native-dom: an answer is never lost to half a character",
        "frozen_from": "labs/native-dom/text-answer-audit-0.0.1.md §12",
        "binary_sha256": hashlib.sha256(Path(args.binary).read_bytes()).hexdigest(),
        "passed": passed == len(checks),
        "score": f"{passed}/{len(checks)}",
        "checks": checks,
        "not_under_test": [
            "an option's label and the value that would be submitted are two different "
            "strings and the snapshot shows only the first -- conformant HTML, and a "
            "separate protocol question",
        ],
        "headless": "no surface binary, no window, no AppKit; one hermetic loopback origin",
    }
    Path(args.receipt).write_text(json.dumps(receipt, indent=2) + "\n")
    print(json.dumps({"passed": receipt["passed"], "score": receipt["score"],
                      "receipt": args.receipt}))
    return 0 if receipt["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
