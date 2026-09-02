#!/usr/bin/env python3
"""Dependency-free G2 court on an HTML target: native stdio and CDP share one Servo target.

Native stdio opens the interactive fixture and snapshots revision 0. A CDP
client discovers the same target through `/json/version`, attaches with a
flattened session, resolves the button through DOM methods and clicks it with
`Runtime.callFunctionOn`. Native stdio then observes revision 1, the mutated
button, and a typed `stale_revision` for the pre-CDP reference.
"""

import argparse
import base64
import hashlib
import http.client
import json
import socket
import struct
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "protocol"))
import check_contract  # noqa: E402


class WebSocket:
    def __init__(self, url):
        host_port, path = url.removeprefix("ws://").split("/", 1)
        host, port = host_port.split(":")
        self.sock = socket.create_connection((host, int(port)), timeout=10)
        key = base64.b64encode(b"minicon-surf-g2").decode()
        request = (
            f"GET /{path} HTTP/1.1\r\nHost: {host_port}\r\nUpgrade: websocket\r\n"
            f"Connection: Upgrade\r\nSec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n"
        )
        self.sock.sendall(request.encode())
        response = self._until(b"\r\n\r\n")
        assert response.startswith(b"HTTP/1.1 101 "), response

    def _until(self, suffix):
        data = bytearray()
        while not data.endswith(suffix):
            data.extend(self.sock.recv(1))
        return bytes(data)

    def _exact(self, length):
        data = bytearray()
        while len(data) < length:
            chunk = self.sock.recv(length - len(data))
            if not chunk:
                raise EOFError("WebSocket closed mid-frame")
            data.extend(chunk)
        return bytes(data)

    def call(self, request_id, method, params=None, session_id=None):
        message = {"id": request_id, "method": method, "params": params or {}}
        if session_id:
            message["sessionId"] = session_id
        payload = json.dumps(message, separators=(",", ":")).encode()
        mask = b"G2ok"
        header = bytearray([0x81])
        if len(payload) < 126:
            header.append(0x80 | len(payload))
        else:
            header.append(0x80 | 126)
            header.extend(struct.pack("!H", len(payload)))
        header.extend(mask)
        header.extend(byte ^ mask[index % 4] for index, byte in enumerate(payload))
        self.sock.sendall(header)
        first = self._exact(2)
        assert first[0] & 0x0F == 1, first
        length = first[1] & 0x7F
        if length == 126:
            length = struct.unpack("!H", self._exact(2))[0]
        response = json.loads(self._exact(length))
        assert response["id"] == request_id
        return response

    def close(self):
        self.sock.sendall(b"\x88\x80G2ok")
        self.sock.close()


