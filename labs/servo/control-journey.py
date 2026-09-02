#!/usr/bin/env python3
"""Drive the Servo control 0.0.1 host through one native W7 journey.

Every request and response is validated with the repository's dependency-free
contract checker. The journey opens an ephemeral profile and one session,
opens the interactive court fixture as a target, snapshots revision 0, clicks
the button through its revision-scoped reference, waits for revision 1 without
sleeping, re-snapshots, proves the old reference is rejected as
`stale_revision`, checks typed refusals, reports memory, and closes everything.
"""

import argparse
import hashlib
import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "protocol"))
import check_contract  # noqa: E402


class Host:
    def __init__(self, binary, fixture_root, config_dir):
        self.process = subprocess.Popen(
            [binary, "serve", "--stdio", "--fixture-root", str(fixture_root),
             "--config-dir", str(config_dir)],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
        )
        self.counter = 0
        self.transcript = []

    def call(self, operation, arguments, deadline_ms=10000, raw=None):
        self.counter += 1
        request = raw if raw is not None else {
            "protocol": "minicon-surf.control", "version": "0.0.1",
            "request_id": f"req_{self.counter}", "deadline_ms": deadline_ms,
            "operation": operation, "arguments": arguments,
        }
        if raw is None:
            check_contract.validate_request(request)
        started = time.monotonic()
        self.process.stdin.write(json.dumps(request) + "\n")
        self.process.stdin.flush()
        line = self.process.stdout.readline()
        elapsed_ms = round((time.monotonic() - started) * 1000, 3)
        if not line:
            raise RuntimeError(f"host exited during {operation}")
        if len(line.encode()) > check_contract.MAX_RESPONSE_BYTES:
            raise RuntimeError("response exceeds byte limit")
        response = json.loads(line)
        check_contract.validate_response(response)
        if raw is None and response["request_id"] != request["request_id"]:
            raise RuntimeError("response request_id differs")
        self.transcript.append({
            "operation": operation, "arguments": arguments if raw is None else raw,
            "response": response, "elapsed_ms": elapsed_ms,
        })
        return response

    def finish(self):
        self.process.stdin.close()
        return self.process.wait(timeout=30)


