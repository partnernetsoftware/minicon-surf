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

The first slice implements profile create/list, session open/list, target
open/list/inspect/close, semantic snapshot, revision-scoped button click,
bounded scroll, revision wait, surface show/hide, and memory report. Other
reserved 0.0.1 operations return
`unsupported_operation`. The target is a deliberately tiny fixed semantic
document, not HTML and not a Web-compatibility claim.

Native requests and one CDP connection may interleave at operation boundaries.
The current host handles only one CDP TCP connection at a time. `target.wait`
can confirm an already-satisfied
condition and produces a typed deadline for an unmet condition, but another
stdio request cannot mutate state while a wait is blocked. Multi-client event
wakeup belongs to the shared CLI/CDP host experiment and is not claimed here.

Hard limits are eight profiles, sixteen sessions, thirty-two targets, eight
surfaces, 128 nodes per target, a 65,536-byte synthetic presentation allocation
per surface, 65,536 request bytes, 4,194,304 response bytes, depth 32 and
collection length 10,000. The NDJSON reader detects an oversized line while
streaming, drains it, emits one typed error and remains synchronized for the
next request.

Run:

```sh
cargo test --locked --manifest-path labs/synthetic-control/Cargo.toml
cargo run --locked --manifest-path labs/synthetic-control/Cargo.toml -- serve --stdio
python3 labs/synthetic-control/cdp-native-journey.py \
  --binary labs/synthetic-control/target/release/minicon-surf-synthetic-control
labs/synthetic-control/run-lifecycle-memory-macos-arm64.sh
```

## Evidence boundary and next step

Eight library tests, one executable-reader test and one process-level stdio
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

The lifecycle memory court runs empty, live, headed, post-hide, and post-close
states through the same release binary and wrapper. Each mode
has one warmup and seven measured runs; order alternates, setup must finish
before the sampler's 300 ms warmup, and the following 1.2-second steady window
is sampled every 10 ms. Maximum observed setup was 3.132 ms.

Median steady-window complete-tree RSS was 1,900,544 bytes empty, 1,933,312
live, 1,949,696 headed, 1,966,080 post-hide, and 1,933,312 post-close. The
bounded surface therefore observed +16 KiB headed versus live, while post-hide
retained +32 KiB versus live despite logical surface ownership returning to
zero. Logical accounted state was 0, 462, 66,079, 462, and 107 bytes. This is a
useful retained-memory warning, not proof that presentation memory returns to
the OS.

The receipt remains `incomplete`. Modes are separate fresh processes rather
than marked stages in one host, RSS is page-granular and not private/PSS,
maximum-capacity RSS is unmeasured, and a two-node synthetic target cannot
establish browser memory efficiency. These results exercise the G1 court
mechanics but do not pass G1 or the product memory gate.
