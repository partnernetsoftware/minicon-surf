#!/usr/bin/env python3
"""The frozen court for H3: no capture is dead by accident.

Frozen from `h3-reserved-captures-design-0.0.1.md` §8 and the ruling that
followed it. It is a **source rule**, not a behaviour rule, so it reads the
shims rather than driving the host — with one runtime criterion that records
what a realm costs, as evidence that adopting the rule changes nothing.

The rule is quantified over a **declared set**, which is what lets it coexist
with the permanently closed C2:

  * every capture that is not declared an exception is referenced at least once;
  * the exception set is exactly `{arrayIndexOf}`;
  * every declared exception carries a written reason beside it in the source;
  * a declared exception is referenced **zero** times -- if one acquires a use,
    the list must be revisited in writing rather than silently.

None of those can be satisfied by adding a call, which is the property the
ruling asked for: a call written to satisfy a rule makes the rule measure
itself.

`--base` and `--main` point the source criteria at any tree, so the rule can be
run against history as well as the working copy.

Strictly headless: no surface binary, no window, no AppKit, and it refuses to
run with the visible-court variable set.

Groups: declaration, reference, exception, reason, runtime.
"""

import argparse
import hashlib
import importlib.util
import json
import os
import re
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
DEFAULT_BASE = Path(__file__).with_name("src") / "dom_shim_base.js"
DEFAULT_MAIN = Path(__file__).with_name("src") / "dom_shim_main.js"

# Frozen by ruling. Growing this list is a failure until someone rules it in.
RESERVED = {"arrayIndexOf"}

# A capture is a binding of an intrinsic taken before any page script can
# replace it. Only these shapes count; a helper built from one is not a capture.
CAPTURE = re.compile(
    r"^\s*const\s+([A-Za-z_$][\w$]*)\s*=\s*"
    r"((?:Array|Map|WeakMap|Object|String|JSON|Reflect|Function|Number|Promise)"
    r"(?:\.prototype)?\.[\w$]+)\s*;",
    re.M,
)


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


def strip_comments(source):
    source = re.sub(r"/\*.*?\*/", "", source, flags=re.S)
    return re.sub(r"//[^\n]*", "", source)


def reason_beside(source, name):
    """A comment on the line above the declaration, or trailing it."""
    for index, line in enumerate(source.split("\n")):
        if re.match(rf"\s*const\s+{re.escape(name)}\s*=", line):
            if "//" in line.split("=", 1)[-1]:
                return True
            above = source.split("\n")[max(0, index - 1)].strip()
            return above.startswith("//") or above.startswith("*")
    return False


