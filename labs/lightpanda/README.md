# Lightpanda lab

Status: **active — macOS arm64 W1/W2/W3 receipts observed, comparison incomplete**
Candidate role: **low-memory/Agent architecture reference and comparative
baseline; not a Rust SDK or headed engine candidate**

## Hypothesis

Lightpanda demonstrates how much memory a browser can avoid when it is designed
for Agents and true headless operation rather than hiding a graphical browser.
Its semantic dump, CDP, WebDriver BiDi, MCP and Agent surfaces can also inform
MiniCon Surf's native control vocabulary.

It cannot by itself satisfy MiniCon Surf: it is written in Zig, has no graphical
rendering surface, cannot prove dynamic headed/headless attachment, and is beta
software with partial Web API coverage.

## Pinned first artifact

- upstream: `lightpanda-io/browser`
- release: `0.4.0`
- asset: `lightpanda-aarch64-macos`
- SHA-256: `840547bb7b98743a3e32618a4d120ac4a75e7c3c2d227ecf5ce8d508ddc118b7`
- upstream release date: 2026-08-31

The runner fetches the release into ignored `target/labs/`, verifies the digest
before execution, disables telemetry and core dumps, and uses an encoded court
fixture without external network access.

## Reproduction

On macOS arm64, from repository root:

```bash
./labs/lightpanda/run-macos-arm64.sh
./labs/lightpanda/run-w2-macos-arm64.sh
./labs/court/run-target-retention-macos-arm64.sh
```

The script emits a redaction-safe JSON object to stdout. It performs one warm-up
and seven measured executions. BSD `time -l` reports process maximum resident
set size; this is neither private memory nor live heap. The first workload also
checks that the semantic tree exposes the expected heading and button.

The W2 runner additionally places a hard 15-second process deadline around
each fetch, verifies that page JavaScript replaced the fixture DOM, and runs a
dependency-free CDP 1.3 journey against a loopback-only server. The journey
creates and attaches one target, navigates it to the same hermetic data URL,
observes post-script state with `Runtime`, resolves the button through `DOM`,
mutates that node through its remote object, observes the mutation on the same
target, and closes the target. The server uses an ephemeral port, disables its
metrics endpoint, and is always reaped by the runner trap.

## Control 0.0.1 host

[`control-host.py`](control-host.py) maps the control `0.0.1` vocabulary onto
Lightpanda's CDP with the same in-page instrumentation the Servo host injects:
a `MutationObserver` revision counter, semantic snapshots, revision-scoped
click actions and `revision_at_least` waits. It takes the same arguments as
`servo-control` and reads the engine path from `MINICON_SURF_LIGHTPANDA`, so
the Servo lab's journey runs unchanged against it:

```sh
MINICON_SURF_LIGHTPANDA=target/labs/lightpanda/0.4.0/lightpanda-aarch64-macos \
python3 labs/servo/control-journey.py \
  --binary labs/lightpanda/control-host.py \
  --technology lightpanda --technology-version 0.4.0 \
  --artifact-sha256 840547bb7b98743a3e32618a4d120ac4a75e7c3c2d227ecf5ce8d508ddc118b7 \
  --receipt labs/lightpanda/evidence/lightpanda-control-0.0.1-journey.json
```

The receipt passes 27 of 27 checks: revision 0 snapshot of heading, label,
textbox with value, button and link; click through a compound reference;
`target.wait` observing revision ≥ 1 without a sleep; an unmet wait as
`deadline_exceeded`; the reused reference as `stale_revision` with both
revisions in details; the post-click snapshot with the mutated button and new
status text; `max_nodes` truncation; the W2 scripted fixture observed after
the first target closes; and typed refusals. Two facts differ from Servo and
are recorded rather than hidden: `memory.report` is `unsupported_capability`
on the Python host because Lightpanda 0.4.0 exposes no in-process reporter
through CDP (the Rust host now answers with attributable process metrics,
see below), and a
second concurrent target is a typed `resource_limit` (`TargetAlreadyLoaded`).
Target open took 2.035 ms and every other operation under 1 ms in the
recorded transcript, against 50.803 ms for Servo's target open.

