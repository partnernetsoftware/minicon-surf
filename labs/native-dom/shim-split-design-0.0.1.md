# Splitting the per-realm shim (native-dom, control 0.0.2)

Design only. Nothing here is implemented. Every number below was measured on
this machine with a **throwaway worktree and a scratch build that is not
committed and is not qualification** — it exists to answer one question,
whether the split can recover enough, before any product code is written.


## 1. The measured defect

`dom_shim.js` is **29,930 bytes** in one IIFE, and **every realm compiles all
of it** — the main document's realm and each of the eight child frames a
target may hold. Child frames run no page script, so most of what they
compile can never be reached from inside them.

The cost is no longer theoretical. The frozen child-frame caps stand at
262,144 (M1, one child) and 1,835,008 (M2, seven children). The current build
measures **261,962** and **1,831,452**: M1 has **182 bytes** of headroom. Two
consecutive slices spent it — page-initiated navigation cost about 6.1 KB per
child and the audit round another 608 bytes, neither of which a child can
use. The next slice that adds shared shim source breaks a frozen cap, so the
browser-API work the plan calls for is blocked behind this.


## 2. Inventory, by actual host call site

What the host evaluates in a **child** realm, in order, is exactly five
scripts plus the action scripts:

| evaluated on a child | source | needs |
| --- | --- | --- |
| `DOM_SHIM_JS` | the whole 29,930 bytes | — |
| `__mcsSeed(tree)` | generated | `build`, `Document`, `Element`, `Text`, `Node` |
| `__mcsLocation(parts, false)` | generated | the plain location object only |
| `__mcsComplete()` | generated | `document` |
| `INSTALL_JS` | fixed | `MutationObserver`, `document.documentElement`, `window.__mcs` |
| `snapshot_script` (+`SERIALIZE_JS`) | generated | tree, attributes, `__mcs` |
| `preflight_script`, `act_script` (+`ACTIVATION_JS`) | generated | tree, attributes, `new Event`, `dispatchEvent`, `__mcs` |

`__mcsPreflight`, `__mcsNavigation` and `__mcsFormNavigation` are defined
**inside those generated scripts**, not in the shim, so they are not resident
per realm and are outside this split.

Against that, the shim's blocks divide as follows.

| block | bytes | child needs it |
| --- | ---: | --- |
| prelude, mutation observers, `Event`, `MutationObserver` | 2,206 | yes — `INSTALL_JS`, act |
| `Node` | 3,098 | yes |
| listener model (`listenersOf`, `addListener`, `removeListener`, `dispatchOn`) | 2,363 | yes — `dispatchOn` is how an action's event is delivered |
| `Text`, `Element` | 6,492 | yes |
| `Document` | 825 | yes |
| selector engine | 3,011 | yes (kept; see §9) |
| `build` / `__mcsSeed` / `__mcsComplete` | 200 | yes |
| **fetch bridge** | 2,290 | **no** — only page script calls `fetch` |
| **cookies and `localStorage`** | 2,309 | **no** — `Document.prototype.cookie` and the store are page surface |
| **location accessors and the intent slot** | 2,685 | **no** — a child gets the plain object (§17.2 of the navigation record) |
| **window as an event target, `onload`, the lifecycle bridge** | 2,118 | **no** — the lifecycle is armed on the main realm alone |
| **`queueMicrotask`** | ~70 | **no** |
| **timers** | 1,953 | **no** — `setTimeout` is main-frame only by design |
| **`console`, `navigator`** | ~190 | **no** — nothing in a child can call them |

**11,239 bytes — 37.6% of the shim — is main-only page surface** that every
child compiles today.


## 3. Dependency graph

The main-only blocks are not independent: they reach into the base's closure.
A split has to carry exactly these edges and no others.

