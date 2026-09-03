#!/usr/bin/env python3
"""Frozen candidate court for the native route's HTTPS design.

Measures standalone `tls-probe` builds (plain-TCP control, rustls+ring,
rustls+aws-lc-rs, macOS SecureTransport) against the pre-registered stages
and criteria of `labs/native-dom/https-design-0.0.1.md`, section 4 and 5.
Nothing here touches the native host or the public network.

Key material: no private key is committed or kept. Before any probe starts
the court generates a disposable test CA, a loopback leaf (IP:127.0.0.1,
DNS:localhost), a wrong-name leaf and a leaf from a second, unpinned CA with
the `openssl` command line into a private temporary directory (0700, files
0600) and deletes it when the run ends, also on failure. Only public
evidence (SHA-256 fingerprints, subjects, SANs, key algorithm, validity)
reaches the receipt. Generation is a separate, timed phase that never
overlaps client measurement. A fixed fixture set is injected only through
an explicit --tls-fixture-dir outside the repository (file names below);
without it the court generates; if generation is impossible it fails closed
and never downloads. The receipt writer refuses any content with a
private-key block or the temporary path.
"""

import argparse
import ctypes
import hashlib
import http.server
import importlib.util
import json
import os
import re
import shutil
import ssl
import statistics
import stat
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LAB = Path(__file__).resolve().parent
FIXTURE_FILES = ("court-ca.pem", "loopback.pem", "loopback.key", "wrong-name.pem", "wrong-name.key",
                 "other-ca.pem", "other-ca-loopback.pem", "other-ca-loopback.key")
PRIVATE_KEY_BLOCK = re.compile(r"BEGIN (RSA |EC |OPENSSH |ENCRYPTED )?PRIVATE KEY")
STACKS = {"plain": [], "rustls-ring": ["rustls-ring"], "rustls-aws-lc": ["rustls-aws-lc"], "secure-transport": ["secure-transport"]}
CRITERIA = {
    "s5_idle_over_feature_off_bytes": 524288,
    "s6_first_handshake_over_idle_bytes": 1048576,
    "s7_per_connection_bytes": 131072,
    "s8_in_use_after_close_over_idle_bytes": 65536,
}
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


HELPER_COURT = load_module("profile_helper_court", ROOT / "labs" / "native-dom" / "profile-helper-court.py")
RETENTION = HELPER_COURT.RETENTION
TreeSampler = HELPER_COURT.TreeSampler
descendants_of = HELPER_COURT.descendants_of


# ------------------------------------------------------------- fixtures


def run_openssl(*args, cwd):
    result = subprocess.run(["openssl", *args], cwd=cwd, capture_output=True, text=True)
    if result.returncode != 0:
        # stderr of openssl never contains key bytes for these commands; keep it short.
        raise RuntimeError(f"openssl {args[0]} failed: {result.stderr.strip()[:200]}")
    return result.stdout


