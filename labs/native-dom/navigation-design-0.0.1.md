# Agent-native navigation and bounded history 0.0.1 (design only)

Status: **proposed, not implemented, nothing measured.** This freezes the
slice, the contract question, the budgets and the court before any code, as
the surface work did. It changes no operation today: the control operation
enum of `0.0.1` is closed and stays closed. Two decisions below need the
root's ruling before implementation starts.

## 1. The gap, stated from the contract

`control-0.0.1` gives a target "navigation and page state" and defines what a
navigation does to identity, but offers no way for an Agent to ask for one.
The vocabulary has `target.open`, which mints a target, and `target.act`,
whose only action is a click. The only navigation an Agent can cause is a
click that happens to follow a link, which is why
`cdp-mapping-0.0.1.json` already records the loss verbatim: *"no
target.navigate exists in 0.0.1; the synthetic host navigates through a link
click and projects no navigation events"*. `Page.navigate` answers `-32601`
on every route measured so far.

So an Agent cannot: open a known URL in a target it already holds, reload a
document, or go back. It can only hunt for a link. That is the smallest
standards-oriented gap worth closing, and it is the one the CDP adapter
already names.

## 2. The slice (smallest that is still standards-oriented)

Three operations, not four, so the enum grows as little as possible:

| Operation | Arguments | Result |
|---|---|---|
| `target.navigate` | `target`, `url` | `target`, `frame`, `generation`, `realm`, `revision`, `url`, `history {position, length}` |
| `target.reload` | `target` | the same shape; a reload is a navigation to the current entry |
| `target.history` | `target`, `delta` (−8..8, non-zero) | the same shape, or typed `not_found` when the entry does not exist |

`target.inspect` gains a bounded `history {position, length, can_go_back,
can_go_forward}`, the way it gained `surface` and `scroll_y`. No new error
code: `invalid_request` for a malformed URL, `permission_denied` for a policy
refusal, `not_found` for a missing history entry, `deadline_exceeded`,
`resource_limit`, `unsupported_operation` on a backend without the slice, and
the existing network reasons in `details` for a fetch that fails.

Not in the slice, deliberately: navigation events or a subscription model, a
`loaderId` equivalent, fragment-only navigation semantics, `history.pushState`
from page script, cross-target or child-frame navigation, prerender, bfcache.
Each is a separate decision with its own memory question.

## 3. Decision 1 for the root: how the closed enum grows

The enum is closed and a name outside the version is `invalid_request`, so
adding operations silently to `0.0.1` is not available. Three ways exist and
they are not equivalent.

- **A. Version bump to `control-0.0.2`** carrying the three operations and the
  `target.inspect` extension. A request names its version exactly (the schema
  fixes `version` as a constant), so one host can serve both: it answers the
  `0.0.1` enum to a `0.0.1` request and the `0.0.2` enum to a `0.0.2` request,
  and a `0.0.1` client is bit-identical to today. Discovery needs no new
  operation because `session.inspect` already reports the capability audit
  ledger and can carry a bounded `operations[]` for the session's version.
  Cost: a second schema, mapping and example set to keep in step.
- **B. A typed feature-negotiation handshake** (`control.hello` or
  `capabilities`) that a client calls first. Cost: the handshake operation is
  itself an enum addition, so it needs A anyway to exist; it adds a round trip
  and a state machine (what is a request that arrives before the handshake?);
  and it invites per-host divergence, which is what the closed enum was meant
  to prevent.
- **C. Overload something existing**, for instance `target.open` against a
  live target. Rejected outright: it would break "one target id for its
  lifetime" and make the result of `target.open` ambiguous.

**Recommendation: A, with discovery through the existing `session.inspect`.**
It keeps one closed enum per version, needs no new handshake operation, keeps
every `0.0.1` client unchanged, and leaves `unsupported_operation` as the
honest answer for a backend that lists but cannot serve an operation. B is
worth revisiting only when two hosts must differ *within* one version, which
is exactly the divergence the contract forbids today.

