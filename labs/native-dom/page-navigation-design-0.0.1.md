# Page-initiated navigation — decision record, design only

Status: **design only. No product code, no court run, no protocol change, no
push.** §10 lists the blockers that remain.

## 1. The defect, measured

On the pushed host, `location` is a plain object literal and `location.assign`
and `location.replace` do not exist. So

```js
location.href = "/other.html";
```

**succeeds silently**: the page reads back the new value and believes it
navigated, while the host committed nothing and the target's URL is unchanged.
That is not an absence — it is the same shape of silent approximation the old
`setTimeout` was, and closing it is the whole point of this slice.

## 2. Scope

**In:** assigning `location.href`, `location.assign(url)`,
`location.replace(url)`, in the **main frame only** (ruling 4: children run no
scripts, so nothing there can raise an intent).

**Out, and not invented here:** `history`, `pushState`, `replaceState`,
`popstate`, `location.hash` as a same-document navigation, `location.reload()`,
and every other member of `location` beyond the three above. Assigning
`location.search`, `location.pathname` and the rest stays what it is today —
a write to a property — and is recorded as a loss rather than quietly made to
navigate.

## 3. The core rule: an intent, never a re-entrant navigation

A realm evaluation **never** re-enters fetch, build or swap. When a page
assigns `location.href` or calls `assign`/`replace`, the realm records a
**navigation intent** into a host-owned sink and returns; nothing about the
live document changes at that moment.

The host consumes the intent **at a boundary**, which is exactly where it
already consumes everything else: after the current evaluation **and** after
its pending-job drain have finished. Two places are explicitly excluded:

- **Never between the two phases of an activation preflight.** The preflight
  approves a signature and the activation re-derives it; a navigation
  consumed in between would change the document under an approval that was
  already given. Timers are already forbidden there, and intents join them.
- **Never inside a lifecycle step.** The four steps run one evaluation each;
  an intent raised in `DOMContentLoaded` is consumed after that step's drain,
  before the next step begins, so the sequence cannot be interleaved with a
  document replacement halfway through an event.

`location.href` also becomes an **accessor**: the getter answers the
document's URL as it does today, and the setter records the intent. It no
longer lies by mutating a plain property.

## 4. One intent per realm, last write wins

A realm holds **at most one** pending intent. A second write in the same turn
**overwrites** the first, and the last write before the boundary is the one
the host consumes.

That is the browser-compatible choice and it is worth saying why rather than
asserting it. In a browser, each assignment queues a navigation and the
navigation algorithm cancels any ongoing one for that navigable, so a turn
that writes three times ends up at the third URL: the earlier ones are
started and abandoned, never *committed*. This host reaches the same
observable end state without starting anything it will abandon — no fetch, no
budget spend, no partial build for a URL the page has already replaced. The
divergence, recorded rather than hidden: a browser may have *begun* the first
fetch, so a server could observe a request this host never makes. That is the
memory-first choice and it is deliberate.

`replace` and `assign` differ only in what they write to history (§6); a later
write of either kind replaces an earlier intent of either kind, and the
**kind of the last write** decides the history behaviour.

**Falsifier:** a page that writes `/a`, then `/b`, then `/c` in one turn
commits `/c`, exactly once, with one fetch of `/c` and none of `/a` or `/b`
observed by the fixture server.

**No queue.** There is no unbounded backlog: one slot, overwritten.

## 5. Reusing the existing path, adding no authority

A consumed intent goes through **exactly** the path an activation-initiated
navigation already uses. Nothing new is written: the same URL preflight
(malformed, scheme, `MAX_URL_BYTES`), the same policy, TLS and address rules,
the same per-document fetch and byte budget, the same atomic build-then-swap
where the candidate is complete before the live target changes, the same typed
failures, and the same audit that records an origin and never a path, a query
or userinfo. A page cannot reach anything through an intent that it could not
reach through a link.

## 6. History

- `location.href = …` and `location.assign(…)` **add** one metadata-only
  entry to the bounded ring, exactly as a link activation does.
- `location.replace(…)` **replaces** the current entry: the ring's length and
  its forward entries are unchanged, and the position does not move.

Nothing else about history changes, and no page-visible history API appears.

## 7. An intent raised while the document is still being built

