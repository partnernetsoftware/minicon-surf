# Servo lab

Status: `exploring`
Decision: `narrow` to a bounded-session Rust-engine candidate. W1 and
same-instance W3 software-rendered runtime are observed, but the W3
attribution-closure court measures linear per-cycle accumulation of live
system-heap bytes that no allocator pressure action recovers, plus a roughly
290 MB graphics-owned footprint spike at every WebView close. Comparative
memory, native Agent control, CDP, surface-detachment, and profile claims
remain open.

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
- Servo allocator backend on this platform: `tikv-jemalloc-sys`
  `0.6.1+5.3.0-1-ge13ca993e8ccb9ba9847cc330696e02839f328f7`, checksum
  `cd8aa5b2ab86a2cefa406d889139c162cbb230092f7d1d7cbc1716405d852a3b`
- minimum Rust version declared by the package: `1.88.0`

Allocator ownership facts verified in the pinned sources and this lab's build
log, which every memory reading below depends on:

- `servo-allocator` installs `tikv-jemallocator` as the Rust global allocator,
  built with `--with-jemalloc-prefix=_rjem_`; jemalloc therefore never replaces
  Apple libmalloc.
- `mozjs_sys` builds SpiderMonkey with `--disable-jemalloc`, so the JavaScript
  malloc heap lives in libmalloc, as do swgl, bundled FreeType and HarfBuzz.
  Servo's memory reporter labels that heap `system-heap-*` via
  `malloc_zone_statistics`.
