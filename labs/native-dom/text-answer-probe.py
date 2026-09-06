#!/usr/bin/env python3
"""A measurement probe, not a court: what a page's own text can do to the
agent's answer, and whether it can reach anything else.

`uncaptured-intrinsic-audit-0.0.1.md` §3.2 left one class open after every
fail-open closed: a node's `name` in the agent's snapshot comes from the page's
`textContent`, which the page owns outright. That is obviously page-controlled
content. The question this probe answers is whether it is **only** that -- or
whether page text can reach node identity, an act's reference, the revision, a
navigation or download decision, a typed refusal, or a bound.

The host path under test, read from the source:

  `textContent`            `dom_shim_base.js:118` -- `childNodes.map(...).join("")`
  a node's reported name   `snapshot_script`, `main.rs` -- `(el.textContent || "").trim()`
                           then `.slice(0, 256)`
  a textarea's value       `dom_shim_base.js:338` -- `this.textContent`
  an option's label        `dom_shim_base.js:385` -- `getAttribute("label") ?? textContent`
  an option's submitted
  value                    `SERIALIZE_JS` -- `o.getAttribute("value") ?? (o.textContent||"").trim()`

Every one of `map`, `join`, `trim` and `slice` on that path is the page's.

Form values and built query strings are page data and never leave this harness:
the server records only **whether** a marker appeared in a query, never the
query.

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

PLAIN = "the paragraph the page really has"
# Disjoint on purpose: an earlier pair made one marker a substring of the
# other, so the server reported both present and the measurement said nothing.
SHOWN = "LABELALPHA"
HIDDEN = "TEXTBETA"
BIG = 200000            # far past the 256-character bound on a node's name
# 255 plain units, then one astral character whose surrogate pair straddles the
# 256th unit, so a cut at 256 leaves a lone high surrogate.
ASTRAL = "a" * 255 + "\U0001F600" + "tail"


def page(patch):
    return (
        "<!doctype html><html><body><main>"
        f"<p id='plain'>{PLAIN}</p>"
        "<p id='big'>" + ("B" * BIG) + "</p>"
        "<a id='plainlink' href='/landed.html'>a plain link</a>"
        "<form id='f' method='get' action='/landed.html'>"
        # The label the agent is shown and the value the host submits come from
        # two different places, and neither needs a patch to differ.
        f"<select id='sel' name='s'><option label='{SHOWN}'>{HIDDEN}</option>"
        "<option>plain option</option></select>"
        f"<textarea id='ta' name='t'>{PLAIN}</textarea>"
        "<button id='go' type='submit'>go</button></form>"
        "</main><script>(function(){"
        # A handler that rewrites text from inside an act, so the probe can ask
        # whether a text mutation invalidates a reference taken before it.
        "try { var link = document.getElementById('plainlink');"
        " link.addEventListener('focus', function () {"
        "  document.getElementById('plain').textContent = 'MOVED-DURING-THE-ACT'; });"
        "} catch (e) {}"
        + patch +
        "})();</script></body></html>"
    ).encode()


PATCHES = {
    "none": "",
    # The page simply writes its own text, which is what a page is entitled to.
    "write_text": (
        "document.getElementById('plain').textContent = 'REWRITTEN-BY-THE-PAGE';"
    ),
    # The bound on a node's name is applied with `String.prototype.slice`.
    "slice_identity": (
        "String.prototype.slice = function () { return String(this); };"
    ),
    # `textContent` is built from `map` and `join`.
    "join_const": (
        "var JN = Array.prototype.join;"
        "Array.prototype.join = function (sep) {"
        " return sep === '' ? 'JOINED-BY-THE-PAGE' : JN.call(this, sep); };"
    ),
    "map_empty": (
        "var MP = Array.prototype.map;"
        "Array.prototype.map = function (fn) {"
        " var out = MP.call(this, fn);"
        " return (out.length && typeof out[0] === 'string') ? [] : out; };"
    ),
    # The sharpest shape: a text that answers one thing when the agent is told
    # and another when the host acts. A browser cannot do this; a stateful
    # replacement can.
    "stateful_join": (
        "var JN2 = Array.prototype.join;"
        "var seen = 0;"
        "Array.prototype.join = function (sep) {"
        " if (sep !== '') return JN2.call(this, sep);"
        " var real = JN2.call(this, sep);"
        " if (real === '" + HIDDEN + "' || real === '" + PLAIN + "') {"
        "  seen++; return seen <= 3 ? '" + SHOWN + "' : real; }"
        " return real; };"
    ),
    # The trim that runs just before the bound is applied.
    "trim_grow": (
        "var TR = String.prototype.trim;"
        "String.prototype.trim = function () {"
        " var s = String(this);"
        " return s.length < 64 ? s + 'X'.repeat(4096) : TR.call(this); };"
    ),
}


def cut_document(body):
    return ("<!doctype html><html><body><main>" + body + "</main></body></html>").encode()


# Where the 256-character cut lands, and what a cut that splits a surrogate
# pair does to the whole answer. Nothing here replaces an intrinsic: this is
# ordinary content at an unlucky offset.
CUTS = {
    "plain": cut_document("<p id='p'>hello</p>"),
    "long_plain": cut_document("<p id='p'>" + ("B" * 200000) + "</p>"),
    "pair_straddles_the_cut": cut_document("<p id='p'>" + ("a" * 255) + "\U0001F600tail</p>"),
    "pair_ends_at_the_cut": cut_document("<p id='p'>" + ("a" * 254) + "\U0001F600tail</p>"),
    "pair_after_the_cut": cut_document("<p id='p'>" + ("a" * 300) + "\U0001F600</p>"),
    "pair_straddles_a_dom_id": cut_document(
        "<p id='" + ("i" * 63) + "\U0001F600tail'>a node with a long id</p>"),
    "pair_straddles_a_value": cut_document(
        "<form><input id='v' type='text' name='v' value='" + ("a" * 255)
        + "\U0001F600tail'></form>"),
    "pair_straddles_an_option": cut_document(
        "<form><select id='s' name='s'><option>" + ("a" * 255)
        + "\U0001F600tail</option></select></form>"),
}


def run_cuts(binary, origin, current, workspace):
    """Each document on its own host, because a page that breaks its own
    snapshot must not be able to explain a later arm's failure."""
    out = {}
    for name in CUTS:
        current["patch"] = "cut:" + name
        host = Host(binary, Path(workspace) / ("cut-" + name), origin)
        try:
            profile = host.ok("profile.create", {"persistence": "ephemeral"})["profile"]
            session = host.ok("session.open", {"profile": profile})["session"]
            target = host.ok("target.open", {"session": session,
                                             "url": origin + "/cut.html"})["target"]
            answer = host.raw("target.snapshot", {"target": target, "format": "semantic",
                                                  "max_bytes": 65536, "max_nodes": 64})
            result = answer.get("result") or {}
            nodes = result.get("nodes") or []
            out[name] = {
                "ok": bool(answer.get("ok")),
                "code": (answer.get("error") or {}).get("code"),
                "message": (answer.get("error") or {}).get("message"),
                "reason": ((answer.get("error") or {}).get("details") or {}).get("reason"),
                "nodes": len(nodes),
                "name_lengths": [len(n.get("name") or "") for n in nodes],
                "dom_ids": [len(n.get("dom_id") or "") for n in nodes],
                "value_lengths": [len(n.get("value") or "") for n in nodes if "value" in n],
            }
        except Exception as error:
            out[name] = {"error": f"{type(error).__name__}: {error}"}
        finally:
            host.finish()
    return out


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
                   "request_id": f"req_tx_{self.counter}", "deadline_ms": 30000,
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
    for key in ("applied", "revision", "byte_count"):
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

    class Handler(network.Handler):
        def do_GET(self):
            path, _, query = self.path.partition("?")
            network.Handler.hits.append(path)
            # Only whether a marker appeared, never the query itself.
            served.append({"path": path,
                           "carried_shown": SHOWN in query,
                           "carried_hidden": HIDDEN in query,
                           "query_bytes": len(query)})
            if path == "/landed.html":
                return self.reply(200, b"<!doctype html><html><body><p>landed</p></body></html>",
                                  "text/html")
            if current["patch"].startswith("cut:"):
                return self.reply(200, CUTS[current["patch"][4:]], "text/html")
            return self.reply(200, page(PATCHES[current["patch"]]), "text/html")

    server = network.Server(("127.0.0.1", 0), Handler)
    origin = f"http://127.0.0.1:{server.server_address[1]}"
    threading.Thread(target=server.serve_forever, daemon=True).start()
    rows = []

    workspace_holder = {}
    with tempfile.TemporaryDirectory(prefix="minicon-surf-tx-") as workspace:
        workspace_holder["path"] = workspace
        for name in PATCHES:
            current["patch"] = name
            rows.append(run_arm(args.binary, origin, served, name, workspace))
        cuts = run_cuts(args.binary, origin, current, workspace)
    server.shutdown()

    receipt = {
        "probe": "the C class: what a page's own text reaches",
        "surrogate_cut": cuts,
        "binary_sha256": hashlib.sha256(Path(args.binary).read_bytes()).hexdigest(),
        "note": ("Form values and built query strings are page data: the server records only "
                 "whether a marker appeared in a query and how many bytes it was, never the "
                 "query. Node text is reported by length and by which fixture marker it "
                 "matches, never verbatim beyond the fixture's own short constants."),
        "bound_under_test": "a node's name is cut at 256 characters in `snapshot_script`",
        "rows": rows,
    }
    Path(args.receipt).write_text(json.dumps(receipt, indent=2) + "\n")
    print(json.dumps({"ran": True, "arms": len(rows), "receipt": args.receipt}))
    return 0


