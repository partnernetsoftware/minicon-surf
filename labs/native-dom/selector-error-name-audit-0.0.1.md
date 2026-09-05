# Selector engine error names — read-only audit, 0.0.1

Design-only. Nothing here is implemented, no court is frozen, the handle does
not widen, and no cap or floor is proposed. Two throwaway builds were made to
price the options and were discarded with their worktree; the repository
carries neither.

## 1. The question

The base's selector engine refuses what it does not support. A page that
catches that refusal cannot tell what happened, because the refusal is a plain
`Error` whose *message* begins with the word `SyntaxError`. A page that does
the standard thing — `catch (e) { if (e.name === "SyntaxError") … }` — reads
`"Error"` and takes the wrong branch. This audit measures the shape the page
actually sees, what depends on it, what the standard shape would cost, and
what could go wrong on the way.

## 2. What a page sees today

Measured on `4a5836f43b38…` (the current build), through a page fixture that
catches each throw and reports `name`, `constructor.name`,
`Object.prototype.toString`, `instanceof Error`, `code`, `message`:

| call | `name` | constructor | tag | `code` | message |
| --- | --- | --- | --- | --- | --- |
| `querySelector("div:hover")` | `Error` | `Error` | `[object Error]` | `undefined` | `SyntaxError: selector not supported…: div:hover` |
| `querySelector("a,b")` | `Error` | `Error` | `[object Error]` | `undefined` | same shape |
| `querySelector("")` | `Error` | `Error` | `[object Error]` | `undefined` | same shape |
| `querySelector("#")` | `Error` | `Error` | `[object Error]` | `undefined` | same shape |
| `querySelectorAll("div:hover")` | `Error` | `Error` | `[object Error]` | `undefined` | same shape |
| `closest("div:hover")` | `Error` | `Error` | `[object Error]` | `undefined` | same shape |
| `matches("div:hover")` | `Error` | `Error` | `[object Error]` | `undefined` | same shape |
| `body.removeChild(orphan)` | `Error` | `Error` | `[object Error]` | `undefined` | `NotFoundError` |
| `classList.add("")` | **`SyntaxError`** | `Error` | `[object Error]` | `undefined` | `the token is empty` |
| `classList.add("a b")` | **`InvalidCharacterError`** | `Error` | `[object Error]` | `undefined` | `the token has whitespace` |

A browser throws a `DOMException` named `SyntaxError` with legacy `code` 12
for the first seven rows, and `NotFoundError` with code 8 for the eighth.

**Three conventions live in one host.** `classList`, the re-entrant dispatch
guard (`InvalidStateError`) and `localStorage` (`QuotaExceededError`) set
`e.name` and read correctly. The selector engine and `removeChild` put the
name in the *message* and leave `e.name` as `"Error"`. `setTimeout`,
`cloneNode` and `fetch` throw real `TypeError`/`RangeError`, which is what the
standard asks for and is not part of this question. The inconsistency is not
theoretical: the same page, catching two refusals from the same host, needs
two different reading strategies, and only one of them is the standard one.

The selector cache is not poisoned by a refusal — a rejected selector is never
cached, and the second call throws identically, measured.

## 3. `DOMException` already exists here

The measured surprise, and the reason this slice is cheap: the engine ships a
real `DOMException`. It was never authored in the shim and never mentioned in
the host.

- `typeof DOMException` is `"function"` in a page realm.
- `new DOMException("m", "SyntaxError")` → `name` `SyntaxError`, `code` **12**,
  `instanceof Error` **true**, `Object.prototype.toString` `[object DOMException]`,
  and it carries a `stack`.
- `new DOMException("m", "NotFoundError")` → `code` **8**. The legacy code
  table is the engine's, not ours to maintain.
- `name` is **read-only** on an instance: assigning to it throws.
- `new DOMException()` defaults to name `"Error"`, code `0` — a call site that
  forgets the second argument fails silently into the shape we already have.
- `e instanceof SyntaxError` is **false**, in this host today and under either
  option. That matches browsers, where the selector refusal is a
  `DOMException` and not an ECMAScript `SyntaxError`; it is worth writing down
  because it looks like a regression and is not one.

## 4. What depends on the error's shape

Read out of the host rather than assumed:

- **The host never reads `.name`.** Not once, in any `.rs` file. The only
  thing it reads is the message: `exception.message()`, truncated to 256
  characters, into `details.engine_error`.
- **No selector ever crosses the boundary.** The string `selector` does not
  appear in the host at all: `target.act`, `target.wait` and the snapshot
  builders locate nodes without one. The selector engine is page-facing only,
  so changing what it throws cannot change any control answer.
