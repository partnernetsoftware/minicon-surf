# A bounded timer slice for the native route — decision record, design only

Status: **design only. No product code, no court run, no protocol change.**
The behaviour at `ac1ece9` is untouched by this file. The decisions and
hazards in §12 go to the root before anything is implemented.

## 1. What exists today, and why it is worse than nothing

`labs/native-dom/src/dom_shim.js` line 338:

```js
g.setTimeout = (fn, _ms, ...args) => { Promise.resolve().then(() => fn(...args)); return 0; };
g.clearTimeout = () => {};
```

Three things are wrong with that, and they are wrong in the direction this
project refuses:

1. **The delay is discarded.** `setTimeout(fn, 5000)` runs `fn` at the next
   job drain, which is the end of the current host evaluation. A page that
   waits five seconds before doing something does it immediately.
2. **`clearTimeout` cannot cancel.** It returns `undefined` and the callback
   still runs. A page that schedules and then cancels runs the thing it
   canceled.
3. **The handle is a lie.** Every call returns `0`, so two timers are
   indistinguishable and a page that stores handles in a map overwrites them.

None of it is recorded as a loss anywhere. This slice's first purpose is to
replace a silent approximation with either a real bounded mechanism or an
explicit refusal.

## 2. Scope

**In:** `setTimeout(fn, ms, ...args)` and `clearTimeout(handle)`, in the
**main frame only**.

**Out, and refused rather than approximated:** `setInterval` /
`clearInterval`, `requestAnimationFrame`, `requestIdleCallback`, workers of
any kind, any background thread, timers in a child frame (children run no
scripts — `child-frame-design-0.0.1.md` §16 — so a child has nothing to
schedule from), and any clock a page can read. `Date.now()` and
`performance.now()` stay absent: a timer's delay is host-side arithmetic and
the realm never learns what time it is.

## 3. Identity, ownership and lifetime

- A timer belongs to **one realm**. Its handle is a small monotonic integer
  minted per realm, starting at 1, never reused within that realm's life, so
  two live timers are always distinguishable and a stale handle is inert.
- A timer holds its callback, its arguments and its due time. Nothing else.
- **Every timer of a realm is destroyed with that realm**: on navigation, on
  reload, on traverse, on a child ending, on `target.close`. A due timer of a
  document that has been replaced never runs, and its callback is dropped so
  its closure is released. This falls out of the realm's own teardown and
  needs no separate step, which is why timers are owned by the realm and not
  by the target.
- `clearTimeout(handle)` removes a pending timer and releases its callback
  immediately. Clearing an unknown, already-fired or already-cleared handle is
  a no-op, as in HTML.

## 4. Time

The host keeps one **monotonic** clock (`std::time::Instant`). A timer's due
time is the instant it was scheduled plus its clamped delay. The realm has no
clock at all, so a page can neither read the time nor measure elapsed time
through this surface.

`ms` is coerced as HTML does: a non-finite or negative value becomes `0`, and
values are clamped to a bounded maximum (§6). No nesting-level clamp is
implemented; a zero-delay timer is due immediately, and the per-boundary
budget of §5 is what stops a chain of them from running forever.

## 5. When a timer runs

**Only at an operation boundary, never between one.** The host runs due
timers at exactly these points, each of which is already a place where the
realm may execute:

| Operation | Runs due timers |
|---|---|
| `target.wait` | yes, on every poll, and the poll sleeps until the next due timer rather than a fixed interval |
| `target.inspect`, `target.snapshot` | yes, before it observes, so what it reports includes what was due |
| `target.act` | yes, **before** the preflight and not between the two phases |
| `target.navigate`, `reload`, `traverse` | no: the document is being replaced and its timers are about to be destroyed |
| `memory.report`, `session.*`, `profile.*` | no: they observe the host, not a document |

Ordering is by due time, then by handle, so equal delays fire in the order
they were scheduled. A callback that schedules another timer does not make the
new one run in the same drain unless it is due; the drain takes a snapshot of
what is due when it starts.

**Bounded per boundary.** At most `MAX_TIMER_CALLBACKS_PER_BOUNDARY` (candidate
32) callbacks run at one boundary, however many are due; the rest wait for the
next one. That is what stops a page from holding the host inside one request.

## 6. Bounds

