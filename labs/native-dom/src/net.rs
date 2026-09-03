//! Bounded network fetch for the native route.
//!
//! Every fetch is limited by scheme (`http`, and `https` only against
//! explicitly pinned roots: rustls with the ring provider, TLS 1.3 or 1.2,
//! ALPN `http/1.1`, names and IP SANs verified in process, a bounded
//! per-profile session cache, no system roots), by address policy (fail
//! closed: only addresses outside every IANA special-purpose range are
//! reachable unless the exact origin is on the host's explicit allowlist), by
//! redirect count with re-authorization at every hop, by header and body
//! bytes, by a per-fetch deadline, and by per-target fetch and byte budgets.
//! Requests are plain HTTP/1.0 `GET` over a fresh TCP connection; the client
//! connects only to the addresses `authorize` vetted, so a name cannot be
//! re-resolved to a different address between the check and the connect. No
//! environment proxy is consulted. Responses with informational status,
//! `Transfer-Encoding`, or malformed or conflicting `Content-Length` are
//! refused rather than guessed at.

use std::io::{self, Read, Write};
use std::net::{IpAddr, Ipv4Addr, Ipv6Addr, SocketAddr, TcpStream, ToSocketAddrs};
use std::path::Path;
use std::sync::Arc;
use std::time::{Duration, Instant};

use url::{Host, Url};

pub const MAX_REDIRECTS: usize = 3;
pub const MAX_HEADER_BYTES: usize = 16 * 1024;
pub const MAX_RESPONSE_BYTES: usize = 1024 * 1024;
pub const PER_FETCH_TIMEOUT: Duration = Duration::from_millis(3000);
pub const CONNECT_TIMEOUT: Duration = Duration::from_millis(1500);
pub const MAX_PENDING_PER_TURN: usize = 4;
pub const MAX_FETCHES_PER_TARGET: usize = 32;
pub const MAX_BYTES_PER_TARGET: usize = 4 * 1024 * 1024;
pub const MAX_EXTERNAL_SCRIPTS: usize = 8;
const USER_AGENT: &str = "MiniCon-Surf-native-dom/0.0.2";
/// Pinned-root input bounds: files, bytes per file, bytes in total, certificates.
pub const MAX_PINNED_ROOT_FILES: usize = 8;
pub const MAX_PINNED_ROOT_FILE_BYTES: usize = 16 * 1024;
pub const MAX_PINNED_ROOT_TOTAL_BYTES: usize = 64 * 1024;
pub const MAX_PINNED_ROOTS: usize = 16;
/// rustls's in-memory client cache below 16 entries rounds to a single
/// server slot that its eviction empties at once (measured in the TLS
/// court), so the per-profile bound is 16 entries: two server slots.
pub const TLS_SESSION_CACHE_ENTRIES: usize = 16;
pub const TLS_PROVIDER: &str = "ring";

/// Why a fetch was refused or failed, mapped onto control 0.0.1 error codes.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct NetError {
    pub code: &'static str,
    pub reason: &'static str,
    pub detail: String,
}

impl NetError {
    fn new(code: &'static str, reason: &'static str, detail: impl Into<String>) -> Self {
        let mut detail = detail.into();
        detail.truncate(256);
        NetError {
            code,
            reason,
            detail,
        }
    }
}

/// One explicitly allowed origin; the only way a non-public address is reachable.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct AllowedOrigin {
    pub scheme: String,
    pub host: String,
    pub port: u16,
}

impl AllowedOrigin {
    pub fn parse(text: &str) -> Result<Self, String> {
        let url = Url::parse(text).map_err(|e| format!("origin is not a URL: {e}"))?;
        if url.scheme() != "http" && url.scheme() != "https" {
            return Err("only http and https origins can be allowed".into());
        }
        if url.path() != "/" || url.query().is_some() || url.fragment().is_some() {
            return Err("origin must not carry a path, query or fragment".into());
        }
        let host = url
            .host_str()
            .ok_or_else(|| "origin lacks a host".to_string())?
            .to_ascii_lowercase();
        let port = url
            .port_or_known_default()
            .ok_or_else(|| "origin lacks a port".to_string())?;
        Ok(AllowedOrigin {
            scheme: url.scheme().to_owned(),
            host,
            port,
        })
    }

    fn matches(&self, url: &Url) -> bool {
        url.scheme() == self.scheme
            && url
                .host_str()
                .is_some_and(|h| h.eq_ignore_ascii_case(&self.host))
            && url.port_or_known_default() == Some(self.port)
    }
}

#[derive(Debug, Clone, Default)]
pub struct Policy {
    pub allowed_origins: Vec<AllowedOrigin>,
    /// True only when pinned roots were loaded; `https` is otherwise
    /// `unsupported_capability`.
    pub https: bool,
}

/// Per-target budget shared by navigation, external scripts and `fetch()`.
#[derive(Debug, Default, Clone)]
pub struct Budget {
    pub fetches: usize,
    pub bytes: usize,
    pub denied: usize,
    pub tls_handshakes: u64,
    pub tls_resumed: u64,
    pub tls_refused: u64,
    pub tls13: u64,
    pub tls12: u64,
}

impl Budget {
    pub fn tls_json(&self) -> serde_json::Value {
        serde_json::json!({
            "handshakes_total":self.tls_handshakes,"resumed_total":self.tls_resumed,
            "refused_total":self.tls_refused,"tls13_total":self.tls13,"tls12_total":self.tls12,
        })
    }

    pub fn absorb_tls(&mut self, other: &Budget) {
        self.tls_handshakes += other.tls_handshakes;
        self.tls_resumed += other.tls_resumed;
        self.tls_refused += other.tls_refused;
        self.tls13 += other.tls13;
        self.tls12 += other.tls12;
    }
}

// ------------------------------------------------------------------ TLS

/// The host's pinned roots: the only trust anchors of the https slice.
/// Loaded once from public-certificate PEM files under fixed bounds; the
/// ring provider is selected explicitly here and nowhere else.
pub struct TlsRoots {
    store: Arc<rustls::RootCertStore>,
    provider: Arc<rustls::crypto::CryptoProvider>,
    pub certificates: usize,
    pub bytes: usize,
    pub files: usize,
}

/// One profile's TLS client: the shared roots plus that profile's own
/// bounded session cache, never shared across profiles.
pub struct TlsClient {
    config: Arc<rustls::ClientConfig>,
}

impl std::fmt::Debug for TlsRoots {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(f, "TlsRoots({} certificates)", self.certificates)
    }
}

impl std::fmt::Debug for TlsClient {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str("TlsClient")
    }
}

