# Agent-native form interaction 0.0.1 (design only, nothing implemented)

Status: **proposed.** No code, no court run, no measurement. This freezes the
slice, the honest semantics, the exclusions, the budgets and the court before
anything is written, and it names the decisions the root must make first.
`control-0.0.1` does not change: only `0.0.2` gains action shapes.

## 1. Where the contract stands

`target.act` takes exactly `{target, reference, action}` and the action is
exactly `{"kind": "click"}`, in the schema, in the checker and in the host. A
click is honoured on three things: an anchor with an `href`, a `button`, and
an `input` whose type is `button`, `submit` or `reset`. Anything else is
refused. A click on an anchor dispatches a cancelable `click` and, if the page
does not prevent it, navigates through the same atomic path a
`target.navigate` uses.

The semantic snapshot names six roles: `heading`, `button`, `link`,
`textbox`, `label`, `text`. A `textbox` entry carries a bounded `value`. So an
Agent can see a text field and cannot change it, and cannot see a checkbox, a
radio, a select or a form at all.

**This is the honest starting point, and it is smaller than it looks.** The
document shim behind the realm models `value`, `type`, `name`, `click()` and
event dispatch. It does **not** model `checked`, `selected`, `selectedIndex`,
`form`, `submit()`, `disabled`, `readOnly` or constraint validation. None of
those exist to be driven yet. A form slice is therefore not only an action
vocabulary: it needs the snapshot to name new roles and the shim to model the
state those roles imply. That is product code, and §9 asks the root to
authorise it explicitly rather than letting it arrive as a side effect.

## 2. The smallest typed set (0.0.2 only)

Five action kinds beside the existing `click`. Each is a closed shape with
bounded arguments, and none carries script.

| Action | Shape | Valid on |
|---|---|---|
| set value | `{"kind":"set_value","value":"<≤1024 bytes>"}` | `textbox` |
| set checked | `{"kind":"set_checked","checked":true\|false}` | `checkbox`, `radio` |
| choose option | `{"kind":"select_option","index":<0..63>}` | `select` |
| submit | `{"kind":"submit"}` | `form` |
| press | `{"kind":"press","key":"enter"\|"space"}` | `button`, `link`, `textbox`, `checkbox`, `radio`, `select` |

`click` keeps its exact current meaning and its exact current shape.

New snapshot roles, each with bounded facts: `checkbox` and `radio` carry
`checked` and the radio's `group`; `select` carries its `options[]` as
`{index, label}` with at most 64 entries and the `selected` index; `form`
carries its `controls[]` as node ids with at most 64 entries, and its
`method` and whether it has an `action`. Every control of any role carries
`disabled` and `read_only` so an Agent can see a refusal coming rather than
discover it.

**Why five and not fewer.** Value, checked state and chosen option are three
different state changes with three different events; submit is a document
transition rather than a state change; and keyboard activation is a distinct
standard behaviour an Agent needs because many controls are reachable only
that way. Folding any pair together would either hide which state changed or
smuggle a second meaning into one kind.

## 3. Event semantics, stated honestly

What the host will do, exactly, and nothing implied beyond it:

| Action | Events, in order | State |
|---|---|---|
| set value | `input`, then `change` | the element's value becomes the argument, verbatim, with no formatting, coercion or masking |
| set checked | `click`, then `change` | the element's checked state becomes the argument; setting a radio true clears the others of its group first |
| choose option | `change` | the select's selected index becomes the argument |
| submit | `submit`, cancelable | if not prevented, the form navigates through the same atomic path as `target.navigate`, `GET` only in this slice |
| press enter | `keydown`, `keypress`, `keyup`, then the activation behaviour of the element: a button or link activates as a click; a single-line textbox submits its form if it has one | as the activation implies |
| press space | `keydown`, `keypress`, `keyup`, then activation: a button activates, a checkbox or radio toggles | as the activation implies |

- A `disabled` control refuses every action typed, and so does a `read_only`
  one for `set_value`. Neither silently no-ops.
- **Constraint validation is not implemented.** `required`, `pattern`, `min`,
  `max` and `type=email` are neither enforced nor reported, and a submit is
  not blocked by them. The snapshot says so per control rather than implying
  a validity the host does not compute.