```
base (every realm)                     main extension (main realm only)
──────────────────                     ────────────────────────────────
document (the one Document instance) ◄── lifecycle steps, readyState,
Document (class, for its prototype)  ◄── Document.prototype.cookie
Node, Element, Text                  ◄── (none directly)
Event (class)                        ◄── lifecycle events, timers' errors
dispatchOn, addListener,             ◄── window EventTarget, onload,
  removeListener, listenersOf            DOMContentLoaded/load dispatch
g (globalThis)                       ◄── fetch, localStorage, timers,
                                          console, navigator, location
__mcsLocation (plain form)           ◄── overridden by the accessor form
```

Nothing flows the other way: no base code calls into the extension. That
matters — it is why a realm without the extension is complete rather than
half-built, and why the base can keep being the sole DOM authority.


## 4. The candidates, measured

**A — a physical split: `dom_shim_base.js` plus `dom_shim_main.js`.** The
base is the table above minus the main-only blocks. The main realm evaluates
the base and then the extension; a child evaluates the base alone. The
extension receives the base's internals through a **one-shot, non-enumerable
handle the base deletes as it hands it over**, the same shape already used
for `__mcsArmLifecycle`: no global mutable cache, no host mirror of DOM
state, one `document` per realm, and no way for page script to reach the
handle (page script runs strictly after both evaluations).

Measured with a scratch build in a throwaway worktree — children evaluate a
generated base of **19,034 bytes**, main unchanged:

| | current build | scratch split | change |
| --- | ---: | ---: | ---: |
| child-frame court | 82/82 | **82/82** | unchanged |
| M1, one child (system) | 261,962 | **223,994** | **−37,968** |
| M1 headroom under 262,144 | 182 | **38,150** | ×210 |
| M2, seven children (system) | 1,831,452 | **1,565,676** | **−265,776** |
| M2 headroom under 1,835,008 | 3,556 | **269,332** | ×76 |
| M1 / M2 (arena) | 253,994 / 1,776,412 | 216,906 / 1,517,804 | −37,088 / −258,608 |
| binary | 5,771,568 | 5,788,080 | +16,512 (see below) |
| incremental build | 4.79 s | 4.86 s | +0.07 s |

The court passing 82 of 82 is the load-bearing number: nothing a child needs
was in what was removed, measured rather than argued. The ratio here is about **3.4 live bytes per source byte per realm** for bulk
text. **That number is historical context and is not the budgeting rule**: the
base-reduction record measures the cost that actually dominates — a member of
a shared prototype costs **600 to 960 bytes of M1 per child**, ten times what
the same source weighs as text — and per-member is what future base growth is
priced in (`base-reduction-design-0.0.1.md` §8.3).

There are **two** measured price classes, and neither is a budget promise for
anything future — each is what one measurement said, recorded so a later
change is priced rather than guessed:

- **per prototype member**: 600 to 960 bytes of M1 per child;
- **per element**, for anything a constructor allocates: measured at **832
  bytes** for `dataset`'s `Proxy` and its closures, against a node that costs
  about 2,082 bytes in total (`gap-triage-0.0.2.md` §10.2).

The binary grew only because the scratch build embeds the base *in addition
to* the untouched monolith. A real split embeds base + extension, whose sum
is the monolith plus a few hundred bytes of handle glue, so binary size
should be approximately unchanged; the court measures it rather than assuming.

**B1 — one shared runtime, many contexts.** QuickJS can hold several
`JSContext`s in one `JSRuntime`, sharing atoms and shapes. This host creates
**one `Runtime` per realm** on purpose: `Runtime::new_with_alloc` is what
gives each realm its own zone or arena allocator, which is how
`memory.report` attributes bytes per realm at all, and it is what makes the
per-realm limit and the per-realm OOM boundary real. Sharing a runtime would
dissolve exactly the accounting these caps are written in, and one page's
allocation failure would become another's. **Rejected**, and note that it
would also be the candidate that "only shifts bytes out of accounting", which
the ruling names as an automatic failure.

