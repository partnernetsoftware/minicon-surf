# MiniCon Surf 0.0.x product-definition plan

Status: **active feasibility experiments — no default engine or compatibility claim**
Outcome: establish whether a Rust browser can be both demonstrably
memory-optimized and Agent-use oriented while supporting dynamic
headed/headless presentation, CDP interoperability, and first-class profiles.

## 1. Product ruling

MiniCon Surf is an independent product in the MiniCon family: a
memory-optimized, Agent-use oriented browser that can also be used directly by
a human. MiniCon is not
a prerequisite, and the MiniCon terminal binary does not absorb a browser
engine or its dependency, security, and update surface. AgenTerm may later
consume the same versioned control contract rather than fork the browser.

The two product outcomes are a conjunction, not a weighted score:

- **Memory-optimized:** on named, reproducible workloads, the complete process
  tree uses materially less memory than named contemporary browser/system-
  WebView baselines. Bounds and attribution are necessary but not sufficient;
  an explainable large footprint still fails.
- **Agent-use oriented:** profile, session, target, semantic observation,
  action, wait, failure, and resource-control semantics are native from the
  first executable, available through the CLI, and projected through a
  qualified CDP compatibility endpoint.

A route that fails either outcome cannot become the default engine or define
the product architecture. Web compatibility may be narrowed before memory is
surrendered; memory may not be hidden or waived because a route accelerates
Agent automation. Conversely, low memory cannot excuse a pixel-only or
sleep-driven Agent interface.

Five contracts are fixed before implementation choices:

1. **Memory optimization is measured product value.** Major live and retained
   bytes have an owner, budget, observable report, bounded failure, and
   recovery path. Every optimization claim compares the complete process tree
   with a named baseline on the same workload and machine. Rust is the
   implementation language, but Rust alone is not accepted as memory evidence.
2. **Agents are first-class users.** A bounded, structured CLI exists from the
   first executable. Stable target/node references, waits, snapshots, actions,
   and typed failures do not depend on screen-coordinate guessing.
3. **CDP is a compatibility edge.** A CDP discovery/WebSocket endpoint maps
   selected domains onto the same targets as the CLI. CDP does not become the
   internal product model, and unsupported methods fail explicitly.
4. **Headed and headless are runtime states.** A presentation surface can
   attach to or detach from a live target without navigation, realm rebuild,
   cookie loss, or automation-session replacement.
5. **Profiles are first-class objects.** Persistent, temporary, and later
   copy-on-write/readonly profiles have explicit identity, locking, budgets,
   policy, inspection, and lifecycle.

The 0.0.x series is allowed to reject every initial engine route. It is not
allowed to dilute either primary outcome to select a winner, hide total memory
behind process boundaries, call startup-only headed/headless selection a
dynamic switch, or advertise unqualified CDP/Web compatibility.

## 2. Markdown-tree DAG PRD

Bracketed IDs are stable decision/evidence nodes. `↳ [ID]` denotes a dependency
on an already-owned node so the tree remains a DAG rather than duplicating it.

