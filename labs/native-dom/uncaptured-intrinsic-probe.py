#!/usr/bin/env python3
"""A measurement probe, not a court: what a page's patched intrinsics do to
the host's decisions and to the agent's answers.

`intrinsic-hardening-h2-audit-0.0.1.md` split the shims' intrinsic surface
into the calls that have a capture available and the calls that do not. This
probe takes the second half and asks it a question with an answer: for each
intrinsic that the host's own scripts call straight off the page's prototypes,
what changes when the page replaces it before the host looks?

It scores nothing and freezes nothing. Every run records, per patch, the
outcome of one fixed sequence -- open, snapshot, act on the link, act on the
GET form, act on the POST form, download the link, read the revision -- and
the audit reads the differences against the unpatched baseline. A patch whose
whole column equals the baseline changed nothing the host decides with.

Strictly headless: no surface binary, no window, no AppKit, and it refuses to
run with the visible-court variable set. One hermetic loopback origin; nothing
is fetched from outside this file.
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

REAL_TEXT = "the paragraph the page really has"
ASKED_FOR = b"the bytes the agent asked for"

# One document for every patch, so a difference in the recorded column is a
# difference the patch made and not a difference the fixture made.
BODY = (
    '<main>'
    f'<p id="para">{REAL_TEXT}</p>'
    '<a id="plain" href="/landed.html">a plain link</a>'
    '<a id="dl" href="/asked.bin" download="asked.bin">a download link</a>'
    '<a id="js" href="javascript:void(0)">a script link</a>'
    '<a id="wsjs" href="   javascript:void(0)">a padded script link</a>'
    '<a id="frag" href="#section">a fragment link</a>'
    '<a id="named" href="/landed.html" target="somewhere">a named-target link</a>'
    '<div id="fakedl" href="/asked.bin" download="x.bin">not a link at all</div>'
    '<form id="getform" method="get" action="/landed.html">'
    '<input id="q" type="text" name="q" value="typed">'
    '<input id="pw" type="password" name="pw" value="PW-NEVER-REPORTED">'
    '<button id="getgo" type="submit">go</button></form>'
    '<form id="postform" method="post" action="/landed.html">'
    '<input id="p" type="text" name="p" value="typed">'
    '<button id="postgo" type="submit">send</button></form>'
    '</main>'
)

# Each patch replaces exactly one intrinsic, and each replacement is the
# strongest a page can make while leaving its own document parseable: a
# constant, an identity, or a lie in one direction.
PATCHES = {
    "baseline": "",
    "toLowerCase_const_a": 'String.prototype.toLowerCase = function () { return "a"; };',
    "toLowerCase_const_get": 'String.prototype.toLowerCase = function () { return "get"; };',
    "toLowerCase_identity": 'String.prototype.toLowerCase = function () { return String(this); };',
    "toLowerCase_upper": 'String.prototype.toLowerCase = function () { return R.toUpperCase.call(this); };',
    "toUpperCase_const": 'String.prototype.toUpperCase = function () { return "A"; };',
    "trim_const_post": 'String.prototype.trim = function () { return "post"; };',
    "trim_identity": 'String.prototype.trim = function () { return String(this); };',
    "regexp_test_true": 'RegExp.prototype.test = function () { return true; };',
    "regexp_test_false": 'RegExp.prototype.test = function () { return false; };',
    "regexp_exec_null": 'RegExp.prototype.exec = function () { return null; };',
    "startsWith_false": 'String.prototype.startsWith = function () { return false; };',
    "replace_identity": 'String.prototype.replace = function () { return String(this); };',
    "slice_empty": 'String.prototype.slice = function () { return ""; };',
    "split_empty": 'String.prototype.split = function () { return []; };',
    "join_const": 'Array.prototype.join = function () { return "JOINED"; };',
    "array_push_noop": 'Array.prototype.push = function () { return 0; };',
    "array_map_empty": 'Array.prototype.map = function () { return []; };',
    "array_filter_empty": 'Array.prototype.filter = function () { return []; };',
    "array_indexOf_zero": 'Array.prototype.indexOf = function () { return 0; };',
    "array_includes_true": 'Array.prototype.includes = function () { return true; };',
    "array_concat_empty": 'Array.prototype.concat = function () { return []; };',
    "array_slice_empty": 'Array.prototype.slice = function () { return []; };',
    "array_forEach_noop": 'Array.prototype.forEach = function () { return undefined; };',
    "array_find_undefined": 'Array.prototype.find = function () { return undefined; };',
    "array_splice_noop": 'Array.prototype.splice = function () { return []; };',
    "object_keys_empty": 'Object.keys = function () { return []; };',
    "object_entries_empty": 'Object.entries = function () { return []; };',
    "object_assign_identity": 'Object.assign = function (t) { return t; };',
    "json_parse_throw": 'JSON.parse = function () { throw new Error("no"); };',
    "json_stringify_forge": 'JSON.stringify = function () { return "{\\"forged\\":true}"; };',
    "array_isArray_false": 'Array.isArray = function () { return false; };',
    "array_from_empty": 'Array.from = function () { return []; };',
    "math_min_zero": 'Math.min = function () { return 0; };',
    "charCodeAt_zero": 'String.prototype.charCodeAt = function () { return 65; };',
    # The selective family. A blanket replacement collapses the page's own
    # document, which is why the blanket rows above all fail closed; these lie
    # about exactly one comparison and leave everything else intact, which is
    # what a page actually trying to move a host decision would do.
    "sel_method_get": (
        'var L = String.prototype.toLowerCase;'
        'String.prototype.toLowerCase = function () {'
        ' return String(this) === "post" ? "get" : L.call(this); };'
    ),
    "sel_target_self": (
        'var L2 = String.prototype.toLowerCase;'
        'String.prototype.toLowerCase = function () {'
        ' return String(this) === "somewhere" ? "_self" : L2.call(this); };'
    ),
    "sel_trim_method_get": (
        'var T = String.prototype.trim;'
        'String.prototype.trim = function () {'
        ' return String(this) === "post" ? "get" : T.call(this); };'
    ),
    "sel_test_scheme_ok": (
        'var TE = RegExp.prototype.test;'
        'RegExp.prototype.test = function (v) {'
        ' return String(v) === "javascript" ? true : TE.call(this, v); };'
    ),
    "sel_exec_no_scheme": (
        'var EX = RegExp.prototype.exec;'
        'RegExp.prototype.exec = function (v) {'
        ' return String(v).indexOf("javascript:") === 0 ? null : EX.call(this, v); };'
    ),
    "sel_replace_no_strip": (
        'var RP = String.prototype.replace;'
        'String.prototype.replace = function (a, b) {'
        ' return String(this).indexOf("   ") === 0 ? String(this) : RP.call(this, a, b); };'
    ),
    "sel_startsWith_frag": (
        'var SW = String.prototype.startsWith;'
        'String.prototype.startsWith = function (v) {'
        ' return String(v) === "#" ? false : SW.call(this, v); };'
    ),
    "sel_div_is_anchor": (
        'var L3 = String.prototype.toLowerCase;'
        'String.prototype.toLowerCase = function () {'
        ' return String(this) === "DIV" ? "a" : L3.call(this); };'
    ),
    "sel_join_signature": (
        'var JN = Array.prototype.join;'
        'String.prototype.__mcsProbe = 1;'
        'Array.prototype.join = function (sep) {'
        ' return sep === " " && this.length === 4 ? "SIG" : JN.call(this, sep); };'
    ),
}


def page_for(patch_name):
    patch = PATCHES[patch_name]
    if not patch:
        script = ""
    else:
        # The originals are captured first so the page keeps working; the
        # patch is installed at the end of the document, after the tree the
        # host will read exists, and before any host script runs.
        script = (
            "<script>(function(){var R={toUpperCase:String.prototype.toUpperCase};"
            "try{" + patch + "}catch(e){}})();</script>"
        )
    return ("<!doctype html><html><body>" + BODY + script + "</body></html>").encode()


class Host:
    def __init__(self, binary, directory, origin):
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
                   "request_id": f"req_uc_{self.counter}", "deadline_ms": 30000,
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
    """One comparable word per control answer: the code, or what applied."""
    if not answer.get("ok"):
        error = answer.get("error") or {}
        reason = (error.get("details") or {}).get("reason")
        return error.get("code", "?") + (f"/{reason}" if reason else "")
    result = answer.get("result") or {}
    for key in ("applied", "committed", "byte_count", "revision"):
        if key in result:
            return f"ok:{key}={result[key]}"
    return "ok"


def run_patch(binary, origin, served, patch_name, directory):
    host = Host(binary, directory, origin)
    record = {"patch": patch_name}
    try:
        profile = host.ok("profile.create", {"persistence": "ephemeral"})["profile"]
        session = host.ok("session.open", {"profile": profile})["session"]
        target = host.ok("target.open", {"session": session, "url": origin + "/page.html"})["target"]
        record["opened"] = True

        snapshot = host.raw("target.snapshot", {"target": target, "format": "semantic",
                                                "max_bytes": 65536, "max_nodes": 64})
        record["snapshot"] = outcome(snapshot)
        nodes = (snapshot.get("result") or {}).get("nodes") or []
        record["node_count"] = len(nodes)
        record["roles"] = [n.get("role") for n in nodes]
        record["activations"] = {n.get("dom_id"): n.get("activation") for n in nodes
                                 if n.get("activation")}
        record["form_methods"] = {n.get("dom_id"): n.get("method") for n in nodes
                                  if n.get("role") == "form"}
        record["names"] = [n.get("name") for n in nodes]
        # Form values are page data and never leave this harness: only whether
        # a value field is present is recorded, never the value.
        record["value_fields"] = sum(1 for n in nodes if "value" in n)
        blob = json.dumps(snapshot)
        record["real_text_present"] = REAL_TEXT in blob
        # A credential source is serialised by no host script and offered as no
        # node. Only whether its value appears is recorded, never a value.
        record["password_value_leaked"] = "PW-NEVER-REPORTED" in blob

        by_id = {n.get("dom_id"): n for n in nodes if n.get("dom_id")}

        def act(dom_id, action):
            if dom_id not in by_id:
                return "absent-from-snapshot"
            del served[:]
            answer = host.raw("target.act", {"target": target,
                                             "reference": by_id[dom_id]["reference"],
                                             "action": action}, validate=False)
            return outcome(answer)

        # The act sequence is re-snapshotted between acts, because a committed
        # navigation replaces the document the references point into.
        def resnap():
            snap = host.raw("target.snapshot", {"target": target, "format": "semantic",
                                                "max_bytes": 65536, "max_nodes": 64})
            got = (snap.get("result") or {}).get("nodes") or []
            return {n.get("dom_id"): n for n in got if n.get("dom_id")}

        record["act_js_link"] = act("js", {"kind": "click"})
        by_id = resnap() or by_id
        record["act_padded_js_link"] = act("wsjs", {"kind": "click"})
        by_id = resnap() or by_id
        record["act_fragment_link"] = act("frag", {"kind": "click"})
        by_id = resnap() or by_id
        record["act_named_link"] = act("named", {"kind": "click"})
        by_id = resnap() or by_id
        record["act_download_link_click"] = act("dl", {"kind": "click"})
        by_id = resnap() or by_id
        del served[:]
        record["act_download"] = act("dl", {"kind": "download"})
        record["download_fetched"] = [p for p in served]
        by_id = resnap() or by_id
        del served[:]
        # A `div` is not a link. Whether the host still believes the realm
        # when the realm has been taught to say otherwise.
        record["act_download_non_link"] = act("fakedl", {"kind": "download"})
        record["non_link_fetched"] = [p for p in served]
        by_id = resnap() or by_id
        del served[:]
        # A paragraph is in the snapshot and is plainly not a link: the
        # refusal it earns is the baseline the row above is compared against.
        record["act_download_text"] = act("para", {"kind": "download"})
        by_id = resnap() or by_id
        record["act_post_button"] = act("postgo", {"kind": "click"})
        by_id = resnap() or by_id
        record["act_post_submit"] = act("postform", {"kind": "submit"})
        record["post_submit_fetched"] = [p for p in served]
        by_id = resnap() or by_id
        record["act_get_submit"] = act("getform", {"kind": "submit"})
        record["get_submit_fetched"] = [p for p in served]
        # Whatever the document is now, ask for the revision the host reports.
        final = host.raw("target.snapshot", {"target": target, "format": "semantic",
                                             "max_bytes": 65536, "max_nodes": 8})
        record["final_snapshot"] = outcome(final)
    except Exception as error:  # a host that dies is itself a finding
        record["error"] = f"{type(error).__name__}: {error}"
    finally:
        host.finish()
    return record


# ------------------------------------------------------------- the interlock
#
# The second scenario. An act is approved in two phases: the host takes a
# preflight signature, then the act script re-derives it and refuses when the
# two differ. The re-derived value is built with `Array.prototype.join`, which
# the page owns. This asks whether owning it is enough to make a changed
# activation pass as the approved one.
#
# The page's own handler does the changing: the act script focuses what it
# acts on before it compares, so a `focus` listener runs inside the window
# between the two derivations.
INTERLOCK_BODY = (
    '<main>'
    '<a id="mover" href="/landed.html">the approved link</a>'
    '</main>'
    '<script>(function(){'
    ' var el = document.getElementById("mover");'
    ' el.addEventListener("focus", function () {'
    '   el.setAttribute("href", "/moved.html");'
    ' });'
    'PATCH'
    '})();</script>'
)

INTERLOCK_PATCHES = {
    "interlock_baseline": "",
    "interlock_join_constant": (
        ' var JN = Array.prototype.join;'
        ' Array.prototype.join = function (sep) {'
        '  return sep === " " && this.length === 4 ? "SIG" : JN.call(this, sep); };'
    ),
}


def interlock_page(patch_name):
    body = INTERLOCK_BODY.replace("PATCH", INTERLOCK_PATCHES[patch_name])
    return ("<!doctype html><html><body>" + body + "</body></html>").encode()


def run_interlock(binary, origin, served, patch_name, directory):
    host = Host(binary, directory, origin)
    record = {"patch": patch_name}
    try:
        profile = host.ok("profile.create", {"persistence": "ephemeral"})["profile"]
        session = host.ok("session.open", {"profile": profile})["session"]
        target = host.ok("target.open", {"session": session,
                                         "url": origin + "/interlock.html"})["target"]
        snapshot = host.raw("target.snapshot", {"target": target, "format": "semantic",
                                                "max_bytes": 65536, "max_nodes": 64})
        nodes = (snapshot.get("result") or {}).get("nodes") or []
        by_id = {n.get("dom_id"): n for n in nodes if n.get("dom_id")}
        record["found_link"] = "mover" in by_id
        del served[:]
        if "mover" in by_id:
            answer = host.raw("target.act", {"target": target,
                                             "reference": by_id["mover"]["reference"],
                                             "action": {"kind": "click"}}, validate=False)
            record["act"] = outcome(answer)
        record["fetched"] = [p for p in served]
        record["moved_fetched"] = "/moved.html" in record["fetched"]
        record["approved_fetched"] = "/landed.html" in record["fetched"]
    except Exception as error:
        record["error"] = f"{type(error).__name__}: {error}"
    finally:
        host.finish()
    return record


# ------------------------------------------------------- the court's own probe
#
# The third scenario. `REALM_PROBE_JS` answers the court's question about
# every realm as one string, built with `Array.prototype.join(":")`, and the
# host splits that string back into seven booleans. The page owns `join`.
# This asks whether owning it is enough to write the court's answer.
DICTATED = "true:false:true:true:true:true:true"

PROBE_PAGES = {
    "probe_plain": b"<!doctype html><html><body><p>plain</p></body></html>",
    "probe_join_dictated": (
        '<!doctype html><html><body><p>plain</p><script>'
        'Array.prototype.join = function (sep) {'
        ' return sep === ":" ? "' + DICTATED + '"'
        ' : Array.prototype.slice.call(this).toString(); };'
        '</script></body></html>'
    ).encode(),
}


def run_realm_probe(binary, origin, page_name, directory):
    """Court-only: this arm needs `--court-realm-probe`, which the host
    refuses without a private court file, so it builds its own host."""
    environment = dict(os.environ)
    for knob in ("MINICON_SURF_NATIVE_REALM_ZONE", "MINICON_SURF_NATIVE_REALM_ARENA",
                 "MINICON_SURF_PROFILE_STORE", VISIBLE_ENV):
        environment.pop(knob, None)
    court = Path(directory) / f"court-{page_name}.log"
    command = [binary, "serve", "--stdio", "--fixture-root", str(FIXTURE_ROOT),
               "--config-dir", str(Path(directory) / f"config-{page_name}"),
               "--allow-origin", origin, "--surface-court-file", str(court),
               "--court-realm-probe", "1"]
    process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                               stderr=subprocess.DEVNULL, text=True, env=environment)
    counter = [0]

    def call(operation, arguments):
        counter[0] += 1
        request = {"protocol": "minicon-surf.control", "version": "0.0.2",
                   "request_id": f"req_rp_{counter[0]}", "deadline_ms": 30000,
                   "operation": operation, "arguments": arguments}
        check_contract.validate_request(request)
        process.stdin.write(json.dumps(request) + "\n")
        process.stdin.flush()
        line = process.stdout.readline()
        return json.loads(line) if line else {"ok": False, "error": {"code": "host_exited"}}

    record = {"page": page_name}
    try:
        profile = call("profile.create", {"persistence": "ephemeral"})["result"]["profile"]
        session = call("session.open", {"profile": profile})["result"]["session"]
        call("target.open", {"session": session, "url": origin + "/probe.html"})
        report = call("memory.report", {})
        owners = ((report.get("result") or {}).get("owners") or {})
        record["realm_probe"] = owners.get("realm_probe")
    except Exception as error:
        record["error"] = f"{type(error).__name__}: {error}"
    finally:
        try:
            process.stdin.close()
            process.wait(timeout=15)
        except Exception:
            process.kill()
    return record


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--binary", required=True)
    parser.add_argument("--receipt", required=True)
    parser.add_argument("--only", default=None, help="comma-separated patch names")
    args = parser.parse_args()
    if os.environ.get(VISIBLE_ENV):
        print(json.dumps({"ran": False, "reason": "the visible-court variable is set"}))
        return 1

    network = RETENTION.load_network_module()
    served = []
    current = {"patch": "baseline"}

    class Handler(network.Handler):
        def do_GET(self):
            path, _, _query = self.path.partition("?")
            network.Handler.hits.append(path)
            served.append(path)
            if path == "/asked.bin":
                return self.reply(200, ASKED_FOR, "application/octet-stream",
                                  [("Content-Disposition", 'attachment; filename="asked.bin"')])
            if path in ("/landed.html", "/moved.html"):
                return self.reply(200, b"<!doctype html><html><body><p>landed</p></body></html>",
                                  "text/html")
            if path == "/probe.html":
                return self.reply(200, PROBE_PAGES[current["patch"]], "text/html")
            if path == "/interlock.html":
                return self.reply(200, interlock_page(current["patch"]), "text/html")
            return self.reply(200, page_for(current["patch"]), "text/html")

    server = network.Server(("127.0.0.1", 0), Handler)
    origin = f"http://127.0.0.1:{server.server_address[1]}"
    threading.Thread(target=server.serve_forever, daemon=True).start()

    wanted = list(PATCHES) if not args.only else args.only.split(",")
    rows = []
    with tempfile.TemporaryDirectory(prefix="minicon-surf-uncaptured-") as directory:
        for name in wanted:
            current["patch"] = name
            rows.append(run_patch(args.binary, origin, served, name, directory))
        interlock = []
        for name in INTERLOCK_PATCHES:
            current["patch"] = name
            interlock.append(run_interlock(args.binary, origin, served, name, directory))
        probes = []
        for name in PROBE_PAGES:
            current["patch"] = name
            probes.append(run_realm_probe(args.binary, origin, name, directory))
    server.shutdown()

    baseline = next((r for r in rows if r["patch"] == "baseline"), {})
    compared = []
    for row in rows:
        if row["patch"] == "baseline":
            continue
        changed = {k: {"baseline": baseline.get(k), "patched": row.get(k)}
                   for k in row if k not in ("patch",) and row.get(k) != baseline.get(k)}
        compared.append({"patch": row["patch"], "changed": changed,
                         "inert": not changed})

    receipt = {
        "probe": "uncaptured-intrinsic",
        "binary_sha256": hashlib.sha256(Path(args.binary).read_bytes()).hexdigest(),
        "patches": len(wanted),
        "rows": rows,
        "differences": compared,
        "inert_patches": [c["patch"] for c in compared if c["inert"]],
        "interlock": interlock,
        "realm_probe": probes,
        "dictated_vector": DICTATED,
    }
    Path(args.receipt).write_text(json.dumps(receipt, indent=2) + "\n")
    print(json.dumps({"ran": True, "patches": len(wanted),
                      "inert": len(receipt["inert_patches"]),
                      "receipt": args.receipt}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
