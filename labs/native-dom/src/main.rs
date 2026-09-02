//! Native bounded route, first slice: HTML parsing and DOM only.
//!
//! `native-dom-control` serves the control 0.0.1 vocabulary from an
//! html5ever-parsed document with no layout, no script realm and no network.
//! It answers semantic snapshots for hermetic court fixtures and refuses every
//! mutation with a typed `unsupported_capability`, so the court can measure what
//! HTML/DOM alone costs and exactly which Agent semantics it cannot provide.

use std::collections::BTreeMap;
use std::error::Error;
use std::ffi::c_void;
use std::io::{self, BufRead, Write};
use std::path::PathBuf;
use std::time::{Duration, Instant};

use dom_query::Document;
use serde_json::{Map, Value, json};

const PROTOCOL: &str = "minicon-surf.control";
const VERSION: &str = "0.0.1";
const MAX_REQUEST_BYTES: usize = 65_536;
const MAX_RESPONSE_BYTES: usize = 4_194_304;
const MAX_DEADLINE_MS: u64 = 120_000;
const MAX_TARGETS: usize = 8;
const MAX_PROFILES: usize = 8;
const MAX_SNAPSHOT_NODES: u64 = 128;
const MAX_FIXTURE_BYTES: u64 = 1_048_576;
const OPERATIONS: &[&str] = &[
    "profile.create", "profile.list", "profile.inspect", "profile.delete",
    "profile.storage.put", "profile.storage.get", "profile.policy.set",
    "session.open", "session.list", "session.inspect", "session.close",
    "target.open", "target.list", "target.inspect", "target.close",
    "target.snapshot", "target.act", "target.wait", "target.screenshot",
    "surface.show", "surface.hide", "memory.report", "memory.trim",
];

// ---------------------------------------------------------------- envelope

struct ControlError {
    code: &'static str,
    message: String,
    retryable: bool,
    scope: Option<(&'static str, String)>,
    details: Option<Value>,
}

impl ControlError {
    fn new(code: &'static str, message: impl Into<String>, retryable: bool) -> Self {
        let mut message = message.into();
        message.truncate(512);
        ControlError { code, message, retryable, scope: None, details: None }
    }

    fn scoped(mut self, kind: &'static str, id: &str) -> Self {
        self.scope = Some((kind, id.to_owned()));
        self
    }

    fn details(mut self, details: Value) -> Self {
        self.details = Some(details);
        self
    }