class Host:
    def __init__(self, binary, directory, allocator, origin):
        environment = dict(os.environ)
        for knob in ("MINICON_SURF_NATIVE_REALM_ZONE", "MINICON_SURF_NATIVE_REALM_ARENA",
                     "MINICON_SURF_PROFILE_STORE", VISIBLE_ENV):
            environment.pop(knob, None)
        if allocator == "arena":
            environment["MINICON_SURF_NATIVE_REALM_ARENA"] = "1"
        command = [binary, "serve", "--stdio", "--fixture-root", str(FIXTURE_ROOT),
                   "--config-dir", str(Path(directory) / "config"), "--allow-origin", origin]
        self.process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                        stderr=subprocess.DEVNULL, text=True, env=environment)
        self.counter = 0

    def ok(self, operation, arguments):
        self.counter += 1
        request = {"protocol": "minicon-surf.control", "version": "0.0.2",
                   "request_id": f"req_cap_{self.counter}", "deadline_ms": 30000,
                   "operation": operation, "arguments": arguments}
        check_contract.validate_request(request)
        self.process.stdin.write(json.dumps(request) + "\n")
        self.process.stdin.flush()
        line = self.process.stdout.readline()
        answer = json.loads(line) if line else {"ok": False, "error": {"code": "host_exited"}}
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
    parser.add_argument("--base", default=str(DEFAULT_BASE))
    parser.add_argument("--main", default=str(DEFAULT_MAIN))
    args = parser.parse_args()
    if os.environ.get(VISIBLE_ENV):
        print(json.dumps({"passed": False, "reason": "the visible-court variable is set"}))
        return 1

    base_source = Path(args.base).read_text()
    main_source = Path(args.main).read_text()
    both = strip_comments(base_source) + "\n" + strip_comments(main_source)
    captures = {name: expression for name, expression in CAPTURE.findall(base_source)}
    counted = {}
    for name in captures:
        counted[name] = len(re.findall(rf"\b{re.escape(name)}\b", both)) - 1

    checks = []

    def expect(name, condition, detail=None):
        checks.append({"check": name, "passed": bool(condition),
                       **({"detail": detail} if detail is not None else {})})

    expect("D1: the base declares captures, and this court can see them",
           len(captures) >= 8, {"captures": len(captures)})

    unused = {name for name, uses in counted.items() if uses <= 0}
    expect("R1: every capture that is not a declared exception is referenced",
           unused <= RESERVED,
           {"unused": sorted(unused), "reserved": sorted(RESERVED)})

    expect("E1: the exception set is exactly the one the ruling froze",
           unused == RESERVED,
           {"unused": sorted(unused), "reserved": sorted(RESERVED),
            "note": "a reserved capture that acquires a use fails here too, on purpose:"
                    " the list is then revisited in writing rather than silently"})

    expect("E2: the exception set has not grown",
           len(RESERVED) == 1, {"reserved": sorted(RESERVED)})

    for name in sorted(RESERVED):
        expect(f"W1: the declared exception {name} carries a written reason beside it",
               name in captures and reason_beside(base_source, name),
               {"declared": name in captures,
                "reason_present": name in captures and reason_beside(base_source, name)})

    # The rule may not be satisfied by writing a call: a reserved capture is
    # expected to stay at zero uses, and this states that expectation as data.
    expect("W2: no call was added to satisfy this court",
           all(counted.get(name, 0) == 0 for name in RESERVED),
           {"reserved_uses": {name: counted.get(name) for name in sorted(RESERVED)}})

    # One runtime criterion: adopting a source rule changes nothing a realm
    # costs, and the numbers are recorded rather than judged.
    network = RETENTION.load_network_module()
    page = b"<!doctype html><html><body><main><p id=p>page</p></main></body></html>"

    class Handler(network.Handler):
        def do_GET(self):
            path, _, _query = self.path.partition("?")
            network.Handler.hits.append(path)
            return self.reply(200, page, "text/html")

    server = network.Server(("127.0.0.1", 0), Handler)
    origin = f"http://127.0.0.1:{server.server_address[1]}"
    threading.Thread(target=server.serve_forever, daemon=True).start()
    bytes_seen = {}
    try:
        for allocator in ("system", "arena"):
            with tempfile.TemporaryDirectory(prefix="minicon-surf-cap-") as directory:
                host = Host(args.binary, directory, allocator, origin)
                try:
                    profile = host.ok("profile.create", {"persistence": "ephemeral"})["profile"]
                    session = host.ok("session.open", {"profile": profile})["session"]
                    target = host.ok("target.open", {"session": session,
                                                     "url": origin + "/a.html"})["target"]
                    snapshot = host.ok("target.snapshot", {"target": target, "format": "semantic",
                                                           "max_bytes": 65536, "max_nodes": 32})
                    report = host.ok("memory.report", {})
                    bytes_seen[allocator] = report["owners"]["script_realms"]["malloc_bytes"]
                    expect(f"[{allocator}] N1: a realm still serves a snapshot with this rule in force",
                           len(snapshot["nodes"]) > 0,
                           {"nodes": len(snapshot["nodes"]),
                            "malloc_bytes": bytes_seen[allocator]})
                finally:
                    host.finish()
    finally:
        server.shutdown()

    receipt = {
        "court": "native-dom capture declarations (H3, declared-set form)",
        "host_sha256": hashlib.sha256(Path(args.binary).read_bytes()).hexdigest(),
        "base_sha256": hashlib.sha256(Path(args.base).read_bytes()).hexdigest(),
        "main_sha256": hashlib.sha256(Path(args.main).read_bytes()).hexdigest(),
        "ruled": {"rule": "every capture not declared an exception is referenced at least once",
                  "reserved": sorted(RESERVED),
                  "reason": "a declared exception carries a written reason beside it",
                  "forbidden": "adding a call to satisfy the rule; deleting or restoring C2"},
        "captures": {name: {"expression": captures[name], "uses": counted[name]}
                     for name in sorted(captures)},
        "tracked_bytes": bytes_seen,
        "checks": checks,
        "checks_passed": sum(1 for c in checks if c["passed"]),
        "checks_total": len(checks),
        "passed": all(c["passed"] for c in checks),
        "limitations": [
            "a source rule: it reads the shims rather than driving the host, and --base/--main can point it at any tree",
            "a reserved capture that acquires a use fails E1 by design, so the list is revisited in writing rather than silently",
            "only const bindings of an intrinsic count as captures; a helper built from one does not",
            "the runtime criterion records what a realm costs as evidence; it judges no threshold, because this rule has no runtime effect",
            "H2 is not in scope, and no direct call site is asked to change here",
        ],
    }
    Path(args.receipt).write_text(json.dumps(receipt, indent=1, sort_keys=True) + "\n")
    print(json.dumps({"passed": receipt["passed"], "checks_passed": receipt["checks_passed"],
                      "checks_total": receipt["checks_total"]}))
    for check in checks:
        if not check["passed"]:
            print("FAIL", json.dumps(check)[:180])
    return 0 if receipt["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