class Host:
    def __init__(self, binary, fixture_root, directory):
        self.ready_path = Path(directory) / "ready.json"
        self.process = subprocess.Popen(
            [binary, "serve", "--stdio", "--fixture-root", str(fixture_root),
             "--config-dir", str(Path(directory) / "config"), "--cdp-port", "0",
             "--ready-file", str(self.ready_path)],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
        )
        self.next_id = 0
        self.transcript = []

    def call(self, operation, arguments, deadline_ms=10000):
        self.next_id += 1
        request = {"protocol": "minicon-surf.control", "version": "0.0.1",
                   "request_id": f"req_g2_{self.next_id}", "deadline_ms": deadline_ms,
                   "operation": operation, "arguments": arguments}
        check_contract.validate_request(request)
        self.process.stdin.write(json.dumps(request, separators=(",", ":")) + "\n")
        self.process.stdin.flush()
        line = self.process.stdout.readline()
        if not line:
            raise RuntimeError(f"host exited during {operation}")
        response = json.loads(line)
        check_contract.validate_response(response)
        assert response["request_id"] == request["request_id"]
        self.transcript.append({"door": "native", "operation": operation, "response": response})
        return response

    def ready(self):
        for _ in range(500):
            if self.ready_path.exists():
                return json.loads(self.ready_path.read_text())
            time.sleep(0.01)
        raise RuntimeError("CDP ready file did not appear")

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
    args = parser.parse_args()
    binary = Path(args.binary)
    fixture_root = Path(args.fixture_root)
    checks = []
    cdp_transcript = []
    with tempfile.TemporaryDirectory(prefix="minicon-surf-servo-g2-") as directory:
        host = Host(str(binary), fixture_root, directory)
        profile = host.call("profile.create", {"persistence": "ephemeral"})["result"]["profile"]
        session = host.call("session.open", {"profile": profile})["result"]["session"]
        target = host.call("target.open", {"session": session, "fixture": "semantic-interactive.html"},
                           30000)["result"]["target"]
        before = host.call("target.snapshot", {"target": target, "format": "semantic",
                                               "max_bytes": 65536, "max_nodes": 64})["result"]
        expect(checks, "native snapshot starts at revision 0", before["revision"] == 0, before["revision"])
        button = next(n for n in before["nodes"] if n["role"] == "button")
        stale_reference = button["reference"]

        ready = host.ready()
        connection = http.client.HTTPConnection("127.0.0.1", ready["cdp_port"], timeout=10)
        connection.request("GET", "/json/version")
        discovery = json.loads(connection.getresponse().read())
        expect(checks, "CDP 1.3 discovery names a loopback browser endpoint",
               discovery.get("Protocol-Version") == "1.3"
               and discovery["webSocketDebuggerUrl"].startswith("ws://127.0.0.1:"), discovery)
        connection = http.client.HTTPConnection("127.0.0.1", ready["cdp_port"], timeout=10)
        connection.request("GET", "/json/list")
        listed = json.loads(connection.getresponse().read())
        expect(checks, "/json/list exposes exactly the native-created target",
               [item["id"] for item in listed] == [target], listed)

        ws = WebSocket(discovery["webSocketDebuggerUrl"])

        def cdp(request_id, method, params=None, session_id=None):
            response = ws.call(request_id, method, params, session_id)
            cdp_transcript.append({"door": "cdp", "method": method, "response": response})
            return response

        targets = cdp(1, "Target.getTargets")["result"]["targetInfos"]
        expect(checks, "Target.getTargets returns the native target id",
               [item["targetId"] for item in targets] == [target] and targets[0]["attached"] is False, targets)
        cdp_session = cdp(2, "Target.attachToTarget", {"targetId": target, "flatten": True})["result"]["sessionId"]
        attached = cdp(3, "Target.getTargets")["result"]["targetInfos"]
        expect(checks, "attached flag reflects the flattened session", attached[0]["attached"] is True, attached)
        root = cdp(4, "DOM.getDocument", session_id=cdp_session)["result"]["root"]
        expect(checks, "DOM.getDocument returns a document root with child count",
               root["nodeId"] == 1 and root["childNodeCount"] == len(before["nodes"]), root)
        node = cdp(5, "DOM.querySelector", {"nodeId": 1, "selector": "#continue"}, cdp_session)["result"]["nodeId"]
        expect(checks, "DOM.querySelector resolves #continue to a node id", node >= 2, node)
        object_id = cdp(6, "DOM.resolveNode", {"nodeId": node}, cdp_session)["result"]["object"]["objectId"]
        clicked = cdp(7, "Runtime.callFunctionOn", {"objectId": object_id,
                                                    "functionDeclaration": "function(){this.click();}"}, cdp_session)
        expect(checks, "Runtime.callFunctionOn click succeeds", "result" in clicked, clicked)
        unsupported = cdp(8, "Page.navigate", {"url": "https://example.invalid"}, cdp_session)
        expect(checks, "Page.navigate is an explicit -32601",
               unsupported.get("error", {}).get("code") == -32601, unsupported)
        bad_selector = cdp(9, "DOM.querySelector", {"nodeId": 1, "selector": "div > span"}, cdp_session)
        expect(checks, "unqualified selector is an explicit -32602",
               bad_selector.get("error", {}).get("code") == -32602, bad_selector)

        after = host.call("target.snapshot", {"target": target, "format": "semantic",
                                              "max_bytes": 65536, "max_nodes": 64})["result"]
        names = [(n["role"], n["name"]) for n in after["nodes"]]
        expect(checks, "native stdio observes revision 1 after the CDP click",
               after["target"] == target and after["revision"] == 1, after["revision"])
        expect(checks, "native snapshot shows the CDP-clicked button and status text",
               ("button", "Clicked") in names and ("text", "Continued") in names, names)
        stale = host.call("target.act", {"target": target, "reference": stale_reference,
                                         "action": {"kind": "click"}})
        expect(checks, "pre-CDP native reference is stale_revision",
               not stale["ok"] and stale["error"]["code"] == "stale_revision"
               and stale["error"]["details"] == {"reference_revision": 0, "current_revision": 1}, stale)
        second_click = cdp(10, "Runtime.callFunctionOn", {"objectId": object_id,
                                                          "functionDeclaration": "function(){this.click();}"}, cdp_session)
        expect(checks, "CDP remote object from revision 0 fails after the mutation",
               second_click.get("error", {}).get("code") == -32000, second_click)
        detached = cdp(11, "Target.detachFromTarget", {"sessionId": cdp_session})
        expect(checks, "Target.detachFromTarget succeeds", "result" in detached, detached)
        ws.close()

        closed = host.call("target.close", {"target": target})
        expect(checks, "native close succeeds", closed["ok"])
        host.call("session.close", {"session": session})
        exit_code = host.finish()
        expect(checks, "host exits cleanly", exit_code == 0, exit_code)

    passed = all(check["passed"] for check in checks)
    receipt = {
        "schema": "minicon-surf.servo-control-g2-receipt/0.0.1",
        "status": "observed" if passed else "failed",
        "technology": "servo",
        "technology_version": "0.5.0",
        "crate_sha256": "331e15df72165ca15b3945970c6870c4b7367be116ded058fda4f41190b265b8",
        "binary_sha256": hashlib.sha256(binary.read_bytes()).hexdigest(),
        "control_contract": "0.0.1",
        "client": "servo-g2-court-client",
        "transport": ["native-stdio", "cdp-loopback-websocket"],
        "workload": {
            "id": "W7",
            "fixture": "semantic-interactive.html",
            "fixture_sha256": hashlib.sha256((fixture_root / "semantic-interactive.html").read_bytes()).hexdigest(),
        },
        "same_target_identity": all(c["passed"] for c in checks if "target id" in c["check"] or "native-created" in c["check"]),
        "revision_transition": [0, 1],
        "qualified_methods": ["Target.getTargets", "Target.attachToTarget", "Target.detachFromTarget",
                              "DOM.getDocument", "DOM.querySelector", "DOM.resolveNode", "Runtime.callFunctionOn"],
        "unsupported_method": {"method": "Page.navigate", "code": -32601},
        "checks": checks,
        "passed": passed,
        "transcript": host.transcript + cdp_transcript,
        "limitations": [
            "custom court client, not Playwright or Puppeteer",
            "single CDP connection; loopback only",
            "DOM.querySelector qualifies only `button` and `#id` selectors over the semantic snapshot",
            "CDP node and object ids are adapter mappings onto native revision-scoped references, not engine DOM ids",
            "navigation, frames, Input and Network domains are not offered",
        ],
    }
    encoded = json.dumps(receipt, indent=2, sort_keys=True) + "\n"
    if args.receipt:
        Path(args.receipt).write_text(encoded, encoding="utf-8")
    print(encoded, end="")
    sys.exit(0 if passed else 1)


if __name__ == "__main__":
    main()
