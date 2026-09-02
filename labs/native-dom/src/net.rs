//! Bounded network fetch for the native route.
//!
//! Every fetch is limited by scheme (`http` only), by address policy (no
//! loopback, private, link-local, carrier-grade NAT, multicast, reserved or
//! unspecified addresses unless the exact origin is on the host's explicit
//! allowlist), by redirect count, by header and body bytes, by a per-fetch
//! deadline, and by per-target fetch and byte budgets. Requests are plain
//! HTTP/1.0 `GET` over a fresh TCP connection so the reader never has to
//! trust chunked framing, and no environment proxy is consulted.

use std::io::{self, Read, Write};
use std::net::{IpAddr, Ipv4Addr, Ipv6Addr, SocketAddr, TcpStream, ToSocketAddrs};
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
        if url.scheme() != "http" {
            return Err("only http origins can be allowed".into());
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
            scheme: "http".into(),
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
}

/// Per-target budget shared by navigation, external scripts and `fetch()`.
#[derive(Debug, Default, Clone)]
pub struct Budget {
    pub fetches: usize,
    pub bytes: usize,
    pub denied: usize,
}

#[derive(Debug, Clone)]
pub struct Response {
    pub status: u16,
    pub url: Url,
    pub content_type: Option<String>,
    pub body: Vec<u8>,
    pub redirects: usize,
}

pub fn is_public_ip(ip: IpAddr) -> bool {
    match ip {
        IpAddr::V4(v4) => is_public_v4(v4),
        IpAddr::V6(v6) => {
            if let Some(mapped) = v6.to_ipv4_mapped() {
                return is_public_v4(mapped);
            }
            let segments = v6.segments();
            let unique_local = (segments[0] & 0xfe00) == 0xfc00;
            let link_local = (segments[0] & 0xffc0) == 0xfe80;
            let documentation = segments[0] == 0x2001 && segments[1] == 0x0db8;
            let discard = segments[0] == 0x0100 && segments[1..4] == [0, 0, 0];
            !(v6.is_loopback()
                || v6.is_unspecified()
                || v6.is_multicast()
                || unique_local
                || link_local
                || documentation
                || discard
                || v6 == Ipv6Addr::new(0, 0, 0, 0, 0, 0xffff, 0, 0))
        }
    }
}

fn is_public_v4(v4: Ipv4Addr) -> bool {
    let octets = v4.octets();
    let cgnat = octets[0] == 100 && (64..=127).contains(&octets[1]);
    let this_network = octets[0] == 0;
    let reserved = octets[0] >= 240;
    let documentation = matches!(
        octets,
        [192, 0, 2, _] | [198, 51, 100, _] | [203, 0, 113, _]
    );
    let benchmarking = octets[0] == 198 && (octets[1] & 0xfe) == 18;
    !(v4.is_loopback()
        || v4.is_private()
        || v4.is_link_local()
        || v4.is_broadcast()
        || v4.is_multicast()
        || v4.is_unspecified()
        || cgnat
        || this_network
        || reserved
        || documentation
        || benchmarking)
}

