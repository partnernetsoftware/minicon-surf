# Frame-aware actions and child navigation — decision record, design only

Status: **design only. No product code, no court run, no protocol change, no
receipt touched.** The behaviour shipped at `eac33da` is unchanged by this
file. Two coherent models are compared, one is recommended, and the exact
decisions the root must make are listed in §12.

The question: a live child frame can be observed but not acted in. Making it
actionable must not weaken either half of the doctrine — the memory bound, and
the determinism an agent depends on. This record's central finding is that
those two pull in the *same* direction here, and that the affordability of the
cheaper model depends on a decision already taken for memory reasons.

## 1. What is true today, at `eac33da`

- A node reference is exactly `(target, revision, node)`. Node ids are
  target-scoped: frame *k* owns the band `[128k+1, 128k+128]`, so an id can
  never mean two nodes in one target revision.
- The target's revision is `revision_base` plus the **main** realm's mutation
  counter. No child's counter is read by anything.
- `target.wait` offers `revision_at_least` and polls that one number every
  5 ms until the deadline.
- `target.act` refuses any reference in a child's band, typed, with
  `action_in_child_frame_unsupported`.
- A child is fetched same-origin, parsed, seeded, and **runs no scripts**
  (§16 of the child-frame record). It has no cookie or storage seeding.
- History is per target and holds the **main** frame's committed URLs.
- The audit ledger records target and origin, never a frame.
- CDP `Page.navigate` refuses `frameId` explicitly rather than ignoring it.

## 2. Why the node-id bands are not enough

The bands were introduced to close an aliasing hazard, and they close exactly
that and nothing else. Four gaps remain, and each is a correctness gap rather
than an ergonomic one.

1. **Staleness is asked of the wrong number.** A reference carries the
   *target* revision, which today moves only when the main realm mutates. A
   child's document could change with the target revision standing still, so a
   reference into that child would still validate and would resolve against a
   document that no longer matches it. The band says *where* a node is, never
   *when*.
2. **Band reuse across document replacement.** Band *k* belongs to the *k*-th
   live child, not to a document. Replace that child and band *k* names a
   different document's nodes. Nothing but a revision that is guaranteed to
   move can separate the two, and today nothing guarantees it for a child.
3. **They do not say where a navigation lands.** A link or a submit inside a
   child could replace the child's document or the target's. A band identifies
   the node; it cannot carry the answer.
4. **They stop at the host boundary.** The audit ledger, `target.wait` and the
   CDP projection all speak in targets. A band is an internal encoding of a
   node id and reaches none of them, so nothing outside the host can say which
   frame an action touched.

Bands are necessary and they are not sufficient. Both models below keep them.

## 3. Model A — one target-global observable revision

The reference stays `(target, revision, node)`. The band selects the frame.
Control `0.0.2` is unchanged: **no schema, no request shape and no result
shape moves.**

**The revision.** A target's observable revision becomes
`revision_base + Σ counters of live frames`. When a frame ends, its final
counter folds into `revision_base`, exactly as a replaced document's already
does, so the number is monotonic across every teardown. A new child starts at
zero and adds nothing.

**What that costs, and the mechanism that makes it affordable.** Naively the
revision must now be read from every live realm — up to eight evaluations for
a number that `target.wait` polls every 5 ms. It does not have to be, and the
reason is the decision already taken for memory: **a child runs no scripts**,
so a child's counter can change only as the direct result of a host-initiated
action in that child. The host caches each frame's counter and re-reads a
child's only after acting in it. The steady-state cost of reading the revision
is therefore one evaluation, as it is today, and a wait loop polls exactly what
it polls now.

This is the finding worth stating plainly: **model A is cheap because children
are script-free. Enabling child scripts would force the polling model back to
one evaluation per frame, and the two decisions are therefore coupled.**

**Staleness.** Every applied action already advances the revision by one and
therefore stales every outstanding reference, in every frame. Model A widens
that from "the main frame's mutations" to "any frame's mutations". The
practical difference is small, because an agent already re-snapshots after
every action; the semantics stay "one revision per applied action", now
counted over all frames.

**Addressing.** `target.act` resolves the frame from the band and acts there.
Nothing is added to the request.

**Direct child navigation** is reachable only from inside the child: a link
click or a GET submit in a child navigates that child. `target.navigate`
cannot address a frame, because giving it one is a request-shape change and
model A's whole claim is that it makes none. That is model A's real
limitation, and it is recorded rather than hidden.

## 4. Model B — frame-local versioned references and waits (`0.0.3`)

A third schema beside the two that exist, served concurrently, with `0.0.1`
and `0.0.2` untouched, exactly as `0.0.2` was added.

- A reference becomes `(target, frame, revision, node)`, where `revision` is
  that **frame's** own revision and an absent `frame` means the main frame,
  so every existing request stays byte-identical.
- `target.wait` takes an optional `frame`, and `revision_at_least` converges
  on that frame's revision.
- `target.navigate` takes an optional `frame`: a navigation addressed to a
  child replaces the child's document only.
- Results that name a revision name the frame it belongs to.

**What it buys.** Precision: only the frame that changed stales, so an agent
holding references into two frames does not lose both because one moved.
A wait that converges on the frame the agent cares about. Frame identity that
reaches the audit ledger and the CDP mapping naturally, because it is in the
request.

**What it costs.** A protocol version, with its schema, its mapping, its
examples and its checker branch, and a second failure axis to specify and
prove: a reference whose frame ended is `not_found`, one whose frame lives but
whose revision moved is `stale_revision`, and the order between them has to be
fixed (frame first, then revision) and tested. It also needs per-frame revision
bookkeeping that model A does not.

