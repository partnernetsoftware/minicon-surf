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
and `memory.report`. It explicitly rejects the remaining reserved operations:
`session.inspect` and `target.screenshot`. A name outside this version
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
| `session.close` | `session` | Closes owned targets/surfaces and releases the persistent writer lock when its last session closes. |

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
result, plus an action request and its stale-node failure. Run:

```sh
python3 protocol/check_contract.py
```

The dependency-free checker validates byte/depth/collection bounds, envelope
shape, typed IDs, exact success/failure exclusivity, echoed request identity,
and node-reference revision consistency. It also executes negative self-tests.
It complements the JSON Schema; it is not a general-purpose JSON Schema
implementation.

This reviewed vocabulary, schema, checked examples, and explicit CDP mapping
satisfy the paper-model minimum for G0. The separate synthetic journey now
satisfies G2's engine-neutral two-frontend minimum; it does not broaden this
paper contract into a general CDP compatibility claim.
