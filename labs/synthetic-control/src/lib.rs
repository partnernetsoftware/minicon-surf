use serde::Serialize;
use serde_json::{Value, json};
use std::collections::BTreeMap;
use std::mem::size_of;
use std::thread;
use std::time::{Duration, Instant};

pub mod cdp;

pub const PROTOCOL: &str = "minicon-surf.control";
pub const VERSION: &str = "0.0.1";
pub const MAX_REQUEST_BYTES: usize = 65_536;
pub const MAX_RESPONSE_BYTES: usize = 4_194_304;
pub const MAX_DEPTH: usize = 32;
pub const MAX_COLLECTION: usize = 10_000;
pub const MAX_PROFILES: usize = 8;
pub const MAX_SESSIONS: usize = 16;
pub const MAX_TARGETS: usize = 32;
pub const MAX_NODES_PER_TARGET: usize = 128;
pub const KNOWN_OPERATIONS: &[&str] = &[
    "profile.create",
    "profile.list",
    "profile.inspect",
    "profile.delete",
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
];

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Request {
    pub request_id: String,
    pub deadline: Duration,
    pub operation: String,
    pub arguments: Value,
}

#[derive(Debug, Clone, Serialize)]
pub struct Response {
    protocol: &'static str,
    version: &'static str,
    request_id: String,
    ok: bool,
    #[serde(skip_serializing_if = "Option::is_none")]
    result: Option<Value>,
    #[serde(skip_serializing_if = "Option::is_none")]
    error: Option<ControlError>,
}

#[derive(Debug)]
pub struct ParseError(Box<Response>);

impl ParseError {
    pub fn into_response(self) -> Response {
        *self.0
    }
}

impl From<Response> for ParseError {
    fn from(response: Response) -> Self {
        Self(Box::new(response))
    }
}

#[derive(Debug, Clone, Serialize)]
pub struct ControlError {
    code: &'static str,
    message: String,
    retryable: bool,
    #[serde(skip_serializing_if = "Option::is_none")]
    scope: Option<Scope>,
    #[serde(skip_serializing_if = "Option::is_none")]
    details: Option<Value>,
}

#[derive(Debug, Clone, Serialize)]
struct Scope {
    kind: &'static str,
    id: String,
}

impl Response {
    fn success(request_id: String, result: Value) -> Self {
        Self {
            protocol: PROTOCOL,
            version: VERSION,
            request_id,
            ok: true,
            result: Some(result),
            error: None,
        }
    }

    pub fn failure(request_id: String, error: ControlError) -> Self {
        Self {
            protocol: PROTOCOL,
            version: VERSION,
            request_id,
            ok: false,
            result: None,
            error: Some(error),
        }
    }

    pub fn invalid(request_id: impl Into<String>, message: impl Into<String>) -> Self {
        Self::failure(
            request_id.into(),
            ControlError::new("invalid_request", message, false),
        )
    }

    pub fn to_bounded_json(&self) -> Vec<u8> {
        let encoded = serde_json::to_vec(self).expect("response serialization is infallible");
        if encoded.len() <= MAX_RESPONSE_BYTES {
            return encoded;
        }
        serde_json::to_vec(&Self::failure(
            self.request_id.clone(),
            ControlError::new("resource_limit", "response exceeds byte limit", true),
        ))
        .expect("bounded failure serialization is infallible")
    }

    pub fn into_outcome(self) -> Result<Value, &'static str> {
        match (self.result, self.error) {
            (Some(result), None) if self.ok => Ok(result),
            (None, Some(error)) if !self.ok => Err(error.code),
            _ => Err("internal"),
        }
    }
}

impl ControlError {
    fn new(code: &'static str, message: impl Into<String>, retryable: bool) -> Self {
        Self {
            code,
            message: truncate_chars(message.into(), 512),
            retryable,
            scope: None,
            details: None,
        }
    }