This is the second real route to implement the shared control boundary; the
vocabulary is therefore engine-neutral for these two engines and this fixture
set. It is not a native CLI inside Lightpanda, and it inherits the same D4
limits as the Servo host.

## Process-per-target combination

Lightpanda 0.4.0 serves one live target per server, so the control host now
starts one Lightpanda process per target by default
(`MINICON_SURF_LIGHTPANDA_PER_TARGET=1`). The shared journey then passes
27 of 27 with a second concurrent target opening instead of `resource_limit`.
On the shared eight-cycle retention court the sampled tree (Python host plus
engines) measured 28,164,096 bytes empty, 60,866,560 with one target,
39,174,144 after eight closes and 279,855,104 with eight concurrent targets
in nine processes, against Servo's 137,101,312 in one process and Chrome's
2,205,646,848. Closing a target ends its process, so the 10,993,664 bytes
retained after eight closes live entirely in the Python host. A host-split
rerun that samples descendants separately confirms it: engine processes hold
29,638,656 bytes with one target, 0 after every close, and 237,027,328 bytes
for eight concurrent targets, while the Python host alone is 28,164,096 empty
and 39,272,448 after eight closes; a Rust host would remove most of that. This is the first measured
`combine` candidate rather than an engine claim: per-target processes buy
concurrency and bounded termination at roughly twice Servo's memory at eight
targets, and the engine itself still exposes no memory reporter.

## Rust host for the combination

[`host/`](host/) is the same process-per-target host written in Rust
(`lightpanda-control`, 783 KB, `serde_json`, `base64` and
`percent-encoding` only): it starts one Lightpanda server per target,
discovers `/json/version` over raw loopback HTTP, speaks CDP over its own
WebSocket client and injects the same in-page instrumentation. The shared
journey passes 27 of 27 with a second concurrent target opening; target open
takes 136.6 ms (process start plus discovery) and every other operation under
6 ms.

```sh
cargo build --release --locked --offline --manifest-path labs/lightpanda/host/Cargo.toml \
  --target-dir labs/lightpanda/host/target
MINICON_SURF_LIGHTPANDA=target/labs/lightpanda/0.4.0/lightpanda-aarch64-macos \
python3 labs/servo/control-journey.py --binary labs/lightpanda/host/target/release/lightpanda-control \
  --technology lightpanda --technology-version 0.4.0 \
  --artifact-sha256 840547bb7b98743a3e32618a4d120ac4a75e7c3c2d227ecf5ce8d508ddc118b7 \
  --receipt labs/lightpanda/evidence/lightpanda-control-0.0.1-journey-rust-host.json
labs/court/run-target-retention-macos-arm64.sh \
  --lightpanda-control labs/lightpanda/host/target/release/lightpanda-control \
  --candidates lightpanda_control --sequential-cycles 8 \
  --receipt labs/court/evidence/macos-arm64-target-retention-lightpanda-per-target-0.4.0-rust-host.json
```

On the shared eight-cycle court the tree measured 1,851,392 bytes empty,
31,719,424 with one target (2,048,000 host plus 29,671,424 engine),
2,572,288 after eight closes (720,896 retained, all host) and 239,878,144
with eight concurrent targets (2,785,280 host plus 237,092,864 engines).
By summed RSS the combination looked like 1.75× Servo at eight targets, but
that measure counts the 82 MB Lightpanda executable once per process. By the
kernel's physical footprint (the court now records both) the eight per-target
engines cost 76,043,448 bytes against Servo's 179,309,736 and Chrome's
867,831,560, one target costs 10,437,568 against Servo's 37,749,864, and
638,976 bytes remain after eight closes. The combination is therefore the
lowest-footprint multi-target route measured, with zero engine retention and
a process boundary per target.

## Attributable process metrics (X9 micro-experiment ME3)

Hypothesis: the process-per-target host can report an engine-neutral,
attributable process metric shape (each child named by an opaque ordinal and
its target, with pid, role, lifecycle state, resident set and physical
footprint) that reconciles with the shared court's independent process-tree
sampler at every stage, without a private API, without changing the
protocol, and without the report ever touching a child.