def expect(checks, name, condition, detail=None):
    checks.append({"check": name, "passed": bool(condition), "detail": detail})
    if not condition:
        print(f"FAILED: {name}: {detail}", file=sys.stderr)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--binary", required=True)
    parser.add_argument("--fixture-root", default=str(ROOT / "labs" / "court" / "fixtures"))
    parser.add_argument("--receipt")
    parser.add_argument("--technology", default="servo")
    parser.add_argument("--technology-version", default="0.5.0")
    parser.add_argument("--artifact-sha256", default="331e15df72165ca15b3945970c6870c4b7367be116ded058fda4f41190b265b8",
                        help="pinned engine artifact digest recorded in the receipt")
    args = parser.parse_args()
    binary = Path(args.binary)
    fixture_root = Path(args.fixture_root)
    checks = []
    with tempfile.TemporaryDirectory(prefix="minicon-surf-servo-control-") as directory:
        host = Host(str(binary), fixture_root, Path(directory) / "config")

        profile = host.call("profile.create", {"persistence": "ephemeral", "name": "court"})
        expect(checks, "ephemeral profile created", profile["ok"] and profile["result"]["created"])
        profile_id = profile["result"]["profile"]

        persistent = host.call("profile.create", {"persistence": "persistent"})
        expect(checks, "persistent profile refused with unsupported_capability",
               not persistent["ok"] and persistent["error"]["code"] == "unsupported_capability")

        session = host.call("session.open", {"profile": profile_id})
        expect(checks, "session opened", session["ok"])
        session_id = session["result"]["session"]

        second = host.call("session.open", {"profile": profile_id})
        expect(checks, "second session refused with resource_limit",
               not second["ok"] and second["error"]["code"] == "resource_limit")

        missing = host.call("target.open", {"session": session_id, "fixture": "does-not-exist.html"})
        expect(checks, "unknown fixture is not_found",
               not missing["ok"] and missing["error"]["code"] == "not_found")

        target = host.call("target.open", {"session": session_id, "fixture": "semantic-interactive.html"}, 30000)
        expect(checks, "interactive fixture target opened at revision 0",
               target["ok"] and target["result"]["revision"] == 0, target)
        target_id = target["result"]["target"]

        snapshot = host.call("target.snapshot", {"target": target_id, "format": "semantic",
                                                 "max_bytes": 65536, "max_nodes": 64})
        check_contract.validate_snapshot(snapshot["result"])
        nodes = snapshot["result"]["nodes"]
        roles = [(n["role"], n["name"]) for n in nodes]
        expect(checks, "snapshot revision 0 lists heading, label, textbox, button, link",
               snapshot["result"]["revision"] == 0 and roles == [
                   ("heading", "Memory and Agent Court"), ("label", "Query"),
                   ("textbox", "Query"), ("button", "Continue"), ("link", "Example result")], roles)
        textbox = next(n for n in nodes if n["role"] == "textbox")
        expect(checks, "textbox carries its value", textbox.get("value") == "bounded browser", textbox)
        button = next(n for n in nodes if n["role"] == "button")
        reference = button["reference"]
        expect(checks, "node references are revision-scoped compound IDs",
               reference["target"] == target_id and reference["revision"] == 0
               and reference["node"].startswith("node_"))

        heading = next(n for n in nodes if n["role"] == "heading")
        non_button = host.call("target.act", {"target": target_id, "reference": heading["reference"],
                                              "action": {"kind": "click"}})
        expect(checks, "clicking a heading is unsupported_capability",
               not non_button["ok"] and non_button["error"]["code"] == "unsupported_capability")

        act = host.call("target.act", {"target": target_id, "reference": reference,
                                       "action": {"kind": "click"}})
        expect(checks, "button click applied and revision advanced past 0",
               act["ok"] and act["result"]["applied"] and act["result"]["revision"] >= 1, act)
        advanced = act["result"]["revision"] if act["ok"] else 0

        wait = host.call("target.wait", {"target": target_id,
                                         "condition": {"kind": "revision_at_least", "revision": 1}})
        expect(checks, "wait observes revision_at_least 1 without a caller sleep",
               wait["ok"] and wait["result"]["matched"] and wait["result"]["revision"] >= 1, wait)

        unmet = host.call("target.wait", {"target": target_id,
                                          "condition": {"kind": "revision_at_least", "revision": advanced + 100}},
                          deadline_ms=300)
        expect(checks, "unmet wait is a typed deadline_exceeded",
               not unmet["ok"] and unmet["error"]["code"] == "deadline_exceeded"
               and unmet["error"]["retryable"] is True, unmet)

        stale = host.call("target.act", {"target": target_id, "reference": reference,
                                         "action": {"kind": "click"}})
        expect(checks, "reused revision-0 reference is stale_revision with revisions in details",
               not stale["ok"] and stale["error"]["code"] == "stale_revision"
               and stale["error"]["details"]["reference_revision"] == 0
               and stale["error"]["details"]["current_revision"] >= 1, stale)

        after = host.call("target.snapshot", {"target": target_id, "format": "semantic",
                                              "max_bytes": 65536, "max_nodes": 64})
        check_contract.validate_snapshot(after["result"])
        after_roles = [(n["role"], n["name"]) for n in after["result"]["nodes"]]
        expect(checks, "post-click snapshot shows Clicked button and Continued status text",
               after["result"]["revision"] >= 1 and ("button", "Clicked") in after_roles
               and ("text", "Continued") in after_roles, after_roles)

        bounded = host.call("target.snapshot", {"target": target_id, "format": "semantic",
                                                "max_bytes": 65536, "max_nodes": 2})
        expect(checks, "max_nodes truncates the snapshot explicitly",
               bounded["ok"] and bounded["result"]["truncated"] is True
               and len(bounded["result"]["nodes"]) == 2, bounded["result"])

        inspect = host.call("target.inspect", {"target": target_id})
        expect(checks, "target.inspect reports fixture, revision and load state",
               inspect["ok"] and inspect["result"]["fixture"] == "semantic-interactive.html"
               and inspect["result"]["revision"] >= 1 and inspect["result"]["load_complete"], inspect)

        memory = host.call("memory.report", {})
        expect(checks, "memory.report answers with a report or a typed unsupported_capability",
               (memory["ok"] and memory["result"]["kind"] == "memory_report")
               or (not memory["ok"] and memory["error"]["code"] == "unsupported_capability"), memory)
        memory_reported = bool(memory["ok"])

        concurrent = host.call("target.open", {"session": session_id, "fixture": "semantic-static.html"}, 30000)
        expect(checks, "second concurrent target either opens or is a typed resource_limit",
               concurrent["ok"] or concurrent["error"]["code"] == "resource_limit", concurrent)
        concurrent_targets_supported = bool(concurrent["ok"])
        if concurrent["ok"]:
            host.call("target.close", {"target": concurrent["result"]["target"]})

        screenshot = host.call("target.screenshot", {"target": target_id})
        expect(checks, "target.screenshot is unsupported_operation",
               not screenshot["ok"] and screenshot["error"]["code"] == "unsupported_operation")
        trim = host.call("memory.trim", {})
        expect(checks, "memory.trim is unsupported_operation",
               not trim["ok"] and trim["error"]["code"] == "unsupported_operation")

        malformed = host.call("invalid", {}, raw={"protocol": "minicon-surf.control", "version": "0.0.1",
                                                  "request_id": "req_bad", "deadline_ms": 1000,
                                                  "operation": "target.explode", "arguments": {}})
        expect(checks, "unknown operation is invalid_request",
               not malformed["ok"] and malformed["error"]["code"] == "invalid_request")

        closed = host.call("target.close", {"target": target_id})
        expect(checks, "target closed", closed["ok"])
        gone = host.call("target.snapshot", {"target": target_id, "format": "semantic",
                                             "max_bytes": 65536, "max_nodes": 64})
        expect(checks, "closed target is not_found",
               not gone["ok"] and gone["error"]["code"] == "not_found")

        scripted = host.call("target.open", {"session": session_id, "fixture": "semantic-scripted.html"}, 30000)
        scripted_roles = None
        if scripted["ok"]:
            scripted_snapshot = host.call("target.snapshot", {"target": scripted["result"]["target"], "format": "semantic",
                                                              "max_bytes": 65536, "max_nodes": 64})
            if scripted_snapshot["ok"]:
                scripted_roles = [(n["role"], n["name"]) for n in scripted_snapshot["result"]["nodes"]]
            host.call("target.close", {"target": scripted["result"]["target"]})
        expect(checks, "W2 scripted fixture snapshot shows the script-built heading and button",
               scripted_roles == [("heading", "After script"), ("button", "Agent visible action")], scripted_roles)

        session_closed = host.call("session.close", {"session": session_id})
        expect(checks, "session closed", session_closed["ok"])
        exit_code = host.finish()
        expect(checks, "host exits cleanly on stdin close", exit_code == 0, exit_code)

    passed = all(check["passed"] for check in checks)
    receipt = {
        "schema": "minicon-surf.servo-control-journey-receipt/0.0.1",
        "status": "observed" if passed else "failed",
        "technology": args.technology,
        "technology_version": args.technology_version,
        "artifact_sha256": args.artifact_sha256,
        "host_sha256": hashlib.sha256(binary.read_bytes()).hexdigest(),
        "control_contract": "0.0.1",
        "memory_report_offered": memory_reported,
        "concurrent_targets_supported": concurrent_targets_supported,
        "platform": {"os": "macos", "architecture": "arm64"},
        "workload": {
            "id": "W7-native",
            "fixture": "semantic-interactive.html",
            "fixture_sha256": hashlib.sha256((fixture_root / "semantic-interactive.html").read_bytes()).hexdigest(),
            "transport": "NDJSON on stdio; fixture bytes as percent-encoded data URL",
        },
        "checks": checks,
        "passed": passed,
        "transcript": host.transcript,
        "limitations": [
            "native stdio only; no CDP edge shares this target yet",
            "click and revision_at_least are the only action and condition kinds",
            "revision is driven by a MutationObserver installed after load; navigation is not covered",
            "one session per host process; profiles are ephemeral and not engine cookie jars",
            "rendering and process model belong to the named technology, not to this journey",
        ],
    }
    encoded = json.dumps(receipt, indent=2, sort_keys=True) + "\n"
    if args.receipt:
        Path(args.receipt).write_text(encoded, encoding="utf-8")
    print(encoded, end="")
    sys.exit(0 if passed else 1)


if __name__ == "__main__":
    main()
