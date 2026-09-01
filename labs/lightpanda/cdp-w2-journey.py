#!/usr/bin/env python3
"""Dependency-free CDP W2 journey for the pinned Lightpanda lab artifact."""

import argparse
import base64
import hashlib
import json
import os
import socket
import struct
import urllib.parse


class WebSocket:
    def __init__(self, url: str, timeout: float):
        parsed = urllib.parse.urlsplit(url)
        if parsed.scheme != "ws" or parsed.hostname not in ("127.0.0.1", "localhost"):
            raise ValueError("court only permits a loopback ws:// endpoint")
        self.sock = socket.create_connection((parsed.hostname, parsed.port), timeout)
        self.sock.settimeout(timeout)
        nonce = base64.b64encode(os.urandom(16)).decode("ascii")
        path = parsed.path or "/"
        if parsed.query:
            path += "?" + parsed.query
        request = (
            f"GET {path} HTTP/1.1\r\nHost: {parsed.hostname}:{parsed.port}\r\n"
            f"Upgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Key: {nonce}\r\n"
            "Sec-WebSocket-Version: 13\r\n\r\n"
        )
        self.sock.sendall(request.encode("ascii"))
        response = self._until(b"\r\n\r\n")
        if not response.startswith(b"HTTP/1.1 101"):
            raise RuntimeError("CDP WebSocket upgrade failed")

    def _until(self, marker: bytes) -> bytes:
        data = b""
        while marker not in data:
            chunk = self.sock.recv(4096)
            if not chunk:
                raise EOFError("unexpected WebSocket EOF")
            data += chunk
        return data

    def _exact(self, size: int) -> bytes:
        data = b""
        while len(data) < size:
            chunk = self.sock.recv(size - len(data))
            if not chunk:
                raise EOFError("unexpected WebSocket EOF")
            data += chunk
        return data

    def send_json(self, value):
        payload = json.dumps(value, separators=(",", ":")).encode("utf-8")
        mask = os.urandom(4)
        first = bytes([0x81])
        length = len(payload)
        if length < 126:
            header = first + bytes([0x80 | length])
        elif length < 65536:
            header = first + bytes([0x80 | 126]) + struct.pack("!H", length)
        else:
            header = first + bytes([0x80 | 127]) + struct.pack("!Q", length)
        masked = bytes(byte ^ mask[index % 4] for index, byte in enumerate(payload))
        self.sock.sendall(header + mask + masked)

    def recv_json(self):
        while True:
            first, second = self._exact(2)
            opcode = first & 0x0F
            length = second & 0x7F
            if length == 126:
                length = struct.unpack("!H", self._exact(2))[0]
            elif length == 127:
                length = struct.unpack("!Q", self._exact(8))[0]
            if second & 0x80:
                mask = self._exact(4)
            else:
                mask = None
            payload = self._exact(length)
            if mask:
                payload = bytes(byte ^ mask[index % 4] for index, byte in enumerate(payload))
            if opcode == 0x9:
                self._send_control(0xA, payload)
                continue
            if opcode == 0x8:
                raise EOFError("CDP WebSocket closed")
            if opcode != 0x1:
                continue
            return json.loads(payload.decode("utf-8"))

    def _send_control(self, opcode: int, payload: bytes):
        mask = os.urandom(4)
        masked = bytes(byte ^ mask[index % 4] for index, byte in enumerate(payload))
        self.sock.sendall(bytes([0x80 | opcode, 0x80 | len(payload)]) + mask + masked)

    def close(self):
        self.sock.close()


class CDP:
    def __init__(self, websocket: WebSocket):
        self.websocket = websocket
        self.next_id = 1

    def call(self, method, params=None, session_id=None):
        call_id = self.next_id
        self.next_id += 1
        message = {"id": call_id, "method": method}
        if params is not None:
            message["params"] = params
        if session_id is not None:
            message["sessionId"] = session_id
        self.websocket.send_json(message)
        while True:
            response = self.websocket.recv_json()
            if response.get("id") != call_id:
                continue
            if "error" in response:
                raise RuntimeError(f"{method} failed: {response['error']}")
            return response.get("result", {})


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--endpoint", required=True)
    parser.add_argument("--fixture", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    fixture = open(args.fixture, "rb").read()
    fixture_url = "data:text/html," + urllib.parse.quote_from_bytes(fixture, safe="")
    websocket = WebSocket(args.endpoint, timeout=10.0)
    cdp = CDP(websocket)
    try:
        created = cdp.call("Target.createTarget", {"url": "about:blank"})
        target_id = created["targetId"]
        attached = cdp.call(
            "Target.attachToTarget", {"targetId": target_id, "flatten": True}
        )
        session_id = attached["sessionId"]
        cdp.call("Page.enable", session_id=session_id)
        cdp.call("Runtime.enable", session_id=session_id)
        cdp.call("DOM.enable", session_id=session_id)
        cdp.call("Page.navigate", {"url": fixture_url}, session_id)

        state = cdp.call(
            "Runtime.evaluate",
            {
                "expression": "JSON.stringify({heading:document.querySelector('h1')?.textContent,button:document.querySelector('button')?.textContent,court:document.documentElement.dataset.court})",
                "returnByValue": True,
                "awaitPromise": True,
            },
            session_id,
        )
        before = json.loads(state["result"]["value"])
        expected = {
            "heading": "After script",
            "button": "Agent visible action",
            "court": "script-complete",
        }
        if before != expected:
            raise AssertionError(f"unexpected post-script state: {before!r}")

        root = cdp.call("DOM.getDocument", {"depth": 1}, session_id)["root"]
        button_id = cdp.call(
            "DOM.querySelector", {"nodeId": root["nodeId"], "selector": "button"}, session_id
        )["nodeId"]
        resolved = cdp.call("DOM.resolveNode", {"nodeId": button_id}, session_id)
        object_id = resolved["object"]["objectId"]
        cdp.call(
            "Runtime.callFunctionOn",
            {
                "objectId": object_id,
                "functionDeclaration": "function(){this.dataset.agentAction='clicked';this.textContent='Agent action complete';}",
                "returnByValue": True,
            },
            session_id,
        )
        after_result = cdp.call(
            "Runtime.evaluate",
            {
                "expression": "JSON.stringify({text:document.querySelector('button').textContent,action:document.querySelector('button').dataset.agentAction})",
                "returnByValue": True,
            },
            session_id,
        )
        after = json.loads(after_result["result"]["value"])
        if after != {"text": "Agent action complete", "action": "clicked"}:
            raise AssertionError(f"CDP action did not persist on the target: {after!r}")

        targets = cdp.call("Target.getTargets")["targetInfos"]
        if not any(item["targetId"] == target_id for item in targets):
            raise AssertionError("created target identity disappeared")
        cdp.call("Target.closeTarget", {"targetId": target_id})

        reviewed = {
            "cdp_protocol": "1.3",
            "domains": ["Target", "Page", "Runtime", "DOM"],
            "same_target": True,
            "scripted_fixture_observed": True,
            "dom_action_observed": True,
        }
        encoded = json.dumps(reviewed, sort_keys=True, separators=(",", ":")).encode()
        reviewed["observation_sha256"] = hashlib.sha256(encoded).hexdigest()
        with open(args.output, "w", encoding="utf-8") as output:
            json.dump(reviewed, output, sort_keys=True, separators=(",", ":"))
            output.write("\n")
    finally:
        websocket.close()


if __name__ == "__main__":
    main()