    fn to_json(&self) -> Value {
        let mut error = json!({"code":self.code,"message":self.message,"retryable":self.retryable});
        if let Some((kind, id)) = &self.scope {
            error["scope"] = json!({"kind":kind,"id":id});
        }
        if let Some(details) = &self.details {
            error["details"] = details.clone();
        }
        error
    }
}

fn invalid(message: &str) -> ControlError {
    ControlError::new("invalid_request", message, false)
}

fn not_found(kind: &'static str, id: &str) -> ControlError {
    ControlError::new("not_found", format!("{kind} does not exist"), false).scoped(kind, id)
}

fn unsupported_operation(operation: &str) -> ControlError {
    ControlError::new(
        "unsupported_operation",
        format!("{operation} is reserved by control 0.0.1 but not offered by the native DOM slice"),
        false,
    )
}

struct Request {
    request_id: String,
    deadline: Duration,
    operation: String,
    arguments: Value,
}

fn valid_id(prefix: &str, value: &str) -> bool {
    let Some(suffix) = value.strip_prefix(prefix) else {
        return false;
    };
    let bytes = suffix.as_bytes();
    !bytes.is_empty()
        && bytes.len() <= 64
        && (bytes[0].is_ascii_lowercase() || bytes[0].is_ascii_digit())
        && bytes
            .iter()
            .all(|b| b.is_ascii_lowercase() || b.is_ascii_digit() || *b == b'_' || *b == b'-')
}

fn parse_request(bytes: &[u8]) -> Result<Request, (String, ControlError)> {
    let value: Value = serde_json::from_slice(bytes)
        .map_err(|_| ("req_invalid".to_owned(), invalid("request is not valid JSON")))?;
    let object = value
        .as_object()
        .ok_or_else(|| ("req_invalid".to_owned(), invalid("request is not an object")))?;
    let request_id = object
        .get("request_id")
        .and_then(Value::as_str)
        .filter(|id| valid_id("req_", id))
        .map(str::to_owned)
        .ok_or_else(|| ("req_invalid".to_owned(), invalid("request_id is missing or malformed")))?;
    let fail = |message: &str| (request_id.clone(), invalid(message));
    let expected = ["protocol", "version", "request_id", "deadline_ms", "operation", "arguments"];
    if object.len() != expected.len() || !expected.iter().all(|key| object.contains_key(*key)) {
        return Err(fail("request fields differ from the envelope"));
    }
    if object.get("protocol").and_then(Value::as_str) != Some(PROTOCOL) {
        return Err(fail("protocol differs"));
    }
    if object.get("version").and_then(Value::as_str) != Some(VERSION) {
        return Err(fail("version differs"));
    }
    let deadline_ms = object
        .get("deadline_ms")
        .and_then(Value::as_u64)
        .filter(|ms| (1..=MAX_DEADLINE_MS).contains(ms))
        .ok_or_else(|| fail("deadline_ms is out of range"))?;
    let operation = object
        .get("operation")
        .and_then(Value::as_str)
        .filter(|op| OPERATIONS.contains(op))
        .ok_or_else(|| fail("operation is not part of control 0.0.1"))?
        .to_owned();
    let arguments = object
        .get("arguments")
        .filter(|a| a.as_object().is_some_and(|o| o.len() <= 64))
        .cloned()
        .ok_or_else(|| fail("arguments must be a bounded object"))?;
    Ok(Request { request_id, deadline: Duration::from_millis(deadline_ms), operation, arguments })
}

fn exact_object<'a>(value: &'a Value, keys: &[&str]) -> Result<&'a Map<String, Value>, ControlError> {
    let object = value.as_object().ok_or_else(|| invalid("arguments must be an object"))?;
    if object.len() != keys.len() || !keys.iter().all(|key| object.contains_key(*key)) {
        return Err(invalid(&format!("expected exactly the fields {keys:?}")));
    }
    Ok(object)
}

fn string_field<'a>(object: &'a Map<String, Value>, key: &str) -> Result<&'a str, ControlError> {
    object
        .get(key)
        .and_then(Value::as_str)
        .filter(|s| !s.is_empty() && s.len() <= 256)
        .ok_or_else(|| invalid(&format!("{key} must be a bounded string")))
}

fn typed_field<'a>(object: &'a Map<String, Value>, key: &str, prefix: &str) -> Result<&'a str, ControlError> {
    let value = string_field(object, key)?;
    if !valid_id(&format!("{prefix}_"), value) {
        return Err(invalid(&format!("{key} is not a {prefix} identifier")));
    }
    Ok(value)
}

fn bounded_u64(object: &Map<String, Value>, key: &str, min: u64, max: u64) -> Result<u64, ControlError> {
    object
        .get(key)
        .and_then(Value::as_u64)
        .filter(|v| (min..=max).contains(v))
        .ok_or_else(|| invalid(&format!("{key} must be an integer in {min}..={max}")))
}

fn envelope(request_id: &str, body: Result<Value, ControlError>) -> Vec<u8> {
    let response = match body {
        Ok(result) => json!({"protocol":PROTOCOL,"version":VERSION,"request_id":request_id,"ok":true,"result":result}),
        Err(error) => json!({"protocol":PROTOCOL,"version":VERSION,"request_id":request_id,"ok":false,"error":error.to_json()}),
    };
    let bytes = serde_json::to_vec(&response).expect("response serializes");
    if bytes.len() > MAX_RESPONSE_BYTES {
        return envelope(request_id, Err(ControlError::new("internal", "response exceeds byte limit", false)));
    }
    bytes
}