    fn scoped(mut self, kind: &'static str, id: impl Into<String>) -> Self {
        self.scope = Some(Scope {
            kind,
            id: id.into(),
        });
        self
    }

    fn details(mut self, details: Value) -> Self {
        self.details = Some(details);
        self
    }
}

fn truncate_chars(mut value: String, maximum: usize) -> String {
    if let Some((index, _)) = value.char_indices().nth(maximum) {
        value.truncate(index);
    }
    value
}

pub fn parse_request(bytes: &[u8]) -> Result<Request, ParseError> {
    if bytes.len() > MAX_REQUEST_BYTES {
        return Err(Response::invalid("req_invalid", "request exceeds byte limit").into());
    }
    let document: Value = serde_json::from_slice(bytes)
        .map_err(|_| Response::invalid("req_invalid", "request is not valid JSON"))?;
    validate_bounds(&document, 0)
        .map_err(|message| Response::invalid(request_id_or_fallback(&document), message))?;
    let object = document
        .as_object()
        .ok_or_else(|| Response::invalid("req_invalid", "request is not an object"))?;
    let allowed = [
        "protocol",
        "version",
        "request_id",
        "deadline_ms",
        "operation",
        "arguments",
    ];
    if object.len() != allowed.len() || !allowed.iter().all(|key| object.contains_key(*key)) {
        return Err(
            Response::invalid(request_id_or_fallback(&document), "request fields differ").into(),
        );
    }
    let request_id = object
        .get("request_id")
        .and_then(Value::as_str)
        .filter(|value| valid_typed_id("req", value))
        .ok_or_else(|| Response::invalid("req_invalid", "request_id differs"))?
        .to_owned();
    if object.get("protocol").and_then(Value::as_str) != Some(PROTOCOL)
        || object.get("version").and_then(Value::as_str) != Some(VERSION)
    {
        return Err(Response::invalid(request_id, "protocol or version differs").into());
    }
    let deadline_ms = object
        .get("deadline_ms")
        .and_then(Value::as_u64)
        .filter(|value| (1..=120_000).contains(value))
        .ok_or_else(|| Response::invalid(request_id.clone(), "deadline_ms differs"))?;
    let operation = object
        .get("operation")
        .and_then(Value::as_str)
        .filter(|value| known_operation(value))
        .ok_or_else(|| Response::invalid(request_id.clone(), "operation differs"))?
        .to_owned();
    let arguments = object
        .get("arguments")
        .filter(|value| value.is_object())
        .ok_or_else(|| Response::invalid(request_id.clone(), "arguments is not an object"))?
        .clone();
    Ok(Request {
        request_id,
        deadline: Duration::from_millis(deadline_ms),
        operation,
        arguments,
    })
}

fn request_id_or_fallback(document: &Value) -> String {
    document
        .get("request_id")
        .and_then(Value::as_str)
        .filter(|value| valid_typed_id("req", value))
        .unwrap_or("req_invalid")
        .to_owned()
}

fn validate_bounds(value: &Value, depth: usize) -> Result<(), &'static str> {
    if depth > MAX_DEPTH {
        return Err("request nesting exceeds limit");
    }
    match value {
        Value::Array(values) => {
            if values.len() > MAX_COLLECTION {
                return Err("request collection exceeds limit");
            }
            for value in values {
                validate_bounds(value, depth + 1)?;
            }
        }
        Value::Object(values) => {
            if values.len() > MAX_COLLECTION {
                return Err("request collection exceeds limit");
            }
            for value in values.values() {
                validate_bounds(value, depth + 1)?;
            }
        }
        _ => {}
    }
    Ok(())
}

fn known_operation(value: &str) -> bool {
    KNOWN_OPERATIONS.contains(&value)
}

