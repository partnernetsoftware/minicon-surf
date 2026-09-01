use fs2::FileExt;
use serde::{Deserialize, Serialize};
use serde_json::{Value, json};
use std::collections::BTreeMap;
use std::fs::{self, File, OpenOptions};
use std::io;
use std::mem::size_of;
use std::path::{Path, PathBuf};
use std::thread;
use std::time::{Duration, Instant};

pub mod cdp;

pub const PROTOCOL: &str = "minicon-surf.control";
pub const VERSION: &str = "0.0.1";
pub const MAX_REQUEST_BYTES: usize = 65_536;
pub const MAX_RESPONSE_BYTES: usize = 4_194_304;
pub const MAX_DEPTH: usize = 32;
pub const MAX_COLLECTION: usize = 10_000;
pub const MAX_PROFILES: usize = 8;
pub const MAX_PROFILE_ENTRIES: usize = 32;
pub const MAX_PROFILE_KEY_BYTES: usize = 64;
pub const MAX_PROFILE_VALUE_BYTES: usize = 1024;
pub const MAX_SESSIONS: usize = 16;
pub const MAX_TARGETS: usize = 32;
pub const MAX_SURFACES: usize = 8;
pub const MAX_NODES_PER_TARGET: usize = 128;
pub const SYNTHETIC_PRESENTATION_BYTES: usize = 65_536;
pub const KNOWN_OPERATIONS: &[&str] = &[
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
];

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Request {
    pub request_id: String,
    pub deadline: Duration,
    pub operation: String,
    pub arguments: Value,
}

#[derive(Debug, Clone, Serialize)]
pub struct Response {
    protocol: &'static str,
    version: &'static str,
    request_id: String,
    ok: bool,
    #[serde(skip_serializing_if = "Option::is_none")]
    result: Option<Value>,
    #[serde(skip_serializing_if = "Option::is_none")]
    error: Option<ControlError>,
}

#[derive(Debug)]
pub struct ParseError(Box<Response>);

impl ParseError {
    pub fn into_response(self) -> Response {
        *self.0
    }
}

impl From<Response> for ParseError {
    fn from(response: Response) -> Self {
        Self(Box::new(response))
    }
}

#[derive(Debug, Clone, Serialize)]
pub struct ControlError {
    code: &'static str,
    message: String,
    retryable: bool,
    #[serde(skip_serializing_if = "Option::is_none")]
    scope: Option<Scope>,
    #[serde(skip_serializing_if = "Option::is_none")]
    details: Option<Value>,
}

#[derive(Debug, Clone, Serialize)]
struct Scope {
    kind: &'static str,
    id: String,
}

impl Response {
    fn success(request_id: String, result: Value) -> Self {
        Self {
            protocol: PROTOCOL,
            version: VERSION,
            request_id,
            ok: true,
            result: Some(result),
            error: None,
        }
    }

    pub fn failure(request_id: String, error: ControlError) -> Self {
        Self {
            protocol: PROTOCOL,
            version: VERSION,
            request_id,
            ok: false,
            result: None,
            error: Some(error),
        }
    }

    pub fn invalid(request_id: impl Into<String>, message: impl Into<String>) -> Self {
        Self::failure(
            request_id.into(),
            ControlError::new("invalid_request", message, false),
        )
    }

    pub fn to_bounded_json(&self) -> Vec<u8> {
        let encoded = serde_json::to_vec(self).expect("response serialization is infallible");
        if encoded.len() <= MAX_RESPONSE_BYTES {
            return encoded;
        }
        serde_json::to_vec(&Self::failure(
            self.request_id.clone(),
            ControlError::new("resource_limit", "response exceeds byte limit", true),
        ))
        .expect("bounded failure serialization is infallible")
    }

    pub fn into_outcome(self) -> Result<Value, &'static str> {
        match (self.result, self.error) {
            (Some(result), None) if self.ok => Ok(result),
            (None, Some(error)) if !self.ok => Err(error.code),
            _ => Err("internal"),
        }
    }
}

impl ControlError {
    fn new(code: &'static str, message: impl Into<String>, retryable: bool) -> Self {
        Self {
            code,
            message: truncate_chars(message.into(), 512),
            retryable,
            scope: None,
            details: None,
        }
    }

    fn scoped(mut self, kind: &'static str, id: impl Into<String>) -> Self {
        self.scope = Some(Scope {
            kind,
            id: id.into(),
        });
        self
    }

    fn details(mut self, details: Value) -> Self {
        self.details = Some(details);
        self
    }
}

fn truncate_chars(mut value: String, maximum: usize) -> String {
    if let Some((index, _)) = value.char_indices().nth(maximum) {
        value.truncate(index);
    }
    value
}

pub fn parse_request(bytes: &[u8]) -> Result<Request, ParseError> {
    if bytes.len() > MAX_REQUEST_BYTES {
        return Err(Response::invalid("req_invalid", "request exceeds byte limit").into());
    }
    let document: Value = serde_json::from_slice(bytes)
        .map_err(|_| Response::invalid("req_invalid", "request is not valid JSON"))?;
    validate_bounds(&document, 0)
        .map_err(|message| Response::invalid(request_id_or_fallback(&document), message))?;
    let object = document
        .as_object()
        .ok_or_else(|| Response::invalid("req_invalid", "request is not an object"))?;
    let allowed = [
        "protocol",
        "version",
        "request_id",
        "deadline_ms",
        "operation",
        "arguments",
    ];
    if object.len() != allowed.len() || !allowed.iter().all(|key| object.contains_key(*key)) {
        return Err(
            Response::invalid(request_id_or_fallback(&document), "request fields differ").into(),
        );
    }
    let request_id = object
        .get("request_id")
        .and_then(Value::as_str)
        .filter(|value| valid_typed_id("req", value))
        .ok_or_else(|| Response::invalid("req_invalid", "request_id differs"))?
        .to_owned();
    if object.get("protocol").and_then(Value::as_str) != Some(PROTOCOL)
        || object.get("version").and_then(Value::as_str) != Some(VERSION)
    {
        return Err(Response::invalid(request_id, "protocol or version differs").into());
    }
    let deadline_ms = object
        .get("deadline_ms")
        .and_then(Value::as_u64)
        .filter(|value| (1..=120_000).contains(value))
        .ok_or_else(|| Response::invalid(request_id.clone(), "deadline_ms differs"))?;
    let operation = object
        .get("operation")
        .and_then(Value::as_str)
        .filter(|value| known_operation(value))
        .ok_or_else(|| Response::invalid(request_id.clone(), "operation differs"))?
        .to_owned();
    let arguments = object
        .get("arguments")
        .filter(|value| value.is_object())
        .ok_or_else(|| Response::invalid(request_id.clone(), "arguments is not an object"))?
        .clone();
    Ok(Request {
        request_id,
        deadline: Duration::from_millis(deadline_ms),
        operation,
        arguments,
    })
}

fn request_id_or_fallback(document: &Value) -> String {
    document
        .get("request_id")
        .and_then(Value::as_str)
        .filter(|value| valid_typed_id("req", value))
        .unwrap_or("req_invalid")
        .to_owned()
}

fn validate_bounds(value: &Value, depth: usize) -> Result<(), &'static str> {
    if depth > MAX_DEPTH {
        return Err("request nesting exceeds limit");
    }
    match value {
        Value::Array(values) => {
            if values.len() > MAX_COLLECTION {
                return Err("request collection exceeds limit");
            }
            for value in values {
                validate_bounds(value, depth + 1)?;
            }
        }
        Value::Object(values) => {
            if values.len() > MAX_COLLECTION {
                return Err("request collection exceeds limit");
            }
            for value in values.values() {
                validate_bounds(value, depth + 1)?;
            }
        }
        _ => {}
    }
    Ok(())
}

fn known_operation(value: &str) -> bool {
    KNOWN_OPERATIONS.contains(&value)
}

