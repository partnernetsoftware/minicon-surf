// The base every realm compiles: the tree, its events, and exactly what the
// host's own snapshot, preflight and action scripts need. A child frame runs
// no page script and gets this and nothing else, because what it cannot reach
// it should not pay for; the page surface a script needs is in
// `dom_shim_main.js` and is evaluated only in a realm that runs script.
// Nothing here is emulated beyond what the courts use: unsupported selectors
// throw, and timers do not exist at this layer.
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
  // An event's state is closure-owned and keyed by the event, so nothing a
  // page can reach writes it: `preventDefault` is the only door to
  // `defaultPrevented`, and the dispatcher is the only writer of `target`,
  // `currentTarget`, `eventPhase` and `dispatching`. That matters beyond
  // fidelity — the host reads `defaultPrevented` back to decide whether an
  // activation proceeds.
  const eventState = new WeakMap();
  const NONE = 0;
  const AT_TARGET = 2;
  const BUBBLING_PHASE = 3;
  class Event {
    constructor(type, init) {
      // A default parameter covers `undefined` and not `null`, and a page may
      // pass either.
      const d = init === null || init === undefined ? {} : init;
      eventState.set(this, {
        type: String(type),
        bubbles: !!d.bubbles,
        cancelable: !!d.cancelable,
        composed: !!d.composed,
        defaultPrevented: false,
        target: null,
        currentTarget: null,
        eventPhase: NONE,
        dispatching: false,
        stop: false,
        stopImmediate: false,
        // The clock this realm already inherited and does not model; this is
        // no new guarantee, only a number read from what was already there.
        timeStamp: g.performance && g.performance.now ? g.performance.now() : 0,
      });
    }
    // Synthesized by an agent, never by a person: `isTrusted` would be a
    // claim that is not true.
    get isTrusted() { return false; }
    get type() { return eventState.get(this).type; }
    get bubbles() { return eventState.get(this).bubbles; }
    get cancelable() { return eventState.get(this).cancelable; }
    get composed() { return eventState.get(this).composed; }
    get defaultPrevented() { return eventState.get(this).defaultPrevented; }
    get target() { return eventState.get(this).target; }
    get currentTarget() { return eventState.get(this).currentTarget; }
    get eventPhase() { return eventState.get(this).eventPhase; }
    get dispatching() { return eventState.get(this).dispatching; }
    get timeStamp() { return eventState.get(this).timeStamp; }
    preventDefault() { const s = eventState.get(this); if (s.cancelable) s.defaultPrevented = true; }
    stopPropagation() { eventState.get(this).stop = true; }
    stopImmediatePropagation() { const s = eventState.get(this); s.stop = true; s.stopImmediate = true; }
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
  // A registration is its own record, and identity is the record rather than
  // the callback: the same function registered on two targets, or under two
  // types, is two registrations, and removing one says nothing about the
  // other. A dispatch snapshots records, so removal during it is seen by
  // exactly the dispatches whose snapshot holds that record, and a re-add
  // makes a new record no snapshot in flight contains. The type is a string,
  // whatever the page passed, because an event's type is one too.
  function addListener(target, type, fn) {
    if (typeof fn !== "function") return;
    const key = String(type);
    const map = listenersOf(target);
    if (!map.has(key)) map.set(key, []);
    const list = map.get(key);
    // The same target, type and function identity is registered once: a
    // repeat is a no-op, as the standard has it within this host's bounds.
    for (const record of list) if (record.callback === fn) return;
    list.push({ callback: fn, removed: false });
  }
  function removeListener(target, type, fn) {
    const list = listenersOf(target).get(String(type));
    if (!list) return;
    for (let i = 0; i < list.length; i += 1) {
      if (list[i].callback === fn) { list[i].removed = true; list.splice(i, 1); return; }
    }
  }
  // The path is the node chain, and then — only for an event that bubbles —
  // the window, which is the document's parent *event target* and never a
  // parentNode: nothing is appended to the tree and document.parentNode
  // stays null.
  function dispatchOn(target, event) {
    const state = eventState.get(event);
    // Dispatching something that is not an event is refused: answering `true`
    // would report a dispatch that never happened.
    if (!state) {
      const error = new Error("the argument is not an event");
      error.name = "TypeError";
      throw error;
    }
    // An event already in flight is refused before anything is written, so
    // the dispatch in progress is not corrupted — and a handler that
    // dispatches the event it was handed no longer recurses until the stack
    // runs out. A completed event may be dispatched again.
    if (state.dispatching) {
      const error = new Error("the event is already being dispatched");
      error.name = "InvalidStateError";
      throw error;
    }
    // Nothing is reset here: a stop flag set before a dispatch is effective
    // for that dispatch, and the flags are cleared when this one completes.
    state.dispatching = true;
    state.target = target;

    const path = [];
    if (target && typeof target.nodeType === "number") {
      for (let n = target; n; n = n.parentNode) path.push(n);
      // Only a path that actually reached the document continues to the
      // window: a detached subtree bubbles through its own ancestors and
      // stops there, as the standard has it.
      if (state.bubbles && path[path.length - 1] === document) path.push(g);
    } else {
      path.push(target);
    }
    try {
      for (const node of path) {
        state.currentTarget = node;
        state.eventPhase = node === target ? AT_TARGET : BUBBLING_PHASE;
        if (state.stop || state.stopImmediate) break;
        const list = listenersOf(node).get(state.type);
        if (list) {
          // The list is copied so a listener added during this dispatch is
          // not called by it, and one removed during it stays silent even if
          // the same function is registered again.
          for (const record of [...list]) {
            if (record.removed) continue;
            if (state.stopImmediate) break;
            try { record.callback.call(node, event); } catch (error) {}
          }
        }
        if (state.stop || !state.bubbles) break;
      }
    } finally {
      // Every path out leaves the event clean, and its target is the last one
      // it was dispatched to.
      state.currentTarget = null;
      state.eventPhase = NONE;
      state.dispatching = false;
      state.stop = false;
      state.stopImmediate = false;
    }
    return !state.defaultPrevented;
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
  // Every realm reports its own address. The accessor form, with the
  // navigation-intent slot behind it, belongs to a realm that runs page
  // script and is installed by the main extension.
  g.__mcsLocation = (parts) => {
    g.location = { href: parts.href, origin: parts.origin, protocol: parts.protocol, host: parts.host, hostname: parts.hostname, port: parts.port, pathname: parts.pathname, search: parts.search, hash: parts.hash, toString() { return parts.href; } };
  };
  g.window = g; g.self = g; g.document = document;
  g.Node = Node; g.Element = Element; g.Text = Text; g.Document = Document; g.Event = Event; g.MutationObserver = MutationObserver;
  g.location = { href: "minicon-surf://court/fixture", protocol: "minicon-surf:", origin: "null", toString() { return "minicon-surf://court/fixture"; } };
  // The one door between this base and the main extension. It is not a
  // capability a realm keeps: it deletes itself as it hands the internals
  // over, so a main realm has consumed it before any page script runs, and a
  // realm that gets no extension is sealed by the host before anything else
  // is evaluated in it. Nothing here is reachable or enumerable afterwards.
  Object.defineProperty(g, "__mcsInternals", {
    value: (take) => {
      delete g.__mcsInternals;
      return take({ g, document, Document, Element, Event, addListener, removeListener, dispatchOn });
    },
    writable: false, configurable: true, enumerable: false,
  });
})();
