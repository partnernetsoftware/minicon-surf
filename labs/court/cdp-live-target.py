#!/usr/bin/env python3
"""Run the same hermetic live-target CDP journey against a browser server."""

import argparse
import base64
import json
import os
import pathlib
import socket
import struct
import subprocess
import tempfile
import time
import urllib.parse
import urllib.request


class WebSocket:
    def __init__(self, url: str, timeout: float = 10.0):
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
        payload = json.dumps(value, separators=(",", ":")).encode()
        mask = os.urandom(4)
        if len(payload) < 126:
            header = bytes([0x81, 0x80 | len(payload)])
        elif len(payload) < 65536:
            header = bytes([0x81, 0x80 | 126]) + struct.pack("!H", len(payload))
        else:
            header = bytes([0x81, 0x80 | 127]) + struct.pack("!Q", len(payload))
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
            mask = self._exact(4) if second & 0x80 else None
            payload = self._exact(length)
            if mask:
                payload = bytes(byte ^ mask[index % 4] for index, byte in enumerate(payload))
            if opcode == 0x9:
                self._send_control(0xA, payload)
            elif opcode == 0x8:
                raise EOFError("CDP WebSocket closed")
            elif opcode == 0x1:
                return json.loads(payload.decode())

    def _send_control(self, opcode: int, payload: bytes):
        mask = os.urandom(4)
        masked = bytes(byte ^ mask[index % 4] for index, byte in enumerate(payload))
        self.sock.sendall(bytes([0x80 | opcode, 0x80 | len(payload)]) + mask + masked)

    def close(self):
        self.sock.close()


class CDP:
    def __init__(self, websocket):
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


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def discover(port: int, deadline: float):
    url = f"http://127.0.0.1:{port}/json/version"
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=0.25) as response:
                document = json.load(response)
            endpoint = document["webSocketDebuggerUrl"]
            if not endpoint.startswith(f"ws://127.0.0.1:{port}/"):
                raise RuntimeError("discovery returned a non-loopback endpoint")
            return endpoint
        except (OSError, KeyError, json.JSONDecodeError):
            time.sleep(0.025)
    raise TimeoutError("CDP discovery endpoint did not become ready")


def launch(args, temporary_directory):
    port = free_port()
    if args.engine == "lightpanda":
        command = [
            args.browser,
            "serve",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--disable-metrics",
            "--watchdog-ms",
            "15000",
        ]
        environment = dict(os.environ)
        environment.update(
            LIGHTPANDA_DISABLE_TELEMETRY="true", LIGHTPANDA_DISABLE_CORE_DUMP="1"
        )
    else:
        profile = pathlib.Path(temporary_directory) / "profile"
        command = [
            args.browser,
            "--headless=new",
            f"--remote-debugging-port={port}",
            f"--user-data-dir={profile}",
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-background-networking",
            "--disable-breakpad",
            "--disable-component-update",
            "--disable-crash-reporter",
            "--disable-default-apps",
            "--disable-extensions",
            "--disable-features=OptimizationHints,MediaRouter",
            "--disable-sync",
            "--metrics-recording-only",
            "--no-startup-window",
        ]
        environment = None
    process = subprocess.Popen(
        command,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        env=environment,
    )
    try:
        endpoint = discover(port, time.monotonic() + 10.0)
    except BaseException:
        process.terminate()
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
        raise
    return process, endpoint


def run_journey(endpoint, fixture, hold_ms):
    fixture_url = "data:text/html," + urllib.parse.quote_from_bytes(fixture, safe="")
    websocket = WebSocket(endpoint)
    cdp = CDP(websocket)
    target_id = None
    try:
        target_id = cdp.call("Target.createTarget", {"url": "about:blank"})["targetId"]
        session_id = cdp.call(
            "Target.attachToTarget", {"targetId": target_id, "flatten": True}
        )["sessionId"]
        cdp.call("Page.enable", session_id=session_id)
        cdp.call("Runtime.enable", session_id=session_id)
        cdp.call("Page.navigate", {"url": fixture_url}, session_id)
        expression = "JSON.stringify([document.querySelector('h1')?.textContent,document.querySelector('input')?.value,document.querySelector('button')?.textContent,document.querySelector('a')?.textContent])"
        expected = [
            "Memory and Agent Court",
            "bounded browser",
            "Continue",
            "Example result",
        ]
        deadline = time.monotonic() + 5.0
        while True:
            result = cdp.call(
                "Runtime.evaluate",
                {"expression": expression, "returnByValue": True},
                session_id,
            )
            value = result.get("result", {}).get("value")
            if value and json.loads(value) == expected:
                break
            if time.monotonic() >= deadline:
                raise AssertionError("W1 semantic state did not become ready")
            time.sleep(0.025)
        time.sleep(hold_ms / 1000.0)
    finally:
        if target_id is not None:
            cdp.call("Target.closeTarget", {"targetId": target_id})
        websocket.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--engine", choices=("lightpanda", "chrome"), required=True)
    parser.add_argument("--browser", required=True)
    parser.add_argument("--fixture", required=True)
    parser.add_argument("--hold-ms", type=int, default=2000)
    args = parser.parse_args()
    if args.hold_ms <= 0:
        parser.error("--hold-ms must be positive")

    with tempfile.TemporaryDirectory(prefix="minicon-surf-court-") as directory:
        process = None
        try:
            process, endpoint = launch(args, directory)
            run_journey(endpoint, pathlib.Path(args.fixture).read_bytes(), args.hold_ms)
        finally:
            if process is not None and process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()


if __name__ == "__main__":
    main()
