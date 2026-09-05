#!/usr/bin/env python3
"""Candidate A: the page-facing view of an `Event` belongs to the realm that
can look at it.

Frozen before the code. On the build it was written on, every child realm
carries the ten accessors it can never read, and the divergence criterion
fails there.

What it asserts is the whole trade: a main realm keeps the view and its
values; a child realm has none of it; and a child's host actions, its
snapshots and the DOM's own reset — the one base path that still reads
`defaultPrevented` — all keep working without it.

Strictly headless: no surface, no window, no AppKit, one hermetic loopback
origin, both allocators, supervised hosts with the wall-clock kill.
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

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "protocol"))
import check_contract  # noqa: E402,F401

VISIBLE_ENV = "MINICON_SURF_ALLOW_VISIBLE_COURT"
# The ten that leave the base. `defaultPrevented` is not among them.
VIEW = ("isTrusted", "type", "bubbles", "cancelable", "composed", "target",
        "currentTarget", "eventPhase", "dispatching", "timeStamp")


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


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--binary", required=True)
    parser.add_argument("--receipt", required=True)
    args = parser.parse_args()
    if os.environ.get(VISIBLE_ENV):
        print(json.dumps({"passed": False, "reason": "the visible-court variable is set"}))
        return 1

    network = RETENTION.load_network_module()
    names = ",".join(f"'{name}'" for name in VIEW)

    class Handler(network.Handler):
        def do_GET(self):
            path, _, _query = self.path.partition("?")
            network.Handler.hits.append(path)
            if path == "/view.html":
                # The main realm's page reads the whole view back out of a
                # dispatch, so what is asserted is the values and not merely
                # the presence of ten names.
                return self.reply(200, (
                    "<!doctype html><html><body><main><p id=\"m\">start</p>"
                    "<p id=\"t\">target</p></main><script>"
                    "var t=document.getElementById('t');var seen='none';"
                    "t.addEventListener('probe', function(ev){"
                    "  seen=[String(ev.isTrusted),ev.type,String(ev.bubbles),"
                    "String(ev.cancelable),String(ev.composed),"
                    "String(ev.target===t),String(ev.currentTarget===t),"
                    "String(ev.eventPhase),String(ev.dispatching),"
                    "(typeof ev.timeStamp)].join('|'); });"
                    "t.dispatchEvent(new Event('probe',{bubbles:true,cancelable:true}));"
                    "var missing=[];var view=[" + names + "];"
                    "for (var i=0;i<view.length;i++){"
                    "  if (!(view[i] in Event.prototype)) missing.push(view[i]); }"
                    "document.getElementById('m').textContent="
                    "seen+'//'+(missing.length?missing.join(','):'none-missing');"
                    "</script></body></html>").encode())
            # A parent whose child carries a form with a reset button and a
            # checkbox: the child's actions, snapshot and the DOM's own reset.
            if path == "/parent.html":
                return self.reply(200, (
                    "<!doctype html><html><body><main><p>parent</p>"
                    "<iframe src=\"/child.html\"></iframe></main>"
                    "</body></html>").encode())
            if path == "/child.html":
                return self.reply(200, (
                    "<!doctype html><html><body><main>"
                    "<form><input id=\"c\" type=\"checkbox\" checked>"
                    "<input type=\"reset\" value=\"undo\"></form>"
                    "<p id=\"cm\">child text</p></main>"
                    "</body></html>").encode())
            return self.reply(404, b"gone")

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
            directory = tempfile.TemporaryDirectory(prefix="minicon-surf-view-")
            host = JOBS.Supervised(args.binary, directory.name, origin, allocator)
            try:
                def snapshot(target, nodes=64):
                    answer = host.call("target.snapshot",
                                       {"target": target, "format": "semantic",
                                        "max_bytes": 65536, "max_nodes": nodes})
                    return answer["result"] if answer.get("ok") else None

                profile = host.ok("profile.create", {"persistence": "ephemeral"})["profile"]
                session = host.ok("session.open", {"profile": profile})["session"]

                opened = host.call("target.open",
                                   {"session": session, "url": f"{origin}/view.html"},
                                   deadline_ms=8000)
                said = None
                if opened.get("ok"):
                    shot = snapshot(opened["result"]["target"])
                    texts = [n.get("name") for n in (shot or {}).get("nodes", [])
                             if n.get("role") == "text"]
                    said = texts[0] if texts else None
                    host.ok("target.close", {"target": opened["result"]["target"]})
                expect(tag + "a main realm keeps the whole page-facing view, and its values",
                       said == "false|probe|true|true|false|true|true|2|true|number"
                              "//none-missing",
                       {"said": said})

                # A child: its actions through the bridge, its snapshot, and
                # the DOM's own reset, which is the one base path still
                # reading defaultPrevented.
                opened = host.call("target.open",
                                   {"session": session, "url": f"{origin}/parent.html"},
                                   deadline_ms=8000)
                child_ok = opened.get("ok") is True
                target = opened["result"]["target"] if child_ok else None
                inspected = host.call("target.inspect", {"target": target}) if target else {}
                frames = (inspected.get("result") or {}).get("frames") or []
                # A frame entry is an object; the operations take its id.
                frame = frames[1]["frame"] if len(frames) > 1 else None
                shot = None
                if frame:
                    answer = host.call("target.snapshot",
                                       {"target": target, "frame": frame,
                                        "format": "semantic", "max_bytes": 65536,
                                        "max_nodes": 64})
                    shot = answer["result"] if answer.get("ok") else None
                nodes = (shot or {}).get("nodes") or []
                expect(tag + "a child realm still answers a snapshot, selectors and all",
                       bool(nodes) and any(n.get("role") == "text" for n in nodes),
                       {"nodes": len(nodes)})

                boxes = [n for n in nodes if n.get("role") in ("checkbox", "switch")]
                buttons = [n for n in nodes if n.get("role") == "button"]
                acted = None
                if boxes:
                    answer = host.call("target.act",
                                       {"target": target,
                                        "reference": boxes[0]["reference"],
                                        "action": {"kind": "set_checked", "checked": False}},
                                       deadline_ms=8000)
                    acted = (answer.get("result") or {}).get("applied") if answer.get("ok") \
                        else (answer.get("error") or {}).get("code")
                expect(tag + "a child's host action still applies through the bridge",
                       acted is True, {"acted": acted, "boxes": len(boxes)})

                # The reset button: the base's own click path raises an event
                # and reads defaultPrevented from it, in a child realm with no
                # page-facing view at all.
                # References are taken again after the action above moved the
                # revision: a stale one is refused, and a refusal here would
                # have looked like a reset that did not happen.
                if frame:
                    answer = host.call("target.snapshot",
                                       {"target": target, "frame": frame,
                                        "format": "semantic", "max_bytes": 65536,
                                        "max_nodes": 64})
                    buttons = [n for n in ((answer.get("result") or {}).get("nodes") or [])
                               if n.get("role") == "button"]
                undone = None
                if buttons:
                    answer = host.call("target.act",
                                       {"target": target,
                                        "reference": buttons[0]["reference"],
                                        "action": {"kind": "click"}}, deadline_ms=8000)
                    if answer.get("ok"):
                        answer = host.call("target.snapshot",
                                           {"target": target, "frame": frame,
                                            "format": "semantic", "max_bytes": 65536,
                                            "max_nodes": 64})
                        after = (answer.get("result") or {}).get("nodes") or []
                        boxes_after = [n for n in after
                                       if n.get("role") in ("checkbox", "switch")]
                        undone = boxes_after[0].get("checked") if boxes_after else None
                expect(tag + "and the DOM's own reset still reads its own cancellation",
                       undone is True, {"checked_after_reset": undone})
                if target:
                    host.ok("target.close", {"target": target})
            finally:
                if host.timeouts:
                    killed_hosts.append({"group": f"view-{allocator}",
                                         "allocator": allocator, "timeouts": host.timeouts})
                host.finish()
                directory.cleanup()

        # The divergence itself, read through the court-only realm probe.
        probe_directory = tempfile.TemporaryDirectory(prefix="minicon-surf-view-probe-")
        probe_file = Path(probe_directory.name) / "court.ndjson"
        closed = subprocess.run(
            [args.binary, "serve", "--stdio", "--fixture-root", str(RETENTION.FIXTURE_ROOT),
             "--config-dir", str(Path(probe_directory.name) / "closed"),
             "--allow-origin", origin, "--court-realm-probe", "1"],
            input="", capture_output=True, text=True, timeout=30,
            env={k: v for k, v in os.environ.items() if k != VISIBLE_ENV})
        expect("the realm probe is refused without the private court file",
               closed.returncode != 0 and not closed.stdout.strip(),
               {"code": closed.returncode})
        directory = tempfile.TemporaryDirectory(prefix="minicon-surf-view-divergence-")
        host = JOBS.Supervised(args.binary, directory.name, origin, "system",
                               extra=("--court-realm-probe", "1",
                                      "--surface-court-file", str(probe_file)))
        try:
            answer = host.call("profile.create", {"persistence": "ephemeral"})
            if not answer.get("ok"):
                expect("the host accepts the court-only realm probe", False,
                       {"reason": (answer.get("error") or {}).get("code", "refused")})
            else:
                profile = answer["result"]["profile"]
                session = host.ok("session.open", {"profile": profile})["session"]
                opened = host.call("target.open",
                                   {"session": session, "url": f"{origin}/parent.html"},
                                   deadline_ms=8000)
                report = host.call("memory.report", {})
                probe = ((report.get("result") or {}).get("owners") or {}).get("realm_probe") or {}
                expect("the main realm has the page-facing view and every child realm has none",
                       opened.get("ok") is True
                       and probe.get("main_event_view") is True
                       and probe.get("children_event_view") == 0
                       and probe.get("realms_probed", 0) >= 2,
                       {"probe": probe})
                if opened.get("ok"):
                    host.ok("target.close", {"target": opened["result"]["target"]})
        finally:
            if host.timeouts:
                killed_hosts.append({"group": "divergence", "allocator": "system",
                                     "timeouts": host.timeouts})
            host.finish()
            directory.cleanup()
            expect("the probe seam's court file is gone when the host is",
                   not probe_file.exists(), {"court_file": probe_file.exists()})
            probe_directory.cleanup()
    finally:
        server.shutdown()

    receipt = {
        "court": "native-dom page-facing Event view (control 0.0.2)",
        "host_sha256": hashlib.sha256(Path(args.binary).read_bytes()).hexdigest(),
        "view": list(VIEW),
        "checks": checks,
        "checks_passed": sum(1 for c in checks if c["passed"]),
        "checks_total": len(checks),
        "passed": all(c["passed"] for c in checks) and not killed_hosts,
        "hosts_killed": killed_hosts,
        "limitations": [
            "design-frozen court: the divergence criterion fails until the accessors move",
            "the M1 and M2 floors are measured by the child-frame and shim-footprint courts on the same binary",
            "a child realm's absence of the view is read through the court-only probe, because a child has no eval surface by design",
            "one hermetic loopback origin, macOS only; no surface, no window, no AppKit",
        ],
    }
    Path(args.receipt).write_text(json.dumps(receipt, indent=1, sort_keys=True) + "\n")
    print(json.dumps({"passed": receipt["passed"], "checks_passed": receipt["checks_passed"],
                      "checks_total": receipt["checks_total"],
                      "hosts_killed": len(killed_hosts)}))
    for check in checks:
        if not check["passed"]:
            print("FAIL " + json.dumps(check))
    return 0 if receipt["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
