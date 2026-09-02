#![allow(clippy::result_large_err)]

//! Servo-backed host for the MiniCon Surf control 0.0.1 vocabulary.
//!
//! One long-lived Servo instance answers bounded NDJSON requests on stdio:
//! ephemeral profile, one session, hermetic fixture targets, semantic
//! snapshots with revision-scoped node references, click actions, revision
//! waits, and a memory report. Every other reserved operation returns a typed
//! `unsupported_operation` failure rather than falling through to the engine.

use std::cell::{Cell, RefCell};
use std::collections::BTreeMap;
use std::error::Error;
use std::ffi::{CStr, c_void};
use std::io::{self, BufRead, Write};
use std::path::PathBuf;
use std::rc::Rc;
use std::sync::mpsc;
use std::time::{Duration, Instant};

mod cdp_edge;

use percent_encoding::{NON_ALPHANUMERIC, percent_encode};
use profile_traits::mem::{MemoryReportResult, ReportKind};
use serde_json::{Map, Value, json};
use servo::{
    JSValue, LoadStatus, Opts, RenderingContext, ServoBuilder, SoftwareRenderingContext, WebView,
    WebViewBuilder, WebViewDelegate,
};
use servo_base::generic_channel::{GenericCallback, TryReceiveError};
use url::Url;

const PROTOCOL: &str = "minicon-surf.control";
const VERSION: &str = "0.0.1";
const MAX_REQUEST_BYTES: usize = 65_536;
const MAX_RESPONSE_BYTES: usize = 4_194_304;
const MAX_DEADLINE_MS: u64 = 120_000;
const MAX_TARGETS: usize = 8;
const MAX_PROFILES: usize = 8;
const MAX_SNAPSHOT_NODES: u64 = 128;
const VIEWPORT_WIDTH: u32 = 800;
const VIEWPORT_HEIGHT: u32 = 600;
const OPERATIONS: &[&str] = &[
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
];

const INSTALL_JS: &str = r#"(() => {
  if (!window.__mcs) {
    const s = { revision: 0, snapshot: -1, nodes: [] };
    new MutationObserver(() => { s.revision += 1; }).observe(
      document.documentElement,
      { childList: true, subtree: true, characterData: true, attributes: true });
    window.__mcs = s;
  }
  return String(window.__mcs.revision);
})()"#;

const REVISION_JS: &str = "(() => String(window.__mcs ? window.__mcs.revision : -1))()";

fn snapshot_script(max_nodes: u64) -> String {
    format!(
        r#"(() => {{
  const s = window.__mcs;
  if (!s) return JSON.stringify({{ error: "uninstrumented" }});
  const role = (el) => {{
    const t = el.tagName.toLowerCase();
    if (/^h[1-6]$/.test(t)) return "heading";
    if (t === "button" || (t === "input" && /^(button|submit|reset)$/.test(el.type))) return "button";
    if (t === "a" && el.hasAttribute("href")) return "link";
    if (t === "input" || t === "textarea") return "textbox";
    if (t === "label") return "label";
    if (t === "p" || t === "li") return "text";
    return null;
  }};
  const out = [];
  const nodes = [];
  let truncated = false;
  const all = document.body ? document.body.querySelectorAll("*") : [];
  for (const el of all) {{
    const r = role(el);
    if (!r) continue;
    if (out.length >= {max_nodes}) {{ truncated = true; break; }}
    let name = (el.textContent || "").trim();
    const entry = {{ node: "node_" + (nodes.length + 1), role: r }};
    if (r === "textbox") {{
      const label = el.id ? document.querySelector('label[for="' + el.id + '"]') : null;
      name = (label ? label.textContent : (el.getAttribute("aria-label") || el.name || "")).trim();
      entry.value = String(el.value || "").slice(0, 256);
    }}
    entry.name = name.slice(0, 256);
    if (el.id) entry.dom_id = String(el.id).slice(0, 64);
    nodes.push(el);
    out.push(entry);
  }}
  s.nodes = nodes;
  s.snapshot = s.revision;
  return JSON.stringify({{ revision: s.revision, truncated, nodes: out }});
}})()"#
    )
}

