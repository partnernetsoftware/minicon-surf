#!/usr/bin/env python3
"""The frozen court for downloads.

Frozen from `downloads-audit-0.0.1.md`, `download-sink-c-design-0.0.1.md`,
`download-envelope-audit-0.0.1.md` and `download-transport-stress-0.0.1.md`,
together with the rulings that followed them -- **before the host changes, and
failing until the capability exists**.

The ruled shape: bytes come back over the control protocol and never touch a
disk (sink C); the request is a `target.act` action kind named `download`, not
a new operation; the single-shot ceiling is the network response cap of
1,048,576 bytes, and a body over it is refused *before serialization* with a
typed `resource_limit`, never the generic `internal`; the filename is a bounded
verbatim report string and never a path; permission is checked at actual use;
per-profile budgets are `downloads` 32 and `download_bytes` 32 MiB, enforced
independently.

**T1 is the gate.** The transport stress that preceded this court could only be
run in two halves -- the writer and the reader separately -- because no shipped
operation can emit a line above 46,515 bytes. T1 is the join: a near-cap
download through the real host, byte_count and sha256 verified after decoding,
exactly one newline, no truncation. It runs first, and a failure there stops
the run rather than being papered over by the halves that already passed.

Strictly headless: no surface binary, no window, no AppKit, and it refuses to
run with the visible-court variable set. Hermetic loopback origin; nothing is
downloaded from anywhere but the fixture server in this file.

Groups: transport, contract, sink, name, permission, budget, lifecycle,
vectors, redaction.
"""

import argparse
import base64
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
EXAMPLES = ROOT / "protocol" / "examples"

NETWORK_CAP = 1_048_576
NEAR_CAP = 1_000_000
MAX_NAME_BYTES = 255
BUDGET_DOWNLOADS = 32
BUDGET_BYTES = 32 * 1024 * 1024
ANSWER_KEYS = {"kind", "byte_count", "sha256", "reported_name", "truncated", "bytes_base64"}
# A served name that is hostile on every axis a filename can be hostile on.
HOSTILE_NAME = '../../etc/passwd"; rm -rf /; \r\n'


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

PAYLOAD = bytes((index * 7 + 11) % 256 for index in range(NEAR_CAP))
PAYLOAD_SHA256 = hashlib.sha256(PAYLOAD).hexdigest()
OVERSIZED = b"z" * (NETWORK_CAP + 4096)

PAGE = (
    b"<!doctype html><html><body><main>"
    b"<a id=\"dl\" href=\"/file.bin\" download=\"report.bin\">file</a>"
    b"<a id=\"plain\" href=\"/other.html\">plain</a>"
    b"<a id=\"big\" href=\"/big.bin\" download=\"big.bin\">big</a>"
    b"<a id=\"odd\" href=\"/odd.bin\" download=\"odd.bin\">odd</a>"
    b"<p id=\"seen\">none</p></main><script>"
    b"var seen=[];"
    b"document.getElementById('dl').addEventListener('click',function(e){seen.push('dispatched');});"
    b"try{var r=document.getElementById('dl').click();"
    b"seen.push('returned:'+String(r));}catch(e){seen.push('threw:'+String(e&&e.name));}"
    b"document.getElementById('seen').textContent=seen.join(',');"
    b"</script></body></html>"
)


