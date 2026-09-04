#!/usr/bin/env python3
"""Qualified CDP frame-tree court for the native route with a named client.

Frozen with labs/native-dom/cdp-qualification-0.0.1.json before the edge was
implemented. The court drives control 0.0.1 over stdio itself (every request
validated by protocol/check_contract.py) and drives the pinned puppeteer-core
through puppeteer-frame-tree.mjs over a command channel, so one live target is
observed through both doors: discovery lists exactly the native targets, the
CDP frame tree maps one-to-one onto the native frame through adapter-scoped
ids, a link click through CDP is the native same-frame navigation (revision,
generation and realm verified on the stdio side), the frame id survives it,
sessions never see another target's frames, a target closed over stdio turns
the CDP session into a typed failure, and adapters and owners are zero after
teardown. Everything runs under the default allocator and the opt-in arena
with footprint and owners sampled at empty, live and post-close.
"""

import argparse
import hashlib
import importlib.util
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

FIXTURE_ROOT = ROOT / "labs" / "court" / "fixtures"
QUALIFICATION = json.loads(Path(__file__).with_name("cdp-qualification-0.0.1.json").read_text(encoding="utf-8"))


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
    def __init__(self, binary, directory, allocator):
        environment = dict(os.environ)
        for knob in ("MINICON_SURF_NATIVE_REALM_ZONE", "MINICON_SURF_NATIVE_REALM_ARENA"):
            environment.pop(knob, None)
        if allocator == "arena":
            environment["MINICON_SURF_NATIVE_REALM_ARENA"] = "1"
        self.ready = Path(directory) / "ready.json"
        self.process = subprocess.Popen(
            [binary, "serve", "--stdio", "--fixture-root", str(FIXTURE_ROOT), "--config-dir", str(Path(directory) / "config"),
             "--cdp-port", "0", "--ready-file", str(self.ready)],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, env=environment)
        self.counter = 0

    def call(self, operation, arguments, deadline_ms=30000):
        self.counter += 1
        request = {"protocol": "minicon-surf.control", "version": "0.0.1", "request_id": f"req_cdpft_{self.counter}",
                   "deadline_ms": deadline_ms, "operation": operation, "arguments": arguments}
        check_contract.validate_request(request)
        self.process.stdin.write(json.dumps(request) + "\n")
        self.process.stdin.flush()
        line = self.process.stdout.readline()
        if not line:
            raise RuntimeError(f"host exited during {operation}")
        response = json.loads(line)
        check_contract.validate_response(response)
        return response

    def ok(self, operation, arguments, deadline_ms=30000):
        response = self.call(operation, arguments, deadline_ms)
        if not response["ok"]:
            raise RuntimeError(f"{operation} failed: {response['error']}")
        return response["result"]

    def endpoint(self):
        for _ in range(500):
            if self.ready.exists():
                return json.loads(self.ready.read_text())
            time.sleep(0.01)
        raise RuntimeError("CDP ready file did not appear")

    def finish(self):
        self.process.stdin.close()
        return self.process.wait(timeout=30)


class Client:
    def __init__(self, modules_root):
        self.process = subprocess.Popen(
            ["node", str(Path(__file__).with_name("puppeteer-frame-tree.mjs")), str(modules_root)],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)

    def command(self, command, **arguments):
        self.process.stdin.write(json.dumps({"command": command, **arguments}) + "\n")
        self.process.stdin.flush()
        line = self.process.stdout.readline()
        if not line:
            raise RuntimeError(f"client exited during {command}")
        return json.loads(line)

    def send(self, name, method, params=None):
        answer = self.command("send", name=name, method=method, params=params or {})
        if not answer.get("ok"):
            error = answer.get("error")
            if not isinstance(error, dict):
                error = {"message": str(error), "protocol_code": None}
            return {"ok": False, "error": error}
        return answer

    def finish(self):
        try:
            self.command("disconnect")
        except Exception:  # noqa: BLE001
            pass
        if self.process.poll() is None:
            self.process.stdin.close()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()


