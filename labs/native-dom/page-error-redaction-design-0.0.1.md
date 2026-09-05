# Page-authored text in a host error — design, 0.0.1

Design-only when written; **ruled, frozen and implemented since**, and §8
records what was built and measured. The court described in §5 was frozen
unchanged one commit ahead of the fix. No `DOMException`
work, no attribute-name validator, no handle change, no cap or floor moved.

## 1. The defect, measured

An uncaught throw in a page's own top-level script becomes a control error
whose `details.engine_error` is the exception's message, verbatim, to 256
characters. The message is page-authored, so anything the page puts in it
leaves the host. On the current build `4a5836f43b38…`, with a fixture whose
script reads an input's value and throws a string built from it:

```
{"code":"target_crashed",
 "details":{"engine_error":"carrying <the input's value>","script":"inline","script_index":0},
 "message":"a script threw","retryable":false,"scope":{"id":"target_2","kind":"target"}}
```

The value is a synthetic token this lab invented; the point is the shape. The
standing rule is that form values, option values and built query strings never
appear in the audit ledger, the court-only log, error details, receipts or any
diagnostic. They appear in error details today, by construction, for any page
that throws.

## 2. Where it comes from

One site: `labs/native-dom/src/main.rs:2020`. The realm's `eval` catches, asks
the exception for its `message()`, truncates to 256 characters and puts it in
`details.engine_error`. Every operation that evaluates anything inherits it,
because they all go through that one function; the page-script loop then
re-labels the failure `target_crashed` and keeps the details it was handed.

The host never reads the exception's `name`, and the ledger is not involved:
`audit_action` records fixed vocabularies and cannot carry page text.

## 3. What does *not* leak, measured rather than assumed

This is narrower than it first looks, and the design should not fix what is
not broken. Measured against the same build, all passing today:

| path | result |
| --- | --- |
| a listener that throws during the load lifecycle | contained — the open succeeds, nothing reaches the answer |
| a timer callback that throws, drained under a later request | contained |
| a listener that throws under the agent's own `target.act` | contained |
| a rejection nobody handles | contained |
| `session.inspect`, `target.list`, `memory.report` afterwards | clean |

So the leak is exactly **one path**: an uncaught throw in a page's top-level
script, through `engine_error`. The other four are contained because the
dispatcher and the job drain swallow what a page handler throws — which is
worth writing down, because it means the repair is small and the court's job
is to keep all five true rather than to fix five things.

## 4. What must survive the redaction

A redaction that answers "something went wrong" for everything is not a
diagnostic, and the ruling asks for both halves:

1. **The typed code stays.** `target_crashed` for a script that threw,
   `deadline_exceeded` for a host that ran out of time, and they stay distinct
   — a page that hangs must not become a page that threw.
2. **The retryable bit and the scope stay**: `retryable: false` and a target
   scope on a crash, `retryable: true` on the deadline.
3. **A fixed host reason stays.** `message` is host-authored — "a script
   threw" — and is kept as it is.
4. **`details` stops varying with the page.** Two pages that throw the same
   class with different values must produce byte-identical details. That is a
   stronger requirement than "strip the value", and deliberately so: it rules
   out a hash, a length, a first character, a truncation, or any other residue
   that carries page information without looking like it.

## 5. The court, frozen from this section

Eleven criteria, run on both allocators with a fresh host per arm. Values are
opaque synthetic tokens; a check reports *that* a value was found, never what
it was, and the last criterion re-reads the receipt from disk to prove the
court did not become the leak it is about.

- **R1** a page's own throw does not carry its value into the answer.
- **R2** nor any encoding or fragment of it: percent, JSON escape, character
  codes, hex, upper case, reversed, and any eight-character window, so a
  redaction that only strips the literal string fails here.
- **R3** nor a listener that throws during the lifecycle.
- **R4** nor a timer callback that throws when the host drains.
- **R5** nor a listener that throws under the agent's own action.
- **R6** nor a rejection nobody handles.
- **R7** the typed code, the retryable bit and the scope survive.
- **R8** two pages, one class, two values, identical details.
- **R8b** and a fixed host reason is still said out loud.
- **R9** a host that ran out of time is not a host whose page threw.
- **R10** and neither does the ledger the session keeps.
- **R11** the court's own receipt carries no page value.

**Measured on `4a5836f43b38…`: 17 of 23, `passed: false`.** R1, R2 and R8 fail
on both allocators and nothing else fails. That is the falsification, and it
is the shape §3 predicts: one path, not five.

