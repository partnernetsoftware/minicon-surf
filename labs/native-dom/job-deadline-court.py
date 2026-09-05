#!/usr/bin/env python3
"""The frozen court for the pending-job deadline escape.

Frozen from `job-deadline-design-0.0.1.md` §5, §6 and §8.6 before the host
changes, and failing until the fix exists.

**This court can meet a host that hangs**, which is the defect it is about, so
no request waits without a bound: every one is sent on a worker with an
absolute wall-clock limit, and a host that misses it is killed by its exact
pid, reaped, and recorded as a timeout — which is the falsification, never a
wait that continues. The receipt names every host that had to be killed.

Strictly headless: no surface binary, no window, no AppKit, and it refuses to
run with the visible-court variable set. One hermetic loopback origin, both
allocators, a fresh host per group so a killed one costs only its group.

Groups: open, chain, timer, throwing, typing, atomicity, usability, owners,
secrecy.
"""

import argparse
import hashlib
import importlib.util
import json
import os
import signal
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "protocol"))
import check_contract  # noqa: E402

VISIBLE_ENV = "MINICON_SURF_ALLOW_VISIBLE_COURT"
# J1: an operation whose job is interrupted answers within its deadline plus
# this margin. J3: the request after any interruption answers within this.
DEADLINE_MARGIN_MS = 500
NEXT_REQUEST_MS = 1000
# J2: a finite chain of this many jobs completes inside this deadline.
CHAIN_JOBS = 1000
CHAIN_DEADLINE_MS = 5000
# J4: owners return to the empty-host baseline within this.
OWNER_RETURN_BYTES = 65536


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


def page(title, script, body=""):
    return ("<!doctype html><html><body><main><h1>" + title + "</h1>"
            "<p id=\"m\">start</p>" + body + "</main>"
            "<script>var mark=document.getElementById('m');"
            "var write=function(t){mark.textContent=t;};" + script
            + "</script></body></html>").encode()