/// Load pinned roots. Errors name counts and limits only, never a path or
/// file content; a file carrying a private-key block is refused outright.
pub fn load_pinned_roots(paths: &[impl AsRef<Path>]) -> Result<TlsRoots, String> {
    use base64::Engine as _;
    if paths.is_empty() {
        return Err("no pinned root given".into());
    }
    if paths.len() > MAX_PINNED_ROOT_FILES {
        return Err(format!(
            "more than {MAX_PINNED_ROOT_FILES} pinned root files"
        ));
    }
    let mut store = rustls::RootCertStore::empty();
    let mut total = 0usize;
    let mut certificates = 0usize;
    for (index, path) in paths.iter().enumerate() {
        let text = std::fs::read_to_string(path.as_ref())
            .map_err(|e| format!("pinned root file {index} unreadable: {}", e.kind()))?;
        if text.len() > MAX_PINNED_ROOT_FILE_BYTES {
            return Err(format!(
                "pinned root file {index} exceeds {MAX_PINNED_ROOT_FILE_BYTES} bytes"
            ));
        }
        total += text.len();
        if total > MAX_PINNED_ROOT_TOTAL_BYTES {
            return Err(format!(
                "pinned roots exceed {MAX_PINNED_ROOT_TOTAL_BYTES} bytes in total"
            ));
        }
        if text.contains("PRIVATE KEY") {
            return Err(format!(
                "pinned root file {index} carries a private-key block; refused"
            ));
        }
        let mut body = String::new();
        let mut inside = false;
        let mut found = 0usize;
        for line in text.lines() {
            let line = line.trim();
            if line == "-----BEGIN CERTIFICATE-----" {
                inside = true;
                body.clear();
            } else if line == "-----END CERTIFICATE-----" {
                inside = false;
                let der = base64::engine::general_purpose::STANDARD
                    .decode(&body)
                    .map_err(|_| format!("pinned root file {index} is not valid PEM"))?;
                certificates += 1;
                found += 1;
                if certificates > MAX_PINNED_ROOTS {
                    return Err(format!(
                        "more than {MAX_PINNED_ROOTS} pinned root certificates"
                    ));
                }
                store
                    .add(rustls_pki_types::CertificateDer::from(der))
                    .map_err(|_| format!("pinned root file {index} holds a certificate that cannot be a trust anchor"))?;
            } else if inside {
                body.push_str(line);
            }
        }
        if found == 0 {
            return Err(format!("pinned root file {index} holds no certificate"));
        }
    }
    Ok(TlsRoots {
        store: Arc::new(store),
        provider: Arc::new(rustls::crypto::ring::default_provider()),
        certificates,
        bytes: total,
        files: paths.len(),
    })
}

impl TlsRoots {
    /// A client for one profile: TLS 1.3 preferred, 1.2 accepted, ALPN
    /// `http/1.1` only, this profile's own bounded session cache.
    pub fn client(&self) -> TlsClient {
        let mut config = rustls::ClientConfig::builder_with_provider(self.provider.clone())
            .with_protocol_versions(&[&rustls::version::TLS13, &rustls::version::TLS12])
            .expect("the ring provider supports TLS 1.2 and 1.3")
            .with_root_certificates(self.store.clone())
            .with_no_client_auth();
        config.alpn_protocols = vec![b"http/1.1".to_vec()];
        config.resumption = rustls::client::Resumption::store(Arc::new(
            rustls::client::ClientSessionMemoryCache::new(TLS_SESSION_CACHE_ENTRIES),
        ));
        TlsClient {
            config: Arc::new(config),
        }
    }
}

/// Facts of one TLS connection, never certificate contents.
#[derive(Debug, Clone, Copy)]
struct TlsFacts {
    tls13: bool,
    resumed: bool,
}

/// Map a rustls failure onto a typed, non-revealing refusal.
fn tls_error(error: &rustls::Error) -> NetError {
    use rustls::{AlertDescription, CertificateError, Error};
    match error {
        Error::InvalidCertificate(
            CertificateError::NotValidForName | CertificateError::NotValidForNameContext { .. },
        ) => NetError::new(
            "permission_denied",
            "tls_hostname_mismatch",
            "server certificate does not match the URL host",
        ),
        Error::InvalidCertificate(_) => NetError::new(
            "permission_denied",
            "tls_untrusted_root",
            "server certificate does not chain to a pinned root",
        ),
        Error::NoApplicationProtocol => NetError::new(
            "permission_denied",
            "tls_alpn",
            "server did not negotiate http/1.1",
        ),
        Error::AlertReceived(AlertDescription::ProtocolVersion)
        | Error::AlertReceived(AlertDescription::HandshakeFailure)
        | Error::PeerIncompatible(_) => NetError::new(
            "permission_denied",
            "tls_protocol",
            "server offers no acceptable TLS version or parameters",
        ),
        _ => NetError::new("not_found", "tls_handshake", "TLS handshake failed"),
    }
}

fn tls_io_error(stage: &'static str, error: io::Error) -> NetError {
    if let Some(inner) = error
        .get_ref()
        .and_then(|inner| inner.downcast_ref::<rustls::Error>())
    {
        return tls_error(inner);
    }
    io_error(stage, error)
}

fn server_name(url: &Url) -> Result<rustls_pki_types::ServerName<'static>, NetError> {
    match url.host() {
        Some(Host::Ipv4(ip)) => Ok(rustls_pki_types::ServerName::IpAddress(
            IpAddr::V4(ip).into(),
        )),
        Some(Host::Ipv6(ip)) => Ok(rustls_pki_types::ServerName::IpAddress(
            IpAddr::V6(ip).into(),
        )),
        Some(Host::Domain(name)) => rustls_pki_types::ServerName::try_from(name.to_owned())
            .map_err(|_| {
                NetError::new(
                    "invalid_request",
                    "host",
                    "URL host is not a valid server name",
                )
            }),
        None => Err(NetError::new("invalid_request", "host", "URL lacks a host")),
    }
}

trait ReadWrite: Read + Write {}
impl<T: Read + Write> ReadWrite for T {}

/// How the body length was established.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Framing {
    ContentLength,
    NoBody,
    UntilClose,
}

impl Framing {
    pub fn as_str(self) -> &'static str {
        match self {
            Framing::ContentLength => "content-length",
            Framing::NoBody => "no-body",
            Framing::UntilClose => "until-close",
        }
    }
}

#[derive(Debug, Clone)]
pub struct Response {
    pub status: u16,
    pub url: Url,
    pub content_type: Option<String>,
    pub body: Vec<u8>,
    pub redirects: usize,
    pub framing: Framing,
}

/// The profile's cookie jar as the network path sees it: a header for each
/// hop and every `Set-Cookie` line each hop answered with. The network
/// module never knows what a profile is; the host implements this over the
/// session's profile.
pub trait CookieHooks {
    fn cookie_header(&mut self, url: &Url) -> Option<String>;
    fn store(&mut self, url: &Url, set_cookie: &str);
}