This is the case that needs a rule rather than an accident. A page's top-level
script can assign `location.href` before its own document has ever been
committed. The document being built is **not** live: no target exists, no
realm is observable, no history entry has been made.

**The rule: a finite redirect-like chain inside one operation.** The
`target.open` or `target.navigate` that is running consumes the intent and
builds the *next* candidate instead, up to a cap. Its properties:

- **Cap: 3.** The same number as `net::MAX_REDIRECTS`, chosen so a
  script-driven chain cannot outrun what an HTTP redirect chain is already
  allowed to do. A fourth link answers `resource_limit` with the fixed reason
  `navigation_chain_limit`, and the operation fails without committing
  anything.
- **One deadline for the whole operation.** The chain does not extend it; the
  request's absolute deadline covers every link, and exhausting it answers
  `deadline_exceeded` as any other overrun does.
- **One budget.** Every link spends the same per-document allowance, so a
  chain cannot buy itself more fetches than one document has.
- **No intermediate document is ever observable.** Only the last link is
  committed. No target id, no realm, no revision and no history entry exists
  for an abandoned link, and the history entry made at the end is the
  committed URL alone.
- **Failure is atomic**: if any link fails, the whole operation fails and the
  target that was being opened does not exist. For a `target.navigate`, the
  live document is untouched, exactly as a failed navigation already is.

## 8. An intent raised from a committed, live realm

A lifecycle handler, a timer callback, a queued job or an action handler can
raise an intent. The host consumes it at the next boundary, and:

- **What the handler already did stands.** Its mutations, and the revision
  they moved, are kept — the same honesty rule the failed form submit
  established.
- **If the navigation fails, nothing else moves.** The target id, the frame
  id, the generation, the realm id, the history and the committed URL are all
  exactly what they were. The failure is typed and, because a page's URL can
  carry page data, diagnosed in the same sensitive mode a form submit's is:
  a typed reason and the identity, never the address.

## 9. Conflicts, decided

| Situation | Rule |
|---|---|
| several intents in one turn | last write wins (§4); one slot, no queue |
| an intent pending and a host operation navigating | the **host's** operation wins: `target.navigate`, `reload` and `traverse` are the caller's explicit instruction, so a pending intent is **discarded** and counted, not applied afterwards to a document the caller replaced |
| `replace` after `assign` in one turn, or the reverse | the last write's kind decides both the URL and the history behaviour |
| an intent from a lifecycle step | consumed after that step's drain, before the next step; the remaining steps run on the **new** document from its own beginning, and the old document's remaining steps do not run |
| an intent from a timer or a job | consumed at the boundary that ran it, never between the two phases of an activation |
| an intent raised while a build is already following a chain | the same slot and the same cap; it is link *n+1*, not a new chain |
| an intent naming a URL the preflight refuses | typed refusal at consumption, the live document untouched, and the slot cleared so it cannot retry itself |

## 10. Pre-registered court, and the blockers

**Falsification against the pushed host** comes first: today `location.href`
assignment succeeds and commits nothing, `assign` and `replace` do not exist,
so the group that asserts a committed URL after an assignment must fail there.

Criteria, frozen here:

1. **The lie is gone.** After `location.href = "/other.html"`, the committed
   URL is `/other.html`, one history entry was added, and the page's getter
   agrees with the host.
2. **`assign` and `replace`.** `assign` adds an entry; `replace` leaves the
   ring's length and position unchanged.
3. **Last write wins.** Three writes in one turn commit the third, and the
   fixture server sees exactly one request, for the third.
4. **No re-entrancy.** An intent raised during a build follows the chain, and
   at cap 3 a fourth answers `resource_limit` with `navigation_chain_limit`
   and commits nothing; no intermediate URL is ever the committed URL and no
   intermediate history entry exists.
5. **One deadline, one budget.** A chain that exhausts either answers the
   existing typed failure, and a chain cannot make more fetches than one
   document's allowance.
6. **Boundaries.** An intent from `DOMContentLoaded` is consumed before
   `load`; an intent raised inside an activation is not consumed between the
   preflight and the activation, proven by a page whose handler assigns and
   whose activation still completes against the document it approved.
