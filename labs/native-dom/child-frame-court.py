#!/usr/bin/env python3
"""The frozen court for bounded child frames on the native route.

Frozen before any host change, as `child-frame-design-0.0.1.md` §14 requires.
Until child frames exist it fails, which is what freezing the criteria first
means.

Strictly headless: no surface binary, no window, no AppKit, and it refuses to
run with the visible-court variable set. Two hermetic loopback origins, so
"cross-origin" is a real second origin rather than a spelling. Both
allocators, a fresh host per run.

Groups:
 1 enumeration  main first, parents correct, frame_limit 8, ids opaque
 2 narrowing    a snapshot narrowed to a child sees the child and nothing else
 3 refusals     foreign, ended and unknown ids are one and the same not_found
 4 lifetime     parent navigation ends the children and says which; close too
 5 policy       cross-origin, srcdoc, about:, malformed and the ninth child
 6 budget       children are charged to the parent document's fetches and bytes
 7 memory       M1 to M6 of the design, pre-registered before measurement
 8 cdp          the children project flat, and the losses are asserted as losses
 9 secrecy      no child text, URL or path in the ledger, the log or the receipt

First amendment (mechanism and one criterion, recorded before implementation):
running this court against the pre-implementation host to confirm that it
fails showed two things that are not about child frames. A probe target was
left open and its bytes were still counted by the memory group, which is
fixed. And M5's absolute retention cap of 1 MiB was already exceeded, 3.1 MB
over 64 open-and-close cycles, on a host that has no child frames at all: the
criterion as frozen measured the page-granular allocator retention the
navigation increment already recorded, not anything this design does. It is
now the differential the navigation increment settled on, the children's arm
against the identical childless arm, with the same 1 MiB bound and both
absolute numbers reported. The cap's number did not move; what it is measured
against did, and the reason is here rather than erased.

Held out pending the two rulings the design puts to the root (§12): a group
that acts inside a child, which needs a node reference that can name a frame,
and the assertion that a child's projected CDP url is its own. Both are
additions to this file, not rewrites of it.
"""

import argparse
import hashlib
import importlib.util
import json
import os
import sys
import tempfile
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "protocol"))
import check_contract  # noqa: E402

VISIBLE_ENV = "MINICON_SURF_ALLOW_VISIBLE_COURT"

