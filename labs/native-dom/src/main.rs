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
mod frame_region;
mod net;
mod profile;
mod surface;

const PROTOCOL: &str = "minicon-surf.control";
const VERSION: &str = "0.0.1";
/// 0.0.2 adds the navigation slice and nothing else. Both versions are served
/// side by side: a request names its version exactly and that version decides
/// which operations it may use. Nothing is inferred from a request's shape.
const VERSION_NEXT: &str = "0.0.2";
const NAVIGATION_OPERATIONS: &[&str] = &["target.navigate", "target.reload", "target.traverse"];
/// The history window: eight bounded entries, each a committed URL.
const MAX_HISTORY_ENTRIES: usize = 8;
/// The audit ledger `session.inspect` reports: the newest 64 records of the
/// navigation operations. A record is evidence that an operation happened; it
/// is never an authorization, and this host implements no capability
/// attenuation.
const MAX_AUDIT_ENTRIES: usize = 64;
/// A form value's byte bound, and the highest option index an action may name.
const MAX_FORM_VALUE_BYTES: usize = 1024;
const MAX_OPTION_INDEX: u64 = 63;
const MAX_URL_BYTES: usize = 2000;
const MAX_REQUEST_BYTES: usize = 65_536;
const MAX_RESPONSE_BYTES: usize = 4_194_304;
const MAX_DEADLINE_MS: u64 = 120_000;
const MAX_TARGETS: usize = 8;
const MAX_PROFILES: usize = 8;
const MAX_SESSIONS: usize = 8;
const MAX_SNAPSHOT_NODES: u64 = 128;
/// Frames per target, main frame included: the synthetic host's bound.
const MAX_FRAMES_PER_TARGET: usize = 8;
/// Node ids are target-scoped. Each frame's ids come from a disjoint band of
/// this width, so one id never means two nodes within one target revision and
/// a reference taken in a child frame cannot resolve against the main one.
const NODE_BAND: u64 = MAX_SNAPSHOT_NODES;
/// A frame's counter lives in a JavaScript realm as a Number, so it stops
/// representing exact increments here, long before `u64` matters.
const MAX_SAFE_COUNTER: u64 = (1u64 << 53) - 1;

/// The one refusal both limits produce, before anything is dispatched, built
/// or fetched.
fn saturated(id: &str) -> ControlError {
    ControlError::new(
        "resource_limit",
        "the target's revision cannot advance any further",
        false,
    )
    .scoped("target", id)
    .details(json!({"reason":"revision_saturated"}))
}
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
/// The painter's rows: (node id, role, name).
type SemanticRows = Vec<(String, String, String)>;
/// A host-side scroll advances the revision like any other page mutation.
const SCROLL_REVISION_JS: &str = "(() => { if (!window.__mcs) { return '-1'; } window.__mcs.revision += 1; return String(window.__mcs.revision); })()";

/// The realm-side location seed for a document, used by the main frame and by
/// every child frame so an embedded document reports its own address.
fn location_script(url: &Url) -> String {
    format!(
        "__mcsLocation({})",
        json!({
            "href": url.as_str(),
            "origin": url.origin().ascii_serialization(),
            "protocol": format!("{}:", url.scheme()),
            "host": url.host_str().map(|h| match url.port() {
                Some(p) => format!("{h}:{p}"),
                None => h.to_owned(),
            }).unwrap_or_default(),
            "hostname": url.host_str().unwrap_or_default(),
            "port": url.port().map(|p| p.to_string()).unwrap_or_default(),
            "pathname": url.path(),
            "search": url.query().map(|q| format!("?{q}")).unwrap_or_default(),
            "hash": url.fragment().map(|f| format!("#{f}")).unwrap_or_default(),
        })
    )
}

/// The form serialiser and the preflight both phases share, so the URL the
/// host approves and the URL the activation builds are produced by the same
/// code.
const SERIALIZE_JS: &str = r##"
  const enc = (v) => encodeURIComponent(String(v)).replace(/%20/g, "+")
    .replace(/[!'()*]/g, (c) => "%" + c.charCodeAt(0).toString(16).toUpperCase());
  const serialize = (form, submitter) => {
    const pairs = [];
    for (const c of form.elements.slice(0, 64)) {
      const name = c.getAttribute("name");
      if (!name || c.disabled) continue;
      const ct = c.tagName.toLowerCase();
      const ctype = (c.type || "").toLowerCase();
      if (ct === "input" && /^(password|file)$/.test(ctype)) continue;
      if (ct === "input" && /^(checkbox|radio)$/.test(ctype)) {
        if (c.checked) pairs.push([name, c.getAttribute("value") ?? "on"]);
      } else if (ct === "select") {
        const o = c.options[c.selectedIndex];
        if (o) pairs.push([name, o.getAttribute("value") ?? (o.textContent || "").trim()]);
      } else if (ct === "textarea" || (ct === "input" && /^(text|search|url|tel|email|number|hidden)$/.test(ctype))) {
        pairs.push([name, c.value ?? ""]);
      } else if (submitter && c === submitter) {
        pairs.push([name, c.getAttribute("value") ?? ""]);
      }
    }
    return pairs.map(([k, v]) => enc(k) + "=" + enc(v)).join("&");
  };
  // The complete effective activation as one comparable value. The host
  // carries it between the phases and the activation phase re-derives it, so a
  // job that ran in between cannot change what was approved.
  const __mcsNavigation = (el, action) => {
    const tag = el.tagName.toLowerCase();
    const kind = (el.type || "").toLowerCase();
    const submits = (tag === "button" && (kind === "submit" || kind === "")) || (tag === "input" && kind === "submit");
    if (tag === "a" && el.hasAttribute("href")) {
      return { shape: "link", href: urlOf(el.getAttribute("href")), method: "get" };
    }
    let form = null;
    let submitter = null;
    if (tag === "form" && action.kind === "submit") { form = el; }
    else if (submits) { form = el.form; submitter = el; }
    else if (tag === "input" && action.kind === "press" && action.key === "enter") { form = el.form; }
    if (!form) return { shape: "plain", href: null, method: null };
    return __mcsFormNavigation(form, submitter);
  };
  const __mcsFormNavigation = (form, submitter) => {
    const query = serialize(form, submitter);
    const declared = actionOf(form, submitter);
    return {
      shape: "form",
      method: methodOf(form, submitter),
      href: declared + (query ? "?" + query : ""),
    };
  };
  const __mcsPreflight = (el, action) => {
    const decision = el.disabled ? "control_disabled" : activationOf(el);
    const navigation = __mcsNavigation(el, action);
    return {
      decision,
      href: navigation.href,
      shape: navigation.shape,
      signature: [navigation.shape, decision, navigation.method || "", navigation.href || ""].join(" "),
    };
  };
"##;

/// The one activation decision, shared by the snapshot that predicts it and
/// by the scripts that enforce it. `IS_CHILD` and `HAS_BASE_TARGET` are
/// substituted by the host, which knows both and the realm does not.
const ACTIVATION_JS: &str = r##"
  const targetOf = (el, attr) => {
    // HTML consults a base target only when the element has no target
    // attribute at all. A present one, even empty or whitespace, is the
    // element's own answer and means the current frame.
    if (!el.hasAttribute(attr)) return HAS_BASE_TARGET ? "base_target_unmodeled" : "allowed";
    const value = String(el.getAttribute(attr) || "").trim().toLowerCase();
    if (value === "" || value === "_self") return "allowed";
    if (value === "_parent" || value === "_top") return IS_CHILD ? "target_cross_frame" : "allowed";
    return "target_named";
  };
  // HTML strips leading and trailing ASCII whitespace before it parses a URL,
  // so every judgement here is made on the stripped value.
  const urlOf = (raw) => String(raw == null ? "" : raw).replace(/^[ \t\n\r\f]+|[ \t\n\r\f]+$/g, "");
  const schemeDecision = (raw) => {
    const value = urlOf(raw);
    if (value.startsWith("#")) return "fragment_unsupported";
    const scheme = /^([a-zA-Z][a-zA-Z0-9+.-]*):/.exec(value);
    if (scheme && !/^https?$/i.test(scheme[1])) return "scheme_unsupported";
    return "allowed";
  };
  const methodOf = (form, submitter) => {
    const raw = submitter && submitter.hasAttribute("formmethod")
      ? submitter.getAttribute("formmethod")
      : form.getAttribute("method");
    return String(raw || "get").trim().toLowerCase();
  };
  const actionOf = (form, submitter) => {
    const declared = submitter && submitter.hasAttribute("formaction")
      ? submitter.getAttribute("formaction")
      : form.getAttribute("action");
    return urlOf(declared);
  };
  const submitDecision = (form, submitter) => {
    if (!form) return "allowed";
    if (methodOf(form, submitter) !== "get") return "form_method_unsupported";
    // The effective action is judged before any event, not after the submit
    // has already been dispatched.
    const decision = schemeDecision(actionOf(form, submitter));
    if (decision !== "allowed") return decision;
    if (submitter && submitter.hasAttribute("formtarget")) return targetOf(submitter, "formtarget");
    return targetOf(form, "target");
  };
  const linkDecision = (el) => {
    if (el.hasAttribute("download")) return "download_unsupported";
    const decision = schemeDecision(el.getAttribute("href"));
    if (decision !== "allowed") return decision;
    return targetOf(el, "target");
  };
  const activationOf = (el) => {
    const tag = el.tagName.toLowerCase();
    const kind = (el.type || "").toLowerCase();
    if (el.disabled) return "control_disabled";
    if (tag === "a" && el.hasAttribute("href")) return linkDecision(el);
    if (tag === "form") return submitDecision(el, null);
    const submits = (tag === "button" && (kind === "submit" || kind === "")) || (tag === "input" && kind === "submit");
    if (submits) return submitDecision(el.form, el);
    return "allowed";
  };
"##;

fn activation_js(is_child: bool, has_base_target: bool) -> String {
    ACTIVATION_JS
        .replace("IS_CHILD", if is_child { "true" } else { "false" })
        .replace(
            "HAS_BASE_TARGET",
            if has_base_target { "true" } else { "false" },
        )
}

fn snapshot_script(max_nodes: u64, is_child: bool, has_base_target: bool) -> String {
    let activation = activation_js(is_child, has_base_target);
    format!(
        r#"(() => {{
  const s = window.__mcs;
  if (!s) return JSON.stringify({{ error: "uninstrumented" }});
{activation}
  const role = (el) => {{
    const t = el.tagName.toLowerCase();
    const type = (el.type || "").toLowerCase();
    if (/^h[1-6]$/.test(t)) return "heading";
    if (t === "button" || (t === "input" && /^(button|submit|reset)$/.test(type))) return "button";
    if (t === "a" && el.hasAttribute("href")) return "link";
    if (t === "form") return "form";
    if (t === "select") return "select";
    if (t === "input" && type === "checkbox") return "checkbox";
    if (t === "input" && type === "radio") return "radio";
    // Credential and file sources are excluded by design, and so are hidden
    // controls: they are serialised by the realm but never offered as nodes.
    if (t === "textarea") return "textbox";
    if (t === "input" && /^(text|search|url|tel|email|number)$/.test(type)) return "textbox";
    if (t === "label") return "label";
    if (t === "p" || t === "li") return "text";
    return null;
  }};
  const all = document.body ? document.body.querySelectorAll("*") : [];
  const chosen = [];
  let truncated = false;
  for (const el of all) {{
    const r = role(el);
    if (!r) continue;
    if (chosen.length >= {max_nodes}) {{ truncated = true; break; }}
    chosen.push([el, r]);
  }}
  const place = new Map();
  chosen.forEach(([el], index) => place.set(el, index));
  const out = [];
  const nodes = [];
  for (const [el, r] of chosen) {{
    const entry = {{ node: "node_" + (nodes.length + 1), role: r }};
    let name = (el.textContent || "").trim();
    if (r === "textbox") {{
      const label = el.id ? document.querySelector('label[for="' + el.id + '"]') : null;
      name = (label ? label.textContent : (el.getAttribute("aria-label") || el.getAttribute("name") || "")).trim();
      entry.value = String(el.value || "").slice(0, 256);
    }}
    if (r === "checkbox" || r === "radio") {{
      const label = el.id ? document.querySelector('label[for="' + el.id + '"]') : null;
      name = (label ? label.textContent : (el.getAttribute("aria-label") || el.getAttribute("name") || "")).trim();
      entry.checked = !!el.checked;
      if (r === "radio") entry.group = String(el.getAttribute("name") || "").slice(0, 64);
    }}
    if (r === "select") {{
      name = (el.getAttribute("aria-label") || el.getAttribute("name") || "").trim();
      const options = el.options.slice(0, 64);
      entry.options = options.map((o, index) => ({{
        index, label: String(o.label || "").trim().slice(0, 256),
        selected: index === el.selectedIndex, disabled: !!o.disabled,
      }}));
      entry.selected = el.selectedIndex;
    }}
    if (r === "form") {{
      name = (el.getAttribute("aria-label") || el.getAttribute("name") || el.getAttribute("id") || "").trim();
      entry.method = String(el.getAttribute("method") || "get").toLowerCase();
      entry.has_action = el.hasAttribute("action");
      entry.controls = el.elements.slice(0, 64)
        .filter((c) => place.has(c)).map((c) => "node_" + (place.get(c) + 1));
    }}
    if (r === "textbox" || r === "checkbox" || r === "radio" || r === "select" || r === "button") {{
      entry.disabled = !!el.disabled;
      entry.read_only = !!el.readOnly;
      const control = String(el.getAttribute("name") || "");
      if (control) entry.control_name = control.slice(0, 64);
    }}
    // What an activation of this node would decide, over a closed
    // vocabulary, so an agent can see a refusal coming without reading the
    // target, the href or any other page text.
    if (r === "link" || r === "button" || r === "form") entry.activation = activationOf(el);
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

/// Lab-only (court arm): a snapshot-shaped result of a fixed size in two
/// shapes, flat (one padding string) and nested (object-heavy entries),
/// made equal in bytes inside the realm. Not a browser result.
fn microbench_script(nested: bool) -> String {
    format!(
        r#"(() => {{
  const s = window.__mcs;
  if (!s) return JSON.stringify({{ error: "uninstrumented" }});
  const target = 16384;
  const entries = [];
  let nestedText = "";
  while (true) {{
    const i = entries.length + 1;
    entries.push({{ node: "node_" + i, role: i % 3 === 0 ? "link" : (i % 3 === 1 ? "text" : "button"), name: "entry " + i + " " + "n".repeat(180), dom_id: "id_" + i }});
    nestedText = JSON.stringify({{ revision: s.revision, truncated: false, nodes: entries }});
    if (nestedText.length >= target) break;
  }}
  if ({nested}) return nestedText;
  const base = JSON.stringify({{ revision: s.revision, truncated: false, nodes: [], pad: "" }});
  return JSON.stringify({{ revision: s.revision, truncated: false, nodes: [], pad: "p".repeat(nestedText.length - base.length) }});
}})()"#
    )
}

/// The five 0.0.2 actions, applied inside the realm, which is the only
/// authority over form state. The host passes the action as JSON and receives
/// a bounded outcome: applied, a navigation to perform, or a typed refusal.
/// A value never comes back.
/// Phase one of every activating action: the effective method, target and
/// action, the URL the activation would navigate to, and a signature of all of
/// it. Nothing is dispatched, nothing is written, no counter moves.
fn preflight_script(
    revision: u64,
    index: usize,
    action: &str,
    is_child: bool,
    has_base_target: bool,
) -> String {
    let activation = activation_js(is_child, has_base_target);
    let serializer = SERIALIZE_JS;
    format!(
        r#"(() => {{
  const s = window.__mcs;
  if (!s) return JSON.stringify({{ error: "uninstrumented" }});
  if (s.revision !== {revision}) return JSON.stringify({{ stale: true, current: s.revision }});
  if (s.snapshot !== {revision}) return JSON.stringify({{ missing: true }});
  const el = s.nodes[{index}];
  if (!el || !el.isConnected) return JSON.stringify({{ missing: true }});
  const action = {action};
{activation}
{serializer}
  return JSON.stringify(__mcsPreflight(el, action));
}})()"#
    )
}

