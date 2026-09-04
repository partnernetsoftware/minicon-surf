# Bounded child frames on the native route — decision record, design only

Status: **frozen design and frozen court, no product code**. Two decisions in
§12 are genuinely ambiguous and are put to the root rather than guessed.

The doctrine this answers to is a conjunction, not a trade: memory-optimized
and Agent-use oriented, both, with browser convergence allowed to weaken
neither (`plan/plan-0.0.x.md` §1). A child frame is the first feature in this
series where the two pull against each other in an obvious way. A frame is a
second document, a second realm and a second fetch, which is memory; an agent
that cannot see inside an embedded document is a worse agent surface, which is
Agent-use. This record chooses the smallest shape that improves the second
without surrendering the first, and says plainly what it leaves undone.

## 1. What already exists, audited rather than assumed

**The protocol already specifies child frames.** `protocol/README.md` §"Frames
and realms" fixes four separately-moving concepts (target revision, frame
identity, document generation, realm identity), states that enumeration is
bounded and only through the owning target, that `frames[]` carries `frame`,
`parent`, `generation`, `realm` with the main frame first and at most
`frame_limit` entries, that a `frame`/`realm` argument narrows and never
widens, and that a foreign, ended or unknown id is one and the same
`not_found`. Child frames are already part of the contract. **Nothing in this
increment needs a new operation, and this design adds none.**

**The synthetic host (D4) is the executable reference.**
`labs/synthetic-control/src/lib.rs` has `MAX_FRAMES_PER_TARGET = 8`, mints a
main frame plus exactly one bounded child at target open, gives each frame its
own generation and realm id, and on main-frame navigation drains the children,
returning their ids as `ended_frames` beside `retired_realm`. Its court
(`labs/synthetic-control/frame-realm-court.py`) asserts the enumeration, the
narrowed snapshot, the wrong-realm refusal and the post-navigation
`not_found`. That is the shape to converge on, and it is 0.0.1 — no version
moves.

**The native route has one frame and knows nothing of iframes.**
`labs/native-dom/src/main.rs` carries `frame_id`, `generation`, `realm_id` and
one `Realm` flat on `Target`; `target.inspect` answers a literal
`"frames":[{…,"parent":null,…}]` with `"frame_limit":1`; `target.snapshot`
accepts `frame`/`realm` only to compare them against the target's single pair.
`document_framing` is HTTP transfer framing and has nothing to do with this.
An `<iframe>` element is parsed by html5ever, serialized into the realm's tree
like any other element and then ignored: nothing fetches its `src`, no second
realm exists, and the semantic snapshot gives it no role.

**CDP is already generic and already slightly wrong.**
`labs/native-dom/src/cdp.rs::page_get_frame_tree` maps `frames[]` to
`frameTree` by taking the first as the main frame and every other as a flat
child with `childFrames: []`. It would project native children the day they
exist, with two recorded losses: nesting is flattened, and `describe` fills
every frame's `url` from `inspect["url"]`, which is the *target's* URL. With
one frame that is correct by construction; with children it would report the
parent's address for a child document. The mapping files already record "the
synthetic host has exactly one bounded child frame and no nesting; engine
hosts expose no frames yet".

**The other routes offer nothing to copy.** Lightpanda's evidence is a
low-memory CDP server with one concurrent target and no frame material at all.
Servo's README names frame and realm mapping as a *next step* that was never
taken; its CDP surface stops before frame identity. Neither route has iframe
memory evidence, so neither can settle a native design. The honest comparison
is in §11 and it is short.

## 2. The route, and the ones rejected

Chosen: **bounded, same-origin, depth-one child documents, observed but not
acted in.** A child frame is built when its parent document is built, from an
`<iframe src>` that resolves same-origin, under the parent document's own
fetch and byte budget. It gets its own frame id, its own generation, its own
realm and its own seeded tree. `target.inspect` enumerates it.
`target.snapshot` narrows to it. `target.act` does not reach it in this slice.

Rejected, with reasons:

- *A `frame` field on the node reference so actions reach children.* It is the
  natural next step for Agent use and it is a request-shape change to 0.0.2
  (`reference` is exactly `(target, revision, node)` in `protocol/README.md`
  and enforced by `exact_object`). §12.1 puts it to the root rather than
  taking it.
- *A new `frame.*` operation family.* Unnecessary: inspect plus narrowed
  snapshot already cover enumeration and observation, which is what the
  envelope asks to prefer.
