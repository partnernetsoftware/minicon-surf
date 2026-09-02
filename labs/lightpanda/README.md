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
because Lightpanda 0.4.0 exposes no in-process reporter through CDP, and a
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

## Per-cycle retention slope

On the shared slope court (1, 8 and 32 sequential cycles, seven runs each),
Lightpanda retained 5,292,032, 6,782,976 and 6,864,896 bytes above empty, a
5,783,352-byte warm-up intercept and a 39,062-byte-per-cycle slope, against
791,477 for Servo and 799,476 for Chrome. Lightpanda is the only measured
route whose per-cycle accumulation is small enough for a long single-target
Agent session; its one-target limit remains the constraint.

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