/// IPv4 addresses outside every IANA special-purpose range (RFC 6890 and
/// successors). Anything listed there is refused.
fn is_public_v4(v4: Ipv4Addr) -> bool {
    let [a, b, c, _] = v4.octets();
    let special = a == 0                                  // 0.0.0.0/8 this network
        || a == 10                                        // 10.0.0.0/8 private
        || (a == 100 && (64..=127).contains(&b))          // 100.64.0.0/10 shared address space
        || a == 127                                       // 127.0.0.0/8 loopback
        || (a == 169 && b == 254)                         // 169.254.0.0/16 link local
        || (a == 172 && (16..=31).contains(&b))           // 172.16.0.0/12 private
        || (a == 192 && b == 0 && c == 0)                 // 192.0.0.0/24 IETF protocol assignments
        || (a == 192 && b == 0 && c == 2)                 // 192.0.2.0/24 TEST-NET-1
        || (a == 192 && b == 88 && c == 99)               // 192.88.99.0/24 6to4 relay anycast (deprecated)
        || (a == 192 && b == 168)                         // 192.168.0.0/16 private
        || (a == 198 && (b == 18 || b == 19))             // 198.18.0.0/15 benchmarking
        || (a == 198 && b == 51 && c == 100)              // 198.51.100.0/24 TEST-NET-2
        || (a == 203 && b == 0 && c == 113)               // 203.0.113.0/24 TEST-NET-3
        || a >= 224; // 224.0.0.0/4 multicast, 240.0.0.0/4 reserved, 255.255.255.255 broadcast
    !special
}

/// IPv6 fails closed: only 2000::/3 global unicast can be public, and the
/// special-purpose blocks inside it are refused as well. Addresses that
/// embed an IPv4 address are judged by that address.
fn is_public_v6(v6: Ipv6Addr) -> bool {
    if let Some(mapped) = v6.to_ipv4_mapped() {
        return is_public_v4(mapped);
    }
    let s = v6.segments();
    // IPv4-compatible ::a.b.c.d (deprecated) and NAT64 64:ff9b::/96 embed IPv4.
    if s[..6] == [0, 0, 0, 0, 0, 0] || (s[0] == 0x64 && s[1] == 0xff9b && s[2..6] == [0, 0, 0, 0]) {
        return false;
    }
    if (s[0] & 0xe000) != 0x2000 {
        // Outside 2000::/3: loopback, unspecified, ULA fc00::/7, link-local
        // fe80::/10, deprecated site-local fec0::/10, multicast ff00::/8,
        // discard 100::/64, NAT64 local-use 64:ff9b:1::/48 and anything else.
        return false;
    }
    let teredo = s[0] == 0x2001 && s[1] == 0;
    let benchmarking = s[0] == 0x2001 && s[1] == 0x0002 && s[2] == 0;
    let orchid =
        s[0] == 0x2001 && (s[1] & 0xfff0) == 0x0010 || s[0] == 0x2001 && (s[1] & 0xfff0) == 0x0020;
    let documentation =
        (s[0] == 0x2001 && s[1] == 0x0db8) || (s[0] == 0x3fff && (s[1] & 0xf000) == 0);
    if s[0] == 0x2002 {
        // 6to4: judge the embedded IPv4 address.
        let embedded = Ipv4Addr::new((s[1] >> 8) as u8, s[1] as u8, (s[2] >> 8) as u8, s[2] as u8);
        return is_public_v4(embedded);
    }
    !(teredo || benchmarking || orchid || documentation)
}

pub fn is_public_ip(ip: IpAddr) -> bool {
    match ip {
        IpAddr::V4(v4) => is_public_v4(v4),
        IpAddr::V6(v6) => is_public_v6(v6),
    }
}

/// Validate a URL against scheme and address policy and return the exact
/// addresses a connection may use. Allowlisted origins skip address
/// classification; every other host must resolve only to public addresses.
pub fn authorize(url: &Url, policy: &Policy) -> Result<Vec<SocketAddr>, NetError> {
    match url.scheme() {
        "http" => {}
        "https" if policy.https => {}
        "https" => {
            return Err(NetError::new(
                "unsupported_capability",
                "tls_no_pinned_roots",
                "https needs an explicitly pinned root; no system roots are consulted",
            ));
        }
        other => {
            return Err(NetError::new(
                "unsupported_capability",
                "scheme",
                format!("scheme {other} is not offered; only http and pinned https are"),
            ));
        }
    }
    if url.username() != "" || url.password().is_some() {
        return Err(NetError::new(
            "permission_denied",
            "credentials",
            "URLs with embedded credentials are refused",
        ));
    }
    let host = url
        .host()
        .ok_or_else(|| NetError::new("invalid_request", "host", "URL lacks a host"))?;
    let port = url
        .port_or_known_default()
        .ok_or_else(|| NetError::new("invalid_request", "port", "URL lacks a port"))?;
    let addresses = match host {
        Host::Ipv4(ip) => vec![SocketAddr::new(IpAddr::V4(ip), port)],
        Host::Ipv6(ip) => vec![SocketAddr::new(IpAddr::V6(ip), port)],
        Host::Domain(name) => {
            let lowered = name.to_ascii_lowercase();
            let allowlisted = policy.allowed_origins.iter().any(|o| o.matches(url));
            if !allowlisted && (lowered == "localhost" || lowered.ends_with(".localhost")) {
                return Err(NetError::new(
                    "permission_denied",
                    "address",
                    "loopback names are refused unless the origin is allowlisted",
                ));
            }
            resolve(name, port)?
        }
    };
    if addresses.is_empty() {
        return Err(NetError::new(
            "not_found",
            "dns",
            "host resolved to no addresses",
        ));
    }
    if policy.allowed_origins.iter().any(|o| o.matches(url)) {
        return Ok(addresses);
    }
    if let Some(blocked) = addresses.iter().find(|a| !is_public_ip(a.ip())) {
        return Err(NetError::new(
            "permission_denied",
            "address",
            format!("address class refused: {}", classify(blocked.ip())),
        ));
    }
    Ok(addresses)
}

fn classify(ip: IpAddr) -> &'static str {
    match ip {
        IpAddr::V4(v4) if v4.is_loopback() => "loopback",
        IpAddr::V4(v4) if v4.is_private() => "private",
        IpAddr::V4(v4) if v4.is_link_local() => "link-local (includes metadata services)",
        IpAddr::V4(_) => "special-purpose IPv4",
        IpAddr::V6(v6) if v6.is_loopback() => "loopback",
        IpAddr::V6(v6) if (v6.segments()[0] & 0xfe00) == 0xfc00 => "unique-local",
        IpAddr::V6(v6) if (v6.segments()[0] & 0xffc0) == 0xfe80 => "link-local",
        IpAddr::V6(_) => "non-global IPv6",
    }
}

fn resolve(name: &str, port: u16) -> Result<Vec<SocketAddr>, NetError> {
    let candidates: Vec<SocketAddr> = (name, port)
        .to_socket_addrs()
        .map_err(|e| NetError::new("not_found", "dns", format!("name did not resolve: {e}")))?
        .collect();
    if candidates.is_empty() {
        return Err(NetError::new(
            "not_found",
            "dns",
            "name resolved to no addresses",
        ));
    }
    Ok(candidates)
}

/// Perform one bounded GET, following at most `MAX_REDIRECTS` redirects with
/// the policy re-applied at every hop. `deadline` is the request deadline;
/// each hop is additionally capped by `PER_FETCH_TIMEOUT`.
#[cfg_attr(not(test), allow(dead_code))]
pub fn fetch(
    url: &str,
    policy: &Policy,
    budget: &mut Budget,
    deadline: Instant,
) -> Result<Response, NetError> {
    fetch_with(url, policy, budget, deadline, None, None)
}

