# Round C: the tag is the host's

Design record for the first of the two implementation rounds ruled from
`element-fact-design-0.0.1.md`. Written **before** the code, with the court
frozen from §4 and failing until the code lands.

Scope, from the ruling: **round C only.** The host-owned immutable tag reader,
with the page's prior assignment behaviour preserved. **D is not implemented,
and F1 and F2 stay open** — a `method="post"` form is still submitted as a GET
and a `target="somewhere"` link is still activated when a page lies about them,
and this round does not change that. No new intrinsic capture, no widened
internals handle, no bound moved, nothing slimmed, no visual run, no soak,
nothing downloaded. The C+D combined pricing in the previous round is design
evidence about what a second round would cost; it is **not a promise that D will
land**.

## 1. The two re-freezes, ruled explicitly

Two frozen values fail by design the moment a base-shim change lands, and the
ruling authorises moving them. Both are recorded here before the code, and both
are amended chronologically in the court files rather than silently rewritten.

**`property-shape-court.py`'s `window` fingerprint.** The court pins the shape
of every own property of the objects the shim installs onto, and *any* new
global moves the `window` row — the same would have been true of `__mcsJson`
when H1 added it. `EXPECTED["window"]` moves from
`112:156a0f8b:Object:v:011|Function:v:011|Error:v:011` to whatever this round
measures, and the old value stays in the file as a comment with the date and the
reason. **`Element.prototype` does not move in this round**: C adds no prototype
member, and the court's row for it must stay exactly as it is. If it moves, this
round has done something it did not intend and the court should say so.

**`signature-integrity-court.py`'s base-byte pin.** Group D pins
`dom_shim_base.js` at 32,898 bytes so that slice could not pay for itself with
base bytes. That pin was correct for that slice and is correct now: it is what
will catch this round changing the base. It moves to the measured new value,
with the old one kept beside it in the comment, and `dom_shim_main.js` stays at
**26,485** — this round does not touch the main shim at all.

Nothing else moves. The typed refusal vocabulary is unchanged, every Rust-side
scheme, origin and bound guard is unchanged, and no protocol field changes.

## 2. What is trusted today, and what replaces it

`download_probe_script` asks `el.tagName.toLowerCase() !== "a"`
(`main.rs:775`), and `role()` asks the same question to decide whether the agent
is offered the element as a link at all (`main.rs:647`). `tagName` and
`localName` are ordinary writable own properties set in the `Element`
constructor (`dom_shim_base.js:315-318`), so a page closes the whole question
with `div.tagName = "A"` — measured in
`fail-open-triage-audit-0.0.1.md`, no intrinsic replaced.

The base shim gains a closure-owned `WeakMap` from element to its lower-case
tag, written in the constructor through the already-captured `weakMapSet`, and
read back through `__mcsTag`, a non-writable, non-configurable global on the
`__mcsJson` pattern. All seven host-script sites that asked
`el.tagName.toLowerCase()` ask `__mcsTag(el)`.

**No sixteenth capture.** `weakMapSet` and `weakMapGet` already exist and are
already referenced; this round adds none, and
`signature-integrity-court.py`'s fifteen-capture guard must stay green — it is
the guard that caught a sixteenth in the previous round's first build.

**The page's assignment still lands.** C removes nothing: `localName`,
`tagName` and `nodeName` stay writable own data properties, so
`el.tagName = "A"` still succeeds, still changes what the page reads back, and
still throws nothing. What changes is only that the host stops asking the
element and asks its own store instead. That is the "no-op landing" the ruling
requires, and it comes for free here because nothing is taken away — unlike
round D, where `__attrs` moves and an ignoring setter is mandatory
(`element-fact-design-0.0.1.md` §5).

## 3. What this closes, and what it does not

Closes **F5**, on both routes measured: the selective `toLowerCase` that reads
`"DIV"` as `"a"`, and the direct write of `tagName`/`localName`. And it closes
it further upstream than the download probe — with `role()` reading the host's
tag, the div never becomes a node, so the agent is not offered a link that is
not one.

Does not close **F1** or **F2**. They remain exactly as
`fail-open-triage-audit-0.0.1.md` recorded them, they are not folded in here,
and the court below says `tag` everywhere it means tag.

Does not close the remaining C-class snapshot corruptions: a node's **name**
still comes from `textContent`, which is the page's. Not a fail-open, not in
this ruling.

## 4. The court, frozen before the code

`element-tag-court.py`, three groups. Frozen now and run now against
`0da1c6b1`; the failing receipt is the falsification arm.