| Bound | Candidate | Why |
|---|---|---|
| pending timers per realm | 64 | a page that schedules more gets a refusal from `setTimeout`, which throws in the realm exactly as an over-limit allocation does |
| callbacks run per operation boundary | 32 | one request cannot be held by a chain of zero-delay timers |
| maximum delay | 2^31 − 1 ms | HTML's own overflow point; larger values clamp to it rather than wrapping |
| callback memory | none of its own | a callback and its closure live in the realm and are already inside `REALM_MEMORY_LIMIT` (16 MiB); a timer adds a handle, an instant and a reference |

## 7. Revision, and the guard timers make load-bearing

A due callback may mutate the DOM. Those mutations move the main frame's
counter through the `MutationObserver` that is already installed, so they move
the **target-global revision** through the one checked helper, exactly like
any other page-driven mutation. Nothing about the revision model changes.

Two consequences must be stated rather than discovered:

1. **`frame-action-design-0.0.1.md` §15's cache proof still holds**, and only
   because children stay script-free. A child has no timers, so a child's
   counter still moves only under a host evaluation in its realm. If timers
   were ever granted to children, that proof and `target.wait`'s single
   evaluation would both have to be reopened.
2. **§29's preflight signature stops being theoretical.** A due timer is a
   second way for the document to change between the two phases of an
   activation. The design already re-derives and compares the whole effective
   activation before dispatch, which covers it; §5 additionally forbids
   running timers *between* the phases, so the two guards agree rather than
   race.

## 8. Failures, typed

- **A callback throws.** The exception is caught, the timer is discarded, and
  a bounded per-target counter records it. The target does **not** crash: a
  timer is asynchronous and a thrown callback is not evidence that the
  document is unusable. This differs from a script that throws during a
  document build, which still fails the build, and the difference is
  deliberate.
- **A callback exceeds the request's deadline.** The realm's interrupt handler
  already stops execution at the deadline; the operation answers
  `deadline_exceeded` as it does for any long evaluation, and the timer is
  discarded so the next request is not held by the same callback.
- **`setTimeout` over the pending bound** throws in the realm, which surfaces
  as the page's own error, not as a host failure.
- **A handle that is unknown** is a no-op for `clearTimeout`.

## 9. What the host reports

`memory.report` gains one owner beside the existing ones:

```
"timers": { "objects": <live pending across realms>,
            "object_limit": MAX_PENDING_TIMERS_PER_REALM * live realms,
            ... the six attribution counters of §14.1 ... }
```

All counters are host-minted integers. No callback source, no delay
distribution and no page text ever appears.

Whether `target.inspect` should also carry a pending-timer count is a decision
(§12.4), because that is an additive result field and this increment is
otherwise shape-neutral.

## 10. What stays a loss

- `setInterval`, `requestAnimationFrame`, `requestIdleCallback`, workers.
- Timers in a child frame.
- Any clock readable from the realm.
- CDP: `Runtime.evaluate` with `awaitPromise`, `Runtime.callFunctionOn` with
  timers, `Emulation.setVirtualTimePolicy` and every other timer-adjacent CDP
  method stay unimplemented and are recorded as losses in both mappings.
- Real elapsed-time fidelity: a timer runs at the first operation boundary
  **at or after** its due time, so a 10 ms timer with no requests for a second
  runs a second late. This host has no background thread by design; the delay
  is a lower bound, and that is a loss rather than an approximation.

## 11. The frozen court

`labs/native-dom/timer-court.py`, hermetic, headless, both allocators, to be
written before any host change and to fail until the slice exists.

1. **Delay is honoured.** A page schedules at 0 ms and at 50 ms; a snapshot
   taken immediately sees only the first callback's effect, and one taken
   after the host has waited past 50 ms sees both.
2. **`clearTimeout` cancels.** A cleared timer never runs, proven by a mark it
   would have written, and clearing an unknown or already-fired handle is a
   no-op.
3. **Handles are distinct.** Two timers scheduled in one turn get different
   handles, and clearing one leaves the other.
4. **Ordering.** Equal delays fire in scheduling order; a smaller delay
   scheduled later fires first.
5. **Only at boundaries.** No callback runs while the host is idle: a mark is
   absent after a sleep with no request, and present after the next
   observation.
6. **The per-boundary budget holds.** A chain of zero-delay timers advances by
   at most the budget per operation and never holds one request open.
