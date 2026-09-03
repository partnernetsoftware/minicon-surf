#!/usr/bin/env python3
"""Read-only attribution court for the profile court's churned footprint.

The profile court's frozen total-live criterion is unmet (80/82). Before any
fix candidate, this court splits the churned footprint into what the host can
name: the profile store, the realms (with the cookie/storage mirrors inside
them), the parsed documents, the network counters, libmalloc's in-use versus
allocated bytes, and the remainder. It changes nothing in the host and moves
no gate; every arm runs in a fresh process, one warm-up plus seven measured
runs, under the default allocator and the opt-in arena.

Arms, all replaying one fixed timeline (the profile court's target sequence):

- off-equal-churn: no --profile-root (feature off), three ephemeral profiles,
  the same pages; the in-memory jar and mirrors still exist.
- store-no-data: --profile-root, two persistent profiles and one ephemeral,
  the same number of opens but every page is the cookie-free echo page and
  no control-plane writes.
- store-data: --profile-root and the profile court's pages, cookies,
  storage and control-plane budget writes (the fault injection is not churn
  and is left out).
- restart-steady: a setup host persists alpha with a cookie and a storage
  key, exits; the measured fresh host opens alpha and one storage page.

After every open and every close the court samples physical footprint and
RSS from outside and the host's memory.report from inside, so the first
close after which the footprint never falls back is visible per run. The
court deletes the keychain items it created. Only fake values are used.
"""

import argparse
import hashlib
import importlib.util
import json
import os
import statistics
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "protocol"))

FAKE = {"alpha": "court-alpha-7f3a", "beta": "court-beta-2c9e", "scratch": "court-scratch-51d0"}
KEYCHAIN_SERVICE = "minicon-surf.native-dom.profile-master-key"
SETTLE_SECONDS = 0.03


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


PROFILE = load_module("profile_court", Path(__file__).with_name("profile-court.py"))
RETENTION = PROFILE.RETENTION
NETWORK = PROFILE.NETWORK
Host = PROFILE.Host


def keychain_account(root):
    canonical = os.path.realpath(root)
    return hashlib.sha256(canonical.encode()).hexdigest()[:32]


def delete_keychain_item(root):
    result = subprocess.run(["security", "delete-generic-password", "-s", KEYCHAIN_SERVICE, "-a", keychain_account(root)],
                            capture_output=True, text=True)
    return result.returncode == 0


def sample(host, label):
    time.sleep(SETTLE_SECONDS)
    outside = RETENTION.sample_process(host.process.pid)
    report = host.ok("memory.report", {})
    owners = report["owners"]
    realms = owners["script_realms"]
    return {
        "stage": label,
        "physical_footprint_bytes": outside["physical_footprint_bytes"],
        "resident_bytes": outside["resident_bytes"],
        "libmalloc_size_in_use": report["libmalloc"]["size_in_use"],
        "libmalloc_size_allocated": report["libmalloc"]["size_allocated"],
        "libmalloc_blocks_in_use": report["libmalloc"]["blocks_in_use"],
        "owners": {
            "profiles": owners["profiles"]["objects"],
            "profile_accounted_bytes": owners["profiles"]["bytes"],
            "cookies": owners["profiles"]["cookies"],
            "storage_keys": owners["profiles"]["storage_keys"],
            "sessions": owners["sessions"]["objects"],
            "targets": owners["targets"]["objects"],
            "document_bytes": owners["targets"]["fixture_bytes"],
            "elements": owners["targets"]["elements"],
            "script_realms": realms["objects"],
            "script_realm_bytes": realms["malloc_bytes"],
            "network_fetches": owners["network"]["fetches"],
            "network_bytes": owners["network"]["bytes"],
            "adapters": owners["adapters"]["objects"],
        },
        "arena_used_bytes": sum(a["used_bytes"] for a in realms["dedicated_arenas"]),
        "arena_high_water_bytes": sum(a["high_water_bytes"] for a in realms["dedicated_arenas"]),
        "arena_blocks_leaked_total": report["allocator"]["arena_blocks_leaked_total"],
    }


def set_url(origin, name, value, attrs="Path%3D/%3B%20Max-Age%3D3600"):
    return f"{origin}/cookie/set?name={name}&value={value}&attrs={attrs}"