fn valid_typed_id(kind: &str, value: &str) -> bool {
    let Some(suffix) = value
        .strip_prefix(kind)
        .and_then(|rest| rest.strip_prefix('_'))
    else {
        return false;
    };
    (1..=64).contains(&suffix.len())
        && suffix.bytes().enumerate().all(|(index, byte)| {
            byte.is_ascii_lowercase()
                || byte.is_ascii_digit()
                || (index > 0 && matches!(byte, b'_' | b'-'))
        })
}

#[derive(Debug, Clone, Serialize, Deserialize)]
struct Profile {
    format_version: u32,
    id: String,
    name: String,
    persistence: Persistence,
    policy: ProfilePolicy,
    cookies: BTreeMap<String, String>,
    local_storage: BTreeMap<String, String>,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
enum Persistence {
    Persistent,
    Ephemeral,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
struct ProfilePolicy {
    network: String,
    permissions: String,
}

#[derive(Debug)]
struct ProfileWriterLock {
    file: File,
    sessions: usize,
}

#[derive(Debug, Clone)]
struct Session {
    id: String,
    profile_id: String,
}

#[derive(Debug, Clone)]
struct Target {
    id: String,
    session_id: String,
    realm_id: String,
    revision: u64,
    scroll_y: u64,
    nodes: Vec<Node>,
}

#[derive(Debug)]
struct Surface {
    id: String,
    target_id: String,
    presentation: Box<[u8]>,
}

#[derive(Debug, Clone)]
struct Node {
    id: String,
    role: String,
    name: String,
}

#[derive(Debug, Default)]
pub struct ControlState {
    profiles: BTreeMap<String, Profile>,
    sessions: BTreeMap<String, Session>,
    targets: BTreeMap<String, Target>,
    surfaces: BTreeMap<String, Surface>,
    unavailable_profiles: BTreeMap<String, String>,
    profile_locks: BTreeMap<String, ProfileWriterLock>,
    profile_root: Option<PathBuf>,
    next_profile: u64,
    next_session: u64,
    next_target: u64,
    next_surface: u64,
}

impl ControlState {
    pub fn with_profile_root(profile_root: Option<PathBuf>) -> io::Result<Self> {
        let mut state = Self {
            profile_root,
            ..Self::default()
        };
        let Some(root) = state.profile_root.as_ref() else {
            return Ok(state);
        };
        fs::create_dir_all(root)?;
        for entry in fs::read_dir(root)? {
            let entry = entry?;
            if !entry.file_type()?.is_dir() {
                continue;
            }
            let Some(name) = entry.file_name().to_str().map(str::to_owned) else {
                continue;
            };
            if !valid_profile_name(&name) {
                continue;
            }
            match load_persistent_profile(&entry.path(), &name) {
                Ok(profile) if state.profiles.len() < MAX_PROFILES => {
                    state.profiles.insert(profile.id.clone(), profile);
                }
                Ok(_) => {
                    state
                        .unavailable_profiles
                        .insert(name, "profile capacity exceeded during load".into());
                }
                Err(error) => {
                    state
                        .unavailable_profiles
                        .insert(name, truncate_chars(error.to_string(), 160));
                }
            }
        }
        Ok(state)
    }

    pub fn execute(&mut self, request: Request) -> Response {
        let request_id = request.request_id.clone();
        let outcome = match request.operation.as_str() {
            "profile.create" => self.profile_create(&request.arguments),
            "profile.list" => self.profile_list(&request.arguments),
            "profile.inspect" => self.profile_inspect(&request.arguments),
            "profile.delete" => self.profile_delete(&request.arguments),
            "profile.storage.put" => self.profile_storage_put(&request.arguments),
            "profile.storage.get" => self.profile_storage_get(&request.arguments),
            "profile.policy.set" => self.profile_policy_set(&request.arguments),
            "session.open" => self.session_open(&request.arguments),
            "session.list" => self.session_list(&request.arguments),
            "session.close" => self.session_close(&request.arguments),
            "target.open" => self.target_open(&request.arguments),
            "target.list" => self.target_list(&request.arguments),
            "target.inspect" => self.target_inspect(&request.arguments),
            "target.close" => self.target_close(&request.arguments),
            "target.snapshot" => self.target_snapshot(&request.arguments),
            "target.act" => self.target_act(&request.arguments),
            "target.wait" => self.target_wait(&request.arguments, request.deadline),
            "surface.show" => self.surface_show(&request.arguments),
            "surface.hide" => self.surface_hide(&request.arguments),
            "memory.report" => self.memory_report(&request.arguments),
            _ => Err(ControlError::new(
                "unsupported_operation",
                "operation is reserved but not implemented by synthetic-control",
                false,
            )),
        };
        match outcome {
            Ok(result) => Response::success(request_id, result),
            Err(error) => Response::failure(request_id, error),
        }
    }

    fn profile_create(&mut self, arguments: &Value) -> Result<Value, ControlError> {
        let object = allowed_object(arguments, &["persistence"], &["name", "policy"])?;
        let persistence = match string_field(object, "persistence")? {
            "persistent" => Persistence::Persistent,
            "ephemeral" => Persistence::Ephemeral,
            _ => return Err(invalid("persistence differs")),
        };
        if self.profiles.len() >= MAX_PROFILES {
            return Err(limit("profile capacity reached"));
        }
        self.next_profile += 1;
        let name = match object.get("name") {
            Some(value) => value
                .as_str()
                .filter(|name| valid_profile_name(name))
                .ok_or_else(|| invalid("profile name differs"))?
                .to_owned(),
            None if persistence == Persistence::Ephemeral => {
                format!("ephemeral_{}", self.next_profile)
            }
            None => return Err(invalid("persistent profile requires name")),
        };
        let id = format!("profile_{name}");
        if self.profiles.contains_key(&id) || self.unavailable_profiles.contains_key(&name) {
            return Err(
                ControlError::new("conflict", "profile name already exists", false)
                    .scoped("profile", &id),
            );
        }
        let policy = parse_policy(object.get("policy"))?;
        let profile = Profile {
            format_version: 1,
            id: id.clone(),
            name: name.clone(),
            persistence,
            policy,
            cookies: BTreeMap::new(),
            local_storage: BTreeMap::new(),
        };
        if persistence == Persistence::Persistent {
            let root = self.profile_root.as_ref().ok_or_else(|| {
                ControlError::new(
                    "unsupported_capability",
                    "persistent profiles require an explicit profile root",
                    false,
                )
            })?;
            let directory = root.join(&name);
            fs::create_dir(&directory).map_err(|error| {
                ControlError::new(
                    "conflict",
                    format!("profile directory unavailable: {error}"),
                    false,
                )
                .scoped("profile", &id)
            })?;
            if let Err(error) = restrict_profile_directory(&directory) {
                let _ = fs::remove_dir(&directory);
                return Err(ControlError::new(
                    "internal",
                    format!("profile directory permissions failed: {error}"),
                    false,
                )
                .scoped("profile", &id));
            }
            if let Err(error) = save_persistent_profile(root, &profile) {
                let _ = fs::remove_dir(&directory);
                return Err(ControlError::new(
                    "internal",
                    format!("persistent profile creation failed: {error}"),
                    false,
                )
                .scoped("profile", &id));
            }
        }
        self.profiles.insert(id.clone(), profile);
        Ok(
            json!({"kind":"profile","profile":id,"name":name,"persistence":persistence,"created":true}),
        )
    }

    fn profile_list(&self, arguments: &Value) -> Result<Value, ControlError> {
        exact_object(arguments, &[])?;
        Ok(json!({
            "kind":"profile_list",
            "profiles":self.profiles.values().map(|profile| json!({
                "profile":profile.id,
                "name":profile.name,
                "persistence":profile.persistence,
            })).collect::<Vec<_>>(),
            "unavailable":self.unavailable_profiles.iter().map(|(name,error)| json!({
                "name":name,"error":"corrupt_or_incompatible","detail":error
            })).collect::<Vec<_>>()
        }))
    }

