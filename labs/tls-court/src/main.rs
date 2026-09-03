//! Standalone TLS candidate probe for the native route's HTTPS design.
//!
//! Never linked into the host. One binary shape, one TLS stack selected at
//! build time by a Cargo feature (none = plain TCP control). Driven over
//! stdio by `court.py` with one JSON command per line:
//!
//! - `configure`: load pinned roots (PEM files), server name, resumption
//!   entries (0 = disabled), minimum version; builds the stack's config.
//! - `open`: open `count` connections to 127.0.0.1:`port`, one GET each,
//!   keep them alive; answers with the negotiated facts per connection.
//! - `close`: close every live connection.
//! - `report`: live connections and libmalloc statistics.
//! - `trim`: `malloc_zone_pressure_relief` (diagnostic).
//! - `probe`: one connection attempt reported as facts or a typed refusal.
//! - `exit`.
//!
//! Only loopback addresses are ever dialled; the pinned roots are the
//! court's disposable fixtures, generated per run and deleted afterwards.

use std::io::{self, BufRead, Read, Write};
use std::net::{Ipv4Addr, SocketAddr, TcpStream};
use std::time::Duration;

use serde_json::{Value, json};

const IO_TIMEOUT: Duration = Duration::from_secs(3);
const MAX_RESPONSE_BYTES: usize = 64 * 1024;

#[repr(C)]
struct MallocStatistics {
    blocks_in_use: u32,
    size_in_use: usize,
    max_size_in_use: usize,
    size_allocated: usize,
}

unsafe extern "C" {
    fn malloc_zone_statistics(zone: *mut libc::c_void, stats: *mut MallocStatistics);
    fn malloc_zone_pressure_relief(zone: *mut libc::c_void, goal: usize) -> usize;
}

fn libmalloc() -> Value {
    let mut stats = MallocStatistics {
        blocks_in_use: 0,
        size_in_use: 0,
        max_size_in_use: 0,
        size_allocated: 0,
    };
    // SAFETY: a null zone sums every zone; the struct matches malloc_statistics_t.
    unsafe { malloc_zone_statistics(std::ptr::null_mut(), &mut stats) };
    json!({"blocks_in_use":stats.blocks_in_use,"size_in_use":stats.size_in_use,"size_allocated":stats.size_allocated})
}

fn trim() -> usize {
    // SAFETY: a null zone requests relief from every zone; a zero goal asks for all.
    unsafe { malloc_zone_pressure_relief(std::ptr::null_mut(), 0) }
}

fn pem_certificates(path: &str) -> Result<Vec<Vec<u8>>, String> {
    use base64::Engine as _;
    let text = std::fs::read_to_string(path).map_err(|e| format!("{path}: {e}"))?;
    let mut out = Vec::new();
    let mut body = String::new();
    let mut inside = false;
    for line in text.lines() {
        if line.starts_with("-----BEGIN CERTIFICATE-----") {
            inside = true;
            body.clear();
        } else if line.starts_with("-----END CERTIFICATE-----") {
            inside = false;
            let der = base64::engine::general_purpose::STANDARD
                .decode(body.trim())
                .map_err(|e| format!("{path}: {e}"))?;
            out.push(der);
        } else if inside {
            body.push_str(line.trim());
        }
    }
    if out.is_empty() {
        return Err(format!("{path}: no certificate"));
    }
    Ok(out)
}

/// A live connection of whichever stack was built.
trait Connection {
    fn stream(&mut self) -> &mut dyn ReadWrite;
    fn facts(&self) -> Value;
    fn shutdown(&mut self);
}

trait ReadWrite: Read + Write {}
impl<T: Read + Write> ReadWrite for T {}

fn tcp(port: u16) -> Result<TcpStream, String> {
    let address = SocketAddr::from((Ipv4Addr::LOCALHOST, port));
    let stream =
        TcpStream::connect_timeout(&address, IO_TIMEOUT).map_err(|e| format!("connect: {e}"))?;
    stream
        .set_read_timeout(Some(IO_TIMEOUT))
        .map_err(|e| e.to_string())?;
    stream
        .set_write_timeout(Some(IO_TIMEOUT))
        .map_err(|e| e.to_string())?;
    stream.set_nodelay(true).map_err(|e| e.to_string())?;
    Ok(stream)
}

