# G1 — adjudication and evidence index, 0.0.1

Adjudication only. **Nothing measured, nothing run, no threshold moved, no
court changed, no receipt written or refreshed, nothing downloaded, no visual
run and no soak.** This document reads evidence already on main and proposes a
gate status. It does not declare one.

**The proposal, up front: G1 moves from BLOCKED to INCOMPLETE. It is not a
PASS, and this document does not claim one.**

## 1. What G1 actually asks

From the gate table:

> **Is the route bounded and materially more memory-efficient?** Evidence:
> deterministic workloads report complete process-tree component, peak and
> post-close values against named same-machine baselines. Safe failure: reject
> or narrow the route; **attribution alone does not pass**.

Four requirements, and they are worth separating because they have different
states:

| # | requirement | state |
| --- | --- | --- |
| 1 | deterministic workloads | **met** |
| 2 | complete process-tree component, peak and post-close | **met** |
| 3 | against **named same-machine** baselines | **met**, for one cell |
| 4 | *materially* more efficient, not attribution alone | **met on the measured cell**, unproven in general |

## 2. Evidence index

Same machine, same day, macOS arm64. Provenance is identical across all three:
native `2d57ce864002406e…`, Lightpanda `840547bb…` at 0.4.0, Chrome
`392011a7…` at 152.0.7977.82.

| receipt | covers |
| --- | --- |
| `labs/court/evidence/macos-arm64-w1-lightpanda-0.4.0-vs-chrome-152.0.7977.82` | the two **baselines only** — that court has no native arm. Chrome median peak tree RSS 1,237,024,768 over 9 processes; Lightpanda 28,147,712 over 1; ratio 43.948 over 7 alternating repetitions |
| `…/macos-arm64-target-retention-native-dom-0.0.0-lightpanda-0.4.0-chrome-152.0.7977.82` | all three candidates, **system arm** (the control) |
| `…/macos-arm64-target-retention-arena-native-dom-0.0.0-lightpanda-0.4.0-chrome-152.0.7977.82` | all three candidates, **arena arm** — the surface D6 is judged on |
| `…/macos-arm64-target-retention-footprint-native-dom-0.0.0-lightpanda-0.4.0-servo-0.5.0-chrome-152.0.7977.75` | the older five-candidate footprint run that **does** carry a Servo column, on Chrome 152.0.7977.75 — a different day and a different Chrome build |
| `labs/native-dom/g1-campaign-0.0.1.md` | the campaign that delivered the route half and reported the comparison half **blocked**; superseded on the comparison half only |
| `labs/native-dom/evidence/native-dom-control-0.0.2-profile` | D6's live-footprint criterion, which is a profile-cabinet gate and **not** G1 |

Arena arm, median of 7, tree physical footprint:

| stage | native-dom | Lightpanda | Chrome |
| --- | ---: | ---: | ---: |
| empty | 1,950,032 | 8,389,160 | 288,759,416 |
| one target | 2,785,640 | 9,110,104 | 591,063,840 |
| eight concurrent | 6,799,912 | **one target only** | 850,185,728 |
| retained after all closes | 884,760 | 1,572,912 | 114,786,712 |

## 3. What changed, and why it is a status change rather than a pass

Before this round the comparison half had **never been run**: `g1-campaign`
says so and names the block — an authorized Lightpanda artifact. That
authorization was given, the digest verified, the two existing harnesses run
unmodified. **The block is gone.** A gate whose blocking condition has been
removed is no longer BLOCKED, whatever else is still missing.

That is the whole of the change. It is not evidence that the route passes.

## 4. What is still missing, named rather than waved at

These are why the proposal is INCOMPLETE and not PASS. Each is a real gap, and
none is closed by the receipts above.

1. **One platform.** Every receipt in the index is macOS arm64. The lab
   discipline is explicit that a result from one OS/ISA and workload is
   evidence only for that cell. Linux and Windows cells do not exist.
2. **One fixture.** One synthetic hermetic page. The plan's own earlier entry
   says G1 "still needs private/PSS measures, more fixtures and platforms".
3. **Neither private memory nor PSS.** The court's own limitation: summed RSS
   is neither, and can double-count shared pages. Physical footprint corrects
   part of this and is the measure used above, but it is not PSS.
