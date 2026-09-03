# G3 surface process for the native route: architecture, IPC and court (frozen)

Status: `implemented, measured; narrow` (see section 7) (architecture, message
set, failure semantics, owners and the native G3 court pre-registered
before the implementation; amendment A1 keeps platform details out of
public results and A2 applies input at idle; follows
cdx-k68's verdict on the candidate court: a separate surface process on the
direct Cocoa path, the in-process candidates stay rejected at 34 of 42).
macOS only. Nothing here changes control 0.0.1: `surface.show {target}`
and `surface.hide {surface}` already exist in the vocabulary with the
synthetic host's shapes, human input never travels through a control
operation, and the only observable addition is a `scroll_y` field on
`target.inspect` and a `surfaces` owner in `memory.report`.

## 1. Ownership

- The host alone owns profile, session, target, frame, realm, revision, DOM
  and network. The surface process owns exactly one window and the pixels
  it was given. It never sees an object id, a URL, a cookie, a node
  reference or any authority object; it receives bounded presentation
  frames and returns bounded human input as coordinates and keys.
- `surface.show` spawns one child per surface; `surface.hide` ends it. A
  surface's end never closes, reloads or rebuilds the target, the frame,
  the realm, the revision, the CLI door or a CDP adapter. Teardown order at
  `target.close` and `session.close` stays adapters → surfaces → target.
- At most one surface per target (`conflict` otherwise) and at most 8 per
  host (`resource_limit`), as in the synthetic host.

## 2. Process

- A separate minimal binary, `native-dom-surface`, in `labs/native-dom-surface`
  (direct Cocoa through objc2 and objc2-app-kit, a CoreGraphics CPU bitmap in
  an `NSImageView`; no Wry, winit or softbuffer; no TLS, profile or engine
  code), so the host binary is not loaded twice. AppKit's own link
  dependencies (Metal, OpenGL, QuartzCore) and the WindowServer/compositor
  gap are recorded, not hidden.
- Spawned with `std::process::Command` from an absolute path given once at
  startup (`--surface-binary FILE`); no pre-exec closure, no cwd, no uid or
  gid, so the standard library uses `posix_spawnp` and never `fork`s the
  multi-threaded host (the same argument as the keychain helper).
- Accessory activation policy: no Dock icon, no menu bar, no focus steal.
  The window is titled by the host's surface id only; the child never logs.
- The child checks at start that no descriptor beyond 0, 1 and 2 is open
  and refuses to serve otherwise.

## 3. Transport and messages

Transport: the child's stdin (host → child) and stdout (child → host)
pipes, inherited descriptors created by the host; stderr is null. No
socket, no port, no file, no environment value carries data. Every message
is a 16-byte header followed by a bounded payload:

| offset | field |
|---|---|
| 0 | magic `MCSF` (the header is 20 bytes: the length field below makes it so) |
| 4 | version `1` |
| 5 | kind |
| 6 | flags (reserved, must be 0) |
| 7 | reserved (0) |
| 8 | generation (u32): the host's spawn generation; every message from either side carries it |
| 12 | sequence (u32): per direction, strictly increasing |
| 16 | payload length (u32), bounded per kind |

Host → child:

| kind | payload | bound |
|---|---|---|
| `HELLO` 1 | width u16, height u16, max_fps u8, queue_max u8, title length u8 + ASCII title | width ≤ 1024, height ≤ 768, max_fps ≤ 30, queue_max = 1, title ≤ 32 |
| `FRAME` 2 | frame sequence u32, width u16, height u16, format u8 (0 = BGRA8, stride = width × 4), pixels | pixels = width × height × 4 exactly, ≤ 3,145,728 |
| `CLOSE` 3 | none | – |

Child → host:

| kind | payload | bound |
|---|---|---|
| `READY` 16 | window number i64, screen x i32, screen y i32, content width u16, content height u16 | – |
| `FRAME_ACK` 17 | frame sequence u32 | – |
| `INPUT` 18 | input kind u8 (1 mouse down, 2 mouse up, 3 click, 4 scroll, 5 key), x u16, y u16, delta i16, key u16, modifiers u8 | ≤ 64 per second; the child drops beyond |
| `ERROR` 19 | code u16, text length u8 + ASCII text | text ≤ 128 |
| `CLOSED` 20 | none | – |

