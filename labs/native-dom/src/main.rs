//! Native bounded route, second slice: HTML parsing, DOM and a bounded script realm.
//!
//! `native-dom-control` serves the control 0.0.1 vocabulary from an
//! html5ever-parsed document mirrored into a QuickJS realm (`rquickjs`) with a
//! minimal DOM shim. Inline and same-origin external `<script>` elements run
//! after parsing, DOM events and `MutationObserver` work, `fetch()` is served
//! by the bounded network module between evaluation turns, and the same
//! in-page instrumentation the engine hosts inject runs unchanged. There is
//! still no layout, storage or timers beyond microtasks; those remain typed
//! failures or documented gaps.

use std::collections::BTreeMap;
use std::error::Error;
use std::ffi::c_void;
use std::io::{self, BufRead, Write};
use std::path::PathBuf;
use std::time::{Duration, Instant};

use dom_query::{Document, NodeRef};
use rquickjs::allocator::Allocator;
use rquickjs::{Context, Runtime};
use serde_json::{Map, Value, json};
use url::Url;

mod arena;
mod cdp;
mod net;
mod profile;

const PROTOCOL: &str = "minicon-surf.control";
const VERSION: &str = "0.0.1";
const MAX_REQUEST_BYTES: usize = 65_536;
const MAX_RESPONSE_BYTES: usize = 4_194_304;
const MAX_DEADLINE_MS: u64 = 120_000;
const MAX_TARGETS: usize = 8;
const MAX_PROFILES: usize = 8;
const MAX_SESSIONS: usize = 8;
const MAX_SNAPSHOT_NODES: u64 = 128;
const MAX_FIXTURE_BYTES: u64 = 1_048_576;
const REALM_MEMORY_LIMIT: usize = 16 * 1024 * 1024;
const REALM_STACK_LIMIT: usize = 512 * 1024;
const MAX_NETWORK_ROUNDS: usize = 64;
const DOM_SHIM_JS: &str = include_str!("dom_shim.js");
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
  if (t === "a" && el.hasAttribute("href")) {{
    const ev = new Event("click", {{ bubbles: true, cancelable: true }});
    el.dispatchEvent(ev);
    if (ev.defaultPrevented) return JSON.stringify({{ applied: true }});
    return JSON.stringify({{ navigate: el.getAttribute("href") }});
  }}
  if (!(t === "button" || (t === "input" && /^(button|submit|reset)$/.test(el.type)))) {{
    return JSON.stringify({{ unsupported: true }});
  }}
  el.click();
  return JSON.stringify({{ applied: true }});
}})()"#
    )
}

// ---------------------------------------------------------------- envelope

#[derive(Debug)]
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

fn profile_budgets() -> Value {
    json!({
        "cookies_per_host":profile::MAX_COOKIES_PER_HOST,
        "cookies_per_profile":profile::MAX_COOKIES_PER_PROFILE,
        "cookie_bytes":profile::MAX_COOKIE_BYTES,
        "storage_keys_per_origin":profile::MAX_STORAGE_KEYS_PER_ORIGIN,
        "storage_value_bytes":profile::MAX_STORAGE_VALUE_BYTES,
        "accounted_bytes_per_profile":profile::MAX_ACCOUNTED_BYTES_PER_PROFILE,
        "record_bytes":profile::MAX_RECORD_BYTES,
    })
}

fn store_error(error: profile::StoreError, profile_id: &str) -> ControlError {
    let message = match &error {
        profile::StoreError::KeychainUnavailable(_) => {
            "keychain unavailable: persistent profiles fail closed"
        }
        profile::StoreError::Corrupt(_) => "profile record is corrupt or incompatible",
        profile::StoreError::Io(_) => "profile record could not be written",
    };
    ControlError::new(error.code(), message, false)
        .scoped("profile", profile_id)
        .details(json!({"reason":error.detail()}))
}

fn commit_failed(scope_id: &str, detail: &str) -> ControlError {
    let kind = if scope_id.starts_with("target_") {
        "target"
    } else {
        "profile"
    };
    ControlError::new(
        "internal",
        "profile storage commit failed; the previous record is kept",
        false,
    )
    .scoped(kind, scope_id)
    .details(json!({"reason":"storage_commit_failed","detail":detail}))
}

fn not_found(kind: &'static str, id: &str) -> ControlError {
    ControlError::new("not_found", format!("{kind} does not exist"), false).scoped(kind, id)
}