// ------------------------------------------------------------- allocator

#[repr(C)]
#[derive(Default)]
struct MallocStatistics {
    blocks_in_use: u32,
    size_in_use: usize,
    max_size_in_use: usize,
    size_allocated: usize,
}

unsafe extern "C" {
    fn malloc_zone_statistics(zone: *mut c_void, stats: *mut MallocStatistics);
}

fn libmalloc_statistics() -> Value {
    let mut stats = MallocStatistics::default();
    // SAFETY: a null zone aggregates every malloc zone; the out-pointer is a
    // valid, exclusively borrowed C-layout struct for the duration of the call.
    unsafe { malloc_zone_statistics(std::ptr::null_mut(), &mut stats) };
    json!({"size_in_use":stats.size_in_use,"size_allocated":stats.size_allocated})
}

// --------------------------------------------------------------- document

struct SemanticNode {
    role: &'static str,
    name: String,
    value: Option<String>,
    dom_id: Option<String>,
}

fn clip(text: &str, limit: usize) -> String {
    text.chars().take(limit).collect()
}

/// Walk body elements in document order and keep the Agent-visible ones with
/// the same role rules the engine hosts apply in-page.
fn semantic_nodes(document: &Document, max_nodes: usize) -> (Vec<SemanticNode>, bool) {
    let mut labels = BTreeMap::new();
    for label in document.select("label[for]").nodes() {
        if let Some(target) = label.attr("for") {
            labels.insert(target.to_string(), label.text().trim().to_owned());
        }
    }
    let mut nodes = Vec::new();
    let mut truncated = false;
    for element in document.select("body *").nodes() {
        let Some(tag) = element.node_name() else { continue };
        let tag = tag.to_ascii_lowercase();
        let input_type = element.attr("type").map(|t| t.to_ascii_lowercase()).unwrap_or_default();
        let role = match tag.as_str() {
            "h1" | "h2" | "h3" | "h4" | "h5" | "h6" => "heading",
            "button" => "button",
            "input" if matches!(input_type.as_str(), "button" | "submit" | "reset") => "button",
            "a" if element.has_attr("href") => "link",
            "input" | "textarea" => "textbox",
            "label" => "label",
            "p" | "li" => "text",
            _ => continue,
        };
        if nodes.len() >= max_nodes {
            truncated = true;
            break;
        }
        let dom_id = element.attr("id").map(|id| clip(&id, 64)).filter(|id| !id.is_empty());
        let (name, value) = if role == "textbox" {
            let name = dom_id
                .as_ref()
                .and_then(|id| labels.get(id).cloned())
                .or_else(|| element.attr("aria-label").map(|v| v.trim().to_owned()))
                .or_else(|| element.attr("name").map(|v| v.trim().to_owned()))
                .unwrap_or_default();
            let value = if tag == "textarea" {
                element.text().to_string()
            } else {
                element.attr("value").map(|v| v.to_string()).unwrap_or_default()
            };
            (name, Some(clip(&value, 256)))
        } else {
            (element.text().trim().to_owned(), None)
        };
        nodes.push(SemanticNode { role, name: clip(&name, 256), value, dom_id });
    }
    (nodes, truncated)
}

// ------------------------------------------------------------------- host

struct Profile {
    id: String,
    name: Option<String>,
}

struct Session {
    id: String,
    profile_id: String,
}

struct Target {
    id: String,
    session_id: String,
    fixture: String,
    fixture_bytes: usize,
    document: Document,
    element_count: usize,
    last_snapshot: Option<(u64, usize)>,
}

struct Host {
    fixture_root: PathBuf,
    profiles: BTreeMap<String, Profile>,
    session: Option<Session>,
    targets: BTreeMap<String, Target>,
    next_profile: u64,
    next_session: u64,
    next_target: u64,
}

impl Host {
    fn target(&self, id: &str) -> Result<&Target, ControlError> {
        self.targets.get(id).ok_or_else(|| not_found("target", id))
    }

