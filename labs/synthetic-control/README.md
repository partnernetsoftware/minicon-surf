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
per line and writes exactly one bounded JSON response per line. State lives for
the host process. Diagnostics never form part of stdout.

The first slice implements profile create/list, session open/list, target
open/list/inspect/close, semantic snapshot, revision-scoped button click,
revision wait, and memory report. Other reserved 0.0.1 operations return
`unsupported_operation`. The target is a deliberately tiny fixed semantic
document, not HTML and not a Web-compatibility claim.

The current host is sequential. `target.wait` can confirm an already-satisfied
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
```

## Evidence boundary and next step

Four library tests, one executable-reader test and one process-level stdio
journey cover revision invalidation, waits, capacity failure, memory owners,
schema-operation drift, parser bounds, oversized-line recovery and persistent
identity across requests. The process journey also closes its target and
observes the target owner count and logical accounted bytes decrease. The memory
report is explicitly a logical owned-capacity lower bound: it excludes map and
allocator overhead and is not RSS, private memory, PSS, or heap profiling.

This is native stdio control evidence only. It does not satisfy G2 until a CDP
adapter connects to this exact `ControlState` and an external journey proves
both transports see and mutate one target identity. It also does not satisfy
G1 until the process-tree court measures peak, post-close and comparative
behavior.
