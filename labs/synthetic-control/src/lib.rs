use fs2::FileExt;
use serde::{Deserialize, Serialize};
use serde_json::{Value, json};
use std::collections::{BTreeMap, VecDeque};
use std::fs::{self, File, OpenOptions};
use std::io;
use std::mem::size_of;
use std::path::{Path, PathBuf};
use std::thread;
use std::time::{Duration, Instant};

pub mod adapter;
pub mod capability;
pub use adapter::{AdapterHandle, TargetAnchor, Teardown};
pub use capability::{AuditRecord, Capability, Chain};

#[cfg(feature = "mimalloc-lab")]
#[global_allocator]
static GLOBAL_ALLOCATOR: mimalloc::MiMalloc = mimalloc::MiMalloc;

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
pub const MAX_FRAMES_PER_TARGET: usize = 8;
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
    "memory.trim",
];

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Request {
    pub request_id: String,
    pub deadline: Duration,
    pub operation: String,
    pub arguments: Value,
    /// Optional attenuation of the caller's authority (see `capability`).
    pub capability: Option<Capability>,
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
    pub(crate) code: &'static str,
    message: String,
    retryable: bool,
    #[serde(skip_serializing_if = "Option::is_none")]
    scope: Option<Scope>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub(crate) details: Option<Value>,
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

    /// Scope by a kind that arrived as data; unknown kinds are impossible
    /// here because the capability parser only admits schema kinds.
    fn scoped_owned(self, kind: String, id: String) -> Self {
        let kind = match kind.as_str() {
            "profile" => "profile",
            "session" => "session",
            "target" => "target",
            "frame" => "frame",
            "realm" => "realm",
            _ => "surface",
        };
        self.scoped(kind, id)
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
    let has_capability = object.contains_key("capability");
    if object.len() != allowed.len() + usize::from(has_capability)
        || !allowed.iter().all(|key| object.contains_key(*key))
    {
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
    let capability = match object.get("capability") {
        Some(value) => Some(
            Capability::parse(value)
                .map_err(|message| Response::invalid(request_id.clone(), message))?,
        ),
        None => None,
    };
    Ok(Request {
        request_id,
        deadline: Duration::from_millis(deadline_ms),
        operation,
        arguments,
        capability,
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

/// One browsing-context node of a target. The frame id survives same-frame
/// navigation; the document generation and the realm do not.
#[derive(Debug, Clone)]
struct Frame {
    id: String,
    parent: Option<String>,
    generation: u64,
    realm_id: String,
    nodes: Vec<Node>,
}

#[derive(Debug, Clone)]
struct Target {
    id: String,
    session_id: String,
    revision: u64,
    scroll_y: u64,
    /// Main frame first, then bounded child frames in creation order.
    frames: Vec<Frame>,
    /// The only strong reference to this target's identity; adapters hold
    /// `Weak` copies and the teardown checks that none was upgraded and kept.
    anchor: std::sync::Arc<TargetAnchor>,
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
    /// Bounded capability audit ledger: diagnostics, never authority.
    audit: VecDeque<AuditRecord>,
    adapters: BTreeMap<String, adapter::Adapter>,
    next_adapter: u64,
    next_frame: u64,
    next_realm: u64,
    targets_closed_total: usize,
    adapters_detached_total: usize,
    owner_references_extended_total: usize,
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
        // Attenuation first: the same request without a capability is the
        // upper bound of what this request may do.
        let attenuation = match &request.capability {
            Some(capability) => {
                let deadline_ms = request.deadline.as_millis() as u64;
                let decision = self.authorize(
                    capability,
                    &request.operation,
                    &request.arguments,
                    deadline_ms,
                );
                let (chain, refusal) = match decision {
                    Ok(chain) => (chain, None),
                    Err(error) => (
                        self.ownership_chain(&request.operation, &request.arguments)
                            .ok()
                            .flatten()
                            .unwrap_or_default(),
                        Some(error),
                    ),
                };
                let record = AuditRecord {
                    request_id: request_id.clone(),
                    actor: capability.actor.clone(),
                    reason: capability.reason.clone(),
                    operation: request.operation.clone(),
                    owner: capability.owner.clone(),
                    chain,
                    decision: refusal
                        .as_ref()
                        .map_or_else(|| "allowed".to_owned(), |e| format!("refused:{}", e.code)),
                };
                self.record_audit(record);
                if let Some(error) = refusal {
                    return Response::failure(request_id, error);
                }
                Some(capability.result_bytes)
            }
            None => None,
        };
        let outcome = self.dispatch(&request);
        let outcome = match (outcome, attenuation) {
            (Ok(result), Some(result_bytes))
                if serde_json::to_vec(&result)
                    .map_or(true, |encoded| encoded.len() > result_bytes) =>
            {
                let error = limit("result exceeds the capability result budget").details(
                    json!({"reason":"result_budget_exceeded","result_bytes":result_bytes}),
                );
                if let Some(last) = self.audit.back_mut() {
                    last.decision = "refused:resource_limit".into();
                }
                Err(error)
            }
            (outcome, _) => outcome,
        };
        match outcome {
            Ok(result) => Response::success(request_id, result),
            Err(error) => Response::failure(request_id, error),
        }
    }

    fn dispatch(&mut self, request: &Request) -> Result<Value, ControlError> {
        match request.operation.as_str() {
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
            "session.inspect" => self.session_inspect(&request.arguments),
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
            "memory.trim" => self.memory_trim(&request.arguments),
            _ => Err(ControlError::new(
                "unsupported_operation",
                "operation is reserved but not implemented by synthetic-control",
                false,
            )),
        }
    }

    fn session_inspect(&self, arguments: &Value) -> Result<Value, ControlError> {
        let object = exact_object(arguments, &["session"])?;
        let session_id = typed_field(object, "session", "session")?;
        let session = self
            .sessions
            .get(session_id)
            .ok_or_else(|| not_found("session", session_id))?;
        let targets = self
            .targets
            .values()
            .filter(|target| target.session_id == session_id)
            .map(|target| target.id.clone())
            .collect::<Vec<_>>();
        let surfaces = self
            .surfaces
            .values()
            .filter(|surface| targets.contains(&surface.target_id))
            .map(|surface| surface.id.clone())
            .collect::<Vec<_>>();
        Ok(json!({
            "kind":"session",
            "session":session.id,
            "profile":session.profile_id,
            "targets":targets,
            "surfaces":surfaces,
            "capability_audit":self.audit_for_session(session_id),
            "audit_limit":capability::MAX_AUDIT_RECORDS,
        }))
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
        let mut teardown = Teardown::default();
        for target_id in &target_ids {
            let report = self.teardown_target(target_id)?;
            teardown.adapters_detached += report.adapters_detached;
            teardown.surfaces_released += report.surfaces_released;
            teardown.released_presentation_bytes += report.released_presentation_bytes;
            teardown.owner_reference_extended |= report.owner_reference_extended;
        }
        // The profile writer lock is released only after every target is gone.
        self.release_profile_lock(&session.profile_id);
        Ok(
            json!({"kind":"session_closed","session":session.id,"profile":session.profile_id,"closed_targets":target_ids.len(),"teardown":teardown.to_json()}),
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
        let main_nodes = vec![
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
            Node {
                id: "node_link_1".into(),
                role: "link".into(),
                name: "Example result".into(),
            },
        ];
        let child_nodes = vec![Node {
            id: "node_embedded_heading_1".into(),
            role: "heading".into(),
            name: "Embedded court".into(),
        }];
        debug_assert!(main_nodes.len() + child_nodes.len() <= MAX_NODES_PER_TARGET);
        let main = self.new_frame(None, main_nodes);
        let child = self.new_frame(Some(main.id.clone()), child_nodes);
        let anchor_realm = main.realm_id.clone();
        self.targets.insert(
            id.clone(),
            Target {
                id: id.clone(),
                session_id: session.to_owned(),
                revision: 0,
                scroll_y: 0,
                frames: vec![main, child],
                anchor: std::sync::Arc::new(TargetAnchor {
                    target_id: id.clone(),
                    session_id: session.to_owned(),
                    realm_id: anchor_realm,
                }),
            },
        );
        Ok(json!({"kind":"target","target":id,"session":session,"revision":0}))
    }

    /// Mint a frame with its first document and realm. Ids are host-wide
    /// counters and are never reused within this host generation.
    fn new_frame(&mut self, parent: Option<String>, nodes: Vec<Node>) -> Frame {
        self.next_frame += 1;
        self.next_realm += 1;
        Frame {
            id: format!("frame_{}", self.next_frame),
            parent,
            generation: 1,
            realm_id: format!("realm_{}", self.next_realm),
            nodes,
        }
    }

    /// Same-frame navigation of the main frame: the frame id survives, its
    /// generation advances, its realm is retired and a new one minted, every
    /// child frame ends with its realm, and the target revision advances.
    fn navigate_main(&mut self, target_id: &str) -> Result<Value, ControlError> {
        self.next_realm += 1;
        let realm_id = format!("realm_{}", self.next_realm);
        let target = self
            .targets
            .get_mut(target_id)
            .ok_or_else(|| not_found("target", target_id))?;
        let ended: Vec<String> = target.frames.drain(1..).map(|frame| frame.id).collect();
        let main = &mut target.frames[0];
        main.generation += 1;
        let retired = std::mem::replace(&mut main.realm_id, realm_id.clone());
        let generation = main.generation;
        main.nodes = vec![
            Node {
                id: format!("node_heading_g{generation}"),
                role: "heading".into(),
                name: "Navigated court".into(),
            },
            Node {
                id: format!("node_button_g{generation}"),
                role: "button".into(),
                name: "Continue".into(),
            },
            Node {
                id: format!("node_link_g{generation}"),
                role: "link".into(),
                name: "Example result".into(),
            },
        ];
        target.revision += 1;
        Ok(json!({
            "kind":"action","target":target.id,"revision":target.revision,"applied":true,"navigated":true,
            "frame":target.frames[0].id,"generation":generation,"realm":realm_id,"retired_realm":retired,"ended_frames":ended,
        }))
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
        let teardown = self.teardown_target(target_id)?;
        Ok(json!({"kind":"target_closed","target":target_id,"teardown":teardown.to_json()}))
    }

    fn target_snapshot(&self, arguments: &Value) -> Result<Value, ControlError> {
        let object = allowed_object(
            arguments,
            &["target", "format", "max_bytes", "max_nodes"],
            &["frame", "realm"],
        )?;
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
        // A foreign, ended or unknown frame is one and the same refusal.
        let frame = match object.get("frame") {
            None => &target.frames[0],
            Some(_) => {
                let frame_id = typed_field(object, "frame", "frame")?;
                target
                    .frames
                    .iter()
                    .find(|frame| frame.id == frame_id)
                    .ok_or_else(|| {
                        not_found("frame", frame_id)
                            .details(json!({"reason":"frame_not_live_in_target"}))
                    })?
            }
        };
        if object.get("realm").is_some() {
            let realm_id = typed_field(object, "realm", "realm")?;
            if realm_id != frame.realm_id {
                return Err(not_found("realm", realm_id)
                    .details(json!({"reason":"realm_not_live_in_target","frame":frame.id})));
            }
        }
        let nodes = frame
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
            "frame":frame.id,
            "realm":frame.realm_id,
            "generation":frame.generation,
            "truncated":nodes.len() < frame.nodes.len(),
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
        if !target
            .frames
            .iter()
            .any(|frame| frame.nodes.iter().any(|node| node.id == node_id))
        {
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
            .frames
            .iter_mut()
            .flat_map(|frame| frame.nodes.iter_mut())
            .find(|node| node.id == node_id)
            .ok_or_else(|| {
                ControlError::new("not_found", "node does not exist", false)
                    .scoped("target", target_id)
            })?;
        match node.role.as_str() {
            "button" => {
                node.name = "Clicked".into();
                target.revision += 1;
                Ok(
                    json!({"kind":"action","target":target.id,"revision":target.revision,"applied":true}),
                )
            }
            "link" => {
                let target_id = target_id.to_owned();
                self.navigate_main(&target_id)
            }
            _ => Err(ControlError::new(
                "unsupported_capability",
                "click requires a button or link node",
                false,
            )),
        }
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
                    + item
                        .frames
                        .iter()
                        .map(|frame| {
                            size_of::<Frame>()
                                + frame.id.capacity()
                                + frame.realm_id.capacity()
                                + frame.parent.as_ref().map_or(0, String::capacity)
                                + frame.nodes.capacity() * size_of::<Node>()
                                + frame
                                    .nodes
                                    .iter()
                                    .map(|node| {
                                        node.id.capacity()
                                            + node.role.capacity()
                                            + node.name.capacity()
                                    })
                                    .sum::<usize>()
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
        let adapter_bytes = self.adapter_bytes();
        Ok(json!({
            "kind":"memory_report",
            "semantic":"logical-owned-capacity-lower-bound",
            "owners":{
                "profiles":{"objects":self.profiles.len(),"bytes":profile_bytes,"object_limit":MAX_PROFILES},
                "sessions":{"objects":self.sessions.len(),"bytes":session_bytes,"object_limit":MAX_SESSIONS},
                "targets":{"objects":self.targets.len(),"bytes":target_bytes,"object_limit":MAX_TARGETS},
                "frames":{"objects":self.targets.values().map(|t| t.frames.len()).sum::<usize>(),"object_limit":MAX_TARGETS * MAX_FRAMES_PER_TARGET},
                "realms":{"objects":self.targets.values().map(|t| t.frames.len()).sum::<usize>(),"worlds_per_frame":1},
                "surfaces":{"objects":self.surfaces.len(),"bytes":surface_bytes,"object_limit":MAX_SURFACES},
                "adapters":{"objects":self.adapters.len(),"bytes":adapter_bytes,"object_limit":adapter::MAX_ADAPTERS},
            },
            "teardown":self.teardown_counters(),
            "total_accounted_bytes":profile_bytes + session_bytes + target_bytes + surface_bytes + adapter_bytes,
            "limitations":["excludes allocator and map overhead","not RSS/private/PSS/live heap"],
        }))
    }

    fn memory_trim(&self, arguments: &Value) -> Result<Value, ControlError> {
        exact_object(arguments, &[])?;
        #[cfg(feature = "mimalloc-lab")]
        {
            // SAFETY: forced collection is process-global and accepts no pointers.
            unsafe { libmimalloc_sys::mi_collect(true) };
            Ok(json!({
                "kind":"memory_trim",
                "strategy":"mimalloc_collect_force",
                "release_reporting":"unavailable"
            }))
        }
        #[cfg(all(not(feature = "mimalloc-lab"), target_os = "macos"))]
        {
            unsafe extern "C" {
                fn malloc_zone_pressure_relief(zone: *mut std::ffi::c_void, goal: usize) -> usize;
            }
            // SAFETY: a null zone requests pressure relief from every malloc zone;
            // the function accepts no Rust-owned pointer and does not retain one.
            let released = unsafe { malloc_zone_pressure_relief(std::ptr::null_mut(), 0) };
            Ok(
                json!({"kind":"memory_trim","strategy":"malloc_zone_pressure_relief","release_reporting":"bytes","released_bytes":released}),
            )
        }
        #[cfg(all(not(feature = "mimalloc-lab"), not(target_os = "macos")))]
        {
            Err(ControlError::new(
                "unsupported_capability",
                "allocator trim is not qualified on this platform",
                false,
            ))
        }
    }
}

fn target_summary(target: &Target) -> Value {
    json!({
        "kind":"target",
        "target":target.id,
        "session":target.session_id,
        "realm":target.frames[0].realm_id,
        "revision":target.revision,
        "scroll_y":target.scroll_y,
        "frames":target.frames.iter().map(|frame| json!({
            "frame":frame.id,"parent":frame.parent,"generation":frame.generation,"realm":frame.realm_id,
        })).collect::<Vec<_>>(),
        "realms":target.frames.iter().map(|frame| json!({
            "realm":frame.realm_id,"frame":frame.id,"world":"main",
        })).collect::<Vec<_>>(),
        "frame_limit":MAX_FRAMES_PER_TARGET,
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

pub(crate) fn invalid(message: impl Into<String>) -> ControlError {
    ControlError::new("invalid_request", message, false)
}

pub(crate) fn limit(message: impl Into<String>) -> ControlError {
    ControlError::new("resource_limit", message, true)
}

pub(crate) fn not_found(kind: &'static str, id: &str) -> ControlError {
    ControlError::new("not_found", format!("{kind} does not exist"), false).scoped(kind, id)
}

#[cfg(test)]
mod tests {
    use super::*;

    pub(crate) fn request(id: &str, operation: &str, arguments: Value) -> Request {
        Request {
            request_id: id.into(),
            deadline: Duration::from_millis(5),
            operation: operation.into(),
            arguments,
            capability: None,
        }
    }

    pub(crate) fn result(response: Response) -> Value {
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
    fn frames_and_realms_have_distinct_identities_and_lifetimes() {
        let mut state = ControlState::default();
        let profile = result(state.execute(request(
            "req_p",
            "profile.create",
            json!({"persistence":"ephemeral"}),
        )))["profile"]
            .as_str()
            .unwrap()
            .to_owned();
        let session = result(state.execute(request(
            "req_s",
            "session.open",
            json!({"profile":profile}),
        )))["session"]
            .as_str()
            .unwrap()
            .to_owned();
        let target = result(state.execute(request(
            "req_t",
            "target.open",
            json!({"session":session}),
        )))["target"]
            .as_str()
            .unwrap()
            .to_owned();
        let other = result(state.execute(request(
            "req_t2",
            "target.open",
            json!({"session":session}),
        )))["target"]
            .as_str()
            .unwrap()
            .to_owned();
        let inspect =
            result(state.execute(request("req_i", "target.inspect", json!({"target":target}))));
        let frames = inspect["frames"].as_array().unwrap();
        assert_eq!(frames.len(), 2, "main frame plus one bounded child");
        assert!(frames[0]["parent"].is_null() && frames[1]["parent"] == frames[0]["frame"]);
        assert_eq!(frames[0]["generation"], 1);
        assert_eq!(inspect["realms"].as_array().unwrap().len(), 2);
        assert_eq!(inspect["frame_limit"], MAX_FRAMES_PER_TARGET);
        let main = frames[0]["frame"].as_str().unwrap().to_owned();
        let child = frames[1]["frame"].as_str().unwrap().to_owned();
        let main_realm = frames[0]["realm"].as_str().unwrap().to_owned();
        let child_realm = frames[1]["realm"].as_str().unwrap().to_owned();
        let snap = |state: &mut ControlState, extra: Value| {
            let mut arguments =
                json!({"target":target,"format":"semantic","max_bytes":65536,"max_nodes":16});
            for (key, value) in extra.as_object().unwrap() {
                arguments[key] = value.clone();
            }
            state.execute(request("req_snap", "target.snapshot", arguments))
        };
        let default = result(snap(&mut state, json!({})));
        assert_eq!(default["frame"], main);
        assert_eq!(default["realm"], main_realm);
        assert_eq!(default["generation"], 1);
        assert_eq!(default["nodes"].as_array().unwrap().len(), 3);
        let embedded = result(snap(&mut state, json!({"frame":child,"realm":child_realm})));
        assert_eq!(embedded["nodes"][0]["name"], "Embedded court");
        // Foreign, ended and unknown frames are one refusal.
        let foreign_frame = result(state.execute(request(
            "req_i2",
            "target.inspect",
            json!({"target":other}),
        )))["frames"][0]["frame"]
            .as_str()
            .unwrap()
            .to_owned();
        for frame in [foreign_frame.as_str(), "frame_9999"] {
            let refused = snap(&mut state, json!({"frame":frame}));
            assert!(!refused.ok);
            let error = refused.error.unwrap();
            assert_eq!(error.code, "not_found");
            assert_eq!(error.scope.as_ref().unwrap().kind, "frame");
        }
        let wrong_realm = snap(&mut state, json!({"frame":main,"realm":child_realm}));
        assert_eq!(wrong_realm.error.unwrap().code, "not_found");
        // Navigation through the link: frame survives, generation and realm change, child ends.
        let link = default["nodes"][2]["reference"].clone();
        let navigated = result(state.execute(request(
            "req_nav",
            "target.act",
            json!({"target":target,"reference":link,"action":{"kind":"click"}}),
        )));
        assert_eq!(navigated["navigated"], true);
        assert_eq!(navigated["frame"], main);
        assert_eq!(navigated["generation"], 2);
        assert_eq!(navigated["retired_realm"], main_realm);
        assert_eq!(navigated["ended_frames"][0], child);
        assert_eq!(navigated["revision"], 1);
        let after = result(state.execute(request(
            "req_i3",
            "target.inspect",
            json!({"target":target}),
        )));
        assert_eq!(after["frames"].as_array().unwrap().len(), 1);
        assert_eq!(
            after["frames"][0]["frame"], main,
            "the frame id survived the navigation"
        );
        assert_ne!(after["frames"][0]["realm"], main_realm, "the realm did not");
        assert_eq!(after["revision"], 1);
        let stale = state.execute(request("req_stale", "target.act", json!({"target":target,"reference":default["nodes"][1]["reference"],"action":{"kind":"click"}})));
        assert_eq!(stale.error.unwrap().code, "stale_revision");
        let retired = snap(&mut state, json!({"frame":main,"realm":main_realm}));
        let error = retired.error.unwrap();
        assert_eq!(error.code, "not_found");
        assert_eq!(error.scope.as_ref().unwrap().kind, "realm");
        let ended = snap(&mut state, json!({"frame":child}));
        assert_eq!(ended.error.unwrap().code, "not_found");
        let fresh = result(snap(&mut state, json!({"frame":main})));
        assert_eq!(fresh["generation"], 2);
        assert_eq!(fresh["nodes"][0]["name"], "Navigated court");
        // A second navigation keeps the frame and mints yet another realm.
        let second_realm = fresh["realm"].as_str().unwrap().to_owned();
        let again = result(state.execute(request("req_nav2", "target.act", json!({"target":target,"reference":fresh["nodes"][2]["reference"],"action":{"kind":"click"}}))));
        assert_eq!(again["generation"], 3);
        assert_ne!(again["realm"], second_realm);
        assert_eq!(again["frame"], main);
        // Owners: two targets held four frames, now three; closing both leaves zero.
        let report = result(state.execute(request("req_m", "memory.report", json!({}))));
        assert_eq!(report["owners"]["frames"]["objects"], 3);
        assert_eq!(report["owners"]["realms"]["objects"], 3);
        result(state.execute(request("req_c1", "target.close", json!({"target":target}))));
        result(state.execute(request("req_c2", "target.close", json!({"target":other}))));
        let report = result(state.execute(request("req_m2", "memory.report", json!({}))));
        assert_eq!(report["owners"]["frames"]["objects"], 0);
        assert_eq!(report["owners"]["realms"]["objects"], 0);
    }

    #[test]
    fn frame_and_realm_arguments_narrow_but_never_own() {
        let mut state = ControlState::default();
        let profile = result(state.execute(request(
            "req_p",
            "profile.create",
            json!({"persistence":"ephemeral"}),
        )))["profile"]
            .as_str()
            .unwrap()
            .to_owned();
        let session = result(state.execute(request(
            "req_s",
            "session.open",
            json!({"profile":profile}),
        )))["session"]
            .as_str()
            .unwrap()
            .to_owned();
        let target = result(state.execute(request(
            "req_t",
            "target.open",
            json!({"session":session}),
        )))["target"]
            .as_str()
            .unwrap()
            .to_owned();
        let inspect =
            result(state.execute(request("req_i", "target.inspect", json!({"target":target}))));
        let child = inspect["frames"][1]["frame"].as_str().unwrap().to_owned();
        let arguments = json!({"target":target,"format":"semantic","max_bytes":65536,"max_nodes":16,"frame":child});
        let owned = Capability::parse(&json!({"owner":{"kind":"target","id":target},"scope":["target.snapshot"],"budget":{"result_bytes":65536,"deadline_ms":100},"audit":{"actor":"agent.test","reason":"frame"}})).unwrap();
        let chain = state
            .authorize(&owned, "target.snapshot", &arguments, 50)
            .unwrap();
        assert_eq!(
            chain.target.as_deref(),
            Some(target.as_str()),
            "the chain comes from the target, the frame only narrows"
        );
        let frame_owner = Capability::parse(&json!({"owner":{"kind":"frame","id":child},"scope":["target.snapshot"],"budget":{"result_bytes":65536,"deadline_ms":100},"audit":{"actor":"agent.test","reason":"frame"}})).unwrap();
        let error = state
            .authorize(&frame_owner, "target.snapshot", &arguments, 50)
            .unwrap_err();
        assert_eq!(error.code, "permission_denied");
        assert_eq!(error.details.unwrap()["reason"], "kind_is_not_an_owner");
        let realm_owner = Capability::parse(&json!({"owner":{"kind":"realm","id":inspect["frames"][1]["realm"]},"scope":["target.snapshot"],"budget":{"result_bytes":65536,"deadline_ms":100},"audit":{"actor":"agent.test","reason":"realm"}})).unwrap();
        assert_eq!(
            state
                .authorize(&realm_owner, "target.snapshot", &arguments, 50)
                .unwrap_err()
                .details
                .unwrap()["reason"],
            "kind_is_not_an_owner"
        );
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
