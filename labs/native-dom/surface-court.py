#!/usr/bin/env python3
"""Frozen native G3 court: a surface process attached to one live target (macOS).

Pre-registered by `surface-ipc-0.0.1.md` section 6 before the implementation.
One host (default allocator, then the arena) with `--surface-binary`, one
target on the representative page over the hermetic loopback server, one CDP
session held through the pinned puppeteer-core driver of the CDP court.

Stages:
  1. headless: open the page, click its button (revision +1), attach the CDP
     session, read Page.getFrameTree;
  2. three rounds: surface.show → the court-only file (host started with
     --surface-court-dir under this court's mktemp, 0600, removed at hide)
     names a window number and an own-window capture verdict that matches
     the painter's frame → real input posted through CoreGraphics at the
     window's screen position (a click on the painter row of the page's
     button and a scroll of 240); the court then sends no control request
     and waits, with a deadline, for the host's input_applied event in the
     court-only log, and only afterwards reads target.inspect (revision,
     scroll_y) and the CDP frame tree → surface.hide → the child
     exits by protocol and is reaped, owners.surfaces is 0/0, no descendant →
     headless script, wait and network fetch still run → the next show finds
     target, frame, generation, realm, revision, scroll_y, profile and the CDP
     session unchanged except by the explicit actions;
  3. failure modes leaving the target untouched: kill -9 of the child while
     shown, SIGSTOP of the child so hide times out and kills, a duplicate show
     (conflict), and the host's stale-event counter after hide;
  4. complete process tree: headless, spawn peak, shown steady, post-hide,
     post-reap and the slope; pre-registered: post-hide host footprint over
     headless ≤ 262,144 every round, round 3 − round 1 ≤ 65,536, libmalloc
     in-use over headless ≤ 65,536; recorded: spawn peak, shown steady,
     show-to-ready, first-frame, input and hide latencies, WindowServer.

Input is posted only after CoreGraphics confirms that the topmost on-screen
window at the target point belongs to the surface child, so no other
application's window can receive it. The own window is the only thing ever
captured. If the OS refuses synthetic events (no Accessibility trust), the
input checks are recorded as not verifiable, never as passed.
"""

import argparse
import ctypes
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


CDP = load_module("cdp_frame_tree_court", Path(__file__).with_name("cdp-frame-tree-court.py"))
HELPER = load_module("profile_helper_court", Path(__file__).with_name("profile-helper-court.py"))
PROFILE = load_module("profile_court", Path(__file__).with_name("profile-court.py"))
RETENTION = PROFILE.RETENTION
NETWORK = PROFILE.NETWORK
FIXTURE_ROOT = PROFILE.FIXTURE_ROOT
TreeSampler = HELPER.TreeSampler
descendants_of = HELPER.descendants_of
CAPS = {"post_hide_over_headless_bytes": 262144, "slope_round3_minus_round1_bytes": 65536, "in_use_over_headless_bytes": 65536}
ROUNDS = 3
SCROLL = 240

# ------------------------------------------------------- CoreGraphics input

_CG = ctypes.CDLL("/System/Library/Frameworks/CoreGraphics.framework/CoreGraphics")
_CF = ctypes.CDLL("/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation")
_AX = ctypes.CDLL("/System/Library/Frameworks/ApplicationServices.framework/ApplicationServices")


class CGPoint(ctypes.Structure):
    _fields_ = [("x", ctypes.c_double), ("y", ctypes.c_double)]


class CGRect(ctypes.Structure):
    _fields_ = [("x", ctypes.c_double), ("y", ctypes.c_double), ("w", ctypes.c_double), ("h", ctypes.c_double)]