    fn profile_inspect(&self, arguments: &Value) -> Result<Value, ControlError> {
        let object = exact_object(arguments, &["profile"])?;
        let profile_id = typed_field(object, "profile", "profile")?;
        let profile = self
            .profiles
            .get(profile_id)
            .ok_or_else(|| not_found("profile", profile_id))?;
        Ok(profile_summary(profile, self.session_count(profile_id)))
    }

    fn profile_delete(&mut self, arguments: &Value) -> Result<Value, ControlError> {
        let object = exact_object(arguments, &["profile"])?;
        let profile_id = typed_field(object, "profile", "profile")?;
        if self.session_count(profile_id) != 0 {
            return Err(
                ControlError::new("conflict", "profile has live sessions", false)
                    .scoped("profile", profile_id),
            );
        }
        let profile = self
            .profiles
            .get(profile_id)
            .cloned()
            .ok_or_else(|| not_found("profile", profile_id))?;
        if profile.persistence == Persistence::Persistent {
            let root = self.profile_root.as_ref().ok_or_else(|| {
                ControlError::new("internal", "persistent profile root missing", false)
            })?;
            delete_persistent_profile(root, &profile).map_err(|error| {
                ControlError::new(
                    "internal",
                    format!("persistent profile deletion failed: {error}"),
                    false,
                )
                .scoped("profile", profile_id)
            })?;
        }
        self.profiles.remove(profile_id);
        Ok(
            json!({"kind":"profile_deleted","profile":profile.id,"name":profile.name,"persistence":profile.persistence}),
        )
    }

    fn profile_storage_put(&mut self, arguments: &Value) -> Result<Value, ControlError> {
        let object = exact_object(arguments, &["session", "kind", "key", "value"])?;
        let session_id = typed_field(object, "session", "session")?;
        let kind = string_field(object, "kind")?;
        let key = bounded_string_field(object, "key", 1, MAX_PROFILE_KEY_BYTES)?;
        let value = bounded_string_field(object, "value", 0, MAX_PROFILE_VALUE_BYTES)?;
        let profile_id = self
            .sessions
            .get(session_id)
            .ok_or_else(|| not_found("session", session_id))?
            .profile_id
            .clone();
        let mut candidate = self
            .profiles
            .get(&profile_id)
            .cloned()
            .ok_or_else(|| not_found("profile", &profile_id))?;
        let bucket = profile_bucket_mut(&mut candidate, kind)?;
        if !bucket.contains_key(key) && bucket.len() >= MAX_PROFILE_ENTRIES {
            return Err(limit("profile storage entry capacity reached"));
        }
        bucket.insert(key.to_owned(), value.to_owned());
        self.persist_if_needed(&candidate)?;
        self.profiles.insert(profile_id.clone(), candidate);
        Ok(
            json!({"kind":"profile_storage","profile":profile_id,"storage_kind":kind,"key":key,"stored":true}),
        )
    }

    fn profile_storage_get(&self, arguments: &Value) -> Result<Value, ControlError> {
        let object = exact_object(arguments, &["session", "kind", "key"])?;
        let session_id = typed_field(object, "session", "session")?;
        let kind = string_field(object, "kind")?;
        let key = bounded_string_field(object, "key", 1, MAX_PROFILE_KEY_BYTES)?;
        let profile_id = &self
            .sessions
            .get(session_id)
            .ok_or_else(|| not_found("session", session_id))?
            .profile_id;
        let profile = self
            .profiles
            .get(profile_id)
            .ok_or_else(|| not_found("profile", profile_id))?;
        let value = profile_bucket(profile, kind)?.get(key).cloned();
        Ok(
            json!({"kind":"profile_storage","profile":profile_id,"storage_kind":kind,"key":key,"found":value.is_some(),"value":value}),
        )
    }

    fn profile_policy_set(&mut self, arguments: &Value) -> Result<Value, ControlError> {
        let object = exact_object(arguments, &["session", "network", "permissions"])?;
        let session_id = typed_field(object, "session", "session")?;
        let network = policy_network(string_field(object, "network")?)?;
        let permissions = policy_permissions(string_field(object, "permissions")?)?;
        let profile_id = self
            .sessions
            .get(session_id)
            .ok_or_else(|| not_found("session", session_id))?
            .profile_id
            .clone();
        let mut candidate = self
            .profiles
            .get(&profile_id)
            .cloned()
            .ok_or_else(|| not_found("profile", &profile_id))?;
        candidate.policy = ProfilePolicy {
            network: network.to_owned(),
            permissions: permissions.to_owned(),
        };
        self.persist_if_needed(&candidate)?;
        self.profiles.insert(profile_id.clone(), candidate);
        Ok(
            json!({"kind":"profile_policy","profile":profile_id,"network":network,"permissions":permissions}),
        )
    }

    fn session_count(&self, profile_id: &str) -> usize {
        self.sessions
            .values()
            .filter(|session| session.profile_id == profile_id)
            .count()
    }

    fn persist_if_needed(&self, profile: &Profile) -> Result<(), ControlError> {
        if profile.persistence == Persistence::Ephemeral {
            return Ok(());
        }
        let root = self.profile_root.as_ref().ok_or_else(|| {
            ControlError::new("internal", "persistent profile root missing", false)
        })?;
        save_persistent_profile(root, profile).map_err(|error| {
            ControlError::new(
                "internal",
                format!("persistent profile write failed: {error}"),
                true,
            )
            .scoped("profile", &profile.id)
        })
    }

    fn acquire_profile_lock(&mut self, profile_id: &str) -> Result<(), ControlError> {
        let profile = self
            .profiles
            .get(profile_id)
            .ok_or_else(|| not_found("profile", profile_id))?;
        if profile.persistence == Persistence::Ephemeral {
            return Ok(());
        }
        if let Some(lock) = self.profile_locks.get_mut(profile_id) {
            lock.sessions += 1;
            return Ok(());
        }
        let root = self.profile_root.as_ref().ok_or_else(|| {
            ControlError::new("internal", "persistent profile root missing", false)
        })?;
        let lock_path = root.join(&profile.name).join("writer.lock");
        let file = OpenOptions::new()
            .create(true)
            .truncate(false)
            .read(true)
            .write(true)
            .open(lock_path)
            .map_err(|error| {
                ControlError::new(
                    "internal",
                    format!("profile lock open failed: {error}"),
                    true,
                )
            })?;
        restrict_profile_file(&file).map_err(|error| {
            ControlError::new(
                "internal",
                format!("profile lock permissions failed: {error}"),
                false,
            )
            .scoped("profile", profile_id)
        })?;
        file.try_lock_exclusive().map_err(|_| {
            ControlError::new("profile_locked", "profile has another writer", true)
                .scoped("profile", profile_id)
        })?;
        self.profile_locks.insert(
            profile_id.to_owned(),
            ProfileWriterLock { file, sessions: 1 },
        );
        Ok(())
    }

    fn release_profile_lock(&mut self, profile_id: &str) {
        let should_remove = self.profile_locks.get_mut(profile_id).is_some_and(|lock| {
            lock.sessions -= 1;
            lock.sessions == 0
        });
        if should_remove && let Some(lock) = self.profile_locks.remove(profile_id) {
            let _ = FileExt::unlock(&lock.file);
        }
    }

    fn session_open(&mut self, arguments: &Value) -> Result<Value, ControlError> {
        let object = exact_object(arguments, &["profile"])?;
        let profile = typed_field(object, "profile", "profile")?;
        if !self.profiles.contains_key(profile) {
            return Err(not_found("profile", profile));
        }
        if self.sessions.len() >= MAX_SESSIONS {
            return Err(limit("session capacity reached"));
        }
        self.acquire_profile_lock(profile)?;
        self.next_session += 1;
        let id = format!("session_{}", self.next_session);
        self.sessions.insert(
            id.clone(),
            Session {
                id: id.clone(),
                profile_id: profile.to_owned(),
            },
        );
        Ok(json!({"kind":"session","session":id,"profile":profile}))
    }