# Pre-registered before any measurement (design §13). A failure narrows the
# claim or the change is optimized; the number never moves afterwards.
CAPS = {
    "child_owner_bytes": 262144,        # M1, per child
    "seven_children_owner_bytes": 1835008,  # M2, the bound, no super-linear term
    "navigation_return_bytes": 65536,   # M3
    "close_retention_bytes": 1048576,   # M5
    "frame_limit": 8,
    "cycles": 64,
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


def page(title, bodies):
    return ("<!doctype html><html><body><main><h1>" + title + "</h1>"
            + "".join(bodies) + "</main></body></html>").encode()


class FrameHandler:
    """The fixture pages. Built as a subclass of the network journey's handler
    so the byte, redirect and timeout behaviour stays the one already proven."""

    @staticmethod
    def build(network, other_origin_holder):
        class Handler(network.Handler):
            def do_GET(self):
                path, _, _query = self.path.partition("?")
                network.Handler.hits.append(path)
                other = other_origin_holder[0]
                pages = {
                    # One same-origin child, and the identical page without it.
                    "/parent-one.html": page("Parent one", ['<iframe src="/child-a.html"></iframe>']),
                    "/parent-none.html": page("Parent one", []),
                    # The bound, and one over it.
                    "/parent-seven.html": page("Parent seven", [f'<iframe src="/child-{n}.html"></iframe>' for n in range(7)]),
                    "/parent-nine.html": page("Parent nine", [f'<iframe src="/child-{n}.html"></iframe>' for n in range(9)]),
                    # Every refused shape, in one document that still commits.
                    "/parent-policy.html": page("Parent policy", [
                        f'<iframe src="{other}/child-a.html"></iframe>',
                        '<iframe srcdoc="<h1>Inline</h1>"></iframe>',
                        '<iframe src="about:blank"></iframe>',
                        '<iframe src="http://[::bad"></iframe>',
                        "<iframe></iframe>",
                        '<iframe src="/child-a.html"></iframe>',
                    ]),
                    # A child that itself embeds: depth stops at one.
                    "/parent-nested.html": page("Parent nested", ['<iframe src="/child-nested.html"></iframe>']),
                    "/child-nested.html": page("Child nested", ['<iframe src="/child-a.html"></iframe>']),
                    "/child-a.html": page("Child A", ['<p id="ca">embedded alpha</p>']),
                    "/landed.html": page("Landed", ['<p id="lp">landed</p>']),
                }
                for n in range(9):
                    pages[f"/child-{n}.html"] = page(f"Child {n}", [f'<p>embedded {n}</p>'])
                if path in pages:
                    return self.reply(200, pages[path])
                return super().do_GET()
        return Handler


def frames_of(inspect):
    return inspect.get("frames") or []


def at(frames, index, field):
    """A frame's field, or a name that cannot exist, so a host without child
    frames fails these criteria rather than crashing the court."""
    if len(frames) > index:
        return frames[index][field]
    if field == "generation":
        return 0
    return "frame_absent" if field == "frame" else "realm_absent"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--binary", required=True)
    parser.add_argument("--receipt", required=True)
    args = parser.parse_args()
    if os.environ.get(VISIBLE_ENV):
        print(json.dumps({"passed": False, "reason": "the visible-court variable is set"}))
        return 1

    network = RETENTION.load_network_module()
    other_holder = [""]
    handler = FrameHandler.build(network, other_holder)
    server = network.Server(("127.0.0.1", 0), handler)
    origin = f"http://127.0.0.1:{server.server_address[1]}"
    threading.Thread(target=server.serve_forever, daemon=True).start()
    # A second loopback port is a genuinely different origin.
    other = network.Server(("127.0.0.1", 0), handler)
    other_origin = f"http://127.0.0.1:{other.server_address[1]}"
    other_holder[0] = other_origin
    threading.Thread(target=other.serve_forever, daemon=True).start()

    checks = []

    def expect(name, condition, detail=None):
        checks.append({"check": name, "passed": bool(condition),
                       **({"detail": detail} if detail is not None else {})})

    def refused(response, code, kind=None, reason=None):
        error = response.get("error") or {}
        if response.get("ok") or error.get("code") != code:
            return False
        if kind is not None and (error.get("scope") or {}).get("kind") != kind:
            return False
        return reason is None or (error.get("details") or {}).get("reason") == reason

    responses = []
    try:
        for allocator in ("system", "arena"):
            with tempfile.TemporaryDirectory(prefix="minicon-surf-child-court-") as directory:
                host = RETENTION.Host(args.binary, directory, origin, allocator)
                tag = f"[{allocator}] "

                def call(operation, arguments, version="0.0.2", deadline_ms=30000):
                    request = {"protocol": "minicon-surf.control", "version": version,
                               "request_id": "req_child_0", "deadline_ms": deadline_ms,
                               "operation": operation, "arguments": arguments}
                    check_contract.validate_request(request)
                    host.counter += 1
                    request["request_id"] = f"req_child_{host.counter}"
                    host.process.stdin.write(json.dumps(request) + "\n")
                    host.process.stdin.flush()
                    line = host.process.stdout.readline()
                    if not line:
                        raise RuntimeError(f"host exited during {operation}")
                    response = json.loads(line)
                    check_contract.validate_response(response)
                    responses.append(json.dumps(response))
                    return response

                def ok(operation, arguments, **kw):
                    response = call(operation, arguments, **kw)
                    if not response["ok"]:
                        raise RuntimeError(f"{operation} failed: {response['error']}")
                    return response["result"]

                def open_page(session, path):
                    return ok("target.open", {"session": session, "url": f"{origin}{path}"})["target"]

                def snap(target, **extra):
                    return call("target.snapshot", {"target": target, "format": "semantic",
                                                    "max_bytes": 65536, "max_nodes": 64, **extra})

                def owners():
                    report = ok("memory.report", {})
                    return (report["owners"]["script_realms"]["malloc_bytes"]
                            + report["owners"]["targets"]["fixture_bytes"])

                try:
                    profile = ok("profile.create", {"persistence": "ephemeral"})["profile"]
                    session = ok("session.open", {"profile": profile})["session"]
                    empty_owners = owners()
                    empty_footprint = RETENTION.sample_process(host.process.pid)["physical_footprint_bytes"]

                    # 1. Enumeration.
                    one = open_page(session, "/parent-one.html")
                    inspect = ok("target.inspect", {"target": one})
                    frames = frames_of(inspect)
                    ids = [f["frame"] for f in frames]
                    expect(tag + "one iframe enumerates a main frame and one child, main first",
                           len(frames) == 2 and frames[0]["parent"] is None
                           and frames[1]["parent"] == frames[0]["frame"]
                           and len(set(ids)) == 2 and inspect["frame_limit"] == CAPS["frame_limit"],
                           {"count": len(frames), "frame_limit": inspect.get("frame_limit")})
                    expect(tag + "every frame carries its own generation and realm",
                           all(f.get("generation") == 1 for f in frames)
                           and len({f["realm"] for f in frames}) == len(frames)
                           and {r["frame"] for r in inspect["realms"]} == set(ids),
                           {"generations": [f.get("generation") for f in frames]})
                    none = open_page(session, "/parent-none.html")
                    expect(tag + "the same page without the iframe still enumerates one frame",
                           len(frames_of(ok("target.inspect", {"target": none}))) == 1)
                    seven = open_page(session, "/parent-seven.html")
                    expect(tag + "seven children reach the bound and no more",
                           len(frames_of(ok("target.inspect", {"target": seven}))) == CAPS["frame_limit"])

                    # 2. Narrowing.
                    child = at(frames, 1, "frame")
                    child_realm = at(frames, 1, "realm")
                    main_frame, main_realm = at(frames, 0, "frame"), at(frames, 0, "realm")
                    embedded = snap(one, frame=child, realm=child_realm)
                    parent_snap = snap(one, frame=main_frame)
                    embedded_names = [n.get("name") for n in embedded["result"]["nodes"]] if embedded["ok"] else []
                    parent_names = [n.get("name") for n in parent_snap["result"]["nodes"]] if parent_snap["ok"] else []
                    expect(tag + "a snapshot narrowed to the child observes the child's document",
                           embedded.get("ok")
                           and embedded["result"]["frame"] == child
                           and embedded["result"]["realm"] == child_realm
                           and embedded["result"]["generation"] == 1
                           and any("Child A" in (n or "") for n in embedded_names),
                           {"frame": embedded.get("result", {}).get("frame"), "names": len(embedded_names)})
                    expect(tag + "the main frame's snapshot does not contain the child's nodes",
                           parent_snap.get("ok")
                           and not any("Child A" in (n or "") for n in parent_names)
                           and any("Parent one" in (n or "") for n in parent_names),
                           {"names": len(parent_names)})
                    expect(tag + "a realm belonging to the other frame is not_found on either side",
                           refused(snap(one, frame=main_frame, realm=child_realm), "not_found", "realm")
                           and refused(snap(one, frame=child, realm=main_realm), "not_found", "realm"))
                    nested = open_page(session, "/parent-nested.html")
                    expect(tag + "a child's own iframe is not built: depth stops at one",
                           len(frames_of(ok("target.inspect", {"target": nested}))) == 2)
                    ok("target.close", {"target": nested})

                    # 2b. The hazard §15 records: a child's node ids are not the
                    # main frame's, and a child's reference never acts here.
                    child_nodes = [n["reference"]["node"] for n in embedded["result"]["nodes"]] if embedded.get("ok") else []
                    main_nodes = [n["reference"]["node"] for n in parent_snap["result"]["nodes"]] if parent_snap.get("ok") else []
                    expect(tag + "a child's node ids are disjoint from the main frame's in one revision",
                           child_nodes and main_nodes and not (set(child_nodes) & set(main_nodes)),
                           {"child": len(child_nodes), "main": len(main_nodes),
                            "shared": len(set(child_nodes) & set(main_nodes))})
                    if child_nodes:
                        borrowed = embedded["result"]["nodes"][0]["reference"]
                        answer = call("target.act", {"target": one, "reference": borrowed,
                                                     "action": {"kind": "click"}})
                        expect(tag + "a reference from a child is refused rather than acting on the main frame",
                               refused(answer, "unsupported_capability", None,
                                       "action_in_child_frame_unsupported"),
                               {"error": answer.get("error")})
                    else:
                        expect(tag + "a reference from a child is refused rather than acting on the main frame",
                               False, {"reason": "the child snapshot produced no nodes"})

                    # 3. Refusals, all one refusal.
                    foreign = at(frames_of(ok("target.inspect", {"target": seven})), 1, "frame")
                    expect(tag + "a frame from another target is not_found in this one",
                           refused(snap(one, frame=foreign), "not_found", "frame", "frame_not_live_in_target"))
                    expect(tag + "a frame that never existed is the same not_found",
                           refused(snap(one, frame="frame_999999"), "not_found", "frame", "frame_not_live_in_target"))

                    # 4. Lifetime.
                    before = ok("target.inspect", {"target": one})
                    navigated = ok("target.navigate", {"target": one, "url": f"{origin}/landed.html"})
                    after = ok("target.inspect", {"target": one})
                    expect(tag + "parent navigation ends the children and names them",
                           navigated.get("ended_frames") == [child]
                           and navigated["frame"] == main_frame
                           and navigated["generation"] == at(frames_of(before), 0, "generation") + 1
                           and navigated["realm"] != main_realm
                           and len(frames_of(after)) == 1,
                           {"ended": navigated.get("ended_frames"), "frames": len(frames_of(after))})
                    expect(tag + "an ended frame and its realm are afterwards the same not_found",
                           refused(snap(one, frame=child), "not_found", "frame", "frame_not_live_in_target")
                           and refused(snap(one, realm=child_realm), "not_found", "realm"))
                    retired_before = ok("memory.report", {})["owners"]["realms"]["retired_total"]
                    ok("target.close", {"target": seven})
                    retired_after = ok("memory.report", {})["owners"]["realms"]["retired_total"]
                    expect(tag + "closing a target retires every one of its realms exactly once",
                           retired_after - retired_before == CAPS["frame_limit"],
                           {"retired": retired_after - retired_before})

                    # 5. Policy, in one document that still commits.
                    policy_target = open_page(session, "/parent-policy.html")
                    policy_inspect = ok("target.inspect", {"target": policy_target})
                    expect(tag + "cross-origin, srcdoc, about:, malformed and absent srcs are all refused",
                           len(frames_of(policy_inspect)) == 2,
                           {"frames": len(frames_of(policy_inspect))})
                    expect(tag + "the parent still committed and the refusals were charged as denied",
                           policy_inspect["network"]["denied"] >= 1,
                           {"denied": policy_inspect["network"]["denied"]})
                    nine = open_page(session, "/parent-nine.html")
                    nine_inspect = ok("target.inspect", {"target": nine})
                    expect(tag + "the ninth child is over the bound, skipped, and costs no fetch",
                           len(frames_of(nine_inspect)) == CAPS["frame_limit"]
                           and nine_inspect["network"]["fetches"] <= CAPS["frame_limit"],
                           {"frames": len(frames_of(nine_inspect)),
                            "fetches": nine_inspect["network"]["fetches"]})

                    # 6. Budget: the parent document pays for its children,
                    # out of the limits that already exist.
                    limits = ok("memory.report", {})["owners"]["network"]["limits"]
                    seven_target_b = open_page(session, "/parent-seven.html")
                    seven_network = ok("target.inspect", {"target": seven_target_b})["network"]
                    ok("target.close", {"target": seven_target_b})
                    expect(tag + "a parent's fetches include one per child and no new budget appears",
                           seven_network["fetches"] == CAPS["frame_limit"]
                           and limits["fetches_per_document"] == 32
                           and limits["bytes_per_document"] == 4 * 1024 * 1024,
                           {"fetches": seven_network["fetches"],
                            "per_document": [limits["fetches_per_document"], limits["bytes_per_document"]]})

                    # 7. Memory, pre-registered.
                    ok("target.close", {"target": nine})
                    ok("target.close", {"target": policy_target})
                    ok("target.close", {"target": one})
                    ok("target.close", {"target": none})
                    base_target = open_page(session, "/parent-none.html")
                    base_owners = owners()
                    ok("target.close", {"target": base_target})
                    one_target = open_page(session, "/parent-one.html")
                    m1 = owners() - base_owners
                    ok("target.close", {"target": one_target})
                    expect(tag + f"M1: one child costs at most {CAPS['child_owner_bytes']} live owner bytes",
                           0 < m1 <= CAPS["child_owner_bytes"], {"owner_bytes": m1})
                    seven_target = open_page(session, "/parent-seven.html")
                    m2 = owners() - base_owners
                    ok("target.close", {"target": seven_target})
                    expect(tag + f"M2: seven children cost at most {CAPS['seven_children_owner_bytes']} live owner bytes",
                           0 < m2 <= CAPS["seven_children_owner_bytes"], {"owner_bytes": m2})

                    cycle_target = open_page(session, "/parent-one.html")
                    retired_start = ok("memory.report", {})["owners"]["realms"]["retired_total"]
                    marks = []
                    for cycle in range(CAPS["cycles"]):
                        ok("target.navigate", {"target": cycle_target, "url": f"{origin}/parent-one.html"})
                        if cycle % 8 == 7:
                            marks.append(RETENTION.sample_process(host.process.pid)["physical_footprint_bytes"])
                    retired_end = ok("memory.report", {})["owners"]["realms"]["retired_total"]
                    m3 = owners() - base_owners
                    expect(tag + "M3: repeated parent navigation returns to the one-child level",
                           abs(m3 - m1) <= CAPS["navigation_return_bytes"], {"owner_bytes": m3, "one_child": m1})
                    expect(tag + "M3: every navigation retires exactly the main realm and its children",
                           retired_end - retired_start == CAPS["cycles"] * 2,
                           {"retired": retired_end - retired_start})
                    first_half = marks[len(marks) // 2 - 1] - marks[0] if len(marks) >= 4 else 0
                    second_half = marks[-1] - marks[len(marks) // 2 - 1] if len(marks) >= 4 else 0
                    expect(tag + "M4: the footprint's growth does not accelerate over the cycles",
                           second_half <= max(first_half, 0) + CAPS["navigation_return_bytes"],
                           {"first_half": first_half, "second_half": second_half})
                    ok("target.close", {"target": cycle_target})

                    def churn(path):
                        start = RETENTION.sample_process(host.process.pid)["physical_footprint_bytes"]
                        for _ in range(CAPS["cycles"]):
                            closing = open_page(session, path)
                            ok("target.close", {"target": closing})
                        return (owners(),
                                RETENTION.sample_process(host.process.pid)["physical_footprint_bytes"] - start)
                    control_owners, control_retention = churn("/parent-none.html")
                    child_owners, child_retention = churn("/parent-seven.html")
                    expect(tag + "M5: open and close with the bound of children returns every owner",
                           child_owners == empty_owners and control_owners == empty_owners,
                           {"owner_bytes": child_owners - empty_owners,
                            "control_owner_bytes": control_owners - empty_owners})
                    expect(tag + f"M5: the children's arm retains at most {CAPS['close_retention_bytes']} more than the identical childless arm",
                           child_retention - control_retention <= CAPS["close_retention_bytes"],
                           {"excess_bytes": child_retention - control_retention,
                            "children_bytes": child_retention, "control_bytes": control_retention,
                            "allocator": allocator})

                    # 9. Secrecy: nothing about a child's document escapes.
                    audit = ok("session.inspect", {"session": session})
                    blob = json.dumps(audit)
                    expect(tag + "no child URL, path or document text appears in the audit",
                           "child-" not in blob and "embedded" not in blob and "iframe" not in blob,
                           {"audit_bytes": len(blob)})
                finally:
                    host.finish()
    finally:
        server.shutdown()
        other.shutdown()

    leaked = [word for word in ("embedded alpha", "child-a.html", "child-nested")
              if any(word in response for response in responses)]
    receipt = {
        "court": "native-dom child frames (control 0.0.2)",
        "host_sha256": hashlib.sha256(Path(args.binary).read_bytes()).hexdigest(),
        "caps": CAPS,
        "checks": checks,
        "checks_passed": sum(1 for c in checks if c["passed"]),
        "checks_total": len(checks),
        "passed": all(c["passed"] for c in checks),
        "limitations": [
            "design-frozen court: it fails until bounded child frames exist on the native route",
            "observation only: acting inside a child needs a node reference that can name a frame, which is unruled",
            "two hermetic loopback origins, macOS only; no surface, no window, no AppKit",
            "no pid, path, window or desktop fact is recorded",
        ],
        "child_document_text_in_responses": leaked,
    }
    Path(args.receipt).write_text(json.dumps(receipt, indent=1, sort_keys=True) + "\n")
    print(json.dumps({"passed": receipt["passed"], "checks_passed": receipt["checks_passed"],
                      "checks_total": receipt["checks_total"]}))
    for check in checks:
        if not check["passed"]:
            print("FAIL", json.dumps(check)[:200])
    return 0 if receipt["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
