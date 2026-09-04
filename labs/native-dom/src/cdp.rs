//! Loopback CDP edge for the native bounded route (D4).
//!
//! The edge owns no page state. Every qualified method is translated into a
//! control 0.0.1 operation and sent over a channel to the host's main loop,
//! which executes it against the same targets, frames and revisions the stdio
//! door uses; the edge keeps only target names, adapter ids and per-session
//! node tables. The HTTP and WebSocket handling mirrors the synthetic and
//! Servo edges (loopback only, masked client frames, no fragmentation, bounded
//! header and message sizes, read timeouts) but is a copy, not a shared
//! crate: labs share courts, not dependency graphs.
//!
//! `Page.FrameId`s are adapter-scoped per connection and one-to-one with a
//! native frame while both live; they are never the native ids. No
//! `Runtime.ExecutionContextId` is ever emitted, and every method that would
//! need lifecycle, network or execution-context events is an explicit
//! `-32601` (see `cdp-qualification-0.0.1.json`).

use std::collections::BTreeMap;
use std::io::{self, Read, Write};
use std::net::{Ipv4Addr, SocketAddrV4, TcpListener, TcpStream};
use std::sync::Arc;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::mpsc::{self, Sender};
use std::thread::{self, JoinHandle};
use std::time::Duration;

use base64::Engine;
use base64::engine::general_purpose::STANDARD;
use serde_json::{Value, json};
use sha1::{Digest, Sha1};

const MAX_HTTP_HEADER_BYTES: usize = 16_384;
const MAX_CDP_MESSAGE_BYTES: usize = 65_536;
const WEBSOCKET_GUID: &str = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11";
const NATIVE_REPLY_TIMEOUT: Duration = Duration::from_secs(30);
const WEBSOCKET_PATH: &str = "/devtools/browser/native-dom-control";

/// One native operation requested by the edge; the host answers on `reply`
/// with the operation's result or its typed error code.
pub struct BridgeRequest {
    pub operation: String,
    pub arguments: Value,
    pub reply: Sender<Result<Value, String>>,
}

pub struct Server {
    port: u16,
    shutdown: Arc<AtomicBool>,
    join: Option<JoinHandle<io::Result<()>>>,
}

impl Server {
    pub fn start(port: u16, bridge: Sender<BridgeRequest>) -> io::Result<Self> {
        let listener = TcpListener::bind(SocketAddrV4::new(Ipv4Addr::LOCALHOST, port))?;
        let port = listener.local_addr()?.port();
        listener.set_nonblocking(true)?;
        let shutdown = Arc::new(AtomicBool::new(false));
        let thread_shutdown = shutdown.clone();
        let join = thread::spawn(move || serve(listener, bridge, thread_shutdown));
        Ok(Self {
            port,
            shutdown,
            join: Some(join),
        })
    }

    pub fn port(&self) -> u16 {
        self.port
    }

    pub fn browser_websocket_url(&self) -> String {
        format!("ws://127.0.0.1:{}{WEBSOCKET_PATH}", self.port)
    }
}

impl Drop for Server {
    fn drop(&mut self) {
        self.shutdown.store(true, Ordering::Release);
        if let Some(join) = self.join.take() {
            let _ = join.join();
        }
    }
}

fn serve(
    listener: TcpListener,
    bridge: Sender<BridgeRequest>,
    shutdown: Arc<AtomicBool>,
) -> io::Result<()> {
    while !shutdown.load(Ordering::Acquire) {
        match listener.accept() {
            Ok((stream, address)) => {
                if !address.ip().is_loopback() {
                    continue;
                }
                if let Err(error) =
                    handle_connection(stream, listener.local_addr()?.port(), &bridge)
                {
                    eprintln!("native-dom-control CDP connection closed: {error}");
                }
            }
            Err(error) if error.kind() == io::ErrorKind::WouldBlock => {
                thread::sleep(Duration::from_millis(2));
            }
            Err(error) => return Err(error),
        }
    }
    Ok(())
}

