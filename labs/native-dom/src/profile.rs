//! Engine-backed profiles for the native route (P6, design D1–D6).
//!
//! Portable parts: the cookie jar (RFC 6265 storage and matching where the
//! bounded `http` path can honour them, failing closed elsewhere), the
//! origin-keyed storage with budgets, the sealed record format and the
//! atomic write. Platform part: the master key, which lives only in the
//! macOS keychain; without it persistent profiles fail closed.

use std::cell::Cell;
use std::collections::BTreeMap;
use std::fs::{self, OpenOptions};
use std::io::Write;
use std::path::{Path, PathBuf};
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};

use base64::Engine as _;
use base64::engine::general_purpose::STANDARD;
use chacha20poly1305::aead::{Aead, KeyInit, Payload};
use chacha20poly1305::{XChaCha20Poly1305, XNonce};
use serde_json::{Map, Value, json};
use url::Url;
use zeroize::Zeroizing;

pub const STORE_FORMAT: &str = "minicon-surf.profile-store/1";
pub const PROTOCOL_TAG: &str = "minicon-surf.control/0.0.1";
pub const MAX_COOKIE_BYTES: usize = 4096;
pub const MAX_COOKIES_PER_HOST: usize = 32;
pub const MAX_COOKIES_PER_PROFILE: usize = 256;
pub const MAX_STORAGE_KEYS_PER_ORIGIN: usize = 32;
pub const MAX_STORAGE_VALUE_BYTES: usize = 1024;
pub const MAX_STORAGE_KEY_BYTES: usize = 64;
pub const MAX_ACCOUNTED_BYTES_PER_PROFILE: usize = 128 * 1024;
pub const MAX_RECORD_BYTES: usize = 4 * 1024 * 1024;
/// The origin of fixture targets: storage under it is never persisted.
pub const OPAQUE_ORIGIN: &str = "minicon-surf://court";
/// Pseudo-host of control-plane cookies (`profile.storage.put`): budgeted
/// and persisted, never sent on the network.
pub const CONTROL_HOST: &str = "control";
pub const RECORD_FILE: &str = "profile.v1.sealed";
pub const LOCK_FILE: &str = "writer.lock";

pub fn now_seconds() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_secs())
        .unwrap_or(0)
}

pub fn valid_profile_name(name: &str) -> bool {
    let bytes = name.as_bytes();
    (1..=32).contains(&bytes.len())
        && bytes[0].is_ascii_lowercase() | bytes[0].is_ascii_digit()
        && bytes
            .iter()
            .all(|b| b.is_ascii_lowercase() || b.is_ascii_digit() || *b == b'-')
}

// ------------------------------------------------------------------ cookies

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum SameSite {
    Lax,
    Strict,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Cookie {
    pub name: String,
    pub value: String,
    pub host: String,
    pub path: String,
    pub http_only: bool,
    pub same_site: SameSite,
    /// Unix seconds; `None` is a session cookie (volatile jar).
    pub expires: Option<u64>,
}

impl Cookie {
    fn accounted_bytes(&self) -> usize {
        self.name.len() + self.value.len() + self.host.len() + self.path.len() + 16
    }

    fn to_json(&self) -> Value {
        json!({
            "name":self.name,"value":self.value,"host":self.host,"path":self.path,
            "http_only":self.http_only,"same_site":match self.same_site { SameSite::Lax => "lax", SameSite::Strict => "strict" },
            "expires":self.expires,
        })
    }

    fn from_json(value: &Value) -> Option<Cookie> {
        Some(Cookie {
            name: value["name"].as_str()?.to_owned(),
            value: value["value"].as_str()?.to_owned(),
            host: value["host"].as_str()?.to_owned(),
            path: value["path"].as_str()?.to_owned(),
            http_only: value["http_only"].as_bool()?,
            same_site: match value["same_site"].as_str()? {
                "lax" => SameSite::Lax,
                "strict" => SameSite::Strict,
                _ => return None,
            },
            expires: value["expires"].as_u64(),
        })
    }
}

/// Why a `Set-Cookie` line was refused; the reason is recorded, never the
/// cookie.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum CookieRejection {
    Malformed,
    TooLarge,
    Domain,
    Secure,
    SameSiteNone,
    Prefix,
    Partitioned,
    HostBudget,
    ProfileBudget,
}

impl CookieRejection {
    pub fn name(&self) -> &'static str {
        match self {
            CookieRejection::Malformed => "malformed",
            CookieRejection::TooLarge => "too_large",
            CookieRejection::Domain => "domain",
            CookieRejection::Secure => "secure",
            CookieRejection::SameSiteNone => "samesite_none",
            CookieRejection::Prefix => "prefix",
            CookieRejection::Partitioned => "partitioned",
            CookieRejection::HostBudget => "host_budget",
            CookieRejection::ProfileBudget => "profile_budget",
        }
    }
}

/// RFC 6265 §5.1.4 default-path.
fn default_path(url: &Url) -> String {
    let path = url.path();
    if !path.starts_with('/') {
        return "/".into();
    }
    match path.rfind('/') {
        Some(0) | None => "/".into(),
        Some(index) => path[..index].to_owned(),
    }
}

fn path_matches(cookie_path: &str, request_path: &str) -> bool {
    request_path == cookie_path
        || (request_path.starts_with(cookie_path)
            && (cookie_path.ends_with('/') || request_path[cookie_path.len()..].starts_with('/')))
}

/// The two jars of one profile with one matching rule.
#[derive(Debug, Clone, Default)]
pub struct Jar {
    /// Cookies with an expiry: written through to the record.
    pub persistent: Vec<Cookie>,
    /// Session cookies: memory only, shared by every session of the profile,
    /// gone when the host's profile lifetime ends.
    pub volatile: Vec<Cookie>,
}

impl Jar {
    pub fn len(&self) -> usize {
        self.persistent.len() + self.volatile.len()
    }

    pub fn accounted_bytes(&self) -> usize {
        self.persistent
            .iter()
            .chain(&self.volatile)
            .map(Cookie::accounted_bytes)
            .sum()
    }

    fn count_for_host(&self, host: &str) -> usize {
        self.persistent
            .iter()
            .chain(&self.volatile)
            .filter(|c| c.host == host)
            .count()
    }

    fn remove(&mut self, host: &str, name: &str, path: &str) {
        self.persistent
            .retain(|c| !(c.host == host && c.name == name && c.path == path));
        self.volatile
            .retain(|c| !(c.host == host && c.name == name && c.path == path));
    }

    pub fn expire(&mut self, now: u64) {
        self.persistent
            .retain(|c| c.expires.is_none_or(|expiry| expiry > now));
    }

    /// Parse and store one `Set-Cookie` line received from `url`
    /// (RFC 6265 §5.2/§5.3 subset; everything else fails closed).
    pub fn store(&mut self, url: &Url, line: &str, now: u64) -> Result<(), CookieRejection> {
        let host = url
            .host_str()
            .map(|h| h.to_ascii_lowercase())
            .ok_or(CookieRejection::Malformed)?;
        self.store_for_host(&host, url.path(), &default_path(url), line, now)
    }