fn act_script(revision: u64, index: usize) -> String {
    format!(
        r#"(() => {{
  const s = window.__mcs;
  if (!s) return JSON.stringify({{ error: "uninstrumented" }});
  if (s.revision !== {revision}) return JSON.stringify({{ stale: true, current: s.revision }});
  if (s.snapshot !== {revision}) return JSON.stringify({{ missing: true }});
  const el = s.nodes[{index}];
  if (!el || !el.isConnected) return JSON.stringify({{ missing: true }});
  const t = el.tagName.toLowerCase();
  if (!(t === "button" || (t === "input" && /^(button|submit|reset)$/.test(el.type)))) {{
    return JSON.stringify({{ unsupported: true }});
  }}
  el.click();
  return JSON.stringify({{ applied: true }});
}})()"#
    )
}

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
        ControlError {
            code,
            message,
            retryable,
            scope: None,
            details: None,
        }
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
        format!("{operation} is reserved by control 0.0.1 but not offered by this Servo host"),
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
        && bytes[0].is_ascii_lowercase() | bytes[0].is_ascii_digit()
        && bytes
            .iter()
            .all(|b| b.is_ascii_lowercase() || b.is_ascii_digit() || *b == b'_' || *b == b'-')
}

fn parse_request(bytes: &[u8]) -> Result<Request, (String, ControlError)> {
    let value: Value = serde_json::from_slice(bytes).map_err(|_| {
        (
            "req_invalid".to_owned(),
            invalid("request is not valid JSON"),
        )
    })?;
    let object = value.as_object().ok_or_else(|| {
        (
            "req_invalid".to_owned(),
            invalid("request is not an object"),
        )
    })?;
    let request_id = object
        .get("request_id")
        .and_then(Value::as_str)
        .filter(|id| valid_id("req_", id))
        .map(str::to_owned)
        .ok_or_else(|| {
            (
                "req_invalid".to_owned(),
                invalid("request_id is missing or malformed"),
            )
        })?;
    let fail = |message: &str| (request_id.clone(), invalid(message));
    let expected = [
        "protocol",
        "version",
        "request_id",
        "deadline_ms",
        "operation",
        "arguments",
    ];
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
    Ok(Request {
        request_id,
        deadline: Duration::from_millis(deadline_ms),
        operation,
        arguments,
    })
}

fn exact_object<'a>(
    value: &'a Value,
    keys: &[&str],
) -> Result<&'a Map<String, Value>, ControlError> {
    let object = value
        .as_object()
        .ok_or_else(|| invalid("arguments must be an object"))?;
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

fn typed_field<'a>(
    object: &'a Map<String, Value>,
    key: &str,
    prefix: &str,
) -> Result<&'a str, ControlError> {
    let value = string_field(object, key)?;
    if !valid_id(&format!("{prefix}_"), value) {
        return Err(invalid(&format!("{key} is not a {prefix} identifier")));
    }
    Ok(value)
}

fn bounded_u64(
    object: &Map<String, Value>,
    key: &str,
    min: u64,
    max: u64,
) -> Result<u64, ControlError> {
    object
        .get(key)
        .and_then(Value::as_u64)
        .filter(|v| (min..=max).contains(v))
        .ok_or_else(|| invalid(&format!("{key} must be an integer in {min}..={max}")))
}

fn envelope(request_id: &str, body: Result<Value, ControlError>) -> Vec<u8> {
    let response = match body {
        Ok(result) => {
            json!({"protocol":PROTOCOL,"version":VERSION,"request_id":request_id,"ok":true,"result":result})
        }
        Err(error) => {
            json!({"protocol":PROTOCOL,"version":VERSION,"request_id":request_id,"ok":false,"error":error.to_json()})
        }
    };
    let bytes = serde_json::to_vec(&response).expect("response serializes");
    if bytes.len() > MAX_RESPONSE_BYTES {
        return envelope(
            request_id,
            Err(ControlError::new(
                "internal",
                "response exceeds byte limit",
                false,
            )),
        );
    }
    bytes
}

// ------------------------------------------------------------- allocators

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

fn jemalloc_stat(name: &CStr) -> Option<usize> {
    let mut epoch: u64 = 1;
    let mut epoch_len = size_of::<u64>();
    // SAFETY: `epoch` refreshes cached statistics; pointers reference live locals.
    let code = unsafe {
        tikv_jemalloc_sys::mallctl(
            c"epoch".as_ptr(),
            (&raw mut epoch).cast(),
            &mut epoch_len,
            (&raw mut epoch).cast(),
            epoch_len,
        )
    };
    if code != 0 {
        return None;
    }
    let mut value: usize = 0;
    let mut value_len = size_of::<usize>();
    // SAFETY: read-only mallctl of a size_t statistic into a live local.
    let code = unsafe {
        tikv_jemalloc_sys::mallctl(
            name.as_ptr(),
            (&raw mut value).cast(),
            &mut value_len,
            std::ptr::null_mut(),
            0,
        )
    };
    (code == 0).then_some(value)
}

