# Bounded document lifecycle — decision record

Status: **design frozen, no product code.** Scope is the lifecycle and
nothing else: no `classList`, no page-initiated navigation, no history.

## 1. What is missing, measured

On build `561062d146e60170`, through the control door: a listener registered
with `document.addEventListener("DOMContentLoaded", …)` **never fires**, and
`window.addEventListener` is `undefined`, so a page cannot register a `load`
handler even in principle. `__mcsComplete()` sets `document.readyState` to
`"complete"` and dispatches nothing at all. A page that builds itself on load
therefore stays exactly as the server sent it.

## 2. The window becomes a real EventTarget

Not a special case for one event name. The listener model the DOM shim
already has — a `Map` of type to functions, `addEventListener`,
`removeEventListener`, `dispatchEvent` — is extracted so that the global
object carries the same three methods with the same semantics as a node. A
custom event dispatched on `window` reaches a listener registered for it; a
removed listener stops receiving; a handler that throws does not stop the
next one, which is what the node model already does.

`window.onload` is supported as a **property**: assigning a function
registers it, assigning again replaces it, assigning `null` removes it, and
reading it returns what was assigned. It is one accessor over the same
listener list, not a second dispatch path.

## 3. Four observable steps, four host boundaries

The standard's sequence, in the bounded form this host can honour:

| # | Step | Where |
|---|---|---|
| 1 | `readyState = "interactive"`, then `readystatechange` | document |
| 2 | `DOMContentLoaded` | document |
| 3 | `readyState = "complete"`, then `readystatechange` | document |
| 4 | `load` | window |

**Each step is its own host evaluation.** They are not chained inside one
call, and `DOMContentLoaded` and `load` are never fired synchronously from
the same evaluation. That matters because every host evaluation ends with the
job drain: microtasks a handler queues in step 2 run to completion — under the
request's deadline, as the job slice established — **before** step 4 begins.
A page that awaits something in `DOMContentLoaded` therefore observes the
ordering the standard gives it.

Each event fires **exactly once** per document, is **not cancelable**, and
neither `DOMContentLoaded` nor `load` bubbles. The standard marks
`DOMContentLoaded` as bubbling; it is dispatched at the document, which is the
root of this tree, so no listener can tell the difference, and the host
constructs it non-bubbling as ruled. `readystatechange` is dispatched at the
document and does not bubble either.

A handler that throws does not stop the listeners after it, and does not stop
the steps after it: the shim's dispatch already isolates each listener, and
the host runs each step regardless of what the previous one raised. What a
handler mutates advances the target's revision exactly as any page mutation
does, and what it schedules — a timer, a job — is bounded by the rules those
slices already fixed.

## 4. Where the lifecycle sits

Unchanged: the document's own scripts run first, in document order, exactly as
they do today. The lifecycle begins only after the last of them has run and
its jobs have drained. A failed build is still atomic — if any step is
interrupted at the deadline, the target commits nothing, no realm survives and
no history entry is made.

## 5. Children

A child frame runs no scripts, which this slice does not change. A child may
pass through the same `readyState` transitions so that its document reports
`"complete"` like any other, but **no script and therefore no listener can
exist in it**, and nothing here is a route to enabling one. The court asserts
that a child's lifecycle observably does nothing.

## 6. Losses, recorded rather than implied

- `defer` and `async` script ordering: not modelled. Scripts run in document
  order after parsing, as before.
- Resource load: no CSS, image, font or subresource load participates in
  `load`. It fires after the document's scripts and its jobs, not after any
  resource.
- `pageshow`, `pagehide`, `beforeunload`, `unload`, `visibilitychange`,
  `readystatechange` as an **`onreadystatechange` property**, `DOMContentLoaded`
  as a property: none exist.
- Listener options — capture, `once`, `passive` — and
  `stopImmediatePropagation`: none exist; a listener is a function in a list.
- This is four steps of an event loop, not an event loop.

## 7. Memory

No resident cost: four `Event` objects per document, dispatched into listeners
the page already owns, plus one listener map on the global. The criteria are
**bounded owners and paired differentials**, never an absolute footprint gate,
because the footprint instrument is known to be noisy on this route:

| # | Criterion |
|---|---|
| M1 | the **frozen fixture's workload**, gated: that page with its stated listeners costs at most 65,536 live owner bytes more than the same page with none. It bounds that fixture and no other page. The quiet page's **total** owner bytes are reported beside it; the fixed infrastructure lives inside that total and is given no number of its own, because the two arms cannot isolate it (§10.5) |
| M2 | 128 document replacements leave the live owners within 65,536 bytes of the one-document baseline |
| M3 | closing every target returns the live owners to the empty-host baseline exactly |

## 8. The frozen court

`labs/native-dom/lifecycle-court.py`, hermetic, headless, both allocators,
frozen before the code and run against the current build first.

1. **Order.** A page that records each step observes exactly
   `interactive → DOMContentLoaded → complete → load`, in that order, once
   each.
2. **`readyState`.** It reads `loading` while the page's scripts run,
   `interactive` inside `DOMContentLoaded`, and `complete` inside `load`.