## 4. Decision 2 for the root: what history keeps

An Agent that goes back expects the previous page. A browser gives it from a
back/forward cache, holding the document and its realm alive. Memory-first
forbids that here: a retained document is retained bytes, and the surface work
showed how hard those are to give back.

**Proposed rule: history is metadata only.** An entry is a bounded URL plus
the profile-scoped facts needed to refetch it, capped at 8 entries and 1 KiB
each. Going back is a fresh navigation to that URL: new generation, new realm,
revision advances, and *no page state is restored*. Form input, scroll
position and script state are gone, exactly as after a reload.

This is a real loss against the standards an Agent may assume, and it must be
recorded in the loss matrix and in the CDP mapping rather than hidden. The
alternative, a one-entry document cache, would need its own G1 evidence and is
not proposed. The root should rule whether the loss is acceptable for the
slice or whether the slice waits for a measured cache design.

## 5. Identity and atomicity (invariants the court checks)

- The target id and the main frame id survive every navigation. The document
  generation increments by one, a new realm id is minted, the old realm id is
  `not_found` with `realm` scope, and the target revision advances so that
  every prior node reference is `stale_revision`. This is what a link click
  already does on the native route (revision +1, generation 2, new realm, same
  frame); the three operations must be indistinguishable from it in identity.
- **Atomic failure rollback.** A navigation that fails at any point (address
  policy, TLS, HTTP status, byte or time budget, parse) leaves the target
  exactly as it was: same generation, same realm id, same revision, same DOM,
  and node references still valid. Nothing is torn down until the replacement
  document is parsed and its realm is seeded. The cost is that both documents
  are briefly alive, which the peak budget in §7 accounts for; the benefit is
  that an Agent never lands in a half-navigated target it cannot describe.
- A failed navigation adds no history entry and does not move the position.
- `target.close` and `session.close` are unchanged, including the teardown
  order adapters → surfaces → target.

## 6. Policy, profile and network reuse

A navigation fetch is the same bounded fetch `target.open` performs, with the
same profile and no new authority: allowlisted origins with the fail-closed
address policy, redirect bound and the recorded downgrade rule, response byte
and per-fetch time budgets, the pinned-root TLS client of that profile with
its session cache, and the profile's cookie jar and `localStorage` synced
before and committed after, `Secure` cookies included. `profile.policy.set`
with network offline refuses a navigation as `permission_denied` before any
socket. Nothing about the slice widens what a profile may reach.

## 7. Memory budgets (pre-registered, to be measured, not yet claimed)

| Item | Budget | Why this number |
|---|---|---|
| one history entry | ≤ 1,024 bytes accounted | a bounded URL and its profile-scoped facts, nothing else |
| history per target | ≤ 8 entries, ≤ 8,192 bytes | the surface owner's shape: a small hard cap the Agent can see |
| navigation peak, over steady state | ≤ old document + new document + 64 KiB | atomicity keeps both alive for one moment; the slack is the parse and the control plane |
| post-navigation retention, after the old realm retires | ≤ 262,144 bytes over the pre-navigation steady state | the surface court's S2 number, so the two slices are comparable |
| 128-navigation soak | **differential, not absolute** (see below) | the control plane's own per-request churn makes an absolute cap meaningless |

The soak must be differential because of what the control-churn court already
measured on this route: every control request grows the host by roughly 0.1 to
1.5 KB with no plateau by 128 requests, born in the realm evaluations and the
response serialization, with libmalloc in-use returning each time. A 128
navigation soak with an absolute cap would fail for reasons that have nothing
to do with navigation. The pre-registered figure is therefore the difference
between a navigating arm and a non-navigating arm of **identical request
count, shape and deadline**, with the navigation arm's excess capped at 8 KiB
per navigation and its slope over the last 64 navigations capped at 1 KiB per
navigation. Both arms report their absolute numbers beside the difference.

## 8. Multi-backend loss matrix (what each route can and cannot serve)

