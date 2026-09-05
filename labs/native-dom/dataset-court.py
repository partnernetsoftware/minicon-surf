#!/usr/bin/env python3
"""`dataset` belongs to the realm that reads it, and to the elements a page
actually touches.

Frozen before the code. The build it was written on allocates a `Proxy`, its
handler and three closures in every `Element` constructor, in every realm, for
an API no host script names — so its per-element gate fails there.

This court measures a cost that scales with element count, which no other
court does: two child documents of different sizes in the same host, and the
marginal bytes per node between them.

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
SMALL = 16
LARGE = 112
# Measured 2,082.6 bytes per child node with the Proxy in the constructor and
# 1,250.6 with the accessor lazy. The gate sits between them, with margin on
# both sides, and it is a gate on the marginal cost of a node — not on a total
# any other court already holds.
PER_NODE_GATE = 1600


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


def nodes_document(count):
    return ("<!doctype html><html><body><main>"
            + "".join(f'<p id="n{i}">node {i}</p>' for i in range(count))
            + "</main></body></html>").encode()


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
            if path == f"/child-{SMALL}.html":
                return self.reply(200, nodes_document(SMALL))
            if path == f"/child-{LARGE}.html":
                return self.reply(200, nodes_document(LARGE))
            if path.startswith("/parent-"):
                count = path[len("/parent-"):-len(".html")]
                return self.reply(200, (
                    "<!doctype html><html><body><main>"
                    f'<iframe src="/child-{count}.html"></iframe>'
                    "</main></body></html>").encode())
            if path == "/page.html":
                # A main realm reads and writes data-* through the accessor,
                # and asks whether the view it gets is the same one twice.
                return self.reply(200, (
                    "<!doctype html><html><body><main><p id=\"m\">start</p>"
                    "<p id=\"t\" data-court-name=\"alpha\" data-plain=\"beta\">t</p>"
                    "</main><script>"
                    "var t=document.getElementById('t');var out=[];"
                    "out.push(t.dataset.courtName==='alpha');"
                    "out.push(t.dataset.plain==='beta');"
                    "out.push(('courtName' in t.dataset));"
                    "out.push(!('absent' in t.dataset));"
                    "t.dataset.newOne='gamma';"
                    "out.push(t.getAttribute('data-new-one')==='gamma');"
                    "out.push(t.dataset===t.dataset);"
                    "out.push(t.dataset.missing===undefined);"
                    "document.getElementById('m').textContent=out.join(',');"
                    "</script></body></html>").encode())
            if path == "/reset-child.html":
                return self.reply(200, (
                    "<!doctype html><html><body><main>"
                    "<form><input id=\"c\" type=\"checkbox\" checked>"
                    "<input type=\"reset\" value=\"undo\"></form>"
                    "<p id=\"cm\">child text</p></main></body></html>").encode())
            if path == "/reset-parent.html":
                return self.reply(200, (
                    "<!doctype html><html><body><main><p>parent</p>"
                    "<iframe src=\"/reset-child.html\"></iframe></main>"
                    "</body></html>").encode())
            return self.reply(404, b"gone")

    server = network.Server(("127.0.0.1", 0), Handler)
    origin = f"http://127.0.0.1:{server.server_address[1]}"
    threading.Thread(target=server.serve_forever, daemon=True).start()
    checks = []
    killed_hosts = []
    measured = {}

    def expect(name, condition, detail=None):
        checks.append({"check": name, "passed": bool(condition),
                       **({"detail": detail} if detail is not None else {})})

    try:
        for allocator in ("system", "arena"):
            tag = f"[{allocator}] "
            directory = tempfile.TemporaryDirectory(prefix="minicon-surf-dataset-")
            host = JOBS.Supervised(args.binary, directory.name, origin, allocator)
            try:
                def owners():
                    answer = host.call("memory.report", {})
                    if not answer.get("ok"):
                        return None
                    owned = answer["result"]["owners"]
                    return owned["script_realms"]["malloc_bytes"] + owned["targets"]["fixture_bytes"]

                def snapshot(target, **extra):
                    answer = host.call("target.snapshot",
                                       {"target": target, "format": "semantic",
                                        "max_bytes": 131072, "max_nodes": 128, **extra})
                    return answer["result"] if answer.get("ok") else None

                def cost_of(page):
                    base = owners()
                    opened = host.call("target.open",
                                       {"session": session, "url": f"{origin}{page}"},
                                       deadline_ms=10000)
                    if not opened.get("ok"):
                        return None
                    live = owners() - base
                    host.ok("target.close", {"target": opened["result"]["target"]})
                    return live

                profile = host.ok("profile.create", {"persistence": "ephemeral"})["profile"]
                session = host.ok("session.open", {"profile": profile})["session"]
                empty = owners()

                small = cost_of(f"/parent-{SMALL}.html")
                large = cost_of(f"/parent-{LARGE}.html")
                marginal = None
                if small is not None and large is not None:
                    marginal = (large - small) / (LARGE - SMALL)
                measured[allocator] = {"small": small, "large": large, "per_node": marginal}
                expect(tag + f"a child node costs at most {PER_NODE_GATE} bytes at the margin",
                       marginal is not None and 0 < marginal <= PER_NODE_GATE,
                       {"per_node": marginal, "small": small, "large": large})

                opened = host.call("target.open",
                                   {"session": session, "url": f"{origin}/page.html"},
                                   deadline_ms=8000)
                said = None
                if opened.get("ok"):
                    shot = snapshot(opened["result"]["target"])
                    texts = [n.get("name") for n in (shot or {}).get("nodes", [])
                             if n.get("role") == "text"]
                    said = texts[0] if texts else None
                    host.ok("target.close", {"target": opened["result"]["target"]})
                expect(tag + "a main realm reads and writes data-* and gets the same view twice",
                       said == "true,true,true,true,true,true,true", {"said": said})

                # The child invariants, unchanged by this slice.
                opened = host.call("target.open",
                                   {"session": session, "url": f"{origin}/reset-parent.html"},
                                   deadline_ms=8000)
                target = opened["result"]["target"] if opened.get("ok") else None
                inspected = host.call("target.inspect", {"target": target}) if target else {}
                frames = (inspected.get("result") or {}).get("frames") or []
                frame = frames[1]["frame"] if len(frames) > 1 else None
                shot = snapshot(target, frame=frame) if frame else None
                nodes = (shot or {}).get("nodes") or []
                expect(tag + "a child realm still answers a snapshot",
                       bool(nodes) and any(n.get("role") == "text" for n in nodes),
                       {"nodes": len(nodes)})
                boxes = [n for n in nodes if n.get("role") in ("checkbox", "switch")]
                acted = None
                if boxes:
                    answer = host.call("target.act",
                                       {"target": target, "reference": boxes[0]["reference"],
                                        "action": {"kind": "set_checked", "checked": False}},
                                       deadline_ms=8000)
                    acted = (answer.get("result") or {}).get("applied") if answer.get("ok") \
                        else (answer.get("error") or {}).get("code")
                expect(tag + "and still applies a host action through the bridge",
                       acted is True, {"acted": acted})
                undone = None
                if frame:
                    shot = snapshot(target, frame=frame)
                    buttons = [n for n in ((shot or {}).get("nodes") or [])
                               if n.get("role") == "button"]
                    if buttons:
                        answer = host.call("target.act",
                                           {"target": target, "reference": buttons[0]["reference"],
                                            "action": {"kind": "click"}}, deadline_ms=8000)
                        if answer.get("ok"):
                            after = snapshot(target, frame=frame)
                            boxes_after = [n for n in ((after or {}).get("nodes") or [])
                                           if n.get("role") in ("checkbox", "switch")]
                            undone = boxes_after[0].get("checked") if boxes_after else None
                expect(tag + "and still runs the DOM's own reset",
                       undone is True, {"checked_after_reset": undone})
                if target:
                    host.ok("target.close", {"target": target})
                closed = owners()
                expect(tag + "closing every target returns the owners exactly",
                       closed is not None and closed == empty,
                       {"closed": closed, "empty": empty})
            finally:
                if host.timeouts:
                    killed_hosts.append({"group": f"dataset-{allocator}",
                                         "allocator": allocator, "timeouts": host.timeouts})
                host.finish()
                directory.cleanup()

        # A child realm has no dataset at all, read through the court-only probe.
        probe_directory = tempfile.TemporaryDirectory(prefix="minicon-surf-dataset-probe-")
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
        directory = tempfile.TemporaryDirectory(prefix="minicon-surf-dataset-divergence-")
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
                                   {"session": session, "url": f"{origin}/reset-parent.html"},
                                   deadline_ms=8000)
                report = host.call("memory.report", {})
                probe = ((report.get("result") or {}).get("owners") or {}).get("realm_probe") or {}
                expect("the main realm has dataset and every child realm has none",
                       opened.get("ok") is True
                       and probe.get("main_dataset") is True
                       and probe.get("children_dataset") == 0
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
        "court": "native-dom lazy dataset (control 0.0.2)",
        "host_sha256": hashlib.sha256(Path(args.binary).read_bytes()).hexdigest(),
        "gate": {"per_node_bytes": PER_NODE_GATE, "small_nodes": SMALL, "large_nodes": LARGE},
        "measured": measured,
        "checks": checks,
        "checks_passed": sum(1 for c in checks if c["passed"]),
        "checks_total": len(checks),
        "passed": all(c["passed"] for c in checks) and not killed_hosts,
        "hosts_killed": killed_hosts,
        "limitations": [
            "design-frozen court: the per-element gate and the divergence fail until dataset is lazy",
            "the gate is a marginal cost between two child documents, not a total; the M1 and M2 floors and the main slack stay with the child-frame and shim-footprint courts",
            "a child realm's absence of dataset is read through the court-only probe, because a child has no eval surface by design",
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
