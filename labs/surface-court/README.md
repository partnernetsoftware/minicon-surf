# surface-court: macOS native-surface candidate probes for G3

Standalone measurement lab for
[`labs/native-dom/surface-design-0.0.1.md`](../native-dom/surface-design-0.0.1.md).
Nothing here is linked into the native host; the probe criteria S1–S9 and
the stages were frozen before any candidate was built. macOS only.

`surface-probe` is one binary shape with the candidate chosen at build
time (`--features cocoa`: objc2 message sends, an `NSWindow` with an
`NSImageView` over a CPU bitmap; `--features winit-softbuffer`: winit's
event loop pumped from the outside, softbuffer painting a CPU buffer; no
feature is the plain control that only owns the buffer). `court.py` builds
each probe, then per run starts a fresh process, samples it headless, and
runs three rounds of show → pump → capture → hide, sampling footprint, RSS,
libmalloc in-use, the surface owner and its 256,000-byte backing, the
process's on-screen window numbers and the dyld images of interest after
every step, over the complete process tree. The window shows a colour-bar
test pattern; the capture reads back the own window by its window number
and never the desktop. One warm-up plus seven measured runs per candidate.

## Results (`evidence/surface-court-0.0.1.json`, 34 of 42; no candidate meets the frozen criteria)

| criterion | direct Cocoa (objc2) | winit + softbuffer |
|---|---|---|
| S1 owner and backing 0 → 1 (256,000) → 0 every round | yes | yes |
| S2 post-hide over headless, every round (≤ 262,144) | **9,879,648 / 10,158,176 / 10,190,944** (round medians) | **16,924,768 / 17,530,976 / 19,546,208** |
| S3 slope, post-hide round 3 − round 1 (≤ 65,536) | **294,912** (262,144 … 606,208) | **2,621,440** (638,976 … 4,603,904) |
| S4 libmalloc in-use after hide over headless (≤ 65,536) | **13,560,240** | **16,664,560** |
| S5 one process, no descendant | yes | yes |
| S6 no Metal or OpenGL image loaded | **no**: both are AppKit link dependencies, present at headless before any window | **no**, same |
| S7 a real window number on screen while shown, gone after hide | yes, 21 of 21 rounds | yes, 21 of 21 |
| S8 own-window pixels match the pattern | yes, 21 of 21 (bars classified by dominant channels; colour management shifts saturated values) | yes, 21 of 21 |
| S9 show / hide wall time (gate 1,000 ms, target 200 ms) | 9 ms / 1 ms medians (max 52 / 1.4) | 57 ms / 52 ms medians (max 153 / 60) |

Stage medians in bytes (physical footprint / RSS / libmalloc in-use):

| stage | plain control | direct Cocoa | winit + softbuffer |
|---|---|---|---|
| headless (after `NSApplication` init for the AppKit candidates) | 1,343,800 / 2,015,232 / 283,856 | 6,652,528 / 28,491,776 / 1,485,168 | 6,619,760 / 28,721,152 / 1,458,784 |
| shown, round 1 | 1,704,248 / 2,392,064 / 546,000 | 16,859,832 / 52,477,952 / 15,445,360 | 24,068,792 / 60,686,336 / 18,994,560 |
| post-hide, round 1 | 1,704,248 / 2,392,064 / 283,856 | 16,433,848 / 52,969,472 / 14,856,160 | 23,528,120 / 61,095,936 / 17,983,120 |
| shown, round 3 | 1,737,040 / 2,408,448 / 546,000 | 17,318,584 / 53,821,440 / 15,633,376 | 27,280,056 / 64,815,104 / 19,412,960 |
| post-hide, round 3 | 1,753,424 / 2,424,832 / 283,856 | 16,728,760 / 53,264,384 / 15,047,840 | 26,149,560 / 63,717,376 / 18,125,840 |
| complete-tree peak | 1,753,424 | 17,384,120 | 28,066,488 |

Builds and boundaries (release, `--locked --offline`): plain 554,576 bytes,
7 crates, libSystem only; direct Cocoa 587,680 bytes (+33,104), 13 crates,
links AppKit, Foundation, CoreFoundation, CoreGraphics and libobjc; winit +
softbuffer 1,207,952 bytes (+653,376), 42 crates, links AppKit,
ApplicationServices, Carbon, ColorSync, CoreServices, CoreVideo,
QuartzCore and the rest. Closures from the vendored sources: the objc2
stack is 305,911 Rust lines with 45,493 `unsafe` occurrences (generated
message-send bindings; every send is `unsafe`); winit + softbuffer adds
92,043 Rust lines and 1,547 `unsafe` on top of the same objc2 stack. Both
run as accessory applications (no Dock icon, no menu bar, no focus steal).
The Cocoa probe pumps `nextEventMatchingMask:` itself between commands and
hands no loop away; winit owns `NSApplication` and is driven through
`pump_app_events`, and it creates its window only from `resumed` or
`about_to_wait`, never synchronously. Every command runs inside an
autorelease pool: without one a stdio main loop never drains AppKit's
autoreleased objects and the per-round residual roughly doubles.

The WindowServer diagnostic was not captured (`pgrep` found no process by
that name from this session); GPU and compositor memory stay an
unattributed platform-service gap either way.

## What the numbers say

- Closing an AppKit window does not return the process to headless. The
  first window costs about 10 MB of footprint and 13.6 MB of heap on the
  direct path (17 MB and 16.7 MB on winit), and nearly all of it stays after
  the window, its view, image, bitmap and buffer are released: window-server
  connection, CoreAnimation and colour/font infrastructure that AppKit keeps
  for the process. `NSApplication` alone (headless) already costs 5.3 MB over
  the plain control.
- Each further show/hide round leaves about 0.15 MB behind on the direct
  path (median slope 294,912 over two rounds) and about 1.3 MB per round on
  winit; a host cycling surfaces would grow.
- Metal and OpenGL images are loaded as AppKit's own link dependencies
  before any window exists; the probe creates no Metal device, and the
  footprint numbers above already include whatever those images cost.
- The direct Cocoa path is the smaller and cleaner one on every axis that
  differs: 33 KB of binary against 653 KB, 13 crates against 42, 9 ms show
  and 1 ms hide against 57 and 52, one-fifth of the per-round residual,
  synchronous window creation, no event-loop ownership transfer, and the
  same pixel readback. Wry and Tauri were not measured: the ecosystem
  reference already records that a Wry view exists only through a window
  and dies with it, which fails the design's section 2 before any number.

## Recommendation (for cdx-k68's ruling, nothing merged)

No in-process AppKit surface can satisfy "post-hide footprint close to
headless": the cost is a one-time process-wide attachment to the window
server plus a per-cycle residual, not the window itself. The candidate
that follows from this evidence is a surface *process*: the host stays
headless-shaped and keeps the target, realm, profile and revision; `show`
spawns a small surface process built on the direct Cocoa path that owns
AppKit, receives the bounded pixel and semantic fixture over a pipe or
shared memory, and shows the window; `hide` ends that process, so the OS
reclaims everything and the host's post-hide footprint is exactly headless
by construction, with no slope. The complete-tree peak while shown would be
the host plus about 17 MB for the surface process (a full copy of AppKit's
one-time cost, as the helper-experiment discipline requires to be counted).
If cdx-k68 prefers an in-process surface, the frozen S2/S3/S4 cannot hold
and G3 would have to be re-framed around a "headed-capable" process state;
this lab does not recommend that.