| Route | `target.navigate` | `target.reload` | `target.history` | Known blocker |
|---|---|---|---|---|
| native bounded route (html5ever + QuickJS) | implementable within the existing bounded fetch and realm lifecycle | same | metadata-only, per §4 | none known; this is where the slice is proposed to land first |
| Lightpanda 0.4.0 | its CDP journey already navigates a target to a hermetic URL, and its retention is bounded at about 7 MB through 128 cycles, the best of any route | expected through the same door | **unverified**: no evidence in this repository that its CDP surface serves navigation history | must be qualified by the same court before any claim |
| Servo 0.5.0 | navigation exists, but the measured cost is linear accumulation of roughly 0.9 MB per navigation cycle in driver-owned state that no allocator action recovers, and its CDP surface answers `Page.navigate` with `-32601` | same accumulation | same | the 128-navigation soak is expected to fail on the pinned release; the route's G1 recovery dependency is already red |
| synthetic control host | already models navigation identity through a link click | trivial | metadata-only | not HTML; it qualifies vocabulary and identity, never memory |

The matrix is a design expectation, not a measurement. Only the native route
is proposed for implementation; the other rows say what a court would have to
establish before the operation could be listed there rather than answered
`unsupported_operation`.

## 9. CDP mapping (adapter losses to record)

| Native | CDP | Loss |
|---|---|---|
| `target.navigate` | `Page.navigate` | the adapter gains the method that is `-32601` today; no `loaderId`, no `frameNavigated` event, and no navigation event stream is proposed in this slice |
| `target.reload` | `Page.reload` | `ignoreCache` and `scriptToEvaluateOnLoad` are unsupported and refused typed rather than ignored |
| `target.history` | `Page.getNavigationHistory` / `Page.navigateToHistoryEntry` | CDP addresses entries by id in a full list; this slice exposes a bounded window and a signed delta, so ids are not projected and a request outside the window is `not_found` |
| history semantics | back/forward cache | no document or realm is retained, so a CDP client that expects restored page state after going back gets a fresh document; recorded as a loss, not emulated |

## 10. Agent concerns

- **Deadlines.** A navigation is network-bound. The request's `deadline_ms`
  bounds the whole operation including the fetch, and expiry is
  `deadline_exceeded` with the target rolled back per §5, never a target left
  mid-navigation.
- **Capability attenuation.** Navigation is authority: it moves a target to a
  new origin under the profile's cookies. It gains no new reach beyond the
  profile's policy, and a profile whose network is offline or whose origin
  allowlist excludes the URL refuses before any socket.
- **Audit.** Every navigation, reload and history move appends one bounded
  entry to the capability audit ledger `session.inspect` already reports:
  operation, target, outcome, and the origin, never the full URL with its
  query.
- **Stale references.** The revision advance makes every prior node reference
  `stale_revision`, which is the existing contract; the court proves the Agent
  learns this from the reference rather than from a silent mismatch.
- **Deterministic waiting.** No new wait condition is proposed. A navigation
  result carries the revision it produced, and `target.wait` with
  `revision_at_least` already makes the settle deterministic, so an Agent
  never polls or sleeps.

## 11. Pre-registered hermetic court (frozen before implementation)

`navigation-court.py`, strictly headless, no surface, no AppKit, hermetic
loopback server only, both allocators, fresh host per run, one warm-up plus
seven runs. It writes `native-dom-control-0.0.2-navigation`. Checks, grouped:

1. **Identity**: navigate A → B keeps the target and frame ids, increments the
   generation by exactly one, mints a new realm, retires the old realm to
   `not_found`, advances the revision, and turns a pre-navigation node
   reference into `stale_revision`.
2. **Reload**: same identity rules, the URL unchanged, the history position
   unchanged and the length unchanged.
3. **History**: after A → B → C, a delta of −1 lands on B and −2 on A;
   forward returns to C; a delta past either end is `not_found`; a new
   navigation from a back position prunes the forward entries; the ninth
   entry evicts the first and the length stays at the cap.