    fn store_for_host(
        &mut self,
        host: &str,
        _request_path: &str,
        default_path: &str,
        line: &str,
        now: u64,
    ) -> Result<(), CookieRejection> {
        if line.len() > MAX_COOKIE_BYTES {
            return Err(CookieRejection::TooLarge);
        }
        let mut parts = line.split(';');
        let pair = parts.next().unwrap_or_default().trim();
        let (name, value) = pair.split_once('=').ok_or(CookieRejection::Malformed)?;
        let (name, value) = (name.trim(), value.trim());
        if name.is_empty()
            || name
                .bytes()
                .any(|b| b.is_ascii_control() || b.is_ascii_whitespace() || b == b'=')
            || value.bytes().any(|b| b.is_ascii_control())
        {
            return Err(CookieRejection::Malformed);
        }
        if name.starts_with("__Host-") || name.starts_with("__Secure-") {
            return Err(CookieRejection::Prefix);
        }
        let mut path = default_path.to_owned();
        let mut http_only = false;
        let mut same_site = SameSite::Lax;
        let mut expires: Option<u64> = None;
        let mut max_age: Option<i64> = None;
        for attribute in parts {
            let attribute = attribute.trim();
            let (key, attribute_value) = match attribute.split_once('=') {
                Some((k, v)) => (k.trim().to_ascii_lowercase(), v.trim()),
                None => (attribute.to_ascii_lowercase(), ""),
            };
            match key.as_str() {
                "domain" => {
                    let domain = attribute_value.trim_start_matches('.').to_ascii_lowercase();
                    if domain != host {
                        return Err(CookieRejection::Domain);
                    }
                }
                "path" => {
                    if attribute_value.starts_with('/') {
                        path = attribute_value.to_owned();
                    }
                }
                "secure" => return Err(CookieRejection::Secure),
                "httponly" => http_only = true,
                "samesite" => {
                    same_site = match attribute_value.to_ascii_lowercase().as_str() {
                        "strict" => SameSite::Strict,
                        "lax" => SameSite::Lax,
                        "none" => return Err(CookieRejection::SameSiteNone),
                        _ => SameSite::Lax,
                    }
                }
                "partitioned" => return Err(CookieRejection::Partitioned),
                "max-age" => {
                    max_age = Some(
                        attribute_value
                            .parse::<i64>()
                            .map_err(|_| CookieRejection::Malformed)?,
                    );
                }
                "expires" => {
                    // Only the absolute form the court uses is parsed; an
                    // unparsable date leaves the cookie a session cookie.
                    if let Some(seconds) = parse_http_date(attribute_value) {
                        expires = Some(seconds);
                    }
                }
                _ => {}
            }
        }
        if let Some(max_age) = max_age {
            expires = Some(if max_age <= 0 {
                0
            } else {
                now.saturating_add(max_age as u64)
            });
        }
        // Expired on receipt: delete the existing cookie and stop.
        if expires.is_some_and(|expiry| expiry <= now) {
            self.remove(host, name, &path);
            return Ok(());
        }
        let existed = self
            .persistent
            .iter()
            .chain(&self.volatile)
            .any(|c| c.host == host && c.name == name && c.path == path);
        if !existed {
            if self.count_for_host(host) >= MAX_COOKIES_PER_HOST {
                return Err(CookieRejection::HostBudget);
            }
            if self.len() >= MAX_COOKIES_PER_PROFILE {
                return Err(CookieRejection::ProfileBudget);
            }
        }
        self.remove(host, name, &path);
        let cookie = Cookie {
            name: name.to_owned(),
            value: value.to_owned(),
            host: host.to_owned(),
            path,
            http_only,
            same_site,
            expires,
        };
        if cookie.expires.is_some() {
            self.persistent.push(cookie);
        } else {
            self.volatile.push(cookie);
        }
        Ok(())
    }

    /// The `Cookie` header for a request to `url` issued by a document at
    /// `document_host` (`None`: a document fetch, which is same-site).
    pub fn header_for(&self, url: &Url, document_host: Option<&str>, now: u64) -> Option<String> {
        let host = url.host_str()?.to_ascii_lowercase();
        let same_site = document_host.is_none_or(|d| d.eq_ignore_ascii_case(&host));
        let pairs = self
            .persistent
            .iter()
            .chain(&self.volatile)
            .filter(|c| c.host == host && c.host != CONTROL_HOST)
            .filter(|c| path_matches(&c.path, url.path()))
            .filter(|c| c.expires.is_none_or(|expiry| expiry > now))
            .filter(|c| same_site || !matches!(c.same_site, SameSite::Strict | SameSite::Lax))
            .map(|c| format!("{}={}", c.name, c.value))
            .collect::<Vec<_>>();
        (!pairs.is_empty()).then(|| pairs.join("; "))
    }

    /// What `document.cookie` shows a document at `url`: non-HttpOnly only.
    pub fn document_cookie(&self, url: &Url, now: u64) -> String {
        let Some(host) = url.host_str().map(|h| h.to_ascii_lowercase()) else {
            return String::new();
        };
        self.persistent
            .iter()
            .chain(&self.volatile)
            .filter(|c| c.host == host && !c.http_only)
            .filter(|c| path_matches(&c.path, url.path()))
            .filter(|c| c.expires.is_none_or(|expiry| expiry > now))
            .map(|c| format!("{}={}", c.name, c.value))
            .collect::<Vec<_>>()
            .join("; ")
    }

    /// A control-plane cookie: budgeted and persisted, never sent.
    pub fn put_control(&mut self, key: &str, value: &str, now: u64) -> Result<(), CookieRejection> {
        let line = format!("{key}={value}; Max-Age=31536000");
        self.store_for_host(CONTROL_HOST, "/", "/", &line, now)
    }

    pub fn get_control(&self, key: &str) -> Option<&str> {
        self.persistent
            .iter()
            .find(|c| c.host == CONTROL_HOST && c.name == key)
            .map(|c| c.value.as_str())
    }
}

/// `Expires` in the IMF-fixdate form only (`Wdy, DD Mon YYYY HH:MM:SS GMT`).
fn parse_http_date(value: &str) -> Option<u64> {
    let fields: Vec<&str> = value.split_whitespace().collect();
    if fields.len() != 6 || fields[5] != "GMT" {
        return None;
    }
    let day: u64 = fields[1].parse().ok()?;
    let month = [
        "jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec",
    ]
    .iter()
    .position(|m| m.eq_ignore_ascii_case(fields[2]))? as u64
        + 1;
    let year: u64 = fields[3].parse().ok()?;
    let mut clock = fields[4].split(':');
    let hour: u64 = clock.next()?.parse().ok()?;
    let minute: u64 = clock.next()?.parse().ok()?;
    let second: u64 = clock.next()?.parse().ok()?;
    if year < 1970 || !(1..=12).contains(&month) || !(1..=31).contains(&day) {
        return None;
    }
    // Days since the epoch (proleptic Gregorian).
    let (y, m) = if month <= 2 {
        (year - 1, month + 9)
    } else {
        (year, month - 3)
    };
    let era = y / 400;
    let yoe = y - era * 400;
    let doy = (153 * m + 2) / 5 + day - 1;
    let doe = yoe * 365 + yoe / 4 - yoe / 100 + doy;
    let days = era * 146097 + doe - 719468;
    Some(days * 86400 + hour * 3600 + minute * 60 + second)
}

// ------------------------------------------------------------------ storage

#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct Storage {
    pub origins: BTreeMap<String, BTreeMap<String, String>>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum StorageRejection {
    KeyTooLarge,
    ValueTooLarge,
    KeyBudget,
    ProfileBudget,
}

impl Storage {
    pub fn keys(&self) -> usize {
        self.origins.values().map(BTreeMap::len).sum()
    }

    pub fn accounted_bytes(&self) -> usize {
        self.origins
            .iter()
            .map(|(origin, map)| {
                origin.len()
                    + map
                        .iter()
                        .map(|(k, v)| k.len() + v.len() + 16)
                        .sum::<usize>()
            })
            .sum()
    }

    pub fn set(
        &mut self,
        origin: &str,
        key: &str,
        value: &str,
        other_bytes: usize,
    ) -> Result<(), StorageRejection> {
        if key.len() > MAX_STORAGE_KEY_BYTES || key.is_empty() {
            return Err(StorageRejection::KeyTooLarge);
        }
        if value.len() > MAX_STORAGE_VALUE_BYTES {
            return Err(StorageRejection::ValueTooLarge);
        }
        let map = self.origins.entry(origin.to_owned()).or_default();
        if !map.contains_key(key) && map.len() >= MAX_STORAGE_KEYS_PER_ORIGIN {
            return Err(StorageRejection::KeyBudget);
        }
        let previous = map.get(key).map_or(0, |v| v.len() + key.len() + 16);
        let projected =
            self.accounted_bytes() + other_bytes + key.len() + value.len() + 16 - previous;
        if projected > MAX_ACCOUNTED_BYTES_PER_PROFILE {
            return Err(StorageRejection::ProfileBudget);
        }
        self.origins
            .get_mut(origin)
            .expect("origin exists")
            .insert(key.to_owned(), value.to_owned());
        Ok(())
    }