7. **Live-realm failure.** A failed intent keeps the handler's mutations and
   the revision, and leaves target, frame, generation, realm, history and URL
   unchanged; the error carries no address.
8. **Host wins.** A pending intent plus an explicit `target.navigate` commits
   the caller's URL, and the intent is discarded, not applied after it.
9. **Secrecy.** No URL, query or page text from an intent appears in the
   ledger, the court log or the receipt.
10. **Memory.** The sink is one slot per realm: owners return to the
    empty-host baseline on close, and 128 intent-driven navigations leave the
    live owners within the frozen 65,536-byte bound. Paired differentials and
    bounded owners only; no absolute footprint gate.
11. **Same-build regressions**: lifecycle, job, timer, frame-action, form,
    child-frame, frame-realm, CDP and navigation, all on one binary.

### The blockers I am not deciding alone

1. **The chain cap of 3.** It is chosen by analogy to `MAX_REDIRECTS`, not by
   measurement, and it is the only number in this design that a real page
   might legitimately exceed — a login flow that bounces twice through a
   script and once through HTTP would sit at the edge. If the root wants a
   different cap, it should be set before the court is frozen, because it
   cannot move afterwards.
2. **Discarding a pending intent when the host navigates.** §9 chooses the
   caller over the page. It is the safer default and it diverges from a
   browser, where the page's navigation would already have started. I want
   that divergence ruled rather than assumed.
3. **Whether `location.reload()` belongs here.** It is one line over the
   existing `target.reload` path and it is the fourth thing a page reaches
   for. I have left it out to keep the scope as ruled, and I would rather be
   told to add it than add it unasked.

## 11. The rulings, and four corrections they came with

§§1–10 stay as written except where this section says otherwise.

### 11.1 The three blockers, ruled

- **The chain cap stays 3**, and it is **its own budget**. It is not shared
  with, and does not consume, `net::MAX_REDIRECTS`: an HTTP redirect chain of
  three hops inside one link is still allowed, and a script chain of three
  links is still allowed, and neither borrows from the other. The two are
  counted separately and refused separately, with `navigation_chain_limit`
  for the script chain.
- **An explicit agent navigation wins.** `target.navigate`, `target.reload`
  and `target.traverse` discard any pending intent **before** the explicit
  operation begins, so nothing the page asked for can be applied to a document
  the caller has already replaced. The discard increments a bounded
  host-owned `discarded_total` with the fixed cause `caller_override`, and
  **the discarded URL is never logged, recorded or reported.**
- **`location.reload()` is in scope**, no-argument only. It reloads the
  relevant document's current URL through the same deadline, policy and
  atomic path as everything else, and it **does not add, move or replace** a
  history entry. It takes the same single slot as the others.

**One slot, and the last write decides everything.** A later `href`, `assign`,
`replace` or `reload` replaces whatever was pending and determines **both**
the destination **and** the history behaviour. A `replace` after an `assign`
replaces one entry; an `assign` after a `replace` adds one; a `reload` after
either touches history not at all.

### 11.2 An intent during build or lifecycle is replace-like

WHATWG is explicit: a `Location` navigation from a document that is **not
completely loaded**, without transient activation, uses **replace** handling.
This host has no activation model at all — it cannot tell a user gesture from
a script — so it takes the conservative half of that rule unconditionally:

> **Every intent raised while the document is still building, and every intent
> raised during the four lifecycle steps, is replace-like.** It adds no
> history entry.

That is a deliberate mapping, not an accident: without an activation model the
alternative would be to add entries a browser would not, and inventing history
a user never created is the worse error. An intent raised **after** the
lifecycle has finished — from a timer, a job or an action handler — follows
§6: `href` and `assign` add an entry, `replace` replaces one, `reload` leaves
history alone. The court proves the build and lifecycle cases add **no** entry
and that the ring's length is unchanged.

### 11.3 The `href` getter reads the committed document

`location.href` answers the **relevant document's current URL** — the one the
host has committed — and it keeps answering that until a new document is
committed. Recording an intent does **not** change what the getter returns.
So a page that assigns and then reads back gets the old URL, which is a
divergence from a browser, where the getter reflects the pending navigation
early in some cases; it is recorded here rather than hidden, and it is the
honest direction: the getter never claims a navigation that has not happened.

### 11.4 What `target.act` answers when a handler queues a navigation

