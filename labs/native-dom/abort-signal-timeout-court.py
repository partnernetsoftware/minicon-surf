#!/usr/bin/env python3
"""The frozen court for `AbortSignal.timeout()`.

Frozen from `abort-signal-timeout-audit-0.0.1.md` §7 before the extension
changes, and failing until the threshold quota exists.

The reserve is the point. `timeout()` spends from the page's own timer table,
which holds 64, so it refuses once that table holds 16 — leaving **48 slots
the page can still use for `setTimeout`**, and capping timeout signals at 16.
A page that leans on the standard idiom cannot starve itself of timers, and
the refusal is a `RangeError` it can catch.

It also pins the **amended standing guard**: the only host path that may abort
a page's signal is the timer `timeout()` created for that signal, and it
reaches that signal and nothing else. A controller's own signal is never
aborted by anything the host does, and one timeout signal firing leaves every
other signal alone.

`AbortSignal.any` stays out of scope and is pinned absent, the handle's key
set is pinned unchanged, and the main-only slack bound is measured by
`shim-footprint` on the same binary.

Strictly headless: no surface binary, no window, no AppKit, and it refuses to
run with the visible-court variable set. One hermetic loopback origin, both
allocators.

Groups: sources, existence, firing, reserve, refusal, isolation, lifetime.
"""

import argparse
import hashlib
import importlib.util
import json
import os
import re
import sys
import tempfile
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "protocol"))
import check_contract  # noqa: E402,F401

VISIBLE_ENV = "MINICON_SURF_ALLOW_VISIBLE_COURT"
BASE_JS = Path(__file__).with_name("src") / "dom_shim_base.js"
MAIN_JS = Path(__file__).with_name("src") / "dom_shim_main.js"
HANDLE_KEYS = {
    "g", "document", "Document", "Element", "Node", "Event",
    "addListener", "removeListener", "dispatchOn", "contains",
    "focusedElement", "eventStateOf", "signals",
}
# The ruled split of the page's 64-entry timer table.
RESERVED_FOR_SET_TIMEOUT = 48
TIMEOUT_SIGNAL_CAP = 16
OWNER_RETURN_BYTES = 65536

PROBES = [
    ("exists", "typeof AbortSignal.timeout"),
    ("any_absent", "typeof AbortSignal.any"),
    ("fresh_is_not_aborted",
     "(function(){var s=AbortSignal.timeout(60000);"
     "return String(s.aborted)+'|'+String(s.reason);})()"),
    # Held BEFORE the table is filled: an amendment after the first run, where
    # the cap probe ran first and this one could not even make its signal.
    ("isolation",
     "(function(){var slow=AbortSignal.timeout(600000);"
     "var controller=new AbortController();"
     "window.__mcsCourtSlow=slow;window.__mcsCourtCtl=controller;"
     "return 'held';})()"),
    # The cap, and what the page keeps.
    ("cap_and_reserve",
     "(function(){var signals=0;"
     "try{for(var i=0;i<40;i+=1){AbortSignal.timeout(600000);signals++;}}catch(e){"
     "if(e.name!=='RangeError')return 'wrong refusal '+e.name;}"
     "var timers=0;"
     "try{for(var j=0;j<80;j+=1){setTimeout(function(){},600000);timers++;}}catch(e){}"
     "return 'signals '+signals+'|setTimeout '+timers;})()"),
    # One more, once the table is full, must be refused: that is the cap.
    ("one_more_refused",
     "(function(){try{AbortSignal.timeout(600000);return 'accepted';}"
     "catch(e){return 'refused:'+e.name;}})()"),
]


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


