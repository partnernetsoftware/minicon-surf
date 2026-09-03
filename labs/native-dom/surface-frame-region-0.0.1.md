# Surface frame region 0.0.1: the pre-registered candidate for the G3 post-hide retention

Status: approved for implementation by cdx-k68 after the paired
attribution (`surface-ipc-0.0.1.md` §8); this file freezes the design,
the constraints and the pass criteria before any code. No cap moves.

## 1. What the attribution settled

The host's post-hide retention on the G3 surface prototype is one or two
copies of the freed frame kept by the default zone plus small-block churn.
It scales with the frame size, is independent of the child, and is not
the spawn machinery (0 to 33 KB). The frozen surface court stays 106 of
110: S2 (post-hide host footprint ≤ 262,144 over headless, every round)
and S3 (slope over three rounds ≤ 65,536) fail under both allocators.

## 2. The candidate

The frame is no longer a `Vec<u8>` in the default zone. Each surface
record owns a dedicated anonymous `mmap` region of exactly the frame's
page-rounded length; the painter writes into that mapping in place; the
pipe write borrows the mapping; `munmap` runs exactly once when the
record is dropped, so the frame's pages return to the kernel with the
child instead of staying in a zone cache. Nothing else changes: no global
allocator hack, no resident broker, no pre-warmed baseline, no change to
the IPC, the child, the painter's output or the public result shapes
beyond the additions in §3.3.

## 3. Constraints (from the ruling, binding)

### 3.1 Length and failure

- The region length is `width × height × 4` computed with checked
  arithmetic, rounded up to the page size with checked arithmetic, and
  refused when the pixel bytes exceed the protocol's `MAX_PIXEL_BYTES`
  (3 MiB) or the rounding overflows.
- A refused length is a typed `resource_limit`; an `mmap` failure is a
  typed `internal`. Either fails `surface.show` before any child exists:
  no half-created surface, no spawn, owners unchanged.
- No address and no length of a mapping enters a result, a receipt or the
  court-only log.
- The macOS implementation lives in one small module; every `unsafe`
  call is wrapped there; `Drop` performs `munmap` exactly once. Other
  platforms are `unsupported` (typed `unsupported_capability`); no claim
  of equal behaviour is made for them.

### 3.2 Ownership, borrowing and teardown order

- The painter writes directly into `&mut [u8]` over the mapping. No
  intermediate `Vec`, no encode copy, no clone of a frame anywhere in the
  host (the previous "newest wins" queue that copied a frame while one
  was in flight is replaced by a resend flag: after the acknowledgement
  the current mapping is written again).
- The pipe write borrows the slice for the duration of a synchronous
  `write_all`; the reader thread, the acknowledgement path and the child
  never hold a host pointer.
- Hide order: (1) the record leaves the host's map, so no new paint or
  input can reach it; (2) no frame is pending in host memory (the write
  is synchronous; the resend flag is cleared); (3) `CLOSE`, `CLOSED`,
  exit and reap, or kill and reap after the deadline; (4) the record is
  dropped and the mapping unmapped. Every other exit of a record (failed
  show, child gone, target close, session close, host exit, kill after
  timeout) ends in the same drop, so the mapping is unmapped and the
  owner's bytes return to 0 on every path.
- Unit tests prove: map and unmap counters move exactly once per region;
  an over-limit size is refused typed; a failed show (child exits at
  once), a protocol error (child writes garbage) and a `READY` timeout
  (child never answers) each end with the child reaped and the region
  unmapped; a second hide of the same surface is `not_found`; no double
  unmap and no leaked mapping.

### 3.3 Reporting

`memory.report` → `owners.surfaces` gains `frame`:
`reserved_bytes` (page-rounded mapped length of the live regions),
`touched_bytes` (resident pages of those regions, from `mincore`),
`live_bytes` (the frames' pixel bytes), `regions_mapped_total`,
`regions_unmapped_total`, `unmapped_bytes_total`, `backing`
(`anonymous_mmap`), and `host` with the process's `virtual_bytes` and
`physical_footprint_bytes`. `presentation_bytes` of `surface.show` and
`owners.surfaces.bytes` count the whole mapping while it exists and 0
after hide. A virtual reservation is never used to hide bytes: the
mapping is exactly the frame's pages.

## 4. Criteria (frozen; the court does not change)

The frozen surface court runs unchanged. The candidate is accepted for
the G3 cell only if all of these hold under both allocators:

- S2: post-hide host footprint ≤ 262,144 over headless in every round.
- S3: slope, round 3 − round 1, ≤ 65,536.
- The 106 mechanics checks stay green; the unit tests (40 + 2 plus the
  new ones) and every regression court (profile, journeys, network,
  frame-realm, CDP, HTTPS, Secure cookie) stay at their recorded counts.
- The headed complete-tree peak does not worsen.
- Show and hide latency stay within 10 percent of the recorded medians.
- Measurement: the frozen court (three rounds per allocator) at least
  twice on the same build to see whether the one-or-two-copies variance
  is gone, and the attribution court's product cell (one warm-up plus
  seven runs) for the stage deltas.

Outcomes: both pass → the G3 cell for the macOS bounded painter is
closed for this route only; no CSS or layout claim, and the WindowServer
gap stays. S2 passes and S3 fails → `partial repair`, rejected for G3,
no cap moves, followed by a read-only attribution of the snapshot and
`serde_json` churn before a second candidate. S2 fails → rejected.