fn valid_typed_id(kind: &str, value: &str) -> bool {
    let Some(suffix) = value
        .strip_prefix(kind)
        .and_then(|rest| rest.strip_prefix('_'))
    else {
        return false;
    };
    (1..=64).contains(&suffix.len())
        && suffix.bytes().enumerate().all(|(index, byte)| {
            byte.is_ascii_lowercase()
                || byte.is_ascii_digit()
                || (index > 0 && matches!(byte, b'_' | b'-'))
        })
}

#[derive(Debug, Clone)]
struct Profile {
    id: String,
    persistence: Persistence,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
enum Persistence {
    Persistent,
    Ephemeral,
}

#[derive(Debug, Clone)]
struct Session {
    id: String,
    profile_id: String,
}

#[derive(Debug, Clone)]
struct Target {
    id: String,
    session_id: String,
    revision: u64,
    nodes: Vec<Node>,
}

#[derive(Debug, Clone)]
struct Node {
    id: String,
    role: String,
    name: String,
}

#[derive(Debug, Default)]
pub struct ControlState {
    profiles: BTreeMap<String, Profile>,
    sessions: BTreeMap<String, Session>,
    targets: BTreeMap<String, Target>,
    next_profile: u64,
    next_session: u64,
    next_target: u64,
}

impl ControlState {
    pub fn execute(&mut self, request: Request) -> Response {
        let request_id = request.request_id.clone();
        let outcome = match request.operation.as_str() {
            "profile.create" => self.profile_create(&request.arguments),
            "profile.list" => self.profile_list(&request.arguments),
            "session.open" => self.session_open(&request.arguments),
            "session.list" => self.session_list(&request.arguments),
            "target.open" => self.target_open(&request.arguments),
            "target.list" => self.target_list(&request.arguments),
            "target.inspect" => self.target_inspect(&request.arguments),
            "target.close" => self.target_close(&request.arguments),
            "target.snapshot" => self.target_snapshot(&request.arguments),
            "target.act" => self.target_act(&request.arguments),
            "target.wait" => self.target_wait(&request.arguments, request.deadline),
            "memory.report" => self.memory_report(&request.arguments),
            _ => Err(ControlError::new(
                "unsupported_operation",
                "operation is reserved but not implemented by synthetic-control",
                false,
            )),
        };
        match outcome {
            Ok(result) => Response::success(request_id, result),
            Err(error) => Response::failure(request_id, error),
        }
    }

    fn profile_create(&mut self, arguments: &Value) -> Result<Value, ControlError> {
        let object = exact_object(arguments, &["persistence"])?;
        let persistence = match string_field(object, "persistence")? {
            "persistent" => Persistence::Persistent,
            "ephemeral" => Persistence::Ephemeral,
            _ => return Err(invalid("persistence differs")),
        };
        if self.profiles.len() >= MAX_PROFILES {
            return Err(limit("profile capacity reached"));
        }
        self.next_profile += 1;
        let id = format!("profile_{}", self.next_profile);
        self.profiles.insert(
            id.clone(),
            Profile {
                id: id.clone(),
                persistence,
            },
        );
        Ok(json!({"kind":"profile","profile":id,"persistence":persistence}))
    }

    fn profile_list(&self, arguments: &Value) -> Result<Value, ControlError> {
        exact_object(arguments, &[])?;
        Ok(json!({
            "kind":"profile_list",
            "profiles":self.profiles.values().map(|profile| json!({
                "profile":profile.id,
                "persistence":profile.persistence,
            })).collect::<Vec<_>>()
        }))
    }

    fn session_open(&mut self, arguments: &Value) -> Result<Value, ControlError> {
        let object = exact_object(arguments, &["profile"])?;
        let profile = typed_field(object, "profile", "profile")?;
        if !self.profiles.contains_key(profile) {
            return Err(not_found("profile", profile));
        }
        if self.sessions.len() >= MAX_SESSIONS {
            return Err(limit("session capacity reached"));
        }
        self.next_session += 1;
        let id = format!("session_{}", self.next_session);
        self.sessions.insert(
            id.clone(),
            Session {
                id: id.clone(),
                profile_id: profile.to_owned(),
            },
        );
        Ok(json!({"kind":"session","session":id,"profile":profile}))
    }

