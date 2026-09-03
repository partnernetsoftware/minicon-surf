#!/usr/bin/env python3
"""Frozen court for the native route's pinned-roots HTTPS slice (rustls + ring).

Pre-registered before the implementation, per the design
`https-design-0.0.1.md` (sections 2 and 6) and cdx-k68's verdict. The host
under test enables HTTPS only when `--pinned-root FILE` (public certificates)
and an explicit `--allow-origin https://…` are given; without a pinned root
every https fetch is `unsupported_capability` (`tls_no_pinned_roots`). No
system roots, no public web, loopback only.

Key material: the court generates disposable fixtures (a test CA, a loopback
leaf with IP and DNS names, a wrong-name leaf, a second unpinned CA) with the
openssl command line into a private temporary directory before any host
process starts and deletes it at the end, also on failure; only public
certificate evidence reaches the receipt; the receipt writer refuses
private-key blocks and the temporary path. Nothing is downloaded.

Stages and checks, under the default allocator and the opt-in arena, feature
off (no pinned root) and enabled:

  1. a document over https from the pinned origin loads, its same-origin
     script fetch runs over https, TLS 1.3 negotiated; a TLS 1.2-only server
     negotiates TLS 1.2;
  2. negatives, each a typed refusal before any HTTP and never a crash:
     wrong name (tls_hostname_mismatch), unpinned issuer (tls_untrusted_root),
     TLS 1.1-only (tls_protocol, when the local OpenSSL can serve it), ALPN
     h2-only (tls_alpn), https without a pinned root (tls_no_pinned_roots),
     redirect https → http (redirect_downgrade), redirect to a private
     address (address), redirect loop and count cap, deadline, body cap,
     header cap: all unchanged from the http cell;
  3. session reuse: the second https fetch of the same profile resumes; a
     second profile's first https fetch is a full handshake (no cross-profile
     cache);
  4. Secure cookies: set over https, sent back over https, never sent to the
     http origin of the same host, hidden from an http document;
     `SameSite=None; Secure` accepted over https, `SameSite=None` without
     `Secure` refused, `Secure` over http still refused; `Domain` stays
     exact-host;
  5. a failed https navigation (link to a wrong-name origin) leaves frame,
     generation, realm, revision and jar unchanged;
  6. memory, complete process tree: enabled empty over feature-off empty,
     first https target over first http target of the same page, eight https
     targets against eight http targets per target, post-close libmalloc
     in-use enabled against feature-off, owners at zero, no descendant;
  7. the binary and dependency deltas are recorded, not gated.

Pre-registered host increments (physical footprint unless stated):
  H1 enabled empty over feature-off empty          <= 524,288
  H2 first https target over first http target     <= 1,048,576
  H3 eight https targets over eight http, per target <= 131,072
  H4 post-close libmalloc in-use, enabled - off    <= 65,536
Exceeding one keeps the slice opt-in and narrow; the caps do not move.
"""

import argparse
import hashlib
import http.server
import importlib.util
import json
import os
import re
import ssl
import subprocess
import sys
import tempfile
import threading
import time
import urllib.parse
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


PROFILE = load_module("profile_court", Path(__file__).with_name("profile-court.py"))
HELPER = load_module("profile_helper_court", Path(__file__).with_name("profile-helper-court.py"))
TLS = load_module("tls_court", ROOT / "labs" / "tls-court" / "court.py")
RETENTION = PROFILE.RETENTION
FIXTURE_ROOT = PROFILE.FIXTURE_ROOT
PRIVATE_KEY_BLOCK = re.compile(r"BEGIN (RSA |EC |OPENSSH |ENCRYPTED )?PRIVATE KEY")
FAKE = {"alpha": "court-alpha-7f3a", "beta": "court-beta-2c9e"}
CAPS = {
    "h1_enabled_empty_over_off_bytes": 524288,
    "h2_first_https_over_first_http_bytes": 1048576,
    "h3_eight_https_over_eight_http_per_target_bytes": 131072,
    "h4_post_close_in_use_enabled_over_off_bytes": 65536,
}