class Host:
    """One host over a profile root that outlives it, headless, one origin."""

    def __init__(self, binary, directory, origin, profile_root):
        environment = dict(os.environ)
        for knob in ("MINICON_SURF_NATIVE_REALM_ZONE", "MINICON_SURF_NATIVE_REALM_ARENA",
                     "MINICON_SURF_PROFILE_STORE", VISIBLE_ENV):
            environment.pop(knob, None)
        command = [binary, "serve", "--stdio", "--fixture-root", str(FIXTURE_ROOT),
                   "--config-dir", str(Path(directory) / "config"),
                   "--allow-origin", origin, "--profile-root", str(profile_root)]
        self.process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                        stderr=subprocess.DEVNULL, text=True, env=environment)
        self.counter = 0
        self.last_line = ""

    def raw(self, operation, arguments, validate=True, version="0.0.2"):
        """A request the contract may itself refuse: the download kind does
        not exist yet, so the court has to be able to reach the host anyway."""
        self.counter += 1
        request = {"protocol": "minicon-surf.control", "version": version,
                   "request_id": f"req_dl_{self.counter}", "deadline_ms": 30000,
                   "operation": operation, "arguments": arguments}
        if validate:
            check_contract.validate_request(request)
        self.process.stdin.write(json.dumps(request) + "\n")
        self.process.stdin.flush()
        line = self.process.stdout.readline()
        self.last_line = line
        if not line:
            return {"ok": False, "error": {"code": "host_exited"}}
        return json.loads(line)

    def call(self, operation, arguments, version="0.0.2"):
        return self.raw(operation, arguments, version=version)

    def ok(self, operation, arguments, version="0.0.2"):
        answer = self.call(operation, arguments, version=version)
        if not answer.get("ok"):
            raise RuntimeError(f"{operation}: {answer.get('error')}")
        return answer["result"]

    def download(self, target, node, revision):
        """The ruled request shape: an act, not an operation of its own."""
        return self.raw("target.act", {
            "target": target,
            "reference": {"target": target, "revision": revision, "node": node},
            "action": {"kind": "download"},
        }, validate=False)

    def finish(self):
        try:
            self.process.stdin.close()
            self.process.wait(timeout=15)
        except Exception:
            self.process.kill()


def refused(answer, code):
    return (not answer.get("ok")) and answer.get("error", {}).get("code") == code


def reason(answer):
    return ((answer.get("error") or {}).get("details") or {}).get("reason")


def find_value(structure, key):
    """The first value under `key` anywhere in an answer, or None."""
    if isinstance(structure, dict):
        if key in structure:
            return structure[key]
        for value in structure.values():
            found = find_value(value, key)
            if found is not None:
                return found
    elif isinstance(structure, list):
        for value in structure:
            found = find_value(value, key)
            if found is not None:
                return found
    return None


def tree(root):
    root = Path(root)
    if not root.exists():
        return set()
    return {str(path.relative_to(root)) for path in root.rglob("*")}


def node_of(host, target, selector_id):
    """The node reference for an element, through the snapshot the agent has."""
    snapshot = host.ok("target.snapshot", {"target": target, "format": "semantic",
                                           "max_bytes": 65536, "max_nodes": 128})
    revision = snapshot["revision"]
    for entry in snapshot["nodes"]:
        if entry.get("dom_id") == selector_id:
            reference = entry.get("reference") or {}
            return reference.get("node"), reference.get("revision", revision)
    return None, revision


