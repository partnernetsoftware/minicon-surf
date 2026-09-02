#!/usr/bin/env python3
"""Lightpanda-backed host for the MiniCon Surf control 0.0.1 vocabulary.

The host speaks bounded NDJSON on stdio and maps the same operations the Servo
lab offers onto Lightpanda's CDP. Because Lightpanda 0.4.0 serves one live
target per server, the host runs one Lightpanda process per target
(`MINICON_SURF_LIGHTPANDA_PER_TARGET=1`, the default) so concurrent targets and
bounded per-target termination come from the process model; set it to `0` to
keep the original single-server mapping and observe the engine's own limit.
It maps: ephemeral profiles, one session, hermetic
fixture targets, semantic snapshots with revision-scoped node references,
click actions and revision waits. The in-page instrumentation is the same
JavaScript the Servo host injects, so the journey measures the vocabulary,
not the engine's embedding API. Every other reserved operation is a typed
`unsupported_operation`; memory reporting is `unsupported_capability` because
Lightpanda 0.4.0 exposes no in-process memory reporter through CDP.

Usage mirrors servo-control:

    control-host.py serve --stdio --fixture-root DIR --config-dir DIR

The Lightpanda executable is named by MINICON_SURF_LIGHTPANDA.
"""

import importlib.util
import json
import os
import pathlib
import re
import subprocess
import sys
import time
import urllib.parse

PROTOCOL = "minicon-surf.control"
VERSION = "0.0.1"
MAX_REQUEST_BYTES = 65_536
MAX_RESPONSE_BYTES = 4_194_304
MAX_TARGETS = 8
MAX_PROFILES = 8
MAX_SNAPSHOT_NODES = 128
ID_SUFFIX = r"[a-z0-9][a-z0-9_-]{0,63}"
OPERATIONS = {
    "profile.create", "profile.list", "profile.inspect", "profile.delete",
    "profile.storage.put", "profile.storage.get", "profile.policy.set",
    "session.open", "session.list", "session.inspect", "session.close",
    "target.open", "target.list", "target.inspect", "target.close",
    "target.snapshot", "target.act", "target.wait", "target.screenshot",
    "surface.show", "surface.hide", "memory.report", "memory.trim",
}

INSTALL_JS = """(() => {
  if (!window.__mcs) {
    const s = { revision: 0, snapshot: -1, nodes: [] };
    new MutationObserver(() => { s.revision += 1; }).observe(
      document.documentElement,
      { childList: true, subtree: true, characterData: true, attributes: true });
    window.__mcs = s;
  }
  return String(window.__mcs.revision);
})()"""
REVISION_JS = "(() => String(window.__mcs ? window.__mcs.revision : -1))()"
READY_JS = "(() => String(document.readyState === 'complete' && !!document.querySelector('h1')))()"


def snapshot_script(max_nodes):
    return """(() => {
  const s = window.__mcs;
  if (!s) return JSON.stringify({ error: "uninstrumented" });
  const role = (el) => {
    const t = el.tagName.toLowerCase();
    if (/^h[1-6]$/.test(t)) return "heading";
    if (t === "button" || (t === "input" && /^(button|submit|reset)$/.test(el.type))) return "button";
    if (t === "a" && el.hasAttribute("href")) return "link";
    if (t === "input" || t === "textarea") return "textbox";
    if (t === "label") return "label";
    if (t === "p" || t === "li") return "text";
    return null;
  };
  const out = [];
  const nodes = [];
  let truncated = false;
  const all = document.body ? document.body.querySelectorAll("*") : [];
  for (const el of all) {
    const r = role(el);
    if (!r) continue;
    if (out.length >= MAX_NODES) { truncated = true; break; }
    let name = (el.textContent || "").trim();
    const entry = { node: "node_" + (nodes.length + 1), role: r };
    if (r === "textbox") {
      const label = el.id ? document.querySelector('label[for="' + el.id + '"]') : null;
      name = (label ? label.textContent : (el.getAttribute("aria-label") || el.name || "")).trim();
      entry.value = String(el.value || "").slice(0, 256);
    }
    entry.name = name.slice(0, 256);
    if (el.id) entry.dom_id = String(el.id).slice(0, 64);
    nodes.push(el);
    out.push(entry);
  }
  s.nodes = nodes;
  s.snapshot = s.revision;
  return JSON.stringify({ revision: s.revision, truncated, nodes: out });
})()""".replace("MAX_NODES", str(int(max_nodes)))