4. **Atomic rollback**, one check per failure kind: a denied origin, an
   offline profile, a TLS failure against a pinned root, a 404, an
   over-budget body and an expired deadline. Each leaves generation, realm id,
   revision and the node reference exactly as before, adds no history entry,
   and returns the typed error with its reason.
5. **Policy reuse**: cookies set on A are sent on the navigation to A's
   origin, `Secure` cookies persist across a navigation, `localStorage`
   survives per origin, and the pinned-root TLS client is the profile's.
6. **Agent**: the audit ledger gains exactly one entry per operation and
   records the origin without the query; `target.wait` on the returned
   revision settles without polling; an unsupported argument is refused typed.
7. **Memory**: the budgets of §7, with the 128-navigation soak run as the
   differential of two arms of identical request count.
8. **CDP**: `Page.navigate` and `Page.reload` map through and the recorded
   losses hold; a history request outside the window is refused typed.

Failure of any memory budget narrows the slice; it never moves a budget.

## 12. Blockers for the root

1. **Decision 1**, §3: version bump to `0.0.2` with discovery through
   `session.inspect` (recommended), or a negotiation handshake.
2. **Decision 2**, §4: metadata-only history with no restored page state
   (recommended, memory-first), or wait for a measured document-cache design.
3. Whether the Lightpanda row may be qualified in this increment or stays
   `unsupported_operation` until a separate court runs.

Nothing is implemented until 1 and 2 are ruled. When they are, the order is:
schema and mapping first, then the court, then the host.

## 13. Amendment after the ruling (the sections above stay as the decision record)

The root accepted both decisions with refinements. Sections 1 to 12 are left
exactly as they were proposed, so the record shows what was asked and what was
answered; where this section differs, this section governs.

### 13.1 Decision 1 accepted, as option A, with refinements

- `control-0.0.2` gets its **own schema file with its own version
  discriminator**. It is not a patch of `0.0.1`.
- A host serves **both versions concurrently**. `0.0.1` stays byte-compatible
  and semantically unchanged: same operations, same results, same errors.
- `session.inspect` **may advertise** `supported_protocol_versions` and the
  exact `0.0.2` operation set. This is **host-defined discovery and advisory
  only**. A caller that wants `0.0.2` sends `0.0.2` explicitly. A caller must
  **never** strip the version or any field and retry, and a host must never
  infer a version from the shape of a request.
- Examples and the contract checker keep **separate coverage for both
  versions**; `0.0.1` coverage is not rewritten to serve `0.0.2`.

### 13.2 Decision 2 accepted, as metadata-only, with refinements

- The delta operation is **`target.traverse`**, not `target.history`. The word
  *history* is reserved for a later state or listing operation. The bounded
  `history {position, length, can_go_back, can_go_forward}` field on
  `target.inspect` is state, so it keeps that name.
- An entry stores **only** a bounded canonical final committed URL, plus a
  host-minted bounded identity and type **if and only if** the CDP mapping
  needs them (§13.3). It stores **no** DOM, realm, response body, form state,
  scroll position, script state, cookie or storage snapshot, and **no profile
  facts**. This supersedes the phrase "plus the profile-scoped facts needed to
  refetch it" in §4 and §7: an entry is a URL and, at most, an identity.
- `target.traverse` refetches that final URL **under the profile's current
  policy and state**, not under whatever was in force when the entry was
  made, and it always produces a fresh document generation and realm.
- `target.reload` **never appends** an entry and never moves the position.
- `target.navigate` from a back position **truncates the forward entries**.
- A redirect chain commits **only the final canonical URL**; intermediate hops
  are never entries.
- A failed navigation, reload or traverse **mutates nothing**: no entry, no
  position change, no identity change, per the atomicity rule of §5.

### 13.3 CDP mapping, narrowed

- `Page.navigate` and `Page.reload` are **in the slice**.
- `Page.getNavigationHistory` and `Page.navigateToHistoryEntry` may be
  implemented **only if the host remains the sole history authority**:
  adapter-scoped ids map onto host-minted entry identities, and an evicted,
  foreign or stale id fails **typed**. If that cannot be met exactly, both
  methods stay explicitly `-32601` in this increment. **Adapter-invented
  history authority is forbidden**, so the adapter never keeps its own list,
  never renumbers, and never answers from a cache.