fn form_action_script(
    revision: u64,
    index: usize,
    action: &str,
    is_child: bool,
    has_base_target: bool,
    signature: &str,
) -> String {
    let activation = activation_js(is_child, has_base_target);
    let serializer = SERIALIZE_JS;
    let expected = serde_json::to_string(signature).expect("signature serializes");
    format!(
        r#"(() => {{
  const s = window.__mcs;
  if (!s) return JSON.stringify({{ error: "uninstrumented" }});
  if (s.revision !== {revision}) return JSON.stringify({{ stale: true, current: s.revision }});
  if (s.snapshot !== {revision}) return JSON.stringify({{ missing: true }});
  const el = s.nodes[{index}];
  if (!el || !el.isConnected) return JSON.stringify({{ missing: true }});
  const action = {action};
  // A successful action changes observable state, so the revision advances
  // once. Writing a value is not a DOM mutation, so the observer would not
  // notice it; if the page's own handlers already advanced it, that stands.
  // A refusal never advances it, and neither does a canceled default on its
  // own: only what the handlers really changed does.
  const startRevision = s.revision;
  const settle = (outcome) => {{
    if (outcome.applied && s.revision === startRevision) s.revision = startRevision + 1;
    return JSON.stringify(outcome);
  }};
  const refuse = (reason) => JSON.stringify({{ unsupported: true, reason }});
  const t = el.tagName.toLowerCase();
  const type = (el.type || "").toLowerCase();
  const isCheck = t === "input" && /^(checkbox|radio)$/.test(type);
  const isLine = t === "input" && /^(text|search|url|tel|email|number)$/.test(type);
  const isText = t === "textarea" || isLine;
  const isButton = t === "button" || (t === "input" && /^(button|submit|reset)$/.test(type));
  const isSubmitter = (t === "button" && (type === "submit" || type === "")) || (t === "input" && type === "submit");
{activation}
{serializer}
  // Every refusal happens here, before anything is written.
  if (el.disabled) return refuse("control_disabled");
  // What the preflight approved, re-derived from the document as it is now.
  // Jobs can run between the two evaluations without moving the revision, so
  // equality of the whole effective activation is what is checked, not the
  // revision alone. The value is compared here and never leaves the realm.
  if (action.kind === "click" || action.kind === "press" || action.kind === "submit") {{
    const decision = activationOf(el);
    if (decision !== "allowed") return refuse(decision);
    if (__mcsPreflight(el, action).signature !== {expected}) return refuse("preflight_mismatch");
  }}
  const fire = (name, cancelable) => {{
    const ev = new Event(name, {{ bubbles: true, cancelable: !!cancelable }});
    el.dispatchEvent(ev);
    return ev.defaultPrevented;
  }};
  const submitForm = (form, submitter) => {{
    const decision = submitDecision(form, submitter);
    if (decision !== "allowed") return refuse(decision);
    const ev = new Event("submit", {{ bubbles: true, cancelable: true }});
    form.dispatchEvent(ev);
    // A canceled submit is not applied: no navigation begins. Whatever the
    // handler changed stays, and the revision it moved stays moved.
    if (ev.defaultPrevented) return JSON.stringify({{ applied: false, default_prevented: true, role: "form" }});
    // The same navigation the preflight approved, built by the same code and
    // from the same submitter.
    const navigation = __mcsFormNavigation(form, submitter);
    // A submit that navigates has exactly one observable consequence, the
    // document that replaces this one, and the navigation counts it. Settling
    // here as well would count the same event twice.
    return JSON.stringify({{ navigate: navigation.href, applied: true, role: "form", current: !actionOf(form, submitter) }});
  }};
  if (action.kind === "set_value") {{
    if (!isText) return refuse("role_mismatch");
    if (el.readOnly) return refuse("control_read_only");
    el.value = action.value;
    fire("input", false);
    fire("change", false);
    // The host counts the bytes from its own validated string; the realm
    // reports no length, because its idea of length is not the contract's.
    return settle({{ applied: true, role: "textbox" }});
  }}
  if (action.kind === "set_checked") {{
    if (!isCheck) return refuse("role_mismatch");
    // The whole group is captured, because setting a radio true clears its
    // sibling and a canceled default must put that back too.
    const before = el.__groupState ? el.__groupState() : [[el, !!el.checked]];
    el.checked = action.checked;
    if (fire("click", true)) {{
      if (el.__restoreGroup) el.__restoreGroup(before); else el.checked = before[0][1];
      return JSON.stringify({{ applied: false, default_prevented: true, role: type }});
    }}
    fire("change", false);
    return settle({{ applied: true, role: type }});
  }}
  if (action.kind === "select_option") {{
    if (t !== "select") return refuse("role_mismatch");
    const options = el.options;
    if (action.index >= options.length) return JSON.stringify({{ absent: true, reason: "option_out_of_range" }});
    if (options[action.index].disabled) return refuse("option_disabled");
    el.selectedIndex = action.index;
    fire("change", false);
    return settle({{ applied: true, role: "select" }});
  }}
  if (action.kind === "submit") {{
    const form = t === "form" ? el : el.form;
    if (!form) return refuse("role_mismatch");
    return submitForm(form, t === "form" ? null : el);
  }}
  if (action.kind === "press") {{
    // The closed activation matrix. Anything outside it is refused before a
    // single event is dispatched and before the revision can move.
    const enter = action.key === "enter";
    const allowed = enter
      ? ((t === "a" && el.hasAttribute("href")) || isButton || isLine)
      : (isButton || isCheck);
    if (!allowed) return refuse("key_role_unsupported");
    if (enter && isLine && !el.form) return refuse("key_role_unsupported");
    const keyRole = isCheck ? type : (isButton ? "button" : (t === "a" ? "link" : "form"));
    // The ruled sequence: a canceled keydown suppresses keypress, keyup is
    // dispatched in every case, and the activation waits for all of it.
    const phase = (name) => {{
      const ev = new Event(name, {{ bubbles: true, cancelable: true }});
      ev.key = enter ? "Enter" : " ";
      el.dispatchEvent(ev);
      return ev.defaultPrevented;
    }};
    let canceled = phase("keydown");
    if (!canceled) canceled = phase("keypress");
    if (phase("keyup")) canceled = true;
    if (canceled) return JSON.stringify({{ applied: false, default_prevented: true, role: keyRole }});
    if (!enter && isCheck) {{
      // A radio is set, never toggled, and the whole group is captured so a
      // canceled default puts back the sibling this would clear.
      const was = !!el.checked;
      const before = el.__groupState ? el.__groupState() : [[el, was]];
      el.checked = type === "radio" ? true : !was;
      if (fire("click", true)) {{
        if (el.__restoreGroup) el.__restoreGroup(before); else el.checked = was;
        return JSON.stringify({{ applied: false, default_prevented: true, role: type }});
      }}
      fire("change", false);
      return settle({{ applied: true, role: type }});
    }}
    if (isButton) {{
      // Every subtype clicks first; only an uncanceled click reaches the
      // submit or the reset behind it.
      if (fire("click", true)) return JSON.stringify({{ applied: false, default_prevented: true, role: "button" }});
      if (isSubmitter && el.form) return submitForm(el.form, el);
      if (type === "reset" && el.form && !el.form.reset()) {{
        return JSON.stringify({{ applied: false, default_prevented: true, role: "button" }});
      }}
      return settle({{ applied: true, role: "button" }});
    }}
    if (enter && t === "a") {{
      if (fire("click", true)) return JSON.stringify({{ applied: false, default_prevented: true, role: "link" }});
      return settle({{ navigate: el.getAttribute("href"), applied: true, role: "link" }});
    }}
    return submitForm(el.form, null);
  }}
  return refuse("action_unsupported");
}})()"#
    )
}

fn act_script(
    revision: u64,
    index: usize,
    is_child: bool,
    has_base_target: bool,
    signature: &str,
) -> String {
    let activation = activation_js(is_child, has_base_target);
    let serializer = SERIALIZE_JS;
    let expected = serde_json::to_string(signature).expect("signature serializes");
    format!(
        r#"(() => {{
  const s = window.__mcs;
  if (!s) return JSON.stringify({{ error: "uninstrumented" }});
  if (s.revision !== {revision}) return JSON.stringify({{ stale: true, current: s.revision }});
  if (s.snapshot !== {revision}) return JSON.stringify({{ missing: true }});
  const el = s.nodes[{index}];
  if (!el || !el.isConnected) return JSON.stringify({{ missing: true }});
{activation}
{serializer}
  const t = el.tagName.toLowerCase();
  const decision = activationOf(el);
  if (decision !== "allowed") return JSON.stringify({{ unsupported: true, reason: decision }});
  if (__mcsPreflight(el, {{ kind: "click" }}).signature !== {expected}) {{
    return JSON.stringify({{ unsupported: true, reason: "preflight_mismatch" }});
  }}
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
    version: String,
    deadline: Duration,
    operation: String,
    arguments: Value,
}

/// Whether an operation exists in the version that named it.
fn operation_available(operation: &str, version: &str) -> bool {
    OPERATIONS.contains(&operation)
        || (version == VERSION_NEXT && NAVIGATION_OPERATIONS.contains(&operation))
}

/// A profile's policy: the network switch and the default permission answer.
/// Both are exactly the values control 0.0.1 reserved for
/// `profile.policy.set`.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
struct ProfilePolicy {
    online: bool,
    allow_by_default: bool,
}

impl Default for ProfilePolicy {
    /// What a profile has before anything is set, and what a record written
    /// before this field existed means when it is read back.
    fn default() -> ProfilePolicy {
        ProfilePolicy {
            online: true,
            allow_by_default: true,
        }
    }
}

impl ProfilePolicy {
    fn network(self) -> &'static str {
        if self.online { "online" } else { "offline" }
    }

    fn permissions(self) -> &'static str {
        if self.allow_by_default {
            "allow_by_default"
        } else {
            "deny_by_default"
        }
    }

    fn to_json(self) -> Value {
        json!({
            "network": self.network(),
            "permissions": self.permissions(),
            // This slice has no API that consumes a permission, so the value
            // is stored, reported and audited and grants nothing.
            "permissions_effect": "recorded_only",
        })
    }
}

/// At most this many distinct origins are shared inside one ledger; a record
/// beyond that owns its own copy, which stays bounded because the ledger is.
const MAX_AUDIT_ORIGINS: usize = 32;

/// One bounded audit record. It names an origin, never a path, a query or
/// userinfo, and carries no value the page or the profile holds. The
/// operation and outcome are fixed vocabularies, so they are static; the
/// target and origin repeat across navigations, so they are shared handles;
/// the session and its profile belong to the ledger, not to the record.
#[derive(Debug, Clone)]
struct AuditEntry {
    sequence: u64,
    deadline_ms: u64,
    /// For a value-carrying action, how many bytes it had. Never the value.
    value_bytes: Option<u64>,
    target: std::rc::Rc<str>,
    /// The frame the action touched, main or child. Never a URL, a name or
    /// any other page text: an opaque id this host minted.
    frame: Option<std::rc::Rc<str>>,
    origin: Option<std::rc::Rc<str>>,
    operation: &'static str,
    outcome: &'static str,
}

impl AuditEntry {
    fn to_json(&self, session: &str, profile: &str) -> Value {
        json!({
            "sequence": self.sequence, "session": session, "profile": profile,
            "target": &*self.target, "frame": self.frame.as_deref(), "operation": self.operation,
            "origin": self.origin.as_deref(), "outcome": self.outcome,
            "deadline_ms": self.deadline_ms, "result_bytes_limit": MAX_RESPONSE_BYTES,
            "value_bytes": self.value_bytes,
        })
    }
}

/// One session's ledger: a ring of at most `MAX_AUDIT_ENTRIES` records
/// reserved once, plus the shared handles its records name.
#[derive(Debug)]
struct Ledger {
    entries: std::collections::VecDeque<AuditEntry>,
    origins: Vec<std::rc::Rc<str>>,
    targets: Vec<std::rc::Rc<str>>,
    /// Frame ids repeat across records like target ids do, and there are at
    /// most `MAX_TARGETS * MAX_FRAMES_PER_TARGET` of them in a session.
    frames: Vec<std::rc::Rc<str>>,
    dropped: u64,
}

/// The default ledger is the reserved one: nothing may create a ring that
/// has to grow on its first records.
impl Default for Ledger {
    fn default() -> Ledger {
        Ledger::new()
    }
}

impl Ledger {
    fn new() -> Ledger {
        Ledger {
            frames: Vec::new(),
            entries: std::collections::VecDeque::with_capacity(MAX_AUDIT_ENTRIES),
            origins: Vec::new(),
            targets: Vec::new(),
            dropped: 0,
        }
    }

    /// A shared handle for a value that repeats, or an owned one when the
    /// table is full. Both are the same type, so a record never knows which.
    fn share(table: &mut Vec<std::rc::Rc<str>>, value: &str, capacity: usize) -> std::rc::Rc<str> {
        if let Some(shared) = table.iter().find(|entry| &***entry == value) {
            return std::rc::Rc::clone(shared);
        }
        let shared: std::rc::Rc<str> = std::rc::Rc::from(value);
        if table.len() < capacity {
            table.push(std::rc::Rc::clone(&shared));
        }
        shared
    }

    /// Append one record, dropping the oldest when the ring is full. The ring
    /// never moves its records and never grows past its reserved capacity.
    fn append(&mut self, entry: AuditEntry) {
        if self.entries.len() == MAX_AUDIT_ENTRIES {
            self.entries.pop_front();
            self.dropped = self.dropped.saturating_add(1);
        }
        self.entries.push_back(entry);
    }

    /// What the ledger costs: the reserved ring plus the shared strings it
    /// holds. Sharing hides nothing here; the capacity is stated.
    fn bytes(&self) -> usize {
        let shared: usize = self
            .origins
            .iter()
            .chain(self.targets.iter())
            .map(|entry| entry.len())
            .sum();
        let owned: usize = self
            .entries
            .iter()
            .map(|entry| {
                let target = if self
                    .targets
                    .iter()
                    .any(|handle| std::rc::Rc::ptr_eq(handle, &entry.target))
                {
                    0
                } else {
                    entry.target.len()
                };
                let origin = entry.origin.as_ref().map_or(0, |origin| {
                    if self
                        .origins
                        .iter()
                        .any(|handle| std::rc::Rc::ptr_eq(handle, origin))
                    {
                        0
                    } else {
                        origin.len()
                    }
                });
                target + origin
            })
            .sum();
        self.capacity_bytes() + shared + owned
    }

    fn capacity_bytes(&self) -> usize {
        self.entries.capacity() * std::mem::size_of::<AuditEntry>()
    }
}

/// The scheme, host and port of a URL and nothing else: no path, no query, no
/// fragment and no userinfo ever reaches the ledger.
fn origin_only(url: &str) -> Option<String> {
    let url = Url::parse(url).ok()?;
    let origin = url.origin();
    origin.is_tuple().then(|| origin.ascii_serialization())
}

/// Saturating lifetime diagnostics for one target, kept beside it so they
/// survive the document swap. They are reported and never gate: what gates is
/// the per-document budget of `net`, the operation's deadline and the policy.
#[derive(Debug, Clone, Copy, Default)]
struct Lifetime {
    retired_fetches: u64,
    retired_bytes: u64,
    navigation_attempts: u64,
    navigation_commits: u64,
    navigation_refusals: u64,
}

impl Lifetime {
    /// Absorb the budget of a document that is being replaced.
    fn retire(&mut self, budget: &net::Budget) {
        self.retired_fetches = self.retired_fetches.saturating_add(budget.fetches as u64);
        self.retired_bytes = self.retired_bytes.saturating_add(budget.bytes as u64);
    }

    fn to_json(self, active: &net::Budget) -> Value {
        json!({
            "fetches_total": self.retired_fetches.saturating_add(active.fetches as u64),
            "bytes_total": self.retired_bytes.saturating_add(active.bytes as u64),
            "navigation_attempts_total": self.navigation_attempts,
            "navigation_commits_total": self.navigation_commits,
            "navigation_refusals_total": self.navigation_refusals,
            "gates": false,
        })
    }
}

/// A target's bounded history: committed URLs and a position, nothing else.
/// No document, realm, body, form, scroll, script, cookie or storage state is
/// kept, so going back refetches rather than restoring a page.
#[derive(Debug, Clone)]
struct History {
    entries: Vec<String>,
    position: usize,
}

impl History {
    fn new(url: &str) -> History {
        History {
            entries: vec![url.to_owned()],
            position: 0,
        }
    }

    /// A navigation commits the final URL: the forward entries are dropped and
    /// the oldest entry is evicted once the window is full.
    fn commit(&mut self, url: &str) {
        self.entries.truncate(self.position + 1);
        self.entries.push(url.to_owned());
        while self.entries.len() > MAX_HISTORY_ENTRIES {
            self.entries.remove(0);
        }
        self.position = self.entries.len() - 1;
    }

    fn at(&self, delta: i64) -> Option<(usize, String)> {
        let position = i64::try_from(self.position).ok()?.checked_add(delta)?;
        let position = usize::try_from(position).ok()?;
        self.entries
            .get(position)
            .map(|url| (position, url.clone()))
    }

    fn bytes(&self) -> usize {
        self.entries.iter().map(String::len).sum()
    }