def act_script(revision, index):
    return """(() => {
  const s = window.__mcs;
  if (!s) return JSON.stringify({ error: "uninstrumented" });
  if (s.revision !== REV) return JSON.stringify({ stale: true, current: s.revision });
  if (s.snapshot !== REV) return JSON.stringify({ missing: true });
  const el = s.nodes[IDX];
  if (!el || !el.isConnected) return JSON.stringify({ missing: true });
  const t = el.tagName.toLowerCase();
  if (!(t === "button" || (t === "input" && /^(button|submit|reset)$/.test(el.type)))) {
    return JSON.stringify({ unsupported: true });
  }
  el.click();
  return JSON.stringify({ applied: true });
})()""".replace("REV", str(int(revision))).replace("IDX", str(int(index)))


class ControlError(Exception):
    def __init__(self, code, message, retryable=False, scope=None, details=None):
        super().__init__(message)
        self.code, self.message, self.retryable = code, message[:512], retryable
        self.scope, self.details = scope, details

    def to_json(self):
        error = {"code": self.code, "message": self.message, "retryable": self.retryable}
        if self.scope:
            error["scope"] = {"kind": self.scope[0], "id": self.scope[1]}
        if self.details is not None:
            error["details"] = self.details
        return error


def invalid(message):
    return ControlError("invalid_request", message)


def not_found(kind, ident):
    return ControlError("not_found", f"{kind} does not exist", scope=(kind, ident))


def unsupported_operation(operation):
    return ControlError("unsupported_operation",
                        f"{operation} is reserved by control 0.0.1 but not offered by this Lightpanda host")