- Every applied action advances the target revision, because it changes
  observable state. Filling three fields therefore costs three snapshots.
  That cost is real and is stated rather than hidden; a bounded batch action
  is deliberately **not** proposed here and is a separate decision.
- A submit that navigates is atomic in the existing sense: the replacement
  document is built completely before anything live changes, and a failure
  leaves the form, its values, the generation, the realm and the revision
  exactly as they were.

## 4. Explicit exclusions

Each is excluded because honouring it would need behaviour this host does not
have, and each would need its own justification to enter later:

- input methods and composition events, and any non-Latin input path;
- `contenteditable` and rich text;
- file inputs, drag and drop, and the clipboard;
- autofill, password managers and any credential source;
- arbitrary key sequences, key repeat, modifiers and pointer coordinates:
  the only keys are `enter` and `space`, and there is no coordinate anywhere
  in the vocabulary;
- `POST` and multipart submission, and any request body the Agent composes;
- generic JavaScript evaluation, which this route has never offered through
  the control door and which this slice does not introduce.

## 5. Bounds

| Thing | Bound |
|---|---|
| a value argument | 1,024 bytes of UTF-8 |
| options reported per select | 64 |
| controls reported per form | 64 |
| forms reported per document | 16 |
| option label and control name in a snapshot | 256 bytes each, as today |
| actions per request | one; there is no batch |

Anything beyond a bound is a typed `resource_limit` or `invalid_request`, not
a truncation.

## 6. Identity, capability, audit and budgets

- The node reference stays `(target, revision, node)`. An action against an
  older revision is `stale_revision`, exactly as a click is today, and the
  revision an action produces is returned so `target.wait` settles
  deterministically.
- The request's deadline bounds the whole action including any navigation a
  submit causes; the result stays inside the response budget.
- Capability attenuation stays unsupported and fail-closed on this route, as
  `session.inspect` already reports.
- **The audit ledger never records a value.** A record names the operation,
  the action kind, the node's role, and for `set_value` the byte length of
  the argument and nothing else. No value, no option label, no field name, no
  form action URL beyond the origin the ledger already records. That rule is
  stronger than the navigation ledger's and is the reason a form slice can be
  audited at all.

## 7. CDP mapping, losses recorded

| Native | CDP | Loss |
|---|---|---|
| `set_value`, `set_checked`, `select_option` | `DOM.setNodeValue`, `Input.dispatchKeyEvent`, `Runtime.callFunctionOn` | none of these is a typed form action: CDP either sets a node's text or replays raw key events. The adapter does not project them and they stay `-32601`, because mapping a typed action onto raw key replay would claim an input path this host does not have |
| `submit` | `Page.navigate` after a form serialisation | not projected: the adapter would have to serialise the form itself, which would make it a second authority over form state |
| `press` | `Input.dispatchKeyEvent` | not projected: only two keys exist here and the CDP method implies the full keyboard |
| snapshot roles | `DOM.getDocument` attributes | the new roles and their bounded facts have no CDP equivalent and are visible only through the native door |

The slice therefore adds **no** CDP surface. That is a deliberate loss, and
the mapping records it rather than inventing an adapter-side form model.

## 8. Multi-backend loss matrix (design expectation, not measurement)

| Route | Text value | Checkbox, radio | Select | Submit | Keys |
|---|---|---|---|---|---|
| native bounded route | implementable, but needs the shim to model value events and the snapshot to name the role | needs `checked` and radio grouping in the shim, neither of which exists | needs `selected`, `options` and `selectedIndex`, none of which exists | needs `form`, control association and `submit`, none of which exists | needs the activation rules above; no key model exists today |
| Lightpanda 0.4.0 | a real engine, so the behaviour exists, but nothing in this repository has driven a form through its control host | same | same | same | same |
| Servo 0.5.0 | a real engine, so the behaviour exists; its route is already narrow and its G1 recovery dependency red, so a form court would measure a route that is not a memory candidate | same | same | same | same |

Only the native route is proposed for implementation. The other two rows stay
`unsupported_operation` or unverified until each passes the same court, and no
row changes on inherited evidence.

## 9. Decisions the root must make before any code

1. **Product-code scope.** The slice cannot exist without extending the
   document shim (checked, radio groups, select and options, form and its
   controls, `disabled`, `read_only`, `submit`) and the snapshot's roles. That
   is more than an action vocabulary. Authorise it explicitly or narrow the
   slice to what today's shim can already honour, which is `set_value` and
   `press enter` on a textbox and nothing else.