/// `fetch` with a cookie jar: the request of every hop carries the jar's
/// header for that hop's URL and every hop's `Set-Cookie` lines reach the
/// jar, redirects included, before the next hop is followed.
pub fn fetch_with(
    url: &str,
    policy: &Policy,
    budget: &mut Budget,
    deadline: Instant,
    mut cookies: Option<&mut dyn CookieHooks>,
    tls: Option<&TlsClient>,
) -> Result<Response, NetError> {
    let mut current = Url::parse(url)
        .map_err(|e| NetError::new("invalid_request", "url", format!("URL is malformed: {e}")))?;
    if budget.fetches >= MAX_FETCHES_PER_TARGET {
        budget.denied += 1;
        return Err(NetError::new(
            "resource_limit",
            "fetch-count",
            format!("target already performed {MAX_FETCHES_PER_TARGET} fetches"),
        ));
    }
    budget.fetches += 1;
    let mut redirects = 0usize;
    loop {
        let addresses = authorize(&current, policy).inspect_err(|_| budget.denied += 1)?;
        let hop_deadline = std::cmp::min(deadline, Instant::now() + PER_FETCH_TIMEOUT);
        let remaining_budget = MAX_BYTES_PER_TARGET.saturating_sub(budget.bytes);
        let cap = std::cmp::min(MAX_RESPONSE_BYTES, remaining_budget);
        let cookie_header = cookies
            .as_deref_mut()
            .and_then(|jar| jar.cookie_header(&current));
        let hop = match get_once(
            &current,
            &addresses,
            cap,
            hop_deadline,
            cookie_header.as_deref(),
            tls,
        ) {
            Ok(hop) => hop,
            Err(error) => {
                if error.reason.starts_with("tls_") {
                    budget.tls_refused += 1;
                    budget.denied += 1;
                }
                return Err(error);
            }
        };
        if let Some(facts) = hop.tls {
            budget.tls_handshakes += 1;
            if facts.resumed {
                budget.tls_resumed += 1;
            }
            if facts.tls13 {
                budget.tls13 += 1;
            } else {
                budget.tls12 += 1;
            }
        }
        budget.bytes += hop.body.len();
        if let Some(jar) = cookies.as_deref_mut() {
            for line in &hop.set_cookie {
                jar.store(&current, line);
            }
        }
        if matches!(hop.status, 301 | 302 | 303 | 307 | 308) {
            let location = hop.location.as_deref().ok_or_else(|| {
                NetError::new(
                    "not_found",
                    "redirect",
                    "redirect response without a Location header",
                )
            })?;
            if redirects >= MAX_REDIRECTS {
                budget.denied += 1;
                return Err(NetError::new(
                    "resource_limit",
                    "redirect-count",
                    format!("more than {MAX_REDIRECTS} redirects"),
                ));
            }
            redirects += 1;
            let next = current.join(location).map_err(|e| {
                NetError::new(
                    "invalid_request",
                    "redirect",
                    format!("Location is not a valid URL: {e}"),
                )
            })?;
            // A verified origin never downgrades: https → http is refused as
            // a hop, before any authorization of the plain target.
            if current.scheme() == "https" && next.scheme() != "https" {
                budget.denied += 1;
                return Err(NetError::new(
                    "permission_denied",
                    "redirect_downgrade",
                    "redirect from https to http is refused",
                ));
            }
            current = next;
            continue;
        }
        return Ok(Response {
            status: hop.status,
            url: current,
            content_type: hop.content_type,
            body: hop.body,
            redirects,
            framing: hop.framing,
        });
    }
}

#[derive(Debug)]
struct Hop {
    status: u16,
    content_type: Option<String>,
    location: Option<String>,
    set_cookie: Vec<String>,
    body: Vec<u8>,
    framing: Framing,
    tls: Option<TlsFacts>,
}

/// Connect to one of the vetted addresses only. This function never resolves
/// a name, so the address `authorize` checked is the address used.
fn connect(addresses: &[SocketAddr], deadline: Instant) -> Result<TcpStream, NetError> {
    let mut last = None;
    for address in addresses {
        let remaining = deadline.saturating_duration_since(Instant::now());
        if remaining.is_zero() {
            return Err(NetError::new(
                "deadline_exceeded",
                "connect",
                "deadline expired before connecting",
            ));
        }
        match TcpStream::connect_timeout(address, std::cmp::min(CONNECT_TIMEOUT, remaining)) {
            Ok(stream) => return Ok(stream),
            Err(error) => last = Some(error),
        }
    }
    let detail = last
        .map(|e| e.to_string())
        .unwrap_or_else(|| "no address".into());
    Err(NetError::new(
        "not_found",
        "connect",
        format!("connection failed: {detail}"),
    ))
}