// ------------------------------------------------------------------ engine

#[derive(Default)]
struct Delegate {
    loaded: Cell<bool>,
    crashed: RefCell<Option<String>>,
}

impl WebViewDelegate for Delegate {
    fn notify_load_status_changed(&self, _webview: WebView, status: LoadStatus) {
        if status == LoadStatus::Complete {
            self.loaded.set(true);
        }
    }

    fn notify_new_frame_ready(&self, webview: WebView) {
        webview.paint();
    }

    fn notify_crashed(&self, _webview: WebView, reason: String, _backtrace: Option<String>) {
        self.crashed.replace(Some(reason));
    }
}

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
    webview: WebView,
    delegate: Rc<Delegate>,
    last_snapshot: Option<(u64, usize)>,
}

struct Host {
    servo: servo::Servo,
    context: Rc<SoftwareRenderingContext>,
    fixture_root: PathBuf,
    profiles: BTreeMap<String, Profile>,
    session: Option<Session>,
    targets: BTreeMap<String, Target>,
    next_profile: u64,
    next_session: u64,
    next_target: u64,
}

impl Host {
    fn spin(&self) {
        self.servo.spin_event_loop();
    }

    fn evaluate(
        &self,
        webview: &WebView,
        script: &str,
        deadline: Instant,
        target_id: &str,
    ) -> Result<JSValue, ControlError> {
        let slot = Rc::new(RefCell::new(None));
        let sink = slot.clone();
        webview.evaluate_javascript(script, move |result| {
            sink.replace(Some(result));
        });
        loop {
            self.spin();
            if let Some(result) = slot.borrow_mut().take() {
                return result.map_err(|error| {
                    ControlError::new("internal", "JavaScript evaluation failed", false)
                        .scoped("target", target_id)
                        .details(json!({"engine_error":format!("{error:?}").chars().take(256).collect::<String>()}))
                });
            }
            if Instant::now() >= deadline {
                return Err(ControlError::new(
                    "deadline_exceeded",
                    "engine did not answer before deadline",
                    true,
                )
                .scoped("target", target_id));
            }
            std::thread::sleep(Duration::from_millis(1));
        }
    }

    fn evaluate_json(
        &self,
        webview: &WebView,
        script: &str,
        deadline: Instant,
        target_id: &str,
    ) -> Result<Value, ControlError> {
        match self.evaluate(webview, script, deadline, target_id)? {
            JSValue::String(text) => serde_json::from_str(&text).map_err(|_| {
                ControlError::new("internal", "engine returned malformed snapshot JSON", false)
                    .scoped("target", target_id)
            }),
            other => Err(ControlError::new(
                "internal",
                "engine returned a non-string value",
                false,
            )
            .scoped("target", target_id)
            .details(json!({"value":format!("{other:?}").chars().take(128).collect::<String>()}))),
        }
    }

    fn revision(
        &self,
        webview: &WebView,
        deadline: Instant,
        target_id: &str,
    ) -> Result<u64, ControlError> {
        match self.evaluate(webview, REVISION_JS, deadline, target_id)? {
            JSValue::String(text) => text
                .parse::<i64>()
                .ok()
                .filter(|r| *r >= 0)
                .map(|r| r as u64)
                .ok_or_else(|| {
                    ControlError::new(
                        "internal",
                        "target lost its revision instrumentation",
                        false,
                    )
                    .scoped("target", target_id)
                }),
            _ => Err(
                ControlError::new("internal", "revision query returned a non-string", false)
                    .scoped("target", target_id),
            ),
        }
    }

    fn target(&self, id: &str) -> Result<&Target, ControlError> {
        self.targets.get(id).ok_or_else(|| not_found("target", id))
    }

