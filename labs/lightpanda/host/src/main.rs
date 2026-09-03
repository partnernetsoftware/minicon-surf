//! Rust control 0.0.1 host for Lightpanda with one engine process per target.
//!
//! `lightpanda-control serve --stdio --fixture-root DIR --config-dir DIR`
//! mirrors `servo-control`. Every `target.open` starts one Lightpanda server
//! on a loopback port, discovers its CDP endpoint over raw HTTP (no proxy),
//! attaches with a flattened session, navigates to the fixture as a `data:`
//! URL and installs the same in-page revision instrumentation the Servo host
//! uses. `target.close` ends the process, so engine retention is zero by
//! construction. The host itself is a small Rust process, which is the point:
//! the Python court host's own footprint is no longer part of the tree.

use std::collections::BTreeMap;
use std::error::Error;
use std::io::{self, BufRead, Read, Write};
use std::net::{Ipv4Addr, SocketAddrV4, TcpListener, TcpStream};
use std::path::PathBuf;
use std::process::{Child, Command, Stdio};
use std::time::{Duration, Instant};

use base64::Engine as _;
use base64::engine::general_purpose::STANDARD;
use percent_encoding::{NON_ALPHANUMERIC, percent_encode};
use serde_json::{Map, Value, json};

mod procinfo;

const PROTOCOL: &str = "minicon-surf.control";
const VERSION: &str = "0.0.1";
const MAX_REQUEST_BYTES: usize = 65_536;
const MAX_RESPONSE_BYTES: usize = 4_194_304;
const MAX_DEADLINE_MS: u64 = 120_000;
const MAX_TARGETS: usize = 8;
const MAX_PROFILES: usize = 8;
const MAX_SNAPSHOT_NODES: u64 = 128;
const MAX_CDP_MESSAGE_BYTES: usize = 4_194_304;
const ENGINE_START_TIMEOUT: Duration = Duration::from_secs(10);
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
const READY_JS: &str =
    "(() => String(document.readyState === 'complete' && !!document.querySelector('h1')))()";

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
        format!("{operation} is reserved by control 0.0.1 but not offered by this Lightpanda host"),
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

// ----------------------------------------------------------------- engine

/// One Lightpanda server process with a loopback CDP WebSocket.
struct Engine {
    child: Child,
    stream: TcpStream,
    next_id: u64,
    /// Opaque per-host ordinal; the report names children by it, never by
    /// command line or path.
    ordinal: u64,
    /// Host generation at spawn; the report's generation is never below it.
    spawned_generation: u64,
    /// Identity read right after the spawn so a reused pid is detectable.
    identity: Option<procinfo::ProcessIdentity>,
}

fn free_port() -> io::Result<u16> {
    let listener = TcpListener::bind(SocketAddrV4::new(Ipv4Addr::LOCALHOST, 0))?;
    Ok(listener.local_addr()?.port())
}

fn engine_error(message: &str, detail: impl std::fmt::Display) -> ControlError {
    ControlError::new("internal", message, true)
        .details(json!({"engine_error":format!("{detail}").chars().take(256).collect::<String>()}))
}

impl Engine {
    fn start(binary: &str) -> Result<Self, ControlError> {
        let port = free_port().map_err(|e| engine_error("no loopback port available", e))?;
        let mut child = Command::new(binary)
            .args([
                "serve",
                "--host",
                "127.0.0.1",
                "--port",
                &port.to_string(),
                "--disable-metrics",
                "--watchdog-ms",
                "15000",
            ])
            .env("LIGHTPANDA_DISABLE_TELEMETRY", "true")
            .env("LIGHTPANDA_DISABLE_CORE_DUMP", "1")
            .stdin(Stdio::null())
            .stdout(Stdio::null())
            .stderr(Stdio::null())
            .spawn()
            .map_err(|e| engine_error("engine process did not start", e))?;
        let started = Instant::now();
        let endpoint = loop {
            match discover(port) {
                Ok(endpoint) => break endpoint,
                Err(error) => {
                    if started.elapsed() >= ENGINE_START_TIMEOUT {
                        let _ = child.kill();
                        let _ = child.wait();
                        return Err(engine_error(
                            "CDP discovery endpoint did not become ready",
                            error,
                        ));
                    }
                    std::thread::sleep(Duration::from_millis(20));
                }
            }
        };
        let stream = match websocket_connect(&endpoint) {
            Ok(stream) => stream,
            Err(error) => {
                let _ = child.kill();
                let _ = child.wait();
                return Err(engine_error("CDP WebSocket upgrade failed", error));
            }
        };
        #[cfg(target_os = "macos")]
        let identity = procinfo::identity(child.id()).map(|(identity, _)| identity);
        #[cfg(not(target_os = "macos"))]
        let identity = None;
        Ok(Engine {
            child,
            stream,
            next_id: 0,
            ordinal: 0,
            spawned_generation: 0,
            identity,
        })
    }

