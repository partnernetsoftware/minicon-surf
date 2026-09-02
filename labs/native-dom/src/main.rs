//! Native bounded route, second slice: HTML parsing, DOM and a bounded script realm.
//!
//! `native-dom-control` serves the control 0.0.1 vocabulary from an
//! html5ever-parsed document mirrored into a QuickJS realm (`rquickjs`) with a
//! minimal DOM shim. Inline and same-origin external `<script>` elements run
//! after parsing, DOM events and `MutationObserver` work, `fetch()` is served
//! by the bounded network module between evaluation turns, and the same
//! in-page instrumentation the engine hosts inject runs unchanged. There is
//! still no layout, storage or timers beyond microtasks; those remain typed
//! failures or documented gaps.

use std::collections::BTreeMap;
use std::error::Error;
use std::ffi::c_void;
use std::io::{self, BufRead, Write};
use std::path::PathBuf;
use std::time::{Duration, Instant};

use dom_query::{Document, NodeRef};
use rquickjs::{Context, Runtime};
use serde_json::{Map, Value, json};
use url::Url;

mod net;

const PROTOCOL: &str = "minicon-surf.control";
const VERSION: &str = "0.0.1";
const MAX_REQUEST_BYTES: usize = 65_536;
const MAX_RESPONSE_BYTES: usize = 4_194_304;
const MAX_DEADLINE_MS: u64 = 120_000;
const MAX_TARGETS: usize = 8;
const MAX_PROFILES: usize = 8;
const MAX_SNAPSHOT_NODES: u64 = 128;
const MAX_FIXTURE_BYTES: u64 = 1_048_576;
const REALM_MEMORY_LIMIT: usize = 16 * 1024 * 1024;
const REALM_STACK_LIMIT: usize = 512 * 1024;
const MAX_NETWORK_ROUNDS: usize = 64;
const DOM_SHIM_JS: &str = include_str!("dom_shim.js");
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

