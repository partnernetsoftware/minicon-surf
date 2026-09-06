# Integration and consistency: what the documents say against each other

Read-only. No product code, no court criterion, no bound, handle or base byte
changed, no visual run, no soak, nothing downloaded. Where a fix is
documentation and is unambiguous it is prepared in the same commit; where a fix
would touch a court it is **reported and not made**, because this round may not
change courts.

## 1. Four of the seven named documents do not exist

The audit was asked to reconcile `PRD.md`, its owning `prd` modules,
`plan/plan-0.0.x.md`, `evidence-registry.json`, `alignment-contract.json` and
`release-policy.json`.

**Only `plan/plan-0.0.x.md` exists in this repository.** There is no `PRD.md`,
no `prd/` directory, no `evidence-registry.json`, no `alignment-contract.json`
and no `release-policy.json` — and none of them has ever existed here:
`git log --all --diff-filter=D` finds no deletion of any such path across 419
commits, and `git ls-files` matches none of the names.

The tracked root is `AGENTS.md`, `README.md`, `LICENSE-*`, `labs/`, `plan/`,
`protocol/`. So the governance layer those four names describe lives somewhere
else, or does not exist yet. **Everything below audits what is actually here**,
and the four missing documents are reported as the first finding rather than
invented.

What plays their parts today:

| the role | what actually holds it |
| --- | --- |
| product definition and outcomes | `plan/plan-0.0.x.md` §1, and `AGENTS.md`'s "Non-negotiable product outcomes" |
| the rules a round must follow | `AGENTS.md` |
| the evidence register | `labs/native-dom/README.md`'s 34-row binary ledger, plus `labs/native-dom/evidence/` (107 receipts) |
| the alignment/consistency record | the per-round entries in `plan/plan-0.0.x.md` |
| release policy | nothing found |

## 2. The evidence ledger stops thirteen binaries ago

`labs/native-dom/README.md` carries the document that owns *which binary
carries which evidence*: a 34-row ledger, newest row `8ff70b9f26c1…`.

*(Correction, recorded rather than silently fixed: this section first named the
newest row as `420cdf5b82bf…`. That is the row **below** it; the newest is
labelled `**current, …**` and my pattern skipped it. The gap it reports is
unchanged — thirteen binaries, none of them in the register — but the hash was
wrong and the maintenance round that repaired the ledger is where it was
caught.)*

Since that row was written, **thirteen commits have changed
`labs/native-dom/src/`**, and opening the receipts each of them committed shows
**thirteen distinct binaries** — though not one per commit: two rounds produced
no receipt at all, and one commit produced two binaries because it also rebuilt
the defect it removed. The commits:

```
b00dd3a  Stress the download transport          7cbf052  Fork profiles with copy-on-write
6374d62  Serve downloads over the control       092b50e  Refuse a fork whose source cannot be re-read
4b7d42c  Stop routing host answers through …    210f740  Freeze the capture-declaration guard
a229c13  Ask the realm probe a question …       ed9169e  Make the revision registry the host's
579820e  Refuse a snapshot answer the host …    7b9e11a  Make the approval bind …
a26aea3  Make an element's tag the host's       a0482ed  Make the attributes … the host's
5535eaa  Never lose an answer to half a character
```

Grepping that README for `signature-integrity`, `element-tag`,
`attribute-fact`, `text-answer`, `uncaptured-intrinsic`, `registry-brand`,
`snapshot-schema`, `probe-truthfulness`, `__mcsTag` or `__mcsAttr` returns
**zero**. The whole security-and-answer line — every court frozen, every
implementation, every re-freeze — is absent from the register that is supposed
to name it.

**This is the largest gap this audit found, and it is archive work rather than
a contradiction.** The plan carries all of it, round by round, with receipts and
scores; the register does not. The fix is not attempted here: those rows are
long, exactly formatted, and each names a binary, the receipts rerun on it and
the ruling it carries. Improvising thirteen of them from memory is how a
register acquires numbers nobody measured. **What it needs is a decision on
shape** — thirteen rows, or one consolidated row for the line with its five
binaries and their receipts — and then a round that writes it with the receipts
open.

## 3. Two courts state a status that is no longer true

Both still pass. Both are wrong on the page, and one of them is wrong in every
receipt it writes.

| where | what it says | what is true |
| --- | --- | --- |
| `element-tag-court.py:419-420`, the `not_under_test` field **written into every receipt** | *"F1 methodOf: a declared POST submitted as a GET — still open after this round"*, and the same for F2 | F1 and F2 were closed at `a0482ed` by `attribute-fact-court.py` (154/154) |
| `signature-integrity-court.py:15-16`, the docstring | *"F1 `methodOf`, F2 `targetOf` and F5 the download probe's node kind: they are separate candidates, **they are still open**"* | all three are closed — F5 at `958f5c0`, F1 and F2 at `a0482ed` |