def snapshot_arguments(target, **extra):
    return {"target": target, "format": "semantic", "max_bytes": 65536, "max_nodes": 32, **extra}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--binary", required=True)
    parser.add_argument("--client-modules", default=str(ROOT / "target" / "labs" / "d4"))
    parser.add_argument("--receipt")
    args = parser.parse_args()
    checks = []
    client_identity = None
    footprints = {}
    mappings = {}

    def expect(name, condition, detail=None):
        checks.append({"check": name, "passed": bool(condition), **({"detail": detail} if detail is not None else {})})

    def refused(response, code, kind=None):
        error = response.get("error") or {}
        return (not response["ok"] and error["code"] == code and (kind is None or (error.get("scope") or {}).get("kind") == kind))

    # puppeteer-core 24.15.0 surfaces a CDP error as a ProtocolError whose
    # message carries the edge's text ("Protocol error (Method): <message>")
    # and whose numeric `code` is not populated by this version, so typed
    # failures are matched on the edge's message; the code is recorded when
    # the client exposes it.
    EDGE_MESSAGES = {-32601: "Method not found", -32000: None}

    def cdp_failed(answer, protocol_code=None, contains=None):
        if answer.get("ok"):
            return False
        error = answer.get("error") or {}
        message = str(error.get("message", ""))
        if protocol_code is not None:
            code = error.get("protocol_code")
            expected_text = EDGE_MESSAGES.get(protocol_code)
            if code is not None and code != protocol_code:
                return False
            if code is None and expected_text and expected_text not in message:
                return False
        return contains is None or contains in message

    for allocator in ("system", "arena"):
        tag = f"[{allocator}] "
        with tempfile.TemporaryDirectory(prefix="minicon-surf-cdp-frame-") as directory:
            host = Host(args.binary, directory, allocator)
            client = None
            try:
                profile = host.ok("profile.create", {"persistence": "ephemeral"})["profile"]
                session = host.ok("session.open", {"profile": profile})["session"]
                info = host.endpoint()
                empty = RETENTION.sample_process(host.process.pid)["physical_footprint_bytes"]
                a = host.ok("target.open", {"session": session, "fixture": "semantic-nav.html"})["target"]
                b = host.ok("target.open", {"session": session, "fixture": "semantic-static.html"})["target"]
                inspect_a = host.ok("target.inspect", {"target": a})
                inspect_b = host.ok("target.inspect", {"target": b})
                frame_a, realm_a = inspect_a["frames"][0]["frame"], inspect_a["frames"][0]["realm"]
                frame_b = inspect_b["frames"][0]["frame"]

                client = Client(args.client_modules)
                version = client.command("version")
                client_identity = {k: version.get(k) for k in ("client", "version", "node")}
                expect(tag + "the named client is the pinned puppeteer-core",
                       version.get("client") == "puppeteer-core" and version.get("version") == QUALIFICATION["named_client"]["version"], client_identity)
                connected = client.command("connect", endpoint=info["browser_websocket_url"])
                expect(tag + "puppeteer.connect succeeds over the loopback WebSocket endpoint", connected.get("ok"), connected)
                waited = client.command("waitForTarget", id=a)
                expect(tag + "waitForTarget finds the exact native target id", waited.get("ok") and waited.get("id") == a, waited)
                listed = client.command("targets")
                ids = sorted(t["id"] for t in listed.get("targets", []))
                expect(tag + "browser.targets lists exactly the native targets", ids == sorted([a, b]) and all(t["type"] == "page" for t in listed["targets"]), listed)

                attached = client.command("attach", name="A", id=a)
                expect(tag + "createCDPSession attaches a flattened session to A", attached.get("ok") and attached.get("attached"), attached)
                live_report = host.ok("memory.report", {})
                expect(tag + "the host holds one adapter per auto-attached target plus the explicit session, and no new owner",
                       live_report["owners"]["adapters"]["objects"] == 3 and live_report["owners"]["targets"]["objects"] == 2, live_report["owners"].get("adapters"))
                tree = client.send("A", "Page.getFrameTree")
                frame_tree = (tree.get("result") or {}).get("frameTree") or {}
                cdp_frame = (frame_tree.get("frame") or {}).get("id")
                expect(tag + "Page.getFrameTree returns one main frame with no children",
                       tree.get("ok") and cdp_frame and frame_tree.get("childFrames") == [] and "parentId" not in frame_tree["frame"], tree)
                expect(tag + "the CDP frame id is adapter-scoped and differs from the native frame id",
                       bool(cdp_frame) and cdp_frame != frame_a and str(cdp_frame).startswith("cdp_frame_"), {"cdp": cdp_frame, "native": frame_a})
                mappings[allocator] = {"native_frame": frame_a, "cdp_frame": cdp_frame}
                live = RETENTION.sample_process(host.process.pid)["physical_footprint_bytes"]

                document = client.send("A", "DOM.getDocument")
                root = ((document.get("result") or {}).get("root") or {})
                native_nodes = host.ok("target.snapshot", snapshot_arguments(a))
                expect(tag + "DOM.getDocument reports the native semantic node count", document.get("ok") and root.get("childNodeCount") == len(native_nodes["nodes"]), root)
                revision_before = native_nodes["revision"]
                link = client.send("A", "DOM.querySelector", {"nodeId": root.get("nodeId", 1), "selector": "a"})
                node_id = (link.get("result") or {}).get("nodeId")
                resolved = client.send("A", "DOM.resolveNode", {"nodeId": node_id})
                object_id = ((resolved.get("result") or {}).get("object") or {}).get("objectId")
                expect(tag + "querySelector('a') and resolveNode yield a session-scoped object", link.get("ok") and resolved.get("ok") and object_id, {"link": link, "resolved": resolved})
                clicked = client.send("A", "Runtime.callFunctionOn", {"objectId": object_id, "functionDeclaration": "function(){this.click();}"})
                expect(tag + "Runtime.callFunctionOn clicks the link", clicked.get("ok"), clicked)
                after = host.ok("target.inspect", {"target": a})
                expect(tag + "stdio sees the same-frame navigation: revision +1, generation 2, new realm, same frame",
                       after["revision"] == revision_before + 1 and after["frames"][0]["generation"] == 2 and after["frames"][0]["realm"] != realm_a
                       and after["frames"][0]["frame"] == frame_a and after["fixture"] == "semantic-static.html", after["frames"])
                stale = host.call("target.act", {"target": a, "reference": native_nodes["nodes"][0]["reference"], "action": {"kind": "click"}})
                expect(tag + "the pre-navigation native reference is stale_revision", refused(stale, "stale_revision"))
                retired = host.call("target.snapshot", snapshot_arguments(a, realm=realm_a))
                expect(tag + "the retired realm is not_found with realm scope", refused(retired, "not_found", "realm"))
                stale_object = client.send("A", "Runtime.callFunctionOn", {"objectId": object_id, "functionDeclaration": "function(){this.click();}"})
                expect(tag + "the pre-navigation CDP object fails typed (the native act is stale_revision)",
                       cdp_failed(stale_object, -32000, "native control operation failed"), stale_object)

                tree_after = client.send("A", "Page.getFrameTree")
                after_frame = (((tree_after.get("result") or {}).get("frameTree") or {}).get("frame") or {}).get("id")
                expect(tag + "the CDP frame id survives the navigation", tree_after.get("ok") and after_frame == cdp_frame, {"before": cdp_frame, "after": after_frame})
                document_after = client.send("A", "DOM.getDocument")
                root_after = ((document_after.get("result") or {}).get("root") or {})
                new_snapshot = host.ok("target.snapshot", snapshot_arguments(a))
                expect(tag + "after re-fetching the document CDP sees the new node count", document_after.get("ok") and root_after.get("childNodeCount") == len(new_snapshot["nodes"]), root_after)
                button = client.send("A", "DOM.querySelector", {"nodeId": 1, "selector": "button"})
                button_object = client.send("A", "DOM.resolveNode", {"nodeId": (button.get("result") or {}).get("nodeId")})
                button_click = client.send("A", "Runtime.callFunctionOn", {"objectId": ((button_object.get("result") or {}).get("object") or {}).get("objectId"), "functionDeclaration": "function(){this.click();}"})
                continued = host.ok("target.inspect", {"target": a})
                expect(tag + "the client continues on the new document: the button click is accepted and nothing navigates",
                       button_click.get("ok") and continued["frames"][0]["generation"] == 2 and continued["frames"][0]["realm"] == after["frames"][0]["realm"]
                       and continued["revision"] >= after["revision"], {"click": button_click, "revision": continued["revision"]})

                attached_b = client.command("attach", name="B", id=b)
                tree_b = client.send("B", "Page.getFrameTree")
                frame_b_cdp = (((tree_b.get("result") or {}).get("frameTree") or {}).get("frame") or {}).get("id")
                expect(tag + "a session on B sees B's frame with a different adapter id and never A's",
                       attached_b.get("ok") and tree_b.get("ok") and frame_b_cdp not in (cdp_frame, frame_a, frame_b) and frame_b_cdp != cdp_frame, {"b": frame_b_cdp})
                expect(tag + "four adapters are registered while both explicit sessions and both auto-attached sessions live", host.ok("memory.report", {})["owners"]["adapters"]["objects"] == 4)
                # Court amendment, recorded when control 0.0.2 added the
                # navigation slice: Page.navigate and Page.reload are mapped
                # now, so they are no longer method-not-found. These targets
                # are fixtures with no URL, so the mapped call fails typed on
                # that instead, and the history methods stay unmapped because
                # the host remains the only history authority.
                navigate = client.send("A", "Page.navigate", {"url": "https://example.com/"})
                expect(tag + "Page.navigate is mapped and fails typed on a fixture target, not -32601",
                       not navigate.get("ok") and not cdp_failed(navigate, -32601), navigate)
                reload_answer = client.send("A", "Page.reload", {})
                expect(tag + "Page.reload is mapped and fails typed on a fixture target, not -32601",
                       not reload_answer.get("ok") and not cdp_failed(reload_answer, -32601), reload_answer)
                for method in ("Page.getNavigationHistory", "Page.navigateToHistoryEntry"):
                    unmapped = client.send("A", method, {})
                    expect(tag + f"{method} is an explicit -32601: the host is the only history authority",
                           cdp_failed(unmapped, -32601), unmapped)
                enable = client.send("A", "Runtime.enable")
                expect(tag + "Runtime.enable is an explicit -32601 (no realm projection)", cdp_failed(enable, -32601), enable)

                closed = host.ok("target.close", {"target": a})
                expect(tag + "target.close over stdio detaches both of A's adapters (auto-attached and explicit)", closed.get("teardown", {}).get("adapters_detached") == 2, closed)
                after_close = client.send("A", "Page.getFrameTree")
                expect(tag + "the closed target's CDP session fails typed", cdp_failed(after_close, -32000, "detached"), after_close)
                expect(tag + "owners drop to one target and B's two adapters", host.ok("memory.report", {})["owners"]["adapters"]["objects"] == 2)
                detached = client.command("detach", name="B")
                expect(tag + "detaching the explicit B session releases its adapter", detached.get("ok") and host.ok("memory.report", {})["owners"]["adapters"]["objects"] == 1, detached)
                disconnected = client.command("disconnect")
                time.sleep(0.3)
                expect(tag + "browser.disconnect releases the auto-attached adapter and leaves the host serving",
                       disconnected.get("ok") and host.ok("memory.report", {})["owners"]["adapters"]["objects"] == 0 and host.ok("target.list", {})["targets"][0]["target"] == b)
                host.ok("target.close", {"target": b})
                report = host.ok("memory.report", {})["owners"]
                expect(tag + "after the closes every owner below the session is zero", report["targets"]["objects"] == 0 and report["adapters"]["objects"] == 0 and report["frames"]["objects"] == 0)
                post_close = RETENTION.sample_process(host.process.pid)["physical_footprint_bytes"]
                footprints[allocator] = {"empty": empty, "live_two_targets_one_session": live, "post_close": post_close}
                host.ok("session.close", {"session": session})
                expect(tag + "host exits cleanly", host.finish() == 0)
            finally:
                if client is not None:
                    client.finish()
                if host.process.poll() is None:
                    host.process.kill()
                    host.process.wait()

    passed = sum(1 for check in checks if check["passed"])
    receipt = {
        "schema": "minicon-surf.native-dom-cdp-frame-tree-receipt/0.0.1",
        "status": "observed",
        "technology": "native-dom",
        "technology_version": "0.0.2",
        "host_sha256": hashlib.sha256(Path(args.binary).read_bytes()).hexdigest(),
        "qualification": QUALIFICATION,
        "client": client_identity,
        "passed": passed == len(checks),
        "checks_passed": passed,
        "checks_total": len(checks),
        "checks": checks,
        "frame_id_mapping": mappings,
        "footprint_bytes": footprints,
        "limitations": [
            "one named client and version; target.page() and every puppeteer Page API are outside the claim",
            "loopback only; one connection at a time",
            "Page.FrameId is adapter-scoped per connection; Runtime.ExecutionContextId is never emitted",
            "no child frames, no Web API growth, no realm projection",
        ],
    }
    encoded = json.dumps(receipt, indent=2, sort_keys=True) + "\n"
    if args.receipt:
        Path(args.receipt).write_text(encoded, encoding="utf-8")
    print(json.dumps({"passed": receipt["passed"], "checks_passed": passed, "checks_total": len(checks), "client": client_identity, "footprint_bytes": footprints, "frame_id_mapping": mappings}, indent=1))
    for check in checks:
        if not check["passed"]:
            print("FAIL", check)
    return 0 if receipt["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