    fn session_list(&self, arguments: &Value) -> Result<Value, ControlError> {
        exact_object(arguments, &[])?;
        Ok(json!({
            "kind":"session_list",
            "sessions":self.sessions.values().map(|session| json!({
                "session":session.id,
                "profile":session.profile_id,
            })).collect::<Vec<_>>()
        }))
    }

    fn target_open(&mut self, arguments: &Value) -> Result<Value, ControlError> {
        let object = exact_object(arguments, &["session"])?;
        let session = typed_field(object, "session", "session")?;
        if !self.sessions.contains_key(session) {
            return Err(not_found("session", session));
        }
        if self.targets.len() >= MAX_TARGETS {
            return Err(limit("target capacity reached"));
        }
        self.next_target += 1;
        let id = format!("target_{}", self.next_target);
        let nodes = vec![
            Node {
                id: "node_heading_1".into(),
                role: "heading".into(),
                name: "Memory and Agent Court".into(),
            },
            Node {
                id: "node_button_1".into(),
                role: "button".into(),
                name: "Continue".into(),
            },
        ];
        debug_assert!(nodes.len() <= MAX_NODES_PER_TARGET);
        self.targets.insert(
            id.clone(),
            Target {
                id: id.clone(),
                session_id: session.to_owned(),
                revision: 0,
                nodes,
            },
        );
        Ok(json!({"kind":"target","target":id,"session":session,"revision":0}))
    }

    fn target_list(&self, arguments: &Value) -> Result<Value, ControlError> {
        exact_object(arguments, &[])?;
        Ok(json!({
            "kind":"target_list",
            "targets":self.targets.values().map(target_summary).collect::<Vec<_>>()
        }))
    }

    fn target_inspect(&self, arguments: &Value) -> Result<Value, ControlError> {
        let object = exact_object(arguments, &["target"])?;
        let target_id = typed_field(object, "target", "target")?;
        let target = self
            .targets
            .get(target_id)
            .ok_or_else(|| not_found("target", target_id))?;
        Ok(target_summary(target))
    }

    fn target_close(&mut self, arguments: &Value) -> Result<Value, ControlError> {
        let object = exact_object(arguments, &["target"])?;
        let target_id = typed_field(object, "target", "target")?;
        self.targets
            .remove(target_id)
            .ok_or_else(|| not_found("target", target_id))?;
        Ok(json!({"kind":"target_closed","target":target_id}))
    }

    fn target_snapshot(&self, arguments: &Value) -> Result<Value, ControlError> {
        let object = exact_object(arguments, &["target", "format", "max_bytes", "max_nodes"])?;
        let target_id = typed_field(object, "target", "target")?;
        if string_field(object, "format")? != "semantic" {
            return Err(invalid("snapshot format differs"));
        }
        let max_bytes = bounded_u64(object, "max_bytes", 1, MAX_RESPONSE_BYTES as u64)? as usize;
        let max_nodes = bounded_u64(object, "max_nodes", 1, MAX_COLLECTION as u64)? as usize;
        let target = self
            .targets
            .get(target_id)
            .ok_or_else(|| not_found("target", target_id))?;
        let nodes = target
            .nodes
            .iter()
            .take(max_nodes)
            .map(|node| {
                json!({
                    "reference":{"target":target.id,"revision":target.revision,"node":node.id},
                    "role":node.role,
                    "name":node.name,
                })
            })
            .collect::<Vec<_>>();
        let result = json!({
            "kind":"semantic_snapshot",
            "target":target.id,
            "revision":target.revision,
            "truncated":nodes.len() < target.nodes.len(),
            "nodes":nodes,
        });
        if serde_json::to_vec(&result).map_or(true, |encoded| encoded.len() > max_bytes) {
            return Err(limit("snapshot exceeds caller byte limit"));
        }
        Ok(result)
    }