The public result shape does not change, and the operation never claims
success before the queued navigation's outcome is known. The intent is
consumed at the boundary **inside the same `target.act`**, so the outcome is
always known before the answer is written, and the rule is exactly the one the
form submit already established:

- **The navigation commits.** The answer is the navigation-shaped action
  result the click and submit paths already return — `navigated`, the new
  `frame`, `generation`, `realm`, `retired_realm`, `ended_frames`, `url`,
  `fixture` and `network` — carrying the action's own `action` and `role`
  fields where the named version has them. `applied` refers to the action's
  own effect, and `revision` is the new document's.
- **The navigation fails.** The operation answers the navigation's **typed
  failure**, not a success. Whatever the handler completed stands and the
  revision reflects it, exactly as a failed submit does today, and because a
  page's URL can carry page data the failure is redacted in the same
  sensitive mode: a typed reason and the identity, never the address, the
  query or any free text that could hold either.

No third shape is introduced, and no field is added to say "there was also a
navigation".

### 11.5 The slot is cleared on every path, and why `caller_override` exists

The slot is cleared **before** the host acts on it, on every path out:
consumption, a refused preflight, a deadline, and the retirement of the realm
that holds it. A refusal in particular must clear it, or the same refused
intent would be retried at the next boundary forever.

**The invariant this produces:** at the end of every operation the slot is
empty, because an intent is always consumed at a boundary inside the operation
that raised it. That has a consequence worth stating rather than discovering:
**`caller_override` is not reachable in production today.** No pending intent
survives an operation, so no explicit navigation can ever find one.

It is kept, and it is reachable **only through a deterministic court-only
seam** that suppresses exactly one consumption, so the court can then run
`target.navigate` and observe the discard, the counter and the fixed cause.
There is no product race to exercise and none is invented. The counter exists
because the invariant above is a property of today's boundaries, not a law: if
a later slice ever defers a consumption, the discard must already be defined,
counted and silent about its URL rather than improvised then.

The court asserts both halves: the slot is empty after every operation, and
the seam-driven discard counts exactly one `caller_override` with no URL
anywhere.


## 12. A frozen criterion that read a field the host does not report

Before the court judged anything, three of its history criteria read
`history.entries` as a list. The bounded history reports **`length`** and
**`position`** — with `can_go_back` and `can_go_forward` — and no entry list
at all, by design, because an entry is metadata the caller never needs to
enumerate. Those criteria would have compared an empty list to a number and
been unfalsifiable.

Corrected before any product code: a replace-like intent asserts
`length == 1` and `position == 0`, a reload asserts the same, and the
post-lifecycle `href` setter asserts `length == 2` and `position == 1`. The
history's shape is what the host reports, not what the criterion assumed.


## 13. Three court corrections found while running it

The frozen court judged the first implementation at 32/42, then 38/42, then
40/42. Three of the remaining failures were the court's, not the host's, and
each is recorded here before the code that answers it.

**13.1 A reload fixture that could not stop.** `/reload-call.html` guarded
its `location.reload()` with a flag on `document.body`. A reload rebuilds the
document from scratch, so the flag is gone every time and the page reloaded
forever until the build chain hit its cap — the criterion measured
`MAX_SCRIPT_CHAIN`, not one rebuild, and no correct host could pass it. The
fixture is now one-shot **at the server**: the first response for that path
carries the reload script and the second carries none. The criterion also
asserts the path was fetched exactly twice, so a host that never reloads
fails instead of passing on an unchanged URL.

**13.2 A hold that nothing could have raised.** The caller-override group
opened a page whose intent comes from a timer, then read `memory.report`.
`memory.report` is not a timer boundary — `target.inspect`, `target.snapshot`,
`target.act` and `target.wait` are — so at that moment nothing had run, the
slot was empty, and the criterion measured the absence of a feature rather
than the discard. One observation now precedes the counters.

**13.3 The seam had one criterion where it needed three.** The frozen court
asked only that the host accept `--court-hold-intent`. The ruling requires
the seam to be *doubly* constrained, and acceptance alone cannot show that.
Two criteria are added: a host started with the knob and **no**
`--surface-court-file` must refuse before it serves anything (measured as a
non-zero exit with an empty stdout), and the private court file must be gone
once the host is. A fourth criterion asserts the held intent was actually
pending (`pending == 1` before the override, `0` after) and that no ordinary
result surface names the seam.

