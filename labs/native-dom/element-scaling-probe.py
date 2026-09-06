#!/usr/bin/env python3
"""A measurement probe, not a court: what does one element cost a realm, and
what did the element-fact hardening add to it?

Every round of the element-fact programme measured its own child-frame M1/M2
deltas and stayed under a frozen ceiling. Those ceilings bound a realm's
**fixed** cost. Nothing bounds the cost that scales with the page, and rounds C
(`a26aea3`) and D (`a0482ed`) both added per-element state: a `WeakMap` entry
and a `{tag, a}` record built in the `Element` constructor, for every element a
document has.

This probe measures the slope directly. Tracked `script_realms.malloc_bytes` is
exact and deterministic, so a fresh host per point and one target per host is
enough; no averaging is needed and none is done.

  Family A -- scale:  0, 500, 1000, 2000, 4000 elements, two attributes each.
                      Gives bytes per element and the empty-realm intercept.
  Family B -- attrs:  2000 elements carrying 0, 1, 3 and 6 attributes.
                      Separates the flat per-element charge from the
                      per-attribute one, so a change to either is visible
                      on its own.

Run it against two binaries to price a change: the slope difference is the
per-element cost of whatever lies between them. A binary built at a different
absolute path has a different hash by the same-path provenance rule in
AGENTS.md, and that is expected -- runtime memory does not depend on the build
path, so an off-path baseline is a valid comparison and an invalid hash match.

Strictly headless: no surface binary, no window, no AppKit, and it refuses to
run with the visible-court variable set. Fixtures are generated into a
temporary directory; nothing is fetched and no network is opened.
"""

import argparse
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "protocol"))
import check_contract  # noqa: E402

VISIBLE_ENV = "MINICON_SURF_ALLOW_VISIBLE_COURT"
ALLOCATOR_KNOBS = {"system": None, "arena": "MINICON_SURF_NATIVE_REALM_ARENA"}

SCALE_COUNTS = (0, 500, 1000, 2000, 4000)
ATTR_COUNTS = (0, 1, 3, 6)
ATTR_ELEMENTS = 2000