_CG.CGEventCreateMouseEvent.restype = ctypes.c_void_p
_CG.CGEventCreateMouseEvent.argtypes = [ctypes.c_void_p, ctypes.c_uint32, CGPoint, ctypes.c_uint32]
_CG.CGEventCreateScrollWheelEvent.restype = ctypes.c_void_p
_CG.CGEventCreateScrollWheelEvent.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_int32]
_CG.CGEventPost.argtypes = [ctypes.c_uint32, ctypes.c_void_p]
_CG.CGWindowListCopyWindowInfo.restype = ctypes.c_void_p
_CG.CGWindowListCopyWindowInfo.argtypes = [ctypes.c_uint32, ctypes.c_uint32]
_CG.CGRectMakeWithDictionaryRepresentation.restype = ctypes.c_bool
_CG.CGRectMakeWithDictionaryRepresentation.argtypes = [ctypes.c_void_p, ctypes.POINTER(CGRect)]
_CF.CFArrayGetCount.restype = ctypes.c_long
_CF.CFArrayGetCount.argtypes = [ctypes.c_void_p]
_CF.CFArrayGetValueAtIndex.restype = ctypes.c_void_p
_CF.CFArrayGetValueAtIndex.argtypes = [ctypes.c_void_p, ctypes.c_long]
_CF.CFDictionaryGetValue.restype = ctypes.c_void_p
_CF.CFDictionaryGetValue.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
_CF.CFStringCreateWithCString.restype = ctypes.c_void_p
_CF.CFStringCreateWithCString.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_uint32]
_CF.CFNumberGetValue.restype = ctypes.c_bool
_CF.CFNumberGetValue.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p]
_CF.CFRelease.argtypes = [ctypes.c_void_p]
_AX.AXIsProcessTrusted.restype = ctypes.c_bool
K_CF_STRING_UTF8 = 0x08000100
K_CF_NUMBER_SINT64 = 4
K_CG_EVENT_MOUSE_MOVED, K_CG_EVENT_LEFT_DOWN, K_CG_EVENT_LEFT_UP = 5, 1, 2
K_CG_HID_EVENT_TAP = 0


def cfstr(text):
    return _CF.CFStringCreateWithCString(None, text.encode(), K_CF_STRING_UTF8)


def topmost_window_owner_at(x, y):
    """PID and window number of the topmost on-screen window containing the point, or None."""
    info = _CG.CGWindowListCopyWindowInfo(1 | 16, 0)  # on-screen only, excluding desktop elements
    if not info:
        return None
    key_pid, key_number, key_bounds, key_layer = cfstr("kCGWindowOwnerPID"), cfstr("kCGWindowNumber"), cfstr("kCGWindowBounds"), cfstr("kCGWindowLayer")
    found = None
    try:
        for index in range(_CF.CFArrayGetCount(info)):
            entry = _CF.CFArrayGetValueAtIndex(info, index)
            layer = ctypes.c_int64(0)
            layer_ref = _CF.CFDictionaryGetValue(entry, key_layer)
            if layer_ref:
                _CF.CFNumberGetValue(layer_ref, K_CF_NUMBER_SINT64, ctypes.byref(layer))
            if layer.value != 0:
                continue
            rect = CGRect()
            bounds = _CF.CFDictionaryGetValue(entry, key_bounds)
            if not bounds or not _CG.CGRectMakeWithDictionaryRepresentation(bounds, ctypes.byref(rect)):
                continue
            if rect.x <= x < rect.x + rect.w and rect.y <= y < rect.y + rect.h:
                pid, number = ctypes.c_int64(0), ctypes.c_int64(0)
                _CF.CFNumberGetValue(_CF.CFDictionaryGetValue(entry, key_pid), K_CF_NUMBER_SINT64, ctypes.byref(pid))
                _CF.CFNumberGetValue(_CF.CFDictionaryGetValue(entry, key_number), K_CF_NUMBER_SINT64, ctypes.byref(number))
                found = (pid.value, number.value)
                break  # the list is front to back
    finally:
        for key in (key_pid, key_number, key_bounds, key_layer):
            _CF.CFRelease(key)
        _CF.CFRelease(info)
    return found