```text
[S00] MiniCon Surf 0.0.x — prove the product shape before building the product
├── [N0] two non-negotiable outcomes — both must pass
│   ├── memory-optimized: materially lower complete-process-tree memory on named courts
│   ├── Agent-use oriented: native semantic control from the first executable
│   ├── compatibility · schedule · framework convenience · binary size are subordinate
│   ├── one outcome cannot compensate, average with, or serve as a proxy for the other
│   └── any default/product-core route must satisfy ↳ [M2] and ↳ [A3]
├── [C1] charter and product-family boundary
│   ├── Rust implementation; language choice alone proves no outcome
│   ├── [x] shared control boundary proven by two real routes before any product crate exists
│   ├── independent MiniCon-family product; MiniCon installation not required
│   ├── AgenTerm consumes a versioned contract, never CLI prose or UI internals
│   ├── separate repository, binary, profiles, versioning and release cadence
│   └── [-] no browser engine linked into the MiniCon terminal executable
├── [M2] accountable memory model
│   ├── comparative baselines: named Chrome/Brave/system-WebView versions and modes
│   ├── workloads: empty host · first target · representative pages · per-target delta
│   ├── owners: DOM · JS heap · network · decoded images · fonts · render · storage
│   ├── [~] measures: complete process tree · live · retained · resident/private · peak; shared court now records summed RSS and kernel physical footprint side by side
│   ├── lifecycle: steady · post-close reuse · navigation soak · profile/target growth
│   ├── [~] same-process maximum-capacity court exposes retained RSS/physical footprint
│   ├── limits: process · profile · target · response · DOM · image · cache
│   ├── pressure ladder: evict → trim → hibernate → terminate one target
│   ├── release budgets prevent a later feature from silently spending the advantage
│   └── [-] attribution or hard caps without comparative reduction are not optimization
├── [A3] Agent-native control plane
│   ├── CLI from first executable; bounded JSON input/output and typed errors
│   ├── profile/session/target identity shared by every frontend
│   ├── semantic snapshot with revision-scoped stable node references
│   ├── open · list · inspect · act · wait · screenshot · show · hide · memory
│   ├── waits observe conditions; callers do not guess with sleeps
│   ├── [x] synthetic stdio/CDP host shares one target identity and revision
│   ├── [x] control 0.0.1 hosted on two real engines (Servo, Lightpanda): HTML target · semantic snapshot · revision-scoped click · wait; Servo stdio and loopback CDP share one target
│   └── local authority and authentication are explicit before remote exposure
├── [D4] CDP compatibility adapter
│   ├── ↳ [A3] maps onto the same profile/session/target authority
│   ├── discovery endpoint · WebSocket transport · attach/detach lifecycle
│   ├── first candidate domains: Target · Page · Runtime · DOM · Network · Input
│   ├── domain/method/version matrix names exact supported behavior
│   ├── [~] tool journeys qualify selected Playwright/Puppeteer clients: puppeteer-core 24.15 connects and lists the Servo target; page handles need frame/realm/network mapping
│   └── [-] no claim that Chromium-specific behavior exists when it does not
├── [H5] dynamic presentation surface
│   ├── browser session and page lifetime do not belong to the GUI
│   ├── show attaches a native surface to the existing live target
│   ├── hide releases presentation resources while page execution continues
│   ├── repeated hide/show preserves page · realm · profile · scroll · Agent target
│   ├── [~] synthetic buffer court proves ownership/state mechanics, not a native surface
│   ├── hibernate is distinct: discard reconstructible state under memory pressure
│   └── CLI, CDP and human input arbitrate focus and mutations deterministically
├── [P6] first-class profile system
│   ├── named persistent and ephemeral profiles in the first usable slice
│   ├── cookies · storage · cache · history · downloads · permissions · network policy
│   ├── single-writer ownership; multiple clients attach through the owning process
│   ├── profile-specific budgets and diagnostics
│   ├── [x] synthetic G4: two persistent + one ephemeral isolate storage/policy/locks
│   ├── later: readonly and copy-on-write task profiles with explicit commit/discard
│   └── corrupt, locked or incompatible profiles fail closed without harming others
├── [E7] bounded engine experiments
│   ├── candidates declare total dependency/process cost and security-update owner
│   ├── independent labs/{techName} use the same workloads and receipt schema
│   ├── [~] Lightpanda 0.4.0: W1/W2/W3/W7-native observed; retention bounded at ~7 MB through 128 cycles; one target per server, so combine: process-per-target under a Rust control host gives 8 targets at 76 MB footprint (Servo 179 MB, Chrome 868 MB)
│   ├── [~] Servo 0.5.0: W1/W3/W7; one target 37.7 MB footprint vs Chrome 597.6 MB · 8 concurrent 179 MB vs 868 MB (RSS 87.5/1,232 · 137/2,207); narrowed to bounded sessions — ~0.7–0.9 MB/cycle growth linear to 128 cycles (130.6 MB retained) and ~290 MB close spike owned by Apple GL-on-Metal driver, no CPU-only path in the pinned release
│   ├── [~] native bounded route measures HTML/DOM/layout/JS/Web API cost incrementally; DOM slice 21/27; + bounded QuickJS realm 27/27; + bounded http fetch and representative page 35/35 — live footprint below Lightpanda at every stage, post-close equals live (retention risk, G1 open)
│   ├── compatibility route may evaluate a system engine without hiding its memory
│   ├── JS candidates require heap/time/task/capability limits and teardown evidence
│   ├── representative journeys choose Web APIs; specification breadth alone does not
│   ├── a compatibility-only route is labelled and cannot set the product memory claim
│   └── default route survives only if it satisfies ↳ [N0] ↳ [H5] ↳ [P6]
├── [G8] 0.0.x decision gates
│   ├── [x] G0 terminology: versioned vocabulary/schema/mappings share one meaning
│   ├── G1 memory court can attribute/cap synthetic state; allocator purge/relief rejected as recovery path for Servo, gate open
│   ├── [x] G2 synthetic and Servo HTML targets are controlled interchangeably by CLI and a named CDP client
│   ├── G3 a live stateful page crosses headless → headed → headless without reload
│   ├── [x] G4 synthetic profiles prove restart, storage/policy isolation and lock behavior
│   ├── [~] G5 route ledger records measured wins, costs, gaps and verdicts per route; no route is default-eligible
│   └── G6 default eligibility requires memory and Agent gates independently green
└── [-] explicit 0.0.x non-goals
    ├── no claim of full Web, Chrome, extension, media, DRM or CDP compatibility
    ├── no numeric memory promise before workload, OS and measurement are fixed
    ├── no default route selected only because it is fastest to integrate or most compatible
    ├── no silent fallback from bounded native behavior to an unmeasured process
    ├── no remote-open control port, credential plaintext or shared unlocked profile
    └── no premature extraction/rewrite of MiniCon or AgenTerm platform layers
```

## 3. Mermaid flowchart memory palace

Read left to right. The Profile Cabinet owns durable identity; the Session Hall
owns live pages; CLI and CDP enter through separate doors but meet at one
control desk. The Window Dock attaches or detaches without owning the session.
Every route passes through the Memory Court before an engine decision survives.

```mermaid
flowchart LR
    U["Two required outcomes [N0]<br/>memory-optimized AND Agent-use oriented"]

    subgraph ENTRY["Control doors"]
        CLI["CLI door [A3]<br/>bounded JSON · waits · actions"]
        CDP["CDP door [D4]<br/>discovery · WebSocket<br/>qualified domains"]
    end

    subgraph ID["Profile Cabinet [P6]"]
        PP["persistent profile [G4 synthetic]<br/>named · locked · bounded"]
        EP["ephemeral profile [G4 synthetic]<br/>isolated · discardable"]
        CP["later COW/readonly<br/>task branch"]
    end

    subgraph LIVE["Session Hall"]
        CTRL["one control desk [A3]<br/>control 0.0.1 · G0 checked<br/>profile · session · target · revision"]
        PAGE["live target<br/>DOM · realm · network · storage"]
        ARB["input arbitration<br/>Agent · CDP · human"]
    end

    subgraph SURF["Window Dock [H5]"]
        OFF["headless<br/>no attached presentation"]
        ON["headed<br/>native surface attached"]
        HIB["hibernate<br/>reconstructible state trimmed"]
    end

    subgraph MEM["Memory Court [M2]"]
        BOOK["ownership ledger<br/>live · retained · resident · peak"]
        RETAIN["same-process retention court<br/>maximum capacity · post-release · trim<br/>slope vs warm-up · attribution closure"]
        LIMIT["budget judge<br/>process · profile · target · resource"]
        BASE["comparative baseline<br/>same workload · machine · mode"]
        PRESS{"bounded AND materially<br/>below named baseline?"}
    end

    subgraph LAB["Engine Lab [E7]"]
        LP["Lightpanda 0.4.0<br/>W1/W2/W3/W7 · lowest RSS · 39 KB/cycle<br/>one target per server → process-per-target combine: 8 targets 237 MB engines"]
        SERVO["Servo 0.5.0<br/>CGL-backed W1/W3 · W7 stdio + CDP on one target<br/>narrow: ~0.9 MB/cycle growth owned by Apple GL driver<br/>~290 MB close spike · no CPU-only path · D4 clients open"]
        NATIVE["bounded native route<br/>DOM + QuickJS realm + bounded http fetch<br/>2.4 MB one target · 4.7 MB eight (footprint)<br/>post-close = live: retention risk · layout/storage/https open"]
        COMPAT["compatibility route<br/>total process cost visible"]
        DECIDE["G5 route verdict<br/>keep · narrow · combine · reject"]
    end

    SYN["synthetic control court<br/>shared authority · bounded surface<br/>persistent profile mechanics"]

    AT["AgenTerm<br/>later versioned consumer"]
    MINI["MiniCon terminal<br/>independent · unchanged"]
    FAIL["local bounded failure<br/>evict · trim · hibernate<br/>terminate one target"]

    U --> CLI & CDP
    SYN -. contract 0.0.1 .-> CTRL
    CLI & CDP --> CTRL
    PP & EP --> CTRL
    CP -. later .-> CTRL
    CTRL --> PAGE --> ARB
    PAGE --> OFF
    OFF -->|show; no reload| ON
    ON -->|hide; same target| OFF
    OFF & ON -->|memory pressure| HIB
    PAGE --> BOOK --> RETAIN --> LIMIT --> PRESS
    BASE --> PRESS
    LP & SERVO & NATIVE & COMPAT --> BOOK
    PRESS -->|yes| DECIDE
    PRESS -->|no| FAIL
    DECIDE -->|Agent gate also green| CTRL
    AT -. same protocol .-> CTRL
    MINI -. product family only .-> CLI
```

