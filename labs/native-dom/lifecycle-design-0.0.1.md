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
| M1a | the **infrastructure**: a page that registers no listeners at all costs at most 65,536 live owner bytes more on the lifecycle build than the same page on the build before it — a cross-build number, reported with both figures |
| M1b | the **frozen fixture's workload**: that page with its stated listeners costs at most 65,536 live owner bytes more than the same page with none. It bounds that fixture and no other page |
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

**M1 reports the two separately** and never infers one from the other: the
infrastructure delta measured with a page that registers **no** listeners, and
the workload delta of one **frozen fixture** with a stated number of
listeners. Neither number is a statement about an arbitrary page, and the
record says so.

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