## 5. The semantics both models must define, and how they differ

| Question | Model A | Model B |
|---|---|---|
| link click in a child | navigates that child | same |
| GET submit in a child | serialises in the child's realm, navigates that child | same |
| child navigation vs parent replacement | always child-local; a child can never replace its parent's document | same, plus `target.navigate` may address a child explicitly |
| parent navigation teardown | unchanged: children end, realms retire, children before parent | same |
| child frame generation | +1 per child navigation; the child's frame id survives | same |
| child realm replacement | old child realm retired, new one minted, `realms_retired_total` +1 | same |
| history scope | the target's history stays the **main** frame's; a child navigation adds no entry, and `traverse` refetches the main URL and rebuilds children from scratch | same |
| cookies for a child fetch | the child navigation fetches on a copy of the jar and merges it only on commit, exactly as a child build does today | same |
| storage | untouched: a child has no storage view while it runs no scripts | same |
| audit | one additive frame field on the action and navigation records, interned like the target id | same |
| capability ownership | unchanged: frames are never owners, ownership resolves from `target` | same |
| CDP `Page.navigate` `frameId` | stays refused typed; there is no native operation to carry it — a recorded loss | maps to `target.navigate` with `frame` |
| foreign, ended or unknown frame | one `not_found`, unchanged | same, and ordered before the staleness test |

## 6. Memory, quantified

Per-frame bookkeeping is arithmetic on field sizes, not a measurement, and it
is stated as such:

| Item | Model A | Model B |
|---|---|---|
| cached counter per frame | 8 bytes | 8 bytes |
| per-frame revision base | — | 8 bytes |
| per-frame last snapshot | — | 24 bytes |
| per frame, total | **8 bytes** | **40 bytes** |
| seven children | 56 bytes | 280 bytes |
| audit frame id, interned, per entry | 8 bytes × 64 = 512 bytes per session | same |

Against the measured cost of a child frame — **247,034 live owner bytes**,
almost all of it the QuickJS realm — the bookkeeping of either model is
between 0.02% and 0.12% of one child. **Per-frame bookkeeping is not a memory
question.** The only material lever in this area is child scripts, whose cost
is unbounded and page-dependent, and which would also re-open the storage
divergence §16 closed.

## 7. Are child scripts still excluded? Yes, and more firmly

Three things now argue for keeping them out, where §16 had one:

1. The storage divergence §16 recorded: a same-origin child with its own
   realm would give one origin two independent `localStorage` copies.
2. The memory cost is unbounded and page-dependent, which is the one cost this
   product is not allowed to hide.
3. **Model A's affordability depends on it** (§3). Script-free children make a
   frame's counter change only when the host acts in it, which is what keeps
   the revision read at one evaluation.

The honest consequence, which must be recorded rather than discovered: an
action in a child behaves exactly as it would in a **script-free document**.
No handler can cancel a click, no submit handler runs, `preventDefault` never
happens. That is not a divergence from the child's document — that document
genuinely has no handlers — it is a consequence of the loss already recorded
in §16, and the court should assert it so that no reader mistakes an
uncancelable click for a bug.

Two alternatives were considered and are not recommended now: a single storage
view shared by same-origin frames, which fixes divergence at the cost of
implementing sharing; and a read-only storage view for children, which is
cheaper and silently drops writes, replacing one divergence with another.

## 8. What either model must prove before it is believed

Enumeration and narrowing as they are, plus: an action in a child changes only
that child and advances the target revision exactly once; a reference from
before that action is stale in **every** frame under A, and in the acted frame
under B; a link and a GET submit in a child navigate the child, keep the
child's frame id, advance its generation, retire and replace its realm, leave
the parent's identity, URL, generation and history untouched, and end nothing;
a parent navigation still ends every child; the child navigation's cookies
commit only if it commits; band reuse after a child navigation is caught by
staleness; the audit records the frame without a URL or any page text; and the
memory criteria of the child-frame court are unchanged by the bookkeeping.

## 9. The recommendation

**Model A, with the action surface bounded to what a script-free document can
answer for.** It reaches the agent value — read an embedded document, fill it,
submit it, follow a link in it — for no protocol version, no request or result
shape change, and no new failure axis. Its costs are two, both recordable: a
mutation in any frame stales references in all of them, which an agent's
existing discipline already absorbs; and a child can be navigated only from
inside itself, so CDP `Page.navigate` with a `frameId` stays refused.

Model B is the better long-term shape and should be reached from A rather than
instead of it: the frame-local reference is *additive*, so a `0.0.3` that adds
`frame` to the reference and to `wait` can be built later without invalidating
anything A ships. Doing B now buys precision that nothing has yet asked for,
and pays a version for it.

## 10. What this record does not decide

Whether an action in a child should be allowed at all is the root's, not
mine — this record only says what it would mean and what it would cost.

## 11. Scope discipline

Nothing here is implemented, no court is run, no schema, mapping or example
changes, and the receipts committed at `eac33da` are untouched. The plan gains
one proposed and open node.

## 12. The exact decisions required before any code

1. **Model A or model B.** A recommends itself on cost; B on precision. If A,
   `0.0.2` is untouched; if B, a `0.0.3` schema, mapping, examples and checker
   branch come with it.
2. **Are actions in a child allowed at all**, given §7: they behave as they
   would in a script-free document, so no page handler can cancel them.
3. **Do child scripts stay excluded?** The recommendation is yes, and model
   A's cost model depends on that answer.
4. **Does a child navigation enter the target's history?** The recommendation
   is no, because history is metadata-only and `traverse` refetches the main
   URL. That is a recorded loss either way.