2. **Revision per action.** Every applied action advancing the revision means
   one snapshot per field. Accept that cost, or open a separate decision on a
   bounded batch action.
3. **Option addressing.** `select_option` by index within the snapshot's
   bounded option list is proposed. By label would be friendlier and
   ambiguous; by node reference would need options to be addressable nodes.
4. **Submit scope.** `GET` only, no body composition. Confirm, or exclude
   submit from this slice entirely until a body-carrying request has its own
   authority review.

## 10. Contract compatibility, to be proven not asserted

`control-0.0.1` keeps `action` as exactly `{"kind":"click"}`: the same schema
file, the same checker branch, the same examples, byte for byte. `0.0.2`
alone gains the five shapes. The proof obligations are the ones the navigation
slice already met: the two schemas differ only in identity, version constant
and the action shapes; a `0.0.2` action sent under `0.0.1` is
`invalid_request` and is never inferred; each version keeps its own examples;
and the checker validates the action shape against the version the request
names.

## 11. Pre-registered court criteria

`form-court.py`, strictly headless, hermetic loopback only, no surface, no
window, no AppKit, both allocators, fresh host per run, one warm-up plus
seven runs.

1. **Typed vocabulary**: each action applies on its own roles and is refused
   typed on every other role; a `disabled` and a `read_only` control refuse;
   an over-long value, an out-of-range option index and an unknown key are
   refused before any state changes.
2. **Events and state**: the ordered events of §3 are observed for each
   action, the state afterwards is what the action asked for, and a radio set
   true clears its group.
3. **Identity**: every applied action advances the revision, an older
   reference is `stale_revision`, and the returned revision settles
   `target.wait` without polling.
4. **Submit**: a form submit navigates atomically with the same identity
   rules as `target.navigate`; a prevented submit changes nothing; a failed
   submit leaves values, generation, realm and revision exactly as they were.
5. **Audit**: one record per applied action, naming the kind and the role,
   with the value's byte length and never the value, the label, the field
   name or anything beyond the origin already recorded. The court asserts the
   ledger's text contains none of the values it typed.
6. **Memory**: a repeated cycle of edit, reset and submit, and a cycle that
   replaces the realm, measured as a **differential** against a control arm of
   identical request count, deadline and target. The navigation increment
   showed why an absolute cap is the wrong instrument here: what such a soak
   counts is page-granular allocator retention of a realm built and destroyed
   per navigation, which moved 114 KB between builds. The form court's
   pre-registered figures are therefore: the live owners after 128 cycles
   (values, options and form state the host holds) stay bounded and return to
   zero at close, and the differential's excess is **reported with its per-run
   distribution and its observer effect** rather than gated, until a stable
   instrument exists for it.
7. **CDP**: the methods of §7 stay `-32601`, proven with the pinned client.

No criterion here is a gate. The slice's verdict follows the same vocabulary
as the others: keep, narrow, reject.

## 12. Amendment after the ruling (sections 1 to 11 stay as the proposal)

The root accepted the slice with rulings. Where this section differs from
what was proposed above, this section governs; the proposal is left intact as
the record of what was asked.

### 12.1 Scope of the product change

The bounded realm-shim and semantic-snapshot extension needed for all five
actions is authorised. Two limits on it:

- **The QuickJS realm stays the sole DOM and form authority.** The host mirrors
  no form state: it reads what the realm reports and writes through the realm.
  Nothing about a control's value, checked state or selection is remembered
  outside the realm, so nothing can disagree with it.
- Only the enumerated properties, roles, associations and event behaviour are
  added, each with its bound, and every gap stays a recorded loss rather than
  an approximation. The shim gains `checked` with radio grouping, `selected`,
  `selectedIndex` and `options`, `disabled` and `readOnly` reflection, the
  form association of a control and a form's `elements`, and `submit`. It
  gains nothing else.

### 12.2 One revision per action

Accepted as proposed: every successful action advances the revision, there is
no batch action in this slice, and the deterministic contract is the returned
revision together with `stale_revision` on anything older. Batch or
transaction semantics are a future Agent optimisation, not a gap here.

### 12.3 Choosing an option

