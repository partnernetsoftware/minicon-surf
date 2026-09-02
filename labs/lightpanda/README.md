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