- **One court reads a name**, `element-api-court.py:302`, and it reads the two
  `classList` names — which this audit does not propose to move.
- **The page is the only consumer**, and it is the one being served.

## 5. What it would cost

Two candidates were built and measured against the same-worktree baseline
(`fd49bafe…`, HEAD), each with `child-frame-court` on both allocators:

| | M1 (system) | Δ per child | M2 | source |
| --- | --- | --- | --- | --- |
| today, baseline | 224,458 | — | 1,569,708 | — |
| **A** — real `DOMException` | **224,762** | **+304** | 1,571,836 (+2,128) | +100 bytes of base |
| **B** — `e.name` on a plain `Error` | 225,098 | +640 | 1,574,188 (+4,480) | +86 bytes of base |

Both stay far inside the frozen floors of 245,760 and 1,720,320 — **20,998
bytes of M1 headroom under A**, 20,662 under B — and neither proposes moving
anything. M2 is exactly seven times the M1 delta in both, so the cost is
per-child and has no super-linear term.

**The standard option is the cheaper one.** A is half the price of B because
`DOMException` is the engine's own constructor, while B allocates a helper
closure in every realm. On the candidate A build, `element-view` (23/23),
`element-api` (28/28), `form` (179/179), `frame-actions` (182/182) and
`page-navigation` (80/80) all still pass, so nothing in the existing evidence
depends on the current shape.

## 6. Risks, including one that is not about names at all

1. **The global is page-replaceable.** Measured: a page can assign
   `globalThis.DOMException`, and equally `globalThis.Error`. A throw site
   that names the global at throw time therefore throws whatever the page last
   put there. This is *already* true of every `new Error(...)` in the base;
   the candidate captures the constructor once at base load, the way the
   privileged path captures `Reflect.apply` and the rest. Any ruling here
   should say the constructor is captured, not merely referenced.
2. **A `DOMException` blinds the host's own diagnostic.** Measured: rquickjs's
   `as_exception()` returns `None` for a `DOMException`, so the host falls back
   to `"Exception generated by QuickJS"` and `details.engine_error` carries no
   message at all. Today the selector refusal reaches the host with its full
   text. This is a real loss on the host side for any script the *host*
   evaluates, and it is a forward dependency for the pending attribute-name
   validator: if that validator throws `DOMException`, a host-evaluated
   builder that trips it reports a contentless error. No host script calls
   `setAttribute` today, so nothing trips it yet.
3. **Scope.** `removeChild`'s `NotFoundError` has the same defect as the
   selector engine and is one line away; `classList` already reads correctly
   and would only be touched to give it a `code`. Widening or not widening is
   a ruling, not a technical constraint.

## 7. A finding that is not this slice, and should not wait for it

While measuring risk 2 I measured what the host records when a page script
throws uncaught, and it is worse than the selector question.

An uncaught page throw becomes `target_crashed` with
`details.engine_error` = the exception message, verbatim, 256 characters. The
message is page-authored. Measured, on the current build, with a fixture whose
script reads an input's value and builds a selector from it (the value is a
synthetic fixture string invented for this probe, not page data from any real
document):

```
{"code":"target_crashed",
 "details":{"engine_error":"SyntaxError: selector not supported by the native DOM slice: [value=\"hunter2-form-value\"]:secret",
            "script":"inline","script_index":0}, …}
```

The form value is in the control error. A second fixture confirms the shape is
general and not the selector engine's fault: `throw new Error("carrying " + v)`
puts `carrying hunter2-form-value` in `engine_error` just the same. The
standing rule is that form values, option values and built query strings never
appear in error details; **they appear there today, by construction, for any
page that throws.**

The selector engine aggravates it — it embeds page-composed text into a
message the page never chose to expose — but the fix belongs on the host side,
where `engine_error` is filled, and it must not be taken as a side effect of
this slice. Candidate A happens to blank the message and so happens to hide
this instance, which is exactly the wrong reason for the leak to stop. Recorded
here, unfixed, for a ruling of its own.

## 8. What is being asked for

No implementation, no court, no freeze. The rulings this audit needs:

1. **A or B or neither** for the selector engine: the standard `DOMException`
   at +304 bytes per child, the named plain `Error` at +640, or leave the shape
   as it is and write the loss down.
2. **Scope**: selector engine only, or `removeChild` with it, or `classList` a
   `code` as well.
3. **Capture**: whether the constructor is captured at base load (this audit
   recommends yes, and notes today's `new Error` is exposed the same way).
4. **The leak in §7**, which is a separate slice and, on the measured evidence,
   a more urgent one than the name.