    fn execute(&mut self, request: &Request) -> Result<Value, ControlError> {
        let deadline = Instant::now() + request.deadline;
        let a = &request.arguments;
        match request.operation.as_str() {
            "profile.create" => self.profile_create(a),
            "profile.list" => Ok(json!({"kind":"profile_list","profiles":self.profiles.values().map(|p| json!({"profile":p.id,"name":p.name,"persistence":"ephemeral"})).collect::<Vec<_>>()})),
            "profile.inspect" => {
                let object = exact_object(a, &["profile"])?;
                let id = typed_field(object, "profile", "profile")?;
                let profile = self.profiles.get(id).ok_or_else(|| not_found("profile", id))?;
                Ok(json!({"kind":"profile","profile":profile.id,"name":profile.name,"persistence":"ephemeral","sessions":self.session.iter().filter(|s| s.profile_id == profile.id).count()}))
            }
            "profile.delete" => {
                let object = exact_object(a, &["profile"])?;
                let id = typed_field(object, "profile", "profile")?;
                if !self.profiles.contains_key(id) {
                    return Err(not_found("profile", id));
                }
                if self.session.as_ref().is_some_and(|s| s.profile_id == id) {
                    return Err(ControlError::new("conflict", "profile has a live session", true).scoped("profile", id));
                }
                self.profiles.remove(id);
                Ok(json!({"kind":"profile_deleted","profile":id,"persistence":"ephemeral"}))
            }
            "session.open" => self.session_open(a),
            "session.list" => Ok(json!({"kind":"session_list","sessions":self.session.iter().map(|s| json!({"session":s.id,"profile":s.profile_id})).collect::<Vec<_>>()})),
            "session.close" => self.session_close(a),
            "target.open" => self.target_open(a),
            "target.list" => Ok(json!({"kind":"target_list","targets":self.targets.values().map(|t| json!({"target":t.id,"session":t.session_id,"fixture":t.fixture})).collect::<Vec<_>>()})),
            "target.inspect" => {
                let object = exact_object(a, &["target"])?;
                let id = typed_field(object, "target", "target")?;
                let target = self.target(id)?;
                Ok(json!({"kind":"target","target":target.id,"session":target.session_id,"fixture":target.fixture,"revision":0,"load_complete":true,"crashed":false,"script_realm":false}))
            }
            "target.close" => {
                let object = exact_object(a, &["target"])?;
                let id = typed_field(object, "target", "target")?;
                self.targets.remove(id).ok_or_else(|| not_found("target", id))?;
                Ok(json!({"kind":"target_closed","target":id}))
            }
            "target.snapshot" => self.target_snapshot(a),
            "target.act" => self.target_act(a),
            "target.wait" => self.target_wait(a, deadline),
            "memory.report" => Ok(self.memory_report()),
            other => Err(unsupported_operation(other)),
        }
    }

    fn profile_create(&mut self, arguments: &Value) -> Result<Value, ControlError> {
        let object = arguments.as_object().ok_or_else(|| invalid("arguments must be an object"))?;
        let allowed = ["persistence", "name"];
        if !object.contains_key("persistence") || object.keys().any(|k| !allowed.contains(&k.as_str())) {
            return Err(invalid("profile.create accepts persistence and an optional name"));
        }
        match string_field(object, "persistence")? {
            "ephemeral" => {}
            "persistent" => {
                return Err(ControlError::new("unsupported_capability", "the native DOM slice offers ephemeral profiles only", false));
            }
            _ => return Err(invalid("persistence must be ephemeral or persistent")),
        }
        let name = match object.get("name") {
            None => None,
            Some(_) => {
                let name = string_field(object, "name")?;
                if !name.bytes().all(|b| b.is_ascii_alphanumeric() || b == b'-' || b == b'_') || name.len() > 64 {
                    return Err(invalid("name must be a short safe identifier"));
                }
                if self.profiles.values().any(|p| p.name.as_deref() == Some(name)) {
                    return Err(ControlError::new("conflict", "profile name already exists", false));
                }
                Some(name.to_owned())
            }
        };
        if self.profiles.len() >= MAX_PROFILES {
            return Err(ControlError::new("resource_limit", "profile capacity reached", true));
        }
        self.next_profile += 1;
        let id = format!("profile_{}", self.next_profile);
        self.profiles.insert(id.clone(), Profile { id: id.clone(), name: name.clone() });
        Ok(json!({"kind":"profile","profile":id,"name":name,"persistence":"ephemeral","created":true}))
    }