    fn execute(&mut self, request: &Request) -> Result<Value, ControlError> {
        let deadline = Instant::now() + request.deadline;
        let a = &request.arguments;
        match request.operation.as_str() {
            "profile.create" => self.profile_create(a),
            "profile.list" => Ok(
                json!({"kind":"profile_list","profiles":self.profiles.values().map(|p| json!({"profile":p.id,"name":p.name,"persistence":"ephemeral"})).collect::<Vec<_>>()}),
            ),
            "profile.inspect" => {
                let object = exact_object(a, &["profile"])?;
                let id = typed_field(object, "profile", "profile")?;
                let profile = self
                    .profiles
                    .get(id)
                    .ok_or_else(|| not_found("profile", id))?;
                Ok(
                    json!({"kind":"profile","profile":profile.id,"name":profile.name,"persistence":"ephemeral","sessions":self.session.iter().filter(|s| s.profile_id == profile.id).count()}),
                )
            }
            "profile.delete" => {
                let object = exact_object(a, &["profile"])?;
                let id = typed_field(object, "profile", "profile")?;
                if !self.profiles.contains_key(id) {
                    return Err(not_found("profile", id));
                }
                if self.session.as_ref().is_some_and(|s| s.profile_id == id) {
                    return Err(
                        ControlError::new("conflict", "profile has a live session", true)
                            .scoped("profile", id),
                    );
                }
                self.profiles.remove(id);
                Ok(json!({"kind":"profile_deleted","profile":id,"persistence":"ephemeral"}))
            }
            "session.open" => self.session_open(a),
            "session.list" => Ok(
                json!({"kind":"session_list","sessions":self.session.iter().map(|s| json!({"session":s.id,"profile":s.profile_id})).collect::<Vec<_>>()}),
            ),
            "session.close" => self.session_close(a),
            "target.open" => self.target_open(a, deadline),
            "target.list" => Ok(
                json!({"kind":"target_list","targets":self.targets.values().map(|t| json!({"target":t.id,"session":t.session_id,"fixture":t.fixture})).collect::<Vec<_>>()}),
            ),
            "target.inspect" => {
                let object = exact_object(a, &["target"])?;
                let id = typed_field(object, "target", "target")?;
                let target = self.target(id)?;
                let revision = self.revision(&target.webview, deadline, id)?;
                Ok(
                    json!({"kind":"target","target":target.id,"session":target.session_id,"fixture":target.fixture,"revision":revision,"load_complete":target.delegate.loaded.get(),"crashed":target.delegate.crashed.borrow().is_some()}),
                )
            }
            "target.close" => {
                let object = exact_object(a, &["target"])?;
                let id = typed_field(object, "target", "target")?;
                let target = self
                    .targets
                    .remove(id)
                    .ok_or_else(|| not_found("target", id))?;
                drop(target);
                self.spin();
                Ok(json!({"kind":"target_closed","target":id}))
            }
            "target.snapshot" => self.target_snapshot(a, deadline),
            "target.act" => self.target_act(a, deadline),
            "target.wait" => self.target_wait(a, deadline),
            "memory.report" => self.memory_report(deadline),
            other => Err(unsupported_operation(other)),
        }
    }

    fn profile_create(&mut self, arguments: &Value) -> Result<Value, ControlError> {
        let object = arguments
            .as_object()
            .ok_or_else(|| invalid("arguments must be an object"))?;
        let allowed = ["persistence", "name"];
        if !object.contains_key("persistence")
            || object.keys().any(|k| !allowed.contains(&k.as_str()))
        {
            return Err(invalid(
                "profile.create accepts persistence and an optional name",
            ));
        }
        match string_field(object, "persistence")? {
            "ephemeral" => {}
            "persistent" => {
                return Err(ControlError::new(
                    "unsupported_capability",
                    "this Servo host offers ephemeral profiles only",
                    false,
                ));
            }
            _ => return Err(invalid("persistence must be ephemeral or persistent")),
        }
        let name = match object.get("name") {
            None => None,
            Some(_) => {
                let name = string_field(object, "name")?;
                if !name
                    .bytes()
                    .all(|b| b.is_ascii_alphanumeric() || b == b'-' || b == b'_')
                    || name.len() > 64
                {
                    return Err(invalid("name must be a short safe identifier"));
                }
                if self
                    .profiles
                    .values()
                    .any(|p| p.name.as_deref() == Some(name))
                {
                    return Err(ControlError::new(
                        "conflict",
                        "profile name already exists",
                        false,
                    ));
                }
                Some(name.to_owned())
            }
        };
        if self.profiles.len() >= MAX_PROFILES {
            return Err(ControlError::new(
                "resource_limit",
                "profile capacity reached",
                true,
            ));
        }
        self.next_profile += 1;
        let id = format!("profile_{}", self.next_profile);
        self.profiles.insert(
            id.clone(),
            Profile {
                id: id.clone(),
                name: name.clone(),
            },
        );
        Ok(
            json!({"kind":"profile","profile":id,"name":name,"persistence":"ephemeral","created":true}),
        )
    }

