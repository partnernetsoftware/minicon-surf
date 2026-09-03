# G3 native surface for the native route: design and probe criteria before code

Status: `measured` (probe criteria, candidate matrix and court stages
pre-registered before any candidate probe was built; results in
`labs/surface-court/README.md`; nothing here is in the native host,
pending cdx-k68's ruling). macOS only.

## 1. Question

G3 asks whether headed and headless are runtime states of one live target:
a presentation surface that attaches to the existing target, shows a real
OS window a human can see, detaches by releasing the window, its view and
its backing store, and leaves the target, frame, realm, profile, revision
and scroll untouched while scripts, waits and network keep running, and
can attach again. The synthetic court proved the ownership mechanics with a
65,536-byte buffer; it could not prove GUI resource teardown. This design
chooses the smallest macOS path that can, CPU-first and low-memory, and
freezes how it will be judged.

## 2. What does not count

- `setVisible(false)`, `orderOut:` or any hidden-but-alive window is not
  detachment: the window, view and backing store must be released and the
  surface owner must return to zero.
- Closing a window must never recreate the target or the realm; a page
  lifetime bound to a window is the Wry/Tauri shape the ecosystem reference
  already rejected (X9: Wry builds a view only from a window and drops the
  webview with it; visibility is a view attribute, not detachment).
- A candidate that pulls Metal, OpenGL or a large GPU cache into the host
  process is narrowed on the spot; the pixels are a CPU buffer handed to the
  window server, nothing more.

## 3. Candidate matrix (frozen axes)

| axis | A: direct Cocoa (objc2 0.6 + objc2-app-kit 0.3, CoreGraphics CPU bitmap) | B: winit 0.30 + softbuffer 0.4 (CPU buffer) | C: tao 0.35 + softbuffer | D: Wry 0.55 / Tauri 2.11 |
|---|---|---|---|---|
| language boundary | Rust over Objective-C message sends (`unsafe` at each send, generated bindings) | Rust; winit's macOS backend holds the ObjC boundary | same as B (Tauri's winit fork) | Rust over WebKit and a window |
| event-loop ownership | the host pumps `nextEventMatchingMask:` itself between control operations; no loop is handed away | winit owns `NSApplication`; `pump_app_events` lets an outer loop drive it | same | Tauri/Wry own the loop |
| window/view/backing teardown | `NSWindow close` with `releasedWhenClosed`, view and bitmap dropped explicitly | window and softbuffer surface dropped; winit releases the `NSWindow` | same | webview dropped with its window (X9 objection) |
| GPU / WindowServer | CPU bitmap in an `NSImageView`; WindowServer composites | softbuffer paints a CPU buffer into a `CALayer` | same | WebKit's GPU process and compositor |
| measured here | yes | yes | recorded as B's shape; not measured separately | not measured: rejected on the X9 evidence |

Recorded per measured candidate: Rust lines and `unsafe` occurrences of
the closure, crates added, release binary bytes and delta against a plain
probe, dynamic libraries linked, dyld images loaded at each stage (with
Metal, OpenGL, QuartzCore and CoreGraphics named), footprint and RSS over
the complete process tree at each stage, surface owner count and backing
bytes, show and hide wall time, whether the window's pixels could be read
back (own window only, never the desktop), and the WindowServer footprint
before and after as a diagnostic that is not attributed to the host.

## 4. Probe criteria (pre-registered)

The probe is a fresh process per run, one warm-up plus seven measured
runs, driven over stdio: `headless` → three rounds of `show` → `pump` →
`capture` → `hide` → sample. All must hold for a candidate to be eligible.

| criterion | limit |
|---|---|
| S1 surface owner and backing bytes go 0 → 1 (exactly the 320 × 200 × 4 = 256,000-byte buffer plus the owner record) → 0 in every round | exact |
| S2 post-hide footprint over the headless footprint of the same process, every round | ≤ 262,144 bytes |
| S3 no slope: post-hide footprint of round 3 minus round 1 | ≤ 65,536 bytes |
| S4 libmalloc in-use after hide over headless in-use | ≤ 65,536 bytes |
| S5 complete process tree: one process, no descendant at any stage | exact |
| S6 no Metal or OpenGL image loaded into the process by the candidate at any stage | exact |
| S7 the window is a real OS window: it has a window number, is on screen while shown, and is gone after hide (`windowNumbersWithOptions`) | exact |
| S8 the fixture pixels are read back from the own window while shown (a colour-bar test pattern, no text, never the desktop) | observed, or recorded as not verifiable when the OS refuses capture without permission |
| S9 show and hide each complete within 200 ms of wall time | recorded, gate at 1,000 ms |

Recorded, not gated: headless and shown footprint, RSS, the WindowServer
diagnostic, binary and dependency deltas, `unsafe` counts, activation
policy and whether the process appears in the Dock or steals focus.

## 5. The native G3 court (frozen, unexecuted)

When a candidate is approved, `labs/native-dom/surface-court.py` will
observe the same target: open a target on the representative page, click
and scroll it (revision 2, scroll 240), hold one CDP session; then three
rounds of `surface.show` (a real window with the bounded pixel fixture and
the page's semantic snapshot as its content) → the page runs a script, a
wait and a network fetch while shown → `surface.hide` → the same script,
wait and fetch while hidden; after every hide the target, frame,
generation, realm, revision (advanced only by the explicit actions), scroll,
profile and the CDP session are unchanged; `memory.report.owners.surfaces`
goes 0 → 1 → 0 with the backing bytes; post-hide footprint is within the
S2 cap of headless and shows no slope over the rounds; the host stays one
process; window numbers appear and disappear; the WindowServer diagnostic
is recorded. Default allocator and the arena. Regressions unchanged.

## 7. Result of the candidate court (recorded after the freeze)

`labs/surface-court/evidence/surface-court-0.0.1.json`, 34 of 42: both
measured candidates meet S1, S5, S7, S8 and S9 and fail S2, S3, S4 and S6.
The first AppKit window costs about 10 MB of footprint and 13.6 MB of heap
on the direct Cocoa path (17 MB and 16.7 MB on winit + softbuffer) and
nearly all of it stays after the window, view and backing store are
released; each further round leaves about 0.15 MB (Cocoa) or 1.3 MB (winit)
behind; Metal and OpenGL images are AppKit's link dependencies, present at
headless. The direct Cocoa path is the smaller one on every differing axis
(33 KB of binary against 653 KB, 13 crates against 42, 9 ms show and 1 ms
hide against 57 and 52, a fifth of the residual). Recommendation, subject
to ruling: a surface process built on the direct Cocoa path, spawned by
`show` and ended by `hide`, so the host's post-hide footprint is headless
by construction; the frozen S2–S4 cannot hold for any in-process AppKit
surface. Two mechanism amendments are recorded: every probe command runs in
an autorelease pool (a stdio loop never drains AppKit's pools), and
captured bars are classified by dominant channels because colour
management shifts saturated values. The WindowServer diagnostic was not
captured in this run.

## 6. Out of scope

Input events from the window into the page, resizing, multiple surfaces per
target, non-macOS platforms, and any GPU path.