def generate_fixtures(directory):
    """Disposable CA, leaves and a second CA; keys are P-256, never read by the court."""
    os.chmod(directory, 0o700)
    days = "30"
    old_umask = os.umask(0o077)
    try:
        def key(name):
            run_openssl("ecparam", "-name", "prime256v1", "-genkey", "-noout", "-out", name, cwd=directory)

        def ca(keyfile, certfile, cn):
            key(keyfile)
            run_openssl("req", "-x509", "-new", "-key", keyfile, "-sha256", "-days", days, "-subj", f"/CN={cn}",
                        "-addext", "basicConstraints=critical,CA:TRUE", "-addext", "keyUsage=critical,keyCertSign,cRLSign",
                        "-out", certfile, cwd=directory)

        def leaf(name, keyfile, certfile, ca_cert, ca_key, san):
            key(keyfile)
            run_openssl("req", "-new", "-key", keyfile, "-subj", f"/CN={name}", "-out", f"{name}.csr", cwd=directory)
            Path(directory, f"{name}.ext").write_text(
                f"basicConstraints=CA:FALSE\nkeyUsage=critical,digitalSignature\nextendedKeyUsage=serverAuth\nsubjectAltName={san}\n")
            run_openssl("x509", "-req", "-in", f"{name}.csr", "-CA", ca_cert, "-CAkey", ca_key, "-CAcreateserial",
                        "-days", days, "-sha256", "-extfile", f"{name}.ext", "-out", certfile, cwd=directory)
            for extra in (f"{name}.csr", f"{name}.ext"):
                os.unlink(Path(directory, extra))

        ca("court-ca.key", "court-ca.pem", "MiniCon Surf disposable court CA")
        ca("other-ca.key", "other-ca.pem", "MiniCon Surf disposable OTHER CA (not pinned)")
        leaf("loopback", "loopback.key", "loopback.pem", "court-ca.pem", "court-ca.key", "IP:127.0.0.1,DNS:localhost")
        leaf("wrong-name", "wrong-name.key", "wrong-name.pem", "court-ca.pem", "court-ca.key", "DNS:wrong.invalid")
        leaf("other-ca-loopback", "other-ca-loopback.key", "other-ca-loopback.pem", "other-ca.pem", "other-ca.key",
             "IP:127.0.0.1,DNS:localhost")
        for srl in Path(directory).glob("*.srl"):
            srl.unlink()
    finally:
        os.umask(old_umask)
    for name in FIXTURE_FILES:
        os.chmod(Path(directory, name), 0o600)


def public_evidence(directory):
    """Fingerprint, subject, SAN, key algorithm and validity of each certificate: public data only."""
    evidence = {}
    for name in FIXTURE_FILES:
        if not name.endswith(".pem"):
            continue
        path = Path(directory, name)
        text = path.read_text()
        if PRIVATE_KEY_BLOCK.search(text):
            raise RuntimeError(f"{name} carries a private-key block; refusing to use it")
        out = run_openssl("x509", "-in", str(path), "-noout", "-fingerprint", "-sha256", "-subject", "-dates",
                          "-ext", "subjectAltName", cwd=directory)
        algorithm = run_openssl("x509", "-in", str(path), "-noout", "-text", cwd=directory)
        key_algorithm = next((line.strip() for line in algorithm.splitlines() if "Public Key Algorithm" in line), "")
        evidence[name] = {
            "sha256_fingerprint": next((line.split("=", 1)[1] for line in out.splitlines() if "Fingerprint" in line), None),
            "subject": next((line.split("=", 1)[1].strip() for line in out.splitlines() if line.startswith("subject")), None),
            "not_before": next((line.split("=", 1)[1] for line in out.splitlines() if line.startswith("notBefore")), None),
            "not_after": next((line.split("=", 1)[1] for line in out.splitlines() if line.startswith("notAfter")), None),
            "subject_alt_name": next((line.strip() for line in out.splitlines() if "IP Address" in line or "DNS:" in line), None),
            "public_key": key_algorithm.replace("Public Key Algorithm:", "").strip(),
        }
    return evidence


class Fixtures:
    """Either generated into a private temporary directory or injected from outside the repository."""

    def __init__(self, fixture_dir):
        self.temporary = fixture_dir is None
        self.generation_seconds = None
        if self.temporary:
            self.directory = tempfile.mkdtemp(prefix="minicon-surf-tls-fixtures-")
            started = time.monotonic()
            generate_fixtures(self.directory)
            self.generation_seconds = round(time.monotonic() - started, 3)
        else:
            self.directory = str(Path(fixture_dir).expanduser().resolve())
            if Path(self.directory).is_relative_to(ROOT):
                raise RuntimeError("--tls-fixture-dir must point outside the repository")
            for name in FIXTURE_FILES:
                path = Path(self.directory, name)
                if not path.is_file():
                    raise RuntimeError(f"fixture {name} is missing; nothing is downloaded or generated in this mode")
                if name.endswith(".key") and stat.S_IMODE(path.stat().st_mode) & 0o077:
                    raise RuntimeError(f"fixture {name} must be mode 0600")
        self.evidence = public_evidence(self.directory)

    def path(self, name):
        return str(Path(self.directory, name))

    def cleanup(self):
        if self.temporary:
            shutil.rmtree(self.directory, ignore_errors=True)