def write_fixtures(root):
    root.mkdir(parents=True, exist_ok=True)
    for n in SCALE_COUNTS:
        rows = "".join(f'<div id="d{i}" class="row" data-k="v{i}"><span>{i}</span></div>'
                       for i in range(n // 2))
        root.joinpath(f"scale-{n}.html").write_text(
            f"<!doctype html><html><head><title>scale {n}</title></head>"
            f"<body>{rows}</body></html>\n")
    for na in ATTR_COUNTS:
        body = "".join("<div" + "".join(f' a{k}="v{i}{k}"' for k in range(na)) + "></div>"
                       for i in range(ATTR_ELEMENTS))
        root.joinpath(f"attr-{na}.html").write_text(
            f"<!doctype html><html><head><title>attr {na}</title></head>"
            f"<body>{body}</body></html>\n")
    root.joinpath("attr-none.html").write_text(
        "<!doctype html><html><head><title>none</title></head><body></body></html>\n")


class Host:
    def __init__(self, binary, directory, fixture_root, allocator):
        environment = dict(os.environ)
        for knob in ALLOCATOR_KNOBS.values():
            if knob:
                environment.pop(knob, None)
        environment.pop(VISIBLE_ENV, None)
        if ALLOCATOR_KNOBS[allocator]:
            environment[ALLOCATOR_KNOBS[allocator]] = "1"
        command = [binary, "serve", "--stdio", "--fixture-root", str(fixture_root),
                   "--config-dir", str(Path(directory) / "config")]
        self.process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                        stderr=subprocess.DEVNULL, text=True, env=environment)
        self.counter = 0

    def call(self, operation, arguments):
        self.counter += 1
        request = {"protocol": "minicon-surf.control", "version": "0.0.1",
                   "request_id": f"req_es_{self.counter}", "deadline_ms": 30000,
                   "operation": operation, "arguments": arguments}
        check_contract.validate_request(request)
        self.process.stdin.write(json.dumps(request) + "\n")
        self.process.stdin.flush()
        line = self.process.stdout.readline()
        if not line:
            raise RuntimeError(f"host exited during {operation}")
        answer = json.loads(line)
        check_contract.validate_response(answer)
        if not answer["ok"]:
            raise RuntimeError(f"{operation} failed: {answer['error']['code']}")
        return answer["result"]

    def finish(self):
        self.process.stdin.close()
        return self.process.wait(timeout=30)


def realm_bytes_for(binary, fixture_root, allocator, fixture):
    """One host, one target, one fixture. A fresh process each time so no
    allocator history from a previous point can carry into this one."""
    with tempfile.TemporaryDirectory(prefix="minicon-surf-es-") as directory:
        host = Host(binary, directory, fixture_root, allocator)
        try:
            profile = host.call("profile.create", {"persistence": "ephemeral"})["profile"]
            session = host.call("session.open", {"profile": profile})["session"]
            empty = host.call("memory.report", {})["owners"]["script_realms"]["malloc_bytes"]
            host.call("target.open", {"session": session, "fixture": fixture})
            time.sleep(0.2)
            live = host.call("memory.report", {})["owners"]["script_realms"]["malloc_bytes"]
            if host.finish() != 0:
                raise RuntimeError("host exited with failure")
            return {"empty": empty, "live": live}
        finally:
            if host.process.poll() is None:
                host.process.kill()
                host.process.wait()


def least_squares_slope(points):
    xs = [x for x, _ in points]
    ys = [y for _, y in points]
    mx = sum(xs) / len(xs)
    my = sum(ys) / len(ys)
    return sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / sum((x - mx) ** 2 for x in xs)


def measure(binary, fixture_root, allocator):
    scale = {n: realm_bytes_for(binary, fixture_root, allocator, f"scale-{n}.html")
             for n in SCALE_COUNTS}
    attrs = {n: realm_bytes_for(binary, fixture_root, allocator, f"attr-{n}.html")
             for n in ATTR_COUNTS}
    attrs["none"] = realm_bytes_for(binary, fixture_root, allocator, "attr-none.html")
    points = [(n, scale[n]["live"]) for n in SCALE_COUNTS]
    slope = least_squares_slope(points)
    bare = (attrs[0]["live"] - attrs["none"]["live"]) / ATTR_ELEMENTS
    per_attribute = ((attrs[6]["live"] - attrs[0]["live"])
                     / (6 * ATTR_ELEMENTS))
    return {
        "scale": {str(n): scale[n] for n in SCALE_COUNTS},
        "attrs": {str(n): attrs[n] for n in list(ATTR_COUNTS) + ["none"]},
        "empty_realm_bytes": scale[0]["live"],
        "bytes_per_element_slope": slope,
        "bytes_per_bare_element": bare,
        "bytes_per_attribute": per_attribute,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--binary", required=True)
    parser.add_argument("--receipt", required=True)
    parser.add_argument("--label", default="", help="what this binary is, for the receipt")
    args = parser.parse_args()
    if os.environ.get(VISIBLE_ENV):
        print(json.dumps({"ran": False, "reason": "the visible-court variable is set"}))
        return 1

    with tempfile.TemporaryDirectory(prefix="minicon-surf-es-fixtures-") as fixtures:
        root = Path(fixtures)
        write_fixtures(root)
        arms = {arm: measure(args.binary, root, arm) for arm in ALLOCATOR_KNOBS}

    receipt = {
        "probe": "element scaling: tracked realm bytes per element, and per attribute",
        "label": args.label,
        "binary_sha256": hashlib.sha256(Path(args.binary).read_bytes()).hexdigest(),
        "scale_counts": list(SCALE_COUNTS),
        "attr_counts": list(ATTR_COUNTS),
        "attr_elements": ATTR_ELEMENTS,
        "arms": arms,
    }
    Path(args.receipt).write_text(json.dumps(receipt, indent=2) + "\n")
    for arm, m in arms.items():
        print(f"{arm:>7}: {m['bytes_per_element_slope']:8.1f} B/element  "
              f"bare {m['bytes_per_bare_element']:7.1f}  "
              f"per-attribute {m['bytes_per_attribute']:6.1f}  "
              f"empty realm {m['empty_realm_bytes']:,}")
    print(json.dumps({"ran": True, "receipt": args.receipt}))
    return 0


sys.exit(main())
