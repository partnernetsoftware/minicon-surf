#!/usr/bin/env python3
"""The frozen court for the bounded timer slice.

Frozen from `timer-design-0.0.1.md` §11 and §14 before the host changes, and
failing until the slice exists. Three of its checks fail on the current host
for a stronger reason than absence: today's shim discards the delay, cannot
cancel, and hands every timer the same handle, so those checks describe a
defect rather than a missing feature.

Strictly headless: no surface binary, no window, no AppKit, and it refuses to
run with the visible-court variable set. One hermetic loopback origin, both
allocators, a fresh host per run. Every fixture value is the court's own; no
callback source, delay or page text reaches the receipt.

Groups: delay, cancel, handles, ordering, boundaries, budget, bound,
teardown, revision, throwing, deadline, children, secrecy, cdp.
"""

import argparse
import hashlib
import importlib.util
import json
import os
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "protocol"))
import check_contract  # noqa: E402

VISIBLE_ENV = "MINICON_SURF_ALLOW_VISIBLE_COURT"
MAX_SAFE = (1 << 53) - 1
PENDING_LIMIT = 64
BOUNDARY_BUDGET = 32


def handles(binary, origin, expect, tag, start, expected_more):
    """A realm whose next handle is seeded near the safe integer: exactly the
    handles below it can be minted, and none at or above it."""
    import subprocess
    with tempfile.TemporaryDirectory(prefix="minicon-surf-timer-handle-") as directory:
        environment = {k: v for k, v in os.environ.items() if k != VISIBLE_ENV}
        process = subprocess.Popen(
            [binary, "serve", "--stdio", "--fixture-root", str(RETENTION.FIXTURE_ROOT),
             "--config-dir", str(Path(directory) / "config"), "--allow-origin", origin,
             "--court-timer-handle", str(start)],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            text=True, env=environment)
        counter = [0]

        def ok(operation, arguments):
            counter[0] += 1
            request = {"protocol": "minicon-surf.control", "version": "0.0.2",
                       "request_id": f"req_handle_{counter[0]}", "deadline_ms": 30000,
                       "operation": operation, "arguments": arguments}
            check_contract.validate_request(request)
            process.stdin.write(json.dumps(request) + "\n")
            process.stdin.flush()
            line = process.stdout.readline()
            if not line:
                raise RuntimeError("host exited")
            answer = json.loads(line)
            check_contract.validate_response(answer)
            if not answer["ok"]:
                raise RuntimeError(f"{operation} failed: {answer['error']}")
            return answer["result"]

        try:
            profile = ok("profile.create", {"persistence": "ephemeral"})["profile"]
            session = ok("session.open", {"profile": profile})["session"]
            target = ok("target.open", {"session": session, "url": f"{origin}/handle.html"})["target"]
            observed = ok("target.snapshot", {"target": target, "format": "semantic",
                                              "max_bytes": 65536, "max_nodes": 32})
            texts = [n.get("name") or "" for n in observed["nodes"] if n.get("role") == "text"]
            state = texts[0] if texts else ""
            expect(tag + f"exactly {expected_more} more handles can be minted at the boundary",
                   state == f"made {expected_more}",
                   {"observed_len": len(state)})
        finally:
            process.stdin.close()
            process.wait(timeout=30)


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