## 4. 0.0.x evidence ledger

| Gate | Question | Minimum evidence | Safe failure |
|---|---|---|---|
| G0 vocabulary | Do all frontends name the same objects? | versioned schema plus CLI/CDP mapping examples | change the paper model before code depends on it |
| G1 memory | Is the route bounded and materially more memory-efficient? | deterministic workloads report complete process-tree component, peak and post-close values against named same-machine baselines | reject or narrow the route; attribution alone does not pass |
| G2 control | Can existing automation share native targets? | one journey performed by CLI and a named CDP client against one target | narrow the compatibility matrix |
| G3 surface | Is headed/headless truly dynamic? | stateful page retains target, realm, DOM mutation, scroll and profile across show/hide | repair ownership; do not relabel restart as switching |
| G4 profile | Is identity isolated and durable? | two named profiles plus one ephemeral profile prove cookie/storage/policy separation and lock behavior | block persistence or multi-client use |
| G5 route | Which use, if any, has each route earned? | same workloads, platforms, protocol journeys and total-process measurements; verdict is keep/narrow/combine/reject | record rejection; compatibility-only routes remain labelled |
| G6 default | Can the route represent MiniCon Surf? | G1 memory and G2 Agent control independently green, plus the required surface/profile gates | no default engine; continue labs without weakening either outcome |

## 4b. G5 route ledger

One row per route, same courts, same machine (macOS arm64, one hermetic
fixture set, summed process-tree RSS). Verdicts are `keep · narrow · combine ·
reject`; none is default-eligible because no route has passed G1.

| Route | Measured wins | Measured costs | Gaps | Verdict |
|---|---|---|---|---|
| Servo 0.5.0 (Rust engine, direct embedding) | W1/W3 rendered; W7 through control `0.0.1` with stdio and loopback CDP on one HTML target (27/27, 17/17); 8 concurrent targets in one process at 179.3 MB footprint vs Chrome 867.8 MB; one target 37.7 MB vs 597.6 MB (summed RSS 137.0/2,206.9 and 87.5/1,232) | ~36 MB warm-up plus ~0.9 MB per navigation cycle owned by Apple's GL-on-Metal driver under the CGL "software" context; ~290 MB graphics spike at every close; no allocator action recovers it; 800-crate graph, ~1.5 GiB build | no CPU-only rendering path in the pinned release; `hide` is visibility only, no context detach (G3); profiles are not engine cookie jars; D4 external clients | **narrow** to bounded sessions; reopen only with a driver-free rendering context measured by the same slope/peak courts |
| Lightpanda 0.4.0 (Zig engine, CDP server) | lowest memory of any route: 22.7 MB empty, 27.9 MB one target, retention bounded at ~7 MB through 128 cycles (Servo linear at ~0.7 MB per cycle to 130.6 MB); W2 CDP journey; W7-native through a control host (27/27); target open 2.0 ms | one concurrent target only (`TargetAlreadyLoaded`); no in-process memory reporter; not Rust, not embeddable as the product engine | native CLI, dynamic surface (G3), profiles (P6), Linux/Windows cells | **keep** as low-memory reference; **combine** candidate: one engine process per target under a Rust control host gives eight targets at 76.0 MB physical footprint (240 MB summed RSS, which counts the executable eight times), 0.6 MB retained after eight closes, and per-target termination; 2.4× below Servo and 11× below Chrome at eight targets |
| Chrome 152 (compatibility/system baseline) | full Web compatibility; 8 concurrent targets; qualified CDP | 288 MB empty and 597.6 MB one target by footprint (803 MB and 1,232 MB summed RSS), 115.3 MB warm-up plus 799 KB per cycle of RSS, 2,206.9 MB at eight targets across nine processes | not a candidate engine; digest-identified install rather than pinned artifact | **baseline only**; labelled compatibility reference, cannot set the memory claim |
| Synthetic control host (engine-neutral Rust) | G0 vocabulary, G2 mechanism, G4 profile isolation, surface mechanics; capacity/allocator courts | not HTML; no rendering, no real cookie jar | G3 native surface; G1 has no browser baseline | **keep** as the court and contract reference, not a product crate |
| Native bounded route: html5ever DOM, bounded QuickJS realm, bounded `http` fetch with fail-closed address policy | shared journey 27/27; network court 35/35 on a hermetic representative page (fetch-driven results, click-triggered fetch, nineteen typed policy negatives, concurrency and budget caps, cross-origin script refused); footprint beside Lightpanda single server: empty 1.3 vs 8.4 MB, one target 2.4 vs 9.1 MB, eight targets 4.7 MB vs one-target limit | post-close footprint equals live at every stage; retained above empty 1.9 MB on the fixture court (Lightpanda 1.5 MB) and 4.6 MB after eight representative pages (Lightpanda 1.0 MB after one); logical owners reach zero but nothing is returned to the OS | no layout, https, cookies, storage, images, fonts or real timers; scripts run after parse; DOM shim covers the fixtures and instrumentation only, not Web compatibility | **keep** as the route's measured base with a named retention risk; next: allocator/realm retention repair measured by the same courts, then a bounded profile store, then https |

