# native-dom-surface: the native route's G3 surface process (macOS)

The separate, minimal window process the native host spawns for
`surface.show` and ends at `surface.hide`
([`labs/native-dom/surface-ipc-0.0.1.md`](../native-dom/surface-ipc-0.0.1.md)).
Direct Cocoa through objc2 and objc2-app-kit: one `NSWindow` with an
`NSImageView` over a CPU bitmap, an accessory activation policy (no Dock
icon, no menu bar, no focus steal), a floating window level, and an
autorelease pool around every loop turn. No Wry, winit or softbuffer; no
TLS, profile or engine code. AppKit's own link dependencies (Metal, OpenGL,
QuartzCore) come with it and are recorded, not hidden.

The child owns no authority object. It receives `HELLO`, `FRAME` and
`CLOSE` on its stdin pipe and answers `READY`, `FRAME_ACK`, `INPUT`,
`ERROR` and `CLOSED` on its stdout pipe, in the 20-byte-header, per-kind
bounded, generation- and sequence-stamped messages of the crate's library
(`src/lib.rs`, shared with the host as a path dependency without the
`window` feature, so the host's dependency tree gains no AppKit crate). It
refuses to serve if any descriptor beyond stdio is open, limits input to 64
events per second, and leaves through `_exit` once `CLOSED` is flushed,
because AppKit's exit-time handlers otherwise wait on a run loop that the
child never runs.

Build: `cargo build --release --locked --offline --features window`
(544,176 bytes on the recording cell). The host is given the absolute path
through `--surface-binary`.

Headless by default: in window mode the child exits 68 before creating
any AppKit object unless `MINICON_SURF_ALLOW_VISIBLE_COURT=1` is in its
environment, which the host hands down only under its own `--visual 1`
plus the same variable. The court-only modes `protocol`, `drain` and
`exit` never touch AppKit (they map neither AppKit nor CoreGraphics). A
window that is shown is ordered front without becoming key and never
activates the process.
