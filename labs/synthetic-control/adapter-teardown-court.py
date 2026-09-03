#!/usr/bin/env python3
"""Dependency-free court for adapter teardown ordering (X9 micro-experiment ME2).

The loopback CDP edge is the adapter: each attached CDP session holds only a
weak handle to its target. The court attaches adapters, tears targets and
sessions down from the native side while they are attached, and checks the
owner ledger, the logical bytes, the teardown reports, the profile lock and
the adapter's typed failures afterwards. Native requests are validated by
protocol/check_contract.py before they are sent.
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


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--binary", required=True)
    parser.add_argument("--receipt")
    args = parser.parse_args()
    g2 = load_g2()
    checks = []

    def expect(name, condition, detail=None):
        checks.append({"check": name, "passed": bool(condition), **({"detail": detail} if detail is not None else {})})

    with tempfile.TemporaryDirectory() as directory:
        ready_path = str(Path(directory) / "ready.json")
        host = g2.Host(args.binary, ready_path)
        original_call = host.call

        def call(operation, arguments):
            request = {"protocol": "minicon-surf.control", "version": "0.0.1", "request_id": "req_g2_0",
                       "deadline_ms": 100, "operation": operation, "arguments": arguments}
            check_contract.validate_request(request)
            response = original_call(operation, arguments)
            check_contract.validate_response(response)
            return response

        host.call = call
        try:
            profile = host.call("profile.create", {"persistence": "ephemeral"})["result"]["profile"]
            session = host.call("session.open", {"profile": profile})["result"]["session"]
            target = host.call("target.open", {"session": session})["result"]["target"]
            for _ in range(100):
                if Path(ready_path).exists():
                    break
                time.sleep(0.01)
            ready = json.loads(Path(ready_path).read_text())
            connection = http.client.HTTPConnection("127.0.0.1", ready["cdp_port"], timeout=3)
            connection.request("GET", "/json/version")
            discovery = json.loads(connection.getresponse().read())
            ws = g2.WebSocket(discovery["webSocketDebuggerUrl"])
            request_id = [0]

            def cdp(method, params=None, session_id=None):
                request_id[0] += 1
                return ws.call(request_id[0], method, params, session_id)

            def owners():
                return host.call("memory.report", {})["result"]

            base = owners()
            expect("ledger starts with no adapters", base["owners"]["adapters"]["objects"] == 0
                   and base["teardown"]["adapters_detached_total"] == 0)

            # 1. Attach: one adapter, accounted bytes rise by the adapter record only.
            cdp_session = cdp("Target.attachToTarget", {"targetId": target, "flatten": True})["result"]["sessionId"]
            attached = owners()
            adapter_bytes = attached["owners"]["adapters"]["bytes"]
            expect("attach registers one adapter with accounted bytes",
                   attached["owners"]["adapters"]["objects"] == 1 and adapter_bytes > 0
                   and attached["total_accounted_bytes"] == base["total_accounted_bytes"] + adapter_bytes,
                   {"adapter_bytes": adapter_bytes})
            expect("adapter does not add a target or session owner",
                   attached["owners"]["targets"]["objects"] == 1 and attached["owners"]["sessions"]["objects"] == 1)
            root = cdp("DOM.getDocument", session_id=cdp_session)
            expect("adapter operates through the target while it lives", "result" in root)

            # 2. Adapter authority is the target's only: it cannot reach beyond it.
            inspect = host.call("session.inspect", {"session": session})["result"]
            entries = inspect["capability_audit"]
            expect("adapter calls are attenuated to their target and audited",
                   entries and all(e["actor"] == "cdp.adapter" and e["owner"] == {"kind": "target", "id": target}
                                   and e["decision"] == "allowed" for e in entries),
                   {"records": len(entries)})

            # 3. Native target.close while attached: adapters first, then the target.
            closed = host.call("target.close", {"target": target})["result"]
            expect("target.close reports one adapter detached before the target dropped",
                   closed["teardown"]["adapters_detached"] == 1 and closed["teardown"]["owner_reference_extended"] is False,
                   closed["teardown"])
            after_close = owners()
            expect("owner ledger returns to the session baseline after the close",
                   after_close["owners"]["adapters"]["objects"] == 0 and after_close["owners"]["targets"]["objects"] == 0
                   and after_close["total_accounted_bytes"] < base["total_accounted_bytes"])
            expect("no owner reference was extended", after_close["teardown"]["owner_references_extended_total"] == 0
                   and after_close["teardown"]["order"] == ["adapters", "surfaces", "target", "profile_lock"])
            stale = cdp("DOM.getDocument", session_id=cdp_session)
            expect("adapter command after the close is a typed detachment",
                   stale.get("error", {}).get("code") == -32000 and "detached" in stale["error"]["message"], stale.get("error"))
            stale_again = cdp("DOM.getDocument", session_id=cdp_session)
            expect("the detached CDP session no longer exists", stale_again.get("error", {}).get("message") == "session does not exist")
            listed = cdp("Target.getTargets")["result"]["targetInfos"]
            expect("no target remains listed", listed == [])

            # 4. Explicit detach releases the ledger entry without touching the target.
            second = host.call("target.open", {"session": session})["result"]["target"]
            cdp_session = cdp("Target.attachToTarget", {"targetId": second, "flatten": True})["result"]["sessionId"]
            expect("second adapter attached", owners()["owners"]["adapters"]["objects"] == 1)
            cdp("Target.detachFromTarget", {"sessionId": cdp_session})
            detached = owners()
            expect("explicit detach removes the adapter and keeps the target",
                   detached["owners"]["adapters"]["objects"] == 0 and detached["owners"]["targets"]["objects"] == 1)

            # 5. session.close with an attached adapter and a surface: order and lock release.
            cdp_session = cdp("Target.attachToTarget", {"targetId": second, "flatten": True})["result"]["sessionId"]
            surface = host.call("surface.show", {"target": second})["result"]["surface"]
            closed_session = host.call("session.close", {"session": session})["result"]
            expect("session.close detaches adapters, releases surfaces, closes targets",
                   closed_session["closed_targets"] == 1 and closed_session["teardown"]["adapters_detached"] == 1
                   and closed_session["teardown"]["surfaces_released"] == 1
                   and closed_session["teardown"]["released_presentation_bytes"] == 65536
                   and closed_session["teardown"]["owner_reference_extended"] is False, closed_session["teardown"])
            after_session = owners()
            expect("every owner below the profile is zero after session.close",
                   all(after_session["owners"][k]["objects"] == 0 for k in ("sessions", "targets", "surfaces", "adapters")))
            deleted = host.call("profile.delete", {"profile": profile})
            expect("profile lock was released after the targets: delete succeeds", deleted["ok"])
            stale = cdp("DOM.getDocument", session_id=cdp_session)
            expect("adapter attached at session.close is a typed detachment",
                   stale.get("error", {}).get("code") == -32000 and "detached" in stale["error"]["message"])
            hidden = host.call("surface.hide", {"surface": surface})
            expect("the released surface is gone", not hidden["ok"] and hidden["error"]["code"] == "not_found")

            # 6. Failure paths: attaching to a closed target and adapter capacity.
            missing = cdp("Target.attachToTarget", {"targetId": second, "flatten": True})
            expect("attach to a closed target is refused", missing.get("error", {}).get("code") == -32000)
            profile2 = host.call("profile.create", {"persistence": "ephemeral"})["result"]["profile"]
            session2 = host.call("session.open", {"profile": profile2})["result"]["session"]
            third = host.call("target.open", {"session": session2})["result"]["target"]
            sessions = [cdp("Target.attachToTarget", {"targetId": third, "flatten": True})["result"]["sessionId"] for _ in range(16)]
            over = cdp("Target.attachToTarget", {"targetId": third, "flatten": True})
            expect("the seventeenth adapter is refused at the capacity bound",
                   over.get("error", {}).get("message") == "adapter capacity reached" and owners()["owners"]["adapters"]["objects"] == 16)
            closed_third = host.call("target.close", {"target": third})["result"]
            expect("closing the target detaches all sixteen adapters at once",
                   closed_third["teardown"]["adapters_detached"] == 16 and owners()["owners"]["adapters"]["objects"] == 0)
            expect("every stale CDP session fails typed",
                   all(cdp("DOM.getDocument", session_id=s).get("error", {}).get("code") == -32000 for s in sessions))
            final = owners()
            expect("teardown counters are complete",
                   final["teardown"]["targets_closed_total"] == 3 and final["teardown"]["adapters_detached_total"] == 18
                   and final["teardown"]["owner_references_extended_total"] == 0, final["teardown"])
            host.call("session.close", {"session": session2})
            ws.close()
            host.finish()
            expect("host exits cleanly", True)
        finally:
            if host.process.poll() is None:
                host.process.kill()
                host.process.wait()

    passed = sum(1 for check in checks if check["passed"])
    receipt = {
        "schema": "minicon-surf.synthetic-adapter-teardown-receipt/0.0.1",
        "status": "observed",
        "technology": "synthetic-control",
        "technology_version": "0.0.1",
        "host_sha256": hashlib.sha256(Path(args.binary).read_bytes()).hexdigest(),
        "adapter": "loopback CDP edge; each attached CDP session holds a weak handle to its target and every native call it makes is attenuated to that target",
        "semantic": "owner counts and logical bytes from memory.report; teardown reports from target.close and session.close; typed CDP failures after teardown",
        "passed": passed == len(checks),
        "checks_passed": passed,
        "checks_total": len(checks),
        "checks": checks,
        "limitations": [
            "the stored-strong-reference failure path cannot be reached through CDP; it is covered by a unit test that keeps an upgraded reference and observes owner_reference_extended",
            "logical bytes are the host's accounted lower bound, not process memory",
            "one adapter kind (CDP); embedder and plugin adapters do not exist yet",
        ],
    }
    encoded = json.dumps(receipt, indent=2, sort_keys=True) + "\n"
    if args.receipt:
        Path(args.receipt).write_text(encoded, encoding="utf-8")
    print(encoded, end="")
    return 0 if receipt["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
