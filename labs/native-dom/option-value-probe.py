#!/usr/bin/env python3
"""A measurement probe, not a court: what an agent can see of a select, and
what actually reaches the server.

`text-answer-audit-0.0.1.md` §4 measured, with no patch of any kind, that
`<option label="A">B</option>` shows the agent `A` and submits `B`. That was
left as `not_under_test` in `text-answer-court.py` and named a separate protocol
question. This probe answers it across the shapes an author can write.

  what the agent sees   `snapshot_script`: `options[] = {index, label, selected, disabled}`
                        and `selected`, where `label` is `getAttribute("label") ?? textContent`
  what the host submits  `SERIALIZE_JS`: `o.getAttribute("value") ?? (o.textContent || "").trim()`

Form values and built query strings are page data. The server records only
**which marker** it saw and how many bytes the query was, never the query, and
this probe records nothing else about it.

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

# Disjoint markers: no one of them is a substring of another, so "which marker
# reached the server" is a question with one answer.
MARKERS = ["Malpha", "Mbeta", "Mgamma", "Mdelta", "Mepsi", "Mzeta", "Meta", "Mtheta",
           "Miota", "Mkappa", "Mlambda", "Mmu"]
EMOJI = "\U0001F600"

# One select per shape an author can write. `sel` is the control's name so the
# server can tell them apart without reading a value.
SELECTS = {
    # The shape the audit found: a label attribute and no value.
    "label_only": "<option label='Malpha'>Mbeta</option><option>Mgamma</option>",
    # A label and an explicit value: three strings, one shown.
    "label_and_value": "<option label='Mdelta' value='Mepsi'>Mzeta</option><option>Meta</option>",
    # A value and no label: the text is shown, the value is submitted.
    "value_only": "<option value='Mtheta'>Miota</option><option>Mkappa</option>",
    # The honest control: what is shown is what is sent.
    "plain": "<option>Mlambda</option><option>Mmu</option>",
    # Two options an agent cannot tell apart, carrying different values.
    "duplicate_labels": "<option label='same' value='Malpha'>x</option>"
                        "<option label='same' value='Mbeta'>y</option>",
    # A disabled option, and a selected one the agent never chose.
    "disabled_first": "<option disabled label='Mgamma' value='Mdelta'>d</option>"
                      "<option label='Mepsi' value='Mzeta'>e</option>",
    "preselected_second": "<option label='Meta' value='Mtheta'>a</option>"
                          "<option selected label='Miota' value='Mkappa'>b</option>",
    # Whitespace and an astral character on both sides of the pair.
    "whitespace_and_unicode": "<option label='  Mlambda" + EMOJI + "  ' value='  Mmu"
                              + EMOJI + "  '>t</option><option>Malpha</option>",
}


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
                   "request_id": f"req_ov_{self.counter}", "deadline_ms": 30000,
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
    current = {"shape": "plain", "mutate": False}

    def body():
        options = SELECTS[current["shape"]]
        mutation = (
            "var b = document.getElementById('go');"
            "b.addEventListener('focus', function () {"
            " var o = document.getElementById('s').options[0];"
            " o.setAttribute('value', 'Mmu'); });"
        ) if current["mutate"] else ""
        return (
            "<!doctype html><html><body><main>"
            "<p id='mark'>marker</p>"
            "<form id='f' method='get' action='/landed.html'>"
            "<select id='s' name='sel'>" + options + "</select>"
            "<button id='go' type='submit'>go</button></form>"
            "</main><script>(function(){" + mutation + "})();</script></body></html>"
        ).encode()

    class Handler(network.Handler):
        def do_GET(self):
            path, _, query = self.path.partition("?")
            network.Handler.hits.append(path)
            served.append({"path": path, "query_bytes": len(query),
                           "markers": [m for m in MARKERS if m in query],
                           "carried_emoji": "%F0%9F%98%80" in query,
                           "carried_space": "+" in query or "%20" in query})
            if path == "/landed.html":
                return self.reply(200, b"<!doctype html><html><body><p>landed</p></body></html>",
                                  "text/html")
            return self.reply(200, body(), "text/html")

    server = network.Server(("127.0.0.1", 0), Handler)
    origin = f"http://127.0.0.1:{server.server_address[1]}"
    threading.Thread(target=server.serve_forever, daemon=True).start()
    rows = []

    with tempfile.TemporaryDirectory(prefix="minicon-surf-ov-") as workspace:
        for shape in SELECTS:
            for door in ("form", "submitter"):
                current["shape"], current["mutate"] = shape, False
                rows.append(run_arm(args.binary, origin, served, workspace,
                                    shape, door, False))
        # The mutation arm: the page changes the selected option's value from
        # inside the window the act opens, on the door that has one.
        for shape in ("label_only", "label_and_value"):
            current["shape"], current["mutate"] = shape, True
            rows.append(run_arm(args.binary, origin, served, workspace, shape,
                                "submitter", True))
    server.shutdown()

    receipt = {
        "probe": "an option's label, and the value that reaches the server",
        "binary_sha256": hashlib.sha256(Path(args.binary).read_bytes()).hexdigest(),
        "note": ("Form values and built query strings are page data. The server records only "
                 "which disjoint marker it saw, how many bytes the query was, and whether an "
                 "emoji or a space survived encoding -- never the query."),
        "shown_to_the_agent": "options[] = {index, label, selected, disabled} and selected",
        "rows": rows,
    }
    Path(args.receipt).write_text(json.dumps(receipt, indent=2) + "\n")
    print(json.dumps({"ran": True, "arms": len(rows), "receipt": args.receipt}))
    return 0


def run_arm(binary, origin, served, workspace, shape, door, mutate):
    tag = f"{shape}-{door}{'-mut' if mutate else ''}"
    record = {"shape": shape, "door": door, "mutated_between": mutate}
    host = Host(binary, Path(workspace) / tag, origin)
    try:
        profile = host.ok("profile.create", {"persistence": "ephemeral"})["profile"]
        session = host.ok("session.open", {"profile": profile})["session"]
        target = host.ok("target.open", {"session": session,
                                         "url": origin + "/page.html"})["target"]

        def snapshot():
            answer = host.raw("target.snapshot", {"target": target, "format": "semantic",
                                                  "max_bytes": 65536, "max_nodes": 64})
            nodes = (answer.get("result") or {}).get("nodes") or []
            return {n.get("dom_id"): n for n in nodes if n.get("dom_id")}

        by_id = snapshot()
        select = by_id.get("s") or {}
        record["marker_in_snapshot"] = "mark" in by_id
        record["shown"] = {
            "options": [{"index": o.get("index"), "label": o.get("label"),
                         "selected": o.get("selected"), "disabled": o.get("disabled")}
                        for o in select.get("options") or []],
            "selected": select.get("selected"),
            "exposes_value": any("value" in o for o in select.get("options") or []),
        }
        # Choose the first enabled option the agent can see, the way an agent
        # planning from the snapshot would.
        wanted = next((o["index"] for o in record["shown"]["options"] if not o["disabled"]), 0)
        record["chose_index"] = wanted
        chose = host.raw("target.act", {"target": target,
                                        "reference": select["reference"],
                                        "action": {"kind": "select_option", "index": wanted}},
                         validate=False)
        record["select_option"] = outcome(chose)
        # A disabled option, asked for on purpose, so the refusal is measured
        # rather than assumed.
        disabled = next((o["index"] for o in record["shown"]["options"] if o["disabled"]), None)
        if disabled is not None:
            by_id = snapshot()
            record["select_disabled"] = outcome(host.raw(
                "target.act", {"target": target, "reference": by_id["s"]["reference"],
                               "action": {"kind": "select_option", "index": disabled}},
                validate=False))

        by_id = snapshot()
        del served[:]
        which = "f" if door == "form" else "go"
        answer = host.raw("target.act", {"target": target,
                                         "reference": by_id[which]["reference"],
                                         "action": {"kind": "submit"}}, validate=False)
        record["submit"] = outcome(answer)
        record["server"] = list(served)
    except Exception as error:
        record["error"] = f"{type(error).__name__}: {error}"
    finally:
        host.finish()
    return record


if __name__ == "__main__":
    sys.exit(main())