fn unsupported_operation(operation: &str) -> ControlError {
    ControlError::new(
        "unsupported_operation",
        format!("{operation} is reserved by control 0.0.1 but not offered by the native DOM slice"),
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

fn allowed_object<'a>(
    value: &'a Value,
    required: &[&str],
    optional: &[&str],
) -> Result<&'a Map<String, Value>, ControlError> {
    let object = value
        .as_object()
        .ok_or_else(|| invalid("arguments must be an object"))?;
    if !required.iter().all(|key| object.contains_key(*key))
        || object
            .keys()
            .any(|key| !required.contains(&key.as_str()) && !optional.contains(&key.as_str()))
    {
        return Err(invalid("arguments fields differ"));
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

// ------------------------------------------------------------- allocator

#[repr(C)]
#[derive(Default)]
struct MallocStatistics {
    blocks_in_use: u32,
    size_in_use: usize,
    max_size_in_use: usize,
    size_allocated: usize,
}

#[cfg(target_os = "macos")]
unsafe extern "C" {
    fn malloc_zone_statistics(zone: *mut c_void, stats: *mut MallocStatistics);
    fn malloc_zone_pressure_relief(zone: *mut c_void, goal: usize) -> usize;
    fn malloc_create_zone(start_size: usize, flags: u32) -> *mut c_void;
    fn malloc_destroy_zone(zone: *mut c_void);
    fn malloc_zone_malloc(zone: *mut c_void, size: usize) -> *mut c_void;
    fn malloc_zone_calloc(zone: *mut c_void, count: usize, size: usize) -> *mut c_void;
    fn malloc_zone_free(zone: *mut c_void, ptr: *mut c_void);
    fn malloc_size(ptr: *const c_void) -> usize;
}

#[cfg(target_os = "macos")]
fn zone_statistics(zone: *mut c_void) -> Value {
    let mut stats = MallocStatistics::default();
    // SAFETY: a null zone aggregates every malloc zone and a non-null zone
    // came from malloc_create_zone; the out-pointer is a valid, exclusively
    // borrowed C-layout struct for the duration of the call.
    unsafe { malloc_zone_statistics(zone, &mut stats) };
    json!({"size_in_use":stats.size_in_use,"size_allocated":stats.size_allocated,"blocks_in_use":stats.blocks_in_use})
}

#[cfg(target_os = "macos")]
fn libmalloc_statistics() -> Value {
    zone_statistics(std::ptr::null_mut())
}

#[cfg(not(target_os = "macos"))]
fn libmalloc_statistics() -> Value {
    Value::Null
}

/// Blocks still in use inside a dedicated zone at the moment it was destroyed,
/// summed over every closed realm. A non-zero value means QuickJS or the
/// shim leaked blocks that only the zone teardown reclaimed.
static ZONE_BLOCKS_LEAKED: std::sync::atomic::AtomicUsize = std::sync::atomic::AtomicUsize::new(0);
static ZONES_DESTROYED: std::sync::atomic::AtomicUsize = std::sync::atomic::AtomicUsize::new(0);

/// A dedicated libmalloc zone that one QuickJS realm allocates from, so that
/// closing the target destroys the zone and returns its pages to the OS
/// instead of leaving freed blocks inside the default zone's regions.
///
/// Invariants: the zone is owned by exactly one `Realm`, is never cloned, and
/// is dropped after the realm's `Context` and `Runtime` (field order), so
/// `JS_FreeRuntime` has released every block before `malloc_destroy_zone`.
/// The allocator handed to QuickJS only borrows the zone pointer and has no
/// destructor, so nothing is destroyed twice.
#[cfg(target_os = "macos")]
struct Zone(*mut c_void);

#[cfg(target_os = "macos")]
impl Zone {
    fn create() -> Result<Self, ControlError> {
        // SAFETY: malloc_create_zone has no preconditions; null is checked.
        let zone = unsafe { malloc_create_zone(0, 0) };
        if zone.is_null() {
            return Err(ControlError::new(
                "internal",
                "malloc zone creation failed",
                false,
            ));
        }
        Ok(Zone(zone))
    }

    fn blocks_in_use(&self) -> usize {
        let mut stats = MallocStatistics::default();
        // SAFETY: the zone came from malloc_create_zone and is still alive.
        unsafe { malloc_zone_statistics(self.0, &mut stats) };
        stats.blocks_in_use as usize
    }
}

#[cfg(target_os = "macos")]
impl Drop for Zone {
    fn drop(&mut self) {
        let leaked = self.blocks_in_use();
        ZONE_BLOCKS_LEAKED.fetch_add(leaked, std::sync::atomic::Ordering::Relaxed);
        ZONES_DESTROYED.fetch_add(1, std::sync::atomic::Ordering::Relaxed);
        // SAFETY: the zone was created by malloc_create_zone; the runtime that
        // allocated from it has already been freed (see the struct invariant),
        // and any block still counted above is reclaimed here.
        unsafe { malloc_destroy_zone(self.0) };
    }
}

/// rquickjs allocator that routes every QuickJS allocation into one zone and
/// keeps the accounting QuickJS itself would have done with its default
/// allocator: rquickjs documents `set_memory_limit` as a no-op under a custom
/// allocator, so the byte limit is enforced here on the block sizes libmalloc
/// actually serves, and the live byte count is exposed through `used`.
///
/// Contract: every allocation is charged by its real `malloc_size` after it
/// is served and released before it is freed; a block that would push the
/// count over the limit (or overflow it) is freed again and null is
/// returned, so nothing is ever left both unaccounted and live. `realloc`
/// never touches the old block until a charged replacement exists: on any
/// failure it returns null and the old block stays valid and counted.
#[cfg(target_os = "macos")]
struct ZoneAllocator {
    zone: *mut c_void,
    limit: usize,
    /// Live bytes served by this zone, updated with compare-and-swap loops so
    /// the count stays exact even if a future rquickjs build calls the
    /// allocator from more than one thread.
    used: std::sync::Arc<std::sync::atomic::AtomicUsize>,
}

#[cfg(target_os = "macos")]
impl ZoneAllocator {
    /// Try to add a served block's real size to the count. Fails without
    /// changing the count when the total would exceed the limit or overflow.
    fn try_charge(&self, ptr: *mut c_void) -> bool {
        // SAFETY: the block was just returned by this zone.
        let size = unsafe { malloc_size(ptr) };
        let limit = self.limit;
        self.used
            .fetch_update(
                std::sync::atomic::Ordering::SeqCst,
                std::sync::atomic::Ordering::SeqCst,
                |current| {
                    current
                        .checked_add(size)
                        .filter(|total| limit == 0 || *total <= limit)
                },
            )
            .is_ok()
    }

    /// Charge a freshly served block or give it back and report null.
    fn charge_or_release(&self, ptr: *mut c_void) -> *mut u8 {
        if ptr.is_null() {
            return std::ptr::null_mut();
        }
        if self.try_charge(ptr) {
            return ptr.cast();
        }
        // SAFETY: the block came from this zone a moment ago and was never
        // handed to QuickJS.
        unsafe { malloc_zone_free(self.zone, ptr) };
        std::ptr::null_mut()
    }

    fn release(&self, size: usize) {
        let _ = self.used.fetch_update(
            std::sync::atomic::Ordering::SeqCst,
            std::sync::atomic::Ordering::SeqCst,
            |current| Some(current.saturating_sub(size)),
        );
    }

    /// Cheap pre-check on the requested size; the real check happens on the
    /// served size in `try_charge`.
    fn within_limit(&self, additional: usize) -> bool {
        let current = self.used.load(std::sync::atomic::Ordering::SeqCst);
        self.limit == 0
            || current
                .checked_add(additional)
                .is_some_and(|total| total <= self.limit)
    }
}

// SAFETY: every call forwards to libmalloc zone functions with pointers that
// the rquickjs bridge guarantees came from this same allocator (`dealloc`
// and `realloc`), or with a size QuickJS requested; `usable_size` uses
// malloc_size, which is valid for any block served by any libmalloc zone.
#[cfg(target_os = "macos")]
unsafe impl Allocator for ZoneAllocator {
    fn alloc(&mut self, size: usize) -> *mut u8 {
        if !self.within_limit(size) {
            return std::ptr::null_mut();
        }
        // SAFETY: plain zone malloc; a zero size yields a minimal block and
        // null on exhaustion is reported to QuickJS as out of memory.
        self.charge_or_release(unsafe { malloc_zone_malloc(self.zone, size) })
    }

    fn calloc(&mut self, count: usize, size: usize) -> *mut u8 {
        let Some(total) = count.checked_mul(size) else {
            return std::ptr::null_mut();
        };
        if !self.within_limit(total) {
            return std::ptr::null_mut();
        }
        // SAFETY: plain zone calloc with an overflow-checked product.
        self.charge_or_release(unsafe { malloc_zone_calloc(self.zone, count, size) })
    }

    unsafe fn dealloc(&mut self, ptr: *mut u8) {
        // The bridge filters null, but stay safe if called directly.
        if ptr.is_null() {
            return;
        }
        // SAFETY: the caller guarantees the block came from this allocator.
        let size = unsafe { malloc_size(ptr.cast()) };
        self.release(size);
        // SAFETY: as above; a foreign pointer would abort inside libmalloc
        // rather than corrupt another zone.
        unsafe { malloc_zone_free(self.zone, ptr.cast()) }
    }

    unsafe fn realloc(&mut self, ptr: *mut u8, new_size: usize) -> *mut u8 {
        if ptr.is_null() {
            return self.alloc(new_size);
        }
        // SAFETY: the caller guarantees the block came from this allocator.
        let old = unsafe { malloc_size(ptr.cast()) };
        if !self.within_limit(new_size.saturating_sub(old)) {
            return std::ptr::null_mut();
        }
        // Serve and charge the replacement first; the old block is untouched
        // until the replacement is fully accounted. A zero new size yields a
        // minimal block, matching this platform's realloc.
        // SAFETY: plain zone malloc.
        let replacement = unsafe { malloc_zone_malloc(self.zone, new_size) };
        if replacement.is_null() || !self.try_charge(replacement) {
            if !replacement.is_null() {
                // SAFETY: never handed out; came from this zone.
                unsafe { malloc_zone_free(self.zone, replacement) };
            }
            return std::ptr::null_mut();
        }
        // SAFETY: both blocks are live, distinct, and at least
        // min(old, new_size) bytes long; the old block is readable in full
        // because `old` is its usable size.
        unsafe {
            std::ptr::copy_nonoverlapping(
                ptr,
                replacement.cast::<u8>(),
                std::cmp::min(old, new_size),
            );
        }
        self.release(old);
        // SAFETY: the old block came from this allocator and is no longer
        // referenced.
        unsafe { malloc_zone_free(self.zone, ptr.cast()) };
        replacement.cast()
    }

    unsafe fn usable_size(ptr: *mut u8) -> usize {
        // The bridge answers null with 0 before reaching here; mirror it.
        if ptr.is_null() {
            return 0;
        }
        // SAFETY: the caller guarantees the block came from a libmalloc zone.
        unsafe { malloc_size(ptr.cast()) }
    }
}

/// Which allocator serves a realm's QuickJS heap.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
enum RealmAllocation {
    /// rquickjs's default: QuickJS mallocs into the default libmalloc zone.
    System,
    /// One libmalloc zone per realm, destroyed after the runtime drops (macOS).
    Zone,
    /// One reserved mapping per realm with a boundary-tag heap over it,
    /// unmapped once the runtime and its allocator are gone (macOS).
    Arena,
}

impl RealmAllocation {
    fn name(self) -> &'static str {
        match self {
            RealmAllocation::System => "system",
            RealmAllocation::Zone => "zone",
            RealmAllocation::Arena => "arena",
        }
    }
}

/// Address space reserved per arena realm. QuickJS's own 16 MiB limit binds
/// first; the extra room lets a large reallocation hold old and new buffers
/// at once, as the default allocator can, instead of failing early. Pages
/// cost nothing until written.
const REALM_ARENA_BYTES: usize = 32 * 1024 * 1024;

/// One bounded QuickJS realm holding the mirrored document. Fields drop in
/// declaration order: the context and runtime free every QuickJS block
/// before the optional zone that served them is destroyed. The optional
/// arena is shared with the runtime's allocator through an `Rc`, so its
/// mapping outlives every allocator call whatever the order.
struct Realm {
    context: Context,
    runtime: Runtime,
    #[cfg(target_os = "macos")]
    zone_used: Option<std::sync::Arc<std::sync::atomic::AtomicUsize>>,
    #[cfg(target_os = "macos")]
    zone: Option<Zone>,
    #[cfg(target_os = "macos")]
    arena: Option<std::rc::Rc<arena::Arena>>,
}

impl Realm {
    #[cfg(target_os = "macos")]
    fn new(allocation: RealmAllocation) -> Result<Self, ControlError> {
        let zone = if allocation == RealmAllocation::Zone {
            Some(Zone::create()?)
        } else {
            None
        };
        let zone_used = zone
            .as_ref()
            .map(|_| std::sync::Arc::new(std::sync::atomic::AtomicUsize::new(0)));
        let arena = if allocation == RealmAllocation::Arena {
            Some(arena::Arena::reserve(REALM_ARENA_BYTES).map_err(|e| {
                ControlError::new("internal", format!("realm arena failed: {e}"), false)
            })?)
        } else {
            None
        };
        let runtime = match (&zone, &zone_used, &arena) {
            (Some(zone), Some(used), _) => Runtime::new_with_alloc(ZoneAllocator {
                zone: zone.0,
                limit: REALM_MEMORY_LIMIT,
                used: used.clone(),
            }),
            (_, _, Some(arena)) => Runtime::new_with_alloc(arena::ArenaAllocator(arena.clone())),
            _ => Runtime::new(),
        }
        .map_err(|e| ControlError::new("internal", format!("script runtime failed: {e}"), false))?;
        // quickjs-ng checks this limit in its own malloc wrappers before any
        // allocator is called, so it binds under every allocator here (see
        // `quickjs_enforces_its_limit_under_a_custom_allocator`); the zone
        // allocator additionally enforces it on served sizes.
        runtime.set_memory_limit(REALM_MEMORY_LIMIT);
        runtime.set_max_stack_size(REALM_STACK_LIMIT);
        let context = Context::full(&runtime).map_err(|e| {
            ControlError::new("internal", format!("script context failed: {e}"), false)
        })?;
        Ok(Realm {
            context,
            runtime,
            zone_used,
            zone,
            arena,
        })
    }

    #[cfg(not(target_os = "macos"))]
    fn new(allocation: RealmAllocation) -> Result<Self, ControlError> {
        if allocation != RealmAllocation::System {
            return Err(ControlError::new(
                "unsupported_capability",
                "dedicated realm zones and arenas exist only on macOS",
                false,
            ));
        }
        let runtime = Runtime::new().map_err(|e| {
            ControlError::new("internal", format!("script runtime failed: {e}"), false)
        })?;
        runtime.set_memory_limit(REALM_MEMORY_LIMIT);
        runtime.set_max_stack_size(REALM_STACK_LIMIT);
        let context = Context::full(&runtime).map_err(|e| {
            ControlError::new("internal", format!("script context failed: {e}"), false)
        })?;
        Ok(Realm { context, runtime })
    }

    #[cfg(target_os = "macos")]
    fn zone_statistics(&self) -> Option<Value> {
        self.zone.as_ref().map(|zone| zone_statistics(zone.0))
    }

    #[cfg(not(target_os = "macos"))]
    fn zone_statistics(&self) -> Option<Value> {
        None
    }

    #[cfg(target_os = "macos")]
    fn arena_statistics(&self) -> Option<Value> {
        self.arena.as_ref().map(|arena| {
            let stats = arena.statistics();
            json!({"reserved_bytes":stats.capacity,"used_bytes":stats.used,"blocks":stats.blocks,"high_water_bytes":stats.high_water,"decommitted_from":arena.decommitted_from()})
        })
    }

    #[cfg(not(target_os = "macos"))]
    fn arena_statistics(&self) -> Option<Value> {
        None
    }

    /// Mark the arena's free tail reusable; bytes advised, zero without one.
    fn trim_arena(&self) -> usize {
        #[cfg(target_os = "macos")]
        if let Some(arena) = &self.arena {
            return arena.trim();
        }
        0
    }

    /// Live QuickJS bytes: the zone allocator's own count when a zone serves
    /// the realm, otherwise QuickJS's accounting through the default allocator.
    fn malloc_bytes(&self) -> usize {
        #[cfg(target_os = "macos")]
        if let Some(used) = &self.zone_used {
            return used.load(std::sync::atomic::Ordering::Relaxed);
        }
        self.runtime.memory_usage().malloc_size.max(0) as usize
    }

    /// Evaluate a script, run the microtasks it queued, and return its string result.
    fn eval(
        &self,
        script: &str,
        deadline: Instant,
        target_id: &str,
    ) -> Result<String, ControlError> {
        self.runtime
            .set_interrupt_handler(Some(Box::new(move || Instant::now() >= deadline)));
        let outcome = self
            .context
            .with(|ctx| match ctx.eval::<rquickjs::Value, _>(script) {
                Ok(value) => {
                    if value.is_undefined() || value.is_null() {
                        Ok(String::new())
                    } else {
                        let text: String = ctx
                            .globals()
                            .get::<_, rquickjs::Function>("String")
                            .and_then(|f| f.call((value,)))
                            .unwrap_or_default();
                        Ok(text)
                    }
                }
                Err(error) => {
                    let exception = ctx.catch();
                    let message = exception
                        .as_exception()
                        .and_then(|e| e.message())
                        .unwrap_or_else(|| format!("{error}"));
                    Err(message)
                }
            });
        self.runtime.set_interrupt_handler(None);
        let result = match outcome {
            Ok(text) => text,
            Err(message) => {
                let code = if Instant::now() >= deadline {
                    "deadline_exceeded"
                } else {
                    "internal"
                };
                return Err(ControlError::new(
                    code,
                    "script evaluation failed",
                    code == "deadline_exceeded",
                )
                .scoped("target", target_id)
                .details(json!({"engine_error":message.chars().take(256).collect::<String>()})));
            }
        };
        self.drain_jobs(deadline);
        Ok(result)
    }

    fn drain_jobs(&self, deadline: Instant) {
        while Instant::now() < deadline {
            match self.runtime.execute_pending_job() {
                Ok(true) => continue,
                _ => break,
            }
        }
    }
}

// --------------------------------------------------------------- document

fn serialize_children(node: &NodeRef, out: &mut Vec<Value>) {
    for child in node.children() {
        if child.is_text() {
            out.push(json!({"x": child.text().to_string()}));
        } else if child.is_element() {
            let name = child.node_name().map(|n| n.to_string()).unwrap_or_default();
            let attrs: Map<String, Value> = child
                .attrs()
                .iter()
                .map(|a| (a.name.local.to_string(), json!(a.value.to_string())))
                .collect();
            let mut children = Vec::new();
            serialize_children(&child, &mut children);
            out.push(json!({"e": name, "a": attrs, "c": children}));
        }
    }
}

// ------------------------------------------------------------------- host

/// One profile: identity, its two cookie jars, its origin-keyed storage and,
/// for persistent profiles, the sealed record on disk and the writer lock.
struct Profile {
    id: String,
    name: Option<String>,
    persistent: bool,
    jar: profile::Jar,
    storage: profile::Storage,
    /// The data key while the profile is loaded (persistent only); zeroized
    /// when the profile is dropped.
    dek: Option<zeroize::Zeroizing<Vec<u8>>>,
    /// The data key wrapped by the master key, stored unchanged on every write.
    wrapped_dek: Option<profile::WrappedDek>,
    directory: Option<PathBuf>,
    /// Set after a failed disk commit: no further writes for this host.
    read_only: bool,
    /// Held while a session is open on a persistent profile.
    lock: Option<std::fs::File>,
}

/// A target's working copy of its profile's jar and storage, synced from the
/// profile before an operation and committed back after it.
#[derive(Debug, Clone)]
struct TargetIo {
    jar: profile::Jar,
    storage: profile::Storage,
    origin: String,
    document_host: Option<String>,
    cookie_rejections: u64,
}

struct JarHooks<'a> {
    jar: &'a mut profile::Jar,
    document_host: Option<&'a str>,
    now: u64,
    rejections: &'a mut u64,
}

impl net::CookieHooks for JarHooks<'_> {
    fn cookie_header(&mut self, url: &Url) -> Option<String> {
        self.jar.header_for(url, self.document_host, self.now)
    }

    fn store(&mut self, url: &Url, set_cookie: &str) {
        if self.jar.store(url, set_cookie, self.now).is_err() {
            *self.rejections += 1;
        }
    }
}

struct Session {
    id: String,
    profile_id: String,
}

struct Target {
    id: String,
    session_id: String,
    fixture: String,
    url: Option<Url>,
    document_framing: &'static str,
    fixture_bytes: usize,
    element_count: usize,
    script_count: usize,
    skipped_scripts: Vec<Value>,
    budget: net::Budget,
    realm: Realm,
    last_snapshot: Option<(u64, usize)>,
    /// The main frame's id: minted with the target and kept for its life.
    frame_id: String,
    /// Document generation of the main frame: 1 for the first document,
    /// +1 for every same-frame navigation.
    generation: u64,
    /// The live realm's id: minted with each document, retired with it.
    realm_id: String,
    /// Target revisions are monotonic across navigations: the realm counts
    /// from zero for each document, so its count is offset by this base.
    revision_base: u64,
    io: TargetIo,
}

/// Where a document comes from: a court fixture file or a URL fetched under
/// the network policy.
enum Source {
    Fixture(String),
    Url(String),
}

/// Fixture names and relative fixture links share one shape: a lowercase
/// `.html` file name inside the court, never a path.
fn valid_fixture_name(name: &str) -> bool {
    name.ends_with(".html")
        && name
            .bytes()
            .all(|b| b.is_ascii_lowercase() || b.is_ascii_digit() || b == b'-' || b == b'.')
        && !name.contains("..")
}

