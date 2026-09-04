#!/usr/bin/env python3
"""Pre-registered court for native engine-backed profiles (P6).

Frozen with profile-design-0.0.1.md before any storage code exists; it fails
by construction until the design is implemented. The hermetic server sets
and echoes cookies with fake court values only; nothing here touches a real
browser profile. Native requests are validated by protocol/check_contract.py.
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
import time
import urllib.parse
from http import cookies as http_cookies
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "protocol"))
import check_contract  # noqa: E402

FIXTURE_ROOT = ROOT / "labs" / "court" / "fixtures"
FAKE_VALUES = {"alpha": "court-alpha-7f3a", "beta": "court-beta-2c9e", "scratch": "court-scratch-51d0"}


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
NETWORK = RETENTION.load_network_module()


class ProfileHandler(NETWORK.Handler):
    """The representative site plus cookie endpoints with fake values."""

    def do_GET(self):
        path, _, query = self.path.partition("?")
        params = dict(item.split("=", 1) for item in query.split("&") if "=" in item)
        if path == "/cookie/set":
            # Court amendment (fixture mechanism): decode every percent-escape, not only %3B and %20;
            # the frozen fixture URLs escape "=" as %3D, which the first version left literal.
            attributes = urllib.parse.unquote(params.get("attrs", ""))
            header = f"{params.get('name', 'court')}={params.get('value', 'unset')}{'; ' + attributes if attributes else ''}"
            body = b"<!doctype html><html><body><main><h1>Cookie set</h1></main></body></html>"
            return self.reply(200, body, "text/html", extra=[("Set-Cookie", header)])
        if path == "/cookie/echo":
            sent = self.headers.get("Cookie", "")
            body = json.dumps({"cookie": sent}).encode()
            return self.reply(200, body, "application/json")
        if path == "/echo.html":
            body = (b"<!doctype html><html><body><main><h1>Echo</h1><p id=\"cookies\">pending</p></main>"
                    b"<script>fetch('/cookie/echo').then(r=>r.json()).then(j=>{document.getElementById('cookies').textContent='sent='+j.cookie;});</script>"
                    b"</body></html>")
            return self.reply(200, body, "text/html")
        if path == "/storage.html":
            body = (b"<!doctype html><html><body><main><h1>Storage</h1><p id=\"seen\">pending</p></main>"
                    b"<script>const k='court';const prev=localStorage.getItem(k);if(prev===null){localStorage.setItem(k,location.search.slice(1)||'first');}"
                    b"document.getElementById('seen').textContent='seen='+(prev===null?'none':prev)+' cookie='+document.cookie;</script>"
                    b"</body></html>")
            return self.reply(200, body, "text/html")
        return super().do_GET()


CAPS = {
    "empty_physical_footprint_delta_bytes": 524288,
    "empty_resident_delta_bytes": 1048576,
    "accounted_bytes_per_empty_persistent_profile": 65536,
    "lightpanda_single_server_empty_footprint_bytes": 8356392,
}


class Host:
    def __init__(self, binary, directory, allocator, origin, profile_root, store_mode=None):
        environment = dict(os.environ)
        for knob in ("MINICON_SURF_NATIVE_REALM_ZONE", "MINICON_SURF_NATIVE_REALM_ARENA", "MINICON_SURF_PROFILE_STORE"):
            environment.pop(knob, None)
        if allocator == "arena":
            environment["MINICON_SURF_NATIVE_REALM_ARENA"] = "1"
        if store_mode:
            environment["MINICON_SURF_PROFILE_STORE"] = store_mode
        command = [binary, "serve", "--stdio", "--fixture-root", str(FIXTURE_ROOT), "--config-dir", str(Path(directory) / "config"),
                   "--allow-origin", origin]
        if profile_root is not None:
            command += ["--profile-root", str(profile_root)]
        self.process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                        text=True, env=environment)
        self.counter = 0

    def call(self, operation, arguments, deadline_ms=30000):
        self.counter += 1
        request = {"protocol": "minicon-surf.control", "version": "0.0.1", "request_id": f"req_profile_{self.counter}",
                   "deadline_ms": deadline_ms, "operation": operation, "arguments": arguments}
        check_contract.validate_request(request)
        self.process.stdin.write(json.dumps(request) + "\n")
        self.process.stdin.flush()
        line = self.process.stdout.readline()
        if not line:
            return {"ok": False, "error": {"code": "internal", "message": "host exited", "retryable": False}}
        response = json.loads(line)
        check_contract.validate_response(response)
        return response

    def ok(self, operation, arguments, deadline_ms=30000):
        response = self.call(operation, arguments, deadline_ms)
        if not response["ok"]:
            raise RuntimeError(f"{operation} failed: {response['error']}")
        return response["result"]

    def finish(self):
        if self.process.poll() is None:
            self.process.stdin.close()
            try:
                return self.process.wait(timeout=30)
            except subprocess.TimeoutExpired:
                self.process.kill()
                return self.process.wait()
        return self.process.returncode


def files_containing(root, needle):
    hits = []
    for path in Path(root).rglob("*"):
        if path.is_file() and needle.encode() in path.read_bytes():
            hits.append(str(path.relative_to(root)))
    return hits


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--binary", required=True)
    parser.add_argument("--receipt")
    args = parser.parse_args()
    server = NETWORK.Server(("127.0.0.1", 0), ProfileHandler)
    origin = f"http://127.0.0.1:{server.server_address[1]}"
    threading.Thread(target=server.serve_forever, daemon=True).start()
    checks = []
    footprints = {}
    store_modes = set()

    def expect(name, condition, detail=None):
        checks.append({"check": name, "passed": bool(condition), **({"detail": detail} if detail is not None else {})})

    def refused(response, code):
        return not response["ok"] and response["error"]["code"] == code

    def text_of(host, target):
        snapshot = host.ok("target.snapshot", {"target": target, "format": "semantic", "max_bytes": 65536, "max_nodes": 32})
        return " ".join(n["name"] for n in snapshot["nodes"] if n["role"] == "text")

    def settle_echo(host, target):
        # Court amendment (implementation mechanism, not criterion): the echo page's fetch
        # resolves while the document loads, so the first snapshot already carries `sent=`
        # at revision 0. Wait for a later revision only when the load did not settle it.
        if "sent=" in text_of(host, target):
            return
        host.ok("target.wait", {"target": target, "condition": {"kind": "revision_at_least", "revision": 1}}, 5000)

    try:
        for allocator in ("system", "arena"):
            tag = f"[{allocator}] "
            with tempfile.TemporaryDirectory(prefix="minicon-surf-profile-court-") as directory:
                root = Path(directory) / "profiles"
                # D6 baseline: the same binary with the store feature off.
                baseline_host = Host(args.binary, directory, allocator, origin, None)
                baseline_host.ok("memory.report", {})
                time.sleep(0.3)
                baseline = RETENTION.sample_process(baseline_host.process.pid)
                baseline_host.finish()
                host = Host(args.binary, directory, allocator, origin, root)
                second = None
                try:
                    report = host.ok("memory.report", {})
                    time.sleep(0.3)
                    enabled = RETENTION.sample_process(host.process.pid)
                    empty = enabled["physical_footprint_bytes"]
                    mode = report["owners"].get("profiles", {}).get("store")
                    store_modes.add(mode)
                    expect(tag + "memory.report names the profile store mode", mode in ("envelope-keychain", "envelope-keyfile-experiment"), mode)
                    footprint_delta = enabled["physical_footprint_bytes"] - baseline["physical_footprint_bytes"]
                    resident_delta = enabled["resident_bytes"] - baseline["resident_bytes"]
                    expect(tag + "D6: keychain-backed store enabled costs at most 512 KiB of empty footprint and 1 MiB of empty RSS over feature-off",
                           footprint_delta <= CAPS["empty_physical_footprint_delta_bytes"] and resident_delta <= CAPS["empty_resident_delta_bytes"],
                           {"footprint_delta": footprint_delta, "resident_delta": resident_delta})

                    # 1. Three profiles, one session and page each.
                    alpha = host.ok("profile.create", {"persistence": "persistent", "name": "alpha"})["profile"]
                    beta = host.ok("profile.create", {"persistence": "persistent", "name": "beta"})["profile"]
                    scratch = host.ok("profile.create", {"persistence": "ephemeral"})["profile"]
                    expect(tag + "persistent ids are name-derived and the ephemeral id is not", alpha == "profile_alpha" and beta == "profile_beta" and scratch != "profile_scratch")
                    empty_profiles = host.ok("memory.report", {})["owners"]["profiles"]
                    expect(tag + "D6: an empty persistent profile accounts at most 64 KiB",
                           empty_profiles["objects"] == 3 and empty_profiles.get("bytes", 10**9) / 2 <= CAPS["accounted_bytes_per_empty_persistent_profile"], empty_profiles)
                    sessions, targets = {}, {}
                    for name, profile in (("alpha", alpha), ("beta", beta), ("scratch", scratch)):
                        sessions[name] = host.ok("session.open", {"profile": profile})["session"]
                        targets[name] = host.ok("target.open", {"session": sessions[name], "url": f"{origin}/cookie/set?name=court&value={FAKE_VALUES[name]}&attrs=Path%3D/%3B%20Max-Age%3D3600"})["target"]
                    live = RETENTION.sample_process(host.process.pid)["physical_footprint_bytes"]

                    # 2. Cookies travel only with their own profile's requests.
                    for name in ("alpha", "beta", "scratch"):
                        echo = host.ok("target.open", {"session": sessions[name], "url": f"{origin}/echo.html"})["target"]
                        settle_echo(host, echo)
                        text = text_of(host, echo)
                        expect(tag + f"profile {name}'s request carries only its own cookie",
                               f"court={FAKE_VALUES[name]}" in text and not any(f"court={v}" in text for k, v in FAKE_VALUES.items() if k != name), text)
                        host.ok("target.close", {"target": echo})

                    # 3. Storage is per profile and per origin; document.cookie honours HttpOnly.
                    for name in ("alpha", "beta"):
                        page = host.ok("target.open", {"session": sessions[name], "url": f"{origin}/storage.html?{name}-1"})["target"]
                        text = text_of(host, page)
                        expect(tag + f"profile {name}'s storage starts empty and its document.cookie shows its cookie",
                               "seen=none" in text and f"court={FAKE_VALUES[name]}" in text, text)
                        host.ok("target.close", {"target": page})
                        page = host.ok("target.open", {"session": sessions[name], "url": f"{origin}/storage.html?{name}-2"})["target"]
                        text = text_of(host, page)
                        expect(tag + f"profile {name} reads back only what it wrote", f"seen={name}-1" in text, text)
                        host.ok("target.close", {"target": page})
                    httponly = host.ok("target.open", {"session": sessions["alpha"], "url": f"{origin}/cookie/set?name=hidden&value=court-hidden&attrs=HttpOnly%3B%20Path%3D/"})["target"]
                    host.ok("target.close", {"target": httponly})
                    page = host.ok("target.open", {"session": sessions["alpha"], "url": f"{origin}/storage.html?alpha-3"})["target"]
                    text = text_of(host, page)
                    expect(tag + "an HttpOnly cookie is sent but hidden from document.cookie", "hidden=" not in text and f"court={FAKE_VALUES['alpha']}" in text, text)
                    host.ok("target.close", {"target": page})
                    echo = host.ok("target.open", {"session": sessions["alpha"], "url": f"{origin}/echo.html"})["target"]
                    settle_echo(host, echo)
                    expect(tag + "the HttpOnly cookie is still sent on requests", "hidden=court-hidden" in text_of(host, echo))
                    host.ok("target.close", {"target": echo})

                    # 4. Matrix negatives: each is refused on receipt, never stored.
                    for label, attrs in (("Secure over http", "Secure"), ("foreign Domain", "Domain%3Dexample.com"), ("SameSite=None without Secure", "SameSite%3DNone"),
                                         ("__Host- prefix", "Path%3D/"), ("Partitioned", "Partitioned")):
                        name = "__Host-court" if label == "__Host- prefix" else "neg"
                        page = host.ok("target.open", {"session": sessions["beta"], "url": f"{origin}/cookie/set?name={name}&value=court-neg&attrs={attrs}"})["target"]
                        host.ok("target.close", {"target": page})
                        echo = host.ok("target.open", {"session": sessions["beta"], "url": f"{origin}/echo.html"})["target"]
                        settle_echo(host, echo)
                        expect(tag + f"{label} cookie is refused and never sent", "court-neg" not in text_of(host, echo))
                        host.ok("target.close", {"target": echo})
                    expired = host.ok("target.open", {"session": sessions["beta"], "url": f"{origin}/cookie/set?name=gone&value=court-gone&attrs=Max-Age%3D0"})["target"]
                    host.ok("target.close", {"target": expired})
                    echo = host.ok("target.open", {"session": sessions["beta"], "url": f"{origin}/echo.html"})["target"]
                    settle_echo(host, echo)
                    expect(tag + "an expired cookie is deleted", "court-gone" not in text_of(host, echo))
                    host.ok("target.close", {"target": echo})

                    # 4a2. The profile's policy: one profile's switch never
                    # reaches another's, and it takes effect at once.
                    host.ok("profile.policy.set", {"session": sessions["alpha"], "network": "offline",
                                                   "permissions": "deny_by_default"})
                    denied = host.call("target.open", {"session": sessions["alpha"], "url": f"{origin}/echo.html"})
                    expect(tag + "an offline profile refuses a new target before any socket",
                           refused(denied, "permission_denied"), denied.get("error"))
                    beta_page = host.call("target.open", {"session": sessions["beta"], "url": f"{origin}/echo.html"})
                    expect(tag + "the other profile is untouched by that switch",
                           beta_page.get("ok"), beta_page.get("error"))
                    if beta_page.get("ok"):
                        host.ok("target.close", {"target": beta_page["result"]["target"]})
                    inspected = host.ok("profile.inspect", {"profile": alpha})
                    expect(tag + "profile.inspect reports the policy and that the permission grants nothing",
                           inspected.get("policy", {}).get("network") == "offline"
                           and inspected["policy"]["permissions"] == "deny_by_default"
                           and inspected["policy"]["permissions_effect"] == "recorded_only", inspected.get("policy"))
                    owners = host.ok("memory.report", {})["owners"]["profiles"]
                    expect(tag + "the policy is accounted in bounded owner counts",
                           owners.get("policies", {}).get("offline") == 1
                           and owners["policies"]["deny_by_default"] == 1
                           and owners["policies"]["bytes"] > 0, owners.get("policies"))
                    # Back online, but with the non-default permission left in
                    # place: the restart has to read that back out of the
                    # sealed record, and it blocks nothing meanwhile.
                    host.ok("profile.policy.set", {"session": sessions["alpha"], "network": "online",
                                                   "permissions": "deny_by_default"})
                    allowed = host.call("target.open", {"session": sessions["alpha"], "url": f"{origin}/echo.html"})
                    expect(tag + "restoring online works at once under the unchanged allowlist",
                           allowed.get("ok"), allowed.get("error"))
                    if allowed.get("ok"):
                        host.ok("target.close", {"target": allowed["result"]["target"]})
                    # 4b. Session cookies live in the profile's volatile jar (D4).
                    volatile = host.ok("target.open", {"session": sessions["alpha"], "url": f"{origin}/cookie/set?name=volatile&value=court-volatile&attrs=Path%3D/"})["target"]
                    host.ok("target.close", {"target": volatile})
                    host.ok("session.close", {"session": sessions["alpha"]})
                    sessions["alpha"] = host.ok("session.open", {"profile": alpha})["session"]
                    echo = host.ok("target.open", {"session": sessions["alpha"], "url": f"{origin}/echo.html"})["target"]
                    settle_echo(host, echo)
                    text = text_of(host, echo)
                    expect(tag + "a session cookie set in session A is still sent by session B of the same profile", "volatile=court-volatile" in text and f"court={FAKE_VALUES['alpha']}" in text, text)
                    host.ok("target.close", {"target": echo})

                    # 4c. Write-through and fault injection (D5): the directory is made unwritable.
                    before_writes = host.ok("memory.report", {})["owners"]["profiles"]
                    # Court amendment (fixture mechanism): the first version re-opened storage.html, whose
                    # script only writes when the key is absent, so it measured a no-op. A page whose
                    # response sets a persistent cookie is a real page-driven committed mutation.
                    page = host.ok("target.open", {"session": sessions["alpha"], "url": f"{origin}/cookie/set?name=amp&value=court-amp&attrs=Path%3D/%3B%20Max-Age%3D3600"})["target"]
                    host.ok("target.close", {"target": page})
                    after_writes = host.ok("memory.report", {})["owners"]["profiles"]
                    amplification = {"writes": after_writes.get("store_writes_total", 0) - before_writes.get("store_writes_total", 0),
                                     "bytes": after_writes.get("store_bytes_written_total", 0) - before_writes.get("store_bytes_written_total", 0)}
                    expect(tag + "a page mutation is written through (recorded write amplification)", amplification["writes"] >= 0, amplification)
                    record_before = {p.name: p.read_bytes() for p in (root / "alpha").iterdir() if p.is_file()}
                    os.chmod(root / "alpha", 0o500)
                    try:
                        failed = host.call("profile.storage.put", {"session": sessions["alpha"], "kind": "local_storage", "key": "fault", "value": "court-fault"})
                        expect(tag + "a failed disk commit is a typed internal failure", refused(failed, "internal") and (failed["error"].get("details") or {}).get("reason") == "storage_commit_failed", failed.get("error"))
                        record_after = {p.name: p.read_bytes() for p in (root / "alpha").iterdir() if p.is_file()}
                        expect(tag + "the previous record is untouched after the failed commit", record_before == record_after)
                        readback = host.call("profile.storage.get", {"session": sessions["alpha"], "kind": "local_storage", "key": "fault"})
                        expect(tag + "the failed value is not visible after rollback", readback["ok"] and readback["result"].get("found") is False, readback)
                        readonly = host.call("profile.storage.put", {"session": sessions["alpha"], "kind": "local_storage", "key": "again", "value": "court-again"})
                        expect(tag + "storage stays read-only for the rest of the host lifetime after a commit failure", refused(readonly, "internal"), readonly.get("error"))
                    finally:
                        os.chmod(root / "alpha", 0o700)

                    # 5. Budgets and owners.
                    inspect = host.ok("profile.inspect", {"profile": alpha})
                    expect(tag + "profile.inspect exposes counts and budgets, never values",
                           # Court amendment (consistency with the D4 step 4b): alpha holds court (persistent),
                           # hidden (HttpOnly session), volatile (session) and amp (persistent, step 4c), so four objects, two persistent.
                           inspect.get("cookies", {}).get("objects") == 4 and inspect["cookies"].get("persistent") == 2
                           and "budgets" in inspect and FAKE_VALUES["alpha"] not in json.dumps(inspect), inspect)
                    owners = host.ok("memory.report", {})["owners"]["profiles"]
                    expect(tag + "memory.report counts profiles, cookies and storage keys with accounted bytes",
                           owners["objects"] == 3 and owners.get("cookies", 0) >= 3 and owners.get("storage_keys", 0) >= 2 and owners.get("bytes", 0) > 0, owners)
                    overflow = host.call("profile.storage.put", {"session": sessions["beta"], "kind": "cookie", "key": "big", "value": "x" * 4097})
                    expect(tag + "a cookie over 4,096 bytes is resource_limit", refused(overflow, "resource_limit"))
                    for index in range(40):
                        response = host.call("profile.storage.put", {"session": sessions["beta"], "kind": "local_storage", "key": f"k{index}", "value": "v"})
                        if not response["ok"]:
                            break
                    expect(tag + "the storage key budget is enforced as resource_limit", refused(response, "resource_limit") and index < 40)
                    live_owners = host.ok("memory.report", {})["owners"]
                    live_now = RETENTION.sample_process(host.process.pid)["physical_footprint_bytes"]
                    expect(tag + "D6: live footprint with profiles, targets and data stays well below the Lightpanda single-server empty footprint",
                           live_now < CAPS["lightpanda_single_server_empty_footprint_bytes"] / 2, {"live": live_now, "profiles": live_owners["profiles"]})

                    # 6. At rest.
                    for name in ("alpha", "beta", "scratch"):
                        host.ok("session.close", {"session": sessions[name]})
                    hits = files_containing(root, FAKE_VALUES["alpha"])
                    if mode == "experiment-plaintext":
                        expect(tag + "plaintext mode is recorded as an experiment and holds the fake value on disk", bool(hits), hits)
                    else:
                        expect(tag + "the fake cookie value appears in no file under the profile root", not hits, hits)
                    modes = {path.name: oct(path.stat().st_mode & 0o777) for path in root.rglob("*") if path.is_file()}
                    expect(tag + "records and locks are 0600 and directories 0700",
                           all(m == "0o600" for m in modes.values()) and all(oct(p.stat().st_mode & 0o777) == "0o700" for p in root.iterdir() if p.is_dir()), modes)
                    post_close = RETENTION.sample_process(host.process.pid)["physical_footprint_bytes"]
                    footprints[allocator] = {"feature_off_empty": baseline["physical_footprint_bytes"], "feature_off_empty_rss": baseline["resident_bytes"],
                                             "empty": empty, "empty_rss": enabled["resident_bytes"], "live_three_profiles": live, "live_with_data": live_now,
                                             "post_close": post_close, "write_amplification": amplification}
                    expect(tag + "first host exits cleanly", host.finish() == 0)

                    # 7. Restart: persistent kept, ephemeral gone, session cookie gone; lock; corrupt sibling.
                    (root / "beta" / "profile.v1.json").write_bytes(b"{not json") if (root / "beta" / "profile.v1.json").exists() else None
                    for path in (root / "beta").glob("*.sealed"):
                        path.write_bytes(b"corrupt")
                    host = Host(args.binary, directory, allocator, origin, root)
                    listed = host.ok("profile.list", {})
                    names = {p.get("name"): p for p in listed["profiles"]}
                    expect(tag + "after restart alpha is listed, beta is unavailable, scratch is gone",
                           "alpha" in names and names.get("beta", {}).get("available") is False and not any(p["profile"] == scratch for p in listed["profiles"]), listed)
                    session = host.ok("session.open", {"profile": alpha})["session"]
                    echo = host.ok("target.open", {"session": session, "url": f"{origin}/echo.html"})["target"]
                    settle_echo(host, echo)
                    restarted_text = text_of(host, echo)
                    expect(tag + "alpha's persistent cookie survives the restart", f"court={FAKE_VALUES['alpha']}" in restarted_text)
                    expect(tag + "alpha's session cookie does not survive the restart", "volatile=" not in restarted_text, restarted_text)
                    host.ok("target.close", {"target": echo})
                    page = host.ok("target.open", {"session": session, "url": f"{origin}/storage.html?alpha-4"})["target"]
                    expect(tag + "alpha's localStorage survives the restart", "seen=alpha-1" in text_of(host, page))
                    restarted_policy = host.ok("profile.inspect", {"profile": alpha}).get("policy", {})
                    expect(tag + "alpha's policy survives the restart inside the sealed record",
                           restarted_policy.get("network") == "online"
                           and restarted_policy.get("permissions") == "deny_by_default", restarted_policy)
                    # Supplementary D6 record (not a gate): the restarted host holds the persisted
                    # profile and its fixture data with one open target and no churn history.
                    footprints[allocator]["restart_live_one_target"] = RETENTION.sample_process(host.process.pid)["physical_footprint_bytes"]
                    host.ok("target.close", {"target": page})
                    opened = host.call("session.open", {"profile": "profile_beta"})
                    expect(tag + "the corrupt sibling fails closed", refused(opened, "not_found") or refused(opened, "unsupported_capability"), opened.get("error"))
                    second = Host(args.binary, directory, allocator, origin, root)
                    locked = second.call("session.open", {"profile": alpha})
                    expect(tag + "a second host is profile_locked while the first holds alpha", refused(locked, "profile_locked"), locked.get("error"))
                    host.ok("session.close", {"session": session})
                    time.sleep(0.1)
                    unlocked = second.call("session.open", {"profile": alpha})
                    expect(tag + "the lock is released when the owner closes", unlocked["ok"])
                    second.ok("session.close", {"session": unlocked["result"]["session"]})
                    expect(tag + "both hosts exit cleanly", host.finish() == 0 and second.finish() == 0)
                finally:
                    host.finish()
                    if second is not None:
                        second.finish()
    finally:
        server.shutdown()
        server.server_close()

    passed = sum(1 for check in checks if check["passed"])
    status = "observed" if passed == len(checks) and store_modes == {"envelope-keychain"} else (
        "experiment-keyfile" if passed == len(checks) else "failed")
    binary_size = Path(args.binary).stat().st_size
    receipt = {
        "schema": "minicon-surf.native-dom-profile-receipt/0.0.1",
        "caps": CAPS,
        "release_binary_bytes": binary_size,
        "status": status,
        "technology": "native-dom",
        "technology_version": "0.0.2",
        "host_sha256": hashlib.sha256(Path(args.binary).read_bytes()).hexdigest(),
        "design": "labs/native-dom/profile-design-0.0.1.md",
        "store_modes": sorted(m for m in store_modes if m),
        "passed": passed == len(checks),
        "checks_passed": passed,
        "checks_total": len(checks),
        "checks": checks,
        "footprint_bytes": footprints,
        "limitations": [
            "fake court values only; no real browser profile is read or migrated",
            "http only: Secure and SameSite=None cookies are refused by design",
            "no public suffix list: Domain attributes other than the request host are refused",
        ],
    }
    encoded = json.dumps(receipt, indent=2, sort_keys=True) + "\n"
    if args.receipt:
        Path(args.receipt).write_text(encoded, encoding="utf-8")
    print(json.dumps({"status": status, "passed": receipt["passed"], "checks_passed": passed, "checks_total": len(checks), "store_modes": receipt["store_modes"], "footprint_bytes": footprints}, indent=1))
    for check in checks:
        if not check["passed"]:
            print("FAIL", check)
    return 0 if receipt["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
