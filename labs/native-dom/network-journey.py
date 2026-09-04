#!/usr/bin/env python3
"""Network court for the native route: a hermetic representative page plus
policy negatives, driven through control 0.0.1.

A loopback HTTP server serves the representative fixture set and synthetic
endpoints (redirect chains, a redirect into private space, an oversized body,
a slow body, a non-HTML document). The host is started with exactly that
origin allowlisted; a second host without the allowlist proves loopback is
refused by default. Every request and response is validated with the
repository's contract checker. Physical footprint of the host is sampled
after the representative page settles and, optionally, the same page is
loaded in a single Lightpanda server for a same-court comparison.
"""

import argparse
import ctypes
import ctypes.util
import hashlib
import http.server
import importlib.util
import json
import os
import socketserver
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "protocol"))
import check_contract  # noqa: E402

FIXTURES = ROOT / "labs" / "court" / "fixtures" / "representative"
CONTENT_TYPES = {".html": "text/html; charset=utf-8", ".js": "text/javascript", ".json": "application/json"}


class RusageInfoV4(ctypes.Structure):
    _fields_ = [("ri_uuid", ctypes.c_uint8 * 16)] + [
        (name, ctypes.c_uint64) for name in (
            "ri_user_time", "ri_system_time", "ri_pkg_idle_wkups", "ri_interrupt_wkups", "ri_pageins",
            "ri_wired_size", "ri_resident_size", "ri_phys_footprint", "ri_proc_start_abstime",
            "ri_proc_exit_abstime", "ri_child_user_time", "ri_child_system_time", "ri_child_pkg_idle_wkups",
            "ri_child_interrupt_wkups", "ri_child_pageins", "ri_child_elapsed_abstime", "ri_diskio_bytesread",
            "ri_diskio_byteswritten", "ri_cpu_time_qos_default", "ri_cpu_time_qos_maintenance",
            "ri_cpu_time_qos_background", "ri_cpu_time_qos_utility", "ri_cpu_time_qos_legacy",
            "ri_cpu_time_qos_user_initiated", "ri_cpu_time_qos_user_interactive", "ri_billed_system_time",
            "ri_serviced_system_time", "ri_logical_writes", "ri_lifetime_max_phys_footprint", "ri_instructions",
            "ri_cycles", "ri_billed_energy", "ri_serviced_energy", "ri_interval_max_phys_footprint",
            "ri_runnable_time",
        )
    ]


_LIBPROC = ctypes.CDLL(ctypes.util.find_library("proc"))
_LIBPROC.proc_pid_rusage.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_void_p]
_LIBPROC.proc_pid_rusage.restype = ctypes.c_int


def footprint(pid):
    info = RusageInfoV4()
    if _LIBPROC.proc_pid_rusage(pid, 4, ctypes.byref(info)) != 0:
        return None
    return {"physical_footprint_bytes": int(info.ri_phys_footprint),
            "resident_bytes": int(info.ri_resident_size),
            "lifetime_max_physical_footprint_bytes": int(info.ri_lifetime_max_phys_footprint)}


class Handler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.0"
    hits = []
    broken_pipes = 0

    def log_message(self, *_):
        pass

    def reply(self, status, body=b"", content_type="text/html; charset=utf-8", extra=()):
        # A client that stops reading at its byte cap closes the socket; the
        # harness reclaims that quietly and counts it, without touching what
        # the client itself reports.
        try:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            for name, value in extra:
                self.send_header(name, value)
            self.end_headers()
            if body:
                self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            Handler.broken_pipes += 1

    def do_GET(self):
        path, _, _query = self.path.partition("?")
        Handler.hits.append(path)
        if path.startswith("/redirect/"):
            remaining = int(path.rsplit("/", 1)[1])
            if remaining == 0:
                return self.reply(200, b"<!doctype html><h1>Redirect landed</h1>")
            return self.reply(302, extra=[("Location", f"/redirect/{remaining - 1}")])
        if path == "/redirect-private":
            return self.reply(302, extra=[("Location", "http://169.254.169.254/latest/meta-data/")])
        if path == "/redirect-loop":
            return self.reply(302, extra=[("Location", "/redirect-loop")])
        if path == "/big":
            return self.reply(200, b"<!doctype html><p>" + b"x" * (1_100_000) + b"</p>")
        if path == "/slow":
            time.sleep(4.0)
            return self.reply(200, b"<!doctype html><h1>slow</h1>")
        if path == "/notfound":
            return self.reply(404, b"<!doctype html><h1>missing</h1>")
        if path == "/":
            path = "/index.html"
        target = (FIXTURES / path.lstrip("/")).resolve()
        if target.is_file() and FIXTURES in target.parents:
            return self.reply(200, target.read_bytes(), CONTENT_TYPES.get(target.suffix, "application/octet-stream"))
        return self.reply(404, b"<!doctype html><h1>missing</h1>")


