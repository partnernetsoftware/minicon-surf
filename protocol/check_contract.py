#!/usr/bin/env python3
"""Dependency-free contract example and bound checker with negative tests."""

import json
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parent
PROTOCOL = "minicon-surf.control"
VERSION = "0.0.1"
MAX_REQUEST_BYTES = 65_536
MAX_RESPONSE_BYTES = 4_194_304
MAX_DEPTH = 32
MAX_COLLECTION = 10_000
ID_SUFFIX = r"[a-z0-9][a-z0-9_-]{0,63}"
REQUEST_ID = re.compile(rf"^req_{ID_SUFFIX}$")
OBJECT_ID = {
    kind: re.compile(rf"^{kind}_{ID_SUFFIX}$")
    for kind in ("profile", "session", "target", "frame", "realm", "surface", "node")
}
OPERATIONS = {
    "profile.create",
    "profile.list",
    "profile.inspect",
    "profile.delete",
    "profile.storage.put",
    "profile.storage.get",
    "profile.policy.set",
    "session.open",
    "session.list",
    "session.inspect",
    "session.close",
    "target.open",
    "target.list",
    "target.inspect",
    "target.close",
    "target.snapshot",
    "target.act",
    "target.wait",
    "target.screenshot",
    "surface.show",
    "surface.hide",
    "memory.report",
    "memory.trim",
}
ERROR_CODES = {
    "invalid_request",
    "not_found",
    "conflict",
    "profile_locked",
    "stale_revision",
    "deadline_exceeded",
    "resource_limit",
    "unsupported_operation",
    "unsupported_capability",
    "permission_denied",
    "target_crashed",
    "internal",
}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def bounded(value, depth=0):
    require(depth <= MAX_DEPTH, "JSON nesting exceeds contract bound")
    if isinstance(value, dict):
        require(len(value) <= MAX_COLLECTION, "object exceeds collection bound")
        for key, child in value.items():
            require(isinstance(key, str), "object key is not a string")
            bounded(child, depth + 1)
    elif isinstance(value, list):
        require(len(value) <= MAX_COLLECTION, "array exceeds collection bound")
        for child in value:
            bounded(child, depth + 1)


def load_bounded(path, maximum):
    encoded = path.read_bytes()
    require(len(encoded) <= maximum, f"{path.name} exceeds wire byte bound")
    return json.loads(encoded)


def common(document):
    require(isinstance(document, dict), "envelope is not an object")
    require(document.get("protocol") == PROTOCOL, "protocol differs")
    require(document.get("version") == VERSION, "version differs")
    require(REQUEST_ID.fullmatch(document.get("request_id", "")), "invalid request ID")
    bounded(document)


def object_id(kind, value):
    require(isinstance(value, str) and OBJECT_ID[kind].fullmatch(value), f"invalid {kind} ID")


ACTOR = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,63}$")
OWNER_KINDS = {"profile", "session", "target"}


def validate_capability(capability):
    """The optional attenuation envelope: shape only; the host decides authority."""
    require(isinstance(capability, dict), "capability is not an object")
    require(set(capability) == {"owner", "scope", "budget", "audit"}, "capability fields differ")
    validate_scope(capability["owner"])
    scope = capability["scope"]
    require(isinstance(scope, list) and 1 <= len(scope) <= len(OPERATIONS), "capability scope differs")
    require(len(set(scope)) == len(scope) and set(scope) <= OPERATIONS, "capability scope lists unknown operations")
    budget = capability["budget"]
    require(isinstance(budget, dict) and set(budget) == {"result_bytes", "deadline_ms"}, "capability budget differs")
    require(type(budget["result_bytes"]) is int and 1 <= budget["result_bytes"] <= MAX_RESPONSE_BYTES, "capability result budget differs")
    require(type(budget["deadline_ms"]) is int and 1 <= budget["deadline_ms"] <= 120_000, "capability deadline budget differs")
    audit = capability["audit"]
    require(isinstance(audit, dict) and set(audit) == {"actor", "reason"}, "capability audit differs")
    require(isinstance(audit["actor"], str) and ACTOR.fullmatch(audit["actor"]), "capability actor differs")
    require(isinstance(audit["reason"], str) and 1 <= len(audit["reason"]) <= 128, "capability reason differs")