**A — the tag is the host's.** On four arms — unpatched, the selective
`toLowerCase`, the direct `tagName`/`localName` write, and an arm that tries to
replace and then delete `__mcsTag` itself — a `<div href= download=>` is never
offered as a node and never downloads, while the honest `<a download>` beside it
in the same document delivers its bytes on every arm. A `<p>` that *is* a node
is still refused `not_a_link`, so the vocabulary is preserved rather than
widened. The escalation pairs stay refused and unchanged: an anchor with a
`javascript:` href, one with a `file://` href pointing at a real file the court
writes, and one pointing at a second loopback origin the host was never told to
allow — `scheme_unsupported`, `scheme_unsupported` with the file's bytes never
returned, and `permission_denied/address`.

**B — the page keeps what it had.** A page that assigns `el.tagName` does not
throw and its script runs to completion; it reads back what it wrote, which the
court observes by having the page echo the value into a paragraph the snapshot
carries. Ordinary markup still classifies: the anchor is a `link`, the form a
`form`, the text input a `textbox`, the button a `button`. And a `cloneNode` of
an `<a href download>`, appended to the document, is still a `link` and still
downloads — the construction path the store must not miss.

**C — technique and cost.** No sixteenth capture. No host script still reads
`.tagName.toLowerCase()`. `__mcsTag` is installed `writable: false,
configurable: false`. And three frozen ceilings, set from the previous round's
measured candidate and now fixed: `dom_shim_base.js` grows by **at most 400
bytes**, child-frame M1 by at most **2,048** and M2 by at most **14,336**, on
**both** allocators. The measured candidate cost +293, +1,776/+1,792 and
+12,496/+14,048, so each ceiling has margin and none is vacuous.
`dom_shim_main.js` must not change at all.

Every source criterion first asserts that the region it inspects was found and
non-empty, and every refusal criterion is paired with an arm where the honest
element must still work.

## 4b. What the round actually cost, and one pin the ruling did not name

Measured on `e9e07111`, the built binary, against the ceilings frozen in §4:

| | before | after | delta | ceiling |
| --- | ---: | ---: | ---: | ---: |
| `dom_shim_base.js` | 32,898 | 33,290 | **+392** | +400 |
| `dom_shim_main.js` | 26,485 | 26,485 | 0 | must not move |
| child-frame M1, system | 233,962 | 235,658 | **+1,696** | +2,048 |
| child-frame M2, system | 1,636,236 | 1,648,172 | **+11,936** | +14,336 |
| child-frame M1, arena | 225,898 | 227,850 | **+1,952** | +2,048 |
| child-frame M2, arena | 1,579,500 | 1,592,540 | **+13,040** | +14,336 |

Every ceiling holds, and the base-byte one held only after the comments were
cut twice: the first draft was +920 and the second +445. **The ceiling counts
source bytes, and comments cost source bytes while costing nothing per realm**
— the per-construct table measured a comment at 0. That is worth recording for
round D rather than acted on here: the cap was frozen and the code was made to
fit it, not the other way round.

The two ruled re-freezes landed exactly as predicted. `window` moved from
`112:156a0f8b` to `113:5899bf6e`, one property more, and **`Element.prototype`
did not move at all** — `40:26312e4` before and after, on both allocator arms,
which is the check that round C added no prototype member.

**A third pin was not named in the ruling and is left failing rather than
amended.** `registry-brand-court.py`'s N3 — *"the shims are untouched by this
work"* — pins the SHA-256 of both shims from that round, and round C is the
first slice since to change `dom_shim_base.js`. It reads **14/15**. It is the
same class as the two authorised re-freezes: a pin whose purpose was to prove
*that* slice was host-side only, now correctly reporting that a *later* slice
was not. The recommended amendment is the same shape as the others — move the
base-shim hash to `3561e774…`, keep the old one beside it with the date and the
reason, and leave the main-shim hash at `d319246e…` untouched, since round C
does not touch the main shim. **It is not applied here**, because the ruling
authorised two re-freezes and naming a third is the coordinator's call, not
this round's.

## 5. Safe failures

- A tag missing from the store reads `""`, which is no tag the classifier
  knows: the element gets no role, no activation and no download. The failure
  direction is that the agent is offered less, never more.
- If `__mcsTag` is absent from a realm the host script throws, the act fails,
  and a failed act is a refusal — the same shape `__mcsJson` already has.
- A page that writes `tagName` changes only what the page itself reads. Nothing
  it can write reaches a host decision, and nothing it writes can make the host
  crash, because no property is removed and no setter is introduced.

## 6. Non-goals

No D. No new capture. No key added to the internals handle — `__mcsTag` is a
separate global, and `property-shape`'s thirteen-key handle check must stay
green. No bound moved, no protocol change, no refusal string changed, no Rust
guard touched, nothing slimmed, no visual run, no soak, nothing downloaded. F1
and F2 stay open and recorded.