    fn to_json(&self) -> Value {
        json!({
            "position": self.position,
            "length": self.entries.len(),
            "can_go_back": self.position > 0,
            "can_go_forward": self.position + 1 < self.entries.len(),
        })
    }
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
    let version = object
        .get("version")
        .and_then(Value::as_str)
        .filter(|value| *value == VERSION || *value == VERSION_NEXT)
        .ok_or_else(|| fail("version differs"))?
        .to_owned();
    let deadline_ms = object
        .get("deadline_ms")
        .and_then(Value::as_u64)
        .filter(|ms| (1..=MAX_DEADLINE_MS).contains(ms))
        .ok_or_else(|| fail("deadline_ms is out of range"))?;
    let operation = object
        .get("operation")
        .and_then(Value::as_str)
        .filter(|op| operation_available(op, &version))
        .ok_or_else(|| fail("operation is not part of the control version it names"))?
        .to_owned();
    let arguments = object
        .get("arguments")
        .filter(|a| a.as_object().is_some_and(|o| o.len() <= 64))
        .cloned()
        .ok_or_else(|| fail("arguments must be a bounded object"))?;
    Ok(Request {
        request_id,
        version,
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

fn envelope(request_id: &str, version: &str, body: Result<Value, ControlError>) -> Vec<u8> {
    let response = match body {
        Ok(result) => {
            json!({"protocol":PROTOCOL,"version":version,"request_id":request_id,"ok":true,"result":result})
        }
        Err(error) => {
            json!({"protocol":PROTOCOL,"version":version,"request_id":request_id,"ok":false,"error":error.to_json()})
        }
    };
    let bytes = serde_json::to_vec(&response).expect("response serializes");
    if bytes.len() > MAX_RESPONSE_BYTES {
        return envelope(
            request_id,
            version,
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
        self.eval_staged(script, deadline, target_id, &mut |_, _| {})
    }

    /// `eval` with court-only stage samples: after the realm produced its
    /// value (realm-side allocations done), after the value crossed into a
    /// host `String`, after the realm value was dropped, after the queued
    /// jobs ran. Each sample carries the realm's arena statistics when an
    /// arena serves it.
    fn eval_staged(
        &self,
        script: &str,
        deadline: Instant,
        target_id: &str,
        stage: &mut dyn FnMut(&str, Option<Value>),
    ) -> Result<String, ControlError> {
        self.runtime
            .set_interrupt_handler(Some(Box::new(move || Instant::now() >= deadline)));
        let outcome = self
            .context
            .with(|ctx| match ctx.eval::<rquickjs::Value, _>(script) {
                Ok(value) => {
                    stage("after_realm_eval", self.arena_statistics());
                    if value.is_undefined() || value.is_null() {
                        Ok(String::new())
                    } else {
                        let text: String = ctx
                            .globals()
                            .get::<_, rquickjs::Function>("String")
                            .and_then(|f| f.call((value,)))
                            .unwrap_or_default();
                        stage("after_string_crossing", self.arena_statistics());
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
        stage("after_js_value_drop", self.arena_statistics());
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
        stage("after_jobs_drained", self.arena_statistics());
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
    directory: Option<PathBuf>,
    /// Set after a failed disk commit: no further writes for this host.
    read_only: bool,
    /// Held while a session is open on a persistent profile.
    lock: Option<std::fs::File>,
    /// This profile's TLS client over the host's pinned roots; `None`
    /// without pinned roots.
    tls: Option<std::sync::Arc<net::TlsClient>>,
    /// The network switch and default permission answer of this profile.
    policy: ProfilePolicy,
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
    /// The profile's TLS client (its own bounded session cache); `None`
    /// keeps https `unsupported_capability` for this target.
    tls: Option<std::sync::Arc<net::TlsClient>>,
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

/// A bounded child frame: a static embedded document with its own identity,
/// generation and realm. It runs no scripts (design §16), so its realm holds
/// the shim, the seeded tree, its location and the revision instrumentation
/// and nothing else.
/// What one frame's last observation authorises. A node index is honoured
/// only against the record of the frame whose band it lies in, so one frame's
/// snapshot can never authorise an index in another.
#[derive(Clone, Copy)]
struct FrameSnapshot {
    /// The target-global revision the snapshot reported.
    reference_revision: u64,
    /// That frame's own counter when it was observed.
    frame_revision: u64,
    nodes: usize,
}

struct ChildFrame {
    id: String,
    generation: u64,
    realm_id: String,
    realm: Realm,
    /// This frame's own mutation counter, cached. A child runs no scripts, so
    /// it can move only under a host evaluation in this realm, and the cache
    /// is refreshed exactly there.
    counter: u64,
    snapshot: Option<FrameSnapshot>,
    /// Whether this document carries a `<base target>`. That feature decides
    /// an activation which names no target of its own, and this host does not
    /// model it, so such an activation fails closed instead of being treated
    /// as self.
    base_target: bool,
    /// The final URL of the response that built this frame, after redirects.
    /// It is reported as the optional additive `url` of a `frames[]` entry and
    /// its bytes are owner-accounted like any other document byte.
    url: Option<Url>,
    /// The document's own bytes and elements, so the memory report attributes
    /// an embedded document to its owner.
    bytes: usize,
    element_count: usize,
}

/// Why a frame was not built. A closed set of fixed reasons: never a `src`,
/// a redirect target or any other page text.
const FRAME_SKIP_REASONS: [&str; 13] = [
    "no_network_origin",
    "no_src",
    "srcdoc",
    "sandboxed",
    "malformed_src",
    "scheme_not_fetched",
    "cross_origin_src",
    "cross_origin_redirect",
    "not_html",
    "status_not_ok",
    "fetch_failed",
    "realm_build_failed",
    "frame_limit",
];

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
    /// The main frame's last observation. Children keep their own.
    last_snapshot: Option<FrameSnapshot>,
    /// Whether the main document carries a `<base target>`.
    base_target: bool,
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
    /// Bounded scroll offset owned by the host (the synthetic host's rule);
    /// moved only by surface input in this slice.
    scroll_y: u64,
    /// Bounded child frames in document order, built with this document and
    /// ended with it. At most `MAX_FRAMES_PER_TARGET - 1`.
    children: Vec<ChildFrame>,
    /// One saturating count per fixed reason, in the order of
    /// `FRAME_SKIP_REASONS`. Bounded by construction however many iframes a
    /// document carries, and never a URL.
    frames_skipped: [u64; FRAME_SKIP_REASONS.len()],
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
        self.eval_staged(script, deadline, policy, &mut |_, _| {})
    }

    fn eval_staged(
        &mut self,
        script: &str,
        deadline: Instant,
        policy: &net::Policy,
        stage: &mut dyn FnMut(&str, Option<Value>),
    ) -> Result<String, ControlError> {
        let result = self.realm.eval_staged(script, deadline, &self.id, stage)?;
        self.pump_network(deadline, policy)?;
        self.drain_store_writes(deadline)?;
        stage("after_network_pump", self.realm.arena_statistics());
        Ok(result)
    }

    /// Court-only diagnostic: the realm's collector, never a fix.
    fn run_gc(&self) {
        self.realm.runtime.run_gc();
    }

    /// The document's URL for cookie purposes; fixture targets have none.
    fn document_url(&self) -> Option<Url> {
        self.url.clone()
    }

    /// Seed the realm's cookie and storage mirrors from the working copy.
    /// The main frame first, then the bounded children in document order.
    /// The target-global revision from a main-frame counter, checked. `None`
    /// means it is not representable, which is refused rather than saturated:
    /// a wrong number here would silently stop staleness from discriminating.
    fn global_revision(&self, main_counter: u64) -> Option<u64> {
        self.children.iter().try_fold(
            self.revision_base.checked_add(main_counter)?,
            |total, child| total.checked_add(child.counter),
        )
    }

    /// Live QuickJS bytes of this target's frames, the children included.
    fn realm_malloc_bytes(&self) -> usize {
        self.realm.malloc_bytes()
            + self
                .children
                .iter()
                .map(|child| child.realm.malloc_bytes())
                .sum::<usize>()
    }

    fn frame_count(&self) -> usize {
        self.children.len() + 1
    }

    fn frames_json(&self) -> Value {
        // `url` is optional and additive: it is the final URL of the response
        // that built the frame, and it is absent when a frame has none, which
        // is what a fixture target is.
        let entry =
            |frame: &str, parent: Value, generation: u64, realm: &str, url: Option<&Url>| {
                let mut value = json!({"frame":frame,"parent":parent,
                "generation":generation,"realm":realm});
                if let Some(url) = url {
                    value["url"] = json!(url.as_str());
                }
                value
            };
        let mut frames = vec![entry(
            &self.frame_id,
            Value::Null,
            self.generation,
            &self.realm_id,
            self.url.as_ref(),
        )];
        frames.extend(self.children.iter().map(|child| {
            entry(
                &child.id,
                json!(self.frame_id),
                child.generation,
                &child.realm_id,
                child.url.as_ref(),
            )
        }));
        Value::Array(frames)
    }

    /// The bounded skip tally: one entry per reason that actually happened.
    fn frames_skipped_json(&self) -> Value {
        Value::Array(
            FRAME_SKIP_REASONS
                .iter()
                .zip(self.frames_skipped.iter())
                .filter(|(_, count)| **count > 0)
                .map(|(reason, count)| json!({"reason":reason,"count":count}))
                .collect(),
        )
    }

    fn frames_skipped_total(&self) -> u64 {
        self.frames_skipped
            .iter()
            .fold(0u64, |total, count| total.saturating_add(*count))
    }

    /// Document bytes this target owns: its own, every child's, and the URL
    /// bytes each frame keeps.
    fn document_bytes(&self) -> usize {
        self.fixture_bytes
            + self.url.as_ref().map(|url| url.as_str().len()).unwrap_or(0)
            + self
                .children
                .iter()
                .map(|child| {
                    child.bytes
                        + child
                            .url
                            .as_ref()
                            .map(|url| url.as_str().len())
                            .unwrap_or(0)
                })
                .sum::<usize>()
    }

    fn realms_json(&self) -> Value {
        let mut realms = vec![json!({"realm":self.realm_id,"frame":self.frame_id,"world":"main"})];
        realms.extend(
            self.children
                .iter()
                .map(|child| json!({"realm":child.realm_id,"frame":child.id,"world":"main"})),
        );
        Value::Array(realms)
    }

    /// Which frame an optional `frame` argument names: `None` for the main
    /// frame, `Some(index)` for a child. A frame that is not live in this
    /// target is not resolved here; the caller refuses it.
    fn frame_index(&self, frame: &str) -> Option<Option<usize>> {
        if frame == self.frame_id {
            return Some(None);
        }
        self.children
            .iter()
            .position(|child| child.id == frame)
            .map(Some)
    }

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
                                self.io.tls.as_deref(),
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
    /// The child frames the last committed navigation ended, read once by the
    /// result that reports them.
    ended_frames: Vec<String>,
    realms_retired_total: u64,
    navigations_total: u64,
    /// One bounded history per target with a URL, kept beside the targets so
    /// it survives the document swap a navigation performs.
    histories: BTreeMap<String, History>,
    /// Saturating per-target diagnostics, likewise kept across the swap.
    lifetimes: BTreeMap<String, Lifetime>,
    /// The newest bounded audit records per session, released with it.
    audits: BTreeMap<String, Ledger>,
    /// Monotonic across the host, so a record's place in the order is visible
    /// even after older records have been dropped.
    next_audit_sequence: u64,
    /// The deadline the request being executed carries, for the ledger.
    current_deadline_ms: u64,
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
    /// Pinned roots for the https slice; `None` keeps https unsupported.
    tls_roots: Option<net::TlsRoots>,
    /// TLS counters of targets that no longer exist.
    tls_retired: net::Budget,
    /// The surface process binary; `None` keeps `surface.show` unsupported.
    surface_binary: Option<PathBuf>,
    surfaces: BTreeMap<String, SurfaceRecord>,
    next_surface: u64,
    surface_generation: u32,
    surface_stats: surface::Stats,
    /// Court-only event log, present only with `--surface-court-file`.
    surface_court: Option<surface::CourtLog>,
    /// Court-only: the attribution court's child mode, frame size and
    /// in-process stage sampling (`--surface-court-stages`).
    surface_child_mode: Option<String>,
    surface_frame: surface::FrameSize,
    surface_stages: bool,
    /// Court-only (`--court-child-build-failure 1`): every child frame's realm
    /// construction fails, so the court can prove that a child that cannot be
    /// built is skipped with its fixed reason and never fails its parent.
    court_child_build_failure: bool,
    /// Court-only (`--court-revision-base N`): every target opens with this
    /// revision base, so the aggregate boundary is reachable.
    court_revision_base: u64,
    /// Court-only (`--court-frame-counter N`): every realm's counter starts
    /// here, so the per-frame Number boundary is reachable.
    court_frame_counter: u64,
    surface_snapshot_arm: Option<String>,
    surface_court_gc: bool,
    /// Visible windows need a double opt-in: `--visual 1` and the
    /// environment `MINICON_SURF_ALLOW_VISIBLE_COURT=1`. Without both the
    /// host never spawns a window: `surface.show` is refused unless a
    /// court-only no-AppKit child mode is set. Default: headless.
    surface_visual: bool,
}

/// One attached surface: its process and the frame it currently shows.
struct SurfaceRecord {
    id: String,
    target_id: String,
    process: surface::Process,
    painting: surface::Painting,
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
    fn tls_client(&self) -> Option<std::sync::Arc<net::TlsClient>> {
        self.tls_roots
            .as_ref()
            .map(|roots| std::sync::Arc::new(roots.client()))
    }

    /// Keep a closing target's TLS counters in the host totals.
    fn retire_target(&mut self, target: &Target) {
        self.tls_retired.absorb_tls(&target.budget);
        // Children before their parent, realm before frame: every realm the
        // target owned is retired exactly once, the main frame's included.
        self.realms_retired_total += target.children.len() as u64 + 1;
    }

    // ------------------------------------------------------------ surfaces

    /// The semantic rows of a target for the painter: the same snapshot the
    /// control door serves, kept as (node, role, name).
    fn surface_rows(
        target: &mut Target,
        deadline: Instant,
        policy: &net::Policy,
        stage: &mut dyn FnMut(&str, Option<Value>),
        arm: Option<&str>,
        gc: bool,
    ) -> Result<(SemanticRows, u64), ControlError> {
        // Court-only arms (`--surface-court-snapshot-arm`): `evaluate_only`
        // evaluates the same script and drops its text unparsed;
        // `parse_drop` parses and drops the `Value` before any row exists;
        // `microbench_flat` / `microbench_nested` evaluate lab-only scripts
        // that produce equal-byte JSON of the two shapes (not a browser
        // result). The product path is the default.
        stage("before_realm_eval", target.realm.arena_statistics());
        let script = match arm {
            Some("microbench_flat") => microbench_script(false),
            Some("microbench_nested") => microbench_script(true),
            // The painter observes the main frame, which is the only frame
            // it paints.
            _ => snapshot_script(64, false, target.base_target),
        };
        if arm == Some("evaluate_only") {
            let text = target.eval_staged(&script, deadline, policy, stage)?;
            drop(text);
            stage("after_string_drop", target.realm.arena_statistics());
            let revision = Self::revision(target, deadline, policy)?;
            if gc {
                target.run_gc();
                stage("after_gc", target.realm.arena_statistics());
            }
            return Ok((Vec::new(), revision));
        }
        let raw = Self::eval_json_staged(target, &script, deadline, policy, stage)?;
        // The painter observes the main frame, but the revision it reports is
        // the target's, children included: there is one such number.
        let revision = raw
            .get("revision")
            .and_then(Value::as_u64)
            .ok_or_else(|| {
                ControlError::new("internal", "snapshot lacks a revision", false)
                    .scoped("target", &target.id)
            })
            .and_then(|counter| {
                target
                    .global_revision(counter)
                    .ok_or_else(|| saturated(&target.id))
            })?;
        if arm == Some("parse_drop") {
            drop(raw);
            stage("after_value_drop", target.realm.arena_statistics());
            if gc {
                target.run_gc();
                stage("after_gc", target.realm.arena_statistics());
            }
            return Ok((Vec::new(), revision));
        }
        let nodes = raw
            .get("nodes")
            .and_then(Value::as_array)
            .cloned()
            .unwrap_or_default()
            .iter()
            .map(|entry| {
                (
                    entry
                        .get("node")
                        .and_then(Value::as_str)
                        .unwrap_or("node_0")
                        .to_owned(),
                    entry
                        .get("role")
                        .and_then(Value::as_str)
                        .unwrap_or("")
                        .to_owned(),
                    entry
                        .get("name")
                        .and_then(Value::as_str)
                        .unwrap_or("")
                        .to_owned(),
                )
            })
            .collect();
        stage("after_rows_extract", target.realm.arena_statistics());
        drop(raw);
        stage("after_value_drop", target.realm.arena_statistics());
        if gc {
            target.run_gc();
            stage("after_gc", target.realm.arena_statistics());
        }
        Ok((nodes, revision))
    }

    /// Court-only navigation stage sample: the process sample plus the owners
    /// that separate a navigation's own live bytes from what is merely
    /// resident. Same gate as every other court-only stage, so it is absent
    /// unless a court asks for it, and it changes nothing the host does.
    fn navigation_stage(&self, label: &str, target_id: &str) {
        if !self.surface_stages {
            return;
        }
        let Some(log) = &self.surface_court else {
            return;
        };
        let mut event = surface::self_sample();
        event["event"] = json!("stage");
        event["stage"] = json!(label);
        event["operation"] = json!("navigation");
        let empty = net::Budget::default();
        let target = self.targets.get(target_id);
        let budget = target.map_or(&empty, |target| &target.budget);
        let lifetime = self.lifetimes.get(target_id).copied().unwrap_or_default();
        event["owners"] = json!({
            "history_entries": self.histories.values().map(|h| h.entries.len()).sum::<usize>(),
            "history_bytes": self.histories.values().map(History::bytes).sum::<usize>(),
            "audit_entries": self.audits.values().map(|l| l.entries.len()).sum::<usize>(),
            "audit_bytes": self.audits.values().map(Ledger::bytes).sum::<usize>(),
            "audit_capacity_bytes": self.audits.values().map(Ledger::capacity_bytes).sum::<usize>(),
            "realm_malloc_bytes": self.targets.values().map(Target::realm_malloc_bytes).sum::<usize>(),
            "realms": self.targets.len(),
            "document_fetches": budget.fetches,
            "document_bytes": budget.bytes,
            "generation": target.map(|target| target.generation),
            "lifetime": lifetime.to_json(budget),
            "arena": target.and_then(|target| target.realm.arena_statistics()),
        });
        log.append(event);
    }

    /// Court-only request stage sample with the operation name, for the
    /// control-plane churn court. Same gate as `surface_stage`.
    fn request_stage(&self, label: &str, operation: Option<&str>) {
        if !self.surface_stages {
            return;
        }
        if let Some(log) = &self.surface_court {
            let mut event = surface::self_sample();
            event["event"] = json!("stage");
            event["stage"] = json!(label);
            event["operation"] = json!(operation);
            log.append(event);
        }
    }

    /// Court-only stage sample: appended to the court log only when it exists.
    fn surface_stage(&self, label: &str) {
        if !self.surface_stages {
            return;
        }
        if let Some(log) = &self.surface_court {
            let mut event = surface::self_sample();
            event["event"] = json!("stage");
            event["stage"] = json!(label);
            log.append(event);
        }
    }

    fn surface_show(
        &mut self,
        arguments: &Value,
        deadline: Instant,
    ) -> Result<Value, ControlError> {
        let object = exact_object(arguments, &["target"])?;
        let target_id = typed_field(object, "target", "target")?.to_owned();
        if !self.targets.contains_key(&target_id) {
            return Err(not_found("target", &target_id));
        }
        let headless_child = self
            .surface_child_mode
            .as_deref()
            .is_some_and(surface::is_headless_child_mode);
        if !self.surface_visual && !headless_child {
            return Err(ControlError::new(
                "unsupported_capability",
                "visible surfaces are not enabled on this host (headless by default; --visual 1 with MINICON_SURF_ALLOW_VISIBLE_COURT=1 opts in)",
                false,
            )
            .scoped("target", &target_id)
            .details(json!({"reason":"visible_surface_not_enabled"})));
        }
        let Some(binary) = self.surface_binary.clone() else {
            return Err(ControlError::new(
                "unsupported_capability",
                "no surface binary was given to this host",
                false,
            ));
        };
        if self.surfaces.values().any(|s| s.target_id == target_id) {
            return Err(ControlError::new(
                "conflict",
                "target already has an attached surface",
                false,
            )
            .scoped("target", &target_id));
        }
        if self.surfaces.len() >= surface::MAX_SURFACES {
            return Err(ControlError::new(
                "resource_limit",
                "surface capacity reached",
                true,
            ));
        }
        let policy = self.policy_for_target(&target_id);
        let started = Instant::now();
        self.surface_stage("show_entry");
        let frame_size = self.surface_frame;
        let arm = self.surface_snapshot_arm.clone();
        let gc = self.surface_court_gc;
        let stages = self.surface_stages;
        // Field-level borrows: the court log and the stats beside the target.
        let court = &self.surface_court;
        let mut stage = |label: &str, arena: Option<Value>| {
            if !stages {
                return;
            }
            if let Some(log) = court {
                let mut event = surface::self_sample();
                event["event"] = json!("stage");
                event["stage"] = json!(label);
                if let Some(arena) = arena {
                    event["arena"] = arena;
                }
                log.append(event);
            }
        };
        let target = self
            .targets
            .get_mut(&target_id)
            .ok_or_else(|| not_found("target", &target_id))?;
        let scroll_y = target.scroll_y;
        let (nodes, revision) =
            Self::surface_rows(target, deadline, &policy, &mut stage, arm.as_deref(), gc)?;
        stage("after_snapshot", None);
        let painting = surface::paint(&nodes, scroll_y, revision, frame_size).map_err(|error| {
            stage("show_failed", None);
            let (code, text, retryable) = match error {
                frame_region::RegionError::TooLarge => {
                    ("resource_limit", "frame exceeds the protocol bound", false)
                }
                frame_region::RegionError::Unsupported => (
                    "unsupported_capability",
                    "frame regions are not supported on this platform",
                    false,
                ),
                frame_region::RegionError::Os(_) => {
                    ("internal", "frame region could not be mapped", true)
                }
            };
            ControlError::new(code, text, retryable)
                .scoped("target", &target_id)
                .details(json!({"reason":"frame_region","detail":error.to_string()}))
        })?;
        stage("after_painter", None);
        self.next_surface += 1;
        self.surface_generation = self.surface_generation.wrapping_add(1);
        let id = format!("surface_{}", self.next_surface);
        let child_mode = self.surface_child_mode.clone();
        let spawned = surface::Process::spawn(
            &binary,
            self.surface_generation,
            &id,
            painting.pixels.as_slice(),
            frame_size,
            child_mode.as_deref(),
            self.surface_visual,
            &mut self.surface_stats,
            &mut |label: &str| stage(label, None),
        );
        let (process, ready_ms, first_frame_ms) = spawned.map_err(|detail| {
            stage("show_failed", None);
            ControlError::new("internal", "surface process did not start", true)
                .scoped("target", &target_id)
                .details(json!({"reason":"surface_process","detail":detail}))
        })?;
        if let Some(log) = court {
            // The court captures the own window itself (by this number) so the
            // host never links or pays for CoreGraphics capture.
            log.append(json!({
                "event":"shown","surface":id,"target":target_id,
                "window":{"number":process.ready.window_number,"content_x":process.ready.screen_x,"content_y":process.ready.screen_y,
                          "content_width":process.ready.content_width,"content_height":process.ready.content_height},
                "layout":painting.layout_json(),"revision":revision,
            }));
        }
        // The whole mapping counts while it exists, 0 after hide.
        let presentation_bytes = painting.pixels.mapped_len();
        self.surfaces.insert(
            id.clone(),
            SurfaceRecord {
                id: id.clone(),
                target_id: target_id.clone(),
                process,
                painting,
            },
        );
        stage("shown", None);
        Ok(json!({
            "kind":"surface","surface":id,"target":target_id,"state":"headed",
            "presentation_bytes":presentation_bytes,
            "frame":{"width":frame_size.width,"height":frame_size.height,"format":"bgra8"},
            "painter":surface::PAINTER,
            "latency":{"ready_ms":ready_ms,"first_frame_ms":first_frame_ms,"show_ms":started.elapsed().as_millis() as u64},
        }))
    }

    fn surface_hide(&mut self, arguments: &Value) -> Result<Value, ControlError> {
        let object = exact_object(arguments, &["surface"])?;
        let surface_id = typed_field(object, "surface", "surface")?;
        let record = self
            .surfaces
            .remove(surface_id)
            .ok_or_else(|| not_found("surface", surface_id))?;
        let released = record.painting.pixels.mapped_len();
        self.surface_stage("hide_entry");
        let SurfaceRecord {
            id: record_id,
            target_id: record_target,
            process,
            painting,
        } = record;
        // Order: the record already left the map (no new paint or input), no
        // frame is pending in host memory, then CLOSE/reap (or kill), then
        // the mapping is unmapped with the painting.
        let teardown = process.hide(&mut self.surface_stats);
        self.surface_stage("after_close_reap_join");
        drop(painting);
        self.surface_stage("after_frame_drop");
        if let Some(log) = &self.surface_court {
            log.append(json!({"event":"hidden","surface":record_id,"target":record_target,"exit":teardown.exit.name(),"ms":teardown.ms}));
        }
        Ok(json!({
            "kind":"surface_hidden","surface":record_id,"target":record_target,"state":"headless",
            "released_presentation_bytes":released,
            "teardown":{"exit":teardown.exit.name(),"reaped":teardown.reaped,"ms":teardown.ms},
        }))
    }

    /// Release every surface of a target (teardown order adapters →
    /// surfaces → target); returns how many.
    fn release_surfaces_of(&mut self, target_id: &str) -> usize {
        let ids: Vec<String> = self
            .surfaces
            .values()
            .filter(|s| s.target_id == target_id)
            .map(|s| s.id.clone())
            .collect();
        for id in &ids {
            if let Some(record) = self.surfaces.remove(id) {
                let teardown = record.process.hide(&mut self.surface_stats);
                if let Some(log) = &self.surface_court {
                    log.append(json!({"event":"hidden","surface":record.id,"target":record.target_id,"exit":teardown.exit.name(),"ms":teardown.ms}));
                }
            }
        }
        ids.len()
    }

    /// Apply pending surface input while the host is idle: FIFO per surface,
    /// each event an atomic host-internal mutation, stale generations and
    /// stale frames dropped; a child that went away is released.
    fn pump_surfaces(&mut self, deadline: Instant) {
        let ids: Vec<String> = self.surfaces.keys().cloned().collect();
        for id in ids {
            let (inputs, gone, generation) = {
                let Some(record) = self.surfaces.get_mut(&id) else {
                    continue;
                };
                let inputs = record
                    .process
                    .poll(&mut self.surface_stats, record.painting.pixels.as_slice());
                (
                    inputs,
                    record.process.is_gone(),
                    record.process.generation(),
                )
            };
            if generation != self.surface_generation {
                // Only the newest spawn generation is current.
                self.surface_stats.stale_events_dropped_total += inputs.len() as u64;
            } else {
                for input in inputs {
                    self.apply_surface_input(&id, input, deadline);
                }
            }
            if gone && let Some(record) = self.surfaces.remove(&id) {
                let error = record.process.last_error().map(str::to_owned);
                let teardown = record.process.hide(&mut self.surface_stats);
                if let Some(log) = &self.surface_court {
                    log.append(json!({"event":"child_exit","surface":record.id,"target":record.target_id,"exit":teardown.exit.name(),"error":error}));
                }
            }
        }
    }

    fn apply_surface_input(&mut self, surface_id: &str, input: surface::Input, deadline: Instant) {
        self.surface_stats.input_events_total += 1;
        let policy = self
            .surfaces
            .get(surface_id)
            .map(|record| self.policy_for_target(&record.target_id))
            .unwrap_or_else(|| self.policy.clone());
        let Some(record) = self.surfaces.get(surface_id) else {
            return;
        };
        let target_id = record.target_id.clone();
        let frame_revision = record.painting.revision;
        let hit = record
            .painting
            .row_at(usize::from(input.y))
            .map(|row| row.node.clone());
        if !self.targets.contains_key(&target_id) {
            return;
        }
        self.sync_target_io(&target_id);
        let Some(target) = self.targets.get_mut(&target_id) else {
            return;
        };
        let Ok(current) = Self::revision(target, deadline, &policy) else {
            return;
        };
        let mut applied: Option<(&'static str, u64)> = None;
        match input.kind {
            surface::INPUT_KIND_CLICK => {
                // A click is valid only against the frame it was made on.
                if current != frame_revision || input.x >= self.surface_frame.width {
                    self.surface_stats.stale_events_dropped_total += 1;
                } else if let Some(node) = hit
                    && let Some(index) = node
                        .strip_prefix("node_")
                        .and_then(|s| s.parse::<usize>().ok())
                        .filter(|n| *n >= 1)
                {
                    // A surface click reaches the main frame only: the
                    // painter paints that frame's rows and nothing else. It
                    // takes the same two phases as a control-door click, so a
                    // painted row cannot activate what the door refuses.
                    let base = target.revision_base;
                    let children: u64 = target
                        .children
                        .iter()
                        .fold(0u64, |sum, child| sum.saturating_add(child.counter));
                    let has_base_target = target.base_target;
                    let counter = current.saturating_sub(base).saturating_sub(children);
                    let preflight = Self::eval_json(
                        target,
                        &preflight_script(
                            counter,
                            index - 1,
                            "{\"kind\":\"click\"}",
                            false,
                            has_base_target,
                        ),
                        deadline,
                        &policy,
                    );
                    let approved = preflight.ok().filter(|value| {
                        value.get("decision").and_then(Value::as_str) == Some("allowed")
                    });
                    let signature = approved.as_ref().and_then(|value| {
                        value
                            .get("signature")
                            .and_then(Value::as_str)
                            .map(str::to_owned)
                    });
                    if counter < MAX_SAFE_COUNTER
                        && current.checked_add(1).is_some()
                        && let Some(signature) = signature
                        && let Ok(outcome) = Self::eval_json(
                            target,
                            &act_script(counter, index - 1, false, has_base_target, &signature),
                            deadline,
                            &policy,
                        )
                        && outcome.get("applied").and_then(Value::as_bool) == Some(true)
                    {
                        let after = Self::revision(target, deadline, &policy).unwrap_or(current);
                        applied = Some(("click", after));
                    } else {
                        self.surface_stats.stale_events_dropped_total += 1;
                    }
                }
            }
            surface::INPUT_KIND_SCROLL => {
                let next = if input.delta >= 0 {
                    target.scroll_y.saturating_add(input.delta as u64)
                } else {
                    target
                        .scroll_y
                        .saturating_sub(input.delta.unsigned_abs() as u64)
                }
                .min(surface::MAX_SCROLL);
                target.scroll_y = next;
                // The synthetic host's rule: a scroll advances the revision.
                if let Ok(text) = target.eval(SCROLL_REVISION_JS, deadline, &policy)
                    && let Ok(after) = text.parse::<u64>()
                {
                    // A scroll that cannot advance the target's revision is
                    // dropped rather than reported with a wrong number.
                    applied = target
                        .global_revision(after)
                        .map(|revision| ("scroll", revision));
                }
            }
            _ => {}
        }
        let _ = target;
        if let Some((kind, revision)) = applied {
            let _ = self.commit_target_io(&target_id, deadline);
            let scroll_y = self
                .targets
                .get(&target_id)
                .map(|t| t.scroll_y)
                .unwrap_or(0);
            if let Some(log) = &self.surface_court {
                log.append(json!({"event":"input_applied","surface":surface_id,"target":target_id,"kind":kind,"revision":revision,"scroll_y":scroll_y}));
            }
            // Repaint after any applied input so the window follows the page.
            let Some(target) = self.targets.get_mut(&target_id) else {
                return;
            };
            if let Ok((nodes, rev)) =
                Self::surface_rows(target, deadline, &policy, &mut |_, _| {}, None, false)
                && let Some(record) = self.surfaces.get_mut(surface_id)
            {
                // Repaint into the frame the surface already owns: no new buffer.
                surface::paint_into(&mut record.painting, &nodes, scroll_y, rev);
                let _ = record
                    .process
                    .send_frame(record.painting.pixels.as_slice(), &mut self.surface_stats);
                if let Some(log) = &self.surface_court {
                    log.append(json!({"event":"repainted","surface":surface_id,"layout":record.painting.layout_json()}));
                }
            }
        }
    }

    /// `owners.surfaces.frame`: the live mappings (reserved, touched, live),
    /// the lifetime unmap counters and the process's virtual size and
    /// physical footprint. No address, no length of a single mapping.
    fn surface_frame_owner(&self) -> Value {
        let counters = frame_region::counters();
        let (footprint, virtual_size) = surface::vm_sample();
        json!({
            "backing":"anonymous_mmap",
            "regions":self.surfaces.len(),
            "reserved_bytes":self.surfaces.values().map(|s| s.painting.pixels.mapped_len()).sum::<usize>(),
            "touched_bytes":self.surfaces.values().map(|s| s.painting.pixels.touched_bytes()).sum::<usize>(),
            "live_bytes":self.surfaces.values().map(|s| s.painting.pixels.frame_len()).sum::<usize>(),
            "regions_mapped_total":counters.regions_mapped_total,
            "regions_unmapped_total":counters.regions_unmapped_total,
            "unmapped_bytes_total":counters.unmapped_bytes_total,
            "host":{"virtual_bytes":virtual_size,"physical_footprint_bytes":footprint},
        })
    }

    fn tls_owner(&self) -> Value {
        let mut totals = self.tls_retired.clone();
        for target in self.targets.values() {
            totals.absorb_tls(&target.budget);
        }
        let mut owner = totals.tls_json();
        let roots = self.tls_roots.as_ref();
        owner["enabled"] = json!(roots.is_some());
        owner["pinned_roots"] = json!(roots.map_or(0, |r| r.certificates));
        owner["pinned_root_files"] = json!(roots.map_or(0, |r| r.files));
        owner["pinned_root_bytes"] = json!(roots.map_or(0, |r| r.bytes));
        owner["provider"] = json!(roots.map(|_| net::TLS_PROVIDER));
        owner["session_cache_entries_per_profile"] = json!(net::TLS_SESSION_CACHE_ENTRIES);
        owner["live_connections"] = json!(0);
        owner["limits"] = json!({"root_files":net::MAX_PINNED_ROOT_FILES,"root_file_bytes":net::MAX_PINNED_ROOT_FILE_BYTES,"root_total_bytes":net::MAX_PINNED_ROOT_TOTAL_BYTES,"roots":net::MAX_PINNED_ROOTS});
        owner
    }

    /// Operations only the in-process CDP edge may request: adapter
    /// bookkeeping is not part of control 0.0.1 and never reaches stdio,
    /// whose parser refuses unknown operation names.
    fn execute_bridge(&mut self, operation: &str, arguments: Value) -> Result<Value, String> {
        let outcome = match operation {
            "adapter.attach" => self.adapter_attach(&arguments),
            "adapter.detach" => self.adapter_detach(&arguments),
            "adapter.inspect" => self.adapter_inspect(&arguments),
            _ => {
                // The bridge speaks the newest version, so the navigation
                // operations it maps are available to it.
                if !operation_available(operation, VERSION_NEXT) {
                    return Err("invalid_request".into());
                }
                self.next_bridge_request += 1;
                let request = Request {
                    request_id: format!("req_cdp_{}", self.next_bridge_request),
                    version: VERSION_NEXT.to_owned(),
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
    /// The network policy that applies to one target: the host's allowlist
    /// with the owning profile's switch. The switch is enforced inside
    /// `authorize`, before a name is resolved or a socket is opened, so it
    /// covers a navigation and a page's own fetch alike.
    fn policy_for_target(&self, target_id: &str) -> net::Policy {
        match self.target_profile_id(target_id) {
            Some(profile) => self.policy_for_profile(&profile),
            None => self.policy.clone(),
        }
    }

    fn policy_for_profile(&self, profile_id: &str) -> net::Policy {
        let mut policy = self.policy.clone();
        if let Some(profile) = self.profiles.get(profile_id) {
            policy.offline = !profile.policy.online;
        }
        policy
    }

    fn policy_for_session(&self, session_id: &str) -> net::Policy {
        match self.sessions.get(session_id) {
            Some(session) => self.policy_for_profile(&session.profile_id),
            None => self.policy.clone(),
        }
    }

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
            tls: profile.tls.clone(),
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
        let (Some(source), Some(dek), Some(directory)) =
            (&self.key_source, &profile.dek, &profile.directory)
        else {
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
            online: profile.policy.online,
            allow_by_default: profile.policy.allow_by_default,
        };
        let bytes = profile::seal_record(source, profile_id, dek, &data)
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
        if mode == profile::StoreMode::KeychainEnvelope && !profile::disable_keychain_interaction()
        {
            eprintln!(
                "native-dom-control: keychain interaction could not be disabled; persistent profiles fail closed"
            );
        }
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
                Ok((dek, data)) if self.profiles.len() < MAX_PROFILES => {
                    let policy_from_record = ProfilePolicy {
                        online: data.online,
                        allow_by_default: data.allow_by_default,
                    };
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
                            directory: Some(directory),
                            read_only: false,
                            lock: None,
                            tls: self.tls_client(),
                            policy: policy_from_record,
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
    /// The target-global revision: the base plus every live frame's counter.
    /// The main frame's counter is read from its realm, because page scripts
    /// and queued jobs can move it; a child's is taken from the cache, which
    /// is sound exactly while children run no scripts.
    fn revision(
        target: &mut Target,
        deadline: Instant,
        policy: &net::Policy,
    ) -> Result<u64, ControlError> {
        let text = target.eval(REVISION_JS, deadline, policy)?;
        let counter = text
            .parse::<i64>()
            .ok()
            .filter(|r| *r >= 0)
            .map(|r| r as u64);
        if let Some(counter) = counter {
            return target
                .global_revision(counter)
                .ok_or_else(|| saturated(&target.id));
        }
        counter
            .and_then(|counter| target.global_revision(counter))
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
        Self::eval_json_staged(target, script, deadline, policy, &mut |_, _| {})
    }

    /// `eval_json` with court-only stage samples around the parse: the host
    /// `String` alive, the `Value` parsed beside it, the `String` dropped.
    fn eval_json_staged(
        target: &mut Target,
        script: &str,
        deadline: Instant,
        policy: &net::Policy,
        stage: &mut dyn FnMut(&str, Option<Value>),
    ) -> Result<Value, ControlError> {
        let text = target.eval_staged(script, deadline, policy, stage)?;
        stage("before_serde_parse", target.realm.arena_statistics());
        let value = serde_json::from_str(&text).map_err(|_| {
            ControlError::new("internal", "engine returned malformed snapshot JSON", false)
                .scoped("target", &target.id)
        })?;
        stage("after_serde_parse", target.realm.arena_statistics());
        drop(text);
        stage("after_string_drop", target.realm.arena_statistics());
        Ok(value)
    }

    fn execute(&mut self, request: &Request) -> Result<Value, ControlError> {
        let deadline = Instant::now() + request.deadline;
        self.current_deadline_ms = request.deadline.as_millis() as u64;
        let a = &request.arguments;
        // Target operations run on a working copy of the profile that is
        // synced before and committed after; a failed commit is the
        // operation's typed failure.
        let target_scoped = matches!(
            request.operation.as_str(),
            "target.inspect"
                | "target.snapshot"
                | "target.act"
                | "target.wait"
                | "target.navigate"
                | "target.reload"
                | "target.traverse"
        );
        let target_id = if target_scoped {
            a.get("target").and_then(Value::as_str).map(str::to_owned)
        } else {
            None
        };
        if let Some(id) = &target_id {
            self.sync_target_io(id);
            self.request_stage("after_sync_io", Some(&request.operation));
        }
        let outcome = self.dispatch(request, deadline);
        self.request_stage("after_dispatch", Some(&request.operation));
        if let Some(id) = &target_id
            && self.targets.contains_key(id)
        {
            self.commit_target_io(id, deadline)?;
            self.request_stage("after_commit_io", Some(&request.operation));
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
                    "policy":profile.policy.to_json(),
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
            "profile.policy.set" => self.profile_policy_set(a),
            "session.inspect" => self.session_inspect(a),
            "session.close" => self.session_close(a),
            "target.open" => self.target_open(a, deadline),
            "target.list" => Ok(
                json!({"kind":"target_list","targets":self.targets.values().map(|t| json!({"target":t.id,"session":t.session_id,"fixture":t.fixture,"url":t.url.as_ref().map(Url::as_str)})).collect::<Vec<_>>()}),
            ),
            "target.inspect" => {
                let object = exact_object(a, &["target"])?;
                let id = typed_field(object, "target", "target")?.to_owned();
                let policy = self.policy_for_target(&id);
                let https = self.tls_roots.is_some();
                let surface_id = self
                    .surfaces
                    .values()
                    .find(|s| s.target_id == id)
                    .map(|s| s.id.clone());
                let target = self.target_mut(&id)?;
                let revision = Self::revision(target, deadline, &policy)?;
                Ok(json!({
                    "kind":"target","target":target.id,"session":target.session_id,"fixture":target.fixture,
                    "url":target.url.as_ref().map(Url::as_str),"document_framing":target.document_framing,"revision":revision,"load_complete":true,"crashed":false,
                    "script_realm":true,"scripts_run":target.script_count,"scripts_skipped":target.skipped_scripts,
                    "frames":target.frames_json(),
                    "frames_skipped":target.frames_skipped_json(),
                    "realms":target.realms_json(),
                    "frame_limit":MAX_FRAMES_PER_TARGET,
                    "network":target_network(&target.budget, https),
                    "surface":surface_id,
                    "scroll_y":target.scroll_y,
                    "history":self.histories.get(&id).map(History::to_json)
                }))
            }
            "target.navigate" => self.target_navigate(a, deadline),
            "target.reload" => self.target_reload(a, deadline),
            "target.traverse" => self.target_traverse(a, deadline),
            "target.close" => {
                let object = exact_object(a, &["target"])?;
                let id = typed_field(object, "target", "target")?;
                if !self.targets.contains_key(id) {
                    return Err(not_found("target", id));
                }
                let detached = self.detach_adapters_of(id);
                let released = self.release_surfaces_of(id);
                let closed = self.targets.remove(id).expect("checked above");
                self.histories.remove(id);
                self.lifetimes.remove(id);
                self.retire_target(&closed);
                Ok(
                    json!({"kind":"target_closed","target":id,"teardown":{"adapters_detached":detached,"surfaces_released":released,"order":["adapters","surfaces","target"]}}),
                )
            }
            "surface.show" => self.surface_show(a, deadline),
            "surface.hide" => self.surface_hide(a),
            "target.snapshot" => self.target_snapshot(a, deadline),
            "target.act" => self.target_act(a, &request.version, deadline),
            "target.wait" => self.target_wait(a, deadline),
            "memory.report" => Ok(self.memory_report()),
            "memory.trim" => {
                exact_object(a, &[])?;
                #[cfg(target_os = "macos")]
                {
                    // SAFETY: a null zone requests pressure relief from every
                    // malloc zone; a zero goal asks for everything reclaimable.
                    let released = unsafe { malloc_zone_pressure_relief(std::ptr::null_mut(), 0) };
                    let arena_released: usize = self
                        .targets
                        .values()
                        .map(|t| {
                            t.realm.trim_arena()
                                + t.children
                                    .iter()
                                    .map(|child| child.realm.trim_arena())
                                    .sum::<usize>()
                        })
                        .sum();
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
                directory: None,
                read_only: false,
                lock: None,
                tls: self.tls_client(),
                policy: ProfilePolicy::default(),
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
        // Seal first: a missing or locked keychain fails here, before any file.
        let bytes = profile::seal_record(source, &id, &dek, &profile::RecordData::default())
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
                directory: Some(directory),
                read_only: false,
                lock: None,
                tls: self.tls_client(),
                policy: ProfilePolicy::default(),
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
        self.audits.remove(&session.id);
        let closed = ids.len();
        let mut detached = 0;
        let mut released = 0;
        for id in ids {
            detached += self.detach_adapters_of(&id);
            released += self.release_surfaces_of(&id);
            if let Some(closed) = self.targets.remove(&id) {
                self.histories.remove(&id);
                self.lifetimes.remove(&id);
                self.retire_target(&closed);
            }
        }
        let _ = released;
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
        let policy = self.policy_for_session(session);
        let revision = Self::revision(&mut target, deadline, &policy)?;
        let summary = json!({
            "kind":"target","target":id,"session":session,"revision":revision,"fixture":target.fixture,
            "url":target.url.as_ref().map(Url::as_str),"document_framing":target.document_framing,"scripts_run":target.script_count,
            "scripts_skipped":target.skipped_scripts.len(),
            "frame":target.frame_id,"generation":target.generation,"realm":target.realm_id,
            "network":target_network(&target.budget, self.tls_roots.is_some())
        });
        self.targets.insert(id.clone(), target);
        // A target opened at a URL starts its history with that URL.
        if let Some(url) = self
            .targets
            .get(&id)
            .and_then(|target| target.url.as_ref())
            .map(Url::to_string)
        {
            self.histories.insert(id.clone(), History::new(&url));
        }
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
        let policy = self.policy_for_session(session);
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
                    net::fetch_with(
                        &raw,
                        &policy,
                        &mut budget,
                        deadline,
                        Some(&mut hooks),
                        io.tls.as_deref(),
                    )
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

        // Court-only: the boundary between the candidate's bytes arriving and
        // its document existing.
        self.navigation_stage("candidate_fetched", id);
        let text = String::from_utf8_lossy(&bytes).into_owned();
        let document = Document::from(text.as_str());
        let element_count = document.select("*").nodes().len();
        let base_target = document
            .select("base")
            .nodes()
            .iter()
            .any(|node| node.attr("target").is_some());
        let mut tree = Vec::new();
        serialize_children(&document.root(), &mut tree);
        // Embedded documents in document order, collected before the parse is
        // dropped. `Err` is a source that will never be fetched and the reason
        // it is refused, decided without touching the network.
        let mut embedded: Vec<Result<String, &'static str>> = Vec::new();
        for node in document.select("iframe").nodes() {
            if node.attr("srcdoc").is_some() {
                embedded.push(Err("srcdoc"));
                continue;
            }
            // A sandbox without allow-same-origin means an opaque origin, and
            // every child here is same-origin by construction. The frame is
            // not built rather than built with more authority than the page
            // asked for.
            if node.attr("sandbox").is_some() {
                embedded.push(Err("sandboxed"));
                continue;
            }
            match node.attr("src") {
                Some(src) if !src.trim().is_empty() => embedded.push(Ok(src.to_string())),
                _ => embedded.push(Err("no_src")),
            }
        }
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
                        io.tls.as_deref(),
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
            realm.eval(&location_script(base_url), deadline, id)?;
            io.origin = base_url.origin().ascii_serialization();
            io.document_host = base_url.host_str().map(|h| h.to_ascii_lowercase());
        } else {
            io.origin = profile::OPAQUE_ORIGIN.to_owned();
            io.document_host = None;
        }
        // The realm id is minted only once the document exists; a failed
        // build never consumes one. It is taken here, before the children
        // mint theirs, so a child can never carry its parent's realm id.
        self.next_realm += 1;
        let main_realm_id = format!("realm_{}", self.next_realm);
        // Bounded child frames, built with their parent, under its budget and
        // its deadline, same-origin only. A child that cannot be built is
        // skipped with a reason; it never fails the parent's document.
        let mut children: Vec<ChildFrame> = Vec::new();
        let mut frames_skipped = [0u64; FRAME_SKIP_REASONS.len()];
        let skip = |tally: &mut [u64; FRAME_SKIP_REASONS.len()], reason: &str| {
            let index = FRAME_SKIP_REASONS
                .iter()
                .position(|candidate| *candidate == reason)
                .expect("a frame skip reason is one of the closed set");
            tally[index] = tally[index].saturating_add(1);
        };
        for source in embedded {
            if children.len() + 1 >= MAX_FRAMES_PER_TARGET {
                skip(&mut frames_skipped, "frame_limit");
                continue;
            }
            let src = match source {
                Ok(src) => src,
                Err(reason) => {
                    skip(&mut frames_skipped, reason);
                    continue;
                }
            };
            let Some(base_url) = &base else {
                skip(&mut frames_skipped, "no_network_origin");
                continue;
            };
            let Ok(resolved) = base_url.join(&src) else {
                skip(&mut frames_skipped, "malformed_src");
                continue;
            };
            // The scheme first, so a document that is never fetched is not
            // reported as one refused for its origin.
            if !matches!(resolved.scheme(), "http" | "https") {
                skip(&mut frames_skipped, "scheme_not_fetched");
                continue;
            }
            if !net::same_origin(base_url, &resolved) {
                budget.denied += 1;
                skip(&mut frames_skipped, "cross_origin_src");
                continue;
            }
            // The attempt runs against a copy of the jar. A child that is not
            // kept leaves nothing behind: the copy is dropped and the parent
            // commits with the jar it had, exactly as a failed navigation
            // candidate leaves the live document untouched. The attempt
            // counters stay, because they are diagnostics of what was tried.
            let mut jar = io.jar.clone();
            let mut rejections = io.cookie_rejections;
            let document_host = base_url.host_str().map(|h| h.to_ascii_lowercase());
            let mut hooks = JarHooks {
                jar: &mut jar,
                document_host: document_host.as_deref(),
                now,
                rejections: &mut rejections,
            };
            let outcome = net::fetch_with(
                resolved.as_str(),
                &policy,
                &mut budget,
                deadline,
                Some(&mut hooks),
                io.tls.as_deref(),
            );
            let response = match outcome {
                Ok(response) if response.status >= 400 => {
                    skip(&mut frames_skipped, "status_not_ok");
                    continue;
                }
                Ok(response) => response,
                Err(_) => {
                    skip(&mut frames_skipped, "fetch_failed");
                    continue;
                }
            };
            // Same origin has to hold on what answered, not only on what was
            // asked for: a redirect that leaves the origin is not a child.
            if !net::same_origin(base_url, &response.url) {
                budget.denied += 1;
                skip(&mut frames_skipped, "cross_origin_redirect");
                continue;
            }
            let html = response
                .content_type
                .as_deref()
                .map(|value| {
                    value
                        .split(';')
                        .next()
                        .unwrap_or_default()
                        .trim()
                        .eq_ignore_ascii_case("text/html")
                })
                .unwrap_or(false);
            if !html {
                skip(&mut frames_skipped, "not_html");
                continue;
            }
            let text = String::from_utf8_lossy(&response.body).into_owned();
            let parsed = Document::from(text.as_str());
            let element_count = parsed.select("*").nodes().len();
            let child_base_target = parsed
                .select("base")
                .nodes()
                .iter()
                .any(|node| node.attr("target").is_some());
            let mut child_tree = Vec::new();
            serialize_children(&parsed.root(), &mut child_tree);
            drop(parsed);
            // Every construction failure here is this child's alone: the
            // half-built realm is dropped and the parent still commits.
            let built = (|| -> Result<Realm, ControlError> {
                let realm = Realm::new(self.realm_allocation)?;
                if self.court_child_build_failure {
                    return Err(ControlError::new(
                        "internal",
                        "court-only: the child realm build is forced to fail",
                        false,
                    ));
                }
                realm.eval(DOM_SHIM_JS, deadline, id)?;
                realm.eval(
                    &format!(
                        "__mcsSeed({})",
                        serde_json::to_string(&child_tree).expect("tree serializes")
                    ),
                    deadline,
                    id,
                )?;
                realm.eval(&location_script(&response.url), deadline, id)?;
                realm.eval("__mcsComplete()", deadline, id)?;
                realm.eval(INSTALL_JS, deadline, id)?;
                Ok(realm)
            })();
            let Ok(realm) = built else {
                skip(&mut frames_skipped, "realm_build_failed");
                continue;
            };
            // The child is kept, so what its fetch changed is kept with it.
            io.jar = jar;
            io.cookie_rejections = rejections;
            self.next_frame += 1;
            self.next_realm += 1;
            children.push(ChildFrame {
                id: format!("frame_{}", self.next_frame),
                generation: 1,
                realm_id: format!("realm_{}", self.next_realm),
                realm,
                counter: 0,
                snapshot: None,
                base_target: child_base_target,
                url: Some(response.url.clone()),
                bytes: response.body.len(),
                element_count,
            });
        }
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
            realm_id: main_realm_id,
            revision_base,
            io,
            scroll_y: 0,
            children,
            frames_skipped,
            base_target,
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
        // Court-only seams: they do nothing unless the knobs are given.
        if self.court_frame_counter > 0 {
            let seed = format!(
                "(() => {{ window.__mcs.revision = {}; return String(window.__mcs.revision); }})()",
                self.court_frame_counter
            );
            target.eval(&seed, deadline, &policy)?;
        }
        if self.court_revision_base > 0 && target.revision_base == 0 {
            target.revision_base = self.court_revision_base;
        }
        Ok(target)
    }

    /// Append one bounded record of a navigation operation. It is evidence
    /// that the operation happened, never an authorization: this host
    /// implements no capability attenuation.
    fn audit_navigation(
        &mut self,
        target_id: &str,
        operation: &'static str,
        url: Option<&str>,
        outcome: &'static str,
    ) {
        let Some(session) = self.targets.get(target_id).map(|t| t.session_id.clone()) else {
            return;
        };
        let sequence = self.next_audit_sequence;
        self.next_audit_sequence = self.next_audit_sequence.saturating_add(1);
        let deadline_ms = self.current_deadline_ms;
        let origin = url.and_then(origin_only);
        let ledger = self.audits.entry(session).or_default();
        // The target id and the origin repeat over a run, so they are shared
        // rather than allocated again for every record.
        let target = Ledger::share(&mut ledger.targets, target_id, MAX_TARGETS);
        let origin =
            origin.map(|origin| Ledger::share(&mut ledger.origins, &origin, MAX_AUDIT_ORIGINS));
        ledger.append(AuditEntry {
            sequence,
            deadline_ms,
            value_bytes: None,
            target,
            frame: None,
            origin,
            operation,
            outcome,
        });
    }

    /// `profile.policy.set`: the network switch and the default permission
    /// answer of the session's profile, with exactly the arguments control
    /// 0.0.1 reserved. A persistent profile writes the change through with
    /// the store's existing atomic replacement; a failed write leaves the live
    /// policy and the record exactly as they were.
    fn profile_policy_set(&mut self, arguments: &Value) -> Result<Value, ControlError> {
        let object = exact_object(arguments, &["session", "network", "permissions"])?;
        let session_id = typed_field(object, "session", "session")?.to_owned();
        let online = match string_field(object, "network")? {
            "online" => true,
            "offline" => false,
            _ => return Err(invalid("network must be online or offline")),
        };
        let allow_by_default = match string_field(object, "permissions")? {
            "allow_by_default" => true,
            "deny_by_default" => false,
            _ => {
                return Err(invalid(
                    "permissions must be allow_by_default or deny_by_default",
                ));
            }
        };
        let profile_id = self
            .sessions
            .get(&session_id)
            .map(|session| session.profile_id.clone())
            .ok_or_else(|| not_found("session", &session_id))?;
        let profile = self
            .profiles
            .get_mut(&profile_id)
            .ok_or_else(|| not_found("profile", &profile_id))?;
        let persistent = profile.persistent;
        if persistent && profile.read_only {
            return Err(commit_failed(
                &profile_id,
                "storage is read-only after an earlier failed commit",
            ));
        }
        let previous = profile.policy;
        profile.policy = ProfilePolicy {
            online,
            allow_by_default,
        };
        if persistent && let Err(error) = self.write_profile(&profile_id) {
            // The disk refused: the live policy goes back to what the record
            // still says, and nothing about this profile changed.
            let profile = self.profiles.get_mut(&profile_id).expect("profile exists");
            profile.policy = previous;
            profile.read_only = true;
            return Err(error);
        }
        let policy = self
            .profiles
            .get(&profile_id)
            .expect("profile exists")
            .policy;
        Ok(json!({
            "kind":"profile_policy","profile":profile_id,"session":session_id,
            "policy":policy.to_json(),"persisted":persistent,
        }))
    }

    /// One bounded audit record for a form action. It names the kind and the
    /// outcome and, for a value, only how many bytes it had. No value, no
    /// label, no field name and no query ever reaches the ledger.
    fn audit_action(
        &mut self,
        target_id: &str,
        frame_index: Option<usize>,
        kind: &str,
        outcome: &str,
        value_bytes: Option<u64>,
    ) {
        let Some(session) = self.targets.get(target_id).map(|t| t.session_id.clone()) else {
            return;
        };
        let frame_id = self.targets.get(target_id).and_then(|t| match frame_index {
            None => Some(t.frame_id.clone()),
            Some(child) => t.children.get(child).map(|c| c.id.clone()),
        });
        let sequence = self.next_audit_sequence;
        self.next_audit_sequence = self.next_audit_sequence.saturating_add(1);
        let deadline_ms = self.current_deadline_ms;
        let ledger = self.audits.entry(session).or_default();
        let target = Ledger::share(&mut ledger.targets, target_id, MAX_TARGETS);
        let frame = frame_id
            .map(|id| Ledger::share(&mut ledger.frames, &id, MAX_TARGETS * MAX_FRAMES_PER_TARGET));
        // The action's kind and outcome are fixed vocabularies, so they cost
        // nothing per record and cannot smuggle page data.
        let operation = match kind {
            "set_value" => "target.act:set_value",
            "set_checked" => "target.act:set_checked",
            "select_option" => "target.act:select_option",
            "submit" => "target.act:submit",
            "press" => "target.act:press",
            _ => "target.act:click",
        };
        let outcome = match outcome {
            "applied" => "applied",
            "committed" => "committed",
            "unsupported_capability" => "unsupported_capability",
            "not_found" => "not_found",
            "resource_limit" => "resource_limit",
            "invalid_request" => "invalid_request",
            "deadline_exceeded" => "deadline_exceeded",
            "permission_denied" => "permission_denied",
            _ => "internal",
        };
        ledger.append(AuditEntry {
            sequence,
            deadline_ms,
            value_bytes,
            target,
            frame,
            origin: None,
            operation,
            outcome,
        });
    }

    /// `session.inspect`: read-only and bounded. Identity and owner chain, the
    /// live targets and surfaces it owns, the versions and exact operations
    /// this host serves, and the audit ledger.
    fn session_inspect(&mut self, arguments: &Value) -> Result<Value, ControlError> {
        let object = exact_object(arguments, &["session"])?;
        let id = typed_field(object, "session", "session")?.to_owned();
        let session = self
            .sessions
            .get(&id)
            .ok_or_else(|| not_found("session", &id))?;
        let profile = session.profile_id.clone();
        let targets: Vec<&String> = self
            .targets
            .values()
            .filter(|t| t.session_id == id)
            .map(|t| &t.id)
            .collect();
        let surfaces: Vec<&String> = self
            .surfaces
            .values()
            .filter(|s| {
                self.targets
                    .get(&s.target_id)
                    .is_some_and(|t| t.session_id == id)
            })
            .map(|s| &s.id)
            .collect();
        let ledger = self.audits.get(&id);
        Ok(json!({
            "kind":"session_inspection","session":id,"profile":profile,
            "targets":targets,"surfaces":surfaces,
            "supported_protocol_versions":[VERSION, VERSION_NEXT],
            "operations":{
                VERSION: OPERATIONS,
                VERSION_NEXT: OPERATIONS.iter().chain(NAVIGATION_OPERATIONS.iter()).collect::<Vec<_>>(),
            },
            // Discovery is advisory: a caller that wants 0.0.2 sends 0.0.2.
            "discovery":"advisory",
            // The ledger records; it never grants. No capability attenuation
            // is implemented here, so an attenuated request is refused.
            "capability_attenuation":"unsupported",
            "audit":{
                "entries": ledger.map(|ledger| ledger.entries.iter().map(|e| e.to_json(&id, &profile)).collect::<Vec<_>>()).unwrap_or_default(),
                "count": ledger.map_or(0, |ledger| ledger.entries.len()),
                "limit": MAX_AUDIT_ENTRIES,
                "dropped_total": ledger.map_or(0, |ledger| ledger.dropped),
                "records":"the navigation operations; an entry names an origin, never a path, a query or userinfo, and is evidence rather than authorization",
            },
        }))
    }

    /// The result every navigation operation returns: the identity after the
    /// swap, the committed URL and the bounded history state.
    /// The frames the last committed navigation ended, carried from the swap
    /// to the result that reports it. It is emptied by whichever result reads
    /// it, so a navigation that ended nothing reports an empty list.
    fn take_ended_frames(&mut self) -> Vec<String> {
        std::mem::take(&mut self.ended_frames)
    }

    fn navigation_result(&mut self, id: &str, deadline: Instant) -> Result<Value, ControlError> {
        let policy = self.policy_for_target(id);
        let target = self.target_mut(id)?;
        let revision = Self::revision(target, deadline, &policy)?;
        let history = self
            .histories
            .get(id)
            .ok_or_else(|| {
                ControlError::new("internal", "target has no history", false).scoped("target", id)
            })?
            .to_json();
        let target = self.targets.get(id).expect("target exists");
        let url = target.url.as_ref().map(Url::as_str).ok_or_else(|| {
            ControlError::new("internal", "navigated target has no URL", false).scoped("target", id)
        })?;
        // The navigation result's field set is pinned by the contract, so the
        // frames this navigation ended are not added to it. They are observable
        // through the enumeration and the not_found that follows, and the click
        // path reports them where 0.0.1 already does.
        let result = json!({
            "kind":"navigation","target":id,"frame":target.frame_id,"generation":target.generation,
            "realm":target.realm_id,"revision":revision,"url":url,"history":history,
        });
        self.take_ended_frames();
        Ok(result)
    }

    /// A URL a navigation may address: absolute, `http` or `https`, bounded.
    /// Whether policy allows reaching it is the fetch's decision, not this one.
    fn navigation_url(value: &str, id: &str) -> Result<Url, ControlError> {
        if value.len() > MAX_URL_BYTES {
            return Err(
                ControlError::new("invalid_request", "url exceeds its byte bound", false)
                    .scoped("target", id),
            );
        }
        let url = Url::parse(value).map_err(|_| {
            ControlError::new("invalid_request", "url is not absolute", false).scoped("target", id)
        })?;
        if !matches!(url.scheme(), "http" | "https") {
            return Err(ControlError::new(
                "invalid_request",
                "url scheme is not http or https",
                false,
            )
            .scoped("target", id));
        }
        Ok(url)
    }

    /// The target's current URL; a fixture target has none and cannot be
    /// navigated by URL in this slice.
    fn current_url(&self, id: &str) -> Result<String, ControlError> {
        self.targets
            .get(id)
            .ok_or_else(|| not_found("target", id))?
            .url
            .as_ref()
            .map(Url::to_string)
            .ok_or_else(|| {
                ControlError::new(
                    "unsupported_capability",
                    "this target was opened from a fixture and has no URL to navigate",
                    false,
                )
                .scoped("target", id)
                .details(json!({"reason":"target_has_no_url"}))
            })
    }

    /// `target.navigate`: the document is replaced atomically and the final
    /// committed URL becomes the newest history entry, dropping any forward
    /// entries and evicting the oldest once the window is full.
    fn target_navigate(
        &mut self,
        arguments: &Value,
        deadline: Instant,
    ) -> Result<Value, ControlError> {
        let object = exact_object(arguments, &["target", "url"])?;
        let id = typed_field(object, "target", "target")?.to_owned();
        let requested = object
            .get("url")
            .and_then(Value::as_str)
            .ok_or_else(|| invalid("url must be a string"))?;
        let url = Self::navigation_url(requested, &id)?;
        self.current_url(&id)?;
        let outcome = self.navigate(&id, url.as_str(), deadline);
        let category = outcome
            .as_ref()
            .err()
            .map_or("committed", |error| error.code);
        self.audit_navigation(&id, "target.navigate", Some(url.as_str()), category);
        outcome?;
        let committed = self.current_url(&id)?;
        match self.histories.get_mut(&id) {
            Some(history) => history.commit(&committed),
            None => {
                self.histories.insert(id.clone(), History::new(&committed));
            }
        }
        self.navigation_stage("after_history_audit", &id);
        let result = self.navigation_result(&id, deadline);
        self.navigation_stage("result_built", &id);
        result
    }

    /// `target.reload`: the same document address again. It appends no entry
    /// and does not move the position.
    fn target_reload(
        &mut self,
        arguments: &Value,
        deadline: Instant,
    ) -> Result<Value, ControlError> {
        let object = exact_object(arguments, &["target"])?;
        let id = typed_field(object, "target", "target")?.to_owned();
        let url = self.current_url(&id)?;
        let outcome = self.navigate(&id, &url, deadline);
        let category = outcome
            .as_ref()
            .err()
            .map_or("committed", |error| error.code);
        self.audit_navigation(&id, "target.reload", Some(&url), category);
        outcome?;
        self.navigation_result(&id, deadline)
    }

    /// `target.traverse`: refetch the entry at a signed offset under the
    /// profile's current policy. No page state is restored, because none was
    /// kept; only the position moves.
    fn target_traverse(
        &mut self,
        arguments: &Value,
        deadline: Instant,
    ) -> Result<Value, ControlError> {
        let object = exact_object(arguments, &["target", "delta"])?;
        let id = typed_field(object, "target", "target")?.to_owned();
        let delta = object
            .get("delta")
            .and_then(Value::as_i64)
            .filter(|delta| {
                *delta != 0
                    && usize::try_from(delta.unsigned_abs()).unwrap_or(usize::MAX)
                        <= MAX_HISTORY_ENTRIES
            })
            .ok_or_else(|| invalid("delta must be a non-zero offset within the history window"))?;
        self.current_url(&id)?;
        let (position, url) = self
            .histories
            .get(&id)
            .and_then(|history| history.at(delta))
            .ok_or_else(|| {
                ControlError::new("not_found", "no history entry at that offset", false)
                    .scoped("target", &id)
                    .details(json!({"reason":"history_offset_out_of_window"}))
            })?;
        let outcome = self.navigate(&id, &url, deadline);
        let category = outcome
            .as_ref()
            .err()
            .map_or("committed", |error| error.code);
        self.audit_navigation(&id, "target.traverse", Some(&url), category);
        outcome?;
        if let Some(history) = self.histories.get_mut(&id) {
            history.position = position;
        }
        self.navigation_result(&id, deadline)
    }

    /// Same-frame navigation after a link click: the new document is built
    /// completely (fetch under the target's own policy and budget, parse,
    /// realm, scripts) before anything in the live target changes; on any
    /// failure the target keeps its document, realm, generation and
    /// revision, and only the network budget records the attempt.
    fn navigate(&mut self, id: &str, href: &str, deadline: Instant) -> Result<Value, ControlError> {
        self.navigate_with(id, href, deadline, false)
    }

    /// `sensitive` marks a navigation whose address carries page data, which
    /// is a form's serialised query. Its failures are diagnosed by their typed
    /// reason and the identity alone: no address, no query, no free text that
    /// could hold either.
    /// A link or a GET submit inside a child frame replaces **that child's
    /// document only**. The candidate is fetched, parsed, seeded and
    /// instrumented in full before anything about the live child changes, so a
    /// failure leaves the child's identity, document, state and the target's
    /// revision exactly as they were. It spends the current parent document's
    /// remaining aggregate allowance and never a fresh one, and it touches
    /// neither the parent's identity nor the target's history.
    fn navigate_child(
        &mut self,
        id: &str,
        child: usize,
        href: &str,
        deadline: Instant,
        sensitive: bool,
    ) -> Result<Value, ControlError> {
        let policy = self.policy_for_target(id);
        let now = profile::now_seconds();
        let allocation = self.realm_allocation;
        let refuse = |code: &'static str, reason: &'static str| -> ControlError {
            ControlError::new(code, "the embedded document could not be replaced", false)
                .scoped("target", id)
                .details(json!({"navigation":"failed","redacted":sensitive,"reason":reason}))
        };
        let target = self.target_mut(id)?;
        // Room for the fold, the navigation's own advance and the generation,
        // all checked before the network is touched or a realm is built.
        let counter = target.children[child].counter;
        if counter >= MAX_SAFE_COUNTER
            || target.children[child].generation.checked_add(1).is_none()
            || target
                .revision_base
                .checked_add(counter)
                .and_then(|base| base.checked_add(1))
                .is_none()
        {
            return Err(saturated(id));
        }
        // A child's href resolves against the child's own document, and the
        // origin it must stay inside is the parent document's.
        let Some(parent_url) = target.url.clone() else {
            return Err(refuse("unsupported_capability", "child_has_no_url"));
        };
        let Some(child_url) = target.children[child].url.clone() else {
            return Err(refuse("unsupported_capability", "child_has_no_url"));
        };
        let Ok(resolved) = child_url.join(href) else {
            return Err(refuse("invalid_request", "malformed_href"));
        };
        if !matches!(resolved.scheme(), "http" | "https") {
            return Err(refuse("unsupported_capability", "scheme_not_fetched"));
        }
        if !net::same_origin(&parent_url, &resolved) {
            target.budget.denied += 1;
            return Err(refuse("permission_denied", "cross_origin_src"));
        }
        // The attempt runs on a copy of the jar and against the parent
        // document's own remaining allowance.
        let mut jar = target.io.jar.clone();
        let mut rejections = target.io.cookie_rejections;
        let document_host = parent_url.host_str().map(|h| h.to_ascii_lowercase());
        let tls = target.io.tls.clone();
        let mut budget = std::mem::take(&mut target.budget);
        let response = {
            let mut hooks = JarHooks {
                jar: &mut jar,
                document_host: document_host.as_deref(),
                now,
                rejections: &mut rejections,
            };
            net::fetch_with(
                resolved.as_str(),
                &policy,
                &mut budget,
                deadline,
                Some(&mut hooks),
                tls.as_deref(),
            )
        };
        // The spend is a fact whatever the outcome, so the budget goes back
        // before anything else can fail.
        self.target_mut(id)?.budget = budget;
        let response = match response {
            Ok(response) if response.status >= 400 => {
                return Err(refuse("not_found", "status_not_ok"));
            }
            Ok(response) => response,
            Err(error) => {
                return Err(refuse(
                    if error.code == "resource_limit" {
                        "resource_limit"
                    } else {
                        "permission_denied"
                    },
                    "fetch_failed",
                ));
            }
        };
        if !net::same_origin(&parent_url, &response.url) {
            self.target_mut(id)?.budget.denied += 1;
            return Err(refuse("permission_denied", "cross_origin_redirect"));
        }
        let html = response
            .content_type
            .as_deref()
            .map(|value| {
                value
                    .split(';')
                    .next()
                    .unwrap_or_default()
                    .trim()
                    .eq_ignore_ascii_case("text/html")
            })
            .unwrap_or(false);
        if !html {
            return Err(refuse("unsupported_capability", "not_html"));
        }
        let text = String::from_utf8_lossy(&response.body).into_owned();
        let parsed = Document::from(text.as_str());
        let element_count = parsed.select("*").nodes().len();
        let base_target = parsed
            .select("base")
            .nodes()
            .iter()
            .any(|node| node.attr("target").is_some());
        let mut tree = Vec::new();
        serialize_children(&parsed.root(), &mut tree);
        drop(parsed);
        let built = (|| -> Result<Realm, ControlError> {
            let realm = Realm::new(allocation)?;
            realm.eval(DOM_SHIM_JS, deadline, id)?;
            realm.eval(
                &format!(
                    "__mcsSeed({})",
                    serde_json::to_string(&tree).expect("tree serializes")
                ),
                deadline,
                id,
            )?;
            realm.eval(&location_script(&response.url), deadline, id)?;
            realm.eval("__mcsComplete()", deadline, id)?;
            realm.eval(INSTALL_JS, deadline, id)?;
            Ok(realm)
        })();
        let Ok(realm) = built else {
            return Err(refuse("internal", "realm_build_failed"));
        };
        // Everything that could fail has succeeded, so the swap is atomic
        // from here: the old realm retires, the counter it had folds into the
        // target's base with the one this navigation is worth, and the child
        // keeps its identity.
        let target = self.target_mut(id)?;
        target.io.jar = jar;
        target.io.cookie_rejections = rejections;
        let child_frame = &mut target.children[child];
        let retired_realm = child_frame.realm_id.clone();
        let folded = child_frame.counter;
        child_frame.realm = realm;
        child_frame.generation += 1;
        child_frame.counter = 0;
        child_frame.snapshot = None;
        child_frame.url = Some(response.url.clone());
        child_frame.bytes = response.body.len();
        child_frame.element_count = element_count;
        child_frame.base_target = base_target;
        self.next_realm += 1;
        let realm_id = format!("realm_{}", self.next_realm);
        let target = self.target_mut(id)?;
        target.children[child].realm_id = realm_id.clone();
        target.revision_base = target
            .revision_base
            .checked_add(folded)
            .and_then(|base| base.checked_add(1))
            .expect("the fold was checked before the candidate was built");
        self.realms_retired_total += 1;
        let policy = self.policy_for_target(id);
        let target = self.target_mut(id)?;
        let revision = Self::revision(target, deadline, &policy)?;
        let child_frame = &target.children[child];
        Ok(json!({
            "kind":"action","target":id,"revision":revision,"applied":true,"navigated":true,
            "frame":child_frame.id,"generation":child_frame.generation,"realm":realm_id,
            "retired_realm":retired_realm,"ended_frames":[],
            "url":child_frame.url.as_ref().map(Url::as_str),"fixture":target.fixture,
            "network":{"fetches":target.budget.fetches,"bytes":target.budget.bytes,"denied":target.budget.denied},
        }))
    }

    fn navigate_with(
        &mut self,
        id: &str,
        href: &str,
        deadline: Instant,
        sensitive: bool,
    ) -> Result<Value, ControlError> {
        let policy = self.policy_for_target(id);
        {
            let lifetime = self.lifetimes.entry(id.to_owned()).or_default();
            lifetime.navigation_attempts = lifetime.navigation_attempts.saturating_add(1);
        }
        self.navigation_stage("navigation_entry", id);
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
            if current.checked_add(1).is_none() || target.generation.checked_add(1).is_none() {
                return Err(saturated(id));
            }
            source.map(|source| {
                (
                    target.session_id.clone(),
                    source,
                    target.frame_id.clone(),
                    target.generation,
                    current,
                )
            })
        };
        let built = match prepared {
            Ok((session, source, frame_id, generation, base_revision)) => {
                match self.io_for(&session, None) {
                    Ok(io) => self.build_target(
                        id,
                        &session,
                        source,
                        // A fresh document budget: the candidate never spends
                        // the live document's, and never inherits its spend.
                        net::Budget::default(),
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
        self.navigation_stage("candidate_built", id);
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
                let retired = std::mem::replace(target, replacement);
                let retired_realm = retired.realm_id;
                // The replaced document's children end with it, in reverse
                // order of construction, each realm retired exactly once.
                let ended_frames: Vec<String> = retired
                    .children
                    .iter()
                    .map(|child| child.id.clone())
                    .collect();
                self.realms_retired_total += retired.children.len() as u64;
                self.ended_frames = ended_frames.clone();
                // The replaced document's spend leaves the gate and becomes a
                // lifetime diagnostic; its TLS counters stay attributable.
                let lifetime = self.lifetimes.entry(id.to_owned()).or_default();
                lifetime.retire(&retired.budget);
                lifetime.navigation_commits = lifetime.navigation_commits.saturating_add(1);
                self.tls_retired.absorb_tls(&retired.budget);
                self.realms_retired_total += 1;
                self.navigations_total += 1;
                self.navigation_stage("after_swap", id);
                let target = self.target_mut(id)?;
                let revision = Self::revision(target, deadline, &policy)?;
                Ok(json!({
                    "kind":"action","target":id,"revision":revision,"applied":true,"navigated":true,
                    "frame":target.frame_id,"generation":target.generation,"realm":target.realm_id,
                    "retired_realm":retired_realm,"ended_frames":ended_frames,"url":target.url.as_ref().map(Url::as_str),"fixture":target.fixture,
                    "network":{"fetches":target.budget.fetches,"bytes":target.budget.bytes,"denied":target.budget.denied},
                }))
            }
            Err(error) => {
                // The candidate is discarded whole: the live document keeps
                // its own budget, realm, generation and revision untouched.
                // Only the saturating diagnostic records the refusal.
                let generation = target.generation;
                let realm = target.realm_id.clone();
                let lifetime = self.lifetimes.entry(id.to_owned()).or_default();
                lifetime.navigation_refusals = lifetime.navigation_refusals.saturating_add(1);
                if sensitive {
                    // Only the typed reason and the identity survive, and the
                    // reason is a fixed vocabulary the host itself wrote.
                    let reason = error
                        .details
                        .as_ref()
                        .and_then(|details| details.get("reason"))
                        .and_then(Value::as_str)
                        .unwrap_or("navigation_failed")
                        .to_owned();
                    let message = "the form submission did not navigate";
                    return Err(ControlError::new(error.code, message, error.retryable)
                        .scoped("target", id)
                        .details(json!({
                            "navigation":"failed","redacted":true,"reason":reason,
                            "generation":generation,"realm":realm,
                        })));
                }
                let mut details = error.details.clone().unwrap_or_else(|| json!({}));
                details["navigation"] = json!("failed");
                details["href"] = json!(href);
                details["generation"] = json!(generation);
                details["realm"] = json!(realm);
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
        let policy = self.policy_for_target(&id);
        let target = self.target_mut(&id)?;
        // A foreign, ended or unknown frame or realm is one and the same
        // refusal, whether the target has children or not.
        let selected = match object.get("frame") {
            Some(_) => {
                let frame = typed_field(object, "frame", "frame")?;
                target.frame_index(frame).ok_or_else(|| {
                    not_found("frame", frame).details(json!({"reason":"frame_not_live_in_target"}))
                })?
            }
            None => None,
        };
        let (frame_id, realm_id, generation) = match selected {
            None => (
                target.frame_id.clone(),
                target.realm_id.clone(),
                target.generation,
            ),
            Some(index) => {
                let child = &target.children[index];
                (child.id.clone(), child.realm_id.clone(), child.generation)
            }
        };
        // A realm argument asserts the narrowed frame's own current realm. A
        // realm that is live in another frame of the same target is refused
        // exactly like one that never existed.
        if object.get("realm").is_some() {
            let realm = typed_field(object, "realm", "realm")?;
            if realm != realm_id {
                return Err(not_found("realm", realm)
                    .details(json!({"reason":"realm_not_live_in_target","frame":frame_id})));
            }
        }
        // The revision a snapshot reports is the target's, whichever frame it
        // observed: a node reference is scoped to (target, revision, node).
        // Each frame's own counter is kept beside it, because that is what
        // authorises an action in that frame and nothing else.
        let (raw, frame_counter, revision) = match selected {
            None => {
                let has_base_target = target.base_target;
                let raw = Self::eval_json(
                    target,
                    &snapshot_script(max_nodes, false, has_base_target),
                    deadline,
                    &policy,
                )?;
                let counter = raw.get("revision").and_then(Value::as_u64).unwrap_or(0);
                let revision = Self::revision(target, deadline, &policy)?;
                (raw, counter, revision)
            }
            Some(index) => {
                let has_base_target = target.children[index].base_target;
                let text = target.children[index].realm.eval(
                    &snapshot_script(max_nodes, true, has_base_target),
                    deadline,
                    &id,
                )?;
                let value: Value = serde_json::from_str(&text).map_err(|_| {
                    ControlError::new("internal", "engine returned malformed snapshot JSON", false)
                        .scoped("target", &id)
                })?;
                let counter = value.get("revision").and_then(Value::as_u64).unwrap_or(0);
                // The only place a child's counter can move is under an
                // evaluation in its realm, so the cache is refreshed here,
                // before the global revision is read from it.
                target.children[index].counter = counter;
                let revision = Self::revision(target, deadline, &policy)?;
                (value, counter, revision)
            }
        };
        if raw.get("error").is_some() {
            return Err(ControlError::new(
                "internal",
                "target lost its revision instrumentation",
                false,
            )
            .scoped("target", &id));
        }
        if raw.get("revision").and_then(Value::as_u64).is_none() {
            return Err(
                ControlError::new("internal", "snapshot lacks a revision", false)
                    .scoped("target", &id),
            );
        }
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
            // Target-scoped node ids: each frame's band is disjoint, so a
            // reference taken in a child can never resolve in the main frame.
            let node = entry
                .get("node")
                .and_then(Value::as_str)
                .unwrap_or("node_0")
                .to_owned();
            let node = match selected {
                None => node,
                Some(index) => {
                    let within = node
                        .strip_prefix("node_")
                        .and_then(|n| n.parse::<u64>().ok())
                        .unwrap_or(0);
                    format!("node_{}", NODE_BAND * (index as u64 + 1) + within)
                }
            };
            let mut item = json!({
                "reference":{"target":id,"revision":revision,"node":node},
                "role":entry.get("role").cloned().unwrap_or(Value::Null),
                "name":entry.get("name").cloned().unwrap_or(Value::Null),
            });
            if let Some(value) = entry.get("value") {
                item["value"] = value.clone();
            }
            // The bounded form facts the realm reported. Each is copied as
            // it came; the host keeps no form state of its own.
            for fact in [
                "checked",
                "group",
                "options",
                "selected",
                "method",
                "has_action",
                "controls",
                "disabled",
                "read_only",
                "control_name",
                "activation",
            ] {
                if let Some(value) = entry.get(fact) {
                    item[fact] = value.clone();
                }
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
        let observed = FrameSnapshot {
            reference_revision: revision,
            frame_revision: frame_counter,
            nodes: count,
        };
        let target = self.targets.get_mut(&id).expect("target exists");
        match selected {
            None => target.last_snapshot = Some(observed),
            Some(index) => target.children[index].snapshot = Some(observed),
        }
        Ok(json!({
            "kind":"semantic_snapshot","target":id,"revision":revision,
            "frame":frame_id,"realm":realm_id,"generation":generation,
            "truncated":truncated,"nodes":nodes,
        }))
    }

    /// The bounded shape of each action, checked before the realm sees it.
    fn validate_action(action: &Map<String, Value>, kind: &str) -> Result<(), ControlError> {
        let exact = |fields: &[&str]| -> Result<(), ControlError> {
            let expected: std::collections::BTreeSet<&str> = fields.iter().copied().collect();
            let given: std::collections::BTreeSet<&str> =
                action.keys().map(String::as_str).collect();
            if expected == given {
                Ok(())
            } else {
                Err(invalid("action fields differ"))
            }
        };
        match kind {
            "click" | "submit" => exact(&["kind"]),
            "set_value" => {
                exact(&["kind", "value"])?;
                let value = action
                    .get("value")
                    .and_then(Value::as_str)
                    .ok_or_else(|| invalid("set_value takes a string"))?;
                if value.len() > MAX_FORM_VALUE_BYTES {
                    return Err(ControlError::new(
                        "invalid_request",
                        "value exceeds its byte bound",
                        false,
                    ));
                }
                Ok(())
            }
            "set_checked" => {
                exact(&["kind", "checked"])?;
                action
                    .get("checked")
                    .and_then(Value::as_bool)
                    .map(|_| ())
                    .ok_or_else(|| invalid("set_checked takes a boolean"))
            }
            "select_option" => {
                exact(&["kind", "index"])?;
                action
                    .get("index")
                    .and_then(Value::as_u64)
                    .filter(|index| *index <= MAX_OPTION_INDEX)
                    .map(|_| ())
                    .ok_or_else(|| invalid("select_option index differs"))
            }
            "press" => {
                exact(&["kind", "key"])?;
                match string_field(action, "key")? {
                    "enter" | "space" => Ok(()),
                    _ => Err(invalid("press offers enter and space only")),
                }
            }
            _ => Err(invalid(
                "action kind is not part of the control version it names",
            )),
        }
    }

    fn target_act(
        &mut self,
        arguments: &Value,
        version: &str,
        deadline: Instant,
    ) -> Result<Value, ControlError> {
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
        // The version a request named decides which actions exist. 0.0.1 has
        // the click and nothing else; nothing is inferred from the shape.
        let kind = string_field(action, "kind")?.to_owned();
        let form_action = kind != "click";
        // The host counts UTF-8 bytes from its own validated string. The
        // realm's idea of length is UTF-16 code units and is never trusted.
        let value_bytes = action
            .get("value")
            .and_then(Value::as_str)
            .map(|value| value.len() as u64);
        if form_action && version != VERSION_NEXT {
            return Err(invalid(
                "this action is not part of the control version it names",
            ));
        }
        Self::validate_action(action, &kind)?;
        let index = node
            .strip_prefix("node_")
            .and_then(|s| s.parse::<usize>().ok())
            .filter(|n| *n >= 1)
            .ok_or_else(|| {
                ControlError::new("not_found", "node does not exist", false).scoped("target", &id)
            })?
            - 1;
        // A node id names its frame by the band it lies in. Band 0 is the
        // main frame; band k is the k-th live child.
        let band = index as u64 / NODE_BAND;
        let within = index % NODE_BAND as usize;
        let policy = self.policy_for_target(&id);
        let target = self.target_mut(&id)?;
        let frame: Option<usize> = if band == 0 {
            None
        } else {
            let child = (band - 1) as usize;
            if child >= target.children.len() {
                // A band with no live frame behind it is the same refusal a
                // foreign or ended frame gets.
                return Err(ControlError::new("not_found", "node does not exist", false)
                    .scoped("target", &id));
            }
            Some(child)
        };
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
        // Only the observed frame's own record authorises an index, so a
        // snapshot of one frame can never authorise a node in another, and a
        // replaced document cannot inherit its band's authorisation.
        let observed = match frame {
            None => target.last_snapshot,
            Some(child) => target.children[child].snapshot,
        };
        let frame_counter = match frame {
            None => current.saturating_sub(
                target.revision_base
                    + target
                        .children
                        .iter()
                        .fold(0u64, |sum, child| sum.saturating_add(child.counter)),
            ),
            Some(child) => target.children[child].counter,
        };
        if !observed.is_some_and(|observed| {
            observed.reference_revision == revision
                && observed.frame_revision == frame_counter
                && within < observed.nodes
        }) {
            return Err(
                ControlError::new("not_found", "node does not exist", false).scoped("target", &id)
            );
        }
        let (is_child, has_base_target) = match frame {
            None => (false, target.base_target),
            Some(child) => (true, target.children[child].base_target),
        };
        // Neither limit may be reached silently. A frame whose counter cannot
        // represent one more exact increment, or a target whose global
        // revision cannot advance, refuses here: before any preflight, any
        // event, any fetch and any build.
        if frame_counter >= MAX_SAFE_COUNTER || current.checked_add(1).is_none() {
            return Err(saturated(&id));
        }
        let encoded =
            serde_json::to_string(&Value::Object(action.clone())).expect("action serializes");
        // Phase one: what this activation would do, decided without doing any
        // of it. The signature it answers is page data and stays inside the
        // realm; only its fixed-vocabulary decision reaches a caller.
        let activating = matches!(kind.as_str(), "click" | "press" | "submit");
        let mut signature = String::new();
        if activating {
            let script =
                preflight_script(frame_counter, within, &encoded, is_child, has_base_target);
            let preflight = match frame {
                None => Self::eval_json(target, &script, deadline, &policy)?,
                Some(child) => {
                    let text = target.children[child].realm.eval(&script, deadline, &id)?;
                    serde_json::from_str(&text).map_err(|_| {
                        ControlError::new("internal", "engine returned malformed preflight", false)
                            .scoped("target", &id)
                    })?
                }
            };
            if let Some(current) = preflight.get("current").and_then(Value::as_u64) {
                return Err(ControlError::new(
                    "stale_revision",
                    "node reference revision no longer matches the target",
                    true,
                )
                .scoped("target", &id)
                .details(json!({"reference_revision":revision,"current_revision":current})));
            }
            if preflight.get("missing").is_some() {
                return Err(ControlError::new("not_found", "node does not exist", false)
                    .scoped("target", &id));
            }
            let decision = preflight
                .get("decision")
                .and_then(Value::as_str)
                .unwrap_or("action_unsupported");
            if decision != "allowed" {
                self.audit_action(&id, frame, &kind, "unsupported_capability", None);
                return Err(ControlError::new(
                    "unsupported_capability",
                    "this host does not model that activation",
                    false,
                )
                .scoped("target", &id)
                .details(json!({"reason": decision})));
            }
            signature = preflight
                .get("signature")
                .and_then(Value::as_str)
                .unwrap_or_default()
                .to_owned();
            // Phase one, host half: the URL this activation would navigate to,
            // judged before anything is dispatched. Its text never reaches an
            // error, a record or a receipt.
            if let Some(href) = preflight.get("href").and_then(Value::as_str) {
                let target = self.target_mut(&id)?;
                let document = match frame {
                    None => target.url.clone(),
                    Some(child) => target.children[child].url.clone(),
                };
                let origin = target.url.clone();
                if let Some(document) = document {
                    let Ok(resolved) = document.join(href) else {
                        return Err(ControlError::new(
                            "invalid_request",
                            "the activation's address is malformed",
                            false,
                        )
                        .scoped("target", &id)
                        .details(json!({"reason":"malformed_action"})));
                    };
                    if !matches!(resolved.scheme(), "http" | "https") {
                        return Err(ControlError::new(
                            "unsupported_capability",
                            "this host does not model that activation",
                            false,
                        )
                        .scoped("target", &id)
                        .details(json!({"reason":"scheme_unsupported"})));
                    }
                    if resolved.as_str().len() > MAX_URL_BYTES {
                        self.audit_action(&id, frame, &kind, "resource_limit", None);
                        return Err(ControlError::new(
                            "resource_limit",
                            "the submitted URL exceeds its bound",
                            false,
                        )
                        .scoped("target", &id)
                        .details(json!({"reason":"submitted_url_bytes"})));
                    }
                    // Every live child is same-origin with its parent, so an
                    // activation that would leave that origin is refused here
                    // rather than after the page's handlers have run.
                    if frame.is_some()
                        && origin.is_some_and(|parent| !net::same_origin(&parent, &resolved))
                    {
                        return Err(ControlError::new(
                            "permission_denied",
                            "an embedded document stays inside its parent's origin",
                            false,
                        )
                        .scoped("target", &id)
                        .details(json!({"reason":"cross_origin_action"})));
                    }
                }
            }
        }
        let policy = self.policy_for_target(&id);
        let target = self.target_mut(&id)?;
        let script = if form_action {
            form_action_script(
                frame_counter,
                within,
                &encoded,
                is_child,
                has_base_target,
                &signature,
            )
        } else {
            act_script(frame_counter, within, is_child, has_base_target, &signature)
        };
        let outcome = match frame {
            None => Self::eval_json(target, &script, deadline, &policy)?,
            Some(child) => {
                let text = target.children[child].realm.eval(&script, deadline, &id)?;
                let value: Value = serde_json::from_str(&text).map_err(|_| {
                    ControlError::new("internal", "engine returned malformed action JSON", false)
                        .scoped("target", &id)
                })?;
                // The child's counter can only have moved here, so the cache
                // is refreshed at the one place that can move it.
                let text = target.children[child]
                    .realm
                    .eval(REVISION_JS, deadline, &id)?;
                if let Ok(moved) = text.parse::<u64>() {
                    target.children[child].counter = moved;
                }
                value
            }
        };
        let base = target.revision_base;
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
            let reason = outcome
                .get("reason")
                .and_then(Value::as_str)
                .unwrap_or("action_unsupported")
                .to_owned();
            let message = if form_action {
                "this action does not apply to that control"
            } else {
                "click requires a button or link node"
            };
            self.audit_action(&id, frame, &kind, "unsupported_capability", None);
            return Err(ControlError::new("unsupported_capability", message, false)
                .scoped("target", &id)
                .details(json!({"reason": reason})));
        }
        if outcome.get("absent").is_some() {
            self.audit_action(&id, frame, &kind, "not_found", None);
            return Err(ControlError::new("not_found", "no such option", false)
                .scoped("target", &id)
                .details(json!({"reason": outcome.get("reason").and_then(Value::as_str).unwrap_or("absent")})));
        }
        if let Some(href) = outcome.get("navigate").and_then(Value::as_str) {
            let href = href.to_owned();
            // A submit's query is page data: it is bounded here and never put
            // into an error, a log or a record.
            if let Some(base_url) = self.targets.get(&id).and_then(|t| t.url.as_ref())
                && base_url
                    .join(&href)
                    .map(|url| url.as_str().len())
                    .unwrap_or(0)
                    > MAX_URL_BYTES
            {
                self.audit_action(&id, frame, &kind, "resource_limit", None);
                return Err(ControlError::new(
                    "resource_limit",
                    "the submitted URL exceeds its bound",
                    false,
                )
                .scoped("target", &id)
                .details(json!({"reason": "submitted_url_bytes"})));
            }
            // A form's query is page data: this navigation is diagnosed in
            // sensitive mode, so no href, URL or query reaches an error.
            let navigated = match frame {
                None => self.navigate_with(&id, &href, deadline, form_action),
                Some(child) => self.navigate_child(&id, child, &href, deadline, form_action),
            };
            self.audit_action(
                &id,
                frame,
                &kind,
                navigated
                    .as_ref()
                    .err()
                    .map_or("committed", |error| error.code),
                None,
            );
            // A click keeps exactly the result it has always returned; a form
            // action adds its own two fields to that same shape and takes
            // nothing away.
            let mut navigated = navigated?;
            if form_action {
                navigated["action"] = json!(kind);
                navigated["role"] = outcome.get("role").cloned().unwrap_or(json!("form"));
            }
            return Ok(navigated);
        }
        if outcome.get("default_prevented").and_then(Value::as_bool) == Some(true) {
            // The page canceled the default. The events were dispatched and
            // any handler effects stand, so the revision is read again and
            // reported; the action's own effect did not happen.
            let after = Self::revision(target, deadline, &policy)?;
            self.audit_action(&id, frame, &kind, "default_prevented", value_bytes);
            let mut result = json!({"kind":"action","target":id,"revision":after,
                                    "applied":false,"default_prevented":true});
            if form_action {
                result["action"] = json!(kind);
                result["role"] = outcome.get("role").cloned().unwrap_or(json!(null));
            }
            return Ok(result);
        }
        if outcome.get("applied").and_then(Value::as_bool) != Some(true) {
            return Err(
                ControlError::new("internal", "engine did not confirm the action", false)
                    .scoped("target", &id),
            );
        }
        let after = Self::revision(target, deadline, &policy)?;
        self.audit_action(&id, frame, &kind, "applied", value_bytes);
        let mut result = json!({"kind":"action","target":id,"revision":after,"applied":true});
        if form_action {
            result["action"] = json!(kind);
            result["role"] = outcome.get("role").cloned().unwrap_or(json!(null));
            if let Some(bytes) = value_bytes {
                result["value_bytes"] = json!(bytes);
            }
        }
        Ok(result)
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
        let policy = self.policy_for_target(&id);
        loop {
            self.pump_surfaces(deadline);
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
        // Document owners count every frame's document, children included.
        let fixture_bytes: usize = self.targets.values().map(Target::document_bytes).sum();
        let frames_skipped_total: u64 = self.targets.values().fold(0u64, |total, t| {
            total.saturating_add(t.frames_skipped_total())
        });
        let elements: usize = self
            .targets
            .values()
            .map(|t| t.element_count + t.children.iter().map(|c| c.element_count).sum::<usize>())
            .sum();
        let realm_bytes: usize = self.targets.values().map(Target::realm_malloc_bytes).sum();
        // One frame and one realm per frame, the children included.
        let frame_objects: usize = self.targets.values().map(Target::frame_count).sum();
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
                    "policies":{"offline":self.profiles.values().filter(|p| !p.policy.online).count(),
                        "deny_by_default":self.profiles.values().filter(|p| !p.policy.allow_by_default).count(),
                        "bytes":self.profiles.len() * std::mem::size_of::<ProfilePolicy>(),
                        "permissions_effect":"recorded_only"},
                    "objects":self.profiles.len(),"object_limit":MAX_PROFILES,
                    "persistent":self.profiles.values().filter(|p| p.persistent).count(),
                    "unavailable":self.unavailable_profiles.len(),
                    "bytes":self.profiles.values().map(|p| p.jar.accounted_bytes() + p.storage.accounted_bytes()).sum::<usize>(),
                    "cookies":self.profiles.values().map(|p| p.jar.len()).sum::<usize>(),
                    "storage_keys":self.profiles.values().map(|p| p.storage.keys()).sum::<usize>(),
                    "store":self.key_source.as_ref().map(|k| k.mode.name()),
                    "store_writes_total":self.store_writes_total,
                    "store_bytes_written_total":self.store_bytes_written_total,
                    "cookie_rejections_total":self.cookie_rejections_total,
                    "budgets":profile_budgets(),
                },
                "sessions":{"objects":self.sessions.len(),"object_limit":MAX_SESSIONS,
                    "audit_entries":self.audits.values().map(|l| l.entries.len()).sum::<usize>(),
                    "audit_entry_limit":MAX_AUDIT_ENTRIES,
                    "audit_bytes":self.audits.values().map(Ledger::bytes).sum::<usize>(),
                    "audit_capacity_bytes":self.audits.values().map(Ledger::capacity_bytes).sum::<usize>(),
                    "audit_dropped_total":self.audits.values().map(|l| l.dropped).sum::<u64>()},
                "targets":{"objects":self.targets.len(),"object_limit":MAX_TARGETS,"fixture_bytes":fixture_bytes,"elements":elements,
                    "history_entries":self.histories.values().map(|h| h.entries.len()).sum::<usize>(),
                    "history_entry_limit":MAX_HISTORY_ENTRIES,
                    "history_bytes":self.histories.values().map(History::bytes).sum::<usize>(),
                    "lifetime":self.targets.values().map(|target| {
                        let lifetime = self.lifetimes.get(&target.id).copied().unwrap_or_default();
                        json!({"target":target.id,"network":lifetime.to_json(&target.budget)})
                    }).collect::<Vec<_>>()},
                "frames":{"objects":frame_objects,"object_limit":MAX_TARGETS * MAX_FRAMES_PER_TARGET,"frames_per_target":MAX_FRAMES_PER_TARGET,"skipped_total":frames_skipped_total},
                "realms":{"objects":frame_objects,"retired_total":self.realms_retired_total,"navigations_total":self.navigations_total},
                "adapters":{"objects":self.adapters.len(),"object_limit":MAX_ADAPTERS,"detached_total":self.adapters_detached_total},
                "surfaces":{"objects":self.surfaces.len(),"object_limit":surface::MAX_SURFACES,
                    "bytes":self.surfaces.values().map(|s| s.painting.pixels.mapped_len()).sum::<usize>(),
                    "painter":surface::PAINTER,"binary":self.surface_binary.is_some(),
                    "frame":self.surface_frame_owner(),
                    "process":self.surface_stats.to_json(self.surface_generation, self.surfaces.len())},
                "script_realms":{"objects":frame_objects,"malloc_bytes":realm_bytes,"memory_limit_bytes":REALM_MEMORY_LIMIT,"dedicated_zones":zones,"dedicated_arenas":arenas},
                "network":{"fetches":fetches,"bytes":network_bytes,"denied":denied,"limits":{"redirects":net::MAX_REDIRECTS,"response_bytes":net::MAX_RESPONSE_BYTES,"per_fetch_ms":net::PER_FETCH_TIMEOUT.as_millis() as u64,"pending_per_turn":net::MAX_PENDING_PER_TURN,"fetches_per_document":net::MAX_FETCHES_PER_DOCUMENT,"bytes_per_document":net::MAX_BYTES_PER_DOCUMENT,"allowed_origins":self.policy.allowed_origins.len()},"tls":self.tls_owner()},
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

/// A target's network facts; the TLS counters appear only when the https
/// slice is enabled, so the feature-off shape is unchanged.
fn target_network(budget: &net::Budget, https: bool) -> Value {
    let mut network = json!({"fetches":budget.fetches,"bytes":budget.bytes,"denied":budget.denied});
    if https {
        network["tls"] = budget.tls_json();
    }
    network
}

fn usage() -> ! {
    eprintln!(
        "usage: native-dom-control serve --stdio --fixture-root DIR --config-dir DIR [--allow-origin http://HOST:PORT]... [--cdp-port PORT --ready-file PATH] [--profile-root DIR] [--pinned-root FILE]... [--surface-binary FILE] [--surface-court-file FILE] [--surface-child-mode MODE] [--surface-court-frame WxH] [--surface-court-stages 1] [--surface-court-snapshot-arm ARM] [--surface-court-gc 1] [--visual 1 (needs MINICON_SURF_ALLOW_VISIBLE_COURT=1)]"
    );
    std::process::exit(64);
}

fn main() -> Result<(), Box<dyn Error>> {
    let arguments: Vec<String> = std::env::args().skip(1).collect();
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
    let mut pinned_roots: Vec<PathBuf> = Vec::new();
    let mut surface_binary: Option<PathBuf> = None;
    let mut surface_court_path: Option<PathBuf> = None;
    let mut surface_child_mode: Option<String> = None;
    let mut surface_frame = surface::FrameSize::DEFAULT;
    let mut surface_stages = false;
    let mut court_child_build_failure = false;
    let mut court_revision_base = 0u64;
    let mut court_frame_counter = 0u64;
    let mut surface_snapshot_arm: Option<String> = None;
    let mut surface_court_gc = false;
    let mut visual_flag = false;
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
            "--pinned-root" => pinned_roots.push(PathBuf::from(&pair[1])),
            "--surface-binary" if surface_binary.is_none() => {
                surface_binary = Some(PathBuf::from(&pair[1]))
            }
            "--surface-court-file" if surface_court_path.is_none() => {
                surface_court_path = Some(PathBuf::from(&pair[1]))
            }
            // Court-only knobs for the attribution court; they change nothing
            // unless given and are documented as such.
            "--surface-child-mode" if surface_child_mode.is_none() => {
                // Only the no-AppKit modes exist; anything else is a usage error.
                if !surface::is_headless_child_mode(&pair[1]) {
                    usage();
                }
                surface_child_mode = Some(pair[1].clone())
            }
            "--surface-court-frame" => {
                surface_frame = surface::FrameSize::parse(&pair[1]).unwrap_or_else(|| usage())
            }
            "--surface-court-stages" => surface_stages = pair[1] == "1",
            "--court-child-build-failure" => court_child_build_failure = pair[1] == "1",
            "--court-revision-base" => {
                court_revision_base = pair[1].parse::<u64>().unwrap_or_else(|_| usage())
            }
            "--court-frame-counter" => {
                court_frame_counter = pair[1].parse::<u64>().unwrap_or_else(|_| usage())
            }
            "--surface-court-snapshot-arm" => {
                if !matches!(
                    pair[1].as_str(),
                    "evaluate_only" | "parse_drop" | "microbench_flat" | "microbench_nested"
                ) {
                    usage();
                }
                surface_snapshot_arm = Some(pair[1].clone());
            }
            "--surface-court-gc" => surface_court_gc = pair[1] == "1",
            "--visual" => visual_flag = pair[1] == "1",
            _ => usage(),
        }
    }
    if cdp_port.is_some() != ready_file.is_some() {
        usage();
    }
    // The surface binary must be an absolute, existing file: the spawn path
    // is fixed here once and never derived from anything else.
    let surface_binary = match surface_binary {
        Some(path) if path.is_absolute() && path.is_file() => Some(path),
        Some(_) => {
            eprintln!("--surface-binary: must be an absolute path to an existing file");
            std::process::exit(64);
        }
        None => None,
    };
    let surface_court = match surface_court_path {
        Some(path) => match surface::CourtLog::create(path) {
            Ok(log) => Some(log),
            Err(error) => {
                eprintln!("--surface-court-file: {}", error.kind());
                std::process::exit(64);
            }
        },
        None => None,
    };
    // The https slice exists only with explicitly pinned public roots; the
    // ring provider is selected inside `load_pinned_roots` and nowhere else.
    let tls_roots = if pinned_roots.is_empty() {
        None
    } else {
        match net::load_pinned_roots(&pinned_roots) {
            Ok(roots) => {
                policy.https = true;
                Some(roots)
            }
            Err(message) => {
                eprintln!("--pinned-root: {message}");
                std::process::exit(64);
            }
        }
    };
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
    // Visible windows: fail closed unless both the flag and the environment
    // agree; a flag without the environment is a configuration error, not a
    // silent downgrade.
    let visual_env = std::env::var_os("MINICON_SURF_ALLOW_VISIBLE_COURT").as_deref()
        == Some(std::ffi::OsStr::new("1"));
    if visual_flag && !visual_env {
        eprintln!(
            "--visual 1 needs MINICON_SURF_ALLOW_VISIBLE_COURT=1 in the environment; refusing to start"
        );
        std::process::exit(2);
    }
    let surface_visual = visual_flag && visual_env;

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
        ended_frames: Vec::new(),
        realms_retired_total: 0,
        navigations_total: 0,
        histories: BTreeMap::new(),
        lifetimes: BTreeMap::new(),
        audits: BTreeMap::new(),
        next_audit_sequence: 0,
        current_deadline_ms: 0,
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
        tls_roots,
        tls_retired: net::Budget::default(),
        surface_binary,
        surfaces: BTreeMap::new(),
        next_surface: 0,
        surface_generation: 0,
        surface_stats: surface::Stats::default(),
        surface_court,
        surface_child_mode,
        surface_frame,
        surface_stages,
        court_child_build_failure,
        court_revision_base,
        court_frame_counter,
        surface_snapshot_arm,
        surface_court_gc,
        surface_visual,
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
        // Surface input is the third source: applied while idle, FIFO per
        // surface, between operations.
        host.pump_surfaces(Instant::now() + Duration::from_millis(250));
        let line = match line_receiver.try_recv() {
            Ok(line) => line,
            Err(std::sync::mpsc::TryRecvError::Empty) => {
                std::thread::sleep(Duration::from_millis(1));
                continue;
            }
            Err(std::sync::mpsc::TryRecvError::Disconnected) => break,
        };
        // Court-only request stages (`--surface-court-stages 1`): the line
        // bytes alive, the request parsed, executed, serialized, the request
        // dropped, the response written and dropped.
        host.request_stage("request_read", None);
        let mut operation: Option<String> = None;
        let response = match line {
            Line::Eof => break,
            Line::Oversized => envelope(
                "req_invalid",
                VERSION,
                Err(invalid("request exceeds byte limit")),
            ),
            Line::Bytes(bytes) if bytes.is_empty() => {
                envelope("req_invalid", VERSION, Err(invalid("request is empty")))
            }
            Line::Bytes(bytes) => match parse_request(&bytes) {
                Ok(request) => {
                    if host.surface_stages {
                        operation = Some(request.operation.clone());
                    }
                    host.request_stage("request_parsed", operation.as_deref());
                    let body = host.execute(&request);
                    host.request_stage("after_execute", operation.as_deref());
                    let serialized = envelope(&request.request_id, &request.version, body);
                    host.request_stage("response_serialized", operation.as_deref());
                    serialized
                }
                Err(error) => envelope(&error.0, VERSION, Err(error.1)),
            },
        };
        host.request_stage("request_dropped", operation.as_deref());
        out.write_all(&response)?;
        out.write_all(b"\n")?;
        out.flush()?;
        host.request_stage("response_written", operation.as_deref());
        drop(response);
        host.request_stage("response_dropped", operation.as_deref());
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
        // Process-global counter shared with parallel tests: a lower bound only.
        assert!(ZONES_DESTROYED.load(Ordering::Relaxed) > destroyed);
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

#[cfg(test)]
mod navigation_tests {
    use super::*;

    /// The per-document budget gates; the lifetime totals only count, and they
    /// saturate rather than wrap.
    #[test]
    fn lifetime_diagnostics_saturate_and_never_gate() {
        let mut lifetime = Lifetime::default();
        let spent = net::Budget {
            fetches: 30,
            bytes: 1024,
            ..net::Budget::default()
        };
        lifetime.retire(&spent);
        lifetime.retire(&spent);
        lifetime.navigation_attempts = 3;
        lifetime.navigation_commits = 2;
        lifetime.navigation_refusals = 1;
        let active = net::Budget {
            fetches: 2,
            bytes: 16,
            ..net::Budget::default()
        };
        let reported = lifetime.to_json(&active);
        assert_eq!(reported["fetches_total"], json!(62));
        assert_eq!(reported["bytes_total"], json!(2064));
        assert_eq!(reported["navigation_attempts_total"], json!(3));
        assert_eq!(reported["navigation_commits_total"], json!(2));
        assert_eq!(reported["navigation_refusals_total"], json!(1));
        assert_eq!(reported["gates"], json!(false), "diagnostics never gate");
        // The totals are far above one document's gate and change nothing.
        assert!(reported["fetches_total"].as_u64().unwrap() > net::MAX_FETCHES_PER_DOCUMENT as u64);
        lifetime.retired_fetches = u64::MAX;
        lifetime.retire(&spent);
        assert_eq!(
            lifetime.retired_fetches,
            u64::MAX,
            "saturating, never wrapping"
        );
    }

    /// A window of eight bounded URLs: a commit drops the forward entries and
    /// evicts the oldest; nothing but URLs and a position is kept.
    #[test]
    fn history_is_a_bounded_window_of_urls() {
        let mut history = History::new("http://127.0.0.1/a");
        assert_eq!(history.to_json()["length"], json!(1));
        assert_eq!(history.to_json()["can_go_back"], json!(false));
        for index in 0..MAX_HISTORY_ENTRIES {
            history.commit(&format!("http://127.0.0.1/{index}"));
        }
        assert_eq!(
            history.entries.len(),
            MAX_HISTORY_ENTRIES,
            "the window is capped"
        );
        assert_eq!(
            history.to_json()["position"],
            json!(MAX_HISTORY_ENTRIES - 1)
        );
        assert_eq!(history.to_json()["can_go_forward"], json!(false));
        assert_eq!(
            history.entries[0], "http://127.0.0.1/0",
            "the first entry was evicted once the window filled"
        );
        let (position, url) = history.at(-2).expect("two back");
        assert_eq!(position, MAX_HISTORY_ENTRIES - 3);
        assert_eq!(url, history.entries[MAX_HISTORY_ENTRIES - 3]);
        assert!(history.at(1).is_none(), "past the newest entry");
        assert!(
            history.at(-(MAX_HISTORY_ENTRIES as i64)).is_none(),
            "before the oldest entry"
        );
        // Committing from a back position truncates what was ahead.
        history.position = 2;
        history.commit("http://127.0.0.1/new");
        assert_eq!(history.position, 3);
        assert_eq!(history.entries.len(), 4);
        assert_eq!(history.to_json()["can_go_forward"], json!(false));
        assert!(history.bytes() > 0 && history.bytes() < 1024 * MAX_HISTORY_ENTRIES);
    }
}

#[cfg(test)]
mod revision_tests {
    use super::*;

    /// A target with `children` children, each holding `counter`, on `base`.
    fn target_with(base: u64, counters: &[u64]) -> Target {
        let mut target = Target {
            id: "target_1".into(),
            session_id: "session_1".into(),
            fixture: "court".into(),
            url: None,
            document_framing: "fixture",
            fixture_bytes: 0,
            element_count: 0,
            script_count: 0,
            skipped_scripts: Vec::new(),
            budget: net::Budget::default(),
            realm: Realm::new(RealmAllocation::System).expect("a realm"),
            last_snapshot: None,
            base_target: false,
            frame_id: "frame_1".into(),
            generation: 1,
            realm_id: "realm_1".into(),
            revision_base: base,
            io: TargetIo {
                jar: profile::Jar::default(),
                storage: profile::Storage::default(),
                origin: profile::OPAQUE_ORIGIN.to_owned(),
                document_host: None,
                cookie_rejections: 0,
                tls: None,
            },
            scroll_y: 0,
            children: Vec::new(),
            frames_skipped: [0; FRAME_SKIP_REASONS.len()],
        };
        for (index, counter) in counters.iter().enumerate() {
            target.children.push(ChildFrame {
                id: format!("frame_{}", index + 2),
                generation: 1,
                realm_id: format!("realm_{}", index + 2),
                realm: Realm::new(RealmAllocation::System).expect("a realm"),
                counter: *counter,
                snapshot: None,
                base_target: false,
                url: None,
                bytes: 0,
                element_count: 0,
            });
        }
        target
    }

    #[test]
    fn the_global_revision_is_the_base_plus_every_live_frame() {
        let target = target_with(10, &[3, 4]);
        assert_eq!(target.global_revision(5), Some(22));
        assert_eq!(
            target_with(0, &[]).global_revision(0),
            Some(0),
            "an untouched target reads zero"
        );
    }

    #[test]
    fn a_sum_that_would_not_fit_is_refused_rather_than_saturated() {
        let target = target_with(u64::MAX - 1, &[1]);
        assert_eq!(
            target.global_revision(0),
            Some(u64::MAX),
            "a read at the maximum is a real answer"
        );
        assert_eq!(
            target.global_revision(1),
            None,
            "and one past it is refused"
        );
        let many = target_with(u64::MAX - 3, &[1, 1, 1]);
        assert_eq!(many.global_revision(0), Some(u64::MAX));
        assert_eq!(
            many.global_revision(1),
            None,
            "a parent replacement folding several children is checked, not saturated"
        );
    }

    #[test]
    fn the_frame_counter_limit_is_the_realms_and_it_comes_first() {
        assert_eq!(MAX_SAFE_COUNTER, (1u64 << 53) - 1);
        // The realm's Number gives out long before the host's u64, which is
        // the whole reason there are two limits.
        assert_eq!(u64::MAX / MAX_SAFE_COUNTER, 2048);
        let target = target_with(0, &[]);
        assert_eq!(
            target.global_revision(MAX_SAFE_COUNTER),
            Some(MAX_SAFE_COUNTER),
            "a counter at its own limit is still an exact global revision"
        );
    }
}

#[cfg(test)]
mod ledger_tests {
    use super::*;

    /// The ring keeps its capacity, evicts the oldest, counts the drops and
    /// shares the values that repeat, without changing what a record says.
    #[test]
    fn the_ledger_is_a_preallocated_ring_that_shares_repeats() {
        let mut ledger = Ledger::new();
        let reserved = ledger.entries.capacity();
        assert!(reserved >= MAX_AUDIT_ENTRIES, "the ring is reserved once");
        for sequence in 0..(MAX_AUDIT_ENTRIES as u64 + 10) {
            let target = Ledger::share(&mut ledger.targets, "target_1", MAX_TARGETS);
            let origin =
                Ledger::share(&mut ledger.origins, "http://127.0.0.1:1", MAX_AUDIT_ORIGINS);
            ledger.append(AuditEntry {
                sequence,
                deadline_ms: 15000,
                value_bytes: None,
                target,
                frame: None,
                origin: Some(origin),
                operation: "target.navigate",
                outcome: "committed",
            });
        }
        assert_eq!(
            ledger.entries.len(),
            MAX_AUDIT_ENTRIES,
            "capacity unchanged"
        );
        assert_eq!(ledger.entries.capacity(), reserved, "the ring never grew");
        assert_eq!(ledger.dropped, 10, "every eviction is counted");
        assert_eq!(ledger.entries.front().expect("oldest").sequence, 10);
        assert_eq!(
            ledger.entries.back().expect("newest").sequence,
            MAX_AUDIT_ENTRIES as u64 + 9
        );
        // The repeated target and origin were allocated once, not per record.
        assert_eq!(ledger.targets.len(), 1);
        assert_eq!(ledger.origins.len(), 1);
        assert_eq!(
            std::rc::Rc::strong_count(&ledger.targets[0]),
            MAX_AUDIT_ENTRIES + 1
        );
        // A record still says exactly what it said before.
        let rendered = ledger
            .entries
            .back()
            .unwrap()
            .to_json("session_1", "profile_1");
        assert_eq!(rendered["session"], json!("session_1"));
        assert_eq!(rendered["profile"], json!("profile_1"));
        assert_eq!(rendered["target"], json!("target_1"));
        assert_eq!(rendered["operation"], json!("target.navigate"));
        assert_eq!(rendered["origin"], json!("http://127.0.0.1:1"));
        assert_eq!(rendered["outcome"], json!("committed"));
        assert_eq!(rendered["deadline_ms"], json!(15000));
        assert_eq!(rendered["result_bytes_limit"], json!(MAX_RESPONSE_BYTES));
        assert_eq!(rendered["frame"], json!(null));
        assert_eq!(
            rendered.as_object().map(|fields| fields.len()),
            Some(11),
            "the nine of the navigation record, the value's byte length, and the frame an action touched"
        );
        assert_eq!(
            rendered["value_bytes"],
            json!(null),
            "a navigation record carries no value length"
        );
        // The reserved ring is reported, so sharing hides nothing.
        assert!(ledger.capacity_bytes() >= MAX_AUDIT_ENTRIES * std::mem::size_of::<AuditEntry>());
        assert!(ledger.bytes() >= ledger.capacity_bytes());
    }

    /// Beyond the sharing table a record owns its value; it is never dropped
    /// and never renamed.
    #[test]
    fn origins_beyond_the_table_are_owned_not_lost() {
        let mut ledger = Ledger::new();
        for index in 0..(MAX_AUDIT_ORIGINS + 4) {
            let origin = Ledger::share(
                &mut ledger.origins,
                &format!("http://127.0.0.1:{index}"),
                MAX_AUDIT_ORIGINS,
            );
            let target = Ledger::share(&mut ledger.targets, "target_1", MAX_TARGETS);
            ledger.append(AuditEntry {
                sequence: index as u64,
                deadline_ms: 1,
                value_bytes: None,
                target,
                frame: None,
                origin: Some(origin),
                operation: "target.traverse",
                outcome: "not_found",
            });
        }
        assert_eq!(
            ledger.origins.len(),
            MAX_AUDIT_ORIGINS,
            "the table is bounded"
        );
        let newest = ledger.entries.back().expect("newest");
        assert_eq!(
            newest.to_json("session_1", "profile_1")["origin"],
            json!(format!("http://127.0.0.1:{}", MAX_AUDIT_ORIGINS + 3)),
            "an origin past the table is still reported exactly"
        );
        assert!(
            ledger.bytes() > ledger.capacity_bytes(),
            "owned bytes are accounted"
        );
    }
}