G6 stays closed: no route is independently green on both G1 and G2/A3.

## 5. Current experimental frontier

- [x] G0 vocabulary is checked in control contract `0.0.1`: profile, session,
  target, frame, realm, surface, revision and compound node reference have one
  owner/lifetime meaning; typed opaque IDs, request/success/failure envelopes,
  deadlines, byte/depth/collection bounds and stable error codes have a JSON
  Schema plus a dependency-free checker. Four paired snapshot/action examples
  and seven negative cases pass. The machine-readable CDP mapping explicitly
  leaves profile, surface and revision unmapped rather than borrowing Chromium
  semantics. This closes the paper-model G0 minimum. The synthetic host now
  honors a native subset and projects the qualified synthetic slice through
  CDP; product executable and broad client/HTML qualification remain open.
- [~] The engine-neutral Rust synthetic-control host now consumes bounded
  control `0.0.1` NDJSON and preserves profile → session → target identity in
  one process. Its process-level journey snapshots revision 0, clicks a
  revision-scoped button, observes revision 1, rejects the reused reference as
  `stale_revision`, satisfies a condition wait, and reports explicit profile,
  session and target memory owners; closing the target reduces both its owner
  count and logical accounted bytes. Fixed capacity limits and a streaming
  oversized-line drain prevent unbounded request/state growth. Ten library,
  one reader and one process integration test pass. Its logical memory ledger
  is a lower bound, not RSS/private/PSS, so G1 remains open.
- [x] G2's stated synthetic minimum is now observed. One host exposes bounded
  native stdio and a loopback CDP discovery/WebSocket edge backed exclusively
  by the same `Arc<Mutex<ControlState>>`. The named dependency-free
  `synthetic-g2-court-client` found the exact native-created target ID, attached
  with flattened sessions, resolved and clicked its button through qualified
  Target/DOM/Runtime methods, and native stdio then observed revision 0 → 1 and
  a typed `stale_revision` for the pre-CDP reference. `Page.navigate` returned
  explicit `-32601`. This closes only G2's engine-neutral mechanism: the target
  is synthetic rather than HTML, only seven methods are qualified, one CDP
  connection is supported, and Playwright/Puppeteer qualification remains D4
  work. There is no claim of broad CDP compatibility or remote-safe exposure.
- [x] G4's deliberately small synthetic minimum is observed across two
  concurrent hosts and three restart generations. Named persistent `alpha` and
  `beta` retain distinct cookie/local-storage values and network/permission
  policies; ephemeral `scratch` is absent after restart. A competing process
  gets typed `profile_locked`, then opens the same identity after owner close.
  A corrupt sibling fails closed while healthy profiles remain available, and
  Unix records/locks use private permissions. This does not complete P6:
  synthetic unencrypted values are not real credentials or an engine cookie
  jar; cache, history, downloads, permission prompts, readonly and COW remain
  open.
- [~] The synthetic surface mechanics court holds one CDP attachment while
  native stdio performs three headless → headed → headless cycles. Target,
  native session, realm, clicked DOM, revision 2 and scroll position 240 remain
  unchanged. Each show creates one bounded 65,536-byte presentation owner;
  each hide removes it and returns logical accounting to the live baseline.
  This does not pass G3: the attachment is a synthetic buffer, not a native
  window/rendering context, and therefore cannot prove GUI resource teardown.
- [~] The expanded synthetic lifecycle court separates empty, live, headed,
  post-hide and post-close steady windows with a 300 ms sampler warmup; every
  measured setup completed within 3.008 ms. Across seven runs per state, median
  complete-tree RSS was 1,966,080, 2,015,232, 2,031,616, 2,031,616 and
  2,015,232 bytes. Headed was +16 KiB versus live, and post-hide retained
  +16 KiB versus live even though logical surface ownership returned to zero.
  Logical bytes were 0, 634, 66,251, 634 and 279. This exposes a real retained-
  RSS gap and still does not pass G1: modes are separate fresh processes and
  private/PSS are absent; the staged companion now covers maximum capacity,
  but the synthetic state has no meaningful browser-efficiency baseline.
- [~] The same-process maximum-capacity court now closes two important G1
  evidence gaps. One host crosses empty → one target → headed → post-hide → 8
  profiles/16 sessions/32 targets/8 surfaces plus 512 × 1,024-byte storage
  values → zero owners → allocator trim. All profile/session/target/surface/
  storage overflow attempts returned `resource_limit` in all seven runs.
  Median RSS rose from 1,966,080 to 2,949,120 bytes and Apple physical
  footprint from 1,048,888 to 2,015,568 bytes. With every logical owner back at
  zero, 983,040 RSS bytes and 966,680 physical-footprint bytes remained above
  the initial state. `malloc_zone_pressure_relief` returned zero in every run
  and did not change either median, so this first trim strategy is rejected as
  ineffective for the court. G1 remains open: this is still synthetic rather
  than HTML/engine work, has no meaningful browser baseline, and does not yet
  provide an effective retained-memory recovery path.
- [~] A controlled allocator branch now tests the recovery-path hypothesis
  without changing the default allocator. Seven same-source runs per binary
  show `mimalloc` 0.1.52 forced collection reducing post-release Apple physical
  footprint by a 704,512-byte median versus zero for the system allocator, but
  no RSS reduction in either branch. Mimalloc also starts 573,512 physical-
  footprint bytes above system and remains 524,336 bytes higher at maximum
  capacity (its RSS is likewise higher). Verdict: **narrow/keep only as an
  allocator-purge lab**; do not make it default. This is useful evidence that
  an explicit purge path can work, not evidence of overall memory optimization.
  Secure mode, real engine allocations and non-macOS cells remain untested, so
  G1 stays open.
