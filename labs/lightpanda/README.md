# Lightpanda lab

Status: **active — first macOS arm64 W1 receipt observed, comparison incomplete**
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
```

The script emits a redaction-safe JSON object to stdout. It performs one warm-up
and seven measured executions. BSD `time -l` reports process maximum resident
set size; this is neither private memory nor live heap. The first workload also
checks that the semantic tree exposes the expected heading and button.

## Open gates

- Compare with a named same-machine headless Chrome build using a complete
  attributable process-tree sampler.
- Add W2 scripted mutation, CDP target reuse and multiple-page/session workloads.
- Establish whether Lightpanda remains single-process for every measured mode.
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