- *Cross-origin children.* The host already refuses cross-origin external
  scripts (`net::same_origin`, `budget.denied += 1`). A cross-origin child
  document would import an origin's bytes, cookies and storage identity into a
  target under a single jar keyed by `document_host`, which is a security
  design of its own and is not needed to prove the frame model. Refused and
  recorded, exactly like a cross-origin script.
- *Nesting.* Depth one. A child's own `<iframe>` is not built. Nesting
  multiplies the memory question without changing the identity question.
- *Lazy or on-demand child building.* A child that is built only when observed
  makes `target.inspect` results depend on observation order. Children are
  built with their parent or not at all.

## 3. Identity

A child frame's id is minted from the same host-wide counter as the main
frame's (`frame_<n>`), never reused within a host generation, and opaque. Its
`parent` is its parent frame's id. Its realm id comes from the same
`realm_<n>` counter. The order of `frames[]` is the main frame first, then
children in document order of their `<iframe>` elements, which is stable for a
given document.

A child frame's identity belongs to its target. It is never addressable except
through `target` plus the `frame` argument, so nothing enumerates one target's
frames through another.

## 4. Lifetime, generation and realm replacement

- A child frame begins when its parent document is committed and ends when
  that document is replaced or its target closes. Within a document a child is
  never created or destroyed, because this host does not run a mutation-driven
  frame lifecycle; a script that appends an `<iframe>` after load does not get
  a frame, and that is a recorded loss (§10).
- Every frame carries its own generation, starting at 1. A child's generation
  never advances in this slice, because nothing navigates a child.
- Every frame carries its own realm. A parent navigation mints a new realm for
  the main frame, retires the old one, ends every child frame and retires
  every child realm. Realm ids are never reused.
- `target.navigate`, `target.reload` and `target.traverse` all replace the
  main document and therefore all end the children. `target.act`'s form submit
  and link click navigate through the same path and end them too.
- The navigation result reports `ended_frames`, the array of ended child frame
  ids, beside the existing `frame`, `generation`, `realm` and `retired_realm`.
  That field is already in the 0.0.1 contract through the synthetic host
  (`lib.rs:1066`); the native route adopts it rather than inventing one.

## 5. One refusal for foreign, ended and unknown ids

Unchanged and extended to children: a `frame` or `realm` that is not live in
the named target is `not_found` with the same reason strings already used
(`frame_not_live_in_target`, `realm_not_live_in_target`), whether it belongs to
another target, ended with its parent's document, or never existed. A caller
cannot tell those three apart, which is the point. A `realm` argument that
names a *live* realm of a *different* live frame of the same target is also
`not_found`: the realm must be the narrowed frame's own current realm.

## 6. Capability ownership

Frames and realms are never capability owners. The native host implements no
capability attenuation at all and fails closed on the field
(`invalid_request`), which this slice does not change. Ownership resolves from
the `target` argument, as it does today.

## 7. Cross-origin, cookies and storage

- A child is built only if its resolved `src` is same-origin with its parent
  document under `net::same_origin`. Anything else — cross-origin, a
  non-`http(s)` scheme, `about:`, `srcdoc`, a malformed or missing `src` — is
  refused before any fetch, charged as a denied attempt on the parent's
  budget, and recorded in the same skipped-source shape the parent already
  uses for scripts.
- Because a child is same-origin by construction, it shares its parent's
  origin, its cookie jar and its `document_host`. No new origin, jar or
  storage partition is introduced by this slice. That is the direct
  consequence of §2's refusal, and it is why that refusal is also the
  conservative security choice.
- A fixture target's children may come only from fixture files, as with every
  other fetch on that path. The network boundary does not widen.

## 8. Budgets

Child fetches are charged to the **parent document's** budget, not to a fresh
one: the existing `MAX_FETCHES_PER_DOCUMENT` (32) and `MAX_BYTES_PER_DOCUMENT`
(4 MiB) cover the parent, its scripts, its children and its children's
scripts together. A document that spends its budget on children has less for
scripts, which is the correct incentive and needs no new number. Exhausting
the budget skips the remaining children with a recorded reason; it never fails
the parent's navigation.

Deadlines are shared the same way: children are built inside the same deadline
as their parent's document, and a child that does not make it is skipped, not
fatal.

## 9. Teardown order

Deterministic and reverse of construction: children before parents, realm
before frame, and the parent's old realm only after every child realm is gone.
On `target.close` the same order runs before the target's own storage and
network state is dropped. Each ended realm increments `realms_retired_total`
exactly once, so the memory report's counters stay a true count of retirements
rather than of navigations.

## 10. Limits, and the losses they buy