- [x] The public lab governance, hermetic W1/W2 fixtures, receipt schema and
  redaction rules exist under `labs/` and `AGENTS.md`.
- [x] The shared Rust process-tree sampler has deadline cleanup, recursively
  sampled RSS, argument-redacted JSON, and a wrapper-exclusion mode; three unit
  and seven process-level integration tests qualify those mechanics on the
  current Unix/macOS cell. Warmup selection proves delayed first sampling and
  zero samples for warmup-time exit without extending the launch-time deadline.
  RSS remains neither private memory nor PSS.
- [~] Lightpanda `0.4.0` macOS arm64 W1 is the first observed reference:
  the pinned official artifact passed its SHA-256 check, emitted the expected
  semantic heading/button, and exposed CDP 1.3 discovery on loopback.
- [~] Seven post-warm-up executions reported a 25,575,424-byte median and
  25,690,112-byte maximum using BSD `time -l` process maximum RSS. The receipt
  remains `incomplete`: it is root-process-only, one short `data:` document,
  one OS/ISA, with no same-machine Chrome complete-process-tree baseline.
- [~] Lightpanda W2 now proves page-script DOM mutation plus a real CDP 1.3
  journey across Target, Page, Runtime and DOM: one target is created,
  navigated, semantically observed, mutated through its resolved remote object,
  re-observed and closed. CLI and CDP still do not share one long-lived target,
  and Input/external-client qualification remains open.
- [~] Servo `0.5.0` is pinned by exact crate checksum, release tag/commit and
  lockfile. Its public Rust embedding API compiles on macOS arm64, including
  window/offscreen/software rendering contexts. A real software-rendered W1
  now loads the fixture, observes its four named semantic values through a JS
  callback, verifies an 800×600 screenshot, holds for two seconds, and shuts
  down across seven post-warmup runs. Median sampled complete-tree RSS was
  92,700,672 bytes, maximum 92,880,896 bytes, with one process observed in all
  runs. Status remains `incomplete`: software rendering and direct Rust control
  are not like-for-like with the CDP baseline; summed RSS is not private/PSS;
  comparative soak and concurrent-target cost are open. The 800 locked packages
  and about 1.5 GiB cold build state remain integration-cost facts, not RSS.
  Public `show`/`hide` still does not prove live rendering-context detach,
  profiles are not MiniCon Surf profile objects, and Servo devtools must not be
  called CDP.
- [~] Servo W3 now exercises the actual public close lifecycle in one engine
  instance: `WebViewInner::drop` sends `CloseWebView`, removes the paint
  webview, and the host keeps spinning the event loop across eight sequential
  build/semantic-observe/drop cycles. Seven runs measured median complete-tree
  RSS of 44,056,576 bytes empty, 86,638,592 with the first target, 85,983,232
  after its close, 97,730,560 with the eighth target, and 95,174,656 after all
  eight closes. The final state retained 51,101,696 bytes above empty. This is
  a material route risk, not a leak claim: caches, allocator retention and
  reclaimable engine state are not separated. Servo remains `keep`, conditional
  on internal memory-report attribution and effective pressure recovery; W3
  does not pass G1 and direct Rust callbacks still do not pass G2/D4.
- [~] Servo's promoted W3 attribution court isolates RSS sampling from its
  public memory reporter in separate runs. Seven repetitions measured median
  RSS of 44,220,416 bytes empty and 96,256,000 after eight closes, a 51,888,128-
  byte retained delta. Explicit reported ownership was 2,746,696 bytes empty,
  9,448,352 live, and 2,759,160 after eight closes: only 12,464 bytes retained
  above empty in every run. Live JS/image/layout/display-list prefixes vanish
  after close, while non-explicit system-heap reservation rises from a
  37,748,736-byte to 62,914,560-byte median. This materially narrows the cause:
  retained RSS is not explained by Servo's reported live target owners and is
  more consistent with allocator reservation or unreported/reclaimable state.
  It is still not proof of a leak or of recoverability. Servo stays `keep`, and
  a measured jemalloc/engine pressure court becomes its next G1 dependency.
- [~] Servo's paired pressure court now gives control-wait and forced jemalloc
  purge separate fresh processes, each with one warmup and seven measured W3
  runs. Control `post_close → post_action` RSS changed by zero in all runs.
  `arena.4096.purge` succeeded and reduced RSS in all seven, with a 1,638,400-
  byte median, while explicit reported ownership changed by zero. Yet the
  post-purge state retained 49,692,672 bytes above empty: only about 3.193% of
  its 51,314,688-byte post-close retention was recovered. Verdict: **effective
  but insufficient**. Keep purge as one pressure-ladder action, but Servo's G1
  recovery dependency remains red; decay/tcache, engine cache, hibernate and
  terminate-one-target routes require distinct evidence.
- [~] Servo's attribution-closure court supersedes the decay/tcache follow-up.
  A source audit found jemalloc built with `--disable-stats` (so no receipt had
  held a jemalloc figure), jemalloc linked under the `_rjem_` prefix, and
  SpiderMonkey built with `--disable-jemalloc`, so the "system-heap" the earlier
  attribution blamed is Apple libmalloc, which `arena.4096.purge` cannot touch.
  With jemalloc `stats` enabled, in-process libmalloc statistics, physical
  footprint after a 3 s settle, and control cells of 1/8/32 build/observe/drop
  cycles (seven runs each), settled footprint retention above empty was
  34,881,800, 45,122,472 and 64,080,848 bytes. Least squares over 21 runs gives
  a 35,757,687-byte warm-up intercept and an 889,348-byte-per-cycle slope, of
  which 538,212 bytes per cycle are libmalloc bytes still in use: linear
  accumulation of never-freed C/C++ allocations, not allocator retention.
  jemalloc resident plus libmalloc reserved explain only a 0.739 share of the
  32-cycle footprint retention, so attribution does not close and a quarter is
  owned by neither allocator. After eight closes, `malloc_zone_pressure_relief`
  released zero bytes in all seven runs and moved nothing; jemalloc purge
  recovered 1,523,712 footprint bytes; no action reached empty plus warm-up
  plus 4 MiB. The kernel's lifetime maximum footprint hit 315,278,344 bytes
  after one close (21.2 MB empty, 39.3 MB live) because 210 MB of dirty
  graphics-owned memory appears for about one second during teardown of the
  GL-backed software context. Verdict: **narrow** Servo to bounded sessions;
  its G1 recovery dependency is red until an upstream system-heap fix or a
  measured process-per-target termination design exists. The next court must
  name the libmalloc growth owner under `MallocStackLogging` and pass only if
  at least 70% of the per-cycle growth attributes to one library or call-site
  family in all seven runs.