7. **The pending bound holds.** Scheduling past the bound throws in the realm
   and the host answers the page's error, with the live count at the bound.
8. **Teardown.** After a navigation, a reload, a traverse and a close, a timer
   scheduled before it never runs and the owner count returns to zero.
9. **Revision.** A due callback that mutates advances the target-global
   revision by what the observer counted, and `target.wait` returns when a
   timer's mutation reaches the awaited revision, without a fixed-interval
   sleep.
10. **A throwing callback** is counted, discarded, and leaves the target
    usable: the next observation succeeds and the revision is intact.
11. **Deadline.** A callback that outlives the request's deadline answers
    `deadline_exceeded`, the timer is gone afterwards, and the target still
    answers the next request.
12. **Children.** A child frame has no timer surface: the shim in a child
    realm exposes `setTimeout` as the same refusal every other unmodelled
    feature gets, and no child timer can be scheduled or fire.
13. **Secrecy.** No callback source, delay, or page text in the ledger, the
    court log or the receipt; the timer owner reports integers only.
14. **CDP.** The timer-adjacent methods stay `-32601`.

## 12. Candidate memory and latency criteria, pre-registered

To be frozen with the court, from the published evidence of the earlier
slices, and never moved afterwards:

| # | Workload | Candidate criterion |
|---|---|---|
| T1 | 64 pending timers on one target vs the same page with none | live owner bytes ≤ 65,536, i.e. the form court's realm plateau, since a pending timer is a handle, an instant and a reference |
| T2 | 64 scheduled and cleared, 64 cycles | owner bytes return to the no-timer baseline exactly; `dropped_total` equals 4,096 |
| T3 | a chain of 4,096 zero-delay callbacks across boundaries | process footprint growth over the last half no worse than the first half; absolute retention reported, not gated |
| T4 | navigation with 64 pending | owners return to the one-document baseline and `dropped_total` accounts for every one |
| T5 | latency | a `target.wait` for a 50 ms timer returns within 50 ms + 20 ms of scheduling, measured host-side; reported, and gated only as an upper bound of 250 ms so a fixed-interval sleep cannot pass it |

## 13. The decisions and hazards for the root

1. **Implement or refuse?** The honest alternative to implementing timers is
   to make `setTimeout` throw `unsupported_capability`-style in the realm and
   record it as a loss. Today's shim is worse than either. My recommendation
   is to implement the bounded slice, because an agent-facing browser that
   silently runs canceled callbacks is a correctness hazard for every page
   that uses a debounce.
2. **Determinism versus monotonic time.** The ruling fixes monotonic host
   time. It follows that a court run's timing is not reproducible and that a
   timer's delay is a lower bound tied to when requests arrive. If
   reproducibility matters more than fidelity, the alternative is a virtual
   clock that advances to the next due timer at each boundary, which makes
   runs exact and removes T5 entirely. I have designed for the ruling and I am
   flagging the cost rather than reopening it.
3. **The exception policy.** §8 keeps the target alive when a callback throws,
   which differs from a build-time script. Confirm.
4. **`target.inspect`'s pending count.** Additive result field, or keep it in
   `memory.report` only? The rest of this slice is shape-neutral.
5. **The hazard I would most want reviewed.** Running timers at observation
   boundaries means `target.snapshot` can change the document it is about to
   report. The design runs due timers *before* observing, so the snapshot is
   consistent with itself, but an agent that snapshots twice with no action in
   between can now see different documents. That is true of any page with
   timers and it is new to this host, so it belongs in the record and in the
   README rather than in a surprise.

## 14. The root's rulings, and the refinements they came with

§§1–13 stay as written; where this section differs it governs.

**D1 — implement.** Today's silent zero-delay, no-cancel shim is
unacceptable, so the bounded slice is built rather than refused.

**D2 — monotonic host `Instant`, not virtual time.** The consequence is
recorded as a loss rather than smoothed over: a timer runs at the first
operation boundary **at or after** its due time, so its delay is a lower
bound, and with no requests arriving nothing fires.

**D3 — a throwing or deadline-interrupted callback is discarded and the
target stays usable.** Whatever that callback completed **before** it threw
stands, and the revision reports it: the observer already counted those
mutations and nothing rolls them back.

**D4 — `target.inspect` carries `timers: {pending, limit}`** and nothing
else. No callback, no source, no arguments, no delay and no next-due time:
those are page data or a timing channel.

