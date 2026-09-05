# `classList` and `CustomEvent` (native-dom, control 0.0.2)

Design before code. This is candidate C of `browser-gap-triage-0.0.1.md`, the
last of the three that triage proposed; A (the document lifecycle) and B
(page-initiated navigation) have landed. It is written and ruled by me while
the root is unavailable, and every ruling below is mine and marked as such,
so a later review can overturn any of them against the record rather than
against my memory.


## 1. The gap, measured on the current build

Probed through the control door on `878a86a847d3…`, the shim-split build, by
a page that writes what it finds into its own document:

```
classList:undefined  CustomEvent:undefined  className:string  DOMTokenList:undefined
```

So the `class` attribute is fully there — `className` reflects it, and the
selector engine matches `.token` against it — and the two things a page uses
to *work* with it are not. A page that toggles a class to express state, which
is how most pages express state an agent then reads back through a selector,
throws today.


## 2. Scope, and what stays out

In: `element.classList` with `add`, `remove`, `toggle`, `contains`, `length`,
`value` and `toString`; and `CustomEvent(type, { bubbles, cancelable, detail })`
whose `detail` survives a dispatch through the existing listener model.

Out, and recorded as losses rather than hidden: iteration and index access on
the list (`for…of`, `item`, `[0]`), `replace`, `supports`, the `DOMTokenList`
constructor as a global, `relList`/`sandbox`/any other token-list attribute,
and `Event.composed`/`composedPath`. Nothing here adds an event phase, a
listener option or a new authority.


## 3. Where it lives, and the one base byte it costs

The root's standing ruling is that main-only browser APIs live in the main
extension. Both belong there: a child frame runs no page script, so nothing in
a child realm can call `classList` or construct a `CustomEvent`, and the
host's own snapshot, preflight and action scripts use neither — they read the
`class` attribute directly and construct plain `Event`s.

The extension reaches the base through the one-shot handle, which today
carries `g`, `document`, `Document`, `Event`, `addListener`, `removeListener`
and `dispatchOn`. Installing an accessor on `Element.prototype` needs
`Element`, so **`Element` joins the handle**.

*My ruling, for review:* that is not base growth in the sense the root
guarded against. `Element` is already the base's own class, already reachable
from every realm as `window.Element`, and adding an identifier to the handle
object grants a child realm nothing it did not have — the handle is deleted
in a child before anything else is evaluated there. The base's source grows by
nine bytes; the shim-footprint court re-measures the floor either way, and the
16 KiB M1 floor is the check that this claim is not merely an argument.


## 4. One authority: the attribute is the state

`classList` holds no tokens. Every call parses `getAttribute("class")`,
computes, and writes back through `setAttribute` — the same path `className`
uses. There is no cached token set, no per-element list object kept alive, and
nothing to fall out of step with the attribute. The accessor returns a fresh
view each time it is read, which costs an allocation per read and buys the
guarantee that a list can never disagree with the attribute it describes.


## 5. Rulings, mine, each recorded as a divergence or a choice

**5.1 A call that changes nothing touches nothing.** In the standard,
`remove()` of an absent token still runs the update steps and writes the
attribute, which a `MutationObserver` sees. Here, a call whose token set is
unchanged does not call `setAttribute` at all, so it produces no mutation
record and does not advance the revision.

*Why:* the revision is this host's gating primitive — a caller holds a
snapshot and an action is refused if the revision moved. Spurious revisions
are not a cosmetic difference here; they cost a caller a re-snapshot for a
change that did not happen. The divergence is recorded, not hidden, and the
court proves it in both directions.

**5.2 The revision advances per flush, not per call.** This is existing
behaviour and the court states it rather than changing it: mutation records
batch into one microtask callback, so ten changing calls in one turn advance
the revision once. What the court asserts is that a turn that changed
something advances it, and a turn that changed nothing does not.