fn handle_connection(
    mut stream: TcpStream,
    port: u16,
    bridge: &Sender<BridgeRequest>,
) -> io::Result<()> {
    stream.set_nonblocking(false)?;
    stream.set_read_timeout(Some(Duration::from_secs(30)))?;
    stream.set_write_timeout(Some(Duration::from_secs(10)))?;
    let header = read_http_header(&mut stream)?;
    let text = std::str::from_utf8(&header)
        .map_err(|_| io::Error::new(io::ErrorKind::InvalidData, "HTTP header is not UTF-8"))?;
    let mut lines = text.split("\r\n");
    let request_line = lines.next().unwrap_or_default();
    let mut request_fields = request_line.split_whitespace();
    let method = request_fields.next().unwrap_or_default();
    let path = request_fields.next().unwrap_or_default();
    if method != "GET" {
        return write_http(
            &mut stream,
            "405 Method Not Allowed",
            "text/plain",
            b"GET only",
        );
    }
    let headers = lines
        .filter_map(|line| line.split_once(':'))
        .map(|(name, value)| (name.trim().to_ascii_lowercase(), value.trim().to_owned()))
        .collect::<BTreeMap<_, _>>();
    match path {
        "/json/version" => {
            let body = serde_json::to_vec(&json!({
                "Browser":"MiniCon Surf native-dom-control/0.0.2",
                "Protocol-Version":"1.3",
                "webSocketDebuggerUrl":format!("ws://127.0.0.1:{port}{WEBSOCKET_PATH}"),
            }))?;
            write_http(&mut stream, "200 OK", "application/json", &body)
        }
        "/json" | "/json/list" => {
            let targets = native(bridge, "target.list", json!({}))
                .map_err(|_| io::Error::other("native target discovery failed"))?;
            let entries = target_entries(&targets)
                .into_iter()
                .map(|(id, label)| {
                    json!({
                        "id":id,
                        "type":"page",
                        "title":label,
                        "url":format!("minicon-surf://court/{label}"),
                        "webSocketDebuggerUrl":format!("ws://127.0.0.1:{port}{WEBSOCKET_PATH}"),
                    })
                })
                .collect::<Vec<_>>();
            write_http(
                &mut stream,
                "200 OK",
                "application/json",
                &serde_json::to_vec(&entries)?,
            )
        }
        WEBSOCKET_PATH => {
            upgrade_websocket(&mut stream, &headers)?;
            websocket_loop(stream, bridge)
        }
        _ => write_http(&mut stream, "404 Not Found", "text/plain", b"not found"),
    }
}

fn target_entries(list: &Value) -> Vec<(String, String)> {
    list["targets"]
        .as_array()
        .into_iter()
        .flatten()
        .filter_map(|target| {
            Some((
                target["target"].as_str()?.to_owned(),
                target["fixture"].as_str().unwrap_or("document").to_owned(),
            ))
        })
        .collect()
}

fn read_http_header(stream: &mut TcpStream) -> io::Result<Vec<u8>> {
    let mut header = Vec::new();
    let mut byte = [0_u8; 1];
    while header.len() < MAX_HTTP_HEADER_BYTES {
        stream.read_exact(&mut byte)?;
        header.push(byte[0]);
        if header.ends_with(b"\r\n\r\n") {
            return Ok(header);
        }
    }
    Err(io::Error::new(
        io::ErrorKind::InvalidData,
        "HTTP header exceeds limit",
    ))
}

fn write_http(
    stream: &mut TcpStream,
    status: &str,
    content_type: &str,
    body: &[u8],
) -> io::Result<()> {
    write!(
        stream,
        "HTTP/1.1 {status}\r\nContent-Type: {content_type}\r\nContent-Length: {}\r\nConnection: close\r\n\r\n",
        body.len()
    )?;
    stream.write_all(body)
}

fn upgrade_websocket(stream: &mut TcpStream, headers: &BTreeMap<String, String>) -> io::Result<()> {
    let key = headers
        .get("sec-websocket-key")
        .ok_or_else(|| io::Error::new(io::ErrorKind::InvalidData, "WebSocket key missing"))?;
    if !headers
        .get("upgrade")
        .is_some_and(|value| value.eq_ignore_ascii_case("websocket"))
    {
        return Err(io::Error::new(
            io::ErrorKind::InvalidData,
            "WebSocket upgrade missing",
        ));
    }
    let accept = STANDARD.encode(Sha1::digest(format!("{key}{WEBSOCKET_GUID}").as_bytes()));
    write!(
        stream,
        "HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Accept: {accept}\r\n\r\n"
    )
}