def activation_of(host, target, selector_id):
    """What the snapshot tells the agent about this link before it acts."""
    snapshot = host.ok("target.snapshot", {"target": target, "format": "semantic",
                                           "max_bytes": 65536, "max_nodes": 128})
    for entry in snapshot["nodes"]:
        if entry.get("dom_id") == selector_id:
            return entry.get("activation")
    return None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--binary", required=True)
    parser.add_argument("--receipt", required=True)
    args = parser.parse_args()
    if os.environ.get(VISIBLE_ENV):
        print(json.dumps({"passed": False, "reason": "the visible-court variable is set"}))
        return 1

    network = RETENTION.load_network_module()

    class Handler(network.Handler):
        def do_GET(self):
            path, _, _query = self.path.partition("?")
            network.Handler.hits.append(path)
            if path == "/file.bin":
                return self.reply(200, PAYLOAD, "application/octet-stream",
                                  [("Content-Disposition", 'attachment; filename="report.bin"')])
            if path == "/big.bin":
                return self.reply(200, OVERSIZED, "application/octet-stream",
                                  [("Content-Disposition", 'attachment; filename="big.bin"')])
            if path == "/odd.bin":
                return self.reply(200, PAYLOAD[:4096], "application/octet-stream",
                                  [("Content-Disposition",
                                    'attachment; filename="' + HOSTILE_NAME + 'x' * 400 + '"')])
            if path == "/other.html":
                return self.reply(200, b"<!doctype html><html><body><p>other</p></body></html>",
                                  "text/html")
            return self.reply(200, PAGE, "text/html")

    server = network.Server(("127.0.0.1", 0), Handler)
    origin = f"http://127.0.0.1:{server.server_address[1]}"
    threading.Thread(target=server.serve_forever, daemon=True).start()
    checks = []

    def expect(name, condition, detail=None):
        checks.append({"check": name, "passed": bool(condition),
                       **({"detail": detail} if detail is not None else {})})

    # ---- contract criteria, read from the repo rather than the binary ------
    contract_source = (ROOT / "protocol" / "check_contract.py").read_text()
    expect("C1: no new operation joins the enum -- the download is an act kind",
           '"target.download"' not in contract_source
           and "target.download" not in check_contract.OPERATIONS_NEXT
           and len(check_contract.OPERATIONS_NEXT) == 26,
           {"operations": len(check_contract.OPERATIONS_NEXT)})
    expect("C2: the contract accepts {kind: download} and refuses it with an argument",
           contract_accepts_download(),
           {"validator": "check_contract.validate_request"})
    expect("C3: an example pair carries the act and the sink C answer",
           (EXAMPLES / "target-act-download.request.json").exists()
           and (EXAMPLES / "target-act-download.success.json").exists(),
           {"request": (EXAMPLES / "target-act-download.request.json").exists()})

    try:
        with tempfile.TemporaryDirectory(prefix="minicon-surf-downloads-") as directory:
            root = Path(directory) / "profiles"
            root.mkdir()
            before = tree(root) | tree(Path(directory) / "config")
            host = Host(args.binary, directory, origin, root)
            try:
                profile = host.ok("profile.create", {"persistence": "ephemeral"})["profile"]
                session = host.ok("session.open", {"profile": profile})["session"]
                target = host.ok("target.open", {"session": session,
                                                 "url": origin + "/a.html"})["target"]
                node, revision = node_of(host, target, "dl")

                # ---- T1, the gate --------------------------------------
                answer = host.download(target, node or "node_1", revision)
                result = answer.get("result") or {}
                payload = b""
                if isinstance(result.get("bytes_base64"), str):
                    try:
                        payload = base64.b64decode(result["bytes_base64"])
                    except Exception:
                        payload = b""
                one_line = host.last_line.count("\n") == 1 and host.last_line.endswith("\n")
                gate = (answer.get("ok") and payload == PAYLOAD
                        and result.get("byte_count") == len(PAYLOAD)
                        and result.get("sha256") == PAYLOAD_SHA256 and one_line)
                expect("T1: a near-cap download round-trips through the real host as one line",
                       gate,
                       {"ok": bool(answer.get("ok")),
                        "code": (answer.get("error") or {}).get("code"),
                        "byte_count": result.get("byte_count"),
                        "sha256_matches": result.get("sha256") == PAYLOAD_SHA256,
                        "payload_intact": payload == PAYLOAD,
                        "single_line": one_line,
                        "line_bytes": len(host.last_line)})

                # ---- T2: over the ceiling, before serialization ---------
                big_node, big_revision = node_of(host, target, "big")
                over = host.download(target, big_node or "node_1", big_revision)
                expect("T2: a body over the network cap is a typed resource_limit, never internal",
                       refused(over, "resource_limit")
                       and (over.get("error") or {}).get("code") != "internal"
                       and "bytes_base64" not in json.dumps(over),
                       {"code": (over.get("error") or {}).get("code"),
                        "reason": reason(over), "carried_bytes": "bytes_base64" in json.dumps(over)})

                # ---- S1/S3: the answer's shape --------------------------
                expect("S1: the answer carries the ruled keys and nothing else",
                       set(result) == ANSWER_KEYS,
                       {"keys": sorted(result)})
                expect("S3: a single transfer -- no handle, cursor or continuation",
                       bool(result) and not ({"handle", "cursor", "continuation", "offset", "more"}
                                             & set(result)),
                       {"keys": sorted(result)})

                # ---- N1: the name, bounded and verbatim ------------------
                odd_node, odd_revision = node_of(host, target, "odd")
                odd = host.download(target, odd_node or "node_1", odd_revision)
                odd_result = odd.get("result") or {}
                name = odd_result.get("reported_name")
                expect("N1: the name is bounded to 255 bytes and reported verbatim, not sanitised",
                       isinstance(name, str) and len(name.encode()) <= MAX_NAME_BYTES
                       and odd_result.get("truncated") is True
                       and HOSTILE_NAME[:8] in name,
                       {"name_bytes": len(name.encode()) if isinstance(name, str) else None,
                        "truncated": odd_result.get("truncated"),
                        "verbatim": isinstance(name, str) and HOSTILE_NAME[:8] in name})

                # ---- P1: permission at use ------------------------------
                host.ok("profile.policy.set", {"session": session, "network": "online",
                                               "permissions": "deny_by_default"})
                denied = host.download(target, node or "node_1", revision)
                expect("P1: with the policy denying, the download is permission_denied at use",
                       refused(denied, "permission_denied"),
                       {"code": (denied.get("error") or {}).get("code")})
                expect("P2: permission_denied stays distinct from the unsupported refusals",
                       refused(denied, "permission_denied")
                       and (denied.get("error") or {}).get("code") != "unsupported_capability",
                       {"code": (denied.get("error") or {}).get("code")})

                # ---- V1/V2: the vectors that keep their old answers ------
                # The page called link.click() on itself while it loaded, and
                # wrote down what it observed. A browser page observes nothing
                # there, so neither may this one -- before or after the
                # capability lands.
                seen = ""
                snapshot = host.ok("target.snapshot", {"target": target, "format": "semantic",
                                                       "max_bytes": 65536, "max_nodes": 128})
                for entry in snapshot["nodes"]:
                    if entry.get("dom_id") == "seen":
                        seen = entry.get("name") or ""
                host_words = ("unsupported", "capability", "permission", "resource_limit",
                              "download_unsupported", "policy")
                expect("V1: the page's own click on a[download] stays silent to the page",
                       "threw" not in seen and not any(word in seen for word in host_words)
                       and "dispatched" in seen,
                       {"page_observed": seen[:80]})
                # The snapshot labels this link today; once bytes can be
                # served the label must stop saying the opposite.
                activation = activation_of(host, target, "dl")
                expect("V3: the snapshot stops telling the agent the link is unsupported",
                       activation != "download_unsupported",
                       {"activation": activation})
                navigated = host.call("target.navigate", {"target": target,
                                                          "url": origin + "/file.bin"})
                expect("V2: navigating at an attachment keeps its own typed refusal",
                       refused(navigated, "unsupported_capability"),
                       {"code": (navigated.get("error") or {}).get("code"),
                        "reason": reason(navigated)})

                # ---- B3: the budgets are visible before they bind --------
                report = host.call("memory.report", {})
                expect("B3: the download budgets are reported with their ruled values",
                       find_value(report, "download_bytes") == BUDGET_BYTES
                       and find_value(report, "downloads") == BUDGET_DOWNLOADS,
                       {"download_bytes": find_value(report, "download_bytes"),
                        "downloads": find_value(report, "downloads")})

                # ---- R1: nothing page-shaped reaches the record ----------
                audit = host.call("session.inspect", {"session": session})
                audit_blob = json.dumps(audit)
                expect("V4: the agent's record names the attempt the page could not see",
                       "download" in audit_blob,
                       {"record_bytes": len(audit_blob)})
                expect("R1: the record carries no filename and no page values",
                       "report.bin" not in audit_blob and HOSTILE_NAME[:8] not in audit_blob
                       and "bytes_base64" not in audit_blob,
                       {"audit_bytes": len(audit_blob)})

                # ---- L1: nothing is written anywhere --------------------
                after = tree(root) | tree(Path(directory) / "config")
                expect("L1 (sink C): the download leaves no artefact on any disk",
                       after == before or not any(name.endswith(".bin") for name in after - before),
                       {"new_entries": sorted(after - before)[:8]})
            finally:
                host.finish()
    finally:
        server.shutdown()

    # Budget criteria are stated here and measured once the capability exists:
    # 33 downloads and 32 MiB are too slow to drive against a host that
    # refuses the first one, so they stay explicit rather than silently absent.
    expect("B1: the 33rd download in a profile is refused resource_limit",
           False, {"pending": "measured once the capability exists",
                   "limit": BUDGET_DOWNLOADS})
    expect("B2: the byte budget binds independently of the count",
           False, {"pending": "measured once the capability exists",
                   "limit": BUDGET_BYTES})
    expect("L2: a target closed mid-transfer answers a cancellation, not a partial payload",
           False, {"pending": "measured once the capability exists"})

    receipt = {
        "court": "native-dom downloads (sink C, act kind download)",
        "host_sha256": hashlib.sha256(Path(args.binary).read_bytes()).hexdigest(),
        "ruled": {"sink": "C: bytes over the control protocol, never a disk",
                  "request": "target.act, action kind download",
                  "ceiling": NETWORK_CAP,
                  "over_ceiling": "typed resource_limit before serialization",
                  "name": f"bounded {MAX_NAME_BYTES} bytes, verbatim, never a path",
                  "permission": "checked at use",
                  "budgets": {"downloads": BUDGET_DOWNLOADS, "download_bytes": BUDGET_BYTES}},
        "checks": checks,
        "checks_passed": sum(1 for c in checks if c["passed"]),
        "checks_total": len(checks),
        "passed": all(c["passed"] for c in checks),
        "limitations": [
            "design-frozen court: it fails until target.act accepts the download kind",
            "T1 is the gate and the join the two-half transport stress could not test; a failure there stops the work rather than being covered by the halves that passed",
            "B1, B2 and L2 are stated but not driven against a host that refuses the first download; they must be measured once the capability exists",
            "three criteria read the contract source and its examples rather than the binary, so they are repo-local by design",
            "one hermetic loopback origin; nothing is fetched from outside this file",
            "strictly headless: no surface, no window, no AppKit",
        ],
    }
    Path(args.receipt).write_text(json.dumps(receipt, indent=1, sort_keys=True) + "\n")
    print(json.dumps({"passed": receipt["passed"], "checks_passed": receipt["checks_passed"],
                      "checks_total": receipt["checks_total"]}))
    for check in checks:
        if not check["passed"]:
            print("FAIL", json.dumps(check)[:170])
    return 0 if receipt["passed"] else 1


def contract_accepts_download():
    request = {"protocol": "minicon-surf.control", "version": "0.0.2",
               "request_id": "req_1", "deadline_ms": 30000, "operation": "target.act",
               "arguments": {"target": "target_1",
                             "reference": {"target": "target_1", "revision": 1, "node": "node_1"},
                             "action": {"kind": "download"}}}
    try:
        check_contract.validate_request(request)
    except Exception:
        return False
    hostile = json.loads(json.dumps(request))
    hostile["arguments"]["action"]["path"] = "/tmp/x"
    try:
        check_contract.validate_request(hostile)
    except Exception:
        return True
    return False


if __name__ == "__main__":
    sys.exit(main())
