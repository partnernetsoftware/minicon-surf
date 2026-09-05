// The main extension: the page surface a realm needs only if page script
// runs in it. It is evaluated after `dom_shim_base.js`, in the same realm,
// and it reaches the base's internals through the one-shot handle the base
// deletes as it hands them over — so this runs before any page script, and
// leaves no door behind it. There is one DOM in the realm and this extends
// it; nothing here mirrors, copies or caches any of it.
__mcsInternals((internals) => {
  const g = internals.g;
  const document = internals.document;
  const Document = internals.Document;
  const Event = internals.Event;
  const addListener = internals.addListener;
  const removeListener = internals.removeListener;
  const dispatchOn = internals.dispatchOn;
  const Element = internals.Element;
  const eventStateOf = internals.eventStateOf;
  const Node = internals.Node;
  // Ten members no child realm can call, so no child realm compiles them: a
  // member on a shared prototype costs every child 600 to 960 bytes of M1,
  // and these are reachable from page script alone. Each is the base's own
  // implementation, moved rather than rewritten.
  Object.defineProperty(Node.prototype, "firstChild", {
    get() { return this.childNodes[0] || null; }, configurable: true });
  Object.defineProperty(Node.prototype, "lastChild", {
    get() { return this.childNodes[this.childNodes.length - 1] || null; }, configurable: true });
  Object.defineProperty(Node.prototype, "parentElement", {
    get() { return this.parentNode && this.parentNode.nodeType === 1 ? this.parentNode : null; },
    configurable: true });
  const containsHelper = internals.contains;
  Node.prototype.contains = function (other) { return containsHelper(this, other); };
  Node.prototype.appendChild = function (node) { this.append(node); return node; };
  Node.prototype.remove = function () { if (this.parentNode) this.parentNode.removeChild(this); };
  Object.defineProperty(Element.prototype, "innerText", {
    get() { return this.textContent; }, configurable: true });
  Object.defineProperty(Element.prototype, "defaultValue", {
    get() {
      return this.localName === "textarea" ? this.textContent : (this.getAttribute("value") ?? "");
    }, configurable: true });
  Element.prototype.focus = function () {};
  Element.prototype.blur = function () {};
  Element.prototype.submit = function () {
    return dispatchOn(this, new Event("submit", { bubbles: true, cancelable: true }));
  };
  // The page-facing view of an event, installed where a page can read it and
  // nowhere else. Every one is read-only, exactly as it was in the base.
  const eventView = {
    isTrusted: () => false,
    type: (state) => state.type,
    bubbles: (state) => state.bubbles,
    cancelable: (state) => state.cancelable,
    composed: (state) => state.composed,
    target: (state) => state.target,
    currentTarget: (state) => state.currentTarget,
    eventPhase: (state) => state.eventPhase,
    dispatching: (state) => state.dispatching,
    timeStamp: (state) => state.timeStamp,
  };
  for (const name of Object.keys(eventView)) {
    const read = eventView[name];
    Object.defineProperty(Event.prototype, name, {
      get() { return read(eventStateOf(this)); },
      configurable: true,
      enumerable: false,
    });
  }
  // `classList` holds no tokens: the `class` attribute is the state and every
  // call reads and writes it, so a list can never disagree with the attribute
  // it describes. A call that changes nothing writes nothing, so it produces
  // no mutation record and does not advance the revision — a deliberate
  // divergence, because the revision is what gates a caller's action and a
  // spurious one costs a re-snapshot for a change that did not happen.
  const tokenError = (name, why) => { const e = new Error(why); e.name = name; return e; };
  const checkToken = (token) => {
    const text = String(token);
    if (text === "") throw tokenError("SyntaxError", "the token is empty");
    if (/\s/.test(text)) throw tokenError("InvalidCharacterError", "the token has whitespace");
    return text;
  };
  // The attribute is the state and the list is a view of it: every read and
  // every mutation reparses, so a list held across a direct write to `class`
  // answers about the attribute as it is now, and a mutation through it never
  // drops what was written directly.
  const tokensOf = (el) => {
    const raw = el.getAttribute("class");
    const list = [];
    for (const token of String(raw === null || raw === undefined ? "" : raw).split(/\s+/)) {
      if (token && list.indexOf(token) < 0) list.push(token);
    }
    return list;
  };
  // A call that changes nothing writes nothing: the comparison is against the
  // attribute's own string, so a call that would normalize ragged whitespace
  // is a change and does write.
  const writeTokens = (el, after) => {
    const text = after.join(" ");
    if (el.getAttribute("class") === text) return;
    el.setAttribute("class", text);
  };
  // `element.classList` is the same object every time, per the standard. The
  // entry is allocated on first access, holds no tokens — it is a handle, not
  // a cache — and dies with the element.
  const classLists = new WeakMap();
  const classListOf = (el) => {
    let list = classLists.get(el);
    if (list) return list;
    list = {
      get length() { return tokensOf(el).length; },
      get value() {
        const raw = el.getAttribute("class");
        return raw === null || raw === undefined ? "" : String(raw);
      },
      set value(text) { el.setAttribute("class", String(text)); },
      toString() {
        const raw = el.getAttribute("class");
        return raw === null || raw === undefined ? "" : String(raw);
      },
      contains(token) { return tokensOf(el).indexOf(checkToken(token)) >= 0; },
      add(...names) {
        const after = tokensOf(el);
        for (const name of names) {
          const token = checkToken(name);
          if (after.indexOf(token) < 0) after.push(token);
        }
        writeTokens(el, after);
      },
      remove(...names) {
        let after = tokensOf(el);
        for (const name of names) {
          const token = checkToken(name);
          after = after.filter((kept) => kept !== token);
        }
        writeTokens(el, after);
      },
      toggle(name, force) {
        const token = checkToken(name);
        const tokens = tokensOf(el);
        const present = tokens.indexOf(token) >= 0;
        const wanted = force === undefined ? !present : !!force;
        if (wanted !== present) {
          writeTokens(el, wanted ? tokens.concat([token]) : tokens.filter((k) => k !== token));
        }
        return wanted;
      },
    };
    classLists.set(el, list);
    return list;
  };
  Object.defineProperty(Element.prototype, "classList", {
    get() { return classListOf(this); },
    configurable: true,
    enumerable: false,
  });
  // An event with one own property. `detail` is the page's own value, by
  // reference, and it reaches no snapshot, receipt, ledger, error or counter.
  class CustomEvent_ extends Event {
    constructor(type, init) {
      super(type, init);
      // `Event` normalizes the dictionary itself now; this reads the same one
      // for its own member, which is read-only like every other.
      const dictionary = init === null || init === undefined ? {} : init;
      const detail = dictionary.detail === undefined ? null : dictionary.detail;
      Object.defineProperty(this, "detail", {
        value: detail, writable: false, enumerable: true, configurable: true,
      });
    }
  }
  g.CustomEvent = CustomEvent_;
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
  // This replaces the base's plain seed: a realm that runs page script gets
  // the accessor form and the intent slot behind it. Which form a realm has
  // is no longer a parameter — it is which sources that realm compiled.
  g.__mcsLocation = (parts) => {
    committed = parts;
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
});