    pub fn remove(&mut self, origin: &str, key: &str) {
        if let Some(map) = self.origins.get_mut(origin) {
            map.remove(key);
            if map.is_empty() {
                self.origins.remove(origin);
            }
        }
    }

    pub fn clear(&mut self, origin: &str) {
        self.origins.remove(origin);
    }

    pub fn get(&self, origin: &str, key: &str) -> Option<&str> {
        self.origins
            .get(origin)
            .and_then(|map| map.get(key))
            .map(String::as_str)
    }

    pub fn origin_json(&self, origin: &str) -> Value {
        let mut map = Map::new();
        if let Some(entries) = self.origins.get(origin) {
            for (key, value) in entries {
                map.insert(key.clone(), json!(value));
            }
        }
        Value::Object(map)
    }
}

// ------------------------------------------------------------- the record

/// Everything a persistent profile keeps on disk, in the clear only inside
/// the seal.
#[derive(Debug, Clone, Default)]
pub struct RecordData {
    pub persistent_cookies: Vec<Cookie>,
    pub storage: Storage,
}

impl RecordData {
    pub fn to_json(&self, profile_id: &str) -> Value {
        let storage: Map<String, Value> = self
            .storage
            .origins
            .iter()
            .filter(|(origin, _)| origin.as_str() != OPAQUE_ORIGIN)
            .map(|(origin, map)| {
                (
                    origin.clone(),
                    Value::Object(map.iter().map(|(k, v)| (k.clone(), json!(v))).collect()),
                )
            })
            .collect();
        json!({
            "format_version":1,
            "profile":profile_id,
            "cookies":self.persistent_cookies.iter().map(Cookie::to_json).collect::<Vec<_>>(),
            "storage":storage,
        })
    }

    pub fn from_json(value: &Value, profile_id: &str) -> Option<RecordData> {
        if value["format_version"].as_u64() != Some(1)
            || value["profile"].as_str() != Some(profile_id)
        {
            return None;
        }
        let cookies = value["cookies"]
            .as_array()?
            .iter()
            .map(Cookie::from_json)
            .collect::<Option<Vec<_>>>()?;
        if cookies.len() > MAX_COOKIES_PER_PROFILE
            || cookies
                .iter()
                .any(|c| c.expires.is_none() || c.accounted_bytes() > MAX_COOKIE_BYTES + 64)
        {
            return None;
        }
        let mut storage = Storage::default();
        for (origin, map) in value["storage"].as_object()? {
            let map = map.as_object()?;
            if map.len() > MAX_STORAGE_KEYS_PER_ORIGIN {
                return None;
            }
            for (key, entry) in map {
                let entry = entry.as_str()?;
                if key.len() > MAX_STORAGE_KEY_BYTES || entry.len() > MAX_STORAGE_VALUE_BYTES {
                    return None;
                }
                storage
                    .origins
                    .entry(origin.clone())
                    .or_default()
                    .insert(key.clone(), entry.to_owned());
            }
        }
        if storage.accounted_bytes() > MAX_ACCOUNTED_BYTES_PER_PROFILE {
            return None;
        }
        Some(RecordData {
            persistent_cookies: cookies,
            storage,
        })
    }
}

// --------------------------------------------------------------- the seal

#[derive(Debug)]
pub enum StoreError {
    /// No master key can be reached: keychain locked, denied, missing or
    /// interactive, or no key source on this platform.
    KeychainUnavailable(String),
    /// The record does not authenticate, is malformed or out of bounds.
    Corrupt(String),
    /// The disk refused the commit; the previous record is untouched.
    Io(String),
}

impl StoreError {
    pub fn code(&self) -> &'static str {
        match self {
            StoreError::KeychainUnavailable(_) => "unsupported_capability",
            StoreError::Corrupt(_) => "not_found",
            StoreError::Io(_) => "internal",
        }
    }

    pub fn detail(&self) -> String {
        match self {
            StoreError::KeychainUnavailable(d) | StoreError::Corrupt(d) | StoreError::Io(d) => {
                d.clone()
            }
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum StoreMode {
    /// D1: master key only in the macOS keychain.
    KeychainEnvelope,
    /// Explicit experiment knob: master key file under the config directory.
    /// Receipts under this mode are never `observed`.
    KeyfileExperiment,
}

impl StoreMode {
    pub fn name(self) -> &'static str {
        match self {
            StoreMode::KeychainEnvelope => "envelope-keychain",
            StoreMode::KeyfileExperiment => "envelope-keyfile-experiment",
        }
    }
}

// ---------------------------------------------------------- master key

pub const KEYCHAIN_SERVICE: &str = "minicon-surf.native-dom.profile-master-key";
/// One helper exchange must finish within this; a kill after it is failure
/// cleanup, recorded as such, never a memory-recovery mechanism.
pub const HELPER_DEADLINE: Duration = Duration::from_secs(10);
/// The hidden subcommand of this same binary that talks to the keychain.
pub const HELPER_SUBCOMMAND: &str = "keychain-helper";

const HELPER_MAGIC: &[u8; 4] = b"MCSK";
const HELPER_VERSION: u8 = 1;
const OP_WRAP: u8 = 1;
const OP_UNWRAP: u8 = 2;
const ACCOUNT_LEN: usize = 32;
const AAD_MAX: usize = 256;
const PAYLOAD_MAX: usize = 128;
const DEK_LEN: usize = 32;
const NONCE_LEN: usize = 24;
const TAG_LEN: usize = 16;
const WRAPPED_LEN: usize = NONCE_LEN + DEK_LEN + TAG_LEN;
/// Fixed-length, versioned request: magic, version, op, reserved, account,
/// AAD length and padded AAD, payload length and padded payload.
pub const REQUEST_LEN: usize = 8 + ACCOUNT_LEN + 2 + AAD_MAX + 2 + PAYLOAD_MAX;
/// Fixed-length response: magic, version, status, reserved, OSStatus code,
/// open descriptors seen by the helper, payload length and padded payload.
pub const RESPONSE_LEN: usize = 8 + 4 + 2 + 2 + PAYLOAD_MAX;

const STATUS_OK: u8 = 0;
const STATUS_KEYCHAIN: u8 = 1;
const STATUS_MALFORMED: u8 = 2;
const STATUS_FD_WHITELIST: u8 = 3;
const STATUS_DOES_NOT_AUTHENTICATE: u8 = 4;
const STATUS_ENTROPY: u8 = 5;

/// The data key sealed under the master key. Stored once in the record and
/// stable across writes; only the master-key holder can produce or open it.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct WrappedDek {
    pub nonce: Vec<u8>,
    pub sealed: Vec<u8>,
}

impl WrappedDek {
    fn to_bytes(&self) -> Vec<u8> {
        let mut out = self.nonce.clone();
        out.extend_from_slice(&self.sealed);
        out
    }

    fn from_bytes(bytes: &[u8]) -> Option<WrappedDek> {
        if bytes.len() != WRAPPED_LEN {
            return None;
        }
        Some(WrappedDek {
            nonce: bytes[..NONCE_LEN].to_vec(),
            sealed: bytes[NONCE_LEN..].to_vec(),
        })
    }
}

/// The AAD of the wrapped data key binds store format, protocol version,
/// the canonical profile root (as its keychain account) and the profile,
/// so a wrapped key swapped from another root or profile does not open.
fn dek_aad(account: &str, profile_id: &str) -> Vec<u8> {
    format!("{STORE_FORMAT}|{PROTOCOL_TAG}|{account}|{profile_id}|dek").into_bytes()
}

/// Counters the host reports under `owners.profiles.keychain_helper`.
#[derive(Default)]
pub struct HelperStats {
    pub spawns_total: Cell<u64>,
    pub failures_total: Cell<u64>,
    pub timeout_kills_total: Cell<u64>,
    pub last_pid: Cell<Option<u32>>,
    pub last_lifetime_ms: Cell<Option<u64>>,
    pub live: Cell<u32>,
}

