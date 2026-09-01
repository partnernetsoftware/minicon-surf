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
revision wait, and memory report. Other reserved 0.0.1 operations return
`unsupported_operation`. The target is a deliberately tiny fixed semantic
document, not HTML and not a Web-compatibility claim.

Native requests and one CDP connection may interleave at operation boundaries.
The current host handles only one CDP TCP connection at a time. `target.wait`
can confirm an already-satisfied
condition and produces a typed deadline for an unmet condition, but another
stdio request cannot mutate state while a wait is blocked. Multi-client event
wakeup belongs to the shared CLI/CDP host experiment and is not claimed here.

Hard limits are eight profiles, sixteen sessions, thirty-two targets, 128
nodes per target, 65,536 request bytes, 4,194,304 response bytes, depth 32 and
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

Six library tests, one executable-reader test and one process-level stdio
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

The first lifecycle memory court runs empty, one-live-target, and
create-then-close states through the same release binary and wrapper. Each mode
has one warmup and seven measured runs; order alternates, setup must finish
before the sampler's 300 ms warmup, and the following 1.2-second steady window
is sampled every 10 ms. Maximum observed setup was 3.278 ms.

Median steady-window complete-tree RSS was 1,785,856 bytes empty, 1,802,240
bytes live, and 1,802,240 bytes post-close: an observed +16 KiB live delta and
+16 KiB retained delta at `ps` RSS granularity. Logical accounted state was 0,
418, and 107 bytes respectively. The target owner fell to zero post-close; the
remaining logical bytes belong to its profile/session.

The receipt remains `incomplete`. Modes are separate fresh processes rather
than marked stages in one host, RSS is page-granular and not private/PSS,
maximum-capacity RSS is unmeasured, and a two-node synthetic target cannot
establish browser memory efficiency. These results exercise the G1 court
mechanics but do not pass G1 or the product memory gate.