Rules: unknown kind, bad magic or version, non-zero flags, a payload over
its bound, a sequence that does not increase, or a generation other than
the current one fails closed (the host ends the child and reports
`internal` with `details.reason` `surface_protocol`; the child exits 65).
The host waits at most 2,000 ms for `READY` and 1,000 ms for each
`FRAME_ACK` and for `CLOSED` after `CLOSE`; a child that misses a deadline
is killed and reaped as failure cleanup and counted (`kills_total`,
`timeouts_total`), never as the normal path. Back-pressure: at most one
frame in flight; a newer frame replaces a queued one. The host keeps only
the latest frame (≤ 3,145,728 bytes) as the surface's accounted bytes.
Input events from a generation that is no longer current, or arriving
after `hide` began, are dropped and counted (`stale_events_dropped_total`).

## 4. The bounded semantic painter (host side)

The first slice paints the target's semantic snapshot, not its CSS: each
node is one row (role colour bar, an ASCII rendering of its name with a
built-in 5 × 7 bitmap font, indented by depth), links and buttons get their
own colours, the current `scroll_y` offsets the rows, and a hit map keeps
row → node reference for the frame. It is labelled `bounded-semantic-painter`
in every receipt and is not a layout or CSS renderer. Frame: 640 × 400
BGRA. Input mapping on the host: a click at (x, y) resolves through the hit
map to a node and runs the existing click path (the same revision rule as
`target.act`); a scroll delta moves `scroll_y` within 0 … 1,000,000 and
advances the revision by one (the synthetic host's rule; node references
taken before a scroll therefore go `stale_revision`, which the court
records); keys are recorded and ignored in this slice.

Input timing (A2): the surface reader thread delivers `INPUT` into the
host's multiplex loop as a third source next to the stdio door and the
CDP bridge. Every loop iteration handles, in this order, pending bridge
requests, then pending surface input events (each applied atomically as a
host-internal mutation of its target, FIFO per surface, dropped when its
generation is not current), then one stdio request; while an operation
runs nothing else is applied (operations stay serial and atomic), and
`target.wait` drains the same input queue between its polls. An idle host
therefore applies a click or scroll within its 1 ms loop period plus the
work itself, and the CLI or CDP observe the new revision afterwards.

## 5. Owners and reporting

`memory.report.owners.surfaces`: `objects`, `object_limit`, `bytes` (latest
frames kept by the host), `process` `{generation, live, spawns_total,
exits_clean_total, kills_total, timeouts_total, protocol_failures_total,
frames_sent_total, frames_acked_total, input_events_total,
stale_events_dropped_total}`. No pid, path or command line reaches a
receipt. `target.inspect` gains `surface` (id or null) and `scroll_y`.
The surface choice is never written to the profile record.

Public results (A1): `surface.show` answers only engine-neutral fields:
`kind` `surface`, `surface`, `target`, `state` `headed`, `presentation_bytes`,
`frame` `{width, height, format}`, `painter` `bounded-semantic-painter` and
`latency` `{ready_ms, first_frame_ms}`; `surface.hide` answers `kind`
`surface_hidden`, `surface`, `target`, `state` `headless`,
`released_presentation_bytes` and `teardown` `{exit: protocol|killed|gone,
reaped: bool, ms}`. No window number, screen coordinate, capture verdict,
hit map, pid or platform handle ever appears in a control response.

Court-only channel (A1, name corrected by cdx-k68): only when the host is
started with `--surface-court-file FILE` (a file the court places under
its mktemp directory) it appends there, mode 0600, one JSON line per
event with a monotonic millisecond stamp: `shown` (window number, content
rectangle in CoreGraphics screen coordinates, the painter's rows → node
map, and the own-window capture verdict taken by the host through
CoreGraphics on its own window number only), `frame_acked`,
`input_applied` (kind, revision, scroll), `hidden` and `child_exit`. The
file is removed when the host exits. The format is court-only, outside
the protocol and its schema; the receipt keeps sanitized conclusions only
(present or gone, pixels matched, latencies) and no path or handle.
Without the flag no file, no thread and no allocation exist for it.

## 6. The native G3 court (frozen, unexecuted until implemented)

`labs/native-dom/surface-court.py`, default allocator and the arena, the
host with `--surface-binary`, one CDP session held through the pinned
puppeteer-core driver of the CDP court:

1. headless: open the representative page, click its button (revision 2),
   attach the CDP session and read `Page.getFrameTree`;