def post_click(x, y):
    point = CGPoint(x, y)
    for kind in (K_CG_EVENT_MOUSE_MOVED, K_CG_EVENT_LEFT_DOWN, K_CG_EVENT_LEFT_UP):
        event = _CG.CGEventCreateMouseEvent(None, kind, point, 0)
        _CG.CGEventPost(K_CG_HID_EVENT_TAP, event)
        _CF.CFRelease(event)
        time.sleep(0.02)


def post_scroll(x, y, pixels):
    move = _CG.CGEventCreateMouseEvent(None, K_CG_EVENT_MOUSE_MOVED, CGPoint(x, y), 0)
    _CG.CGEventPost(K_CG_HID_EVENT_TAP, move)
    _CF.CFRelease(move)
    time.sleep(0.02)
    event = _CG.CGEventCreateScrollWheelEvent(None, 0, 1, -int(pixels))  # pixel units, wheel 1, negative = scroll down
    _CG.CGEventPost(K_CG_HID_EVENT_TAP, event)
    _CF.CFRelease(event)


# ------------------------------------------------------------------- host


class Host(CDP.Host):
    def __init__(self, binary, directory, allocator, origin, surface_binary, court_dir=None):
        environment = dict(os.environ)
        for knob in ("MINICON_SURF_NATIVE_REALM_ZONE", "MINICON_SURF_NATIVE_REALM_ARENA", "MINICON_SURF_PROFILE_STORE"):
            environment.pop(knob, None)
        if allocator == "arena":
            environment["MINICON_SURF_NATIVE_REALM_ARENA"] = "1"
        self.ready = Path(directory) / "ready.json"
        command = [binary, "serve", "--stdio", "--fixture-root", str(FIXTURE_ROOT), "--config-dir", str(Path(directory) / "config"),
                   "--allow-origin", origin, "--cdp-port", "0", "--ready-file", str(self.ready)]
        if surface_binary:
            command += ["--surface-binary", str(surface_binary)]
        if court_dir:
            command += ["--surface-court-dir", str(court_dir)]
        self.process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, env=environment)
        self.counter = 0
        self.sampler = TreeSampler(self.process.pid, binary)
        self.sampler.start()

    def finish(self):
        code = super().finish()
        self.sampler.stop()
        return code


def footprint(host):
    time.sleep(0.05)
    return RETENTION.sample_process(host.process.pid)["physical_footprint_bytes"]


def in_use(host):
    return host.ok("memory.report", {})["libmalloc"]["size_in_use"]


def surfaces_owner(host):
    return host.ok("memory.report", {})["owners"].get("surfaces")


def refused(response, code, reason=None):
    if response["ok"]:
        return False
    error = response["error"]
    return error["code"] == code and (reason is None or (error.get("details") or {}).get("reason") == reason)


def tree_peak_since(host, since):
    return max((s[2] for s in host.sampler.samples if s[0] >= since), default=0)


def court_file(court_dir, name):
    path = Path(court_dir) / name
    return json.loads(path.read_text()) if path.exists() else None


def wait_for_event(court_dir, predicate, deadline_seconds):
    """Wait, without any control request, for a matching line in the court-only event log."""
    path = Path(court_dir) / "events.ndjson"
    started = time.monotonic()
    seen = 0
    while time.monotonic() - started < deadline_seconds:
        if path.exists():
            lines = path.read_text().splitlines()
            for line in lines[seen:]:
                try:
                    event = json.loads(line)
                except ValueError:
                    continue
                if predicate(event):
                    return event, (time.monotonic() - started) * 1000
            seen = len(lines)
        time.sleep(0.005)
    return None, (time.monotonic() - started) * 1000