impl HelperStats {
    pub fn to_json(&self) -> Value {
        json!({
            "spawns_total":self.spawns_total.get(),"failures_total":self.failures_total.get(),
            "timeout_kills_total":self.timeout_kills_total.get(),"last_pid":self.last_pid.get(),
            "last_lifetime_ms":self.last_lifetime_ms.get(),"live":self.live.get(),
            "deadline_ms":HELPER_DEADLINE.as_millis() as u64,
        })
    }
}

/// Where the master key lives. In keychain mode the host never touches the
/// keychain itself: the master key is used only inside a short-lived helper
/// process of this same binary, which wraps or unwraps the data key.
pub struct KeySource {
    pub mode: StoreMode,
    /// Keychain account: the first 32 hex digits of SHA-256 of the canonical
    /// profile root, so each root has its own master key.
    pub account: String,
    pub keyfile: Option<PathBuf>,
    binary: Option<PathBuf>,
    pub helper: HelperStats,
}

impl KeySource {
    pub fn new(mode: StoreMode, profile_root: &Path, config_dir: &Path) -> KeySource {
        use sha2::Digest;
        let canonical =
            fs::canonicalize(profile_root).unwrap_or_else(|_| profile_root.to_path_buf());
        let digest = sha2::Sha256::digest(canonical.to_string_lossy().as_bytes());
        let account = digest
            .iter()
            .take(16)
            .map(|b| format!("{b:02x}"))
            .collect::<String>();
        KeySource {
            mode,
            account,
            keyfile: (mode == StoreMode::KeyfileExperiment)
                .then(|| config_dir.join("profile-master.key")),
            binary: std::env::current_exe().ok(),
            helper: HelperStats::default(),
        }
    }

    pub fn key_id(&self) -> String {
        format!("{}:{}", self.mode.name(), self.account)
    }

    /// Wrap a freshly generated data key. Keychain mode: one helper exchange.
    pub fn wrap_dek(&self, profile_id: &str, dek: &[u8]) -> Result<WrappedDek, StoreError> {
        if dek.len() != DEK_LEN {
            return Err(StoreError::Io("data key length".into()));
        }
        match self.mode {
            StoreMode::KeychainEnvelope => {
                let bytes = self.helper_call(OP_WRAP, profile_id, dek)?;
                WrappedDek::from_bytes(&bytes).ok_or_else(|| {
                    StoreError::KeychainUnavailable(
                        "helper returned a malformed wrapped key".into(),
                    )
                })
            }
            StoreMode::KeyfileExperiment => {
                let master = self.keyfile_master_key()?;
                let (nonce, sealed) =
                    seal_with_aad(&master, &dek_aad(&self.account, profile_id), dek)?;
                Ok(WrappedDek { nonce, sealed })
            }
        }
    }

    /// Unwrap a stored data key. Keychain mode: one helper exchange that
    /// returns only the data key; the master key never leaves the helper.
    pub fn unwrap_dek(
        &self,
        profile_id: &str,
        wrapped: &WrappedDek,
    ) -> Result<Zeroizing<Vec<u8>>, StoreError> {
        match self.mode {
            StoreMode::KeychainEnvelope => {
                let dek = self.helper_call(OP_UNWRAP, profile_id, &wrapped.to_bytes())?;
                if dek.len() != DEK_LEN {
                    return Err(StoreError::Corrupt("data key length".into()));
                }
                Ok(dek)
            }
            StoreMode::KeyfileExperiment => {
                let master = self.keyfile_master_key()?;
                let dek = open_with_aad(
                    &master,
                    &dek_aad(&self.account, profile_id),
                    &wrapped.nonce,
                    &wrapped.sealed,
                )?;
                if dek.len() != DEK_LEN {
                    return Err(StoreError::Corrupt("data key length".into()));
                }
                Ok(dek)
            }
        }
    }

    fn keyfile_master_key(&self) -> Result<Zeroizing<Vec<u8>>, StoreError> {
        let path = self.keyfile.as_ref().expect("keyfile mode has a path");
        if let Ok(bytes) = fs::read(path) {
            if bytes.len() == DEK_LEN {
                return Ok(Zeroizing::new(bytes));
            }
            return Err(StoreError::Corrupt(
                "master key file has a wrong length".into(),
            ));
        }
        let key = random_bytes(DEK_LEN)?;
        write_private(path, &key).map_err(StoreError::Io)?;
        Ok(key)
    }

    /// One exchange with the helper: spawn (posix_spawn through
    /// `std::process::Command`: absolute program path, no pre-exec closure,
    /// no uid/gid/groups/chroot/cwd), write the fixed request on the child's
    /// stdin and close it, read exactly one fixed response then EOF, reap the
    /// child, and fail closed on any deviation. Secrets travel only inside
    /// the two pipes; nothing goes to argv, the environment, files or logs.
    fn helper_call(
        &self,
        op: u8,
        profile_id: &str,
        payload: &[u8],
    ) -> Result<Zeroizing<Vec<u8>>, StoreError> {
        use std::io::Read;
        use std::process::{Command, Stdio};
        use std::sync::mpsc;
        let Some(binary) = &self.binary else {
            return Err(StoreError::KeychainUnavailable(
                "helper binary path unknown".into(),
            ));
        };
        let request = encode_request(
            op,
            &self.account,
            &dek_aad(&self.account, profile_id),
            payload,
        )
        .ok_or_else(|| StoreError::Io("helper request out of bounds".into()))?;
        let stats = &self.helper;
        stats.spawns_total.set(stats.spawns_total.get() + 1);
        let started = Instant::now();
        let mut child = Command::new(binary)
            .arg(HELPER_SUBCOMMAND)
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::null())
            .spawn()
            .map_err(|e| {
                stats.failures_total.set(stats.failures_total.get() + 1);
                StoreError::KeychainUnavailable(format!("helper spawn failed: {}", e.kind()))
            })?;
        stats.live.set(1);
        stats.last_pid.set(Some(child.id()));
        let fail = |detail: String| {
            stats.failures_total.set(stats.failures_total.get() + 1);
            StoreError::KeychainUnavailable(detail)
        };
        let mut stdin = child.stdin.take().expect("piped stdin");
        let mut stdout = child.stdout.take().expect("piped stdout");
        // A helper that exits early makes this write fail with EPIPE (SIGPIPE
        // is ignored by the Rust runtime); the response, if any, still tells why.
        let write_result = stdin.write_all(&request).and_then(|()| stdin.flush());
        drop(stdin);
        let (sender, receiver) = mpsc::channel();
        let reader = std::thread::spawn(move || {
            let mut buffer = Zeroizing::new(vec![0u8; RESPONSE_LEN]);
            let outcome = stdout.read_exact(&mut buffer).and_then(|()| {
                let mut extra = [0u8; 16];
                stdout.read(&mut extra)
            });
            let _ = sender.send((buffer, outcome));
        });
        let received = receiver.recv_timeout(HELPER_DEADLINE);
        let status = match received {
            Ok(_) => child.wait(),
            Err(_) => {
                let _ = child.kill();
                let _ = child.wait();
                stats
                    .timeout_kills_total
                    .set(stats.timeout_kills_total.get() + 1);
                stats.live.set(0);
                let _ = reader.join();
                return Err(fail("helper deadline exceeded; killed and reaped".into()));
            }
        };
        let _ = reader.join();
        stats.live.set(0);
        stats
            .last_lifetime_ms
            .set(Some(started.elapsed().as_millis() as u64));
        let (buffer, outcome) = received.expect("received above");
        let exit = status.map_err(|e| fail(format!("helper wait failed: {}", e.kind())))?;
        match outcome {
            Ok(0) => {}
            Ok(_) => return Err(fail("helper produced output beyond one response".into())),
            Err(e) => return Err(fail(format!("helper response incomplete: {}", e.kind()))),
        }
        if !exit.success() {
            return Err(fail(format!(
                "helper exited with {}",
                exit.code().unwrap_or(-1)
            )));
        }
        let response =
            decode_response(&buffer).ok_or_else(|| fail("helper response malformed".into()))?;
        if let (Err(e), STATUS_OK) = (write_result, response.status) {
            return Err(fail(format!("helper request write failed: {}", e.kind())));
        }
        match response.status {
            STATUS_OK => Ok(response.payload),
            STATUS_DOES_NOT_AUTHENTICATE => {
                stats.failures_total.set(stats.failures_total.get() + 1);
                Err(StoreError::Corrupt(
                    "wrapped data key does not authenticate for this root and profile".into(),
                ))
            }
            STATUS_KEYCHAIN => Err(fail(format!("keychain read refused: {}", response.code))),
            STATUS_FD_WHITELIST => Err(fail(format!(
                "helper saw {} descriptors beyond stdio",
                response.open_fds
            ))),
            STATUS_MALFORMED => Err(fail("helper rejected the request".into())),
            STATUS_ENTROPY => Err(fail("helper had no entropy".into())),
            other => Err(fail(format!("helper status {other}"))),
        }
    }
}

