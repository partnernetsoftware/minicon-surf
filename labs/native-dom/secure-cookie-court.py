#!/usr/bin/env python3
"""Frozen court: persistent Secure cookies across a host restart (P6 × HTTPS).

Pre-registered before it was run. The pinned-roots HTTPS slice and the
sealed persistent profile store already exist; this court asks whether a
`Secure` cookie set by a verified https origin into a persistent profile
survives a host restart with its rules intact:

  1. host A (pinned root, --profile-root): persistent `alpha` sets over
     https a persistent Secure cookie (Max-Age), a persistent plain cookie, a
     volatile Secure session cookie (no expiry), a short-lived persistent
     cookie (Max-Age=60) and a path-scoped cookie (Path=/other); the https
     echo carries the matching ones, the http echo of the same host only the
     plain one; a cookie with `Expires` in the past is deleted on receipt and
     `Max-Age=0` deletes an existing cookie; the record's clear-text envelope
     holds only the sealed fields (no precomputed send decision);
  2. negatives never touch jar or record: a wrong-name origin, an unpinned
     origin, `Secure` over http, and a failed https navigation (link to a
     wrong-name origin) leave `profile.inspect` counts and the record bytes
     unchanged;
  3. persistent `beta` is isolated: its https echo never carries alpha's
     cookies and alpha's never carries beta's;
  4. at rest no file under the profile root contains any fixture value,
     cookie name or localStorage marker;
  5. host B (same root, same pinned root, clock offset +120 s through
     MINICON_SURF_CLOCK_OFFSET_SECONDS so nothing sleeps) after A exits: the
     record is unsealed again through the keychain; alpha and beta list as
     available; alpha's https echo carries the persistent Secure and plain
     cookies but neither the volatile session cookie nor the expired
     short-lived one, nor the Path=/other cookie; its http echo only the
     plain one; an http document does not see the Secure cookie; a second
     allowlisted https host name (localhost, same server) receives none of
     the 127.0.0.1 cookies, so host, path and Secure are matched by the
     current rules at send time; beta stays isolated; the restarted host's
     first https fetch is a full handshake (the session cache is never
     persisted);
  6. host C (same root, no pinned root): https is unsupported_capability
     tls_no_pinned_roots and the http echo still carries only the plain
     cookie, so a persisted Secure cookie stays locked without TLS;
  7. owners are zero after every close, no descendant, hosts exit cleanly;
     footprints are recorded as diagnostics, not gated.

Default allocator and the arena. Disposable fixtures generated per run and
deleted afterwards; only fake values; the keychain item of each run is
deleted; the receipt refuses private-key blocks and the temporary path.
"""

import argparse
import hashlib
import json
import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "protocol"))

HTTPS = None


def load_module(name, path):
    import importlib.util
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    saved = sys.argv
    sys.argv = [name]
    try:
        spec.loader.exec_module(module)
    finally:
        sys.argv = saved
    return module


HTTPS = load_module("https_court", Path(__file__).with_name("https-court.py"))
ATTR = load_module("profile_attribution_court", Path(__file__).with_name("profile-attribution-court.py"))
TLS = HTTPS.TLS
RETENTION = HTTPS.RETENTION
Host = HTTPS.Host
text_of = HTTPS.text_of
refused = HTTPS.refused
settle = HTTPS.PROFILE_SETTLE
FAKE = {"alpha_secure": "court-alpha-locked-7f3a", "alpha_plain": "court-alpha-open-1b2c", "beta_secure": "court-beta-locked-2c9e", "beta_plain": "court-beta-open-4d5e",
        "volatile": "court-alpha-volatile-6a1f", "short": "court-alpha-short-3c4d", "pathed": "court-alpha-pathed-5e6f", "marker": "court-marker-9f8e"}
NAMES = ("courtlocked", "courtopen", "courtvolatile", "courtshort", "courtpathed")
SEALED_FIELDS = {"format", "protocol", "profile", "key_id", "dek_nonce", "dek_sealed", "record_nonce", "record_sealed"}
CLOCK_OFFSET = 120


def record_bytes(root, name):
    return {p.name: p.read_bytes() for p in (root / name).iterdir() if p.is_file()}


def record_hashes(root, name):
    return {k: hashlib.sha256(v).hexdigest() for k, v in record_bytes(root, name).items()}


