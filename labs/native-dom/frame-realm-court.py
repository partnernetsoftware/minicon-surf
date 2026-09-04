#!/usr/bin/env python3
"""Frame and realm identity court for the native bounded route.

Every native-dom target exposes exactly one main frame and one main-world
realm. The court checks the control 0.0.1 frame/realm rules on real documents:
bounded enumeration, host-wide monotonic ids, foreign/retired/unknown ids
refused alike, a real link click on a hermetic fixture and over the bounded
network as a same-frame navigation (frame kept, generation +1, realm
retired and replaced, revision monotonic, old node references stale), failed
navigations that leave the target untouched, owners at zero after close, and
the exact losses (no child frames, no capability attenuation on this host,
no CDP projection). Native requests are validated by
protocol/check_contract.py before they are sent.
"""

import argparse
import hashlib
import importlib.util
import json
import sys
import tempfile
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "protocol"))
import check_contract  # noqa: E402


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


def snapshot_arguments(target, **extra):
    return {"target": target, "format": "semantic", "max_bytes": 65536, "max_nodes": 32, **extra}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--binary", required=True)
    parser.add_argument("--receipt")
    args = parser.parse_args()
    network = RETENTION.load_network_module()
    server = network.Server(("127.0.0.1", 0), network.Handler)
    origin = f"http://127.0.0.1:{server.server_address[1]}"
    threading.Thread(target=server.serve_forever, daemon=True).start()
    checks = []

    def expect(name, condition, detail=None):
        checks.append({"check": name, "passed": bool(condition), **({"detail": detail} if detail is not None else {})})

    def refused(response, code, kind=None):
        error = response.get("error") or {}
        return (not response["ok"] and error["code"] == code
                and (kind is None or (error.get("scope") or {}).get("kind") == kind))

    footprints = {}
    try:
        for allocator in ("system", "arena"):
            with tempfile.TemporaryDirectory(prefix="minicon-surf-frame-court-") as directory:
                host = RETENTION.Host(args.binary, directory, origin, allocator)
                raw = host.call

                def call(operation, arguments, deadline_ms=30000):
                    request = {"protocol": "minicon-surf.control", "version": "0.0.1", "request_id": "req_ret_0",
                               "deadline_ms": deadline_ms, "operation": operation, "arguments": arguments}
                    check_contract.validate_request(request)
                    host.counter += 1
                    request["request_id"] = f"req_ret_{host.counter}"
                    host.process.stdin.write(json.dumps(request) + "\n")
                    host.process.stdin.flush()
                    line = host.process.stdout.readline()
                    if not line:
                        raise RuntimeError(f"host exited during {operation}")
                    response = json.loads(line)
                    check_contract.validate_response(response)
                    return response

                def ok(operation, arguments, deadline_ms=30000):
                    response = call(operation, arguments, deadline_ms)
                    if not response["ok"]:
                        raise RuntimeError(f"{operation} failed: {response['error']}")
                    return response["result"]

                try:
                    profile = ok("profile.create", {"persistence": "ephemeral"})["profile"]
                    session = ok("session.open", {"profile": profile})["session"]
                    empty = RETENTION.sample_process(host.process.pid)["physical_footprint_bytes"]
                    tag = f"[{allocator}] "

                    # 1. Initial identity on two fixture targets.
                    opened = ok("target.open", {"session": session, "fixture": "semantic-nav.html"})
                    a = opened["target"]
                    b = ok("target.open", {"session": session, "fixture": "semantic-static.html"})["target"]
                    live = RETENTION.sample_process(host.process.pid)["physical_footprint_bytes"]
                    inspect_a = ok("target.inspect", {"target": a})
                    inspect_b = ok("target.inspect", {"target": b})
                    expect(tag + "open names frame, generation 1 and realm", opened["generation"] == 1 and opened["frame"].startswith("frame_") and opened["realm"].startswith("realm_"))
                    expect(tag + "inspect enumerates exactly one main frame and one main-world realm with frame_limit 1",
                           len(inspect_a["frames"]) == 1 and inspect_a["frames"][0]["parent"] is None and inspect_a["frame_limit"] == 1
                           and inspect_a["realms"] == [{"realm": inspect_a["frames"][0]["realm"], "frame": inspect_a["frames"][0]["frame"], "world": "main"}])
                    frame_a, realm_a = inspect_a["frames"][0]["frame"], inspect_a["frames"][0]["realm"]
                    frame_b, realm_b = inspect_b["frames"][0]["frame"], inspect_b["frames"][0]["realm"]
                    expect(tag + "frame and realm ids are host-wide and disjoint across targets", frame_a != frame_b and realm_a != realm_b)
                    snap = ok("target.snapshot", snapshot_arguments(a))
                    expect(tag + "a snapshot names its frame, realm and generation and carries the target revision",
                           snap["frame"] == frame_a and snap["realm"] == realm_a and snap["generation"] == 1 and snap["revision"] == inspect_a["revision"])
                    named = ok("target.snapshot", snapshot_arguments(a, frame=frame_a, realm=realm_a))
                    expect(tag + "naming the live frame and realm is accepted", named["frame"] == frame_a and named["realm"] == realm_a)
                    foreign = call("target.snapshot", snapshot_arguments(a, frame=frame_b))
                    unknown = call("target.snapshot", snapshot_arguments(a, frame="frame_9999"))
                    expect(tag + "another target's frame and an unknown frame are the same not_found",
                           refused(foreign, "not_found", "frame") and refused(unknown, "not_found", "frame") and foreign["error"]["message"] == unknown["error"]["message"])
                    wrong_realm = call("target.snapshot", snapshot_arguments(a, realm=realm_b))
                    expect(tag + "another target's realm is not_found with realm scope", refused(wrong_realm, "not_found", "realm"))

                    # 2. Failed navigation first: the https link leaves the court and the policy refuses it.
                    links = [n for n in snap["nodes"] if n["role"] == "link"]
                    outside = next(n for n in links if n["name"] == "Example result")
                    failed = call("target.act", {"target": a, "reference": outside["reference"], "action": {"kind": "click"}})
                    expect(tag + "a fixture target cannot follow a link out of the court",
                           refused(failed, "unsupported_capability") and failed["error"]["details"].get("navigation") == "failed", failed.get("error"))
                    after_failed = ok("target.inspect", {"target": a})
                    expect(tag + "a failed navigation leaves frame, realm, generation and revision untouched",
                           after_failed["frames"][0] == inspect_a["frames"][0] and after_failed["revision"] == inspect_a["revision"])
                    still = call("target.snapshot", snapshot_arguments(a, realm=realm_a))
                    expect(tag + "the old realm is still live after the failed navigation", still["ok"] and still["result"]["generation"] == 1)
                    stay = next(n for n in still["result"]["nodes"] if n["role"] == "button")
                    stayed = ok("target.act", {"target": a, "reference": stay["reference"], "action": {"kind": "click"}})
                    expect(tag + "the old document still acts and advances the revision without navigating",
                           "navigated" not in stayed and stayed["revision"] == inspect_a["revision"] + 1)

                    # 3. Successful navigation through the in-court link.
                    fresh = ok("target.snapshot", snapshot_arguments(a))
                    inside = next(n for n in fresh["nodes"] if n["role"] == "link" and n["name"] == "Court result")
                    revision_before = fresh["revision"]
                    navigated = ok("target.act", {"target": a, "reference": inside["reference"], "action": {"kind": "click"}})
                    expect(tag + "a link click inside the court is a same-frame navigation",
                           navigated.get("navigated") is True and navigated["frame"] == frame_a and navigated["generation"] == 2
                           and navigated["retired_realm"] == realm_a and navigated["realm"] != realm_a and navigated["fixture"] == "semantic-static.html", navigated)
                    expect(tag + "the target revision stays monotonic across the navigation", navigated["revision"] == revision_before + 1)
                    after = ok("target.inspect", {"target": a})
                    expect(tag + "after navigation: same frame, generation 2, new realm, one frame still",
                           after["frames"] == [{"frame": frame_a, "parent": None, "generation": 2, "realm": navigated["realm"]}] and after["fixture"] == "semantic-static.html")
                    stale = call("target.act", {"target": a, "reference": stay["reference"], "action": {"kind": "click"}})
                    expect(tag + "a node reference from before the navigation is stale_revision", refused(stale, "stale_revision"))
                    retired = call("target.snapshot", snapshot_arguments(a, realm=realm_a))
                    expect(tag + "the retired realm is not_found with realm scope and a typed reason",
                           refused(retired, "not_found", "realm") and retired["error"]["details"]["reason"] == "realm_not_live_in_target")
                    new_snapshot = ok("target.snapshot", snapshot_arguments(a, frame=frame_a, realm=navigated["realm"]))
                    expect(tag + "the new document is observable through the surviving frame at generation 2",
                           new_snapshot["generation"] == 2 and new_snapshot["nodes"][0]["name"] == "Memory and Agent Court" and new_snapshot["revision"] == navigated["revision"])
                    waited = ok("target.wait", {"target": a, "condition": {"kind": "revision_at_least", "revision": navigated["revision"]}}, 2000)
                    expect(tag + "waits use the absolute revision after navigation", waited["matched"] is True)

                    # 4. Over the bounded network: same-origin success, then three failures that roll back.
                    c = ok("target.open", {"session": session, "url": f"{origin}/nav.html"})["target"]
                    inspect_c = ok("target.inspect", {"target": c})
                    frame_c, realm_c = inspect_c["frames"][0]["frame"], inspect_c["frames"][0]["realm"]
                    nav = ok("target.snapshot", snapshot_arguments(c))
                    by_name = {n["name"]: n for n in nav["nodes"] if n["role"] == "link"}
                    # Court amendment, recorded when the form slice was
                    # implemented: these two assertions still counted fetches
                    # cumulatively across a target's life, which the
                    # per-document budget ruling replaced. This court had not
                    # been rerun since that ruling. The limits and the fetch
                    # itself are unchanged; only what the count is scoped to is
                    # now explicit, and the cumulative fact moved to the
                    # lifetime diagnostic that never gates.
                    def lifetime_of(target):
                        rows = ok("memory.report", {})["owners"]["targets"].get("lifetime", [])
                        row = next((r for r in rows if r["target"] == target), None)
                        return row["network"]["fetches_total"] if row else None

                    lifetime_before = lifetime_of(c)
                    moved = ok("target.act", {"target": c, "reference": by_name["About this court"]["reference"], "action": {"kind": "click"}})
                    expect(tag + "a same-origin link navigates over the bounded network",
                           moved.get("navigated") is True and moved["url"].endswith("/about.html") and moved["generation"] == 2 and moved["frame"] == frame_c
                           and moved["retired_realm"] == realm_c and moved["network"]["fetches"] == 1, moved)
                    expect(tag + "the committed document owns a fresh budget and the lifetime total still counts the fetch",
                           lifetime_of(c) == lifetime_before + 1,
                           {"before": lifetime_before, "after": lifetime_of(c)})
                    about = ok("target.snapshot", snapshot_arguments(c))
                    back = next(n for n in about["nodes"] if n["role"] == "link")
                    returned = ok("target.act", {"target": c, "reference": back["reference"], "action": {"kind": "click"}})
                    expect(tag + "navigating back mints a third realm on the same frame",
                           returned["generation"] == 3 and returned["frame"] == frame_c and returned["realm"] not in (realm_c, moved["realm"]) and returned["url"].endswith("/nav.html"))
                    state = ok("target.inspect", {"target": c})
                    nav = ok("target.snapshot", snapshot_arguments(c))
                    by_name = {n["name"]: n for n in nav["nodes"] if n["role"] == "link"}
                    outcomes = {}
                    for name, code in (("Leave the origin", "unsupported_capability"), ("Private address", "permission_denied"),
                                       ("Missing page", "not_found"), ("Not a document", "unsupported_capability")):
                        response = call("target.act", {"target": c, "reference": by_name[name]["reference"], "action": {"kind": "click"}})
                        outcomes[name] = response.get("error", {}).get("code")
                        expect(tag + f"'{name}' navigation fails typed as {code} and leaves the target untouched",
                               refused(response, code) and response["error"]["details"].get("navigation") == "failed"
                               and ok("target.inspect", {"target": c})["frames"] == state["frames"], response.get("error"))
                    expect(tag + "failed navigations are charged as denied attempts, never as documents",
                           ok("target.inspect", {"target": c})["revision"] == state["revision"]
                           and ok("target.snapshot", snapshot_arguments(c, realm=state["frames"][0]["realm"]))["generation"] == 3)

                    # 5. Losses recorded by construction: no capability on this host, no child frames.
                    attenuated = {"protocol": "minicon-surf.control", "version": "0.0.1", "request_id": "req_ret_cap",
                                  "deadline_ms": 1000, "operation": "target.snapshot", "arguments": snapshot_arguments(a),
                                  "capability": {"owner": {"kind": "target", "id": a}, "scope": ["target.snapshot"],
                                                 "budget": {"result_bytes": 65536, "deadline_ms": 1000},
                                                 "audit": {"actor": "agent.court", "reason": "loss"}}}
                    check_contract.validate_request(attenuated)
                    host.process.stdin.write(json.dumps(attenuated) + "\n")
                    host.process.stdin.flush()
                    answer = json.loads(host.process.stdout.readline())
                    expect(tag + "capability attenuation is not implemented here and fails closed as invalid_request",
                           refused(answer, "invalid_request"))

                    # 6. Owners and teardown.
                    report = ok("memory.report", {})["owners"]
                    expect(tag + "owners count one frame and one realm per live target, with retirements tallied",
                           report["frames"]["objects"] == 3 and report["realms"]["objects"] == 3 and report["realms"]["retired_total"] == 3
                           and report["realms"]["navigations_total"] == 3)
                    for target in (a, b, c):
                        ok("target.close", {"target": target})
                    report = ok("memory.report", {})["owners"]
                    expect(tag + "closing every target leaves zero frames, realms and script realms",
                           report["frames"]["objects"] == 0 and report["realms"]["objects"] == 0 and report["script_realms"]["objects"] == 0)
                    gone = call("target.snapshot", snapshot_arguments(a, frame=frame_a))
                    expect(tag + "a closed target's frame is not_found at the target", refused(gone, "not_found", "target"))
                    post_close = RETENTION.sample_process(host.process.pid)["physical_footprint_bytes"]
                    footprints[allocator] = {"empty": empty, "two_fixture_targets_live": live, "post_close": post_close}
                    ok("session.close", {"session": session})
                    expect(tag + "host exits cleanly", host.finish() == 0)
                finally:
                    if host.process.poll() is None:
                        host.process.kill()
                        host.process.wait()
    finally:
        server.shutdown()
        server.server_close()

    passed = sum(1 for check in checks if check["passed"])
    receipt = {
        "schema": "minicon-surf.native-dom-frame-realm-receipt/0.0.1",
        "status": "observed",
        "technology": "native-dom",
        "technology_version": "0.0.2",
        "host_sha256": hashlib.sha256(Path(args.binary).read_bytes()).hexdigest(),
        "semantic": "one main frame and one main-world realm per native target; a link click is a same-frame navigation built completely before the swap; native requests validated by protocol/check_contract.py; run under the default allocator and the opt-in arena",
        "passed": passed == len(checks),
        "checks_passed": passed,
        "checks_total": len(checks),
        "checks": checks,
        "footprint_bytes": footprints,
        "losses": [
            "no child frames: every native-dom target is one main frame; frame_limit is 1",
            "no capability attenuation on this host: a request carrying the field is refused invalid_request (fail-closed, no downgrade)",
            "no CDP projection of frames or realms on this host",
            "navigation is a link click only; no target.navigate, no history, no form submission",
            "fixture targets may follow links only to court fixture files; url targets follow links under the same policy, budget, redirect, size and deadline limits as target.open",
        ],
    }
    encoded = json.dumps(receipt, indent=2, sort_keys=True) + "\n"
    if args.receipt:
        Path(args.receipt).write_text(encoded, encoding="utf-8")
    print(json.dumps({"passed": receipt["passed"], "checks_passed": passed, "checks_total": len(checks), "footprint_bytes": footprints}, indent=1))
    for check in checks:
        if not check["passed"]:
            print("FAIL", check)
    return 0 if receipt["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