Each sentence was true when it was written, and "after this round" is a
defensible reading of the first. But a receipt is read without its round, and
`element-tag-court.py`'s text is emitted into fresh JSON every time the court
runs. **Neither is edited here**, because this round may not change courts. The
exact edits, for approval:

- `element-tag-court.py`, the two `not_under_test` entries: replace *"still
  open after this round"* with *"closed later, at `a0482ed`; still not tested
  here"*.
- `signature-integrity-court.py`, the docstring sentence: replace *"they are
  still open"* with *"they were separate candidates and were closed later, at
  `958f5c0` and `a0482ed`; nothing below tests them"*.

Neither touches a criterion, a fixture, a score or a receipt already written.

## 4. A corrected sentence, still asserted in two places

`uncaptured-intrinsic-audit-0.0.1.md` §4 corrected H1's claim that *"the host
re-checks the node kind itself"*: at the time, `main.rs` refused `not_a_link`
only when the **realm** declined to supply an href, and the realm decided that
with `el.tagName.toLowerCase()`. The claim survives, unqualified, in two places:

- `intrinsic-hardening-audit-0.0.1.md:87`, as a Mermaid edge label
  (*"resisted: the host re-checks the node kind"*);
- `host-answer-court.py:12`, in the court's preamble.

There is a twist that has to be recorded precisely rather than glossed:
**the sentence is true again today, for a different reason.** Round C
(`958f5c0`) made `download_probe_script` ask `__mcsTag(el)`, a store the page
cannot write, so the host really does re-check the kind itself now. It was false
when H1 wrote it, was proven false in the F5 audit, and became true when F5 was
closed.

The design record is amended in this commit with a dated pointer at the diagram.
`host-answer-court.py` is a court and is **reported, not edited**; the exact
edit is to add, after that sentence, *"— false when this was written, proven so
in `uncaptured-intrinsic-audit-0.0.1.md` §4, and true again since `958f5c0`
made the tag the host's."*

## 5. The receipt convention had no owning document

The convention ruled on 2026-09-06 — a committed receipt is the history of the
round that produced it and is never refreshed; a re-frozen live guard reports
its current status in a **separate verification receipt** naming the current
binary — appears in **no tracked file**. `AGENTS.md` has "Lab discipline" and
"Change hygiene" and says nothing about receipt historicity, and no court or
audit states it.

`AGENTS.md` is amended in this commit, which is the documentation touch the
ruling said to wait for. Two consequences follow and are recorded with it:

- **No verification receipt exists yet.** Every current-status run in this line
  went to a scratch directory and was reported in a commit message. That was
  correct under "historical receipts untouched" and incomplete under "use a
  separate verification receipt": the convention's second half has never been
  exercised.
- The live guards it applies to are named so the next round knows them:
  `element-tag-court.py`'s rebased cost equalities,
  `signature-integrity-court.py`'s base-byte pin, `registry-brand-court.py`'s
  N3 shim hashes, and `property-shape-court.py`'s fingerprints.

## 6. Receipt and court naming does not pair mechanically

Seven courts have no receipt at the name a tool would derive from theirs, and
every one is a naming difference rather than a missing receipt: `child-frame` →
`-child-frames`, `timer` → `-timers`, `host-answer` → `-host-answers`,
`capture-declaration` → `-capture-declarations`, `frame-action` →
`-frame-actions`, `class-name-query` → `-class-name-query-hold`, and
`retention-court.py`, which is a shared library rather than a court.

Two of those are worth a decision rather than a note. `-class-name-query-hold`
is named for what it is — a court that reads **5 of 36 by design** because the
capability stopped at its cost gate — and that is good naming. And
`text-answer-court.json`, written in the last round, is the **only** receipt in
107 that keeps the `-court` suffix; every other drops it. It is distinct from
`-text-answer.json`, the probe's receipt, so nothing collides, but the pattern
is now broken in one place. Renaming a committed receipt would rewrite history,
so the convention of §5 says leave it; it is recorded here so the next receipt
does not copy it.

## 7. One orphaned receipt

Of 107 receipts, exactly one is referenced by no tracked document:
`native-dom-control-0.0.2-job-deadline-falsification.json`. Its court and its
passing receipt are both referenced; only the falsification arm is unnamed. A
first pass flagged four more, all of them mine from this session — the plan
names them with brace notation (`…-element-tag{,-falsification}.json`), which a
literal search misses. **They are referenced; the checker was wrong**, and that
is recorded here because a consistency audit that invents four orphans is worse
than one that finds none.

## 8. Open items, reconciled

No decision is changed. Status is what the owning document says today.