def validate_request(document):
    common(document)
    fields = {"protocol", "version", "request_id", "deadline_ms", "operation", "arguments"}
    require(fields <= set(document) <= fields | {"capability"}, "request fields differ")
    deadline = document["deadline_ms"]
    require(type(deadline) is int and 1 <= deadline <= 120_000, "deadline differs")
    require(document["operation"] in OPERATIONS, "operation differs")
    require(isinstance(document["arguments"], dict), "arguments is not an object")
    if "capability" in document:
        validate_capability(document["capability"])
    encoded = json.dumps(document, separators=(",", ":"), ensure_ascii=False).encode()
    require(len(encoded) <= MAX_REQUEST_BYTES, "request exceeds byte bound")
    if document["operation"].startswith("target."):
        target = document["arguments"].get("target")
        if target is not None:
            object_id("target", target)
    if document["operation"] == "target.snapshot":
        arguments = document["arguments"]
        fields = {"target", "format", "max_bytes", "max_nodes"}
        require(fields <= set(arguments) <= fields | {"frame", "realm"}, "snapshot arguments differ")
        if "frame" in arguments:
            object_id("frame", arguments["frame"])
        if "realm" in arguments:
            object_id("realm", arguments["realm"])
        require(arguments["format"] == "semantic", "snapshot format differs")
        require(
            type(arguments["max_bytes"]) is int
            and 1 <= arguments["max_bytes"] <= MAX_RESPONSE_BYTES,
            "snapshot byte limit differs",
        )
        require(
            type(arguments["max_nodes"]) is int
            and 1 <= arguments["max_nodes"] <= MAX_COLLECTION,
            "snapshot node limit differs",
        )
    elif document["operation"] == "target.act":
        arguments = document["arguments"]
        require(
            set(arguments) == {"target", "reference", "action"},
            "action arguments differ",
        )
        reference = arguments["reference"]
        require(set(reference) == {"target", "revision", "node"}, "action reference differs")
        object_id("target", reference["target"])
        object_id("node", reference["node"])
        require(reference["target"] == arguments["target"], "action target differs")
        require(type(reference["revision"]) is int and reference["revision"] >= 0, "action revision differs")
        require(arguments["action"] == {"kind": "click"}, "initial action differs")


def validate_scope(scope):
    require(set(scope) == {"kind", "id"}, "scope fields differ")
    require(scope["kind"] in OBJECT_ID and scope["kind"] != "node", "scope kind differs")
    object_id(scope["kind"], scope["id"])


def validate_response(document):
    common(document)
    require(type(document.get("ok")) is bool, "response ok is not boolean")
    expected = {"protocol", "version", "request_id", "ok", "result" if document["ok"] else "error"}
    require(set(document) == expected, "success/failure fields are not exclusive")
    encoded = json.dumps(document, separators=(",", ":"), ensure_ascii=False).encode()
    require(len(encoded) <= MAX_RESPONSE_BYTES, "response exceeds byte bound")
    if document["ok"]:
        require(isinstance(document["result"], dict), "result is not an object")
        validate_snapshot(document["result"])
    else:
        error = document["error"]
        require(isinstance(error, dict), "error is not an object")
        require(set(error) <= {"code", "message", "retryable", "scope", "details"}, "error fields differ")
        require({"code", "message", "retryable"} <= set(error), "error fields missing")
        require(error["code"] in ERROR_CODES, "error code differs")
        require(isinstance(error["message"], str) and 1 <= len(error["message"]) <= 512, "error message differs")
        require(type(error["retryable"]) is bool, "retryable is not boolean")
        if "scope" in error:
            validate_scope(error["scope"])
        if "details" in error:
            require(isinstance(error["details"], dict), "details is not an object")


def validate_snapshot(result):
    if result.get("kind") != "semantic_snapshot":
        return
    object_id("target", result.get("target"))
    revision = result.get("revision")
    require(type(revision) is int and revision >= 0, "snapshot revision differs")
    require(type(result.get("truncated")) is bool, "snapshot truncation flag differs")
    require(isinstance(result.get("nodes"), list), "snapshot nodes differ")
    if "frame" in result or "realm" in result or "generation" in result:
        object_id("frame", result.get("frame"))
        object_id("realm", result.get("realm"))
        require(type(result.get("generation")) is int and result["generation"] >= 1, "snapshot generation differs")
    for item in result["nodes"]:
        reference = item.get("reference", {})
        require(set(reference) == {"target", "revision", "node"}, "node reference fields differ")
        object_id("target", reference["target"])
        object_id("node", reference["node"])
        require(reference["target"] == result["target"], "node target differs from snapshot")
        require(reference["revision"] == revision, "node revision differs from snapshot")