5. **May the audit ledger carry a frame id?** It is additive and interned, and
   without it an action in a child is indistinguishable in the ledger from one
   in the main frame.
6. **Under A, does `target.navigate` stay main-frame-only**, leaving CDP's
   `frameId` refused? Under B it would not.

---

# Part II — the root's rulings, and the design they fix

Still design only: no product code, no court run, no protocol change, and no
receipt touched. §§1–12 stay as written; where Part II differs from them, Part
II governs.

## 13. The six rulings

1. **Model A**, now. Model B stays the preferred later protocol shape and is
   reachable from A because its frame-local reference is additive.
2. **Existing typed actions are allowed in a live, same-origin, script-free
   child**, and only after the frame is resolved from the node band *and* that
   frame's own observed snapshot is validated (§16).
3. **Child scripts stay excluded.**
4. **A child navigation never enters or moves the target's history.**
5. **Every action audit entry carries an interned frame id**, main or child,
   bounded, with no page data.
6. **`target.navigate` and CDP `Page.navigate` stay main-frame-only.** Any
   `frameId` stays typed-refused.

## 14. The global revision, defined

For a target `T` at any instant let `F(T)` be its live frames, the main frame
`m` first, then children `c₁…c_k`. Each live frame `f` has a realm counter
`n_f ∈ ℕ`, which its realm only ever increments. `B(T) ∈ ℕ` is the target's
revision base. The observable revision is

> **R(T) = B(T) + Σ_{f ∈ F(T)} n_f**

with every addition saturating in `u64`.

Two invariants:

- **(M) Monotonic.** No host operation decreases `R`.
- **(S) One per application.** Every applied action and every committed
  navigation increases `R` by exactly one; nothing else increases it.

### 14.1 Every event, and its effect

| # | Event | Rule | ΔR |
|---|---|---|---|
| 1 | mutation in a child `c` (only a host-initiated action can cause one) | `n_c ← n_c + 1` | **+1** |
| 2 | mutation in the main frame | `n_m ← n_m + 1` | **+1** |
| 3 | child `c` replaced by its own navigation | fold `B ← B + n_c + 1`, new realm starts at `n_c = 0`, `F` unchanged | **+1** |
| 4 | child `c` ends | fold `B ← B + n_c`, remove `c` from `F` | **0** |
| 5 | parent navigation | every child ends by rule 4, then fold `B ← B + n_m + 1` and the new main realm starts at `n_m = 0` | **+1** |
| 6 | a child is built with its parent's document | it joins `F` with `n = 0` | **0** |
| 7 | any failed operation | nothing is folded, no counter moves, `F` unchanged | **0** |

**Proof of (M).** `R` changes only through counter increments, which are
non-negative; through folds, each of which adds to `B` exactly the counter it
is about to remove from the sum, plus a non-negative constant; and through
membership changes, each of which is accompanied by its own fold (rules 3, 4,
5, and rule 6 which adds a zero). No rule subtracts from `B` and no counter
decreases, so `ΔR ≥ 0` for every rule and, by composition, for every sequence
of them. ∎

**Proof of (S).** Rules 1 and 2 add one by the settle rule already proven in
the form slice. Rule 3: `R_after = (B + n_c + 1) + Σ_{f≠c} n_f + 0 =
R_before + 1`. Rule 5: each rule-4 fold leaves `R` fixed, then
`R_after = (B' + n_m + 1) + 0 = R_before + 1`. Rules 4, 6 and 7 add nothing
and are the only rules that change `F` or fail. ∎

**Saturation.** At `u64::MAX` the sum stops discriminating and staleness would
silently stop working, so the host does not wrap and does not pretend: once
`R` saturates, every action and navigation on that target is refused
`resource_limit` with a fixed reason. It is unreachable in practice and it is
specified rather than left to overflow.

## 15. `target.wait`, and why one evaluation is sound

`target.wait` reads `n_m` from the main realm on every poll and takes every
`n_c` from a cache. It never polls a child's realm.

**Claim.** While child scripts are excluded, `n_c` can change only during a
host-initiated evaluation in `c`'s realm.

**Proof.** `n_c` is incremented by exactly two mechanisms: the
`MutationObserver` installed by the instrumentation, which fires only on a
mutation of `c`'s document, and the explicit settle in the action script. A
child's realm is seeded with the shim, the tree, its location and the
instrumentation, and **no page script is ever evaluated in it** (ruling 3), so
no timer, microtask, event source or fetch continuation exists inside it.
A QuickJS realm executes only while the host is inside an `eval` on it.
Therefore, between two host evaluations in `c`, no code runs in `c`, no
mutation occurs, and `n_c` is unchanged. ∎

**Consequence and dependency.** The host caches `n_c` when it builds the child
and refreshes it only after an operation that evaluated in that child. The
main frame is different — it runs page scripts and can have queued jobs and
fetch settlements — so `n_m` is re-read every poll, exactly as today. The
steady-state cost of reading `R` is therefore one evaluation, unchanged.

**This proof is a dependency, not a convenience.** It holds only while ruling 3
holds. Seeding any script, timer or job source into a child invalidates it and
forces `target.wait` back to one evaluation per live frame. Any future
proposal to enable child scripts must reopen this section first.

## 16. The action gate, and per-frame snapshots

Today one `last_snapshot: (revision, count)` per target authorises a node
index. With frames that record would let a snapshot of one frame authorise an
index in another, because the index is only compared against a count. It is
replaced by **one bounded record per frame**:

```
FrameSnapshot { reference_revision: u64, frame_revision: u64, nodes: u32 }
```

`reference_revision` is the global `R` the snapshot reported,
`frame_revision` is that frame's own `n_f` at the time, and `nodes` is how
many entries it returned. An action is served only when **all** of these hold,
each checked before anything is dispatched and none of them able to move a
revision:

1. the node id lies in a band that belongs to a **live** frame of this target
   (otherwise `not_found`, the same refusal a foreign or ended frame gets);
2. that frame has a `FrameSnapshot` (otherwise `not_found`: nothing observed
   it);
3. `reference.revision == FrameSnapshot.reference_revision` — the reference
   came from *that* observation;
4. `reference.revision == R(T)` now — nothing has happened since
   (otherwise `stale_revision`);
5. `n_f == FrameSnapshot.frame_revision` — that frame's document has not been
   replaced under an unchanged global revision, which rules 3 and 4 of §14.1
   already make impossible but which is checked rather than assumed;
6. the index is below `FrameSnapshot.nodes`.

Check 5 is the one that closes band reuse: a band names the *k*-th live child,
so without it a replaced child could inherit an authorisation. A frame's record
is dropped when its document is replaced or its frame ends.

## 17. Child link and GET submit

A link click or a GET submit inside a child **replaces that child's document
only**. The child's frame id survives, its generation increments, a new realm
is minted and the old one retired, and `R` advances by exactly one (§14.1
rule 3) — all of it atomically: the candidate child document is fetched,
parsed, seeded and instrumented in full before anything about the live child
changes, exactly as a target's own navigation already works.

A failure leaves the child's identity, its document, its state and `R`
untouched, save for handler effects explicitly dispatched before the failure —
of which a script-free child has none, so in this slice the rollback is total.
The parent's identity, URL, generation, realm and history are untouched in
both outcomes, and no frame ends.

**Budget.** A child navigation spends the **current parent document's**
remaining aggregate allowance — the same 32 fetches and 4 MiB that already
cover the parent, its scripts and its children. It is never given a fresh
budget. Exhaustion refuses the navigation with `resource_limit` and leaves the
child exactly as it was. Replacing the parent's document or closing the target
ends the allowance with it; the replacement's own budget is what the new
children and their navigations spend.

**History.** Nothing about a child navigation enters or moves the target's
history (ruling 4), and `target.traverse` continues to refetch the main URL and
rebuild children from scratch.

## 18. Failing closed on what is not modelled

Each of these is refused typed, before any event, with a fixed reason, rather
than approximated:

| Feature | Decision |
|---|---|
| `<iframe sandbox>` | **the frame is not built at all**, tallied `sandboxed` (§19) |
| link or form `target` other than absent or `_self` | activation refused, `unsupported_capability`, reason `target_not_self` |
| `_top`, `_parent`, `_blank` | the same refusal; they are not special cases, they are simply not `_self` |
| `<a download>` | refused, reason `download_unsupported` |
| a `javascript:` href or form action | refused, reason `scheme_unsupported` |
| a fragment-only href | refused, reason `fragment_unsupported`; no navigation, no revision movement |

**Why sandboxed frames are skipped rather than observed.** A sandbox without
`allow-same-origin` gives the child an opaque origin. This host's entire child
model rests on a child being *same-origin by construction*: that is what lets
it share the parent's jar, skip a storage partition, and be actionable at all.
A sandboxed child would need an opaque-origin model that does not exist here,
and building one anyway would grant the agent more than the page asked for
while the host models none of the `allow-*` tokens. Skipping is the fail-closed
answer, and it keeps the invariant that every live child is same-origin.
Observation-only sandboxed frames can be granted later, once opaque origins
are modelled; that is strictly additive to this decision.

## 19. Amendments to the already-pushed child-frame contract

Two of §18's decisions change behaviour shipped at `eac33da`. Both are
recorded here before implementation, with the criterion that falsifies each,
as the ruling requires.

**19.1 A sandboxed iframe is no longer built.** Today a same-origin
`<iframe sandbox src=…>` becomes an ordinary child frame. It will not be built,
and `sandboxed` joins the closed skip vocabulary as its thirteenth reason.
*Falsifying criterion:* a page whose only embedded document carries `sandbox`
enumerates one frame and tallies exactly `{"sandboxed": 1}`; the criterion
fails against the host at `eac33da`, which builds two frames and tallies
nothing.

**19.2 A link whose target is not `_self` no longer navigates.** Today a click
or an enter press on `<a href target="_blank">` navigates the current target,
which is neither what the page asked for nor something this host models. It
becomes a typed refusal. *Falsifying criterion:* a click on a link with
`target="_blank"` is refused `unsupported_capability` with reason
`target_not_self`, the URL, generation, realm and revision all unchanged; the
criterion fails against `eac33da`, which navigates.

Both apply to the main frame as well as to children, because the reason they
are wrong does not depend on which frame they are in.

## 20. Fixed-vocabulary state so an agent can anticipate a refusal

An agent must be able to see that an activation will be refused without
reading page text. The semantic snapshot's activatable nodes gain one bounded
fact, over a closed vocabulary and never a URL, a target name or any other
page text:

- `activation`: `"allowed"`, or one of `"target_not_self"`,
  `"download_unsupported"`, `"scheme_unsupported"`, `"fragment_unsupported"`,
  `"control_disabled"`, `"form_method_unsupported"`.

It is a host-level additive diagnostic, described normatively in
`labs/native-dom/README.md` beside `frames_skipped`, and the protocol obliges
no host to report it. `frames_skipped` gains `sandboxed`. Nothing else about
the snapshot changes.

## 21. The courts, pre-registered

Frozen here before any code, to be written into the court files and run in the
implementation round.

1. **Identity.** An action in a child changes only that child; the parent's
   URL, generation, realm and history are untouched; no frame ends.