`select_option` addresses an option by the numeric index the snapshot gave in
that revision, and nothing else. An option is **not** a separately addressable
node, and selection by label is not offered because labels are ambiguous. The
snapshot reports each option as `{index, label, selected, disabled}`, bounded
at 64 per select, where the label is informative and never a selector. The
option's `value` attribute is not exposed in the snapshot; the realm uses it
internally when a submit serialises, and the audit never records it.

### 12.4 Submit, GET only, and exactly how it serialises

A submit builds a bounded `application/x-www-form-urlencoded` UTF-8 form data
set from the form's **successful controls** and puts it in the query of the
resolved action URL.

- **Successful controls** are, in document order within the form: a text input
  or textarea with a name; a checkbox or radio with a name that is checked; a
  select's selected option, contributing that option's value; and the explicit
  submitter when the submit came from a named submit control. A control that
  is disabled, or has no name, contributes nothing.
- **Hidden inputs** contribute only because they are modelled explicitly, with
  a name and a value, and they count against the same per-form control bound.
- **A checkbox with no value attribute** contributes `on`, as the standard
  requires.
- **Encoding**: each name and value is percent-encoded, a space becomes `+`,
  and pairs are joined with `&` in the order above. The set is UTF-8.
- **The action URL**: an empty or absent action means the document's current
  URL. For `GET` the form data set **replaces** any existing query on that
  URL entirely, and any fragment is dropped, as the standard requires. The
  result is resolved against the document's URL exactly as a link href is.
- **Hard limit**: the encoded URL, query included, is at most 2,000 bytes,
  which is the host's existing URL bound and is smaller than every network
  bound it passes through. Beyond it the submit is a typed `resource_limit`
  **before** any state or navigation changes.
- **`POST`, multipart and file** are refused typed as `unsupported_capability`
  before any mutation or navigation, and are never simulated by turning them
  into a `GET`.
- The navigation a submit performs is the existing atomic one, under the same
  address, TLS, redirect, byte, deadline and profile policy rules, with the
  same rollback: a refused or failed submit changes no value, no generation,
  no realm and no revision.

### 12.5 Values never leave the realm and the page

A form value, an option value and a built query string are page data. They may
not appear in the audit ledger, the court-only log, an error's details, a
receipt, or any diagnostic. A record of a submit names the operation, the
role, the origin of the resolved URL and nothing more. `target.inspect` may
still report the committed URL, query included, because that is the browser
state an Agent must be able to read; every diagnostic path uses the origin or
a redacted URL instead. Court fixtures use fake values only, and the court
asserts that none of the values it typed appears anywhere in the ledger, the
court log or the receipt.

### 12.6 Refusals happen before mutation

A disabled or read-only control, an unsupported method, an out-of-range
index, an over-long value and an over-long resulting URL are all refused
before anything changes. Reset needs no new action: a click on a reset control
is already supported and restores the form through the realm.

### 12.7 Memory criteria, numeric and pre-registered

Diagnostics are not a substitute for these. Each is a live-owner fact:

| Criterion | Threshold |
|---|---|
| a semantic snapshot of the form fixture | within the caller's `max_bytes`, and the response within its existing bound |
| forms, controls and options reported | ≤ 16 forms, ≤ 64 controls per form, ≤ 64 options per select |
| realm live bytes, plateau | after 128 edit-and-reset cycles on the same bounded control, no more than 65,536 bytes above the value after 8 cycles. Linear growth of a 1,024-byte value over 128 cycles would exceed 131,072, so this separates a plateau from linear growth |
| realm live bytes, tail | the last 64 cycles add no more than 8,192 bytes |
| audit ring | capped at 64 records, and no value, label or query anywhere in it |
| after a submit replaces the realm | the new realm's live bytes are within 65,536 of a freshly opened document's |
| after target and session close | every form, option, control and audit owner reads zero |
| arena counters | blocks leaked stays zero and arenas unmapped equals arenas retired |

The default and arena footprint differential, its per-run distribution and its
observer effect are reported as **diagnostics only**. They are never a pass,
never a substitute for the criteria above, and no cap is set on them, for the
reason the navigation increment recorded.

### 12.8 Unchanged by this ruling

The CDP methods of §7 stay explicitly `-32601`; the slice adds no adapter
surface. Lightpanda and Servo stay unverified and change only on their own
measured evidence. `control-0.0.1` keeps `action` as exactly
`{"kind":"click"}`, byte for byte, and only `0.0.2` gains the five shapes.