- `tikv-jemalloc-sys` defaults to `--disable-stats`; until this lab enabled the
  `stats` feature, `servo-allocator::heap_reports()` returned nothing and no
  receipt contained a jemalloc figure.

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
- [official tikv-jemallocator project](https://github.com/tikv/jemallocator)

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
  --receipt labs/servo/evidence/macos-arm64-0.5.0-w3-attribution-closure.json
```

The W3 runtime takes `FIXTURE CONFIG_DIRECTORY STAGE_MS CYCLES MODE`, where
`MODE` is `{rss|internal}-{control|jemalloc-purge|libmalloc-relief|both}`.
Earlier receipts (`w3-memory`, `w3-memory-attribution`, `w3-jemalloc-purge`)
were produced by the previous eight-cycle protocol and are preserved as
historical evidence; the current driver supersedes them.

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

The promoted purge court adds a sixth stage after all eight closes. Independent
fresh-process branches either wait for the same interval or invoke the linked
jemalloc 5.3.0 lineage's `arena.4096.purge` (`4096` is
`MALLCTL_ARENAS_ALL`). Every mallctl returned success. Across seven runs,
control RSS changed by exactly zero in all runs; purge reduced RSS in all seven
by 1,638,400 bytes at the median. Purge explicit-owner delta was zero, so the
closed ownership state stayed stable. However, post-purge RSS still retained a
49,692,672-byte median above empty: purge recovered only about 3.193% of the
purge branch's 51,314,688-byte post-close retention.

Verdict on that court: **jemalloc all-arena purge is effective but
insufficient**, and the attribution-closure court below shows it was also
aimed at the wrong heap.

#### Attribution-closure and per-cycle-slope court

The promoted court (`w3-attribution-closure` receipt) enables jemalloc `stats`,
reads `stats.resident/allocated/mapped` and libmalloc `malloc_zone_statistics`
in-process at the end of every stage, samples complete-tree RSS and Apple
physical footprint (`proc_pid_rusage` `ri_phys_footprint`, plus the kernel's
lifetime maximum) from outside after a 3 s settle, and counts threads. Control
cells run 1, 8 and 32 build/observe/drop cycles; pressure cells run 8 cycles
followed by jemalloc purge, `malloc_zone_pressure_relief(NULL, 0)`, or both.
Each cell has one warmup and seven measured runs; one process was observed in
every sample.

Settled retention after all closes minus empty, medians:

| cycles | RSS | physical footprint | jemalloc resident | jemalloc allocated | libmalloc reserved | libmalloc in use | threads |
|---|---|---|---|---|---|---|---|
| 1 | 42,696,704 | 34,881,800 | 13,238,272 | 2,309,856 | 8,388,608 | 1,548,096 | +11 |
| 8 | 52,903,936 | 45,122,472 | 16,859,136 | 2,523,264 | 16,777,216 | 5,317,968 | +11 |
| 32 | 70,549,504 | 64,080,848 | 18,268,160 | 3,718,280 | 29,360,128 | 18,282,912 | +11 |

Least-squares over all 21 control runs gives `retained = intercept + slope ×
cycles` with a 35,757,687-byte footprint intercept (44,037,922 for RSS) and a
889,348-byte-per-cycle footprint slope (765,990 for RSS). Of that slope,
538,212 bytes per cycle are libmalloc bytes still **in use**, 544,783 are
libmalloc reserved, and 142,683 are jemalloc resident; jemalloc allocated grows
by roughly 45 KB per cycle. The eleven extra threads appear with the first
target and never exit, but do not grow with cycles.

Three readings follow. First, the earlier 51.9 MB headline conflated a
one-time warm-up cost of about 36 MB footprint with per-cycle growth; the
warm-up is not something a purge should recover. Second, the per-cycle growth
is not allocator retention: live libmalloc bytes rise linearly, which is the
signature of C/C++ allocations (SpiderMonkey is the largest system-heap owner
while a target is live) that are never freed after a WebView closes. Third,
attribution does not close: jemalloc resident plus libmalloc reserved explain
a 0.624/0.746/0.739 median share of settled footprint retention at 1/8/32
cycles (0.510/0.636/0.684 of RSS). The remaining quarter is owned by neither
allocator; `vmmap` at the settled state shows graphics-owned memory, thread
stacks and other direct mappings as the candidates.

Pressure actions after eight closes, medians of `post_action − post_all_closes`:

| action | RSS | physical footprint | jemalloc resident | libmalloc released |
|---|---|---|---|---|
| control wait | +16,384 | 0 | 0 | n/a |
| jemalloc purge | −1,703,936 | −1,523,712 | −3,751,936 | n/a |
| libmalloc relief | +16,384 | 0 | 0 | 0 bytes in all seven runs |
| purge then relief | −1,622,016 | −1,638,400 | −3,719,168 | 0 bytes in all seven runs |

No action brings the settled footprint back within 4 MiB of empty plus the
fitted warm-up (61.2 MB): the four post-action medians are 66.4, 64.9, 66.8
and 65.8 MB. `malloc_zone_pressure_relief` cannot help because the libmalloc
growth is in-use bytes rather than free reserved space.

The court also measured peaks. The kernel's lifetime maximum footprint reached
315,278,344 bytes after a single close, 330,729,080 after eight and
357,076,232 after thirty-two, against a 21.2 MB empty and 39.3 MB live
footprint. A `vmmap --summary` taken 0.2 s after a close showed 210.1 MB of
dirty "owned unmapped (graphics)" memory that fell to 6.2 MB by 4.9 s; the
software rendering context on this platform therefore still tears down through
Apple's GL driver, and every close is a transient memory-pressure event roughly
eight times the live footprint.

Verdict: **narrow**. Servo stays a running Rust-engine candidate only for
bounded sessions. Its G1 recovery dependency is red: accumulation is linear at
roughly 0.9 MB per navigation cycle, no pressure action recovers it, and the
close-time peak is unbounded by the live state. Reopening the route requires
either an upstream fix for the system-heap growth or a process-per-target
termination design that this lab has not measured.

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

The next Servo memory experiment must name the owner of the 538,212 bytes per
cycle of live libmalloc growth: run the W3 runtime under `MallocStackLogging`
and attribute `leaks`/`heap` output by library and call-stack family at the
settled post-close state, then repeat with `js_jit` disabled. It passes only if
at least 70% of the per-cycle growth is attributed to one named library or
call-site family in all seven runs; otherwise the route narrows further. The
close-time graphics spike needs its own court against an offscreen or
GPU-backed context before any headed claim.

In parallel, its lifecycle still needs the shared native target vocabulary and
bounded JSON CLI. A separate experiment must attempt
context detachment while preserving JavaScript state; visibility-only `hide`
is an explicit failure for that question.