# -------------------------------------------------------------- servers


class Handler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_GET(self):
        body = b"<!doctype html><html><body><main><h1>tls court</h1></main></body></html>"
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_):
        pass


def start_server(fixtures, cert=None, key=None, alpn=("http/1.1",), minimum=ssl.TLSVersion.TLSv1_2, maximum=None, seclevel0=False):
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    facts = {"tls": cert is not None}
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
        facts.update({"cert": cert, "alpn": list(alpn), "minimum": str(minimum), "maximum": str(maximum) if maximum else None})
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, server.server_address[1], facts


# ---------------------------------------------------------------- probes


def build(stack, skip_build):
    target = LAB / "target" / stack
    binary = target / "release" / "tls-probe"
    features = STACKS[stack]
    command = ["cargo", "build", "--release", "--locked", "--offline", "--target-dir", str(target)]
    if features:
        command += ["--features", ",".join(features)]
    record = {"stack": stack, "features": features}
    if not skip_build or not binary.exists():
        started = time.monotonic()
        result = subprocess.run(command, cwd=LAB, capture_output=True, text=True)
        record["build_seconds"] = round(time.monotonic() - started, 1)
        if result.returncode != 0:
            tail = [line for line in result.stderr.splitlines() if line.startswith(("error", "warning: build failed", "  ="))][:6]
            record.update({"built": False, "error": " | ".join(tail)[:600]})
            return None, record
    record["built"] = True
    record["binary_bytes"] = binary.stat().st_size
    record["binary_sha256"] = hashlib.sha256(binary.read_bytes()).hexdigest()
    tree = ["cargo", "tree", "--offline", "--edges", "normal", "--prefix", "none", "--target-dir", str(target)]
    if features:
        tree += ["--features", ",".join(features)]
    listing = subprocess.run(tree, cwd=LAB, capture_output=True, text=True).stdout
    crates = sorted({line.split(" ")[0] + " " + line.split(" ")[1] for line in listing.splitlines() if " v" in line})
    record["crates"] = len(crates)
    record["crate_list"] = crates
    return binary, record