2. **The revision, over every event of §14.1.** A child mutation, a child
   replacement, a child end, a parent mutation and a parent navigation each
   move `R` by exactly the table's amount, and `R` never decreases across the
   whole sequence.
3. **Stale references.** After an action in any frame, a reference taken
   before it is `stale_revision` in **every** frame; a reference from a
   child's snapshot is refused after that child navigates, even though its
   band is reused by the replacement.
4. **Per-frame snapshot isolation.** A node index valid in one frame's
   snapshot is `not_found` when presented in another frame's band, and a frame
   with no snapshot cannot be acted in.
5. **Wait convergence.** `target.wait` returns for a revision reached by a
   child's mutation, and the host reads no child realm while waiting.
6. **Child link and GET submit.** Each replaces the child's document, keeps
   its frame id, increments its generation, retires and replaces its realm,
   advances `R` by one, and leaves the parent and the history untouched.
7. **Rollback.** A child navigation that fails leaves identity, document,
   state and `R` exactly as they were, and its cookies never reach the
   profile.
8. **Aggregate budget.** A parent document whose allowance is spent refuses a
   child navigation with `resource_limit` and leaves the child untouched; a
   parent navigation resets the allowance for the new document's frames.
9. **Audit secrecy.** Every action record carries its frame id and no URL,
   value, target name or other page text; the interned frame ids stay bounded.
10. **Teardown.** A parent navigation still ends every child in reverse order
    and retires every realm exactly once, with the new global revision correct
    afterwards.
11. **Fail-closed vocabulary.** Sandbox, non-`_self` targets, `download`,
    `javascript:` and fragment hrefs each refuse typed with their fixed
    reason, move nothing, and are visible in the snapshot's `activation` fact
    beforehand.
12. **Memory.** The child-frame court's criteria are rerun unchanged, with the
    per-frame bookkeeping inside the same caps: one child ≤ 262,144 live owner
    bytes, seven ≤ 1,835,008, owners returning to the baseline on close.

## 22. What is unchanged

Same-origin before the fetch and after every redirect, `text/html` only, the
transactional jar for every child fetch including a child navigation's, URL
bytes in owner accounting, `frames_skipped` with its fixed vocabulary,
`scripts_skipped` untouched, frames never capability owners, one `not_found`
for a foreign, ended or unknown frame, the pinned navigation result, and no
protocol expansion of any kind.

## 23. Two corrections from the sign-off (Parts I and II stay as written)

### 23.1 The revision invariant was too strong, and the court already knew

§14's invariant (S) claimed every applied action advances `R` by exactly one.
That is false for the main frame and the evidence was already committed: the
form court records a failed submit moving the revision from 1 to 3, because
the page's own submit handler mutated the document. The algebra of §14.1 is
unaffected — the folds and the membership rules are right — but the claim made
about it was not.

The realm's settle rule is: on an applied outcome, if the counter has not
moved during the action, add one; if handlers already moved it, add nothing.
Writing `h` for the mutations the realm honestly counted during an action,
the corrected invariants are:

- **(M) Monotonic.** No host operation decreases `R`. Unchanged and still
  proven by §14.1.
- **(N) Committed navigation.** `R` advances by **exactly one** from the
  **pre-navigation** `R`, that is from its value at the moment of commit,
  after any handler the action ran. Measured from before the triggering
  action the advance is `h + 1`, and the difference between those two
  readings is exactly the handler's own effect.
- **(A) Applied non-navigation action.** `ΔR = max(h, 1)`, so at least one and
  otherwise exactly what the realm counted. Not `h + 1`: the settle rule does
  not add a second increment on top of a handler's.
- **(C) Canceled or failed action.** `ΔR = h ≥ 0`. Only handler effects, which
  is precisely the 1 → 3 the form court observed.
- **(F) Script-free frames.** In a child, `h = 0` always, so an applied action
  advances exactly one and a canceled one advances nothing. **This is a
  property of script-free frames, not a target-wide invariant**, and it must
  not be written into any criterion that a main-frame action can reach.

A regression criterion is added to preserve the existing behaviour rather than
tidy it away: a main-frame submit whose handler mutates advances `R` by the
handler's count, the answer still reports `applied: false`, and the revision
the caller reads afterwards is the one that includes those mutations.

### 23.2 Effective targets, context-aware

§18's single "not `_self`" rule was too blunt. The rule is the effective
target computed the way HTML computes it, judged against the frame the
activation happens in.

**Computing it.** For a link, the element's `target`. For a submit, the
submitter's `formtarget` if it has one, otherwise the form's `target`. In both
cases, if the element carries no target of its own **and the document has a
`<base target>`**, the effective target is decided by a feature this host does
not model, so the activation is refused `base_target_unmodeled` rather than
silently treated as self. An explicit element target overrides a base target,
as in HTML, so a document with a `<base target>` does not poison activations
that name their own.

**Normalising it.** Trim, then compare ASCII-case-insensitively against the
reserved keywords, so `_SELF`, `_Blank` and `_top` are recognised as keywords
rather than as names.

**Judging it.**

| Effective target | Main frame | Child frame |
|---|---|---|
| absent, empty, `_self` | allowed | allowed |
| `_parent`, `_top` | allowed — in the main frame they *are* the current context | refused `target_cross_frame`: they name a context this slice does not act on |
| `_blank` | refused `target_named` | refused `target_named` |
| any other name | refused `target_named` | refused `target_named` |

This replaces §18's `target_not_self` with two reasons, because an agent that
sees `target_cross_frame` learns something different from one that sees
`target_named`, and §19.2's falsifying criterion is restated in those terms.

**Form submission, fully audited.** Before any event is dispatched:

- **method.** The effective method is the submitter's `formmethod` if present,
  otherwise the form's `method`, normalised case-insensitively. Only `GET` is
  honoured; anything else is refused `form_method_unsupported`. A POST form
  with a `formmethod="get"` submitter is therefore allowed, which is what HTML
  says, and a GET form with a `formmethod="post"` submitter is refused, which
  it did not used to be.
- **target.** The effective target above, refused before dispatch rather than
  after.
- **action.** A submitter's `formaction` overrides the form's `action` and is
  resolved under exactly the same origin, scheme and budget rules. Honouring
  it is not an approximation — it is the same URL machinery — and silently
  ignoring it would submit to the wrong address, which is worse than either
  refusing or honouring.
- **`formenctype` and `formnovalidate`** have no meaning here: an enctype only
  matters for a method this host refuses, and this host implements no
  constraint validation. They are recorded as inert rather than obeyed.

`download`, unsupported schemes and fragment-only hrefs stay fail-closed as
§18 has them.

### 23.3 The activation fact, restated

The closed vocabulary becomes: `"allowed"`, `"target_named"`,
`"target_cross_frame"`, `"base_target_unmodeled"`, `"download_unsupported"`,
`"scheme_unsupported"`, `"fragment_unsupported"`, `"control_disabled"`,
`"form_method_unsupported"`. It carries the decision and never the target's
name, the href, the action or any other page text. For a form node it reflects
the form's own method and target; a submitter's overrides are the submitter's
own fact, so an agent reading a form and its buttons sees which button it may
press.

### 23.4 Court, extended

Added to §21: the regression criterion of 23.1; every cell of 23.2's table in
both a main frame and a child, with the old host falsifying the cases it gets
wrong; `formmethod` and `formtarget` overrides in both directions; a
`formaction` that is honoured; a `<base target>` document refusing an
activation that has no explicit target of its own while allowing one that
does; and the case-insensitive normalisation of every reserved keyword.

## 24. Findings from the implementation (recorded, not tidied away)

**24.1 A submit that navigates was counting its event twice.** The realm's
submit path settled — adding one to its frame's counter — and the navigation
that followed added one of its own, so a GET submit advanced `R` by two while
a link click advanced it by one. Both have exactly one observable consequence,
the document that replaces the old one, so the submit path no longer settles
and both are one. This corrects the main frame as well as a child; no
committed criterion asserted the old arithmetic, and §23.1's invariant (N) is
what says which of the two is right.

**24.2 The court's own arithmetic, twice.** A criterion measured a child
submit's advance from before the action rather than from the pre-navigation
revision, and another expected a handler that mutates two nodes to advance the
revision twice when the observer coalesces one batch into one increment. Both
were the court being wrong about the host. The first is now measured the way
§23.1 defines it; the second asserts what is actually true — the failed
submit's handler effects stand and the revision advanced only by them — and
the multi-step behaviour the ruling asked to preserve stays asserted where it
was already observed, in the form court, which still passes unchanged.

**24.3 A pre-existing flaky test, unrelated and untouched.**
`frame_region::tests::map_write_and_unmap_exactly_once` failed once during
this work and reproduces on unmodified sources: its counters are global and
another test's mapping can land between its two samples. Three reruns and a
single-threaded run all pass. It is recorded here rather than fixed, because
fixing it belongs to the surface slice and not to this increment.

## 25. Two audit blockers, amended before code

### 25.1 Saturation was specified and not implemented

§14 says that once `R` saturates, every action and navigation is refused
`resource_limit` with a fixed reason. The code does not do that. It saturates
silently in `revision()` when it folds the base, the main counter and the
children; `navigate_child` folds the base with a saturating add; the main
navigation computes `base_revision + 1` and `generation + 1` unchecked; and
the stale-revision detail reports `base + current` unchecked. A host at the
boundary would therefore stop discriminating staleness while continuing to
serve actions — the exact failure the section exists to prevent — and the
court never went near the boundary.

**The rule, restated so it can be implemented and proven.** All global-revision
arithmetic lives in one place and is **checked**, never saturating:

- `R = base + main counter + Σ child counters`, and if that sum is not
  representable the read answers `resource_limit` rather than a wrong number.
  A read *at* `u64::MAX` is legitimate and stays observable; only an
  unrepresentable one is refused.
- A fold is `base' = base + counter + advance`, checked. An unrepresentable
  fold is refused **before** the operation that would need it.
- Every operation that must advance — an applied action, a main navigation, a
  child navigation, a scroll — checks that it *can* advance **before it
  dispatches an event, fetches a document or builds a realm**, and refuses
  `resource_limit` with reason `revision_saturated` when it cannot. Nothing is
  dispatched, no realm is built, no history entry is made, no budget is spent
  and no identity moves.
- A generation increment is checked the same way and refuses with the same
  reason.

**Proving it.** A court-only knob seeds a target's revision base, so the court
can stand the host at the boundary and prove: a read at the maximum still
answers; an action, a main navigation and a child navigation are each refused
`resource_limit` with the fixed reason; and afterwards the URL, generation,
realm, history, budget and every frame's identity are exactly what they were.
Unit tests cover the arithmetic itself, including that a parent replacement
folding several children is checked rather than saturating.

### 25.2 The surface paths compute a different revision

`surface_rows` derives its snapshot revision as `revision_base + main counter`
and the scroll path reports `revision_base + after`. Both omit the cached
child counters, so after an action in a child they report a **smaller** number
than the target-global revision — a second, disagreeing definition of the one
value that staleness depends on. That is a violation of §14 and it is fixed by
routing both through the same checked helper as everything else. The
regression is a unit test on that helper: the surface paths themselves need a
surface process, and nothing here runs one.