**5.3 Token errors keep their standard names.** An empty token throws a
`SyntaxError`, and a token containing whitespace throws an
`InvalidCharacterError`. The realm has no `DOMException`, so these are
`Error`s whose `name` is set to those strings — the page sees the name it
expects and the host learns nothing new.

**5.4 `toggle` keeps the standard's shape**, including the second argument:
`toggle(t, true)` only adds, `toggle(t, false)` only removes, and the return
value is whether the token is present afterwards.

**5.5 Order and duplicates.** Tokens are kept in first-seen order, added at
the end, and a duplicate `add` is a no-op by 5.1. The serialized attribute is
the token set joined by single spaces, so writing normalizes the whitespace
the page wrote — which is what the standard does too.

**5.6 `CustomEvent` is `Event` plus one own property.** `detail` defaults to
`null` and is whatever the page passed, by reference, and it never crosses
into the host: no snapshot, receipt, ledger, error or counter reads it. It is
page data in the realm and stays there.


## 6. Pre-registered court: `element-api-court.py`

Headless, both allocators, one hermetic loopback origin, supervised hosts.
Frozen before the code. Every criterion fails on `878a86a847d3…`.

1. **Reflection.** After `add("a")` and `add("b")`, `getAttribute("class")`
   is `"a b"`, `querySelector(".b")` finds the element, and `className` agrees.
2. **Removal and toggling.** `remove("a")` leaves `"b"`; `toggle("b")` returns
   `false` and empties it; `toggle("c", true)` returns `true` and adds it;
   `toggle("c", false)` returns `false` and removes it.
3. **`contains` and `length`** answer over the attribute the page wrote
   directly, including one written with ragged whitespace.
4. **A changing turn advances the revision; a no-op turn does not.** Two
   observations bracket a turn that only calls `remove` for absent tokens and
   `add` for present ones: the revision is unchanged. A turn that changes the
   attribute advances it.
5. **Errors.** `add("")` throws `SyntaxError`; `add("a b")` throws
   `InvalidCharacterError`; neither leaves the attribute changed.
6. **`CustomEvent`.** A listener on an ancestor receives a bubbling
   `CustomEvent` whose `detail` is the object the page passed, with its own
   fields intact, and `event.type` is what was constructed.
7. **The child realm has neither.** With the court-only realm probe, a child
   frame's realm reports `classList` and `CustomEvent` absent, which is what
   "main-only" has to mean; and the parent's realm has both.
8. **Nothing else moved.** The same-binary suite, and the shim-footprint
   court's floors re-passed with the extension larger and the base nine bytes
   larger.

Criterion 7 needs the realm probe to answer about names other than the
internals handle, so the probe's fixed name list grows from one to three. It
stays court-only, refused before the host serves without the private court
file, and reports counts of fixed names — never page data.


## 7. What this does not do

No protocol change, no new operation, no new authority, no resident host
state, no child-realm surface, and no event-model change: the same listener
store, the same path, the same absence of capture, options and
`stopImmediatePropagation`.


## 8. Two criteria of mine that measured the wrong turn

Both revision criteria were wrong in the same way, and the implementation is
what exposed them.

**8.1 The observation was the boundary.** "A turn that changes the attribute
advances the revision" read the revision twice through `target.inspect`. But
`target.inspect` *is* the boundary that runs the due timer, so the first read
already included the change: `[1, 1]`, and the criterion failed against a host
that was behaving correctly. The "before" is now the revision `target.open`
itself reports, which crosses no timer boundary.

**8.2 The quiet page proved nothing.** The no-op page called `remove` for an
absent token and `add` for a present one — and then wrote its result into its
own marked element **in the same turn**. That write is a mutation, so the
revision would advance whatever `classList` did, and the criterion could not
see the thing it was written to see. Worse, it passed.

The no-op turn now touches nothing: it makes its calls and schedules a second
timer, and only that later turn writes what it learned. So the first boundary
is a turn of pure no-op calls whose revision must not move, and the text that
arrives on the next boundary is what proves the calls ran at all rather than
threw — without which the criterion would pass on a host where `classList`
does not exist.