def host_with_clock(binary, directory, allocator, origins, root, pinned_roots=(), clock_offset=0):
    saved = os.environ.get("MINICON_SURF_CLOCK_OFFSET_SECONDS")
    os.environ["MINICON_SURF_CLOCK_OFFSET_SECONDS"] = str(clock_offset)
    try:
        return Host(binary, directory, allocator, origins, root, pinned_roots=pinned_roots)
    finally:
        if saved is None:
            os.environ.pop("MINICON_SURF_CLOCK_OFFSET_SECONDS", None)
        else:
            os.environ["MINICON_SURF_CLOCK_OFFSET_SECONDS"] = saved


def files_containing(root, needle):
    return [str(p.relative_to(root)) for p in Path(root).rglob("*") if p.is_file() and needle.encode() in p.read_bytes()]


def echo_text(host, session, origin):
    target = host.ok("target.open", {"session": session, "url": f"{origin}/echo.html"})["target"]
    settle(host, target)
    text = text_of(host, target)
    host.ok("target.close", {"target": target})
    return text


def set_cookie(host, session, origin, name, value, attrs):
    response = host.call("target.open", {"session": session, "url": f"{origin}/cookie/set?name={name}&value={value}&attrs={attrs}"})
    if response["ok"]:
        host.ok("target.close", {"target": response["result"]["target"]})
    return response


