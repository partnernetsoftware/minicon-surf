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
    constructor() { this.parentNode = null; this.childNodes = []; this.__listeners = new Map(); }
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
    addEventListener(type, fn) { if (typeof fn !== "function") return; if (!this.__listeners.has(type)) this.__listeners.set(type, []); this.__listeners.get(type).push(fn); }
    removeEventListener(type, fn) { const l = this.__listeners.get(type); if (!l) return; const i = l.indexOf(fn); if (i >= 0) l.splice(i, 1); }
    dispatchEvent(event) {
      event.target = this;
      const path = []; for (let n = this; n; n = n.parentNode) path.push(n);
      for (const n of path) {
        event.currentTarget = n;
        const l = n.__listeners.get(event.type);
        if (l) for (const fn of [...l]) { try { fn.call(n, event); } catch (e) {} }
        if (event.__stop || !event.bubbles) break;
      }
      event.currentTarget = null;
      return !event.defaultPrevented;
    }
    __descendants(out = []) { for (const c of this.childNodes) { if (c.nodeType === 1) { out.push(c); c.__descendants(out); } } return out; }
    querySelectorAll(selector) { const chain = parseSelector(selector); return this.__descendants().filter((el) => matchChain(el, chain, this)); }
    querySelector(selector) { const chain = parseSelector(selector); for (const el of this.__descendants()) if (matchChain(el, chain, this)) return el; return null; }
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
    get value() { if (this.__value !== null) return this.__value; return this.localName === "textarea" ? this.textContent : (this.getAttribute("value") ?? ""); }
    set value(v) { this.__value = String(v); }
    get innerText() { return this.textContent; }
    matches(selector) { return matchChain(this, parseSelector(selector), null); }
    click() { this.dispatchEvent(new Event("click", { bubbles: true, cancelable: true })); }
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
  g.__mcsLocation = (href) => { try { const u = new URL(href); g.location = { href: u.href, origin: u.origin, protocol: u.protocol, host: u.host, hostname: u.hostname, port: u.port, pathname: u.pathname, search: u.search, hash: u.hash, toString() { return u.href; } }; } catch (e) { g.location = { href, toString() { return href; } }; } };
  g.window = g; g.self = g; g.document = document;
  g.Node = Node; g.Element = Element; g.Text = Text; g.Document = Document; g.Event = Event; g.MutationObserver = MutationObserver;
  g.queueMicrotask = (fn) => { Promise.resolve().then(fn); };
  g.setTimeout = (fn, _ms, ...args) => { Promise.resolve().then(() => fn(...args)); return 0; };
  g.clearTimeout = () => {};
  g.console = { log() {}, warn() {}, error() {}, debug() {}, info() {} };
  g.navigator = { userAgent: "MiniCon Surf native-dom (QuickJS)" };
  g.location = { href: "minicon-surf://court/fixture", protocol: "minicon-surf:", origin: "null", toString() { return "minicon-surf://court/fixture"; } };
})();