2. three rounds of: `surface.show` → the court-only file names a window
   number and a capture verdict that matches the painter's frame → real
   input posted by the court through CoreGraphics at the window's screen
   position (a click on the row of the page's button and a scroll of 240),
   after which the court sends no control request at all and waits, with a
   deadline, for the host's `input_applied` event in the court-only log;
   only then does it read `target.inspect` (revision and `scroll_y`) and
   the CDP frame tree (unchanged, same frame id) → `surface.hide` → the child exits by protocol
   and is reaped within the deadline, `owners.surfaces` is 0 objects and 0
   bytes, no descendant remains → headless script, wait and network fetch
   still run → the next `show` finds target, frame, generation, realm,
   revision, `scroll_y`, profile and the CDP session unchanged except by
   the explicit actions;
3. failure modes, each leaving the target untouched: the court kills the
   child (`kill -9`) while shown → the host reports the surface gone and
   owners return to zero; the court stops the child (`SIGSTOP`) → `hide`
   times out, kills and reaps, counted; a second `show` on the same target
   → `conflict`; after `hide` a late input cannot apply (generation check;
   counted as stale when observed);
4. complete process tree stages: headless, spawn peak (the sampler's
   maximum while the child starts), shown steady, post-hide, post-reap,
   and the slope over the rounds; pre-registered: post-hide host footprint
   over headless ≤ 262,144 bytes every round, round 3 minus round 1 ≤
   65,536, libmalloc in-use over headless ≤ 65,536; recorded: spawn peak,
   shown steady, show-to-ready, first-frame, input and hide latencies, and
   the WindowServer diagnostic;
5. regressions unchanged: 27/27, 35/35, 62/62, 58/58, profile v1 80/82,
   HTTPS 74/74, Secure cookies 78/78 on the same binary.

If the court cannot post input (the OS refuses synthetic events without
Accessibility trust for the terminal), the input checks are recorded as
not verifiable rather than passed, and the run says so.

## 7. Result (recorded after the freeze)

`native-dom-control-0.0.2-surface`, 106 of 110 under both allocators:
every functional check holds (real window with a window number, own-window
capture 19 of 19, real click and scroll applied by the idle host before any
request in 0.3 to 13 ms, CDP session continuity, conflict on a duplicate
show, protocol exit in 8 to 12 ms with the child reaped, owners to zero,
target and realm continuity, kill and stop failure modes counted, no stale
input). The post-hide host footprint stays 1.2 to 2.6 MB over headless
(cap 262,144) with a slope over three rounds far above the 65,536 cap:
the default zone keeps one or two copies of the freed frame (section 8
replaces the first reading that blamed the spawn machinery for 1.13 MB).
Verdict: `narrow`; G3 stays open. The court-only channel is `--surface-court-file`
as corrected by cdx-k68.

## 8. Paired attribution of the post-hide retention (recorded)

`surface-attribution-court.py` (twelve cells: three frame sizes on the real
child, protocol and drain modes without AppKit, an exiting child; eleven
in-process stages per show and hide, sampled only with
`--surface-court-stages 1`): the retained bytes equal one or two copies of
the freed frame kept by the default zone plus small-block churn; they
scale with the frame size (1,245,184 / 458,752 / 262,144 bytes after round
1 for 1,024,000 / 262,144 / 65,536-byte frames) and do not depend on the
child at all; whether the zone keeps one or two copies varies between
runs of the same binary (in round 3 the painter receives a non-resident
block the zone already owns and both stay resident after hide); spawn
costs 0 to 33 KB, the reader thread's 16 KB stack is recovered at join,
in-use returns to within 7 KB of headless, `memory.trim` releases 0, and
the first non-releasing stage of every hide is `hide_entry`.
Candidate for ruling: a dedicated `mmap` region for the frame, `munmap`
at hide (section 8 of the lab README states the expected effect and the
criteria). No cap moves; the surface court stays 106 of 110, narrow.

## 9. The frame region (approved, implemented, measured)

`surface-frame-region-0.0.1.md` froze the candidate and its criteria
before the code. The frame is now one anonymous `mmap` region per surface
record (page-rounded, checked, refused above the protocol bound before
any child exists), painted in place, borrowed by the pipe write, unmapped
exactly once at drop on every path; `owners.surfaces.frame` reports it.
On this build the painter costs exactly the mapping and the drop returns
exactly the mapping in every round of every attribution cell, and the
surface court shows no 1 MB step in either of two runs: the frame's
retention and its one-or-two-copies variance are gone. The residual
small-block churn (about 0.2 to 0.5 MB after three rounds, mostly the
script realm's snapshot evaluation and control-plane JSON) still fails S2
from round 2 and S3 under both allocators, so the candidate is rejected
for G3 under the pre-registered outcomes and no cap moves; the region
stays as the frame's backing. Next: the read-only attribution of the
snapshot and `serde_json` churn before a second candidate.