    fn session_list(&self, arguments: &Value) -> Result<Value, ControlError> {
        exact_object(arguments, &[])?;
        Ok(json!({
            "kind":"session_list",
            "sessions":self.sessions.values().map(|session| json!({
                "session":session.id,
                "profile":session.profile_id,
            })).collect::<Vec<_>>()
        }))
    }

    fn session_close(&mut self, arguments: &Value) -> Result<Value, ControlError> {
        let object = exact_object(arguments, &["session"])?;
        let session_id = typed_field(object, "session", "session")?;
        let session = self
            .sessions
            .remove(session_id)
            .ok_or_else(|| not_found("session", session_id))?;
        let target_ids = self
            .targets
            .values()
            .filter(|target| target.session_id == session_id)
            .map(|target| target.id.clone())
            .collect::<Vec<_>>();
        for target_id in &target_ids {
            self.targets.remove(target_id);
            self.surfaces
                .retain(|_, surface| surface.target_id != *target_id);
        }
        self.release_profile_lock(&session.profile_id);
        Ok(
            json!({"kind":"session_closed","session":session.id,"profile":session.profile_id,"closed_targets":target_ids.len()}),
        )
    }

    fn target_open(&mut self, arguments: &Value) -> Result<Value, ControlError> {
        let object = exact_object(arguments, &["session"])?;
        let session = typed_field(object, "session", "session")?;
        if !self.sessions.contains_key(session) {
            return Err(not_found("session", session));
        }
        if self.targets.len() >= MAX_TARGETS {
            return Err(limit("target capacity reached"));
        }
        self.next_target += 1;
        let id = format!("target_{}", self.next_target);
        let nodes = vec![
            Node {
                id: "node_heading_1".into(),
                role: "heading".into(),
                name: "Memory and Agent Court".into(),
            },
            Node {
                id: "node_button_1".into(),
                role: "button".into(),
                name: "Continue".into(),
            },
        ];
        debug_assert!(nodes.len() <= MAX_NODES_PER_TARGET);
        self.targets.insert(
            id.clone(),
            Target {
                id: id.clone(),
                session_id: session.to_owned(),
                realm_id: format!("realm_{}", self.next_target),
                revision: 0,
                scroll_y: 0,
                nodes,
            },
        );
        Ok(json!({"kind":"target","target":id,"session":session,"revision":0}))
    }

    fn target_list(&self, arguments: &Value) -> Result<Value, ControlError> {
        exact_object(arguments, &[])?;
        Ok(json!({
            "kind":"target_list",
            "targets":self.targets.values().map(target_summary).collect::<Vec<_>>()
        }))
    }

    fn target_inspect(&self, arguments: &Value) -> Result<Value, ControlError> {
        let object = exact_object(arguments, &["target"])?;
        let target_id = typed_field(object, "target", "target")?;
        let target = self
            .targets
            .get(target_id)
            .ok_or_else(|| not_found("target", target_id))?;
        Ok(target_summary(target))
    }

    fn target_close(&mut self, arguments: &Value) -> Result<Value, ControlError> {
        let object = exact_object(arguments, &["target"])?;
        let target_id = typed_field(object, "target", "target")?;
        self.targets
            .remove(target_id)
            .ok_or_else(|| not_found("target", target_id))?;
        self.surfaces
            .retain(|_, surface| surface.target_id != target_id);
        Ok(json!({"kind":"target_closed","target":target_id}))
    }

    fn target_snapshot(&self, arguments: &Value) -> Result<Value, ControlError> {
        let object = exact_object(arguments, &["target", "format", "max_bytes", "max_nodes"])?;
        let target_id = typed_field(object, "target", "target")?;
        if string_field(object, "format")? != "semantic" {
            return Err(invalid("snapshot format differs"));
        }
        let max_bytes = bounded_u64(object, "max_bytes", 1, MAX_RESPONSE_BYTES as u64)? as usize;
        let max_nodes = bounded_u64(object, "max_nodes", 1, MAX_COLLECTION as u64)? as usize;
        let target = self
            .targets
            .get(target_id)
            .ok_or_else(|| not_found("target", target_id))?;
        let nodes = target
            .nodes
            .iter()
            .take(max_nodes)
            .map(|node| {
                json!({
                    "reference":{"target":target.id,"revision":target.revision,"node":node.id},
                    "role":node.role,
                    "name":node.name,
                })
            })
            .collect::<Vec<_>>();
        let result = json!({
            "kind":"semantic_snapshot",
            "target":target.id,
            "revision":target.revision,
            "truncated":nodes.len() < target.nodes.len(),
            "nodes":nodes,
        });
        if serde_json::to_vec(&result).map_or(true, |encoded| encoded.len() > max_bytes) {
            return Err(limit("snapshot exceeds caller byte limit"));
        }
        Ok(result)
    }

    fn target_act(&mut self, arguments: &Value) -> Result<Value, ControlError> {
        let object = exact_object(arguments, &["target", "reference", "action"])?;
        let target_id = typed_field(object, "target", "target")?;
        let reference = exact_object(
            object
                .get("reference")
                .ok_or_else(|| invalid("reference missing"))?,
            &["target", "revision", "node"],
        )?;
        let reference_target = typed_field(reference, "target", "target")?;
        let node_id = typed_field(reference, "node", "node")?;
        let revision = bounded_u64(reference, "revision", 0, u64::MAX)?;
        if reference_target != target_id {
            return Err(invalid("reference target differs"));
        }
        let action = object
            .get("action")
            .and_then(Value::as_object)
            .ok_or_else(|| invalid("action missing"))?;
        let action_kind = string_field(action, "kind")?;
        let target = self
            .targets
            .get_mut(target_id)
            .ok_or_else(|| not_found("target", target_id))?;
        if revision != target.revision {
            return Err(ControlError::new(
                "stale_revision",
                "node reference revision no longer matches the target",
                true,
            )
            .scoped("target", target_id)
            .details(json!({"reference_revision":revision,"current_revision":target.revision})));
        }
        if !target.nodes.iter().any(|node| node.id == node_id) {
            return Err(ControlError::new("not_found", "node does not exist", false)
                .scoped("target", target_id));
        }
        if action_kind == "scroll" {
            if action.len() != 2 {
                return Err(invalid("scroll action fields differ"));
            }
            target.scroll_y = bounded_u64(action, "y", 0, 1_000_000)?;
            target.revision += 1;
            return Ok(
                json!({"kind":"action","target":target.id,"revision":target.revision,"applied":true,"scroll_y":target.scroll_y}),
            );
        }
        if action.len() != 1 {
            return Err(invalid("click action fields differ"));
        }
        if action_kind != "click" {
            return Err(ControlError::new(
                "unsupported_capability",
                "synthetic target only supports click and bounded scroll",
                false,
            ));
        }
        let node = target
            .nodes
            .iter_mut()
            .find(|node| node.id == node_id)
            .ok_or_else(|| {
                ControlError::new("not_found", "node does not exist", false)
                    .scoped("target", target_id)
            })?;
        if node.role != "button" {
            return Err(ControlError::new(
                "unsupported_capability",
                "click requires a button node",
                false,
            ));
        }
        node.name = "Clicked".into();
        target.revision += 1;
        Ok(json!({"kind":"action","target":target.id,"revision":target.revision,"applied":true}))
    }

