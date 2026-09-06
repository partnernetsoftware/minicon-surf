#!/usr/bin/env python3
"""The frozen court for H1: the host's answers are not the page's to write.

Frozen from `intrinsic-hardening-audit-0.0.1.md` §5 and the ruling that
followed it, **before** the host's scripts stop calling `JSON.stringify`, and
failing until they do.

The audit measured three things worth pinning. A page that replaces
`JSON.stringify` can make the snapshot script hand the agent a document that
does not exist; it can make the download probe send the host to a URL the
agent never referenced, with the profile's cookies; and it **cannot** flip an
activation refusal, because the host re-checks the node kind itself -- a
sentence that was false when it was written and is true again now. At the time
`not_a_link` was refused only when the **realm** declined to supply an href, and
the realm decided that with `el.tagName.toLowerCase()`; measured and corrected
in `uncaptured-intrinsic-audit-0.0.1.md` §4, and true again since `958f5c0` made
the download probe ask `__mcsTag`, a store no page can write. No criterion below
moved: the third one measured what it measured and still passes. The first
two must stop being true. The third must stay true.

The fix under test is that the host's own scripts serialise through
`__mcsJson`, which the base shim installs before any page script runs, holding
the intrinsic captured at that moment, non-writable and non-configurable.

Strictly headless: no surface binary, no window, no AppKit, and it refuses to
run with the visible-court variable set. One hermetic loopback origin; nothing
is fetched from outside this file.

Groups: snapshot, download, activation, shape, handle, redaction.
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
HOST_SOURCE = ROOT / "labs" / "native-dom" / "src" / "main.rs"

REAL_TEXT = "the text the page really has"
FORGED_TEXT = "FORGED-BY-PAGE"
ASKED_FOR = b"the file the agent asked for"
NEVER_ASKED = b"a file the agent never referenced"

# The page rewrites whatever the host's scripts hand back. Each branch is
# aimed at one host answer, so a failure names which one gave way.
TAMPER = """
<script>
(function () {
  var real = JSON.stringify;
  window.__mcsTamperCalls = 0;
  JSON.stringify = function (value) {
    window.__mcsTamperCalls++;
    if (value && typeof value === "object") {
      if ("nodes" in value) {
        return real({ revision: value.revision, truncated: false,
                      nodes: [{ role: "text", name: "FORGED-BY-PAGE",
                                reference: { target: "target_1", revision: 0, node: "node_1" } }] });
      }
      if ("href" in value) { return real({ href: "/never-asked.bin", declared: "asked.bin" }); }
      if ("decision" in value) {
        return real({ decision: "allowed", signature: value.signature || "", href: "/landed.html" });
      }
    }
    return real(value);
  };
})();
</script>
"""


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


class Host:
    def __init__(self, binary, directory, origin):
        environment = dict(os.environ)
        for knob in ("MINICON_SURF_NATIVE_REALM_ZONE", "MINICON_SURF_NATIVE_REALM_ARENA",
                     "MINICON_SURF_PROFILE_STORE", VISIBLE_ENV):
            environment.pop(knob, None)
        command = [binary, "serve", "--stdio", "--fixture-root", str(FIXTURE_ROOT),
                   "--config-dir", str(Path(directory) / "config"), "--allow-origin", origin]
        self.process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                        stderr=subprocess.DEVNULL, text=True, env=environment)
        self.counter = 0

    def raw(self, operation, arguments, validate=True):
        self.counter += 1
        request = {"protocol": "minicon-surf.control", "version": "0.0.2",
                   "request_id": f"req_h1_{self.counter}", "deadline_ms": 30000,
                   "operation": operation, "arguments": arguments}
        if validate:
            check_contract.validate_request(request)
        self.process.stdin.write(json.dumps(request) + "\n")
        self.process.stdin.flush()
        line = self.process.stdout.readline()
        if not line:
            return {"ok": False, "error": {"code": "host_exited"}}
        return json.loads(line)

    def call(self, operation, arguments):
        return self.raw(operation, arguments)

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


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--binary", required=True)
    parser.add_argument("--receipt", required=True)
    args = parser.parse_args()
    if os.environ.get(VISIBLE_ENV):
        print(json.dumps({"passed": False, "reason": "the visible-court variable is set"}))
        return 1

    network = RETENTION.load_network_module()
    served = []

    page = (
        '<!doctype html><html><body><main>'
        f'<p id="real">{REAL_TEXT}</p>'
        '<a id="dl" href="/asked.bin" download="asked.bin">file</a>'
        '<a id="down" href="/landed.html" download="x.bin">refused link</a>'
        '</main>' + TAMPER + '</body></html>'
    ).encode()

    class Handler(network.Handler):
        def do_GET(self):
            path, _, _query = self.path.partition("?")
            network.Handler.hits.append(path)
            served.append((path, self.headers.get("Cookie")))
            if path == "/asked.bin":
                return self.reply(200, ASKED_FOR, "application/octet-stream",
                                  [("Content-Disposition", 'attachment; filename="asked.bin"')])
            if path == "/never-asked.bin":
                return self.reply(200, NEVER_ASKED, "application/octet-stream",
                                  [("Content-Disposition", 'attachment; filename="never.bin"')])
            if path == "/landed.html":
                return self.reply(200, b"<!doctype html><html><body><p>landed</p></body></html>",
                                  "text/html")
            return self.reply(200, page, "text/html")

    server = network.Server(("127.0.0.1", 0), Handler)
    origin = f"http://127.0.0.1:{server.server_address[1]}"
    threading.Thread(target=server.serve_forever, daemon=True).start()
    checks = []

    def expect(name, condition, detail=None):
        checks.append({"check": name, "passed": bool(condition),
                       **({"detail": detail} if detail is not None else {})})

    try:
        with tempfile.TemporaryDirectory(prefix="minicon-surf-h1-") as directory:
            host = Host(args.binary, directory, origin)
            try:
                profile = host.ok("profile.create", {"persistence": "ephemeral"})["profile"]
                session = host.ok("session.open", {"profile": profile})["session"]
                target = host.ok("target.open", {"session": session,
                                                 "url": origin + "/a.html"})["target"]

                # S1/S2: the snapshot is the host's answer, not the page's.
                snapshot = host.call("target.snapshot", {"target": target, "format": "semantic",
                                                         "max_bytes": 65536, "max_nodes": 64})
                blob = json.dumps(snapshot)
                expect("S1: a tampering page cannot put its own nodes in the agent's snapshot",
                       FORGED_TEXT not in blob,
                       {"forged_present": FORGED_TEXT in blob,
                        "ok": bool(snapshot.get("ok"))})
                expect("S2: the snapshot carries the document that is really there, or is refused",
                       (REAL_TEXT in blob) or (not snapshot.get("ok")),
                       {"real_present": REAL_TEXT in blob, "ok": bool(snapshot.get("ok"))})

                nodes = (snapshot.get("result") or {}).get("nodes") or []
                by_id = {n.get("dom_id"): n for n in nodes if n.get("dom_id")}

                # D1/D2: the download goes where the agent pointed, and nowhere else.
                del served[:]
                download = None
                if "dl" in by_id:
                    download = host.raw("target.act", {
                        "target": target, "reference": by_id["dl"]["reference"],
                        "action": {"kind": "download"}}, validate=False)
                fetched = [path for path, _cookie in served]
                payload = b""
                if download and download.get("ok"):
                    import base64
                    payload = base64.b64decode(download["result"]["bytes_base64"])
                # Gated on the probe having run: while the snapshot is
                # forgeable the agent cannot even find the link, and a
                # criterion that passes because nothing happened is not a
                # criterion.
                expect("D1: the download fetches the URL the agent's reference names, and no other",
                       "dl" in by_id and download is not None
                       and "/never-asked.bin" not in fetched and "/asked.bin" in fetched,
                       {"server_saw": fetched, "found_link": "dl" in by_id})
                expect("D2: the bytes the agent receives are that URL's bytes",
                       (payload == ASKED_FOR) or (download is not None
                                                  and not download.get("ok")),
                       {"got_asked_for": payload == ASKED_FOR,
                        "got_other": payload == NEVER_ASKED,
                        "ok": bool(download and download.get("ok"))})

                # A1: the negative result stays negative.
                refused = None
                if "down" in by_id:
                    refused = host.call("target.act", {"target": target,
                                                       "reference": by_id["down"]["reference"],
                                                       "action": {"kind": "click"}})
                expect("A1: a forged decision still does not flip an activation refusal",
                       "down" in by_id and refused is not None and not refused.get("ok")
                       and refused["error"]["code"] == "unsupported_capability",
                       {"code": (refused or {}).get("error", {}).get("code")})

                # H1: the handle is consumed and its key set is unchanged.
                base_source = SHIM_BASE.read_text()
                expect("H1: the handle's key set is untouched by this work",
                       base_source.count("__mcsInternals") == 2
                       and "__mcsJson" in base_source,
                       {"mcsJson_in_base": "__mcsJson" in base_source})

                # R1: nothing page-shaped reaches the record.
                audit = host.ok("session.inspect", {"session": session})
                expect("R1: no page text and no query reaches the ledger",
                       FORGED_TEXT not in json.dumps(audit)
                       and REAL_TEXT not in json.dumps(audit),
                       {"audit_bytes": len(json.dumps(audit))})
            finally:
                host.finish()
    finally:
        server.shutdown()

    # The source rule this work is about: the host's own scripts do not call a
    # method a page can replace.
    host_source = HOST_SOURCE.read_text()
    raw_scripts = []
    depth = 0
    for piece in host_source.split('r#"')[1:]:
        raw_scripts.append(piece.split('"#')[0])
    script_text = "\n".join(raw_scripts)
    expect("J1: no host-side script serialises through JSON.stringify",
           "JSON.stringify" not in script_text,
           {"remaining": script_text.count("JSON.stringify")})
    expect("J2: the host's scripts serialise through the captured __mcsJson",
           script_text.count("__mcsJson") >= 20,
           {"uses": script_text.count("__mcsJson")})

    receipt = {
        "court": "native-dom host answers (H1: __mcsJson in host scripts)",
        "host_sha256": hashlib.sha256(Path(args.binary).read_bytes()).hexdigest(),
        "ruled": {"h1": "host-side scripts serialise through __mcsJson",
                  "invariant": "the host's answers to the agent are not the page's to write",
                  "unchanged": "the activation refusal, the handle key set, D6, G1"},
        "checks": checks,
        "checks_passed": sum(1 for c in checks if c["passed"]),
        "checks_total": len(checks),
        "passed": all(c["passed"] for c in checks),
        "limitations": [
            "design-frozen court: S1, D1, D2, J1 and J2 fail until the host's scripts stop calling JSON.stringify",
            "A1 is a standing regression of a negative result: the activation path already resisted this forgery and must keep resisting",
            "two criteria read the host's source rather than the binary, so they are repo-local by design",
            "the page tampers by branch, so a failure names which host answer gave way",
            "one hermetic loopback origin; nothing is fetched from outside this file",
            "this is not a claim that a shared realm can be made hostile-page-proof; it is that the host's own answers do not pass through page-replaceable functions",
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