- [x] Servo's growth-owner court passes that gate and names the owner. Under
  `MallocStackLogging`, `malloc_history` snapshots at the settled post-close
  state for 1 and 17 cycles (seven run pairs) put a 0.9997 to 0.9999 share of
  the 561,383-byte-per-cycle libmalloc growth in Apple's Metal-backed OpenGL
  renderer (`GLDPipelineProgramRec`, `AGX::UserCommonShaderFactory`,
  `GLRRenderPipelineKey`); SpiderMonkey, sqlite, fonts and every Rust crate
  grew by zero. The pinned `SoftwareRenderingContext` is a CGL context on this
  platform, Servo 0.5.0 does not enable WebRender's swgl compositor, so the
  release has no CPU-only rendering path. This converts the Servo memory risk
  from an engine-allocation question into a rendering-context dependency:
  the route reopens only with a context that never enters the platform GL
  driver, measured by the same slope and peak courts (footprint slope below
  256 KB per cycle, lifetime peak within 2× live, `apple-gl-metal` absent from
  per-cycle growth in all seven runs). [H5] inherits the same constraint: any
  headed surface on macOS must budget the driver's per-context pipeline cache.
- [~] The first HTML-backed host of control `0.0.1` now exists in the Servo
  lab. `servo-control` serves bounded NDJSON on stdio from one long-lived
  engine and offers ephemeral profiles, one session, hermetic fixture targets,
  semantic snapshots, revision-scoped click actions, `revision_at_least` waits
  and a memory report; other reserved operations are typed
  `unsupported_operation`. Against the new W7 fixture
  `semantic-interactive.html`, the checked journey passes 25 of 25 checks:
  revision 0 snapshot (heading, label, textbox with value, button, link),
  click through a compound reference, wait observing revision ≥ 1 without a
  sleep, unmet wait as `deadline_exceeded`, reused reference as
  `stale_revision` with both revisions in details, post-click snapshot showing
  the mutated button and new status text, `max_nodes` truncation and typed
  refusals. Target open took 205.754 ms; every other operation under 13 ms.
  This restores [N0] symmetry for the Servo route: it now carries Agent-side
  evidence under the same vocabulary as the synthetic host, so a G5 verdict
  can weigh both gates. It does not pass G2/D4: no CDP edge shares this
  target, navigation/frames are uncovered, click and `revision_at_least` are
  the only kinds, and profiles are not engine cookie jars.
- [~] The shared W3 retention court now accepts the Servo control host as a
  third candidate, rotating with Lightpanda `0.4.0` and Chrome
  `152.0.7977.75` over seven repetitions. Median complete-tree RSS was
  44,613,632 bytes empty, 87,457,792 with one target and 94,601,216 after
  eight closes for Servo; 22,659,072, 27,934,720 and 29,523,968 for
  Lightpanda; 803,078,144, 1,232,109,568 and 934,428,672 for Chrome. Eight
  concurrent targets cost Servo 136,953,856 bytes in one process against
  Chrome's 2,206,859,264 in nine; Lightpanda still rejects a second target.
  This is Servo's first same-machine named baseline and the first multi-target
  route below Chrome by more than an order of magnitude, satisfying the
  "named baseline" clause of G1 for this cell while the gate stays open:
  summed RSS, one fixture, native rather than CDP transport, a CGL-backed
  context, and Servo's own linear retention. Court discovery now bypasses
  environment proxies after a loopback proxy masqueraded as an engine `503`.
- [x] G2's mechanism is now observed on an HTML document. The Servo control
  host opens a loopback CDP 1.3 edge whose seven qualified methods are
  translated into native operations delivered to the same main loop. The
  checked journey passes 17 of 17: native stdio opens `semantic-interactive`
  and snapshots revision 0; the CDP client finds exactly that target through
  discovery and `Target.getTargets`, attaches flattened, resolves `#continue`
  through DOM methods and clicks it with `Runtime.callFunctionOn`; native
  stdio observes revision 1, the mutated button and new status text, and
  rejects the pre-CDP reference as `stale_revision`; the revision-0 remote
  object fails on a second click, `Page.navigate` is `-32601`. G2 therefore
  holds for both the synthetic and the Servo HTML target with one target
  identity and revision across both doors. D4 remains open: court client
  rather than Playwright/Puppeteer, one connection, `button`/`#id` selectors
  only, no navigation, frames, Input or Network domains.
- [x] The control `0.0.1` boundary is now implemented by two real engines.
  A Lightpanda-backed host maps the same operations onto CDP with the same
  in-page instrumentation the Servo host injects, and the Servo lab's journey
  runs unchanged against both: 27 of 27 checks on each. Differences are
  recorded as facts, not hidden: Lightpanda's `memory.report` is
  `unsupported_capability` and its second concurrent target is a typed
  `resource_limit`, while Servo offers both. Lightpanda's target open took
  2.035 ms against Servo's 50.803 ms. This satisfies the change-hygiene
  precondition that two real routes prove the shared boundary before any
  product crate absorbs it; extraction remains deliberately deferred until a
  route also passes G1.
- [~] The shared retention court now takes a cycle count and a candidate
  subset, and a slope receipt fits retained summed RSS against 1, 8 and 32
  sequential cycles for all three routes (seven runs each). Warm-up intercept
  and per-cycle slope in bytes: Servo 43,050,609 and 791,477; Lightpanda
  5,783,352 and 39,062; Chrome 115,322,685 and 799,476. Servo's slope
  reproduces its own lab's 765,990, Chrome accumulates at nearly the same
  rate, and Lightpanda's per-cycle term is about one twentieth of either.
  [M2]'s "navigation soak" lifecycle measure therefore has a comparable
  number per route; G1 stays open because the measure is summed RSS on one
  fixture and no route is both low-slope and multi-target.