    fn target_act(&mut self, arguments: &Value) -> Result<Value, ControlError> {
        let object = exact_object(arguments, &["target", "reference", "action"])?;
        let target_id = typed_field(object, "target", "target")?;
        let reference = exact_object(
            object
                .get("reference")
                .ok_or_else(|| invalid("reference missing"))?,
            &["target", "revision", "node"],
        )?;
        let reference_target = typed_field(reference, "target", "target")?;
        let node_id = typed_field(reference, "node", "node")?;
        let revision = bounded_u64(reference, "revision", 0, u64::MAX)?;
        if reference_target != target_id {
            return Err(invalid("reference target differs"));
        }
        let action = exact_object(
            object
                .get("action")
                .ok_or_else(|| invalid("action missing"))?,
            &["kind"],
        )?;
        if string_field(action, "kind")? != "click" {
            return Err(ControlError::new(
                "unsupported_capability",
                "synthetic target only supports click",
                false,
            ));
        }
        let target = self
            .targets
            .get_mut(target_id)
            .ok_or_else(|| not_found("target", target_id))?;
        if revision != target.revision {
            return Err(ControlError::new(
                "stale_revision",
                "node reference revision no longer matches the target",
                true,
            )
            .scoped("target", target_id)
            .details(json!({"reference_revision":revision,"current_revision":target.revision})));
        }
        let node = target
            .nodes
            .iter_mut()
            .find(|node| node.id == node_id)
            .ok_or_else(|| {
                ControlError::new("not_found", "node does not exist", false)
                    .scoped("target", target_id)
            })?;
        if node.role != "button" {
            return Err(ControlError::new(
                "unsupported_capability",
                "click requires a button node",
                false,
            ));
        }
        node.name = "Clicked".into();
        target.revision += 1;
        Ok(json!({"kind":"action","target":target.id,"revision":target.revision,"applied":true}))
    }

    fn target_wait(&self, arguments: &Value, deadline: Duration) -> Result<Value, ControlError> {
        let object = exact_object(arguments, &["target", "condition"])?;
        let target_id = typed_field(object, "target", "target")?;
        let condition = exact_object(
            object
                .get("condition")
                .ok_or_else(|| invalid("condition missing"))?,
            &["kind", "revision"],
        )?;
        if string_field(condition, "kind")? != "revision_at_least" {
            return Err(ControlError::new(
                "unsupported_capability",
                "synthetic target only supports revision_at_least",
                false,
            ));
        }
        let expected = bounded_u64(condition, "revision", 0, u64::MAX)?;
        let started = Instant::now();
        loop {
            let target = self
                .targets
                .get(target_id)
                .ok_or_else(|| not_found("target", target_id))?;
            if target.revision >= expected {
                return Ok(
                    json!({"kind":"wait","target":target.id,"revision":target.revision,"matched":true}),
                );
            }
            if started.elapsed() >= deadline {
                return Err(ControlError::new(
                    "deadline_exceeded",
                    "condition was not met before deadline",
                    true,
                )
                .scoped("target", target_id));
            }
            thread::sleep(Duration::from_millis(1));
        }
    }