/// One bounded HTTP/1.1 GET over an established connection.
fn get(stream: &mut dyn ReadWrite, host: &str, path: &str) -> Result<usize, String> {
    let request = format!(
        "GET {path} HTTP/1.1\r\nHost: {host}\r\nUser-Agent: minicon-surf-tls-probe\r\nConnection: keep-alive\r\n\r\n"
    );
    stream
        .write_all(request.as_bytes())
        .map_err(|e| format!("write: {e}"))?;
    stream.flush().map_err(|e| format!("flush: {e}"))?;
    let mut buffer = Vec::new();
    let mut chunk = [0u8; 4096];
    let head_end = loop {
        let n = stream.read(&mut chunk).map_err(|e| format!("read: {e}"))?;
        if n == 0 {
            return Err("closed before headers".into());
        }
        buffer.extend_from_slice(&chunk[..n]);
        if let Some(i) = buffer.windows(4).position(|w| w == b"\r\n\r\n") {
            break i + 4;
        }
        if buffer.len() > MAX_RESPONSE_BYTES {
            return Err("headers too large".into());
        }
    };
    let head = String::from_utf8_lossy(&buffer[..head_end]).to_string();
    let length = head
        .lines()
        .find_map(|l| {
            l.to_ascii_lowercase()
                .strip_prefix("content-length:")
                .map(|v| v.trim().parse::<usize>().unwrap_or(0))
        })
        .unwrap_or(0);
    if length > MAX_RESPONSE_BYTES {
        return Err("body too large".into());
    }
    while buffer.len() < head_end + length {
        let n = stream
            .read(&mut chunk)
            .map_err(|e| format!("read body: {e}"))?;
        if n == 0 {
            return Err("closed before body".into());
        }
        buffer.extend_from_slice(&chunk[..n]);
    }
    Ok(buffer.len())
}

// ----------------------------------------------------------------- plain

#[cfg(not(any(
    feature = "rustls-ring",
    feature = "rustls-aws-lc",
    feature = "secure-transport"
)))]
mod stack {
    use super::*;
    pub const NAME: &str = "plain-tcp";
    pub struct Config {
        pub server_name: String,
    }
    pub fn configure(
        _roots: &[Vec<u8>],
        server_name: &str,
        _resumption: usize,
        _min: &str,
    ) -> Result<Config, String> {
        Ok(Config {
            server_name: server_name.to_owned(),
        })
    }
    pub struct Plain(TcpStream);
    impl Connection for Plain {
        fn stream(&mut self) -> &mut dyn ReadWrite {
            &mut self.0
        }
        fn facts(&self) -> Value {
            json!({"tls":false})
        }
        fn shutdown(&mut self) {
            let _ = self.0.shutdown(std::net::Shutdown::Both);
        }
    }
    pub fn connect(
        config: &Config,
        port: u16,
        server_name: Option<&str>,
    ) -> Result<Box<dyn Connection>, String> {
        let _ = (server_name, &config.server_name);
        Ok(Box::new(Plain(tcp(port)?)))
    }
    pub fn sessions_cached(_config: &Config) -> Value {
        Value::Null
    }
}

// ---------------------------------------------------------------- rustls

#[cfg(any(feature = "rustls-ring", feature = "rustls-aws-lc"))]
mod stack {
    use super::*;
    use std::sync::Arc;

    #[cfg(feature = "rustls-ring")]
    pub const NAME: &str = "rustls-ring";
    #[cfg(feature = "rustls-aws-lc")]
    pub const NAME: &str = "rustls-aws-lc";
    #[cfg(feature = "rustls-aws-lc")]
    use rustls::crypto::aws_lc_rs as provider;
    #[cfg(feature = "rustls-ring")]
    use rustls::crypto::ring as provider;

    pub struct Config {
        pub client: Arc<rustls::ClientConfig>,
        pub server_name: String,
    }

    pub fn configure(
        roots: &[Vec<u8>],
        server_name: &str,
        resumption: usize,
        min: &str,
    ) -> Result<Config, String> {
        let mut store = rustls::RootCertStore::empty();
        for der in roots {
            store
                .add(rustls_pki_types::CertificateDer::from(der.clone()))
                .map_err(|e| format!("root: {e}"))?;
        }
        let versions: &[&'static rustls::SupportedProtocolVersion] = match min {
            "1.3" => &[&rustls::version::TLS13],
            _ => &[&rustls::version::TLS13, &rustls::version::TLS12],
        };
        let mut client =
            rustls::ClientConfig::builder_with_provider(Arc::new(provider::default_provider()))
                .with_protocol_versions(versions)
                .map_err(|e| format!("versions: {e}"))?
                .with_root_certificates(store)
                .with_no_client_auth();
        client.alpn_protocols = vec![b"http/1.1".to_vec()];
        client.resumption = if resumption == 0 {
            rustls::client::Resumption::disabled()
        } else {
            rustls::client::Resumption::in_memory_sessions(resumption)
        };
        Ok(Config {
            client: Arc::new(client),
            server_name: server_name.to_owned(),
        })
    }

    pub struct Tls(rustls::StreamOwned<rustls::ClientConnection, TcpStream>);