### 25.3 An explicitly empty target was conflated with an absent one

`targetOf` reads `hasAttribute` and then treats a trimmed-empty value the same
as an absent attribute, so `target=""` under a `<base target>` was refused
`base_target_unmodeled` when the ruling — and HTML — say it is allowed. HTML's
"getting an element's target" returns the attribute's value **if the element
has the attribute at all**, and consults the base element only when it does
not. So:

- attribute absent → the base decides; with a base present that is
  `base_target_unmodeled`, without one it is `allowed`;
- attribute present and empty → `allowed`, and the base is never consulted;
- attribute present and whitespace-only → it names a context whose name is
  whitespace, which this host does not model: `target_named`, fail-closed;
- otherwise the keywords of §23.2, normalised case-insensitively.

The same rule applies to a submitter's `formtarget`, where an explicitly empty
value likewise means the current frame and suppresses the base.

The court's gap that let this through is recorded with it: it tested an empty
target only on a page with no base, and tested a base only against an absent
target and an explicit `_self`, so the one combination that distinguishes the
two readings was never taken. It now tests, in the main frame and in a child,
that an explicit empty `target` and an explicit empty `formtarget` under a
`<base target>` are `allowed` and navigate the current frame, while an absent
target under the same base stays `base_target_unmodeled`.

## 26. Third blocker: the effective action was never preflighted

§23.2 says the effective method, target **and action** are audited before any
event. Only the first two are. The realm dispatches `submit`, then builds the
navigate URL from `formaction` or `action`, and every judgement about that URL
— its scheme, its resolution, its byte bound, its origin — happens in the host
*after* the page's handlers have already run. The court proved an allowed
`formaction` is honoured and had no falsifier for a forbidden one.

The same gap exists one level down in `linkDecision`: the scheme is matched
against the raw attribute, so a value with leading whitespace — which HTML
strips before it parses a URL — slips past the test and reaches the host as a
`javascript:` URL, refused only after the click was dispatched.

### 26.1 A preflight phase, before any event

Every activating action — `click`, `press`, `submit` — is decided in two
phases. The first dispatches nothing:

1. **In the realm.** The effective method, the effective target and the
   effective action are computed exactly as §23.2 defines them, with HTML's
   leading and trailing ASCII whitespace stripped from any URL value before it
   is judged and schemes compared case-insensitively. The phase answers a
   fixed-vocabulary decision and, when the action would navigate, the URL it
   would navigate to. No event is dispatched, nothing is written, no counter
   moves.
2. **In the host.** That URL is resolved against the frame's own document URL
   and judged: a resolution that fails is `invalid_request`; a scheme that is
   not `http`/`https` is `unsupported_capability` with `scheme_unsupported`; a
   resolved URL over `MAX_URL_BYTES` is `resource_limit` with
   `submitted_url_bytes`, which used to be decided after the submit had been
   dispatched; and for a **child frame** a URL that leaves the parent
   document's origin is `permission_denied` with `cross_origin_action`, which
   is the invariant every live child already has to satisfy.

Only if both phases pass does the second phase run: the events, the effects,
the navigation.

Because nothing executes in a realm between two host evaluations, the two
phases see the same document. In a child, which runs no scripts, that is
trivially true; in the main frame it holds because a realm runs only while the
host is inside an `eval` on it, which is the same argument §15 makes for the
counter cache.

### 26.2 What stays after the dispatch, and why

The origin's **admissibility under the network policy** stays where it is,
after the submit event. It cannot be decided without resolving the name — the
allowlist is only one of its inputs, and a non-allowlisted public address is
legitimate — so deciding it early would mean performing a network act before
the page's own handler had run, which is worse than the ordering it would fix,
and would let activation timing leak the shape of an allowlist the host
deliberately hides. Redirects, status, media type and everything else the
network answers stay there for the same reason.

So the existing rule is preserved exactly: **a failure that happens only after
a valid preflight and a dispatched submit keeps the handler's effects**, and
the revision they moved stays moved. That is what the form court's failed
submit already proves, and it is a different case from a refusal, which now
happens before anything is dispatched at all.

### 26.3 Court

Added: a form whose `action` and a submitter whose `formaction` carry an
unsupported scheme, a malformed value, and a value over the URL bound, each
refused typed **before** any event — proven by a page whose submit handler
leaves a mark, which must be absent — with no revision, identity or history
movement; the same for a link href with leading whitespace before a
`javascript:` scheme, which used to slip past; a child whose action would
leave the parent's origin, refused `cross_origin_action` before dispatch; and,
unchanged, the existing case where a valid preflight is followed by a fetch
failure and the handler's mark **is** present. Every reason stays in the fixed
vocabulary and no URL, query or page text enters any receipt.

## 27. The first hard limit is the realm's, not the host's

§25.1 reasoned about `u64`. That is the second limit. A frame's counter lives
in a JavaScript realm as a Number, so it stops representing exact increments
at **2^53 − 1**, far below anything `u64` cares about. Above that the realm
would answer a counter that did not really advance, and staleness would fail
silently — the same failure §14 exists to prevent, reached much earlier.

The model therefore has **two** limits, and they are not the same kind:

- **Per frame, `MAX_SAFE_COUNTER = 2^53 − 1`.** Before any action is
  dispatched in a frame whose counter cannot represent one more exact
  increment, the action is refused `resource_limit` with reason
  `revision_saturated`: no event, no write, no counter movement, no identity
  or history change.
- **Per target, `u64`, checked.** The aggregate `base + main + Σ children` is
  computed with checked arithmetic in one helper. A read that is not
  representable answers `resource_limit`; a read *at* the maximum is a real
  answer and stays observable.