| item | owning document | status |
| --- | --- | --- |
| **G1** memory court vs a named baseline | `g1-locator-audit-0.0.1.md`, `g1-campaign-0.0.1.md` | **BLOCKED**: both baselines absent; needs one Lightpanda artefact (digest `840547bb…` already pinned) and 28 Servo crates, two separate authorisations |
| **G3** native surface | `surface-design-0.0.1.md` and the surface courts | **BLOCKED**: needs the visual double opt-in, which the standing rule reserves to the owner |
| **P6** history persistence | `history-persistence-audit-0.0.1.md` §11 | **DECIDED — deferred** by ruling |
| **G6** both outcomes green on one route | plan §1 | **OPEN**: follows G1 and G2/A3 |
| **D6** process-RSS attribution | `first-realm-engine-audit-0.0.1.md`; `-profile` reads 90/94 | **OPEN**, four narrow checks |
| **H2**, the capturable sites | `intrinsic-hardening-h2-audit-0.0.1.md` | **OPEN, and its headline number is stale**: 85 of 156 there; `uncaptured-intrinsic-audit-0.0.1.md` §1 measured **121 uncaptured today** under a wider method list, and explains the reconciliation. The plan carries both figures in different entries with no pointer between them |
| the **71 uncapturable sites** | same | **superseded** by the 121 count; a pointer is added to the plan in this commit |
| `arrayIndexOf`, the dead capture | `dead-capture-audit-0.0.1.md` | **DECIDED — permanent HOLD**, kept at zero uses, guarded by `capture-declaration-court.py` |
| **C1** batched `defineProperties` | `shim-reduction-audit-0.0.1.md` §9 | **DECIDED — withdrawn** after the extrapolation error |
| the HTTP response cache | `cache-audit-0.0.1.md` §10 | **DECIDED — the absence is the design** |
| `getElementsByClassName` | `class-name-query-court.py`, receipt `-class-name-query-hold` | **DECIDED — held at its cost gate**; its 36-criterion court reads 5/36 by design and is the gate for whenever 1,408 bytes are reclaimed |
| `AbortSignal.any()` | `abort-signal-surface-audit-0.0.1.md` §§135, 149, 175 | **DECIDED — out of scope, never triaged**, needs its own design. **It appears nowhere in `plan/plan-0.0.x.md`**, so a reader of the plan alone would not know it exists; a line is added in this commit |
| the option label/value preview gap | `option-value-audit-0.0.1.md` §15 and the comment at `form-court.py:405` | **DECIDED — deliberate**, recorded twice |
| node names from `textContent` | `text-answer-audit-0.0.1.md` | **DECIDED — page-controlled content that reaches nothing**; the one defect inside it is closed |
| F1, F2, F3, F4, F5 and the court probe | the five design records | **CLOSED**, each with a frozen court and a falsification receipt |

## 9. What is consistent

Reported because an audit that lists only faults is not a measurement.

- **Every path a document names resolves.** 102 distinct
  `labs/…`, `protocol/…` and `evidence/…` references in `plan/plan-0.0.x.md`,
  and **none missing**.
- **No untracked or unpushed artefact is implied.** The worktree is clean,
  `HEAD == origin/main == 6aa48df`, and every receipt, court and audit named in
  the last eight rounds is tracked and pushed.
- **Every settled ruling in this line has one owning document**, a court or an
  explicit statement that it needs none, a named receipt identity and a plan
  entry: downloads, copy-on-write, H1, H3, the realm probe, the registry brand,
  the strict parse, the cache, G1's locator, the uncaptured audit, signature
  integrity, the tag, the attributes, the text cut and the option value.
- **The falsification convention is followed everywhere**: every frozen court in
  this line has a pre-change receipt with `passed: false` and a post-change
  receipt with `passed: true`, each naming its own binary.
- **The deliberately failing court is labelled as such** in both its receipt
  name and the ledger row that introduced it.

## 10. What this commit changes, and what it leaves

Changed, all documentation:

1. `AGENTS.md` — the receipt convention of §5, in "Lab discipline".
2. `plan/plan-0.0.x.md` — this audit's entry, the `AbortSignal.any` line, and
   the 71 → 121 pointer.
3. `intrinsic-hardening-audit-0.0.1.md` — a dated pointer at the corrected
   sentence of §4.
4. `element-tag-design-0.0.1.md` — a dated pointer recording that F1 and F2,
   open when that round was written, closed at `a0482ed`.

Left, and reported for a ruling:

1. The thirteen missing ledger rows in `labs/native-dom/README.md` (§2) — needs
   a decision on shape before anyone writes them.
2. The two stale court strings (§3) and the court half of the corrected
   sentence (§4) — exact edits given; this round may not change courts.
3. The orphaned `job-deadline-falsification` receipt (§7) — either name it in
   its audit or accept it as an unreferenced historical arm.
4. Whether `PRD.md`, `evidence-registry.json`, `alignment-contract.json` and
   `release-policy.json` should exist here at all (§1). If they should, that is
   a round of its own, and this audit is the inventory it would start from.
