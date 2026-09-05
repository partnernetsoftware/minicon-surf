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
| M1 | a page with lifecycle listeners costs at most 65,536 live owner bytes more than the identical page whose listeners are removed before the lifecycle runs |
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