3. **Targets.** `DOMContentLoaded` has the document as `target` and, inside
   the listener, as `currentTarget`; `load` has the window as both.
4. **The window is an EventTarget.** A custom-typed event dispatched on the
   window reaches its listener; `removeEventListener` stops it; `window.onload`
   fires, is replaceable, and clears on `null`.
5. **A microtask between the steps.** A job queued inside `DOMContentLoaded`
   runs before `load` begins.
6. **A throw does not stop anything.** A listener that throws leaves the next
   listener in the same step and every later step running.
7. **Exactly once, and again on a new document.** Each event fires once per
   document; a reload and a navigation each run the whole sequence again for
   the new document and never re-run it for the old one.
8. **Deadline and atomicity.** An infinite job queued in a lifecycle handler
   is interrupted at the deadline, the build commits no target, and the host
   answers the next request.
9. **Children.** A child frame's lifecycle is observably inert: no listener
   can exist there, and the child still reports a complete document.
10. **Revision.** What a handler mutates is in the first snapshot and the
    revision includes it.
11. **Memory.** M1, M2 and M3 of §7.

Every group that could meet a host which hangs keeps the watchdog the
job-deadline court established: an exact-pid kill, a reap, and the timeout
recorded as the falsification.

## 9. Correction before any code: bubbling, cost, and what the shim is not

Three corrections, recorded in order. Where this section differs from §2, §3
or §7, this section governs. The four boundaries of §3 and every criterion
except the ones named here are unchanged.

### 9.1 `DOMContentLoaded` bubbles, and it bubbles to the window

§3 said `DOMContentLoaded` does not bubble and argued the difference was
unobservable because the document is the root of this tree. That was wrong on
both counts. In the standard the event is `bubbles: true` and its propagation
path continues **past** the document to the window, so a listener registered
on the window does see it — which is exactly how a great many pages register
one. Freezing it as a loss because of my own misreading would have moved this
host away from the standard rather than toward it.

Corrected:

- **`DOMContentLoaded`**: dispatched at the document, `target` is the
  document, `bubbles` is true, and it continues to the window, where a
  listener sees `target` = document and `currentTarget` = window.
- **`load`**: dispatched at the window, `target` and `currentTarget` are the
  window, and it does **not** bubble. A listener on the document never sees
  it.
- **`readystatechange`**: dispatched at the document and does not bubble.

**How the path is built, and what it must not do.** The window becomes the
document's parent **event target**, not its parent node. Nothing appends the
window to the DOM tree, `document.parentNode` stays `null`, and no traversal,
selector or serialisation sees a new node. The dispatch walks an event path
that is the node chain and then, when it reaches the document and the event
bubbles, the window — one explicit step, not a faked link.

### 9.2 Two costs, and only one of them is fixed

§7 said "no resident cost", which is false. The first correction said "small
and fixed", which is also wrong in its second half, because the same sentence
admitted the realm holds every listener a page registers and a page chooses
how many. The two costs are separated:

- **The EventTarget infrastructure** — the global's listener map and its
  `onload` slot — is a **fixed, small, per-realm** cost that exists whether or
  not a page registers anything.
- **The listeners themselves are page-owned and variable.** A page may
  register as many as it likes, and each is a function the realm holds. The
  only bound is the one that already exists: the 16 MiB realm limit and the
  request deadline. **This slice adds no per-listener cap**, and it therefore
  does not claim a tighter bound than those. Claiming one would mean
  pre-registering a listener count cap, which would be a scope expansion this
  slice does not take.

**The two are reported separately and neither is inferred from the other.**
What is **gated** is the workload delta of one **frozen fixture** with a
stated number of listeners. What is **reported without a verdict** is the
quiet page's total owner bytes, inside which the fixed infrastructure lives.
Neither number is a statement about an arbitrary page, and §10.6 says why the
infrastructure gets no gate of its own.

### 9.3 What the window's EventTarget is, and is not

It is a **bounded shim**, not a standard `EventTarget`, and the differences
are recorded rather than left to be discovered:

- **Duplicate listeners are not de-duplicated.** The standard drops a second
  `addEventListener` with the same type, callback and capture flag; this shim
  appends it, so such a listener runs twice. It is the node model's existing
  behaviour, unchanged here, and it is a divergence.
- **No listener options at all**: no `capture`, no `once`, no `passive`, no
  `signal`. A listener is a function in a list.
- **No `handleEvent` objects**: a listener must be a function; an object with
  a `handleEvent` method is ignored.
- **No capture phase and no `stopImmediatePropagation`**, as §6 already
  records.
- `EventTarget` is not exposed as a constructor, and nothing else in the realm
  becomes an event target by this change.

### 9.4 Court, adjusted

The order group additionally asserts that a **window** listener for
`DOMContentLoaded` fires, with `target` the document and `currentTarget` the
window; that a **document** listener for `load` never fires; and that the
window listener runs after the document's own, in path order. The duplicate
listener divergence is asserted as the divergence it is, so the record cannot
drift back into claiming standard behaviour.

## 10. Four criteria that could not prove what they claimed

Recorded before the court changes and before any product code.