    fn session_open(&mut self, arguments: &Value) -> Result<Value, ControlError> {
        let object = exact_object(arguments, &["profile"])?;
        let profile = typed_field(object, "profile", "profile")?;
        if !self.profiles.contains_key(profile) {
            return Err(not_found("profile", profile));
        }
        if self.session.is_some() {
            return Err(ControlError::new(
                "resource_limit",
                "this Servo host owns one live session; close it first",
                true,
            ));
        }
        self.next_session += 1;
        let id = format!("session_{}", self.next_session);
        self.session = Some(Session {
            id: id.clone(),
            profile_id: profile.to_owned(),
        });
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
        let closed: Vec<String> = self.targets.keys().cloned().collect();
        self.targets.clear();
        self.spin();
        Ok(
            json!({"kind":"session_closed","session":session.id,"profile":session.profile_id,"closed_targets":closed.len()}),
        )
    }

    fn target_open(&mut self, arguments: &Value, deadline: Instant) -> Result<Value, ControlError> {
        let object = exact_object(arguments, &["session", "fixture"])?;
        let session = typed_field(object, "session", "session")?;
        if self.session.as_ref().map(|s| s.id.as_str()) != Some(session) {
            return Err(not_found("session", session));
        }
        let fixture = string_field(object, "fixture")?;
        if !fixture.ends_with(".html")
            || !fixture
                .bytes()
                .all(|b| b.is_ascii_lowercase() || b.is_ascii_digit() || b == b'-' || b == b'.')
            || fixture.contains("..")
        {
            return Err(invalid("fixture must be a court fixture file name"));
        }
        let bytes = std::fs::read(self.fixture_root.join(fixture)).map_err(|_| {
            ControlError::new("not_found", "fixture does not exist in the court", false)
        })?;
        if self.targets.len() >= MAX_TARGETS {
            return Err(ControlError::new(
                "resource_limit",
                "target capacity reached",
                true,
            ));
        }
        let url = Url::parse(&format!(
            "data:text/html,{}",
            percent_encode(&bytes, NON_ALPHANUMERIC)
        ))
        .map_err(|_| ControlError::new("internal", "fixture could not be encoded", false))?;
        self.next_target += 1;
        let id = format!("target_{}", self.next_target);
        let delegate = Rc::new(Delegate::default());
        let webview = WebViewBuilder::new(&self.servo, self.context.clone())
            .delegate(delegate.clone())
            .url(url)
            .build();
        loop {
            self.spin();
            if delegate.loaded.get() {
                break;
            }
            if let Some(reason) = delegate.crashed.borrow_mut().take() {
                return Err(
                    ControlError::new("target_crashed", reason, false).scoped("target", &id)
                );
            }
            if Instant::now() >= deadline {
                return Err(ControlError::new(
                    "deadline_exceeded",
                    "fixture did not finish loading before deadline",
                    true,
                )
                .scoped("target", &id));
            }
            std::thread::sleep(Duration::from_millis(1));
        }
        let revision = match self.evaluate(&webview, INSTALL_JS, deadline, &id)? {
            JSValue::String(text) => text.parse::<u64>().unwrap_or(0),
            _ => 0,
        };
        self.targets.insert(
            id.clone(),
            Target {
                id: id.clone(),
                session_id: session.to_owned(),
                fixture: fixture.to_owned(),
                webview,
                delegate,
                last_snapshot: None,
            },
        );
        Ok(
            json!({"kind":"target","target":id,"session":session,"revision":revision,"fixture":fixture}),
        )
    }

