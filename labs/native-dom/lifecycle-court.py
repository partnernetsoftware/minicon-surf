#!/usr/bin/env python3
"""The frozen court for the bounded document lifecycle.

Frozen from `lifecycle-design-0.0.1.md` §8 before the host changes, and run
against the current build first, where it fails.

A lifecycle handler can queue a job that never returns, so every host this
court starts is supervised exactly as the job-deadline court's is: each
request goes out on a worker with an absolute wall-clock limit, and a host
that misses it is killed by its exact pid, reaped, and recorded as a timeout,
which is the falsification rather than a wait that continues.

Strictly headless: no surface binary, no window, no AppKit, and it refuses to
run with the visible-court variable set. One hermetic loopback origin, both
allocators, a fresh host per group.

Groups: order, readyState, targets, event target, microtask, throw, once,
deadline, children, revision, memory.
"""

import argparse
import hashlib
import importlib.util
import json
import os
import signal
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "protocol"))
import check_contract  # noqa: E402

VISIBLE_ENV = "MINICON_SURF_ALLOW_VISIBLE_COURT"
OWNER_BYTES = 65536
REPLACEMENTS = 128


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
JOBS = load_module("job_deadline_court", Path(__file__).with_name("job-deadline-court.py"))


def page(title, script, body=""):
    return ("<!doctype html><html><body><main><h1>" + title + "</h1>"
            "<p id=\"m\">start</p>" + body + "</main>"
            "<script>var mark=document.getElementById('m');"
            "var write=function(t){mark.textContent=t;};" + script
            + "</script></body></html>").encode()