A note on how that number was reached, because it bears on trusting the court.
The first run failed **R11 on itself**, 10 of 23: the synthetic values were
readable — `qxzv-lifecycle-3390-vwqs` — and shared eight characters with the
criterion that reports them and with the fixture's own URL, so the scan found
its own vocabulary and called it a leak. The values are now opaque. A court
that can catch itself was worth having.

## 6. The repair, and what it must not be

The recommendation, for the ruling to accept or replace:

**`details.engine_error` stops carrying an exception message at all.** In its
place, a closed host vocabulary — one of a fixed, enumerable set of reasons
the host itself authors, in the same spirit as the action ledger's fixed
`kind` and `outcome`. The exception's text is never copied to a caller,
whether the script was the page's or the host's, because the host's own
scripts run page listeners and a message from one of them is page text
wearing a host script's name.

Two weaker options, recorded so the ruling can compare rather than infer:

- **Drop the key.** Simplest, and R7/R8b still hold because typing and the
  reason live elsewhere. Costs the host its only channel for "what actually
  went wrong" during bring-up.
- **Keep the message only for host-authored scripts.** Attractive and, I
  think, wrong: the host cannot tell whose text a message carries once a page
  handler has run inside its script.

**What the repair must not be:** the `DOMException` candidate from
`selector-error-name-audit-0.0.1.md` happens to blank `engine_error`, because
rquickjs's `as_exception()` returns `None` for a `DOMException` and the host
falls back to a contentless string. That hides this instance without fixing
anything — a page throwing a plain `Error` still leaks, measured — and it
would make the leak's absence an accident of an unrelated slice. The
redaction is therefore specified, and must be verified, on a build **without**
candidate A: R1, R2 and R8 must pass with the selector engine exactly as it is
today.

## 7. Pending, and deliberately not decided here

1. **May `details` name the exception's class?** `TypeError`, `RangeError` and
   the rest are a closed ECMAScript set and carry no page value, but the class
   is page-chosen, so it is a bit of page-controlled information in a host
   diagnostic. R8 as frozen compares two pages throwing the *same* class, so
   the court does not pre-empt this either way.
2. **`details.script`** carries the script's origin: `"inline"`, or for an
   external script its `src` as written in the document. That is page-derived
   text in a diagnostic, same-origin and not a form value, and it is outside
   what this ruling asked for. Read from the source, not measured. Recorded
   so it is not discovered later as a surprise.
3. **Scope of the error-class slice** — selector engine, `removeChild`,
   `classList` — stays as ruled in the audit: candidate A provisionally
   accepted with the constructor captured at base load, implementation
   deferred until this redaction is designed and verified.
4. **Cost.** The repair is host-side Rust and touches no shim source, so M1
   and M2 should not move at all. That is a prediction, to be measured on the
   candidate rather than assumed, against the unchanged floors of 245,760 and
   1,720,320.


## 8. Ruled, built, measured

The ruling took the recommendation in §6 whole. `details.engine_error` no
longer carries an exception message: it says one of exactly two words the host
authors itself, `EVAL_FAILURE_THREW` for a script that threw and
`EVAL_FAILURE_DEADLINE` for a deadline that expired, chosen from what the host
knows — whether its own clock ran out — and never from anything the page said.
No class, no length, no first character, no digest.

The exception is caught and dropped at the catch site rather than filtered
downstream, which is the part worth keeping: a filter is a place a later path
can forget to call, and there is now nothing to forget, because the message
never leaves the function that catches it.

**The frozen court reads 23 of 23** on `30004da4d050…`, against 17 of 23 with
`passed: false` on `4a5836f43b38…`, the build it was frozen on. R1, R2 and R8
turned over; R3 through R7, R9, R10 and R11 passed before and pass now, which
is the point of having frozen them.

**It costs nothing.** M1 224,458, M2 1,569,708 and main-only slack 38,496 are
byte-identical to the previous binary — the change is host-side Rust and
touches no shim source — against unchanged floors of 245,760 and 1,720,320.
Seventeen receipts were rerun on the binary; `cargo test` is 54 of 54, clippy
is clean under `-D warnings`, and the contract passes.

The verification was run with the selector engine **exactly as it is**, with
no `DOMException` candidate anywhere in the build, so the values are gone
because of this repair and not as an accident of an unrelated one. That was
the condition §6 set for itself and it is met.

`details.script` keeps carrying an external script's `src` as written in the
document. It is page-derived text in a diagnostic, it is not a form value, it
stays as it is by ruling, and it is written down here so it is a known
position rather than an oversight. If a court ever shows it leaking something
that matters, that is its own ruling.
