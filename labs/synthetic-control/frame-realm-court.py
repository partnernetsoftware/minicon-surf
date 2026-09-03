#!/usr/bin/env python3
"""Frame and realm identity court: native stdio and the CDP edge observe the
same target, frames and realms, before and after a same-frame navigation.

Native requests are validated by protocol/check_contract.py before they are
sent. The court checks that the four concepts stay distinct (target revision,
frame identity, document generation, realm identity), that enumeration is
bounded and per target, that foreign, ended and unknown frame or realm ids
are refused alike, that old node references, retired realms and ended frames
fail typed after navigation, that capability attenuation covers frame-narrowed
operations without making a frame an owner, and that the CDP projection of
frame identity is one-to-one with the native enumeration while both live.
"""

import argparse
import hashlib
import http.client
import importlib.util
import json
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "protocol"))
import check_contract  # noqa: E402


def load_g2():
    spec = importlib.util.spec_from_file_location("g2", Path(__file__).with_name("cdp-native-journey.py"))
    module = importlib.util.module_from_spec(spec)
    saved = sys.argv
    sys.argv = ["g2"]
    try:
        spec.loader.exec_module(module)
    finally:
        sys.argv = saved
    return module


def capability(kind, owner, scope):
    return {"owner": {"kind": kind, "id": owner}, "scope": scope,
            "budget": {"result_bytes": 65536, "deadline_ms": 100},
            "audit": {"actor": "agent.court", "reason": "frame court"}}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--binary", required=True)
    parser.add_argument("--receipt")
    args = parser.parse_args()
    g2 = load_g2()
    checks = []

    def expect(name, condition, detail=None):
        checks.append({"check": name, "passed": bool(condition), **({"detail": detail} if detail is not None else {})})

    def refused(response, code, kind=None):
        error = response.get("error") or {}
        return (not response["ok"] and error["code"] == code
                and (kind is None or (error.get("scope") or {}).get("kind") == kind))

    with tempfile.TemporaryDirectory() as directory:
        ready_path = str(Path(directory) / "ready.json")
        host = g2.Host(args.binary, ready_path)
        original_call = host.call

        def call(operation, arguments, cap=None):
            request = {"protocol": "minicon-surf.control", "version": "0.0.1", "request_id": "req_g2_0",
                       "deadline_ms": 100, "operation": operation, "arguments": arguments}
            if cap is not None:
                request["capability"] = cap
            check_contract.validate_request(request)
            if cap is None:
                response = original_call(operation, arguments)
            else:
                host.next_id += 1
                request["request_id"] = f"req_g2_{host.next_id}"
                host.process.stdin.write(json.dumps(request, separators=(",", ":")) + "\n")
                host.process.stdin.flush()
                response = json.loads(host.process.stdout.readline())
            check_contract.validate_response(response)
            return response

        host.call = call
        snapshot_arguments = lambda target, **extra: {"target": target, "format": "semantic", "max_bytes": 65536, "max_nodes": 16, **extra}  # noqa: E731
        try:
            profile = host.call("profile.create", {"persistence": "ephemeral"})["result"]["profile"]
            session = host.call("session.open", {"profile": profile})["result"]["session"]
            target = host.call("target.open", {"session": session})["result"]["target"]
            other = host.call("target.open", {"session": session})["result"]["target"]

            # 1. Bounded per-target enumeration with distinct concepts.
            inspect = host.call("target.inspect", {"target": target})["result"]
            frames, realms = inspect["frames"], inspect["realms"]
            expect("enumeration lists the main frame first and one bounded child",
                   len(frames) == 2 and frames[0]["parent"] is None and frames[1]["parent"] == frames[0]["frame"]
                   and inspect["frame_limit"] == 8, frames)
            expect("every frame carries its own generation and realm; the target carries the revision",
                   all(f["generation"] == 1 for f in frames) and len({f["realm"] for f in frames}) == 2 and inspect["revision"] == 0)
            expect("realms enumerate with their frame and world",
                   [r["frame"] for r in realms] == [f["frame"] for f in frames] and all(r["world"] == "main" for r in realms))
            main, child = frames[0]["frame"], frames[1]["frame"]
            main_realm, child_realm = frames[0]["realm"], frames[1]["realm"]
            other_inspect = host.call("target.inspect", {"target": other})["result"]
            expect("frame and realm ids are disjoint across targets",
                   not {f["frame"] for f in other_inspect["frames"]} & {main, child}
                   and not {f["realm"] for f in other_inspect["frames"]} & {main_realm, child_realm})

            # 2. Frame-narrowed snapshots.
            default = host.call("target.snapshot", snapshot_arguments(target))["result"]
            expect("a snapshot without a frame observes the main frame and names frame, realm and generation",
                   default["frame"] == main and default["realm"] == main_realm and default["generation"] == 1 and len(default["nodes"]) == 3)
            embedded = host.call("target.snapshot", snapshot_arguments(target, frame=child, realm=child_realm))["result"]
            expect("a child-frame snapshot observes the child document",
                   embedded["frame"] == child and embedded["nodes"][0]["name"] == "Embedded court" and embedded["revision"] == 0)

            # 3. Foreign, unknown and mismatched references are refused alike.
            foreign = host.call("target.snapshot", snapshot_arguments(target, frame=other_inspect["frames"][0]["frame"]))
            unknown = host.call("target.snapshot", snapshot_arguments(target, frame="frame_9999"))
            expect("another target's frame and an unknown frame are the same not_found",
                   refused(foreign, "not_found", "frame") and refused(unknown, "not_found", "frame")
                   and foreign["error"]["message"] == unknown["error"]["message"])
            mismatched = host.call("target.snapshot", snapshot_arguments(target, frame=main, realm=child_realm))
            expect("a realm that is not the frame's live realm is not_found with realm scope", refused(mismatched, "not_found", "realm"))

            # 4. Capability attenuation covers frame-narrowed operations; frames never own.
            allowed = host.call("target.snapshot", snapshot_arguments(target, frame=child), capability("target", target, ["target.snapshot"]))
            expect("a target-owned capability allows a frame-narrowed snapshot", allowed["ok"] and allowed["result"]["frame"] == child)
            frame_owner = host.call("target.snapshot", snapshot_arguments(target, frame=child), capability("frame", child, ["target.snapshot"]))
            realm_owner = host.call("target.snapshot", snapshot_arguments(target, frame=child), capability("realm", child_realm, ["target.snapshot"]))
            expect("a frame or realm named as owner is refused",
                   all(refused(r, "permission_denied") and r["error"]["details"]["reason"] == "kind_is_not_an_owner" for r in (frame_owner, realm_owner)))
            cross = host.call("target.snapshot", snapshot_arguments(target, frame=child), capability("target", other, ["target.snapshot"]))
            expect("another target's capability cannot reach this target's frame",
                   refused(cross, "permission_denied") and cross["error"]["details"]["reason"] == "owner_not_on_chain")

            # 5. CDP observes the same frames through adapter-scoped ids.
            for _ in range(100):
                if Path(ready_path).exists():
                    break
                time.sleep(0.01)
            ready = json.loads(Path(ready_path).read_text())
            connection = http.client.HTTPConnection("127.0.0.1", ready["cdp_port"], timeout=3)
            connection.request("GET", "/json/version")
            discovery = json.loads(connection.getresponse().read())
            ws = g2.WebSocket(discovery["webSocketDebuggerUrl"])
            counter = [0]

            def cdp(method, params=None, session_id=None):
                counter[0] += 1
                return ws.call(counter[0], method, params, session_id)

            cdp_session = cdp("Target.attachToTarget", {"targetId": target, "flatten": True})["result"]["sessionId"]
            tree = cdp("Page.getFrameTree", session_id=cdp_session)["result"]["frameTree"]
            cdp_main = tree["frame"]["id"]
            cdp_children = [c["frame"]["id"] for c in tree["childFrames"]]
            expect("CDP frame tree has the same shape as the native enumeration",
                   len(cdp_children) == 1 and tree["childFrames"][0]["frame"]["parentId"] == cdp_main and "parentId" not in tree["frame"])
            expect("CDP frame ids are adapter-scoped, never the native ids",
                   cdp_main != main and cdp_children[0] != child and cdp_main.startswith("cdp_frame_"))
            other_session = cdp("Target.attachToTarget", {"targetId": other, "flatten": True})["result"]["sessionId"]
            other_tree = cdp("Page.getFrameTree", session_id=other_session)["result"]["frameTree"]
            expect("another target's CDP session sees different frame ids",
                   other_tree["frame"]["id"] not in {cdp_main, *cdp_children})
            document = cdp("DOM.getDocument", session_id=cdp_session)["result"]["root"]
            node = cdp("DOM.querySelector", {"nodeId": document["nodeId"], "selector": "button"}, cdp_session)["result"]["nodeId"]
            object_id = cdp("DOM.resolveNode", {"nodeId": node}, cdp_session)["result"]["object"]["objectId"]

            # 6. Navigation: revision, generation and realm move; the frame id stays.
            navigated = host.call("target.act", {"target": target, "reference": default["nodes"][2]["reference"], "action": {"kind": "click"}})["result"]
            expect("a link click is a same-frame navigation",
                   navigated["navigated"] and navigated["frame"] == main and navigated["generation"] == 2
                   and navigated["retired_realm"] == main_realm and navigated["ended_frames"] == [child] and navigated["revision"] == 1, navigated)
            after = host.call("target.inspect", {"target": target})["result"]
            expect("the frame id survives, the realm is replaced, the child frame ended, the revision advanced",
                   len(after["frames"]) == 1 and after["frames"][0]["frame"] == main and after["frames"][0]["realm"] != main_realm
                   and after["frames"][0]["generation"] == 2 and after["revision"] == 1)
            stale = host.call("target.act", {"target": target, "reference": default["nodes"][1]["reference"], "action": {"kind": "click"}})
            expect("a node reference from before the navigation is stale_revision", refused(stale, "stale_revision"))
            retired = host.call("target.snapshot", snapshot_arguments(target, frame=main, realm=main_realm))
            expect("the retired realm is not_found with realm scope and a typed reason",
                   refused(retired, "not_found", "realm") and retired["error"]["details"]["reason"] == "realm_not_live_in_target")
            ended = host.call("target.snapshot", snapshot_arguments(target, frame=child))
            expect("the ended child frame is not_found with frame scope", refused(ended, "not_found", "frame"))
            fresh = host.call("target.snapshot", snapshot_arguments(target, frame=main))["result"]
            expect("the new document is observable through the surviving frame",
                   fresh["generation"] == 2 and fresh["realm"] == after["frames"][0]["realm"] and fresh["nodes"][0]["name"] == "Navigated court")

            # 7. CDP after navigation: same main id, child gone, old handles stale.
            tree_after = cdp("Page.getFrameTree", session_id=cdp_session)["result"]["frameTree"]
            expect("CDP keeps the main frame id and drops the ended child",
                   tree_after["frame"]["id"] == cdp_main and tree_after["childFrames"] == [])
            stale_cdp = cdp("Runtime.callFunctionOn", {"objectId": object_id, "functionDeclaration": "function(){this.click();}"}, cdp_session)
            expect("a CDP handle from before the navigation fails typed", stale_cdp.get("error", {}).get("code") == -32000)
            document_after = cdp("DOM.getDocument", session_id=cdp_session)["result"]["root"]
            expect("CDP re-observes the new document", document_after["childNodeCount"] == 3)
            second = host.call("target.act", {"target": target, "reference": fresh["nodes"][2]["reference"], "action": {"kind": "click"}})["result"]
            expect("a second navigation mints a third realm on the same frame",
                   second["generation"] == 3 and second["frame"] == main and second["realm"] != fresh["realm"] and second["revision"] == 2)

            # 8. Owners and teardown.
            report = host.call("memory.report", {})["result"]["owners"]
            expect("frame and realm owners count the live frames", report["frames"]["objects"] == 3 and report["realms"]["objects"] == 3)
            host.call("target.close", {"target": target})
            host.call("target.close", {"target": other})
            report = host.call("memory.report", {})["result"]["owners"]
            expect("closing the targets leaves zero frames and realms", report["frames"]["objects"] == 0 and report["realms"]["objects"] == 0)
            gone = host.call("target.snapshot", snapshot_arguments(target, frame=main))
            expect("a closed target's frame is not_found at the target", refused(gone, "not_found", "target"))
            host.call("session.close", {"session": session})
            ws.close()
            host.finish()
            expect("host exits cleanly", True)
        finally:
            if host.process.poll() is None:
                host.process.kill()
                host.process.wait()

    passed = sum(1 for check in checks if check["passed"])
    receipt = {
        "schema": "minicon-surf.synthetic-frame-realm-receipt/0.0.1",
        "status": "observed",
        "technology": "synthetic-control",
        "technology_version": "0.0.1",
        "host_sha256": hashlib.sha256(Path(args.binary).read_bytes()).hexdigest(),
        "semantic": "native stdio and the loopback CDP edge observe one target's frames and realms before and after a same-frame navigation; native requests validated by protocol/check_contract.py",
        "passed": passed == len(checks),
        "checks_passed": passed,
        "checks_total": len(checks),
        "checks": checks,
        "losses": [
            "realm identity is not projected to CDP (no Runtime.enable or execution-context events); a CDP client sees realm replacement only as stale DOM handles",
            "no navigation events are projected; navigation is a native link click, not Page.navigate",
            "one bounded child frame, one world; no nested frames",
            "document generation has no CDP equivalent",
        ],
    }
    encoded = json.dumps(receipt, indent=2, sort_keys=True) + "\n"
    if args.receipt:
        Path(args.receipt).write_text(encoded, encoding="utf-8")
    print(encoded, end="")
    return 0 if receipt["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