    fn memory_report(&self, arguments: &Value) -> Result<Value, ControlError> {
        exact_object(arguments, &[])?;
        let profile_bytes = self
            .profiles
            .values()
            .map(|item| size_of::<Profile>() + item.id.capacity())
            .sum::<usize>();
        let session_bytes = self
            .sessions
            .values()
            .map(|item| size_of::<Session>() + item.id.capacity() + item.profile_id.capacity())
            .sum::<usize>();
        let target_bytes = self
            .targets
            .values()
            .map(|item| {
                size_of::<Target>()
                    + item.id.capacity()
                    + item.session_id.capacity()
                    + item.nodes.capacity() * size_of::<Node>()
                    + item
                        .nodes
                        .iter()
                        .map(|node| {
                            node.id.capacity() + node.role.capacity() + node.name.capacity()
                        })
                        .sum::<usize>()
            })
            .sum::<usize>();
        Ok(json!({
            "kind":"memory_report",
            "semantic":"logical-owned-capacity-lower-bound",
            "owners":{
                "profiles":{"objects":self.profiles.len(),"bytes":profile_bytes,"object_limit":MAX_PROFILES},
                "sessions":{"objects":self.sessions.len(),"bytes":session_bytes,"object_limit":MAX_SESSIONS},
                "targets":{"objects":self.targets.len(),"bytes":target_bytes,"object_limit":MAX_TARGETS},
            },
            "total_accounted_bytes":profile_bytes + session_bytes + target_bytes,
            "limitations":["excludes allocator and map overhead","not RSS/private/PSS/live heap"],
        }))
    }
}

fn target_summary(target: &Target) -> Value {
    json!({
        "kind":"target",
        "target":target.id,
        "session":target.session_id,
        "revision":target.revision,
    })
}

fn exact_object<'a>(
    value: &'a Value,
    expected: &[&str],
) -> Result<&'a serde_json::Map<String, Value>, ControlError> {
    let object = value
        .as_object()
        .ok_or_else(|| invalid("arguments differ"))?;
    if object.len() != expected.len() || !expected.iter().all(|key| object.contains_key(*key)) {
        return Err(invalid("arguments fields differ"));
    }
    Ok(object)
}

fn string_field<'a>(
    object: &'a serde_json::Map<String, Value>,
    key: &str,
) -> Result<&'a str, ControlError> {
    object
        .get(key)
        .and_then(Value::as_str)
        .ok_or_else(|| invalid(format!("{key} differs")))
}

fn typed_field<'a>(
    object: &'a serde_json::Map<String, Value>,
    key: &str,
    kind: &str,
) -> Result<&'a str, ControlError> {
    let value = string_field(object, key)?;
    if !valid_typed_id(kind, value) {
        return Err(invalid(format!("{key} ID differs")));
    }
    Ok(value)
}

fn bounded_u64(
    object: &serde_json::Map<String, Value>,
    key: &str,
    minimum: u64,
    maximum: u64,
) -> Result<u64, ControlError> {
    object
        .get(key)
        .and_then(Value::as_u64)
        .filter(|value| (minimum..=maximum).contains(value))
        .ok_or_else(|| invalid(format!("{key} differs")))
}

fn invalid(message: impl Into<String>) -> ControlError {
    ControlError::new("invalid_request", message, false)
}

fn limit(message: impl Into<String>) -> ControlError {
    ControlError::new("resource_limit", message, true)
}