    fn surface_show(&mut self, arguments: &Value) -> Result<Value, ControlError> {
        let object = exact_object(arguments, &["target"])?;
        let target_id = typed_field(object, "target", "target")?;
        if !self.targets.contains_key(target_id) {
            return Err(not_found("target", target_id));
        }
        if self
            .surfaces
            .values()
            .any(|surface| surface.target_id == target_id)
        {
            return Err(ControlError::new(
                "conflict",
                "target already has an attached surface",
                false,
            )
            .scoped("target", target_id));
        }
        if self.surfaces.len() >= MAX_SURFACES {
            return Err(limit("surface capacity reached"));
        }
        self.next_surface += 1;
        let id = format!("surface_{}", self.next_surface);
        self.surfaces.insert(
            id.clone(),
            Surface {
                id: id.clone(),
                target_id: target_id.to_owned(),
                presentation: vec![0_u8; SYNTHETIC_PRESENTATION_BYTES].into_boxed_slice(),
            },
        );
        Ok(
            json!({"kind":"surface","surface":id,"target":target_id,"state":"headed","presentation_bytes":SYNTHETIC_PRESENTATION_BYTES}),
        )
    }

    fn surface_hide(&mut self, arguments: &Value) -> Result<Value, ControlError> {
        let object = exact_object(arguments, &["surface"])?;
        let surface_id = typed_field(object, "surface", "surface")?;
        let surface = self
            .surfaces
            .remove(surface_id)
            .ok_or_else(|| not_found("surface", surface_id))?;
        Ok(
            json!({"kind":"surface_hidden","surface":surface.id,"target":surface.target_id,"state":"headless","released_presentation_bytes":surface.presentation.len()}),
        )
    }

    fn target_wait(&self, arguments: &Value, deadline: Duration) -> Result<Value, ControlError> {
        let object = exact_object(arguments, &["target", "condition"])?;
        let target_id = typed_field(object, "target", "target")?;
        let condition = exact_object(
            object
                .get("condition")
                .ok_or_else(|| invalid("condition missing"))?,
            &["kind", "revision"],
        )?;
        if string_field(condition, "kind")? != "revision_at_least" {
            return Err(ControlError::new(
                "unsupported_capability",
                "synthetic target only supports revision_at_least",
                false,
            ));
        }
        let expected = bounded_u64(condition, "revision", 0, u64::MAX)?;
        let started = Instant::now();
        loop {
            let target = self
                .targets
                .get(target_id)
                .ok_or_else(|| not_found("target", target_id))?;
            if target.revision >= expected {
                return Ok(
                    json!({"kind":"wait","target":target.id,"revision":target.revision,"matched":true}),
                );
            }
            if started.elapsed() >= deadline {
                return Err(ControlError::new(
                    "deadline_exceeded",
                    "condition was not met before deadline",
                    true,
                )
                .scoped("target", target_id));
            }
            thread::sleep(Duration::from_millis(1));
        }
    }

    fn memory_report(&self, arguments: &Value) -> Result<Value, ControlError> {
        exact_object(arguments, &[])?;
        let profile_bytes = self
            .profiles
            .values()
            .map(|item| {
                size_of::<Profile>()
                    + item.id.capacity()
                    + item.name.capacity()
                    + item.policy.network.capacity()
                    + item.policy.permissions.capacity()
                    + map_owned_bytes(&item.cookies)
                    + map_owned_bytes(&item.local_storage)
            })
            .sum::<usize>();
        let session_bytes = self
            .sessions
            .values()
            .map(|item| size_of::<Session>() + item.id.capacity() + item.profile_id.capacity())
            .sum::<usize>();
        let target_bytes = self
            .targets
            .values()
            .map(|item| {
                size_of::<Target>()
                    + item.id.capacity()
                    + item.session_id.capacity()
                    + item.realm_id.capacity()
                    + item.nodes.capacity() * size_of::<Node>()
                    + item
                        .nodes
                        .iter()
                        .map(|node| {
                            node.id.capacity() + node.role.capacity() + node.name.capacity()
                        })
                        .sum::<usize>()
            })
            .sum::<usize>();
        let surface_bytes = self
            .surfaces
            .values()
            .map(|item| {
                size_of::<Surface>()
                    + item.id.capacity()
                    + item.target_id.capacity()
                    + item.presentation.len()
            })
            .sum::<usize>();
        Ok(json!({
            "kind":"memory_report",
            "semantic":"logical-owned-capacity-lower-bound",
            "owners":{
                "profiles":{"objects":self.profiles.len(),"bytes":profile_bytes,"object_limit":MAX_PROFILES},
                "sessions":{"objects":self.sessions.len(),"bytes":session_bytes,"object_limit":MAX_SESSIONS},
                "targets":{"objects":self.targets.len(),"bytes":target_bytes,"object_limit":MAX_TARGETS},
                "surfaces":{"objects":self.surfaces.len(),"bytes":surface_bytes,"object_limit":MAX_SURFACES},
            },
            "total_accounted_bytes":profile_bytes + session_bytes + target_bytes + surface_bytes,
            "limitations":["excludes allocator and map overhead","not RSS/private/PSS/live heap"],
        }))
    }
}

fn target_summary(target: &Target) -> Value {
    json!({
        "kind":"target",
        "target":target.id,
        "session":target.session_id,
        "realm":target.realm_id,
        "revision":target.revision,
        "scroll_y":target.scroll_y,
    })
}

fn exact_object<'a>(
    value: &'a Value,
    expected: &[&str],
) -> Result<&'a serde_json::Map<String, Value>, ControlError> {
    let object = value
        .as_object()
        .ok_or_else(|| invalid("arguments differ"))?;
    if object.len() != expected.len() || !expected.iter().all(|key| object.contains_key(*key)) {
        return Err(invalid("arguments fields differ"));
    }
    Ok(object)
}

fn allowed_object<'a>(
    value: &'a Value,
    required: &[&str],
    optional: &[&str],
) -> Result<&'a serde_json::Map<String, Value>, ControlError> {
    let object = value
        .as_object()
        .ok_or_else(|| invalid("arguments differ"))?;
    if !required.iter().all(|key| object.contains_key(*key))
        || object
            .keys()
            .any(|key| !required.contains(&key.as_str()) && !optional.contains(&key.as_str()))
    {
        return Err(invalid("arguments fields differ"));
    }
    Ok(object)
}

fn string_field<'a>(
    object: &'a serde_json::Map<String, Value>,
    key: &str,
) -> Result<&'a str, ControlError> {
    object
        .get(key)
        .and_then(Value::as_str)
        .ok_or_else(|| invalid(format!("{key} differs")))
}

fn typed_field<'a>(
    object: &'a serde_json::Map<String, Value>,
    key: &str,
    kind: &str,
) -> Result<&'a str, ControlError> {
    let value = string_field(object, key)?;
    if !valid_typed_id(kind, value) {
        return Err(invalid(format!("{key} ID differs")));
    }
    Ok(value)
}

fn bounded_u64(
    object: &serde_json::Map<String, Value>,
    key: &str,
    minimum: u64,
    maximum: u64,
) -> Result<u64, ControlError> {
    object
        .get(key)
        .and_then(Value::as_u64)
        .filter(|value| (minimum..=maximum).contains(value))
        .ok_or_else(|| invalid(format!("{key} differs")))
}

fn bounded_string_field<'a>(
    object: &'a serde_json::Map<String, Value>,
    key: &str,
    minimum: usize,
    maximum: usize,
) -> Result<&'a str, ControlError> {
    string_field(object, key).and_then(|value| {
        if (minimum..=maximum).contains(&value.len()) {
            Ok(value)
        } else {
            Err(invalid(format!("{key} byte length differs")))
        }
    })
}

fn valid_profile_name(value: &str) -> bool {
    (1..=48).contains(&value.len())
        && value.bytes().enumerate().all(|(index, byte)| {
            byte.is_ascii_lowercase()
                || byte.is_ascii_digit()
                || (index > 0 && matches!(byte, b'_' | b'-'))
        })
}