    fn session_open(&mut self, arguments: &Value) -> Result<Value, ControlError> {
        let object = exact_object(arguments, &["profile"])?;
        let profile = typed_field(object, "profile", "profile")?;
        if !self.profiles.contains_key(profile) {
            return Err(not_found("profile", profile));
        }
        if self.session.is_some() {
            return Err(ControlError::new("resource_limit", "this host owns one live session; close it first", true));
        }
        self.next_session += 1;
        let id = format!("session_{}", self.next_session);
        self.session = Some(Session { id: id.clone(), profile_id: profile.to_owned() });
        Ok(json!({"kind":"session","session":id,"profile":profile}))
    }

    fn session_close(&mut self, arguments: &Value) -> Result<Value, ControlError> {
        let object = exact_object(arguments, &["session"])?;
        let id = typed_field(object, "session", "session")?;
        let session = match self.session.take() {
            Some(session) if session.id == id => session,
            other => {
                self.session = other;
                return Err(not_found("session", id));
            }
        };
        let closed = self.targets.len();
        self.targets.clear();
        Ok(json!({"kind":"session_closed","session":session.id,"profile":session.profile_id,"closed_targets":closed}))
    }

    fn target_open(&mut self, arguments: &Value) -> Result<Value, ControlError> {
        let object = exact_object(arguments, &["session", "fixture"])?;
        let session = typed_field(object, "session", "session")?;
        if self.session.as_ref().map(|s| s.id.as_str()) != Some(session) {
            return Err(not_found("session", session));
        }
        let fixture = string_field(object, "fixture")?;
        if !fixture.ends_with(".html")
            || !fixture.bytes().all(|b| b.is_ascii_lowercase() || b.is_ascii_digit() || b == b'-' || b == b'.')
            || fixture.contains("..")
        {
            return Err(invalid("fixture must be a court fixture file name"));
        }
        let path = self.fixture_root.join(fixture);
        let size = std::fs::metadata(&path).map(|m| m.len()).unwrap_or(0);
        if size > MAX_FIXTURE_BYTES {
            return Err(ControlError::new("resource_limit", "fixture exceeds the bounded document size", false));
        }
        let bytes = std::fs::read(&path)
            .map_err(|_| ControlError::new("not_found", "fixture does not exist in the court", false))?;
        if self.targets.len() >= MAX_TARGETS {
            return Err(ControlError::new("resource_limit", "target capacity reached", true));
        }
        let text = String::from_utf8_lossy(&bytes).into_owned();
        let document = Document::from(text.as_str());
        let element_count = document.select("*").nodes().len();
        self.next_target += 1;
        let id = format!("target_{}", self.next_target);
        self.targets.insert(
            id.clone(),
            Target {
                id: id.clone(),
                session_id: session.to_owned(),
                fixture: fixture.to_owned(),
                fixture_bytes: bytes.len(),
                document,
                element_count,
                last_snapshot: None,
            },
        );
        Ok(json!({"kind":"target","target":id,"session":session,"revision":0,"fixture":fixture}))
    }

