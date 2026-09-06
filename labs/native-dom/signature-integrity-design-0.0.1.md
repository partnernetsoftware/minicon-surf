# The approval must bind, the fragment must be seen, and the probe must be the host's

Design record for the slice that closes **F4** (approval-signature integrity),
**F3** (fragment detection) and the **`REALM_PROBE` truthfulness** hole named in
`uncaptured-intrinsic-audit-0.0.1.md`. Written **before** the code, with the
court frozen from §5 and failing until the code lands.

Scope, taken verbatim from the ruling: only non-replaceable string
concatenation with a literal separator, and `value[0]` indexing. **No new
intrinsic capture. No widened handle. No change to base bytes or to any
bound. Not mixed with H2 or with slimming.** F1 (`methodOf`), F2 (`targetOf`)
and F5 (the download probe's node kind) stay out and stay open as separate
design candidates; nothing here folds them in quietly.

## 1. What the audit left, and what measuring it again changed

The audit named `Array.prototype.join` as the way into F4. Before freezing a
court on that, the same fixture was run against two more intrinsics, and the
result changed the design:

| route | patched | measured on `ba46420b` |
| --- | --- | --- |
| `join` | `Array.prototype.join(" ")` on the four-element signature | act **applied**, host fetched `/moved.html` — the URL the agent never approved |
| `replace` | `String.prototype.replace`, returning a string constant for the two hrefs | act **applied**, host fetched `/moved.html` — **the same defeat through a different intrinsic** |
| global `String` | `window.String`, selective | act applied, but the host fetched **`/landed.html`**, the approved URL |

The second row is the one that matters: **concatenating the signature would
have closed one route and left another open**, and a court frozen on `join`
alone would have passed over a live F4. The signature is only as honest as its
four inputs, and one of them — `href`, produced by `urlOf` — was itself built
with a replaceable `replace`.

The third row is a correction to my own first reading of it. The global
`String` *is* page-replaceable and the host's scripts do use it, but in this
shape it is not a route into F4: the page's own `setAttribute` stores
`String(value)`, so a patch that lies about `"/moved.html"` also prevents the
href from moving, and what the host then fetches is exactly what the agent
approved. No divergence, no finding. It remains a live route into the court
probe (§4), where nothing stores anything.

## 2. F4 — the approval signature

`__mcsPreflight` (`main.rs:558`) builds the value the two phases compare:

```js
signature: [navigation.shape, decision, navigation.method || "", navigation.href || ""].join(" "),
```

and `urlOf` (`main.rs:579`) builds the fourth field:

```js
const urlOf = (raw) => String(raw == null ? "" : raw).replace(/^[ \t\n\r\f]+|[ \t\n\r\f]+$/g, "");
```

**The change.** The signature becomes concatenation with a literal separator,
and `urlOf` is rebuilt from operations a page cannot reach: `"" + raw`
coercion, `.length` (an own property of a string, not a prototype method),
index reads, and `+=`.

```js
const urlOf = (raw) => {
  const s = raw == null ? "" : "" + raw;
  let a = 0, b = s.length;
  while (a < b && WS(s[a])) a++;
  while (b > a && WS(s[b - 1])) b--;
  let out = "";
  for (let i = a; i < b; i++) out += s[i];
  return out;
};
```

`WS` is a literal comparison against the five ASCII whitespace characters HTML
strips, written out rather than tested with a regex, because
`RegExp.prototype.test` is the page's.

**`"" + raw` is safe here and not merely equivalent-looking.** `setAttribute`
stores `String(value)` (`dom_shim_base.js:321`), so every attribute value in
the map is already a string primitive; `"" + raw` on a string primitive
consults no prototype and no `Symbol.toPrimitive`. Dropping `String(...)` also
removes the host script's dependence on a *global* the page can reassign.

**What this does not close, stated so it is not later mistaken for closed.**
The signature's other two variable fields are `decision` and `method`.
`decision` comes from `activationOf` and `method` from `methodOf`, both of
which still route through `trim`/`toLowerCase`/`test` — the F1 and F2 sites. A
page that changes a *target* or a *formmethod* between the two derivations,
while patching the intrinsic those two read through, can still make two
different activations compare equal. That is F1/F2's slice, it is named in the
court's own preamble, and the criteria here say **href** where they mean href.

## 3. F3 — the fragment

`schemeDecision` (`main.rs:582`) asks `value.startsWith("#")`. It becomes
`value[0] === "#"`. String index reads are own properties of the string exotic
object; no prototype method is consulted and nothing on `String.prototype`
shadows them.

Two behaviours must not move, both measured on `ba46420b` before the change and
pinned by the court: `href=""` and `href="   "` both read `allowed` and both
navigate to the current document. `value[0]` on an empty string is `undefined`,
which is not `"#"`, so the empty case falls through to the scheme check exactly
as it does today — the empty case is not a new fragment and not a new refusal.

## 4. The court's own probe

`REALM_PROBE_JS` (`main.rs:237-246`) builds seven booleans and hands them over
as one string:

```js
String(typeof window.__mcsInternals !== "undefined"), … ].join(":")
```

Both halves are the page's. Measured: a dictated `join(":")` reports
`main_present: true` where the truth is `false`; a swapped global `String`
flips **every one of the seven fields**. The parse's conservative defaults
(`unwrap_or(true)` for the two internals fields, and a fixed fallback string on
eval failure) protect against a probe that *fails*, never against one that
answers a well-formed lie.

**The change.** The seven expressions are booleans, and a boolean concatenated
with a string is converted by the specification itself — `Boolean.prototype
.toString` is never consulted. So the whole answer becomes

```js
(typeof window.__mcsInternals !== "undefined") + ":" + (…) + ":" + …
```

with no `String(` and no `.join(`. There is no page-owned operation left on the
path, which is the only claim in this document that is unconditional.

## 5. The court, frozen before the code

`signature-integrity-court.py`, **34 criteria** in four groups. It is frozen
now and run now against `ba46420b`, which scores **19/34**; the failing receipt
(`evidence/native-dom-control-0.0.2-signature-integrity-falsification.json`) is
the falsification arm. Every control and every anti-vacuity criterion passes
today, and the fifteen failures are exactly the defects the slice must close --
which is the shape a freeze should have: it fails for the reason it was written
and for no other.

**A — the approval binds (18).** A page moves the approved link's `href` from
its own `focus` handler, which runs inside the window `act_script` opens
between the two derivations. Refused `preflight_mismatch`, with **no request
reaching the server**, on three arms: unpatched, `join` patched, `replace`
patched. The same three arms with the href **not** moved must navigate and be
fetched — that is the anti-vacuity half, and without it a host that refused
everything would score ten out of ten. Nine more criteria repeat the whole pattern for a
**form**, whose `action` moves in the submitter's `focus` handler and whose
`actionOf` reads through the same `urlOf`.

Two things about the form arm were measured rather than assumed, and both
changed the fixture. A form is **not focusable**, so a `submit` aimed at the
form itself opens no window for page code at all and can demonstrate nothing;
and a `click` on a submit button goes through `act_script`, which calls
`el.click()` and never submits, so it can demonstrate a bypassed refusal but no
navigation. The arm that works is `kind: "submit"` aimed at the **submitter**,
which is focusable and does navigate. A third measurement is recorded in the
receipt rather than argued here: under the `replace` patch the moved form
action is bypassed but the host still fetches the **approved** URL, because the
patch flattens both addresses to the same string. The criterion is still the
strict one -- the act must be refused -- because after the fix `urlOf` no
longer reads through `replace` and the two addresses differ again.

**B — the fragment is seen (5).** `#section` reads `fragment_unsupported` and
is refused, unpatched and with `startsWith` patched to answer `false` for
`"#"`. Anti-vacuity: a plain link in the same patched document must still read
`allowed` and still navigate. Two criteria pin the empty and whitespace-only
hrefs at today's measured behaviour.

**C — the probe is the host's (5).** `owners.realm_probe` reports the same
seven values on a plain page, on a page dictating a vector through `join(":")`,
and on a page swapping the global `String`. Anti-vacuity: the marker element's
`dom_id` must appear in the snapshot on every arm, which proves the page's
script ran before the probe was asked — a court whose patch never installed
would otherwise pass by doing nothing.

**D — the technique and its cost (6).** No `.join(` or `String(` left in
`REALM_PROBE_JS`; no `.join(` in the signature; no `.replace(` or `String(` in
`urlOf`; no `.startsWith(` in `schemeDecision`; the fifteen declared captures
unchanged and no sixteenth; and `dom_shim_base.js` exactly **32,898** bytes and
`dom_shim_main.js` exactly **26,485** bytes, so the slice cannot pay for itself
with base bytes and cannot be confused with slimming. Every source criterion
first asserts that the region it inspects was **found and non-empty**, because
a source check that silently matches nothing is a pass that means nothing —
recorded as a real failure mode in `snapshot-schema-court.py`.

## 6. Cost

The three edits are all inside host scripts (`REALM_PROBE_JS`,
`ACTIVATION_JS`, `SERIALIZE_JS`), which are compiled at each evaluation and are
**not resident per realm**, so the per-realm price measured by
`shim-footprint-court.py` should not move at all. `dom_shim_base.js` and
`dom_shim_main.js` are not touched, which group D pins to the byte. The
runtime cost is `urlOf` becoming a bounded loop over an href instead of one
regex pass — hrefs are bounded by `MAX_URL_BYTES` (2000) and `urlOf` runs a
small fixed number of times per act. Measured numbers go in the implementation
record, not here.

## 7. Safe failures

- A signature that cannot be derived refuses the act; it cannot approve one.
  Every new failure mode in `urlOf` returns a string, and a wrong string is a
  mismatch, which is a refusal.
- `value[0]` on an empty string is `undefined`, which is not `"#"`: the empty
  href keeps today's `allowed`, and its refusals, if any, come from the scheme
  and origin checks the Rust half makes for itself.
- If the probe's answer is ever short or malformed the existing parse defaults
  still apply, unchanged; this slice removes a way to make it *well-formed and
  false*, and takes nothing away from the failure path.

## 8. Non-goals

F1 `methodOf`, F2 `targetOf` and F5 the download probe's node kind stay open
and stay separate. No new capture, no widened handle, no bound moved, no base
byte moved, nothing from H2 or from slimming, no protocol change, no visual
run, no soak, no download, no crate fetch.
