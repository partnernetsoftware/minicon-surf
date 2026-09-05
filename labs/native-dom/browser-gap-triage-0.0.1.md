# Standard-browser gap triage — read-only, design only

Status: **triage only. No product code, no court run, no protocol change.**
Every claim below is measured against the current build
(`561062d146e60170`) with a read-only probe through the existing control
door, not inferred from reading the shim.

The two purposes decide what qualifies: a candidate must buy real-web
compatibility that an agent can act on, and it must not introduce resident
memory, an unbounded surface, or a second authority.

## 1. What the probe found

One page, one snapshot, on the shipped host:

```
ready=loading            history=undefined         classList=undefined
CustomEvent=undefined    XHR=undefined             location.assign=undefined
window.addEventListener=undefined                  document.addEventListener=function
DOMContentLoaded fired = none
location.href = "/other.html"  →  set succeeded, page sees the new value,
                                  host committed nothing (url still p.html)
```

Three of those are the candidates. The rest are recorded as known absences
with no candidate attached yet.

## 2. Candidate A — the document lifecycle

**What is missing.** `__mcsComplete()` sets `document.readyState` to
`"complete"` and dispatches **nothing**. No `DOMContentLoaded` reaches a
listener, and `window.addEventListener` does not exist at all, so a page
cannot register a `load` handler even in principle. Measured: a listener
registered on `document` never fires.

**Agent value: the highest of the three.** A great many real pages build their
content in `DOMContentLoaded` or `load`. Against this host they stay as the
server sent them, so a snapshot shows an un-initialised document and an agent
acts on a page that never came up. Nothing in the contract is wrong; the page
simply never ran.

**Standard semantics, bounded.** `readyState` goes `loading` →
`interactive`, at which point `DOMContentLoaded` is dispatched on the document
and bubbles; then `complete`, at which point `load` is dispatched on the
window and does not bubble. Both fire exactly once, in that order, after the
document's own scripts have run. Deferred and async script ordering is **not**
modelled and is a recorded loss.

**Memory and safety.** No resident cost: two Event objects per document,
dispatched into listeners the page already owns. No new authority, no new
protocol surface, and no new way to reach the network. Handlers run inside the
build's existing deadline, and anything they queue is already bounded by the
timer and job work just landed — this candidate is the first that *composes*
with those rather than needing its own bound.

**Evidence gap.** No court asserts any lifecycle event today.

**Minimal court.** A page that builds its content in `DOMContentLoaded` shows
it in the first snapshot and in the revision; `DOMContentLoaded` precedes
`load`; each fires exactly once even across a reload; a handler that throws is
typed like any other build script; a handler that schedules a timer or queues
a job is bounded by the same deadline, and an infinite one is interrupted.

## 3. Candidate B — page-initiated navigation

**What is missing, and it is worse than missing.** `location.assign` and
`location.replace` do not exist, and `location` is a plain object, so
`location.href = "/other.html"` **succeeds silently**: the page reads back the
new value and believes it navigated, while the host committed nothing. That is
the same shape of defect as the old `setTimeout` — a silent approximation
rather than an honest absence.

**Agent value: high.** Login flows, redirects and single-page entry points
navigate this way. Today they leave the agent on a page the site considers
already left.

**Standard semantics, bounded.** An assignment or `assign()` navigates the
frame through **exactly** the path an activation already uses: the same
preflight, the same origin, scheme, bound and policy rules, the same atomic
build-then-swap. `replace()` differs only in not adding a history entry, which
the bounded ring already expresses.

**Memory and safety.** No resident cost. The real hazard is **re-entrancy**: a
page that assigns `location.href` *during its own build* asks the host to
navigate a document that does not exist yet, and one that assigns it from a
timer callback asks mid-boundary. Both need a rule before any code, and that
is why this candidate is second rather than first.

**Evidence gap.** Measured above: the setter succeeds and nothing commits.

**Minimal court.** An assignment during a build is refused typed and the
document still commits; an assignment from a handler or a timer navigates
through the same path and answers the same result shape; a refused scheme,
origin or bound is refused exactly as the same URL in a link would be;
`replace()` leaves the history length unchanged where `assign()` grows it.

## 4. Candidate C — `classList`, and a window that is an event target

**What is missing.** `classList` is undefined, so a page can only change
classes through `setAttribute("class", …)`. The asymmetry is the point: the
snapshot's selector engine already matches `.x`, so this host can *query* a
class a page cannot idiomatically *change*. `CustomEvent` and an
`EventTarget`-shaped `window` are absent for the same reason candidate A
needs the latter.

**Agent value: moderate and broad.** Class toggling is how most pages express
state an agent then reads back through a selector.

**Memory and safety.** Trivial: a token list over an existing attribute, and
one event class. No new authority.

**Evidence gap.** Measured undefined.

**Minimal court.** `add`, `remove`, `toggle` and `contains` reflect into
`getAttribute("class")` and into `querySelector(".x")`; each mutation advances
the revision exactly once; `CustomEvent` carries its `detail` through a
dispatch.

## 5. Recommendation

**Candidate A, the document lifecycle, as the next slice.** It buys the most
real-web behaviour per line, it introduces no authority, no resident memory
and no protocol change, and it is the one that composes with the timer and job
bounds already landed rather than needing its own. It also has the cleanest
falsification: a page that initialises in `DOMContentLoaded` is inert today
and must not be after.

**Candidate B second**, once the re-entrancy rule is ruled: it closes a silent
lie, which is worth more than most features, but it touches navigation
identity and needs a decision before code.

**Candidate C third**, small enough to ride with either.

## 6. Recorded absences with no candidate

`history`, `pushState`/`popstate`, `XMLHttpRequest`, `AbortController`,
deferred/async script ordering, and `location.hash` as a same-document
navigation. Each is a real gap; none is proposed here, and none is claimed to
be small.

## 7. What this triage does not do

It changes no product code, runs no court, and asks for no protocol change.
The probe used the shipped host through the existing control door and left no
process behind.
