# Servo lab

Status: `exploring`
Decision: `keep` as a buildable Rust-engine candidate; no runtime, memory,
Agent, CDP, surface-detachment, or profile claim yet.

## Hypothesis

Servo may offer enough engine ownership to pursue both MiniCon Surf gates:
materially lower complete-process-tree memory and native Agent control. This
probe asks the narrower first question: can a public, pinned Servo embedding
release be consumed directly by an isolated Rust project on macOS arm64?

## Pin and official sources

- crates.io package: `servo = 0.5.0`
- upstream release tag: `v0.5.0`
- tag commit: `1d44e5dd6a8b64c02f9dbf7fcbdf4ebdd0740019`
- crates.io package checksum recorded by Cargo:
  `331e15df72165ca15b3945970c6870c4b7367be116ded058fda4f41190b265b8`
- minimum Rust version declared by the package: `1.88.0`

The dependency uses an exact version and `Cargo.lock` is tracked. Default
features are written explicitly so a later release cannot silently add a
feature to this experiment. `baked-in-resources`, `bundled_freetype`, and
`js_jit` retain three of Servo 0.5.0's four defaults. The `clipboard` default is
deliberately omitted because this compile probe does not exercise host
clipboard integration. Optional Bluetooth, gamepad, GStreamer, WebGPU, and
WebXR features are not enabled.

Only Servo-owned sources define the API interpretation:

- [Servo 0.5.0 embedding documentation](https://doc.servo.org/servo/)
- [Servo 0.5.0 `WebView` documentation](https://doc.servo.org/servo/webview/struct.WebView.html)
- [official Servo repository](https://github.com/servo/servo/tree/v0.5.0)
- [official minimal winit embedder](https://github.com/servo/servo/blob/v0.5.0/components/servo/examples/winit_minimal.rs)

## Scope and reproduction

The compile-only source names the public `ServoBuilder`, `Servo`,
`WebViewBuilder`, `WebView`, `RenderingContext`, `OffscreenRenderingContext`,
and `SoftwareRenderingContext` types in one checked signature. It does not
instantiate the engine and therefore is not W0 or W1 memory evidence.

On macOS arm64:

```sh
labs/servo/run-api-probe.sh
```

All generated build state stays in the ignored `labs/servo/target/` directory.
The reviewed initial cold `cargo check` completed on macOS arm64 with Rust
1.97.0 in 1 minute 54 seconds and produced approximately 1.5 GiB of target
state. Those are integration-cost observations, not runtime performance or
distribution-size measurements.

## Findings against product contracts

### Memory and surface ownership

The public API gives each `WebView` a supplied `RenderingContext`; Servo ships
window, offscreen, and software implementations, and embedders may implement
the trait. `WebView::paint` and context presentation are embedder-driven. This
is promising ownership evidence for offscreen rendering.

It is not evidence for MiniCon Surf's stronger G3 contract. In 0.5.0 the
context is supplied when `WebViewBuilder` creates the view. Public
`WebView::show` and `hide` only change visibility *within that same rendering
context*. This probe found no public operation that replaces or drops a live
view's rendering context while preserving its realm. W5 must therefore test a
real engine and must not call visibility toggling “detach.”

No engine was started, so empty startup, first-target cost, child processes,
memory-profiler coverage, target teardown, and post-close recovery are all
unmeasured. The 1.5 GiB build directory says nothing about RSS.

### Profiles

Servo 0.5.0 publicly exposes `SiteDataManager` and `NetworkManager` from a
`Servo` instance. Site data covers public/private cookies plus local and
session storage; network cache is explicitly separate. This is useful control
surface, but it is not a first-class named-profile abstraction. The compile
probe proves neither two-profile isolation nor single-writer locking, budgets,
history, downloads, permissions, copy-on-write, or a profile-to-target mapping.

### Agent control and CDP

The Rust API exposes navigation, JavaScript evaluation, screenshots, event
delegates, and view identity, so an Agent-native adapter appears technically
possible. The checked crate has Servo's own devtools server, but this lab found
no official claim that it implements Chrome DevTools Protocol. Servo devtools
must not be described as CDP. Stable semantic node references, condition
waits, structured snapshots, network lifecycle interception, CDP discovery,
and one-target CLI/CDP interoperability remain unproven.

## Exact limitations and next experiment

- `cargo check` validates public Rust types and the dependency graph only.
- It does not link or launch a runnable embedder.
- No court fixture was loaded and no process-tree sampler ran.
- Evidence applies only to the named release and macOS arm64 compile cell.
- The exact dependency graph contains 800 packages on this toolchain; feature
  reduction needs its own compile/runtime comparison rather than assumptions.

The next Servo experiment should adapt the official `winit_minimal` example
without changing Servo internals, load the hermetic W1 fixture in an offscreen
context, exit on a typed load/screenshot condition, and sample the complete
process tree. A second experiment must attempt context detachment while
preserving JavaScript state; visibility-only `hide` is an explicit failure for
that question.