impl Target {
    /// Evaluate in the realm, then serve every `fetch()` the script queued
    /// under the network policy and per-target budget before returning.
    fn eval(
        &mut self,
        script: &str,
        deadline: Instant,
        policy: &net::Policy,
    ) -> Result<String, ControlError> {
        let result = self.realm.eval(script, deadline, &self.id)?;
        self.pump_network(deadline, policy)?;
        self.drain_store_writes(deadline)?;
        Ok(result)
    }

    /// The document's URL for cookie purposes; fixture targets have none.
    fn document_url(&self) -> Option<Url> {
        self.url.clone()
    }

    /// Seed the realm's cookie and storage mirrors from the working copy.
    fn seed_store(&mut self, deadline: Instant, read_only: bool) -> Result<(), ControlError> {
        let now = profile::now_seconds();
        let cookie = self
            .document_url()
            .map(|url| self.io.jar.document_cookie(&url, now))
            .unwrap_or_default();
        self.realm.eval(
            &format!("__mcsCookieSeed({})", json!(cookie)),
            deadline,
            &self.id,
        )?;
        let seed = self.io.storage.origin_json(&self.io.origin);
        self.realm.eval(
            &format!(
                "__mcsStorageSeed({}, {})",
                json!(serde_json::to_string(&seed).expect("storage serializes")),
                read_only
            ),
            deadline,
            &self.id,
        )?;
        Ok(())
    }

    /// Apply the page's synchronous cookie and storage writes to the working
    /// copy, in order; the host commits them afterwards.
    fn drain_store_writes(&mut self, deadline: Instant) -> Result<(), ControlError> {
        let now = profile::now_seconds();
        let writes = self.realm.eval("__mcsCookieTake()", deadline, &self.id)?;
        let writes: Vec<String> = serde_json::from_str(&writes).unwrap_or_default();
        if let Some(url) = self.document_url() {
            for line in &writes {
                if self.io.jar.store(&url, line, now).is_err() {
                    self.io.cookie_rejections += 1;
                }
            }
            if !writes.is_empty() {
                let cookie = self.io.jar.document_cookie(&url, now);
                self.realm.eval(
                    &format!("__mcsCookieSeed({})", json!(cookie)),
                    deadline,
                    &self.id,
                )?;
            }
        }
        let ops = self.realm.eval("__mcsStorageTake()", deadline, &self.id)?;
        let ops: Vec<Value> = serde_json::from_str(&ops).unwrap_or_default();
        for op in &ops {
            match op["op"].as_str() {
                Some("set") => {
                    let (Some(key), Some(value)) = (op["key"].as_str(), op["value"].as_str())
                    else {
                        continue;
                    };
                    let other = self.io.jar.accounted_bytes();
                    // The realm mirror already enforced the budgets; a
                    // rejection here is counted and the write dropped.
                    if self
                        .io
                        .storage
                        .set(&self.io.origin, key, value, other)
                        .is_err()
                    {
                        self.io.cookie_rejections += 1;
                    }
                }
                Some("remove") => {
                    if let Some(key) = op["key"].as_str() {
                        self.io.storage.remove(&self.io.origin, key);
                    }
                }
                Some("clear") => self.io.storage.clear(&self.io.origin),
                _ => {}
            }
        }
        Ok(())
    }

    fn pump_network(
        &mut self,
        deadline: Instant,
        policy: &net::Policy,
    ) -> Result<(), ControlError> {
        for _ in 0..MAX_NETWORK_ROUNDS {
            let queued = self.realm.eval("__mcsNetTake()", deadline, &self.id)?;
            let requests: Vec<Value> = serde_json::from_str(&queued).unwrap_or_default();
            if requests.is_empty() {
                return Ok(());
            }
            for (index, request) in requests.iter().enumerate() {
                let Some(id) = request.get("id").and_then(Value::as_u64) else {
                    continue;
                };
                let raw = request
                    .get("url")
                    .and_then(Value::as_str)
                    .unwrap_or_default();
                let outcome = if index >= net::MAX_PENDING_PER_TURN {
                    self.budget.denied += 1;
                    Err(net::NetError {
                        code: "resource_limit",
                        reason: "pending-count",
                        detail: format!(
                            "more than {} fetches queued in one turn",
                            net::MAX_PENDING_PER_TURN
                        ),
                    })
                } else {
                    match self.resolve(raw) {
                        Ok(url) => {
                            let document_host = self.io.document_host.clone();
                            let mut hooks = JarHooks {
                                jar: &mut self.io.jar,
                                document_host: document_host.as_deref(),
                                now: profile::now_seconds(),
                                rejections: &mut self.io.cookie_rejections,
                            };
                            net::fetch_with(
                                url.as_str(),
                                policy,
                                &mut self.budget,
                                deadline,
                                Some(&mut hooks),
                            )
                        }
                        Err(error) => Err(error),
                    }
                };
                let settle = match outcome {
                    Ok(response) => {
                        let body = String::from_utf8_lossy(&response.body).into_owned();
                        let mut headers = Map::new();
                        if let Some(content_type) = &response.content_type {
                            headers.insert("content-type".into(), json!(content_type));
                        }
                        format!(
                            "__mcsNetSettle({id}, true, {})",
                            json!({"status":response.status,"url":response.url.as_str(),"redirects":response.redirects,"headers":headers,"body":body})
                        )
                    }
                    Err(error) => format!(
                        "__mcsNetSettle({id}, false, {})",
                        json!({"code":error.code,"reason":error.reason,"detail":error.detail})
                    ),
                };
                self.realm.eval(&settle, deadline, &self.id)?;
            }
        }
        Err(ControlError::new(
            "resource_limit",
            "script kept queueing fetches across the round limit",
            false,
        )
        .scoped("target", &self.id))
    }

    fn resolve(&self, raw: &str) -> Result<Url, net::NetError> {
        let parsed = match &self.url {
            Some(base) => base.join(raw),
            None => Url::parse(raw),
        };
        parsed.map_err(|e| net::NetError {
            code: "invalid_request",
            reason: "url",
            detail: format!("URL is malformed: {e}"),
        })
    }
}

fn net_error(error: net::NetError, target_id: &str) -> ControlError {
    ControlError::new(
        error.code,
        format!("network policy: {}", error.reason),
        error.code == "deadline_exceeded",
    )
    .scoped("target", target_id)
    .details(json!({"reason":error.reason,"detail":error.detail}))
}

struct Host {
    fixture_root: PathBuf,
    policy: net::Policy,
    realm_allocation: RealmAllocation,
    profiles: BTreeMap<String, Profile>,
    sessions: BTreeMap<String, Session>,
    targets: BTreeMap<String, Target>,
    next_profile: u64,
    next_session: u64,
    next_target: u64,
    next_frame: u64,
    next_realm: u64,
    realms_retired_total: u64,
    navigations_total: u64,
    /// Adapters (today: CDP sessions) registered against live targets. A
    /// record holds names only; the target owns its state and the record is
    /// removed when the target closes.
    adapters: BTreeMap<String, AdapterRecord>,
    next_adapter: u64,
    adapters_detached_total: u64,
    next_bridge_request: u64,
    /// Persistent profile store (D1); `None` keeps the host ephemeral-only.
    profile_root: Option<PathBuf>,
    key_source: Option<profile::KeySource>,
    /// Persistent profile directories that failed to load, by name, with
    /// the reason; they never block healthy siblings.
    unavailable_profiles: BTreeMap<String, String>,
    store_writes_total: u64,
    store_bytes_written_total: u64,
    cookie_rejections_total: u64,
}

const MAX_ADAPTERS: usize = 16;

/// The control-plane storage origin: `profile.storage.*` reads and writes
/// here; pages never reach it and it is persisted like any other origin.
const CONTROL_ORIGIN: &str = "minicon-surf://control";

struct AdapterRecord {
    target_id: String,
    kind: String,
}

impl Host {
    /// Operations only the in-process CDP edge may request: adapter
    /// bookkeeping is not part of control 0.0.1 and never reaches stdio,
    /// whose parser refuses unknown operation names.
    fn execute_bridge(&mut self, operation: &str, arguments: Value) -> Result<Value, String> {
        let outcome = match operation {
            "adapter.attach" => self.adapter_attach(&arguments),
            "adapter.detach" => self.adapter_detach(&arguments),
            "adapter.inspect" => self.adapter_inspect(&arguments),
            _ => {
                if !OPERATIONS.contains(&operation) {
                    return Err("invalid_request".into());
                }
                self.next_bridge_request += 1;
                let request = Request {
                    request_id: format!("req_cdp_{}", self.next_bridge_request),
                    deadline: Duration::from_millis(5000),
                    operation: operation.to_owned(),
                    arguments,
                };
                self.execute(&request)
            }
        };
        outcome.map_err(|error| error.code.to_owned())
    }

    fn adapter_attach(&mut self, arguments: &Value) -> Result<Value, ControlError> {
        let object = exact_object(arguments, &["target", "kind"])?;
        let target = typed_field(object, "target", "target")?;
        if !self.targets.contains_key(target) {
            return Err(not_found("target", target));
        }
        if self.adapters.len() >= MAX_ADAPTERS {
            return Err(ControlError::new(
                "resource_limit",
                "adapter capacity reached",
                true,
            ));
        }
        self.next_adapter += 1;
        let id = format!("adapter_{}", self.next_adapter);
        self.adapters.insert(
            id.clone(),
            AdapterRecord {
                target_id: target.to_owned(),
                kind: string_field(object, "kind")?.to_owned(),
            },
        );
        Ok(json!({"kind":"adapter","adapter":id,"target":target}))
    }

    fn adapter_detach(&mut self, arguments: &Value) -> Result<Value, ControlError> {
        let object = exact_object(arguments, &["adapter"])?;
        let id = string_field(object, "adapter")?;
        self.adapters
            .remove(id)
            .map(|_| json!({"kind":"adapter_detached","adapter":id}))
            .ok_or_else(|| ControlError::new("not_found", "adapter does not exist", false))
    }

    /// Alive only while its target is: the target's close removes the record.
    fn adapter_inspect(&self, arguments: &Value) -> Result<Value, ControlError> {
        let object = exact_object(arguments, &["adapter"])?;
        let id = string_field(object, "adapter")?;
        let record = self
            .adapters
            .get(id)
            .ok_or_else(|| ControlError::new("not_found", "adapter does not exist", false))?;
        if !self.targets.contains_key(&record.target_id) {
            return Err(not_found("target", &record.target_id));
        }
        Ok(
            json!({"kind":"adapter","adapter":id,"target":record.target_id,"adapter_kind":record.kind}),
        )
    }

    /// Detach every adapter of a target that is going away; returns how many.
    fn detach_adapters_of(&mut self, target_id: &str) -> usize {
        let before = self.adapters.len();
        self.adapters
            .retain(|_, record| record.target_id != target_id);
        let detached = before - self.adapters.len();
        self.adapters_detached_total += detached as u64;
        detached
    }

    /// The profile behind a target, through its session.
    fn target_profile_id(&self, target_id: &str) -> Option<String> {
        let target = self.targets.get(target_id)?;
        self.sessions
            .get(&target.session_id)
            .map(|s| s.profile_id.clone())
    }

    /// A working copy of a session's profile for a document at `url`.
    fn io_for(&self, session_id: &str, url: Option<&Url>) -> Result<TargetIo, ControlError> {
        let profile_id = self
            .sessions
            .get(session_id)
            .map(|s| s.profile_id.clone())
            .ok_or_else(|| not_found("session", session_id))?;
        let profile = self
            .profiles
            .get(&profile_id)
            .ok_or_else(|| not_found("profile", &profile_id))?;
        Ok(TargetIo {
            jar: profile.jar.clone(),
            storage: profile.storage.clone(),
            origin: url.map_or_else(
                || profile::OPAQUE_ORIGIN.to_owned(),
                |u| u.origin().ascii_serialization(),
            ),
            document_host: url.and_then(|u| u.host_str().map(|h| h.to_ascii_lowercase())),
            cookie_rejections: 0,
        })
    }

    /// Refresh a target's working copy from its profile before an operation
    /// so writes made through other targets or the control plane are seen.
    fn sync_target_io(&mut self, id: &str) {
        let Some(profile_id) = self.target_profile_id(id) else {
            return;
        };
        let (Some(target), Some(profile)) =
            (self.targets.get_mut(id), self.profiles.get(&profile_id))
        else {
            return;
        };
        target.io.jar = profile.jar.clone();
        target.io.storage = profile.storage.clone();
    }

    /// Seal and write a persistent profile's record (D5 order); ephemeral
    /// profiles commit in memory only.
    fn write_profile(&mut self, profile_id: &str) -> Result<(), ControlError> {
        let profile = self
            .profiles
            .get(profile_id)
            .ok_or_else(|| not_found("profile", profile_id))?;
        if !profile.persistent {
            return Ok(());
        }
        let (Some(source), Some(dek), Some(wrapped), Some(directory)) = (
            &self.key_source,
            &profile.dek,
            &profile.wrapped_dek,
            &profile.directory,
        ) else {
            return Err(ControlError::new(
                "internal",
                "persistent profile lacks its key or directory",
                false,
            )
            .scoped("profile", profile_id));
        };
        let data = profile::RecordData {
            persistent_cookies: profile.jar.persistent.clone(),
            storage: profile.storage.clone(),
        };
        let bytes = profile::seal_record(profile_id, dek, wrapped, &source.key_id(), &data)
            .map_err(|e| store_error(e, profile_id))?;
        let written =
            profile::commit_record(directory, &bytes).map_err(|e| store_error(e, profile_id))?;
        self.store_writes_total += 1;
        self.store_bytes_written_total += written as u64;
        Ok(())
    }

