# Synthetic control lab

Status: `exploring`
Decision: `keep` as the engine-neutral control/lifecycle court; it is not a
browser engine or product crate.

## Hypothesis

The control 0.0.1 vocabulary should be implementable as a bounded Rust state
machine before any engine owns it. A single long-lived executable must preserve
profile/session/target identity across requests, reject stale node references,
observe conditions without caller sleeps, expose explicit logical memory
owners and refuse capacity growth beyond fixed limits.

## Scope

`minicon-surf-synthetic-control serve --stdio` accepts one UTF-8 JSON request
per line and writes exactly one bounded JSON response per line. Optional
`--cdp-port PORT --ready-file PATH` starts a loopback-only CDP edge against the
same `Arc<Mutex<ControlState>>`; the ready record is kept off stdout.

The first slice implements profile create/list/inspect/delete, bounded profile
storage and policy, session open/list/close, target open/list/inspect/close,
semantic snapshot, revision-scoped button click, bounded scroll, revision wait,
surface show/hide, session inspect (identity, owned targets and surfaces, and
the capability audit ledger), memory report, and macOS allocator trim. The
remaining reserved 0.0.1 operation (`target.screenshot`) returns
`unsupported_operation`. With `--profile-root PATH`, named persistent profiles
use bounded versioned JSON records and advisory single-writer locks; without an
explicit root, persistent creation fails. The target is a deliberately tiny
fixed semantic document, not HTML and not a Web-compatibility claim.

Native requests and one CDP connection may interleave at operation boundaries.
The current host handles only one CDP TCP connection at a time. `target.wait`
can confirm an already-satisfied
condition and produces a typed deadline for an unmet condition, but another
stdio request cannot mutate state while a wait is blocked. Multi-client event
wakeup belongs to the shared CLI/CDP host experiment and is not claimed here.

Hard limits are eight profiles, sixteen sessions, thirty-two targets, eight
surfaces, 128 nodes per target, a 65,536-byte synthetic presentation allocation
per surface, 32 cookie and 32 local-storage entries per profile, 64-byte keys,
1,024-byte values, 65,536 request bytes, 4,194,304 response bytes, depth 32 and
collection length 10,000. The NDJSON reader detects an oversized line while
streaming, drains it, emits one typed error and remains synchronized for the
next request.

Run:

```sh
cargo test --locked --manifest-path labs/synthetic-control/Cargo.toml
cargo run --locked --manifest-path labs/synthetic-control/Cargo.toml -- serve --stdio
python3 labs/synthetic-control/cdp-native-journey.py \
  --binary labs/synthetic-control/target/release/minicon-surf-synthetic-control
python3 labs/synthetic-control/profile-isolation-journey.py \
  --binary labs/synthetic-control/target/release/minicon-surf-synthetic-control
python3 labs/synthetic-control/capability-court.py \
  --binary labs/synthetic-control/target/release/minicon-surf-synthetic-control \
  --receipt labs/synthetic-control/evidence/synthetic-control-0.0.1-capability.json
python3 labs/synthetic-control/adapter-teardown-court.py \
  --binary labs/synthetic-control/target/release/minicon-surf-synthetic-control \
  --receipt labs/synthetic-control/evidence/synthetic-control-0.0.1-adapter-teardown.json
python3 labs/synthetic-control/frame-realm-court.py \
  --binary labs/synthetic-control/target/release/minicon-surf-synthetic-control \
  --receipt labs/synthetic-control/evidence/synthetic-control-0.0.1-frame-realm.json
labs/synthetic-control/run-lifecycle-memory-macos-arm64.sh
python3 labs/synthetic-control/staged-capacity-memory-macos-arm64.py \
  --binary labs/synthetic-control/target/release/minicon-surf-synthetic-control \
  --allocator-label system
```

The optional `mimalloc-lab` Cargo feature exists only for allocator comparison;
the default remains the system allocator. Build it into a separate target
directory so the two court binaries cannot be confused:

```sh
cargo build --release --locked --manifest-path labs/synthetic-control/Cargo.toml \
  --features mimalloc-lab --target-dir labs/synthetic-control/target/mimalloc
```

## Evidence boundary and next step

Ten library tests, one executable-reader test and one process-level stdio
journey cover revision invalidation, waits, capacity failure, memory owners,
schema-operation drift, parser bounds, oversized-line recovery and persistent
identity across requests. The process journey also closes its target and
observes the target owner count and logical accounted bytes decrease. The memory
report is explicitly a logical owned-capacity lower bound: it excludes map and
allocator overhead and is not RSS, private memory, PSS, or heap profiling.