#[derive(Default)]
struct ConnectionState {
    next_session: u64,
    sessions: BTreeMap<String, SessionState>,
    /// Adapter-scoped `Page.FrameId` per native frame id, stable for the
    /// connection; never the native id.
    frame_ids: BTreeMap<String, String>,
    next_frame: u64,
    /// Events a method queues to be written before its response (a
    /// flattened attach announces its session first, as the protocol does).
    pre_events: Vec<Value>,
    /// Events queued by a method and written after its response.
    events: Vec<Value>,
}

impl ConnectionState {
    fn frame_id(&mut self, native: &str) -> String {
        if let Some(id) = self.frame_ids.get(native) {
            return id.clone();
        }
        self.next_frame += 1;
        let id = format!("cdp_frame_{}", self.next_frame);
        self.frame_ids.insert(native.to_owned(), id.clone());
        id
    }
}

struct SessionState {
    /// Native target name; the host re-resolves it on every command.
    target: String,
    /// Host-side adapter record; released with the session.
    adapter: String,
    nodes: BTreeMap<u64, Value>,
    objects: BTreeMap<String, Value>,
}

fn websocket_loop(mut stream: TcpStream, bridge: &Sender<BridgeRequest>) -> io::Result<()> {
    let mut connection = ConnectionState::default();
    let outcome = (|| loop {
        match read_websocket_frame(&mut stream)? {
            Frame::Close => return Ok(()),
            Frame::Ping(payload) => write_websocket_frame(&mut stream, 0xA, &payload)?,
            Frame::Text(payload) => {
                let response = match serde_json::from_slice::<Value>(&payload) {
                    Ok(request) => dispatch(request, bridge, &mut connection),
                    Err(_) => cdp_error(Value::Null, -32700, "Parse error"),
                };
                for event in connection.pre_events.drain(..) {
                    write_websocket_frame(&mut stream, 0x1, &serde_json::to_vec(&event)?)?;
                }
                write_websocket_frame(&mut stream, 0x1, &serde_json::to_vec(&response)?)?;
                for event in connection.events.drain(..) {
                    write_websocket_frame(&mut stream, 0x1, &serde_json::to_vec(&event)?)?;
                }
            }
        }
    })();
    // A connection that ends for any reason releases every adapter it held.
    for session in connection.sessions.values() {
        let _ = native(bridge, "adapter.detach", json!({"adapter":session.adapter}));
    }
    outcome
}

enum Frame {
    Text(Vec<u8>),
    Ping(Vec<u8>),
    Close,
}

fn read_websocket_frame(stream: &mut TcpStream) -> io::Result<Frame> {
    let mut header = [0_u8; 2];
    stream.read_exact(&mut header)?;
    if header[0] & 0x80 == 0 {
        return Err(io::Error::new(
            io::ErrorKind::InvalidData,
            "fragmented WebSocket frames are unsupported",
        ));
    }
    let opcode = header[0] & 0x0f;
    if header[1] & 0x80 == 0 {
        return Err(io::Error::new(
            io::ErrorKind::InvalidData,
            "client WebSocket frame is not masked",
        ));
    }
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
    stream.read_exact(&mut mask)?;
    let mut payload = vec![0_u8; length as usize];
    stream.read_exact(&mut payload)?;
    for (index, byte) in payload.iter_mut().enumerate() {
        *byte ^= mask[index % 4];
    }
    match opcode {
        0x1 => Ok(Frame::Text(payload)),
        0x8 => Ok(Frame::Close),
        0x9 => Ok(Frame::Ping(payload)),
        _ => Err(io::Error::new(
            io::ErrorKind::InvalidData,
            "unsupported WebSocket opcode",
        )),
    }
}

