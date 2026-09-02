# Servo lab

Status: `exploring`
Decision: `keep` as a running Rust-engine candidate with a material retained-
RSS risk; W1 and same-instance W3 software-rendered runtime are observed,
while comparative memory, native Agent control, CDP, surface-detachment, and
profile claims remain open.

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

The initial compile-only source names the public `ServoBuilder`, `Servo`,
`WebViewBuilder`, `WebView`, `RenderingContext`, `OffscreenRenderingContext`,
and `SoftwareRenderingContext` types in one checked signature. It does not
instantiate the engine and therefore is not W0 or W1 memory evidence.

The follow-on runtime executable creates an 800×600
`SoftwareRenderingContext`, loads W1 as a percent-encoded `data:` URL, waits
for `LoadStatus::Complete`, verifies the named heading/input/button/link through
`evaluate_javascript`, checks an 800×600 screenshot, holds the live target for
two seconds, and shuts Servo down. Each repetition has a fresh config directory
with `temporary_storage=true`.

On macOS arm64:

```sh
labs/servo/run-api-probe.sh
labs/servo/run-w1-runtime-macos-arm64.sh
labs/servo/run-w3-memory-macos-arm64.sh \
  --receipt labs/servo/evidence/macos-arm64-0.5.0-w3-memory-attribution.json
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

The runtime court started the engine seven measured times after one warmup.
All runs completed both typed observation conditions. The sampled tree
contained one process in every repetition; median peak summed RSS was
92,700,672 bytes and maximum was 92,880,896 bytes. The 1.5 GiB build directory
still says nothing about RSS.

That W1 result is not the memory-optimized claim. Summed RSS is not private/PSS;
the court uses software rendering, one fixture and one platform; no comparable
candidate ran through the same control contract in that execution; soak and
per-target growth remain unmeasured. Comparing it directly with the CDP court
would mix interfaces and rendering paths. W3 below adds retention evidence.

The W3 court keeps one `Servo` instance and one 800×600 software rendering
context alive while it builds, semantically verifies, and drops eight
sequential `WebView`s. This is a real close path: in the pinned source,
`WebViewInner::drop` sends `CloseWebView` and removes the paint webview; the
host continues spinning Servo's event loop during every measured stage.

The initial RSS-only receipt is preserved as historical evidence. The promoted
attribution court runs RSS and Servo's public memory reporter in separate
processes so reporter allocations cannot contaminate RSS windows. Across seven
measured runs per mode after one warmup, median complete-tree RSS was
44,220,416 bytes empty, 86,786,048 with the first target, 86,245,376 after its
close, 98,664,448 with the eighth target, and 96,256,000 after all eight
closes. The final closed state retained a 51,888,128-byte median above empty;
all stage samples observed one process. Dropping a WebView therefore does not
make its first-target resident cost promptly return to the empty baseline in
this court. This is **retention**, not a proven leak: engine caches, allocator
retention and asynchronously reclaimable state are not yet separated.

Servo's explicit owner reports tell a different and useful story. Explicit
reported bytes were 2,746,696 empty, 9,448,352 live, 2,751,088 after the first
close, 9,456,136 on the eighth target, and 2,759,160 after all closes. Every
run retained exactly 12,464 explicit reported bytes above empty. The live JS,
image, layout and display-list prefixes disappear from the largest explicit
reports after close. Meanwhile the separately reported system-heap reservation
rose from a 37,748,736-byte empty median to 62,914,560 bytes after eight closes.
Thus roughly 51.9 MB of retained RSS is not explained by Servo's explicit-
owner delta; allocator reservation or other unreported/reclaimable state is
now the leading hypothesis, not a still-live DOM/JS ownership claim.

Verdict remains `keep`, but the route is now conditioned on internal memory-
report attribution and an effective pressure/recovery experiment. A Rust API
and a successful software-rendered page do not waive that requirement.

### Profiles

Servo 0.5.0 publicly exposes `SiteDataManager` and `NetworkManager` from a
`Servo` instance. Site data covers public/private cookies plus local and
session storage; network cache is explicitly separate. This is useful control
surface, but it is not a first-class named-profile abstraction. The compile
probe proves neither two-profile isolation nor single-writer locking, budgets,
history, downloads, permissions, copy-on-write, or a profile-to-target mapping.

### Agent control and CDP

The runtime directly exercises JavaScript evaluation, screenshot and view
lifecycle callbacks, strengthening the case that an Agent-native adapter is
technically possible. These callbacks are not a native CLI, semantic snapshot,
or stable node contract. The checked crate has Servo's own devtools server, but
this lab found no official claim that it implements Chrome DevTools Protocol.
Servo devtools must not be described as CDP. Stable semantic node references, condition
waits, structured snapshots, network lifecycle interception, CDP discovery,
and one-target CLI/CDP interoperability remain unproven.

## Exact limitations and next experiment

- `cargo check` validates public Rust types and the dependency graph only.
- The API probe alone does not launch; the separate W1 runner does.
- W1/W3 load and render, but only through a software context and direct Rust API.
- Evidence applies only to the named release and macOS arm64 cells.
- The exact dependency graph contains 800 packages on this toolchain; feature
  reduction needs its own compile/runtime comparison rather than assumptions.

The next Servo memory experiment should consume its public
`create_memory_report` evidence now attributes the retained RSS away from
reported live target owners. The next experiment should test jemalloc purge or
an engine pressure action against the observed 51.9 MB retained RSS while
checking that explicit ownership remains closed. In parallel, its lifecycle still needs the shared native
target vocabulary and bounded JSON CLI. A separate experiment must attempt
context detachment while preserving JavaScript state; visibility-only `hide`
is an explicit failure for that question.