**D5 — two observations may differ**, because a timer is page activity. Due
timers run **before** an observation and the observation returns the global
revision that results, so an agent's staleness and wait stay deterministic
against the number it was given.

### 14.1 Attribution, not one bucket

§9's `dropped_total` conflated outcomes that mean different things. The timer
owner reports these six separate saturating integers, one per **timer**
outcome — §15.3 adds a seventh that is not a timer outcome at all but a
host-side fault, and §15.7 reconciles the two:

| counter | means |
|---|---|
| `fired_total` | a callback ran to completion |
| `cleared_total` | removed by `clearTimeout` |
| `retired_total` | destroyed with its realm by navigation, reload, traverse or close |
| `threw_total` | the callback threw and the timer was discarded |
| `deadline_discarded_total` | the callback was interrupted at the request deadline and discarded |
| `refused_total` | `setTimeout` was refused because the pending bound was full |

`pending` and `limit` are the live figures; every one of these is a
host-minted integer.

### 14.2 Handles fail closed before the safe integer

A handle is a per-realm monotonic integer starting at 1 and **never reused**,
including after `clearTimeout`. It is also a JavaScript Number, so the same
limit that binds a frame's counter binds it. The rule is **exclusive and
frozen here**: a handle is minted only while the realm's *next* handle is
**strictly less than** `Number.MAX_SAFE_INTEGER`, so the largest handle ever
issued is `MAX_SAFE_INTEGER − 1` and the value `MAX_SAFE_INTEGER` itself is
never handed to a page. The alternative — allowing `MAX_SAFE_INTEGER` exactly
and keeping a separate exhausted flag — was considered and not taken, because
it buys one handle at the cost of a second piece of state whose only job is to
remember that the counter can no longer be advanced exactly. Refusals are
counted in `refused_total`; a realm that exhausts handles keeps its existing
timers and schedules no new ones. See §15.5 for the seam that proves it.

### 14.3 Only a callable callback

`setTimeout(string)` is **not** evaluated: string bodies, and any other
coercion HTML performs that this host does not model, throw in the realm
rather than being approximated. A first argument that is not callable throws.
This is a refusal, not a compatibility claim.

### 14.4 Cancellation is synchronous

`clearTimeout(handle)` removes the timer inside the same realm turn and
releases its callback immediately, so a callback cleared in one turn cannot
run in the next drain. An unknown handle, an already-fired one and an
already-cleared one are each a no-op, as in HTML.

### 14.5 A callback's own zero-delay timer waits for the next drain

A drain takes its snapshot of what is due when it starts. A zero-delay timer
scheduled *by* a callback is due immediately but is not in that snapshot, so
it runs at the next boundary. That is what keeps the 32-per-boundary bound
meaningful: a chain of zero-delay timers advances one boundary at a time
rather than filling a single drain.

### 14.6 The court must fail meaningfully first

Before any host change, the frozen court is run against `ac1ece9` and its
failures recorded. The three that matter are the ones that describe today's
defect rather than an absent feature: **the delay is honoured**, **`clearTimeout`
cancels**, and **handles are distinct**. Each must fail there, because each is
false of the current shim rather than merely unimplemented.

### 14.7 Unchanged

Child frames have no timer surface, no clock is readable from a realm, no
schema, request or result shape moves beyond D4's additive
`target.inspect` field, no background thread exists, and nothing visual runs.

## 15. Answers to the implementation audit

**15.1 A due time is never earlier than its schedule.** The host reads its
clock **after** the collecting evaluation has returned and been parsed, and a
due time is that instant plus the delay. Since the realm ran the `setTimeout`
call inside the turn that the collect terminates, the schedule happened before
that instant, so `due ≥ schedule + delay` always. The delay stays a lower
bound and can only ever be longer, never shorter, which is the direction §14's
D2 loss already allows. Timers a document's own scripts scheduled are
collected at the end of the build, so their delays start from that document
rather than from whichever operation observes it first.

**15.2 The reported limit is per owning realm.** `memory.report`'s
`owners.timers.object_limit` is `MAX_PENDING_TIMERS × live timer-owning
realms`, which is one per live target because a child runs no scripts and owns
none. `target.inspect`'s `timers.limit` stays the per-target bound. With one
target the two agree; with several they no longer pretend to.

