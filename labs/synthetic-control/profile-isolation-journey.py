#!/usr/bin/env python3
"""Dependency-free G4 court for persistence, isolation, policy, and locks."""

import argparse
import hashlib
import json
import os
import platform
import stat
import subprocess
import tempfile
from pathlib import Path


class Host:
    def __init__(self, binary, profile_root):
        self.process = subprocess.Popen(
            [binary, "serve", "--stdio", "--profile-root", profile_root],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        self.next_id = 0

    def call(self, operation, arguments, expect_ok=True):
        self.next_id += 1
        request_id = f"req_profile_court_{self.next_id}"
        request = {"protocol": "minicon-surf.control", "version": "0.0.1",
                   "request_id": request_id, "deadline_ms": 100,
                   "operation": operation, "arguments": arguments}
        self.process.stdin.write(json.dumps(request, separators=(",", ":")) + "\n")
        self.process.stdin.flush()
        line = self.process.stdout.readline()
        if not line:
            raise RuntimeError(f"profile host exited: {self.process.stderr.read()}")
        response = json.loads(line)
        assert response["request_id"] == request_id
        assert response["ok"] is expect_ok, response
        return response.get("result") if expect_ok else response["error"]

    def finish(self):
        self.process.stdin.close()
        assert self.process.wait(timeout=5) == 0, self.process.stderr.read()


def create_profile(host, name, network, permissions, persistence="persistent"):
    return host.call("profile.create", {
        "persistence": persistence, "name": name,
        "policy": {"network": network, "permissions": permissions},
    })["profile"]


def open_session(host, profile):
    return host.call("session.open", {"profile": profile})["session"]


def put(host, session, kind, value):
    host.call("profile.storage.put", {
        "session": session, "kind": kind, "key": "court", "value": value,
    })


def get(host, session, kind):
    return host.call("profile.storage.get", {
        "session": session, "kind": kind, "key": "court",
    })["value"]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--binary", required=True)
    parser.add_argument("--receipt")
    args = parser.parse_args()
    with tempfile.TemporaryDirectory() as directory:
        root = str(Path(directory) / "profiles")
        owner = Host(args.binary, root)
        alpha = create_profile(owner, "alpha", "online", "deny_by_default")
        beta = create_profile(owner, "beta", "offline", "allow_by_default")
        scratch = create_profile(owner, "scratch", "online", "deny_by_default", "ephemeral")
        alpha_session = open_session(owner, alpha)
        beta_session = open_session(owner, beta)
        scratch_session = open_session(owner, scratch)
        if os.name == "posix":
            for name in ("alpha", "beta"):
                assert stat.S_IMODE((Path(root) / name).stat().st_mode) == 0o700
                assert stat.S_IMODE((Path(root) / name / "profile.json").stat().st_mode) == 0o600
                assert stat.S_IMODE((Path(root) / name / "writer.lock").stat().st_mode) == 0o600
        for session, cookie, local in [
            (alpha_session, "alpha-cookie", "alpha-local"),
            (beta_session, "beta-cookie", "beta-local"),
            (scratch_session, "scratch-cookie", "scratch-local"),
        ]:
            put(owner, session, "cookie", cookie)
            put(owner, session, "local_storage", local)

        contender = Host(args.binary, root)
        listed = contender.call("profile.list", {})
        assert {item["profile"] for item in listed["profiles"]} == {alpha, beta}
        assert scratch not in {item["profile"] for item in listed["profiles"]}
        locked = contender.call("session.open", {"profile": alpha}, expect_ok=False)
        assert locked["code"] == "profile_locked" and locked["scope"]["id"] == alpha

        for session in (alpha_session, beta_session, scratch_session):
            owner.call("session.close", {"session": session})
        owner.finish()

        alpha_reopened = open_session(contender, alpha)
        beta_reopened = open_session(contender, beta)
        assert get(contender, alpha_reopened, "cookie") == "alpha-cookie"
        assert get(contender, alpha_reopened, "local_storage") == "alpha-local"
        assert get(contender, beta_reopened, "cookie") == "beta-cookie"
        assert get(contender, beta_reopened, "local_storage") == "beta-local"
        alpha_policy = contender.call("profile.inspect", {"profile": alpha})["policy"]
        beta_policy = contender.call("profile.inspect", {"profile": beta})["policy"]
        assert alpha_policy == {"network": "online", "permissions": "deny_by_default"}
        assert beta_policy == {"network": "offline", "permissions": "allow_by_default"}
        contender.call("session.close", {"session": alpha_reopened})
        contender.call("session.close", {"session": beta_reopened})
        contender.finish()

        corrupt = Path(root) / "broken"
        corrupt.mkdir()
        (corrupt / "profile.json").write_text("{not-json", encoding="utf-8")
        recovery = Host(args.binary, root)
        recovered = recovery.call("profile.list", {})
        assert {item["profile"] for item in recovered["profiles"]} == {alpha, beta}
        assert recovered["unavailable"][0]["name"] == "broken"
        recovery.call("profile.delete", {"profile": beta})
        assert not (Path(root) / "beta").exists()
        recovery.finish()

        receipt = {
            "schema": "minicon-surf.synthetic-profile-receipt/0.0.1",
            "status": "qualified-synthetic",
            "binary_sha256": hashlib.sha256(Path(args.binary).read_bytes()).hexdigest(),
            "platform": {"os": ("macos" if platform.system() == "Darwin"
                                else platform.system().lower()),
                         "architecture": platform.machine()},
            "profiles": {"persistent": ["alpha", "beta"], "ephemeral": ["scratch"]},
            "processes": {"concurrent_hosts": 2, "restart_generations": 3},
            "persistent_identity_survived_restart": True,
            "ephemeral_absent_after_restart": True,
            "cookie_isolation": True,
            "local_storage_isolation": True,
            "network_policy_isolation": True,
            "permission_policy_isolation": True,
            "single_writer_conflict": "profile_locked",
            "lock_released_after_owner_close": True,
            "corrupt_profile_failed_closed": True,
            "healthy_profiles_survived_corrupt_sibling": True,
            "unix_private_permissions": ({"directory": "0700", "record_and_lock": "0600"}
                                         if os.name == "posix" else None),
            "bounded": {"profiles": 8, "entries_per_bucket": 32,
                        "key_bytes": 64, "value_bytes": 1024},
            "limitations": ["synthetic cookie/local-storage maps, not an engine cookie jar",
                            "cache, history, downloads, and permission prompts are not implemented",
                            "synthetic values are unencrypted and must not hold real credentials",
                            "readonly and copy-on-write profiles remain future work"],
        }
        encoded = json.dumps(receipt, indent=2, sort_keys=True) + "\n"
        if args.receipt:
            Path(args.receipt).write_text(encoded, encoding="utf-8")
        print(encoded, end="")


if __name__ == "__main__":
    main()