def timeline(origin, arm):
    """The profile court's target sequence as (profile, url, keep) steps plus control steps.

    `store-no-data` keeps the same number of page opens but every page is the
    cookie-free echo page, and the control-plane writes are dropped.
    """
    echo = f"{origin}/echo.html"
    steps = []
    for name in ("alpha", "beta", "scratch"):
        steps.append(("open", name, set_url(origin, "court", FAKE[name]), True))
    for name in ("alpha", "beta", "scratch"):
        steps.append(("open", name, echo, False))
    for name in ("alpha", "beta"):
        steps.append(("open", name, f"{origin}/storage.html?{name}-1", False))
        steps.append(("open", name, f"{origin}/storage.html?{name}-2", False))
    steps.append(("open", "alpha", set_url(origin, "hidden", "court-hidden", "HttpOnly%3B%20Path%3D/"), False))
    steps.append(("open", "alpha", f"{origin}/storage.html?alpha-3", False))
    steps.append(("open", "alpha", echo, False))
    for label, attrs in (("neg", "Secure"), ("neg", "Domain%3Dexample.com"), ("neg", "SameSite%3DNone"),
                         ("__Host-court", "Path%3D/"), ("neg", "Partitioned")):
        steps.append(("open", "beta", set_url(origin, label, "court-neg", attrs), False))
        steps.append(("open", "beta", echo, False))
    steps.append(("open", "beta", set_url(origin, "gone", "court-gone", "Max-Age%3D0"), False))
    steps.append(("open", "beta", echo, False))
    steps.append(("open", "alpha", set_url(origin, "volatile", "court-volatile", "Path%3D/"), False))
    steps.append(("session_cycle", "alpha", None, False))
    steps.append(("open", "alpha", echo, False))
    steps.append(("open", "alpha", set_url(origin, "amp", "court-amp"), False))
    steps.append(("put_cookie_overflow", "beta", None, False))
    steps.append(("put_storage_40", "beta", None, False))
    if arm == "store-no-data":
        steps = [("open", p, echo, keep) if kind == "open" else (kind, p, u, keep)
                 for kind, p, u, keep in steps if kind in ("open", "session_cycle")]
    return steps