**B2 — precompiled bytecode.** Compiling the shim once and loading bytecode
per realm is possible in principle, but: loading a serialized object in
rquickjs is an `unsafe` call over bytes whose provenance the type system does
not check — the ruling excludes unsafe bytecode persistence — and, decisively,
**it would not move M1 at all**. Bytecode still deserializes into per-runtime
objects; what it saves is parse time, and parse time is not the bound. The
measured incremental build and startup costs are not where the problem is.
**Rejected on the measurement, not on taste.**

**C — leave the monolith (baseline).** M1 keeps 182 bytes of headroom and the
next shared-shim change breaks a frozen cap. Every browser-API slice in the
plan adds shim source. **Rejected.**

**Recommendation: A.**


## 5. What the split must preserve

- **One DOM authority per realm.** The base owns `document`; the extension
  extends it in the same realm through the handle. There is no second tree,
  no host-side mirror of nodes or listeners, and no cross-realm sharing.
- **No global mutable cache.** The handle is one-shot and deleted on use, like
  `__mcsArmLifecycle`.
- **No child scripts.** The split changes what a child *compiles*, never what
  it may run; `script_count` and the frame-skip tallies are untouched.
- **Identical semantics on main.** The extension is the same source, moved.
  Every existing suite is the proof, and any divergence is a failure, not a
  finding.
- **The plain child location stays.** It is already the script-free form
  (navigation record §15); the split moves the accessor form out of the base
  rather than reintroducing it.


## 6. Pre-registered court: `shim-footprint-court.py`

Strictly headless, both allocators, hermetic loopback origin, supervised
hosts with the wall-clock kill, no surface and no AppKit. To be frozen in its
own commit before any product code, as every slice before it.

Criteria, all falsifiable against the current build:

1. **The caps do not move.** M1 ≤ 262,144 and M2 ≤ 1,835,008, unchanged
   numbers and unchanged fixtures.
2. **A recovered margin, not a shuffle.** M1 ≤ **245,760** (at least 16 KiB
   of headroom) and M2 ≤ **1,720,320** (at least seven times that). The
   scratch measurement is 223,994 and 1,565,676, so the gate is set well
   inside what was measured and still fails any candidate that recovers less
   than a quarter of it.
3. **Bytes leave the process, not the ledger.** In the same run, the M1 arm's
   `physical_footprint_bytes` delta must be at least **16,384 bytes** (one
   page) lower than the baseline build's on the same fixtures. A candidate
   that only moves bytes out of `owners` fails here.
4. **The main realm does not pay for it.** One main target, no children, live
   owner bytes no more than **65,536** above the baseline build measured the
   same way in the same run.
5. **Exact release.** Closing every target returns the owners to the empty
   figure exactly, as the child-frame court already requires.
6. **Nothing else changed.** On the same binary: child-frames 82/82,
   frame-actions 182/182, form 179/179, lifecycle 53/53, timers 68/68,
   job-deadline 42/42, page-navigation 80/80, frame-realm 62/62,
   cdp-frame-tree 64/64.
7. **A child still cannot run script.** A child document carrying a script
   is still skipped and tallied, and `script_count` for children stays zero.

Recorded in the receipt as attribution, not as pass/fail: base source bytes,
extension source bytes, monolith bytes, live owner bytes per realm class,
bytes of live cost per source byte, incremental build seconds, and binary
size — for both the baseline and the candidate build, so the trade is visible
in one place.


## 7. Blockers for the root

1. **The thresholds in §6.2 and §6.4.** 16 KiB of M1 headroom and 64 KiB of
   main slack are the ruling's own numbers; the measurement says both are
   comfortably achievable. Confirm and they freeze.
2. **The selector engine (3,011 bytes) stays in the base.** The scratch split
   kept it and the child court passed, so it is not proven either way. Moving
   it later is a separate measured question; I am not moving it on suspicion.
3. **`console` and `navigator` leave the base.** Nothing in a script-free
   realm can call them and the court proves the child suites still pass, but
   they are the only two globals a *human* debugging a child realm might
   expect to find. Say if they should stay.