fn write_websocket_frame(stream: &mut TcpStream, opcode: u8, payload: &[u8]) -> io::Result<()> {
    stream.write_all(&[0x80 | opcode])?;
    if payload.len() < 126 {
        stream.write_all(&[payload.len() as u8])?;
    } else if payload.len() <= u16::MAX as usize {
        stream.write_all(&[126])?;
        stream.write_all(&(payload.len() as u16).to_be_bytes())?;
    } else {
        stream.write_all(&[127])?;
        stream.write_all(&(payload.len() as u64).to_be_bytes())?;
    }
    stream.write_all(payload)
}

type CdpResult = Result<Value, (i64, &'static str)>;

fn target_info(id: &str, label: &str, attached: bool) -> Value {
    json!({
        "targetId":id,
        "type":"page",
        "title":label,
        "url":format!("minicon-surf://court/{label}"),
        "attached":attached,
        "canAccessOpener":false,
    })
}

fn dispatch(
    request: Value,
    bridge: &Sender<BridgeRequest>,
    connection: &mut ConnectionState,
) -> Value {
    let id = request.get("id").cloned().unwrap_or(Value::Null);
    let Some(method) = request.get("method").and_then(Value::as_str) else {
        return cdp_error(id, -32600, "Invalid Request");
    };
    let params = request.get("params").cloned().unwrap_or_else(|| json!({}));
    let session_id = request.get("sessionId").and_then(Value::as_str);
    if std::env::var_os("MINICON_SURF_CDP_TRACE").is_some() {
        // Diagnostics only: method names, never parameters.
        eprintln!(
            "cdp-trace method={method} session={}",
            session_id.unwrap_or("-")
        );
    }
    let result = match method {
        "Target.getBrowserContexts" => Ok(json!({"browserContextIds":[]})),
        "Browser.getVersion" => Ok(json!({
            "protocolVersion":"1.3",
            "product":"MiniCon Surf native-dom-control/0.0.2",
            "revision":"control-0.0.1",
            "userAgent":"MiniCon Surf native-dom-control",
            "jsVersion":"QuickJS"
        })),
        "Target.setDiscoverTargets" => {
            // Existing targets are announced before the response, which is
            // the order clients build their initial target list from.
            if params.get("discover").and_then(Value::as_bool) == Some(true) {
                match native(bridge, "target.list", json!({})) {
                    Ok(list) => {
                        for (target_id, label) in target_entries(&list) {
                            connection.pre_events.push(json!({
                                "method":"Target.targetCreated",
                                "params":{"targetInfo":target_info(&target_id, &label, false)}
                            }));
                        }
                        Ok(json!({}))
                    }
                    Err(error) => Err(error),
                }
            } else {
                Ok(json!({}))
            }
        }
        "Target.setAutoAttach" => {
            // Browser-level auto-attach replays live targets as flattened
            // sessions announced before the response; each is an adapter
            // record in the host, so adapter counts equal the sessions a
            // client holds. Session-level calls are acknowledged only.
            if session_id.is_none()
                && params.get("autoAttach").and_then(Value::as_bool) == Some(true)
            {
                match native(bridge, "target.list", json!({})) {
                    Ok(list) => {
                        for (target_id, label) in target_entries(&list) {
                            if connection
                                .sessions
                                .values()
                                .any(|session| session.target == target_id)
                            {
                                continue;
                            }
                            let Ok(cdp_session) = attach(bridge, connection, &target_id) else {
                                continue;
                            };
                            connection.pre_events.push(json!({
                                "method":"Target.attachedToTarget",
                                "params":{
                                    "sessionId":cdp_session,
                                    "targetInfo":target_info(&target_id, &label, true),
                                    "waitingForDebugger":false
                                }
                            }));
                        }
                        Ok(json!({}))
                    }
                    Err(error) => Err(error),
                }
            } else {
                Ok(json!({}))
            }
        }
        "Target.getTargets" => target_get_targets(bridge, connection),
        "Target.attachToTarget" => target_attach(&params, bridge, connection),
        "Target.detachFromTarget" => target_detach(&params, bridge, connection),
        "Page.getFrameTree" => page_get_frame_tree(session_id, bridge, connection),
        "Page.navigate" => page_navigate(session_id, &params, bridge, connection),
        "Page.reload" => page_reload(session_id, &params, bridge, connection),
        "DOM.getDocument" => dom_get_document(session_id, bridge, connection),
        "DOM.querySelector" => dom_query_selector(session_id, &params, bridge, connection),
        "DOM.resolveNode" => dom_resolve_node(session_id, &params, bridge, connection),
        "Runtime.callFunctionOn" => {
            runtime_call_function_on(session_id, &params, bridge, connection)
        }
        _ => Err((-32601, "Method not found")),
    };
    // Flattened sessions route responses by their session id; echo it.
    let mut response = match result {
        Ok(result) => json!({"id":id,"result":result}),
        Err((code, message)) => cdp_error(id, code, message),
    };
    if let Some(session) = session_id {
        response["sessionId"] = json!(session);
    }
    response
}

fn target_get_targets(
    bridge: &Sender<BridgeRequest>,
    connection: &mut ConnectionState,
) -> CdpResult {
    let list = native(bridge, "target.list", json!({}))?;
    let target_infos = target_entries(&list)
        .into_iter()
        .map(|(id, label)| {
            let attached = connection
                .sessions
                .values()
                .any(|session| session.target == id);
            target_info(&id, &label, attached)
        })
        .collect::<Vec<_>>();
    Ok(json!({"targetInfos":target_infos}))
}

/// Register an adapter in the host and open a flattened session for it.
fn attach(
    bridge: &Sender<BridgeRequest>,
    connection: &mut ConnectionState,
    target: &str,
) -> Result<String, (i64, &'static str)> {
    let registered = native(
        bridge,
        "adapter.attach",
        json!({"target":target,"kind":"cdp"}),
    )
    .map_err(|_| (-32000, "target does not exist"))?;
    let adapter = registered["adapter"]
        .as_str()
        .ok_or((-32603, "adapter registration is malformed"))?
        .to_owned();
    connection.next_session += 1;
    let session_id = format!("cdp_session_{}", connection.next_session);
    connection.sessions.insert(
        session_id.clone(),
        SessionState {
            target: target.to_owned(),
            adapter,
            nodes: BTreeMap::new(),
            objects: BTreeMap::new(),
        },
    );
    Ok(session_id)
}

fn target_attach(
    params: &Value,
    bridge: &Sender<BridgeRequest>,
    connection: &mut ConnectionState,
) -> CdpResult {
    if params.get("flatten").and_then(Value::as_bool) != Some(true) {
        return Err((-32602, "flatten=true is required"));
    }
    let target = params
        .get("targetId")
        .and_then(Value::as_str)
        .ok_or((-32602, "targetId is required"))?;
    let list = native(bridge, "target.list", json!({}))?;
    let label = target_entries(&list)
        .into_iter()
        .find(|(id, _)| id == target)
        .map(|(_, label)| label)
        .ok_or((-32000, "target does not exist"))?;
    let session_id = attach(bridge, connection, target)?;
    // The protocol announces a flattened session before answering the
    // attach; clients (puppeteer among them) build the session from the event.
    connection.pre_events.push(json!({
        "method":"Target.attachedToTarget",
        "params":{
            "sessionId":session_id,
            "targetInfo":target_info(target, &label, true),
            "waitingForDebugger":false
        }
    }));
    Ok(json!({"sessionId":session_id}))
}

fn target_detach(
    params: &Value,
    bridge: &Sender<BridgeRequest>,
    connection: &mut ConnectionState,
) -> CdpResult {
    let session_id = params
        .get("sessionId")
        .and_then(Value::as_str)
        .ok_or((-32602, "sessionId is required"))?;
    let Some(session) = connection.sessions.remove(session_id) else {
        return Err((-32000, "session does not exist"));
    };
    // The host may already have detached it at a teardown; both are fine.
    let _ = native(bridge, "adapter.detach", json!({"adapter":session.adapter}));
    connection.events.push(json!({
        "method":"Target.detachedFromTarget",
        "params":{"sessionId":session_id,"targetId":session.target}
    }));
    Ok(json!({}))
}

/// The session's target for one command, or a typed detachment when the
/// host tore the target down while the session was still attached.
fn live_target(
    session_id: Option<&str>,
    bridge: &Sender<BridgeRequest>,
    connection: &mut ConnectionState,
) -> Result<String, (i64, &'static str)> {
    let id = session_id.ok_or((-32602, "sessionId is required"))?;
    let (target, adapter) = {
        let session = connection
            .sessions
            .get(id)
            .ok_or((-32000, "session does not exist"))?;
        (session.target.clone(), session.adapter.clone())
    };
    match native(bridge, "adapter.inspect", json!({"adapter":adapter})) {
        Ok(_) => Ok(target),
        Err(_) => {
            connection.sessions.remove(id);
            Err((-32000, "target closed; adapter detached"))
        }
    }
}

fn snapshot(bridge: &Sender<BridgeRequest>, target: &str) -> CdpResult {
    native(
        bridge,
        "target.snapshot",
        json!({"target":target,"format":"semantic","max_bytes":65536,"max_nodes":128}),
    )
}

/// `Page.navigate` maps onto `target.navigate`. The frame id is projected the
/// way the frame tree projects it; there is no `loaderId` and no
/// `frameNavigated` event, because this version projects no events.
fn page_navigate(
    session_id: Option<&str>,
    params: &Value,
    bridge: &Sender<BridgeRequest>,
    connection: &mut ConnectionState,
) -> CdpResult {
    let target = live_target(session_id, bridge, connection)?;
    let url = params
        .get("url")
        .and_then(Value::as_str)
        .ok_or((-32602, "url is required"))?;
    // Arguments this host cannot honour are refused, never ignored.
    for unsupported in ["referrer", "transitionType", "frameId", "referrerPolicy"] {
        if params.get(unsupported).is_some() {
            return Err((-32602, "this host supports only url"));
        }
    }
    let navigated = native(
        bridge,
        "target.navigate",
        json!({"target":target,"url":url}),
    )?;
    Ok(navigation_result(&navigated, connection))
}

/// `Page.reload` maps onto `target.reload`. `ignoreCache` and
/// `scriptToEvaluateOnLoad` have no native meaning and are refused typed
/// rather than silently ignored.
fn page_reload(
    session_id: Option<&str>,
    params: &Value,
    bridge: &Sender<BridgeRequest>,
    connection: &mut ConnectionState,
) -> CdpResult {
    let target = live_target(session_id, bridge, connection)?;
    for unsupported in [
        "ignoreCache",
        "scriptToEvaluateOnLoad",
        "scriptToEvaluateOnLoadIdentifier",
    ] {
        if params.get(unsupported).is_some() {
            return Err((
                -32602,
                "this host supports neither cache control nor load scripts",
            ));
        }
    }
    let reloaded = native(bridge, "target.reload", json!({"target":target}))?;
    Ok(navigation_result(&reloaded, connection))
}

/// The bounded projection of a navigation result: the adapter's frame id and
/// nothing the native result does not already say.
fn navigation_result(native_result: &Value, connection: &mut ConnectionState) -> Value {
    let frame = connection.frame_id(native_result["frame"].as_str().unwrap_or(""));
    json!({"frameId": frame, "loaderId": ""})
}

fn page_get_frame_tree(
    session_id: Option<&str>,
    bridge: &Sender<BridgeRequest>,
    connection: &mut ConnectionState,
) -> CdpResult {
    let target = live_target(session_id, bridge, connection)?;
    let inspect = native(bridge, "target.inspect", json!({"target":target}))?;
    let frames = inspect["frames"].as_array().cloned().unwrap_or_default();
    let Some(main) = frames.first() else {
        return Err((-32000, "target has no frames"));
    };
    let describe = |connection: &mut ConnectionState, frame: &Value| -> Value {
        let id = connection.frame_id(frame["frame"].as_str().unwrap_or(""));
        // Each frame reports its own URL. `frames[].url` is optional, so a
        // frame without one falls back to the target's, and a fixture target
        // to its court address, as before.
        let url = frame["url"]
            .as_str()
            .or_else(|| inspect["url"].as_str())
            .map_or_else(
                || {
                    format!(
                        "minicon-surf://court/{}",
                        inspect["fixture"].as_str().unwrap_or("document")
                    )
                },
                str::to_owned,
            );
        let mut description =
            json!({"id":id,"loaderId":"","url":url,"securityOrigin":"","mimeType":"text/html"});
        if let Some(parent) = frame["parent"].as_str() {
            description["parentId"] = json!(connection.frame_id(parent));
        }
        description
    };
    let main_description = describe(connection, main);
    let children = frames
        .iter()
        .skip(1)
        .map(|frame| json!({"frame":describe(connection, frame),"childFrames":[]}))
        .collect::<Vec<_>>();
    Ok(json!({"frameTree":{"frame":main_description,"childFrames":children}}))
}

fn dom_get_document(
    session_id: Option<&str>,
    bridge: &Sender<BridgeRequest>,
    connection: &mut ConnectionState,
) -> CdpResult {
    let target = live_target(session_id, bridge, connection)?;
    let snapshot = snapshot(bridge, &target)?;
    let session = session_mut(session_id, connection)?;
    session.nodes.clear();
    session.objects.clear();
    let count = snapshot["nodes"].as_array().map_or(0, Vec::len);
    Ok(
        json!({"root":{"nodeId":1,"backendNodeId":1,"nodeType":9,"nodeName":"#document","localName":"","nodeValue":"","childNodeCount":count}}),
    )
}

fn dom_query_selector(
    session_id: Option<&str>,
    params: &Value,
    bridge: &Sender<BridgeRequest>,
    connection: &mut ConnectionState,
) -> CdpResult {
    let target = live_target(session_id, bridge, connection)?;
    if params.get("nodeId").and_then(Value::as_u64) != Some(1) {
        return Err((-32602, "only document-rooted queries are qualified"));
    }
    let selector = params
        .get("selector")
        .and_then(Value::as_str)
        .ok_or((-32602, "selector is required"))?;
    let snapshot = snapshot(bridge, &target)?;
    let nodes = snapshot["nodes"]
        .as_array()
        .into_iter()
        .flatten()
        .collect::<Vec<_>>();
    let reference = if let Some(dom_id) = selector.strip_prefix('#') {
        nodes
            .iter()
            .find(|node| node["dom_id"].as_str() == Some(dom_id))
    } else if selector == "button" {
        nodes.iter().find(|node| node["role"] == "button")
    } else if selector == "a" {
        nodes.iter().find(|node| node["role"] == "link")
    } else {
        return Err((-32602, "only button, a and #id selectors are qualified"));
    }
    .map(|node| node["reference"].clone())
    .ok_or((-32000, "node does not exist"))?;
    let session = session_mut(session_id, connection)?;
    let node_id = session.nodes.len() as u64 + 2;
    session.nodes.insert(node_id, reference);
    Ok(json!({"nodeId":node_id}))
}

fn dom_resolve_node(
    session_id: Option<&str>,
    params: &Value,
    bridge: &Sender<BridgeRequest>,
    connection: &mut ConnectionState,
) -> CdpResult {
    live_target(session_id, bridge, connection)?;
    let node_id = params
        .get("nodeId")
        .and_then(Value::as_u64)
        .ok_or((-32602, "nodeId is required"))?;
    let session = session_mut(session_id, connection)?;
    let reference = session
        .nodes
        .get(&node_id)
        .cloned()
        .ok_or((-32000, "node does not exist"))?;
    let object_id = format!("object_{node_id}");
    session.objects.insert(object_id.clone(), reference);
    Ok(
        json!({"object":{"type":"object","subtype":"node","className":"HTMLElement","description":"element","objectId":object_id}}),
    )
}

fn runtime_call_function_on(
    session_id: Option<&str>,
    params: &Value,
    bridge: &Sender<BridgeRequest>,
    connection: &mut ConnectionState,
) -> CdpResult {
    let target = live_target(session_id, bridge, connection)?;
    let object_id = params
        .get("objectId")
        .and_then(Value::as_str)
        .ok_or((-32602, "objectId is required"))?;
    if params.get("functionDeclaration").and_then(Value::as_str)
        != Some("function(){this.click();}")
    {
        return Err((-32602, "only the qualified click function is supported"));
    }
    let reference = session_mut(session_id, connection)?
        .objects
        .get(object_id)
        .cloned()
        .ok_or((-32000, "remote object does not exist"))?;
    native(
        bridge,
        "target.act",
        json!({"target":target,"reference":reference,"action":{"kind":"click"}}),
    )?;
    Ok(json!({"result":{"type":"undefined"}}))
}

fn session_mut<'a>(
    session_id: Option<&str>,
    connection: &'a mut ConnectionState,
) -> Result<&'a mut SessionState, (i64, &'static str)> {
    let id = session_id.ok_or((-32602, "sessionId is required"))?;
    connection
        .sessions
        .get_mut(id)
        .ok_or((-32000, "session does not exist"))
}