def page(title, script):
    return ("<!doctype html><html><body><main><h1>" + title + "</h1>"
            "<p id=\"m\">start</p></main>"
            "<script>var mark=document.getElementById('m');"
            "var write=function(t){mark.textContent=t;};" + script
            + "</script></body></html>").encode()


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
            pages = {
                # A zero-delay timer and a fifty-millisecond one.
                "/delay.html": page("Delay",
                                    "var seen='';"
                                    "setTimeout(function(){seen=seen+'a';write('seen '+seen);},0);"
                                    "setTimeout(function(){seen=seen+'b';write('seen '+seen);},50);"),
                # Scheduled and cleared in the same turn.
                "/cancel.html": page("Cancel",
                                     "var log='';"
                                     "var h=setTimeout(function(){log=log+'x';write('log '+log);},0);"
                                     "clearTimeout(h);"
                                     "setTimeout(function(){log=log+'y';write('log '+log);},0);"),
                # Two handles in one turn, and clearing one leaves the other.
                "/handles.html": page("Handles",
                                      "var a=setTimeout(function(){write('a ran');},0);"
                                      "var b=setTimeout(function(){write('b ran');},0);"
                                      "var distinct=(a!==b)?'distinct':'same';"
                                      "clearTimeout(a);"
                                      "write('handles '+distinct);"
                                      "setTimeout(function(){write('handles '+distinct+' b ran');},0);"),
                # A later-scheduled shorter delay runs first.
                "/order.html": page("Order",
                                    "var seq='';"
                                    "setTimeout(function(){seq=seq+'1';write('seq '+seq);},40);"
                                    "setTimeout(function(){seq=seq+'2';write('seq '+seq);},0);"
                                    "setTimeout(function(){seq=seq+'3';write('seq '+seq);},0);"),
                # Forty zero-delay timers: a boundary runs at most the budget.
                "/budget.html": page("Budget",
                                     "var n=0;for(var i=0;i<40;i++){"
                                     "setTimeout(function(){n=n+1;write('ran '+n);},0);}"),
                # One past the pending bound.
                "/bound.html": page("Bound",
                                    "var refused=0;var made=0;"
                                    "for(var i=0;i<70;i++){try{setTimeout(function(){},1000);made=made+1;}"
                                    "catch(e){refused=refused+1;}}"
                                    "write('made '+made+' refused '+refused);"),
                # A callback that throws, and one that runs after it.
                "/throws.html": page("Throws",
                                     "setTimeout(function(){write('before throw');"
                                     "throw new Error('court');},0);"
                                     "setTimeout(function(){write('after throw');},0);"),
                # A string body and a non-callable first argument.
                "/callable.html": page("Callable",
                                       "var threw=0;"
                                       "try{setTimeout('write(\\'string ran\\')',0);}catch(e){threw=threw+1;}"
                                       "try{setTimeout(42,0);}catch(e){threw=threw+1;}"
                                       "write('threw '+threw);"),
                # A callback that schedules another zero-delay timer.
                "/chain.html": page("Chain",
                                    "var n=0;var step=function(){n=n+1;write('step '+n);"
                                    "if(n<3){setTimeout(step,0);}};setTimeout(step,0);"),
                # Timers pending across a navigation.
                "/teardown.html": page("Teardown",
                                       "setTimeout(function(){write('late ran');},60);"),
                "/landed.html": page("Landed", ""),
                # Three attempts against a seeded handle boundary.
                "/handle.html": page("Handle",
                                     "var made=0;"
                                     "for(var i=0;i<3;i++){try{setTimeout(function(){},1000);made=made+1;}"
                                     "catch(e){}}"
                                     "write('made '+made);"),
                # A child frame must have no timer surface.
                "/parent-timer.html": ("<!doctype html><html><body><main><h1>Parent timer</h1>"
                                       "<iframe src=\"/child-timer.html\"></iframe>"
                                       "</main></body></html>").encode(),
                "/child-timer.html": page("Child timer",
                                          "setTimeout(function(){write('child ran');},0);"),
            }
            if path in pages:
                return self.reply(200, pages[path])
            return super().do_GET()

    server = network.Server(("127.0.0.1", 0), Handler)
    origin = f"http://127.0.0.1:{server.server_address[1]}"
    threading.Thread(target=server.serve_forever, daemon=True).start()
    checks = []
    responses = []

    def expect(name, condition, detail=None):
        checks.append({"check": name, "passed": bool(condition),
                       **({"detail": detail} if detail is not None else {})})

    try:
        for allocator in ("system", "arena"):
            with tempfile.TemporaryDirectory(prefix="minicon-surf-timer-") as directory:
                host = RETENTION.Host(args.binary, directory, origin, allocator)
                tag = f"[{allocator}] "

                def call(operation, arguments, deadline_ms=30000):
                    request = {"protocol": "minicon-surf.control", "version": "0.0.2",
                               "request_id": "req_timer_0", "deadline_ms": deadline_ms,
                               "operation": operation, "arguments": arguments}
                    check_contract.validate_request(request)
                    host.counter += 1
                    request["request_id"] = f"req_timer_{host.counter}"
                    host.process.stdin.write(json.dumps(request) + "\n")
                    host.process.stdin.flush()
                    line = host.process.stdout.readline()
                    if not line:
                        raise RuntimeError("host exited")
                    answer = json.loads(line)
                    check_contract.validate_response(answer)
                    responses.append(json.dumps(answer))
                    return answer

                def ok(operation, arguments, **kw):
                    answer = call(operation, arguments, **kw)
                    if not answer["ok"]:
                        raise RuntimeError(f"{operation} failed: {answer['error']}")
                    return answer["result"]

                def open_page(session, path):
                    return ok("target.open", {"session": session, "url": f"{origin}{path}"})["target"]

                def mark(target, **extra):
                    answer = call("target.snapshot", {"target": target, "format": "semantic",
                                                      "max_bytes": 65536, "max_nodes": 64, **extra})
                    if not answer.get("ok"):
                        return None
                    texts = [n.get("name") or "" for n in answer["result"]["nodes"]
                             if n.get("role") == "text"]
                    return texts[0] if texts else ""

                try:
                    profile = ok("profile.create", {"persistence": "ephemeral"})["profile"]
                    session = ok("session.open", {"profile": profile})["session"]

                    # 1. The delay is honoured.
                    target = open_page(session, "/delay.html")
                    early = mark(target)
                    time.sleep(0.15)
                    late = mark(target)
                    expect(tag + "a zero-delay timer has run and a fifty-millisecond one has not",
                           early == "seen a", {"observed_len": len(early or "")})
                    expect(tag + "and after the delay has passed, both have run",
                           late == "seen ab", {"observed_len": len(late or "")})
                    ok("target.close", {"target": target})

                    # 2. clearTimeout cancels.
                    target = open_page(session, "/cancel.html")
                    time.sleep(0.05)
                    state = mark(target)
                    expect(tag + "a cleared timer never runs and an uncleared one does",
                           state == "log y", {"observed_len": len(state or "")})
                    ok("target.close", {"target": target})

                    # 3. Handles are distinct, and clearing one leaves the other.
                    target = open_page(session, "/handles.html")
                    time.sleep(0.05)
                    state = mark(target)
                    expect(tag + "two timers scheduled in one turn have different handles",
                           state == "handles distinct b ran", {"observed_len": len(state or "")})
                    ok("target.close", {"target": target})

                    # 4. Ordering: due time first, then scheduling order.
                    target = open_page(session, "/order.html")
                    time.sleep(0.15)
                    state = mark(target)
                    expect(tag + "a shorter delay scheduled later runs first, equal delays in order",
                           state == "seq 231", {"observed_len": len(state or "")})
                    ok("target.close", {"target": target})

                    # 5. A delayed timer runs at the boundary after it is due,
                    # and at no boundary before.
                    target = open_page(session, "/teardown.html")
                    before_due = mark(target)
                    time.sleep(0.2)
                    state = mark(target)
                    expect(tag + "a timer runs at the observation that follows its due time, not before",
                           before_due == "start" and state == "late ran",
                           {"before_len": len(before_due or ""), "after_len": len(state or "")})
                    ok("target.close", {"target": target})

                    # 6. The per-boundary budget holds.
                    target = open_page(session, "/budget.html")
                    first = mark(target)
                    second = mark(target)
                    expect(tag + f"one boundary runs at most {BOUNDARY_BUDGET} callbacks",
                           first == f"ran {BOUNDARY_BUDGET}" and second == "ran 40",
                           {"first_len": len(first or ""), "second_len": len(second or "")})
                    ok("target.close", {"target": target})

                    # 7. The pending bound refuses rather than growing.
                    target = open_page(session, "/bound.html")
                    state = mark(target)
                    timers = ok("target.inspect", {"target": target}).get("timers") or {}
                    expect(tag + f"scheduling past {PENDING_LIMIT} is refused in the realm",
                           state == f"made {PENDING_LIMIT} refused {70 - PENDING_LIMIT}"
                           and timers.get("limit") == PENDING_LIMIT
                           and timers.get("pending") == PENDING_LIMIT,
                           {"timers": timers})
                    expect(tag + "inspect reports pending and limit and nothing else",
                           sorted(timers) == ["limit", "pending"], {"fields": sorted(timers)})
                    ok("target.close", {"target": target})

                    # 8. Teardown with the realm.
                    target = open_page(session, "/teardown.html")
                    before = ok("memory.report", {})["owners"].get("timers") or {}
                    ok("target.navigate", {"target": target, "url": f"{origin}/landed.html"})
                    time.sleep(0.15)
                    state = mark(target)
                    after = ok("memory.report", {})["owners"].get("timers") or {}
                    expect(tag + "a timer pending at a navigation never runs and is retired",
                           state != "late ran"
                           and after.get("retired_total", 0) > before.get("retired_total", -1)
                           and after.get("objects") == 0,
                           {"objects": after.get("objects")})
                    ok("target.close", {"target": target})

                    # 8b. Every realm replacement retires what was pending.
                    for label, replace in (("close", None), ("reload", "reload")):
                        probe = open_page(session, "/teardown.html")
                        ok("target.inspect", {"target": probe})
                        pending = (ok("target.inspect", {"target": probe})
                                   .get("timers") or {}).get("pending")
                        before = (ok("memory.report", {})["owners"].get("timers") or {})
                        if replace is None:
                            ok("target.close", {"target": probe})
                        else:
                            ok("target.reload", {"target": probe})
                        after = (ok("memory.report", {})["owners"].get("timers") or {})
                        expect(tag + f"a {label} retires the pending timers of the realm it replaces",
                               pending == 1
                               and after.get("retired_total", 0) >= before.get("retired_total", 0) + 1,
                               {"pending": pending,
                                "retired": [before.get("retired_total"), after.get("retired_total")]})
                        if replace is not None:
                            ok("target.close", {"target": probe})

                    # 9. A due callback's mutations move the global revision.
                    target = open_page(session, "/teardown.html")
                    start = ok("target.inspect", {"target": target})["revision"]
                    time.sleep(0.15)
                    moved = ok("target.inspect", {"target": target})["revision"]
                    expect(tag + "a due callback's mutation advances the target's revision",
                           moved > start, {"revision": [start, moved]})
                    ok("target.close", {"target": target})

                    # 10. A throwing callback is discarded, the target lives.
                    target = open_page(session, "/throws.html")
                    time.sleep(0.05)
                    state = mark(target)
                    owners = ok("memory.report", {})["owners"].get("timers") or {}
                    expect(tag + "a throwing callback is counted and the next one still runs",
                           state == "after throw" and owners.get("threw_total", 0) >= 1,
                           {"threw_total": owners.get("threw_total")})
                    ok("target.close", {"target": target})

                    # 10b. Only a callable callback.
                    target = open_page(session, "/callable.html")
                    time.sleep(0.05)
                    state = mark(target)
                    expect(tag + "a string body and a non-callable argument both throw",
                           state == "threw 2", {"observed_len": len(state or "")})
                    ok("target.close", {"target": target})

                    # 10c. A callback's own zero-delay timer waits for the next drain.
                    target = open_page(session, "/chain.html")
                    marks = [mark(target)]
                    marks.append(mark(target))
                    marks.append(mark(target))
                    expect(tag + "a timer scheduled by a callback runs at the next boundary",
                           marks == ["step 1", "step 2", "step 3"],
                           {"steps": len([m for m in marks if m])})
                    ok("target.close", {"target": target})

                    # 12. A child frame has no timer surface.
                    target = open_page(session, "/parent-timer.html")
                    frames = ok("target.inspect", {"target": target})["frames"]
                    child = frames[1]["frame"] if len(frames) > 1 else None
                    time.sleep(0.05)
                    state = mark(target, frame=child) if child else None
                    expect(tag + "a child frame's timer never runs, because a child runs no scripts",
                           child is not None and state == "start",
                           {"observed_len": len(state or "")})
                    ok("target.close", {"target": target})

                    # 13. Secrecy.
                    audit = ok("session.inspect", {"session": session})
                    blob = json.dumps(audit)
                    expect(tag + "no callback, delay or page text in the ledger",
                           "setTimeout" not in blob and "ran" not in blob and "step" not in blob,
                           {"audit_bytes": len(blob)})
                finally:
                    host.finish()
        handles(args.binary, origin, expect, "[handle-3] ", MAX_SAFE - 3, 3)
        handles(args.binary, origin, expect, "[handle-0] ", MAX_SAFE, 0)
    finally:
        server.shutdown()

    leaked = [word for word in ("setTimeout", "clearTimeout", "step ", "seen ")
              if any(word in response for response in responses if '"audit"' in response)]
    receipt = {
        "court": "native-dom bounded timers (control 0.0.2)",
        "host_sha256": hashlib.sha256(Path(args.binary).read_bytes()).hexdigest(),
        "bounds": {"pending_per_realm": PENDING_LIMIT, "callbacks_per_boundary": BOUNDARY_BUDGET},
        "checks": checks,
        "checks_passed": sum(1 for c in checks if c["passed"]),
        "checks_total": len(checks),
        "passed": all(c["passed"] for c in checks),
        "limitations": [
            "design-frozen court: it fails until the bounded timer slice exists",
            "a timer's delay is a lower bound: nothing fires while no request arrives",
            "one hermetic loopback origin, macOS only; no surface, no window, no AppKit",
            "no callback source, delay or page text is recorded",
        ],
        "page_text_in_audit_responses": leaked,
    }
    Path(args.receipt).write_text(json.dumps(receipt, indent=1, sort_keys=True) + "\n")
    print(json.dumps({"passed": receipt["passed"], "checks_passed": receipt["checks_passed"],
                      "checks_total": receipt["checks_total"]}))
    for check in checks:
        if not check["passed"]:
            print("FAIL", json.dumps(check)[:160])
    return 0 if receipt["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
