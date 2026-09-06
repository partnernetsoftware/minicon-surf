#!/usr/bin/env python3
"""Stress the control transport at a download-sized line, hermetically.

Read-only and headless. Nothing is downloaded and no capability is
implemented: the fixture is a local server and a stand-in emitter, and the
question is only whether a line the size sink C would write survives the
transport, and how far today's protocol is from ever writing one.

The host-writer half of the same question lives in `transport_tests` in
`src/main.rs`, which drives the real `envelope()` and the real
write_all/newline/flush over a real pipe. This half drives the reader every
court in this directory uses -- `process.stdout.readline()` -- at the same
size, and measures the largest line the shipped protocol can produce.
"""

import base64
import hashlib
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import threading

HERE = os.path.dirname(os.path.abspath(__file__))
NETWORK_CAP = 1_048_576
RESPONSE_BOUND = 4_194_304


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    saved = sys.argv
    sys.argv = [name]
    try:
        spec.loader.exec_module(module)
    finally:
        sys.argv = saved
    return module


def reader_carries_a_download_sized_line():
    """The court reader, on a real pipe, at the size sink C would write."""
    payload = b"x" * NETWORK_CAP
    digest = hashlib.sha256(payload).hexdigest()
    answer = {
        "protocol": "minicon-surf-control",
        "version": "0.0.2",
        "request_id": "req_1",
        "ok": True,
        "result": {
            "kind": "download",
            "byte_count": len(payload),
            "sha256": digest,
            "reported_name": "x" * 255,
            "truncated": False,
            "bytes_base64": base64.b64encode(payload).decode(),
        },
    }
    line = json.dumps(answer, separators=(",", ":")).encode()
    emitter = subprocess.Popen(
        [sys.executable, "-c", "import sys;sys.stdout.buffer.write(sys.stdin.buffer.read());sys.stdout.buffer.write(b'\\n');sys.stdout.buffer.flush()"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
    )
    threading.Thread(target=lambda: (emitter.stdin.write(line), emitter.stdin.close()), daemon=True).start()
    read_back = emitter.stdout.readline()
    emitter.wait()
    parsed = json.loads(read_back)
    decoded = base64.b64decode(parsed["result"]["bytes_base64"])
    return {
        "line_bytes": len(line),
        "read_bytes": len(read_back),
        "intact": decoded == payload,
        "byte_count_matches": parsed["result"]["byte_count"] == len(payload),
        "sha256_matches": hashlib.sha256(decoded).hexdigest() == parsed["result"]["sha256"] == digest,
        "under_bound": len(line) < RESPONSE_BOUND,
    }


def largest_line_the_protocol_can_write(host_module, jobs_module, binary):
    """How close does any shipped operation come to a download-sized line?"""
    net = host_module.load_network_module()
    # Many nodes, each with more text than a node is allowed to report, so the
    # snapshot is pushed against both of its caps at once.
    body = b"".join(
        b"<p id=n%d>%s</p>" % (index, b"y" * 300) for index in range(400)
    )
    document = b"<!doctype html><html><body><main>" + body + b"</main></body></html>"

    class Handler(net.Handler):
        def do_GET(self):
            net.Handler.hits.append(self.path)
            return self.reply(200, document)

    server = net.Server(("127.0.0.1", 0), Handler)
    origin = f"http://127.0.0.1:{server.server_address[1]}"
    threading.Thread(target=server.serve_forever, daemon=True).start()
    directory = tempfile.TemporaryDirectory()
    host = jobs_module.Supervised(binary, directory.name, origin, "system")
    measured = {}
    try:
        profile = host.ok("profile.create", {"persistence": "ephemeral"})["profile"]
        session = host.ok("session.open", {"profile": profile})["session"]
        target = host.ok("target.open", {"session": session, "url": origin + "/a.html"})["target"]
        largest = 0
        for operation, arguments in (
            ("target.snapshot", {"target": target, "format": "semantic", "max_bytes": RESPONSE_BOUND, "max_nodes": 128}),
            ("target.inspect", {"target": target}),
            ("target.traverse", {"target": target, "delta": -1}),
            ("profile.list", {}),
            ("memory.report", {}),
        ):
            answer = host.call(operation, arguments)
            size = len(json.dumps(answer, separators=(",", ":")))
            measured[operation] = size if answer.get("ok") else f"{answer['error']['code']}"
            if answer.get("ok"):
                largest = max(largest, size)
        measured["largest"] = largest
    finally:
        host.finish()
        server.shutdown()
        directory.cleanup()
    return measured


def over_cap_is_refused_before_serialization(host_module, jobs_module, binary):
    """A body over the network cap never reaches the envelope at all."""
    net = host_module.load_network_module()
    oversized = b"<!doctype html><html><body><p>" + b"z" * (NETWORK_CAP + 400_000) + b"</p></body></html>"

    class Handler(net.Handler):
        def do_GET(self):
            net.Handler.hits.append(self.path)
            return self.reply(200, oversized)

    server = net.Server(("127.0.0.1", 0), Handler)
    origin = f"http://127.0.0.1:{server.server_address[1]}"
    threading.Thread(target=server.serve_forever, daemon=True).start()
    directory = tempfile.TemporaryDirectory()
    host = jobs_module.Supervised(binary, directory.name, origin, "system")
    try:
        profile = host.ok("profile.create", {"persistence": "ephemeral"})["profile"]
        session = host.ok("session.open", {"profile": profile})["session"]
        answer = host.call("target.open", {"session": session, "url": origin + "/a.html"})
        error = answer.get("error") or {}
        return {
            "refused": not answer.get("ok"),
            "code": error.get("code"),
            "message": error.get("message"),
            "generic_internal": error.get("code") == "internal",
        }
    finally:
        host.finish()
        server.shutdown()
        directory.cleanup()


def main():
    binary = os.environ.get("BIN") or os.path.join(HERE, "target", "release", "native-dom-control")
    host_module = load("retention", os.path.join(HERE, "retention-court.py"))
    jobs_module = load("jobs", os.path.join(HERE, "job-deadline-court.py"))

    reader = reader_carries_a_download_sized_line()
    print("reader, at a download-sized line")
    print(f"  line written           {reader['line_bytes']:>10,} bytes")
    print(f"  line read by readline  {reader['read_bytes']:>10,} bytes")
    print(f"  payload intact         {reader['intact']}")
    print(f"  byte_count matches     {reader['byte_count_matches']}")
    print(f"  sha256 matches         {reader['sha256_matches']}")
    print(f"  under the 4 MiB bound  {reader['under_bound']}")

    sizes = largest_line_the_protocol_can_write(host_module, jobs_module, binary)
    print("\nlargest line the shipped protocol can write")
    for operation, size in sizes.items():
        if operation == "largest":
            continue
        print(f"  {operation:<18} {size if isinstance(size, str) else format(size, ',') + ' bytes'}")
    print(f"  largest            {sizes['largest']:,} bytes"
          f"  ({sizes['largest'] / RESPONSE_BOUND:.4%} of the response bound)")

    refusal = over_cap_is_refused_before_serialization(host_module, jobs_module, binary)
    print("\na body over the network cap")
    print(f"  refused                {refusal['refused']}")
    print(f"  code                   {refusal['code']}")
    print(f"  message                {refusal['message']}")
    print(f"  generic internal       {refusal['generic_internal']}")

    passed = (
        reader["intact"] and reader["sha256_matches"] and reader["byte_count_matches"]
        and reader["under_bound"] and refusal["refused"] and not refusal["generic_internal"]
        and refusal["code"] == "resource_limit"
    )
    print("\n" + ("transport stress: passed" if passed else "transport stress: FAILED"))
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