class HttpsHandler(PROFILE.ProfileHandler):
    """The profile court's pages plus redirect and bound probes for the TLS cell."""

    def do_GET(self):
        path, _, query = self.path.partition("?")
        params = dict(item.split("=", 1) for item in query.split("&") if "=" in item)
        if path == "/redirect-to":
            return self.reply(302, extra=[("Location", urllib.parse.unquote(params.get("url", "/")))])
        if path == "/link.html":
            target = urllib.parse.unquote(params.get("href", "/"))
            body = (b"<!doctype html><html><body><main><h1>Link</h1><p id=\"state\">before</p>"
                    b"<a id=\"go\" href=\"" + target.encode() + b"\">go</a></main></body></html>")
            return self.reply(200, body)
        if path == "/headers":
            # Court amendment (recorded, after the header-cap fix): a response whose header
            # section is exactly `section` bytes, terminator included, so the cap is probed
            # at cap-1, cap and cap+1 rather than only with an oversized block.
            section = int(params.get("section", "0"))
            body = b"ok"
            fixed = f"HTTP/1.1 200 OK\r\nContent-Type: text/html\r\nContent-Length: {len(body)}\r\nX-Pad: ".encode()
            padding = section - len(fixed) - 4
            self.wfile.write(fixed + b"y" * padding + b"\r\n\r\n" + body)
            self.close_connection = True
            return None
        if path == "/bigheaders":
            # Court amendment (mechanism): the header cap is checked per 8 KiB chunk while the
            # header end is not yet seen, so the block must exceed the cap by more than a chunk.
            return self.reply(200, b"<!doctype html><h1>headers</h1>", extra=[(f"X-Pad-{i}", "y" * 1000) for i in range(40)])
        return super().do_GET()


class QuietServer(http.server.ThreadingHTTPServer):
    """Refused TLS handshakes and byte-capped clients reset connections by design; keep the log clean."""

    def handle_error(self, request, client_address):
        pass


def start_server(fixtures, cert=None, key=None, alpn=("http/1.1",), minimum=ssl.TLSVersion.TLSv1_2, maximum=None, seclevel0=False):
    server = QuietServer(("127.0.0.1", 0), HttpsHandler)
    server.daemon_threads = True
    if cert is not None:
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.minimum_version = minimum
        if maximum is not None:
            context.maximum_version = maximum
        if seclevel0:
            context.set_ciphers("ALL:@SECLEVEL=0")
        context.load_cert_chain(fixtures.path(cert), fixtures.path(key))
        if alpn:
            context.set_alpn_protocols(list(alpn))
        server.socket = context.wrap_socket(server.socket, server_side=True)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, server.server_address[1]


class Host(PROFILE.Host):
    """The profile court's host with pinned roots and several allowed origins."""

    def __init__(self, binary, directory, allocator, origins, profile_root, pinned_roots=()):
        environment = dict(os.environ)
        for knob in ("MINICON_SURF_NATIVE_REALM_ZONE", "MINICON_SURF_NATIVE_REALM_ARENA", "MINICON_SURF_PROFILE_STORE"):
            environment.pop(knob, None)
        if allocator == "arena":
            environment["MINICON_SURF_NATIVE_REALM_ARENA"] = "1"
        command = [binary, "serve", "--stdio", "--fixture-root", str(FIXTURE_ROOT), "--config-dir", str(Path(directory) / "config")]
        for origin in origins:
            command += ["--allow-origin", origin]
        if profile_root is not None:
            command += ["--profile-root", str(profile_root)]
        for root in pinned_roots:
            command += ["--pinned-root", str(root)]
        self.process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, env=environment)
        self.counter = 0
        self.sampler = HELPER.TreeSampler(self.process.pid, binary)
        self.sampler.start()

    def finish(self):
        code = super().finish()
        self.sampler.stop()
        return code


def text_of(host, target):
    snapshot = host.ok("target.snapshot", {"target": target, "format": "semantic", "max_bytes": 65536, "max_nodes": 48})
    return " ".join(n["name"] for n in snapshot["nodes"] if n["role"] == "text")