def describe(name):
    """A node's reported name, by shape rather than verbatim."""
    if name is None:
        return None
    marks = []
    if name == PLAIN:
        marks.append("plain")
    if SHOWN in name:
        marks.append("shown-marker")
    if HIDDEN in name:
        marks.append("hidden-marker")
    if "REWRITTEN" in name:
        marks.append("rewritten")
    if "JOINED" in name:
        marks.append("joined")
    if "MOVED" in name:
        marks.append("moved")
    if "\ud800" <= name[-1:] <= "\udfff":
        marks.append("ends-in-lone-surrogate")
    return {"len": len(name), "marks": marks or ["other"]}


def run_arm(binary, origin, served, patch_name, workspace):
    record = {"patch": patch_name}
    host = Host(binary, Path(workspace) / patch_name, origin)
    try:
        profile = host.ok("profile.create", {"persistence": "ephemeral"})["profile"]
        session = host.ok("session.open", {"profile": profile})["session"]
        target = host.ok("target.open", {"session": session,
                                         "url": origin + "/page.html"})["target"]
        snapshot = host.raw("target.snapshot", {"target": target, "format": "semantic",
                                                "max_bytes": 65536, "max_nodes": 64})
        record["snapshot"] = outcome(snapshot)
        result = snapshot.get("result") or {}
        nodes = result.get("nodes") or []
        record["revision"] = result.get("revision")
        record["node_count"] = len(nodes)
        record["truncated"] = result.get("truncated")
        by_id = {n.get("dom_id"): n for n in nodes if n.get("dom_id")}
        record["names"] = {k: describe(v.get("name")) for k, v in by_id.items()}
        record["snapshot_bytes"] = len(json.dumps(snapshot))
        select = by_id.get("sel") or {}
        record["option_labels"] = [describe(o.get("label")) for o in select.get("options") or []]
        record["textarea_value"] = describe((by_id.get("ta") or {}).get("value"))

        # Node identity: the reference the agent holds names a position in the
        # host's own list, so ask whether page text can move it.
        record["node_ids"] = {k: (v.get("reference") or {}).get("node") for k, v in by_id.items()}

        def act(dom_id, action, ids=None):
            table = ids if ids is not None else by_id
            if dom_id not in table:
                return {"present": False}
            del served[:]
            answer = host.raw("target.act", {"target": target,
                                             "reference": table[dom_id]["reference"],
                                             "action": action}, validate=False)
            return {"present": True, "outcome": outcome(answer),
                    "served": list(served)}

        # The submit comes first and on this host, because the link act below
        # commits a navigation and every reference taken before it goes stale
        # -- which would have left the question this probe exists to ask
        # unanswered.
        record["select_option"] = act("sel", {"kind": "select_option", "index": 0})
        # Selecting moves the revision, so the submit needs a reference taken
        # after it -- otherwise the arm answers `stale_revision` and the
        # question this probe exists to ask goes unanswered.
        fresh = host.raw("target.snapshot", {"target": target, "format": "semantic",
                                             "max_bytes": 65536, "max_nodes": 64})
        after_select = {n.get("dom_id"): n
                        for n in ((fresh.get("result") or {}).get("nodes") or [])
                        if n.get("dom_id")}
        record["option_labels_after_select"] = [
            describe(o.get("label")) for o in (after_select.get("sel") or {}).get("options") or []]
        record["submit"] = act("go", {"kind": "submit"}, after_select)
        host.finish()

        host = Host(binary, Path(workspace) / (patch_name + "-b"), origin)
        profile = host.ok("profile.create", {"persistence": "ephemeral"})["profile"]
        session = host.ok("session.open", {"profile": profile})["session"]
        target = host.ok("target.open", {"session": session,
                                         "url": origin + "/page.html"})["target"]
        again = host.raw("target.snapshot", {"target": target, "format": "semantic",
                                             "max_bytes": 65536, "max_nodes": 64})
        by_id = {n.get("dom_id"): n for n in ((again.get("result") or {}).get("nodes") or [])
                 if n.get("dom_id")}

        # Does writing text during an act invalidate a reference taken before?
        record["act_link_with_text_moved"] = act("plainlink", {"kind": "click"})
        after = host.raw("target.snapshot", {"target": target, "format": "semantic",
                                             "max_bytes": 65536, "max_nodes": 64})
        record["revision_after"] = (after.get("result") or {}).get("revision")
        ids2 = {n.get("dom_id"): n for n in ((after.get("result") or {}).get("nodes") or [])
                if n.get("dom_id")}
        record["stale_reference"] = act("go", {"kind": "submit"})
    except Exception as error:
        record["error"] = f"{type(error).__name__}: {error}"
    finally:
        host.finish()
    return record


if __name__ == "__main__":
    sys.exit(main())