**10.1 The duplicate-listener criterion never registered a duplicate.** It
reused the custom event that is added once, dispatched, removed and
dispatched again, and then asserted that the note appeared once — which is
the *removal* criterion asserted a second time. It cannot show that a
duplicate runs twice. It gets its own event type, the same function added
twice, one dispatch, and an observation of **two** notes.

**10.2 `onload = null` was never tested.** The criterion only replaced one
function with another. It now assigns a function, assigns `null`, observes
that the getter reads `null` and that a dispatch of an **independent** event
does not reach it, and only then assigns the pair that the real `load` will
verify — so a hand-dispatched `load` never pollutes the exactly-once count.

**10.3 The "not inert" criterion passed on the old host.** It asserted only
that a snapshot came back, which is true of a page that never ran its
handler. It now asserts that the snapshot **contains what the handler built**.
That the revision group also checks it is not a defence: a named criterion
must not be able to pass vacuously.

**10.4 A child's `readyState` is not observable, so nothing claims it.** §5
says a child may pass through the same transitions, and there is no control
surface that reports a frame's `readyState`. The court therefore proves only
what it can see: the child frame exists, and its script and its lifecycle
handler did not run. The record does not claim the court proved the
transition.

**10.5 The infrastructure delta cannot be isolated, so it is not faked.**
§9.2's M1a wanted the fixed cost measured on its own, and the keep/remove arms
cannot separate it: both build the same document in the same realm. Rather
than invent a delta, the court **reports the quiet page's total owner bytes**
and **gates the kept-versus-removed workload delta**, which is M1. The fixed
infrastructure is inside the reported total and is not given a number of its
own.

**10.6 The infrastructure figure is a diagnostic, not a criterion.** §9.2
first wrote it as a frozen criterion, M1a, comparing the same page across two
builds. The court has one `--binary` and no baseline binary, no paired
repetition and no code that would judge such a thing, so leaving it written as
a criterion would have let a run claim everything passed while never
evaluating it. And a single-point delta across two builds moves with layout
and allocator, so `≤ 65,536` would not be a causal gate anyway.

So it is **demoted to a diagnostic**: the court reports the current quiet
page's total owner bytes, and if an old and a new receipt happen to be
comparable a cross-build delta may be read from them by hand — with **no
verdict attached**. The court is not grown into a two-binary harness for it.

The gates that remain are exactly three: **M1**, the frozen fixture's
listener workload; **M2**, the owner plateau across 128 replacements; and
**M3**, owners returning to zero on close. No result may be worded to imply
that an arbitrary page's listener cost, or the infrastructure delta, passed a
gate.

**10.7 The quiet-page diagnostic was measuring two targets.** The memory group
opened the quiet page while the listener page was still live and then sampled,
so the number labelled "quiet page total" was the sum of both documents. The
label was false and §10.6's description with it. The order is fixed: after the
late page is closed, the quiet page is opened, sampled and closed **alone**;
then the no-listener arm is opened, sampled and closed; then the listener arm
is opened and sampled, and that sample is what M1 and M2 use. **Any sample
labelled a quiet total is taken with only the quiet target live.** It stays a
diagnostic and changes no gate.

## 11. The lifecycle bridge must not be forgeable

The first implementation exposed `__mcsLifecycle` as an ordinary writable
global. A page's own scripts run before the lifecycle, so a page could call
the steps itself, call them out of order, call them repeatedly, or replace the
function outright — and with it the guarantees that the lifecycle runs **after
the scripts**, that each event fires **exactly once**, and that the host, not
the page, decides when a step happens. It also stored listeners on
`target.__listeners`, a property a page can overwrite, which would break the
lifecycle from a different direction. Neither is acceptable and both are fixed
before this slice is qualified.

### 11.1 A capability, not a name

A `__` prefix is not a boundary. The bridge becomes:

- a **non-enumerable, non-writable, non-configurable** property, so a page can
  neither replace nor shadow it;
- guarded by a **realm-private capability** the host mints per realm from a
  cryptographic random source, installed **before any page script runs** into
  a closure the page cannot reach, and passed by the host with every step
  call. A call without it, or with a wrong one, dispatches nothing;
- **phase-ordered inside that closure**: the only step it will run is the next
  one, `1 → 2 → 3 → 4`. Anything out of order, repeated, or after the fourth
  dispatches nothing and answers the same way a wrong capability does.

The capability never appears in a snapshot, an audit record, an error, a
receipt or any page-readable state. It exists in the host's memory for the
realm's life and in one closure inside that realm.

### 11.2 Listeners live in a closure, not on a property

`__listeners` moves off the targets and into a **closure-owned `WeakMap`**
keyed by the target. A page can no longer replace, read or corrupt the
listener store by assigning a property, and a target that dies takes its
listeners with it.

### 11.3 Court

A malicious page is added: it tries to overwrite `__mcsLifecycle`, calls it
with no capability and with a wrong one, calls the steps out of order and
repeatedly, and assigns `window.__listeners`. After all of that the normal
four steps must still be observed **exactly once each, in order**, and the
capability must appear in no snapshot, no audit record and no receipt.