**15.3 A failed collect is attributed, not swallowed.** If the collect
evaluation fails, its answer will not parse, or its shape is not the one this
host accepts, a schedule the realm had already recorded may be lost with it.
That is counted in `collect_failed_total` beside the other six, so the ledger
shows it rather than a timer silently never firing. It is the seventh
attribution counter and the only one that reports a host-side fault.

**15.4 Retirement is counted wherever a realm is replaced.** Navigation,
reload and traverse all replace the document through the same swap, and close
goes through the same target teardown, so each adds the pending count to
`retired_total`. The court proves the navigation and the close paths, and the
reload path through the same swap.

**15.5 The handle boundary, pre-registered.** The rule §14.2 freezes, stated
once more as the code enforces it — `next >= MAX_SAFE_INTEGER` refuses:

- the largest handle ever issued is **2^53 − 2**, which is
  `MAX_SAFE_INTEGER − 1`;
- the value `MAX_SAFE_INTEGER` is never issued, because issuing it would
  leave `next` unable to advance exactly;
- the refusal is `refused_total`, and the realm throws.

Design and code are one integer apart nowhere: the court seeds the boundary
and counts what can still be minted. A court-only seam seeds a realm's next
handle so the boundary is reachable: with the next handle at 2^53 − 4 exactly
three more timers can be scheduled — 2^53 − 4, 2^53 − 3 and 2^53 − 2, the
largest a handle ever takes — and with it at 2^53 − 1 none can.

### 15.6 The CDP group, and the client that was missing

§11's group 14 — the timer-adjacent CDP methods staying `-32601` — is not in
the court file. When this slice was implemented the pinned client package was
absent from the ignored `target/labs/d4`, which also stopped three CDP checks
in the child-frame, form and navigation courts from running; each reported the
absence rather than passing quietly, which is the behaviour they were written
with.

The client has since been restored **from the local npm cache with no network
fallback**, and verified before anything ran: `puppeteer-core` 24.15.0, the
integrity `sha512-2iy0iBeW…` of the committed qualification, on Node.js
v26.7.0, all three matching `cdp-qualification-0.0.1.json` exactly. Those four
courts were rerun in full on the same host build and their CDP groups pass.

The timer slice's own CDP group is now written and passes with that client;
§20 records the boundary it asserts, which is not the single `-32601` this
section first assumed.

### 15.7 Reconciling the seventh counter with §14.1

§14.1 said six counters "and no other", and §15.3 then added
`collect_failed_total`. The wording, not the design, was wrong, and the
distinction it should have drawn is this:

- **Six timer outcomes.** `fired`, `cleared`, `retired`, `threw`,
  `deadline_discarded` and `refused` partition what happens to a timer. Every
  timer that leaves the pending set leaves it through exactly one of them, and
  that set is closed: nothing else may be added to it without saying which
  outcome it splits.
- **One bridge fault.** `collect_failed_total` counts something that happened
  between the host and the realm, not to a timer. It does not partition
  anything and it is not a lifecycle outcome: it says the host could not read
  what the realm had recorded, so a schedule may have been lost without any of
  the six being able to describe it. **A page can cause it**, because §16.5 is
  right that a page shares the realm and can perturb the contents of its own
  bridge; what it buys by doing so is the loss of its own timers and a counter
  that says the host noticed. It exists precisely so that such a loss is
  visible rather than silent.

So the owner reports seven integers: six that classify timers and one that
admits the host failed. §14.1's "and no other" governs the first group only,
which is what it was written to fix.

## 16. Five blockers from the qualification audit, recorded before any fix

### 16.1 The court is a subset of what §11 froze, so 38 of 38 is not qualification

Three frozen groups are missing from `timer-court.py`: **group 9**, a
`target.wait` that converges on a revision a timer's mutation reached without
a fixed-interval sleep; **group 11**, a callback that outlives the request
deadline; and **group 14**, the timer-adjacent CDP methods. §12's **T1–T5**
memory and latency criteria are absent too, and two of them still name the
`dropped_total` that §14.1 replaced.

So the 38 of 38 already recorded is **mechanics only**, and this record says
so rather than letting the number stand for the frozen court. The caps do not
move: T1–T5 are implemented as §12 froze them, with `dropped_total` replaced
by the counter that actually accounts for each one — **T2 is 64 scheduled and
cleared over 64 cycles, so its counter is `cleared_total` and its criterion is
exactly 4,096**, while **T4 is a navigation with 64 pending, so its counter is
`retired_total` and its criterion is exactly 64**. Both are exact deltas, not
lower bounds, so the six-outcome partition is genuinely exercised: a timer
counted in the wrong bucket fails one of them.