    fn call(
        &mut self,
        method: &str,
        params: Value,
        session: Option<&str>,
    ) -> Result<Value, ControlError> {
        self.next_id += 1;
        let id = self.next_id;
        let mut message = json!({"id":id,"method":method,"params":params});
        if let Some(session) = session {
            message["sessionId"] = json!(session);
        }
        websocket_send_text(
            &mut self.stream,
            &serde_json::to_vec(&message).expect("cdp message serializes"),
        )
        .map_err(|e| engine_error("CDP send failed", e))?;
        loop {
            let payload = websocket_recv_text(&mut self.stream)
                .map_err(|e| engine_error("CDP receive failed", e))?;
            let Ok(value) = serde_json::from_slice::<Value>(&payload) else {
                continue;
            };
            if value.get("id").and_then(Value::as_u64) != Some(id) {
                continue;
            }
            if let Some(error) = value.get("error") {
                return Err(ControlError::new("internal", format!("{method} failed"), true)
                    .details(json!({"engine_error":error.to_string().chars().take(256).collect::<String>()})));
            }
            return Ok(value.get("result").cloned().unwrap_or_else(|| json!({})));
        }
    }

    fn stop(mut self) {
        let _ = websocket_send_close(&mut self.stream);
        let _ = self.child.kill();
        let _ = self.child.wait();
    }
}

fn discover(port: u16) -> io::Result<(String, u16, String)> {
    let mut stream = TcpStream::connect_timeout(
        &SocketAddrV4::new(Ipv4Addr::LOCALHOST, port).into(),
        Duration::from_millis(250),
    )?;
    stream.set_read_timeout(Some(Duration::from_millis(500)))?;
    write!(
        stream,
        "GET /json/version HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\nConnection: close\r\n\r\n"
    )?;
    let mut response = Vec::new();
    stream.read_to_end(&mut response)?;
    let text = String::from_utf8_lossy(&response);
    let (_, body) = text.split_once("\r\n\r\n").ok_or_else(|| {
        io::Error::new(
            io::ErrorKind::InvalidData,
            "discovery response lacks a body",
        )
    })?;
    let document: Value = serde_json::from_str(body.trim())
        .map_err(|_| io::Error::new(io::ErrorKind::InvalidData, "discovery body is not JSON"))?;
    let url = document
        .get("webSocketDebuggerUrl")
        .and_then(Value::as_str)
        .ok_or_else(|| {
            io::Error::new(
                io::ErrorKind::InvalidData,
                "discovery lacks webSocketDebuggerUrl",
            )
        })?;
    let rest = url.strip_prefix("ws://127.0.0.1:").ok_or_else(|| {
        io::Error::new(
            io::ErrorKind::InvalidData,
            "discovery returned a non-loopback endpoint",
        )
    })?;
    let (port_text, path) = rest.split_once('/').unwrap_or((rest, ""));
    let ws_port: u16 = port_text
        .parse()
        .map_err(|_| io::Error::new(io::ErrorKind::InvalidData, "discovery port is malformed"))?;
    Ok(("127.0.0.1".to_owned(), ws_port, format!("/{path}")))
}

