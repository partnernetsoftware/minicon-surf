# MiniCon Surf control contract 0.0.1

Status: **G0 vocabulary checked; synthetic native/CDP subset implemented**.
This freezes object meanings and is exercised by an engine-neutral host. It
does not claim that a browser product executable or broad CDP adapter exists.

The internal contract is not CDP. Native CLI and future in-process clients use
this model; CDP is an adapter whose exact losses are recorded below.

## Object vocabulary and ownership

| Object | One meaning in every frontend | Owner and lifetime |
|---|---|---|
| profile | Storage, network policy, permissions, budgets, and persistence identity | Exists independently of a process. Persistent profiles retain identity; ephemeral profiles are destroyed explicitly or with their owning host. |
| session | One live browser-engine authority opened against exactly one profile | Owns targets and arbitrates clients. Closing it closes its targets, but does not delete a persistent profile. |
| target | One Agent-addressable top-level browsing context with navigation and page state | Belongs to one session for its lifetime. A surface never owns it. Its opaque ID is not recycled within a host generation. |
| frame | One node in a target's browsing-context tree | Belongs to one target. Same-frame navigation may retain frame identity while replacing its document and realms. |
| realm | One JavaScript execution environment for a frame/document/world | Replaced when its document or world is replaced. An evaluation always names its target and, when needed, realm. |
| surface | A presentation attachment that can display and accept human input | Attaches to at most one target. Detaching releases presentation resources without closing or navigating the target. `hidden` is not `hibernated`. |
| revision | A target-local, monotonically increasing semantic-state version | Advances when navigation or observable document state invalidates a snapshot. It never decreases during a target lifetime. |
| node reference | The compound `(target, revision, node)` returned by a semantic snapshot | Valid only for that exact target revision. A later revision produces `stale_revision`; node IDs alone have no meaning. |

Opaque IDs use a typed prefix (`profile_`, `session_`, `target_`, `frame_`,
`realm_`, `surface_`, or `node_`) followed by 1–64 lowercase ASCII letters,
digits, `_`, or `-`. Clients compare them but never parse their suffixes.

```text
profile
└── session (live authority)
    └── target (page lifetime)
        ├── frame
        │   └── realm
        ├── revision
        │   └── node reference = target + revision + node
        └── surface (optional attachment; never owner)
```

## Bounded JSON envelope

[`control-0.0.1.schema.json`](control-0.0.1.schema.json) is the machine-readable
envelope and identity schema. Every request contains the exact protocol and
version, an opaque `request_id`, a bounded deadline, one named operation, and
an arguments object. Every response echoes the identity and is exactly one of
success or typed failure.

Initial limits are deliberately conservative:

- one UTF-8 JSON request: at most 65,536 bytes;
- one UTF-8 JSON response: at most 4,194,304 bytes;
- nesting depth: at most 32; collection length: at most 10,000;
- `deadline_ms`: 1–120,000; expiry is a typed `deadline_exceeded` failure;
- snapshots and evaluations additionally carry caller-selected result limits;
- screenshot pixels are never silently base64-expanded into an unbounded JSON
  response; the eventual transport must return a bounded resource handle or a
  caller-authorized output destination.

The initial operation names reserve the product surface. The synthetic host
implements `profile.create`, `profile.list`, `profile.inspect`,
`profile.delete`, `profile.storage.put`, `profile.storage.get`,
`profile.policy.set`, `session.open`, `session.list`, `session.close`,
`target.open`, `target.list`, `target.inspect`, `target.close`,
`target.snapshot`, `target.act`, `target.wait`, `surface.show`, `surface.hide`,
`session.inspect` (identity, owned targets and surfaces, and the capability
audit ledger), `memory.report`, and `memory.trim`. It explicitly rejects the
remaining reserved operation `target.screenshot`. A name outside this version
is `invalid_request`; a listed operation unavailable on the selected backend is
`unsupported_operation`. Neither falls through to engine-specific behavior.

The qualified synthetic profile slice uses exact bounded arguments:

| Operation | Arguments | Result boundary |
|---|---|---|
| `profile.create` | `persistence`; optional safe `name` and `{network, permissions}` policy | Persistent profiles require the host's explicit profile root; duplicate names conflict. |
| `profile.inspect` / `profile.delete` | `profile` | Inspection exposes policy/counts, not values; deletion conflicts with live sessions. |
| `profile.storage.put` | `session`, `kind`, `key`, `value` | `kind` is `cookie` or `local_storage`; 32 entries/bucket, 64-byte keys, 1,024-byte values. |
| `profile.storage.get` | `session`, `kind`, `key` | Returns `found` plus a bounded value; the session proves writer ownership. |
| `profile.policy.set` | `session`, `network`, `permissions` | Network is online/offline; permissions are allow/deny by default. |
| `target.inspect` | `target` | Identity, revision and the bounded `frames[]`/`realms[]` enumeration (main frame first). |
| `target.snapshot` | `target`, `format`, `max_bytes`, `max_nodes`; optional `frame`, `realm` | The result names the observed `frame`, `realm` and `generation`; a foreign, ended or unknown frame/realm is `not_found`. |
| `session.close` | `session` | Closes owned targets/surfaces and releases the persistent writer lock when its last session closes; the synthetic host tears each target down in the order adapters → surfaces → target and reports it. |
| `memory.trim` | none | On macOS, requests maximal malloc-zone pressure relief and reports released bytes; other platforms are unqualified. |

Persistent records have format version 1 and use write-sync-rename replacement.
On Unix, profile directories are `0700`, while records and lock files are
`0600`. A malformed, incompatible, oversized, or over-permissive profile is
listed unavailable without preventing healthy siblings from loading.

Errors have stable codes, human-readable bounded messages, `retryable`, and an
optional typed scope. The initial codes are `invalid_request`, `not_found`,
`conflict`, `profile_locked`, `stale_revision`, `deadline_exceeded`,
`resource_limit`, `unsupported_operation`, `unsupported_capability`,
`permission_denied`, `target_crashed`, and `internal`. Engine errors are mapped
to these codes or retained in bounded diagnostic detail; they do not redefine
the public contract.

The current synthetic executable offers an NDJSON stream with one request and
one response object per line. A future product executable may also offer a
one-shot invocation, but both must carry the identical envelope. Diagnostics
go to stderr and cannot be required to interpret stdout.

The proposed one-shot projection is
`minicon-surf control --json <request.json`; stdout is the corresponding
success or failure envelope. The command is illustrative and does not exist
yet. Its operation names and object IDs cannot diverge from this schema.

## Version 0.0.2: the navigation slice

`0.0.2` is a **separate schema with its own version discriminator**
([`control-0.0.2.schema.json`](control-0.0.2.schema.json)), not a patch of
`0.0.1`. A host serves both concurrently: a request names its version exactly,
and that version decides which operation set it may use. `0.0.1` is unchanged,
byte for byte and in meaning; the two schemas differ only in their identity,
their version constant and three added operations.

| Operation | Arguments | Result boundary |
|---|---|---|
| `target.navigate` | `target`, `url` (absolute `http`/`https`, ≤ 2,000 bytes) | The target and its main frame keep their ids; the document generation increments, a new realm is minted, the revision advances and every earlier node reference is `stale_revision`. |
| `target.reload` | `target` | The same identity rules. A reload appends no history entry and does not move the position. |
| `target.traverse` | `target`, `delta` (non-zero, \|delta\| ≤ 8) | Refetches that entry's URL under the profile's current policy; an offset outside the window is `not_found`. |

Each result is `{kind: "navigation", target, frame, generation, realm,
revision, url, history}`, where `history` is `{position, length, can_go_back,
can_go_forward}` with the flags agreeing with the position. `target.inspect`
carries the same bounded `history` object.

**History is metadata only.** An entry holds the final canonical committed URL
and, at most, a host-minted identity for the CDP mapping. It holds no document,
realm, response body, form state, scroll position, script state, cookie or
storage snapshot, and no profile facts. Going back therefore refetches and
produces a fresh document: page state is not restored, and that loss is
recorded in the mapping rather than emulated. A redirect chain commits only the
final URL, a navigation from a back position truncates the forward entries, and
a failure mutates nothing at all.

Discovery is advisory. `session.inspect` may advertise
`supported_protocol_versions` and the exact `0.0.2` operation set, but a caller
that wants `0.0.2` **sends `0.0.2`**. No host infers a version from the shape
of a request, and no caller strips the version or a field and retries.

## Frames and realms

Four things change at different times and are never collapsed into one:

| Concept | Identity | Advances or ends when | Reference after that |
|---|---|---|---|
| target revision | integer per target | any navigation or observable document-state change | node references from before are `stale_revision` |
| frame identity | `frame_` id per browsing-context node | minted when the node is created; the main frame lives as long as its target; a child frame ends when it is removed or when its parent's document is replaced; ids are never reused within a host generation | an ended frame id is `not_found` with `frame` scope |
| document (navigation) generation | integer per frame, 1 for the first document | +1 on every same-frame navigation, i.e. every document replacement; the frame id survives it | reported in enumeration; it is not the target revision |
| realm identity | `realm_` id per (frame, document generation, world) | minted with its document; retired when that document is replaced, its world is destroyed or its frame ends; never reused | a retired realm id is `not_found` with `realm` scope |

Rules every host follows:

- Enumeration is bounded and only through the owning target: `target.inspect`
  lists `frames[]` (`frame`, `parent` or null, `generation`, `realm`, and the
  optional `url`) with the main frame first and at most `frame_limit`
  entries, and `realms[]` (`realm`, `frame`, `world`). Ids are opaque and
  encode nothing. `url` is additive and optional: when a host reports it, it
  is the final URL of the response that built that frame, after redirects. It
  is absent whenever a frame has no URL or its host does not track one, so a
  reader treats absence as normal rather than as an error and never infers
  from one host's reporting that another host reports it.
- Why a host did **not** build a frame is a host-level additive diagnostic,
  not a contract obligation: no host is required to report one, and the
  native route's `frames_skipped` — a bounded tally over a closed vocabulary
  of fixed reasons — is described normatively in `labs/native-dom/README.md`
  and its design record, not here.