# One page records every step it can observe, in order.
RECORDER = (
    "var seen=[];"
    "var note=function(t){seen.push(t);write(seen.join(' '));};"
    "note('script:'+document.readyState);"
    "document.addEventListener('readystatechange',function(){note('rsc:'+document.readyState);});"
    "document.addEventListener('DOMContentLoaded',function(e){"
    "note('dcl:'+document.readyState+':'+(e.target===document?'doc':'other')"
    "+':'+(e.currentTarget===document?'doc':'other')+':'+(e.bubbles?'b':'nb')"
    "+':'+(e.cancelable?'c':'nc'));});"
    "document.addEventListener('load',function(){note('load-reached-document');});"
    "if(typeof window.addEventListener==='function'){"
    "window.addEventListener('DOMContentLoaded',function(e){"
    "note('dclwin:'+(e.target===document?'doc':'other')"
    "+':'+(e.currentTarget===window?'win':'other'));});"
    "window.addEventListener('load',function(e){"
    "note('load:'+document.readyState+':'+(e.target===window?'win':'other')"
    "+':'+(e.bubbles?'b':'nb'));});}"
    "else{note('no-window-target');}"
)


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
            path, _, query = self.path.partition("?")
            network.Handler.hits.append(path)
            if path == "/listeners.html":
                # The same document either way; only whether the listeners
                # stay registered differs, so the difference is the lifecycle
                # and not two different pages.
                keep = "1" if "keep=1" in query else "0"
                return self.reply(200, page("Listeners",
                                            "var KEEP=" + keep + ";"
                                            "var fn=function(){write('ran');};"
                                            "document.addEventListener('DOMContentLoaded',fn);"
                                            "if(!KEEP){document.removeEventListener('DOMContentLoaded',fn);}"))
            pages = {
                "/order.html": page("Order", RECORDER),
                # A page that builds its content only on DOMContentLoaded.
                "/late.html": page("Late",
                                   "document.addEventListener('DOMContentLoaded',function(){"
                                   "var p=document.createElement('p');"
                                   "p.textContent='built late';document.body.appendChild(p);});"),
                # A custom event on the window, and onload as a property.
                "/target.html": page("Target",
                                     "var seen=[];var note=function(t){seen.push(t);write(seen.join(' '));};"
                                     "if(typeof window.addEventListener!=='function'){note('no-window-target');}"
                                     "else{"
                                     # Added once, dispatched, removed, dispatched again.
                                     "var fn=function(){note('custom');};"
                                     "window.addEventListener('court',fn);"
                                     "window.dispatchEvent(new Event('court'));"
                                     "window.removeEventListener('court',fn);"
                                     "window.dispatchEvent(new Event('court'));"
                                     # Its own event: the same function added twice,
                                     # dispatched once, must run twice.
                                     "var twice=function(){note('twice');};"
                                     "window.addEventListener('dup',twice);"
                                     "window.addEventListener('dup',twice);"
                                     "window.dispatchEvent(new Event('dup'));"
                                     # onload as a property: set, cleared with null,
                                     # proven not to fire on an independent event,
                                     # then set again for the real load to verify.
                                     "window.onload=function(){note('onload-cleared-ran');};"
                                     "window.onload=null;"
                                     "note('onload-null:'+(window.onload===null?'yes':'no'));"
                                     "window.dispatchEvent(new Event('probe'));"
                                     "window.onload=function(){note('onload-first');};"
                                     "window.onload=function(){note('onload-second');};"
                                     "note('onload-is:'+(typeof window.onload));}"),
                # A microtask queued inside DOMContentLoaded.
                "/microtask.html": page("Microtask",
                                        "var seen=[];var note=function(t){seen.push(t);write(seen.join(' '));};"
                                        "document.addEventListener('DOMContentLoaded',function(){"
                                        "note('dcl');Promise.resolve().then(function(){note('job');});});"
                                        "if(typeof window.addEventListener==='function'){"
                                        "window.addEventListener('load',function(){note('load');});}"),
                # A throwing listener, with another after it and a later step.
                "/throwing.html": page("Throwing",
                                       "var seen=[];var note=function(t){seen.push(t);write(seen.join(' '));};"
                                       "document.addEventListener('DOMContentLoaded',function(){throw new Error('court');});"
                                       "document.addEventListener('DOMContentLoaded',function(){note('second');});"
                                       "if(typeof window.addEventListener==='function'){"
                                       "window.addEventListener('load',function(){note('load');});}"),
                # A lifecycle handler that queues a job which never returns.
                "/hang.html": page("Hang",
                                   "document.addEventListener('DOMContentLoaded',function(){"
                                   "Promise.resolve().then(function(){for(;;){}});});"),
                # A parent whose child must stay script-free.
                "/parent.html": ("<!doctype html><html><body><main><h1>Parent</h1>"
                                 "<iframe src=\"/child.html\"></iframe></main></body></html>").encode(),
                "/child.html": page("Child",
                                    "document.addEventListener('DOMContentLoaded',function(){"
                                    "write('child lifecycle ran');});"),
                "/quiet.html": page("Quiet", ""),
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

    try:
        for allocator in ("system", "arena"):
            tag = f"[{allocator}] "

            def group(label):
                directory = tempfile.TemporaryDirectory(prefix="minicon-surf-life-")
                host = JOBS.Supervised(args.binary, directory.name, origin, allocator)
                return directory, host, label

            def close(directory, host, label):
                if host.timeouts:
                    killed_hosts.append({"group": label, "allocator": allocator,
                                         "timeouts": host.timeouts})
                host.finish()
                directory.cleanup()

            def observe(host, target):
                answer = host.call("target.snapshot",
                                   {"target": target, "format": "semantic",
                                    "max_bytes": 65536, "max_nodes": 64})
                if not answer.get("ok"):
                    return None, []
                texts = [n.get("name") or "" for n in answer["result"]["nodes"]
                         if n.get("role") == "text"]
                return (texts[0] if texts else ""), texts

            def owner_bytes(host):
                answer = host.call("memory.report", {})
                if not answer.get("ok"):
                    return None
                owners = answer["result"]["owners"]
                return owners["script_realms"]["malloc_bytes"] + owners["targets"]["fixture_bytes"]

            # 1, 2, 3: order, readyState and targets, from one recording page.
            directory, host, label = group("order")
            try:
                profile = host.ok("profile.create", {"persistence": "ephemeral"})["profile"]
                session = host.ok("session.open", {"profile": profile})["session"]
                target = host.ok("target.open", {"session": session, "url": f"{origin}/order.html"},
                                 deadline_ms=5000)["target"]
                line, _ = observe(host, target)
                steps = (line or "").split()
                expect(tag + "the four steps happen in the order the standard gives them",
                       steps == ["script:loading", "rsc:interactive",
                                 "dcl:interactive:doc:doc:b:nc", "dclwin:doc:win",
                                 "rsc:complete", "load:complete:win:nb"],
                       {"steps": steps})
                expect(tag + "readyState is loading, then interactive, then complete",
                       len(steps) == 6 and steps[0].endswith("loading")
                       and steps[1].endswith("interactive") and steps[4].endswith("complete"),
                       {"steps": len(steps)})
                expect(tag + "DOMContentLoaded targets the document and load targets the window",
                       len(steps) == 6 and ":doc:doc:" in steps[2]
                       and steps[5].startswith("load:complete:win"),
                       {"steps": len(steps)})
                expect(tag + "DOMContentLoaded bubbles to the window, load does not bubble at all",
                       len(steps) == 6 and steps[2].endswith(":b:nc")
                       and steps[3] == "dclwin:doc:win"
                       and steps[5].endswith(":nb")
                       and "load-reached-document" not in steps,
                       {"steps": len(steps)})
                expect(tag + "the window's listener runs after the document's own, in path order",
                       len(steps) == 6 and steps.index("dclwin:doc:win") > 2,
                       {"steps": len(steps)})
                late = host.ok("target.open", {"session": session, "url": f"{origin}/late.html"},
                               deadline_ms=5000)["target"]
                _, late_texts = observe(host, late)
                expect(tag + "a page that builds itself on DOMContentLoaded is not inert",
                       any("built late" == t for t in late_texts), {"texts": len(late_texts)})
            finally:
                close(directory, host, label)

            # 4: the window is a real EventTarget, and onload is a property.
            directory, host, label = group("event-target")
            try:
                profile = host.ok("profile.create", {"persistence": "ephemeral"})["profile"]
                session = host.ok("session.open", {"profile": profile})["session"]
                target = host.ok("target.open", {"session": session, "url": f"{origin}/target.html"},
                                 deadline_ms=5000)["target"]
                line, _ = observe(host, target)
                steps = (line or "").split()
                expect(tag + "a custom event on the window is delivered once and removal stops it",
                       steps[:1] == ["custom"] and steps.count("custom") == 1,
                       {"steps": steps[:3]})
                expect(tag + "a duplicate listener is not de-duplicated and runs twice: a divergence",
                       steps.count("twice") == 2, {"twice": steps.count("twice")})
                expect(tag + "window.onload cleared with null reads null and does not fire",
                       "onload-null:yes" in steps and "onload-cleared-ran" not in steps,
                       {"steps": len(steps)})
                expect(tag + "window.onload is a property: assignable, replaceable, and readable",
                       "onload-is:function" in steps and "onload-second" in steps
                       and "onload-first" not in steps,
                       {"steps": len(steps)})
            finally:
                close(directory, host, label)

            # 5: a microtask queued in DOMContentLoaded runs before load.
            directory, host, label = group("microtask")
            try:
                profile = host.ok("profile.create", {"persistence": "ephemeral"})["profile"]
                session = host.ok("session.open", {"profile": profile})["session"]
                target = host.ok("target.open", {"session": session, "url": f"{origin}/microtask.html"},
                                 deadline_ms=5000)["target"]
                line, _ = observe(host, target)
                expect(tag + "a job queued in DOMContentLoaded runs before load begins",
                       (line or "").split() == ["dcl", "job", "load"],
                       {"steps": (line or "").split()})
            finally:
                close(directory, host, label)

            # 6: a throwing listener stops nothing.
            directory, host, label = group("throwing")
            try:
                profile = host.ok("profile.create", {"persistence": "ephemeral"})["profile"]
                session = host.ok("session.open", {"profile": profile})["session"]
                opened = host.call("target.open", {"session": session, "url": f"{origin}/throwing.html"},
                                   deadline_ms=5000)
                line, _ = observe(host, opened["result"]["target"]) if opened.get("ok") else (None, [])
                expect(tag + "a listener that throws stops neither the next listener nor the next step",
                       opened.get("ok") and (line or "").split() == ["second", "load"],
                       {"ok": opened.get("ok"), "steps": (line or "").split()})
            finally:
                close(directory, host, label)

            # 7: exactly once, and again for a new document.
            directory, host, label = group("once")
            try:
                profile = host.ok("profile.create", {"persistence": "ephemeral"})["profile"]
                session = host.ok("session.open", {"profile": profile})["session"]
                target = host.ok("target.open", {"session": session, "url": f"{origin}/order.html"},
                                 deadline_ms=5000)["target"]
                first, _ = observe(host, target)
                again, _ = observe(host, target)
                expect(tag + "the sequence does not run again when the same document is observed",
                       first == again and (first or "").count("load:") == 1,
                       {"stable": first == again})
                host.ok("target.reload", {"target": target}, deadline_ms=5000)
                reloaded, _ = observe(host, target)
                host.ok("target.navigate", {"target": target, "url": f"{origin}/order.html"},
                        deadline_ms=5000)
                navigated, _ = observe(host, target)
                expect(tag + "a reload and a navigation each run the whole sequence for the new document",
                       (reloaded or "").count("load:") == 1
                       and (navigated or "").count("load:") == 1
                       and reloaded == first and navigated == first,
                       {"reload_ok": reloaded == first, "navigate_ok": navigated == first})
            finally:
                close(directory, host, label)

            # 8: a lifecycle handler's runaway job is interrupted, atomically.
            directory, host, label = group("deadline")
            try:
                profile = host.ok("profile.create", {"persistence": "ephemeral"})["profile"]
                session = host.ok("session.open", {"profile": profile})["session"]
                opened = host.call("target.open", {"session": session, "url": f"{origin}/hang.html"},
                                   deadline_ms=2000, wall_ms=2500)
                listed = host.call("session.inspect", {"session": session},
                                   deadline_ms=1000, wall_ms=2000)
                targets = (listed.get("result") or {}).get("targets") or []
                expect(tag + "an infinite job from a lifecycle handler is interrupted and commits nothing",
                       (not opened.get("ok"))
                       and (opened.get("error") or {}).get("code") == "deadline_exceeded"
                       and listed.get("ok") and targets == [],
                       {"error": (opened.get("error") or {}).get("code"),
                        "targets": len(targets)})
            finally:
                close(directory, host, label)

            # 9: a child frame's lifecycle is observably inert.
            directory, host, label = group("children")
            try:
                profile = host.ok("profile.create", {"persistence": "ephemeral"})["profile"]
                session = host.ok("session.open", {"profile": profile})["session"]
                target = host.ok("target.open", {"session": session, "url": f"{origin}/parent.html"},
                                 deadline_ms=5000)["target"]
                frames = host.ok("target.inspect", {"target": target})["frames"]
                child = frames[1]["frame"] if len(frames) > 1 else None
                answer = host.call("target.snapshot",
                                   {"target": target, "format": "semantic",
                                    "max_bytes": 65536, "max_nodes": 64,
                                    "frame": child}) if child else {}
                texts = [n.get("name") or "" for n in answer["result"]["nodes"]
                         if n.get("role") == "text"] if answer.get("ok") else []
                # A frame's readyState has no control surface, so nothing here
                # claims the transition happened; only what is observable is
                # asserted (design §10.4).
                expect(tag + "a child frame exists and neither its script nor a lifecycle handler ran",
                       child is not None and texts
                       and all("child lifecycle ran" != t for t in texts),
                       {"texts": len(texts)})
            finally:
                close(directory, host, label)

            # 10 and 11: the revision, and the memory criteria.
            directory, host, label = group("memory")
            try:
                profile = host.ok("profile.create", {"persistence": "ephemeral"})["profile"]
                session = host.ok("session.open", {"profile": profile})["session"]
                empty = owner_bytes(host)
                target = host.ok("target.open", {"session": session, "url": f"{origin}/late.html"},
                                 deadline_ms=5000)["target"]
                state = host.ok("target.inspect", {"target": target})
                _, texts = observe(host, target)
                expect(tag + "what a lifecycle handler mutates is in the first snapshot and the revision",
                       any("built late" == t for t in texts) and state["revision"] > 0,
                       {"revision": state.get("revision"), "texts": len(texts)})
                host.ok("target.close", {"target": target})
                without = host.ok("target.open",
                                  {"session": session, "url": f"{origin}/listeners.html?keep=0"},
                                  deadline_ms=5000)["target"]
                baseline = owner_bytes(host)
                host.ok("target.close", {"target": without})
                with_listeners = host.ok("target.open",
                                         {"session": session, "url": f"{origin}/listeners.html?keep=1"},
                                         deadline_ms=5000)["target"]
                measured = owner_bytes(host)
                # M1b: this fixture's listeners, and this fixture only. The
                # infrastructure delta is M1a, a cross-build number reported
                # beside it, and neither bounds an arbitrary page (design §9.2).
                quiet = host.ok("target.open", {"session": session, "url": f"{origin}/quiet.html"},
                                deadline_ms=5000)["target"]
                quiet_total = owner_bytes(host)
                host.ok("target.close", {"target": quiet})
                expect(tag + f"M1: the frozen fixture's listeners cost at most {OWNER_BYTES} live owner bytes",
                       baseline is not None and measured is not None
                       and abs(measured - baseline) <= OWNER_BYTES,
                       {"listener_workload_bytes": None if measured is None else measured - baseline,
                        "listeners_in_fixture": 1,
                        "no_listener_owner_bytes": baseline,
                        "quiet_page_total_bytes": quiet_total})
                for _ in range(REPLACEMENTS):
                    host.ok("target.navigate",
                            {"target": with_listeners, "url": f"{origin}/listeners.html?keep=1"},
                            deadline_ms=5000)
                after = owner_bytes(host)
                expect(tag + f"M2: {REPLACEMENTS} replacements stay within the one-document baseline",
                       after is not None and measured is not None
                       and abs(after - measured) <= OWNER_BYTES,
                       {"owner_bytes": None if after is None else after - measured})
                host.ok("target.close", {"target": with_listeners})
                closed = owner_bytes(host)
                expect(tag + "M3: closing every target returns the owners exactly",
                       closed is not None and empty is not None and closed == empty,
                       {"owner_bytes": None if closed is None else closed - empty})
            finally:
                close(directory, host, label)
    finally:
        server.shutdown()

    receipt = {
        "court": "native-dom document lifecycle (control 0.0.2)",
        "host_sha256": hashlib.sha256(Path(args.binary).read_bytes()).hexdigest(),
        "criteria": {"owner_bytes": OWNER_BYTES, "replacements": REPLACEMENTS},
        "checks": checks,
        "checks_passed": sum(1 for c in checks if c["passed"]),
        "checks_total": len(checks),
        "passed": all(c["passed"] for c in checks) and not killed_hosts,
        "hosts_killed": killed_hosts,
        "limitations": [
            "design-frozen court: it fails until the bounded lifecycle exists",
            "four steps of an event loop, not an event loop: no defer/async ordering, no resource load, no pageshow, beforeunload or visibility",
            "a host that does not answer inside its wall-clock bound is killed by pid and reaped; that timeout is the falsification",
            "one hermetic loopback origin, macOS only; no surface, no window, no AppKit",
            "memory is judged by bounded owners and paired differentials, never an absolute footprint gate",
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