fn websocket_connect(endpoint: &(String, u16, String)) -> io::Result<TcpStream> {
    let (host, port, path) = endpoint;
    let mut stream = TcpStream::connect_timeout(
        &SocketAddrV4::new(Ipv4Addr::LOCALHOST, *port).into(),
        Duration::from_secs(3),
    )?;
    stream.set_read_timeout(Some(Duration::from_secs(30)))?;
    stream.set_write_timeout(Some(Duration::from_secs(10)))?;
    stream.set_nodelay(true)?;
    let key = STANDARD.encode(b"minicon-surf-lp!");
    write!(
        stream,
        "GET {path} HTTP/1.1\r\nHost: {host}:{port}\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n"
    )?;
    let mut header = Vec::new();
    let mut byte = [0_u8; 1];
    while !header.ends_with(b"\r\n\r\n") {
        stream.read_exact(&mut byte)?;
        header.push(byte[0]);
        if header.len() > 16_384 {
            return Err(io::Error::new(
                io::ErrorKind::InvalidData,
                "upgrade header exceeds limit",
            ));
        }
    }
    if !header.starts_with(b"HTTP/1.1 101") {
        return Err(io::Error::new(
            io::ErrorKind::InvalidData,
            "upgrade was not accepted",
        ));
    }
    Ok(stream)
}

fn websocket_send_frame(stream: &mut TcpStream, opcode: u8, payload: &[u8]) -> io::Result<()> {
    let mut frame = vec![0x80 | opcode];
    if payload.len() < 126 {
        frame.push(0x80 | payload.len() as u8);
    } else if payload.len() <= u16::MAX as usize {
        frame.push(0x80 | 126);
        frame.extend_from_slice(&(payload.len() as u16).to_be_bytes());
    } else {
        frame.push(0x80 | 127);
        frame.extend_from_slice(&(payload.len() as u64).to_be_bytes());
    }
    let mask = [0x4d, 0x43, 0x53, 0x21];
    frame.extend_from_slice(&mask);
    frame.extend(payload.iter().enumerate().map(|(i, b)| b ^ mask[i % 4]));
    stream.write_all(&frame)
}

fn websocket_send_text(stream: &mut TcpStream, payload: &[u8]) -> io::Result<()> {
    websocket_send_frame(stream, 0x1, payload)
}

fn websocket_send_close(stream: &mut TcpStream) -> io::Result<()> {
    websocket_send_frame(stream, 0x8, &[])
}