fn native(bridge: &Sender<BridgeRequest>, operation: &str, arguments: Value) -> CdpResult {
    let (reply, receiver) = mpsc::channel::<Result<Value, String>>();
    bridge
        .send(BridgeRequest {
            operation: operation.to_owned(),
            arguments,
            reply,
        })
        .map_err(|_| (-32603, "control host is gone"))?;
    match receiver.recv_timeout(NATIVE_REPLY_TIMEOUT) {
        Ok(Ok(result)) => Ok(result),
        Ok(Err(_)) => Err((-32000, "native control operation failed")),
        Err(_) => Err((-32603, "control host did not answer")),
    }
}

fn cdp_error(id: Value, code: i64, message: &str) -> Value {
    json!({"id":id,"error":{"code":code,"message":message}})
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn websocket_accept_matches_rfc_example() {
        let accept = STANDARD.encode(Sha1::digest(
            format!("dGhlIHNhbXBsZSBub25jZQ=={WEBSOCKET_GUID}").as_bytes(),
        ));
        assert_eq!(accept, "s3pPLMBiTxaQ9kYGzzhZRbK+xOo=");
    }

    #[test]
    fn frame_ids_are_adapter_scoped_and_stable_per_connection() {
        let mut connection = ConnectionState::default();
        let first = connection.frame_id("frame_7");
        assert_eq!(first, "cdp_frame_1");
        assert_ne!(first, "frame_7");
        assert_eq!(connection.frame_id("frame_7"), first);
        assert_eq!(connection.frame_id("frame_9"), "cdp_frame_2");
        let mut other = ConnectionState::default();
        assert_eq!(
            other.frame_id("frame_9"),
            "cdp_frame_1",
            "another connection maps independently"
        );
    }

    #[test]
    fn unqualified_methods_are_explicit_losses() {
        let (bridge, _receiver) = mpsc::channel();
        let mut connection = ConnectionState::default();
        for method in [
            "Page.enable",
            "Runtime.enable",
            "Network.enable",
            // History stays unmapped: projecting it would need adapter-scoped
            // entry ids, and the host is the only history authority.
            "Page.getNavigationHistory",
            "Page.navigateToHistoryEntry",
        ] {
            let response = dispatch(
                json!({"id":1,"method":method,"params":{}}),
                &bridge,
                &mut connection,
            );
            assert_eq!(response["error"]["code"], -32601, "{method}");
        }
        // The two navigation methods are mapped now, so they are no longer
        // "method not found"; without a session they fail on that instead.
        for method in ["Page.navigate", "Page.reload"] {
            let response = dispatch(
                json!({"id":1,"method":method,"params":{"url":"http://127.0.0.1/a"}}),
                &bridge,
                &mut connection,
            );
            assert_ne!(response["error"]["code"], -32601, "{method} is mapped");
            assert_eq!(
                response["error"]["code"], -32602,
                "{method} needs a session"
            );
        }
        // An argument this host cannot honour is refused, never ignored.
        let response = dispatch(
            json!({"id":1,"method":"Page.reload","params":{"ignoreCache":true}}),
            &bridge,
            &mut connection,
        );
        assert_eq!(response["error"]["code"], -32602, "ignoreCache is refused");
    }
}