- [~] The native bounded route now has its first measured slice. `labs/native-dom`
  serves control `0.0.1` from an html5ever-parsed document with no layout,
  script realm or network, using the same argument shape as `servo-control`.
  The shared journey passes 21 of 27: every static check (revision-0 snapshot
  of heading, label, textbox with value, button and link; references; bounds;
  typed refusals; lifecycle) matches the engine hosts, and the six failures
  are the slice's declared boundary (click `unsupported_capability`, waits for
  revision 1 `deadline_exceeded`, no mutation so no `stale_revision`, W2 shows
  `Before script`). On the eight-cycle retention court beside the three
  engines it measured 2,195,456 bytes empty, 2,539,520 with one target,
  2,785,280 after eight closes and 3,063,808 with eight concurrent targets.
  This is the route's floor, not a browser; the next slice must add a bounded
  script realm and event dispatch and pass only if the six failing checks turn
  green while the court row stays materially below Servo's.
- [~] Lightpanda's one-target limit is now answered by a measured `combine`
  design instead of an accepted narrowing. The control host starts one
  Lightpanda process per target; the shared journey passes 27 of 27 with a
  second concurrent target opening, and on the eight-cycle retention court the
  host-plus-engines tree measured 28,164,096 bytes empty, 60,866,560 with one
  target, 39,174,144 after eight closes and 279,855,104 with eight concurrent
  targets in nine processes, against Servo's 137,101,312 in one process and
  Chrome's 2,205,646,848. Engine retention is zero by construction because a
  close ends the process: a host-split rerun sampling descendants separately
  puts engine processes at 29,638,656 bytes with one target, 0 after every
  close and 237,027,328 with eight concurrent targets, so the 10,993,664
  retained bytes and the 28 MB empty footprint are the Python court host's
  own, which a Rust host would mostly remove. [M2]'s `terminate one target` pressure action therefore has a
  measured process boundary on this route. G1 stays open: summed RSS, one
  fixture, and the design is about twice Servo at eight targets.
- [~] D4 has its first named external client. `puppeteer-core 24.15.0` on
  Node 26 was driven against the Servo control host's CDP edge with method
  tracing. Four handshake acknowledgements (`Target.getBrowserContexts`,
  `Browser.getVersion`, `Target.setDiscoverTargets` replaying native targets
  as `targetCreated`, `Target.setAutoAttach` replaying them as flattened
  `attachedToTarget` sessions) let `puppeteer.connect` succeed over both
  endpoint forms, `waitForTarget` return the native target id and
  `browser.targets()` list it. `target.page()` times out because page
  initialization sends nine unmapped methods (`Network.enable`,
  `Network.setCacheDisabled`, `Fetch.disable`, `Page.enable`,
  `Page.getFrameTree`, `Page.setLifecycleEventsEnabled`, `Runtime.enable`,
  `Performance.enable`, `Log.enable`), each an explicit `-32601`. The
  boundary is frame identity, execution contexts and network lifecycle
  events, which control `0.0.1` deliberately leaves unmapped; the next D4
  step is a frame/realm mapping, not silent acknowledgements.
- [~] A 128-cycle soak on the shared court tests the linear retention
  assumption for the two engine routes (seven runs each). Servo retained
  130,613,248 bytes with 178,192,384 live at the 128th target, within
  669,294 bytes of the linear fit; refit over 1/8/32/128 the slope is
  678,621 bytes per cycle with no plateau. Lightpanda retained 6,963,200
  bytes, 3.8 MB below the linear prediction and within 100 KB of its 32-cycle
  value: its retention is a bounded ~7 MB plateau, and the earlier
  39,062-byte-per-cycle slope was warm-up spread over few cycles. [M2]'s
  navigation-soak measure therefore distinguishes the routes qualitatively:
  Lightpanda is bounded, Servo (under the pinned rendering context) and
  Chrome are not within the measured range.
- [x] The process-per-target combination now has a Rust host
  (`labs/lightpanda/host`, 783 KB, three small dependencies) with its own
  loopback CDP client and the same in-page instrumentation; the shared
  journey passes 27 of 27. On the eight-cycle court the tree is 1,851,392
  bytes empty, 31,719,424 with one target, 2,572,288 after eight closes and
  239,878,144 with eight concurrent targets, of which the host is 1.9 to 2.8
  MB. The combination's cost is therefore the engines' alone: about 1.75×
  Servo's single process at eight targets, one ninth of Chrome, with 720,896
  bytes retained and a process boundary per target. This is the first route
  shape that is simultaneously multi-target, bounded in retention and far
  below the Chrome baseline; G1 still needs private/PSS measures, more
  fixtures and platforms, and the Agent gate still lacks D4 clients.
- [~] The shared court now sums the kernel's physical footprint beside
  summed RSS, and the footprint reverses two RSS readings. Eight per-target
  Lightpanda engines are 76,043,448 bytes of footprint (their 240 MB summed
  RSS counted the 82 MB executable eight times), Servo's single process is
  179,309,736 settled (its GL driver's graphics memory is footprint but not
  RSS, and 396,594,416 within 500 ms of the opens), and Chrome's nine
  processes are 867,831,560 (a third of their summed RSS). At one target the
  footprint order is native DOM 1.4 MB, Lightpanda 9.1 to 10.4 MB, Servo
  37.7 MB, Chrome 597.6 MB. The process-per-target combination is therefore
  the lowest-footprint multi-target route measured, about 2.4× below Servo
  and 11× below Chrome at eight targets, with 638,976 bytes retained after
  eight closes. The memory claim must be stated in footprint from here on;
  summed RSS stays recorded for continuity.
