#!/usr/bin/env python3
"""Dependency-free court for the optional capability attenuation (ME1).

Every request goes through protocol/check_contract.py before it is sent, so
the court cannot pass with a request the paper contract rejects. The court
proves that a capability attenuates and never amplifies: requests without
one are unchanged, a request located only by a surface is a typed refusal,
an owner off the ownership chain is refused, scope, deadline and result
budgets bind, host-wide operations cannot be attenuated, a capability cannot
make a reserved operation work, and the audit ledger is bounded diagnostics
readable through session.inspect.
"""

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "protocol"))
import check_contract  # noqa: E402

OPERATIONS = sorted(check_contract.OPERATIONS)


class Host:
    def __init__(self, binary):
        self.process = subprocess.Popen([binary, "serve", "--stdio"], stdin=subprocess.PIPE,
                                        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        self.next_id = 0

    def call(self, operation, arguments, capability=None, deadline_ms=100):
        self.next_id += 1
        request_id = f"req_cap_court_{self.next_id}"
        request = {"protocol": "minicon-surf.control", "version": "0.0.1", "request_id": request_id,
                   "deadline_ms": deadline_ms, "operation": operation, "arguments": arguments}
        if capability is not None:
            request["capability"] = capability
        check_contract.validate_request(request)
        self.process.stdin.write(json.dumps(request, separators=(",", ":")) + "\n")
        self.process.stdin.flush()
        line = self.process.stdout.readline()
        if not line:
            raise RuntimeError(f"host exited: {self.process.stderr.read()}")
        response = json.loads(line)
        check_contract.validate_response(response)
        assert response["request_id"] == request_id
        return response

    def raw(self, text):
        self.process.stdin.write(text + "\n")
        self.process.stdin.flush()
        return json.loads(self.process.stdout.readline())

    def finish(self):
        self.process.stdin.close()
        return self.process.wait(timeout=5)


def capability(kind, owner, scope, result_bytes=65536, deadline_ms=100, actor="agent.court", reason="court"):
    return {"owner": {"kind": kind, "id": owner}, "scope": scope,
            "budget": {"result_bytes": result_bytes, "deadline_ms": deadline_ms},
            "audit": {"actor": actor, "reason": reason}}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--binary", required=True)
    parser.add_argument("--receipt")
    args = parser.parse_args()
    checks = []

    def expect(name, condition, detail=None):
        checks.append({"check": name, "passed": bool(condition), **({"detail": detail} if detail is not None else {})})

    def refused(response, code, reason=None):
        error = response.get("error") or {}
        return (not response["ok"] and error["code"] == code
                and (reason is None or (error.get("details") or {}).get("reason") == reason))

    host = Host(args.binary)
    try:
        profile = host.call("profile.create", {"persistence": "ephemeral"})["result"]["profile"]
        session = host.call("session.open", {"profile": profile})["result"]["session"]
        target = host.call("target.open", {"session": session})["result"]["target"]
        other_profile = host.call("profile.create", {"persistence": "ephemeral"})["result"]["profile"]
        other_session = host.call("session.open", {"profile": other_profile})["result"]["session"]
        other_target = host.call("target.open", {"session": other_session})["result"]["target"]
        snapshot = {"target": target, "format": "semantic", "max_bytes": 65536, "max_nodes": 16}

        # 1. Compatibility: requests without a capability are unchanged.
        plain = host.call("target.snapshot", snapshot)
        expect("request without capability still succeeds", plain["ok"] and plain["result"]["revision"] == 0)

        # 2. Every owner on the chain is accepted; the result is identical.
        for kind, owner in (("target", target), ("session", session), ("profile", profile)):
            response = host.call("target.snapshot", snapshot, capability(kind, owner, ["target.snapshot"]))
            expect(f"{kind} owner on the chain is allowed", response["ok"] and response["result"] == plain["result"])

        # 3. Surface- or window-located requests are typed refusals.
        surface = host.call("surface.show", {"target": target})["result"]["surface"]
        response = host.call("target.snapshot", snapshot, capability("surface", surface, ["target.snapshot"]))
        expect("surface-located request is permission_denied", refused(response, "permission_denied", "surface_is_not_an_owner"),
               response["error"])
        response = host.call("surface.hide", {"surface": surface}, capability("surface", surface, ["surface.hide"]))
        expect("surface cannot own its own hide", refused(response, "permission_denied", "surface_is_not_an_owner"))
        response = host.call("surface.hide", {"surface": surface}, capability("target", target, ["surface.hide"]))
        expect("the surface's target owns the hide", response["ok"] and response["result"]["state"] == "headless")
        response = host.call("target.snapshot", snapshot, capability("realm", "realm_1", ["target.snapshot"]))
        expect("realm is not an owner", refused(response, "permission_denied", "kind_is_not_an_owner"))

        # 4. Owners off the chain, including foreign and nonexistent ones.
        response = host.call("target.snapshot", snapshot, capability("target", other_target, ["target.snapshot"]))
        expect("another target cannot own this target", refused(response, "permission_denied", "owner_not_on_chain"))
        response = host.call("target.snapshot", snapshot, capability("session", other_session, ["target.snapshot"]))
        expect("another session cannot own this target", refused(response, "permission_denied", "owner_not_on_chain"))
        response = host.call("target.snapshot", snapshot, capability("profile", other_profile, ["target.snapshot"]))
        expect("another profile cannot own this target", refused(response, "permission_denied", "owner_not_on_chain"))
        response = host.call("target.snapshot", snapshot, capability("target", "target_9999", ["target.snapshot"]))
        expect("a nonexistent owner is refused without leaking existence",
               refused(response, "permission_denied", "owner_not_on_chain"))
        response = host.call("session.close", {"session": other_session}, capability("session", session, ["session.close"]))
        expect("a session cannot close another profile's session", refused(response, "permission_denied", "owner_not_on_chain"))
        expect("the other session is still open", host.call("session.inspect", {"session": other_session})["ok"])

        # 5. Scope, deadline and result budgets bind.
        response = host.call("target.snapshot", snapshot, capability("target", target, ["target.wait"]))
        expect("operation outside scope is refused", refused(response, "permission_denied", "operation_outside_scope"))
        response = host.call("target.snapshot", snapshot, capability("target", target, ["target.snapshot"], deadline_ms=50), deadline_ms=100)
        expect("deadline over budget is refused", refused(response, "permission_denied", "deadline_exceeds_budget"))
        response = host.call("target.snapshot", snapshot, capability("target", target, ["target.snapshot"], result_bytes=1024))
        expect("snapshot max_bytes over budget is refused before execution",
               refused(response, "permission_denied", "result_budget_exceeded"))
        response = host.call("target.inspect", {"target": target}, capability("target", target, ["target.inspect"], result_bytes=16))
        expect("a produced result over budget is resource_limit", refused(response, "resource_limit", "result_budget_exceeded"))

        # 6. Host-wide operations cannot be attenuated.
        for operation in ("memory.report", "profile.list", "target.list"):
            response = host.call(operation, {}, capability("profile", profile, [operation]))
            expect(f"{operation} cannot be attenuated", refused(response, "permission_denied", "operation_has_no_owner"))

        # 7. No amplification: a capability grants nothing beyond the plain request.
        response = host.call("target.screenshot", {"target": target}, capability("target", target, OPERATIONS))
        expect("a full-scope capability cannot make a reserved operation work", refused(response, "unsupported_operation"))
        missing = dict(snapshot, target="target_4040")
        response = host.call("target.snapshot", missing, capability("target", "target_4040", ["target.snapshot"]))
        expect("a missing principal fails as not_found exactly like a plain request", refused(response, "not_found"))
        plain_missing = host.call("target.snapshot", missing)
        expect("plain and attenuated missing-principal errors share a code",
               plain_missing["error"]["code"] == response["error"]["code"])

        # 8. Malformed capabilities are invalid_request at the parser.
        bad = {"protocol": "minicon-surf.control", "version": "0.0.1", "request_id": "req_cap_court_bad",
               "deadline_ms": 100, "operation": "target.snapshot", "arguments": snapshot,
               "capability": {"owner": {"kind": "target", "id": target}, "scope": ["target.snapshot"],
                              "budget": {"result_bytes": 65536, "deadline_ms": 100}}}
        response = host.raw(json.dumps(bad, separators=(",", ":")))
        expect("capability without audit is invalid_request", refused(response, "invalid_request"))
        bad["capability"]["audit"] = {"actor": "agent.court", "reason": "court"}
        bad["capability"]["grant"] = "everything"
        response = host.raw(json.dumps(bad, separators=(",", ":")))
        expect("capability with an unknown field is invalid_request", refused(response, "invalid_request"))

        # 9. Audit ledger: bounded diagnostics per session, never authority.
        inspect = host.call("session.inspect", {"session": session})["result"]
        decisions = [entry["decision"] for entry in inspect["capability_audit"]]
        expect("session.inspect lists this session's audit records",
               inspect["session"] == session and "allowed" in decisions and "refused:permission_denied" in decisions,
               {"records": len(decisions)})
        expect("audit records name actor, reason, operation and owner",
               all(set(entry) == {"request_id", "actor", "reason", "operation", "owner", "decision"}
                   for entry in inspect["capability_audit"]))
        other_inspect = host.call("session.inspect", {"session": other_session})["result"]
        expect("audit records are scoped to their session",
               all(entry["operation"] == "session.close" for entry in other_inspect["capability_audit"]))
        for _ in range(70):
            host.call("target.inspect", {"target": target}, capability("target", target, ["target.inspect"]))
        inspect = host.call("session.inspect", {"session": session})["result"]
        expect("audit ledger is bounded", len(inspect["capability_audit"]) <= inspect["audit_limit"] == 64,
               {"records": len(inspect["capability_audit"])})

        # 10. Teardown: after the target closes, its capability is refused like the plain request.
        host.call("target.close", {"target": target})
        response = host.call("target.snapshot", snapshot, capability("target", target, ["target.snapshot"]))
        expect("closed target: attenuated request is not_found", refused(response, "not_found"))
        report = host.call("memory.report", {})["result"]
        expect("memory owners are unchanged by the ledger", report["owners"]["targets"]["objects"] == 1)
        host.call("session.close", {"session": session})
        host.call("session.close", {"session": other_session})
        expect("host exits cleanly", host.finish() == 0)
    finally:
        if host.process.poll() is None:
            host.process.kill()
            host.process.wait()

    passed = sum(1 for check in checks if check["passed"])
    receipt = {
        "schema": "minicon-surf.synthetic-capability-receipt/0.0.1",
        "status": "observed",
        "technology": "synthetic-control",
        "technology_version": "0.0.1",
        "host_sha256": hashlib.sha256(Path(args.binary).read_bytes()).hexdigest(),
        "protocol": "minicon-surf.control 0.0.1 with the optional capability field",
        "semantic": "every request is validated by protocol/check_contract.py before it is sent; a capability attenuates and never amplifies",
        "passed": passed == len(checks),
        "checks_passed": passed,
        "checks_total": len(checks),
        "checks": checks,
        "limitations": [
            "synthetic host with a fixed two-node target; no engine, HTML or CDP path carries a capability yet",
            "the audit ledger is in-memory, bounded to 64 records per host and readable only through session.inspect",
            "deadline and result budgets are per request; no cumulative operation budget exists",
        ],
    }
    encoded = json.dumps(receipt, indent=2, sort_keys=True) + "\n"
    if args.receipt:
        Path(args.receipt).write_text(encoded, encoding="utf-8")
    print(encoded, end="")
    return 0 if receipt["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