    fn target_snapshot(
        &mut self,
        arguments: &Value,
        deadline: Instant,
    ) -> Result<Value, ControlError> {
        let object = exact_object(arguments, &["target", "format", "max_bytes", "max_nodes"])?;
        let id = typed_field(object, "target", "target")?.to_owned();
        if string_field(object, "format")? != "semantic" {
            return Err(ControlError::new(
                "unsupported_capability",
                "only the semantic format is offered",
                false,
            ));
        }
        let max_bytes = bounded_u64(object, "max_bytes", 1, MAX_RESPONSE_BYTES as u64)? as usize;
        let max_nodes = bounded_u64(object, "max_nodes", 1, MAX_SNAPSHOT_NODES)?;
        let target = self.target(&id)?;
        let raw =
            self.evaluate_json(&target.webview, &snapshot_script(max_nodes), deadline, &id)?;
        if raw.get("error").is_some() {
            return Err(ControlError::new(
                "internal",
                "target lost its revision instrumentation",
                false,
            )
            .scoped("target", &id));
        }
        let revision = raw.get("revision").and_then(Value::as_u64).ok_or_else(|| {
            ControlError::new("internal", "snapshot lacks a revision", false).scoped("target", &id)
        })?;
        let mut truncated = raw
            .get("truncated")
            .and_then(Value::as_bool)
            .unwrap_or(false);
        let mut nodes = Vec::new();
        let mut budget = 0usize;
        for entry in raw
            .get("nodes")
            .and_then(Value::as_array)
            .cloned()
            .unwrap_or_default()
        {
            let node = entry
                .get("node")
                .and_then(Value::as_str)
                .unwrap_or("node_0")
                .to_owned();
            let mut item = json!({
                "reference":{"target":id,"revision":revision,"node":node},
                "role":entry.get("role").cloned().unwrap_or(Value::Null),
                "name":entry.get("name").cloned().unwrap_or(Value::Null),
            });
            if let Some(value) = entry.get("value") {
                item["value"] = value.clone();
            }
            if let Some(dom_id) = entry.get("dom_id") {
                item["dom_id"] = dom_id.clone();
            }
            budget += serde_json::to_vec(&item).map(|v| v.len()).unwrap_or(0);
            if budget > max_bytes {
                truncated = true;
                break;
            }
            nodes.push(item);
        }
        let count = nodes.len();
        self.targets
            .get_mut(&id)
            .expect("target exists")
            .last_snapshot = Some((revision, count));
        Ok(
            json!({"kind":"semantic_snapshot","target":id,"revision":revision,"truncated":truncated,"nodes":nodes}),
        )
    }

    fn target_act(&mut self, arguments: &Value, deadline: Instant) -> Result<Value, ControlError> {
        let object = exact_object(arguments, &["target", "reference", "action"])?;
        let id = typed_field(object, "target", "target")?.to_owned();
        let reference = exact_object(
            object
                .get("reference")
                .ok_or_else(|| invalid("reference missing"))?,
            &["target", "revision", "node"],
        )?;
        if typed_field(reference, "target", "target")? != id {
            return Err(invalid("reference target differs"));
        }
        let node = typed_field(reference, "node", "node")?;
        let revision = bounded_u64(reference, "revision", 0, u64::MAX)?;
        let action = object
            .get("action")
            .and_then(Value::as_object)
            .ok_or_else(|| invalid("action missing"))?;
        if action.len() != 1 {
            return Err(invalid("click action fields differ"));
        }
        if string_field(action, "kind")? != "click" {
            return Err(ControlError::new(
                "unsupported_capability",
                "this Servo host offers click only",
                false,
            ));
        }
        let index = node
            .strip_prefix("node_")
            .and_then(|s| s.parse::<usize>().ok())
            .filter(|n| *n >= 1)
            .ok_or_else(|| {
                ControlError::new("not_found", "node does not exist", false).scoped("target", &id)
            })?
            - 1;
        let target = self.target(&id)?;
        let current = self.revision(&target.webview, deadline, &id)?;
        if current != revision {
            return Err(ControlError::new(
                "stale_revision",
                "node reference revision no longer matches the target",
                true,
            )
            .scoped("target", &id)
            .details(json!({"reference_revision":revision,"current_revision":current})));
        }
        if !target
            .last_snapshot
            .is_some_and(|(rev, count)| rev == revision && index < count)
        {
            return Err(
                ControlError::new("not_found", "node does not exist", false).scoped("target", &id)
            );
        }
        let outcome =
            self.evaluate_json(&target.webview, &act_script(revision, index), deadline, &id)?;
        if let Some(current) = outcome.get("current").and_then(Value::as_u64) {
            return Err(ControlError::new(
                "stale_revision",
                "node reference revision no longer matches the target",
                true,
            )
            .scoped("target", &id)
            .details(json!({"reference_revision":revision,"current_revision":current})));
        }
        if outcome.get("missing").is_some() {
            return Err(
                ControlError::new("not_found", "node does not exist", false).scoped("target", &id)
            );
        }
        if outcome.get("unsupported").is_some() {
            return Err(ControlError::new(
                "unsupported_capability",
                "click requires a button node",
                false,
            ));
        }
        if outcome.get("applied").and_then(Value::as_bool) != Some(true) {
            return Err(
                ControlError::new("internal", "engine did not confirm the action", false)
                    .scoped("target", &id),
            );
        }
        // Mutation observers run after the click task; a fresh task reads the settled revision.
        self.spin();
        let after = self.revision(&target.webview, deadline, &id)?;
        Ok(json!({"kind":"action","target":id,"revision":after,"applied":true}))
    }

