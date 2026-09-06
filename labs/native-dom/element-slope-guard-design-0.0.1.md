# A per-element slope guard — design, 0.0.1

Design-only. **No court file exists yet and none is frozen by this document.**
Nothing implemented, no product code, no criterion, cap, floor or protocol
touched, no navigation soak, no visual or surface run, nothing downloaded. This
is the pre-registration the ruling of `382657e` asked for: the baseline, the
sample plan, the thresholds and the tolerance, all chosen and written down
before the court exists, and reported for approval before it is frozen.

`element-scaling-audit-0.0.1.md` found that every guard this lab owns reads a
fixture small enough that the per-element term is invisible, and that the
element-fact rounds spent +232.2 bytes per element while every ceiling they
were measured against passed. This guard is aimed at exactly that term and at
nothing else.

## 1. What it guards, and what it deliberately does not

**Guards**: the two numbers that together describe what a document costs a
realm — the **slope** in tracked bytes per element, and the **intercept**, the
tracked cost of a realm holding an empty document. Both, always, in the same
run. The ruling's requirement is the reason: a refactor that moves cost from
one into the other must not be able to pass by quoting whichever moved down.

**Does not guard, and must not be read as guarding**:

- **M1 and M2 stay exactly as they are.** They bound fixed live owner bytes per
  child realm and they are correct for what they say. This guard is additional
  and separate; neither number is derived from the other and neither may be
  traded against the other.
- **Not RSS, not physical footprint.** It reads tracked
  `script_realms.malloc_bytes` only. A saving that lowers this number without
  lowering RSS is still a saving *in this metric*, and the court says so rather
  than implying more.
- **Not a G1 or D6 criterion.** Per the ruling, the slope is a standing metric
  beside the marginal-target figure, not folded into either gate.

## 2. The baseline, pinned to the shipped build

Measured on `2d57ce864002406e`, the shipped host at `382657e`. These are the
numbers the thresholds below are set against, and they are the numbers a future
amendment would have to move openly.

| | system | arena |
| --- | ---: | ---: |
| slope, bytes per element | **1,329.5872** | **1,279.3224** |
| intercept: tracked bytes, empty document | **329,360** | **319,696** |
| bare element, no attributes | 864.7 | 843.5 |
| each attribute | 229.8 | 200.4 |
| derived: elements before the 16 MiB realm limit | 12,370 | 12,864 |

**The intercept is the measured empty-document figure, not the fitted one.**
The least-squares intercept is 325,673.6 on the system arm, which extrapolates
below the smallest document that can exist; the measured value is an
observation and is what gets pinned.

## 3. The sample plan, fixed in advance

- **Slope family**: documents of **0, 500, 1,000, 2,000 and 4,000** elements,
  each element a `div` carrying two attributes and wrapping a `span`. Five
  points, because a slope cannot be separated from an intercept by fewer than
  two and is not falsifiable with three.
- **Decomposition family**: **2,000** elements carrying **0, 1, 3 and 6**
  attributes, which separates the flat per-element charge from the
  per-attribute one.
- **One host and one target per point**, a fresh process each time, so no
  allocator history carries from one point into the next.
- **Fixtures are generated into a temporary directory by the court itself**, so
  there is no committed fixture to drift and no fixture shared with another
  court that could be edited for another reason.
- **Both allocator arms, independently.** Passing one arm is not passing.
- **Two full independent runs per arm.**

## 4. Tolerance: why it is zero, and where the headroom actually is

The metric is **exactly deterministic on this machine and toolchain**. Two
independent runs of `element-scaling-probe.py` against the shipped binary
produced **all 18 tracked figures identical, to the byte**, and a third
measurement through a separately written probe produced the same values again.

So the guard carries **no noise tolerance at all**. Every byte of headroom in
§5 is deliberate policy headroom, and saying so is the point: a court whose
margin is really unadmitted measurement noise cannot be reasoned about later.

The determinism itself is a criterion. If two runs of one arm disagree by a
single byte, the court fails as **nondeterministic** rather than reporting a
number — because the whole guard assumes an exact metric, and if that
assumption is false the thresholds mean nothing.

## 5. The proposed thresholds — the numbers for approval

Chosen against §2, before any court exists, with the reasoning written down so
a later reader can judge the choice rather than infer it.

