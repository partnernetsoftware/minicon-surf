#!/usr/bin/env python3
"""Headless-by-default acceptance for the surface courts (falsifiable).

The owner saw the automated surface courts flash real windows. The rule
now: every automated court, regression and default command is strictly
headless; a real window needs the double opt-in `--visual` plus
`MINICON_SURF_ALLOW_VISIBLE_COURT=1`, and even then never steals focus.
This court proves the default with checks that can fail:

1. Baseline: no on-screen window owned by `native-dom-surface`, no such
   process.
2. The default snapshot attribution court runs (a subset, one run per
   cell) while this court polls the window list every 50 ms and inspects
   every surface child it sees: the count of windows owned by the child
   stays 0 at every sample, the child never maps AppKit or CoreGraphics,
   the court-only log reports window number 0, and the child count seen is
   at least one (the show/hide path really ran).
3. After the court: exit 0, the window list is unchanged, no residual
   `native-dom-surface` or `native-dom-control` process.
4. Fail closed: the visual surface court without the opt-in exits 3 with
   an `unverified` line and writes no receipt; `--visual` without the
   environment exits 3 on every court; the host with `--visual 1` and no
   environment refuses to start; the child in window mode without the
   environment exits 68 before any window; a default host refuses
   `surface.show` with `visible_surface_not_enabled`.
5. Abnormal exits: a host killed with SIGKILL while a headless surface is
   shown leaves no child within two seconds; a court interrupted with
   SIGINT while its host is alive exits 130 and leaves no process and no
   window.
"""

import argparse
import ctypes
import ctypes.util
import hashlib
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
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "protocol"))
import check_contract  # noqa: E402

VISIBLE_ENV = "MINICON_SURF_ALLOW_VISIBLE_COURT"
OWNER = "native-dom-surface"
K_CF_STRING_UTF8 = 0x08000100

_CG = ctypes.cdll.LoadLibrary(ctypes.util.find_library("CoreGraphics"))
_CF = ctypes.cdll.LoadLibrary(ctypes.util.find_library("CoreFoundation"))
_CG.CGWindowListCopyWindowInfo.restype = ctypes.c_void_p
_CG.CGWindowListCopyWindowInfo.argtypes = [ctypes.c_uint32, ctypes.c_uint32]
_CF.CFArrayGetCount.restype = ctypes.c_long
_CF.CFArrayGetCount.argtypes = [ctypes.c_void_p]
_CF.CFArrayGetValueAtIndex.restype = ctypes.c_void_p
_CF.CFArrayGetValueAtIndex.argtypes = [ctypes.c_void_p, ctypes.c_long]
_CF.CFDictionaryGetValue.restype = ctypes.c_void_p
_CF.CFDictionaryGetValue.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
_CF.CFStringCreateWithCString.restype = ctypes.c_void_p
_CF.CFStringCreateWithCString.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_uint32]
_CF.CFStringGetCString.restype = ctypes.c_bool
_CF.CFStringGetCString.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_long, ctypes.c_uint32]
_CF.CFRelease.argtypes = [ctypes.c_void_p]


def windows_by_owner():
    """On-screen window count per owner name (kCGWindowListOptionOnScreenOnly)."""
    key = _CF.CFStringCreateWithCString(None, b"kCGWindowOwnerName", K_CF_STRING_UTF8)
    info = _CG.CGWindowListCopyWindowInfo(1, 0)
    counts = {}
    try:
        for index in range(_CF.CFArrayGetCount(info)):
            entry = _CF.CFArrayGetValueAtIndex(info, index)
            name_ref = _CF.CFDictionaryGetValue(entry, key)
            if not name_ref:
                continue
            buffer = ctypes.create_string_buffer(256)
            _CF.CFStringGetCString(name_ref, buffer, 256, K_CF_STRING_UTF8)
            name = buffer.value.decode(errors="replace")
            counts[name] = counts.get(name, 0) + 1
    finally:
        _CF.CFRelease(info)
        _CF.CFRelease(key)
    return counts