    fn target_snapshot(&mut self, arguments: &Value) -> Result<Value, ControlError> {
        let object = exact_object(arguments, &["target", "format", "max_bytes", "max_nodes"])?;
        let id = typed_field(object, "target", "target")?.to_owned();
        if string_field(object, "format")? != "semantic" {
            return Err(ControlError::new("unsupported_capability", "only the semantic format is offered", false));
        }
        let max_bytes = bounded_u64(object, "max_bytes", 1, MAX_RESPONSE_BYTES as u64)? as usize;
        let max_nodes = bounded_u64(object, "max_nodes", 1, MAX_SNAPSHOT_NODES)? as usize;
        let target = self.target(&id)?;
        let (semantic, mut truncated) = semantic_nodes(&target.document, max_nodes);
        let mut nodes = Vec::new();
        let mut budget = 0usize;
        for (index, node) in semantic.iter().enumerate() {
            let mut item = json!({
                "reference":{"target":id,"revision":0,"node":format!("node_{}", index + 1)},
                "role":node.role,
                "name":node.name,
            });
            if let Some(value) = &node.value {
                item["value"] = json!(value);
            }
            if let Some(dom_id) = &node.dom_id {
                item["dom_id"] = json!(dom_id);
            }
            budget += serde_json::to_vec(&item).map(|v| v.len()).unwrap_or(0);
            if budget > max_bytes {
                truncated = true;
                break;
            }
            nodes.push(item);
        }
        let count = nodes.len();
        self.targets.get_mut(&id).expect("target exists").last_snapshot = Some((0, count));
        Ok(json!({"kind":"semantic_snapshot","target":id,"revision":0,"truncated":truncated,"nodes":nodes}))
    }

    fn target_act(&mut self, arguments: &Value) -> Result<Value, ControlError> {
        let object = exact_object(arguments, &["target", "reference", "action"])?;
        let id = typed_field(object, "target", "target")?.to_owned();
        let reference = exact_object(object.get("reference").ok_or_else(|| invalid("reference missing"))?, &["target", "revision", "node"])?;
        if typed_field(reference, "target", "target")? != id {
            return Err(invalid("reference target differs"));
        }
        let node = typed_field(reference, "node", "node")?;
        let revision = bounded_u64(reference, "revision", 0, u64::MAX)?;
        let action = object.get("action").and_then(Value::as_object).ok_or_else(|| invalid("action missing"))?;
        if action.len() != 1 {
            return Err(invalid("click action fields differ"));
        }
        if string_field(action, "kind")? != "click" {
            return Err(ControlError::new("unsupported_capability", "the native DOM slice offers no actions", false));
        }
        let target = self.target(&id)?;
        if revision != 0 {
            return Err(ControlError::new("stale_revision", "node reference revision no longer matches the target", true)
                .scoped("target", &id)
                .details(json!({"reference_revision":revision,"current_revision":0})));
        }
        let index = node.strip_prefix("node_").and_then(|s| s.parse::<usize>().ok()).filter(|n| *n >= 1);
        if !index.is_some_and(|n| target.last_snapshot.is_some_and(|(_, count)| n <= count)) {
            return Err(ControlError::new("not_found", "node does not exist", false).scoped("target", &id));
        }
        Err(ControlError::new(
            "unsupported_capability",
            "the native DOM slice has no script realm or event dispatch; click cannot mutate the document",
            false,
        )
        .scoped("target", &id)
        .details(json!({"slice":"html-dom","missing":["script realm","event dispatch","layout"]})))
    }

    fn target_wait(&mut self, arguments: &Value, deadline: Instant) -> Result<Value, ControlError> {
        let object = exact_object(arguments, &["target", "condition"])?;
        let id = typed_field(object, "target", "target")?.to_owned();
        let condition = exact_object(object.get("condition").ok_or_else(|| invalid("condition missing"))?, &["kind", "revision"])?;
        if string_field(condition, "kind")? != "revision_at_least" {
            return Err(ControlError::new("unsupported_capability", "this host offers revision_at_least only", false));
        }
        let expected = bounded_u64(condition, "revision", 0, u64::MAX)?;
        self.target(&id)?;
        if expected == 0 {
            return Ok(json!({"kind":"wait","target":id,"revision":0,"matched":true}));
        }
        // The document never mutates, so an unmet condition is only a deadline.
        let remaining = deadline.saturating_duration_since(Instant::now());
        std::thread::sleep(remaining.min(Duration::from_millis(MAX_DEADLINE_MS)));
        Err(ControlError::new("deadline_exceeded", "condition was not met before deadline", true).scoped("target", &id))
    }