/// Validate a URL against scheme and address policy and return the addresses
/// a connection may use. Allowlisted origins skip address classification.
pub fn authorize(url: &Url, policy: &Policy) -> Result<Vec<SocketAddr>, NetError> {
    if url.scheme() != "http" {
        return Err(NetError::new(
            "unsupported_capability",
            "scheme",
            format!("scheme {} is not offered; only http is", url.scheme()),
        ));
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
    if policy.allowed_origins.iter().any(|o| o.matches(url)) {
        let addresses = match host {
            Host::Ipv4(ip) => vec![SocketAddr::new(IpAddr::V4(ip), port)],
            Host::Ipv6(ip) => vec![SocketAddr::new(IpAddr::V6(ip), port)],
            Host::Domain(name) => resolve(name, port)?,
        };
        return Ok(addresses);
    }
    let addresses = match host {
        Host::Ipv4(ip) => vec![SocketAddr::new(IpAddr::V4(ip), port)],
        Host::Ipv6(ip) => vec![SocketAddr::new(IpAddr::V6(ip), port)],
        Host::Domain(name) => {
            let lowered = name.to_ascii_lowercase();
            if lowered == "localhost" || lowered.ends_with(".localhost") {
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
        IpAddr::V4(_) => "non-public IPv4",
        IpAddr::V6(v6) if v6.is_loopback() => "loopback",
        IpAddr::V6(v6) if (v6.segments()[0] & 0xfe00) == 0xfc00 => "unique-local",
        IpAddr::V6(v6) if (v6.segments()[0] & 0xffc0) == 0xfe80 => "link-local",
        IpAddr::V6(_) => "non-public IPv6",
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
pub fn fetch(
    url: &str,
    policy: &Policy,
    budget: &mut Budget,
    deadline: Instant,
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
        let hop = get_once(&current, &addresses, cap, hop_deadline)?;
        budget.bytes += hop.body.len();
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
            current = current.join(location).map_err(|e| {
                NetError::new(
                    "invalid_request",
                    "redirect",
                    format!("Location is not a valid URL: {e}"),
                )
            })?;
            continue;
        }
        return Ok(Response {
            status: hop.status,
            url: current,
            content_type: hop.content_type,
            body: hop.body,
            redirects,
        });
    }
}

struct Hop {
    status: u16,
    content_type: Option<String>,
    location: Option<String>,
    body: Vec<u8>,
}

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
) -> Result<Hop, NetError> {
    let mut stream = connect(addresses, deadline)?;
    let remaining = deadline.saturating_duration_since(Instant::now());
    stream
        .set_read_timeout(Some(std::cmp::max(remaining, Duration::from_millis(10))))
        .map_err(|e| NetError::new("internal", "socket", e.to_string()))?;
    stream
        .set_write_timeout(Some(std::cmp::max(remaining, Duration::from_millis(10))))
        .map_err(|e| NetError::new("internal", "socket", e.to_string()))?;
    let host_header = match (url.host_str(), url.port()) {
        (Some(host), Some(port)) => format!("{host}:{port}"),
        (Some(host), None) => host.to_owned(),
        _ => return Err(NetError::new("invalid_request", "host", "URL lacks a host")),
    };
    let path = match url.query() {
        Some(query) => format!("{}?{}", url.path(), query),
        None => url.path().to_owned(),
    };
    let request = format!(
        "GET {path} HTTP/1.0\r\nHost: {host_header}\r\nUser-Agent: {USER_AGENT}\r\nAccept: text/html, application/json, text/javascript, */*;q=0.5\r\nConnection: close\r\n\r\n"
    );
    stream
        .write_all(request.as_bytes())
        .map_err(|e| io_error("write", e))?;

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
        let read = stream.read(&mut chunk).map_err(|e| io_error("read", e))?;
        if read == 0 {
            return Err(NetError::new(
                "not_found",
                "response",
                "connection closed before the response headers ended",
            ));
        }
        buffer.extend_from_slice(&chunk[..read]);
        if let Some(index) = find_header_end(&buffer) {
            header_end = index;
            break;
        }
        if buffer.len() > MAX_HEADER_BYTES {
            return Err(NetError::new(
                "resource_limit",
                "header-bytes",
                format!("response headers exceed {MAX_HEADER_BYTES} bytes"),
            ));
        }
    }
    let (status, headers) = parse_head(&buffer[..header_end])?;
    let content_length =
        header_value(&headers, "content-length").and_then(|v| v.parse::<usize>().ok());
    let content_type = header_value(&headers, "content-type").map(|v| v.to_ascii_lowercase());
    let location = header_value(&headers, "location").map(str::to_owned);
    let mut body = buffer[header_end + 4..].to_vec();
    if body.len() > body_cap || content_length.is_some_and(|len| len > body_cap) {
        return Err(NetError::new(
            "resource_limit",
            "response-bytes",
            format!("response body exceeds the {body_cap}-byte cap"),
        ));
    }
    loop {
        if content_length.is_some_and(|len| body.len() >= len) {
            body.truncate(content_length.unwrap_or(body.len()));
            break;
        }
        if Instant::now() >= deadline {
            return Err(NetError::new(
                "deadline_exceeded",
                "read",
                "response body did not arrive before deadline",
            ));
        }
        let read = stream.read(&mut chunk).map_err(|e| io_error("read", e))?;
        if read == 0 {
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
        status,
        content_type,
        location,
        body,
    })
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

fn parse_head(head: &[u8]) -> Result<(u16, Vec<(String, String)>), NetError> {
    let text = std::str::from_utf8(head)
        .map_err(|_| NetError::new("not_found", "response", "response head is not UTF-8"))?;
    let mut lines = text.split("\r\n");
    let status_line = lines.next().unwrap_or_default();
    let mut parts = status_line.splitn(3, ' ');
    let version = parts.next().unwrap_or_default();
    if !version.starts_with("HTTP/1.") {
        return Err(NetError::new(
            "not_found",
            "response",
            "response is not HTTP/1.x",
        ));
    }
    let status = parts
        .next()
        .and_then(|s| s.parse::<u16>().ok())
        .filter(|s| (100..=599).contains(s))
        .ok_or_else(|| NetError::new("not_found", "response", "status code is malformed"))?;
    let headers = lines
        .filter_map(|line| line.split_once(':'))
        .map(|(name, value)| (name.trim().to_ascii_lowercase(), value.trim().to_owned()))
        .collect();
    Ok((status, headers))
}

fn header_value<'a>(headers: &'a [(String, String)], name: &str) -> Option<&'a str> {
    headers
        .iter()
        .find(|(n, _)| n == name)
        .map(|(_, v)| v.as_str())
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

    fn policy_with(origin: &str) -> Policy {
        Policy {
            allowed_origins: vec![AllowedOrigin::parse(origin).unwrap()],
        }
    }

    #[test]
    fn refuses_every_non_public_address_class() {
        for text in [
            "127.0.0.1",
            "10.0.0.1",
            "172.16.5.5",
            "192.168.1.1",
            "169.254.169.254",
            "100.64.0.1",
            "0.0.0.0",
            "255.255.255.255",
            "224.0.0.1",
            "240.0.0.1",
            "192.0.2.1",
            "198.18.0.1",
            "::1",
            "::",
            "fd00::1",
            "fe80::1",
            "ff02::1",
            "::ffff:10.0.0.1",
            "2001:db8::1",
        ] {
            let ip: IpAddr = text.parse().unwrap();
            assert!(!is_public_ip(ip), "{text} must not be public");
        }
        for text in [
            "93.184.216.34",
            "2606:2800:220:1:248:1893:25c8:1946",
            "8.8.8.8",
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
        assert!(AllowedOrigin::parse("https://127.0.0.1:4321").is_err());
        assert!(AllowedOrigin::parse("http://127.0.0.1:4321/path").is_err());
    }

    #[test]
    fn parses_status_and_headers() {
        let (status, headers) =
            parse_head(b"HTTP/1.0 302 Found\r\nLocation: /next\r\nContent-Type: text/html\r\n")
                .unwrap();
        assert_eq!(status, 302);
        assert_eq!(header_value(&headers, "location"), Some("/next"));
        assert_eq!(header_value(&headers, "content-type"), Some("text/html"));
        assert!(parse_head(b"SMTP 220 hi\r\n").is_err());
        assert!(parse_head(b"HTTP/1.1 999 no\r\n").is_err());
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
        let err = fetch(
            "http://10.0.0.1/",
            &policy,
            &mut budget,
            Instant::now() + Duration::from_secs(1),
        )
        .unwrap_err();
        assert_eq!(err.code, "resource_limit");
        assert_eq!(err.reason, "fetch-count");
    }
}