def footprint(host):
    time.sleep(0.03)
    return RETENTION.sample_process(host.process.pid)["physical_footprint_bytes"]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--binary", required=True)
    parser.add_argument("--receipt", required=True)
    parser.add_argument("--tls-fixture-dir", default=None)
    args = parser.parse_args()
    checks, footprints, keychain_deleted = [], {}, 0

    def expect(name, condition, detail=None):
        checks.append({"check": name, "passed": bool(condition), **({"detail": detail} if detail is not None else {})})

    fixtures = TLS.Fixtures(args.tls_fixture_dir)
    servers = []
    try:
        https_server, https_port = HTTPS.start_server(fixtures, "loopback.pem", "loopback.key")
        wrong_server, wrong_port = HTTPS.start_server(fixtures, "wrong-name.pem", "wrong-name.key")
        other_server, other_port = HTTPS.start_server(fixtures, "other-ca-loopback.pem", "other-ca-loopback.key")
        http_server, http_port = HTTPS.start_server(fixtures)
        servers += [https_server, wrong_server, other_server, http_server]
        https, http = f"https://127.0.0.1:{https_port}", f"http://127.0.0.1:{http_port}"
        wrong, other = f"https://127.0.0.1:{wrong_port}", f"https://127.0.0.1:{other_port}"
        # The same TLS server under its DNS SAN: another host name for the cookie rules.
        https_localhost = f"https://localhost:{https_port}"
        origins = [https, wrong, other, http, https_localhost]
        pin = [fixtures.path("court-ca.pem")]

        for allocator in ("system", "arena"):
            tag = f"[{allocator}] "
            with tempfile.TemporaryDirectory(prefix="minicon-surf-secure-cookie-court-") as directory:
                root = Path(directory) / "profiles"
                host = second = third = None
                try:
                    # 1. Host A: persistent profiles, Secure and plain cookies over https.
                    host = Host(args.binary, directory, allocator, origins, root, pinned_roots=pin)
                    alpha = host.ok("profile.create", {"persistence": "persistent", "name": "alpha"})["profile"]
                    beta = host.ok("profile.create", {"persistence": "persistent", "name": "beta"})["profile"]
                    sa = host.ok("session.open", {"profile": alpha})["session"]
                    sb = host.ok("session.open", {"profile": beta})["session"]
                    expect(tag + "alpha sets a persistent Secure cookie over https", set_cookie(host, sa, https, "courtlocked", FAKE["alpha_secure"], "Secure%3B%20Path%3D/%3B%20Max-Age%3D3600")["ok"])
                    expect(tag + "alpha sets a persistent plain cookie over https", set_cookie(host, sa, https, "courtopen", FAKE["alpha_plain"], "Path%3D/%3B%20Max-Age%3D3600")["ok"])
                    expect(tag + "alpha sets a volatile Secure session cookie over https", set_cookie(host, sa, https, "courtvolatile", FAKE["volatile"], "Secure%3B%20Path%3D/")["ok"])
                    expect(tag + "alpha sets a short-lived persistent cookie (Max-Age=60)", set_cookie(host, sa, https, "courtshort", FAKE["short"], "Secure%3B%20Path%3D/%3B%20Max-Age%3D60")["ok"])
                    expect(tag + "alpha sets a Path=/other cookie", set_cookie(host, sa, https, "courtpathed", FAKE["pathed"], "Secure%3B%20Path%3D/other%3B%20Max-Age%3D3600")["ok"])
                    expect(tag + "a cookie with Expires in the past is deleted on receipt", set_cookie(host, sa, https, "courtpast", "court-past", "Secure%3B%20Path%3D/%3B%20Expires%3DThu%2C%2001%20Jan%202015%2000%3A00%3A00%20GMT")["ok"])
                    expect(tag + "a cookie to be deleted is first set", set_cookie(host, sa, https, "courtgone", "court-gone", "Path%3D/%3B%20Max-Age%3D3600")["ok"])
                    expect(tag + "Max-Age=0 deletes the existing cookie", set_cookie(host, sa, https, "courtgone", "court-gone", "Path%3D/%3B%20Max-Age%3D0")["ok"])
                    https_text, http_text = echo_text(host, sa, https), echo_text(host, sa, http)
                    expect(tag + "the https echo carries the persistent Secure, plain, volatile and short-lived cookies and neither the path-scoped, the past-expired nor the deleted one",
                           all(f"{n}={v}" in https_text for n, v in (("courtlocked", FAKE["alpha_secure"]), ("courtopen", FAKE["alpha_plain"]), ("courtvolatile", FAKE["volatile"]), ("courtshort", FAKE["short"])))
                           and "courtpathed=" not in https_text and "courtpast=" not in https_text and "courtgone=" not in https_text, https_text)
                    expect(tag + "the http echo of the same host carries only the plain cookie", "courtlocked=" not in http_text and "courtvolatile=" not in http_text and "courtshort=" not in http_text and f"courtopen={FAKE['alpha_plain']}" in http_text, http_text)
                    marker = host.ok("target.open", {"session": sa, "url": f"{https}/storage.html?{FAKE['marker']}"})["target"]
                    host.ok("target.close", {"target": marker})
                    inspect_before = host.ok("profile.inspect", {"profile": alpha})["cookies"]
                    expect(tag + "profile.inspect counts four persistent and one volatile cookie and shows no value",
                           inspect_before.get("persistent") == 4 and inspect_before.get("volatile") == 1 and not any(v in json.dumps(inspect_before) for v in FAKE.values()), inspect_before)
                    record_before = record_bytes(root, "alpha")
                    sealed = json.loads(record_before["profile.v1.sealed"])
                    expect(tag + "the record's clear-text envelope holds only the sealed fields, no send decision", set(sealed) == SEALED_FIELDS, sorted(sealed))

                    # 2. Negatives touch neither jar nor record.
                    for label, origin in (("a wrong-name origin", wrong), ("an unpinned origin", other)):
                        response = set_cookie(host, sa, origin, "neg", "court-neg", "Secure%3B%20Path%3D/%3B%20Max-Age%3D3600")
                        expect(tag + f"{label} cannot set a cookie: permission_denied", refused(response, "permission_denied"), response.get("error"))
                    response = set_cookie(host, sa, http, "neg2", "court-neg2", "Secure%3B%20Path%3D/%3B%20Max-Age%3D3600")
                    expect(tag + "Secure over http is refused on receipt (the page loads, the cookie is dropped)", response["ok"])
                    import urllib.parse
                    link = host.ok("target.open", {"session": sa, "url": f"{https}/link.html?href={urllib.parse.quote(wrong + '/cookie/set?name=neg3&value=court-neg3&attrs=Secure', safe='')}"})
                    snapshot = host.ok("target.snapshot", {"target": link["target"], "format": "semantic", "max_bytes": 65536, "max_nodes": 48})
                    anchor = next((n for n in snapshot["nodes"] if n.get("role") == "link"), None)
                    clicked = host.call("target.act", {"target": link["target"], "reference": anchor["reference"], "action": {"kind": "click"}}, 8000) if anchor else {"ok": False}
                    expect(tag + "a failed https navigation is a typed failure", not clicked["ok"], clicked.get("error"))
                    host.ok("target.close", {"target": link["target"]})
                    inspect_after = host.ok("profile.inspect", {"profile": alpha})["cookies"]
                    expect(tag + "after the negatives the jar counts are unchanged", inspect_after == inspect_before, {"before": inspect_before, "after": inspect_after})
                    expect(tag + "after the negatives the record bytes and hashes are unchanged",
                           record_bytes(root, "alpha") == record_before and record_hashes(root, "alpha") == {k: hashlib.sha256(v).hexdigest() for k, v in record_before.items()})
                    matrix = echo_text(host, sa, https)
                    expect(tag + "after the negatives the in-memory jar sends the same cookies and no negative value", "court-neg" not in matrix and f"courtlocked={FAKE['alpha_secure']}" in matrix, matrix)

                    # 3. beta isolation.
                    expect(tag + "beta sets its own Secure and plain cookies at the same URLs", set_cookie(host, sb, https, "courtlocked", FAKE["beta_secure"], "Secure%3B%20Path%3D/%3B%20Max-Age%3D3600")["ok"]
                           and set_cookie(host, sb, https, "courtopen", FAKE["beta_plain"], "Path%3D/%3B%20Max-Age%3D3600")["ok"])
                    beta_text, alpha_text = echo_text(host, sb, https), echo_text(host, sa, https)
                    expect(tag + "beta's https echo carries only beta's cookies", FAKE["beta_secure"] in beta_text and FAKE["alpha_secure"] not in beta_text and FAKE["alpha_plain"] not in beta_text, beta_text)
                    expect(tag + "alpha's https echo carries only alpha's cookies", FAKE["alpha_secure"] in alpha_text and FAKE["beta_secure"] not in alpha_text, alpha_text)

                    # 4. At rest.
                    hits = {needle: files_containing(root, needle) for needle in list(FAKE.values()) + list(NAMES)}
                    expect(tag + "no fixture value, cookie name or localStorage marker appears in any file under the profile root", not any(hits.values()), {k: v for k, v in hits.items() if v})
                    footprints.setdefault(allocator, {})["host_a_live"] = footprint(host)
                    for session in (sa, sb):
                        host.ok("session.close", {"session": session})
                    report = host.ok("memory.report", {})
                    expect(tag + "host A: owners zero and no live TLS connection after the closes",
                           report["owners"]["targets"]["objects"] == 0 and report["owners"]["network"]["tls"]["live_connections"] == 0)
                    expect(tag + "host A exits cleanly with no descendant", host.finish() == 0 and host.sampler.max_descendants == 0)

                    # 5. Host B: restart with the pinned root.
                    second = host_with_clock(args.binary, directory, allocator, origins, root, pinned_roots=pin, clock_offset=CLOCK_OFFSET)
                    listed = {p["name"]: p for p in second.ok("profile.list", {})["profiles"]}
                    expect(tag + "after the restart alpha and beta are available", listed.get("alpha", {}).get("available") is True and listed.get("beta", {}).get("available") is True, listed)
                    sa2 = second.ok("session.open", {"profile": alpha})["session"]
                    sb2 = second.ok("session.open", {"profile": beta})["session"]
                    tls_before = second.ok("memory.report", {})["owners"]["network"]["tls"]
                    https_text = echo_text(second, sa2, https)
                    tls_after = second.ok("memory.report", {})["owners"]["network"]["tls"]
                    expect(tag + "restart: the restarted host's first https fetch is a full handshake (no persisted session cache)",
                           tls_after["handshakes_total"] >= 1 and tls_after["resumed_total"] == tls_before["resumed_total"] + max(0, tls_after["handshakes_total"] - tls_before["handshakes_total"] - 1)
                           and tls_after["handshakes_total"] - tls_after["resumed_total"] >= 1, {"before": tls_before, "after": tls_after})
                    http_text = echo_text(second, sa2, http)
                    expect(tag + "restart: the record was unsealed through the keychain (store envelope-keychain, alpha available)", second.ok("profile.inspect", {"profile": alpha}).get("store") == "envelope-keychain")
                    expect(tag + "restart: alpha's https echo carries the persistent Secure and plain cookies only: the volatile, the expired short-lived and the path-scoped ones are absent",
                           f"courtlocked={FAKE['alpha_secure']}" in https_text and f"courtopen={FAKE['alpha_plain']}" in https_text and "courtvolatile=" not in https_text and "courtshort=" not in https_text and "courtpathed=" not in https_text, https_text)
                    expect(tag + "restart: alpha's http echo carries only the plain cookie", "courtlocked=" not in http_text and f"courtopen={FAKE['alpha_plain']}" in http_text, http_text)
                    counts = second.ok("profile.inspect", {"profile": alpha})["cookies"]
                    expect(tag + "restart: the expired short-lived cookie is gone and the volatile one was never persisted (three persistent, zero volatile)", counts.get("persistent") == 3 and counts.get("volatile") == 0, counts)
                    other_host = echo_text(second, sa2, https_localhost)
                    expect(tag + "restart: the same server under another host name receives none of the 127.0.0.1 cookies", "courtlocked=" not in other_host and "courtopen=" not in other_host, other_host)
                    doc = second.ok("target.open", {"session": sa2, "url": f"{http}/storage.html?alpha-1"})["target"]
                    doc_text = text_of(second, doc)
                    second.ok("target.close", {"target": doc})
                    expect(tag + "restart: an http document does not see the Secure cookie", "locked=" not in doc_text and f"open={FAKE['alpha_plain']}" in doc_text, doc_text)
                    beta_text = echo_text(second, sb2, https)
                    expect(tag + "restart: beta stays isolated", FAKE["beta_secure"] in beta_text and FAKE["alpha_secure"] not in beta_text, beta_text)
                    footprints[allocator]["host_b_live"] = footprint(second)
                    for session in (sa2, sb2):
                        second.ok("session.close", {"session": session})
                    expect(tag + "host B exits cleanly with no descendant", second.finish() == 0 and second.sampler.max_descendants == 0)

                    # 6. Host C: no pinned root.
                    third = Host(args.binary, directory, allocator, origins, root)
                    sa3 = third.ok("session.open", {"profile": alpha})["session"]
                    locked_out = third.call("target.open", {"session": sa3, "url": f"{https}/echo.html"})
                    expect(tag + "without a pinned root https is unsupported_capability tls_no_pinned_roots", refused(locked_out, "unsupported_capability", "tls_no_pinned_roots"), locked_out.get("error"))
                    http_text = echo_text(third, sa3, http)
                    expect(tag + "without a pinned root the persisted Secure cookie stays locked: only the plain cookie is sent over http", "locked=" not in http_text and f"open={FAKE['alpha_plain']}" in http_text, http_text)
                    expect(tag + "without a pinned root the Secure cookie is still counted in the profile", third.ok("profile.inspect", {"profile": alpha})["cookies"].get("persistent") == 2)
                    third.ok("session.close", {"session": sa3})
                    expect(tag + "host C exits cleanly", third.finish() == 0)
                finally:
                    for h in (host, second, third):
                        if h is not None and h.process.poll() is None:
                            h.finish()
                    if ATTR.delete_keychain_item(root):
                        keychain_deleted += 1
    finally:
        for server in servers:
            server.shutdown()
        fixtures.cleanup()

    receipt = {
        "schema": "minicon-surf.native-dom-secure-cookie-receipt/0.0.1",
        "technology": "native-dom",
        "technology_version": "0.0.2",
        "host_sha256": hashlib.sha256(Path(args.binary).read_bytes()).hexdigest(),
        "design": "labs/native-dom/https-design-0.0.1.md (section 2, cookies) and profile-design-0.0.1.md (D1-D5)",
        "fixtures": {"mode": "generated-disposable" if fixtures.temporary else "explicit-private-directory", "generation_seconds": fixtures.generation_seconds,
                     "public_certificates": fixtures.evidence},
        "keychain_items_deleted": keychain_deleted,
        "checks": checks,
        "checks_passed": sum(1 for c in checks if c["passed"]),
        "checks_total": len(checks),
        "passed": all(c["passed"] for c in checks),
        "footprint_bytes": footprints,
        "limitations": [
            "pinned test roots on loopback only; no system roots, no public web",
            "fake cookie values only; no real profile is read or migrated",
            "one platform, one fixture set; footprints are diagnostics, not gates",
        ],
    }
    text = json.dumps(receipt, indent=1, sort_keys=True) + "\n"
    TLS.refuse_private_material(text, fixtures)
    Path(args.receipt).write_text(text)
    failed = [c for c in checks if not c["passed"]]
    print(json.dumps({"passed": receipt["passed"], "checks_passed": receipt["checks_passed"], "checks_total": receipt["checks_total"], "footprint_bytes": footprints}, indent=1))
    for check in failed:
        print("FAIL", json.dumps(check)[:500])
    return 0 if receipt["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