fn websocket_recv_text(stream: &mut TcpStream) -> io::Result<Vec<u8>> {
    loop {
        let mut header = [0_u8; 2];
        stream.read_exact(&mut header)?;
        let opcode = header[0] & 0x0f;
        let masked = header[1] & 0x80 != 0;
        let mut length = u64::from(header[1] & 0x7f);
        if length == 126 {
            let mut bytes = [0_u8; 2];
            stream.read_exact(&mut bytes)?;
            length = u64::from(u16::from_be_bytes(bytes));
        } else if length == 127 {
            let mut bytes = [0_u8; 8];
            stream.read_exact(&mut bytes)?;
            length = u64::from_be_bytes(bytes);
        }
        if length > MAX_CDP_MESSAGE_BYTES as u64 {
            return Err(io::Error::new(
                io::ErrorKind::InvalidData,
                "CDP message exceeds limit",
            ));
        }
        let mut mask = [0_u8; 4];
        if masked {
            stream.read_exact(&mut mask)?;
        }
        let mut payload = vec![0_u8; length as usize];
        stream.read_exact(&mut payload)?;
        if masked {
            for (index, byte) in payload.iter_mut().enumerate() {
                *byte ^= mask[index % 4];
            }
        }
        match opcode {
            0x1 => return Ok(payload),
            0x8 => {
                return Err(io::Error::new(
                    io::ErrorKind::ConnectionAborted,
                    "CDP connection closed",
                ));
            }
            0x9 => websocket_send_frame(stream, 0xA, &payload)?,
            _ => {}
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
    engine: Engine,
    cdp_session: String,
    last_snapshot: Option<(u64, usize)>,
}

struct Host {
    engine_binary: String,
    fixture_root: PathBuf,
    profiles: BTreeMap<String, Profile>,
    session: Option<Session>,
    targets: BTreeMap<String, Target>,
    next_profile: u64,
    next_session: u64,
    next_target: u64,
    /// Advances on every engine spawn and every reap; reports carry it.
    generation: u64,
    next_child: u64,
    children_spawned_total: u64,
    children_reaped_total: u64,
}

impl Host {
    fn evaluate(
        target: &mut Target,
        expression: &str,
        deadline: Instant,
    ) -> Result<Value, ControlError> {
        if Instant::now() >= deadline {
            return Err(ControlError::new(
                "deadline_exceeded",
                "engine did not answer before deadline",
                true,
            )
            .scoped("target", &target.id));
        }
        let session = target.cdp_session.clone();
        let result = target
            .engine
            .call(
                "Runtime.evaluate",
                json!({"expression":expression,"returnByValue":true}),
                Some(&session),
            )
            .map_err(|error| error.scoped("target", &target.id))?;
        if let Some(details) = result.get("exceptionDetails") {
            return Err(ControlError::new("internal", "JavaScript evaluation threw", false)
                .scoped("target", &target.id)
                .details(json!({"engine_error":details.to_string().chars().take(256).collect::<String>()})));
        }
        Ok(result
            .get("result")
            .and_then(|r| r.get("value"))
            .cloned()
            .unwrap_or(Value::Null))
    }

    fn evaluate_json(
        target: &mut Target,
        expression: &str,
        deadline: Instant,
    ) -> Result<Value, ControlError> {
        match Self::evaluate(target, expression, deadline)? {
            Value::String(text) => serde_json::from_str(&text).map_err(|_| {
                ControlError::new("internal", "engine returned malformed snapshot JSON", false)
                    .scoped("target", &target.id)
            }),
            other => Err(ControlError::new(
                "internal",
                "engine returned a non-string value",
                false,
            )
            .scoped("target", &target.id)
            .details(json!({"value":other.to_string().chars().take(128).collect::<String>()}))),
        }
    }

    fn revision(target: &mut Target, deadline: Instant) -> Result<u64, ControlError> {
        let value = Self::evaluate(target, REVISION_JS, deadline)?;
        value
            .as_str()
            .and_then(|s| s.parse::<i64>().ok())
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

    fn target_mut(&mut self, id: &str) -> Result<&mut Target, ControlError> {
        self.targets
            .get_mut(id)
            .ok_or_else(|| not_found("target", id))
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
                let id = typed_field(object, "target", "target")?.to_owned();
                let target = self.target_mut(&id)?;
                let revision = Self::revision(target, deadline)?;
                Ok(
                    json!({"kind":"target","target":target.id,"session":target.session_id,"fixture":target.fixture,"revision":revision,"load_complete":true,"crashed":false,"engine_process":true}),
                )
            }
            "target.close" => {
                let object = exact_object(a, &["target"])?;
                let id = typed_field(object, "target", "target")?;
                let target = self
                    .targets
                    .remove(id)
                    .ok_or_else(|| not_found("target", id))?;
                let child = self.stop_engine(target.engine);
                Ok(json!({"kind":"target_closed","target":id,"child":child}))
            }
            "target.snapshot" => self.target_snapshot(a, deadline),
            "target.act" => self.target_act(a, deadline),
            "target.wait" => self.target_wait(a, deadline),
            "memory.report" => {
                exact_object(a, &[])?;
                self.memory_report()
            }
            other => Err(unsupported_operation(other)),
        }
    }

    /// Stop an engine and reap it; the reap advances the generation.
    fn stop_engine(&mut self, engine: Engine) -> Value {
        let child = json!({"child":format!("child_{}", engine.ordinal),"pid":engine.child.id(),"spawned_generation":engine.spawned_generation});
        engine.stop();
        self.generation += 1;
        self.children_reaped_total += 1;
        child
    }

    #[cfg(target_os = "macos")]
    fn memory_report(&mut self) -> Result<Value, ControlError> {
        let host_pid = procinfo::host_pid();
        let mut incomplete: Vec<Value> = Vec::new();
        let mut children = Vec::new();
        let mut owned_pids = std::collections::BTreeSet::new();
        let mut summed_resident: u64 = 0;
        let mut summed_footprint: u64 = 0;
        let mut processes: u64 = 0;
        for target in self.targets.values_mut() {
            let engine = &mut target.engine;
            let pid = engine.child.id();
            let child_id = format!("child_{}", engine.ordinal);
            // The report never terminates a child; `try_wait` only observes.
            let state = match engine.child.try_wait() {
                Ok(Some(_)) => procinfo::ChildState::Exited,
                Ok(None) => procinfo::classify(pid, engine.identity, host_pid),
                Err(_) => procinfo::ChildState::Unreadable,
            };
            let metrics = if state.is_complete() {
                procinfo::metrics(pid)
            } else {
                None
            };
            let state = match (state, metrics) {
                (state, None) if state.is_complete() => procinfo::ChildState::ExitedDuringSample,
                (state, _) => state,
            };
            if let Some(metrics) = metrics {
                summed_resident += metrics.resident_bytes;
                summed_footprint += metrics.physical_footprint_bytes;
                processes += 1;
                owned_pids.insert(pid);
            } else {
                incomplete.push(json!({"child":child_id,"target":target.id,"state":state.name()}));
            }
            children.push(json!({
                "child":child_id,
                "target":target.id,
                "role":"engine",
                "state":state.name(),
                "pid":pid,
                "spawned_generation":engine.spawned_generation,
                "identity_verified":engine.identity.is_some() && state == procinfo::ChildState::Running,
                "metrics":metrics.map(procinfo::Metrics::to_json),
            }));
        }
        // Descendants the host did not spawn as a target engine are listed
        // and summed, but attributed to no owner.
        let mut unattributed = Vec::new();
        let mut frontier = vec![host_pid];
        let mut seen = std::collections::BTreeSet::new();
        while let Some(parent) = frontier.pop() {
            for pid in procinfo::children(parent) {
                if !seen.insert(pid) {
                    continue;
                }
                frontier.push(pid);
                if owned_pids.contains(&pid) {
                    continue;
                }
                let state = match procinfo::identity(pid) {
                    None => "unreadable",
                    Some((_, true)) => "zombie",
                    Some((_, false)) => "running",
                };
                let metrics = procinfo::metrics(pid);
                if let Some(metrics) = metrics {
                    summed_resident += metrics.resident_bytes;
                    summed_footprint += metrics.physical_footprint_bytes;
                    processes += 1;
                } else {
                    incomplete.push(json!({"pid":pid,"parent_pid":parent,"state":state}));
                }
                unattributed.push(json!({"pid":pid,"parent_pid":parent,"role":"unknown","state":state,"metrics":metrics.map(procinfo::Metrics::to_json)}));
            }
        }
        let host = procinfo::metrics(host_pid).map(procinfo::Metrics::to_json);
        match &host {
            Some(metrics) => {
                summed_resident += metrics["resident_bytes"].as_u64().unwrap_or(0);
                summed_footprint += metrics["physical_footprint_bytes"].as_u64().unwrap_or(0);
                processes += 1;
            }
            None => incomplete.push(json!({"host":true,"state":"unreadable"})),
        }
        Ok(json!({
            "kind":"memory_report",
            "semantic":"process-tree-metrics-by-owner",
            "generation":self.generation,
            "host":{"pid":host_pid,"role":"host","state":"running","metrics":host},
            "children":children,
            "unattributed_descendants":unattributed,
            "tree":{
                "processes":processes,
                "summed_resident_bytes":summed_resident,
                "summed_physical_footprint_bytes":summed_footprint,
                "complete":incomplete.is_empty(),
                "incomplete":incomplete,
            },
            "private_bytes":procinfo::private_bytes_statement(),
            "owners":{"targets":{"objects":self.targets.len(),"object_limit":MAX_TARGETS},"engine_processes":children.len()},
            "counters":{"children_spawned_total":self.children_spawned_total,"children_reaped_total":self.children_reaped_total},
            "limitations":[
                "resident_bytes is the task resident set as ps reports it; summing it over processes double counts shared pages, so summed_resident_bytes is not total memory",
                "physical_footprint_bytes is the kernel's per-process phys_footprint (proc_pid_rusage RUSAGE_INFO_V4)",
                "private and shared bytes are unavailable without a task port",
                "the engine exposes no in-process owner attribution; children are attributed by the host's spawn, not by the engine",
                "read-only: the report never terminates or signals a child",
            ],
        }))
    }

    #[cfg(not(target_os = "macos"))]
    fn memory_report(&mut self) -> Result<Value, ControlError> {
        Err(ControlError::new(
            "unsupported_capability",
            "process metrics are qualified on macOS only",
            false,
        )
        .details(json!({"engine_processes":self.targets.len()})))
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
                    "this Lightpanda host offers ephemeral profiles only",
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
                "this Lightpanda host owns one live session; close it first",
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
        for (_, target) in std::mem::take(&mut self.targets) {
            self.stop_engine(target.engine);
        }
        Ok(
            json!({"kind":"session_closed","session":session.id,"profile":session.profile_id,"closed_targets":closed}),
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
        let url = format!(
            "data:text/html,{}",
            percent_encode(&bytes, NON_ALPHANUMERIC)
        );
        self.next_target += 1;
        let id = format!("target_{}", self.next_target);
        let mut engine = Engine::start(&self.engine_binary)?;
        self.generation += 1;
        self.next_child += 1;
        self.children_spawned_total += 1;
        engine.ordinal = self.next_child;
        engine.spawned_generation = self.generation;
        let attach = (|| -> Result<String, ControlError> {
            let created = engine.call("Target.createTarget", json!({"url":"about:blank"}), None)?;
            let cdp_target = created
                .get("targetId")
                .and_then(Value::as_str)
                .ok_or_else(|| engine_error("createTarget lacks targetId", "missing"))?
                .to_owned();
            let attached = engine.call(
                "Target.attachToTarget",
                json!({"targetId":cdp_target,"flatten":true}),
                None,
            )?;
            let cdp_session = attached
                .get("sessionId")
                .and_then(Value::as_str)
                .ok_or_else(|| engine_error("attachToTarget lacks sessionId", "missing"))?
                .to_owned();
            engine.call("Page.enable", json!({}), Some(&cdp_session))?;
            engine.call("Runtime.enable", json!({}), Some(&cdp_session))?;
            engine.call("Page.navigate", json!({"url":url}), Some(&cdp_session))?;
            Ok(cdp_session)
        })();
        let cdp_session = match attach {
            Ok(session) => session,
            Err(error) => {
                engine.stop();
                return Err(error.scoped("target", &id));
            }
        };
        let mut target = Target {
            id: id.clone(),
            session_id: session.to_owned(),
            fixture: fixture.to_owned(),
            engine,
            cdp_session,
            last_snapshot: None,
        };
        let ready = (|| -> Result<u64, ControlError> {
            loop {
                if Self::evaluate(&mut target, READY_JS, deadline)?.as_str() == Some("true") {
                    break;
                }
                std::thread::sleep(Duration::from_millis(5));
            }
            let revision = Self::evaluate(&mut target, INSTALL_JS, deadline)?;
            Ok(revision.as_str().and_then(|s| s.parse().ok()).unwrap_or(0))
        })();
        match ready {
            Ok(revision) => {
                self.targets.insert(id.clone(), target);
                Ok(
                    json!({"kind":"target","target":id,"session":session,"revision":revision,"fixture":fixture}),
                )
            }
            Err(error) => {
                self.stop_engine(target.engine);
                Err(error)
            }
        }
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
        let target = self.target_mut(&id)?;
        let raw = Self::evaluate_json(target, &snapshot_script(max_nodes), deadline)?;
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
        target.last_snapshot = Some((revision, nodes.len()));
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
                "this Lightpanda host offers click only",
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
        let target = self.target_mut(&id)?;
        let current = Self::revision(target, deadline)?;
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
        let outcome = Self::evaluate_json(target, &act_script(revision, index), deadline)?;
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
        let after = Self::revision(target, deadline)?;
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
                "this Lightpanda host offers revision_at_least only",
                false,
            ));
        }
        let expected = bounded_u64(condition, "revision", 0, u64::MAX)?;
        loop {
            let target = self.target_mut(&id)?;
            let revision = Self::revision(target, deadline)?;
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
            std::thread::sleep(Duration::from_millis(5));
        }
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
        "usage: lightpanda-control serve --stdio --fixture-root DIR --config-dir DIR (MINICON_SURF_LIGHTPANDA names the engine)"
    );
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
    let engine_binary = std::env::var("MINICON_SURF_LIGHTPANDA").unwrap_or_default();
    if !fixture_root.is_dir()
        || engine_binary.is_empty()
        || !PathBuf::from(&engine_binary).is_file()
    {
        usage();
    }
    let mut host = Host {
        engine_binary,
        fixture_root,
        profiles: BTreeMap::new(),
        session: None,
        targets: BTreeMap::new(),
        next_profile: 0,
        next_session: 0,
        next_target: 0,
        generation: 0,
        next_child: 0,
        children_spawned_total: 0,
        children_reaped_total: 0,
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
    for (_, target) in std::mem::take(&mut host.targets) {
        target.engine.stop();
    }
    Ok(())
}
