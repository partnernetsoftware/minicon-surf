#!/usr/bin/env python3
"""The frozen court for the revision registry's ownership.

Frozen from `snapshot-validation-audit-0.0.1.md` §9 and the ruling that
followed it, **before** the host stops adopting whatever `window.__mcs` it
finds, and failing until it does.

The audit measured the defect end to end: a page names the global first, the
host keeps the page's object, no observer is installed, the revision never
advances, and an agent's stale reference is then **accepted** -- the click
lands on a swapped element and the server receives a URL the agent never asked
for. Unlike every earlier tampering finding, that one fails open.

So this court's criteria are about ownership, not shape: whatever a page does
with that name, the agent's view must be the host's, the revision must advance
when the document changes, a stale reference must still be refused, and the
server must receive nothing the agent did not ask for.

Strictly headless: no surface binary, no window, no AppKit, and it refuses to
run with the visible-court variable set. One hermetic loopback origin; the
server's request log is the witness for what was and was not fetched.

Groups: ownership, staleness, traffic, install, invariants.
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
SRC = ROOT / "labs" / "native-dom" / "src"
SAFE_TEXT = "safe link"
SWAPPED_TEXT = "swapped link"

PREEMPT = ("window.__mcs = { snapshot: -1, nodes: [] };"
           "Object.defineProperty(window.__mcs, 'revision',"
           " { get: function () { return 0; }, set: function () {} });")
NOBBLE = "Object.defineProperty = function () { return arguments[0]; };"


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


def page_with(tamper):
    return ('<!doctype html><html><body><main>'
            '<button id="trigger">go</button>'
            f'<a id="victim" href="/safe.html">{SAFE_TEXT}</a>'
            '</main><script>' + tamper +
            'document.getElementById("trigger").addEventListener("click", function () {'
            '  var v = document.getElementById("victim"); v.remove();'
            '  var evil = document.createElement("a"); evil.id = "victim";'
            '  evil.setAttribute("href", "/evil.html");'
            f'  evil.textContent = "{SWAPPED_TEXT}";'
            '  document.querySelector("main").appendChild(evil);'
            '});</script></body></html>').encode()


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
                   "request_id": f"req_rb_{self.counter}", "deadline_ms": 30000,
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


def run_case(binary, network, tamper):
    """Open the page, snapshot, click the trigger, then act on the stale
    reference. Returns what the agent saw and what the server was asked for."""
    page = page_with(tamper)
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
    outcome = {"hits": hits}
    try:
        with tempfile.TemporaryDirectory(prefix="minicon-surf-registry-") as directory:
            host = Host(binary, directory, origin)
            try:
                profile = host.ok("profile.create", {"persistence": "ephemeral"})["profile"]
                session = host.ok("session.open", {"profile": profile})["session"]
                opened = host.call("target.open", {"session": session, "url": origin + "/a.html"})
                outcome["open"] = opened
                if not opened.get("ok"):
                    return outcome
                target = opened["result"]["target"]
                first = host.call("target.snapshot", {"target": target, "format": "semantic",
                                                      "max_bytes": 65536, "max_nodes": 32})
                outcome["first"] = first
                if not first.get("ok"):
                    return outcome
                nodes = {n.get("dom_id"): n for n in first["result"]["nodes"] if n.get("dom_id")}
                outcome["first_names"] = [n.get("name") for n in first["result"]["nodes"]]
                outcome["first_revision"] = first["result"]["revision"]
                if "trigger" in nodes:
                    host.call("target.act", {"target": target,
                                             "reference": nodes["trigger"]["reference"],
                                             "action": {"kind": "click"}})
                second = host.call("target.snapshot", {"target": target, "format": "semantic",
                                                       "max_bytes": 65536, "max_nodes": 32})
                outcome["second"] = second
                if second.get("ok"):
                    outcome["second_revision"] = second["result"]["revision"]
                    outcome["second_names"] = [n.get("name") for n in second["result"]["nodes"]]
                if "victim" in nodes:
                    outcome["stale"] = host.call("target.act",
                                                 {"target": target,
                                                  "reference": nodes["victim"]["reference"],
                                                  "action": {"kind": "click"}})
            finally:
                host.finish()
    finally:
        server.shutdown()
    return outcome


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
    seen = {}

    def expect(name, condition, detail=None):
        checks.append({"check": name, "passed": bool(condition),
                       **({"detail": detail} if detail is not None else {})})

    for label, tamper in (("honest", ""), ("the page names __mcs first", PREEMPT),
                          ("the page nobbles defineProperty", NOBBLE)):
        result = run_case(args.binary, network, tamper)
        seen[label] = {k: v for k, v in result.items()
                       if k in ("hits", "first_names", "first_revision", "second_revision",
                                "second_names")}
        stale = result.get("stale")
        opened_ok = result.get("open", {}).get("ok")
        snapshot_ok = result.get("first", {}).get("ok")

        if label == "the page nobbles defineProperty":
            # Whatever the host does about a realm it cannot instrument, it may
            # not quietly proceed: either the open or the snapshot answers a
            # typed refusal, and no navigation happens.
            typed = ((not opened_ok) and isinstance(result["open"].get("error"), dict)) or \
                    ((not snapshot_ok) and isinstance((result.get("first") or {}).get("error"), dict))
            expect(f"I: with a page that {label}, the host refuses rather than proceeding blind",
                   typed or (stale is not None and not stale.get("ok")),
                   {"open_ok": opened_ok, "snapshot_ok": snapshot_ok,
                    "stale_ok": (stale or {}).get("ok")})
            expect(f"T: with a page that {label}, the server is asked for nothing extra",
                   "/evil.html" not in result["hits"], {"hits": result["hits"]})
            continue

        if label == "the page names __mcs first":
            # Amended 2026-09-06, before the repair landed and after the ruling
            # chose a typed refusal: these three were written expecting the
            # host to keep working while ignoring the page's object. The
            # ruling is that a taken name is refused, so a snapshot is not
            # what a correct host produces here -- a reasoned refusal is, and
            # the traffic criterion below carries the same guarantee.
            refusal = result.get("open", {}) if not opened_ok else (result.get("first") or {})
            error = refusal.get("error") or {}
            expect(f"O: with a page that {label}, the realm is refused rather than adopted",
                   (not opened_ok) or (not snapshot_ok),
                   {"open_ok": opened_ok, "snapshot_ok": snapshot_ok})
            expect(f"O: with a page that {label}, the refusal carries its own reason",
                   isinstance(error.get("details"), dict)
                   and error["details"].get("reason") == "registry_occupied",
                   {"code": error.get("code"), "reason": (error.get("details") or {}).get("reason")})
            expect(f"S: with a page that {label}, no reference is ever handed out to act on",
                   stale is None or not stale.get("ok"),
                   {"stale_ok": (stale or {}).get("ok")})
            expect(f"T: with a page that {label}, the server is never asked for the swapped URL",
                   "/evil.html" not in result["hits"], {"hits": result["hits"]})
            continue

        expect(f"O: with a page that is {label}, the agent's snapshot is the host's",
               snapshot_ok and SAFE_TEXT in json.dumps(result.get("first_names")),
               {"names": result.get("first_names")})
        expect(f"O: with a page that is {label}, the revision advances when the document changes",
               result.get("second_revision", 0) > result.get("first_revision", 0),
               {"first": result.get("first_revision"), "second": result.get("second_revision")})
        expect(f"S: with a page that is {label}, a stale reference is refused",
               stale is not None and not stale.get("ok")
               and stale["error"]["code"] == "stale_revision",
               {"code": (stale or {}).get("error", {}).get("code"),
                "ok": (stale or {}).get("ok")})
        expect(f"T: with a page that is {label}, the server is never asked for the swapped URL",
               "/evil.html" not in result["hits"], {"hits": result["hits"]})

    # The lifecycle path installs a second time; it must find its own registry
    # rather than starting a new count.
    result = run_case(args.binary, network, "")
    expect("L: the second install keeps the count rather than resetting it",
           result.get("second_revision", 0) > result.get("first_revision", -1),
           {"first": result.get("first_revision"), "second": result.get("second_revision")})

    # Source criteria: the registry stops being adopted, and nothing else moves.
    host_source = (SRC / "main.rs").read_text()
    # Amended: the installer moved from a constant to a function when it began
    # carrying a per-realm brand, and the court could not find it there.
    install = ""
    for marker in ("const INSTALL_JS: &str = r#\"", "fn install_script(brand: &str) -> String {"):
        for piece in host_source.split(marker)[1:]:
            install = piece.split('"#')[0] if marker.startswith("const") else \
                piece.split('r#"')[1].split('"#')[0]
    expect("N1: the installer no longer adopts whatever it finds",
           "if (!window.__mcs)" not in install, {"install_bytes": len(install)})
    expect("N2: the registry is installed as a defined property, not an assignment",
           "defineProperty" in install and "window.__mcs = s;" not in install,
           {"defines": "defineProperty" in install})
    # Pinned, not recorded: this work is host-side, and a shim that moved would
    # mean it was not.
    #
    # Amendment, 2026-09-06, ruled and recorded rather than absorbed. This pin
    # did its job: the registry-brand slice added no shim source, and the pin is
    # what noticed that a **later** slice did. Round C
    # (`element-tag-design-0.0.1.md`) gives the base shim a closure-owned store
    # for an element's tag, so `dom_shim_base.js` moves from
    # `145f82eef240303af3296628d409aac208850360085438710f497d84bf5ab587` to the
    # hash below. The old value is kept here so the movement can be read off the
    # file. The **main** shim is untouched by round C and its hash below is
    # unchanged; if it ever moves in a tag-only round, that round did something
    # it did not intend.
    expect("N3: the shims are untouched by this work",
           hashlib.sha256((SRC / "dom_shim_base.js").read_bytes()).hexdigest()
           == "3561e77425ba8efabc760c3355e051d6c53af4a9e5ea52228d148c40b8945adc"
           and hashlib.sha256((SRC / "dom_shim_main.js").read_bytes()).hexdigest()
           == "d319246e878b36993d7d607ec1c288f143669a1b1229a8ea60712d8f4030181c",
           {"base": hashlib.sha256((SRC / "dom_shim_base.js").read_bytes()).hexdigest()[:16]})
    expect("N4: the operation enum is unchanged at 26",
           len(check_contract.OPERATIONS_NEXT) == 26,
           {"operations": len(check_contract.OPERATIONS_NEXT)})

    receipt = {
        "court": "native-dom revision registry ownership",
        "host_sha256": hashlib.sha256(Path(args.binary).read_bytes()).hexdigest(),
        "base_sha256": hashlib.sha256((SRC / "dom_shim_base.js").read_bytes()).hexdigest(),
        "main_sha256": hashlib.sha256((SRC / "dom_shim_main.js").read_bytes()).hexdigest(),
        "ruled": {"registry": "host-minted per realm, non-writable, non-configurable,"
                              " non-enumerable, verified by every reader",
                  "page": "naming the global first must not give it the counter",
                  "unchanged": "the handle key set, the protocol, the shims, every bound"},
        "observed": seen,
        "checks": checks,
        "checks_passed": sum(1 for c in checks if c["passed"]),
        "checks_total": len(checks),
        "passed": all(c["passed"] for c in checks),
        "limitations": [
            "design-frozen court: the ownership, staleness and traffic criteria fail on a host that adopts the page's object",
            "the server's request log is the witness: a swapped navigation shows up as a fetch the agent never asked for",
            "the defineProperty case asks only that the host refuse rather than proceed blind; which typed refusal is a ruling, not a fixture",
            "two criteria read the host's source rather than the binary, so they are repo-local by design",
            "one hermetic loopback origin; no surface, no window, no AppKit",
        ],
    }
    Path(args.receipt).write_text(json.dumps(receipt, indent=1, sort_keys=True) + "\n")
    print(json.dumps({"passed": receipt["passed"], "checks_passed": receipt["checks_passed"],
                      "checks_total": receipt["checks_total"]}))
    for check in checks:
        if not check["passed"]:
            print("FAIL", json.dumps(check)[:165])
    return 0 if receipt["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