### 12.9 Order of work, frozen

1. This amendment.
2. The `0.0.2` schema, contract checker and examples, and the CDP mapping
   entries with their losses.
3. The court, frozen before the host changes.
4. The shim and host implementation.
5. One measurement pass against §12.7.

Work stops on any failed frozen semantic or live-memory criterion.

## 13. Corrections after the root audit (sections 1 to 12 stay as written)

The court passed 69 of 69 and still missed five things. Each is recorded here
before any code changes; where this section differs from §2, §3 or §11, this
section governs, and the earlier text stays as the record of what was claimed.

### 13.1 `press` had a fall-through that claimed work it never did

The implementation ended with a catch-all that answered `applied` and advanced
the revision for any pair it did not handle: space on a textbox, enter on a
checkbox, any key on a select or a form. That is a false claim. The activation
matrix is now closed and exhaustive, and every pair outside it is refused
typed **before** the revision moves:

| Key | Role | Behaviour |
|---|---|---|
| enter | link | activates as a click; navigates unless the click is canceled |
| enter | button | activates as a click |
| enter | submit button | submits its form |
| enter | single-line textbox | submits its form when it has one, else refused |
| space | button | activates as a click |
| space | checkbox, radio | toggles, with the click's cancellation respected |

Everything else, including **every key on a select** and space on a textbox, is
`unsupported_capability` with reason `key_role_unsupported`. No keyboard
behaviour is claimed for a select, because none is modelled. A textarea is not
a single-line textbox and enter does not submit from it.

### 13.2 A value's length was counted in the wrong unit

`action.value.length` in the realm is UTF-16 code units, not UTF-8 bytes, so a
non-ASCII value was audited with the wrong number. The host now computes the
length from the request's own already-validated string, which is UTF-8, and
**never trusts or reports a length the realm sends back**. The realm returns no
length at all. Non-ASCII values are covered by a test and by the court.

### 13.3 A failed submit leaked its query into diagnostics

A form's query is page data, and the navigation path put the href, and
sometimes the response URL, into an error's details. Every failure of a form
submission is now diagnosed in a **sensitive mode**: the error carries the
typed code, a bounded reason and the identity facts, and carries no href, URL,
query, control name or value, option value or label, and no encoded fragment
of any of them. The court exercises a denied origin, a missing document, an
offline profile, an unqualified scheme and an expired deadline, and greps the
whole response, the court log, the ledger and the receipt for every fake value
and for its percent-encoded and `+`-encoded forms.

### 13.4 The rollback claim was impossible and is corrected

§3 said a failed submit leaves the form, its values, the generation, the realm
and the revision exactly as they were. That cannot be true once the cancelable
`submit` event has run in the live realm: a handler may mutate the document
before the network fails, and no browser rolls that back. The honest contract:

- A failed submit **preserves identity and history**: the target, the frame,
  the document generation, the realm id, the history entries and position, and
  the committed URL are unchanged, and the document is not replaced.
- **Handler effects may remain**, and the revision reflects them, because the
  page really did change.
- The preflight refusals of §12.4, `POST`, multipart and file, still happen
  **before any event is dispatched or anything mutates**.

A fixture whose submit handler mutates observable DOM, followed by a failing
navigation, proves both halves: identity and history unchanged, handler effect
and revision visible.

### 13.5 What `applied` means, exactly

- `applied: true` means the action's own effect took place: the value, checked
  state or selection was written, or a navigation was started.
- `applied: false` with `default_prevented: true` means the page canceled the
  default. The events were dispatched, any handler effects stand, and the
  revision reflects them; the action's own effect did not happen.
- `set_checked` and every activation respect that ordering: the cancelable
  `click` is dispatched and, when it is canceled, the state goes back to what
  it was and no `change` is fired.
- The semantic snapshot remains the final-state authority whenever a page
  handler transforms what was written.

### 13.6 Court, extended

The court now covers the **full runtime cross-product** of the two keys against
every role, asserting exactly the matrix of §13.1; a non-ASCII value and its
audited byte length; the five submit failure kinds with a whole-output grep for
fake values in plain, percent-encoded and `+`-encoded form; the
submit-handler-mutation fixture of §13.4; and cancellation of a click on a
checkbox and of a submit, asserting `applied` and `default_prevented` exactly
as §13.5 defines them.