def footprint(host):
    time.sleep(0.03)
    return RETENTION.sample_process(host.process.pid)["physical_footprint_bytes"]


def in_use(host):
    return host.ok("memory.report", {})["libmalloc"]["size_in_use"]


def tls_owner(host):
    return host.ok("memory.report", {})["owners"]["network"].get("tls")


def refused(response, code, reason=None):
    if response["ok"]:
        return False
    error = response["error"]
    if error["code"] != code:
        return False
    return reason is None or (error.get("details") or {}).get("reason") == reason


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--binary", required=True)
    parser.add_argument("--receipt", required=True)
    parser.add_argument("--tls-fixture-dir", default=None, help="explicit private fixture directory outside the repository; otherwise disposable fixtures are generated")
    args = parser.parse_args()

    checks, footprints = [], {}

    def expect(name, condition, detail=None):
        checks.append({"check": name, "passed": bool(condition), **({"detail": detail} if detail is not None else {})})

    fixtures = TLS.Fixtures(args.tls_fixture_dir)
    servers = []
    try:
        https_server, https_port = start_server(fixtures, "loopback.pem", "loopback.key")
        tls12_server, tls12_port = start_server(fixtures, "loopback.pem", "loopback.key", maximum=ssl.TLSVersion.TLSv1_2)
        wrong_server, wrong_port = start_server(fixtures, "wrong-name.pem", "wrong-name.key")
        other_server, other_port = start_server(fixtures, "other-ca-loopback.pem", "other-ca-loopback.key")
        h2_server, h2_port = start_server(fixtures, "loopback.pem", "loopback.key", alpn=("h2",))
        http_server, http_port = start_server(fixtures)
        servers += [https_server, tls12_server, wrong_server, other_server, h2_server, http_server]
        try:
            tls11_server, tls11_port = start_server(fixtures, "loopback.pem", "loopback.key", minimum=ssl.TLSVersion.TLSv1, maximum=ssl.TLSVersion.TLSv1_1, seclevel0=True)
            servers.append(tls11_server)
        except (ssl.SSLError, ValueError, OSError):
            tls11_port = None
        https = f"https://127.0.0.1:{https_port}"
        http = f"http://127.0.0.1:{http_port}"
        origins = [https, f"https://127.0.0.1:{tls12_port}", f"https://127.0.0.1:{wrong_port}", f"https://127.0.0.1:{other_port}",
                   f"https://127.0.0.1:{h2_port}", http] + ([f"https://127.0.0.1:{tls11_port}"] if tls11_port else [])

        for allocator in ("system", "arena"):
            tag = f"[{allocator}] "
            with tempfile.TemporaryDirectory(prefix="minicon-surf-https-court-") as directory:
                # Feature off: the same origins, no pinned root.
                off = Host(args.binary, directory, allocator, origins, None)
                # Court amendment (mechanism): the empty sample follows the first request, so a host
                # still starting up is not measured against one that has answered already.
                owner = tls_owner(off)
                off_empty = footprint(off)
                expect(tag + "feature off: the network owner reports TLS disabled with zero pinned roots",
                       owner is not None and owner.get("enabled") is False and owner.get("pinned_roots") == 0, owner)
                profile = off.ok("profile.create", {"persistence": "ephemeral"})["profile"]
                session = off.ok("session.open", {"profile": profile})["session"]
                opened = off.call("target.open", {"session": session, "url": f"{https}/index.html"})
                expect(tag + "feature off: an https target is unsupported_capability tls_no_pinned_roots",
                       refused(opened, "unsupported_capability", "tls_no_pinned_roots"), opened.get("error"))
                http_targets = []
                for _ in range(8):
                    http_targets.append(off.ok("target.open", {"session": session, "url": f"{http}/index.html"})["target"])
                    if len(http_targets) == 1:
                        off_first = footprint(off)
                off_eight = footprint(off)
                for target in http_targets:
                    off.ok("target.close", {"target": target})
                off_post_close_in_use = in_use(off)
                off_post_close = footprint(off)
                expect(tag + "feature off: owners are zero after the closes", off.ok("memory.report", {})["owners"]["targets"]["objects"] == 0)
                expect(tag + "feature off: host exits cleanly", off.finish() == 0 and off.sampler.max_descendants == 0)

                # Enabled: pinned root and explicit https origins.
                # Court amendment (mechanism): both hosts use ephemeral profiles, so the keychain's
                # one-time first-use cost of a persistent profile cannot enter the H deltas.
                host = Host(args.binary, directory, allocator, origins, None, pinned_roots=[fixtures.path("court-ca.pem")])
                owner = tls_owner(host)
                on_empty = footprint(host)
                expect(tag + "enabled: the network owner reports TLS enabled with one pinned root and a bounded per-profile cache of 16",
                       owner is not None and owner.get("enabled") is True and owner.get("pinned_roots") == 1
                       and owner.get("session_cache_entries_per_profile") == 16 and owner.get("provider") == "ring", owner)
                h1 = on_empty - off_empty
                expect(tag + "H1 enabled empty over feature-off empty", h1 <= CAPS["h1_enabled_empty_over_off_bytes"], {"delta": h1})

                alpha = host.ok("profile.create", {"persistence": "ephemeral"})["profile"]
                beta = host.ok("profile.create", {"persistence": "ephemeral"})["profile"]
                sessions = {"alpha": host.ok("session.open", {"profile": alpha})["session"], "beta": host.ok("session.open", {"profile": beta})["session"]}

                # 1. Document and script over https, versions.
                page = host.ok("target.open", {"session": sessions["alpha"], "url": f"{https}/index.html"})
                on_first = footprint(host)
                inspect = host.ok("target.inspect", {"target": page["target"]})
                tls = inspect["network"].get("tls", {})
                expect(tag + "an https document and its same-origin script load over TLS 1.3",
                       page["network"]["fetches"] >= 2 and tls.get("handshakes_total", 0) >= 2 and tls.get("tls13_total", 0) >= 2 and tls.get("refused_total", 0) == 0,
                       {"network": page["network"], "tls": tls})
                first_text = text_of(host, page["target"])
                expect(tag + "the https document renders the same representative page as http", "Representative" in first_text or "results" in first_text.lower(), first_text[:120])
                tls12 = host.ok("target.open", {"session": sessions["alpha"], "url": f"https://127.0.0.1:{tls12_port}/index.html"})
                tls12_tls = host.ok("target.inspect", {"target": tls12["target"]})["network"].get("tls", {})
                expect(tag + "a TLS 1.2-only origin negotiates TLS 1.2", tls12_tls.get("tls12_total", 0) >= 1 and tls12_tls.get("refused_total", 0) == 0, tls12_tls)
                host.ok("target.close", {"target": tls12["target"]})

                # 2. Negatives before any HTTP.
                for label, url, code, reason in (
                        ("wrong name", f"https://127.0.0.1:{wrong_port}/index.html", "permission_denied", "tls_hostname_mismatch"),
                        ("unpinned issuer", f"https://127.0.0.1:{other_port}/index.html", "permission_denied", "tls_untrusted_root"),
                        ("ALPN h2-only", f"https://127.0.0.1:{h2_port}/index.html", "permission_denied", "tls_alpn"),
                        ("redirect https to http", f"{https}/redirect-to?url={urllib.parse.quote(http + '/index.html', safe='')}", "permission_denied", "redirect_downgrade"),
                        # Court amendment (mechanism): the fixture's /redirect-private points at http, which an https
                        # origin refuses as a downgrade first; the private-address rule is exercised with an https target.
                        ("redirect to a private address", f"{https}/redirect-to?url={urllib.parse.quote('https://10.0.0.1/', safe='')}", "permission_denied", "address"),
                        ("redirect loop", f"{https}/redirect-loop", "resource_limit", "redirect-count"),
                        ("body cap", f"{https}/big", "resource_limit", "response-bytes"),
                        ("header cap", f"{https}/bigheaders", "resource_limit", "header-bytes"),
                        ("deadline", f"{https}/slow", "deadline_exceeded", None)):
                    response = host.call("target.open", {"session": sessions["alpha"], "url": url}, 8000)
                    expect(tag + f"{label} is refused as {code}" + (f" {reason}" if reason else ""), refused(response, code, reason), response.get("error"))
                cap = 16 * 1024
                for section, expect_ok in ((cap - 1, True), (cap, True), (cap + 1, False)):
                    response = host.call("target.open", {"session": sessions["alpha"], "url": f"{https}/headers?section={section}"}, 8000)
                    if expect_ok:
                        expect(tag + f"a header section of {section} bytes (cap {cap}) is accepted", response["ok"], response.get("error"))
                        if response["ok"]:
                            host.ok("target.close", {"target": response["result"]["target"]})
                    else:
                        expect(tag + f"a header section of {section} bytes (cap + 1) is refused as resource_limit header-bytes",
                               refused(response, "resource_limit", "header-bytes"), response.get("error"))
                if tls11_port:
                    response = host.call("target.open", {"session": sessions["alpha"], "url": f"https://127.0.0.1:{tls11_port}/index.html"})
                    expect(tag + "TLS 1.1-only is refused as permission_denied tls_protocol", refused(response, "permission_denied", "tls_protocol"), response.get("error"))
                else:
                    expect(tag + "TLS 1.1-only server not exercised: the local OpenSSL refuses to serve it (recorded)", True)
                expect(tag + "a refused TLS fetch never reveals a path, a certificate or a crypto internal",
                       not any(s in json.dumps(checks[-12:]) for s in ("BEGIN", fixtures.directory, "/var/folders", "webpki", "ring::")))
                redirect = host.ok("target.open", {"session": sessions["alpha"], "url": f"{https}/redirect/2"})
                landed = host.ok("target.snapshot", {"target": redirect["target"], "format": "semantic", "max_bytes": 65536, "max_nodes": 48})
                expect(tag + "https redirects within the cap are followed with each hop re-authorized",
                       redirect["network"]["fetches"] >= 1 and any("Redirect landed" in n.get("name", "") for n in landed["nodes"]), [n.get("name") for n in landed["nodes"]])
                host.ok("target.close", {"target": redirect["target"]})

                # 3. Session reuse per profile.
                before = host.ok("memory.report", {})["owners"]["network"]["tls"]
                again = host.ok("target.open", {"session": sessions["alpha"], "url": f"{https}/index.html"})
                after = host.ok("memory.report", {})["owners"]["network"]["tls"]
                expect(tag + "the second https fetch of the same profile resumes its session", after["resumed_total"] > before["resumed_total"], {"before": before, "after": after})
                host.ok("target.close", {"target": again["target"]})
                before = host.ok("memory.report", {})["owners"]["network"]["tls"]
                beta_page = host.ok("target.open", {"session": sessions["beta"], "url": f"{https}/index.html"})
                after = host.ok("memory.report", {})["owners"]["network"]["tls"]
                beta_tls = host.ok("target.inspect", {"target": beta_page["target"]})["network"]["tls"]
                # A target performs three https fetches (document, script, data): exactly one full
                # handshake means the profile started cold and did not reuse alpha's cache.
                expect(tag + "another profile's first https fetch is a full handshake (no cross-profile cache)",
                       beta_tls["handshakes_total"] >= 1 and beta_tls["handshakes_total"] - beta_tls["resumed_total"] == 1, beta_tls)
                host.ok("target.close", {"target": beta_page["target"]})

                # 4. Secure cookies.
                secure = host.ok("target.open", {"session": sessions["alpha"], "url": f"{https}/cookie/set?name=locked&value={FAKE['alpha']}&attrs=Secure%3B%20Path%3D/%3B%20Max-Age%3D3600"})
                host.ok("target.close", {"target": secure["target"]})
                plain = host.ok("target.open", {"session": sessions["alpha"], "url": f"{https}/cookie/set?name=open&value=court-open&attrs=Path%3D/%3B%20Max-Age%3D3600"})
                host.ok("target.close", {"target": plain["target"]})
                echo = host.ok("target.open", {"session": sessions["alpha"], "url": f"{https}/echo.html"})
                PROFILE_SETTLE(host, echo["target"])
                https_text = text_of(host, echo["target"])
                host.ok("target.close", {"target": echo["target"]})
                echo = host.ok("target.open", {"session": sessions["alpha"], "url": f"{http}/echo.html"})
                PROFILE_SETTLE(host, echo["target"])
                http_text = text_of(host, echo["target"])
                host.ok("target.close", {"target": echo["target"]})
                expect(tag + "a Secure cookie set over https is sent back over https", f"locked={FAKE['alpha']}" in https_text and "open=court-open" in https_text, https_text)
                expect(tag + "the Secure cookie is never sent to the http origin of the same host, the plain one still is", "locked=" not in http_text and "open=court-open" in http_text, http_text)
                doc = host.ok("target.open", {"session": sessions["alpha"], "url": f"{http}/storage.html?alpha-1"})
                doc_text = text_of(host, doc["target"])
                host.ok("target.close", {"target": doc["target"]})
                expect(tag + "an http document does not see the Secure cookie in document.cookie", "locked=" not in doc_text and "open=court-open" in doc_text, doc_text)
                none_ok = host.ok("target.open", {"session": sessions["alpha"], "url": f"{https}/cookie/set?name=third&value=court-third&attrs=SameSite%3DNone%3B%20Secure%3B%20Path%3D/"})
                host.ok("target.close", {"target": none_ok["target"]})
                none_bad = host.ok("target.open", {"session": sessions["alpha"], "url": f"{https}/cookie/set?name=neg&value=court-neg&attrs=SameSite%3DNone%3B%20Path%3D/"})
                host.ok("target.close", {"target": none_bad["target"]})
                http_secure = host.ok("target.open", {"session": sessions["alpha"], "url": f"{http}/cookie/set?name=neg2&value=court-neg2&attrs=Secure%3B%20Path%3D/"})
                host.ok("target.close", {"target": http_secure["target"]})
                domain = host.ok("target.open", {"session": sessions["alpha"], "url": f"{https}/cookie/set?name=neg3&value=court-neg3&attrs=Domain%3Dexample.com%3B%20Secure"})
                host.ok("target.close", {"target": domain["target"]})
                echo = host.ok("target.open", {"session": sessions["alpha"], "url": f"{https}/echo.html"})
                PROFILE_SETTLE(host, echo["target"])
                matrix_text = text_of(host, echo["target"])
                host.ok("target.close", {"target": echo["target"]})
                expect(tag + "SameSite=None; Secure is accepted over https; SameSite=None without Secure, Secure over http and a foreign Domain are refused",
                       "third=court-third" in matrix_text and "court-neg" not in matrix_text, matrix_text)

                # 5. A failed https navigation is atomic.
                link = host.ok("target.open", {"session": sessions["alpha"], "url": f"{https}/link.html?href={urllib.parse.quote('https://127.0.0.1:' + str(wrong_port) + '/index.html', safe='')}"})
                before_inspect = host.ok("target.inspect", {"target": link["target"]})
                before_cookies = host.ok("profile.inspect", {"profile": alpha})["cookies"]
                snapshot = host.ok("target.snapshot", {"target": link["target"], "format": "semantic", "max_bytes": 65536, "max_nodes": 48})
                anchor = next((n for n in snapshot["nodes"] if n.get("dom_id") == "go" or n.get("role") == "link"), None)
                # Court amendment (mechanism): the click carries the node reference as the contract requires.
                clicked = host.call("target.act", {"target": link["target"], "reference": anchor["reference"], "action": {"kind": "click"}}, 8000) if anchor else {"ok": False, "error": {"code": "internal", "message": "no link found"}}
                after_inspect = host.ok("target.inspect", {"target": link["target"]})
                after_cookies = host.ok("profile.inspect", {"profile": alpha})["cookies"]
                expect(tag + "a link to a wrong-name https origin fails typed and leaves frame, generation, realm, revision and jar unchanged",
                       not clicked["ok"] and after_inspect["frames"] == before_inspect["frames"] and after_inspect["realms"] == before_inspect["realms"]
                       and after_inspect.get("revision") == before_inspect.get("revision") and after_cookies == before_cookies,
                       {"clicked": clicked.get("error"), "before": before_inspect.get("frames"), "after": after_inspect.get("frames")})
                host.ok("target.close", {"target": link["target"]})
                host.ok("target.close", {"target": page["target"]})

                # 6. Memory over the complete tree.
                https_targets = [host.ok("target.open", {"session": sessions["alpha"], "url": f"{https}/index.html"})["target"] for _ in range(8)]
                on_eight = footprint(host)
                for target in https_targets:
                    host.ok("target.close", {"target": target})
                for name in ("alpha", "beta"):
                    host.ok("session.close", {"session": sessions[name]})
                on_post_close_in_use = in_use(host)
                on_post_close = footprint(host)
                report = host.ok("memory.report", {})
                h2 = on_first - off_first
                h3 = (on_eight - off_eight) / 8
                h4 = on_post_close_in_use - off_post_close_in_use
                expect(tag + "H2 first https target over first http target", h2 <= CAPS["h2_first_https_over_first_http_bytes"], {"delta": h2})
                expect(tag + "H3 eight https targets over eight http targets, per target", h3 <= CAPS["h3_eight_https_over_eight_http_per_target_bytes"], {"per_target": int(h3)})
                expect(tag + "H4 post-close libmalloc in-use enabled over feature-off", h4 <= CAPS["h4_post_close_in_use_enabled_over_off_bytes"], {"delta": h4})
                expect(tag + "owners are zero after every close and no TLS connection is live",
                       report["owners"]["targets"]["objects"] == 0 and report["owners"]["network"]["tls"]["live_connections"] == 0, report["owners"]["network"]["tls"])
                expect(tag + "the host stayed one process with no descendant", host.sampler.max_descendants == 0)
                expect(tag + "enabled host exits cleanly", host.finish() == 0)
                footprints[allocator] = {"off_empty": off_empty, "on_empty": on_empty, "off_first_http": off_first, "on_first_https": on_first,
                                         "off_eight_http": off_eight, "on_eight_https": on_eight, "off_post_close": off_post_close, "on_post_close": on_post_close,
                                         "off_post_close_in_use": off_post_close_in_use, "on_post_close_in_use": on_post_close_in_use,
                                         "tree_peak_enabled": max((s[2] for s in host.sampler.samples), default=0)}
    finally:
        for server in servers:
            server.shutdown()
        fixtures.cleanup()

    receipt = {
        "schema": "minicon-surf.native-dom-https-receipt/0.0.1",
        "technology": "native-dom",
        "technology_version": "0.0.2",
        "host_sha256": hashlib.sha256(Path(args.binary).read_bytes()).hexdigest(),
        "release_binary_bytes": Path(args.binary).stat().st_size,
        "design": "labs/native-dom/https-design-0.0.1.md",
        "caps": CAPS,
        "fixtures": {"mode": "generated-disposable" if fixtures.temporary else "explicit-private-directory", "generation_seconds": fixtures.generation_seconds,
                     "public_certificates": fixtures.evidence},
        "checks": checks,
        "checks_passed": sum(1 for c in checks if c["passed"]),
        "checks_total": len(checks),
        "passed": all(c["passed"] for c in checks),
        "footprint_bytes": footprints,
        "limitations": [
            "pinned test roots on loopback only: no system roots, no public web, no root-store breadth claim",
            "the TLS server is Python's ssl (OpenSSL): court infrastructure outside the host's process tree, never sampled",
            "ring carries C and perlasm crypto inside an otherwise Rust closure; the stack is not pure Rust",
            "one platform, one fixture set, fake values only; no leak-absence claim",
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


def PROFILE_SETTLE(host, target):
    if "sent=" in text_of(host, target):
        return
    host.ok("target.wait", {"target": target, "condition": {"kind": "revision_at_least", "revision": 1}}, 5000)


if __name__ == "__main__":
    sys.exit(main())