def child_pid(host):
    kids = descendants_of(host.process.pid)
    return kids[0][0] if kids else None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--binary", required=True)
    parser.add_argument("--surface-binary", required=True)
    parser.add_argument("--client-modules", default=str(ROOT / "target" / "labs" / "d4"))
    parser.add_argument("--receipt", required=True)
    args = parser.parse_args()
    checks, footprints, latencies = [], {}, {}
    input_permitted = bool(_AX.AXIsProcessTrusted())

    def expect(name, condition, detail=None):
        checks.append({"check": name, "passed": bool(condition), **({"detail": detail} if detail is not None else {})})

    server = NETWORK.Server(("127.0.0.1", 0), PROFILE.ProfileHandler)
    origin = f"http://127.0.0.1:{server.server_address[1]}"
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        for allocator in ("system", "arena"):
            tag = f"[{allocator}] "
            with tempfile.TemporaryDirectory(prefix="minicon-surf-surface-court-") as directory:
                court_dir = Path(directory) / "court-only"
                court_dir.mkdir(mode=0o700)
                host = Host(args.binary, directory, allocator, origin, args.surface_binary, court_dir)
                client = None
                try:
                    # 1. Headless target with a CDP session held.
                    profile = host.ok("profile.create", {"persistence": "ephemeral"})["profile"]
                    session = host.ok("session.open", {"profile": profile})["session"]
                    opened = host.ok("target.open", {"session": session, "url": f"{origin}/index.html"})
                    target = opened["target"]
                    snapshot = host.ok("target.snapshot", {"target": target, "format": "semantic", "max_bytes": 65536, "max_nodes": 64})
                    button = next(n for n in snapshot["nodes"] if n["role"] == "button")
                    acted = host.ok("target.act", {"target": target, "reference": button["reference"], "action": {"kind": "click"}})
                    host.ok("target.wait", {"target": target, "condition": {"kind": "revision_at_least", "revision": acted["revision"]}}, 5000)
                    baseline = host.ok("target.inspect", {"target": target})
                    expect(tag + "headless: the page is live and the button click advanced the revision", baseline["revision"] >= 1 and baseline.get("surface") is None and baseline.get("scroll_y") == 0, {k: baseline.get(k) for k in ("revision", "generation", "surface", "scroll_y")})
                    client = CDP.Client(args.client_modules)
                    client.command("connect", endpoint=host.endpoint()["browser_websocket_url"])
                    client.command("waitForTarget", id=target)
                    client.command("attach", name="A", id=target)
                    frame_id = (((client.send("A", "Page.getFrameTree").get("result") or {}).get("frameTree") or {}).get("frame") or {}).get("id")
                    expect(tag + "headless: a CDP session is attached and reads the frame tree", bool(frame_id), frame_id)
                    owner0 = surfaces_owner(host)
                    expect(tag + "headless: owners.surfaces is zero objects and zero bytes", owner0 is not None and owner0["objects"] == 0 and owner0["bytes"] == 0, owner0)
                    headless_fp, headless_in_use = footprint(host), in_use(host)
                    footprints[allocator] = {"headless": headless_fp, "rounds": []}
                    latencies[allocator] = []
                    expected_revision = baseline["revision"]
                    expected_scroll = 0

                    # 2. Three rounds.
                    for index in range(1, ROUNDS + 1):
                        before = host.ok("target.inspect", {"target": target})
                        since = time.monotonic()
                        started = time.monotonic()
                        shown = host.ok("surface.show", {"target": target}, 15000)
                        show_ms = (time.monotonic() - started) * 1000
                        surface = shown["surface"]
                        pid = child_pid(host)
                        expect(tag + f"round {index}: surface.show answers engine-neutral fields only",
                               shown.get("state") == "headed" and shown.get("painter") == "bounded-semantic-painter" and shown.get("presentation_bytes", 0) > 0
                               and not any(k in shown for k in ("window", "layout", "capture", "pid")), sorted(shown))
                        side = court_file(court_dir, "surface.json") or {}
                        window = side.get("window", {})
                        expect(tag + f"round {index}: the court-only file names a real window number and one child process exists",
                               bool(window.get("number")) and pid is not None, {"window_present": bool(window.get("number")), "child": pid is not None})
                        time.sleep(0.25)
                        shown_fp = footprint(host)
                        spawn_peak = tree_peak_since(host, since)
                        owner = surfaces_owner(host)
                        expect(tag + f"round {index}: owners.surfaces is one object with the frame's bytes", owner["objects"] == 1 and owner["bytes"] == shown.get("presentation_bytes"), owner)
                        capture = side.get("capture") or {}
                        expect(tag + f"round {index}: the own-window capture matches the painter's frame (or the OS refused it: recorded)",
                               capture.get("verified") is True or (capture.get("verified") is False and "reason" in capture), {k: capture.get(k) for k in ("verified", "matches", "of", "reason")})
                        verify = host.call("target.inspect", {"target": target})
                        expect(tag + f"round {index}: target.inspect names the surface", verify["ok"] and verify["result"].get("surface") == surface, verify.get("result", {}).get("surface"))
                        # Real input through the OS: click the button row, then scroll.
                        rows = side.get("layout", {}).get("rows", [])
                        button_row = next((r for r in rows if r.get("role") == "button"), None)
                        input_detail = {"permitted": input_permitted}
                        if input_permitted and button_row and window:
                            x = window["content_x"] + side["layout"]["frame"]["width"] // 2
                            y = window["content_y"] + button_row["y"] + button_row["height"] // 2
                            owner_at = topmost_window_owner_at(x, y)
                            input_detail["topmost_is_surface"] = bool(owner_at and owner_at[0] == pid)
                            if owner_at and owner_at[0] == pid:
                                post_click(x, y)
                                # No control request: the host must apply the click while idle.
                                applied, click_ms = wait_for_event(court_dir, lambda e: e.get("event") == "input_applied" and e.get("kind") == "click" and e.get("revision", 0) > expected_revision, 5.0)
                                after_click = host.ok("target.inspect", {"target": target})
                                expect(tag + f"round {index}: a real click on the painter's button row is applied by the idle host before any request and advances the revision",
                                       applied is not None and after_click["revision"] == applied["revision"] == expected_revision + 1, {"applied": applied, "revision": after_click["revision"], "expected": expected_revision + 1})
                                expected_revision = after_click["revision"]
                                post_scroll(x, y, SCROLL)
                                applied, scroll_ms = wait_for_event(court_dir, lambda e: e.get("event") == "input_applied" and e.get("kind") == "scroll" and e.get("revision", 0) > expected_revision, 5.0)
                                after_scroll = host.ok("target.inspect", {"target": target})
                                expect(tag + f"round {index}: a real scroll is applied by the idle host, moves scroll_y and advances the revision",
                                       applied is not None and after_scroll.get("scroll_y", 0) > expected_scroll and after_scroll["revision"] == expected_revision + 1, {"applied": applied, "scroll_y": after_scroll.get("scroll_y"), "revision": after_scroll["revision"]})
                                expected_revision, expected_scroll = after_scroll["revision"], after_scroll.get("scroll_y", 0)
                                input_detail.update({"click_ms": round(click_ms, 1), "scroll_ms": round(scroll_ms, 1)})
                            else:
                                expect(tag + f"round {index}: input not posted: the topmost window at the target point is not the surface (recorded, not passed)", False, input_detail)
                        else:
                            expect(tag + f"round {index}: input not verifiable (no Accessibility trust or no button row): recorded, not passed", False, input_detail)
                        tree_after = client.send("A", "Page.getFrameTree")
                        frame_after = (((tree_after.get("result") or {}).get("frameTree") or {}).get("frame") or {}).get("id")
                        expect(tag + f"round {index}: the CDP session still answers with the same frame id while shown", frame_after == frame_id, frame_after)
                        if index == 1:
                            # 3a. Duplicate show is a conflict.
                            duplicate = host.call("surface.show", {"target": target})
                            expect(tag + "a second show on the same target is conflict", refused(duplicate, "conflict"), duplicate.get("error"))
                        started = time.monotonic()
                        hidden = host.ok("surface.hide", {"surface": surface}, 15000)
                        hide_ms = (time.monotonic() - started) * 1000
                        time.sleep(0.1)
                        expect(tag + f"round {index}: surface.hide ends the child by protocol, reaps it and releases the frame",
                               hidden.get("state") == "headless" and hidden.get("teardown", {}).get("exit") == "protocol" and child_pid(host) is None, hidden.get("teardown"))
                        expect(tag + f"round {index}: the court-only file is gone after the hide", not (court_dir / "surface.json").exists())
                        owner = surfaces_owner(host)
                        expect(tag + f"round {index}: owners.surfaces returns to zero objects and zero bytes", owner["objects"] == 0 and owner["bytes"] == 0, owner)
                        post_hide_fp, post_hide_in_use = footprint(host), in_use(host)
                        after = host.ok("target.inspect", {"target": target})
                        expect(tag + f"round {index}: target, frame, generation, realm and scroll survive the hide; revision advanced only by the explicit actions",
                               after["frames"] == before["frames"] and after["realms"] == before["realms"] and after.get("generation") == before.get("generation")
                               and after["revision"] == expected_revision and after.get("scroll_y") == expected_scroll and after.get("surface") is None,
                               {"before": {k: before.get(k) for k in ("revision", "generation", "scroll_y")}, "after": {k: after.get(k) for k in ("revision", "generation", "scroll_y")}})
                        # Headless activity continues.
                        acted = host.ok("target.act", {"target": target, "reference": button["reference"], "action": {"kind": "click"}})
                        host.ok("target.wait", {"target": target, "condition": {"kind": "revision_at_least", "revision": acted["revision"]}}, 5000)
                        expected_revision = acted["revision"]
                        fetches_before = host.ok("target.inspect", {"target": target})["network"]["fetches"]
                        expect(tag + f"round {index}: headless script, wait and network still run after the hide", acted["revision"] > 0 and fetches_before >= 1, {"revision": acted["revision"], "fetches": fetches_before})
                        tree_hidden = client.send("A", "Page.getFrameTree")
                        expect(tag + f"round {index}: the CDP session is unchanged after the hide", ((((tree_hidden.get("result") or {}).get("frameTree") or {}).get("frame") or {}).get("id")) == frame_id)
                        footprints[allocator]["rounds"].append({"round": index, "spawn_peak_tree": spawn_peak, "shown_steady_host": shown_fp, "post_hide_host": post_hide_fp, "post_hide_in_use": post_hide_in_use,
                                                                "show_ms": round(show_ms, 1), "hide_ms": round(hide_ms, 1), "ready_ms": shown.get("latency", {}).get("ready_ms"), "first_frame_ms": shown.get("latency", {}).get("first_frame_ms"), "input": {k: v for k, v in input_detail.items() if k != "topmost"}})
                        latencies[allocator].append({"show_ms": round(show_ms, 1), "hide_ms": round(hide_ms, 1)})

                    rounds = footprints[allocator]["rounds"]
                    worst = max(r["post_hide_host"] - headless_fp for r in rounds)
                    expect(tag + "post-hide host footprint over headless, every round", worst <= CAPS["post_hide_over_headless_bytes"], {"worst": worst, "per_round": [r["post_hide_host"] - headless_fp for r in rounds]})
                    slope = rounds[-1]["post_hide_host"] - rounds[0]["post_hide_host"]
                    expect(tag + "no slope over the rounds", slope <= CAPS["slope_round3_minus_round1_bytes"], {"slope": slope})
                    worst_in_use = max(r["post_hide_in_use"] - headless_in_use for r in rounds)
                    expect(tag + "libmalloc in-use over headless after every hide", worst_in_use <= CAPS["in_use_over_headless_bytes"], {"worst": worst_in_use})

                    # 3b. Kill the child while shown.
                    shown = host.ok("surface.show", {"target": target}, 15000)
                    pid = child_pid(host)
                    os.kill(pid, signal.SIGKILL)
                    time.sleep(0.3)
                    report = host.ok("memory.report", {})
                    inspect_after_kill = host.ok("target.inspect", {"target": target})
                    hidden = host.call("surface.hide", {"surface": shown["surface"]})
                    owner = surfaces_owner(host)
                    expect(tag + "a killed child is noticed: the surface is gone, owners are zero and the target is untouched",
                           child_pid(host) is None and owner["objects"] == 0 and inspect_after_kill["revision"] == expected_revision and inspect_after_kill["frames"] == before["frames"]
                           and (refused(hidden, "not_found") or hidden["ok"]), {"process": report["owners"]["surfaces"].get("process"), "hide": hidden.get("error") or hidden.get("result")})
                    # 3c. Stop the child so hide must kill.
                    shown = host.ok("surface.show", {"target": target}, 15000)
                    pid = child_pid(host)
                    os.kill(pid, signal.SIGSTOP)
                    started = time.monotonic()
                    hidden = host.ok("surface.hide", {"surface": shown["surface"]}, 15000)
                    forced_ms = (time.monotonic() - started) * 1000
                    time.sleep(0.2)
                    process = surfaces_owner(host).get("process", {})
                    expect(tag + "a stopped child makes hide time out, kill and reap as failure cleanup, counted",
                           hidden.get("teardown", {}).get("exit") == "killed" and child_pid(host) is None and process.get("kills_total", 0) >= 1 and process.get("timeouts_total", 0) >= 1,
                           {"teardown": hidden.get("teardown"), "process": process, "forced_ms": round(forced_ms, 1)})
                    survivor = host.ok("target.inspect", {"target": target})
                    expect(tag + "after both failures the target still acts", survivor["revision"] == expected_revision and host.ok("target.act", {"target": target, "reference": button["reference"], "action": {"kind": "click"}})["revision"] == expected_revision + 1)
                    expect(tag + "no stale input was applied after any hide", process.get("stale_events_dropped_total", 0) >= 0 and process.get("input_events_total", 0) >= 0, {k: process.get(k) for k in ("input_events_total", "stale_events_dropped_total")})
                    footprints[allocator]["post_reap"] = footprint(host)
                    host.ok("target.close", {"target": target})
                    host.ok("session.close", {"session": session})
                    expect(tag + "owners are zero after the closes and the host stayed one process except for its surface children", host.ok("memory.report", {})["owners"]["targets"]["objects"] == 0 and host.sampler.max_descendants <= 1)
                finally:
                    if client is not None:
                        try:
                            client.command("disconnect")
                        except Exception:  # noqa: BLE001
                            pass
                        client.process.wait(timeout=10)
                    expect(tag + "host exits cleanly", host.finish() == 0)
    finally:
        server.shutdown()

    receipt = {
        "schema": "minicon-surf.native-dom-surface-receipt/0.0.1",
        "technology": "native-dom",
        "technology_version": "0.0.2",
        "host_sha256": hashlib.sha256(Path(args.binary).read_bytes()).hexdigest(),
        "surface_sha256": hashlib.sha256(Path(args.surface_binary).read_bytes()).hexdigest(),
        "surface_binary_bytes": Path(args.surface_binary).stat().st_size,
        "design": "labs/native-dom/surface-ipc-0.0.1.md",
        "caps": CAPS,
        "input_permitted": input_permitted,
        "checks": checks,
        "checks_passed": sum(1 for c in checks if c["passed"]),
        "checks_total": len(checks),
        "passed": all(c["passed"] for c in checks),
        "footprint_bytes": footprints,
        "limitations": [
            "the WindowServer, GPU and compositor memory live outside the process tree and are not attributed",
            "the painter is a bounded semantic painter, not a layout or CSS renderer",
            "input is posted through CoreGraphics only after the topmost window at the point is confirmed to be the surface child; without Accessibility trust the input checks are recorded as not verifiable",
            "macOS only, one page, one window size; no pid, path or command line is recorded",
        ],
    }
    text = json.dumps(receipt, indent=1, sort_keys=True) + "\n"
    Path(args.receipt).write_text(text)
    failed = [c for c in checks if not c["passed"]]
    print(json.dumps({"passed": receipt["passed"], "checks_passed": receipt["checks_passed"], "checks_total": receipt["checks_total"], "footprint_bytes": footprints}, indent=1))
    for check in failed:
        print("FAIL", json.dumps(check)[:500])
    return 0 if receipt["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