    /// Commit a target's working copy back to its profile: disk first for a
    /// persistent profile, memory only otherwise. A failed disk commit rolls
    /// the target and the profile back, marks the profile read-only for the
    /// rest of the host lifetime and reseeds the realm mirrors read-only.
    fn commit_target_io(&mut self, id: &str, deadline: Instant) -> Result<(), ControlError> {
        let Some(profile_id) = self.target_profile_id(id) else {
            return Ok(());
        };
        let Some(target) = self.targets.get(id) else {
            return Ok(());
        };
        let rejections = target.io.cookie_rejections;
        let (jar, storage) = (target.io.jar.clone(), target.io.storage.clone());
        let Some(profile) = self.profiles.get(&profile_id) else {
            return Ok(());
        };
        let unchanged = jar.persistent == profile.jar.persistent
            && jar.volatile == profile.jar.volatile
            && storage == profile.storage;
        self.cookie_rejections_total += rejections;
        if let Some(target) = self.targets.get_mut(id) {
            target.io.cookie_rejections = 0;
        }
        if unchanged {
            return Ok(());
        }
        if profile.read_only {
            self.rollback_target_io(id, &profile_id, deadline)?;
            return Err(commit_failed(
                id,
                "storage is read-only after an earlier failed commit",
            ));
        }
        let previous = {
            let profile = self.profiles.get_mut(&profile_id).expect("profile exists");
            (
                std::mem::replace(&mut profile.jar, jar),
                std::mem::replace(&mut profile.storage, storage),
            )
        };
        match self.write_profile(&profile_id) {
            Ok(()) => Ok(()),
            Err(error) => {
                let profile = self.profiles.get_mut(&profile_id).expect("profile exists");
                profile.jar = previous.0;
                profile.storage = previous.1;
                profile.read_only = true;
                self.rollback_target_io(id, &profile_id, deadline)?;
                Err(commit_failed(id, &error.message))
            }
        }
    }

    fn rollback_target_io(
        &mut self,
        id: &str,
        profile_id: &str,
        deadline: Instant,
    ) -> Result<(), ControlError> {
        let (Some(target), Some(profile)) =
            (self.targets.get_mut(id), self.profiles.get(profile_id))
        else {
            return Ok(());
        };
        target.io.jar = profile.jar.clone();
        target.io.storage = profile.storage.clone();
        target.seed_store(deadline, true)
    }

    /// A control-plane mutation of the session's profile with the same
    /// commit-or-rollback rule as page writes.
    fn commit_control_mutation(
        &mut self,
        profile_id: &str,
        mutate: impl FnOnce(&mut profile::Jar, &mut profile::Storage) -> Result<(), ControlError>,
    ) -> Result<(), ControlError> {
        let profile = self
            .profiles
            .get_mut(profile_id)
            .ok_or_else(|| not_found("profile", profile_id))?;
        if profile.read_only {
            return Err(commit_failed(
                profile_id,
                "storage is read-only after an earlier failed commit",
            ));
        }
        let previous = (profile.jar.clone(), profile.storage.clone());
        mutate(&mut profile.jar, &mut profile.storage)?;
        if let Err(error) = self.write_profile(profile_id) {
            let profile = self.profiles.get_mut(profile_id).expect("profile exists");
            profile.jar = previous.0;
            profile.storage = previous.1;
            profile.read_only = true;
            return Err(commit_failed(profile_id, &error.message));
        }
        Ok(())
    }