| Limit | Value | Why |
|---|---|---|
| frames per target | 8 including the main frame (`frame_limit` becomes 8) | matches the synthetic host and the protocol's `frame_limit`; a ninth `<iframe>` is skipped and recorded |
| depth | 1 | a child's own iframes are not built |
| child fetches and bytes | the parent document's existing 32 fetches / 4 MiB | no new budget |
| child origin | same-origin only | §7 |

Losses, recorded rather than approximated:

1. **No acting inside a child.** `target.act` still takes a reference that
   names only `(target, revision, node)`, so an agent can read an embedded
   document but not click in it. This is the largest Agent-use gap in the
   slice and the subject of §12.1.
2. **No child navigation.** A child's generation never advances; children are
   replaced only by replacing the parent document.
3. **No dynamic frame lifecycle.** Scripts that add or remove iframes after
   load change nothing.
4. **No nesting, no cross-origin, no `srcdoc`.**
5. **CDP projects children flat, with the parent's URL** until §12.2 is
   ruled. Nesting and per-frame URL stay recorded losses in the mapping files.
6. **No realm projection**, unchanged.

## 11. The three routes, honestly

| Route | What it can show about child frames | Verdict for this increment |
|---|---|---|
| synthetic control host (D4) | the full identity, lifetime, enumeration and narrowing contract, executable, at 0.0.1, with one bounded child and `ended_frames` | the reference this design converges on; not a product crate, and its frames hold no real documents, so it says nothing about memory |
| native-dom | real html5ever documents, real QuickJS realms, real fetches under a real budget; today exactly one frame | the only route that can answer the memory question, and the one this design implements |
| Lightpanda | lowest measured memory of any route, but one concurrent target, no in-process reporter, no frame evidence at all | no bearing; cannot be consulted on this |
| Servo | a real engine with real iframes, but its own README names frame and realm mapping as a next step that was never taken, and its memory evidence is about allocator retention, not frames | no bearing yet; if it ever maps frames it becomes the cross-check for the native numbers |

The honest summary is that only one route has evidence here, so the design is
deliberately the smallest thing that route's existing evidence makes
unambiguous, and the two things it does not make unambiguous are in §12.

## 12. The two decisions this design will not guess

**12.1 May a node reference name a frame?** Acting inside a child needs
`reference` to become `(target, revision, frame?, node)`, where an absent
`frame` means the main frame, byte-for-byte compatible for every existing
request. It is a 0.0.2 request-shape change, so it is the root's to make, not
mine. Without it the slice is observation-only and loss 10.1 stands. With it,
the court gains a group and the host gains a per-frame node index. The design
is complete either way; only the court's activation group depends on it.

**12.2 May a `frames[]` entry carry `url`?** The CDP projection reports the
target's URL for every frame (`cdp.rs::describe`). With children that is
wrong. Either `frames[]` gains a `url` per entry, which is a result-shape
addition to 0.0.2 that the schema permits (`result` is any object of ≤64
properties) but the README pins, and CDP becomes correct; or `frames[]` stays
as documented and the CDP loss is widened in writing to say that a child's
`url` is its parent's. I recommend the first, because a child frame whose URL
cannot be read is a poor agent surface and a misleading CDP one, but I have
not taken it.

Until both are ruled, this record and its court are frozen and no product code
is written.

## 13. Pre-registered memory criteria

Registered before any measurement, from the published navigation and form
evidence, and never moved afterwards: a criterion that fails is narrowed or
the change is optimized, and the movement is recorded chronologically.

Every measurement separates two quantities that the earlier increments proved
are not the same thing:

- **active owner bytes** — what `memory.report` attributes to live owners
  (frames, realms, documents), which is the number this design is responsible
  for;
- **allocator retention** — the process footprint that does not come back when
  the owners die, which the navigation increment showed is page-granular and
  dominated by parsing and realm seeding, and which is reported rather than
  gated.

| # | Workload, 64 cycles unless stated | Pre-registered criterion |
|---|---|---|
| M1 | one same-origin child vs the identical page with the `<iframe>` removed | active owner bytes for the child's realm and document ≤ 262,144 per child; the differential is reported, not gated |
| M2 | 7 concurrent children (the bound) vs no children | active owner bytes ≤ 1,835,008 total, i.e. M1's cap per child with no super-linear term |
| M3 | children ended by parent navigation, 64 times | after the 64th navigation, active owner bytes return to within 65,536 of the one-frame baseline; `realms_retired_total` equals exactly 64 × (children + 1) |
| M4 | repeated child replacement through repeated parent navigation, 64 times | process footprint growth over the last 32 cycles ≤ the first 32 cycles' growth, i.e. no unbounded slope; absolute retention reported |
| M5 | open and close a target with the bound of children, 64 times | active owner bytes return to the empty-host baseline exactly; process footprint retention ≤ 1,048,576 over the whole run, reported with its allocator |
| M6 | a page whose ninth iframe is over the bound | no ninth frame, one recorded skip, and no fetch charged for it |

