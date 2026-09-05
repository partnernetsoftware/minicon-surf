# The rest of `AbortSignal` — read-only triage, 0.0.1

Design-only. Nothing implemented, no court frozen, the handle does not widen,
no cap or floor proposed, no navigation or visual path run. Two throwaway
builds carried the candidates and went away with their worktree.

The branded signal shipped in `900b111` deliberately stopped at the minimum.
This triages what is left, one candidate at a time.

## 1. What a page finds today

Measured on the shipped `9975bb659414…`:

| probe | today |
| --- | --- |
| `signal.addEventListener` / `dispatchEvent` / `instanceof EventTarget` | `undefined` / `undefined` / **`false`** |
| `signal.reason` | `undefined`, before and after `abort()` |
| `controller.abort("because")` | **accepted, and the argument is silently dropped** |
| `signal.throwIfAborted` | `undefined` |
| `AbortSignal.abort` / `AbortSignal.timeout` | `undefined` / `undefined` |
| `signal.onabort = fn` | **the assignment sticks and the handler never fires** |
| `abort()` twice | idempotent |
| `signal.aborted = true` | `TypeError` — the getter has no setter |

Two of these fail **silently**, in the same shape as the `handleEvent` defect
this host fixed two rungs ago: `abort(reason)` accepts an argument it discards,
and `onabort` accepts a handler it never calls. A page cannot tell.

## 2. The candidates

- **R1 — the signal is an `EventTarget` and `abort` fires on it.** The class
  extends the `EventTarget` this host already has, and `abort()` dispatches an
  `Event("abort")` on the signal.
- **R2 — `reason`, and `abort(reason)`.** A getter reading a **main-side**
  `WeakMap`; the default is a `DOMException` named `AbortError`.
- **R3 — `throwIfAborted()`.** One method over R2.
- **R4 — `AbortSignal.abort(reason)`**, the static.
- **R5 — `onabort`.** An accessor that registers and replaces one listener,
  keeping the handler in a main-side `WeakMap`.
- **R6 — `AbortSignal.timeout(ms)`**, which needs a timer.

R1–R5 were built together as candidate **CB1**; **CB2** is CB1 plus R6.
Measured on CB1/CB2: the signal is an `EventTarget` (`true`), `abort` fires
once, `reason` is `AbortError: signal is aborted without reason` by default and
`because` when given, `throwIfAborted` and both statics exist, `onabort` fires,
and `AbortSignal.timeout(5)` returns a signal that is not yet aborted.

## 3. Where the authority question actually is

**Nothing here needs the base, the handle or the host.** `reason` and the
`onabort` handler live in `WeakMap`s inside the **main extension's own
closure**, which a page cannot reach — so none of it needs a new handle entry,
and the frozen `abort-signal` court's exact-key-set criterion **still passes on
both candidates**, along with `listener-options`, `capture-phase`,
`passive-listener` and `event-target`.

The one thing that changes shape is reentrancy, and it is worth stating
plainly. Today `controller.abort()` flips a `WeakSet` and runs no page code.
Under R1 it **dispatches**, so page code runs where it did not before.
Measured on CB1, with an element listener that aborts mid-dispatch:

```
first > abort listener > back > third
```

The signal's `abort` listener runs **synchronously inside the element's
dispatch**, between the calling listener's two statements. A nested dispatch
from inside that abort listener also works (`abort > nested ran`), and a
throwing abort listener is swallowed by the per-listener `catch`, leaving
`abort()` returning normally and the signal aborted.

That is not a new *authority*: a page can already dispatch inside a listener,
and this is the page's own `abort()` doing it. But it converts a formerly
inert host call into a page-visible dispatch, and that has a forward
consequence worth ruling on: **if the host ever aborts a signal itself** — a
navigation cancelling in-flight page work, say — R1 means page code would run
inside a host-initiated dispatch. The host aborts nothing today. Any future
slice that gives the host a reason to abort should re-read this paragraph.

## 4. Timers, realms and lifetime — the R6 question

`AbortSignal.timeout(ms)` is the only candidate that owns something. The
measured facts:

- The timer would be the main extension's `setTimeout`, so the signal's abort
  rides the host's existing job and deadline machinery rather than inventing
  one.
- A page can already queue at least 64 pending timers, measured, so the
  budget exists; `timeout()` spends from it.