A navigation folds a **safe** local counter into the base with `checked_add`,
and preflights room for its own `+1` — and for the generation's `+1` — before
it fetches a document or builds a realm. A fold that would not be
representable refuses before the network is touched.

Every place that reports a revision goes through the one helper, the
`stale_revision` details included, so a child's counter is never omitted from
a number a caller compares against. No `BigInt`, and no protocol shape moves
in this increment.

**Court and unit seams.** A court-only knob seeds a frame's counter and a
target's base, so the boundary is reachable: at `MAX_SAFE − 1` an action still
applies and advances by one; at `MAX_SAFE` it is refused with no event
dispatched and nothing moved; at the `u64` aggregate boundary a read still
answers while an action and a navigation are refused. Unit tests cover the
helper directly, including a parent replacement folding several children.

§26.3's falsifiers are extended with the whitespace forms the third blocker
named: a link href and a form `action` and a submitter `formaction` whose
value carries leading or trailing ASCII whitespace before a mixed-case
`JavaScript:` scheme, or before a `#fragment`, are refused with the existing
closed reasons — `scheme_unsupported` and `fragment_unsupported` — before any
event. This is whitespace normalisation for the values this host already
judges, not a claim about URL compatibility in general.

## 28. Correction: a whitespace-only target is an empty one

§25.3 introduced a branch that §23.2 had not ruled: it made a present but
whitespace-only `target` a `target_named` refusal. That is a new rule invented
during a fix, and it is withdrawn. §23.2's normalisation stands unchanged —
trim, then judge — so the rule is exactly:

- **attribute absent** → the base decides; with a `<base target>` present that
  is `base_target_unmodeled`, without one it is `allowed`;
- **attribute present**, trimmed to empty (exactly empty or ASCII whitespace
  only) → `allowed`, and the base is never consulted;
- **attribute present**, otherwise → the keywords of §23.2, normalised
  case-insensitively, else `target_named`.

The same for a submitter's `formtarget`. The court carries both an
exactly-empty and a whitespace-only falsifier, in the main frame and in a
child, under a `<base target>`.

## 29. The preflight had a time-of-check gap

§26.1 claimed nothing executes between the two phases. That is true for a
child and **false for the main frame**: `Realm::eval_staged` drains queued jobs
before it returns, and `Target::eval` then pumps the network, so a queued
promise job can run after the preflight answered. Such a job can write a
control's `value` property, or an element's `action`, without mutating the
DOM — so the revision need not move, and the second phase could serialise a
different URL from the one the host approved. Revision equality is not a
sufficient guard, and the design must not rely on it.

**The fix: the activation phase re-derives and compares.** The preflight
answers, besides its decision, a **signature** of the complete effective
activation — the kind of activation, the decision, the effective method where
one applies, and the exact URL it would navigate to including the serialised
query. The host passes that signature back into the activation phase, which
re-derives it from the document as it is *then* and compares, byte for byte,
**before dispatching any event**. A difference is refused
`unsupported_capability` with the fixed reason `preflight_mismatch`: no event,
no write, no navigation, no revision or identity movement.

The signature is page data. It is carried between the two evaluations and
compared inside the realm; it is never logged, never audited, never put in an
error's details and never written to a receipt. The reason is a fixed word
from the closed vocabulary, which is all a caller learns.

`preflight_mismatch` joins the closed activation vocabulary. The court gains a
main-frame falsifier: a page whose queued microtask rewrites a control's value
between the phases, proving that no event is dispatched, nothing navigates and
nothing moves, while the same page without the rewrite activates normally.

## 30. Three more from the code audit, amended before the fix

### 30.1 A stale detail reported a frame-local number as if it were global

Both stale paths in `target_act` — the one the preflight takes and the one the
activation takes — write `current_revision` straight from the counter the
realm answered. That number is one frame's, so for a main action it omits
every child's counter and for a child action it omits the main frame's and its
siblings'. It also bypasses the checked helper the rest of the host uses,
which the previous report claimed it did not.

Both paths now compute the same value the same way. The host already holds the
global revision it validated against and the frame counter it expected, so the
true global is `validated − expected + reported`, in checked arithmetic; a
result that is not representable refuses `resource_limit` rather than
reporting a number.

**How far the court can falsify this, stated honestly.** A court criterion
holds a main-frame reference, moves a child's counter so a frame-local number
and a global one cannot coincide, and asserts that the stale answer names the
target's revision. That criterion passes against the previous build as well,
and the reason is worth recording rather than hiding: the host's own global
check fires before the realm is ever asked, so the two realm-side paths are
reached only if a frame's counter and the target's global disagree in exactly
compensating ways, which no fixture found produces. They were wrong all the
same, and the arithmetic that replaces them is falsified directly by unit
tests over the conversion in both directions, including the two cases where it
must refuse rather than wrap. The court criterion stands as a regression on the
reachable path, not as proof that the unreachable one was exercised.

### 30.2 A scroll changed observable state before it checked it could

`apply_surface_input` assigns `scroll_y`, then evaluates the increment, and
only afterwards drops the report if the global revision does not fit. At the
boundary it therefore moves the page and advances the realm's counter while
telling the court the input was dropped, and the increment is lost from the
host's view either way. Both limits are checked **before** `scroll_y` is
touched and before anything is evaluated; a refusal leaves the scroll offset
and the revision exactly as they were.

### 30.3 The surface click derived the main counter by hand

It folded the children with saturating adds and subtracted them from the
global, duplicating arithmetic that now exists once. There is one way to get a
frame's counter from the global — a checked helper — and both surface paths
use it.

The regressions are unit seams on the helpers. Nothing here runs a surface
process, and no visual path is exercised.