struct HelperResponse {
    status: u8,
    code: i32,
    open_fds: u16,
    payload: Zeroizing<Vec<u8>>,
}

fn encode_request(op: u8, account: &str, aad: &[u8], payload: &[u8]) -> Option<Zeroizing<Vec<u8>>> {
    if account.len() != ACCOUNT_LEN || aad.len() > AAD_MAX || payload.len() > PAYLOAD_MAX {
        return None;
    }
    let mut out = Zeroizing::new(vec![0u8; REQUEST_LEN]);
    out[..4].copy_from_slice(HELPER_MAGIC);
    out[4] = HELPER_VERSION;
    out[5] = op;
    out[8..8 + ACCOUNT_LEN].copy_from_slice(account.as_bytes());
    let mut at = 8 + ACCOUNT_LEN;
    out[at..at + 2].copy_from_slice(&(aad.len() as u16).to_be_bytes());
    at += 2;
    out[at..at + aad.len()].copy_from_slice(aad);
    at += AAD_MAX;
    out[at..at + 2].copy_from_slice(&(payload.len() as u16).to_be_bytes());
    at += 2;
    out[at..at + payload.len()].copy_from_slice(payload);
    Some(out)
}

struct HelperRequest {
    op: u8,
    account: String,
    aad: Vec<u8>,
    payload: Zeroizing<Vec<u8>>,
}

fn decode_request(bytes: &[u8]) -> Option<HelperRequest> {
    if bytes.len() != REQUEST_LEN
        || &bytes[..4] != HELPER_MAGIC
        || bytes[4] != HELPER_VERSION
        || bytes[6..8] != [0, 0]
    {
        return None;
    }
    let op = bytes[5];
    if op != OP_WRAP && op != OP_UNWRAP {
        return None;
    }
    let account = std::str::from_utf8(&bytes[8..8 + ACCOUNT_LEN]).ok()?;
    if !account.bytes().all(|b| b.is_ascii_hexdigit()) {
        return None;
    }
    let mut at = 8 + ACCOUNT_LEN;
    let aad_len = u16::from_be_bytes([bytes[at], bytes[at + 1]]) as usize;
    at += 2;
    if aad_len == 0 || aad_len > AAD_MAX {
        return None;
    }
    let aad = bytes[at..at + aad_len].to_vec();
    at += AAD_MAX;
    let payload_len = u16::from_be_bytes([bytes[at], bytes[at + 1]]) as usize;
    at += 2;
    if payload_len > PAYLOAD_MAX {
        return None;
    }
    Some(HelperRequest {
        op,
        account: account.to_owned(),
        aad,
        payload: Zeroizing::new(bytes[at..at + payload_len].to_vec()),
    })
}

fn encode_response(status: u8, code: i32, open_fds: u16, payload: &[u8]) -> Zeroizing<Vec<u8>> {
    let mut out = Zeroizing::new(vec![0u8; RESPONSE_LEN]);
    out[..4].copy_from_slice(HELPER_MAGIC);
    out[4] = HELPER_VERSION;
    out[5] = status;
    out[8..12].copy_from_slice(&code.to_be_bytes());
    out[12..14].copy_from_slice(&open_fds.to_be_bytes());
    let len = payload.len().min(PAYLOAD_MAX);
    out[14..16].copy_from_slice(&(len as u16).to_be_bytes());
    out[16..16 + len].copy_from_slice(&payload[..len]);
    out
}

fn decode_response(bytes: &[u8]) -> Option<HelperResponse> {
    if bytes.len() != RESPONSE_LEN
        || &bytes[..4] != HELPER_MAGIC
        || bytes[4] != HELPER_VERSION
        || bytes[6..8] != [0, 0]
    {
        return None;
    }
    let payload_len = u16::from_be_bytes([bytes[14], bytes[15]]) as usize;
    if payload_len > PAYLOAD_MAX {
        return None;
    }
    Some(HelperResponse {
        status: bytes[5],
        code: i32::from_be_bytes([bytes[8], bytes[9], bytes[10], bytes[11]]),
        open_fds: u16::from_be_bytes([bytes[12], bytes[13]]),
        payload: Zeroizing::new(bytes[16..16 + payload_len].to_vec()),
    })
}

/// Serve one request as the helper: no core dumps, no descriptors beyond
/// stdio, keychain UI disabled, one fixed request then EOF on stdin, one
/// fixed response on stdout, then exit. Returns the process exit code: 0
/// whenever a response was written (its status carries any refusal),
/// non-zero only when the exchange itself broke.
#[cfg(target_os = "macos")]
pub fn run_keychain_helper() -> i32 {
    use std::io::Read;
    disable_core_dumps();
    let mut stdout = std::io::stdout().lock();
    let respond = |stdout: &mut std::io::StdoutLock<'_>,
                   status: u8,
                   code: i32,
                   fds: u16,
                   payload: &[u8]|
     -> i32 {
        let bytes = encode_response(status, code, fds, payload);
        match stdout.write_all(&bytes).and_then(|()| stdout.flush()) {
            Ok(()) => 0,
            Err(_) => 65,
        }
    };
    let open_fds = open_descriptors_beyond_stdio();
    if open_fds > 0 {
        return respond(&mut stdout, STATUS_FD_WHITELIST, 0, open_fds, &[]);
    }
    if !disable_keychain_interaction() {
        return respond(&mut stdout, STATUS_KEYCHAIN, -1, 0, &[]);
    }
    let mut request = Zeroizing::new(vec![0u8; REQUEST_LEN]);
    let mut stdin = std::io::stdin().lock();
    if stdin.read_exact(&mut request).is_err() {
        return respond(&mut stdout, STATUS_MALFORMED, 0, 0, &[]);
    }
    let mut extra = [0u8; 1];
    if !matches!(stdin.read(&mut extra), Ok(0)) {
        return respond(&mut stdout, STATUS_MALFORMED, 0, 0, &[]);
    }
    let Some(parsed) = decode_request(&request) else {
        return respond(&mut stdout, STATUS_MALFORMED, 0, 0, &[]);
    };
    drop(request);
    let master = match keychain_master_key(&parsed.account) {
        Ok(key) => key,
        Err(code) => return respond(&mut stdout, STATUS_KEYCHAIN, code, 0, &[]),
    };
    let outcome = match parsed.op {
        OP_WRAP if parsed.payload.len() == DEK_LEN => {
            match seal_with_aad(&master, &parsed.aad, &parsed.payload) {
                Ok((nonce, sealed)) => Ok(Zeroizing::new(WrappedDek { nonce, sealed }.to_bytes())),
                Err(StoreError::Io(_)) => Err(STATUS_ENTROPY),
                Err(_) => Err(STATUS_MALFORMED),
            }
        }
        OP_UNWRAP if parsed.payload.len() == WRAPPED_LEN => {
            match WrappedDek::from_bytes(&parsed.payload) {
                Some(wrapped) => {
                    open_with_aad(&master, &parsed.aad, &wrapped.nonce, &wrapped.sealed)
                        .map_err(|_| STATUS_DOES_NOT_AUTHENTICATE)
                }
                None => Err(STATUS_MALFORMED),
            }
        }
        _ => Err(STATUS_MALFORMED),
    };
    drop(master);
    match outcome {
        Ok(payload) => respond(&mut stdout, STATUS_OK, 0, 0, &payload),
        Err(status) => respond(&mut stdout, status, 0, 0, &[]),
    }
}