4. **What the recovered margin is for.** It is 38 KiB per child. It should be
   spent on the browser-API slices the plan lists, not silently absorbed; a
   budget stated by the root would keep it from being spent by accident.


## 8. Honest limits of this estimate

The scratch build measures the **ceiling**: children compile the base, and
main still compiles the untouched monolith. The real split evaluates base +
extension on main, which costs one more evaluation and the handle glue there;
§6.4 exists precisely because that cost is not yet measured. The worktree and
its build are throwaway, nothing from them is committed, and none of these
numbers are qualification — they are the reason to write the code, not
evidence that it works.


## 9. The rulings, and two corrections the court found in itself

The root ruled: candidate **A**; the thresholds freeze at M1 ≤ 245,760,
M2 ≤ 1,720,320, a physical-footprint recovery of ≥ 16,384 and main-only slack
≤ 65,536; the selector engine stays in the base; `console` and `navigator`
move to the extension, because there is no arbitrary child-realm debug or
eval surface and keeping them would charge production for a hypothetical
observer; the recovered margin is a **standing floor, not a feature budget**;
main-only browser APIs live in the extension; and any future base growth needs
its own proof that child snapshot or action semantics require it, and must
re-pass the floor.

One criterion was added by ruling: **the one-shot internals handle must be
gone on both paths** — consumed before any main page script, and explicitly
destroyed for a base-only child before any later host-driven snapshot or
action evaluation. No latent global capability may remain reachable or
enumerable merely because a child happens to run no script today.

Writing the court then found two faults in the court itself, both recorded
before any product code.

**9.1 Two different measurements compared to one cap.** The court first
restated the frozen child-frame caps against its own arms. On the same
binary it reads about a kilobyte higher than the child-frame court does,
because it measures in a fresh host rather than after that court's earlier
groups. Asserting one court's numbers against the other's caps compares two
measurements. The caps stay where they are proven — in the child-frame court,
on its fixtures, in its order, which the ruling requires to be rerun anyway —
and this court owns the recovery. Its fixtures are the child-frame court's
byte for byte, so the two are on one scale, and its floors are therefore
about a kilobyte stricter than they look.

**9.2 A footprint criterion that could not fail.** The first version read one
child's `physical_footprint_bytes` growth. That figure moves in 16 KiB pages
and does not shrink when memory is freed, so a repeat cycle in a warmed host
measures the allocator's reuse rather than the cost, and — measured against
**the same binary twice** — it reported a 131,072-byte "recovery" and passed.
A criterion that passes when nothing changed proves nothing.

It now reads the first growth of **twenty-eight child realms** in a host that
has done nothing else, three fresh hosts per build, and requires **every
candidate host to sit below every baseline host** by the frozen 16,384. Two
runs of the same binary overlap and fail it, which is the check that it can
fail: measured that way, identical binaries report −311,296 (system) and
−49,152 (arena), while the split's accounted recovery over those realms is
about 1.06 MB.


## 10. What the court measures, as frozen

`shim-footprint-court.py`, headless, both allocators, two binaries in one
run — the candidate and the exact baseline it must beat.

1. M1 ≤ 245,760 and M2 ≤ 1,720,320, and both strictly below the baseline
   build's own numbers, so a floor cannot be met by a number that never moved.
2. Twenty-eight child realms cost at least 16,384 fewer bytes of process
   footprint, every candidate host below every baseline host.
3. A main-only page — page script, a timer, `localStorage`, a `location` read
   and a `load` listener — costs at most 65,536 bytes more than on the
   baseline.
4. Closing every target returns the owners to the empty figure exactly.
5. A script-bearing child is built exactly as the baseline builds it and still
   runs nothing: frames, `frames_skipped`, `scripts_skipped` and
   `script_count` all equal the baseline's.