fn not_found(kind: &'static str, id: &str) -> ControlError {
    ControlError::new("not_found", format!("{kind} does not exist"), false).scoped(kind, id)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn request(id: &str, operation: &str, arguments: Value) -> Request {
        Request {
            request_id: id.into(),
            deadline: Duration::from_millis(5),
            operation: operation.into(),
            arguments,
        }
    }

    fn result(response: Response) -> Value {
        assert!(response.ok, "expected success: {:?}", response.error);
        response.result.unwrap()
    }

    #[test]
    fn one_target_has_revision_scoped_actions_and_waits() {
        let mut state = ControlState::default();
        let profile = result(state.execute(request(
            "req_1",
            "profile.create",
            json!({"persistence":"ephemeral"}),
        )))["profile"]
            .as_str()
            .unwrap()
            .to_owned();
        let session = result(state.execute(request(
            "req_2",
            "session.open",
            json!({"profile":profile}),
        )))["session"]
            .as_str()
            .unwrap()
            .to_owned();
        let target = result(state.execute(request(
            "req_3",
            "target.open",
            json!({"session":session}),
        )))["target"]
            .as_str()
            .unwrap()
            .to_owned();
        let snapshot = result(state.execute(request(
            "req_4",
            "target.snapshot",
            json!({"target":target,"format":"semantic","max_bytes":65536,"max_nodes":10}),
        )));
        let reference = snapshot["nodes"][1]["reference"].clone();
        let action = result(state.execute(request(
            "req_5",
            "target.act",
            json!({"target":target,"reference":reference,"action":{"kind":"click"}}),
        )));
        assert_eq!(action["revision"], 1);
        let wait = result(state.execute(request(
            "req_6",
            "target.wait",
            json!({"target":target,"condition":{"kind":"revision_at_least","revision":1}}),
        )));
        assert_eq!(wait["matched"], true);
        let stale = state.execute(request(
            "req_7",
            "target.act",
            json!({"target":target,"reference":snapshot["nodes"][1]["reference"],"action":{"kind":"click"}}),
        ));
        assert!(!stale.ok);
        assert_eq!(stale.error.unwrap().code, "stale_revision");
        let missed_wait = state.execute(request(
            "req_8",
            "target.wait",
            json!({"target":target,"condition":{"kind":"revision_at_least","revision":2}}),
        ));
        assert_eq!(missed_wait.error.unwrap().code, "deadline_exceeded");
    }

    #[test]
    fn capacity_and_memory_owners_are_explicit() {
        let mut state = ControlState::default();
        for index in 0..MAX_PROFILES {
            let response = state.execute(request(
                &format!("req_{index}"),
                "profile.create",
                json!({"persistence":"ephemeral"}),
            ));
            assert!(response.ok);
        }
        let overflow = state.execute(request(
            "req_overflow",
            "profile.create",
            json!({"persistence":"ephemeral"}),
        ));
        assert_eq!(overflow.error.unwrap().code, "resource_limit");
        let report = result(state.execute(request("req_memory", "memory.report", json!({}))));
        assert_eq!(report["owners"]["profiles"]["objects"], MAX_PROFILES);
        assert!(report["total_accounted_bytes"].as_u64().unwrap() > 0);
    }

    #[test]
    fn parser_rejects_wrong_kind_depth_and_unknown_operation() {
        let wrong_kind = br#"{"protocol":"minicon-surf.control","version":"0.0.1","request_id":"target_wrong","deadline_ms":1,"operation":"profile.list","arguments":{}}"#;
        assert!(parse_request(wrong_kind).is_err());
        let unknown = br#"{"protocol":"minicon-surf.control","version":"0.0.1","request_id":"req_unknown","deadline_ms":1,"operation":"engine.any","arguments":{}}"#;
        assert!(parse_request(unknown).is_err());
        let mut nested = json!({});
        for _ in 0..=MAX_DEPTH {
            nested = json!({"nested":nested});
        }
        let document = json!({
            "protocol":PROTOCOL,"version":VERSION,"request_id":"req_depth",
            "deadline_ms":1,"operation":"profile.list","arguments":nested,
        });
        assert!(parse_request(&serde_json::to_vec(&document).unwrap()).is_err());
    }

    #[test]
    fn reserved_operations_match_protocol_schema() {
        let schema_path = std::path::Path::new(env!("CARGO_MANIFEST_DIR"))
            .join("../../protocol/control-0.0.1.schema.json");
        let schema: Value = serde_json::from_slice(&std::fs::read(schema_path).unwrap()).unwrap();
        let schema_operations = schema["$defs"]["operation"]["enum"]
            .as_array()
            .unwrap()
            .iter()
            .map(|value| value.as_str().unwrap())
            .collect::<std::collections::BTreeSet<_>>();
        let rust_operations = KNOWN_OPERATIONS
            .iter()
            .copied()
            .collect::<std::collections::BTreeSet<_>>();
        assert_eq!(rust_operations, schema_operations);
        assert_eq!(schema["$defs"]["protocol"]["const"], PROTOCOL);
        assert_eq!(schema["$defs"]["version"]["const"], VERSION);
    }
}