    impl Connection for Tls {
        fn stream(&mut self) -> &mut dyn ReadWrite {
            &mut self.0
        }
        fn facts(&self) -> Value {
            let conn = &self.0.conn;
            json!({
                "tls":true,
                "version":conn.protocol_version().map(|v| format!("{v:?}")),
                "cipher":conn.negotiated_cipher_suite().map(|s| format!("{:?}", s.suite())),
                "alpn":conn.alpn_protocol().map(|a| String::from_utf8_lossy(a).to_string()),
                "resumed":conn.handshake_kind().map(|k| matches!(k, rustls::HandshakeKind::Resumed)),
                "peer_certificates":conn.peer_certificates().map(|c| c.len()),
            })
        }
        fn shutdown(&mut self) {
            self.0.conn.send_close_notify();
            let _ = self.0.flush();
            let _ = self.0.sock.shutdown(std::net::Shutdown::Both);
        }
    }

    pub fn connect(
        config: &Config,
        port: u16,
        server_name: Option<&str>,
    ) -> Result<Box<dyn Connection>, String> {
        let name = server_name.unwrap_or(&config.server_name).to_owned();
        let name = rustls_pki_types::ServerName::try_from(name)
            .map_err(|e| format!("server name: {e}"))?;
        let conn = rustls::ClientConnection::new(config.client.clone(), name)
            .map_err(|e| format!("client: {e}"))?;
        let mut stream = rustls::StreamOwned::new(conn, tcp(port)?);
        // Drive the handshake now so a refusal is typed before any HTTP.
        stream
            .conn
            .complete_io(&mut stream.sock)
            .map_err(|e| format!("handshake: {e}"))?;
        Ok(Box::new(Tls(stream)))
    }

    pub fn sessions_cached(_config: &Config) -> Value {
        // rustls does not expose the in-memory store's size; the bound is the configured count.
        Value::Null
    }
}

// ----------------------------------------------------- SecureTransport

#[cfg(feature = "secure-transport")]
mod stack {
    use super::*;
    use security_framework::certificate::SecCertificate;
    use security_framework::secure_transport::{ClientBuilder, SslProtocol, SslStream};

    pub const NAME: &str = "secure-transport";

    pub struct Config {
        pub roots: Vec<SecCertificate>,
        pub server_name: String,
        pub tickets: bool,
        pub min: SslProtocol,
    }

    pub fn configure(
        roots: &[Vec<u8>],
        server_name: &str,
        resumption: usize,
        min: &str,
    ) -> Result<Config, String> {
        let roots = roots
            .iter()
            .map(|der| SecCertificate::from_der(der).map_err(|e| format!("root: {e}")))
            .collect::<Result<Vec<_>, _>>()?;
        Ok(Config {
            roots,
            server_name: server_name.to_owned(),
            tickets: resumption > 0,
            min: if min == "1.3" {
                SslProtocol::TLS13
            } else {
                SslProtocol::TLS12
            },
        })
    }

    pub struct Tls(SslStream<TcpStream>);

    impl Connection for Tls {
        fn stream(&mut self) -> &mut dyn ReadWrite {
            &mut self.0
        }
        fn facts(&self) -> Value {
            let context = self.0.context();
            json!({
                "tls":true,
                "version":context.negotiated_protocol_version().ok().map(|v| match v {
                    SslProtocol::TLS13 => "TLSv1_3".to_owned(),
                    SslProtocol::TLS12 => "TLSv1_2".to_owned(),
                    SslProtocol::TLS11 => "TLSv1_1".to_owned(),
                    SslProtocol::TLS1 => "TLSv1_0".to_owned(),
                    other => format!("{other:?}"),
                }),
                "protocol_max_setting":"platform maximum (SSLSetProtocolVersionMax(kTLSProtocol13) refused with -9830 on the recording macOS)",
                "cipher":context.negotiated_cipher().ok().map(|c| format!("{c:?}")),
                "alpn":context.alpn_protocols().ok().and_then(|p| p.first().cloned()),
                "resumed":Value::Null,
                "peer_certificates":context.peer_trust2().ok().flatten().map(|_| "present"),
            })
        }
        fn shutdown(&mut self) {
            let _ = self.0.close();
            let _ = self.0.get_mut().shutdown(std::net::Shutdown::Both);
        }
    }

    pub fn connect(
        config: &Config,
        port: u16,
        server_name: Option<&str>,
    ) -> Result<Box<dyn Connection>, String> {
        let name = server_name.unwrap_or(&config.server_name);
        let mut builder = ClientBuilder::new();
        builder
            .anchor_certificates(&config.roots)
            .trust_anchor_certificates_only(true)
            .alpn_protocols(&["http/1.1"])
            .protocol_min(config.min)
            // Court amendment (mechanism, recorded): SSLSetProtocolVersionMax(kTLSProtocol13)
            // is refused by SecureTransport on the recording macOS with errSSLIllegalParam
            // (-9830) and aborts every handshake, so the platform's own maximum is used.
            .enable_session_tickets(config.tickets);
        let stream = builder.handshake(name, tcp(port)?).map_err(|e| {
            format!("handshake: {e:?}")
                .chars()
                .take(200)
                .collect::<String>()
        })?;
        Ok(Box::new(Tls(stream)))
    }

