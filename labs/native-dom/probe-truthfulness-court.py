#!/usr/bin/env python3
"""The frozen court for the court realm probe's truthfulness.

Frozen from `realm-probe-audit-0.0.1.md` §6 and the ruling that followed it,
**before** the probe's two page-replaceable calls become syntax, and failing
until they do.

The probe answers two questions about the internals handle: whether a property
of that name exists (`typeof`, which no tampering has been able to move) and
whether it is enumerable (`Object.keys(window).indexOf(...) >= 0`, which a page
can move in both directions). This court states, for each scenario, **what is
actually true**, and requires the probe to say it.

The audit's other finding is carried here as a standing criterion rather than a
claim: whatever a page does, the pair can only fail a court, never clear one.

What this court may not disturb, and checks it has not: `SEAL_JS` stays syntax
with no method call; the probe's other five answers keep working; and nothing
about host answers, permissions, budgets, navigation or downloads is touched --
this probe reaches none of them, which is the audit's §1.

Strictly headless: no surface binary, no window, no AppKit, and it refuses to
run with the visible-court variable set. The probe needs its two court flags,
so the court passes them and writes the court file into a temporary directory.

Groups: truth, tamper-resistance, containment, source.
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
HANDLE = "__mcsInternals"

# Each scenario: what the page does, and what is *true* afterwards about a
# property of that name on window. The probe must report the truth.
SCENARIOS = [
    ("nothing", "", False, False),
    ("re-adds the name plainly", f"window.{HANDLE} = function () {{}};", True, True),
    ("re-adds it non-enumerable",
     f"Object.defineProperty(window, '{HANDLE}', {{ value: 1, enumerable: false, configurable: true }});",
     True, False),
    ("re-adds it and blinds Object.keys",
     f"window.{HANDLE} = function () {{}}; Object.keys = function () {{ return []; }};",
     True, True),
    ("re-adds it and blinds indexOf",
     f"window.{HANDLE} = function () {{}}; Array.prototype.indexOf = function () {{ return -1; }};",
     True, True),
    ("blinds indexOf into always finding",
     "Array.prototype.indexOf = function () { return 0; };", False, False),
    ("blinds Object.keys with nothing there",
     "Object.keys = function () { return []; };", False, False),
]


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


def find(structure, key):
    if isinstance(structure, dict):
        if key in structure:
            return structure[key]
        for value in structure.values():
            found = find(value, key)
            if found is not None:
                return found
    elif isinstance(structure, list):
        for value in structure:
            found = find(value, key)
            if found is not None:
                return found
    return None


class Host:
    def __init__(self, binary, directory, origin):
        environment = dict(os.environ)
        for knob in ("MINICON_SURF_NATIVE_REALM_ZONE", "MINICON_SURF_NATIVE_REALM_ARENA",
                     "MINICON_SURF_PROFILE_STORE", VISIBLE_ENV):
            environment.pop(knob, None)
        command = [binary, "serve", "--stdio", "--fixture-root", str(FIXTURE_ROOT),
                   "--config-dir", str(Path(directory) / "config"), "--allow-origin", origin,
                   "--surface-court-file", str(Path(directory) / "court.log"),
                   "--court-realm-probe", "1"]
        self.process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                        stderr=subprocess.DEVNULL, text=True, env=environment)
        self.counter = 0

    def call(self, operation, arguments):
        self.counter += 1
        request = {"protocol": "minicon-surf.control", "version": "0.0.2",
                   "request_id": f"req_pt_{self.counter}", "deadline_ms": 30000,
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

    for label, tamper, true_present, true_enumerable in SCENARIOS:
        page = ("<!doctype html><html><body><main><p id=\"p\">page</p></main><script>"
                + tamper + "</script></body></html>").encode()

        class Handler(network.Handler):
            def do_GET(self):
                path, _, _query = self.path.partition("?")
                network.Handler.hits.append(path)
                return self.reply(200, page, "text/html")

        server = network.Server(("127.0.0.1", 0), Handler)
        origin = f"http://127.0.0.1:{server.server_address[1]}"
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            with tempfile.TemporaryDirectory(prefix="minicon-surf-probe-") as directory:
                host = Host(args.binary, directory, origin)
                try:
                    profile = host.ok("profile.create", {"persistence": "ephemeral"})["profile"]
                    session = host.ok("session.open", {"profile": profile})["session"]
                    host.ok("target.open", {"session": session, "url": origin + "/a.html"})
                    report = host.ok("memory.report", {})
                    present = find(report, "main_present")
                    enumerable = find(report, "main_enumerable")
                    observed[label] = {"present": present, "enumerable": enumerable,
                                       "true_present": true_present,
                                       "true_enumerable": true_enumerable}
                    expect(f"T: with the page that {label}, `present` is true to the realm",
                           present == true_present,
                           {"reported": present, "true": true_present})
                    expect(f"T: with the page that {label}, `enumerable` is true to the realm",
                           enumerable == true_enumerable,
                           {"reported": enumerable, "true": true_enumerable})
                    # Containment: whatever the page did, a real seal failure is
                    # still visible. The pair may never both read false while a
                    # property of that name exists.
                    expect(f"C: with the page that {label}, a present handle cannot read as absent",
                           not (true_present and present is False and enumerable is False),
                           {"reported": (present, enumerable), "true": (true_present,
                                                                        true_enumerable)})
                finally:
                    host.finish()
        finally:
            server.shutdown()

    # Source criteria: the repair may only change how the probe asks, and must
    # leave the seal and the probe's other answers alone.
    source = HOST_SOURCE.read_text()
    probe = ""
    for piece in source.split('const REALM_PROBE_JS: &str = r#"')[1:]:
        probe = piece.split('"#')[0]
    seal = ""
    for piece in source.split('const SEAL_JS: &str =')[1:]:
        seal = piece.split('"#')[0]
    expect("S1: the probe asks its two handle questions without a replaceable call",
           "Object.keys" not in probe and ".indexOf(" not in probe,
           {"object_keys": "Object.keys" in probe, "index_of": ".indexOf(" in probe})
    # Amendment, 2026-09-06, recorded rather than substituted. S2 was frozen as
    # `probe.count("String(") >= 7`, a spelling that stood in for "seven
    # questions are still asked". The signature-integrity slice had to remove
    # every `String(` from this script, because the global `String` is the
    # page's and a page that swapped it flipped all seven reported fields
    # (`signature-integrity-design-0.0.1.md` §4). The proxy therefore reads 0 on
    # a probe that answers all seven correctly. The criterion below measures the
    # property the old one stood for, and measures it more strictly: the seven
    # questions are each present by name and exactly six literal separators join
    # them. The old spelling is left here in the record and not in the check.
    questions = ["__mcsInternals", "classList", "CustomEvent", "isTrusted",
                 "appendChild", "closest", "dataset"]
    expect("S2: the probe still answers its other five questions",
           all(name in probe for name in questions) and probe.count('+ ":" +') == 6
           and "String(" not in probe and ".join(" not in probe,
           {"missing": [name for name in questions if name not in probe],
            "separators": probe.count('+ ":" +'),
            "legacy_String_calls": probe.count("String(")})
    expect("S3: the seal is untouched and still contains no method call",
           "delete window.__mcsInternals" in seal and "Object." not in seal
           and ".indexOf(" not in seal,
           {"seal_present": "delete window.__mcsInternals" in seal})
    expect("S4: the probe stays double-gated behind the court file",
           "--court-realm-probe: refused without --surface-court-file" in source, None)

    receipt = {
        "court": "native-dom court realm probe truthfulness",
        "host_sha256": hashlib.sha256(Path(args.binary).read_bytes()).hexdigest(),
        "ruled": {"repair": "the two handle questions are asked with syntax, not with"
                            " Object.keys(...).indexOf(...)",
                  "untouched": "SEAL_JS, host answers, permissions, budgets, navigation, downloads"},
        "observed": observed,
        "checks": checks,
        "checks_passed": sum(1 for c in checks if c["passed"]),
        "checks_total": len(checks),
        "passed": all(c["passed"] for c in checks),
        "limitations": [
            "design-frozen court: the tampering scenarios fail until the probe stops asking through Object.keys and indexOf",
            "each scenario states the truth about a property of that name, and the probe is judged against it, not against another run",
            "the containment criterion carries the audit's finding that this channel can only fail a court, never clear one",
            "the probe needs both court flags, so this court passes them and writes the court file into a temporary directory",
            "two criteria read the host's source rather than the binary, so they are repo-local by design",
            "nothing here touches SEAL_JS, and nothing here can reach a host answer: the probe feeds only memory.report's court section",
        ],
    }
    Path(args.receipt).write_text(json.dumps(receipt, indent=1, sort_keys=True) + "\n")
    print(json.dumps({"passed": receipt["passed"], "checks_passed": receipt["checks_passed"],
                      "checks_total": receipt["checks_total"]}))
    for check in checks:
        if not check["passed"]:
            print("FAIL", json.dumps(check)[:150])
    return 0 if receipt["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
