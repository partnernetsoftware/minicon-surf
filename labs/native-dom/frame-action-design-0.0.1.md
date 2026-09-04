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