class Probe:
    def __init__(self, binary):
        self.process = subprocess.Popen([str(binary)], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
        self.sampler = TreeSampler(self.process.pid, str(binary))
        self.sampler.start()
        self.descendants_seen = 0

    def call(self, **command):
        self.process.stdin.write(json.dumps(command) + "\n")
        self.process.stdin.flush()
        line = self.process.stdout.readline()
        if not line:
            raise RuntimeError("probe exited")
        if descendants_of(self.process.pid):
            self.descendants_seen += 1
        return json.loads(line)

    def sample(self, label):
        time.sleep(SETTLE_SECONDS)
        outside = RETENTION.sample_process(self.process.pid)
        report = self.call(op="report")
        return {"stage": label, "physical_footprint_bytes": outside["physical_footprint_bytes"], "resident_bytes": outside["resident_bytes"],
                "libmalloc_size_in_use": report["libmalloc"]["size_in_use"], "libmalloc_size_allocated": report["libmalloc"]["size_allocated"],
                "live": report["live"], "handshakes_total": report["handshakes_total"], "resumed_total": report["resumed_total"]}

    def finish(self):
        try:
            self.call(op="exit")
        except Exception:
            pass
        code = self.process.wait(timeout=30)
        self.sampler.stop()
        return code


def run_once(binary, stack, fixtures, port, plain_port):
    probe = Probe(binary)
    stages, facts = [], {}
    try:
        stages.append(probe.sample("empty"))
        configured = probe.call(op="configure", roots=[fixtures.path("court-ca.pem")], server_name="localhost", resumption=4, min_version="1.2")
        if not configured["ok"]:
            raise RuntimeError(f"configure: {configured['error']}")
        stages.append(probe.sample("idle"))
        target_port = plain_port if stack == "plain" else port
        first = probe.call(op="open", count=1, port=target_port, path="/")
        if not first["ok"]:
            raise RuntimeError(f"first handshake: {first.get('refused')}")
        facts["first"] = first["connections"][0]
        stages.append(probe.sample("first_handshake"))
        probe.call(op="close")
        second = probe.call(op="open", count=1, port=target_port, path="/")
        if not second["ok"]:
            raise RuntimeError(f"second handshake: {second.get('refused')}")
        facts["second"] = second["connections"][0]
        stages.append(probe.sample("second_handshake"))
        stages.append(probe.sample("targets_1"))
        more = probe.call(op="open", count=7, port=target_port, path="/")
        if not more["ok"]:
            raise RuntimeError(f"eight connections: {more.get('refused')}")
        stages.append(probe.sample("targets_8"))
        probe.call(op="close")
        stages.append(probe.sample("post_close"))
        trimmed = probe.call(op="trim")
        stages.append(probe.sample("post_trim"))
        stages[-1]["trim_released_bytes"] = trimmed["released_bytes"]
        exit_code = probe.finish()
    finally:
        if probe.process.poll() is None:
            probe.process.kill()
            probe.process.wait()
            probe.sampler.stop()
    tree = probe.sampler
    return {"stages": stages, "facts": facts, "exit_code": exit_code, "descendants_seen": probe.descendants_seen,
            "max_descendants": tree.max_descendants, "tree_peak": max((s[2] for s in tree.samples), default=0),
            "host_peak": max((s[1] for s in tree.samples), default=0), "samples": len(tree.samples)}


def negatives(binary, fixtures, servers):
    probe = Probe(binary)
    out = {}
    try:
        probe.call(op="configure", roots=[fixtures.path("court-ca.pem")], server_name="localhost", resumption=0, min_version="1.2")
        for name, (port, server_name) in servers.items():
            if port is None:
                out[name] = {"not_exercised": server_name}
                continue
            response = probe.call(op="probe", port=port, server_name=server_name, path="/")
            out[name] = {"refused": response.get("refused"), "connections": response.get("connections", [])}
        report = probe.call(op="report")
        out["refused_total"] = report["refused_total"]
        out["descendants_seen"] = probe.descendants_seen
    finally:
        probe.finish()
    return out


def median(values):
    return int(statistics.median(values))


def summarize(values):
    return {"median": median(values), "minimum": min(values), "maximum": max(values), "values": values}


def aggregate(runs):
    stages = {}
    for stage in [s["stage"] for s in runs[0]["stages"]]:
        rows = [next(s for s in r["stages"] if s["stage"] == stage) for r in runs]
        stages[stage] = {key: summarize([row[key] for row in rows]) for key in
                         ("physical_footprint_bytes", "resident_bytes", "libmalloc_size_in_use", "libmalloc_size_allocated", "live")}
        if stage == "post_trim":
            stages[stage]["trim_released_bytes"] = summarize([row["trim_released_bytes"] for row in rows])
    return {
        "stages": stages,
        "facts": runs[0]["facts"],
        "resumed_on_second_handshake": [r["facts"]["second"].get("resumed") for r in runs],
        "tree": {"max_descendants": max(r["max_descendants"] for r in runs), "descendants_seen_at_calls": sum(r["descendants_seen"] for r in runs),
                 "tree_peak": summarize([r["tree_peak"] for r in runs]), "host_peak": summarize([r["host_peak"] for r in runs]),
                 "samples": summarize([r["samples"] for r in runs])},
        "exit_codes": [r["exit_code"] for r in runs],
    }


def refuse_private_material(text, fixtures):
    if PRIVATE_KEY_BLOCK.search(text):
        raise RuntimeError("receipt would contain a private-key block; refused")
    if fixtures.directory in text:
        raise RuntimeError("receipt would contain the fixture directory path; refused")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--stacks", default="plain,rustls-ring,rustls-aws-lc,secure-transport")
    parser.add_argument("--repetitions", type=int, default=7)
    parser.add_argument("--warmup", type=int, default=1)
    parser.add_argument("--receipt", required=True)
    parser.add_argument("--skip-build", action="store_true", help="reuse probes built by an earlier run")
    parser.add_argument("--tls-fixture-dir", default=None,
                        help=f"explicit private fixture directory outside the repository holding {', '.join(FIXTURE_FILES)}; "
                             "without it disposable fixtures are generated and deleted")
    args = parser.parse_args()

    fixtures = Fixtures(args.tls_fixture_dir)
    servers = []
    checks, results, builds = [], {}, {}

    def expect(name, condition, detail=None):
        checks.append({"check": name, "passed": bool(condition), **({"detail": detail} if detail is not None else {})})

    try:
        tls_server, tls_port, tls_facts = start_server(fixtures, "loopback.pem", "loopback.key")
        plain_server, plain_port, _ = start_server(fixtures)
        servers += [tls_server, plain_server]
        negative_servers = {}
        for name, kwargs in (("wrong_name", {"cert": "wrong-name.pem", "key": "wrong-name.key"}),
                             ("unpinned_issuer", {"cert": "other-ca-loopback.pem", "key": "other-ca-loopback.key"}),
                             ("tls12_only", {"cert": "loopback.pem", "key": "loopback.key", "maximum": ssl.TLSVersion.TLSv1_2}),
                             ("alpn_h2_only", {"cert": "loopback.pem", "key": "loopback.key", "alpn": ("h2",)}),
                             ("tls11_only", {"cert": "loopback.pem", "key": "loopback.key", "minimum": ssl.TLSVersion.TLSv1,
                                             "maximum": ssl.TLSVersion.TLSv1_1, "seclevel0": True})):
            try:
                server, port, _ = start_server(fixtures, **kwargs)
                servers.append(server)
                negative_servers[name] = (port, "localhost")
            except (ssl.SSLError, ValueError, OSError) as error:
                negative_servers[name] = (None, f"local OpenSSL refuses to serve this configuration: {type(error).__name__}")
        # An IP-name probe against the loopback leaf checks the IP SAN path.
        negative_servers["ip_san"] = (tls_port, "127.0.0.1")

        for stack in [s.strip() for s in args.stacks.split(",") if s.strip()]:
            binary, record = build(stack, args.skip_build)
            builds[stack] = record
            if binary is None:
                expect(f"[{stack}] probe builds offline", False, record.get("error"))
                continue
            runs = []
            for repetition in range(args.warmup + args.repetitions):
                run = run_once(binary, stack, fixtures, tls_port, plain_port)
                if repetition >= args.warmup:
                    runs.append(run)
                expect(f"[{stack}] run {repetition}: probe exits cleanly with no live connection and no descendant",
                       run["exit_code"] == 0 and run["stages"][-1]["live"] == 0 and run["max_descendants"] == 0)
            results[stack] = aggregate(runs)
            if stack != "plain":
                results[stack]["negatives"] = negatives(binary, fixtures, negative_servers)
    finally:
        for server in servers:
            server.shutdown()
        fixtures.cleanup()

    plain = results.get("plain")

    def fp(agg, stage):
        return agg["stages"][stage]["physical_footprint_bytes"]["median"]

    for stack, agg in results.items():
        if stack == "plain":
            continue
        tag = f"[{stack}] "
        first, second = agg["facts"]["first"], agg["facts"]["second"]
        neg = agg["negatives"]
        tls12 = neg.get("tls12_only", {})
        expect(tag + "S1 TLS 1.3 negotiated with the pinned root, and TLS 1.2 against a TLS 1.2-only server",
               first.get("version") in ("TLSv1_3", "TLS13", "Tls13", "TLSv1.3") and any("1_2" in str(c.get("version")) or "12" in str(c.get("version")) or "1.2" in str(c.get("version")) for c in tls12.get("connections", [])),
               {"first": first.get("version"), "tls12_only": tls12})
        expect(tag + "S2 wrong-name, unpinned-issuer and TLS 1.1-only servers are refused before HTTP",
               neg["wrong_name"].get("refused") and neg["unpinned_issuer"].get("refused") and ("not_exercised" in neg["tls11_only"] or neg["tls11_only"].get("refused")),
               {k: neg[k] for k in ("wrong_name", "unpinned_issuer", "tls11_only")})
        expect(tag + "S3 ALPN http/1.1 negotiated; an h2-only server yields no http/1.1 ALPN",
               first.get("alpn") == "http/1.1" and not any(c.get("alpn") == "http/1.1" for c in neg["alpn_h2_only"].get("connections", [])),
               {"first_alpn": first.get("alpn"), "h2_only": neg["alpn_h2_only"]})
        expect(tag + "S4 the second handshake reports resumption or the stack cannot report it (recorded)",
               second.get("resumed") in (True, None), {"resumed": agg["resumed_on_second_handshake"]})
        expect(tag + "IP SAN: the loopback leaf verifies for 127.0.0.1", not neg["ip_san"].get("refused"), neg["ip_san"])
        if plain:
            s5 = fp(agg, "idle") - fp(plain, "idle")
            expect(tag + "S5 TLS-enabled idle over the feature-off probe", s5 <= CRITERIA["s5_idle_over_feature_off_bytes"], {"delta": s5})
        s6 = fp(agg, "first_handshake") - fp(agg, "idle")
        expect(tag + "S6 first handshake over idle", s6 <= CRITERIA["s6_first_handshake_over_idle_bytes"], {"delta": s6})
        s7 = (fp(agg, "targets_8") - fp(agg, "targets_1")) / 7
        expect(tag + "S7 eight live connections over one, per connection", s7 <= CRITERIA["s7_per_connection_bytes"], {"per_connection": int(s7)})
        s8 = agg["stages"]["post_close"]["libmalloc_size_in_use"]["median"] - agg["stages"]["idle"]["libmalloc_size_in_use"]["median"]
        expect(tag + "S8 libmalloc in-use after closing all connections is within 64 KiB of idle", s8 <= CRITERIA["s8_in_use_after_close_over_idle_bytes"], {"delta": s8})
        expect(tag + "S9 one process, no descendant at any stage", agg["tree"]["max_descendants"] == 0 and neg.get("descendants_seen", 0) == 0)
        expect(tag + "S10 teardown: live connections are zero after close", agg["stages"]["post_close"]["live"]["maximum"] == 0)

    receipt = {
        "schema": "minicon-surf.tls-court-receipt/0.0.1",
        "design": "labs/native-dom/https-design-0.0.1.md",
        "criteria": CRITERIA,
        "repetitions": args.repetitions,
        "warmup": args.warmup,
        "openssl": subprocess.run(["openssl", "version"], capture_output=True, text=True).stdout.strip(),
        "python_ssl": ssl.OPENSSL_VERSION,
        "fixtures": {"mode": "generated-disposable" if fixtures.temporary else "explicit-private-directory",
                     "generation_seconds": fixtures.generation_seconds, "public_certificates": fixtures.evidence,
                     "parameters": {"key": "P-256", "signature": "sha256", "validity_days": 30,
                                    "loopback_san": "IP:127.0.0.1,DNS:localhost", "wrong_name_san": "DNS:wrong.invalid"}},
        "tls_server": tls_facts,
        "builds": builds,
        "results": results,
        "checks": checks,
        "passed": all(c["passed"] for c in checks),
        "limitations": [
            "the hermetic server is Python's ssl (OpenSSL); negatives that the local OpenSSL refuses to serve are recorded as not exercised",
            "SecureTransport does not expose resumption or its session store; S4 is recorded, not observed, for it",
            "system daemons (trustd, securityd) serve SecureTransport's verification outside the process tree and are not measured",
            "one platform, loopback only, no public network, no leak-absence claim",
        ],
    }
    text = json.dumps(receipt, indent=1, sort_keys=True) + "\n"
    refuse_private_material(text, fixtures)
    Path(args.receipt).write_text(text)
    failed = [c for c in checks if not c["passed"]]
    print(json.dumps({"passed": receipt["passed"], "checks_passed": len(checks) - len(failed), "checks_total": len(checks),
                      "builds": {k: v.get("built") for k, v in builds.items()}}, indent=1))
    for check in failed:
        print("FAIL", json.dumps(check)[:500])
    return 0 if receipt["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