Scope: `host/src/procinfo.rs` reads public libproc interfaces only
(`proc_pidinfo` `PROC_PIDTBSDINFO`/`PROC_PIDTASKINFO`, `proc_pid_rusage`
`RUSAGE_INFO_V4`, `proc_listchildpids`). `memory.report`, previously
`unsupported_capability` on this host, now returns:

- `host` and `children[]`: `{child, target, role, state, pid,
  spawned_generation, identity_verified, metrics}` where `metrics` is
  `resident_bytes` (`pti_resident_size`, the value `ps` prints),
  `virtual_bytes`, `physical_footprint_bytes` (`ri_phys_footprint`) and its
  lifetime maximum; no command line, path, environment or process name is
  emitted;
- lifecycle state per child: `running`, `zombie`, `exited`, `pid_reused`
  (the start time recorded at spawn or the parent pid no longer match),
  `unreadable`, `exited_during_sample`; a child in any state other than
  running or zombie has null metrics and is listed under
  `tree.incomplete`, so `tree.complete` is false and the report cannot be
  mistaken for a full tree;
- `unattributed_descendants[]`: every process below the host that is not a
  target engine, found by walking `proc_listchildpids`, summed but owned by
  nothing (empty in every run);
- `generation`, advanced on every spawn and every reap, with each child's
  `spawned_generation`, so a report is a set at one generation and
  `target.close` returns the reaped child's ordinal, pid and generation;
- `tree.summed_resident_bytes` and `tree.summed_physical_footprint_bytes`
  named as sums, with `private_bytes.available = false` and the reason (a
  private versus shared split needs a task port the host does not request;
  resident sums double count shared pages). Nothing here is Electron's
  `ProcessMetric`; only the idea of a per-process row with a role is
  borrowed, and no field is claimed equivalent.

The report is read-only diagnostics: it never terminates, signals or waits
for a child beyond a non-blocking `try_wait`, and no operation consults it.

Reproduction:

```sh
python3 labs/lightpanda/process-metrics-court.py \
  --binary labs/lightpanda/host/target/release/lightpanda-control \
  --engine target/labs/lightpanda/0.4.0/lightpanda-aarch64-macos \
  --engine-sha256 840547bb7b98743a3e32618a4d120ac4a75e7c3c2d227ecf5ce8d508ddc118b7 \
  --receipt labs/lightpanda/evidence/lightpanda-control-0.0.1-process-metrics.json
```

The court fixes its rules before the run: at `empty`, `one_target`,
`eight_targets` and `post_close` it takes the shared court's sampler
(`ps` pid/ppid/rss plus `proc_pid_rusage` footprint) before and after
`memory.report`; the report must name exactly the sampler's pid set in both
samples, be complete, list as many running identity-verified children as
open targets and no unattributed descendant, and every per-process and
summed value must lie inside the bracket of the two samples widened by
max(1 MiB, 5%); the child closed first must be absent from both the report
and the sampler afterwards, the spawn/reap counters must read eight and
eight, private bytes must be declared unavailable, and no string may look
like a path, command line or environment value. Any violation is a recorded
finding and the receipt's status becomes `disagreement-recorded`.

Evidence (`lightpanda-control-0.0.1-process-metrics` receipt, one warm-up
plus seven runs): every stage agreed in all seven runs (28 of 28
reconciliations, zero findings). Medians, host report against the sampler's
before-sample, in bytes:

| stage | processes | report summed footprint | sampler summed footprint | report summed resident | sampler summed resident | host footprint | children footprint |
|---|---|---|---|---|---|---|---|
| empty | 1 | 1,048,888 | 1,048,888 | 1,867,776 | 1,867,776 | 1,048,888 | 0 |
| one target | 2 | 10,257,296 | 10,257,296 | 30,326,784 | 30,326,784 | 1,163,576 | 9,126,488 |
| eight targets | 9 | 74,470,440 | 74,454,056 | 228,917,248 | 228,900,864 | 1,622,328 | 72,831,728 |
| post-close | 1 | 1,671,480 | 1,671,480 | 2,539,520 | 2,539,520 | 1,671,480 | 0 |