#[cfg(not(target_os = "macos"))]
pub fn run_keychain_helper() -> i32 {
    // No master-key source on this platform: the parent fails closed on the exit code.
    66
}

/// Refuse core dumps for a process that holds key material.
#[cfg(target_os = "macos")]
pub fn disable_core_dumps() {
    let limit = libc::rlimit {
        rlim_cur: 0,
        rlim_max: 0,
    };
    // SAFETY: setrlimit reads a valid, fully initialized rlimit struct.
    unsafe {
        libc::setrlimit(libc::RLIMIT_CORE, &limit);
    }
}

#[cfg(not(target_os = "macos"))]
pub fn disable_core_dumps() {}

/// Descriptors open beyond 0, 1 and 2: the helper's whitelist check. Every
/// descriptor the host opens is close-on-exec, so this must be zero.
#[cfg(target_os = "macos")]
fn open_descriptors_beyond_stdio() -> u16 {
    let mut count = 0u16;
    for fd in 3..1024 {
        // SAFETY: F_GETFD only queries the descriptor flags.
        if unsafe { libc::fcntl(fd, libc::F_GETFD) } != -1 {
            count += 1;
        }
    }
    count
}

#[cfg(target_os = "macos")]
fn keychain_master_key(account: &str) -> Result<Zeroizing<Vec<u8>>, i32> {
    use security_framework::passwords::{get_generic_password, set_generic_password};
    match get_generic_password(KEYCHAIN_SERVICE, account) {
        Ok(bytes) if bytes.len() == DEK_LEN => Ok(Zeroizing::new(bytes)),
        Ok(_) => Err(-2),
        Err(error) if error.code() == -25300 => {
            // errSecItemNotFound: first use of this profile root.
            let key = random_bytes(DEK_LEN).map_err(|_| -3)?;
            set_generic_password(KEYCHAIN_SERVICE, account, &key).map_err(|e| e.code())?;
            Ok(key)
        }
        Err(error) => Err(error.code()),
    }
}

/// Disable every keychain UI for the process; returns whether it took.
#[cfg(target_os = "macos")]
pub fn disable_keychain_interaction() -> bool {
    use security_framework::os::macos::keychain::SecKeychain;
    match SecKeychain::disable_user_interaction() {
        Ok(lock) => {
            std::mem::forget(lock);
            true
        }
        Err(_) => false,
    }
}

#[cfg(not(target_os = "macos"))]
pub fn disable_keychain_interaction() -> bool {
    false
}

pub fn random_bytes(len: usize) -> Result<Zeroizing<Vec<u8>>, StoreError> {
    let mut bytes = Zeroizing::new(vec![0u8; len]);
    getrandom::getrandom(&mut bytes).map_err(|e| StoreError::Io(format!("entropy: {e}")))?;
    Ok(bytes)
}

fn aad(profile_id: &str, kind: &str) -> Vec<u8> {
    format!("{STORE_FORMAT}|{PROTOCOL_TAG}|{profile_id}|{kind}").into_bytes()
}

fn seal_with_aad(
    key: &[u8],
    aad: &[u8],
    plaintext: &[u8],
) -> Result<(Vec<u8>, Vec<u8>), StoreError> {
    let cipher = XChaCha20Poly1305::new_from_slice(key)
        .map_err(|_| StoreError::Corrupt("key length".into()))?;
    let nonce_bytes = random_bytes(NONCE_LEN)?;
    let nonce = XNonce::from_slice(&nonce_bytes);
    let sealed = cipher
        .encrypt(
            nonce,
            Payload {
                msg: plaintext,
                aad,
            },
        )
        .map_err(|_| StoreError::Corrupt("seal failed".into()))?;
    Ok((nonce_bytes.to_vec(), sealed))
}

fn open_with_aad(
    key: &[u8],
    aad: &[u8],
    nonce: &[u8],
    sealed: &[u8],
) -> Result<Zeroizing<Vec<u8>>, StoreError> {
    if nonce.len() != NONCE_LEN {
        return Err(StoreError::Corrupt("nonce length".into()));
    }
    let cipher = XChaCha20Poly1305::new_from_slice(key)
        .map_err(|_| StoreError::Corrupt("key length".into()))?;
    cipher
        .decrypt(XNonce::from_slice(nonce), Payload { msg: sealed, aad })
        .map(Zeroizing::new)
        .map_err(|_| StoreError::Corrupt("record does not authenticate".into()))
}

/// The on-disk file: the wrapped data key and the sealed record. Only the
/// metadata is in the clear.
pub struct SealedFile {
    pub key_id: String,
    pub dek_nonce: Vec<u8>,
    pub dek_sealed: Vec<u8>,
    pub record_nonce: Vec<u8>,
    pub record_sealed: Vec<u8>,
}

impl SealedFile {
    pub fn wrapped(&self) -> WrappedDek {
        WrappedDek {
            nonce: self.dek_nonce.clone(),
            sealed: self.dek_sealed.clone(),
        }
    }

    fn to_json(&self, profile_id: &str) -> Value {
        json!({
            "format":STORE_FORMAT,"protocol":PROTOCOL_TAG,"profile":profile_id,"key_id":self.key_id,
            "dek_nonce":STANDARD.encode(&self.dek_nonce),"dek_sealed":STANDARD.encode(&self.dek_sealed),
            "record_nonce":STANDARD.encode(&self.record_nonce),"record_sealed":STANDARD.encode(&self.record_sealed),
        })
    }

    fn from_json(value: &Value, profile_id: &str) -> Result<SealedFile, StoreError> {
        let field = |name: &str| -> Result<Vec<u8>, StoreError> {
            let text = value[name]
                .as_str()
                .ok_or_else(|| StoreError::Corrupt(format!("{name} missing")))?;
            STANDARD
                .decode(text)
                .map_err(|_| StoreError::Corrupt(format!("{name} is not base64")))
        };
        if value["format"].as_str() != Some(STORE_FORMAT)
            || value["protocol"].as_str() != Some(PROTOCOL_TAG)
            || value["profile"].as_str() != Some(profile_id)
        {
            return Err(StoreError::Corrupt(
                "format, protocol or profile mismatch".into(),
            ));
        }
        Ok(SealedFile {
            key_id: value["key_id"].as_str().unwrap_or_default().to_owned(),
            dek_nonce: field("dek_nonce")?,
            dek_sealed: field("dek_sealed")?,
            record_nonce: field("record_nonce")?,
            record_sealed: field("record_sealed")?,
        })
    }
}

/// Seal `data` for `profile_id` under `dek`, storing the stable wrapped key.
/// No master key is involved: committed mutations never touch the keychain.
pub fn seal_record(
    profile_id: &str,
    dek: &[u8],
    wrapped: &WrappedDek,
    key_id: &str,
    data: &RecordData,
) -> Result<Vec<u8>, StoreError> {
    let plaintext =
        serde_json::to_vec(&data.to_json(profile_id)).map_err(|e| StoreError::Io(e.to_string()))?;
    if plaintext.len() > MAX_RECORD_BYTES {
        return Err(StoreError::Io("record exceeds the size limit".into()));
    }
    let (record_nonce, record_sealed) = seal_with_aad(dek, &aad(profile_id, "record"), &plaintext)?;
    let file = SealedFile {
        key_id: key_id.to_owned(),
        dek_nonce: wrapped.nonce.clone(),
        dek_sealed: wrapped.sealed.clone(),
        record_nonce,
        record_sealed,
    };
    serde_json::to_vec_pretty(&file.to_json(profile_id)).map_err(|e| StoreError::Io(e.to_string()))
}