M1's per-child cap is set from the form court's realm plateau criterion
(65,536 for a realm's live owners) with room for a child's parsed document,
and is deliberately generous in the direction that would catch a per-child
regression rather than one that flatters the design.

## 14. The frozen court

`labs/native-dom/child-frame-court.py`, written before the host changes and
failing until they exist. Groups:

1. **enumeration** — a page with children enumerates main-first, parents
   correct, `frame_limit` 8, ids opaque and distinct; a page without children
   still enumerates exactly one frame.
2. **narrowing** — a snapshot narrowed to a child observes the child's nodes
   and names its `frame`, `realm` and `generation`; narrowed to the main frame
   it does not see the child's nodes; a child realm on the main frame and a
   main realm on the child are both `not_found`.
3. **refusals** — a foreign target's frame, an ended frame and a never-existing
   frame are the same `not_found` with the same reason.
4. **lifetime** — parent navigation ends the children, reports `ended_frames`,
   mints one new main realm, and every ended frame and realm is afterwards
   `not_found`; `target.close` ends them in reverse order.
5. **policy** — a cross-origin `src`, a `srcdoc`, an `about:` src, a malformed
   src and a ninth child are each refused before any fetch, charged as denied
   where a fetch would have happened, and recorded; the parent still commits.
6. **budget** — children and their scripts are charged to the parent
   document's 32 fetches and 4 MiB, and exhausting the budget skips children
   rather than failing the parent.
7. **memory** — M1 to M6 of §13.
8. **cdp** — `Page.getFrameTree` projects the children flat with correct
   parent ids, and the recorded losses are asserted as losses.
9. **secrecy and headlessness** — no child document text, URL or fixture path
   in the ledger, the court log or the receipt; the court refuses to run with
   the visible-court variable set and spawns no surface.

Group 2's activation half and group 8's URL assertion are the only parts that
change with the §12 rulings, and both are written to be added, not rewritten.

## 15. Amendment before implementation: a hazard in §12.1, and what closes it

Running the frozen court against the pre-implementation host, to confirm that
it fails, made a defect in this record visible. It is recorded here in order;
§§1–14 stay as written.

**The hazard.** A node reference is `(target, revision, node)` and a node id is
an index into the frame's realm. With observation-only children, a snapshot
narrowed to a child would return references whose `node` ids are *the same
shape and the same numbers* as the main frame's. Handing such a reference to
`target.act` would not fail: it would resolve the index against the **main**
realm and act on whatever node happens to sit there. A caller doing the
obvious thing — observe the embedded document, then act on what it saw — would
silently mutate the wrong document. That is worse than not supporting the
action at all, and it is a safety property, not an ergonomic one.

**What closes it, without widening anything.** Node ids become **target-scoped
rather than frame-scoped**: each frame's ids are minted from a disjoint band of
the target's id space, so no id ever means two things within one target
revision. The protocol already says node ids alone have no meaning and are
valid only for their `(target, revision)` pair, so a disjoint per-target space
is inside the contract, not an extension of it. Nothing on the wire changes:
the reference stays exactly `(target, revision, node)`.

With that, `target.act` can *see* that a reference belongs to a child frame.
In this slice it refuses it, typed, with `unsupported_capability` and reason
`action_in_child_frame_unsupported`, before any event and without moving the
revision. Refusing is the safe side of the ambiguity and needs no ruling.

**§12.1 is narrowed, not answered.** The question is no longer "may a reference
name a frame", because it does not need to; it is "may an action reach a child
frame at all", which is a capability question that can be answered later
without any change to a request or result shape. Until it is answered the
refusal above stands. §12.2 is unchanged and still open.

**Court, extended.** Two criteria are added: node ids minted for a child frame
are disjoint from the main frame's within one revision, and a reference taken
from a child snapshot is refused by `target.act` with that typed reason
rather than acting on the main frame. The second is the one that would have
caught the hazard.

**M5's criterion, amended, with the number unmoved.** The same baseline run
showed M5's absolute retention bound already exceeded, 3.1 MB over 64
open-and-close cycles, on a host with no child frames at all: as frozen it
measured the page-granular allocator retention the navigation increment
already recorded rather than anything this design does. It becomes the
differential that increment settled on — the children's arm against the
identical childless arm — keeping the same 1 MiB bound and reporting both
absolute numbers. The bound did not move; what it is measured against did.