The largest gap between the report and the sample taken just before it was
49,152 bytes of footprint (three pages) at eight targets; pid sets matched
in every sample, every child was `running` with its identity verified, no
unattributed descendant appeared, the child closed first was absent from
both the report and the sampler in all seven runs, and the counters read
eight spawned and eight reaped. The shared retention court rerun on the
same binary measured 1,065,272 bytes footprint empty, 10,437,544 with one
target, 1,720,632 after eight closes (655,360 retained) and 76,109,008 with
eight concurrent targets, within noise of the recorded receipt; the 27-item
journey passes 27 of 27 with `memory.report` now answered.

Gaps: one machine, one static fixture, eight targets; agreement is within a
fixed bracket, not identity, because the samples are taken at different
instants; private and shared bytes are unavailable to both sides; the engine
has no in-process owner ledger, so attribution stops at the process; the
`pid_reused`, `unreadable` and `exited_during_sample` states are defined and
exercised by construction but were not observed on this court.

Verdict: `keep` as the [X9] process-metric shape for process-per-target
hosts. It changes no gate: the numbers are the same measurements the shared
court already takes, now attributable per target from inside the host; G1,
G3, P6 and G6 stay open.

## Per-cycle retention slope

On the shared slope court (1, 8, 32 and 128 sequential cycles, seven runs
each), Lightpanda retained 5,292,032, 6,782,976, 6,864,896 and 6,963,200
bytes above empty. The growth stops after the first few cycles: the 128-cycle
soak sits 3.8 MB below the linear prediction from the first three points, so
retention is a bounded plateau of about 7 MB rather than a per-cycle term.
Servo's retention on the same court is linear to 128 cycles (130,613,248
bytes) and Chrome's rose at a similar per-cycle rate over 1/8/32. Lightpanda
is therefore the only measured route whose retention is bounded for a long
single-target Agent session; its one-target-per-server limit is answered by
the process-per-target combination above.

## Open gates

- Extend the named same-machine Chrome comparison beyond one small semantic
  fixture and summed RSS; private/PSS and representative pages remain open.
- Add a multiple-concurrent-page route or explicitly accept one target per
  server; the W3 capacity probe observed `TargetAlreadyLoaded` on every second
  concurrent create. Qualify `Input` plus a named external CDP client; the W2
  journey currently covers Target/Page/Runtime/DOM directly.
- Establish whether Lightpanda remains single-process outside the measured
  W3 sequential-target and one-target-capacity modes.
- Reproduce on Linux x86_64 and arm64; no Windows-native artifact exists in the
  pinned release.
- Review implementation and license boundaries before reusing any source-level
  idea.

## Current verdict

`keep` as an architecture reference and measurement target. No SDK or product
engine adoption decision has been made. The first reviewed receipt is
[`evidence/macos-arm64-0.4.0-w1.json`](evidence/macos-arm64-0.4.0-w1.json):
seven process-maximum-RSS samples have a 25,575,424-byte median and a
25,690,112-byte maximum. These are root-process, single-document facts, not a
Chrome comparison or a MiniCon Surf memory claim.

The W2 receipt is
[`evidence/macos-arm64-0.4.0-w2.json`](evidence/macos-arm64-0.4.0-w2.json).
It proves scripted DOM observation and one real CDP target/action journey, but
remains `incomplete` for the same process-tree, baseline, and platform reasons.
Its seven root-process maximum-RSS samples have a 27,131,904-byte median and a
27,721,728-byte maximum; child processes were not sampled or excluded, so these
numbers are not complete-process-tree evidence.

The shared W3 receipt is
[`../court/evidence/macos-arm64-target-retention-lightpanda-0.4.0-vs-chrome-152.0.7977.75.json`](../court/evidence/macos-arm64-target-retention-lightpanda-0.4.0-vs-chrome-152.0.7977.75.json).
Across seven same-server runs, Lightpanda's median complete-tree RSS was
22,626,304 bytes empty, 27,901,952 with the first live target, and 29,442,048
after eight sequential targets were closed. The median post-eight-close minus
empty delta was 6,766,592 bytes. It remained one process, but every concurrent
capacity probe supported one target and rejected the second with
`TargetAlreadyLoaded`. Verdict remains `keep` as a low-memory/Agent reference,
now explicitly **narrowed to a single concurrent target for 0.4.0**.