The synthetic G2 court uses native stdio to create and snapshot a target, then
uses CDP discovery/WebSocket plus `Target.getTargets`, attach/detach,
`DOM.getDocument`, `DOM.querySelector`, `DOM.resolveNode`, and
`Runtime.callFunctionOn` to click it. Native stdio then observes the same target
at revision 1 and rejects its old reference as `stale_revision`. An unsupported
`Page.navigate` returns CDP `-32601`. This qualifies the narrow synthetic G2
mechanism, not HTML/CDP compatibility: the dependency-free named court client
is not Playwright/Puppeteer, the semantic target is not HTML, and only the
listed methods are supported. The endpoint is loopback-only and has no remote
authentication claim.

G1 remains open until process-tree evidence is strong enough to establish its
full gate.

The surface mechanics court keeps one CDP attachment alive while native stdio
performs three show/hide cycles. Target, native session, realm, revision 2,
scroll position 240, and the clicked semantic DOM survive every cycle. Show
adds exactly one bounded logical surface owner; hide removes it and returns the
logical ledger to its live-target baseline. This is not G3: the attachment is a
64 KiB synthetic presentation buffer, not a native window or renderer/GPU
surface.

The synthetic G4 court uses two concurrent host processes and three host
generations. Named persistent profiles `alpha` and `beta` retain distinct
cookie/local-storage values and network/permission policies after restart;
ephemeral `scratch` never appears in the new host. A competing writer receives
typed `profile_locked`, then succeeds after the owner closes. One corrupt
profile is listed as unavailable without blocking either healthy sibling.
Unix profile directories are `0700` and record/lock files `0600`. This passes
the deliberately small synthetic G4 minimum, not the product profile system:
values are unencrypted synthetic strings and must not hold real credentials;
cache, history, downloads, permission prompts, readonly and COW are absent.

The lifecycle memory court runs empty, live, headed, post-hide, and post-close
states through the same release binary and wrapper. Each mode
has one warmup and seven measured runs; order alternates, setup must finish
before the sampler's 300 ms warmup, and the following 1.2-second steady window
is sampled every 10 ms. Maximum observed setup was 3.008 ms.

Median steady-window complete-tree RSS was 1,966,080 bytes empty, 2,015,232
live, 2,031,616 headed, 2,031,616 post-hide, and 2,015,232 post-close. The
bounded surface therefore observed +16 KiB headed versus live, while post-hide
retained +16 KiB versus live despite logical surface ownership returning to
zero. Logical accounted state was 0, 634, 66,251, 634, and 279 bytes. This is a
useful retained-memory warning, not proof that presentation memory returns to
the OS.

The stronger same-process court drives one host through empty, one live target,
one headed target, post-hide, full capacity, post-release, and post-trim. Full
capacity is 8 profiles, 16 sessions, 32 targets, 8 surfaces, and 512 total
1,024-byte storage values; all five attempted overflows returned
`resource_limit` in every run. Across seven runs, median RSS grew from
1,966,080 to 2,949,120 bytes and Apple physical footprint from 1,048,888 to
2,015,568 bytes. After every logical owner returned to zero, both metrics still
retained 983,040 and 966,680 bytes above the initial state. The experimental
`memory.trim` called `malloc_zone_pressure_relief`, reported zero released
bytes in all seven runs, and changed neither median. This trim strategy is
therefore observed ineffective for the court, not presented as a solution.

A same-build-source allocator comparison then repeated that court seven times
per binary. The system allocator measured 1,966,080 bytes median empty RSS and
1,048,888 bytes empty Apple physical footprint; its maximum-capacity medians
were 2,981,888 and 2,048,336 bytes. Forced system trim changed neither
post-release median. `mimalloc` 0.1.52 with `libmimalloc-sys` 0.1.49 measured
2,539,520 bytes empty RSS and 1,622,400 bytes empty physical footprint, rising
to 3,555,328 and 2,572,672 bytes at capacity. Its forced collect reduced
post-release physical footprint by a 704,512-byte median, but RSS did not
change; post-trim physical footprint still remained 245,760 bytes above its
own empty state.

Verdict: **narrow/keep as an allocator-purge lab, not as the default**. It
proves that the court can distinguish an effective physical-footprint purge,
but its empty and maximum-capacity totals are worse than the system allocator
for this workload. Secure mode, browser-engine allocations, portability and
dependency/security maintenance are untested. The feature therefore cannot
support a product allocator decision or a G1 pass.

### Capability attenuation court (X9 micro-experiment ME1)

Hypothesis: the control 0.0.1 authority (profile → session → target) can
carry a per-request capability that names an owner, a scope, a deadline and
result budget, and an audit record, without becoming a second authority: a
request with a capability may do at most what the same request without one
may do, and a request that locates its object only by a surface or window, or
names an owner off the ownership chain, is a typed refusal.