def run_arm(binary, arm, allocator, origin, directory):
    root = Path(directory) / "profiles"
    store = arm != "off-equal-churn"
    persistence = {"alpha": "persistent", "beta": "persistent", "scratch": "ephemeral"} if store else \
        {"alpha": "ephemeral", "beta": "ephemeral", "scratch": "ephemeral"}
    stages = []
    closes = []
    if arm == "restart-steady":
        setup = Host(binary, directory, allocator, origin, root)
        try:
            profile = setup.ok("profile.create", {"persistence": "persistent", "name": "alpha"})["profile"]
            session = setup.ok("session.open", {"profile": profile})["session"]
            for url in (set_url(origin, "court", FAKE["alpha"]), f"{origin}/storage.html?alpha-1"):
                target = setup.ok("target.open", {"session": session, "url": url})["target"]
                setup.ok("target.close", {"target": target})
            setup.ok("session.close", {"session": session})
        finally:
            setup.finish()
        host = Host(binary, directory, allocator, origin, root)
        try:
            stages.append(sample(host, "empty"))
            session = host.ok("session.open", {"profile": "profile_alpha"})["session"]
            stages.append(sample(host, "sessions_open"))
            target = host.ok("target.open", {"session": session, "url": f"{origin}/storage.html?alpha-2"})["target"]
            text = PROFILE_TEXT(host, target)
            if "seen=alpha-1" not in text or f"court={FAKE['alpha']}" not in text:
                raise RuntimeError(f"restart host did not see the persisted data: {text}")
            stages.append(sample(host, "churned_final"))
            before = stages[-1]["physical_footprint_bytes"]
            host.ok("target.close", {"target": target})
            stages.append(sample(host, "close_1"))
            closes.append({"index": 1, "before": before, "after": stages[-1]["physical_footprint_bytes"]})
            host.ok("session.close", {"session": session})
            stages.append(sample(host, "post_close_all"))
            trim = host.ok("memory.trim", {})
            stages.append(sample(host, "post_trim"))
            stages[-1]["trim_released_bytes"] = trim["released_bytes"]
            stages[-1]["trim_arena_released_bytes"] = trim.get("arena_released_bytes", 0)
            exit_code = host.finish()
        finally:
            if host.process.poll() is None:
                host.process.kill()
                host.process.wait()
        return {"stages": stages, "closes": closes, "exit_code": exit_code, "opens": 1}

    host = Host(binary, directory, allocator, origin, root if store else None)
    try:
        stages.append(sample(host, "empty"))
        profiles, sessions, kept = {}, {}, []
        for name in ("alpha", "beta", "scratch"):
            profiles[name] = host.ok("profile.create", {"persistence": persistence[name], "name": name})["profile"]
        stages.append(sample(host, "profiles_created"))
        for name in ("alpha", "beta", "scratch"):
            sessions[name] = host.ok("session.open", {"profile": profiles[name]})["session"]
        stages.append(sample(host, "sessions_open"))
        opens = 0
        for kind, name, url, keep in timeline(origin, arm):
            if kind == "open":
                opens += 1
                target = host.ok("target.open", {"session": sessions[name], "url": url})["target"]
                PROFILE_TEXT(host, target)
                stages.append(sample(host, f"open_{opens}"))
                if keep:
                    kept.append((name, target))
                else:
                    before = stages[-1]["physical_footprint_bytes"]
                    host.ok("target.close", {"target": target})
                    stages.append(sample(host, f"close_{len(closes) + 1}"))
                    closes.append({"index": len(closes) + 1, "before": before, "after": stages[-1]["physical_footprint_bytes"]})
            elif kind == "session_cycle":
                # session.close also closes the session's kept target: recorded as a close.
                before = stages[-1]["physical_footprint_bytes"]
                host.ok("session.close", {"session": sessions[name]})
                stages.append(sample(host, f"close_{len(closes) + 1}"))
                for owner, _ in [k for k in kept if k[0] == name]:
                    closes.append({"index": len(closes) + 1, "before": before, "after": stages[-1]["physical_footprint_bytes"]})
                kept = [k for k in kept if k[0] != name]
                sessions[name] = host.ok("session.open", {"profile": profiles[name]})["session"]
                stages.append(sample(host, "session_cycled"))
            elif kind == "put_cookie_overflow":
                host.call("profile.storage.put", {"session": sessions[name], "kind": "cookie", "key": "big", "value": "x" * 4097})
            elif kind == "put_storage_40":
                for index in range(40):
                    response = host.call("profile.storage.put", {"session": sessions[name], "kind": "local_storage", "key": f"k{index}", "value": "v"})
                    if not response["ok"]:
                        break
                stages.append(sample(host, "control_writes_done"))
        stages.append(sample(host, "churned_final"))
        for _, target in kept:
            before = stages[-1]["physical_footprint_bytes"]
            host.ok("target.close", {"target": target})
            stages.append(sample(host, f"close_{len(closes) + 1}"))
            closes.append({"index": len(closes) + 1, "before": before, "after": stages[-1]["physical_footprint_bytes"]})
        for name in ("alpha", "beta", "scratch"):
            host.ok("session.close", {"session": sessions[name]})
        stages.append(sample(host, "post_close_all"))
        trim = host.ok("memory.trim", {})
        stages.append(sample(host, "post_trim"))
        stages[-1]["trim_released_bytes"] = trim["released_bytes"]
        stages[-1]["trim_arena_released_bytes"] = trim.get("arena_released_bytes", 0)
        exit_code = host.finish()
    finally:
        if host.process.poll() is None:
            host.process.kill()
            host.process.wait()
    return {"stages": stages, "closes": closes, "exit_code": exit_code, "opens": opens}


def PROFILE_TEXT(host, target):
    snapshot = host.ok("target.snapshot", {"target": target, "format": "semantic", "max_bytes": 65536, "max_nodes": 32})
    return " ".join(n["name"] for n in snapshot["nodes"] if n["role"] == "text")


def median(values):
    return int(statistics.median(values))


def summarize(values):
    return {"median": median(values), "minimum": min(values), "maximum": max(values), "values": values}


def stage_map(run):
    return {s["stage"]: s for s in run["stages"]}


def ratchet(run):
    """Per run: releases per close and the first close after which the footprint never falls back."""
    closes = run["closes"]
    releases = [c["before"] - c["after"] for c in closes]
    first_non_releasing = next((c["index"] for c, r in zip(closes, releases) if r <= 0), None)
    floor_after = None
    if first_non_releasing is not None:
        later = [c["after"] for c in closes if c["index"] >= first_non_releasing]
        floor_after = min(later)
    return {"releases": releases, "closes_that_released": sum(1 for r in releases if r > 0),
            "first_non_releasing_close": first_non_releasing, "footprint_floor_after_it": floor_after}