fn parse_policy(value: Option<&Value>) -> Result<ProfilePolicy, ControlError> {
    let Some(value) = value else {
        return Ok(ProfilePolicy {
            network: "online".into(),
            permissions: "deny_by_default".into(),
        });
    };
    let object = exact_object(value, &["network", "permissions"])?;
    Ok(ProfilePolicy {
        network: policy_network(string_field(object, "network")?)?.into(),
        permissions: policy_permissions(string_field(object, "permissions")?)?.into(),
    })
}

fn policy_network(value: &str) -> Result<&str, ControlError> {
    match value {
        "online" | "offline" => Ok(value),
        _ => Err(invalid("network policy differs")),
    }
}

fn policy_permissions(value: &str) -> Result<&str, ControlError> {
    match value {
        "deny_by_default" | "allow_by_default" => Ok(value),
        _ => Err(invalid("permissions policy differs")),
    }
}

fn profile_bucket<'a>(
    profile: &'a Profile,
    kind: &str,
) -> Result<&'a BTreeMap<String, String>, ControlError> {
    match kind {
        "cookie" => Ok(&profile.cookies),
        "local_storage" => Ok(&profile.local_storage),
        _ => Err(invalid("profile storage kind differs")),
    }
}

fn profile_bucket_mut<'a>(
    profile: &'a mut Profile,
    kind: &str,
) -> Result<&'a mut BTreeMap<String, String>, ControlError> {
    match kind {
        "cookie" => Ok(&mut profile.cookies),
        "local_storage" => Ok(&mut profile.local_storage),
        _ => Err(invalid("profile storage kind differs")),
    }
}

fn profile_summary(profile: &Profile, live_sessions: usize) -> Value {
    json!({
        "kind":"profile",
        "profile":profile.id,
        "name":profile.name,
        "persistence":profile.persistence,
        "policy":{"network":profile.policy.network,"permissions":profile.policy.permissions},
        "storage":{"cookies":profile.cookies.len(),"local_storage":profile.local_storage.len()},
        "live_sessions":live_sessions,
        "entry_limit_per_bucket":MAX_PROFILE_ENTRIES,
        "value_byte_limit":MAX_PROFILE_VALUE_BYTES,
    })
}

fn map_owned_bytes(map: &BTreeMap<String, String>) -> usize {
    map.iter()
        .map(|(key, value)| key.capacity() + value.capacity())
        .sum()
}

fn load_persistent_profile(directory: &Path, expected_name: &str) -> io::Result<Profile> {
    validate_profile_permissions(directory, &directory.join("profile.json"))?;
    let bytes = fs::read(directory.join("profile.json"))?;
    if bytes.len() > MAX_RESPONSE_BYTES {
        return Err(io::Error::new(
            io::ErrorKind::InvalidData,
            "profile record exceeds limit",
        ));
    }
    let profile: Profile = serde_json::from_slice(&bytes)
        .map_err(|error| io::Error::new(io::ErrorKind::InvalidData, error))?;
    validate_persistent_profile(&profile, expected_name)?;
    Ok(profile)
}

fn validate_persistent_profile(profile: &Profile, expected_name: &str) -> io::Result<()> {
    let valid = profile.format_version == 1
        && profile.persistence == Persistence::Persistent
        && profile.name == expected_name
        && valid_profile_name(&profile.name)
        && profile.id == format!("profile_{}", profile.name)
        && profile.cookies.len() <= MAX_PROFILE_ENTRIES
        && profile.local_storage.len() <= MAX_PROFILE_ENTRIES
        && profile
            .cookies
            .iter()
            .chain(&profile.local_storage)
            .all(|(key, value)| {
                (1..=MAX_PROFILE_KEY_BYTES).contains(&key.len())
                    && value.len() <= MAX_PROFILE_VALUE_BYTES
            })
        && policy_network(&profile.policy.network).is_ok()
        && policy_permissions(&profile.policy.permissions).is_ok();
    if valid {
        Ok(())
    } else {
        Err(io::Error::new(
            io::ErrorKind::InvalidData,
            "profile record is incompatible or exceeds bounds",
        ))
    }
}

fn save_persistent_profile(root: &Path, profile: &Profile) -> io::Result<()> {
    validate_persistent_profile(profile, &profile.name)?;
    let directory = root.join(&profile.name);
    let final_path = directory.join("profile.json");
    let temporary_path = directory.join("profile.json.tmp");
    let bytes = serde_json::to_vec(profile)
        .map_err(|error| io::Error::new(io::ErrorKind::InvalidData, error))?;
    if bytes.len() > MAX_RESPONSE_BYTES {
        return Err(io::Error::new(
            io::ErrorKind::InvalidData,
            "profile record exceeds limit",
        ));
    }
    let mut temporary = OpenOptions::new()
        .create(true)
        .truncate(true)
        .write(true)
        .open(&temporary_path)?;
    restrict_profile_file(&temporary)?;
    use std::io::Write as _;
    temporary.write_all(&bytes)?;
    temporary.sync_all()?;
    drop(temporary);
    fs::rename(&temporary_path, &final_path)?;
    Ok(())
}

#[cfg(unix)]
fn restrict_profile_directory(path: &Path) -> io::Result<()> {
    use std::os::unix::fs::PermissionsExt;
    fs::set_permissions(path, fs::Permissions::from_mode(0o700))
}

#[cfg(not(unix))]
fn restrict_profile_directory(_path: &Path) -> io::Result<()> {
    Ok(())
}

#[cfg(unix)]
fn restrict_profile_file(file: &File) -> io::Result<()> {
    use std::os::unix::fs::PermissionsExt;
    file.set_permissions(fs::Permissions::from_mode(0o600))
}

#[cfg(not(unix))]
fn restrict_profile_file(_file: &File) -> io::Result<()> {
    Ok(())
}

#[cfg(unix)]
fn validate_profile_permissions(directory: &Path, record: &Path) -> io::Result<()> {
    use std::os::unix::fs::PermissionsExt;
    if fs::metadata(directory)?.permissions().mode() & 0o077 != 0
        || fs::metadata(record)?.permissions().mode() & 0o077 != 0
    {
        return Err(io::Error::new(
            io::ErrorKind::PermissionDenied,
            "profile permissions are too broad",
        ));
    }
    Ok(())
}

#[cfg(not(unix))]
fn validate_profile_permissions(_directory: &Path, _record: &Path) -> io::Result<()> {
    Ok(())
}

fn delete_persistent_profile(root: &Path, profile: &Profile) -> io::Result<()> {
    let directory = root.join(&profile.name);
    for name in ["profile.json.tmp", "profile.json", "writer.lock"] {
        match fs::remove_file(directory.join(name)) {
            Ok(()) => {}
            Err(error) if error.kind() == io::ErrorKind::NotFound => {}
            Err(error) => return Err(error),
        }
    }
    fs::remove_dir(directory)
}

fn invalid(message: impl Into<String>) -> ControlError {
    ControlError::new("invalid_request", message, false)
}

fn limit(message: impl Into<String>) -> ControlError {
    ControlError::new("resource_limit", message, true)
}

