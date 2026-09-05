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
was in what was removed, measured rather than argued. The ratio is about
**3.4 live bytes per source byte per realm**, which is the number to hold
future shim growth against.

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