    fn target_wait(&mut self, arguments: &Value, deadline: Instant) -> Result<Value, ControlError> {
        let object = exact_object(arguments, &["target", "condition"])?;
        let id = typed_field(object, "target", "target")?.to_owned();
        let condition = exact_object(
            object
                .get("condition")
                .ok_or_else(|| invalid("condition missing"))?,
            &["kind", "revision"],
        )?;
        if string_field(condition, "kind")? != "revision_at_least" {
            return Err(ControlError::new(
                "unsupported_capability",
                "this Servo host offers revision_at_least only",
                false,
            ));
        }
        let expected = bounded_u64(condition, "revision", 0, u64::MAX)?;
        loop {
            let target = self.target(&id)?;
            let revision = self.revision(&target.webview, deadline, &id)?;
            if revision >= expected {
                return Ok(json!({"kind":"wait","target":id,"revision":revision,"matched":true}));
            }
            if Instant::now() >= deadline {
                return Err(ControlError::new(
                    "deadline_exceeded",
                    "condition was not met before deadline",
                    true,
                )
                .scoped("target", &id));
            }
            for _ in 0..5 {
                self.spin();
                std::thread::sleep(Duration::from_millis(1));
            }
        }
    }

    fn memory_report(&self, deadline: Instant) -> Result<Value, ControlError> {
        let (callback, receiver) = GenericCallback::<MemoryReportResult>::new_blocking()
            .map_err(|_| ControlError::new("internal", "memory report channel failed", false))?;
        self.servo.create_memory_report(callback);
        let result = loop {
            self.spin();
            match receiver.try_recv() {
                Ok(result) => break result,
                Err(TryReceiveError::Empty) => {}
                Err(TryReceiveError::ReceiveError(_)) => {
                    return Err(ControlError::new(
                        "internal",
                        "memory report callback disconnected",
                        false,
                    ));
                }
            }
            if Instant::now() >= deadline {
                return Err(ControlError::new(
                    "deadline_exceeded",
                    "memory report did not arrive before deadline",
                    true,
                ));
            }
            std::thread::sleep(Duration::from_millis(1));
        };
        let mut explicit = 0usize;
        let mut resident = None;
        for process in &result.results {
            for report in &process.reports {
                match report.kind {
                    ReportKind::NonExplicitSize => {
                        if report.path.first().map(String::as_str) == Some("resident") {
                            resident = Some(report.size);
                        }
                    }
                    _ => explicit += report.size,
                }
            }
        }
        Ok(json!({
            "kind":"memory_report",
            "semantic":"servo-explicit-owner-bytes-plus-allocator-statistics",
            "owners":{
                "profiles":{"objects":self.profiles.len(),"object_limit":MAX_PROFILES},
                "sessions":{"objects":self.session.iter().count(),"object_limit":1},
                "targets":{"objects":self.targets.len(),"object_limit":MAX_TARGETS},
            },
            "engine":{"explicit_reported_bytes":explicit,"reported_resident_bytes":resident},
            "jemalloc":{"allocated":jemalloc_stat(c"stats.allocated"),"resident":jemalloc_stat(c"stats.resident"),"mapped":jemalloc_stat(c"stats.mapped")},
            "libmalloc":libmalloc_statistics(),
            "limitations":["explicit bytes cover Servo's reporters only","platform GL driver state is outside both allocators","not process RSS/private/PSS"],
        }))
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
            return Ok(if oversized {
                Line::Oversized
            } else {
                Line::Bytes(output)
            });
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
            return Ok(if oversized {
                Line::Oversized
            } else {
                Line::Bytes(output)
            });
        }
    }
}

fn usage() -> ! {
    eprintln!(
        "usage: servo-control serve --stdio --fixture-root DIR --config-dir DIR [--cdp-port PORT --ready-file PATH]"
    );
    std::process::exit(64);
}

