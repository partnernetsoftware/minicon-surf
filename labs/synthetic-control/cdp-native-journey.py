#!/usr/bin/env python3
"""Dependency-free G2 court: native stdio and CDP mutate one target."""

import argparse
import base64
import http.client
import json
import os
import socket
import struct
import subprocess
import tempfile
import time
from pathlib import Path


class WebSocket:
    def __init__(self, url):
        host_port, path = url.removeprefix("ws://").split("/", 1)
        host, port = host_port.split(":")
        self.sock = socket.create_connection((host, int(port)), timeout=3)
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
    def __init__(self, binary, ready_file):
        self.process = subprocess.Popen(
            [binary, "serve", "--stdio", "--cdp-port", "0", "--ready-file", ready_file],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=None,
            text=True,
        )
        self.next_id = 0

    def call(self, operation, arguments):
        self.next_id += 1
        request_id = f"req_g2_{self.next_id}"
        request = {"protocol": "minicon-surf.control", "version": "0.0.1",
                   "request_id": request_id, "deadline_ms": 100,
                   "operation": operation, "arguments": arguments}
        self.process.stdin.write(json.dumps(request, separators=(",", ":")) + "\n")
        self.process.stdin.flush()
        response = json.loads(self.process.stdout.readline())
        assert response["request_id"] == request_id
        return response

    def finish(self):
        self.process.stdin.close()
        assert self.process.wait(timeout=5) == 0


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--binary", required=True)
    parser.add_argument("--receipt")
    parser.add_argument("--surface-receipt")
    args = parser.parse_args()
    with tempfile.TemporaryDirectory() as directory:
        ready_path = str(Path(directory) / "ready.json")
        host = Host(args.binary, ready_path)
        profile = host.call("profile.create", {"persistence": "ephemeral"})["result"]["profile"]
        session = host.call("session.open", {"profile": profile})["result"]["session"]
        target = host.call("target.open", {"session": session})["result"]["target"]
        before = host.call("target.snapshot", {"target": target, "format": "semantic",
                           "max_bytes": 65536, "max_nodes": 10})["result"]
        stale_reference = before["nodes"][1]["reference"]
        for _ in range(100):
            if Path(ready_path).exists():
                break
            time.sleep(0.01)
        ready = json.loads(Path(ready_path).read_text())
        connection = http.client.HTTPConnection("127.0.0.1", ready["cdp_port"], timeout=3)
        connection.request("GET", "/json/version")
        discovery = json.loads(connection.getresponse().read())
        ws = WebSocket(discovery["webSocketDebuggerUrl"])
        targets = ws.call(1, "Target.getTargets")["result"]["targetInfos"]
        assert [item["targetId"] for item in targets] == [target]
        cdp_session = ws.call(2, "Target.attachToTarget", {"targetId": target, "flatten": True})["result"]["sessionId"]
        root = ws.call(3, "DOM.getDocument", session_id=cdp_session)["result"]["root"]["nodeId"]
        node = ws.call(4, "DOM.querySelector", {"nodeId": root, "selector": "button"}, cdp_session)["result"]["nodeId"]
        object_id = ws.call(5, "DOM.resolveNode", {"nodeId": node}, cdp_session)["result"]["object"]["objectId"]
        ws.call(6, "Runtime.callFunctionOn", {"objectId": object_id,
                "functionDeclaration": "function(){this.click();}"}, cdp_session)
        unsupported = ws.call(7, "Page.navigate", {"url": "https://example.invalid"}, cdp_session)
        assert unsupported["error"]["code"] == -32601
        after = host.call("target.snapshot", {"target": target, "format": "semantic",
                          "max_bytes": 65536, "max_nodes": 10})["result"]
        assert after["target"] == target and after["revision"] == 1
        assert after["nodes"][1]["name"] == "Clicked"
        stale = host.call("target.act", {"target": target, "reference": stale_reference,
                         "action": {"kind": "click"}})
        assert stale["error"]["code"] == "stale_revision"
        scroll = host.call("target.act", {"target": target,
                           "reference": after["nodes"][0]["reference"],
                           "action": {"kind": "scroll", "y": 240}})["result"]
        assert scroll["revision"] == 2 and scroll["scroll_y"] == 240
        preserved = host.call("target.inspect", {"target": target})["result"]
        baseline_memory = host.call("memory.report", {})["result"]
        baseline_bytes = baseline_memory["total_accounted_bytes"]
        assert baseline_memory["owners"]["surfaces"]["objects"] == 0
        cycles = []
        for cycle in range(3):
            shown = host.call("surface.show", {"target": target})["result"]
            headed_memory = host.call("memory.report", {})["result"]
            assert headed_memory["owners"]["surfaces"]["objects"] == 1
            assert headed_memory["total_accounted_bytes"] >= baseline_bytes + 65536
            visible_targets = ws.call(20 + cycle, "Target.getTargets")["result"]["targetInfos"]
            assert visible_targets[0]["targetId"] == target and visible_targets[0]["attached"] is True
            hidden = host.call("surface.hide", {"surface": shown["surface"]})["result"]
            assert hidden["target"] == target and hidden["released_presentation_bytes"] == 65536
            headless_memory = host.call("memory.report", {})["result"]
            assert headless_memory["owners"]["surfaces"]["objects"] == 0
            assert headless_memory["total_accounted_bytes"] == baseline_bytes
            observed = host.call("target.inspect", {"target": target})["result"]
            assert observed == preserved
            cycles.append({"cycle": cycle + 1, "surface": shown["surface"],
                           "headed_surface_objects": 1, "headless_surface_objects": 0,
                           "released_presentation_bytes": hidden["released_presentation_bytes"]})
        root_after_cycles = ws.call(30, "DOM.getDocument", session_id=cdp_session)["result"]["root"]["nodeId"]
        assert root_after_cycles == 1
        ws.call(8, "Target.detachFromTarget", {"sessionId": cdp_session})
        ws.close()
        host.call("target.close", {"target": target})
        host.finish()
        receipt = {"schema": "minicon-surf.synthetic-g2-receipt/0.0.1", "status": "qualified-synthetic",
                   "client": "synthetic-g2-court-client", "transport": ["native-stdio", "cdp-loopback-websocket"],
                   "same_target_identity": True, "revision_transition": [0, 1],
                   "native_stale_revision_after_cdp_mutation": True,
                   "unsupported_method": {"method": "Page.navigate", "code": -32601},
                   "qualified_methods": ["Target.getTargets", "Target.attachToTarget", "Target.detachFromTarget",
                                         "DOM.getDocument", "DOM.querySelector", "DOM.resolveNode", "Runtime.callFunctionOn"],
                   "limitations": ["synthetic semantic target, not HTML", "custom court client, not Playwright or Puppeteer",
                                   "single CDP connection", "loopback only"]}
        encoded = json.dumps(receipt, indent=2, sort_keys=True) + "\n"
        if args.receipt:
            Path(args.receipt).write_text(encoded)
        surface_receipt = {
            "schema": "minicon-surf.synthetic-surface-receipt/0.0.1",
            "status": "mechanics-only",
            "target_identity_preserved": target,
            "native_session_preserved": preserved["session"],
            "realm_preserved": preserved["realm"],
            "revision_preserved_across_surface_cycles": preserved["revision"],
            "scroll_y_preserved": preserved["scroll_y"],
            "cdp_attachment_preserved": True,
            "cycles": cycles,
            "presentation_bytes_per_attachment": 65536,
            "logical_bytes_returned_to_baseline_after_each_hide": True,
            "limitations": ["synthetic presentation buffer, not a native window",
                            "does not exercise renderer/GPU/window-server resources",
                            "does not pass G3"],
        }
        if args.surface_receipt:
            Path(args.surface_receipt).write_text(
                json.dumps(surface_receipt, indent=2, sort_keys=True) + "\n"
            )
        print(encoded, end="")


if __name__ == "__main__":
    main()