    fn memory_report(&self) -> Value {
        let fixture_bytes: usize = self.targets.values().map(|t| t.fixture_bytes).sum();
        let elements: usize = self.targets.values().map(|t| t.element_count).sum();
        json!({
            "kind":"memory_report",
            "semantic":"native-dom-logical-owners-plus-libmalloc-statistics",
            "owners":{
                "profiles":{"objects":self.profiles.len(),"object_limit":MAX_PROFILES},
                "sessions":{"objects":self.session.iter().count(),"object_limit":1},
                "targets":{"objects":self.targets.len(),"object_limit":MAX_TARGETS,"fixture_bytes":fixture_bytes,"elements":elements},
            },
            "libmalloc":libmalloc_statistics(),
            "limitations":["logical owners are document sizes, not heap bytes","no script, layout, image or network owners exist in this slice","not process RSS/private/PSS"],
        })
    }
}

// ------------------------------------------------------------------- serve

enum Line {
    Eof,
    Oversized,
    Bytes(Vec<u8>),
}

fn read_bounded_line(reader: &mut impl BufRead) -> io::Result<Line> {
    let mut output = Vec::new();
    let mut oversized = false;
    loop {
        let available = reader.fill_buf()?;
        if available.is_empty() {
            if output.is_empty() && !oversized {
                return Ok(Line::Eof);
            }
            return Ok(if oversized { Line::Oversized } else { Line::Bytes(output) });
        }
        let line_end = available.iter().position(|byte| *byte == b'\n');
        let consumed = line_end.map_or(available.len(), |index| index + 1);
        let content = &available[..line_end.unwrap_or(available.len())];
        if !oversized {
            if output.len() + content.len() > MAX_REQUEST_BYTES {
                oversized = true;
                output.clear();
            } else {
                output.extend_from_slice(content);
            }
        }
        reader.consume(consumed);
        if line_end.is_some() {
            return Ok(if oversized { Line::Oversized } else { Line::Bytes(output) });
        }
    }
}

fn usage() -> ! {
    eprintln!("usage: native-dom-control serve --stdio --fixture-root DIR --config-dir DIR");
    std::process::exit(64);
}

fn main() -> Result<(), Box<dyn Error>> {
    let arguments: Vec<String> = std::env::args().skip(1).collect();
    if arguments.len() != 6
        || arguments[0] != "serve"
        || arguments[1] != "--stdio"
        || arguments[2] != "--fixture-root"
        || arguments[4] != "--config-dir"
    {
        usage();
    }
    let fixture_root = PathBuf::from(&arguments[3]);
    if !fixture_root.is_dir() {
        usage();
    }
    let mut host = Host {
        fixture_root,
        profiles: BTreeMap::new(),
        session: None,
        targets: BTreeMap::new(),
        next_profile: 0,
        next_session: 0,
        next_target: 0,
    };
    let stdin = io::stdin();
    let mut reader = stdin.lock();
    let stdout = io::stdout();
    let mut out = stdout.lock();
    loop {
        let response = match read_bounded_line(&mut reader)? {
            Line::Eof => break,
            Line::Oversized => envelope("req_invalid", Err(invalid("request exceeds byte limit"))),
            Line::Bytes(bytes) if bytes.is_empty() => envelope("req_invalid", Err(invalid("request is empty"))),
            Line::Bytes(bytes) => match parse_request(&bytes) {
                Ok(request) => {
                    let body = host.execute(&request);
                    envelope(&request.request_id, body)
                }
                Err((request_id, error)) => envelope(&request_id, Err(error)),
            },
        };
        out.write_all(&response)?;
        out.write_all(b"\n")?;
        out.flush()?;
    }
    Ok(())
}
