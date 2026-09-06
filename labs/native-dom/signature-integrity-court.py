#!/usr/bin/env python3
"""The frozen court for the approval signature, the fragment, and the probe.

Frozen from `signature-integrity-design-0.0.1.md` §5, **before** the host's
scripts stop building those three answers out of intrinsics the page owns, and
failing until they do.

Three things are under test, and one thing is deliberately not. The approval a
preflight takes must still bind when the page moves the approved `href`
between the two derivations -- through `Array.prototype.join` and through
`String.prototype.replace`, both measured to defeat it today. A `#fragment`
must still be seen when `String.prototype.startsWith` lies. The court's own
realm probe must report the realm's truth when the page dictates a vector
through `join(":")` or swaps the global `String`. What is **not** under test is
F1 `methodOf`, F2 `targetOf` and F5 the download probe's node kind: they are
separate candidates, they are still open, and every criterion below says
`href` where it means href.

Half of this court exists to stop the other half passing for the wrong reason.
A host that refused every activation would satisfy every refusal criterion, so
each refusal is paired with an arm where the same page, carrying the same
patch, does **not** move the href and must navigate and be fetched.

Strictly headless: no surface binary, no window, no AppKit, and it refuses to
run with the visible-court variable set. One hermetic loopback origin; nothing
is fetched from outside this file.

Groups: approval, fragment, probe, technique.
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

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "protocol"))
import check_contract  # noqa: E402

VISIBLE_ENV = "MINICON_SURF_ALLOW_VISIBLE_COURT"
FIXTURE_ROOT = ROOT / "labs" / "court" / "fixtures"
HOST_SOURCE = ROOT / "labs" / "native-dom" / "src" / "main.rs"
SHIM_BASE = ROOT / "labs" / "native-dom" / "src" / "dom_shim_base.js"
SHIM_MAIN = ROOT / "labs" / "native-dom" / "src" / "dom_shim_main.js"

# Frozen at the moment of freezing, on `ba46420b`. The slice may not pay for
# itself with base bytes, and may not be confused with slimming.
# Amendment, 2026-09-06, ruled in `element-tag-design-0.0.1.md` §1. This pin
# was frozen at 32,898 so the signature-integrity slice could not pay for
# itself with base bytes, and it did its job: it is what caught round C
# changing the base at all. Round C is authorised to change it, and moves it to
# 33,290 (+392, inside that round's own frozen ceiling of +400). The main shim
# is untouched at 26,485. The old value is kept here so the movement can be
# read off the file.
BASE_BYTES = 33290
MAIN_BYTES = 26485

# The fifteen captures the base declares today. A sixteenth is a different
# round: H3's declared set and its reserved list would both have to reopen.
DECLARED_CAPTURES = [
    "reflectApply", "StringOf", "MapOf", "weakMapGet", "weakMapSet", "mapGet",
    "mapSet", "mapHas", "arraySlice", "arrayPush", "arrayIndexOf",
    "arraySplice", "weakSetAdd", "weakSetHas", "jsonStringify",
]

# The realm probe's truth on a page that patches nothing, measured on
# `ba46420b`: the seal has taken the internals handle away, and the main realm
# has the five main-only page APIs.
PROBE_TRUTH = {
    "main_present": False, "main_enumerable": False, "main_class_list": True,
    "main_custom_event": True, "main_event_view": True, "main_element_view": True,
    "main_dataset": True,
}

APPROVED = "/landed.html"
MOVED = "/moved.html"

# Each patch lies about exactly one intrinsic and leaves the document working,
# which is what a page trying to move a host decision would write. A blanket
# replacement collapses the page's own document and proves nothing.
PATCHES = {
    "none": "",
    "join": (
        "var JN = Array.prototype.join;"
        "Array.prototype.join = function (sep) {"
        " return sep === ' ' && this.length === 4 ? 'SIG' : JN.call(this, sep); };"
    ),
    "replace": (
        "var RP = String.prototype.replace;"
        "String.prototype.replace = function (a, b) {"
        " var self = String(this);"
        " if (self === '/landed.html' || self === '/moved.html') return '/landed.html';"
        " return RP.call(self, a, b); };"
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


def link_page(patch, move):
    """The approved link, and a handler that moves it inside the window."""
    mutate = ("el.setAttribute('href', '%s');" % MOVED) if move else ""
    return (
        "<!doctype html><html><body><main>"
        "<p id='mark'>marker</p>"
        "<a id='mover' href='%s'>the approved link</a>"
        "</main><script>(function(){"
        "var el = document.getElementById('mover');"
        "el.addEventListener('focus', function () { %s });"
        "%s})();</script></body></html>" % (APPROVED, mutate, patch)
    ).encode()


def form_page(patch, move):
    """The same question for a form, whose action reads through the same
    `urlOf` the link's href does. The handler hangs on the submitter, because
    a form is not focusable and a `submit` aimed at the form itself opens no
    window for page code at all -- measured, not assumed."""
    mutate = ("f.setAttribute('action', '%s');" % MOVED) if move else ""
    return (
        "<!doctype html><html><body><main>"
        "<p id='mark'>marker</p>"
        "<form id='f' method='get' action='%s'>"
        "<button id='go' type='submit'>go</button></form>"
        "</main><script>(function(){"
        "var f = document.getElementById('f');"
        "var b = document.getElementById('go');"
        "b.addEventListener('focus', function () { %s });"
        "%s})();</script></body></html>" % (APPROVED, mutate, patch)
    ).encode()


FRAGMENT_PATCH = (
    "var SW = String.prototype.startsWith;"
    "String.prototype.startsWith = function (v) {"
    " return String(v) === '#' ? false : SW.call(this, v); };"
)


def fragment_page(patch):
    return (
        "<!doctype html><html><body><main>"
        "<p id='mark'>marker</p>"
        "<a id='frag' href='#section'>fragment</a>"
        "<a id='empty' href=''>empty</a>"
        "<a id='space' href='   '>whitespace</a>"
        "<a id='plain' href='%s'>plain</a>"
        "</main><script>(function(){%s})();</script></body></html>" % (APPROVED, patch)
    ).encode()


PROBE_PATCHES = {
    "none": "",
    # The page writes the court's answer directly.
    "join": (
        "var PJ = Array.prototype.join;"
        "Array.prototype.join = function (sep) {"
        " return sep === ':' ? 'true:true:false:false:false:false:false'"
        " : PJ.call(this, sep); };"
    ),
    # The page swaps what the probe converts its booleans with.
    "string": (
        "var SG = String;"
        "window.String = function (v) {"
        " return v === true ? 'false' : (v === false ? 'true' : SG(v)); };"
    ),
}


def probe_page(patch):
    return (
        "<!doctype html><html><body><main><p id='mark'>marker</p></main>"
        "<script>(function(){%s})();</script></body></html>" % patch
    ).encode()


class Host:
    def __init__(self, binary, directory, origin, court=False):
        Path(directory).mkdir(parents=True, exist_ok=True)
        environment = dict(os.environ)
        for knob in ("MINICON_SURF_NATIVE_REALM_ZONE", "MINICON_SURF_NATIVE_REALM_ARENA",
                     "MINICON_SURF_PROFILE_STORE", VISIBLE_ENV):
            environment.pop(knob, None)
        command = [binary, "serve", "--stdio", "--fixture-root", str(FIXTURE_ROOT),
                   "--config-dir", str(Path(directory) / "config"), "--allow-origin", origin]
        if court:
            command += ["--surface-court-file", str(Path(directory) / "court.log"),
                        "--court-realm-probe", "1"]
        self.process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                        stderr=subprocess.DEVNULL, text=True, env=environment)
        self.counter = 0

    def raw(self, operation, arguments, validate=True):
        self.counter += 1
        request = {"protocol": "minicon-surf.control", "version": "0.0.2",
                   "request_id": f"req_si_{self.counter}", "deadline_ms": 30000,
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
    args = parser.parse_args()
    if os.environ.get(VISIBLE_ENV):
        print(json.dumps({"passed": False, "reason": "the visible-court variable is set"}))
        return 1

    network = RETENTION.load_network_module()
    served = []
    current = {"page": None}

    class Handler(network.Handler):
        def do_GET(self):
            path, _, _query = self.path.partition("?")
            network.Handler.hits.append(path)
            served.append(path)
            if path in (APPROVED, MOVED):
                return self.reply(200, b"<!doctype html><html><body><p>landed</p></body></html>",
                                  "text/html")
            return self.reply(200, current["page"], "text/html")

    server = network.Server(("127.0.0.1", 0), Handler)
    origin = f"http://127.0.0.1:{server.server_address[1]}"
    threading.Thread(target=server.serve_forever, daemon=True).start()
    checks = []

    def expect(name, condition, detail=None):
        checks.append({"check": name, "passed": bool(condition),
                       **({"detail": detail} if detail is not None else {})})

    def run(directory, page_bytes, tag, court=False):
        """One host, one document, one act; the arm's whole observable result."""
        current["page"] = page_bytes
        host = Host(args.binary, Path(directory) / tag, origin, court=court)
        out = {"marker": False, "host": host}
        try:
            profile = host.ok("profile.create", {"persistence": "ephemeral"})["profile"]
            session = host.ok("session.open", {"profile": profile})["session"]
            target = host.ok("target.open", {"session": session,
                                             "url": origin + "/page.html"})["target"]
            out["target"] = target
            snapshot = host.raw("target.snapshot", {"target": target, "format": "semantic",
                                                    "max_bytes": 65536, "max_nodes": 32})
            nodes = (snapshot.get("result") or {}).get("nodes") or []
            out["by_id"] = {n.get("dom_id"): n for n in nodes if n.get("dom_id")}
            # The page's own script ran before the host looked: without this
            # every criterion below could pass on a page that never installed
            # its patch.
            out["marker"] = "mark" in out["by_id"]
            out["activations"] = {k: v.get("activation") for k, v in out["by_id"].items()}
        except Exception as error:
            out["error"] = f"{type(error).__name__}: {error}"
        return out

    def act(arm, dom_id, action):
        del served[:]
        if dom_id not in arm.get("by_id", {}):
            return {"missing": True, "fetched": []}
        answer = arm["host"].raw("target.act", {"target": arm["target"],
                                                "reference": arm["by_id"][dom_id]["reference"],
                                                "action": action}, validate=False)
        return {"ok": bool(answer.get("ok")), "reason": reason_of(answer),
                "fetched": list(served), "missing": False}

    try:
        with tempfile.TemporaryDirectory(prefix="minicon-surf-si-") as directory:
            # ---------------------------------------------- A: the approval binds
            for label, patch in PATCHES.items():
                arm = run(directory, link_page(patch, move=True), f"link-move-{label}")
                moved = act(arm, "mover", {"kind": "click"})
                arm["host"].finish()
                expect(f"A: with `{label}` patched, a moved href is refused `preflight_mismatch`",
                       arm.get("marker") and not moved["missing"]
                       and moved["ok"] is False and moved["reason"] == "preflight_mismatch",
                       {"marker": arm.get("marker"), "reason": moved["reason"],
                        "ok": moved["ok"]})
                expect(f"A: with `{label}` patched, a moved href reaches the server not at all",
                       arm.get("marker") and moved["fetched"] == [],
                       {"server_saw": moved["fetched"]})

                # The anti-vacuity half: the same page and the same patch, with
                # nothing moved, must still navigate. A host that refused
                # everything fails here.
                still = run(directory, link_page(patch, move=False), f"link-still-{label}")
                went = act(still, "mover", {"kind": "click"})
                still["host"].finish()
                expect(f"A: with `{label}` patched, an unmoved approved link still navigates",
                       still.get("marker") and went["ok"] is True
                       and APPROVED in went["fetched"],
                       {"marker": still.get("marker"), "ok": went["ok"],
                        "server_saw": went["fetched"]})

            # The form's action reads through the same `urlOf`.
            for label, patch in PATCHES.items():
                arm = run(directory, form_page(patch, move=True), f"form-move-{label}")
                moved = act(arm, "go", {"kind": "submit"})
                arm["host"].finish()
                expect(f"A: with `{label}` patched, a moved form action is refused `preflight_mismatch`",
                       arm.get("marker") and not moved["missing"] and moved["ok"] is False
                       and moved["reason"] == "preflight_mismatch",
                       {"marker": arm.get("marker"), "reason": moved["reason"],
                        "ok": moved["ok"]})
                expect(f"A: with `{label}` patched, a moved form action reaches the server not at all",
                       arm.get("marker") and moved["fetched"] == [],
                       {"server_saw": moved["fetched"]})

                still = run(directory, form_page(patch, move=False), f"form-still-{label}")
                went = act(still, "go", {"kind": "submit"})
                still["host"].finish()
                expect(f"A: with `{label}` patched, an unmoved form still submits and is fetched",
                       still.get("marker") and went["ok"] is True
                       and APPROVED in went["fetched"],
                       {"marker": still.get("marker"), "ok": went["ok"],
                        "server_saw": went["fetched"]})

            # ---------------------------------------------- B: the fragment is seen
            for label, patch in (("unpatched", ""), ("startsWith patched", FRAGMENT_PATCH)):
                arm = run(directory, fragment_page(patch), f"frag-{label.split()[0]}")
                refusal = act(arm, "frag", {"kind": "click"})
                expect(f"B: {label}, `#section` reads `fragment_unsupported` and is refused",
                       arm.get("marker")
                       and arm.get("activations", {}).get("frag") == "fragment_unsupported"
                       and refusal["ok"] is False and refusal["reason"] == "fragment_unsupported"
                       and refusal["fetched"] == [],
                       {"marker": arm.get("marker"),
                        "activation": arm.get("activations", {}).get("frag"),
                        "reason": refusal["reason"], "server_saw": refusal["fetched"]})
                arm["host"].finish()

            # Anti-vacuity on its own host: the fragment act above replaces the
            # document, so a plain link judged after it would be stale for a
            # reason that has nothing to do with the rule under test.
            fresh = run(directory, fragment_page(FRAGMENT_PATCH), "frag-plain")
            went = act(fresh, "plain", {"kind": "click"})
            fresh["host"].finish()
            expect("B: with `startsWith` patched, a plain link still reads `allowed` and navigates",
                   fresh.get("marker")
                   and fresh.get("activations", {}).get("plain") == "allowed"
                   and went["ok"] is True and APPROVED in went["fetched"],
                   {"marker": fresh.get("marker"),
                    "activation": fresh.get("activations", {}).get("plain"),
                    "ok": went["ok"], "server_saw": went["fetched"]})

            for dom_id, label in (("empty", "an empty href"), ("space", "a whitespace-only href")):
                arm = run(directory, fragment_page(""), f"href-{dom_id}")
                went = act(arm, dom_id, {"kind": "click"})
                arm["host"].finish()
                expect(f"B: {label} still reads `allowed` and still loads the current document",
                       arm.get("activations", {}).get(dom_id) == "allowed"
                       and went["ok"] is True and went["fetched"] == ["/page.html"],
                       {"activation": arm.get("activations", {}).get(dom_id),
                        "ok": went["ok"], "server_saw": went["fetched"]})

            # ---------------------------------------------- C: the probe is the host's
            for label, patch in PROBE_PATCHES.items():
                arm = run(directory, probe_page(patch), f"probe-{label}", court=True)
                report = arm["host"].raw("memory.report", {})
                arm["host"].finish()
                probe = (((report.get("result") or {}).get("owners") or {})
                         .get("realm_probe") or {})
                got = {key: probe.get(key) for key in PROBE_TRUTH}
                expect(f"C: with `{label}` patched, the probe reports the realm's truth",
                       arm.get("marker") and probe.get("realms_probed", 0) >= 1
                       and got == PROBE_TRUTH,
                       {"marker": arm.get("marker"), "realms_probed": probe.get("realms_probed"),
                        "reported": got})
                if label != "none":
                    expect(f"C: with `{label}` patched, the page's own script had run first",
                           arm.get("marker"), {"marker": arm.get("marker")})

            # ---------------------------------------------- D: technique and cost
            source = HOST_SOURCE.read_text()

            def region(start_marker, end_marker):
                begin = source.find(start_marker)
                if begin < 0:
                    return None
                end = source.find(end_marker, begin + len(start_marker))
                return source[begin:end] if end > begin else None

            probe_js = region("const REALM_PROBE_JS", "const OPERATIONS")
            expect("D: `REALM_PROBE_JS` builds its answer without `join` or the global `String`",
                   probe_js is not None and len(probe_js) > 200
                   and ".join(" not in probe_js and "String(" not in probe_js,
                   {"found": probe_js is not None, "length": len(probe_js or ""),
                    "has_join": probe_js is not None and ".join(" in probe_js,
                    "has_String": probe_js is not None and "String(" in probe_js})

            signature = region("  const __mcsPreflight", "\"##;")
            expect("D: the approval signature is built without `join`",
                   signature is not None and len(signature) > 100 and ".join(" not in signature,
                   {"found": signature is not None, "length": len(signature or ""),
                    "has_join": signature is not None and ".join(" in signature})

            url_of = region("  const urlOf =", "  const schemeDecision")
            expect("D: `urlOf` strips without `replace` and without the global `String`",
                   url_of is not None and len(url_of) > 40
                   and ".replace(" not in url_of and "String(" not in url_of,
                   {"found": url_of is not None, "length": len(url_of or ""),
                    "has_replace": url_of is not None and ".replace(" in url_of,
                    "has_String": url_of is not None and "String(" in url_of})

            scheme = region("  const schemeDecision", "  const methodOf")
            expect("D: the fragment is detected without `startsWith`",
                   scheme is not None and len(scheme) > 100 and ".startsWith(" not in scheme,
                   {"found": scheme is not None, "length": len(scheme or ""),
                    "has_startsWith": scheme is not None and ".startsWith(" in scheme})

            base = SHIM_BASE.read_text()
            found = re.findall(r"^\s*const (\w+) = (?:Reflect|String|Map|WeakMap|WeakSet"
                               r"|Array|JSON|Object)[.\w]*;", base, re.M)
            expect("D: the declared captures are unchanged and there is no sixteenth",
                   found == DECLARED_CAPTURES,
                   {"found": found, "declared": DECLARED_CAPTURES})

            base_bytes = len(SHIM_BASE.read_bytes())
            main_bytes = len(SHIM_MAIN.read_bytes())
            expect("D: no base bytes moved -- this slice is not slimming and does not pay with them",
                   base_bytes == BASE_BYTES and main_bytes == MAIN_BYTES,
                   {"base": base_bytes, "base_frozen": BASE_BYTES,
                    "main": main_bytes, "main_frozen": MAIN_BYTES})
    finally:
        server.shutdown()

    passed = sum(1 for check in checks if check["passed"])
    receipt = {
        "court": "native-dom approval signature, fragment and probe integrity",
        "frozen_from": "labs/native-dom/signature-integrity-design-0.0.1.md §5",
        "binary_sha256": hashlib.sha256(Path(args.binary).read_bytes()).hexdigest(),
        "passed": passed == len(checks),
        "score": f"{passed}/{len(checks)}",
        "checks": checks,
        "not_under_test": [
            "F1 methodOf: a target or formmethod moved between the derivations",
            "F2 targetOf: the target vocabulary read through trim/toLowerCase",
            "F5 the download probe's node kind",
        ],
        "headless": "no surface binary, no window, no AppKit; one hermetic loopback origin",
    }
    Path(args.receipt).write_text(json.dumps(receipt, indent=2) + "\n")
    print(json.dumps({"passed": receipt["passed"], "score": receipt["score"],
                      "receipt": args.receipt}))
    return 0 if receipt["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