| id | criterion | system | arena | headroom | why this number |
| --- | --- | ---: | ---: | ---: | --- |
| **S1** | slope ceiling, bytes per element | **1,400.0** | **1,350.0** | +5.3% / +5.5% | Round C cost +120.1 (+11.0%) and round D +112.0 (+9.2%). The ceiling is **less than half of either**, so any future round of comparable size fails it and needs a ruling — which is the whole purpose. It is not set at the measured value because a court that fails on a one-byte change is a court people route around |
| **S2** | intercept ceiling, tracked bytes | **336,000** | **326,000** | +2.0% | Round D's fixed step was +2,560 (+0.78%). This allows roughly two more rounds of that size before a ruling, and the primary fixed-cost guards remain M1/M2, so this one is a cross-check against the trade rather than the main bound |
| **S3** | slope and intercept are reported in the same run, or the court fails | — | — | — | **This is the anti-trade criterion the ruling required.** A run that produces one without the other is not a partial pass |
| **S4a** | bare-element ceiling | **920.0** | **900.0** | +6.4% / +6.7% | Round C and round D both spent here and nowhere else; the ceiling is again below one round's step |
| **S4b** | per-attribute ceiling | **240.0** | **215.0** | +4.4% / +7.3% | This figure has **not moved across three builds** (229.8 on all three, system, to the byte). A ceiling close to it makes any movement at all a finding |
| **S5** | linearity, R² over the five slope points | **≥ 0.9999** | ≥ 0.9999 | measured 0.99999782 / 0.99999958 | If the cost stops being linear in element count, the slope is not a meaningful summary and the court must say so instead of quoting one |
| **S6** | determinism: two runs per arm, every tracked figure identical | exact | exact | none | §4 |
| **S7** | **falsification**: the same court, run with S1 set to **1,150.0**, must **fail** on the shipped binary | — | — | — | Anti-vacuity. Without it, a ceiling of 1,400 against a measured 1,329.6 could be a criterion that cannot fail. It also states the guard's counterfactual precisely: **had this court existed with a 1,150 ceiling before round C, round C would have failed it** |
| **S8** | both arms independently | — | — | — | The arms differ by 50 bytes per element and an arm-blind pass would hide a regression on one of them |

**Reported, not scored**: the derived realm ceiling in elements (12,370 /
12,864), because it is a function of S1 and S2 and scoring it twice would let
one number fail two criteria.

## 6. Owners, invariants, safe failure

- **Owner**: the `Element` constructor in `dom_shim_base.js`, which builds one
  `WeakMap` entry and one `{tag, a}` record per element.
- **Invariant**: a change to how an element's facts are stored may not raise
  what an element costs a realm past S1, or what an empty realm costs past S2,
  without a recorded ruling that moves the number openly.
- **Safe failure**: the court fails and the round stops for a ruling. It never
  edits a threshold to pass, and a threshold moves only by an amendment that
  keeps the old value beside the new one, chronologically, as every other moved
  pin in this lab does.
- **Non-goals**: bounding RSS; replacing M1/M2; constraining what a page may
  contain; making the slope smaller. This guard does not ask anyone to optimise
  anything — it asks that the number be stated.

## 7. Where the receipt goes

This is a **live guard**: its criteria are pinned to what the tree currently
costs, so by the convention in `AGENTS.md` its status belongs in a
**verification receipt naming the current binary**, and the historical
`-element-scaling` receipts committed at `382657e` are never refreshed. The
first run would be written to `-element-slope-verification`, and the audit's
three measurement receipts stay exactly as they are.

## 8. What is not decided here

- **The court file is not written and nothing is frozen.** Per the ruling, the
  numbers in §5 are reported for approval first. The asymmetry is deliberate: a
  frozen threshold that later has to move is a movement in the record, whereas
  waiting one cycle costs nothing.
- The `__attrs` direct-write and revision question from
  `element-scaling-audit-0.0.1.md` §4 is **out of scope here**, as the ruling
  directed, and stays a separately scoped audit item.
- Whether the slope should ever be *reduced* is not proposed. This guard only
  stops it from moving unremarked.

## 9. Amendment: approved and frozen

Recorded after the fact; §8 above is kept as it stood so the movement is
visible. The ruling that followed this design **approved S1–S8 exactly as
proposed**, with zero measurement-noise tolerance and the policy headroom as
documented, and authorised freezing the court. `element-slope-court.py` is that
court, and its first run on the shipped `2d57ce864002406e` is **17 of 17** in
`evidence/native-dom-control-0.0.2-element-slope-verification.json`. No
threshold moved between §5 and the court; every number in the court file is the
number approved.

**One criterion was strengthened in implementation, and it is recorded here
rather than passed over.** S3 was first written as a type-and-sign test on the
two numbers — and that form *could not fail*, which is exactly the defect S7
exists to prevent for S1. It is now **structural**: it fails unless both an S1
and an S2 check are present for the arm and re-deriving both from that arm's
stored figures reproduces the two values that were scored. The approved wording
is unchanged — joint slope-and-intercept reporting — this is that criterion made
falsifiable rather than a different one.

**It was proved to fail**, not merely reasoned about. A mutant copy of the court
with the S2 scoring removed was run against the same binary and scored **13 of
15, with S3 failing on both arms** (`intercept_checks: 0`). The mutant was
deleted and is not committed; the run is what the claim rests on.

Measured at the freeze, both arms, all reproducing the audit exactly:

| | system | arena |
| --- | ---: | ---: |
| slope | 1,329.5872 | 1,279.3224 |
| intercept | 329,360 | 319,696 |
| bare element | 864.7 | 843.5 |
| each attribute | 229.8 | 200.4 |
| R² | 0.99999782 | 0.99999958 |
| derived element ceiling, reported not scored | 12,370 | 12,864 |
