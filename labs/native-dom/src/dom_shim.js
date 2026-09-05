// Minimal DOM for the native script-realm slice. It implements only what the
// court fixtures and the shared control instrumentation use, and nothing is
// emulated beyond that: unsupported selectors throw, timers run as microtasks.
(() => {
  const g = globalThis;
  const observers = [];
  let flushScheduled = false;
  function contains(a, b) { for (let n = b; n; n = n.parentNode) if (n === a) return true; return false; }
  function scheduleFlush() {
    if (flushScheduled) return;
    flushScheduled = true;
    Promise.resolve().then(() => {
      flushScheduled = false;
      for (const o of observers) {
        if (!o.records.length) continue;
        const records = o.records; o.records = [];
        try { o.callback(records, o.observer); } catch (e) {}
      }
    });
  }
  function record(type, target, extra) {
    for (const o of observers) {
      const inScope = o.target === target || (o.options.subtree && contains(o.target, target));
      if (!inScope) continue;
      if (type === "childList" && !o.options.childList) continue;
      if (type === "attributes" && !o.options.attributes) continue;
      if (type === "characterData" && !o.options.characterData) continue;
      o.records.push(Object.assign({ type, target }, extra));
    }
    scheduleFlush();
  }
  class Event {
    constructor(type, init = {}) { this.type = type; this.bubbles = !!init.bubbles; this.cancelable = !!init.cancelable; this.defaultPrevented = false; this.target = null; this.currentTarget = null; this.__stop = false; }
    preventDefault() { if (this.cancelable) this.defaultPrevented = true; }
    stopPropagation() { this.__stop = true; }
  }
  class MutationObserver {
    constructor(callback) { this.__callback = callback; this.__entries = []; }
    observe(target, options = {}) { const e = { target, options, callback: this.__callback, records: [], observer: this }; observers.push(e); this.__entries.push(e); }
    disconnect() { for (const e of this.__entries) { const i = observers.indexOf(e); if (i >= 0) observers.splice(i, 1); } this.__entries = []; }
    takeRecords() { const out = []; for (const e of this.__entries) { out.push(...e.records); e.records = []; } return out; }
  }
  class Node {
    constructor() { this.parentNode = null; this.childNodes = []; }
    get isConnected() { for (let n = this; n; n = n.parentNode) if (n.nodeType === 9) return true; return false; }
    get firstChild() { return this.childNodes[0] || null; }
    get lastChild() { return this.childNodes[this.childNodes.length - 1] || null; }
    get children() { return this.childNodes.filter((n) => n.nodeType === 1); }
    get parentElement() { return this.parentNode && this.parentNode.nodeType === 1 ? this.parentNode : null; }
    get textContent() { return this.nodeType === 3 ? this.data : this.childNodes.map((c) => c.textContent).join(""); }
    set textContent(value) {
      if (this.nodeType === 3) { const oldValue = this.data; this.data = String(value); record("characterData", this, { oldValue }); return; }
      const removedNodes = this.childNodes.splice(0); for (const c of removedNodes) c.parentNode = null;
      const text = new Text(String(value)); text.parentNode = this; this.childNodes.push(text);
      record("childList", this, { addedNodes: [text], removedNodes });
    }
    contains(other) { return contains(this, other); }
    __detach(node) { const i = this.childNodes.indexOf(node); if (i >= 0) this.childNodes.splice(i, 1); node.parentNode = null; }
    append(...nodes) {
      const addedNodes = [];
      for (let node of nodes) {
        if (!(node instanceof Node)) node = new Text(String(node));
        if (node.parentNode) node.parentNode.__detach(node);
        node.parentNode = this; this.childNodes.push(node); addedNodes.push(node);
      }
      record("childList", this, { addedNodes, removedNodes: [] });
    }
    appendChild(node) { this.append(node); return node; }
    removeChild(node) { if (node.parentNode !== this) throw new Error("NotFoundError"); this.__detach(node); record("childList", this, { addedNodes: [], removedNodes: [node] }); return node; }
    remove() { if (this.parentNode) this.parentNode.removeChild(this); }
    replaceChildren(...nodes) {
      const removedNodes = this.childNodes.splice(0); for (const c of removedNodes) c.parentNode = null;
      const addedNodes = [];
      for (let node of nodes) { if (!(node instanceof Node)) node = new Text(String(node)); if (node.parentNode) node.parentNode.__detach(node); node.parentNode = this; this.childNodes.push(node); addedNodes.push(node); }
      record("childList", this, { addedNodes, removedNodes });
    }
    addEventListener(type, fn) { addListener(this, type, fn); }
    removeEventListener(type, fn) { removeListener(this, type, fn); }
    dispatchEvent(event) { return dispatchOn(this, event); }
    __descendants(out = []) { for (const c of this.childNodes) { if (c.nodeType === 1) { out.push(c); c.__descendants(out); } } return out; }
    querySelectorAll(selector) { const chain = parseSelector(selector); return this.__descendants().filter((el) => matchChain(el, chain, this)); }
    querySelector(selector) { const chain = parseSelector(selector); for (const el of this.__descendants()) if (matchChain(el, chain, this)) return el; return null; }
  }
  // The listener model, shared by every event target. A listener is a
  // function in a list, and the same target, type and function identity is
  // registered once. There are no options and no handleEvent objects, which
  // the design records as divergences rather than hiding.
  // Closure-owned, keyed by the target: a page cannot replace, read or
  // corrupt the store by assigning a property, and a target that dies takes
  // its listeners with it.
  const listenerStore = new WeakMap();
  function listenersOf(target) {
    let map = listenerStore.get(target);
    if (!map) { map = new Map(); listenerStore.set(target, map); }
    return map;
  }
  function addListener(target, type, fn) {
    if (typeof fn !== "function") return;
    const map = listenersOf(target);
    if (!map.has(type)) map.set(type, []);
    const list = map.get(type);
    // The same target, type and function identity is registered once: a
    // repeat is a no-op, as the standard has it within this host's bounds.
    if (list.indexOf(fn) >= 0) return;
    list.push(fn);
  }
  function removeListener(target, type, fn) {
    const list = listenersOf(target).get(type);
    if (!list) return;
    const at = list.indexOf(fn);
    if (at >= 0) list.splice(at, 1);
  }
  // The path is the node chain, and then — only for an event that bubbles —
  // the window, which is the document's parent *event target* and never a
  // parentNode: nothing is appended to the tree and document.parentNode
  // stays null.
  function dispatchOn(target, event) {
    event.target = target;
    const path = [];
    if (target && typeof target.nodeType === "number") {
      for (let n = target; n; n = n.parentNode) path.push(n);
      // Only a path that actually reached the document continues to the
      // window: a detached subtree bubbles through its own ancestors and
      // stops there, as the standard has it.
      if (event.bubbles && path[path.length - 1] === document) path.push(g);
    } else {
      path.push(target);
    }
    for (const node of path) {
      event.currentTarget = node;
      const list = listenersOf(node).get(event.type);
      if (list) for (const fn of [...list]) { try { fn.call(node, event); } catch (error) {} }
      if (event.__stop || !event.bubbles) break;
    }
    event.currentTarget = null;
    return !event.defaultPrevented;
  }

  class Text extends Node { constructor(data) { super(); this.nodeType = 3; this.nodeName = "#text"; this.data = String(data); } }
  class Element extends Node {
    constructor(tag) {
      super(); this.nodeType = 1; this.localName = String(tag).toLowerCase(); this.tagName = this.localName.toUpperCase(); this.nodeName = this.tagName;
      this.__attrs = new Map(); this.__value = null;
      this.dataset = new Proxy({}, {
        get: (_, key) => typeof key === "string" ? this.getAttribute("data-" + kebab(key)) ?? undefined : undefined,
        set: (_, key, value) => { this.setAttribute("data-" + kebab(key), String(value)); return true; },
        has: (_, key) => this.hasAttribute("data-" + kebab(key)),
      });
    }
    getAttribute(name) { const v = this.__attrs.get(String(name).toLowerCase()); return v === undefined ? null : v; }
    hasAttribute(name) { return this.__attrs.has(String(name).toLowerCase()); }
    setAttribute(name, value) { name = String(name).toLowerCase(); const oldValue = this.getAttribute(name); this.__attrs.set(name, String(value)); record("attributes", this, { attributeName: name, oldValue }); }
    removeAttribute(name) { name = String(name).toLowerCase(); if (!this.__attrs.has(name)) return; const oldValue = this.getAttribute(name); this.__attrs.delete(name); record("attributes", this, { attributeName: name, oldValue }); }
    get attributes() { return [...this.__attrs].map(([name, value]) => ({ name, value })); }
    get id() { return this.getAttribute("id") ?? ""; } set id(v) { this.setAttribute("id", v); }
    get className() { return this.getAttribute("class") ?? ""; } set className(v) { this.setAttribute("class", v); }
    get name() { return this.getAttribute("name") ?? ""; }
    get href() { return this.getAttribute("href") ?? ""; }
    get type() { const t = this.getAttribute("type"); if (t) return t.toLowerCase(); return this.localName === "button" ? "submit" : this.localName === "input" ? "text" : ""; }
    get value() {
      if (this.localName === "select") { const o = this.__options()[this.selectedIndex]; return o ? o.value : ""; }
      if (this.__value !== null) return this.__value;
      return this.localName === "textarea" ? this.textContent : (this.getAttribute("value") ?? "");
    }
    set value(v) { this.__value = String(v); }
    // The form model: the realm is the only authority over this state, so
    // every property below is derived from the tree or from a field this
    // element owns, never from anything outside the realm.
    get disabled() { return this.hasAttribute("disabled"); }
    get readOnly() { return this.hasAttribute("readonly"); }
    get defaultValue() { return this.localName === "textarea" ? this.textContent : (this.getAttribute("value") ?? ""); }
    get defaultChecked() { return this.hasAttribute("checked"); }
    get checked() { return this.__checked === undefined ? this.defaultChecked : this.__checked; }
    set checked(v) {
      const on = !!v;
      if (on && this.localName === "input" && this.type === "radio") {
        for (const other of this.__radioGroup()) { if (other !== this) other.__checked = false; }
      }
      this.__checked = on;
    }
    // The bounded checked state of a radio's whole group, so a canceled
    // change can put back the sibling that was cleared, not just this one.
    __groupState() {
      const group = this.__radioGroup();
      const whole = group.includes(this) ? group : [this, ...group];
      return whole.map((el) => [el, !!el.checked]);
    }
    __restoreGroup(state) { for (const [el, was] of state) el.__checked = was; }
    __radioGroup() {
      const name = this.getAttribute("name");
      if (!name) return [this];
      const scope = this.form || g.document;
      const all = scope === g.document ? g.document.querySelectorAll("input") : scope.__controls();
      return [...all].filter((el) => el.localName === "input" && el.type === "radio" && el.getAttribute("name") === name);
    }
    get form() { for (let n = this.parentNode; n; n = n.parentNode) if (n.localName === "form") return n; return null; }
    __options() { return this.localName === "select" ? [...this.querySelectorAll("option")] : []; }
    get options() { return this.__options(); }
    get selectedIndex() {
      const options = this.__options();
      if (this.__selected !== undefined) return this.__selected;
      const marked = options.findIndex((o) => o.hasAttribute("selected"));
      return marked >= 0 ? marked : (options.length ? 0 : -1);
    }
    set selectedIndex(index) { this.__selected = Number(index); }
    get selected() {
      const select = (() => { for (let n = this.parentNode; n; n = n.parentNode) if (n.localName === "select") return n; return null; })();
      if (!select) return this.hasAttribute("selected");
      return select.__options().indexOf(this) === select.selectedIndex;
    }
    get label() { return this.getAttribute("label") ?? this.textContent; }
    __controls() {
      return [...this.querySelectorAll("input")]
        .concat([...this.querySelectorAll("textarea")])
        .concat([...this.querySelectorAll("select")])
        .concat([...this.querySelectorAll("button")]);
    }
    get elements() { return this.localName === "form" ? this.__controls() : []; }
    // A reset asks before it mutates: the cancelable event goes first and the
    // controls are restored only if the page did not cancel it. Returns
    // whether anything was restored.
    reset() {
      const ev = new Event("reset", { bubbles: true, cancelable: true });
      this.dispatchEvent(ev);
      if (ev.defaultPrevented) return false;
      for (const el of this.__controls()) {
        el.__value = null;
        el.__checked = undefined;
        el.__selected = undefined;
      }
      return true;
    }
    submit() { return this.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true })); }
    get innerText() { return this.textContent; }
    matches(selector) { return matchChain(this, parseSelector(selector), null); }
    click() {
      if (this.type === "reset" && (this.localName === "input" || this.localName === "button")) {
        const form = this.form;
        const ev = new Event("click", { bubbles: true, cancelable: true });
        this.dispatchEvent(ev);
        if (!ev.defaultPrevented && form) form.reset();
        return;
      }
      this.dispatchEvent(new Event("click", { bubbles: true, cancelable: true }));
    }
    focus() {} blur() {}
  }
  class Document extends Node {
    constructor() { super(); this.nodeType = 9; this.nodeName = "#document"; this.readyState = "loading"; }
    get documentElement() { return this.children.find((c) => c.localName === "html") || null; }
    get head() { const h = this.documentElement; return h ? h.children.find((c) => c.localName === "head") || null : null; }
    get body() { const h = this.documentElement; return h ? h.children.find((c) => c.localName === "body") || null : null; }
    get title() { const t = this.querySelector("title"); return t ? t.textContent : ""; }
    createElement(tag) { return new Element(tag); }
    createTextNode(data) { return new Text(data); }
    getElementById(id) { id = String(id); for (const el of this.__descendants()) if (el.getAttribute("id") === id) return el; return null; }
  }
  function kebab(key) { return String(key).replace(/[A-Z]/g, (m) => "-" + m.toLowerCase()); }
  const selectorCache = new Map();
  function parseSelector(selector) {
    selector = String(selector).trim();
    if (selectorCache.has(selector)) return selectorCache.get(selector);
    if (!selector || selector.includes(",") || selector.includes(">") || selector.includes("+") || selector.includes("~") || selector.includes(":")) {
      throw new Error("SyntaxError: selector not supported by the native DOM slice: " + selector);
    }
    const chain = selector.split(/\s+/).map((compound) => {
      const parts = []; const re = /(\*|[a-zA-Z][\w-]*)|#([\w-]+)|\.([\w-]+)|\[([\w-]+)(?:=("[^"]*"|'[^']*'|[^\]]+))?\]/g;
      let m; let consumed = 0;
      while ((m = re.exec(compound))) {
        if (m.index !== consumed) throw new Error("SyntaxError: selector not supported by the native DOM slice: " + selector);
        consumed = re.lastIndex;
        if (m[1]) parts.push({ kind: "tag", value: m[1].toLowerCase() });
        else if (m[2]) parts.push({ kind: "id", value: m[2] });
        else if (m[3]) parts.push({ kind: "class", value: m[3] });
        else parts.push({ kind: "attr", name: m[4].toLowerCase(), value: m[5] === undefined ? null : m[5].replace(/^["']|["']$/g, "") });
      }
      if (consumed !== compound.length || !parts.length) throw new Error("SyntaxError: selector not supported by the native DOM slice: " + selector);
      return parts;
    });
    selectorCache.set(selector, chain);
    return chain;
  }
  function matchCompound(el, parts) {
    for (const p of parts) {
      if (p.kind === "tag") { if (p.value !== "*" && el.localName !== p.value) return false; }
      else if (p.kind === "id") { if (el.getAttribute("id") !== p.value) return false; }
      else if (p.kind === "class") { if (!el.className.split(/\s+/).includes(p.value)) return false; }
      else if (p.kind === "attr") { if (!el.hasAttribute(p.name)) return false; if (p.value !== null && el.getAttribute(p.name) !== p.value) return false; }
    }
    return true;
  }
  function matchChain(el, chain, scope) {
    if (!matchCompound(el, chain[chain.length - 1])) return false;
    let node = el;
    for (let i = chain.length - 2; i >= 0; i--) {
      let ancestor = node.parentNode; let found = null;
      while (ancestor && ancestor !== scope && ancestor.nodeType === 1) { if (matchCompound(ancestor, chain[i])) { found = ancestor; break; } ancestor = ancestor.parentNode; }
      if (!found) return false;
      node = found;
    }
    return true;
  }
  function build(parent, entries) {
    for (const entry of entries) {
      if (entry.x !== undefined) { const t = new Text(entry.x); t.parentNode = parent; parent.childNodes.push(t); continue; }
      const el = new Element(entry.e);
      for (const [name, value] of Object.entries(entry.a || {})) el.__attrs.set(name.toLowerCase(), value);
      el.parentNode = parent; parent.childNodes.push(el);
      build(el, entry.c || []);
    }
  }
  const document = new Document();
  g.__mcsSeed = (entries) => { build(document, entries); return document.__descendants().length; };
  g.__mcsComplete = () => { document.readyState = "complete"; };
  // Bounded fetch bridge: scripts queue requests, the host performs them
  // between evaluation turns under its network policy, then settles them.
  const net = { queue: [], pending: new Map(), next: 0 };
  class Headers_ {
    constructor(entries) { this.__map = new Map(Object.entries(entries || {}).map(([k, v]) => [String(k).toLowerCase(), String(v)])); }
    get(name) { const v = this.__map.get(String(name).toLowerCase()); return v === undefined ? null : v; }
    has(name) { return this.__map.has(String(name).toLowerCase()); }
  }
  class Response_ {
    constructor(payload) { this.status = payload.status; this.ok = payload.status >= 200 && payload.status < 300; this.url = payload.url; this.redirected = payload.redirects > 0; this.headers = new Headers_(payload.headers); this.__body = payload.body; this.bodyUsed = false; }
    text() { this.bodyUsed = true; return Promise.resolve(this.__body); }
    json() { this.bodyUsed = true; try { return Promise.resolve(JSON.parse(this.__body)); } catch (e) { return Promise.reject(new SyntaxError("response body is not JSON")); } }
  }
  g.fetch = (input, init) => {
    const url = typeof input === "string" ? input : (input && input.url) ? String(input.url) : String(input);
    const method = init && init.method ? String(init.method).toUpperCase() : "GET";
    if (method !== "GET") return Promise.reject(new TypeError("native-dom fetch offers GET only"));
    if (init && init.body !== undefined && init.body !== null) return Promise.reject(new TypeError("native-dom fetch does not send bodies"));
    return new Promise((resolve, reject) => {
      const id = ++net.next;
      net.pending.set(id, { resolve, reject });
      net.queue.push({ id, url });
    });
  };
  g.Headers = Headers_; g.Response = Response_;
  g.__mcsNetTake = () => { const q = net.queue; net.queue = []; return JSON.stringify(q); };
  g.__mcsNetSettle = (id, ok, payload) => {
    const entry = net.pending.get(id); if (!entry) return false; net.pending.delete(id);
    if (ok) entry.resolve(new Response_(payload)); else { const e = new TypeError("fetch failed: " + payload.code + " (" + payload.reason + ")"); e.code = payload.code; e.reason = payload.reason; entry.reject(e); }
    return true;
  };
  g.__mcsNetPending = () => net.pending.size;
  // Profile-backed cookies and storage. The host seeds the mirrors before a
  // script runs and drains the write queues after it; the page sees its own
  // writes synchronously, the host commits them in order and re-seeds on a
  // commit failure, so a failed commit is never a silently kept write.
  const store = { cookie: "", cookieWrites: [], entries: new Map(), ops: [], readonly: false, keyLimit: 32, valueLimit: 1024 };
  Object.defineProperty(Document.prototype, "cookie", {
    get() { return store.cookie; },
    set(value) { store.cookieWrites.push(String(value)); },
    configurable: true,
  });
  const quota = (why) => { const e = new Error(why); e.name = "QuotaExceededError"; return e; };
  const storage = {
    getItem(key) { const v = store.entries.get(String(key)); return v === undefined ? null : v; },
    setItem(key, value) {
      key = String(key); value = String(value);
      if (store.readonly) throw quota("localStorage is read-only after a failed commit");
      if (value.length > store.valueLimit) throw quota("value exceeds the profile budget");
      if (!store.entries.has(key) && store.entries.size >= store.keyLimit) throw quota("key count exceeds the profile budget");
      store.entries.set(key, value); store.ops.push({ op: "set", key, value });
    },
    removeItem(key) { key = String(key); if (store.readonly) throw quota("localStorage is read-only after a failed commit"); if (store.entries.delete(key)) store.ops.push({ op: "remove", key }); },
    clear() { if (store.readonly) throw quota("localStorage is read-only after a failed commit"); if (store.entries.size) { store.entries.clear(); store.ops.push({ op: "clear" }); } },
    key(index) { const keys = Array.from(store.entries.keys()); return index < keys.length ? keys[index] : null; },
    get length() { return store.entries.size; },
  };
  g.localStorage = storage;
  g.__mcsCookieSeed = (text) => { store.cookie = String(text); };
  g.__mcsCookieTake = () => { const q = store.cookieWrites; store.cookieWrites = []; return JSON.stringify(q); };
  g.__mcsStorageSeed = (json, readonly) => { store.entries = new Map(Object.entries(JSON.parse(json))); store.ops = []; store.readonly = !!readonly; };
  g.__mcsStorageTake = () => { const q = store.ops; store.ops = []; return JSON.stringify(q); };
  // The realm has no URL global; the host passes the parsed parts of the document URL.
  // The document's committed URL, and the one slot a page's navigation
  // intent goes into. The slot is closure-owned: a page can write it only
  // through the location members below, and only the host can read it.
  let committed = null;
  let intent = null;
  // The realm keeps at most this many characters of an address. The host's
  // bound is in UTF-8 bytes and is checked again there, so a shorter
  // non-ASCII address can pass this one and still be refused.
  const MAX_INTENT_CHARS = 2000;
  const recordIntent = (kind, raw) => {
    // Last write wins: one slot, overwritten, never a queue.
    if (raw === undefined) { intent = { kind, url: null, over: false }; return; }
    const text = String(raw);
    // An over-length address is not retained at all: the kind and one fixed
    // marker cross, and the host refuses for one fixed reason.
    if (text.length > MAX_INTENT_CHARS) { intent = { kind, url: null, over: true }; return; }
    intent = { kind, url: text, over: false };
  };
  // `live` is true only for a realm that runs page script. A child frame is
  // built script-free, so nothing there can read an accessor or raise an
  // intent, and it keeps the plain immutable object it had before intents
  // existed — the two forms are indistinguishable to every observer that can
  // exist in that realm, and the accessor form is not free.
  g.__mcsLocation = (parts, live) => {
    committed = parts;
    if (!live) {
      g.location = { href: parts.href, origin: parts.origin, protocol: parts.protocol, host: parts.host, hostname: parts.hostname, port: parts.port, pathname: parts.pathname, search: parts.search, hash: parts.hash, toString() { return parts.href; } };
      return;
    }
    const location = {
      get href() { return committed.href; },
      set href(value) { recordIntent("assign", value); },
      assign(value) { recordIntent("assign", value); },
      replace(value) { recordIntent("replace", value); },
      reload() { recordIntent("reload", undefined); },
      get origin() { return committed.origin; },
      get protocol() { return committed.protocol; },
      get host() { return committed.host; },
      get hostname() { return committed.hostname; },
      get port() { return committed.port; },
      get pathname() { return committed.pathname; },
      get search() { return committed.search; },
      get hash() { return committed.hash; },
      toString() { return committed.href; },
    };
    Object.defineProperty(g, "location", {
      value: location, writable: false, configurable: false, enumerable: true,
    });
  };
  g.window = g; g.self = g; g.document = document;
  g.addEventListener = (type, fn) => addListener(g, type, fn);
  g.removeEventListener = (type, fn) => removeListener(g, type, fn);
  g.dispatchEvent = (event) => dispatchOn(g, event);
  let onloadHandler = null;
  Object.defineProperty(g, "onload", {
    get() { return onloadHandler; },
    set(fn) {
      if (onloadHandler) removeListener(g, "load", onloadHandler);
      onloadHandler = typeof fn === "function" ? fn : null;
      if (onloadHandler) addListener(g, "load", onloadHandler);
    },
    configurable: true,
    enumerable: false,
  });
  // The four observable steps are not exposed here. The host arms them once,
  // before any page script runs, with a capability only it holds; see
  // `lifecycle_arm_script`. This function is handed to that installer and is
  // never reachable from the global.
  const runLifecycleStep = (step) => {
    if (step === 1) {
      document.readyState = "interactive";
      dispatchOn(document, new Event("readystatechange", {}));
    } else if (step === 2) {
      dispatchOn(document, new Event("DOMContentLoaded", { bubbles: true }));
    } else if (step === 3) {
      document.readyState = "complete";
      dispatchOn(document, new Event("readystatechange", {}));
    } else if (step === 4) {
      dispatchOn(g, new Event("load", {}));
    }
  };
  // One-shot, non-enumerable and removed by the installer the moment it has
  // been consumed, so only the first caller — the host, before page scripts —
  // can ever arm the bridge.
  Object.defineProperty(g, "__mcsArmLifecycle", {
    value: (arm) => {
      delete g.__mcsArmLifecycle;
      // The taker empties the slot as it reads it, so an intent is consumed
      // once and a refusal cannot retry itself.
      const takeIntent = () => { const taken = intent; intent = null; return taken; };
      return arm(runLifecycleStep, takeIntent);
    },
    writable: false, configurable: true, enumerable: false,
  });
  g.Node = Node; g.Element = Element; g.Text = Text; g.Document = Document; g.Event = Event; g.MutationObserver = MutationObserver;
  g.queueMicrotask = (fn) => { Promise.resolve().then(fn); };
  // Timers. The realm owns the callbacks and their handles; the host owns the
  // clock and decides when a callback is due, so a page can neither read the
  // time nor measure it here. A handle is minted once and never reused, and
  // the realm refuses to schedule past its bound or past the safe integer.
  const timers = { next: 1, pending: new Map(), scheduled: [], refused: 0, limit: 64, safe: Number.MAX_SAFE_INTEGER };
  // The bridge cannot be replaced, shadowed or enumerated by page script. It
  // shares this realm, so its contents can still be perturbed by the page
  // that owns them; the host validates every read and attributes what it
  // cannot read, so a page can only cost itself its own timers.
  Object.defineProperty(g, "__mcsTimers", { value: timers, writable: false, configurable: false, enumerable: false });
  g.setTimeout = (fn, ms, ...args) => {
    if (typeof fn !== "function") {
      throw new TypeError("setTimeout takes a function: this host evaluates no string bodies");
    }
    if (timers.pending.size >= timers.limit) {
      timers.refused = timers.refused + 1;
      throw new RangeError("too many pending timers");
    }
    if (timers.next >= timers.safe) {
      timers.refused = timers.refused + 1;
      throw new RangeError("timer handles are exhausted");
    }
    let delay = Number(ms);
    if (!Number.isFinite(delay) || delay < 0) delay = 0;
    delay = Math.min(Math.floor(delay), 2147483647);
    const handle = timers.next;
    timers.next = handle + 1;
    timers.pending.set(handle, { fn, args });
    timers.scheduled.push([handle, delay]);
    return handle;
  };
  // Cancellation happens inside this turn: the callback is released here, so
  // no later drain can reach it. An unknown or fired handle is a no-op.
  g.clearTimeout = (handle) => {
    const id = Number(handle);
    if (!timers.pending.has(id)) return;
    timers.pending.delete(id);
    timers.scheduled.push([id, -1]);
  };
  g.console = { log() {}, warn() {}, error() {}, debug() {}, info() {} };
  g.navigator = { userAgent: "MiniCon Surf native-dom (QuickJS)" };
  g.location = { href: "minicon-surf://court/fixture", protocol: "minicon-surf:", origin: "null", toString() { return "minicon-surf://court/fixture"; } };
})();