fn get_once(
    url: &Url,
    addresses: &[SocketAddr],
    body_cap: usize,
    deadline: Instant,
    cookie_header: Option<&str>,
    tls: Option<&TlsClient>,
) -> Result<Hop, NetError> {
    let tcp = connect(addresses, deadline)?;
    let remaining = deadline.saturating_duration_since(Instant::now());
    tcp.set_read_timeout(Some(std::cmp::max(remaining, Duration::from_millis(10))))
        .map_err(|e| NetError::new("internal", "socket", e.to_string()))?;
    tcp.set_write_timeout(Some(std::cmp::max(remaining, Duration::from_millis(10))))
        .map_err(|e| NetError::new("internal", "socket", e.to_string()))?;
    // The handshake runs under the same absolute deadline through the socket
    // timeouts; SNI and the verified name are the original URL host.
    let (mut stream, tls_facts): (Box<dyn ReadWrite>, Option<TlsFacts>) = if url.scheme() == "https"
    {
        let client = tls.ok_or_else(|| {
            NetError::new(
                "unsupported_capability",
                "tls_no_pinned_roots",
                "https needs an explicitly pinned root; no system roots are consulted",
            )
        })?;
        let name = server_name(url)?;
        let connection = rustls::ClientConnection::new(client.config.clone(), name)
            .map_err(|e| tls_error(&e))?;
        let mut tls_stream = rustls::StreamOwned::new(connection, tcp);
        while tls_stream.conn.is_handshaking() {
            if Instant::now() >= deadline {
                return Err(NetError::new(
                    "deadline_exceeded",
                    "tls_handshake",
                    "TLS handshake did not finish before deadline",
                ));
            }
            tls_stream
                .conn
                .complete_io(&mut tls_stream.sock)
                .map_err(|e| tls_io_error("tls_handshake", e))?;
        }
        if tls_stream.conn.alpn_protocol() != Some(b"http/1.1".as_slice()) {
            return Err(NetError::new(
                "permission_denied",
                "tls_alpn",
                "server did not negotiate http/1.1",
            ));
        }
        let facts = TlsFacts {
            tls13: tls_stream.conn.protocol_version() == Some(rustls::ProtocolVersion::TLSv1_3),
            resumed: tls_stream.conn.handshake_kind() == Some(rustls::HandshakeKind::Resumed),
        };
        (Box::new(tls_stream), Some(facts))
    } else {
        (Box::new(tcp), None)
    };
    let host_header = match (url.host_str(), url.port()) {
        (Some(host), Some(port)) => format!("{host}:{port}"),
        (Some(host), None) => host.to_owned(),
        _ => return Err(NetError::new("invalid_request", "host", "URL lacks a host")),
    };
    let path = match url.query() {
        Some(query) => format!("{}?{}", url.path(), query),
        None => url.path().to_owned(),
    };
    let cookie_line = cookie_header
        .filter(|value| !value.is_empty())
        .map(|value| format!("Cookie: {value}\r\n"))
        .unwrap_or_default();
    let request = format!(
        "GET {path} HTTP/1.0\r\nHost: {host_header}\r\nUser-Agent: {USER_AGENT}\r\nAccept: text/html, application/json, text/javascript, */*;q=0.5\r\n{cookie_line}Connection: close\r\n\r\n"
    );
    // The same bound on the request side: a header section the host builds
    // (long path or a large cookie header) is refused, never sent oversized.
    if request.len() > MAX_HEADER_BYTES {
        return Err(NetError::new(
            "resource_limit",
            "request-header-bytes",
            format!("request headers exceed {MAX_HEADER_BYTES} bytes"),
        ));
    }
    stream
        .write_all(request.as_bytes())
        .map_err(|e| tls_io_error("write", e))?;

    let mut buffer = Vec::new();
    let mut chunk = [0u8; 8192];
    let header_end;
    loop {
        if Instant::now() >= deadline {
            return Err(NetError::new(
                "deadline_exceeded",
                "read",
                "response headers did not arrive before deadline",
            ));
        }
        let read = stream
            .read(&mut chunk)
            .map_err(|e| tls_io_error("read", e))?;
        if read == 0 {
            return Err(NetError::new(
                "not_found",
                "response",
                "connection closed before the response headers ended",
            ));
        }
        buffer.extend_from_slice(&chunk[..read]);
        if let Some(index) = find_header_end(&buffer) {
            // The header section is everything up to and including the
            // terminator; it is compared with the cap whether it arrived in
            // one read, with the terminator in the same chunk, or across
            // chunks, so the bound does not depend on chunk boundaries.
            if index + 4 > MAX_HEADER_BYTES {
                return Err(header_cap_exceeded());
            }
            header_end = index;
            break;
        }
        if buffer.len() > MAX_HEADER_BYTES {
            return Err(header_cap_exceeded());
        }
    }
    let head = parse_head(&buffer[..header_end])?;
    let content_type = head.content_type.clone();
    let location = head.location.clone();
    let set_cookie = head.set_cookie.clone();
    let mut body = buffer[header_end + 4..].to_vec();
    let framing = match head.content_length {
        _ if matches!(head.status, 204 | 304) => Framing::NoBody,
        Some(_) => Framing::ContentLength,
        None => Framing::UntilClose,
    };
    if framing == Framing::NoBody {
        return Ok(Hop {
            status: head.status,
            content_type,
            location,
            set_cookie,
            body: Vec::new(),
            framing,
            tls: tls_facts,
        });
    }
    if body.len() > body_cap || head.content_length.is_some_and(|len| len > body_cap) {
        return Err(NetError::new(
            "resource_limit",
            "response-bytes",
            format!("response body exceeds the {body_cap}-byte cap"),
        ));
    }
    loop {
        if let Some(len) = head.content_length
            && body.len() >= len
        {
            body.truncate(len);
            break;
        }
        if Instant::now() >= deadline {
            return Err(NetError::new(
                "deadline_exceeded",
                "read",
                "response body did not arrive before deadline",
            ));
        }
        let read = stream
            .read(&mut chunk)
            .map_err(|e| tls_io_error("read", e))?;
        if read == 0 {
            if let Some(len) = head.content_length {
                return Err(NetError::new(
                    "not_found",
                    "response",
                    format!(
                        "connection closed after {} of {len} declared body bytes",
                        body.len()
                    ),
                ));
            }
            break;
        }
        if body.len() + read > body_cap {
            return Err(NetError::new(
                "resource_limit",
                "response-bytes",
                format!("response body exceeds the {body_cap}-byte cap"),
            ));
        }
        body.extend_from_slice(&chunk[..read]);
    }
    Ok(Hop {
        status: head.status,
        content_type,
        location,
        set_cookie,
        body,
        framing,
        tls: tls_facts,
    })
}

fn header_cap_exceeded() -> NetError {
    NetError::new(
        "resource_limit",
        "header-bytes",
        format!("response headers exceed {MAX_HEADER_BYTES} bytes"),
    )
}

fn io_error(stage: &'static str, error: io::Error) -> NetError {
    match error.kind() {
        io::ErrorKind::TimedOut | io::ErrorKind::WouldBlock => {
            NetError::new("deadline_exceeded", stage, "socket operation timed out")
        }
        _ => NetError::new("not_found", stage, error.to_string()),
    }
}

fn find_header_end(buffer: &[u8]) -> Option<usize> {
    buffer.windows(4).position(|w| w == b"\r\n\r\n")
}

struct Head {
    status: u16,
    content_length: Option<usize>,
    content_type: Option<String>,
    location: Option<String>,
    set_cookie: Vec<String>,
}

/// Parse the status line and headers strictly: HTTP/1.x only, no
/// informational status, no `Transfer-Encoding`, and at most one distinct
/// well-formed `Content-Length` value.
fn parse_head(head: &[u8]) -> Result<Head, NetError> {
    let text = std::str::from_utf8(head)
        .map_err(|_| NetError::new("not_found", "response", "response head is not UTF-8"))?;
    let mut lines = text.split("\r\n");
    let status_line = lines.next().unwrap_or_default();
    let mut parts = status_line.splitn(3, ' ');
    let version = parts.next().unwrap_or_default();
    if version != "HTTP/1.0" && version != "HTTP/1.1" {
        return Err(NetError::new(
            "not_found",
            "response",
            "response is not HTTP/1.0 or HTTP/1.1",
        ));
    }
    let status = parts
        .next()
        .and_then(|s| (s.len() == 3).then(|| s.parse::<u16>().ok()).flatten())
        .filter(|s| (100..=599).contains(s))
        .ok_or_else(|| NetError::new("not_found", "response", "status code is malformed"))?;
    if (100..200).contains(&status) {
        return Err(NetError::new(
            "not_found",
            "response",
            "informational responses are not supported by an HTTP/1.0 client",
        ));
    }
    let mut content_length: Option<usize> = None;
    let mut content_type = None;
    let mut location = None;
    let mut set_cookie = Vec::new();
    for line in lines {
        let Some((name, value)) = line.split_once(':') else {
            if line.is_empty() {
                continue;
            }
            return Err(NetError::new(
                "not_found",
                "response",
                "malformed header line",
            ));
        };
        let name = name.trim().to_ascii_lowercase();
        let value = value.trim();
        match name.as_str() {
            "transfer-encoding" => {
                return Err(NetError::new(
                    "not_found",
                    "response",
                    "Transfer-Encoding is refused; the client speaks HTTP/1.0",
                ));
            }
            "content-length" => {
                for candidate in value.split(',') {
                    let candidate = candidate.trim();
                    let parsed =
                        if !candidate.is_empty() && candidate.bytes().all(|b| b.is_ascii_digit()) {
                            candidate.parse::<usize>().ok()
                        } else {
                            None
                        };
                    let Some(parsed) = parsed else {
                        return Err(NetError::new(
                            "not_found",
                            "response",
                            "Content-Length is malformed",
                        ));
                    };
                    if content_length.is_some_and(|existing| existing != parsed) {
                        return Err(NetError::new(
                            "not_found",
                            "response",
                            "conflicting Content-Length values",
                        ));
                    }
                    content_length = Some(parsed);
                }
            }
            "content-type" => content_type = Some(value.to_ascii_lowercase()),
            "location" => location = Some(value.to_owned()),
            "set-cookie" => set_cookie.push(value.to_owned()),
            _ => {}
        }
    }
    Ok(Head {
        status,
        content_length,
        content_type,
        location,
        set_cookie,
    })
}