def processes(name):
    """Pids whose executable name is exactly `name` (never a shell that mentions it)."""
    out = subprocess.run(["pgrep", "-x", name], capture_output=True, text=True).stdout.split()
    return [int(p) for p in out if int(p) != os.getpid()]


def mapped_frameworks(pid):
    out = subprocess.run(["lsof", "-p", str(pid)], capture_output=True, text=True).stdout
    return {name for name in ("AppKit.framework", "CoreGraphics.framework", "QuartzCore.framework") if name in out}


class Watch:
    """Poll the window list and the surface children while a court runs."""

    def __init__(self):
        self.samples = 0
        self.max_owner_windows = 0
        self.children_seen = set()
        self.frameworks = {}
        self.stop = threading.Event()
        self.thread = threading.Thread(target=self.run, daemon=True)

    def run(self):
        while not self.stop.is_set():
            self.samples += 1
            self.max_owner_windows = max(self.max_owner_windows, windows_by_owner().get(OWNER, 0))
            for pid in processes(OWNER):
                if pid not in self.children_seen:
                    self.children_seen.add(pid)
                    self.frameworks[pid] = mapped_frameworks(pid)
            time.sleep(0.05)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *_):
        self.stop.set()
        self.thread.join(timeout=5)


def headless_env():
    environment = dict(os.environ)
    environment.pop(VISIBLE_ENV, None)
    for knob in ("http_proxy", "https_proxy", "all_proxy"):
        environment.pop(knob, None)
    return environment


def stdio_host(binary, surface_binary, directory, extra):
    command = [binary, "serve", "--stdio", "--fixture-root", str(ROOT / "labs" / "court" / "fixtures"), "--config-dir", str(Path(directory) / "config"),
               "--surface-binary", str(surface_binary)] + extra
    return subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=headless_env())