    /// D1 start-up: no keychain UI for the host lifetime, then load every
    /// persistent profile directory; a directory that fails to load is
    /// listed unavailable with its reason and never touched.
    fn enable_profile_store(&mut self, root: PathBuf, config_dir: PathBuf) -> io::Result<()> {
        std::fs::create_dir_all(&root)?;
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            std::fs::set_permissions(&root, std::fs::Permissions::from_mode(0o700))?;
        }
        std::fs::create_dir_all(&config_dir)?;
        let mode = match std::env::var("MINICON_SURF_PROFILE_STORE").as_deref() {
            Ok("envelope-keyfile-experiment") => profile::StoreMode::KeyfileExperiment,
            _ => profile::StoreMode::KeychainEnvelope,
        };
        // The host holds data keys: no core dumps. It never touches the keychain
        // itself; the helper process disables keychain UI on its own side.
        profile::disable_core_dumps();
        let source = profile::KeySource::new(mode, &root, &config_dir);
        let mut entries: Vec<_> = std::fs::read_dir(&root)?.flatten().collect();
        entries.sort_by_key(|e| e.file_name());
        for entry in entries {
            let Some(name) = entry.file_name().to_str().map(str::to_owned) else {
                continue;
            };
            if !entry.path().is_dir() || !profile::valid_profile_name(&name) {
                continue;
            }
            let id = format!("profile_{name}");
            let directory = entry.path();
            let loaded = profile::check_permissions(&directory)
                .and_then(|()| {
                    std::fs::read(directory.join(profile::RECORD_FILE))
                        .map_err(|e| profile::StoreError::Corrupt(format!("record: {e}")))
                })
                .and_then(|bytes| profile::open_record(&source, &id, &bytes));
            match loaded {
                Ok((dek, wrapped, data)) if self.profiles.len() < MAX_PROFILES => {
                    self.profiles.insert(
                        id.clone(),
                        Profile {
                            id,
                            name: Some(name),
                            persistent: true,
                            jar: profile::Jar {
                                persistent: data.persistent_cookies,
                                volatile: Vec::new(),
                            },
                            storage: data.storage,
                            dek: Some(dek),
                            wrapped_dek: Some(wrapped),
                            directory: Some(directory),
                            read_only: false,
                            lock: None,
                        },
                    );
                }
                Ok(_) => {
                    self.unavailable_profiles
                        .insert(name, "profile capacity exceeded during load".into());
                }
                Err(error) => {
                    // The reason names the failure class, never file contents.
                    self.unavailable_profiles.insert(
                        name,
                        match error {
                            profile::StoreError::KeychainUnavailable(_) => {
                                "keychain unavailable".into()
                            }
                            profile::StoreError::Corrupt(detail) => {
                                format!("corrupt or incompatible: {detail}")
                            }
                            profile::StoreError::Io(detail) => format!("unreadable: {detail}"),
                        },
                    );
                }
            }
        }
        self.profile_root = Some(root);
        self.key_source = Some(source);
        Ok(())
    }

    fn target_mut(&mut self, id: &str) -> Result<&mut Target, ControlError> {
        self.targets
            .get_mut(id)
            .ok_or_else(|| not_found("target", id))
    }

    /// The target's absolute revision: the live realm's count plus the base
    /// carried over every navigation, so it never decreases.
    fn revision(
        target: &mut Target,
        deadline: Instant,
        policy: &net::Policy,
    ) -> Result<u64, ControlError> {
        let text = target.eval(REVISION_JS, deadline, policy)?;
        text.parse::<i64>()
            .ok()
            .filter(|r| *r >= 0)
            .map(|r| target.revision_base + r as u64)
            .ok_or_else(|| {
                ControlError::new(
                    "internal",
                    "target lost its revision instrumentation",
                    false,
                )
                .scoped("target", &target.id)
            })
    }

    fn eval_json(
        target: &mut Target,
        script: &str,
        deadline: Instant,
        policy: &net::Policy,
    ) -> Result<Value, ControlError> {
        let text = target.eval(script, deadline, policy)?;
        serde_json::from_str(&text).map_err(|_| {
            ControlError::new("internal", "engine returned malformed snapshot JSON", false)
                .scoped("target", &target.id)
        })
    }

    fn execute(&mut self, request: &Request) -> Result<Value, ControlError> {
        let deadline = Instant::now() + request.deadline;
        let a = &request.arguments;
        // Target operations run on a working copy of the profile that is
        // synced before and committed after; a failed commit is the
        // operation's typed failure.
        let target_scoped = matches!(
            request.operation.as_str(),
            "target.inspect" | "target.snapshot" | "target.act" | "target.wait"
        );
        let target_id = if target_scoped {
            a.get("target").and_then(Value::as_str).map(str::to_owned)
        } else {
            None
        };
        if let Some(id) = &target_id {
            self.sync_target_io(id);
        }
        let outcome = self.dispatch(request, deadline);
        if let Some(id) = &target_id
            && self.targets.contains_key(id)
        {
            self.commit_target_io(id, deadline)?;
        }
        outcome
    }

    fn dispatch(&mut self, request: &Request, deadline: Instant) -> Result<Value, ControlError> {
        let a = &request.arguments;
        match request.operation.as_str() {
            "profile.create" => self.profile_create(a),
            "profile.list" => {
                let mut profiles = self
                    .profiles
                    .values()
                    .map(|p| json!({"profile":p.id,"name":p.name,"persistence":if p.persistent { "persistent" } else { "ephemeral" },"available":true}))
                    .collect::<Vec<_>>();
                for (name, reason) in &self.unavailable_profiles {
                    profiles.push(json!({"profile":format!("profile_{name}"),"name":name,"persistence":"persistent","available":false,"reason":reason}));
                }
                Ok(json!({"kind":"profile_list","profiles":profiles}))
            }
            "profile.inspect" => {
                let object = exact_object(a, &["profile"])?;
                let id = typed_field(object, "profile", "profile")?;
                let profile = self
                    .profiles
                    .get(id)
                    .ok_or_else(|| not_found("profile", id))?;
                Ok(json!({
                    "kind":"profile","profile":profile.id,"name":profile.name,
                    "persistence":if profile.persistent { "persistent" } else { "ephemeral" },
                    "sessions":self.sessions.values().filter(|s| s.profile_id == profile.id).count(),
                    "cookies":{"objects":profile.jar.len(),"persistent":profile.jar.persistent.len(),"volatile":profile.jar.volatile.len(),"bytes":profile.jar.accounted_bytes()},
                    "storage":{"keys":profile.storage.keys(),"origins":profile.storage.origins.len(),"bytes":profile.storage.accounted_bytes()},
                    "read_only":profile.read_only,
                    "store":self.key_source.as_ref().map(|k| k.mode.name()),
                    "budgets":profile_budgets(),
                }))
            }
            "profile.delete" => {
                let object = exact_object(a, &["profile"])?;
                let id = typed_field(object, "profile", "profile")?;
                if !self.profiles.contains_key(id) {
                    return Err(not_found("profile", id));
                }
                if self.sessions.values().any(|s| s.profile_id == id) {
                    return Err(
                        ControlError::new("conflict", "profile has a live session", true)
                            .scoped("profile", id),
                    );
                }
                let profile = self.profiles.remove(id).expect("profile exists");
                if let Some(directory) = &profile.directory {
                    std::fs::remove_dir_all(directory).map_err(|e| {
                        ControlError::new(
                            "internal",
                            format!("profile directory removal failed: {e}"),
                            true,
                        )
                        .scoped("profile", id)
                    })?;
                }
                Ok(
                    json!({"kind":"profile_deleted","profile":id,"persistence":if profile.persistent { "persistent" } else { "ephemeral" }}),
                )
            }
            "profile.storage.put" => self.profile_storage_put(a),
            "profile.storage.get" => self.profile_storage_get(a),
            "session.open" => self.session_open(a),
            "session.list" => Ok(
                json!({"kind":"session_list","sessions":self.sessions.values().map(|s| json!({"session":s.id,"profile":s.profile_id})).collect::<Vec<_>>()}),
            ),
            "session.close" => self.session_close(a),
            "target.open" => self.target_open(a, deadline),
            "target.list" => Ok(
                json!({"kind":"target_list","targets":self.targets.values().map(|t| json!({"target":t.id,"session":t.session_id,"fixture":t.fixture,"url":t.url.as_ref().map(Url::as_str)})).collect::<Vec<_>>()}),
            ),
            "target.inspect" => {
                let object = exact_object(a, &["target"])?;
                let id = typed_field(object, "target", "target")?.to_owned();
                let policy = self.policy.clone();
                let target = self.target_mut(&id)?;
                let revision = Self::revision(target, deadline, &policy)?;
                Ok(json!({
                    "kind":"target","target":target.id,"session":target.session_id,"fixture":target.fixture,
                    "url":target.url.as_ref().map(Url::as_str),"document_framing":target.document_framing,"revision":revision,"load_complete":true,"crashed":false,
                    "script_realm":true,"scripts_run":target.script_count,"scripts_skipped":target.skipped_scripts,
                    "frames":[{"frame":target.frame_id,"parent":null,"generation":target.generation,"realm":target.realm_id}],
                    "realms":[{"realm":target.realm_id,"frame":target.frame_id,"world":"main"}],
                    "frame_limit":1,
                    "network":{"fetches":target.budget.fetches,"bytes":target.budget.bytes,"denied":target.budget.denied}
                }))
            }
            "target.close" => {
                let object = exact_object(a, &["target"])?;
                let id = typed_field(object, "target", "target")?;
                self.targets
                    .remove(id)
                    .ok_or_else(|| not_found("target", id))?;
                let detached = self.detach_adapters_of(id);
                Ok(
                    json!({"kind":"target_closed","target":id,"teardown":{"adapters_detached":detached,"order":["adapters","target"]}}),
                )
            }
            "target.snapshot" => self.target_snapshot(a, deadline),
            "target.act" => self.target_act(a, deadline),
            "target.wait" => self.target_wait(a, deadline),
            "memory.report" => Ok(self.memory_report()),
            "memory.trim" => {
                exact_object(a, &[])?;
                #[cfg(target_os = "macos")]
                {
                    // SAFETY: a null zone requests pressure relief from every
                    // malloc zone; a zero goal asks for everything reclaimable.
                    let released = unsafe { malloc_zone_pressure_relief(std::ptr::null_mut(), 0) };
                    let arena_released: usize =
                        self.targets.values().map(|t| t.realm.trim_arena()).sum();
                    Ok(json!({
                        "kind":"memory_trim",
                        "strategy":"malloc_zone_pressure_relief+arena_tail_madvise",
                        "release_reporting":"bytes",
                        "released_bytes":released,
                        "arena_released_bytes":arena_released,
                        "libmalloc":libmalloc_statistics(),
                    }))
                }
                #[cfg(not(target_os = "macos"))]
                {
                    Err(ControlError::new(
                        "unsupported_capability",
                        "memory.trim is qualified on macOS only",
                        false,
                    ))
                }
            }
            other => Err(unsupported_operation(other)),
        }
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
        let persistent = match string_field(object, "persistence")? {
            "ephemeral" => false,
            "persistent" => true,
            _ => return Err(invalid("persistence must be ephemeral or persistent")),
        };
        if persistent {
            return self.profile_create_persistent(object);
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
                persistent: false,
                jar: profile::Jar::default(),
                storage: profile::Storage::default(),
                dek: None,
                wrapped_dek: None,
                directory: None,
                read_only: false,
                lock: None,
            },
        );
        Ok(
            json!({"kind":"profile","profile":id,"name":name,"persistence":"ephemeral","created":true}),
        )
    }

    /// D1: a persistent profile exists only when the keychain-backed store
    /// can seal its first record; nothing is written before that succeeds.
    fn profile_create_persistent(
        &mut self,
        object: &Map<String, Value>,
    ) -> Result<Value, ControlError> {
        let (Some(root), Some(source)) = (self.profile_root.clone(), self.key_source.as_ref())
        else {
            return Err(ControlError::new(
                "unsupported_capability",
                "persistent profiles need --profile-root and a master-key source",
                false,
            ));
        };
        let name = object
            .get("name")
            .and_then(Value::as_str)
            .ok_or_else(|| invalid("persistent profiles need a name"))?;
        if !profile::valid_profile_name(name) {
            return Err(invalid(
                "name must be 1 to 32 lowercase letters, digits or hyphens",
            ));
        }
        let id = format!("profile_{name}");
        if self.profiles.contains_key(&id)
            || self.unavailable_profiles.contains_key(name)
            || root.join(name).exists()
        {
            return Err(
                ControlError::new("conflict", "profile name already exists", false)
                    .scoped("profile", &id),
            );
        }
        if self.profiles.len() >= MAX_PROFILES {
            return Err(ControlError::new(
                "resource_limit",
                "profile capacity reached",
                true,
            ));
        }
        let dek = profile::random_bytes(32).map_err(|e| store_error(e, &id))?;
        // Wrap first: a missing or locked keychain fails here, before any file.
        // The wrapped key is stored unchanged by every later write.
        let wrapped = source
            .wrap_dek(&id, &dek)
            .map_err(|e| store_error(e, &id))?;
        let bytes = profile::seal_record(
            &id,
            &dek,
            &wrapped,
            &source.key_id(),
            &profile::RecordData::default(),
        )
        .map_err(|e| store_error(e, &id))?;
        let directory =
            profile::create_profile_dir(&root, name).map_err(|e| store_error(e, &id))?;
        if let Err(error) = profile::commit_record(&directory, &bytes) {
            let _ = std::fs::remove_dir_all(&directory);
            return Err(store_error(error, &id));
        }
        self.store_writes_total += 1;
        self.store_bytes_written_total += bytes.len() as u64;
        self.profiles.insert(
            id.clone(),
            Profile {
                id: id.clone(),
                name: Some(name.to_owned()),
                persistent: true,
                jar: profile::Jar::default(),
                storage: profile::Storage::default(),
                dek: Some(dek),
                wrapped_dek: Some(wrapped),
                directory: Some(directory),
                read_only: false,
                lock: None,
            },
        );
        Ok(
            json!({"kind":"profile","profile":id,"name":name,"persistence":"persistent","created":true,"store":source.mode.name()}),
        )
    }

    fn session_open(&mut self, arguments: &Value) -> Result<Value, ControlError> {
        let object = exact_object(arguments, &["profile"])?;
        let profile = typed_field(object, "profile", "profile")?;
        if !self.profiles.contains_key(profile) {
            return Err(not_found("profile", profile));
        }
        if self.sessions.len() >= MAX_SESSIONS {
            return Err(ControlError::new(
                "resource_limit",
                "session capacity reached",
                true,
            ));
        }
        // One live session per profile: a session is the profile's live handle, and
        // the volatile jar (D4) is shared across the profile's sessions in sequence.
        if self.sessions.values().any(|s| s.profile_id == profile) {
            return Err(ControlError::new(
                "resource_limit",
                "this profile owns one live session; close it first",
                true,
            ));
        }
        let record = self.profiles.get_mut(profile).expect("profile exists");
        record.jar.expire(profile::now_seconds());
        if record.persistent && record.lock.is_none() {
            let directory = record
                .directory
                .clone()
                .expect("persistent profile has a directory");
            match profile::try_lock(&directory) {
                Ok(Some(file)) => record.lock = Some(file),
                Ok(None) => {
                    return Err(ControlError::new(
                        "profile_locked",
                        "another host holds this profile's writer lock",
                        true,
                    )
                    .scoped("profile", profile));
                }
                Err(error) => return Err(store_error(error, profile)),
            }
        }
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

    fn session_close(&mut self, arguments: &Value) -> Result<Value, ControlError> {
        let object = exact_object(arguments, &["session"])?;
        let id = typed_field(object, "session", "session")?;
        let session = self
            .sessions
            .remove(id)
            .ok_or_else(|| not_found("session", id))?;
        let ids: Vec<String> = self
            .targets
            .values()
            .filter(|t| t.session_id == session.id)
            .map(|t| t.id.clone())
            .collect();
        let closed = ids.len();
        let mut detached = 0;
        for id in ids {
            self.targets.remove(&id);
            detached += self.detach_adapters_of(&id);
        }
        // The writer lock goes last and only when no session of this profile
        // remains; the volatile jar stays with the profile.
        if !self
            .sessions
            .values()
            .any(|s| s.profile_id == session.profile_id)
            && let Some(profile) = self.profiles.get_mut(&session.profile_id)
        {
            profile.lock = None;
        }
        Ok(
            json!({"kind":"session_closed","session":session.id,"profile":session.profile_id,"closed_targets":closed,"teardown":{"adapters_detached":detached,"order":["adapters","targets","profile_lock"]}}),
        )
    }

    fn session_profile_for(&self, session_id: &str) -> Result<String, ControlError> {
        self.sessions
            .get(session_id)
            .map(|s| s.profile_id.clone())
            .ok_or_else(|| not_found("session", session_id))
    }

    fn profile_storage_put(&mut self, arguments: &Value) -> Result<Value, ControlError> {
        let object = exact_object(arguments, &["session", "kind", "key", "value"])?;
        let session_id = typed_field(object, "session", "session")?;
        let profile_id = self.session_profile_for(session_id)?;
        let kind = string_field(object, "kind")?.to_owned();
        let key = string_field(object, "key")?.to_owned();
        // The value bound is wide enough for the jar and storage budgets to be the
        // deciding limit (a cookie over 4,096 bytes must surface as resource_limit).
        let value = object
            .get("value")
            .and_then(Value::as_str)
            .filter(|s| s.len() <= 2 * profile::MAX_COOKIE_BYTES)
            .ok_or_else(|| invalid("value must be a string of at most 8192 bytes"))?
            .to_owned();
        if key.is_empty() || key.len() > profile::MAX_STORAGE_KEY_BYTES {
            return Err(invalid("key must be 1 to 64 bytes"));
        }
        let now = profile::now_seconds();
        let outcome =
            self.commit_control_mutation(&profile_id, |jar, storage| match kind.as_str() {
                "cookie" => jar.put_control(&key, &value, now).map_err(|rejection| {
                    ControlError::new("resource_limit", "cookie refused", false)
                        .scoped("profile", &profile_id)
                        .details(json!({"reason":rejection.name()}))
                }),
                "local_storage" => {
                    let other = jar.accounted_bytes();
                    storage
                        .set(CONTROL_ORIGIN, &key, &value, other)
                        .map_err(|rejection| {
                            ControlError::new("resource_limit", "storage budget exceeded", false)
                                .scoped("profile", &profile_id)
                                .details(
                                    json!({"reason":format!("{rejection:?}").to_ascii_lowercase()}),
                                )
                        })
                }
                _ => Err(invalid("kind must be cookie or local_storage")),
            });
        outcome?;
        Ok(json!({"kind":"profile_storage_put","profile":profile_id,"stored":true}))
    }

    fn profile_storage_get(&self, arguments: &Value) -> Result<Value, ControlError> {
        let object = exact_object(arguments, &["session", "kind", "key"])?;
        let session_id = typed_field(object, "session", "session")?;
        let profile_id = self.session_profile_for(session_id)?;
        let kind = string_field(object, "kind")?;
        let key = string_field(object, "key")?;
        let profile = self
            .profiles
            .get(&profile_id)
            .ok_or_else(|| not_found("profile", &profile_id))?;
        let value = match kind {
            "cookie" => profile.jar.get_control(key),
            "local_storage" => profile.storage.get(CONTROL_ORIGIN, key),
            _ => return Err(invalid("kind must be cookie or local_storage")),
        };
        Ok(
            json!({"kind":"profile_storage_get","profile":profile_id,"found":value.is_some(),"value":value}),
        )
    }

    fn target_open(&mut self, arguments: &Value, deadline: Instant) -> Result<Value, ControlError> {
        let object = arguments
            .as_object()
            .ok_or_else(|| invalid("arguments must be an object"))?;
        let by_fixture =
            object.len() == 2 && object.contains_key("session") && object.contains_key("fixture");
        let by_url =
            object.len() == 2 && object.contains_key("session") && object.contains_key("url");
        if !by_fixture && !by_url {
            return Err(invalid(
                "target.open takes session plus exactly one of fixture or url",
            ));
        }
        let session = typed_field(object, "session", "session")?;
        if !self.sessions.contains_key(session) {
            return Err(not_found("session", session));
        }
        if self.targets.len() >= MAX_TARGETS {
            return Err(ControlError::new(
                "resource_limit",
                "target capacity reached",
                true,
            ));
        }
        let source = if by_fixture {
            let fixture = string_field(object, "fixture")?;
            if !valid_fixture_name(fixture) {
                return Err(invalid("fixture must be a court fixture file name"));
            }
            Source::Fixture(fixture.to_owned())
        } else {
            Source::Url(string_field(object, "url")?.to_owned())
        };
        self.next_target += 1;
        self.next_frame += 1;
        let id = format!("target_{}", self.next_target);
        let frame_id = format!("frame_{}", self.next_frame);
        let io = self.io_for(session, None)?;
        let mut target = self.build_target(
            &id,
            session,
            source,
            net::Budget::default(),
            frame_id,
            1,
            0,
            deadline,
            io,
        )?;
        let policy = self.policy.clone();
        let revision = Self::revision(&mut target, deadline, &policy)?;
        let summary = json!({
            "kind":"target","target":id,"session":session,"revision":revision,"fixture":target.fixture,
            "url":target.url.as_ref().map(Url::as_str),"document_framing":target.document_framing,"scripts_run":target.script_count,
            "scripts_skipped":target.skipped_scripts.len(),
            "frame":target.frame_id,"generation":target.generation,"realm":target.realm_id,
            "network":{"fetches":target.budget.fetches,"bytes":target.budget.bytes,"denied":target.budget.denied}
        });
        self.targets.insert(id.clone(), target);
        // The page's writes during load reach the profile now; a failed
        // commit keeps the target (its document is real) but reports the
        // failure with the target id, and its storage is read-only.
        self.commit_target_io(&id, deadline)?;
        Ok(summary)
    }

    /// Build a complete target for one document: fetch or read it, parse
    /// it, mint a realm, seed and run its scripts under the policy and the
    /// given budget, and install the revision instrumentation. Nothing is
    /// shared with any existing target, so a failure leaves the caller's
    /// state untouched; `target.open` inserts the result and a navigation
    /// swaps it into the existing target.
    #[allow(clippy::too_many_arguments)]
    fn build_target(
        &mut self,
        id: &str,
        session: &str,
        source: Source,
        mut budget: net::Budget,
        frame_id: String,
        generation: u64,
        revision_base: u64,
        deadline: Instant,
        mut io: TargetIo,
    ) -> Result<Target, ControlError> {
        let policy = self.policy.clone();
        let now = profile::now_seconds();
        let (label, base, bytes, framing) = match source {
            Source::Fixture(fixture) => {
                let path = self.fixture_root.join(&fixture);
                let size = std::fs::metadata(&path).map(|m| m.len()).unwrap_or(0);
                if size > MAX_FIXTURE_BYTES {
                    return Err(ControlError::new(
                        "resource_limit",
                        "fixture exceeds the bounded document size",
                        false,
                    ));
                }
                let bytes = std::fs::read(&path).map_err(|_| {
                    ControlError::new("not_found", "fixture does not exist in the court", false)
                })?;
                (fixture, None, bytes, "fixture")
            }
            Source::Url(raw) => {
                let response = {
                    let mut hooks = JarHooks {
                        jar: &mut io.jar,
                        document_host: None,
                        now,
                        rejections: &mut io.cookie_rejections,
                    };
                    net::fetch_with(&raw, &policy, &mut budget, deadline, Some(&mut hooks))
                        .map_err(|error| net_error(error, id))?
                };
                if response.status >= 400 {
                    return Err(ControlError::new(
                        "not_found",
                        "document request was not successful",
                        false,
                    )
                    .scoped("target", id)
                    .details(json!({"status":response.status,"url":response.url.as_str()})));
                }
                if response
                    .content_type
                    .as_deref()
                    .is_some_and(|t| !t.starts_with("text/html"))
                {
                    return Err(ControlError::new(
                        "unsupported_capability",
                        "document is not text/html",
                        false,
                    )
                    .scoped("target", id)
                    .details(json!({"content_type":response.content_type})));
                }
                (
                    "url".to_owned(),
                    Some(response.url.clone()),
                    response.body,
                    response.framing.as_str(),
                )
            }
        };

        let text = String::from_utf8_lossy(&bytes).into_owned();
        let document = Document::from(text.as_str());
        let element_count = document.select("*").nodes().len();
        let mut tree = Vec::new();
        serialize_children(&document.root(), &mut tree);
        // Scripts in document order: inline text, or a same-origin external
        // source fetched under the same policy and budget.
        let mut scripts: Vec<(String, String)> = Vec::new();
        let mut skipped = Vec::new();
        let mut external = 0usize;
        for node in document.select("script").nodes() {
            match node.attr("src") {
                None => scripts.push(("inline".into(), node.text().to_string())),
                Some(src) => {
                    let src = src.to_string();
                    let Some(base_url) = &base else {
                        skipped.push(
                            json!({"src":src,"reason":"external scripts need a network origin"}),
                        );
                        continue;
                    };
                    let Ok(resolved) = base_url.join(&src) else {
                        skipped.push(json!({"src":src,"reason":"malformed src"}));
                        continue;
                    };
                    if !net::same_origin(base_url, &resolved) {
                        budget.denied += 1;
                        skipped.push(json!({"src":src,"reason":"cross-origin script refused"}));
                        continue;
                    }
                    if external >= net::MAX_EXTERNAL_SCRIPTS {
                        budget.denied += 1;
                        skipped.push(json!({"src":src,"reason":"external script limit"}));
                        continue;
                    }
                    external += 1;
                    let document_host = base_url.host_str().map(|h| h.to_ascii_lowercase());
                    let mut hooks = JarHooks {
                        jar: &mut io.jar,
                        document_host: document_host.as_deref(),
                        now,
                        rejections: &mut io.cookie_rejections,
                    };
                    match net::fetch_with(
                        resolved.as_str(),
                        &policy,
                        &mut budget,
                        deadline,
                        Some(&mut hooks),
                    ) {
                        Ok(response) if response.status < 400 => scripts
                            .push((src, String::from_utf8_lossy(&response.body).into_owned())),
                        Ok(response) => skipped.push(
                            json!({"src":src,"reason":format!("status {}", response.status)}),
                        ),
                        Err(error) => {
                            skipped.push(json!({"src":src,"reason":error.reason,"code":error.code}))
                        }
                    }
                }
            }
        }
        drop(document);

        let realm = Realm::new(self.realm_allocation)?;
        realm.eval(DOM_SHIM_JS, deadline, id)?;
        let seed = format!(
            "__mcsSeed({})",
            serde_json::to_string(&tree).expect("tree serializes")
        );
        realm.eval(&seed, deadline, id)?;
        if let Some(base_url) = &base {
            realm.eval(
                &format!(
                    "__mcsLocation({})",
                    json!({
                        "href": base_url.as_str(),
                        "origin": base_url.origin().ascii_serialization(),
                        "protocol": format!("{}:", base_url.scheme()),
                        "host": base_url.host_str().map(|h| match base_url.port() {
                            Some(p) => format!("{h}:{p}"),
                            None => h.to_owned(),
                        }).unwrap_or_default(),
                        "hostname": base_url.host_str().unwrap_or_default(),
                        "port": base_url.port().map(|p| p.to_string()).unwrap_or_default(),
                        "pathname": base_url.path(),
                        "search": base_url.query().map(|q| format!("?{q}")).unwrap_or_default(),
                        "hash": base_url.fragment().map(|f| format!("#{f}")).unwrap_or_default(),
                    })
                ),
                deadline,
                id,
            )?;
            io.origin = base_url.origin().ascii_serialization();
            io.document_host = base_url.host_str().map(|h| h.to_ascii_lowercase());
        } else {
            io.origin = profile::OPAQUE_ORIGIN.to_owned();
            io.document_host = None;
        }
        // The realm id is minted only once the document exists; a failed
        // build never consumes one.
        self.next_realm += 1;
        let mut target = Target {
            id: id.to_owned(),
            session_id: session.to_owned(),
            fixture: label,
            url: base,
            document_framing: framing,
            fixture_bytes: bytes.len(),
            element_count,
            script_count: scripts.len(),
            skipped_scripts: skipped,
            budget,
            realm,
            last_snapshot: None,
            frame_id,
            generation,
            realm_id: format!("realm_{}", self.next_realm),
            revision_base,
            io,
        };
        let read_only = self
            .sessions
            .get(session)
            .and_then(|s| self.profiles.get(&s.profile_id))
            .is_some_and(|p| p.read_only);
        target.seed_store(deadline, read_only)?;
        for (index, (origin, script)) in scripts.iter().enumerate() {
            if let Err(error) = target.eval(script, deadline, &policy) {
                let mut details = error.details.clone().unwrap_or_else(|| json!({}));
                details["script_index"] = json!(index);
                details["script"] = json!(origin);
                return Err(ControlError::new("target_crashed", "a script threw", false)
                    .scoped("target", id)
                    .details(details));
            }
        }
        target.eval("__mcsComplete()", deadline, &policy)?;
        target.eval(INSTALL_JS, deadline, &policy)?;
        Ok(target)
    }

    /// Same-frame navigation after a link click: the new document is built
    /// completely (fetch under the target's own policy and budget, parse,
    /// realm, scripts) before anything in the live target changes; on any
    /// failure the target keeps its document, realm, generation and
    /// revision, and only the network budget records the attempt.
    fn navigate(&mut self, id: &str, href: &str, deadline: Instant) -> Result<Value, ControlError> {
        let policy = self.policy.clone();
        let prepared = {
            let target = self.target_mut(id)?;
            let current = Self::revision(target, deadline, &policy)?;
            let source = match &target.url {
                Some(base_url) => base_url
                    .join(href)
                    .map(|resolved| Source::Url(resolved.into()))
                    .map_err(|_| {
                        ControlError::new("invalid_request", "link href is malformed", false)
                            .scoped("target", id)
                    }),
                None if valid_fixture_name(href) => Ok(Source::Fixture(href.to_owned())),
                None => Err(ControlError::new(
                    "unsupported_capability",
                    "a fixture target can only follow links to court fixture files",
                    false,
                )
                .scoped("target", id)),
            };
            source.map(|source| {
                (
                    target.session_id.clone(),
                    source,
                    target.budget.clone(),
                    target.frame_id.clone(),
                    target.generation,
                    current,
                )
            })
        };
        let built = match prepared {
            Ok((session, source, budget, frame_id, generation, base_revision)) => {
                match self.io_for(&session, None) {
                    Ok(io) => self.build_target(
                        id,
                        &session,
                        source,
                        budget,
                        frame_id,
                        generation + 1,
                        base_revision + 1,
                        deadline,
                        io,
                    ),
                    Err(error) => Err(error),
                }
            }
            Err(error) => Err(error),
        };
        // The new document's writes commit before the swap; if the disk
        // refuses them the navigation fails and the old target stays.
        let built = match built {
            Ok(mut replacement) => {
                let profile_id = self.target_profile_id(id);
                let committed = match profile_id.as_deref().and_then(|p| self.profiles.get(p)) {
                    Some(profile)
                        if profile.persistent
                            && (replacement.io.jar.persistent != profile.jar.persistent
                                || replacement.io.storage != profile.storage) =>
                    {
                        if profile.read_only {
                            Err(commit_failed(
                                id,
                                "storage is read-only after an earlier failed commit",
                            ))
                        } else {
                            let profile_id = profile_id.clone().expect("profile id");
                            let previous = {
                                let profile =
                                    self.profiles.get_mut(&profile_id).expect("profile exists");
                                (
                                    std::mem::replace(&mut profile.jar, replacement.io.jar.clone()),
                                    std::mem::replace(
                                        &mut profile.storage,
                                        replacement.io.storage.clone(),
                                    ),
                                )
                            };
                            match self.write_profile(&profile_id) {
                                Ok(()) => Ok(()),
                                Err(error) => {
                                    let profile =
                                        self.profiles.get_mut(&profile_id).expect("profile exists");
                                    profile.jar = previous.0;
                                    profile.storage = previous.1;
                                    profile.read_only = true;
                                    Err(commit_failed(id, &error.message))
                                }
                            }
                        }
                    }
                    Some(_) => {
                        let profile_id = profile_id.clone().expect("profile id");
                        let profile = self.profiles.get_mut(&profile_id).expect("profile exists");
                        profile.jar = replacement.io.jar.clone();
                        profile.storage = replacement.io.storage.clone();
                        Ok(())
                    }
                    None => Ok(()),
                };
                replacement.io.cookie_rejections = 0;
                committed.map(|()| replacement)
            }
            Err(error) => Err(error),
        };
        let target = self.target_mut(id)?;
        match built {
            Ok(replacement) => {
                let retired_realm = std::mem::replace(target, replacement).realm_id;
                self.realms_retired_total += 1;
                self.navigations_total += 1;
                let target = self.target_mut(id)?;
                let revision = Self::revision(target, deadline, &policy)?;
                Ok(json!({
                    "kind":"action","target":id,"revision":revision,"applied":true,"navigated":true,
                    "frame":target.frame_id,"generation":target.generation,"realm":target.realm_id,
                    "retired_realm":retired_realm,"url":target.url.as_ref().map(Url::as_str),"fixture":target.fixture,
                    "network":{"fetches":target.budget.fetches,"bytes":target.budget.bytes,"denied":target.budget.denied},
                }))
            }
            Err(error) => {
                // Only the attempt's network accounting reaches the live
                // target; document, realm, generation and revision are as
                // they were.
                target.budget.denied += 1;
                let mut details = error.details.clone().unwrap_or_else(|| json!({}));
                details["navigation"] = json!("failed");
                details["href"] = json!(href);
                details["generation"] = json!(target.generation);
                details["realm"] = json!(target.realm_id);
                Err(error.details(details))
            }
        }
    }

    fn target_snapshot(
        &mut self,
        arguments: &Value,
        deadline: Instant,
    ) -> Result<Value, ControlError> {
        let object = allowed_object(
            arguments,
            &["target", "format", "max_bytes", "max_nodes"],
            &["frame", "realm"],
        )?;
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
        let policy = self.policy.clone();
        let target = self.target_mut(&id)?;
        // A foreign, retired or unknown frame or realm is one and the same
        // refusal: only the target's live main frame and realm exist.
        if object.get("frame").is_some() {
            let frame = typed_field(object, "frame", "frame")?;
            if frame != target.frame_id {
                return Err(
                    not_found("frame", frame).details(json!({"reason":"frame_not_live_in_target"}))
                );
            }
        }
        if object.get("realm").is_some() {
            let realm = typed_field(object, "realm", "realm")?;
            if realm != target.realm_id {
                return Err(not_found("realm", realm).details(
                    json!({"reason":"realm_not_live_in_target","frame":target.frame_id}),
                ));
            }
        }
        let (frame_id, realm_id, generation, base) = (
            target.frame_id.clone(),
            target.realm_id.clone(),
            target.generation,
            target.revision_base,
        );
        let raw = Self::eval_json(target, &snapshot_script(max_nodes), deadline, &policy)?;
        if raw.get("error").is_some() {
            return Err(ControlError::new(
                "internal",
                "target lost its revision instrumentation",
                false,
            )
            .scoped("target", &id));
        }
        let revision = raw
            .get("revision")
            .and_then(Value::as_u64)
            .map(|r| base + r)
            .ok_or_else(|| {
                ControlError::new("internal", "snapshot lacks a revision", false)
                    .scoped("target", &id)
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
        let count = nodes.len();
        self.targets
            .get_mut(&id)
            .expect("target exists")
            .last_snapshot = Some((revision, count));
        Ok(json!({
            "kind":"semantic_snapshot","target":id,"revision":revision,
            "frame":frame_id,"realm":realm_id,"generation":generation,
            "truncated":truncated,"nodes":nodes,
        }))
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
                "the native DOM slice offers click only",
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
        let policy = self.policy.clone();
        let target = self.target_mut(&id)?;
        let current = Self::revision(target, deadline, &policy)?;
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
        let base = target.revision_base;
        let outcome = Self::eval_json(
            target,
            &act_script(revision - base, index),
            deadline,
            &policy,
        )?;
        if let Some(current) = outcome.get("current").and_then(Value::as_u64) {
            return Err(ControlError::new(
                "stale_revision",
                "node reference revision no longer matches the target",
                true,
            )
            .scoped("target", &id)
            .details(json!({"reference_revision":revision,"current_revision":base + current})));
        }
        if outcome.get("missing").is_some() {
            return Err(
                ControlError::new("not_found", "node does not exist", false).scoped("target", &id)
            );
        }
        if outcome.get("unsupported").is_some() {
            return Err(ControlError::new(
                "unsupported_capability",
                "click requires a button or link node",
                false,
            ));
        }
        if let Some(href) = outcome.get("navigate").and_then(Value::as_str) {
            let href = href.to_owned();
            return self.navigate(&id, &href, deadline);
        }
        if outcome.get("applied").and_then(Value::as_bool) != Some(true) {
            return Err(
                ControlError::new("internal", "engine did not confirm the action", false)
                    .scoped("target", &id),
            );
        }
        let after = Self::revision(target, deadline, &policy)?;
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
                "this host offers revision_at_least only",
                false,
            ));
        }
        let expected = bounded_u64(condition, "revision", 0, u64::MAX)?;
        let policy = self.policy.clone();
        loop {
            let target = self.target_mut(&id)?;
            let revision = Self::revision(target, deadline, &policy)?;
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
            // Only queued microtasks and fetch settlements, both served by
            // the revision poll above, can still change the revision.
            std::thread::sleep(Duration::from_millis(5));
        }
    }

    fn memory_report(&self) -> Value {
        let fixture_bytes: usize = self.targets.values().map(|t| t.fixture_bytes).sum();
        let elements: usize = self.targets.values().map(|t| t.element_count).sum();
        let realm_bytes: usize = self.targets.values().map(|t| t.realm.malloc_bytes()).sum();
        let zones: Vec<Value> = self
            .targets
            .values()
            .filter_map(|t| t.realm.zone_statistics())
            .collect();
        let arenas: Vec<Value> = self
            .targets
            .values()
            .filter_map(|t| t.realm.arena_statistics())
            .collect();
        #[cfg(target_os = "macos")]
        let (arenas_unmapped, arena_leaked) = (
            arena::ARENAS_UNMAPPED.load(std::sync::atomic::Ordering::Relaxed),
            arena::ARENA_BLOCKS_LEAKED.load(std::sync::atomic::Ordering::Relaxed),
        );
        #[cfg(not(target_os = "macos"))]
        let (arenas_unmapped, arena_leaked) = (0usize, 0usize);
        let fetches: usize = self.targets.values().map(|t| t.budget.fetches).sum();
        let network_bytes: usize = self.targets.values().map(|t| t.budget.bytes).sum();
        let denied: usize = self.targets.values().map(|t| t.budget.denied).sum();
        json!({
            "kind":"memory_report",
            "semantic":"native-dom-logical-owners-plus-script-realm-and-libmalloc-statistics",
            "owners":{
                "profiles":{
                    "objects":self.profiles.len(),"object_limit":MAX_PROFILES,
                    "persistent":self.profiles.values().filter(|p| p.persistent).count(),
                    "unavailable":self.unavailable_profiles.len(),
                    "bytes":self.profiles.values().map(|p| p.jar.accounted_bytes() + p.storage.accounted_bytes()).sum::<usize>(),
                    "cookies":self.profiles.values().map(|p| p.jar.len()).sum::<usize>(),
                    "storage_keys":self.profiles.values().map(|p| p.storage.keys()).sum::<usize>(),
                    "store":self.key_source.as_ref().map(|k| k.mode.name()),
                    "keychain_helper":self.key_source.as_ref().map(|k| k.helper.to_json()),
                    "store_writes_total":self.store_writes_total,
                    "store_bytes_written_total":self.store_bytes_written_total,
                    "cookie_rejections_total":self.cookie_rejections_total,
                    "budgets":profile_budgets(),
                },
                "sessions":{"objects":self.sessions.len(),"object_limit":MAX_SESSIONS},
                "targets":{"objects":self.targets.len(),"object_limit":MAX_TARGETS,"fixture_bytes":fixture_bytes,"elements":elements},
                "frames":{"objects":self.targets.len(),"object_limit":MAX_TARGETS,"frames_per_target":1},
                "realms":{"objects":self.targets.len(),"retired_total":self.realms_retired_total,"navigations_total":self.navigations_total},
                "adapters":{"objects":self.adapters.len(),"object_limit":MAX_ADAPTERS,"detached_total":self.adapters_detached_total},
                "script_realms":{"objects":self.targets.len(),"malloc_bytes":realm_bytes,"memory_limit_bytes":REALM_MEMORY_LIMIT,"dedicated_zones":zones,"dedicated_arenas":arenas},
                "network":{"fetches":fetches,"bytes":network_bytes,"denied":denied,"limits":{"redirects":net::MAX_REDIRECTS,"response_bytes":net::MAX_RESPONSE_BYTES,"per_fetch_ms":net::PER_FETCH_TIMEOUT.as_millis() as u64,"pending_per_turn":net::MAX_PENDING_PER_TURN,"fetches_per_target":net::MAX_FETCHES_PER_TARGET,"bytes_per_target":net::MAX_BYTES_PER_TARGET,"allowed_origins":self.policy.allowed_origins.len()}},
            },
            "allocator":{"realm_allocation":self.realm_allocation.name(),"realm_zone":self.realm_allocation == RealmAllocation::Zone,"realm_arena":self.realm_allocation == RealmAllocation::Arena,"realm_arena_reserved_bytes":REALM_ARENA_BYTES,"rust_global":"system","zones_destroyed":ZONES_DESTROYED.load(std::sync::atomic::Ordering::Relaxed),"zone_blocks_leaked_total":ZONE_BLOCKS_LEAKED.load(std::sync::atomic::Ordering::Relaxed),"arenas_unmapped":arenas_unmapped,"arena_blocks_leaked_total":arena_leaked},
            "libmalloc":libmalloc_statistics(),
            "limitations":["logical owners are document sizes, QuickJS malloc bytes and fetched bytes, not process memory","no layout, image or storage owners exist in this slice","not process RSS/private/PSS"],
        })
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
        "usage: native-dom-control serve --stdio --fixture-root DIR --config-dir DIR [--allow-origin http://HOST:PORT]... [--cdp-port PORT --ready-file PATH] [--profile-root DIR]"
    );
    std::process::exit(64);
}