- A `frame` or `realm` argument narrows an operation to that frame or asserts
  which realm the caller believes is live; it never widens it. `target.snapshot`
  accepts optional `frame` (default: the main frame) and optional `realm`
  (must be the frame's current realm), and its result names the `frame`,
  `realm` and `generation` it observed.
- A frame or realm id that does not belong to the named live target, whether
  it belongs to another target, has ended, or never existed, is refused with
  the same `not_found`, so nothing can enumerate one target's frames through
  another.
- Frames and realms are never capability owners (`kind_is_not_an_owner`);
  attenuation resolves the ownership chain from the `target` argument and
  covers every frame- or realm-narrowed operation.
- Navigation in the synthetic host is a click on a link node: it advances the
  target revision, increments the main frame's generation, mints a new main
  realm, ends the bounded child frame and its realm, and leaves every
  earlier node reference stale. There is no `target.navigate` in `0.0.1`;
  hosts that cannot navigate simply never replace a document.
- CDP projects frame identity as an adapter-scoped `Page.FrameId` that is
  one-to-one with a native frame while both live, qualified on the synthetic
  host through `Page.getFrameTree`; realm identity is not yet projected
  (`Runtime.ExecutionContextId`, context events), navigation events and
  document generation have no projection, child frames project flat with no
  nesting, and a projected frame's `url` is only as good as the host behind
  it: the native route is qualified to project each frame's own final URL,
  while the synthetic host has no child document to name and its adapter
  substitutes the target or court address, which stays a recorded loss. These are recorded losses, not
  approximations. Hosts that do not implement the optional `frame`/`realm`
  arguments fail closed with `invalid_request`, exactly as with
  `capability`: a caller that requires frame or realm narrowing MUST NOT
  drop the arguments and retry on that refusal, and there is no feature
  negotiation yet to discover support beforehand.

## Capability attenuation (optional envelope field)

A request may carry one optional `capability` object beside its six fixed
fields. It is an attenuation of the caller's existing authority for that one
request and never a grant: a request with a capability may do at most what
the same request without one may do, the host keeps no grant store, and the
profile/session/target ownership above stays the only authority.

Compatibility is stated precisely. Existing requests without the field are
wire-compatible: byte for byte the same request and the same response on
every `0.0.1` host. A request that carries the field is supported only on a
host that explicitly implements this extension; a host that does not
implements the strict parser and fails closed with `invalid_request`
(`request fields differ`). That refusal is a safety property, because an
attenuation must never be silently ignored. A caller that requires
attenuation MUST NOT remove the `capability` field and retry on that
refusal; it must surface the refusal. There is no feature negotiation yet:
a caller cannot ask a host whether it implements the extension before
sending, which is recorded as a gap for a later handshake, not simulated by
a fallback.

| Field | Meaning | Refusal when violated |
|---|---|---|
| `owner` | `{kind, id}` naming an object on the operation's ownership chain: the target, its session, or its profile (for session operations the session or its profile; for profile operations the profile) | `permission_denied` with `details.reason` `surface_is_not_an_owner`, `kind_is_not_an_owner` (frame, realm) or `owner_not_on_chain` (any object off the chain, including one that does not exist) |
| `scope` | the operations this attenuation allows; the request's operation must be listed | `permission_denied`, `operation_outside_scope` |
| `budget.deadline_ms` | the request's `deadline_ms` may not exceed it | `permission_denied`, `deadline_exceeds_budget` |
| `budget.result_bytes` | a snapshot's `max_bytes` may not exceed it and no result may be larger | `permission_denied`, `result_budget_exceeded` before execution; `resource_limit` if the produced result is larger |
| `audit` | `actor` (`[a-z0-9][a-z0-9_.-]{0,63}`) and a bounded `reason`, recorded with the decision in the host's bounded audit ledger | shape errors are `invalid_request` |

Operations without an owned object (`profile.create`, `profile.list`,
`session.list`, `target.list`, `memory.report`, `memory.trim`) cannot be
attenuated: a capability on them is `permission_denied` with
`operation_has_no_owner`. The synthetic host exposes the last 64 audit
records of a session through `session.inspect`; a record is diagnostics, not
authority. Examples: `target-snapshot-capability.*` (session-owned snapshot)
and `surface-owner-capability.*` (a request that locates the page only by its
surface and is refused).

## CDP projection and explicit losses

The checked machine-readable form is
[`cdp-mapping-0.0.1.json`](cdp-mapping-0.0.1.json).

| Native object/operation | Candidate CDP projection | Qualification boundary |
|---|---|---|
| target | `Target.targetId` | Intended 1:1 identity while exposed; G2 must prove native and CDP clients reach the same live target. |
| session attach | flattened `Target.attachToTarget` session | A CDP session is a client attachment, not the native browser session. |
| frame | `Page.FrameId` | Adapter-scoped mapping; navigation semantics require journey tests. |
| realm | `Runtime.ExecutionContextId` | Adapter-scoped and invalid after context destruction. |
| semantic snapshot | selected `DOM`/`Accessibility` methods | CDP node IDs are not native node references and cannot bypass revision checks. |
| structured action | selected `DOM`, `Runtime`, and `Input` calls | Only methods in the version matrix qualify; coordinate-only action is insufficient for the native Agent contract. |
| wait | events plus adapter-owned condition evaluation | No sleep-based success mapping. Deadline and cancellation remain native semantics. |
| profile | no exact standard mapping | Chromium browser contexts may approximate isolation but do not define MiniCon Surf profile identity, locking, policy, or persistence. |
| surface show/hide | no qualified CDP mapping | CDP target activation is not dynamic presentation attachment. |
| revision/node reference | no exact standard mapping | The adapter maintains mappings and returns explicit stale/unsupported failures; it never equates a `NodeId` with a native reference. |

The synthetic G2 court qualifies discovery/WebSocket plus seven selected
Target/DOM/Runtime methods against one shared target. External
Playwright/Puppeteer journeys and HTML-engine behavior remain D4 evidence.

The native profile projection has no CDP equivalent. Persistent mutations
require a live session holding the profile's advisory writer lock; cookie and
local-storage maps are bounded independently, and policy is part of the
profile record. The synthetic record is versioned and atomically replaced but
is deliberately unencrypted, so it accepts court values only—not credentials.

## Checked examples

The examples demonstrate a snapshot request and its successful revision-scoped
result, an action request and its stale-node failure, a session-attenuated
snapshot, a surface-located request refused as `permission_denied`, a
frame-scoped snapshot naming its realm and generation, and a retired-realm
request refused as `not_found`. Run:

```sh
python3 protocol/check_contract.py
```

The dependency-free checker validates byte/depth/collection bounds, envelope
shape, typed IDs, the optional capability shape, exact success/failure
exclusivity, echoed request identity, and node-reference revision
consistency. It also executes negative self-tests.
It complements the JSON Schema; it is not a general-purpose JSON Schema
implementation.

This reviewed vocabulary, schema, checked examples, and explicit CDP mapping
satisfy the paper-model minimum for G0. The separate synthetic journey now
satisfies G2's engine-neutral two-frontend minimum; it does not broaden this
paper contract into a general CDP compatibility claim.