class Server(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    allow_reuse_address = True


class Host:
    def __init__(self, binary, directory, origin=None):
        command = [binary, "serve", "--stdio", "--fixture-root", str(ROOT / "labs" / "court" / "fixtures"),
                   "--config-dir", str(Path(directory) / "config")]
        if origin:
            command += ["--allow-origin", origin]
        self.process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                        stderr=subprocess.DEVNULL, text=True)
        self.counter = 0
        self.transcript = []

    def call(self, operation, arguments, deadline_ms=10000):
        self.counter += 1
        request = {"protocol": "minicon-surf.control", "version": "0.0.1",
                   "request_id": f"req_net_{self.counter}", "deadline_ms": deadline_ms,
                   "operation": operation, "arguments": arguments}
        check_contract.validate_request(request)
        started = time.monotonic()
        self.process.stdin.write(json.dumps(request) + "\n")
        self.process.stdin.flush()
        line = self.process.stdout.readline()
        elapsed_ms = round((time.monotonic() - started) * 1000, 3)
        if not line:
            raise RuntimeError(f"host exited during {operation}")
        response = json.loads(line)
        check_contract.validate_response(response)
        if response["request_id"] != request["request_id"]:
            raise RuntimeError("response request_id differs")
        self.transcript.append({"operation": operation, "arguments": arguments, "response": response,
                                "elapsed_ms": elapsed_ms})
        return response

    def finish(self):
        self.process.stdin.close()
        return self.process.wait(timeout=30)


def expect(checks, name, condition, detail=None):
    checks.append({"check": name, "passed": bool(condition), "detail": detail})
    if not condition:
        print(f"FAILED: {name}: {json.dumps(detail)[:300]}", file=sys.stderr)


def error_code(response):
    return None if response.get("ok") else response["error"]["code"]


def roles(snapshot):
    return [(n["role"], n["name"]) for n in snapshot["result"]["nodes"]]


def snapshot(host, target):
    return host.call("target.snapshot", {"target": target, "format": "semantic", "max_bytes": 65536, "max_nodes": 64})


PROXY_VARIABLES = ("http_proxy", "https_proxy", "all_proxy", "no_proxy")
TERMINAL_STATE_JS = ("JSON.stringify({status: (document.getElementById('status') || {}).textContent || null,"
                     " results: document.querySelectorAll('#results li').length})")


