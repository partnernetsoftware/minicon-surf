#!/usr/bin/env python3
"""The frozen court for the snapshot parse's strictness.

Frozen from `snapshot-defaulting-audit-0.0.1.md` §9 and the ruling that
followed it, before the parse stops defaulting and failing until it does.

One thing had to be settled before writing it, and it shaped the whole court:
**a page cannot produce a malformed field.** Measured on the shipped binary,
`textContent = 42`, `= {}`, `= null` and `setAttribute("id", 7)` all arrive as
the strings "42", "[object Object]", "null" and "7" -- the realm coerces before
the host ever sees them. So the criteria about missing and mistyped fields
cannot be driven through a fixture. They are pinned where they live: in the
host's source, and in unit tests that hand the parser the shapes a page cannot.

What a page *can* reach is the act path, and that is driven here for real: a
timer that breaks the registry in the window `target.act` opens by running due
timers before resolving a node.

Strictly headless: no surface binary, no window, no AppKit, and it refuses to
run with the visible-court variable set. One hermetic loopback origin; the
server's request log witnesses that no navigation escapes.

Groups: reachable, interlock, source, tests.
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
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "protocol"))
import check_contract  # noqa: E402

VISIBLE_ENV = "MINICON_SURF_ALLOW_VISIBLE_COURT"
FIXTURE_ROOT = ROOT / "labs" / "court" / "fixtures"
SRC = ROOT / "labs" / "native-dom" / "src"
# Pinned: this work is host-side, and a shim that moved would mean it was not.
BASE_SHA = hashlib.sha256((SRC / "dom_shim_base.js").read_bytes()).hexdigest()
MAIN_SHA = hashlib.sha256((SRC / "dom_shim_main.js").read_bytes()).hexdigest()


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

    def call(self, operation, arguments):
        self.counter += 1
        request = {"protocol": "minicon-surf.control", "version": "0.0.2",
                   "request_id": f"req_sc_{self.counter}", "deadline_ms": 30000,
                   "operation": operation, "arguments": arguments}
        check_contract.validate_request(request)
        self.process.stdin.write(json.dumps(request) + "\n")
        self.process.stdin.flush()
        line = self.process.stdout.readline()
        return json.loads(line) if line else {"ok": False, "error": {"code": "host_exited"}}

    def ok(self, operation, arguments):
        answer = self.call(operation, arguments)
        if not answer.get("ok"):
            raise RuntimeError(f"{operation}: {answer.get('error')}")
        return answer["result"]

    def finish(self):
        try:
            self.process.stdin.close()
            self.process.wait(timeout=15)
        except Exception:
            self.process.kill()


def act_after_timer(binary, network, timer_body, wait_s=0.35):
    """Snapshot, let a timer run in the act's own window, then act on the
    reference the agent read before it."""
    page = ('<!doctype html><html><body><main>'
            '<a id="victim" href="/safe.html">safe link</a>'
            '</main><script>setTimeout(function () { ' + timer_body + ' }, 200);</script>'
            '</body></html>').encode()
    hits = []

    class Handler(network.Handler):
        def do_GET(self):
            path, _, _query = self.path.partition("?")
            network.Handler.hits.append(path)
            hits.append(path)
            if path == "/a.html":
                return self.reply(200, page, "text/html")
            return self.reply(200, b"<!doctype html><html><body><p>landed</p></body></html>",
                              "text/html")

    server = network.Server(("127.0.0.1", 0), Handler)
    origin = f"http://127.0.0.1:{server.server_address[1]}"
    threading.Thread(target=server.serve_forever, daemon=True).start()
    out = {"hits": hits}
    try:
        with tempfile.TemporaryDirectory(prefix="minicon-surf-schema-") as directory:
            host = Host(binary, directory, origin)
            try:
                profile = host.ok("profile.create", {"persistence": "ephemeral"})["profile"]
                session = host.ok("session.open", {"profile": profile})["session"]
                target = host.ok("target.open", {"session": session,
                                                 "url": origin + "/a.html"})["target"]
                snapshot = host.ok("target.snapshot", {"target": target, "format": "semantic",
                                                       "max_bytes": 65536, "max_nodes": 32})
                out["names"] = [n.get("name") for n in snapshot["nodes"]]
                victim = [n for n in snapshot["nodes"] if n.get("dom_id") == "victim"][0]
                time.sleep(wait_s)
                out["act"] = host.call("target.act", {"target": target,
                                                      "reference": victim["reference"],
                                                      "action": {"kind": "click"}})
            finally:
                host.finish()
    finally:
        server.shutdown()
    return out


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--binary", required=True)
    parser.add_argument("--receipt", required=True)
    args = parser.parse_args()
    if os.environ.get(VISIBLE_ENV):
        print(json.dumps({"passed": False, "reason": "the visible-court variable is set"}))
        return 1

    network = RETENTION.load_network_module()
    checks = []
    observed = {}

    def expect(name, condition, detail=None):
        checks.append({"check": name, "passed": bool(condition),
                       **({"detail": detail} if detail is not None else {})})

    # R1: the one malformed shape a page can reach. Breaking the registry is the
    # page's doing, and the refusal must not read as the host's fault.
    broken = act_after_timer(args.binary, network,
                             "if (window.__mcs) { window.__mcs.nodes = null; }")
    error = (broken["act"].get("error") or {})
    observed["nodes = null"] = {"code": error.get("code"), "reason":
                                (error.get("details") or {}).get("reason"), "hits": broken["hits"]}
    expect("R1: a page that breaks the registry is refused, not served",
           not broken["act"].get("ok"), {"ok": broken["act"].get("ok")})
    expect("R1b: that refusal does not read as a host fault",
           error.get("code") != "internal",
           {"code": error.get("code")})
    expect("R1c: that refusal names what went wrong",
           isinstance(error.get("details"), dict) and error["details"].get("reason"),
           {"details": error.get("details")})
    expect("R1d: the agent still read the real document before it",
           "safe link" in json.dumps(broken.get("names")), {"names": broken.get("names")})

    # I1/I2: the interlock the audit measured, kept as a standing regression.
    loose = act_after_timer(
        args.binary, network,
        'var e = document.createElement("a"); e.setAttribute("href", "/evil.html");'
        ' var r = window.__mcs; if (r && r.nodes) { for (var i = 0; i < r.nodes.length; i++)'
        ' { r.nodes[i] = e; } }')
    joined = act_after_timer(
        args.binary, network,
        'var e = document.createElement("a"); e.setAttribute("href", "/evil.html");'
        ' document.querySelector("main").appendChild(e);'
        ' var r = window.__mcs; if (r && r.nodes) { for (var i = 0; i < r.nodes.length; i++)'
        ' { r.nodes[i] = e; } }')
    observed["disconnected poison"] = {"code": (loose["act"].get("error") or {}).get("code"),
                                       "hits": loose["hits"]}
    observed["connected poison"] = {"code": (joined["act"].get("error") or {}).get("code"),
                                    "hits": joined["hits"]}
    expect("I1: a disconnected poison is not_found",
           (loose["act"].get("error") or {}).get("code") == "not_found",
           observed["disconnected poison"])
    expect("I2: a connected poison is stale_revision -- connecting it moved the counter",
           (joined["act"].get("error") or {}).get("code") == "stale_revision",
           observed["connected poison"])
    expect("I3: the server is never asked for the swapped URL, either way",
           "/evil.html" not in loose["hits"] and "/evil.html" not in joined["hits"],
           {"loose": loose["hits"], "joined": joined["hits"]})

    # S: the parse itself. A page cannot reach these, so they are pinned at the
    # source and exercised by unit tests rather than pretended in a fixture.
    source = (SRC / "main.rs").read_text()
    expect("S1: the parser never invents a node reference",
           'unwrap_or("node_0")' not in source, {"found": 'unwrap_or("node_0")' in source})
    # Window-based, not exact-string: an exact match that stops matching when
    # the code is reformatted passes for the wrong reason, which is how a
    # criterion comes to measure its own fixture.
    lines = source.split("\n")
    windows = []
    for index, line in enumerate(lines):
        if "snapshot lacks a revision" in line:
            windows.append("\n".join(lines[max(0, index - 10):index + 60]))
    defaulting = [pattern for window in windows for pattern in
                  ("unwrap_or_default()", 'unwrap_or("")', "unwrap_or(Value::Null)",
                   "unwrap_or(false)", 'unwrap_or("node_0")')
                  if pattern in window]
    expect("S2: neither parse site defaults a missing or mistyped field",
           len(windows) == 2 and not defaulting,
           {"sites": len(windows), "defaulting_found": sorted(set(defaulting))})
    expect("S3: truncated is not silently read as false",
           not any("unwrap_or(false)" in window for window in windows),
           {"sites": len(windows)})
    expect("S4: unit tests exercise the shapes a page cannot produce",
           "mod snapshot_schema_tests" in source, {"present": "mod snapshot_schema_tests" in source})
    expect("S5: the shims are untouched by this work",
           hashlib.sha256((SRC / "dom_shim_base.js").read_bytes()).hexdigest() == BASE_SHA
           and hashlib.sha256((SRC / "dom_shim_main.js").read_bytes()).hexdigest() == MAIN_SHA,
           None)
    expect("S6: the operation enum is unchanged at 26",
           len(check_contract.OPERATIONS_NEXT) == 26,
           {"operations": len(check_contract.OPERATIONS_NEXT)})

    receipt = {
        "court": "native-dom snapshot schema strictness",
        "host_sha256": hashlib.sha256(Path(args.binary).read_bytes()).hexdigest(),
        "ruled": {"parse": "missing or mistyped fields are refused, never defaulted",
                  "node_0": "the parser never invents a reference id",
                  "page_fault": "a page-authored break is not an internal host fault",
                  "rejected": "branding snapshot and nodes, measured at 1,872 bytes per realm"},
        "observed": observed,
        "checks": checks,
        "checks_passed": sum(1 for c in checks if c["passed"]),
        "checks_total": len(checks),
        "passed": all(c["passed"] for c in checks),
        "limitations": [
            "design-frozen court: R1b, R1c and the source criteria fail until the parse is made strict",
            "a page cannot produce a malformed field -- measured: textContent = 42, {}, null and setAttribute(\"id\", 7) all arrive as strings -- so those criteria are pinned at the source and in unit tests, not through a fixture",
            "the act path is the one page-reachable route, and it is driven for real through a timer in the window target.act opens",
            "the interlock criteria are a standing regression of what the audit measured, not new behaviour",
            "one hermetic loopback origin; no surface, no window, no AppKit",
        ],
    }
    Path(args.receipt).write_text(json.dumps(receipt, indent=1, sort_keys=True) + "\n")
    print(json.dumps({"passed": receipt["passed"], "checks_passed": receipt["checks_passed"],
                      "checks_total": receipt["checks_total"]}))
    for check in checks:
        if not check["passed"]:
            print("FAIL", json.dumps(check)[:160])
    return 0 if receipt["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