fn main() -> Result<(), Box<dyn Error>> {
    let arguments: Vec<String> = std::env::args().skip(1).collect();
    if arguments.len() < 6
        || arguments[0] != "serve"
        || arguments[1] != "--stdio"
        || arguments[2] != "--fixture-root"
        || arguments[4] != "--config-dir"
        || !(arguments.len() - 6).is_multiple_of(2)
    {
        usage();
    }
    let fixture_root = PathBuf::from(&arguments[3]);
    let config_dir = PathBuf::from(&arguments[5]);
    let mut cdp_port = None;
    let mut ready_file = None;
    for pair in arguments[6..].chunks_exact(2) {
        match pair[0].as_str() {
            "--cdp-port" if cdp_port.is_none() => {
                cdp_port = Some(pair[1].parse::<u16>().unwrap_or_else(|_| usage()));
            }
            "--ready-file" if ready_file.is_none() => ready_file = Some(PathBuf::from(&pair[1])),
            _ => usage(),
        }
    }
    if cdp_port.is_some() != ready_file.is_some() || !fixture_root.is_dir() {
        usage();
    }
    rustls::crypto::aws_lc_rs::default_provider()
        .install_default()
        .map_err(|_| "failed to install rustls crypto provider")?;
    let context = Rc::new(
        SoftwareRenderingContext::new(dpi::PhysicalSize {
            width: VIEWPORT_WIDTH,
            height: VIEWPORT_HEIGHT,
        })
        .map_err(|error| format!("failed to create rendering context: {error:?}"))?,
    );
    context
        .make_current()
        .map_err(|error| format!("failed to make rendering context current: {error:?}"))?;
    let servo = ServoBuilder::default()
        .opts(Opts {
            config_dir: Some(config_dir),
            temporary_storage: true,
            ..Opts::default()
        })
        .build();
    let mut host = Host {
        servo,
        context,
        fixture_root,
        profiles: BTreeMap::new(),
        session: None,
        targets: BTreeMap::new(),
        next_profile: 0,
        next_session: 0,
        next_target: 0,
    };

    let (bridge_sender, bridge_receiver) = mpsc::channel::<cdp_edge::BridgeRequest>();
    let _cdp_server = if let (Some(port), Some(ready_file)) = (cdp_port, ready_file) {
        let server = cdp_edge::Server::start(port, bridge_sender)?;
        let receipt = json!({
            "cdp_port":server.port(),
            "browser_websocket_url":server.browser_websocket_url(),
        });
        std::fs::write(ready_file, serde_json::to_vec(&receipt)?)?;
        Some(server)
    } else {
        drop(bridge_sender);
        None
    };
    let mut next_bridge_request = 0u64;

    let (sender, receiver) = mpsc::channel::<Line>();
    std::thread::spawn(move || {
        let stdin = io::stdin();
        let mut reader = stdin.lock();
        loop {
            match read_bounded_line(&mut reader) {
                Ok(Line::Eof) | Err(_) => {
                    let _ = sender.send(Line::Eof);
                    return;
                }
                Ok(line) => {
                    if sender.send(line).is_err() {
                        return;
                    }
                }
            }
        }
    });

    let stdout = io::stdout();
    let mut out = stdout.lock();
    out.write_all(b"")?;
    loop {
        host.spin();
        // CDP requests reach the same host at operation boundaries.
        while let Ok(bridge) = bridge_receiver.try_recv() {
            next_bridge_request += 1;
            let request = Request {
                request_id: format!("req_cdp_{next_bridge_request}"),
                deadline: Duration::from_millis(5000),
                operation: bridge.operation,
                arguments: bridge.arguments,
            };
            let outcome = host
                .execute(&request)
                .map_err(|error| error.code.to_owned());
            let _ = bridge.reply.send(outcome);
        }
        let line = match receiver.try_recv() {
            Ok(line) => line,
            Err(mpsc::TryRecvError::Empty) => {
                std::thread::sleep(Duration::from_millis(1));
                continue;
            }
            Err(mpsc::TryRecvError::Disconnected) => break,
        };
        let response = match line {
            Line::Eof => break,
            Line::Oversized => envelope("req_invalid", Err(invalid("request exceeds byte limit"))),
            Line::Bytes(bytes) if bytes.is_empty() => {
                envelope("req_invalid", Err(invalid("request is empty")))
            }
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
    host.targets.clear();
    host.spin();
    drop(host);
    Ok(())
}