/// Parse a sealed file without any key: bounds and identity checks only.
pub fn parse_record(profile_id: &str, bytes: &[u8]) -> Result<SealedFile, StoreError> {
    if bytes.len() > MAX_RECORD_BYTES {
        return Err(StoreError::Corrupt("record exceeds the size limit".into()));
    }
    let value: Value = serde_json::from_slice(bytes)
        .map_err(|_| StoreError::Corrupt("record is not JSON".into()))?;
    SealedFile::from_json(&value, profile_id)
}

/// Open the record data with an unwrapped data key.
pub fn open_record_data(
    profile_id: &str,
    dek: &[u8],
    file: &SealedFile,
) -> Result<RecordData, StoreError> {
    let plaintext = open_with_aad(
        dek,
        &aad(profile_id, "record"),
        &file.record_nonce,
        &file.record_sealed,
    )?;
    let record: Value = serde_json::from_slice(&plaintext)
        .map_err(|_| StoreError::Corrupt("sealed record is not JSON".into()))?;
    RecordData::from_json(&record, profile_id)
        .ok_or_else(|| StoreError::Corrupt("record is incompatible or exceeds bounds".into()))
}

/// Parse, unwrap and open in one step (the host's load path).
pub fn open_record(
    source: &KeySource,
    profile_id: &str,
    bytes: &[u8],
) -> Result<(Zeroizing<Vec<u8>>, WrappedDek, RecordData), StoreError> {
    let file = parse_record(profile_id, bytes)?;
    let wrapped = file.wrapped();
    let dek = source.unwrap_dek(profile_id, &wrapped)?;
    let data = open_record_data(profile_id, &dek, &file)?;
    Ok((dek, wrapped, data))
}

// ------------------------------------------------------------------- files

#[cfg(unix)]
fn restrict(path: &Path, mode: u32) -> std::io::Result<()> {
    use std::os::unix::fs::PermissionsExt;
    fs::set_permissions(path, fs::Permissions::from_mode(mode))
}

#[cfg(not(unix))]
fn restrict(_path: &Path, _mode: u32) -> std::io::Result<()> {
    Ok(())
}

/// Create a profile directory (`0700`).
pub fn create_profile_dir(root: &Path, name: &str) -> Result<PathBuf, StoreError> {
    let directory = root.join(name);
    fs::create_dir(&directory).map_err(|e| StoreError::Io(format!("create directory: {e}")))?;
    restrict(&directory, 0o700).map_err(|e| StoreError::Io(format!("restrict directory: {e}")))?;
    Ok(directory)
}

fn write_private(path: &Path, bytes: &[u8]) -> Result<(), String> {
    let mut file = OpenOptions::new()
        .create(true)
        .truncate(true)
        .write(true)
        .open(path)
        .map_err(|e| format!("open: {e}"))?;
    restrict(path, 0o600).map_err(|e| format!("restrict: {e}"))?;
    file.write_all(bytes).map_err(|e| format!("write: {e}"))?;
    file.sync_all().map_err(|e| format!("fsync: {e}"))?;
    Ok(())
}

/// D5: temporary file, fsync, atomic rename over the previous record, then
/// directory fsync. On any failure the previous record is untouched.
pub fn commit_record(directory: &Path, bytes: &[u8]) -> Result<usize, StoreError> {
    let final_path = directory.join(RECORD_FILE);
    let temporary = directory.join(format!("{RECORD_FILE}.tmp"));
    if let Err(error) = write_private(&temporary, bytes) {
        let _ = fs::remove_file(&temporary);
        return Err(StoreError::Io(error));
    }
    if let Err(error) = fs::rename(&temporary, &final_path) {
        let _ = fs::remove_file(&temporary);
        return Err(StoreError::Io(format!("rename: {error}")));
    }
    if let Ok(dir) = fs::File::open(directory) {
        let _ = dir.sync_all();
    }
    Ok(bytes.len())
}

/// Validate a profile directory's permissions before reading it.
#[cfg(unix)]
pub fn check_permissions(directory: &Path) -> Result<(), StoreError> {
    use std::os::unix::fs::PermissionsExt;
    let mode = fs::metadata(directory)
        .map_err(|e| StoreError::Corrupt(format!("directory: {e}")))?
        .permissions()
        .mode()
        & 0o777;
    if mode & 0o077 != 0 {
        return Err(StoreError::Corrupt(
            "directory is readable by others".into(),
        ));
    }
    let record = directory.join(RECORD_FILE);
    let mode = fs::metadata(&record)
        .map_err(|e| StoreError::Corrupt(format!("record: {e}")))?
        .permissions()
        .mode()
        & 0o777;
    if mode & 0o077 != 0 {
        return Err(StoreError::Corrupt("record is readable by others".into()));
    }
    Ok(())
}

#[cfg(not(unix))]
pub fn check_permissions(_directory: &Path) -> Result<(), StoreError> {
    Ok(())
}

pub fn lock_path(directory: &Path) -> PathBuf {
    directory.join(LOCK_FILE)
}