fn parse_request(bytes: &[u8]) -> Result<Request, Box<(String, ControlError)>> {
    let value: Value = serde_json::from_slice(bytes).map_err(|_| {
        Box::new((
            "req_invalid".to_owned(),
            invalid("request is not valid JSON"),
        ))
    })?;
    let object = value.as_object().ok_or_else(|| {
        Box::new((
            "req_invalid".to_owned(),
            invalid("request is not an object"),
        ))
    })?;
    let request_id = object
        .get("request_id")
        .and_then(Value::as_str)
        .filter(|id| valid_id("req_", id))
        .map(str::to_owned)
        .ok_or_else(|| {
            Box::new((
                "req_invalid".to_owned(),
                invalid("request_id is missing or malformed"),
            ))
        })?;
    let fail = |message: &str| Box::new((request_id.clone(), invalid(message)));
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

// ------------------------------------------------------------------ realm

/// One bounded QuickJS realm holding the mirrored document.
struct Realm {
    runtime: Runtime,
    context: Context,
}

impl Realm {
    fn new() -> Result<Self, ControlError> {
        let runtime = Runtime::new().map_err(|e| {
            ControlError::new("internal", format!("script runtime failed: {e}"), false)
        })?;
        runtime.set_memory_limit(REALM_MEMORY_LIMIT);
        runtime.set_max_stack_size(REALM_STACK_LIMIT);
        let context = Context::full(&runtime).map_err(|e| {
            ControlError::new("internal", format!("script context failed: {e}"), false)
        })?;
        Ok(Realm { runtime, context })
    }

    /// Evaluate a script, run the microtasks it queued, and return its string result.
    fn eval(
        &self,
        script: &str,
        deadline: Instant,
        target_id: &str,
    ) -> Result<String, ControlError> {
        self.runtime
            .set_interrupt_handler(Some(Box::new(move || Instant::now() >= deadline)));
        let outcome = self
            .context
            .with(|ctx| match ctx.eval::<rquickjs::Value, _>(script) {
                Ok(value) => {
                    if value.is_undefined() || value.is_null() {
                        Ok(String::new())
                    } else {
                        let text: String = ctx
                            .globals()
                            .get::<_, rquickjs::Function>("String")
                            .and_then(|f| f.call((value,)))
                            .unwrap_or_default();
                        Ok(text)
                    }
                }
                Err(error) => {
                    let exception = ctx.catch();
                    let message = exception
                        .as_exception()
                        .and_then(|e| e.message())
                        .unwrap_or_else(|| format!("{error}"));
                    Err(message)
                }
            });
        self.runtime.set_interrupt_handler(None);
        let result = match outcome {
            Ok(text) => text,
            Err(message) => {
                let code = if Instant::now() >= deadline {
                    "deadline_exceeded"
                } else {
                    "internal"
                };
                return Err(ControlError::new(
                    code,
                    "script evaluation failed",
                    code == "deadline_exceeded",
                )
                .scoped("target", target_id)
                .details(json!({"engine_error":message.chars().take(256).collect::<String>()})));
            }
        };
        self.drain_jobs(deadline);
        Ok(result)
    }

    fn drain_jobs(&self, deadline: Instant) {
        while Instant::now() < deadline {
            match self.runtime.execute_pending_job() {
                Ok(true) => continue,
                _ => break,
            }
        }
    }

    fn malloc_bytes(&self) -> usize {
        self.runtime.memory_usage().malloc_size.max(0) as usize
    }
}

// --------------------------------------------------------------- document

fn serialize_children(node: &NodeRef, out: &mut Vec<Value>) {
    for child in node.children() {
        if child.is_text() {
            out.push(json!({"x": child.text().to_string()}));
        } else if child.is_element() {
            let name = child.node_name().map(|n| n.to_string()).unwrap_or_default();
            let attrs: Map<String, Value> = child
                .attrs()
                .iter()
                .map(|a| (a.name.local.to_string(), json!(a.value.to_string())))
                .collect();
            let mut children = Vec::new();
            serialize_children(&child, &mut children);
            out.push(json!({"e": name, "a": attrs, "c": children}));
        }
    }
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
    url: Option<Url>,
    document_framing: &'static str,
    fixture_bytes: usize,
    element_count: usize,
    script_count: usize,
    skipped_scripts: Vec<Value>,
    budget: net::Budget,
    realm: Realm,
    last_snapshot: Option<(u64, usize)>,
}

impl Target {
    /// Evaluate in the realm, then serve every `fetch()` the script queued
    /// under the network policy and per-target budget before returning.
    fn eval(
        &mut self,
        script: &str,
        deadline: Instant,
        policy: &net::Policy,
    ) -> Result<String, ControlError> {
        let result = self.realm.eval(script, deadline, &self.id)?;
        self.pump_network(deadline, policy)?;
        Ok(result)
    }

    fn pump_network(
        &mut self,
        deadline: Instant,
        policy: &net::Policy,
    ) -> Result<(), ControlError> {
        for _ in 0..MAX_NETWORK_ROUNDS {
            let queued = self.realm.eval("__mcsNetTake()", deadline, &self.id)?;
            let requests: Vec<Value> = serde_json::from_str(&queued).unwrap_or_default();
            if requests.is_empty() {
                return Ok(());
            }
            for (index, request) in requests.iter().enumerate() {
                let Some(id) = request.get("id").and_then(Value::as_u64) else {
                    continue;
                };
                let raw = request
                    .get("url")
                    .and_then(Value::as_str)
                    .unwrap_or_default();
                let outcome = if index >= net::MAX_PENDING_PER_TURN {
                    self.budget.denied += 1;
                    Err(net::NetError {
                        code: "resource_limit",
                        reason: "pending-count",
                        detail: format!(
                            "more than {} fetches queued in one turn",
                            net::MAX_PENDING_PER_TURN
                        ),
                    })
                } else {
                    match self.resolve(raw) {
                        Ok(url) => net::fetch(url.as_str(), policy, &mut self.budget, deadline),
                        Err(error) => Err(error),
                    }
                };
                let settle = match outcome {
                    Ok(response) => {
                        let body = String::from_utf8_lossy(&response.body).into_owned();
                        let mut headers = Map::new();
                        if let Some(content_type) = &response.content_type {
                            headers.insert("content-type".into(), json!(content_type));
                        }
                        format!(
                            "__mcsNetSettle({id}, true, {})",
                            json!({"status":response.status,"url":response.url.as_str(),"redirects":response.redirects,"headers":headers,"body":body})
                        )
                    }
                    Err(error) => format!(
                        "__mcsNetSettle({id}, false, {})",
                        json!({"code":error.code,"reason":error.reason,"detail":error.detail})
                    ),
                };
                self.realm.eval(&settle, deadline, &self.id)?;
            }
        }
        Err(ControlError::new(
            "resource_limit",
            "script kept queueing fetches across the round limit",
            false,
        )
        .scoped("target", &self.id))
    }

    fn resolve(&self, raw: &str) -> Result<Url, net::NetError> {
        let parsed = match &self.url {
            Some(base) => base.join(raw),
            None => Url::parse(raw),
        };
        parsed.map_err(|e| net::NetError {
            code: "invalid_request",
            reason: "url",
            detail: format!("URL is malformed: {e}"),
        })
    }
}

fn net_error(error: net::NetError, target_id: &str) -> ControlError {
    ControlError::new(
        error.code,
        format!("network policy: {}", error.reason),
        error.code == "deadline_exceeded",
    )
    .scoped("target", target_id)
    .details(json!({"reason":error.reason,"detail":error.detail}))
}

struct Host {
    fixture_root: PathBuf,
    policy: net::Policy,
    profiles: BTreeMap<String, Profile>,
    session: Option<Session>,
    targets: BTreeMap<String, Target>,
    next_profile: u64,
    next_session: u64,
    next_target: u64,
}

impl Host {
    fn target_mut(&mut self, id: &str) -> Result<&mut Target, ControlError> {
        self.targets
            .get_mut(id)
            .ok_or_else(|| not_found("target", id))
    }

    fn revision(
        target: &mut Target,
        deadline: Instant,
        policy: &net::Policy,
    ) -> Result<u64, ControlError> {
        let text = target.eval(REVISION_JS, deadline, policy)?;
        text.parse::<i64>()
            .ok()
            .filter(|r| *r >= 0)
            .map(|r| r as u64)
            .ok_or_else(|| {
                ControlError::new(
                    "internal",
                    "target lost its revision instrumentation",
                    false,
                )
                .scoped("target", &target.id)
            })
    }

    fn eval_json(
        target: &mut Target,
        script: &str,
        deadline: Instant,
        policy: &net::Policy,
    ) -> Result<Value, ControlError> {
        let text = target.eval(script, deadline, policy)?;
        serde_json::from_str(&text).map_err(|_| {
            ControlError::new("internal", "engine returned malformed snapshot JSON", false)
                .scoped("target", &target.id)
        })
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
                json!({"kind":"target_list","targets":self.targets.values().map(|t| json!({"target":t.id,"session":t.session_id,"fixture":t.fixture,"url":t.url.as_ref().map(Url::as_str)})).collect::<Vec<_>>()}),
            ),
            "target.inspect" => {
                let object = exact_object(a, &["target"])?;
                let id = typed_field(object, "target", "target")?.to_owned();
                let policy = self.policy.clone();
                let target = self.target_mut(&id)?;
                let revision = Self::revision(target, deadline, &policy)?;
                Ok(json!({
                    "kind":"target","target":target.id,"session":target.session_id,"fixture":target.fixture,
                    "url":target.url.as_ref().map(Url::as_str),"document_framing":target.document_framing,"revision":revision,"load_complete":true,"crashed":false,
                    "script_realm":true,"scripts_run":target.script_count,"scripts_skipped":target.skipped_scripts,
                    "network":{"fetches":target.budget.fetches,"bytes":target.budget.bytes,"denied":target.budget.denied}
                }))
            }
            "target.close" => {
                let object = exact_object(a, &["target"])?;
                let id = typed_field(object, "target", "target")?;
                self.targets
                    .remove(id)
                    .ok_or_else(|| not_found("target", id))?;
                Ok(json!({"kind":"target_closed","target":id}))
            }
            "target.snapshot" => self.target_snapshot(a, deadline),
            "target.act" => self.target_act(a, deadline),
            "target.wait" => self.target_wait(a, deadline),
            "memory.report" => Ok(self.memory_report()),
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
                    "the native DOM slice offers ephemeral profiles only",
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
                "this host owns one live session; close it first",
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
        let closed = self.targets.len();
        self.targets.clear();
        Ok(
            json!({"kind":"session_closed","session":session.id,"profile":session.profile_id,"closed_targets":closed}),
        )
    }

    fn target_open(&mut self, arguments: &Value, deadline: Instant) -> Result<Value, ControlError> {
        let object = arguments
            .as_object()
            .ok_or_else(|| invalid("arguments must be an object"))?;
        let by_fixture =
            object.len() == 2 && object.contains_key("session") && object.contains_key("fixture");
        let by_url =
            object.len() == 2 && object.contains_key("session") && object.contains_key("url");
        if !by_fixture && !by_url {
            return Err(invalid(
                "target.open takes session plus exactly one of fixture or url",
            ));
        }
        let session = typed_field(object, "session", "session")?;
        if self.session.as_ref().map(|s| s.id.as_str()) != Some(session) {
            return Err(not_found("session", session));
        }
        if self.targets.len() >= MAX_TARGETS {
            return Err(ControlError::new(
                "resource_limit",
                "target capacity reached",
                true,
            ));
        }
        self.next_target += 1;
        let id = format!("target_{}", self.next_target);
        let mut budget = net::Budget::default();
        let policy = self.policy.clone();

        let (label, base, bytes, framing) = if by_fixture {
            let fixture = string_field(object, "fixture")?;
            if !fixture.ends_with(".html")
                || !fixture
                    .bytes()
                    .all(|b| b.is_ascii_lowercase() || b.is_ascii_digit() || b == b'-' || b == b'.')
                || fixture.contains("..")
            {
                return Err(invalid("fixture must be a court fixture file name"));
            }
            let path = self.fixture_root.join(fixture);
            let size = std::fs::metadata(&path).map(|m| m.len()).unwrap_or(0);
            if size > MAX_FIXTURE_BYTES {
                return Err(ControlError::new(
                    "resource_limit",
                    "fixture exceeds the bounded document size",
                    false,
                ));
            }
            let bytes = std::fs::read(&path).map_err(|_| {
                ControlError::new("not_found", "fixture does not exist in the court", false)
            })?;
            (fixture.to_owned(), None, bytes, "fixture")
        } else {
            let raw = string_field(object, "url")?;
            let response = net::fetch(raw, &policy, &mut budget, deadline)
                .map_err(|error| net_error(error, &id))?;
            if response.status >= 400 {
                return Err(ControlError::new(
                    "not_found",
                    "document request was not successful",
                    false,
                )
                .scoped("target", &id)
                .details(json!({"status":response.status,"url":response.url.as_str()})));
            }
            if response
                .content_type
                .as_deref()
                .is_some_and(|t| !t.starts_with("text/html"))
            {
                return Err(ControlError::new(
                    "unsupported_capability",
                    "document is not text/html",
                    false,
                )
                .scoped("target", &id)
                .details(json!({"content_type":response.content_type})));
            }
            (
                "url".to_owned(),
                Some(response.url.clone()),
                response.body,
                response.framing.as_str(),
            )
        };

        let text = String::from_utf8_lossy(&bytes).into_owned();
        let document = Document::from(text.as_str());
        let element_count = document.select("*").nodes().len();
        let mut tree = Vec::new();
        serialize_children(&document.root(), &mut tree);
        // Scripts in document order: inline text, or a same-origin external
        // source fetched under the same policy and budget.
        let mut scripts: Vec<(String, String)> = Vec::new();
        let mut skipped = Vec::new();
        let mut external = 0usize;
        for node in document.select("script").nodes() {
            match node.attr("src") {
                None => scripts.push(("inline".into(), node.text().to_string())),
                Some(src) => {
                    let src = src.to_string();
                    let Some(base_url) = &base else {
                        skipped.push(
                            json!({"src":src,"reason":"external scripts need a network origin"}),
                        );
                        continue;
                    };
                    let Ok(resolved) = base_url.join(&src) else {
                        skipped.push(json!({"src":src,"reason":"malformed src"}));
                        continue;
                    };
                    if !net::same_origin(base_url, &resolved) {
                        budget.denied += 1;
                        skipped.push(json!({"src":src,"reason":"cross-origin script refused"}));
                        continue;
                    }
                    if external >= net::MAX_EXTERNAL_SCRIPTS {
                        budget.denied += 1;
                        skipped.push(json!({"src":src,"reason":"external script limit"}));
                        continue;
                    }
                    external += 1;
                    match net::fetch(resolved.as_str(), &policy, &mut budget, deadline) {
                        Ok(response) if response.status < 400 => scripts
                            .push((src, String::from_utf8_lossy(&response.body).into_owned())),
                        Ok(response) => skipped.push(
                            json!({"src":src,"reason":format!("status {}", response.status)}),
                        ),
                        Err(error) => {
                            skipped.push(json!({"src":src,"reason":error.reason,"code":error.code}))
                        }
                    }
                }
            }
        }
        drop(document);

        let realm = Realm::new()?;
        realm.eval(DOM_SHIM_JS, deadline, &id)?;
        let seed = format!(
            "__mcsSeed({})",
            serde_json::to_string(&tree).expect("tree serializes")
        );
        realm.eval(&seed, deadline, &id)?;
        if let Some(base_url) = &base {
            realm.eval(
                &format!("__mcsLocation({})", json!(base_url.as_str())),
                deadline,
                &id,
            )?;
        }
        let mut target = Target {
            id: id.clone(),
            session_id: session.to_owned(),
            fixture: label,
            url: base,
            document_framing: framing,
            fixture_bytes: bytes.len(),
            element_count,
            script_count: scripts.len(),
            skipped_scripts: skipped,
            budget,
            realm,
            last_snapshot: None,
        };
        for (index, (origin, script)) in scripts.iter().enumerate() {
            if let Err(error) = target.eval(script, deadline, &policy) {
                let mut details = error.details.clone().unwrap_or_else(|| json!({}));
                details["script_index"] = json!(index);
                details["script"] = json!(origin);
                return Err(ControlError::new("target_crashed", "a script threw", false)
                    .scoped("target", &id)
                    .details(details));
            }
        }
        target.eval("__mcsComplete()", deadline, &policy)?;
        let revision = target
            .eval(INSTALL_JS, deadline, &policy)?
            .parse::<u64>()
            .unwrap_or(0);
        let summary = json!({
            "kind":"target","target":id,"session":session,"revision":revision,"fixture":target.fixture,
            "url":target.url.as_ref().map(Url::as_str),"document_framing":target.document_framing,"scripts_run":target.script_count,
            "scripts_skipped":target.skipped_scripts.len(),
            "network":{"fetches":target.budget.fetches,"bytes":target.budget.bytes,"denied":target.budget.denied}
        });
        self.targets.insert(id, target);
        Ok(summary)
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
        let policy = self.policy.clone();
        let target = self.target_mut(&id)?;
        let raw = Self::eval_json(target, &snapshot_script(max_nodes), deadline, &policy)?;
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
                "the native DOM slice offers click only",
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
        let policy = self.policy.clone();
        let target = self.target_mut(&id)?;
        let current = Self::revision(target, deadline, &policy)?;
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
        let outcome = Self::eval_json(target, &act_script(revision, index), deadline, &policy)?;
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
        let after = Self::revision(target, deadline, &policy)?;
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
                "this host offers revision_at_least only",
                false,
            ));
        }
        let expected = bounded_u64(condition, "revision", 0, u64::MAX)?;
        let policy = self.policy.clone();
        loop {
            let target = self.target_mut(&id)?;
            let revision = Self::revision(target, deadline, &policy)?;
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
            // Only queued microtasks and fetch settlements, both served by
            // the revision poll above, can still change the revision.
            std::thread::sleep(Duration::from_millis(5));
        }
    }

    fn memory_report(&self) -> Value {
        let fixture_bytes: usize = self.targets.values().map(|t| t.fixture_bytes).sum();
        let elements: usize = self.targets.values().map(|t| t.element_count).sum();
        let realm_bytes: usize = self.targets.values().map(|t| t.realm.malloc_bytes()).sum();
        let fetches: usize = self.targets.values().map(|t| t.budget.fetches).sum();
        let network_bytes: usize = self.targets.values().map(|t| t.budget.bytes).sum();
        let denied: usize = self.targets.values().map(|t| t.budget.denied).sum();
        json!({
            "kind":"memory_report",
            "semantic":"native-dom-logical-owners-plus-script-realm-and-libmalloc-statistics",
            "owners":{
                "profiles":{"objects":self.profiles.len(),"object_limit":MAX_PROFILES},
                "sessions":{"objects":self.session.iter().count(),"object_limit":1},
                "targets":{"objects":self.targets.len(),"object_limit":MAX_TARGETS,"fixture_bytes":fixture_bytes,"elements":elements},
                "script_realms":{"objects":self.targets.len(),"malloc_bytes":realm_bytes,"memory_limit_bytes":REALM_MEMORY_LIMIT},
                "network":{"fetches":fetches,"bytes":network_bytes,"denied":denied,"limits":{"redirects":net::MAX_REDIRECTS,"response_bytes":net::MAX_RESPONSE_BYTES,"per_fetch_ms":net::PER_FETCH_TIMEOUT.as_millis() as u64,"pending_per_turn":net::MAX_PENDING_PER_TURN,"fetches_per_target":net::MAX_FETCHES_PER_TARGET,"bytes_per_target":net::MAX_BYTES_PER_TARGET,"allowed_origins":self.policy.allowed_origins.len()}},
            },
            "libmalloc":libmalloc_statistics(),
            "limitations":["logical owners are document sizes, QuickJS malloc bytes and fetched bytes, not process memory","no layout, image or storage owners exist in this slice","not process RSS/private/PSS"],
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
        "usage: native-dom-control serve --stdio --fixture-root DIR --config-dir DIR [--allow-origin http://HOST:PORT]..."
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
    if !fixture_root.is_dir() {
        usage();
    }
    let mut policy = net::Policy::default();
    for pair in arguments[6..].chunks_exact(2) {
        if pair[0] != "--allow-origin" {
            usage();
        }
        match net::AllowedOrigin::parse(&pair[1]) {
            Ok(origin) => policy.allowed_origins.push(origin),
            Err(message) => {
                eprintln!("--allow-origin: {message}");
                std::process::exit(64);
            }
        }
    }
    let mut host = Host {
        fixture_root,
        policy,
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
            Line::Bytes(bytes) if bytes.is_empty() => {
                envelope("req_invalid", Err(invalid("request is empty")))
            }
            Line::Bytes(bytes) => match parse_request(&bytes) {
                Ok(request) => {
                    let body = host.execute(&request);
                    envelope(&request.request_id, body)
                }
                Err(error) => envelope(&error.0, Err(error.1)),
            },
        };
        out.write_all(&response)?;
        out.write_all(b"\n")?;
        out.flush()?;
    }
    Ok(())
}
