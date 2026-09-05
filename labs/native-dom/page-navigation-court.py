#!/usr/bin/env python3
"""The frozen court for page-initiated navigation.

Frozen from `page-navigation-design-0.0.1.md` §10 and §11 before the host
changes, and run against the pushed build first, where the defect it is about
— an assignment that succeeds and commits nothing — makes it fail.

A page here can queue work that never returns, so every host is supervised as
the job-deadline court's are: each request on a worker with an absolute
wall-clock limit, and a host that misses it killed by its exact pid, reaped,
and recorded as a timeout, which is the falsification rather than a wait.

Strictly headless: no surface binary, no window, no AppKit, and it refuses to
run with the visible-court variable set. One hermetic loopback origin, both
allocators, a fresh host per group.

Groups: the lie, assign and replace, reload, last write wins, the build
chain, boundaries, live-realm failure, caller override, the empty slot,
secrecy, memory.
"""

import argparse
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
CHAIN_CAP = 3
OWNER_BYTES = 65536
NAVIGATIONS = 128
# More page navigations than the audit ring holds, so eviction is observed.
AUDIT_LOOP = 40


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


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--binary", required=True)
    parser.add_argument("--receipt", required=True)
    args = parser.parse_args()
    if os.environ.get(VISIBLE_ENV):
        print(json.dumps({"passed": False, "reason": "the visible-court variable is set"}))
        return 1

    network = RETENTION.load_network_module()
    requested = []
    # A reload rebuilds the document from scratch, so no in-page flag can make
    # one one-shot: the second response for this path carries no script, and
    # the court measures a single rebuild rather than the chain cap (§13).
    reload_served = []

    class Handler(network.Handler):
        def do_GET(self):
            path, _, query = self.path.partition("?")
            network.Handler.hits.append(path)
            requested.append(path)
            # A chain of documents, each assigning the next during its build.
            if path.startswith("/chain-"):
                step = int(path.rsplit("-", 1)[1].partition(".")[0])
                return self.reply(200, page(f"Chain {step}",
                                            f"location.href='/chain-{step + 1}.html';"))
            if path == "/reload-call.html":
                first = not reload_served
                reload_served.append(path)
                return self.reply(200, page(
                    "Reload call",
                    "document.addEventListener('DOMContentLoaded',function(){"
                    "location.reload();});" if first else "write('rebuilt');"))
            pages = {
                # The defect itself: an assignment that must commit.
                "/assign-href.html": page("Assign href",
                                          "document.addEventListener('DOMContentLoaded',"
                                          "function(){location.href='/landed.html';});"),
                "/assign-call.html": page("Assign call",
                                          "document.addEventListener('DOMContentLoaded',"
                                          "function(){location.assign('/landed.html');});"),
                "/replace-call.html": page("Replace call",
                                           "document.addEventListener('DOMContentLoaded',"
                                           "function(){location.replace('/landed.html');});"),

                # Three writes in one turn: only the last may be fetched.
                "/last-write.html": page("Last write",
                                         "document.addEventListener('DOMContentLoaded',function(){"
                                         "location.href='/never-a.html';"
                                         "location.assign('/never-b.html');"
                                         "location.replace('/landed.html');});"),
                # The getter must answer the committed document until a commit.
                "/getter.html": page("Getter",
                                     "document.addEventListener('DOMContentLoaded',function(){"
                                     "var before=String(location.href);"
                                     "location.href='/landed.html';"
                                     "write('same:'+(String(location.href)===before?'yes':'no'));});"),
                # An intent from an action handler, and one that must fail.
                "/handler.html": page("Handler",
                                      "document.getElementById('go').addEventListener('click',"
                                      "function(){write('handler ran');"
                                      "location.href='/landed.html';});",
                                      '<button id="go" type="button">go</button>'),
                "/handler-fail.html": page("Handler fail",
                                           "document.getElementById('go').addEventListener('click',"
                                           "function(){write('handler ran');"
                                           "location.href='/absent.html';});",
                                           '<button id="go" type="button">go</button>'),
                # An intent raised inside a lifecycle step, and after it.
                "/late-intent.html": page("Late intent",
                                          "setTimeout(function(){location.href='/landed.html';},0);"),
                # 17.2: two addresses the realm and the host must bound. One
                # is over the character bound; the other is under it and over
                # the byte bound, because each character is three bytes.
                "/oversize-intent.html": page(
                    "Oversize",
                    "document.addEventListener('DOMContentLoaded',function(){"
                    "location.href='/' + new Array(3001).join('z') + '.html';});"),
                # This one is raised from a timer, so it is refused on the
                # live path rather than during the build.
                "/wide-late.html": page(
                    "Wide",
                    "setTimeout(function(){"
                    "location.href='/' + new Array(801).join('\u4e2d') + '.html';},0);"),
                "/landed.html": page("Landed", ""),
                "/quiet.html": page("Quiet", ""),
            }
            if path in pages:
                return self.reply(200, pages[path])
            if path == "/absent.html":
                return self.reply(404, b"gone")
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

            def group(label, extra=()):
                directory = tempfile.TemporaryDirectory(prefix="minicon-surf-pagenav-")
                host = JOBS.Supervised(args.binary, directory.name, origin, allocator, extra=extra)
                return directory, host, label

            def close(directory, host, label):
                if host.timeouts:
                    killed_hosts.append({"group": label, "allocator": allocator,
                                         "timeouts": host.timeouts})
                host.finish()
                directory.cleanup()

            def open_page(host, session, path, deadline_ms=5000):
                answer = host.call("target.open", {"session": session, "url": f"{origin}{path}"},
                                   deadline_ms=deadline_ms)
                return answer

            def state(host, target):
                answer = host.call("target.inspect", {"target": target})
                return answer["result"] if answer.get("ok") else None

            def counters(host):
                answer = host.call("memory.report", {})
                if not answer.get("ok"):
                    return {}
                return answer["result"]["owners"].get("navigation_intents") or {}

            def owner_bytes(host):
                answer = host.call("memory.report", {})
                if not answer.get("ok"):
                    return None
                owners = answer["result"]["owners"]
                return owners["script_realms"]["malloc_bytes"] + owners["targets"]["fixture_bytes"]

            # 1, 2, 3: the lie is gone, and each form does what it says.
            directory, host, label = group("forms")
            try:
                profile = host.ok("profile.create", {"persistence": "ephemeral"})["profile"]
                session = host.ok("session.open", {"profile": profile})["session"]
                for path, kind in (("/assign-href.html", "the href setter"),
                                   ("/assign-call.html", "assign"),
                                   ("/replace-call.html", "replace")):
                    answer = open_page(host, session, path)
                    live = state(host, answer["result"]["target"]) if answer.get("ok") else None
                    expect(tag + f"{kind} commits the document it names",
                           live is not None and (live.get("url") or "").endswith("/landed.html"),
                           {"tail": (live or {}).get("url", "").rsplit("/", 1)[-1]})
                    # Every one of these is raised during the lifecycle, so all
                    # three are replace-like and none adds an entry (§11.2).
                    # The bounded history reports a length and a position, not
                    # an entry list: a replace-like intent leaves both alone.
                    expect(tag + f"{kind} during the lifecycle adds no history entry",
                           live is not None and (live.get("history") or {}).get("length") == 1
                           and (live.get("history") or {}).get("position") == 0,
                           {"history": (live or {}).get("history")})
                    if answer.get("ok"):
                        host.ok("target.close", {"target": answer["result"]["target"]})
                del reload_served[:]
                del requested[:]
                answer = open_page(host, session, "/reload-call.html")
                live = state(host, answer["result"]["target"]) if answer.get("ok") else None
                expect(tag + "reload rebuilds the same URL and leaves history alone",
                       live is not None and (live.get("url") or "").endswith("/reload-call.html")
                       and requested.count("/reload-call.html") == 2
                       and (live.get("history") or {}).get("length") == 1
                       and (live.get("history") or {}).get("position") == 0,
                       {"history": (live or {}).get("history"),
                        "fetches": requested.count("/reload-call.html")})
            finally:
                close(directory, host, label)

            # 4: last write wins, and nothing else is fetched.
            directory, host, label = group("last-write")
            try:
                profile = host.ok("profile.create", {"persistence": "ephemeral"})["profile"]
                session = host.ok("session.open", {"profile": profile})["session"]
                del requested[:]
                answer = open_page(host, session, "/last-write.html")
                live = state(host, answer["result"]["target"]) if answer.get("ok") else None
                expect(tag + "the last write of three decides the destination",
                       live is not None and (live.get("url") or "").endswith("/landed.html"),
                       {"tail": (live or {}).get("url", "").rsplit("/", 1)[-1]})
                expect(tag + "and the two it replaced are never fetched",
                       "/never-a.html" not in requested and "/never-b.html" not in requested,
                       {"requested": len(requested)})
            finally:
                close(directory, host, label)

            # 5: the build chain, its cap, and no observable intermediate.
            directory, host, label = group("chain")
            try:
                profile = host.ok("profile.create", {"persistence": "ephemeral"})["profile"]
                session = host.ok("session.open", {"profile": profile})["session"]
                # chain-1 assigns chain-2 assigns chain-3 assigns chain-4: four
                # links, one over the cap.
                answer = open_page(host, session, "/chain-1.html")
                expect(tag + f"a script chain longer than {CHAIN_CAP} is refused and commits nothing",
                       (not answer.get("ok"))
                       and (answer.get("error") or {}).get("code") == "resource_limit"
                       and (answer.get("error") or {}).get("details", {}).get("reason")
                       == "navigation_chain_limit",
                       {"error": (answer.get("error") or {}).get("code")})
                listed = host.call("session.inspect", {"session": session})
                targets = (listed.get("result") or {}).get("targets") or []
                expect(tag + "no intermediate document of a refused chain exists",
                       listed.get("ok") and targets == [], {"targets": len(targets)})
            finally:
                close(directory, host, label)

            # 6: boundaries — the getter, and an intent from a timer.
            directory, host, label = group("boundaries")
            try:
                profile = host.ok("profile.create", {"persistence": "ephemeral"})["profile"]
                session = host.ok("session.open", {"profile": profile})["session"]
                answer = open_page(host, session, "/getter.html")
                live = state(host, answer["result"]["target"]) if answer.get("ok") else None
                observed = host.call("target.snapshot",
                                     {"target": answer["result"]["target"], "format": "semantic",
                                      "max_bytes": 65536, "max_nodes": 32}) if answer.get("ok") else {}
                texts = [n.get("name") or "" for n in observed["result"]["nodes"]
                         if n.get("role") == "text"] if observed.get("ok") else []
                expect(tag + "the href getter answers the committed URL until a commit",
                       live is not None and (live.get("url") or "").endswith("/landed.html"),
                       {"tail": (live or {}).get("url", "").rsplit("/", 1)[-1], "texts": len(texts)})
                if answer.get("ok"):
                    host.ok("target.close", {"target": answer["result"]["target"]})
                answer = open_page(host, session, "/late-intent.html")
                target = answer["result"]["target"] if answer.get("ok") else None
                before = state(host, target) if target else None
                after = state(host, target) if target else None
                # This one is raised after the lifecycle, from a timer, so the
                # href setter adds an entry: length two, position one.
                expect(tag + "an intent from a timer commits at the boundary that ran it and adds an entry",
                       after is not None and (after.get("url") or "").endswith("/landed.html")
                       and (after.get("history") or {}).get("length") == 2
                       and (after.get("history") or {}).get("position") == 1,
                       {"tail": (after or {}).get("url", "").rsplit("/", 1)[-1],
                        "history": (after or {}).get("history")})
            finally:
                close(directory, host, label)

            # 7: an action handler's intent, committed and failed.
            directory, host, label = group("handler")
            try:
                profile = host.ok("profile.create", {"persistence": "ephemeral"})["profile"]
                session = host.ok("session.open", {"profile": profile})["session"]
                answer = open_page(host, session, "/handler.html")
                target = answer["result"]["target"] if answer.get("ok") else None
                observed = host.call("target.snapshot",
                                     {"target": target, "format": "semantic",
                                      "max_bytes": 65536, "max_nodes": 32}) if target else {}
                button = next((n["reference"] for n in observed["result"]["nodes"]
                               if n.get("role") == "button"), None) if observed.get("ok") else None
                acted = host.call("target.act", {"target": target, "reference": button,
                                                 "action": {"kind": "click"}}) if button else {}
                live = state(host, target) if target else None
                expect(tag + "an intent from an action handler answers the navigation-shaped result",
                       acted.get("ok") and acted["result"].get("navigated") is True
                       and (acted["result"].get("url") or "").endswith("/landed.html")
                       and live is not None and (live.get("url") or "").endswith("/landed.html"),
                       {"navigated": acted.get("result", {}).get("navigated")})
                if target:
                    host.ok("target.close", {"target": target})
                answer = open_page(host, session, "/handler-fail.html")
                target = answer["result"]["target"] if answer.get("ok") else None
                before = state(host, target) if target else None
                observed = host.call("target.snapshot",
                                     {"target": target, "format": "semantic",
                                      "max_bytes": 65536, "max_nodes": 32}) if target else {}
                button = next((n["reference"] for n in observed["result"]["nodes"]
                               if n.get("role") == "button"), None) if observed.get("ok") else None
                failed = host.call("target.act", {"target": target, "reference": button,
                                                  "action": {"kind": "click"}}) if button else {}
                after = state(host, target) if target else None
                marks = []
                snap = host.call("target.snapshot",
                                 {"target": target, "format": "semantic",
                                  "max_bytes": 65536, "max_nodes": 32}) if target else {}
                if snap.get("ok"):
                    marks = [n.get("name") or "" for n in snap["result"]["nodes"]
                             if n.get("role") == "text"]
                expect(tag + "a failed intent answers the typed failure and keeps every identity",
                       (not failed.get("ok"))
                       and before is not None and after is not None
                       and after["url"] == before["url"]
                       and after["frames"] == before["frames"]
                       and after["history"] == before["history"],
                       {"error": (failed.get("error") or {}).get("code")})
                expect(tag + "the handler's mutation stands and no address is in the failure",
                       any("handler ran" == m for m in marks)
                       and "absent.html" not in json.dumps(failed.get("error") or {}),
                       {"marks": len(marks)})
            finally:
                close(directory, host, label)

            # 8 and 9: the caller wins, and the slot is empty between operations.
            # The seam is doubly constrained: it is accepted only inside the
            # private court mechanism, so the court must also hand the host a
            # court file, and that file must be gone when the host is (§14).
            seam_directory = tempfile.TemporaryDirectory(prefix="minicon-surf-pagenav-seam-")
            seam_file = Path(seam_directory.name) / "court.ndjson"
            # A host asked to hold an intent without the private court file
            # must refuse before it serves anything: this is the fail-closed
            # falsifier, and it fails if a plain host ever accepts the knob.
            closed = subprocess.run(
                [args.binary, "serve", "--stdio", "--fixture-root", str(RETENTION.FIXTURE_ROOT),
                 "--config-dir", str(Path(seam_directory.name) / "closed"),
                 "--allow-origin", origin, "--court-hold-intent", "1"],
                input="", capture_output=True, text=True, timeout=30,
                env={k: v for k, v in os.environ.items() if k != VISIBLE_ENV})
            expect(tag + "the seam is refused without the private court file",
                   closed.returncode != 0 and not closed.stdout.strip(),
                   {"code": closed.returncode, "answered": len(closed.stdout)})
            directory, host, label = group("caller-override",
                                           extra=("--court-hold-intent", "1",
                                                  "--surface-court-file", str(seam_file)))
            try:
                answer = host.call("profile.create", {"persistence": "ephemeral"})
                if not answer.get("ok"):
                    expect(tag + "the host accepts the court-only held-intent seam", False,
                           {"reason": (answer.get("error") or {}).get("code", "refused")})
                else:
                    profile = answer["result"]["profile"]
                    session = host.ok("session.open", {"profile": profile})["session"]
                    opened = open_page(host, session, "/late-intent.html")
                    target = opened["result"]["target"] if opened.get("ok") else None
                    # `memory.report` is not a timer boundary, so the intent
                    # this page raises from a timer needs one observation to
                    # exist at all; the seam then holds what that boundary
                    # would have consumed (§14).
                    if target:
                        state(host, target)
                    before = counters(host)
                    if target:
                        host.ok("target.navigate", {"target": target, "url": f"{origin}/quiet.html"},
                                deadline_ms=5000)
                    after = counters(host)
                    live = state(host, target) if target else None
                    expect(tag + "an explicit navigation discards the pending intent and wins",
                           live is not None and (live.get("url") or "").endswith("/quiet.html")
                           and after.get("discarded_total", 0) == before.get("discarded_total", 0) + 1
                           and after.get("last_cause") == "caller_override",
                           {"discarded": after.get("discarded_total"),
                            "cause": after.get("last_cause")})
                    expect(tag + "and the discarded address is nowhere in the counters",
                           "landed" not in json.dumps(after) and "http" not in json.dumps(after),
                           {"counters": sorted(after)})
                    expect(tag + "the held intent was pending, and the seam is named nowhere",
                           before.get("pending") == 1 and after.get("pending") == 0
                           and "hold" not in json.dumps(state(host, target) or {}),
                           {"pending": [before.get("pending"), after.get("pending")]})
            finally:
                close(directory, host, label)
                # The private court file belongs to the host's life, not to the
                # court's: when the host is gone, so is the file.
                expect(tag + "the private court file is gone when the host is",
                       not seam_file.exists(), {"court_file": seam_file.exists()})
                seam_directory.cleanup()

            # 12: the address bounds, at both ends (§17.2).
            directory, host, label = group("bounds")
            try:
                profile = host.ok("profile.create", {"persistence": "ephemeral"})["profile"]
                session = host.ok("session.open", {"profile": profile})["session"]
                del requested[:]
                answer = open_page(host, session, "/oversize-intent.html")
                error = answer.get("error") or {}
                expect(tag + "an over-length address raised during a build is refused for one fixed reason",
                       (not answer.get("ok")) and error.get("code") == "invalid_request"
                       and error.get("details", {}).get("reason") == "navigation_url",
                       {"error": error.get("code"),
                        "reason": error.get("details", {}).get("reason")})
                expect(tag + "and no part of it is in the failure, and none of it was fetched",
                       "zzz" not in json.dumps(error)
                       and not any("zzz" in path for path in requested),
                       {"requested": len(requested)})
                del requested[:]
                opened = open_page(host, session, "/wide-late.html")
                target = opened["result"]["target"] if opened.get("ok") else None
                # The boundary that runs the timer is the one that must refuse:
                # this address is under the realm's character bound and over
                # the host's byte bound, because each character is three bytes.
                probe = host.call("target.inspect", {"target": target}) if target else {}
                error = probe.get("error") or {}
                expect(tag + "a non-ASCII address over the byte bound is refused on the live path",
                       (not probe.get("ok")) and error.get("code") == "invalid_request"
                       and error.get("details", {}).get("reason") == "navigation_url",
                       {"error": error.get("code"),
                        "reason": error.get("details", {}).get("reason")})
                live = state(host, target) if target else None
                counts = counters(host)
                expect(tag + "the refused document keeps its address, and nothing of the page's is anywhere",
                       live is not None and (live.get("url") or "").endswith("/wide-late.html")
                       and (live.get("history") or {}).get("length") == 1
                       and "\u4e2d" not in json.dumps(error)
                       and "\u4e2d" not in json.dumps(counts)
                       and not any("%E4" in path or "\u4e2d" in path for path in requested),
                       {"history": (live or {}).get("history"), "requested": len(requested)})
            finally:
                close(directory, host, label)

            # 13: a take that fails is a failure, and never a later commit
            # (§17.1). The seam is constrained exactly like the hold seam.
            break_directory = tempfile.TemporaryDirectory(prefix="minicon-surf-pagenav-break-")
            break_file = Path(break_directory.name) / "court.ndjson"
            closed = subprocess.run(
                [args.binary, "serve", "--stdio", "--fixture-root", str(RETENTION.FIXTURE_ROOT),
                 "--config-dir", str(Path(break_directory.name) / "closed"),
                 "--allow-origin", origin, "--court-break-intent-take", "1"],
                input="", capture_output=True, text=True, timeout=30,
                env={k: v for k, v in os.environ.items() if k != VISIBLE_ENV})
            expect(tag + "the break seam is refused without the private court file",
                   closed.returncode != 0 and not closed.stdout.strip(),
                   {"code": closed.returncode, "answered": len(closed.stdout)})
            directory, host, label = group("bridge-failure",
                                           extra=("--court-break-intent-take", "1",
                                                  "--surface-court-file", str(break_file)))
            try:
                answer = host.call("profile.create", {"persistence": "ephemeral"})
                if not answer.get("ok"):
                    expect(tag + "the host accepts the court-only broken-take seam", False,
                           {"reason": (answer.get("error") or {}).get("code", "refused")})
                else:
                    profile = answer["result"]["profile"]
                    session = host.ok("session.open", {"profile": profile})["session"]
                    opened = open_page(host, session, "/late-intent.html")
                    target = opened["result"]["target"] if opened.get("ok") else None
                    # The open's own result is the "before": observing through
                    # a boundary would spend the one-shot seam.
                    before = opened.get("result") if opened.get("ok") else None
                    # This boundary runs the timer, so an intent is raised, and
                    # the take of it answers malformed output without having
                    # evaluated anything: the slot is left full.
                    probe = host.call("target.inspect", {"target": target}) if target else {}
                    error = probe.get("error") or {}
                    expect(tag + "a take that fails answers a typed failure instead of succeeding",
                           (not probe.get("ok")) and error.get("code") == "internal"
                           and error.get("details", {}).get("reason") == "intent_bridge_failed",
                           {"error": error.get("code"),
                            "reason": error.get("details", {}).get("reason")})
                    counts_before = counters(host)
                    after = state(host, target) if target else None
                    counts_after = counters(host)
                    expect(tag + "and the stale intent is discarded, never committed at a later boundary",
                           after is not None
                           and (after.get("url") or "").endswith("/late-intent.html")
                           and (after.get("history") or {}).get("length") == 1
                           and counts_after.get("discarded_total", 0)
                           == counts_before.get("discarded_total", 0) + 1
                           and counts_after.get("last_cause") == "bridge_failure",
                           {"tail": (after or {}).get("url", "").rsplit("/", 1)[-1],
                            "discarded": counts_after.get("discarded_total"),
                            "cause": counts_after.get("last_cause")})
                    # A target that was poisoned and then emptied is a
                    # working target, not a bricked one: it answers, and it is
                    # still the document the caller opened.
                    observed = host.call("target.snapshot",
                                         {"target": target, "format": "semantic",
                                          "max_bytes": 65536, "max_nodes": 32}) if target else {}
                    expect(tag + "and the document the caller opened still works after it",
                           before is not None and after is not None
                           and before.get("url") == after.get("url")
                           and observed.get("ok") is True,
                           {"url": (after or {}).get("url", "").rsplit("/", 1)[-1],
                            "snapshot": observed.get("ok")})
            finally:
                close(directory, host, label)
                expect(tag + "the break seam's court file is gone when the host is",
                       not break_file.exists(), {"court_file": break_file.exists()})
                break_directory.cleanup()

            # 14: the audit §5 promised, present and still bounded (§17.3).
            directory, host, label = group("audit")
            try:
                profile = host.ok("profile.create", {"persistence": "ephemeral"})["profile"]
                session = host.ok("session.open", {"profile": profile})["session"]
                opened = open_page(host, session, "/assign-href.html")
                target = opened["result"]["target"] if opened.get("ok") else None
                audit = host.call("session.inspect", {"session": session})
                entries = ((audit.get("result") or {}).get("audit") or {}).get("entries") or []
                committed = [e for e in entries
                             if str(e.get("operation", "")).startswith("page.navigate.")]
                expect(tag + "a committed intent leaves one bounded page-navigation record",
                       any(e.get("operation") == "page.navigate.assign"
                           and e.get("outcome") == "committed"
                           and e.get("origin") == origin for e in committed),
                       {"records": [e.get("operation") for e in committed]})
                blob = json.dumps(entries)
                expect(tag + "and it names an origin, never a path, a query or page text",
                       "landed.html" not in blob and "assign-href" not in blob
                       and "?" not in blob,
                       {"ledger_bytes": len(blob)})
                if target:
                    host.ok("target.close", {"target": target})
                opened = open_page(host, session, "/wide-late.html")
                refused = opened["result"]["target"] if opened.get("ok") else None
                if refused:
                    host.call("target.inspect", {"target": refused})
                audit = host.call("session.inspect", {"session": session})
                entries = ((audit.get("result") or {}).get("audit") or {}).get("entries") or []
                expect(tag + "a refused intent leaves a record with an outcome and no origin",
                       any(str(e.get("operation", "")).startswith("page.navigate.")
                           and e.get("outcome") == "invalid_request"
                           and e.get("origin") is None for e in entries),
                       {"outcomes": sorted({e.get("outcome") for e in entries})})
                if refused:
                    host.ok("target.close", {"target": refused})
                # More page navigations than the ring holds: it still evicts.
                opened = open_page(host, session, "/assign-href.html")
                loop = opened["result"]["target"] if opened.get("ok") else None
                for _ in range(AUDIT_LOOP if loop else 0):
                    host.ok("target.navigate",
                            {"target": loop, "url": f"{origin}/assign-href.html"},
                            deadline_ms=5000)
                audit = host.call("session.inspect", {"session": session})
                ledger = (audit.get("result") or {}).get("audit") or {}
                expect(tag + "and the ring still holds exactly what it may and drops the rest",
                       ledger.get("count") == ledger.get("limit")
                       and ledger.get("dropped_total", 0) > 0
                       and "landed.html" not in json.dumps(ledger.get("entries") or []),
                       {"count": ledger.get("count"), "dropped": ledger.get("dropped_total")})
            finally:
                close(directory, host, label)

            # 9b, 10, 11: the empty slot, secrecy, and the owners.
            directory, host, label = group("owners")
            try:
                profile = host.ok("profile.create", {"persistence": "ephemeral"})["profile"]
                session = host.ok("session.open", {"profile": profile})["session"]
                empty = owner_bytes(host)
                answer = open_page(host, session, "/assign-href.html")
                target = answer["result"]["target"] if answer.get("ok") else None
                pending = counters(host).get("pending")
                expect(tag + "the slot is empty at the end of every operation",
                       pending == 0, {"pending": pending})
                for _ in range(NAVIGATIONS if target else 0):
                    host.ok("target.navigate", {"target": target, "url": f"{origin}/assign-href.html"},
                            deadline_ms=5000)
                after = owner_bytes(host)
                one = owner_bytes(host)
                expect(tag + f"{NAVIGATIONS} intent-driven navigations stay within the bound",
                       after is not None and one is not None and abs(after - one) <= OWNER_BYTES,
                       {"owner_bytes": None if after is None else after - one})
                if target:
                    host.ok("target.close", {"target": target})
                closed = owner_bytes(host)
                expect(tag + "closing every target returns the owners exactly",
                       closed is not None and empty is not None and closed == empty,
                       {"owner_bytes": None if closed is None else closed - empty})
                audit = host.call("session.inspect", {"session": session})
                blob = json.dumps(audit.get("result") or {})
                expect(tag + "no intent URL, query or page text is in the ledger",
                       audit.get("ok") and "landed.html" not in blob and "never-a" not in blob,
                       {"audit_bytes": len(blob)})
            finally:
                close(directory, host, label)
    finally:
        server.shutdown()

    receipt = {
        "court": "native-dom page-initiated navigation (control 0.0.2)",
        "host_sha256": hashlib.sha256(Path(args.binary).read_bytes()).hexdigest(),
        "criteria": {"chain_cap": CHAIN_CAP, "owner_bytes": OWNER_BYTES,
                     "navigations": NAVIGATIONS, "audit_loop": AUDIT_LOOP},
        "checks": checks,
        "checks_passed": sum(1 for c in checks if c["passed"]),
        "checks_total": len(checks),
        "passed": all(c["passed"] for c in checks) and not killed_hosts,
        "hosts_killed": killed_hosts,
        "limitations": [
            "design-frozen court: it fails until page-initiated navigation exists",
            "href, assign, replace and no-argument reload only; no history API, no hash navigation",
            "every intent raised during a build or the lifecycle is replace-like, because this host has no activation model",
            "caller_override is reachable only through a court-only seam: in production the slot is empty at the end of every operation",
            "a host that does not answer inside its wall-clock bound is killed by pid and reaped; that timeout is the falsification",
            "one hermetic loopback origin, macOS only; no surface, no window, no AppKit",
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