    pub fn sessions_cached(_config: &Config) -> Value {
        // SecureTransport's session cache is system-managed and not enumerable.
        Value::Null
    }
}

fn reply(value: Value) {
    let mut out = io::stdout().lock();
    let _ = writeln!(out, "{value}");
    let _ = out.flush();
}

fn main() {
    let mut config: Option<stack::Config> = None;
    let mut live: Vec<Box<dyn Connection>> = Vec::new();
    let mut handshakes_total = 0u64;
    let mut resumed_total = 0u64;
    let mut refused_total = 0u64;
    let stdin = io::stdin();
    for line in stdin.lock().lines() {
        let Ok(line) = line else { break };
        let command: Value = match serde_json::from_str(&line) {
            Ok(v) => v,
            Err(e) => {
                reply(json!({"ok":false,"error":format!("malformed: {e}")}));
                continue;
            }
        };
        let op = command["op"].as_str().unwrap_or_default();
        match op {
            "configure" => {
                let mut roots = Vec::new();
                for path in command["roots"]
                    .as_array()
                    .into_iter()
                    .flatten()
                    .filter_map(Value::as_str)
                {
                    match pem_certificates(path) {
                        Ok(mut ders) => roots.append(&mut ders),
                        Err(e) => {
                            reply(json!({"ok":false,"error":e}));
                            continue;
                        }
                    }
                }
                let server_name = command["server_name"].as_str().unwrap_or("localhost");
                let resumption = command["resumption"].as_u64().unwrap_or(4) as usize;
                let min = command["min_version"].as_str().unwrap_or("1.2");
                match stack::configure(&roots, server_name, resumption, min) {
                    Ok(c) => {
                        config = Some(c);
                        reply(
                            json!({"ok":true,"stack":stack::NAME,"roots":roots.len(),"resumption":resumption,"min_version":min}),
                        );
                    }
                    Err(e) => reply(json!({"ok":false,"error":e})),
                }
            }
            "open" | "probe" => {
                let Some(cfg) = config.as_ref() else {
                    reply(json!({"ok":false,"error":"configure first"}));
                    continue;
                };
                let port = command["port"].as_u64().unwrap_or(0) as u16;
                let path = command["path"].as_str().unwrap_or("/");
                let count = if op == "probe" {
                    1
                } else {
                    command["count"].as_u64().unwrap_or(1) as usize
                };
                let server_name = command["server_name"].as_str();
                let mut facts = Vec::new();
                let mut refused = None;
                for _ in 0..count {
                    match stack::connect(cfg, port, server_name) {
                        Ok(mut conn) => {
                            let host = server_name.unwrap_or(&cfg.server_name).to_owned();
                            match get(conn.stream(), &host, path) {
                                Ok(bytes) => {
                                    handshakes_total += 1;
                                    let mut f = conn.facts();
                                    if f["resumed"].as_bool() == Some(true) {
                                        resumed_total += 1;
                                    }
                                    f["bytes"] = json!(bytes);
                                    facts.push(f);
                                    if op == "open" {
                                        live.push(conn);
                                    } else {
                                        conn.shutdown();
                                    }
                                }
                                Err(e) => {
                                    refused_total += 1;
                                    refused = Some(e);
                                    break;
                                }
                            }
                        }
                        Err(e) => {
                            refused_total += 1;
                            refused = Some(e);
                            break;
                        }
                    }
                }
                match refused {
                    Some(reason) => reply(json!({"ok":false,"refused":reason,"connections":facts})),
                    None => reply(json!({"ok":true,"connections":facts,"live":live.len()})),
                }
            }
            "close" => {
                for mut conn in live.drain(..) {
                    conn.shutdown();
                }
                reply(json!({"ok":true,"live":0}));
            }
            "report" => {
                reply(json!({
                    "ok":true,"stack":stack::NAME,"live":live.len(),"handshakes_total":handshakes_total,
                    "resumed_total":resumed_total,"refused_total":refused_total,
                    "sessions_cached":config.as_ref().map(stack::sessions_cached),
                    "libmalloc":libmalloc(),
                }));
            }
            "trim" => {
                let released = trim();
                reply(json!({"ok":true,"released_bytes":released,"libmalloc":libmalloc()}));
            }
            "exit" => {
                reply(json!({"ok":true}));
                break;
            }
            other => reply(json!({"ok":false,"error":format!("unknown op {other}")})),
        }
    }
}