### 16.2 The build's own collect is a second, unvalidated bridge

`build_target` inlines its own collect after the document's scripts run, and
that copy: ignores an evaluation, parse or shape failure instead of
attributing it; never adds the realm's `refused` count; and **drops clear
records entirely**, so a timer the page scheduled and cleared in the same turn
stays in the host's pending list until it comes due, inflating
`timers.pending` and being counted as a clear only when it is finally reached.
Two bridges with different rules is one too many: the build path uses the
validated, attributed collect, and there is exactly one implementation.

### 16.3 The realm has clocks, and the design said it had none

§4 claimed "the realm has no clock at all, so a page can neither read the time
nor measure it". That is **false**, and a probe proves it: this engine
supplies `Date` as a function with a real epoch and `performance` as an
object. What is genuinely absent is `setInterval`, `requestAnimationFrame`,
`requestIdleCallback` and `Worker`, all `undefined`, so a page calling them
throws — but that is absence, which the audit is right to say must be proven
rather than assumed.

Corrected, and the root has ruled how: **this slice adds no clock API and uses
no realm-readable clock for scheduling** — that is all §4 may claim. `Date`
and `performance` are shipped behaviour inherited from the engine, and they
are **not** removed or poisoned here: doing that inside a bounded timer slice
would be a broad compatibility regression hidden in an increment that has
nothing to do with them, and it runs against the browser direction. Their
fidelity, determinism and privacy are an explicit **separate gap**, recorded
as such and not closed by this work.

What the court asserts instead is that this implementation neither replaces
nor widens their pre-fix surface: the observed shape of `Date`, `Date.now`
and `performance` is the same before and after, and no epoch value is
asserted. `setInterval`, `requestAnimationFrame`, `requestIdleCallback` and
`Worker` stay absent, and the court proves that rather than assuming it.

### 16.4 A malformed entry inside a well-formed list was ignored

The collect validates that `moved` is an array and then skips any entry it
cannot read. A pair that is not a bounded two-element tuple of a non-negative
handle and an integer delay is therefore silently discarded, which is the same
class of silence as 16.2. Every entry is now validated as a whole; one that
fails counts in `collect_failed_total` and the collect stops rather than
continuing over a list it does not understand.

### 16.5 The bridge is a page-visible global, and always was

`window.__mcsTimers` is reachable and mutable from page script. So is
`window.__mcs`, which carries the revision and the snapshot's node table, and
that predates this slice by every increment. A same-realm bridge cannot be
hidden from a page that shares the realm: there is one global scope and the
host's own evaluations run in it.

What can be done, and is: the binding is made non-writable, non-configurable
and non-enumerable, so a page cannot replace or shadow it; and every read of
it is validated and attributed (16.4), so a page that corrupts its own bridge
loses its own timers and cannot make the host believe something false. What
cannot be done without a second authority — a separate realm or world — is to
stop a page from perturbing its own state, and this record does not pretend
otherwise. **The trust boundary is recorded: the host trusts host-internal
globals only as far as it validates them, and a page can cost itself its own
timers.**

## 17. What this audit leaves open

1. **`Date` and `performance` are a separate gap, ruled out of this slice.**
   A page can read the wall clock and measure elapsed time through them today,
   on every route, and this host models neither. That is recorded as its own
   problem — fidelity, determinism and privacy — for its own increment, and
   nothing here changes them.
2. **Whether the timer CDP group should assert `-32601` for named methods**
   or simply record that no timer method is qualified. The methods are
   unimplemented and answer `-32601` through the same default as every other
   unqualified method, so a court group here restates an existing guarantee;
   it is cheap and it closes §11's group 14 honestly.

## 18. Five more from the extended audit, recorded before the fix

### 18.1 A deadline lost the rest of the due batch

`run_due_timers` took the whole due batch out of the host's queue before
running any of it. If the first callback hit the deadline, the boundary
returned and callbacks 2..N were gone from the host's queue while their
callbacks were still sitting in the realm's map — pending forever, never due
again, never counted. **The host queue gives up an entry only when that entry
has actually been attempted**: due entries are taken one at a time, and
anything not attempted stays where it was.

