//! Engine-backed profiles for the native route (P6, design D1–D6).
//!
//! Portable parts: the cookie jar (RFC 6265 storage and matching where the
//! bounded `http` path can honour them, failing closed elsewhere), the
//! origin-keyed storage with budgets, the sealed record format and the
//! atomic write. Platform part: the master key, which lives only in the
//! macOS keychain; without it persistent profiles fail closed.

use std::collections::BTreeMap;
use std::fs::{self, OpenOptions};
use std::io::Write;
use std::path::{Path, PathBuf};
use std::time::{SystemTime, UNIX_EPOCH};

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
    let wall = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_secs())
        .unwrap_or(0);
    // Court knob: a fixed clock offset in seconds, read once, so expiry and
    // deletion semantics can be observed without sleeping on the wall clock.
    static OFFSET: std::sync::OnceLock<i64> = std::sync::OnceLock::new();
    let offset = *OFFSET.get_or_init(|| {
        std::env::var("MINICON_SURF_CLOCK_OFFSET_SECONDS")
            .ok()
            .and_then(|v| v.parse::<i64>().ok())
            .unwrap_or(0)
    });
    wall.saturating_add_signed(offset)
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
    /// Only with `Secure`, only from a verified `https` origin.
    None,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Cookie {
    pub name: String,
    pub value: String,
    pub host: String,
    pub path: String,
    pub http_only: bool,
    pub same_site: SameSite,
    /// Set only from a verified `https` origin; sent only to one.
    pub secure: bool,
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
            "http_only":self.http_only,"same_site":match self.same_site { SameSite::Lax => "lax", SameSite::Strict => "strict", SameSite::None => "none" },
            "secure":self.secure,"expires":self.expires,
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
                "none" => SameSite::None,
                _ => return None,
            },
            secure: value["secure"].as_bool().unwrap_or(false),
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
        self.store_for_host(
            &host,
            url.scheme() == "https",
            &default_path(url),
            line,
            now,
        )
    }

    fn store_for_host(
        &mut self,
        host: &str,
        secure_origin: bool,
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
        let mut secure = false;
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
                // D3 on the https cell: `Secure` needs a verified https origin.
                "secure" => {
                    if !secure_origin {
                        return Err(CookieRejection::Secure);
                    }
                    secure = true;
                }
                "httponly" => http_only = true,
                "samesite" => {
                    same_site = match attribute_value.to_ascii_lowercase().as_str() {
                        "strict" => SameSite::Strict,
                        "lax" => SameSite::Lax,
                        "none" => SameSite::None,
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
        if same_site == SameSite::None && !secure {
            return Err(CookieRejection::SameSiteNone);
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
            secure,
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
        let secure_origin = url.scheme() == "https";
        let same_site = document_host.is_none_or(|d| d.eq_ignore_ascii_case(&host));
        let pairs = self
            .persistent
            .iter()
            .chain(&self.volatile)
            .filter(|c| c.host == host && c.host != CONTROL_HOST)
            .filter(|c| !c.secure || secure_origin)
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
        let secure_origin = url.scheme() == "https";
        self.persistent
            .iter()
            .chain(&self.volatile)
            .filter(|c| c.host == host && !c.http_only)
            .filter(|c| !c.secure || secure_origin)
            .filter(|c| path_matches(&c.path, url.path()))
            .filter(|c| c.expires.is_none_or(|expiry| expiry > now))
            .map(|c| format!("{}={}", c.name, c.value))
            .collect::<Vec<_>>()
            .join("; ")
    }

    /// A control-plane cookie: budgeted and persisted, never sent.
    pub fn put_control(&mut self, key: &str, value: &str, now: u64) -> Result<(), CookieRejection> {
        let line = format!("{key}={value}; Max-Age=31536000");
        self.store_for_host(CONTROL_HOST, false, "/", &line, now)
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

/// Where the master key comes from. The key itself is fetched for one seal
/// or open and zeroized right after.
pub struct KeySource {
    pub mode: StoreMode,
    /// Keychain account: the first 32 hex digits of SHA-256 of the canonical
    /// profile root, so each root has its own master key.
    pub account: String,
    pub keyfile: Option<PathBuf>,
}

pub const KEYCHAIN_SERVICE: &str = "minicon-surf.native-dom.profile-master-key";

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
        }
    }

    /// Fetch or create the master key. Never prompts: keychain interaction is
    /// disabled for the host lifetime, so anything needing UI fails closed.
    pub fn master_key(&self) -> Result<Zeroizing<Vec<u8>>, StoreError> {
        match self.mode {
            StoreMode::KeychainEnvelope => keychain_master_key(&self.account),
            StoreMode::KeyfileExperiment => {
                let path = self.keyfile.as_ref().expect("keyfile mode has a path");
                if let Ok(bytes) = fs::read(path) {
                    if bytes.len() == 32 {
                        return Ok(Zeroizing::new(bytes));
                    }
                    return Err(StoreError::Corrupt(
                        "master key file has a wrong length".into(),
                    ));
                }
                let key = random_bytes(32)?;
                write_private(path, &key).map_err(StoreError::Io)?;
                Ok(key)
            }
        }
    }
}

#[cfg(target_os = "macos")]
fn keychain_master_key(account: &str) -> Result<Zeroizing<Vec<u8>>, StoreError> {
    use security_framework::passwords::{get_generic_password, set_generic_password};
    match get_generic_password(KEYCHAIN_SERVICE, account) {
        Ok(bytes) if bytes.len() == 32 => Ok(Zeroizing::new(bytes)),
        Ok(_) => Err(StoreError::KeychainUnavailable(
            "keychain item has a wrong length".into(),
        )),
        Err(error) if error.code() == -25300 => {
            // errSecItemNotFound: first use of this profile root.
            let key = random_bytes(32)?;
            set_generic_password(KEYCHAIN_SERVICE, account, &key).map_err(|e| {
                StoreError::KeychainUnavailable(format!("keychain write refused: {}", e.code()))
            })?;
            Ok(key)
        }
        Err(error) => Err(StoreError::KeychainUnavailable(format!(
            "keychain read refused: {}",
            error.code()
        ))),
    }
}

#[cfg(not(target_os = "macos"))]
fn keychain_master_key(_account: &str) -> Result<Zeroizing<Vec<u8>>, StoreError> {
    Err(StoreError::KeychainUnavailable(
        "no master-key source on this platform".into(),
    ))
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

fn seal(
    key: &[u8],
    profile_id: &str,
    kind: &str,
    plaintext: &[u8],
) -> Result<(Vec<u8>, Vec<u8>), StoreError> {
    let cipher =
        XChaCha20Poly1305::new_from_slice(key).map_err(|_| StoreError::Io("key length".into()))?;
    let nonce_bytes = random_bytes(24)?;
    let nonce = XNonce::from_slice(&nonce_bytes);
    let sealed = cipher
        .encrypt(
            nonce,
            Payload {
                msg: plaintext,
                aad: &aad(profile_id, kind),
            },
        )
        .map_err(|_| StoreError::Io("seal failed".into()))?;
    Ok((nonce_bytes.to_vec(), sealed))
}

fn open(
    key: &[u8],
    profile_id: &str,
    kind: &str,
    nonce: &[u8],
    sealed: &[u8],
) -> Result<Zeroizing<Vec<u8>>, StoreError> {
    if nonce.len() != 24 {
        return Err(StoreError::Corrupt("nonce length".into()));
    }
    let cipher = XChaCha20Poly1305::new_from_slice(key)
        .map_err(|_| StoreError::Corrupt("key length".into()))?;
    cipher
        .decrypt(
            XNonce::from_slice(nonce),
            Payload {
                msg: sealed,
                aad: &aad(profile_id, kind),
            },
        )
        .map(Zeroizing::new)
        .map_err(|_| StoreError::Corrupt("record does not authenticate".into()))
}

/// The on-disk file: a sealed data key and the sealed record. Only the
/// metadata is in the clear.
pub struct SealedFile {
    pub key_id: String,
    pub dek_nonce: Vec<u8>,
    pub dek_sealed: Vec<u8>,
    pub record_nonce: Vec<u8>,
    pub record_sealed: Vec<u8>,
}

impl SealedFile {
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

/// Seal `data` for `profile_id` under `dek`, wrapping `dek` with the master
/// key from `source` (fetched and zeroized inside this call).
pub fn seal_record(
    source: &KeySource,
    profile_id: &str,
    dek: &[u8],
    data: &RecordData,
) -> Result<Vec<u8>, StoreError> {
    let master = source.master_key()?;
    let (dek_nonce, dek_sealed) = seal(&master, profile_id, "dek", dek)?;
    drop(master);
    let plaintext =
        serde_json::to_vec(&data.to_json(profile_id)).map_err(|e| StoreError::Io(e.to_string()))?;
    if plaintext.len() > MAX_RECORD_BYTES {
        return Err(StoreError::Io("record exceeds the size limit".into()));
    }
    let (record_nonce, record_sealed) = seal(dek, profile_id, "record", &plaintext)?;
    let file = SealedFile {
        key_id: format!("{}:{}", source.mode.name(), source.account),
        dek_nonce,
        dek_sealed,
        record_nonce,
        record_sealed,
    };
    serde_json::to_vec_pretty(&file.to_json(profile_id)).map_err(|e| StoreError::Io(e.to_string()))
}

/// Open a sealed file: returns the DEK (kept while the profile is loaded)
/// and the record data.
pub fn open_record(
    source: &KeySource,
    profile_id: &str,
    bytes: &[u8],
) -> Result<(Zeroizing<Vec<u8>>, RecordData), StoreError> {
    if bytes.len() > MAX_RECORD_BYTES {
        return Err(StoreError::Corrupt("record exceeds the size limit".into()));
    }
    let value: Value = serde_json::from_slice(bytes)
        .map_err(|_| StoreError::Corrupt("record is not JSON".into()))?;
    let file = SealedFile::from_json(&value, profile_id)?;
    let master = source.master_key()?;
    let dek = open(
        &master,
        profile_id,
        "dek",
        &file.dek_nonce,
        &file.dek_sealed,
    )?;
    drop(master);
    if dek.len() != 32 {
        return Err(StoreError::Corrupt("data key length".into()));
    }
    let plaintext = open(
        &dek,
        profile_id,
        "record",
        &file.record_nonce,
        &file.record_sealed,
    )?;
    let record: Value = serde_json::from_slice(&plaintext)
        .map_err(|_| StoreError::Corrupt("sealed record is not JSON".into()))?;
    let data = RecordData::from_json(&record, profile_id)
        .ok_or_else(|| StoreError::Corrupt("record is incompatible or exceeds bounds".into()))?;
    Ok((dek, data))
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
            secure: false,
            expires: Some(2_000_000_000),
        });
        data.storage
            .set("http://127.0.0.1:1", "court", "alpha-1", 0)
            .unwrap();
        let bytes = seal_record(&source, "profile_alpha", &dek, &data).unwrap();
        assert!(
            !bytes.windows(16).any(|w| w == b"court-alpha-7f3a"),
            "the value is not in the clear"
        );
        let (dek_back, opened) = open_record(&source, "profile_alpha", &bytes).unwrap();
        assert_eq!(&*dek_back, &*dek);
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
    fn secure_cookies_belong_to_https_origins_only() {
        let mut jar = Jar::default();
        let now = 1_000_000;
        let https = url("https://127.0.0.1:8443/cookie/set");
        let http = url("http://127.0.0.1:8080/cookie/set");
        assert!(matches!(
            jar.store(&http, "a=1; Secure; Path=/", now),
            Err(CookieRejection::Secure)
        ));
        jar.store(&https, "a=1; Secure; Path=/; Max-Age=60", now)
            .unwrap();
        jar.store(&https, "b=2; Path=/; Max-Age=60", now).unwrap();
        jar.store(&https, "c=3; SameSite=None; Secure; Path=/", now)
            .unwrap();
        assert!(matches!(
            jar.store(&https, "d=4; SameSite=None; Path=/", now),
            Err(CookieRejection::SameSiteNone)
        ));
        let over_https = jar.header_for(&https, Some("127.0.0.1"), now).unwrap();
        assert!(
            over_https.contains("a=1") && over_https.contains("b=2") && over_https.contains("c=3")
        );
        let over_http = jar.header_for(&http, Some("127.0.0.1"), now).unwrap();
        assert!(
            !over_http.contains("a=1") && over_http.contains("b=2") && !over_http.contains("c=3")
        );
        assert!(!jar.document_cookie(&http, now).contains("a=1"));
        assert!(jar.document_cookie(&https, now).contains("a=1"));
        let json = jar.persistent[0].to_json();
        assert_eq!(json["secure"], json!(true));
        assert!(Cookie::from_json(&json).unwrap().secure);
    }
}