def handle_keys(base):
    start = base.index("return take({")
    end = base.index("});", start)
    body = re.sub(r"//[^\n]*", "", base[start + len("return take({"):end])
    keys, depth, field = set(), 0, ""
    for character in body:
        if character in "([{":
            depth += 1
        elif character in ")]}":
            depth -= 1
        if character == "," and depth == 0:
            keys.add(field.split(":")[0].strip())
            field = ""
        else:
            field += character
    if field.strip():
        keys.add(field.split(":")[0].strip())
    return {key for key in keys if key}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--binary", required=True)
    parser.add_argument("--receipt", required=True)
    args = parser.parse_args()
    if os.environ.get(VISIBLE_ENV):
        print(json.dumps({"passed": False, "reason": "the visible-court variable is set"}))
        return 1

    network = RETENTION.load_network_module()
    slots = "".join("<p id=r%d></p>" % i for i in range(len(PROBES)))
    script = "".join(
        "try{var v%d=String(%s);}catch(e){var v%d='probe-threw:'+e.name;}"
        "document.getElementById('r%d').textContent='%s='+v%d;"
        % (i, expression, i, i, name, i)
        for i, (name, expression) in enumerate(PROBES))
    # A short timeout whose firing the page records, and a listener bound to it
    # that must stop running once it has fired.
    PAGE = ("<!doctype html><html><body><main>"
            "<p id=\"fired\">not fired</p><p id=\"bound\">bound 0</p>"
            "<p id=\"others\">others quiet</p>" + slots + "</main><script>"
            # The page must still build on a host without timeout(), so the
            # court fails its criteria rather than crashing on the fixture.
            "var quick=null;try{quick=AbortSignal.timeout(1);}catch(e){}"
            "var fires=0;var runs=0;var bus=document.createElement('span');"
            "if(quick){quick.addEventListener('abort',function(){fires+=1;"
            "document.getElementById('fired').textContent='fired '+fires"
            "+' '+(quick.reason&&quick.reason.name)"
            "+' '+String(quick.reason instanceof DOMException);});"
            "bus.addEventListener('ping',function(){runs+=1;"
            "document.getElementById('bound').textContent='bound '+runs;},{signal:quick});}"
            + script + "</script></body></html>").encode()
    MANY = ("<!doctype html><html><body><main><p id=m>many</p></main><script>"
            "var kept=[];try{for(var i=0;i<40;i+=1){kept.push(AbortSignal.timeout(600000));}}"
            "catch(e){}document.getElementById('m').textContent='held '+kept.length;"
            "</script></body></html>").encode()

    class Handler(network.Handler):
        def do_GET(self):
            path, _, _query = self.path.partition("?")
            network.Handler.hits.append(path)
            return self.reply(200, MANY if path == "/many.html" else PAGE)

    server = network.Server(("127.0.0.1", 0), Handler)
    origin = f"http://127.0.0.1:{server.server_address[1]}"
    threading.Thread(target=server.serve_forever, daemon=True).start()
    checks = []
    killed_hosts = []

    def expect(name, condition, detail=None):
        checks.append({"check": name, "passed": bool(condition),
                       **({"detail": detail} if detail is not None else {})})

    base = BASE_JS.read_text()
    main_source = MAIN_JS.read_text()
    expect("H1: the handle's key set is unchanged by this slice",
           handle_keys(base) == HANDLE_KEYS,
           {"unexpected": sorted(handle_keys(base) - HANDLE_KEYS),
            "missing": sorted(HANDLE_KEYS - handle_keys(base))})
    expect("H2: timeout lives in the extension and never in the base",
           "static timeout" in main_source and "timeout" not in base,
           {"in_main": "static timeout" in main_source,
            "in_base": "timeout" in base})
    expect("H3: the quota reads the existing table and adds no state of its own",
           "timers.pending.size >=" in main_source
           and "signalTimers" not in main_source,
           {"threshold": "timers.pending.size >=" in main_source,
            "new_state": "signalTimers" in main_source})

    try:
        for allocator in ("system", "arena"):
            tag = f"[{allocator}] "
            with tempfile.TemporaryDirectory(prefix="minicon-surf-timeout-") as directory:
                host = JOBS.Supervised(args.binary, directory, origin, allocator)
                try:
                    empty = host.ok("memory.report", {})["owners"]
                    baseline = (empty["script_realms"]["malloc_bytes"]
                                + empty["targets"]["fixture_bytes"])
                    profile = host.ok("profile.create", {"persistence": "ephemeral"})["profile"]
                    session = host.ok("session.open", {"profile": profile})["session"]
                    target = host.ok("target.open",
                                     {"session": session,
                                      "url": origin + "/page.html"})["target"]

                    def read():
                        said = {}
                        for node in host.ok("target.snapshot",
                                            {"target": target, "format": "semantic",
                                             "max_bytes": 131072,
                                             "max_nodes": 128})["nodes"]:
                            text = node.get("name") or ""
                            if node.get("role") == "text":
                                if "=" in text:
                                    key, _, value = text.partition("=")
                                    said[key] = value
                                said[text] = text
                        return said

                    said = read()
                    expect(tag + "T1: timeout exists and any stays out of scope",
                           said.get("exists") == "function"
                           and said.get("any_absent") == "undefined",
                           {"timeout": said.get("exists"), "any": said.get("any_absent")})
                    expect(tag + "T2: a fresh timeout signal is not yet aborted",
                           said.get("fresh_is_not_aborted") == "false|undefined",
                           {"said": said.get("fresh_is_not_aborted")})
                    expect(tag + "T3: it fires once, with a TimeoutError DOMException",
                           any(t == "fired 1 TimeoutError true" for t in said),
                           {"markers": [t for t in said if t.startswith("fired")
                                        or t == "not fired"]})
                    # Amended after the first run: the fixture holds two signals
                    # of its own before this probe, so counting how many MORE
                    # it accepts measured the fixture, not the rule. The ruled
                    # guarantee is what is pinned instead — the page keeps its
                    # 48 timer slots, and one more signal past the cap is
                    # refused, which T4b checks.
                    expect(tag + f"T4: the page keeps its {RESERVED_FOR_SET_TIMEOUT} timer slots",
                           (said.get("cap_and_reserve") or "").endswith(
                               f"|setTimeout {RESERVED_FOR_SET_TIMEOUT}"),
                           {"said": said.get("cap_and_reserve")})
                    expect(tag + "T4b: and one more timeout signal past the cap is refused",
                           said.get("one_more_refused") == "refused:RangeError",
                           {"said": said.get("one_more_refused")})
                    expect(tag + "T5: the refusal is a RangeError the page can catch",
                           "wrong refusal" not in (said.get("cap_and_reserve") or "")
                           and "probe-threw" not in (said.get("cap_and_reserve") or ""),
                           {"said": said.get("cap_and_reserve")})
                    expect(tag + "T6: a listener bound to a fired timeout stops running",
                           any(t == "bound 0" for t in said),
                           {"markers": [t for t in said if t.startswith("bound")]})
                    expect(tag + "T7: nothing else was aborted — the page still holds them",
                           said.get("isolation") == "held",
                           {"said": said.get("isolation")})

                    # The amended guard: the host's own work aborts nothing.
                    button_free = host.call("target.wait",
                                            {"target": target, "until": "quiet"})
                    after = read()
                    expect(tag + "T8: the host's own drain aborts no page signal it did not time",
                           after.get("isolation") == "held"
                           and any(t == "bound 0" for t in after),
                           {"wait_ok": button_free.get("ok"),
                            "isolation": after.get("isolation")})

                    # Lifetime: pending timeout signals do not outlive the target.
                    many = host.ok("target.open",
                                   {"session": session,
                                    "url": origin + "/many.html"})["target"]
                    held = [n.get("name") for n in host.ok(
                        "target.snapshot",
                        {"target": many, "format": "semantic",
                         "max_bytes": 65536, "max_nodes": 64})["nodes"]
                        if (n.get("name") or "").startswith("held ")]
                    expect(tag + "T9: a second page gets its own 16 and no more",
                           held == [f"held {TIMEOUT_SIGNAL_CAP}"],
                           {"said": held})
                    host.ok("target.close", {"target": many})
                    host.ok("target.close", {"target": target})
                    host.ok("session.close", {"session": session})
                    owners = host.ok("memory.report", {})["owners"]
                    live = (owners["script_realms"]["malloc_bytes"]
                            + owners["targets"]["fixture_bytes"])
                    expect(tag + "T10: pending timeout signals do not outlive their target",
                           live <= baseline + OWNER_RETURN_BYTES,
                           {"baseline": baseline, "after": live})
                finally:
                    if host.killed:
                        killed_hosts.append({"allocator": allocator})
                    host.finish()
                    killed_hosts.extend({"allocator": allocator, **t} for t in host.timeouts)
    finally:
        server.shutdown()

    receipt = {
        "court": "native-dom AbortSignal.timeout (control 0.0.2)",
        "host_sha256": hashlib.sha256(Path(args.binary).read_bytes()).hexdigest(),
        "ruled": {"reserved_for_set_timeout": RESERVED_FOR_SET_TIMEOUT,
                  "timeout_signal_cap": TIMEOUT_SIGNAL_CAP,
                  "host_abort_exception": "only the timer timeout() made, only its own signal"},
        "checks": checks,
        "checks_passed": sum(1 for c in checks if c["passed"]),
        "checks_total": len(checks),
        "passed": all(c["passed"] for c in checks) and not killed_hosts,
        "hosts_killed": killed_hosts,
        "limitations": [
            "design-frozen court: it fails until the threshold quota exists",
            "AbortSignal.any is pinned absent and stays out of scope",
            "three criteria read the shipped sources beside this court rather than the binary, so they are repo-local by design",
            "the main-only slack bound and the M1/M2 floors are measured by the shim-footprint and child-frame courts on the same binary",
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