### 18.2 A clear from inside a running callback was not attributed

A callback may clear another timer that is already in the boundary's due
snapshot. The realm removes that callback and emits its clear record, but the
host was only counting a clear when the handle was still in its own queue —
and `take_due` had already removed it. The clear was real and went uncounted.
The realm emits `-1` **only when it actually removed a pending callback**, so
that record is counted exactly once on its own authority, independently of
whether the host's queue still holds the handle. Removing it from the host
queue stays a separate, idempotent step.

### 18.3 T1 was comparing two different pages

The draft measured `/landed.html` against a page full of timers, so the
difference contained two different documents, two different scripts and two
different realms as well as the timers. T1 now uses **one page with a switch**
that suppresses scheduling, written so both forms have the same byte length
and the same script shape, and the difference is the timers alone.

### 18.4 T2 must be the frozen workload

§12 froze T2 as 64 scheduled and cleared, 64 cycles, and the draft substituted
eight open-and-close cycles. The frozen workload is what runs, and its
criterion is an exact `cleared_total` delta of 4,096 with `retired_total` and
`fired_total` unmoved.

### 18.5 T3 is missing, and T5 needs a defensible reference

T3 — a chain of 4,096 callbacks across boundaries, first-half against
last-half footprint growth — is implemented as §12 froze it, reported rather
than gated except for the no-acceleration bound. T5 measures from the moment
the court asks for the wait, which is the last host-observable point before
the timer can fire, and only §12's 250 ms upper bound gates; the elapsed value
is reported beside it.

No cap moves in any of this.

## 19. A hang I caused against the old build, and what it exposed

Running the extended court against the pushed host wedged it: the
`/forever.html` fixture's callback reached the **old** shim, which ran it as a
promise microtask during the document build, and the host spun at full CPU for
over six minutes without its request deadline interrupting the job. The root
terminated that one process; a check for residue afterwards found none.

Three things are recorded from it.

**19.1 The court must not be able to do that again.** The deadline and
stuck-callback groups run only against a host that has this timer surface at
all, which the court establishes by asking `target.inspect` for its `timers`
field. Against a host without one they record a fixed reason instead of
sending a fixture that host cannot survive. The falsification of the three
defects §14.6 names — delay, cancel, distinct handles — does not depend on
those groups and still runs.

**19.2 The new build must still prove it, and does.** A callback that never
returns is interrupted at the request's deadline, discarded, counted in
`deadline_discarded_total`, and the next request answers; a timer queued
behind it still runs at that next boundary. Both are court criteria on the new
build, and they pass there.

**19.3 A shipped gap this exposed, not this slice's to close.** The hang was a
pending **job** that ran without an effective deadline, not a timer. This
slice removes the route that reached it — `setTimeout` no longer queues a
microtask, so a page cannot get a callback into the job queue through it — but
it does **not** close the general path: a page that writes
`Promise.resolve().then(loop)` still queues a job of its own, and
`drain_jobs` checks its deadline only *between* jobs. That is a real defect in
the shipped host, it predates this work, and it is recorded here for its own
increment rather than widened into this one.

## 20. The CDP boundary, corrected before the group is written

§10 said the timer-adjacent CDP methods "stay unimplemented and are recorded
as losses", which flattens two different refusals into one. The honest
boundary, checked against the code:

| Method | Answer | Why |
|---|---|---|
| `Runtime.evaluate` | **-32601**, method not found | this adapter implements no such method at all |
| `Emulation.setVirtualTimePolicy` | **-32601**, method not found | the same: the domain is not implemented |
| `Runtime.callFunctionOn` with a timer declaration | **-32602**, invalid parameter | the **method exists and is qualified**, for exactly one declaration — `function(){this.click();}` — so any other declaration, timer or not, is refused as a parameter this adapter does not accept |

The difference matters: `-32601` says the adapter has no such method,
`-32602` says it has the method and will not accept that argument. Nothing is
added to make a timer declaration answer `-32601` instead. Recognising timer
source text would mean parsing a page's function body to choose an error
code — brittle, and a new dependency on page text for a diagnostic — and it is
deliberately not done.

So the timer losses are recorded in both CDP mappings with that split, and
§11's group 14 asserts all three outcomes with the pinned client rather than
asserting one code for all of them.