Scope: one optional `capability` field on the request envelope
(`protocol/README.md`, "Capability attenuation"), parsed by the host as
strictly as the schema; `src/capability.rs` resolves the operation's
ownership chain from the host's own maps and refuses with `permission_denied`
plus a `details.reason` of `surface_is_not_an_owner`, `kind_is_not_an_owner`,
`owner_not_on_chain`, `operation_outside_scope`, `deadline_exceeds_budget`,
`result_budget_exceeded` or `operation_has_no_owner`; a produced result larger
than the budget is `resource_limit`. Every decision is appended to a 64-record
in-memory audit ledger that `session.inspect` exposes per session and that
nothing consults for authority. No plugin system, no grant store, no
Electron/Wry/Tauri dependency.

Reproduction: `capability-court.py` above; it validates every request with
`protocol/check_contract.py` before sending it, so the court cannot pass with
a request the paper contract rejects.

Evidence (`synthetic-control-0.0.1-capability` receipt, 33 of 33): requests
without a capability are unchanged and the three on-chain owners return the
identical snapshot; a surface-located snapshot and a surface-owned hide are
refused while the surface's target may hide it; a realm is not an owner;
another target, another session, another profile and a nonexistent object
are all `owner_not_on_chain` (existence is not leaked), and a session cannot
close another profile's session; scope, deadline and both result budgets
bind; `memory.report`, `profile.list` and `target.list` cannot be attenuated;
a full-scope capability cannot make `target.screenshot` work and a missing
principal fails with the same code as the plain request; a capability without
`audit` or with an extra field is `invalid_request`; the ledger lists actor,
reason, operation, owner and decision per session and stays at 64 records
after 70 attenuated requests; after `target.close` the attenuated request is
`not_found` and the memory owners are unchanged. Three unit tests cover the
parser, every refusal reason and the ledger bound, and the G2, G4 and the
native-dom 35-item network court pass unchanged with the extended checker.

Gaps: the target is synthetic; no engine host, CDP edge or embedder carries
a capability yet; budgets are per request, not cumulative; the ledger is
in-memory and per host; hosts that do not implement the field fail closed
with `invalid_request` (a safety property: attenuation is never silently
ignored), a caller that requires attenuation must not strip the field and
retry, and there is no feature negotiation to discover support beforehand.

Verdict: `keep` as the [X9] typed-capability mechanism on the synthetic
court. It moves no gate: G1, G3, P6 and G6 stay open.

### Adapter teardown ordering court (X9 micro-experiment ME2)

Hypothesis: an adapter, anything outside the control desk that keeps a
reference to a target between operations, can be given a reference that
cannot extend the target, its realm, its session or its profile, and the
host can tear a target down in a fixed, reported order (adapters, surfaces,
target, then the profile writer lock at `session.close`) while proving that
no adapter reference survived.

Scope: `src/adapter.rs`, safe Rust only. Each target owns the single strong
`Arc<TargetAnchor>` (names only: target, session and realm ids); an adapter
receives an `AdapterHandle` holding a `Weak`, upgrades it for one operation
and never stores the upgrade. `teardown_target` detaches every adapter that
points at the target, releases its surfaces, then checks
`Arc::strong_count == 1` before dropping the anchor; a surviving strong
reference is reported as `owner_reference_extended` and counted, while the
target leaves the ledger regardless, so a leak holds names, never an owner.
`target.close` and `session.close` return the teardown report;
`memory.report` gains an `adapters` owner (16 at most, accounted bytes) and
host-lifetime teardown counters. The loopback CDP edge is the first
adapter: each attached CDP session holds a weak handle, every native call
it makes is attenuated with a capability owned by that target (actor
`cdp.adapter`), and a command after the host tore the target down is a
typed `-32000` detachment. No adapter gains authority, no unsafe code, no
ecosystem dependency.

Reproduction: `adapter-teardown-court.py` above (native requests validated
by `protocol/check_contract.py`).

Evidence (`synthetic-control-0.0.1-adapter-teardown` receipt, 24 of 24):
attaching adds one adapter and exactly its accounted bytes, no target or
session owner; the adapter's calls are audited as attenuated to its target;
a native `target.close` while attached reports one adapter detached before
the drop with no owner reference extended, returns the ledger to the
session baseline, and turns the stale CDP session into a typed detachment,
after which it no longer exists; explicit detach releases the adapter and
keeps the target; `session.close` with an adapter and a surface attached
reports one adapter detached, one surface released (65,536 bytes) and no
extension, leaves every owner below the profile at zero, and the profile
lock is proven released because `profile.delete` succeeds; attaching to a
closed target is refused; the seventeenth adapter hits the capacity bound;
closing a target with sixteen adapters detaches all sixteen and every stale
session fails typed; the counters end at 3 targets closed, 18 adapters
detached, 0 owner references extended. Four unit tests add the paths the
CDP edge cannot reach: a handle that stored its upgraded reference is
detected at teardown (`owner_reference_extended` true, counter 1) while the
ledger still drops the target and its name does not resurrect it; the
session close order releases the lock only after the targets; the capacity
and `not_found` failures.