def attribute(stage):
    owners = stage["owners"]
    named = owners["script_realm_bytes"] + owners["profile_accounted_bytes"] + owners["document_bytes"]
    return {
        "physical_footprint_bytes": stage["physical_footprint_bytes"],
        "resident_bytes": stage["resident_bytes"],
        "libmalloc_size_in_use": stage["libmalloc_size_in_use"],
        "libmalloc_size_allocated": stage["libmalloc_size_allocated"],
        "script_realm_bytes": owners["script_realm_bytes"],
        "profile_accounted_bytes": owners["profile_accounted_bytes"],
        "document_bytes": owners["document_bytes"],
        "network_bytes_fetched_total": owners["network_bytes"],
        "named_owner_bytes": named,
        "in_use_minus_named_owners": stage["libmalloc_size_in_use"] - named,
        "allocated_minus_in_use": stage["libmalloc_size_allocated"] - stage["libmalloc_size_in_use"],
        "footprint_minus_in_use": stage["physical_footprint_bytes"] - stage["libmalloc_size_in_use"],
        "arena_used_bytes": stage["arena_used_bytes"],
        "arena_high_water_bytes": stage["arena_high_water_bytes"],
    }


def aggregate(runs):
    maps = [stage_map(r) for r in runs]
    common = [s["stage"] for s in runs[0]["stages"] if all(s["stage"] in m for m in maps)]
    stages = {}
    for stage in common:
        rows = [m[stage] for m in maps]
        stages[stage] = {
            "physical_footprint_bytes": summarize([r["physical_footprint_bytes"] for r in rows]),
            "resident_bytes": summarize([r["resident_bytes"] for r in rows]),
            "libmalloc_size_in_use": summarize([r["libmalloc_size_in_use"] for r in rows]),
            "libmalloc_size_allocated": summarize([r["libmalloc_size_allocated"] for r in rows]),
            "owners": {key: summarize([r["owners"][key] for r in rows]) for key in rows[0]["owners"]},
        }
        if stage == "post_trim":
            stages[stage]["trim_released_bytes"] = summarize([r["trim_released_bytes"] for r in rows])
            stages[stage]["trim_arena_released_bytes"] = summarize([r["trim_arena_released_bytes"] for r in rows])
    attribution = {}
    for stage in ("churned_final", "post_close_all", "post_trim"):
        rows = [attribute(m[stage]) for m in maps]
        attribution[stage] = {key: summarize([r[key] for r in rows]) for key in rows[0]}
    ratchets = [ratchet(r) for r in runs]
    firsts = [r["first_non_releasing_close"] for r in ratchets]
    return {
        "opens_per_run": runs[0]["opens"],
        "closes_per_run": len(runs[0]["closes"]),
        "stages": stages,
        "attribution": attribution,
        "ratchet": {
            "first_non_releasing_close": {"values": firsts,
                                          "median": median([f for f in firsts if f is not None]) if any(f is not None for f in firsts) else None},
            "closes_that_released": summarize([r["closes_that_released"] for r in ratchets]),
            "release_per_close_median_bytes": [median([r["releases"][i] for r in ratchets]) for i in range(len(runs[0]["closes"]))],
        },
        "retained_post_close_minus_empty": {
            key: summarize([m["post_close_all"][key] - m["empty"][key] for m in maps])
            for key in ("physical_footprint_bytes", "resident_bytes", "libmalloc_size_in_use", "libmalloc_size_allocated")
        },
        "churned_minus_empty": {
            key: summarize([m["churned_final"][key] - m["empty"][key] for m in maps])
            for key in ("physical_footprint_bytes", "libmalloc_size_in_use", "libmalloc_size_allocated")
        },
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--binary", required=True)
    parser.add_argument("--repetitions", type=int, default=7)
    parser.add_argument("--warmup", type=int, default=1)
    parser.add_argument("--receipt", required=True)
    args = parser.parse_args()

    server = NETWORK.Server(("127.0.0.1", 0), PROFILE.ProfileHandler)
    origin = f"http://127.0.0.1:{server.server_address[1]}"
    threading.Thread(target=server.serve_forever, daemon=True).start()
    arms = ("off-equal-churn", "store-no-data", "store-data", "restart-steady")
    allocators = ("system", "arena")
    results, checks, keychain_deleted = {}, [], 0
    try:
        for allocator in allocators:
            results[allocator] = {}
            for arm in arms:
                runs = []
                for repetition in range(args.warmup + args.repetitions):
                    with tempfile.TemporaryDirectory(prefix="minicon-surf-profile-attribution-") as directory:
                        root = Path(directory) / "profiles"
                        try:
                            run = run_arm(args.binary, arm, allocator, origin, directory)
                        finally:
                            if arm != "off-equal-churn" and delete_keychain_item(root):
                                keychain_deleted += 1
                    if repetition >= args.warmup:
                        runs.append(run)
                    final = stage_map(run)["post_close_all"]["owners"]
                    checks.append({"check": f"[{allocator}] {arm} run {repetition}: host exits cleanly with every owner at zero",
                                   "passed": run["exit_code"] == 0 and final["targets"] == 0 and final["script_realms"] == 0
                                   and final["sessions"] == 0 and run["stages"][-1]["arena_blocks_leaked_total"] == 0})
                results[allocator][arm] = aggregate(runs)
    finally:
        server.shutdown()

    def fp(allocator, arm, stage):
        return results[allocator][arm]["stages"][stage]["physical_footprint_bytes"]["median"]

    comparisons = {}
    for allocator in allocators:
        comparisons[allocator] = {
            "store_enable_empty_delta": fp(allocator, "store-no-data", "empty") - fp(allocator, "off-equal-churn", "empty"),
            "store_no_data_minus_off_at_churned_final": fp(allocator, "store-no-data", "churned_final") - fp(allocator, "off-equal-churn", "churned_final"),
            "data_minus_no_data_at_churned_final": fp(allocator, "store-data", "churned_final") - fp(allocator, "store-no-data", "churned_final"),
            "store_data_minus_off_at_churned_final": fp(allocator, "store-data", "churned_final") - fp(allocator, "off-equal-churn", "churned_final"),
            "store_data_minus_off_at_post_close_all": fp(allocator, "store-data", "post_close_all") - fp(allocator, "off-equal-churn", "post_close_all"),
            "lifecycle_retention_off_arm_post_close_minus_empty": results[allocator]["off-equal-churn"]["retained_post_close_minus_empty"]["physical_footprint_bytes"]["median"],
            "restart_steady_churned_final": fp(allocator, "restart-steady", "churned_final"),
        }

    binary_hash = hashlib.sha256(Path(args.binary).read_bytes()).hexdigest()
    receipt = {
        "schema": "minicon-surf.native-dom-profile-attribution-receipt/0.0.1",
        "technology": "native-dom",
        "technology_version": "0.0.2",
        "host_sha256": binary_hash,
        "purpose": "read-only attribution of the profile court's churned footprint; no gate, no host change",
        "reference": "labs/native-dom/evidence/native-dom-control-0.0.2-profile.json (80/82, total-live criterion unmet)",
        "timeline": "the profile court's target sequence without the fault injection; samples after every open and close",
        "arms": list(arms),
        "allocators": list(allocators),
        "repetitions": args.repetitions,
        "warmup": args.warmup,
        "settle_seconds": SETTLE_SECONDS,
        "keychain_items_deleted": keychain_deleted,
        "checks": checks,
        "passed": all(c["passed"] for c in checks),
        "results": results,
        "comparisons": comparisons,
        "limitations": [
            "named owners are QuickJS malloc bytes, host-accounted profile bytes and document bytes; the realm mirrors of document.cookie and localStorage live inside the realm bytes and are separated only by arm comparison",
            "network bytes are a cumulative fetched counter, not live buffers; live network buffers are inside in_use minus named owners",
            "no CDP edge in this timeline: adapters, listener and its reader thread are zero by construction (their cost is in the CDP frame-tree receipt)",
            "physical footprint counts touched pages; libmalloc size_allocated is the zones' reserved virtual size",
            "one platform, one fixture set, fake values only; no leak-absence claim",
        ],
    }
    Path(args.receipt).write_text(json.dumps(receipt, indent=1, sort_keys=True) + "\n")
    print(json.dumps({"passed": receipt["passed"], "checks": len(checks), "comparisons": comparisons,
                      "keychain_items_deleted": keychain_deleted}, indent=1))
    return 0 if receipt["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