fn not_found(kind: &'static str, id: &str) -> ControlError {
    ControlError::new("not_found", format!("{kind} does not exist"), false).scoped(kind, id)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn request(id: &str, operation: &str, arguments: Value) -> Request {
        Request {
            request_id: id.into(),
            deadline: Duration::from_millis(5),
            operation: operation.into(),
            arguments,
        }
    }

    fn result(response: Response) -> Value {
        assert!(response.ok, "expected success: {:?}", response.error);
        response.result.unwrap()
    }

    #[test]
    fn one_target_has_revision_scoped_actions_and_waits() {
        let mut state = ControlState::default();
        let profile = result(state.execute(request(
            "req_1",
            "profile.create",
            json!({"persistence":"ephemeral"}),
        )))["profile"]
            .as_str()
            .unwrap()
            .to_owned();
        let session = result(state.execute(request(
            "req_2",
            "session.open",
            json!({"profile":profile}),
        )))["session"]
            .as_str()
            .unwrap()
            .to_owned();
        let target = result(state.execute(request(
            "req_3",
            "target.open",
            json!({"session":session}),
        )))["target"]
            .as_str()
            .unwrap()
            .to_owned();
        let snapshot = result(state.execute(request(
            "req_4",
            "target.snapshot",
            json!({"target":target,"format":"semantic","max_bytes":65536,"max_nodes":10}),
        )));
        let reference = snapshot["nodes"][1]["reference"].clone();
        let action = result(state.execute(request(
            "req_5",
            "target.act",
            json!({"target":target,"reference":reference,"action":{"kind":"click"}}),
        )));
        assert_eq!(action["revision"], 1);
        let wait = result(state.execute(request(
            "req_6",
            "target.wait",
            json!({"target":target,"condition":{"kind":"revision_at_least","revision":1}}),
        )));
        assert_eq!(wait["matched"], true);
        let stale = state.execute(request(
            "req_7",
            "target.act",
            json!({"target":target,"reference":snapshot["nodes"][1]["reference"],"action":{"kind":"click"}}),
        ));
        assert!(!stale.ok);
        assert_eq!(stale.error.unwrap().code, "stale_revision");
        let missed_wait = state.execute(request(
            "req_8",
            "target.wait",
            json!({"target":target,"condition":{"kind":"revision_at_least","revision":2}}),
        ));
        assert_eq!(missed_wait.error.unwrap().code, "deadline_exceeded");
    }

    #[test]
    fn capacity_and_memory_owners_are_explicit() {
        let mut state = ControlState::default();
        for index in 0..MAX_PROFILES {
            let response = state.execute(request(
                &format!("req_{index}"),
                "profile.create",
                json!({"persistence":"ephemeral"}),
            ));
            assert!(response.ok);
        }
        let overflow = state.execute(request(
            "req_overflow",
            "profile.create",
            json!({"persistence":"ephemeral"}),
        ));
        assert_eq!(overflow.error.unwrap().code, "resource_limit");
        let report = result(state.execute(request("req_memory", "memory.report", json!({}))));
        assert_eq!(report["owners"]["profiles"]["objects"], MAX_PROFILES);
        assert!(report["total_accounted_bytes"].as_u64().unwrap() > 0);
    }

    #[test]
    fn surface_attachment_releases_memory_without_owning_target_state() {
        let mut state = ControlState::default();
        let profile = result(state.execute(request(
            "req_surface_1",
            "profile.create",
            json!({"persistence":"ephemeral"}),
        )))["profile"]
            .as_str()
            .unwrap()
            .to_owned();
        let session = result(state.execute(request(
            "req_surface_2",
            "session.open",
            json!({"profile":profile}),
        )))["session"]
            .as_str()
            .unwrap()
            .to_owned();
        let target = result(state.execute(request(
            "req_surface_3",
            "target.open",
            json!({"session":session}),
        )))["target"]
            .as_str()
            .unwrap()
            .to_owned();
        let initial = result(state.execute(request(
            "req_surface_4",
            "target.snapshot",
            json!({"target":target,"format":"semantic","max_bytes":65536,"max_nodes":10}),
        )));
        let scroll = result(state.execute(request(
            "req_surface_5",
            "target.act",
            json!({"target":target,"reference":initial["nodes"][0]["reference"],"action":{"kind":"scroll","y":240}}),
        )));
        assert_eq!(scroll["revision"], 1);
        let before = result(state.execute(request("req_surface_6", "memory.report", json!({}))));
        let before_bytes = before["total_accounted_bytes"].as_u64().unwrap();
        let shown = result(state.execute(request(
            "req_surface_7",
            "surface.show",
            json!({"target":target}),
        )));
        let surface = shown["surface"].as_str().unwrap().to_owned();
        let duplicate = state.execute(request(
            "req_surface_8",
            "surface.show",
            json!({"target":target}),
        ));
        assert_eq!(duplicate.error.unwrap().code, "conflict");
        let headed = result(state.execute(request("req_surface_9", "memory.report", json!({}))));
        assert_eq!(headed["owners"]["surfaces"]["objects"], 1);
        assert!(
            headed["total_accounted_bytes"].as_u64().unwrap()
                >= before_bytes + SYNTHETIC_PRESENTATION_BYTES as u64
        );
        result(state.execute(request(
            "req_surface_10",
            "surface.hide",
            json!({"surface":surface}),
        )));
        let after = result(state.execute(request(
            "req_surface_11",
            "target.inspect",
            json!({"target":target}),
        )));
        assert_eq!(after["realm"], "realm_1");
        assert_eq!(after["revision"], 1);
        assert_eq!(after["scroll_y"], 240);
        let headless = result(state.execute(request("req_surface_12", "memory.report", json!({}))));
        assert_eq!(headless["owners"]["surfaces"]["objects"], 0);
        assert_eq!(headless["total_accounted_bytes"], before_bytes);
        let shown_again = result(state.execute(request(
            "req_surface_13",
            "surface.show",
            json!({"target":target}),
        )));
        assert_ne!(shown_again["surface"], shown["surface"]);
        result(state.execute(request(
            "req_surface_14",
            "target.close",
            json!({"target":target}),
        )));
        let closed = result(state.execute(request("req_surface_15", "memory.report", json!({}))));
        assert_eq!(closed["owners"]["surfaces"]["objects"], 0);
        assert_eq!(closed["owners"]["targets"]["objects"], 0);
    }

    #[test]
    fn surface_capacity_is_bounded() {
        let mut state = ControlState::default();
        let profile = result(state.execute(request(
            "req_capacity_profile",
            "profile.create",
            json!({"persistence":"ephemeral"}),
        )))["profile"]
            .as_str()
            .unwrap()
            .to_owned();
        let session = result(state.execute(request(
            "req_capacity_session",
            "session.open",
            json!({"profile":profile}),
        )))["session"]
            .as_str()
            .unwrap()
            .to_owned();
        for index in 0..=MAX_SURFACES {
            let target = result(state.execute(request(
                &format!("req_capacity_target_{index}"),
                "target.open",
                json!({"session":session}),
            )))["target"]
                .as_str()
                .unwrap()
                .to_owned();
            let shown = state.execute(request(
                &format!("req_capacity_surface_{index}"),
                "surface.show",
                json!({"target":target}),
            ));
            if index < MAX_SURFACES {
                assert!(shown.ok);
            } else {
                assert_eq!(shown.error.unwrap().code, "resource_limit");
            }
        }
    }

    #[test]
    fn persistent_profiles_isolate_storage_policy_and_writer_locks() {
        let unique = format!(
            "minicon-surf-profile-test-{}-{}",
            std::process::id(),
            std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .unwrap()
                .as_nanos()
        );
        let root = std::env::temp_dir().join(unique);
        let mut owner = ControlState::with_profile_root(Some(root.clone())).unwrap();
        let alpha = result(owner.execute(request(
            "req_profile_alpha",
            "profile.create",
            json!({"persistence":"persistent","name":"alpha","policy":{"network":"online","permissions":"deny_by_default"}}),
        )))["profile"]
            .as_str()
            .unwrap()
            .to_owned();
        let beta = result(owner.execute(request(
            "req_profile_beta",
            "profile.create",
            json!({"persistence":"persistent","name":"beta","policy":{"network":"online","permissions":"allow_by_default"}}),
        )))["profile"]
            .as_str()
            .unwrap()
            .to_owned();
        let ephemeral = result(owner.execute(request(
            "req_profile_scratch",
            "profile.create",
            json!({"persistence":"ephemeral","name":"scratch"}),
        )))["profile"]
            .as_str()
            .unwrap()
            .to_owned();
        let alpha_session = result(owner.execute(request(
            "req_profile_alpha_session",
            "session.open",
            json!({"profile":alpha}),
        )))["session"]
            .as_str()
            .unwrap()
            .to_owned();
        let beta_session = result(owner.execute(request(
            "req_profile_beta_session",
            "session.open",
            json!({"profile":beta}),
        )))["session"]
            .as_str()
            .unwrap()
            .to_owned();
        let scratch_session = result(owner.execute(request(
            "req_profile_scratch_session",
            "session.open",
            json!({"profile":ephemeral}),
        )))["session"]
            .as_str()
            .unwrap()
            .to_owned();
        for (id, session, value) in [
            ("req_profile_alpha_cookie", &alpha_session, "alpha-cookie"),
            ("req_profile_beta_cookie", &beta_session, "beta-cookie"),
            (
                "req_profile_scratch_cookie",
                &scratch_session,
                "scratch-cookie",
            ),
        ] {
            result(owner.execute(request(
                id,
                "profile.storage.put",
                json!({"session":session,"kind":"cookie","key":"court","value":value}),
            )));
        }
        result(owner.execute(request(
            "req_profile_alpha_local",
            "profile.storage.put",
            json!({"session":alpha_session,"kind":"local_storage","key":"court","value":"alpha-local"}),
        )));
        result(owner.execute(request(
            "req_profile_alpha_policy",
            "profile.policy.set",
            json!({"session":alpha_session,"network":"offline","permissions":"deny_by_default"}),
        )));

        let mut contender = ControlState::with_profile_root(Some(root.clone())).unwrap();
        let locked = contender.execute(request(
            "req_profile_locked",
            "session.open",
            json!({"profile":alpha}),
        ));
        assert_eq!(locked.error.unwrap().code, "profile_locked");
        let profiles =
            result(contender.execute(request("req_profile_reloaded", "profile.list", json!({}))));
        assert_eq!(profiles["profiles"].as_array().unwrap().len(), 2);
        assert!(
            profiles["profiles"]
                .as_array()
                .unwrap()
                .iter()
                .all(|profile| profile["profile"] != ephemeral)
        );

        result(owner.execute(request(
            "req_profile_alpha_close",
            "session.close",
            json!({"session":alpha_session}),
        )));
        drop(owner);
        let reopened = result(contender.execute(request(
            "req_profile_alpha_reopen",
            "session.open",
            json!({"profile":alpha}),
        )))["session"]
            .as_str()
            .unwrap()
            .to_owned();
        let cookie = result(contender.execute(request(
            "req_profile_alpha_cookie_get",
            "profile.storage.get",
            json!({"session":reopened,"kind":"cookie","key":"court"}),
        )));
        assert_eq!(cookie["value"], "alpha-cookie");
        let local = result(contender.execute(request(
            "req_profile_alpha_local_get",
            "profile.storage.get",
            json!({"session":reopened,"kind":"local_storage","key":"court"}),
        )));
        assert_eq!(local["value"], "alpha-local");
        let alpha_inspect = result(contender.execute(request(
            "req_profile_alpha_inspect",
            "profile.inspect",
            json!({"profile":alpha}),
        )));
        let beta_inspect = result(contender.execute(request(
            "req_profile_beta_inspect",
            "profile.inspect",
            json!({"profile":beta}),
        )));
        assert_eq!(alpha_inspect["policy"]["network"], "offline");
        assert_eq!(beta_inspect["policy"]["network"], "online");
        assert_eq!(alpha_inspect["policy"]["permissions"], "deny_by_default");
        assert_eq!(beta_inspect["policy"]["permissions"], "allow_by_default");
        result(contender.execute(request(
            "req_profile_alpha_reclose",
            "session.close",
            json!({"session":reopened}),
        )));
        drop(contender);

        fs::create_dir(root.join("broken")).unwrap();
        fs::write(root.join("broken/profile.json"), b"{not-json").unwrap();
        let mut recovered = ControlState::with_profile_root(Some(root.clone())).unwrap();
        let listed = result(recovered.execute(request(
            "req_profile_corrupt_list",
            "profile.list",
            json!({}),
        )));
        assert_eq!(listed["profiles"].as_array().unwrap().len(), 2);
        assert_eq!(listed["unavailable"][0]["name"], "broken");
        fs::remove_file(root.join("broken/profile.json")).unwrap();
        fs::remove_dir(root.join("broken")).unwrap();
        result(recovered.execute(request(
            "req_profile_beta_delete",
            "profile.delete",
            json!({"profile":beta}),
        )));
        assert!(!root.join("beta").exists());
        drop(recovered);
        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn profile_storage_bounds_fail_without_growing() {
        let mut state = ControlState::default();
        let profile = result(state.execute(request(
            "req_storage_profile",
            "profile.create",
            json!({"persistence":"ephemeral","name":"bounded"}),
        )))["profile"]
            .as_str()
            .unwrap()
            .to_owned();
        let session = result(state.execute(request(
            "req_storage_session",
            "session.open",
            json!({"profile":profile}),
        )))["session"]
            .as_str()
            .unwrap()
            .to_owned();
        for index in 0..MAX_PROFILE_ENTRIES {
            let stored = state.execute(request(
                &format!("req_storage_{index}"),
                "profile.storage.put",
                json!({"session":session,"kind":"cookie","key":format!("key-{index}"),"value":"value"}),
            ));
            assert!(stored.ok);
        }
        let overflow = state.execute(request(
            "req_storage_overflow",
            "profile.storage.put",
            json!({"session":session,"kind":"cookie","key":"overflow","value":"value"}),
        ));
        assert_eq!(overflow.error.unwrap().code, "resource_limit");
        let oversized = state.execute(request(
            "req_storage_oversized",
            "profile.storage.put",
            json!({"session":session,"kind":"local_storage","key":"key","value":"x".repeat(MAX_PROFILE_VALUE_BYTES + 1)}),
        ));
        assert_eq!(oversized.error.unwrap().code, "invalid_request");
        let inspected = result(state.execute(request(
            "req_storage_inspect",
            "profile.inspect",
            json!({"profile":profile}),
        )));
        assert_eq!(inspected["storage"]["cookies"], MAX_PROFILE_ENTRIES);
        assert_eq!(inspected["storage"]["local_storage"], 0);
    }

    #[test]
    fn parser_rejects_wrong_kind_depth_and_unknown_operation() {
        let wrong_kind = br#"{"protocol":"minicon-surf.control","version":"0.0.1","request_id":"target_wrong","deadline_ms":1,"operation":"profile.list","arguments":{}}"#;
        assert!(parse_request(wrong_kind).is_err());
        let unknown = br#"{"protocol":"minicon-surf.control","version":"0.0.1","request_id":"req_unknown","deadline_ms":1,"operation":"engine.any","arguments":{}}"#;
        assert!(parse_request(unknown).is_err());
        let mut nested = json!({});
        for _ in 0..=MAX_DEPTH {
            nested = json!({"nested":nested});
        }
        let document = json!({
            "protocol":PROTOCOL,"version":VERSION,"request_id":"req_depth",
            "deadline_ms":1,"operation":"profile.list","arguments":nested,
        });
        assert!(parse_request(&serde_json::to_vec(&document).unwrap()).is_err());
    }

    #[test]
    fn reserved_operations_match_protocol_schema() {
        let schema_path = std::path::Path::new(env!("CARGO_MANIFEST_DIR"))
            .join("../../protocol/control-0.0.1.schema.json");
        let schema: Value = serde_json::from_slice(&std::fs::read(schema_path).unwrap()).unwrap();
        let schema_operations = schema["$defs"]["operation"]["enum"]
            .as_array()
            .unwrap()
            .iter()
            .map(|value| value.as_str().unwrap())
            .collect::<std::collections::BTreeSet<_>>();
        let rust_operations = KNOWN_OPERATIONS
            .iter()
            .copied()
            .collect::<std::collections::BTreeSet<_>>();
        assert_eq!(rust_operations, schema_operations);
        assert_eq!(schema["$defs"]["protocol"]["const"], PROTOCOL);
        assert_eq!(schema["$defs"]["version"]["const"], VERSION);
    }
}