fn main() -> Result<(), Box<dyn Error>> {
    let arguments: Vec<String> = std::env::args().skip(1).collect();
    // Hidden helper mode: the same signed binary serves one keychain
    // exchange over its stdio pipes and exits. Not part of the usage text.
    if arguments.len() == 1 && arguments[0] == profile::HELPER_SUBCOMMAND {
        std::process::exit(profile::run_keychain_helper());
    }
    if arguments.len() < 6
        || arguments[0] != "serve"
        || arguments[1] != "--stdio"
        || arguments[2] != "--fixture-root"
        || arguments[4] != "--config-dir"
        || !(arguments.len() - 6).is_multiple_of(2)
    {
        usage();
    }
    let fixture_root = PathBuf::from(&arguments[3]);
    if !fixture_root.is_dir() {
        usage();
    }
    let mut policy = net::Policy::default();
    let mut cdp_port = None;
    let mut ready_file = None;
    let mut profile_root: Option<PathBuf> = None;
    for pair in arguments[6..].chunks_exact(2) {
        match pair[0].as_str() {
            "--allow-origin" => match net::AllowedOrigin::parse(&pair[1]) {
                Ok(origin) => policy.allowed_origins.push(origin),
                Err(message) => {
                    eprintln!("--allow-origin: {message}");
                    std::process::exit(64);
                }
            },
            "--cdp-port" if cdp_port.is_none() => {
                cdp_port = Some(pair[1].parse::<u16>().unwrap_or_else(|_| usage()));
            }
            "--ready-file" if ready_file.is_none() => ready_file = Some(PathBuf::from(&pair[1])),
            "--profile-root" if profile_root.is_none() => {
                profile_root = Some(PathBuf::from(&pair[1]))
            }
            _ => usage(),
        }
    }
    if cdp_port.is_some() != ready_file.is_some() {
        usage();
    }
    let realm_zone = std::env::var("MINICON_SURF_NATIVE_REALM_ZONE").is_ok_and(|v| v == "1");
    let realm_arena = std::env::var("MINICON_SURF_NATIVE_REALM_ARENA").is_ok_and(|v| v == "1");
    let realm_allocation = match (realm_zone, realm_arena) {
        (false, false) => RealmAllocation::System,
        (true, false) => RealmAllocation::Zone,
        (false, true) => RealmAllocation::Arena,
        (true, true) => {
            eprintln!(
                "MINICON_SURF_NATIVE_REALM_ZONE and MINICON_SURF_NATIVE_REALM_ARENA exclude each other"
            );
            std::process::exit(64);
        }
    };
    let mut host = Host {
        fixture_root,
        policy,
        realm_allocation,
        profiles: BTreeMap::new(),
        sessions: BTreeMap::new(),
        targets: BTreeMap::new(),
        next_profile: 0,
        next_session: 0,
        next_target: 0,
        next_frame: 0,
        next_realm: 0,
        realms_retired_total: 0,
        navigations_total: 0,
        adapters: BTreeMap::new(),
        next_adapter: 0,
        adapters_detached_total: 0,
        next_bridge_request: 0,
        profile_root: None,
        key_source: None,
        unavailable_profiles: BTreeMap::new(),
        store_writes_total: 0,
        store_bytes_written_total: 0,
        cookie_rejections_total: 0,
    };
    if let Some(root) = profile_root {
        host.enable_profile_store(root, PathBuf::from(&arguments[5]))?;
    }
    // The optional loopback CDP edge reaches this same host through a
    // channel; its requests are executed here, at operation boundaries,
    // against the same targets the stdio door uses.
    let (bridge_sender, bridge_receiver) = std::sync::mpsc::channel::<cdp::BridgeRequest>();
    let _cdp_server = if let (Some(port), Some(ready_file)) = (cdp_port, ready_file) {
        let server = cdp::Server::start(port, bridge_sender)?;
        let receipt = json!({
            "cdp_port":server.port(),
            "browser_websocket_url":server.browser_websocket_url(),
        });
        std::fs::write(ready_file, serde_json::to_vec(&receipt)?)?;
        Some(server)
    } else {
        drop(bridge_sender);
        None
    };
    let (line_sender, line_receiver) = std::sync::mpsc::channel::<Line>();
    std::thread::spawn(move || {
        let stdin = io::stdin();
        let mut reader = stdin.lock();
        loop {
            match read_bounded_line(&mut reader) {
                Ok(Line::Eof) | Err(_) => {
                    let _ = line_sender.send(Line::Eof);
                    return;
                }
                Ok(line) => {
                    if line_sender.send(line).is_err() {
                        return;
                    }
                }
            }
        }
    });
    let stdout = io::stdout();
    let mut out = stdout.lock();
    loop {
        while let Ok(bridge) = bridge_receiver.try_recv() {
            let outcome = host.execute_bridge(&bridge.operation, bridge.arguments);
            let _ = bridge.reply.send(outcome);
        }
        let line = match line_receiver.try_recv() {
            Ok(line) => line,
            Err(std::sync::mpsc::TryRecvError::Empty) => {
                std::thread::sleep(Duration::from_millis(1));
                continue;
            }
            Err(std::sync::mpsc::TryRecvError::Disconnected) => break,
        };
        let response = match line {
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
    // Stop answering the edge before the server thread is joined, so a
    // connection still cleaning up gets an immediate error instead of
    // waiting on a loop that no longer runs.
    drop(bridge_receiver);
    Ok(())
}

#[cfg(all(test, target_os = "macos"))]
mod zone_tests {
    use super::*;
    use std::sync::atomic::{AtomicUsize, Ordering};
    use std::sync::{Arc, Mutex};

    /// The leak counters are process-global, so zone tests run one at a time.
    static SERIAL: Mutex<()> = Mutex::new(());

    fn allocator(limit: usize) -> (ZoneAllocator, Zone, Arc<AtomicUsize>) {
        let zone = Zone::create().unwrap();
        let used = Arc::new(AtomicUsize::new(0));
        (
            ZoneAllocator {
                zone: zone.0,
                limit,
                used: used.clone(),
            },
            zone,
            used,
        )
    }

    #[test]
    fn zone_allocator_accounts_and_enforces_the_limit() {
        let _guard = SERIAL.lock().unwrap_or_else(|e| e.into_inner());
        let (mut alloc, zone, used) = allocator(64 * 1024);
        let small = alloc.alloc(1000);
        assert!(!small.is_null());
        assert!(used.load(Ordering::Relaxed) >= 1000);
        let zero = alloc.alloc(0);
        assert!(!zero.is_null(), "zero-size allocations must be non-null");
        let too_big = alloc.alloc(128 * 1024);
        assert!(too_big.is_null(), "allocations over the limit must fail");
        let overflow = alloc.calloc(usize::MAX, 2);
        assert!(overflow.is_null(), "overflowing calloc must fail");
        let counted_before_failure = used.load(Ordering::Relaxed);
        let too_much_growth = unsafe { alloc.realloc(small, 128 * 1024) };
        assert!(
            too_much_growth.is_null(),
            "growing past the limit must fail"
        );
        assert_eq!(
            used.load(Ordering::Relaxed),
            counted_before_failure,
            "a failed realloc leaves the count untouched"
        );
        assert!(
            unsafe { malloc_size(small.cast()) } >= 1000,
            "and the old block stays valid"
        );
        let grown = unsafe { alloc.realloc(small, 4000) };
        assert!(!grown.is_null());
        assert!(used.load(Ordering::Relaxed) >= 4000);
        let shrunk = unsafe { alloc.realloc(grown, 100) };
        assert!(!shrunk.is_null());
        assert!(
            used.load(Ordering::Relaxed) < 4000,
            "shrinking is accounted by actual sizes"
        );
        let from_null = unsafe { alloc.realloc(std::ptr::null_mut(), 16) };
        assert!(!from_null.is_null());
        unsafe {
            alloc.dealloc(std::ptr::null_mut());
            alloc.dealloc(shrunk);
            alloc.dealloc(zero);
            alloc.dealloc(from_null);
        }
        assert_eq!(
            unsafe { ZoneAllocator::usable_size(std::ptr::null_mut()) },
            0
        );
        assert_eq!(
            used.load(Ordering::Relaxed),
            0,
            "every charged byte is released on dealloc"
        );
        assert_eq!(
            zone.blocks_in_use(),
            0,
            "the zone holds no blocks after frees"
        );
    }

    #[test]
    fn zone_allocator_reports_out_of_memory_as_null_without_charging() {
        let _guard = SERIAL.lock().unwrap_or_else(|e| e.into_inner());
        let (mut alloc, zone, used) = allocator(4096);
        let first = alloc.alloc(3000);
        assert!(!first.is_null());
        let denied = alloc.alloc(3000);
        assert!(denied.is_null(), "the second block would exceed the limit");
        let denied_zeroed = alloc.calloc(1, 3000);
        assert!(denied_zeroed.is_null());
        let counted = used.load(Ordering::Relaxed);
        assert!(
            (3000..=4096).contains(&counted),
            "only the served block is counted"
        );
        unsafe { alloc.dealloc(first) };
        assert_eq!(used.load(Ordering::Relaxed), 0);
        assert_eq!(zone.blocks_in_use(), 0);
    }

    /// The usable size libmalloc serves for `request` bytes in a scratch zone.
    fn served_size(request: usize) -> usize {
        let scratch = Zone::create().unwrap();
        let block = unsafe { malloc_zone_malloc(scratch.0, request) };
        let size = unsafe { malloc_size(block) };
        unsafe { malloc_zone_free(scratch.0, block) };
        size
    }

    #[test]
    fn zone_allocator_charges_the_served_size_not_the_requested_size() {
        let _guard = SERIAL.lock().unwrap_or_else(|e| e.into_inner());
        let served = served_size(17);
        assert!(served > 17, "libmalloc rounds a 17-byte request up");
        // The request passes the pre-check but the served block does not fit.
        let (mut alloc, zone, used) = allocator(served - 1);
        let denied = alloc.alloc(17);
        assert!(denied.is_null(), "a served block over the limit is refused");
        assert_eq!(used.load(Ordering::Relaxed), 0);
        assert_eq!(zone.blocks_in_use(), 0, "the refused block was given back");
        let denied_zeroed = alloc.calloc(17, 1);
        assert!(denied_zeroed.is_null());
        assert_eq!(zone.blocks_in_use(), 0);
    }

    #[test]
    fn zone_allocator_realloc_keeps_the_old_block_on_every_failure() {
        let _guard = SERIAL.lock().unwrap_or_else(|e| e.into_inner());
        let served_new = served_size(40);
        assert!(served_new > 40, "libmalloc rounds a 40-byte request up");
        let (mut probe, _probe_zone, _) = allocator(0);
        let old_served = unsafe { malloc_size(probe.alloc(16).cast()) };
        // Pre-check passes (growth of 24 fits) but the served replacement
        // does not: the old block must survive untouched and counted.
        let (mut alloc, zone, used) = allocator(old_served + served_new - 1);
        let old = alloc.alloc(16);
        assert!(!old.is_null());
        unsafe { std::ptr::write_bytes(old, 0xa5, 16) };
        let counted = used.load(Ordering::Relaxed);
        assert_eq!(counted, old_served);
        let failed = unsafe { alloc.realloc(old, 40) };
        assert!(
            failed.is_null(),
            "growth whose served size exceeds the limit fails"
        );
        assert_eq!(
            used.load(Ordering::Relaxed),
            counted,
            "the count is unchanged"
        );
        assert_eq!(zone.blocks_in_use(), 1, "only the old block is live");
        let bytes = unsafe { std::slice::from_raw_parts(old, 16) };
        assert!(
            bytes.iter().all(|b| *b == 0xa5),
            "the old block is still readable"
        );
        unsafe { std::ptr::write_bytes(old, 0x5a, 16) };
        assert!(
            unsafe { std::slice::from_raw_parts(old, 16) }
                .iter()
                .all(|b| *b == 0x5a),
            "the old block is still writable"
        );
        // A growth that fits copies the bytes and releases the old block.
        let (mut roomy, roomy_zone, roomy_used) = allocator(0);
        let first = roomy.alloc(16);
        unsafe { std::ptr::write_bytes(first, 0x3c, 16) };
        let grown = unsafe { roomy.realloc(first, 4000) };
        assert!(!grown.is_null());
        assert!(
            unsafe { std::slice::from_raw_parts(grown, 16) }
                .iter()
                .all(|b| *b == 0x3c),
            "the bytes moved to the replacement"
        );
        assert_eq!(roomy_zone.blocks_in_use(), 1, "the old block was freed");
        assert_eq!(roomy_used.load(Ordering::Relaxed), unsafe {
            malloc_size(grown.cast())
        });
        // Zero-size reallocation yields a minimal block and frees the old one.
        let minimal = unsafe { roomy.realloc(grown, 0) };
        assert!(!minimal.is_null());
        assert_eq!(roomy_zone.blocks_in_use(), 1);
        assert_eq!(roomy_used.load(Ordering::Relaxed), unsafe {
            malloc_size(minimal.cast())
        });
        let from_null_zero = unsafe { roomy.realloc(std::ptr::null_mut(), 0) };
        assert!(!from_null_zero.is_null());
        unsafe {
            roomy.dealloc(minimal);
            roomy.dealloc(from_null_zero);
            alloc.dealloc(old);
        }
        assert_eq!(roomy_used.load(Ordering::Relaxed), 0);
        assert_eq!(used.load(Ordering::Relaxed), 0);
        assert_eq!(roomy_zone.blocks_in_use(), 0);
        assert_eq!(zone.blocks_in_use(), 0);
    }

    #[test]
    fn realm_frees_every_block_before_its_zone_is_destroyed() {
        let _guard = SERIAL.lock().unwrap_or_else(|e| e.into_inner());
        let before = ZONE_BLOCKS_LEAKED.load(Ordering::Relaxed);
        let destroyed = ZONES_DESTROYED.load(Ordering::Relaxed);
        let realm = Realm::new(RealmAllocation::Zone).unwrap();
        let deadline = Instant::now() + Duration::from_secs(5);
        realm.eval(DOM_SHIM_JS, deadline, "target_test").unwrap();
        realm
            .eval(
                "__mcsSeed([{e:'html',a:{},c:[{e:'body',a:{},c:[{e:'h1',a:{},c:[{x:'x'}]}]}]}]); \
                 const p = []; for (let i = 0; i < 2000; i++) p.push({i, s: 'value' + i}); String(p.length)",
                deadline,
                "target_test",
            )
            .unwrap();
        assert!(
            realm.malloc_bytes() > 100_000,
            "the zone accounting sees the realm's heap"
        );
        let over = realm.eval(
            "const big = []; while (true) big.push(new Array(4096).fill(1));",
            deadline,
            "target_test",
        );
        assert!(
            over.is_err(),
            "exceeding the realm limit must fail inside the zone"
        );
        assert!(
            realm.malloc_bytes() <= REALM_MEMORY_LIMIT,
            "the count never exceeds the limit"
        );
        drop(realm);
        assert_eq!(ZONES_DESTROYED.load(Ordering::Relaxed), destroyed + 1);
        assert_eq!(
            ZONE_BLOCKS_LEAKED.load(Ordering::Relaxed),
            before,
            "no block may remain in use when the zone is destroyed"
        );
    }
}

#[cfg(all(test, target_os = "macos"))]
mod arena_realm_tests {
    use super::*;
    use std::sync::atomic::Ordering;

    fn grow_until_throw(realm: &Realm) {
        let deadline = Instant::now() + Duration::from_secs(20);
        realm.eval(DOM_SHIM_JS, deadline, "target_test").unwrap();
        let over = realm.eval(
            "globalThis.big = []; while (true) big.push(new Array(4096).fill(1));",
            deadline,
            "target_test",
        );
        assert!(over.is_err(), "exceeding the realm limit must fail");
    }

    #[test]
    fn quickjs_enforces_its_limit_under_a_custom_allocator() {
        // The arena carries no byte limit of its own: the 16 MiB cap must
        // come from quickjs-ng's malloc wrappers, which check malloc_limit
        // before calling any allocator, so the arena (twice the cap) is never
        // the binding constraint.
        let realm = Realm::new(RealmAllocation::Arena).unwrap();
        grow_until_throw(&realm);
        let counted = realm.runtime.memory_usage().malloc_size.max(0) as usize;
        assert!(
            counted <= REALM_MEMORY_LIMIT,
            "QuickJS's count stays under the cap"
        );
        assert!(
            counted > REALM_MEMORY_LIMIT / 2,
            "and the realm really filled up"
        );
        let stats = realm.arena.as_ref().unwrap().statistics();
        assert!(
            stats.used <= REALM_MEMORY_LIMIT + 1024 * 1024,
            "the arena served about the cap"
        );
        assert!(
            stats.high_water < REALM_ARENA_BYTES,
            "and never needed the whole reservation"
        );
    }

    #[test]
    fn realm_frees_every_block_before_its_arena_is_unmapped() {
        let before = arena::ARENA_BLOCKS_LEAKED.load(Ordering::Relaxed);
        let realm = Realm::new(RealmAllocation::Arena).unwrap();
        let deadline = Instant::now() + Duration::from_secs(5);
        realm.eval(DOM_SHIM_JS, deadline, "target_test").unwrap();
        realm
            .eval(
                "__mcsSeed([{e:'html',a:{},c:[{e:'body',a:{},c:[{e:'h1',a:{},c:[{x:'x'}]}]}]}]); \
                 const p = []; for (let i = 0; i < 2000; i++) p.push({i, s: 'value' + i}); String(p.length)",
                deadline,
                "target_test",
            )
            .unwrap();
        assert!(
            realm.malloc_bytes() > 100_000,
            "QuickJS accounting sees the heap"
        );
        let arena = realm.arena.clone().unwrap();
        assert!(arena.statistics().blocks > 1000);
        drop(realm);
        assert_eq!(
            arena.statistics().blocks,
            0,
            "JS_FreeRuntime returned every block before the realm handle went away"
        );
        assert_eq!(
            std::rc::Rc::strong_count(&arena),
            1,
            "the runtime's allocator released its hold, so the runtime is gone"
        );
        drop(arena);
        assert_eq!(
            arena::ARENA_BLOCKS_LEAKED.load(Ordering::Relaxed),
            before,
            "nothing leaked"
        );
    }

    #[test]
    fn trim_on_a_live_arena_realm_reports_its_free_tail() {
        let realm = Realm::new(RealmAllocation::Arena).unwrap();
        grow_until_throw(&realm);
        let deadline = Instant::now() + Duration::from_secs(5);
        realm
            .eval("globalThis.big = null;", deadline, "target_test")
            .unwrap();
        realm.runtime.run_gc();
        let stats = realm.arena.as_ref().unwrap().statistics();
        assert!(
            stats.used < REALM_MEMORY_LIMIT / 4,
            "the heap emptied after the collection"
        );
        let released = realm.trim_arena();
        assert!(
            released > 4 * 1024 * 1024,
            "the free tail is returned page by page"
        );
        realm.eval("const again = []; for (let i = 0; i < 20000; i++) again.push({i}); String(again.length)", deadline, "target_test").unwrap();
    }
}