/// Open the writer lock file (`0600`) and take the exclusive advisory lock
/// without blocking; `None` means another writer holds it.
pub fn try_lock(directory: &Path) -> Result<Option<fs::File>, StoreError> {
    use fs2::FileExt;
    let path = lock_path(directory);
    let file = OpenOptions::new()
        .create(true)
        .truncate(false)
        .read(true)
        .write(true)
        .open(&path)
        .map_err(|e| StoreError::Io(format!("lock open: {e}")))?;
    restrict(&path, 0o600).map_err(|e| StoreError::Io(format!("lock restrict: {e}")))?;
    match file.try_lock_exclusive() {
        Ok(()) => Ok(Some(file)),
        Err(error) if error.kind() == std::io::ErrorKind::WouldBlock => Ok(None),
        Err(error) => {
            if fs2::lock_contended_error().kind() == error.kind() {
                Ok(None)
            } else {
                Err(StoreError::Io(format!("lock: {error}")))
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn url(text: &str) -> Url {
        Url::parse(text).unwrap()
    }

    #[test]
    fn jar_follows_the_matrix() {
        let mut jar = Jar::default();
        let origin = url("http://127.0.0.1:8080/cookie/set");
        let now = 1_000_000;
        assert_eq!(
            jar.store(&origin, "court=alpha; Path=/; Max-Age=3600", now),
            Ok(())
        );
        assert_eq!(jar.store(&origin, "volatile=v; Path=/", now), Ok(()));
        assert_eq!(
            jar.store(&origin, "s=1; Secure", now),
            Err(CookieRejection::Secure)
        );
        assert_eq!(
            jar.store(&origin, "d=1; Domain=example.com", now),
            Err(CookieRejection::Domain)
        );
        assert_eq!(jar.store(&origin, "d=1; Domain=127.0.0.1", now), Ok(()));
        assert_eq!(
            jar.store(&origin, "n=1; SameSite=None", now),
            Err(CookieRejection::SameSiteNone)
        );
        assert_eq!(
            jar.store(&origin, "__Host-x=1; Path=/", now),
            Err(CookieRejection::Prefix)
        );
        assert_eq!(
            jar.store(&origin, "p=1; Partitioned", now),
            Err(CookieRejection::Partitioned)
        );
        assert_eq!(
            jar.store(&origin, "hidden=h; HttpOnly; Path=/", now),
            Ok(())
        );
        assert_eq!(jar.store(&origin, "gone=g; Max-Age=0", now), Ok(()));
        let header = jar
            .header_for(
                &url("http://127.0.0.1:8080/cookie/echo"),
                Some("127.0.0.1"),
                now,
            )
            .unwrap();
        assert!(
            header.contains("court=alpha")
                && header.contains("volatile=v")
                && header.contains("hidden=h")
                && header.contains("d=1")
        );
        assert!(!header.contains("gone="));
        assert_eq!(
            jar.header_for(
                &url("http://127.0.0.1:8080/cookie/echo"),
                Some("other.invalid"),
                now
            ),
            None,
            "cross-site sends nothing"
        );
        assert_eq!(
            jar.header_for(&url("http://localhost:8080/"), None, now),
            None,
            "another host sees nothing"
        );
        assert!(
            jar.header_for(&url("http://127.0.0.1:9999/"), None, now)
                .is_some(),
            "cookies are host-scoped, not port-scoped (RFC 6265 section 8.5)"
        );
        let visible = jar.document_cookie(&url("http://127.0.0.1:8080/x"), now);
        assert!(visible.contains("court=alpha") && !visible.contains("hidden="));
        assert_eq!(jar.persistent.len(), 1, "only court has an expiry");
        assert_eq!(
            jar.volatile.len(),
            3,
            "volatile, d and hidden are session cookies"
        );
        jar.expire(now + 4000);
        assert_eq!(jar.persistent.len(), 0, "court expired");
        let big = format!("big={}", "x".repeat(4096));
        assert_eq!(
            jar.store(&origin, &big, now),
            Err(CookieRejection::TooLarge)
        );
        let scoped = url("http://127.0.0.1:8080/app/page");
        jar.store(&scoped, "scoped=1", now).unwrap();
        assert!(
            jar.header_for(&url("http://127.0.0.1:8080/app/other"), None, now)
                .unwrap()
                .contains("scoped=1")
        );
        assert!(
            !jar.header_for(&url("http://127.0.0.1:8080/elsewhere"), None, now)
                .unwrap_or_default()
                .contains("scoped=1")
        );
    }

    #[test]
    fn http_date_parses_the_fixdate_form() {
        assert_eq!(parse_http_date("Thu, 01 Jan 1970 00:00:00 GMT"), Some(0));
        assert_eq!(
            parse_http_date("Wed, 21 Oct 2015 07:28:00 GMT"),
            Some(1_445_412_480)
        );
        assert_eq!(parse_http_date("not a date"), None);
    }

    #[test]
    fn storage_budgets_hold() {
        let mut storage = Storage::default();
        for index in 0..MAX_STORAGE_KEYS_PER_ORIGIN {
            storage
                .set("http://a", &format!("k{index}"), "v", 0)
                .unwrap();
        }
        assert_eq!(
            storage.set("http://a", "extra", "v", 0),
            Err(StorageRejection::KeyBudget)
        );
        assert_eq!(
            storage.set("http://b", "big", &"x".repeat(1025), 0),
            Err(StorageRejection::ValueTooLarge)
        );
        assert_eq!(
            storage.set("http://b", "k", "v", MAX_ACCOUNTED_BYTES_PER_PROFILE),
            Err(StorageRejection::ProfileBudget)
        );
        storage.remove("http://a", "k0");
        assert_eq!(storage.keys(), MAX_STORAGE_KEYS_PER_ORIGIN - 1);
    }

    #[test]
    fn sealed_records_round_trip_and_bind_identity() {
        let directory =
            std::env::temp_dir().join(format!("minicon-surf-profile-test-{}", std::process::id()));
        let _ = fs::remove_dir_all(&directory);
        fs::create_dir_all(&directory).unwrap();
        let source = KeySource::new(StoreMode::KeyfileExperiment, &directory, &directory);
        let dek = random_bytes(32).unwrap();
        let mut data = RecordData::default();
        data.persistent_cookies.push(Cookie {
            name: "court".into(),
            value: "court-alpha-7f3a".into(),
            host: "127.0.0.1".into(),
            path: "/".into(),
            http_only: false,
            same_site: SameSite::Lax,
            expires: Some(2_000_000_000),
        });
        data.storage
            .set("http://127.0.0.1:1", "court", "alpha-1", 0)
            .unwrap();
        let wrapped = source.wrap_dek("profile_alpha", &dek).unwrap();
        let bytes = seal_record("profile_alpha", &dek, &wrapped, &source.key_id(), &data).unwrap();
        assert!(
            !bytes.windows(16).any(|w| w == b"court-alpha-7f3a"),
            "the value is not in the clear"
        );
        let (dek_back, wrapped_back, opened) =
            open_record(&source, "profile_alpha", &bytes).unwrap();
        assert_eq!(&*dek_back, &*dek);
        assert_eq!(wrapped_back, wrapped, "the wrapped key is stored unchanged");
        let again = seal_record("profile_alpha", &dek, &wrapped, &source.key_id(), &data).unwrap();
        let (_, wrapped_again, _) = open_record(&source, "profile_alpha", &again).unwrap();
        assert_eq!(
            wrapped_again, wrapped,
            "a rewrite keeps the wrapped key stable"
        );
        let other_root = directory.join("other-root");
        fs::create_dir_all(&other_root).unwrap();
        let other = KeySource::new(StoreMode::KeyfileExperiment, &other_root, &directory);
        assert!(
            matches!(
                other.unwrap_dek("profile_alpha", &wrapped),
                Err(StoreError::Corrupt(_))
            ),
            "another root cannot unwrap the key even with the same master key file"
        );
        assert!(
            matches!(
                source.unwrap_dek("profile_beta", &wrapped),
                Err(StoreError::Corrupt(_))
            ),
            "another profile cannot unwrap the key"
        );
        assert_eq!(opened.persistent_cookies, data.persistent_cookies);
        assert_eq!(
            opened.storage.get("http://127.0.0.1:1", "court"),
            Some("alpha-1")
        );
        assert!(
            matches!(
                open_record(&source, "profile_beta", &bytes),
                Err(StoreError::Corrupt(_))
            ),
            "another identity cannot open it"
        );
        let mut tampered = bytes.clone();
        let index = tampered
            .windows(13)
            .position(|w| w == b"record_sealed")
            .unwrap()
            + 20;
        tampered[index] ^= 0x01;
        assert!(matches!(
            open_record(&source, "profile_alpha", &tampered),
            Err(StoreError::Corrupt(_))
        ));
        commit_record(&directory, &bytes).unwrap();
        assert!(
            directory.join(RECORD_FILE).exists()
                && !directory.join(format!("{RECORD_FILE}.tmp")).exists()
        );
        let _ = fs::remove_dir_all(&directory);
    }

    #[test]
    fn helper_envelopes_are_fixed_length_and_strict() {
        let account = "0123456789abcdef0123456789abcdef";
        let aad = dek_aad(account, "profile_alpha");
        let request = encode_request(OP_WRAP, account, &aad, &[7u8; 32]).unwrap();
        assert_eq!(request.len(), REQUEST_LEN);
        let parsed = decode_request(&request).unwrap();
        assert_eq!(parsed.op, OP_WRAP);
        assert_eq!(parsed.account, account);
        assert_eq!(parsed.aad, aad);
        assert_eq!(&*parsed.payload, &[7u8; 32]);
        assert!(encode_request(OP_WRAP, "short", &aad, &[0; 32]).is_none());
        assert!(encode_request(OP_WRAP, account, &[0; 257], &[0; 32]).is_none());
        assert!(encode_request(OP_WRAP, account, &aad, &[0; 129]).is_none());
        let mut bad = request.clone();
        bad[4] = 2;
        assert!(decode_request(&bad).is_none(), "another version is refused");
        let mut bad = request.clone();
        bad[5] = 9;
        assert!(decode_request(&bad).is_none(), "an unknown op is refused");
        let mut bad = request.clone();
        bad[6] = 1;
        assert!(
            decode_request(&bad).is_none(),
            "reserved bytes must be zero"
        );
        assert!(decode_request(&request[..REQUEST_LEN - 1]).is_none());
        let response = encode_response(STATUS_OK, 0, 0, &[9u8; 72]);
        assert_eq!(response.len(), RESPONSE_LEN);
        let decoded = decode_response(&response).unwrap();
        assert_eq!(decoded.status, STATUS_OK);
        assert_eq!(&*decoded.payload, &[9u8; 72]);
        let refused = encode_response(STATUS_KEYCHAIN, -25293, 0, &[]);
        let decoded = decode_response(&refused).unwrap();
        assert_eq!(
            (decoded.status, decoded.code, decoded.payload.len()),
            (STATUS_KEYCHAIN, -25293, 0)
        );
        let mut bad = response.clone();
        bad[14] = 1;
        assert!(
            decode_response(&bad).is_none(),
            "a payload length over the bound is refused"
        );
        assert!(decode_response(&response[..RESPONSE_LEN - 1]).is_none());
    }
}