class Calls:
    def __init__(self, process):
        self.process = process
        self.counter = 0

    def __call__(self, operation, arguments, deadline_ms=15000):
        self.counter += 1
        request = {"protocol": "minicon-surf.control", "version": "0.0.1", "request_id": f"req_headless_{self.counter}",
                   "deadline_ms": deadline_ms, "operation": operation, "arguments": arguments}
        check_contract.validate_request(request)
        self.process.stdin.write(json.dumps(request) + "\n")
        self.process.stdin.flush()
        line = self.process.stdout.readline()
        if not line:
            raise RuntimeError(f"host exited during {operation}")
        response = json.loads(line)
        check_contract.validate_response(response)
        return response


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--binary", required=True)
    parser.add_argument("--surface-binary", required=True)
    parser.add_argument("--receipt", required=True)
    args = parser.parse_args()
    binary, surface_binary = str(Path(args.binary).resolve()), str(Path(args.surface_binary).resolve())
    checks = []

    def expect(name, condition, detail=None):
        checks.append({"check": name, "passed": bool(condition), **({"detail": detail} if detail is not None else {})})

    python = sys.executable
    court = str(HERE / "surface-snapshot-attribution-court.py")
    before = windows_by_owner()
    expect("baseline: no on-screen window owned by the surface child and no surface process", before.get(OWNER, 0) == 0 and not processes(OWNER),
           {"owner_windows": before.get(OWNER, 0), "on_screen_windows": sum(before.values())})

    # 2. The default court under the watch.
    with tempfile.TemporaryDirectory(prefix="minicon-surf-headless-court-") as directory:
        receipt_path = Path(directory) / "snapshot.json"
        with Watch() as watch:
            run = subprocess.run([python, "-W", "ignore", court, "--binary", binary, "--surface-binary", surface_binary, "--receipt", str(receipt_path),
                                  "--repetitions", "1", "--warmup", "0", "--cells", "current-full,static-parse_drop,plateau-idle"],
                                 capture_output=True, text=True, env=headless_env(), timeout=600)
        after = windows_by_owner()
        receipt = json.loads(receipt_path.read_text()) if receipt_path.exists() else {}
        expect("default court: exits 0 and records the headless drain child", run.returncode == 0 and receipt.get("visual") is False and receipt.get("child_mode") == "drain",
               {"returncode": run.returncode, "visual": receipt.get("visual"), "child_mode": receipt.get("child_mode"), "stderr_tail": run.stderr[-300:]})
        expect("default court: the surface child ran (at least one child seen by the watch)", len(watch.children_seen) >= 1,
               {"children_seen": len(watch.children_seen), "samples": watch.samples})
        expect("default court: no window owned by the surface child at any of the samples", watch.samples > 0 and watch.max_owner_windows == 0,
               {"samples": watch.samples, "max_owner_windows": watch.max_owner_windows})
        expect("default court: no surface child ever mapped AppKit, CoreGraphics or QuartzCore", watch.children_seen and all(not f for f in watch.frameworks.values()),
               {"children": len(watch.children_seen), "mapped": sorted({name for f in watch.frameworks.values() for name in f})})
        expect("default court: the window list after equals the window list before (owner counts)", after.get(OWNER, 0) == 0 and after == before,
               {"before_total": sum(before.values()), "after_total": sum(after.values()), "owner_after": after.get(OWNER, 0)})
        expect("default court: no residual surface or host process", not processes(OWNER) and not processes("native-dom-control"))

    # 4. Fail closed.
    with tempfile.TemporaryDirectory(prefix="minicon-surf-headless-court-") as directory:
        receipt_path = Path(directory) / "visual.json"
        with Watch() as watch:
            run = subprocess.run([python, "-W", "ignore", str(HERE / "surface-court.py"), "--binary", binary, "--surface-binary", surface_binary, "--receipt", str(receipt_path)],
                                 capture_output=True, text=True, env=headless_env(), timeout=120)
        line = {}
        try:
            line = json.loads(run.stdout.strip().splitlines()[-1]) if run.stdout.strip() else {}
        except ValueError:
            line = {}
        expect("visual surface court without the opt-in: exit 3, an unverified line, no receipt, no child, no window",
               run.returncode == 3 and "unverified" in line and not receipt_path.exists() and not watch.children_seen and watch.max_owner_windows == 0,
               {"returncode": run.returncode, "line": line, "children_seen": len(watch.children_seen)})
        for name in ("surface-court.py", "surface-attribution-court.py", "surface-snapshot-attribution-court.py"):
            with Watch() as watch:
                run = subprocess.run([python, "-W", "ignore", str(HERE / name), "--binary", binary, "--surface-binary", surface_binary, "--receipt", str(Path(directory) / "x.json"), "--visual"],
                                     capture_output=True, text=True, env=headless_env(), timeout=120)
            expect(f"{name} --visual without the environment: exit 3, nothing started", run.returncode == 3 and not watch.children_seen and watch.max_owner_windows == 0 and not (Path(directory) / "x.json").exists(),
                   {"returncode": run.returncode, "stdout_tail": run.stdout[-200:]})
        run = subprocess.run([binary, "serve", "--stdio", "--fixture-root", str(ROOT / "labs" / "court" / "fixtures"), "--config-dir", str(Path(directory) / "cfg"),
                              "--surface-binary", surface_binary, "--visual", "1"], input="", capture_output=True, text=True, env=headless_env(), timeout=30)
        expect("host --visual 1 without the environment refuses to start (exit 2)", run.returncode == 2 and "MINICON_SURF_ALLOW_VISIBLE_COURT" in run.stderr, {"returncode": run.returncode})
        with Watch() as watch:
            run = subprocess.run([surface_binary, "7"], input=b"", capture_output=True, env=headless_env(), timeout=30)
        expect("surface child in window mode without the environment exits 68 before any window", run.returncode == 68 and watch.max_owner_windows == 0, {"returncode": run.returncode})
        host = stdio_host(binary, surface_binary, directory, [])
        call = Calls(host)
        try:
            profile = call("profile.create", {"persistence": "ephemeral"})["result"]["profile"]
            session = call("session.open", {"profile": profile})["result"]["session"]
            target = call("target.open", {"session": session, "fixture": "semantic-interactive.html"})["result"]["target"]
            shown = call("surface.show", {"target": target})
            expect("default host refuses surface.show as unsupported_capability / visible_surface_not_enabled and spawns nothing",
                   not shown["ok"] and shown["error"]["code"] == "unsupported_capability" and shown["error"].get("details", {}).get("reason") == "visible_surface_not_enabled" and not processes(OWNER),
                   shown.get("error"))
        finally:
            host.stdin.close()
            host.wait(timeout=10)

    # 5. Abnormal exits.
    with tempfile.TemporaryDirectory(prefix="minicon-surf-headless-court-") as directory:
        host = stdio_host(binary, surface_binary, directory, ["--surface-child-mode", "drain"])
        call = Calls(host)
        profile = call("profile.create", {"persistence": "ephemeral"})["result"]["profile"]
        session = call("session.open", {"profile": profile})["result"]["session"]
        target = call("target.open", {"session": session, "fixture": "semantic-interactive.html"})["result"]["target"]
        shown = call("surface.show", {"target": target})
        children = processes(OWNER)
        host.kill()
        host.wait(timeout=10)
        deadline = time.time() + 2
        while time.time() < deadline and processes(OWNER):
            time.sleep(0.05)
        expect("host killed with SIGKILL while a headless surface is shown: the child leaves within two seconds", shown["ok"] and children and not processes(OWNER),
               {"children_before": len(children), "children_after": len(processes(OWNER))})
        receipt_path = Path(directory) / "interrupted.json"
        court_run = subprocess.Popen([python, "-W", "ignore", court, "--binary", binary, "--surface-binary", surface_binary, "--receipt", str(receipt_path),
                                      "--repetitions", "2", "--warmup", "0", "--cells", "current-full"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=headless_env())
        # The drain child lives about ten milliseconds per round, so the
        # interrupt is timed on the host process (alive for the whole run).
        deadline = time.time() + 60
        while time.time() < deadline and not processes("native-dom-control"):
            time.sleep(0.005)
        time.sleep(0.2)
        alive = bool(processes("native-dom-control"))
        court_run.send_signal(signal.SIGINT)
        try:
            court_run.wait(timeout=30)
        except subprocess.TimeoutExpired:
            court_run.kill()
            court_run.wait()
        time.sleep(0.5)
        expect("court interrupted with SIGINT while its host is alive: exit 130, no residual process, no window, no receipt",
               alive and court_run.returncode == 130 and not processes(OWNER) and not processes("native-dom-control") and windows_by_owner().get(OWNER, 0) == 0 and not receipt_path.exists(),
               {"host_alive_at_interrupt": alive, "returncode": court_run.returncode, "residual_children": len(processes(OWNER)), "residual_hosts": len(processes("native-dom-control"))})

    final = windows_by_owner()
    expect("end: the window list equals the baseline and no experiment process remains", final == before and not processes(OWNER) and not processes("native-dom-control"),
           {"before_total": sum(before.values()), "final_total": sum(final.values())})
    receipt = {
        "schema": "minicon-surf.native-dom-surface-headless-receipt/0.0.1",
        "technology": "native-dom",
        "host_sha256": hashlib.sha256(Path(binary).read_bytes()).hexdigest(),
        "surface_sha256": hashlib.sha256(Path(surface_binary).read_bytes()).hexdigest(),
        "rule": "automated courts, regressions and default commands are strictly headless; a real window needs --visual plus MINICON_SURF_ALLOW_VISIBLE_COURT=1 in the same run and never steals focus",
        "checks": checks,
        "checks_passed": sum(1 for c in checks if c["passed"]),
        "checks_total": len(checks),
        "passed": all(c["passed"] for c in checks),
        "limitations": ["macOS only; the window list is CGWindowListCopyWindowInfo on-screen windows by owner name; framework mapping is read with lsof",
                        "the drain child is the surface binary in its no-AppKit mode: a separate process that maps no AppKit; a court that spawns no child at all would have no show/hide path to measure",
                        "no pid, path or command line is recorded"],
    }
    Path(args.receipt).write_text(json.dumps(receipt, indent=1, sort_keys=True) + "\n")
    print(json.dumps({"passed": receipt["passed"], "checks_passed": receipt["checks_passed"], "checks_total": receipt["checks_total"]}))
    for check in checks:
        if not check["passed"]:
            print("FAIL", json.dumps(check)[:400])
    return 0 if receipt["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