4. **No like-for-like at eight targets.** Lightpanda reaches **one** concurrent
   target in 7 of 7 repetitions. The eight-target row is route-against-Chrome
   only; there is no Lightpanda number to compare, and the route's advantage at
   that count is therefore measured against one baseline, not two.
5. **Servo was not built** for these runs, by ruling. The Servo column exists
   only in an older receipt on a different day against a different Chrome
   build, so it is not part of this same-day comparison.

## 5. Why `status: "incomplete"` in the receipts is not the answer to this question

The three receipts each carry `status: "incomplete"`. That field is the
**court's** standing honesty label and is not a verdict on G1: it records that
concurrent capacity is reported per candidate rather than forced into a
like-for-like count, and that a candidate is absent. A court can be honest and
complete about what it measured while the gate above it stays open. The two
must not be conflated in either direction — the receipts' label neither proves
nor denies the gate.

## 6. The three candidate statuses

| status | would mean | fits? |
| --- | --- | --- |
| **BLOCKED** | work cannot proceed without something outside the repo | **No longer true.** The artifact was authorized and the runs are done |
| **INCOMPLETE** | the gate's evidence shape is satisfied for a measured cell; generalization is unmet | **Yes** — this is the proposal |
| **PASS** | the route is bounded and materially more efficient, established | **No.** One platform, one fixture, no PSS, and no eight-target baseline pair |

## 7. What would move INCOMPLETE to PASS

Falsifiable, so that a later round cannot declare victory by narrative:

1. A second platform cell (Linux x86-64 or arm64) with the same two baselines
   and the same harnesses.
2. More than one fixture, including one that is not synthetic.
3. A private-memory or PSS measure beside footprint, or an explicit ruling that
   footprint is the measure the claim is stated in and PSS is out of scope.
4. Either an eight-target comparison against a baseline that can reach eight,
   or an explicit ruling that Lightpanda's one-target limit is itself the
   finding and the route's multi-target claim stands against Chrome alone.

Item 4 is the one that needs a decision rather than a measurement, and it is
the cheapest of the four.

## 8. What this document does not do

It does not declare a status, move a threshold, change a court, write or
refresh a receipt, or claim G1 passed. The proposal in §6 is for a ruling.

---

## 9. Ruled — INCOMPLETE, and the one-target boundary is decided

Recorded chronologically; §§1–8 stand as written, including §6's table and
§7's four conditions, so the proposal and the decision can be read against
each other.

### 9.1 The status

**G1 is INCOMPLETE. It is not PASS.** The authorized Lightpanda artifact was
obtained and both existing harnesses ran, so the blocking condition is gone;
the coverage gaps in §4 — platform, fixture, PSS/private memory, and
cross-baseline multi-target — leave the gate unfinished.

The distinction in §5 is kept: a receipt's internal `status: "incomplete"` is
the court's label about what that run measured, and the G1 adjudication above
it is a separate judgement. Neither is read off the other.

### 9.2 Condition 4 is decided, not deferred

**Lightpanda's one concurrent target is accepted as that baseline's capability
boundary.** It is a property of the baseline, not a missing measurement, and
the consequences are fixed rather than left to a later reader:

- **No eight-target Lightpanda number may be manufactured** — not by
  extrapolation, not by running eight engines and summing them, not by any
  construction that would put a number in that cell. The cell is empty because
  the baseline cannot reach it.
- **The eight-target result is labelled `route-vs-Chrome`**, explicitly,
  wherever it appears.
- **Lightpanda participates in the units it can actually be measured in** —
  empty, one target, and retention — and its numbers there stand as a full
  comparison.

This moves §7's item 4 out of the open list: it is **a decided branch, not a
hidden gap**, and no future round should reopen it as though a missing number
were an outstanding task.

### 9.3 What still stands between INCOMPLETE and PASS

Three conditions, unchanged from §7, and **any one unmet forbids PASS**:

1. a second platform cell;
2. a fixture that is not synthetic;
3. a private-memory or PSS measure beside footprint, **or** an explicit ruling
   that footprint is the measure the claim is stated in.

Servo stays unbuilt, no visual run, no soak, and no threshold or court moves.
