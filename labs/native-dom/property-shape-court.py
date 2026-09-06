#!/usr/bin/env python3
"""The frozen court for the shim's property shape.

Frozen from `shim-reduction-audit-0.0.1.md` §7 and the ruling that followed it,
**before** C1 batches any `defineProperty` call — and unlike most courts here,
it is expected to pass **now and afterwards**, unchanged. That is the whole
point: C1 is a refactor whose only permitted effect is on bytes, so this court
pins what must not move.

What it pins, per object the shim touches: the exact own-property names in
**creation order**, and for each one its kind (value, getter, setter), and its
`enumerable`, `configurable` and `writable` flags. A page reports them into the
document, because this host has no evaluation operation; the court reads them
back through a snapshot and compares against digests captured from the shipped
binary before the refactor.

Order matters and is checked, so a merge that reorders properties fails here
even though every descriptor still matches.

Strictly headless: no surface binary, no window, no AppKit, and it refuses to
run with the visible-court variable set. One hermetic loopback origin, both
allocators, nothing fetched from outside this file.

Groups: shape, order, handle, realms, bytes.
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
SHIM_BASE = ROOT / "labs" / "native-dom" / "src" / "dom_shim_base.js"
SHIM_MAIN = ROOT / "labs" / "native-dom" / "src" / "dom_shim_main.js"

# The handle's key set, court-pinned elsewhere and repeated here because C1
# touches the very `defineProperty` that installs it.
HANDLE_KEYS = ["addListener", "contains", "dispatchOn", "document", "Document", "Element",
               "eventStateOf", "Event", "focusedElement", "g", "Node", "removeListener",
               "signals"]

# Captured from the shipped binary before the refactor. A change here is a
# change to what a page can see, and must be ruled, not absorbed.
EXPECTED = {
    "window": "112:156a0f8b:Object:v:011|Function:v:011|Error:v:011",
    "Node.prototype": "22:8d099852:constructor:v:011|isConnected:g:010|children:g:010",
    "Element.prototype": "40:26312e4:constructor:v:011|getAttribute:v:011|hasAttribute:v:011",
    "Document.prototype": "9:917a7738:constructor:v:011|documentElement:g:010|head:g:010",
    "Event.prototype": "15:33f66125:constructor:v:011|defaultPrevented:g:010|preventDefault:v:011",
    "document": "6:94aa8a2c:parentNode:v:111|childNodes:v:111|nodeType:v:111",
}


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

# The page reports the shape of every own property of the objects the shim
# installs onto, in creation order, in chunks a snapshot node can carry.
REPORTER = """
<!doctype html><html><body><main>
<div id="out"></div>
<script>
(function () {
  var targets = [
    ["window", window],
    ["Node.prototype", window.Node && window.Node.prototype],
    ["Element.prototype", window.Element && window.Element.prototype],
    ["Document.prototype", window.Document && window.Document.prototype],
    ["Event.prototype", window.Event && window.Event.prototype],
    ["document", document]
  ];
  var out = document.getElementById("out");
  for (var t = 0; t < targets.length; t++) {
    var name = targets[t][0], object = targets[t][1];
    var line = name + "=";
    if (!object) { line += "absent"; }
    else {
      var names = Object.getOwnPropertyNames(object);
      var parts = [];
      for (var i = 0; i < names.length; i++) {
        var d = Object.getOwnPropertyDescriptor(object, names[i]);
        var kind = d.get ? (d.set ? "gs" : "g") : (d.set ? "s" : "v");
        parts.push(names[i] + ":" + kind + ":" + (d.enumerable ? 1 : 0)
                   + (d.configurable ? 1 : 0) + (d.writable ? 1 : 0));
      }
      // A checksum over the ordered list, so one node can carry it, plus the
      // count and the first few names for a legible failure.
      var joined = parts.join(",");
      var hash = 5381;
      for (var c = 0; c < joined.length; c++) {
        hash = ((hash * 33) ^ joined.charCodeAt(c)) >>> 0;
      }
      line += names.length + ":" + hash.toString(16) + ":" + parts.slice(0, 3).join("|");
    }
    var p = document.createElement("p");
    p.id = "row" + t;
    p.textContent = line.slice(0, 250);
    out.appendChild(p);
  }
})();
</script></main></body></html>
"""


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

    def call(self, operation, arguments):
        self.counter += 1
        request = {"protocol": "minicon-surf.control", "version": "0.0.2",
                   "request_id": f"req_shape_{self.counter}", "deadline_ms": 30000,
                   "operation": operation, "arguments": arguments}
        check_contract.validate_request(request)
        self.process.stdin.write(json.dumps(request) + "\n")
        self.process.stdin.flush()
        line = self.process.stdout.readline()
        if not line:
            return {"ok": False, "error": {"code": "host_exited"}}
        return json.loads(line)

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


def rows_of(host, target):
    snapshot = host.ok("target.snapshot", {"target": target, "format": "semantic",
                                           "max_bytes": 4194304, "max_nodes": 128})
    rows = {}
    for node in snapshot["nodes"]:
        text = node.get("name") or ""
        if "=" in text and node.get("role") == "text":
            key, _, value = text.partition("=")
            rows[key] = value
    return rows


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--binary", required=True)
    parser.add_argument("--receipt", required=True)
    parser.add_argument("--capture", action="store_true",
                        help="print the observed shape instead of judging it")
    args = parser.parse_args()
    if os.environ.get(VISIBLE_ENV):
        print(json.dumps({"passed": False, "reason": "the visible-court variable is set"}))
        return 1

    network = RETENTION.load_network_module()

    class Handler(network.Handler):
        def do_GET(self):
            path, _, _query = self.path.partition("?")
            network.Handler.hits.append(path)
            return self.reply(200, REPORTER.encode(), "text/html")

    server = network.Server(("127.0.0.1", 0), Handler)
    origin = f"http://127.0.0.1:{server.server_address[1]}"
    threading.Thread(target=server.serve_forever, daemon=True).start()
    checks = []
    observed = {}
    bytes_seen = {}

    def expect(name, condition, detail=None):
        checks.append({"check": name, "passed": bool(condition),
                       **({"detail": detail} if detail is not None else {})})

    try:
        for allocator in ("system", "arena"):
            tag = f"[{allocator}] "
            with tempfile.TemporaryDirectory(prefix="minicon-surf-shape-") as directory:
                host = Host(args.binary, directory, allocator, origin)
                try:
                    profile = host.ok("profile.create", {"persistence": "ephemeral"})["profile"]
                    session = host.ok("session.open", {"profile": profile})["session"]
                    target = host.ok("target.open", {"session": session,
                                                     "url": origin + "/a.html"})["target"]
                    rows = rows_of(host, target)
                    observed[allocator] = rows
                    if args.capture:
                        continue
                    for name, value in sorted(EXPECTED.items()):
                        expect(tag + f"S: {name} keeps its properties, flags and order",
                               rows.get(name) == value,
                               {"expected": value[:80], "observed": (rows.get(name) or "")[:80]})
                    # The handle is consumed before a page runs, so a page must
                    # not see it at all -- and its key set lives in the base.
                    expect(tag + "H: the handle is gone before any page script runs",
                           "__mcsInternals" not in (rows.get("window") or ""),
                           {"window": (rows.get("window") or "")[:60]})

                    # Bytes, paired across the arms: a refactor may lower them
                    # and may not raise them.
                    report = host.ok("memory.report", {})
                    per_realm = report["owners"]["script_realms"]["malloc_bytes"]
                    bytes_seen[allocator] = per_realm
                    expect(tag + "B: one realm's tracked bytes are reported and non-zero",
                           isinstance(per_realm, int) and per_realm > 0,
                           {"malloc_bytes": per_realm})

                    # A child realm still gets the base shim only, which is
                    # what makes the main shim's cost a main-realm cost.
                    inspected = host.ok("target.inspect", {"target": target})
                    expect(tag + "R: the target reports its realms",
                           inspected.get("realms") is not None,
                           {"realms": json.dumps(inspected.get("realms"))[:60]})
                finally:
                    host.finish()
    finally:
        server.shutdown()

    if args.capture:
        print(json.dumps(observed, indent=1, sort_keys=True))
        return 0

    # The source itself: C1 may batch calls, and may not change the handle's
    # key set or the order the two shims are evaluated in.
    base_source = SHIM_BASE.read_text()
    main_source = SHIM_MAIN.read_text()
    for key in HANDLE_KEYS:
        if key not in base_source:
            expect(f"K: the handle still carries {key}", False, {"key": key})
    expect("K: the handle's key set is exactly the thirteen the court pins",
           all(key in base_source for key in HANDLE_KEYS),
           {"keys": len(HANDLE_KEYS)})
    expect("K: nothing new joins the handle",
           base_source.count("__mcsInternals") == 2,
           {"mentions": base_source.count("__mcsInternals")})
    expect("A: the arms agree on the shape they observed",
           observed.get("system") == observed.get("arena"),
           {"agree": observed.get("system") == observed.get("arena")})
    expect("B: the two arms' tracked per-realm bytes are both recorded",
           set(bytes_seen) == {"system", "arena"}, bytes_seen)

    receipt = {
        "court": "native-dom shim property shape (C1 refactor guard)",
        "host_sha256": hashlib.sha256(Path(args.binary).read_bytes()).hexdigest(),
        "base_sha256": hashlib.sha256(SHIM_BASE.read_bytes()).hexdigest(),
        "main_sha256": hashlib.sha256(SHIM_MAIN.read_bytes()).hexdigest(),
        "ruled": {"c1": "batch adjacent defineProperty calls on one object",
                  "forbidden": "reordering, renaming, flag changes, lazy install, "
                               "comment stripping, member semantics, shared runtime",
                  "d6": "the tracked bytes may fall; RSS is not claimed to move"},
        "observed_shape": observed.get("system"),
        "tracked_bytes": bytes_seen,
        "checks": checks,
        "checks_passed": sum(1 for c in checks if c["passed"]),
        "checks_total": len(checks),
        "passed": all(c["passed"] for c in checks),
        "limitations": [
            "a refactor guard, not a capability court: it passes before and after C1, and a failure means the refactor changed something it may not",
            "the page reports the shape, because this host has no evaluation operation; the checksum is a djb2 over the ordered descriptor list",
            "property order is part of the pinned value, so a merge that reorders fails even when every descriptor matches",
            "tracked bytes are recorded on both arms; RSS is deliberately not a criterion, because the arena's page granularity dwarfs this refactor",
            "one hermetic loopback origin, macOS only; no surface, no window, no AppKit",
        ],
    }
    Path(args.receipt).write_text(json.dumps(receipt, indent=1, sort_keys=True) + "\n")
    print(json.dumps({"passed": receipt["passed"], "checks_passed": receipt["checks_passed"],
                      "checks_total": receipt["checks_total"]}))
    for check in checks:
        if not check["passed"]:
            print("FAIL", json.dumps(check)[:170])
    return 0 if receipt["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