def lightpanda_comparison(binary, url):
    """Load the same hermetic page in one Lightpanda server and sample footprint.

    The engine inherits the court's environment minus proxy variables: an
    inherited loopback proxy answered for 127.0.0.1 and the page never
    reached the server. The stripped names are recorded, and the comparison
    is labelled same-workload only when the engine reaches the same terminal
    state the native host reached (status text and eight results).
    """
    spec = importlib.util.spec_from_file_location("cdp", ROOT / "labs" / "court" / "cdp-live-target.py")
    cdp_support = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cdp_support)
    stripped = sorted(name for name in os.environ if name.lower() in PROXY_VARIABLES)
    environment = {name: value for name, value in os.environ.items() if name.lower() not in PROXY_VARIABLES}
    environment.update(LIGHTPANDA_DISABLE_TELEMETRY="true", LIGHTPANDA_DISABLE_CORE_DUMP="1")
    port = cdp_support.free_port()
    process = subprocess.Popen(
        [binary, "serve", "--host", "127.0.0.1", "--port", str(port), "--disable-metrics", "--watchdog-ms", "15000"],
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=environment,
    )
    try:
        endpoint = cdp_support.discover(port, time.monotonic() + 10.0)
        websocket = cdp_support.WebSocket(endpoint)
        cdp = cdp_support.CDP(websocket)
        cdp.call("Target.getTargets")
        empty = footprint(process.pid)
        target = cdp.call("Target.createTarget", {"url": "about:blank"})["targetId"]
        session = cdp.call("Target.attachToTarget", {"targetId": target, "flatten": True})["sessionId"]
        cdp.call("Page.enable", session_id=session)
        cdp.call("Runtime.enable", session_id=session)
        cdp.call("Page.navigate", {"url": url}, session)
        deadline = time.monotonic() + 10
        state = None
        while time.monotonic() < deadline:
            value = cdp.call("Runtime.evaluate", {"expression": TERMINAL_STATE_JS, "returnByValue": True}, session).get("result", {}).get("value")
            try:
                state = json.loads(value) if value else None
            except json.JSONDecodeError:
                state = {"raw": str(value)[:120]}
            if state and state.get("results") == 8:
                break
            time.sleep(0.05)
        time.sleep(0.5)
        loaded = footprint(process.pid)
        cdp.call("Target.closeTarget", {"targetId": target})
        time.sleep(0.5)
        closed = footprint(process.pid)
        websocket.close()
        same_state = bool(state) and state.get("results") == 8 and state.get("status") == "8 results"
        return {"engine": "lightpanda", "version": "0.4.0",
                "transport": "CDP over loopback WebSocket, single server",
                "proxy_variables_stripped": stripped,
                "terminal_state": state, "same_terminal_state_as_native": same_state,
                "comparison": "same-workload footprint" if same_state else "not comparable: engine did not reach the native terminal state",
                "empty": empty, "representative_page": loaded, "after_close": closed,
                "retained_above_empty_bytes": (closed or {}).get("physical_footprint_bytes", 0) - (empty or {}).get("physical_footprint_bytes", 0),
                "note": "Lightpanda applies its own network policy; only page footprint at the shared terminal states is compared"}
    finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--binary", required=True)
    parser.add_argument("--lightpanda", help="optional Lightpanda executable for a same-page footprint comparison")
    parser.add_argument("--receipt")
    args = parser.parse_args()
    binary = Path(args.binary)
    server = Server(("127.0.0.1", 0), Handler)
    port = server.server_address[1]
    origin = f"http://127.0.0.1:{port}"
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    checks = []
    footprints = {}
    try:
        with tempfile.TemporaryDirectory(prefix="minicon-surf-native-net-") as directory:
            # Default policy: loopback is refused even for the court's own server.
            closed = Host(str(binary), directory)
            profile = closed.call("profile.create", {"persistence": "ephemeral"})["result"]["profile"]
            session = closed.call("session.open", {"profile": profile})["result"]["session"]
            refused = closed.call("target.open", {"session": session, "url": f"{origin}/index.html"})
            expect(checks, "loopback origin is permission_denied without an allowlist",
                   error_code(refused) == "permission_denied", refused)
            closed.finish()

            host = Host(str(binary), directory, origin)
            profile = host.call("profile.create", {"persistence": "ephemeral"})["result"]["profile"]
            session = host.call("session.open", {"profile": profile})["result"]["session"]
            footprints["empty"] = footprint(host.process.pid)

            opened = host.call("target.open", {"session": session, "url": f"{origin}/index.html"}, 30000)
            expect(checks, "representative page opens through the allowlisted origin",
                   opened["ok"] and opened["result"]["url"] == f"{origin}/index.html"
                   and opened["result"]["scripts_run"] == 1 and opened["result"]["network"]["fetches"] == 3
                   and opened["result"]["document_framing"] == "content-length", opened)
            target = opened["result"]["target"]
            first = snapshot(host, target)
            check_contract.validate_snapshot(first["result"])
            names = roles(first)
            links = [n for r, n in names if r == "link"]
            native_state = {"status": next((n for r, n in names if r == "text" and n.endswith("results")), None),
                            "results": len(links) - 3 if len(links) >= 3 else len(links)}
            expect(checks, "snapshot lists nav, heading, form and eight fetched results",
                   ("heading", "Representative Court") in names and ("label", "Query") in names
                   and ("textbox", "Query") in names and ("button", "Continue") in names
                   and ("text", "8 results") in names and len(links) == 11
                   and links[3:] == ["Alpha result", "Beta result", "Gamma result", "Delta result",
                                     "Epsilon result", "Zeta result", "Eta result", "Theta result"], names)
            time.sleep(0.5)
            footprints["representative_page"] = footprint(host.process.pid)
            button = next(n for n in first["result"]["nodes"] if n["role"] == "button")
            revision = first["result"]["revision"]
            act = host.call("target.act", {"target": target, "reference": button["reference"], "action": {"kind": "click"}})
            expect(checks, "button click triggers a fetch and the revision advances",
                   act["ok"] and act["result"]["revision"] > revision, act)
            waited = host.call("target.wait", {"target": target, "condition": {"kind": "revision_at_least", "revision": revision + 1}})
            expect(checks, "wait observes the fetched status without a sleep", waited["ok"], waited)
            second = snapshot(host, target)
            expect(checks, "post-click snapshot shows the fetched status text",
                   ("text", "Continued") in roles(second), roles(second))
            inspect = host.call("target.inspect", {"target": target})
            expect(checks, "inspect reports url, framing, one script and four fetches with none denied",
                   inspect["ok"] and inspect["result"]["url"] == f"{origin}/index.html"
                   and inspect["result"]["network"] == {"fetches": 4, "bytes": inspect["result"]["network"]["bytes"], "denied": 0}
                   and inspect["result"]["scripts_run"] == 1 and inspect["result"]["scripts_skipped"] == [], inspect)
            memory = host.call("memory.report", {})
            # Court amendment, recorded when the navigation slice landed: the
            # fetch and byte limits bound one document, not a target's whole
            # life, so the reported keys are per_document and a navigation
            # gives its committed document a fresh budget. The limits
            # themselves did not move; only what they are scoped to is now
            # explicit, and the lifetime totals beside them never gate.
            limits = memory["result"]["owners"]["network"]["limits"]
            expect(checks, "the fetch and byte limits are named per document, at their frozen values",
                   limits.get("fetches_per_document") == 32 and limits.get("bytes_per_document") == 4194304
                   and "fetches_per_target" not in limits and "bytes_per_target" not in limits, limits)
            expect(checks, "memory.report exposes network owners and limits",
                   memory["ok"] and memory["result"]["owners"]["network"]["fetches"] == 4
                   and memory["result"]["owners"]["network"]["limits"]["redirects"] == 3
                   and memory["result"]["owners"]["network"]["limits"]["allowed_origins"] == 1, memory)

            negatives = [
                # Court amendment (recorded): https is a pinned-roots capability since the HTTPS slice;
                # without a pinned root the refusal keeps the code and names the missing pin.
                ("https scheme is unsupported_capability", f"https://127.0.0.1:{port}/index.html", "unsupported_capability", "tls_no_pinned_roots"),
                ("file scheme is unsupported_capability", "file:///etc/hosts", "unsupported_capability", "scheme"),
                ("ftp scheme is unsupported_capability", "ftp://example.com/", "unsupported_capability", "scheme"),
                ("private 10/8 is permission_denied", "http://10.0.0.1/", "permission_denied", "address"),
                ("link-local metadata address is permission_denied", "http://169.254.169.254/latest/meta-data/", "permission_denied", "address"),
                ("carrier-grade NAT is permission_denied", "http://100.64.0.1/", "permission_denied", "address"),
                ("IETF protocol assignments block is permission_denied", "http://192.0.0.170/", "permission_denied", "address"),
                ("IPv6 unique-local is permission_denied", "http://[fd00::1]/", "permission_denied", "address"),
                ("IPv6 loopback on the court port is permission_denied", f"http://[::1]:{port}/index.html", "permission_denied", "address"),
                ("localhost name is permission_denied", f"http://localhost:{port}/index.html", "permission_denied", "address"),
                ("loopback on another port is permission_denied", "http://127.0.0.1:1/", "permission_denied", "address"),
                ("RFC 2606 .invalid name is not_found", "http://court.invalid/", "not_found", "dns"),
                ("more than three redirects is resource_limit", f"{origin}/redirect/5", "resource_limit", "redirect-count"),
                ("redirect loop is resource_limit", f"{origin}/redirect-loop", "resource_limit", "redirect-count"),
                ("redirect into private space is permission_denied", f"{origin}/redirect-private", "permission_denied", "address"),
                ("body over the cap is resource_limit", f"{origin}/big", "resource_limit", "response-bytes"),
                ("slow body is deadline_exceeded", f"{origin}/slow", "deadline_exceeded", None),
                ("404 document is not_found", f"{origin}/notfound", "not_found", None),
                ("non-HTML document is unsupported_capability", f"{origin}/data.json", "unsupported_capability", None),
            ]
            for name, url, code, reason in negatives:
                response = host.call("target.open", {"session": session, "url": url}, 10000)
                detail_reason = (response.get("error", {}).get("details") or {}).get("reason")
                expect(checks, name, error_code(response) == code and (reason is None or detail_reason == reason),
                       response)
            three_redirects = host.call("target.open", {"session": session, "url": f"{origin}/redirect/3"}, 10000)
            expect(checks, "exactly three redirects are followed",
                   three_redirects["ok"] and ("heading", "Redirect landed") in roles(snapshot(host, three_redirects["result"]["target"])), three_redirects)
            host.call("target.close", {"target": three_redirects["result"]["target"]}) if three_redirects["ok"] else None

            many = host.call("target.open", {"session": session, "url": f"{origin}/many.html"}, 10000)
            outcomes = roles(snapshot(host, many["result"]["target"])) if many["ok"] else None
            expect(checks, "six concurrent fetches: four succeed and two are resource_limit",
                   many["ok"] and outcomes.count(("text", "ok 200")) == 4
                   and outcomes.count(("text", "rejected resource_limit")) == 2 and ("text", "done") in outcomes, outcomes)
            if many["ok"]:
                host.call("target.close", {"target": many["result"]["target"]})

            count = host.call("target.open", {"session": session, "url": f"{origin}/count.html"}, 30000)
            summary = roles(snapshot(host, count["result"]["target"])) if count["ok"] else None
            expect(checks, "sequential fetches stop at the per-document budget of 32",
                   count["ok"] and ("text", "ok=31 first_failure=resource_limit") in summary, summary)
            if count["ok"]:
                host.call("target.close", {"target": count["result"]["target"]})

            xorigin = host.call("target.open", {"session": session, "url": f"{origin}/xorigin.html"}, 10000)
            xinspect = host.call("target.inspect", {"target": xorigin["result"]["target"]}) if xorigin["ok"] else None
            expect(checks, "cross-origin external script is skipped and reported, inline script still runs",
                   xorigin["ok"] and ("text", "inline ran") in roles(snapshot(host, xorigin["result"]["target"]))
                   and xinspect["result"]["scripts_skipped"] == [{"src": "http://example.com/never-fetched.js", "reason": "cross-origin script refused"}]
                   and xinspect["result"]["network"]["denied"] == 1
                   and not any(hit.endswith("never-fetched.js") for hit in Handler.hits), xinspect)
            if xorigin["ok"]:
                host.call("target.close", {"target": xorigin["result"]["target"]})

            fixture = host.call("target.open", {"session": session, "fixture": "semantic-interactive.html"}, 10000)
            expect(checks, "fixture targets still open beside url targets", fixture["ok"], fixture)
            if fixture["ok"]:
                host.call("target.close", {"target": fixture["result"]["target"]})

            pages = [target]
            while len(pages) < 8:
                extra = host.call("target.open", {"session": session, "url": f"{origin}/index.html"}, 30000)
                if not extra["ok"]:
                    break
                pages.append(extra["result"]["target"])
            expect(checks, "eight representative pages are live concurrently", len(pages) == 8, len(pages))
            time.sleep(0.5)
            footprints["eight_representative_pages"] = footprint(host.process.pid)
            for page in pages:
                host.call("target.close", {"target": page})
            time.sleep(0.5)
            footprints["after_closes"] = footprint(host.process.pid)
            owners = host.call("memory.report", {})["result"]["owners"]
            owners_after_closes = {"targets": owners["targets"]["objects"], "script_realms": owners["script_realms"]["objects"],
                                   "script_realm_malloc_bytes": owners["script_realms"]["malloc_bytes"],
                                   "network_fetches": owners["network"]["fetches"], "network_bytes": owners["network"]["bytes"]}
            expect(checks, "logical owners return to zero after closes (not a footprint recovery claim)",
                   owners_after_closes == {"targets": 0, "script_realms": 0, "script_realm_malloc_bytes": 0,
                                           "network_fetches": 0, "network_bytes": 0}, owners_after_closes)
            host.call("session.close", {"session": session})
            exit_code = host.finish()
            expect(checks, "host exits cleanly", exit_code == 0, exit_code)
        comparison = lightpanda_comparison(args.lightpanda, f"{origin}/index.html") if args.lightpanda else None
    finally:
        server.shutdown()
        server.server_close()

    passed = all(check["passed"] for check in checks)
    receipt = {
        "schema": "minicon-surf.native-dom-network-court-receipt/0.0.1",
        "status": "observed" if passed else "failed",
        "technology": "native-dom",
        "technology_version": "0.0.2",
        "host_sha256": hashlib.sha256(binary.read_bytes()).hexdigest(),
        "control_contract": "0.0.1",
        "platform": {"os": "macos", "architecture": "arm64"},
        "workload": {
            "id": "R1-representative-hermetic-page",
            "fixtures_sha256": {name: hashlib.sha256((FIXTURES / name).read_bytes()).hexdigest()
                                for name in sorted(os.listdir(FIXTURES))},
            "server": "loopback HTTP/1.0 server in the court process on an ephemeral port, allowlisted as the only origin",
            "network_limits": {"scheme": "http", "redirects": 3, "header_bytes": 16384, "response_bytes": 1048576,
                               "per_fetch_ms": 3000, "pending_per_turn": 4, "fetches_per_document": 32,
                               "bytes_per_document": 4194304, "external_scripts": 8,
                               "scope": "the fetch and byte limits bound one document; a navigation commits a fresh budget with its new realm, and the failed candidate's spend is discarded with it"},
        },
        "checks": checks,
        "passed": passed,
        "footprint": footprints,
        "footprint_retained_above_empty_bytes": (footprints["after_closes"] or {}).get("physical_footprint_bytes", 0)
                                                 - (footprints["empty"] or {}).get("physical_footprint_bytes", 0),
        "owners_after_closes": owners_after_closes,
        "native_terminal_state": native_state,
        "server_hits": {path: Handler.hits.count(path) for path in sorted(set(Handler.hits))},
        "server_broken_pipes_reclaimed": Handler.broken_pipes,
        "lightpanda_comparison": comparison,
        "transcript": host.transcript,
        "limitations": [
            "one hermetic page set; not a Web-compatibility claim; the DOM shim covers what the fixtures and instrumentation use",
            "http only; https, cookies, storage, images, fonts, layout and real timers are absent",
            "public-address negatives are refused before any connection, so they exercise policy, not reachability",
            "the .invalid negative depends on the system resolver returning no address",
            "footprint samples are single readings after a 500 ms settle, not the seven-run court",
            "the native host never consults proxy variables; the engine comparison strips them from its environment and records which",
            "logical owners reaching zero after closes says nothing about footprint recovery; footprint retained above empty is reported separately and is a QuickJS, network-buffer and allocator retention risk",
        ],
    }
    encoded = json.dumps(receipt, indent=2, sort_keys=True) + "\n"
    if args.receipt:
        Path(args.receipt).write_text(encoded, encoding="utf-8")
    print(encoded, end="")
    sys.exit(0 if passed else 1)


if __name__ == "__main__":
    main()
