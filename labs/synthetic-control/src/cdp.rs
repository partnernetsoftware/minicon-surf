//! Minimal loopback CDP edge for proving shared synthetic target authority.

use crate::{ControlState, Request};
use base64::Engine;
use base64::engine::general_purpose::STANDARD;
use serde_json::{Value, json};
use sha1::{Digest, Sha1};
use std::collections::BTreeMap;
use std::io::{self, Read, Write};
use std::net::{Ipv4Addr, SocketAddrV4, TcpListener, TcpStream};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex};
use std::thread::{self, JoinHandle};
use std::time::Duration;

const MAX_HTTP_HEADER_BYTES: usize = 16_384;
const MAX_CDP_MESSAGE_BYTES: usize = 65_536;
const WEBSOCKET_GUID: &str = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11";

pub struct Server {
    port: u16,
    shutdown: Arc<AtomicBool>,
    join: Option<JoinHandle<io::Result<()>>>,
}

impl Server {
    pub fn start(port: u16, state: Arc<Mutex<ControlState>>) -> io::Result<Self> {
        let listener = TcpListener::bind(SocketAddrV4::new(Ipv4Addr::LOCALHOST, port))?;
        let port = listener.local_addr()?.port();
        listener.set_nonblocking(true)?;
        let shutdown = Arc::new(AtomicBool::new(false));
        let thread_shutdown = shutdown.clone();
        let join = thread::spawn(move || serve(listener, state, thread_shutdown));
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
        format!(
            "ws://127.0.0.1:{}/devtools/browser/synthetic-control",
            self.port
        )
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
    state: Arc<Mutex<ControlState>>,
    shutdown: Arc<AtomicBool>,
) -> io::Result<()> {
    while !shutdown.load(Ordering::Acquire) {
        match listener.accept() {
            Ok((stream, address)) => {
                if !address.ip().is_loopback() {
                    continue;
                }
                if let Err(error) = handle_connection(stream, listener.local_addr()?.port(), &state)
                {
                    // A malformed or disconnected client is isolated to its connection.
                    eprintln!("synthetic CDP connection closed: {error}");
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
    state: &Arc<Mutex<ControlState>>,
) -> io::Result<()> {
    stream.set_nonblocking(false)?;
    stream.set_read_timeout(Some(Duration::from_secs(10)))?;
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
                "Browser":"MiniCon Surf synthetic-control/0.0.1",
                "Protocol-Version":"1.3",
                "webSocketDebuggerUrl":format!("ws://127.0.0.1:{port}/devtools/browser/synthetic-control"),
            }))?;
            write_http(&mut stream, "200 OK", "application/json", &body)
        }
        "/json" | "/json/list" => {
            let mut request = 0;
            let targets = native_call(state, &mut request, "target.list", json!({}))
                .map_err(|_| io::Error::other("native target discovery failed"))?;
            let entries = targets["targets"]
                .as_array()
                .into_iter()
                .flatten()
                .map(|target| {
                    let id = target["target"].as_str().unwrap_or("target_invalid");
                    json!({
                        "id":id,
                        "type":"page",
                        "title":"Synthetic Agent Court",
                        "url":"minicon-surf://synthetic/semantic-court",
                        "webSocketDebuggerUrl":format!("ws://127.0.0.1:{port}/devtools/browser/synthetic-control"),
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
        "/devtools/browser/synthetic-control" => {
            upgrade_websocket(&mut stream, &headers)?;
            websocket_loop(stream, state)
        }
        _ => write_http(&mut stream, "404 Not Found", "text/plain", b"not found"),
    }
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
    next_native_request: u64,
    next_session: u64,
    sessions: BTreeMap<String, SessionState>,
}

#[derive(Default)]
struct SessionState {
    target: String,
    root_node: Option<u64>,
    nodes: BTreeMap<u64, Value>,
    objects: BTreeMap<String, Value>,
}

fn websocket_loop(mut stream: TcpStream, state: &Arc<Mutex<ControlState>>) -> io::Result<()> {
    let mut connection = ConnectionState::default();
    loop {
        match read_websocket_frame(&mut stream)? {
            Frame::Close => return Ok(()),
            Frame::Ping(payload) => write_websocket_frame(&mut stream, 0xA, &payload)?,
            Frame::Text(payload) => {
                let response = match serde_json::from_slice::<Value>(&payload) {
                    Ok(request) => dispatch(request, state, &mut connection),
                    Err(_) => cdp_error(Value::Null, -32700, "Parse error"),
                };
                write_websocket_frame(&mut stream, 0x1, &serde_json::to_vec(&response)?)?;
            }
        }
    }
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
    let masked = header[1] & 0x80 != 0;
    if !masked {
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

fn dispatch(
    request: Value,
    state: &Arc<Mutex<ControlState>>,
    connection: &mut ConnectionState,
) -> Value {
    let id = request.get("id").cloned().unwrap_or(Value::Null);
    let Some(method) = request.get("method").and_then(Value::as_str) else {
        return cdp_error(id, -32600, "Invalid Request");
    };
    let params = request.get("params").cloned().unwrap_or_else(|| json!({}));
    let session_id = request.get("sessionId").and_then(Value::as_str);
    let result = match method {
        "Target.getTargets" => target_get_targets(state, connection),
        "Target.attachToTarget" => target_attach(&params, state, connection),
        "Target.detachFromTarget" => target_detach(&params, connection),
        "DOM.getDocument" => dom_get_document(session_id, state, connection),
        "DOM.querySelector" => dom_query_selector(session_id, &params, state, connection),
        "DOM.resolveNode" => dom_resolve_node(session_id, &params, connection),
        "Runtime.callFunctionOn" => {
            runtime_call_function_on(session_id, &params, state, connection)
        }
        _ => Err((-32601, "Method not found")),
    };
    match result {
        Ok(result) => json!({"id":id,"result":result}),
        Err((code, message)) => cdp_error(id, code, message),
    }
}

type CdpResult = Result<Value, (i64, &'static str)>;

fn target_get_targets(
    state: &Arc<Mutex<ControlState>>,
    connection: &mut ConnectionState,
) -> CdpResult {
    let result = native(state, connection, "target.list", json!({}))?;
    let target_infos = result["targets"]
        .as_array()
        .into_iter()
        .flatten()
        .filter_map(|target| target["target"].as_str())
        .map(|id| {
            json!({
                "targetId":id,
                "type":"page",
                "title":"Synthetic Agent Court",
                "url":"minicon-surf://synthetic/semantic-court",
                "attached":connection.sessions.values().any(|session| session.target == id),
                "canAccessOpener":false,
            })
        })
        .collect::<Vec<_>>();
    Ok(json!({"targetInfos":target_infos}))
}

fn target_attach(
    params: &Value,
    state: &Arc<Mutex<ControlState>>,
    connection: &mut ConnectionState,
) -> CdpResult {
    if params.get("flatten").and_then(Value::as_bool) != Some(true) {
        return Err((-32602, "flatten=true is required"));
    }
    let target = params
        .get("targetId")
        .and_then(Value::as_str)
        .ok_or((-32602, "targetId is required"))?;
    let targets = native(state, connection, "target.list", json!({}))?;
    if !targets["targets"]
        .as_array()
        .into_iter()
        .flatten()
        .any(|entry| entry["target"].as_str() == Some(target))
    {
        return Err((-32000, "target does not exist"));
    }
    connection.next_session += 1;
    let session_id = format!("cdp_session_{}", connection.next_session);
    connection.sessions.insert(
        session_id.clone(),
        SessionState {
            target: target.to_owned(),
            ..SessionState::default()
        },
    );
    Ok(json!({"sessionId":session_id}))
}

fn target_detach(params: &Value, connection: &mut ConnectionState) -> CdpResult {
    let session_id = params
        .get("sessionId")
        .and_then(Value::as_str)
        .ok_or((-32602, "sessionId is required"))?;
    if connection.sessions.remove(session_id).is_none() {
        return Err((-32000, "session does not exist"));
    }
    Ok(json!({}))
}

fn dom_get_document(
    session_id: Option<&str>,
    state: &Arc<Mutex<ControlState>>,
    connection: &mut ConnectionState,
) -> CdpResult {
    let target = session(session_id, connection)?.target.clone();
    let snapshot = native(
        state,
        connection,
        "target.snapshot",
        json!({
            "target":target,"format":"semantic","max_bytes":65536,"max_nodes":128
        }),
    )?;
    let session = session_mut(session_id, connection)?;
    session.root_node = Some(1);
    session.nodes.clear();
    session.objects.clear();
    for (index, node) in snapshot["nodes"]
        .as_array()
        .into_iter()
        .flatten()
        .enumerate()
    {
        session
            .nodes
            .insert(index as u64 + 2, node["reference"].clone());
    }
    Ok(
        json!({"root":{"nodeId":1,"backendNodeId":1,"nodeType":9,"nodeName":"#document","localName":"","nodeValue":"","childNodeCount":snapshot["nodes"].as_array().map_or(0, Vec::len)}}),
    )
}

fn dom_query_selector(
    session_id: Option<&str>,
    params: &Value,
    state: &Arc<Mutex<ControlState>>,
    connection: &mut ConnectionState,
) -> CdpResult {
    let target = session(session_id, connection)?.target.clone();
    if params.get("nodeId").and_then(Value::as_u64) != Some(1)
        || params.get("selector").and_then(Value::as_str) != Some("button")
    {
        return Err((-32602, "only document button query is qualified"));
    }
    let snapshot = native(
        state,
        connection,
        "target.snapshot",
        json!({
            "target":target,"format":"semantic","max_bytes":65536,"max_nodes":128
        }),
    )?;
    let reference = snapshot["nodes"]
        .as_array()
        .into_iter()
        .flatten()
        .find(|node| node["role"] == "button")
        .map(|node| node["reference"].clone())
        .ok_or((-32000, "button does not exist"))?;
    let session = session_mut(session_id, connection)?;
    session.nodes.insert(2, reference);
    Ok(json!({"nodeId":2}))
}

fn dom_resolve_node(
    session_id: Option<&str>,
    params: &Value,
    connection: &mut ConnectionState,
) -> CdpResult {
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
        json!({"object":{"type":"object","subtype":"node","className":"HTMLButtonElement","description":"button","objectId":object_id}}),
    )
}

fn runtime_call_function_on(
    session_id: Option<&str>,
    params: &Value,
    state: &Arc<Mutex<ControlState>>,
    connection: &mut ConnectionState,
) -> CdpResult {
    let object_id = params
        .get("objectId")
        .and_then(Value::as_str)
        .ok_or((-32602, "objectId is required"))?;
    if params.get("functionDeclaration").and_then(Value::as_str)
        != Some("function(){this.click();}")
    {
        return Err((-32602, "only the qualified click function is supported"));
    }
    let session = session(session_id, connection)?;
    let target = session.target.clone();
    let reference = session
        .objects
        .get(object_id)
        .cloned()
        .ok_or((-32000, "remote object does not exist"))?;
    native(
        state,
        connection,
        "target.act",
        json!({"target":target,"reference":reference,"action":{"kind":"click"}}),
    )?;
    Ok(json!({"result":{"type":"undefined"}}))
}

fn session<'a>(
    session_id: Option<&str>,
    connection: &'a ConnectionState,
) -> Result<&'a SessionState, (i64, &'static str)> {
    let id = session_id.ok_or((-32602, "sessionId is required"))?;
    connection
        .sessions
        .get(id)
        .ok_or((-32000, "session does not exist"))
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

fn native(
    state: &Arc<Mutex<ControlState>>,
    connection: &mut ConnectionState,
    operation: &str,
    arguments: Value,
) -> CdpResult {
    native_call(
        state,
        &mut connection.next_native_request,
        operation,
        arguments,
    )
}

fn native_call(
    state: &Arc<Mutex<ControlState>>,
    next_request: &mut u64,
    operation: &str,
    arguments: Value,
) -> CdpResult {
    *next_request += 1;
    let response = state
        .lock()
        .map_err(|_| (-32603, "control state lock failed"))?
        .execute(Request {
            request_id: format!("req_cdp_{}", *next_request),
            deadline: Duration::from_millis(100),
            operation: operation.to_owned(),
            arguments,
        });
    response
        .into_outcome()
        .map_err(|_| (-32000, "native control operation failed"))
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
            b"dGhlIHNhbXBsZSBub25jZQ==258EAFA5-E914-47DA-95CA-C5AB0DC85B11",
        ));
        assert_eq!(accept, "s3pPLMBiTxaQ9kYGzzhZRbK+xOo=");
    }

    #[test]
    fn unsupported_method_is_explicit() {
        let state = Arc::new(Mutex::new(ControlState::default()));
        let mut connection = ConnectionState::default();
        let response = dispatch(
            json!({"id":7,"method":"Page.navigate","params":{}}),
            &state,
            &mut connection,
        );
        assert_eq!(response["error"]["code"], -32601);
    }
}