6. The internals handle is gone from the main realm and from **every child
   realm**, present nowhere and enumerable nowhere, read through a court-only
   realm probe that is refused before the host serves anything without the
   private court file, and whose court file is gone when the host is.

The receipt attributes both builds' source bytes, binary size, build seconds,
owner bytes per arm and every footprint sample.


## 11. The split as built, and what the court caught in it

`dom_shim.js` is gone. In its place:

- **`dom_shim_base.js`, 19,936 bytes**, compiled by every realm: the tree,
  `Event`, `MutationObserver`, the listener model, the selector engine, the
  seed, the plain location object, and a one-shot non-enumerable
  `__mcsInternals` handle that deletes itself as it hands the base's internals
  over — `g`, `document`, `Document`, `Event`, `addListener`,
  `removeListener`, `dispatchOn`, and nothing else.
- **`dom_shim_main.js`, 11,702 bytes**, compiled only by a realm that runs
  page script: the fetch bridge, cookies and `localStorage`, the location
  accessors and the navigation-intent slot, the window as an event target
  with `onload` and the lifecycle bridge, `queueMicrotask`, timers, `console`
  and `navigator`. It is one call through the handle, in the same realm,
  extending the same `document`.

A child realm evaluates the base and is then **sealed by the host**: the
handle is deleted before anything else is evaluated in that realm, and the
host refuses the realm if it survives. Which location form a realm has is no
longer a parameter the host passes — it is which sources that realm compiled,
so `location_script` lost its `live` argument.

**11.1 The court caught the parameter that was left behind.** The first build
passed the child-frame court and every footprint gate and failed
page-navigation at 38 of 80: no intent committed at all. The moved location
block still carried the `live` parameter from the earlier narrowing, and the
host no longer passes it, so `undefined` took the plain branch and the main
realm got a location object with no setters. The split supersedes that
parameter; removing it is what the accessor form being *the extension's* form
means. Nothing about the previous slice's semantics changed — the court is
80 of 80 again.

**11.2 The first comparison used the wrong baseline.** The binary I passed as
the baseline was an earlier round's build, not `origin/main`'s. The numbers
were close but they were not the comparison the ruling asked for. The
baseline below is built from `origin/main` (`1bbdf00`) in a throwaway
worktree, hash `7f2429d96df8…`; note it is not byte-identical to the build of
the same commit made in the main tree, because the build directory is part of
what a Rust binary embeds. Every number here is from one court run over those
two binaries.

**11.3 Measured, candidate against the exact baseline, in one court run.**

| | baseline | split | change |
| --- | ---: | ---: | ---: |
| M1, one child (system) | 261,961 | **221,657** | −40,304 |
| M2, seven children (system) | 1,831,451 | **1,549,323** | −282,128 |
| M1 / M2 (arena) | 254,041 / 1,776,427 | 214,809 / 1,503,307 | −39,232 / −273,120 |
| main-only page (system) | 273,512 | 273,224 | **−288** |
| main-only page (arena) | 265,720 | 265,288 | **−432** |
| 28 child realms, footprint (system) | 9,895,936 min | 8,880,128 max | −1,015,808 |
| 28 child realms, footprint (arena) | 13,812,480 min | 11,223,808 max | −2,588,672 |
| shim source per child | 29,930 | 19,936 | −9,994 |
| shim source total | 29,930 | 31,638 | +1,708 |
| binary | 5,771,568 | 5,773,040 | +1,472 |
| incremental build | 5.46 s | 6.12 s | +0.66 s |

The main realm did not pay for the split at all: it costs a few hundred bytes
*less* on both allocators, inside the run-to-run variation, against a slack of
65,536 it never approached.

On the child-frame court's own fixtures and order, M1 is **221,658** against
the unmoved cap of 262,144 — **40,486 bytes of headroom**, where there were
182. The floor the ruling set is 16 KiB; what stands above it is a floor to
hold, not a budget: any future base growth owes its own proof that a child's
snapshot or action semantics require it, and must re-pass the floor.