- [~] The native route's second slice adds a bounded script realm. Each
  target mirrors its html5ever tree into a QuickJS realm (`rquickjs 0.12.2`,
  16 MiB heap cap, 512 KiB stack, deadline interrupt) behind a deliberately
  small DOM shim (nodes, events with bubbling, `MutationObserver` as
  microtasks, attributes, `dataset`, a selector subset); inline scripts run
  after parsing and the engine hosts' instrumentation runs unchanged. The
  shared journey passes 27 of 27, including the six checks slice 1 failed by
  design; target open takes 5.873 ms and the realm holds 227,920 malloc
  bytes for the interactive fixture. On the eight-cycle court the slice is
  1,343,800 bytes of footprint empty, 2,457,912 with one target, 3,113,272
  after eight closes and 4,440,376 with eight concurrent targets: about four
  times below Lightpanda's single server and fifteen times below Servo at
  one target, with the full action vocabulary. It is not a Web-compatibility
  claim (no layout, network, storage or timers; the shim covers the fixtures
  and instrumentation only); the next slice adds bounded network fetch and a
  representative page under the same journey and court.
- [~] The native route's third slice adds a bounded `http` fetch and a
  hermetic representative page. The client is `http` only, fails closed on
  every IANA special-purpose IPv4 range and on every IPv6 address outside
  2000::/3 or inside its special blocks, refuses `localhost` names and
  embedded credentials, follows at most three redirects with the policy
  re-applied per hop, caps headers at 16 KiB, bodies at 1 MiB, fetches at
  3 s, queued `fetch()` calls at four per turn and 32 per target, external
  scripts at eight same-origin sources, refuses informational statuses,
  `Transfer-Encoding` and conflicting `Content-Length`, and connects only
  to the addresses it authorized. Only an exact `--allow-origin` reaches
  a non-public address; the court allowlists its own loopback server and
  proves a host without it refuses loopback. The network court passes 35 of
  35: the representative page (results filled by `fetch` from an external
  same-origin script, a click-triggered fetch observed through a revision
  wait), nineteen typed negatives, the concurrency and budget caps, a
  refused cross-origin script, and logical owners at zero after closes.
  Footprint is reported stage by stage beside Lightpanda's single server:
  on the fixture court 1,343,800 empty, 2,408,760 one target, 3,211,576
  after eight closes and 4,718,904 with eight concurrent targets against
  8,356,392, 9,077,336, 9,912,920 and a one-target limit; on the
  representative page 2,720,056 live and 5,964,232 with eight pages against
  9,486,936 for one. Lower at every live stage, but post-close equals live
  everywhere and retained-above-empty exceeds Lightpanda's (1,867,776
  against 1,540,144 on the court; 4,620,432 after eight pages against
  1,015,856 after one), so the lifecycle is a QuickJS, parsed-tree,
  network-buffer and allocator retention risk. G1, G3, P6 and G6 stay open.
- [~] The first unfair short-fetch/persistent-server comparison remains
  rejected. Its replacement gives Lightpanda `0.4.0` and installed Google
  Chrome `152.0.7977.65` the same fresh-profile CDP W1 target, semantic-ready
  condition, two-second hold, alternating order, seven measured repetitions
  and recursive 10 ms sampler. Median summed-tree RSS was 28,131,328 bytes for
  Lightpanda (one process) and 1,236,467,712 bytes for Chrome (nine processes),
  an observed 43.953× court ratio. Status remains `incomplete`: summed RSS can
  double-count shared pages; feature sets differ; this is one static fixture,
  OS and ISA; installed Chrome is digest-identified but not a pinned download;
  retention, soak and marginal-target cost remain unmeasured. This is strong
  route-selection evidence, not yet the MiniCon Surf memory claim or G1 pass.
- [~] The shared W3 court now keeps each real browser server alive across eight
  sequential semantic-target create/observe/close cycles, then probes
  concurrent capacity separately. Across seven alternating runs, Lightpanda
  `0.4.0` median complete-tree RSS was 22,626,304 bytes empty, 27,901,952 with
  the first target, and 29,442,048 after all eight closes: 6,766,592 bytes
  retained above empty. Chrome `152.0.7977.75` measured 803,373,056,
  1,231,011,840 and 930,168,832 bytes respectively, retaining 124,715,008
  bytes. Lightpanda stayed single-process but rejected every second concurrent
  target with `TargetAlreadyLoaded`; Chrome supported the eight-target probe
  at 2,200,485,888-byte median summed-tree RSS. This strengthens G1 lifecycle
  evidence and creates a material Agent/functionality constraint: Lightpanda
  remains `keep` as a low-memory reference but is **narrowed to one concurrent
  target** for this release. G1 stays open because summed RSS is not private/
  PSS, the workload is one small fixture/platform, feature breadth differs,
  and Lightpanda is not the Rust/dynamic-surface product engine.

## 6. First sequencing

1. Write the vocabulary and protocol sketch for profile, browser session,
   target, frame, execution realm, surface, node reference and revision.
2. Build the memory-court harness before selecting data structures or embedding
   an engine; fix workloads, named baselines and OS measurement semantics.
3. [x] Prove one in-memory synthetic target through native CLI and CDP transport.
4. [~] Prove surface attachment/detachment against that target without giving
   the surface ownership of page lifetime; synthetic mechanics pass, native
   presentation resources remain open.
5. [x] Prove persistent and ephemeral profile isolation with a deliberately
   small synthetic storage model; product/engine-backed profile breadth remains
   P6 work.
6. [~] Run independent `labs/{techName}` spikes behind the established
   contracts, publish comparable memory and Agent-control evidence, and issue
   an explicit keep/narrow/combine/reject verdict for every route. The G5
   ledger now holds Servo (narrow), Lightpanda (keep + combine), Chrome
   (baseline), synthetic (keep) and the native DOM slice (keep as floor);
   every route except Chrome runs the same control `0.0.1` journey and the
   same retention court.
7. [~] Next, in order: a Rust control host for the process-per-target
   combine (done: `labs/lightpanda/host`, 1.9 MB empty); the native route's script-realm slice (done: 27/27 at 2.5 MB one target) and its bounded-network slice with a representative page (done: 35/35; post-close retention unrecovered)
   measured by the unchanged journey and court; D4 qualification of a named
   external CDP client against the shared edge; and a Servo rerun only when
   a driver-free rendering context exists. G1 closes only when one route is
   both materially below the baselines and low-slope on the shared court.

The first code milestone is therefore not “render a website.” It is “one
bounded target has one identity and state while CLI, CDP, and an optional
window observe and control it without changing its lifetime.”
