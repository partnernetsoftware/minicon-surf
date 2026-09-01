#!/usr/bin/env python3
"""Place the synthetic host in one checked lifecycle state and hold it steady."""

import argparse
import json
import pathlib
import subprocess
import time


class Host:
    def __init__(self, binary):
        self.process = subprocess.Popen(
            [binary, "serve", "--stdio"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
        )
        self.next_request = 0

    def call(self, operation, arguments):
        self.next_request += 1
        request_id = f"req_lifecycle_{self.next_request}"
        request = {
            "protocol": "minicon-surf.control",
            "version": "0.0.1",
            "request_id": request_id,
            "deadline_ms": 100,
            "operation": operation,
            "arguments": arguments,
        }
        self.process.stdin.write(json.dumps(request, separators=(",", ":")) + "\n")
        self.process.stdin.flush()
        response = json.loads(self.process.stdout.readline())
        if response.get("request_id") != request_id or response.get("ok") is not True:
            raise RuntimeError("synthetic lifecycle request failed")
        return response["result"]

    def close(self):
        self.process.stdin.close()
        if self.process.wait(timeout=3) != 0:
            raise RuntimeError("synthetic host exited unsuccessfully")


def create_target(host):
    profile = host.call("profile.create", {"persistence": "ephemeral"})["profile"]
    session = host.call("session.open", {"profile": profile})["session"]
    target = host.call("target.open", {"session": session})["target"]
    snapshot = host.call(
        "target.snapshot",
        {
            "target": target,
            "format": "semantic",
            "max_bytes": 65536,
            "max_nodes": 10,
        },
    )
    if snapshot["target"] != target or snapshot["revision"] != 0:
        raise AssertionError("synthetic lifecycle target identity differed")
    return target


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--mode", choices=("empty", "live", "headed", "post-hide", "post-close"), required=True
    )
    parser.add_argument("--binary", required=True)
    parser.add_argument("--hold-ms", type=int, required=True)
    parser.add_argument("--observation", required=True)
    args = parser.parse_args()
    if args.hold_ms <= 0:
        parser.error("--hold-ms must be positive")

    started = time.monotonic()
    host = Host(args.binary)
    try:
        target_closed = False
        surface_hidden = False
        if args.mode == "empty":
            target = None
        else:
            target = create_target(host)
            if args.mode in ("headed", "post-hide"):
                surface = host.call("surface.show", {"target": target})["surface"]
                if args.mode == "post-hide":
                    hidden = host.call("surface.hide", {"surface": surface})
                    if hidden["target"] != target:
                        raise AssertionError("hidden surface target identity differed")
                    surface_hidden = True
            elif args.mode == "post-close":
                closed = host.call("target.close", {"target": target})
                if closed["target"] != target:
                    raise AssertionError("closed target identity differed")
                target_closed = True
        memory = host.call("memory.report", {})
        expected_targets = 1 if args.mode == "live" else 0
        if args.mode in ("headed", "post-hide"):
            expected_targets = 1
        if memory["owners"]["targets"]["objects"] != expected_targets:
            raise AssertionError("target owner count differed")
        expected_surfaces = 1 if args.mode == "headed" else 0
        if memory["owners"]["surfaces"]["objects"] != expected_surfaces:
            raise AssertionError("surface owner count differed")
        setup_ms = round((time.monotonic() - started) * 1000, 3)
        observation = {
            "mode": args.mode,
            "setup_ms": setup_ms,
            "target_objects": expected_targets,
            "target_closed": target_closed,
            "surface_objects": expected_surfaces,
            "surface_hidden": surface_hidden,
            "logical_accounted_bytes": memory["total_accounted_bytes"],
        }
        pathlib.Path(args.observation).write_text(
            json.dumps(observation, sort_keys=True, separators=(",", ":")) + "\n",
            encoding="utf-8",
        )
        time.sleep(args.hold_ms / 1000)
    finally:
        host.close()


if __name__ == "__main__":
    main()