/// True when `other` shares scheme, host and port with `base`.
pub fn same_origin(base: &Url, other: &Url) -> bool {
    base.scheme() == other.scheme()
        && base.host_str().map(str::to_ascii_lowercase)
            == other.host_str().map(str::to_ascii_lowercase)
        && base.port_or_known_default() == other.port_or_known_default()
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::net::TcpListener;
    use std::sync::mpsc;
    use std::thread;

    fn policy_with(origin: &str) -> Policy {
        Policy {
            https: false,
            allowed_origins: vec![AllowedOrigin::parse(origin).unwrap()],
        }
    }

    /// Serve one canned response on a loopback listener and hand back the
    /// request line the client sent.
    /// Serve one response written in the given pieces with a pause between
    /// them, so the client's reads see the pieces as separate chunks.
    fn serve_in_pieces(pieces: Vec<Vec<u8>>) -> SocketAddr {
        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        let address = listener.local_addr().unwrap();
        thread::spawn(move || {
            let (mut stream, _) = listener.accept().unwrap();
            let mut request = Vec::new();
            let mut chunk = [0u8; 1024];
            while !request.windows(4).any(|w| w == b"\r\n\r\n") {
                let n = stream.read(&mut chunk).unwrap();
                if n == 0 {
                    break;
                }
                request.extend_from_slice(&chunk[..n]);
            }
            for (index, piece) in pieces.iter().enumerate() {
                if index > 0 {
                    thread::sleep(Duration::from_millis(60));
                }
                if stream.write_all(piece).is_err() {
                    break;
                }
                let _ = stream.flush();
            }
        });
        address
    }

    /// A response whose header section is exactly `section` bytes long,
    /// terminator included, followed by a two-byte body.
    fn response_with_header_section(section: usize) -> Vec<u8> {
        let fixed = b"HTTP/1.0 200 OK\r\nContent-Length: 2\r\nX-Pad: ";
        let tail = b"\r\n\r\n";
        let padding = section - fixed.len() - tail.len();
        let mut out = fixed.to_vec();
        out.extend(std::iter::repeat_n(b'y', padding));
        out.extend_from_slice(tail);
        assert_eq!(out.len(), section);
        out.extend_from_slice(b"ok");
        out
    }

    fn header_outcome(pieces: Vec<Vec<u8>>) -> Result<Hop, NetError> {
        let address = serve_in_pieces(pieces);
        get_once(
            &Url::parse("http://example.com/").unwrap(),
            &[address],
            1024,
            Instant::now() + Duration::from_secs(5),
            None,
            None,
        )
    }

    #[test]
    fn header_cap_is_exact_regardless_of_chunking() {
        let cap = MAX_HEADER_BYTES;
        // One read, terminator in the same chunk as the padding.
        assert!(header_outcome(vec![response_with_header_section(cap - 1)]).is_ok());
        assert!(header_outcome(vec![response_with_header_section(cap)]).is_ok());
        let err = header_outcome(vec![response_with_header_section(cap + 1)]).unwrap_err();
        assert_eq!((err.code, err.reason), ("resource_limit", "header-bytes"));
        // Terminator arrives in a later chunk than the padding.
        for (section, expect_ok) in [(cap - 1, true), (cap, true), (cap + 1, false)] {
            let response = response_with_header_section(section);
            let split = section - 3;
            let pieces = vec![response[..split].to_vec(), response[split..].to_vec()];
            let outcome = header_outcome(pieces);
            assert_eq!(
                outcome.is_ok(),
                expect_ok,
                "section {section} across chunks"
            );
            if !expect_ok {
                assert_eq!(outcome.unwrap_err().reason, "header-bytes");
            }
        }
        // Padding split into many small chunks, terminator last.
        let response = response_with_header_section(cap + 1);
        let pieces: Vec<Vec<u8>> = response.chunks(5000).map(|c| c.to_vec()).collect();
        assert_eq!(header_outcome(pieces).unwrap_err().reason, "header-bytes");
        // A single header line longer than the cap, sent in one write.
        let mut single = b"HTTP/1.0 200 OK\r\nX-Long: ".to_vec();
        single.extend(std::iter::repeat_n(b'z', cap + 10));
        single.extend_from_slice(b"\r\nContent-Length: 0\r\n\r\n");
        assert_eq!(
            header_outcome(vec![single]).unwrap_err().reason,
            "header-bytes"
        );
    }

    #[test]
    fn request_headers_are_bounded_too() {
        let (address, _) = serve_once(b"HTTP/1.0 200 OK\r\nContent-Length: 0\r\n\r\n");
        let cookie = "a=".to_owned() + &"x".repeat(MAX_HEADER_BYTES);
        let err = get_once(
            &Url::parse("http://example.com/").unwrap(),
            &[address],
            1024,
            soon(),
            Some(&cookie),
            None,
        )
        .unwrap_err();
        assert_eq!(
            (err.code, err.reason),
            ("resource_limit", "request-header-bytes")
        );
    }

    fn serve_once(response: &'static [u8]) -> (SocketAddr, mpsc::Receiver<String>) {
        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        let address = listener.local_addr().unwrap();
        let (sender, receiver) = mpsc::channel();
        thread::spawn(move || {
            let (mut stream, _) = listener.accept().unwrap();
            let mut request = Vec::new();
            let mut chunk = [0u8; 1024];
            while !request.windows(4).any(|w| w == b"\r\n\r\n") {
                let n = stream.read(&mut chunk).unwrap();
                if n == 0 {
                    break;
                }
                request.extend_from_slice(&chunk[..n]);
            }
            // Tests that only care about the response drop the receiver.
            let _ = sender.send(String::from_utf8_lossy(&request).into_owned());
            let _ = stream.write_all(response);
        });
        (address, receiver)
    }

    fn soon() -> Instant {
        Instant::now() + Duration::from_secs(5)
    }

    #[test]
    fn refuses_every_special_purpose_ipv4_range() {
        for text in [
            "0.0.0.0",
            "0.1.2.3",
            "10.0.0.1",
            "100.64.0.1",
            "100.127.255.255",
            "127.0.0.1",
            "127.255.255.254",
            "169.254.1.1",
            "169.254.169.254",
            "172.16.0.1",
            "172.31.255.255",
            "192.0.0.1",
            "192.0.0.170",
            "192.0.2.1",
            "192.88.99.1",
            "192.168.1.1",
            "198.18.0.1",
            "198.19.255.255",
            "198.51.100.1",
            "203.0.113.1",
            "224.0.0.1",
            "239.255.255.255",
            "240.0.0.1",
            "255.255.255.255",
        ] {
            let ip: IpAddr = text.parse().unwrap();
            assert!(!is_public_ip(ip), "{text} must not be public");
        }
        for text in [
            "93.184.216.34",
            "8.8.8.8",
            "100.63.255.255",
            "100.128.0.1",
            "172.32.0.1",
            "198.17.0.1",
            "198.20.0.1",
            "192.0.1.1",
            "192.0.3.1",
        ] {
            let ip: IpAddr = text.parse().unwrap();
            assert!(is_public_ip(ip), "{text} must be public");
        }
    }

    #[test]
    fn ipv6_fails_closed_outside_global_unicast_and_inside_special_blocks() {
        for text in [
            "::1",
            "::",
            "::ffff:10.0.0.1",
            "::ffff:127.0.0.1",
            "::10.0.0.1",
            "::93.184.216.34",
            "64:ff9b::10.0.0.1",
            "64:ff9b::5db8:d822",
            "64:ff9b:1::1",
            "100::1",
            "fc00::1",
            "fd12::1",
            "fe80::1",
            "fec0::1",
            "ff02::1",
            "2001::1",
            "2001:2::1",
            "2001:10::1",
            "2001:20::1",
            "2001:db8::1",
            "3fff::1",
            "2002:0a00:0001::1",
            "2002:7f00:0001::1",
            "1::1",
            "4000::1",
            "e000::1",
        ] {
            let ip: IpAddr = text.parse().unwrap();
            assert!(!is_public_ip(ip), "{text} must not be public");
        }
        for text in [
            "2606:2800:220:1:248:1893:25c8:1946",
            "2001:4860:4860::8888",
            "2002:5db8:d822::1",
            "2a00::1",
            "3ffe::1",
        ] {
            let ip: IpAddr = text.parse().unwrap();
            assert!(is_public_ip(ip), "{text} must be public");
        }
    }

    #[test]
    fn authorize_refuses_schemes_credentials_and_loopback_names() {
        let policy = Policy::default();
        let err = |u: &str| authorize(&Url::parse(u).unwrap(), &policy).unwrap_err();
        assert_eq!(err("https://example.com/").code, "unsupported_capability");
        assert_eq!(err("file:///etc/hosts").code, "unsupported_capability");
        assert_eq!(err("ftp://example.com/").code, "unsupported_capability");
        let mut with_credentials = Url::parse("http://example.com/").unwrap();
        with_credentials.set_username("court").unwrap();
        assert_eq!(
            authorize(&with_credentials, &policy).unwrap_err().code,
            "permission_denied"
        );
        assert_eq!(err("http://localhost:8080/").code, "permission_denied");
        assert_eq!(err("http://a.localhost/").code, "permission_denied");
        assert_eq!(err("http://127.0.0.1:1/").code, "permission_denied");
        assert_eq!(
            err("http://169.254.169.254/latest/meta-data/").code,
            "permission_denied"
        );
        assert_eq!(err("http://[fd00::1]/").code, "permission_denied");
        assert_eq!(err("http://[::1]:9/").code, "permission_denied");
        assert_eq!(err("http://10.0.0.1/").code, "permission_denied");
        assert_eq!(err("http://192.0.0.170/").code, "permission_denied");
    }

    #[test]
    fn allowlist_matches_exact_origin_only() {
        let policy = policy_with("http://127.0.0.1:4321");
        let ok = authorize(
            &Url::parse("http://127.0.0.1:4321/index.html").unwrap(),
            &policy,
        );
        assert_eq!(
            ok.unwrap(),
            vec!["127.0.0.1:4321".parse::<SocketAddr>().unwrap()]
        );
        let other_port = authorize(&Url::parse("http://127.0.0.1:4322/").unwrap(), &policy);
        assert_eq!(other_port.unwrap_err().code, "permission_denied");
        let other_host = authorize(&Url::parse("http://localhost:4321/").unwrap(), &policy);
        assert_eq!(other_host.unwrap_err().code, "permission_denied");
        assert!(AllowedOrigin::parse("https://127.0.0.1:4321").is_ok());
        assert!(AllowedOrigin::parse("ftp://127.0.0.1:4321").is_err());
        assert!(AllowedOrigin::parse("http://127.0.0.1:4321/path").is_err());
    }

    #[test]
    fn head_parser_refuses_ambiguous_framing() {
        let ok = parse_head(b"HTTP/1.0 302 Found\r\nLocation: /next\r\nContent-Type: text/html\r\nContent-Length: 12\r\n").unwrap();
        assert_eq!((ok.status, ok.content_length), (302, Some(12)));
        assert_eq!(ok.location.as_deref(), Some("/next"));
        assert_eq!(ok.content_type.as_deref(), Some("text/html"));
        let same_twice =
            parse_head(b"HTTP/1.1 200 OK\r\nContent-Length: 5\r\nContent-Length: 5\r\n").unwrap();
        assert_eq!(same_twice.content_length, Some(5));
        for (head, why) in [
            (&b"SMTP 220 hi\r\n"[..], "not http"),
            (b"HTTP/2 200 OK\r\n", "http/2"),
            (b"HTTP/1.1 999 no\r\n", "status range"),
            (b"HTTP/1.1 20 no\r\n", "status width"),
            (b"HTTP/1.1 100 Continue\r\n", "informational"),
            (
                b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n",
                "transfer-encoding",
            ),
            (
                b"HTTP/1.1 200 OK\r\nContent-Length: 5\r\nContent-Length: 6\r\n",
                "conflict",
            ),
            (
                b"HTTP/1.1 200 OK\r\nContent-Length: 5, 6\r\n",
                "list conflict",
            ),
            (b"HTTP/1.1 200 OK\r\nContent-Length: -1\r\n", "negative"),
            (b"HTTP/1.1 200 OK\r\nContent-Length: 1e3\r\n", "not digits"),
            (b"HTTP/1.1 200 OK\r\nContent-Length: 0x10\r\n", "hex"),
            (b"HTTP/1.1 200 OK\r\nnot a header\r\n", "malformed line"),
        ] {
            assert!(parse_head(head).is_err(), "{why} must be refused");
        }
    }

    #[test]
    fn same_origin_compares_scheme_host_and_port() {
        let base = Url::parse("http://127.0.0.1:4321/a/b.html").unwrap();
        assert!(same_origin(
            &base,
            &Url::parse("http://127.0.0.1:4321/app.js").unwrap()
        ));
        assert!(!same_origin(
            &base,
            &Url::parse("http://127.0.0.1:4322/app.js").unwrap()
        ));
        assert!(!same_origin(
            &base,
            &Url::parse("http://example.com/app.js").unwrap()
        ));
    }

    #[test]
    fn budget_refuses_the_thirty_third_fetch_before_any_network() {
        let policy = Policy::default();
        let mut budget = Budget {
            fetches: MAX_FETCHES_PER_TARGET,
            ..Budget::default()
        };
        let err = fetch("http://10.0.0.1/", &policy, &mut budget, soon()).unwrap_err();
        assert_eq!((err.code, err.reason), ("resource_limit", "fetch-count"));
    }

    #[test]
    fn connect_uses_only_the_vetted_addresses() {
        // The URL names a public host, but the only address handed to the
        // connector is the local listener: the request must land there with
        // the URL's host in the Host header, proving no second resolution.
        let (address, received) = serve_once(
            b"HTTP/1.0 200 OK\r\nContent-Type: text/plain\r\nContent-Length: 2\r\n\r\nok",
        );
        let url = Url::parse("http://example.com/vetted").unwrap();
        let hop = get_once(&url, &[address], 1024, soon(), None, None).unwrap();
        assert_eq!(
            (hop.status, hop.body.as_slice(), hop.framing),
            (200, &b"ok"[..], Framing::ContentLength)
        );
        let request = received.recv().unwrap();
        assert!(request.starts_with("GET /vetted HTTP/1.0\r\n"), "{request}");
        assert!(request.contains("Host: example.com\r\n"), "{request}");
    }

    #[test]
    fn redirect_hops_are_reauthorized_against_the_policy() {
        let (private_hop, _) = serve_once(
            b"HTTP/1.0 302 Found\r\nLocation: http://10.0.0.1/secret\r\nContent-Length: 0\r\n\r\n",
        );
        let policy = policy_with(&format!("http://127.0.0.1:{}", private_hop.port()));
        let mut budget = Budget::default();
        let err = fetch(
            &format!("http://127.0.0.1:{}/start", private_hop.port()),
            &policy,
            &mut budget,
            soon(),
        )
        .unwrap_err();
        assert_eq!((err.code, err.reason), ("permission_denied", "address"));
        assert_eq!(budget.denied, 1);

        // A redirect to a loopback port that is not allowlisted is refused too.
        let (other_port, _) = serve_once(b"HTTP/1.0 200 OK\r\nContent-Length: 0\r\n\r\n");
        let location = format!(
            "HTTP/1.0 302 Found\r\nLocation: http://127.0.0.1:{}/\r\nContent-Length: 0\r\n\r\n",
            other_port.port()
        );
        let leaked: &'static [u8] = Box::leak(location.into_bytes().into_boxed_slice());
        let (first_hop, _) = serve_once(leaked);
        let policy = policy_with(&format!("http://127.0.0.1:{}", first_hop.port()));
        let mut budget = Budget::default();
        let err = fetch(
            &format!("http://127.0.0.1:{}/", first_hop.port()),
            &policy,
            &mut budget,
            soon(),
        )
        .unwrap_err();
        assert_eq!((err.code, err.reason), ("permission_denied", "address"));
    }

    #[test]
    fn bodies_are_framed_strictly() {
        let (truncated, _) = serve_once(b"HTTP/1.0 200 OK\r\nContent-Length: 10\r\n\r\nshort");
        let err = get_once(
            &Url::parse("http://example.com/").unwrap(),
            &[truncated],
            1024,
            soon(),
            None,
            None,
        )
        .unwrap_err();
        assert_eq!((err.code, err.reason), ("not_found", "response"));

        let (until_close, _) =
            serve_once(b"HTTP/1.0 200 OK\r\nContent-Type: text/html\r\n\r\n<h1>x</h1>");
        let hop = get_once(
            &Url::parse("http://example.com/").unwrap(),
            &[until_close],
            1024,
            soon(),
            None,
            None,
        )
        .unwrap();
        assert_eq!(
            (hop.framing, hop.body.as_slice()),
            (Framing::UntilClose, &b"<h1>x</h1>"[..])
        );

        let (too_big, _) = serve_once(b"HTTP/1.0 200 OK\r\nContent-Length: 2048\r\n\r\n");
        let err = get_once(
            &Url::parse("http://example.com/").unwrap(),
            &[too_big],
            1024,
            soon(),
            None,
            None,
        )
        .unwrap_err();
        assert_eq!((err.code, err.reason), ("resource_limit", "response-bytes"));

        let (chunked, _) = serve_once(
            b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n5\r\nhello\r\n0\r\n\r\n",
        );
        let err = get_once(
            &Url::parse("http://example.com/").unwrap(),
            &[chunked],
            1024,
            soon(),
            None,
            None,
        )
        .unwrap_err();
        assert_eq!((err.code, err.reason), ("not_found", "response"));

        let (no_body, _) = serve_once(b"HTTP/1.1 204 No Content\r\nContent-Length: 99\r\n\r\n");
        let hop = get_once(
            &Url::parse("http://example.com/").unwrap(),
            &[no_body],
            1024,
            soon(),
            None,
            None,
        )
        .unwrap();
        assert_eq!((hop.framing, hop.body.len()), (Framing::NoBody, 0));
    }

    #[test]
    fn pinned_roots_are_bounded_and_never_private() {
        let directory =
            std::env::temp_dir().join(format!("minicon-surf-pinned-test-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&directory);
        std::fs::create_dir_all(&directory).unwrap();
        let with_key = directory.join("leak.pem");
        // The marker is assembled at run time so the repository never carries the literal.
        let marker = format!("-----BEGIN EC {} KEY-----", "PRIVATE");
        std::fs::write(
            &with_key,
            format!(
                "{marker}
not-a-real-key
-----END EC KEY-----
"
            ),
        )
        .unwrap();
        let error = load_pinned_roots(&[&with_key]).unwrap_err();
        assert!(error.contains("private-key block"), "{error}");
        assert!(!error.contains("leak.pem"), "no path in the error: {error}");
        let empty = directory.join("empty.pem");
        std::fs::write(&empty, "nothing here\n").unwrap();
        assert!(
            load_pinned_roots(&[&empty])
                .unwrap_err()
                .contains("no certificate")
        );
        let many: Vec<_> = (0..MAX_PINNED_ROOT_FILES + 1)
            .map(|_| empty.clone())
            .collect();
        assert!(load_pinned_roots(&many).unwrap_err().contains("more than"));
        let big = directory.join("big.pem");
        std::fs::write(&big, "x".repeat(MAX_PINNED_ROOT_FILE_BYTES + 1)).unwrap();
        assert!(load_pinned_roots(&[&big]).unwrap_err().contains("exceeds"));
        let none: [&std::path::Path; 0] = [];
        assert!(load_pinned_roots(&none).is_err());
        let _ = std::fs::remove_dir_all(&directory);
    }

    #[test]
    fn https_is_unsupported_without_pinned_roots_and_downgrade_is_typed() {
        let policy = Policy::default();
        let err = authorize(&Url::parse("https://127.0.0.1:8443/").unwrap(), &policy).unwrap_err();
        assert_eq!(
            (err.code, err.reason),
            ("unsupported_capability", "tls_no_pinned_roots")
        );
        let mut budget = Budget::default();
        let err = fetch("https://127.0.0.1:8443/", &policy, &mut budget, soon()).unwrap_err();
        assert_eq!(err.reason, "tls_no_pinned_roots");
        assert_eq!(budget.denied, 1);
    }
}