class Supervised:
    """One host, and a wall clock over it. A request that does not answer in
    time costs the host its life: killed by pid, reaped, and recorded."""

    def __init__(self, binary, directory, origin, allocator):
        self.host = RETENTION.Host(binary, directory, origin, allocator)
        self.killed = False
        self.timeouts = []

    @property
    def pid(self):
        return self.host.process.pid

    def call(self, operation, arguments, deadline_ms=5000, wall_ms=None):
        if self.killed:
            return {"ok": False, "error": {"code": "host_killed"}}
        wall = wall_ms if wall_ms is not None else deadline_ms + 4000
        request = {"protocol": "minicon-surf.control", "version": "0.0.2",
                   "request_id": "req_job_0", "deadline_ms": deadline_ms,
                   "operation": operation, "arguments": arguments}
        check_contract.validate_request(request)
        self.host.counter += 1
        request["request_id"] = f"req_job_{self.host.counter}"
        answered = {}

        def pump():
            try:
                self.host.process.stdin.write(json.dumps(request) + "\n")
                self.host.process.stdin.flush()
                line = self.host.process.stdout.readline()
                answered["line"] = line
            except Exception as error:  # a killed host closes its pipes
                answered["error"] = str(error)

        worker = threading.Thread(target=pump, daemon=True)
        began = time.monotonic()
        worker.start()
        worker.join(wall / 1000.0)
        elapsed_ms = (time.monotonic() - began) * 1000
        if worker.is_alive() or "line" not in answered:
            # The falsification: this host did not answer inside its bound.
            self.timeouts.append({"operation": operation,
                                  "deadline_ms": deadline_ms,
                                  "wall_ms": wall,
                                  "waited_ms": round(elapsed_ms, 1)})
            self.kill()
            return {"ok": False, "error": {"code": "host_timeout"},
                    "elapsed_ms": elapsed_ms}
        line = answered["line"]
        if not line:
            self.killed = True
            return {"ok": False, "error": {"code": "host_exited"}}
        answer = json.loads(line)
        check_contract.validate_response(answer)
        answer["elapsed_ms"] = elapsed_ms
        return answer

    def ok(self, operation, arguments, **kw):
        answer = self.call(operation, arguments, **kw)
        if not answer.get("ok"):
            raise RuntimeError(f"{operation}: {answer.get('error')}")
        return answer["result"]

    def kill(self):
        """Exactly this host, by pid, and reaped."""
        if self.killed:
            return
        self.killed = True
        try:
            os.kill(self.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        try:
            self.host.process.wait(timeout=10)
        except Exception:
            pass

    def finish(self):
        if self.killed:
            return
        try:
            self.host.process.stdin.close()
            self.host.process.wait(timeout=15)
        except Exception:
            self.kill()


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
                # A job that never returns, queued by the document's own script.
                "/infinite.html": page("Infinite",
                                       "Promise.resolve().then(function(){for(;;){}});"),
                # A build interrupted after a mutation: this proves the build
                # commits nothing, not that a handler's effect survives.
                "/mutate-then-hang.html": page("Mutate then hang",
                                               "write('mutated');"
                                               "Promise.resolve().then(function(){for(;;){}});"),
                # A live target whose handler mutates and then queues a job
                # that never returns: the action must fail and the mutation
                # must stand.
                "/handler-hang.html": page("Handler hang",
                                           "document.getElementById('go').addEventListener('click',"
                                           "function(){write('handler ran');"
                                           "Promise.resolve().then(function(){for(;;){}});});",
                                           '<a id="go" href="#stay">go</a>'),
                # A finite chain that must be allowed to finish.
                "/chain.html": page("Chain",
                                    "var n=0;var step=function(){n=n+1;write('n '+n);"
                                    "if(n<" + str(CHAIN_JOBS) + "){Promise.resolve().then(step);}};"
                                    "Promise.resolve().then(step);"),
                # A job that throws, and a later one that must still run.
                "/throwing.html": page("Throwing",
                                       "Promise.resolve().then(function(){throw new Error('court');});"
                                       "Promise.resolve().then(function(){write('after throw');});"),
                # queueMicrotask, whose throw may or may not be observable.
                "/queue-throw.html": page("Queue throw",
                                          "queueMicrotask(function(){throw new Error('court');});"
                                          "queueMicrotask(function(){write('after queue throw');});"),
                # A rejection nobody handles: a different event from a throw.
                "/rejection.html": page("Rejection",
                                        "Promise.reject(new Error('court'));"
                                        "Promise.resolve().then(function(){write('after rejection');});"),
                # A timer whose callback queues a job, finite and infinite.
                "/timer-job.html": page("Timer job",
                                        "setTimeout(function(){"
                                        "Promise.resolve().then(function(){write('timer job ran');});},0);"),
                "/timer-hang.html": page("Timer hang",
                                         "setTimeout(function(){"
                                         "Promise.resolve().then(function(){for(;;){}});},0);"),
                "/quiet.html": page("Quiet", "", '<a id="q" href="/quiet.html">q</a>'),
            }
            if path in pages:
                return self.reply(200, pages[path])
            return super().do_GET()

    server = network.Server(("127.0.0.1", 0), Handler)
    origin = f"http://127.0.0.1:{server.server_address[1]}"
    threading.Thread(target=server.serve_forever, daemon=True).start()
    checks = []
    killed_hosts = []

    def expect(name, condition, detail=None):
        checks.append({"check": name, "passed": bool(condition),
                       **({"detail": detail} if detail is not None else {})})

    def timed_out(answer):
        return (answer.get("error") or {}).get("code") in ("host_timeout", "host_killed",
                                                           "host_exited")

    try:
        for allocator in ("system", "arena"):
            tag = f"[{allocator}] "

            def fresh(directory):
                return Supervised(args.binary, directory, origin, allocator)

            def counters(host):
                report = host.call("memory.report", {})
                if not report.get("ok"):
                    return {}
                return report["result"]["owners"].get("jobs") or {}

            def owner_bytes(host):
                report = host.call("memory.report", {})
                if not report.get("ok"):
                    return None
                owners = report["result"]["owners"]
                return owners["script_realms"]["malloc_bytes"] + owners["targets"]["fixture_bytes"]

            # 1, 5, 6, 7: an infinite job during target.open.
            with tempfile.TemporaryDirectory(prefix="minicon-surf-job-") as directory:
                host = fresh(directory)
                try:
                    profile = host.ok("profile.create", {"persistence": "ephemeral"})["profile"]
                    session = host.ok("session.open", {"profile": profile})["session"]
                    empty = owner_bytes(host)
                    opened = host.call("target.open",
                                       {"session": session, "url": f"{origin}/infinite.html"},
                                       deadline_ms=2000,
                                       wall_ms=2000 + DEADLINE_MARGIN_MS)
                    expect(tag + "J1: an infinite job's open answers inside its deadline and margin",
                           not timed_out(opened),
                           {"code": (opened.get("error") or {}).get("code"),
                            "elapsed_ms": round(opened.get("elapsed_ms", 0), 1)})
                    expect(tag + "the open fails deadline_exceeded, retryable, and says nothing of the page",
                           (not opened.get("ok"))
                           and (opened.get("error") or {}).get("code") == "deadline_exceeded"
                           and (opened.get("error") or {}).get("retryable") is True
                           and "for(;;)" not in json.dumps(opened.get("error") or {}),
                           {"error": (opened.get("error") or {}).get("code")})
                    listed = host.call("session.inspect", {"session": session},
                                       deadline_ms=1000, wall_ms=NEXT_REQUEST_MS + 1000)
                    targets = (listed.get("result") or {}).get("targets") or []
                    expect(tag + "J3 and atomicity: the host answers next and committed no target",
                           listed.get("ok") and targets == [],
                           {"targets": len(targets),
                            "elapsed_ms": round(listed.get("elapsed_ms", 0), 1)})
                    after = owner_bytes(host)
                    expect(tag + "J4: the owners return to the empty-host baseline",
                           after is not None and empty is not None
                           and abs(after - empty) <= OWNER_RETURN_BYTES,
                           {"owner_bytes": None if after is None else after - empty})
                finally:
                    if host.timeouts:
                        killed_hosts.append({"group": "infinite-open", "allocator": allocator,
                                             "timeouts": host.timeouts})
                    host.finish()

            # 6b: a mutation made before the infinite job stands.
            with tempfile.TemporaryDirectory(prefix="minicon-surf-job-") as directory:
                host = fresh(directory)
                try:
                    profile = host.ok("profile.create", {"persistence": "ephemeral"})["profile"]
                    session = host.ok("session.open", {"profile": profile})["session"]
                    opened = host.call("target.open",
                                       {"session": session, "url": f"{origin}/mutate-then-hang.html"},
                                       deadline_ms=2000, wall_ms=2000 + DEADLINE_MARGIN_MS)
                    # The build is interrupted, so no target exists to observe;
                    # what must be true is that the failure says so honestly.
                    # This group is failed-build atomicity, and nothing more:
                    # an interrupted build leaves no target to observe, so it
                    # is not evidence about handler effects (design §10).
                    expect(tag + "an interrupted build reports the deadline and commits nothing",
                           (not opened.get("ok"))
                           and (opened.get("error") or {}).get("code") == "deadline_exceeded",
                           {"error": (opened.get("error") or {}).get("code")})
                finally:
                    if host.timeouts:
                        killed_hosts.append({"group": "mutate-then-hang", "allocator": allocator,
                                             "timeouts": host.timeouts})
                    host.finish()

            # 6c: a handler's effect before an infinite job stands, on a live
            # target, and the target keeps answering (design §10).
            with tempfile.TemporaryDirectory(prefix="minicon-surf-job-") as directory:
                host = fresh(directory)
                try:
                    profile = host.ok("profile.create", {"persistence": "ephemeral"})["profile"]
                    session = host.ok("session.open", {"profile": profile})["session"]
                    target = host.ok("target.open",
                                     {"session": session, "url": f"{origin}/handler-hang.html"},
                                     deadline_ms=2000, wall_ms=6000)["target"]
                    before = host.ok("target.inspect", {"target": target})
                    snap = host.call("target.snapshot",
                                     {"target": target, "format": "semantic",
                                      "max_bytes": 65536, "max_nodes": 32})
                    link = next((n["reference"] for n in snap["result"]["nodes"]
                                 if n.get("role") == "link"), None) if snap.get("ok") else None
                    acted = host.call("target.act",
                                      {"target": target, "reference": link,
                                       "action": {"kind": "click"}},
                                      deadline_ms=1500,
                                      wall_ms=1500 + DEADLINE_MARGIN_MS) if link else {}
                    expect(tag + "an action whose handler queues an infinite job fails deadline_exceeded",
                           link is not None and not timed_out(acted)
                           and (not acted.get("ok"))
                           and (acted.get("error") or {}).get("code") == "deadline_exceeded",
                           {"code": (acted.get("error") or {}).get("code"),
                            "elapsed_ms": round(acted.get("elapsed_ms", 0), 1)})
                    after_snap = host.call("target.snapshot",
                                           {"target": target, "format": "semantic",
                                            "max_bytes": 65536, "max_nodes": 32},
                                           deadline_ms=1000, wall_ms=NEXT_REQUEST_MS + 1000)
                    marks = [n.get("name") for n in after_snap["result"]["nodes"]
                             if n.get("role") == "text"] if after_snap.get("ok") else []
                    after = host.call("target.inspect", {"target": target},
                                      deadline_ms=1000, wall_ms=NEXT_REQUEST_MS + 1000)
                    expect(tag + "the handler's mutation stands and the next observation shows it",
                           after_snap.get("ok")
                           and any("handler ran" == (m or "") for m in marks),
                           {"marks": len(marks)})
                    expect(tag + "the revision includes it and the target keeps answering",
                           after.get("ok")
                           and after["result"]["revision"] > before["revision"],
                           {"revision": [before.get("revision"),
                                         (after.get("result") or {}).get("revision")]})
                finally:
                    if host.timeouts:
                        killed_hosts.append({"group": "handler-hang", "allocator": allocator,
                                             "timeouts": host.timeouts})
                    host.finish()

            # 2: a finite chain must be allowed to finish.
            with tempfile.TemporaryDirectory(prefix="minicon-surf-job-") as directory:
                host = fresh(directory)
                try:
                    profile = host.ok("profile.create", {"persistence": "ephemeral"})["profile"]
                    session = host.ok("session.open", {"profile": profile})["session"]
                    opened = host.call("target.open",
                                       {"session": session, "url": f"{origin}/chain.html"},
                                       deadline_ms=CHAIN_DEADLINE_MS,
                                       wall_ms=CHAIN_DEADLINE_MS + 4000)
                    marks = []
                    if opened.get("ok"):
                        snap = host.call("target.snapshot",
                                         {"target": opened["result"]["target"], "format": "semantic",
                                          "max_bytes": 65536, "max_nodes": 32})
                        if snap.get("ok"):
                            marks = [n.get("name") for n in snap["result"]["nodes"]
                                     if n.get("role") == "text"]
                    expect(tag + f"J2: a chain of {CHAIN_JOBS} jobs finishes and the document commits",
                           opened.get("ok") and any(f"n {CHAIN_JOBS}" == (m or "") for m in marks),
                           {"ok": opened.get("ok"),
                            "elapsed_ms": round(opened.get("elapsed_ms", 0), 1)})
                    ran = counters(host).get("run_total")
                    expect(tag + "and the jobs it ran are counted as completions",
                           isinstance(ran, int) and ran >= CHAIN_JOBS, {"run_total": ran})
                finally:
                    if host.timeouts:
                        killed_hosts.append({"group": "chain", "allocator": allocator,
                                             "timeouts": host.timeouts})
                    host.finish()

            # 4: a job that throws is the page's business.
            with tempfile.TemporaryDirectory(prefix="minicon-surf-job-") as directory:
                host = fresh(directory)
                try:
                    profile = host.ok("profile.create", {"persistence": "ephemeral"})["profile"]
                    session = host.ok("session.open", {"profile": profile})["session"]
                    before = counters(host)
                    for page_path, label in (("/throwing.html", "a throwing job"),
                                             ("/queue-throw.html", "a throwing queueMicrotask"),
                                             ("/rejection.html", "an unhandled rejection")):
                        opened = host.call("target.open",
                                           {"session": session, "url": f"{origin}{page_path}"},
                                           deadline_ms=2000, wall_ms=6000)
                        marks = []
                        if opened.get("ok"):
                            snap = host.call("target.snapshot",
                                             {"target": opened["result"]["target"],
                                              "format": "semantic",
                                              "max_bytes": 65536, "max_nodes": 32})
                            if snap.get("ok"):
                                marks = [n.get("name") for n in snap["result"]["nodes"]
                                         if n.get("role") == "text"]
                        expect(tag + f"{label} does not fail the operation and the drain continues",
                               opened.get("ok") and any("after" in (m or "") for m in marks),
                               {"ok": opened.get("ok"), "marks": len(marks)})
                    after = counters(host)
                    threw = after.get("threw_total", 0) - before.get("threw_total", 0)
                    expect(tag + "the raises the host can observe are counted, and only those",
                           isinstance(after.get("threw_total"), int) and threw >= 1
                           and "source" not in json.dumps(after) and "court" not in json.dumps(after),
                           {"threw_delta": threw, "counters": sorted(after)})
                finally:
                    if host.timeouts:
                        killed_hosts.append({"group": "throwing", "allocator": allocator,
                                             "timeouts": host.timeouts})
                    host.finish()

            # 3: a job queued by a timer callback, finite and infinite.
            with tempfile.TemporaryDirectory(prefix="minicon-surf-job-") as directory:
                host = fresh(directory)
                try:
                    profile = host.ok("profile.create", {"persistence": "ephemeral"})["profile"]
                    session = host.ok("session.open", {"profile": profile})["session"]
                    target = host.ok("target.open",
                                     {"session": session, "url": f"{origin}/timer-job.html"},
                                     deadline_ms=2000, wall_ms=6000)["target"]
                    snap = host.call("target.snapshot",
                                     {"target": target, "format": "semantic",
                                      "max_bytes": 65536, "max_nodes": 32})
                    marks = [n.get("name") for n in snap["result"]["nodes"]
                             if n.get("role") == "text"] if snap.get("ok") else []
                    expect(tag + "a job queued by a timer callback runs at that boundary",
                           any("timer job ran" == (m or "") for m in marks), {"marks": len(marks)})
                    hang = host.ok("target.open",
                                   {"session": session, "url": f"{origin}/timer-hang.html"},
                                   deadline_ms=2000, wall_ms=6000)["target"]
                    observed = host.call("target.snapshot",
                                         {"target": hang, "format": "semantic",
                                          "max_bytes": 65536, "max_nodes": 32},
                                         deadline_ms=1500, wall_ms=1500 + DEADLINE_MARGIN_MS)
                    expect(tag + "an infinite job from a timer callback is interrupted, not endless",
                           not timed_out(observed)
                           and (not observed.get("ok"))
                           and (observed.get("error") or {}).get("code") == "deadline_exceeded",
                           {"code": (observed.get("error") or {}).get("code"),
                            "elapsed_ms": round(observed.get("elapsed_ms", 0), 1)})
                    usable = host.call("target.inspect", {"target": hang},
                                       deadline_ms=1000, wall_ms=NEXT_REQUEST_MS + 1000)
                    expect(tag + "J3: the target still answers after that interruption",
                           usable.get("ok"),
                           {"elapsed_ms": round(usable.get("elapsed_ms", 0), 1)})
                    interrupted = counters(host).get("drains_interrupted_total")
                    expect(tag + "the interrupted drain is counted once as a drain",
                           isinstance(interrupted, int) and interrupted >= 1,
                           {"drains_interrupted_total": interrupted})
                finally:
                    if host.timeouts:
                        killed_hosts.append({"group": "timer-job", "allocator": allocator,
                                             "timeouts": host.timeouts})
                    host.finish()

            # 9: secrecy.
            with tempfile.TemporaryDirectory(prefix="minicon-surf-job-") as directory:
                host = fresh(directory)
                try:
                    profile = host.ok("profile.create", {"persistence": "ephemeral"})["profile"]
                    session = host.ok("session.open", {"profile": profile})["session"]
                    host.call("target.open", {"session": session, "url": f"{origin}/throwing.html"},
                              deadline_ms=2000, wall_ms=6000)
                    audit = host.call("session.inspect", {"session": session})
                    blob = json.dumps(audit.get("result") or {})
                    expect(tag + "no job source, rejection value or page text in the ledger",
                           audit.get("ok") and "court" not in blob and "Promise" not in blob
                           and "for(;;)" not in blob,
                           {"audit_bytes": len(blob)})
                finally:
                    if host.timeouts:
                        killed_hosts.append({"group": "secrecy", "allocator": allocator,
                                             "timeouts": host.timeouts})
                    host.finish()
    finally:
        server.shutdown()

    receipt = {
        "court": "native-dom pending-job deadline (control 0.0.2)",
        "host_sha256": hashlib.sha256(Path(args.binary).read_bytes()).hexdigest(),
        "criteria": {"deadline_margin_ms": DEADLINE_MARGIN_MS,
                     "next_request_ms": NEXT_REQUEST_MS,
                     "chain_jobs": CHAIN_JOBS,
                     "chain_deadline_ms": CHAIN_DEADLINE_MS,
                     "owner_return_bytes": OWNER_RETURN_BYTES},
        "checks": checks,
        "checks_passed": sum(1 for c in checks if c["passed"]),
        "checks_total": len(checks),
        "passed": all(c["passed"] for c in checks) and not killed_hosts,
        "hosts_killed": killed_hosts,
        "limitations": [
            "design-frozen court: it fails until the pending-job deadline is enforced",
            "a host that does not answer inside its wall-clock bound is killed by pid and reaped; that timeout is the falsification",
            "one hermetic loopback origin, macOS only; no surface, no window, no AppKit",
            "no job source, rejection value or page text is recorded",
        ],
    }
    Path(args.receipt).write_text(json.dumps(receipt, indent=1, sort_keys=True) + "\n")
    print(json.dumps({"passed": receipt["passed"], "checks_passed": receipt["checks_passed"],
                      "checks_total": receipt["checks_total"],
                      "hosts_killed": len(killed_hosts)}))
    for check in checks:
        if not check["passed"]:
            print("FAIL", json.dumps(check)[:170])
    return 0 if receipt["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