- **A timeout signal nobody keeps still holds a timer** until it fires. There
  is no cancellation path in the standard's `timeout()`, so an abandoned
  signal is an abandoned timer, and a page that makes them in a loop spends
  the timer budget rather than leaking memory — the budget refuses at its
  limit, which is the existing bound doing its job.
- The realm question is the one to rule on: a timeout is meaningless once the
  target is closed, and the host already tears timers down with the realm.

## 5. Cost

Against the shipped binary, three runs each, stable to the byte:

| | M1 | M2 | main-only slack |
| --- | --- | --- | --- |
| shipped `9975bb659414…` | 232,298 | 1,624,588 | 54,560 |
| **CB1** (R1–R5) | **232,298** | 1,626,636 | **60,032** |
| **CB2** (+R6) | 234,346 | 1,626,636 | **61,504** |

**No base source changes in either candidate** — the two shim files were
diffed and only `dom_shim_main.js` moved — so per-child cost should be nil.
CB1 measures exactly that. CB2's M1 step of 2,048 is one allocator block, and
it is a quantization artefact of the subtraction rather than per-child code;
it is reported as measured rather than explained away, and it is the reason
the table is here instead of a claim of "free".

**The binding constraint is the main-only slack, not M1.** The bound is 65,536
and CB2 lands at 61,504, leaving **4,032 bytes**. R1–R5 alone leave 5,504.
This is the first slice in the batch where the slack, not the child floor, is
what runs out.

## 6. Loss matrix

| the page expects | R1–R5 | +R6 | notes |
| --- | --- | --- | --- |
| `signal` is an `EventTarget` | **served** | served | it extends the host's own class |
| `abort` event fires once | **served** | served | measured |
| `abort()` idempotent | served | served | unchanged |
| `reason`, default `AbortError` | **served** | served | main-side `WeakMap`, page-unreachable |
| `abort(reason)` respected | **served** | served | today the argument is dropped in silence |
| `throwIfAborted()` | **served** | served | throws the reason |
| `AbortSignal.abort(reason)` | **served** | served | |
| `onabort` fires | **served** | served | today it is set and never called |
| `AbortSignal.timeout(ms)` | not served | **served** | spends a timer from the page's budget |
| `signal.reason` writable by a page | never | never | a getter only |
| an abort listener's exception propagating | never | never | swallowed, as everywhere in this host |
| `AbortSignal.any()` | never here | never here | not triaged; it composes signals and would need its own design |

## 7. Pending rulings

1. **R1–R5 together, or in pieces.** They cost no per-child bytes and 5,472 of
   main-only slack. R2/R5 close two silent failures, which is the same reason
   the `handleEvent` rung was taken.
2. **R6 or not.** It is the only candidate that owns a timer, and it is what
   pushes the slack to 4,032 of its bound.
3. **The reentrancy note in §3** as a written position: `abort()` becomes a
   dispatch, and no host path may abort a page's signal until that is ruled
   again.
4. **The slack bound.** At 61,504 of 65,536, the next main-only slice needs a
   number before it starts, not after.
5. **`AbortSignal.any()`** is out of scope here and should stay a separate
   candidate if it is ever wanted.


## 8. Ruled

**R1 through R5 are accepted** — the signal is an `EventTarget` and `abort`
fires on it, `reason` exists and `abort(reason)` is respected,
`throwIfAborted()` throws it, `AbortSignal.abort(reason)` mints an
already-aborted signal, and `onabort` is a real handler. R2 and R5 are the
reason: they close the two failures a page cannot currently see.

**The reentrancy constraint is frozen with it.** `abort()` becoming a dispatch
is acceptable *because it is the page calling it*: a page can already dispatch
inside its own listener. **No host path may abort a page's signal.** If a
future slice wants one — a navigation cancelling in-flight page work, say —
that is a new ruling, not an extension of this one. An abort listener's
exception stays swallowed locally, as everywhere else in this host.

`reason`, the `onabort` handler and the brand stay in closure-owned `WeakMap`s
and `WeakSet`s. **The handle's exact key set does not grow**, and the frozen
criterion that pins it stays as it is.

**R6 `timeout()` is deferred**, recorded as its own candidate. It introduces a
held timer with budget and deadline semantics, and the main-only slack is down
to roughly 4,032 bytes; both deserve their own measurement rather than riding
in on this one. `AbortSignal.any()` was never in scope and stays out.

**The slack is now the gate.** Whatever comes next through the main extension
states its expected slack before it starts, because 65,536 minus what this
slice takes is what is left for everything after it.