Gaps: one adapter kind; the leak detector observes a strong count at
teardown and cannot force a rogue adapter to release; logical bytes are the
host's accounted lower bound, not process memory; no embedder or plugin
adapter exists.

Verdict: `keep` as the [X9] adapter reference and teardown-order shape on
the synthetic court. No gate moves: G1, G3, P6 and G6 stay open.

### Frame and realm court (D4 design-first increment)

Hypothesis: control 0.0.1 can name frames and realms with engine-neutral
identity and lifetime rules that keep four concepts apart (target revision,
frame identity, document generation, realm identity), enumerate them per
target within a bound, refuse foreign, ended and unknown references alike,
survive a same-frame navigation with the frame id but not the realm, and be
observed identically through native stdio and the CDP edge, without a new
operation, without letting CDP ids become authority, and without making a
frame or realm a capability owner.

Scope: the rules live in `protocol/README.md` ("Frames and realms") and the
loss matrix in `protocol/cdp-mapping-0.0.1.json`; `target.snapshot` gains
optional `frame`/`realm` arguments and its result names the observed frame,
realm and generation; `target.inspect` lists bounded `frames[]`
(main first, `parent`, `generation`, `realm`) and `realms[]`; the synthetic
target has a main frame with heading, button and link plus one bounded
child frame ("Embedded court"), eight frames at most; a click on the link is
a same-frame navigation that advances the target revision, increments the
main frame's generation, mints a new main realm, ends the child frame with
its realm and leaves every earlier node reference stale; `memory.report`
counts frames and realms as owners; the CDP edge answers `Page.getFrameTree`
with adapter-scoped `cdp_frame_N` ids that are one-to-one with native frames
while both live and never the native ids. Realm identity, navigation events,
nested frames, isolated worlds and document generation are recorded losses
with no projection. No `target.navigate` exists; hosts that do not implement
the optional arguments fail closed with `invalid_request`.

Reproduction: `frame-realm-court.py` above (native requests validated by
`protocol/check_contract.py`, which now carries a frame-scoped snapshot and a
retired-realm refusal among its 12 examples and 15 negatives).

Evidence (`synthetic-control-0.0.1-frame-realm` receipt, 28 of 28): the
enumeration lists the main frame first with one bounded child, each frame
with its own generation and realm while the target carries the revision;
ids are disjoint across targets; a snapshot without a frame observes the
main frame and names frame, realm and generation, a child-frame snapshot
observes the child document; another target's frame and an unknown frame
are the same `not_found` with the same message, and a realm that is not the
frame's live realm is `not_found` with realm scope; a target-owned capability
allows a frame-narrowed snapshot, a frame or realm named as owner is
`kind_is_not_an_owner`, and another target's capability is
`owner_not_on_chain`; the CDP frame tree has the native shape with
adapter-scoped ids and another target's CDP session sees different ids; the
link click reports the surviving frame, generation 2, the retired realm, the
ended child and revision 1; afterwards the old node reference is
`stale_revision`, the retired realm `not_found` with a typed reason, the
ended child `not_found` with frame scope, the new document observable
through the surviving frame, CDP keeps the main id and drops the child, a
CDP handle from before fails typed and `DOM.getDocument` re-observes the new
document; a second navigation mints a third realm on the same frame; owners
count three frames and realms across two targets and zero after both close.
Two unit tests cover the same lifetimes and the owner rule in-process; the
capability (33/33), adapter (24/24), G2 and G4 courts pass unchanged.

Gaps: synthetic only, one child, one world, no nested frames, no navigation
from CDP, no realm projection; engine hosts (Servo, Lightpanda, native)
expose no frames yet and fail closed on the optional arguments; no external
client was run.

Verdict: `keep` as the D4 frame/realm rule set and its first court. It moves
no gate: G1, G3, P6 and G6 stay open, and D4 stays open until an engine host
and a named external client observe the same frames.

All memory receipts remain `incomplete`. The lifecycle modes are separate
fresh processes, while the staged companion supplies same-process capacity and
retention evidence. RSS is page-granular rather than private/PSS, Apple
physical footprint is platform-specific, and a two-node synthetic target
cannot establish browser memory efficiency. Together they strengthen the G1
court mechanics but do not pass G1 or the product memory gate.