The caps did not move. 42 criteria became 50; the four new ones are all
falsifiers for the ruling's fail-closed condition.


## 14. The seam's fail-closed shape, and where the court log is created

`--court-hold-intent 1` is accepted only when `--surface-court-file` is also
given: otherwise the host prints one line and exits 64 **before serving**, so
no operation can reach it. The knob suppresses exactly one consumption; the
intent it would have committed is taken into a host-owned slot instead, which
is what `pending` reports and what the next explicit `target.navigate`,
`target.reload` or `target.traverse` discards as `caller_override`. In
ordinary operation that slot is always empty at the end of an operation,
because every intent is consumed at a boundary inside the operation that
raised it.

The private log is now created **after** every configuration check that can
exit — pinned roots, the realm-allocation conflict, the visible-window
refusal. `CourtLog`'s destructor removes the file, and a `process::exit` does
not run destructors, so creating it last is what makes "removed on every exit
path" true rather than aspirational. A normal return, an end of input and an
unwinding panic all take the file with them; a `SIGKILL` does not, and no
in-process mechanism can make it.


## 15. One frozen memory cap, and the single narrowing pre-registered for it

Measured on the same court, same binary copy, both allocators:

| criterion | frozen cap | HEAD before the slice | with the slice |
| --- | --- | --- | --- |
| child-frame M1, one child | 262144 | 255226 | 262970 |
| child-frame M2, seven children | 1835008 | 1784300 | 1838508 |

The slice costs about 7.7 KiB of live owner bytes per child realm, and M1 had
6.9 KiB of headroom. **The caps do not move.** The cost is narrowed instead,
and this is the only candidate, registered before the code exists:

*The change.* `__mcsLocation(parts, live)` gains a second argument. A
**script-running** realm — the main document's — receives the accessor form:
fourteen closures over the committed parts, the setters, `assign`, `replace`,
`reload` and the intent slot they write. A **script-free** realm — every child
frame — receives exactly the plain immutable data object it received before
this slice, with no setters, no `assign`/`replace`/`reload` and no slot.

*The semantic proof.* A child frame is built script-free by construction:
`build_target`'s child loop parses `text/html`, seeds the tree and the
location and never evaluates page script, and the design forbids child
scripts. No page code runs in that realm, so no page code can read those
getters, call those methods, or raise an intent there. The two forms are
therefore indistinguishable to every observer that exists — the difference is
unobservable, not hidden. A host-side attempt to take an intent from a child
realm is already impossible: intents are taken from the main realm's
capability alone. Nothing on a script-running realm changes, so no lie is
introduced where a page can look.

*The expected recovery.* Removing the per-child accessor form should return
each child to its pre-slice cost: M1 back to about 255 KiB (under 262144) and
M2 to about 1.79 MiB (under 1835008), leaving the main realm's cost as the
slice's only memory change. If either cap still fails after this one change,
the slice stops and is reported; there is no second optimization and no
amended cap.


## 16. What the narrowing actually recovered

The pre-registered change was made and measured on the same court, the same
copied release binary, both allocators:

| criterion | frozen cap | HEAD before the slice | with the slice | after the narrowing |
| --- | --- | --- | --- | --- |
| child-frame M1, one child | 262144 | 255226 | 262970 | **261354** |
| child-frame M2, seven children | 1835008 | 1784300 | 1838508 | **1827196** |

Both caps hold, and the child-frame court is 82 of 82 again. The recovery is
**partial, not complete**: a child still costs about 6.1 KiB more than it did
before this slice. The accessor form was about 1.6 KiB of that; the rest is
the shim source itself, which every realm compiles whether or not it will run
page script, and which this slice made larger. §15 predicted a return to
about 255 KiB and that did not happen — the prediction was wrong about where
the cost lived, and the measurement is what stands.

That leaves M1 with **790 bytes of headroom** on the default allocator. It is
under the cap, so the slice proceeds; but the next slice that adds shim source
will exceed M1, and the honest reading is that this cap is now nearly spent,
not that it is comfortable. No second optimization was attempted and no cap
moved.