def expect_invalid(document, validator):
    try:
        validator(document)
    except ValueError:
        return
    raise AssertionError("negative contract case unexpectedly passed")


def main():
    schema = json.loads((ROOT / "control-0.0.1.schema.json").read_text())
    require(schema["$defs"]["protocol"]["const"] == PROTOCOL, "schema protocol differs")
    require(schema["$defs"]["version"]["const"] == VERSION, "schema version differs")
    require(set(schema["$defs"]["operation"]["enum"]) == OPERATIONS, "schema operations differ")
    require(set(schema["$defs"]["error"]["properties"]["code"]["enum"]) == ERROR_CODES, "schema errors differ")
    for kind in OBJECT_ID:
        require(f"{kind}_id" in schema["$defs"], f"schema lacks {kind} ID")
        require(
            schema["$defs"][f"{kind}_id"]["pattern"] == OBJECT_ID[kind].pattern,
            f"schema {kind} pattern differs",
        )
    mapping = load_bounded(ROOT / "cdp-mapping-0.0.1.json", MAX_RESPONSE_BYTES)
    require(mapping["native_protocol"] == PROTOCOL, "mapping protocol differs")
    require(mapping["native_version"] == VERSION, "mapping version differs")
    mapped = {item["native"]: item for item in mapping["objects"]}
    require(len(mapped) == len(mapping["objects"]), "mapping contains duplicate objects")
    require(
        set(mapped) == {"profile", "session", "target", "frame", "realm", "surface", "revision", "node_reference"},
        "mapping object coverage differs",
    )
    for native in ("profile", "surface", "revision"):
        require(mapped[native]["mapping"] == "none" and mapped[native]["cdp"] is None, f"{native} loss is implicit")
    require(
        mapped["target"]["mapping"] == "qualified-synthetic-one-to-one",
        "target mapping differs",
    )
    require(all(item.get("boundary") for item in mapping["objects"]), "mapping boundary is empty")
    examples = ROOT / "examples"
    request = load_bounded(examples / "target-snapshot.request.json", MAX_REQUEST_BYTES)
    success = load_bounded(examples / "target-snapshot.success.json", MAX_RESPONSE_BYTES)
    stale_request = load_bounded(examples / "target-act-stale.request.json", MAX_REQUEST_BYTES)
    failure = load_bounded(examples / "target-act-stale.failure.json", MAX_RESPONSE_BYTES)
    validate_request(request)
    validate_request(stale_request)
    validate_response(success)
    validate_response(failure)
    require(request["request_id"] == success["request_id"], "response does not echo request ID")
    require(stale_request["request_id"] == failure["request_id"], "failure does not echo request ID")
    capability_request = load_bounded(examples / "target-snapshot-capability.request.json", MAX_REQUEST_BYTES)
    capability_success = load_bounded(examples / "target-snapshot-capability.success.json", MAX_RESPONSE_BYTES)
    surface_request = load_bounded(examples / "surface-owner-capability.request.json", MAX_REQUEST_BYTES)
    surface_failure = load_bounded(examples / "surface-owner-capability.failure.json", MAX_RESPONSE_BYTES)
    validate_request(capability_request)
    validate_request(surface_request)
    validate_response(capability_success)
    validate_response(surface_failure)
    require(capability_request["request_id"] == capability_success["request_id"], "capability success does not echo request ID")
    require(surface_request["request_id"] == surface_failure["request_id"], "capability failure does not echo request ID")
    require(capability_request["operation"] in capability_request["capability"]["scope"], "capability example is outside its own scope")
    require(capability_request["deadline_ms"] <= capability_request["capability"]["budget"]["deadline_ms"], "capability example exceeds its deadline budget")
    require(surface_request["capability"]["owner"]["kind"] not in OWNER_KINDS, "surface example must name a non-owner")
    require(surface_failure["error"]["code"] == "permission_denied", "surface example must be a typed refusal")
    require(surface_failure["error"]["details"]["reason"] == "surface_is_not_an_owner", "surface refusal reason differs")
    frame_request = load_bounded(examples / "target-snapshot-frame.request.json", MAX_REQUEST_BYTES)
    frame_success = load_bounded(examples / "target-snapshot-frame.success.json", MAX_RESPONSE_BYTES)
    retired_request = load_bounded(examples / "target-snapshot-retired-realm.request.json", MAX_REQUEST_BYTES)
    retired_failure = load_bounded(examples / "target-snapshot-retired-realm.failure.json", MAX_RESPONSE_BYTES)
    validate_request(frame_request)
    validate_request(retired_request)
    validate_response(frame_success)
    validate_response(retired_failure)
    require(frame_request["request_id"] == frame_success["request_id"], "frame success does not echo request ID")
    require(retired_request["request_id"] == retired_failure["request_id"], "retired-realm failure does not echo request ID")
    require(frame_success["result"]["frame"] == frame_request["arguments"]["frame"], "frame snapshot names another frame")
    require(frame_success["result"]["realm"] == frame_request["arguments"]["realm"], "frame snapshot names another realm")
    require(retired_failure["error"]["code"] == "not_found" and retired_failure["error"]["scope"]["kind"] == "realm", "retired realm must be not_found with realm scope")

    wrong_revision = json.loads(json.dumps(success))
    wrong_revision["result"]["nodes"][0]["reference"]["revision"] = 6
    expect_invalid(wrong_revision, validate_response)
    both_branches = json.loads(json.dumps(success))
    both_branches["error"] = failure["error"]
    expect_invalid(both_branches, validate_response)
    unknown_operation = json.loads(json.dumps(request))
    unknown_operation["operation"] = "engine.do_anything"
    expect_invalid(unknown_operation, validate_request)
    oversized = json.loads(json.dumps(request))
    oversized["arguments"]["padding"] = "x" * MAX_REQUEST_BYTES
    expect_invalid(oversized, validate_request)
    wrong_target_id = json.loads(json.dumps(request))
    wrong_target_id["arguments"]["target"] = "session_wrong_kind"
    expect_invalid(wrong_target_id, validate_request)
    too_deep = {
        "protocol": PROTOCOL,
        "version": VERSION,
        "request_id": "req_depth",
        "deadline_ms": 1,
        "operation": "profile.list",
        "arguments": {},
    }
    cursor = too_deep["arguments"]
    for _ in range(MAX_DEPTH + 1):
        cursor["nested"] = {}
        cursor = cursor["nested"]
    expect_invalid(too_deep, validate_request)
    too_many = {
        "protocol": PROTOCOL,
        "version": VERSION,
        "request_id": "req_collection",
        "deadline_ms": 1,
        "operation": "profile.list",
        "arguments": {"items": [None] * (MAX_COLLECTION + 1)},
    }
    expect_invalid(too_many, validate_request)
    missing_audit = json.loads(json.dumps(capability_request))
    del missing_audit["capability"]["audit"]
    expect_invalid(missing_audit, validate_request)
    unknown_scope = json.loads(json.dumps(capability_request))
    unknown_scope["capability"]["scope"].append("engine.do_anything")
    expect_invalid(unknown_scope, validate_request)
    zero_budget = json.loads(json.dumps(capability_request))
    zero_budget["capability"]["budget"]["result_bytes"] = 0
    expect_invalid(zero_budget, validate_request)
    node_owner = json.loads(json.dumps(capability_request))
    node_owner["capability"]["owner"] = {"kind": "node", "id": "node_button_1"}
    expect_invalid(node_owner, validate_request)
    extra_field = json.loads(json.dumps(capability_request))
    extra_field["capability"]["grant"] = "everything"
    expect_invalid(extra_field, validate_request)
    wrong_frame_kind = json.loads(json.dumps(frame_request))
    wrong_frame_kind["arguments"]["frame"] = "realm_court_child_1"
    expect_invalid(wrong_frame_kind, validate_request)
    unknown_snapshot_argument = json.loads(json.dumps(frame_request))
    unknown_snapshot_argument["arguments"]["world"] = "isolated"
    expect_invalid(unknown_snapshot_argument, validate_request)
    zero_generation = json.loads(json.dumps(frame_success))
    zero_generation["result"]["generation"] = 0
    expect_invalid(zero_generation, validate_response)
    print("control 0.0.1: vocabulary mapping, 12 examples, and 15 negative cases passed")


if __name__ == "__main__":
    main()