def load_cdp_support():
    path = pathlib.Path(__file__).resolve().parents[1] / "court" / "cdp-live-target.py"
    spec = importlib.util.spec_from_file_location("minicon_surf_cdp_support", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def valid_id(prefix, value):
    return isinstance(value, str) and re.fullmatch(prefix + ID_SUFFIX, value) is not None


def exact_object(value, keys):
    if not isinstance(value, dict) or set(value) != set(keys):
        raise invalid(f"expected exactly the fields {list(keys)}")
    return value


def string_field(obj, key):
    value = obj.get(key)
    if not isinstance(value, str) or not value or len(value) > 256:
        raise invalid(f"{key} must be a bounded string")
    return value


def typed_field(obj, key, prefix):
    value = string_field(obj, key)
    if not valid_id(prefix + "_", value):
        raise invalid(f"{key} is not a {prefix} identifier")
    return value


def bounded_int(obj, key, low, high):
    value = obj.get(key)
    if not isinstance(value, int) or isinstance(value, bool) or not (low <= value <= high):
        raise invalid(f"{key} must be an integer in {low}..={high}")
    return value


class Engine:
    """One Lightpanda server process plus its CDP connection."""

    def __init__(self, support, engine_path, directory):
        launch_args = type("Args", (), {"engine": "lightpanda", "browser": engine_path})()
        self.process, endpoint = support.launch(launch_args, directory)
        self.cdp = support.CDP(support.WebSocket(endpoint))

    def stop(self):
        try:
            self.cdp.websocket.close()
        except Exception:
            pass
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait()


class Host:
    def __init__(self, fixture_root, directory, engine, per_target):
        self.support = load_cdp_support()
        self.fixture_root = pathlib.Path(fixture_root)
        self.directory = directory
        self.engine_path = engine
        self.per_target = per_target
        self.shared = None if per_target else Engine(self.support, engine, directory)
        self.profiles = {}
        self.session = None
        self.targets = {}
        self.counters = {"profile": 0, "session": 0, "target": 0}

    # ---- engine helpers -------------------------------------------------
    def evaluate(self, target, expression, deadline):
        if time.monotonic() >= deadline:
            raise ControlError("deadline_exceeded", "engine did not answer before deadline", True,
                               scope=("target", target["id"]))
        try:
            result = target["engine"].cdp.call("Runtime.evaluate", {"expression": expression, "returnByValue": True},
                                               target["cdp_session"])
        except RuntimeError as error:
            raise ControlError("internal", "JavaScript evaluation failed", scope=("target", target["id"]),
                               details={"engine_error": str(error)[:256]})
        if "exceptionDetails" in result:
            raise ControlError("internal", "JavaScript evaluation threw", scope=("target", target["id"]),
                               details={"engine_error": json.dumps(result["exceptionDetails"])[:256]})
        return result.get("result", {}).get("value")

    def evaluate_json(self, target, expression, deadline):
        value = self.evaluate(target, expression, deadline)
        if not isinstance(value, str):
            raise ControlError("internal", "engine returned a non-string value", scope=("target", target["id"]),
                               details={"value": str(value)[:128]})
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            raise ControlError("internal", "engine returned malformed snapshot JSON", scope=("target", target["id"]))

    def revision(self, target, deadline):
        value = self.evaluate(target, REVISION_JS, deadline)
        try:
            revision = int(value)
        except (TypeError, ValueError):
            revision = -1
        if revision < 0:
            raise ControlError("internal", "target lost its revision instrumentation", scope=("target", target["id"]))
        return revision

    def target(self, ident):
        if ident not in self.targets:
            raise not_found("target", ident)
        return self.targets[ident]

    # ---- operations -----------------------------------------------------
    def execute(self, request):
        deadline = time.monotonic() + request["deadline_ms"] / 1000.0
        op, a = request["operation"], request["arguments"]
        if op == "profile.create":
            return self.profile_create(a)
        if op == "profile.list":
            return {"kind": "profile_list", "profiles": [
                {"profile": p["id"], "name": p["name"], "persistence": "ephemeral"} for p in self.profiles.values()]}
        if op == "profile.inspect":
            ident = typed_field(exact_object(a, ["profile"]), "profile", "profile")
            if ident not in self.profiles:
                raise not_found("profile", ident)
            profile = self.profiles[ident]
            return {"kind": "profile", "profile": ident, "name": profile["name"], "persistence": "ephemeral",
                    "sessions": 1 if self.session and self.session["profile"] == ident else 0}
        if op == "profile.delete":
            ident = typed_field(exact_object(a, ["profile"]), "profile", "profile")
            if ident not in self.profiles:
                raise not_found("profile", ident)
            if self.session and self.session["profile"] == ident:
                raise ControlError("conflict", "profile has a live session", True, scope=("profile", ident))
            del self.profiles[ident]
            return {"kind": "profile_deleted", "profile": ident, "persistence": "ephemeral"}
        if op == "session.open":
            return self.session_open(a)
        if op == "session.list":
            return {"kind": "session_list", "sessions": [
                {"session": self.session["id"], "profile": self.session["profile"]}] if self.session else []}
        if op == "session.close":
            return self.session_close(a)
        if op == "target.open":
            return self.target_open(a, deadline)
        if op == "target.list":
            return {"kind": "target_list", "targets": [
                {"target": t["id"], "session": t["session"], "fixture": t["fixture"]} for t in self.targets.values()]}
        if op == "target.inspect":
            ident = typed_field(exact_object(a, ["target"]), "target", "target")
            target = self.target(ident)
            return {"kind": "target", "target": ident, "session": target["session"], "fixture": target["fixture"],
                    "revision": self.revision(target, deadline), "load_complete": True, "crashed": False}
        if op == "target.close":
            ident = typed_field(exact_object(a, ["target"]), "target", "target")
            target = self.targets.pop(ident, None)
            if target is None:
                raise not_found("target", ident)
            self.close_engine_target(target)
            return {"kind": "target_closed", "target": ident}
        if op == "target.snapshot":
            return self.target_snapshot(a, deadline)
        if op == "target.act":
            return self.target_act(a, deadline)
        if op == "target.wait":
            return self.target_wait(a, deadline)
        if op == "memory.report":
            raise ControlError("unsupported_capability",
                               "Lightpanda 0.4.0 exposes no in-process memory reporter through CDP",
                               details={"engine_processes": len(self.targets) if self.per_target else 1})
        raise unsupported_operation(op)

    def profile_create(self, a):
        if not isinstance(a, dict) or "persistence" not in a or not set(a) <= {"persistence", "name"}:
            raise invalid("profile.create accepts persistence and an optional name")
        persistence = string_field(a, "persistence")
        if persistence == "persistent":
            raise ControlError("unsupported_capability", "this Lightpanda host offers ephemeral profiles only")
        if persistence != "ephemeral":
            raise invalid("persistence must be ephemeral or persistent")
        name = None
        if "name" in a:
            name = string_field(a, "name")
            if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", name):
                raise invalid("name must be a short safe identifier")
            if any(p["name"] == name for p in self.profiles.values()):
                raise ControlError("conflict", "profile name already exists")
        if len(self.profiles) >= MAX_PROFILES:
            raise ControlError("resource_limit", "profile capacity reached", True)
        self.counters["profile"] += 1
        ident = f"profile_{self.counters['profile']}"
        self.profiles[ident] = {"id": ident, "name": name}
        return {"kind": "profile", "profile": ident, "name": name, "persistence": "ephemeral", "created": True}

    def session_open(self, a):
        profile = typed_field(exact_object(a, ["profile"]), "profile", "profile")
        if profile not in self.profiles:
            raise not_found("profile", profile)
        if self.session is not None:
            raise ControlError("resource_limit", "this Lightpanda host owns one live session; close it first", True)
        self.counters["session"] += 1
        ident = f"session_{self.counters['session']}"
        self.session = {"id": ident, "profile": profile}
        return {"kind": "session", "session": ident, "profile": profile}

    def session_close(self, a):
        ident = typed_field(exact_object(a, ["session"]), "session", "session")
        if self.session is None or self.session["id"] != ident:
            raise not_found("session", ident)
        closed = list(self.targets.values())
        self.targets.clear()
        for target in closed:
            self.close_engine_target(target)
        session, self.session = self.session, None
        return {"kind": "session_closed", "session": session["id"], "profile": session["profile"],
                "closed_targets": len(closed)}

    def close_engine_target(self, target):
        if self.per_target:
            target["engine"].stop()
            return
        try:
            target["engine"].cdp.call("Target.closeTarget", {"targetId": target["cdp_target"]})
        except RuntimeError:
            pass

    def target_open(self, a, deadline):
        obj = exact_object(a, ["session", "fixture"])
        session = typed_field(obj, "session", "session")
        if self.session is None or self.session["id"] != session:
            raise not_found("session", session)
        fixture = string_field(obj, "fixture")
        if not re.fullmatch(r"[a-z0-9.-]+\.html", fixture) or ".." in fixture:
            raise invalid("fixture must be a court fixture file name")
        path = self.fixture_root / fixture
        if not path.is_file():
            raise ControlError("not_found", "fixture does not exist in the court")
        if len(self.targets) >= MAX_TARGETS:
            raise ControlError("resource_limit", "target capacity reached", True)
        url = "data:text/html," + urllib.parse.quote_from_bytes(path.read_bytes(), safe="")
        self.counters["target"] += 1
        ident = f"target_{self.counters['target']}"
        engine = self.shared
        if self.per_target:
            try:
                engine = Engine(self.support, self.engine_path, self.directory)
            except (OSError, RuntimeError, TimeoutError) as error:
                raise ControlError("internal", "engine process did not start", True,
                                   details={"engine_error": str(error)[:256]})
        try:
            cdp_target = engine.cdp.call("Target.createTarget", {"url": "about:blank"})["targetId"]
            cdp_session = engine.cdp.call("Target.attachToTarget", {"targetId": cdp_target, "flatten": True})["sessionId"]
            engine.cdp.call("Page.enable", session_id=cdp_session)
            engine.cdp.call("Runtime.enable", session_id=cdp_session)
            engine.cdp.call("Page.navigate", {"url": url}, cdp_session)
        except RuntimeError as error:
            if self.per_target:
                engine.stop()
            raise ControlError("resource_limit" if "TargetAlreadyLoaded" in str(error) else "internal",
                               "engine refused a new target", True, details={"engine_error": str(error)[:256]})
        target = {"id": ident, "session": session, "fixture": fixture, "cdp_target": cdp_target,
                  "cdp_session": cdp_session, "last_snapshot": None, "engine": engine}
        try:
            while True:
                if self.evaluate(target, READY_JS, deadline) == "true":
                    break
                time.sleep(0.01)
            revision = self.evaluate(target, INSTALL_JS, deadline)
        except ControlError:
            if self.per_target:
                engine.stop()
            raise
        self.targets[ident] = target
        return {"kind": "target", "target": ident, "session": session,
                "revision": int(revision) if str(revision).isdigit() else 0, "fixture": fixture}

    def target_snapshot(self, a, deadline):
        obj = exact_object(a, ["target", "format", "max_bytes", "max_nodes"])
        ident = typed_field(obj, "target", "target")
        if string_field(obj, "format") != "semantic":
            raise ControlError("unsupported_capability", "only the semantic format is offered")
        max_bytes = bounded_int(obj, "max_bytes", 1, MAX_RESPONSE_BYTES)
        max_nodes = bounded_int(obj, "max_nodes", 1, MAX_SNAPSHOT_NODES)
        target = self.target(ident)
        raw = self.evaluate_json(target, snapshot_script(max_nodes), deadline)
        if "error" in raw:
            raise ControlError("internal", "target lost its revision instrumentation", scope=("target", ident))
        revision = raw.get("revision")
        if not isinstance(revision, int):
            raise ControlError("internal", "snapshot lacks a revision", scope=("target", ident))
        truncated = bool(raw.get("truncated"))
        nodes, budget = [], 0
        for entry in raw.get("nodes", []):
            item = {"reference": {"target": ident, "revision": revision, "node": entry.get("node", "node_0")},
                    "role": entry.get("role"), "name": entry.get("name")}
            if "value" in entry:
                item["value"] = entry["value"]
            if "dom_id" in entry:
                item["dom_id"] = entry["dom_id"]
            budget += len(json.dumps(item))
            if budget > max_bytes:
                truncated = True
                break
            nodes.append(item)
        target["last_snapshot"] = (revision, len(nodes))
        return {"kind": "semantic_snapshot", "target": ident, "revision": revision,
                "truncated": truncated, "nodes": nodes}

    def target_act(self, a, deadline):
        obj = exact_object(a, ["target", "reference", "action"])
        ident = typed_field(obj, "target", "target")
        reference = exact_object(obj["reference"], ["target", "revision", "node"])
        if typed_field(reference, "target", "target") != ident:
            raise invalid("reference target differs")
        node = typed_field(reference, "node", "node")
        revision = bounded_int(reference, "revision", 0, 2 ** 63)
        action = obj["action"]
        if not isinstance(action, dict) or len(action) != 1:
            raise invalid("click action fields differ")
        if string_field(action, "kind") != "click":
            raise ControlError("unsupported_capability", "this Lightpanda host offers click only")
        match = re.fullmatch(r"node_(\d+)", node)
        if not match or int(match.group(1)) < 1:
            raise ControlError("not_found", "node does not exist", scope=("target", ident))
        index = int(match.group(1)) - 1
        target = self.target(ident)
        current = self.revision(target, deadline)
        if current != revision:
            raise ControlError("stale_revision", "node reference revision no longer matches the target", True,
                               scope=("target", ident),
                               details={"reference_revision": revision, "current_revision": current})
        snapshot = target["last_snapshot"]
        if snapshot is None or snapshot[0] != revision or index >= snapshot[1]:
            raise ControlError("not_found", "node does not exist", scope=("target", ident))
        outcome = self.evaluate_json(target, act_script(revision, index), deadline)
        if "current" in outcome:
            raise ControlError("stale_revision", "node reference revision no longer matches the target", True,
                               scope=("target", ident),
                               details={"reference_revision": revision, "current_revision": outcome["current"]})
        if outcome.get("missing"):
            raise ControlError("not_found", "node does not exist", scope=("target", ident))
        if outcome.get("unsupported"):
            raise ControlError("unsupported_capability", "click requires a button node")
        if outcome.get("applied") is not True:
            raise ControlError("internal", "engine did not confirm the action", scope=("target", ident))
        after = self.revision(target, deadline)
        return {"kind": "action", "target": ident, "revision": after, "applied": True}

    def target_wait(self, a, deadline):
        obj = exact_object(a, ["target", "condition"])
        ident = typed_field(obj, "target", "target")
        condition = exact_object(obj["condition"], ["kind", "revision"])
        if string_field(condition, "kind") != "revision_at_least":
            raise ControlError("unsupported_capability", "this Lightpanda host offers revision_at_least only")
        expected = bounded_int(condition, "revision", 0, 2 ** 63)
        while True:
            target = self.target(ident)
            revision = self.revision(target, deadline)
            if revision >= expected:
                return {"kind": "wait", "target": ident, "revision": revision, "matched": True}
            if time.monotonic() >= deadline:
                raise ControlError("deadline_exceeded", "condition was not met before deadline", True,
                                   scope=("target", ident))
            time.sleep(0.005)

    def shutdown(self):
        for target in list(self.targets.values()):
            self.close_engine_target(target)
        if self.shared is not None:
            self.shared.stop()


def parse_request(line):
    try:
        value = json.loads(line)
    except json.JSONDecodeError:
        raise ControlError("invalid_request", "request is not valid JSON")
    if not isinstance(value, dict):
        raise ControlError("invalid_request", "request is not an object")
    request_id = value.get("request_id")
    if not valid_id("req_", request_id):
        raise ControlError("invalid_request", "request_id is missing or malformed")
    expected = {"protocol", "version", "request_id", "deadline_ms", "operation", "arguments"}
    if set(value) != expected:
        raise ControlError("invalid_request", "request fields differ from the envelope", details={"request_id": request_id})
    if value["protocol"] != PROTOCOL or value["version"] != VERSION:
        raise ControlError("invalid_request", "protocol or version differs", details={"request_id": request_id})
    deadline = value["deadline_ms"]
    if not isinstance(deadline, int) or isinstance(deadline, bool) or not (1 <= deadline <= 120_000):
        raise ControlError("invalid_request", "deadline_ms is out of range", details={"request_id": request_id})
    if value["operation"] not in OPERATIONS:
        raise ControlError("invalid_request", "operation is not part of control 0.0.1", details={"request_id": request_id})
    if not isinstance(value["arguments"], dict) or len(value["arguments"]) > 64:
        raise ControlError("invalid_request", "arguments must be a bounded object", details={"request_id": request_id})
    return value


def envelope(request_id, ok, body):
    response = {"protocol": PROTOCOL, "version": VERSION, "request_id": request_id, "ok": ok}
    response["result" if ok else "error"] = body
    encoded = json.dumps(response, separators=(",", ":"))
    if len(encoded.encode()) > MAX_RESPONSE_BYTES:
        return envelope(request_id, False, ControlError("internal", "response exceeds byte limit").to_json())
    return encoded


def main():
    argv = sys.argv[1:]
    if len(argv) != 6 or argv[:2] != ["serve", "--stdio"] or argv[2] != "--fixture-root" or argv[4] != "--config-dir":
        print("usage: control-host.py serve --stdio --fixture-root DIR --config-dir DIR", file=sys.stderr)
        sys.exit(64)
    engine = os.environ.get("MINICON_SURF_LIGHTPANDA")
    if not engine or not os.path.isfile(engine):
        print("MINICON_SURF_LIGHTPANDA must name the Lightpanda executable", file=sys.stderr)
        sys.exit(64)
    config_dir = pathlib.Path(argv[5])
    config_dir.mkdir(parents=True, exist_ok=True)
    per_target = os.environ.get("MINICON_SURF_LIGHTPANDA_PER_TARGET", "1") != "0"
    host = Host(argv[3], str(config_dir), engine, per_target)
    try:
        for raw in sys.stdin.buffer:
            line = raw.rstrip(b"\n")
            if len(line) > MAX_REQUEST_BYTES:
                output = envelope("req_invalid", False, ControlError("invalid_request", "request exceeds byte limit").to_json())
            elif not line:
                output = envelope("req_invalid", False, ControlError("invalid_request", "request is empty").to_json())
            else:
                try:
                    request = parse_request(line.decode("utf-8", "replace"))
                except ControlError as error:
                    request_id = (error.details or {}).get("request_id", "req_invalid")
                    error.details = None
                    output = envelope(request_id, False, error.to_json())
                else:
                    try:
                        output = envelope(request["request_id"], True, host.execute(request))
                    except ControlError as error:
                        output = envelope(request["request_id"], False, error.to_json())
            sys.stdout.write(output + "\n")
            sys.stdout.flush()
    finally:
        host.shutdown()


if __name__ == "__main__":
    main()