### 13.4 Backends, narrowed

Only the native route is implemented and qualified in this increment.
Lightpanda and Servo stay explicit `unsupported_operation` or unverified rows
until each passes **this same court**. No shim, no partial mapping and no
inherited pass: a row changes only on its own measured evidence.

### 13.5 Order of work, frozen

1. `control-0.0.2` schema, contract checker coverage and examples for both
   versions, and the CDP mapping entries with their losses.
2. The hermetic court of §11, frozen before the host changes.
3. The native host implementation.
4. Headless measurements against the pre-registered budgets of §7, including
   the differential soak.

Atomic rollback, the differential memory court and every existing gate are
preserved. Any ambiguity that would require touching the closed contract
beyond this is raised before the change, not decided in code.

## 14. Audit-ledger representation repair (pre-registered before the code)

The differential soak failed one pre-registered cap after the audit ledger
landed: over 128 navigations the navigating arm exceeded the control arm by
1,146,880 bytes under the default allocator against the 1,048,576 budget,
about 9.0 KB per navigation instead of 8. The arena arm held at 720,896 and
both tail slopes held. Before the ledger the same court measured 917,504 and
passed, so the ledger costs roughly 1.8 KB per navigation. **The cap does not
move.** This section freezes one repair before it is written.

### 14.1 What costs the bytes

Every navigation appends a record that owns four freshly allocated strings:
the target id, the operation name, the outcome category and the origin. In a
soak those four are the *same* values over and over, so the host allocates and
frees roughly four small blocks per navigation, and the default zone keeps the
freed pages. Growing the ledger by one entry also shifted the whole vector
when it was full, since the oldest was removed from the front.

### 14.2 The repair

One bounded representation change, no behaviour change:

- **A preallocated ring of exactly 64 records per session.** The deque is
  reserved once, at its capacity; a full ledger pushes the newest and pops the
  oldest with no move of the remaining records.
- **Compact fields.** The operation and the outcome become `&'static str`:
  both are already fixed vocabularies (three operation names; `committed` plus
  the typed error codes, which the host already holds as static strings), so
  they cost nothing per record.
- **Shared, bounded origins and target ids.** Each ledger keeps a small
  interning table, at most 32 origins and at most one entry per live target,
  and a record holds a shared handle. Repeated navigation to the same origin
  allocates nothing after the first. If a table is full, that record owns its
  own copy, which stays bounded because the ledger itself is.
- **Implicit owner chain.** A ledger belongs to one session and a session
  belongs to one profile for its life, so neither is stored per record; both
  are materialised at `session.inspect` from the session itself.

### 14.3 What must not change

- Every observable field of a record stays exactly as it is: `sequence`,
  `session`, `profile`, `target`, `operation`, `origin`, `outcome`,
  `deadline_ms`, `result_bytes_limit`. The target id materialises identically
  even though it is compact inside.
- The 64-entry capacity, the eviction of the oldest, `dropped_total`, and the
  release of a ledger with its session.
- No memoisation that keeps an unbounded string, no global allocator change,
  no counterfactual tuning of either soak arm, and no weakening of any check.

### 14.4 Honest accounting

`memory.report` keeps reporting the live entry count, the limit and the drop
total, and adds the reserved capacity of the rings and the bytes the interning
tables hold, so the ledger's cost is stated rather than hidden by sharing.

### 14.5 Acceptance, frozen

- Both allocators meet the existing differential cap of 8,192 bytes per
  navigation over 128 navigations, and the existing tail-slope budget.
- The audit semantics, the capacity and the drop count are unchanged, proven
  by the court's ledger assertions.
- Every other frozen check stays green.

If the repair misses the cap, the outcome is recorded `narrow` and the work
stops there. The cap is not moved and the arms are not retuned.
