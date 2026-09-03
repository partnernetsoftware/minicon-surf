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

Seven contracts are fixed before implementation choices:

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
6. **Many experimental backends, one product authority.** Independent native,
   embedded, worker-process, and compatibility backends may advance in
   parallel behind the same control and measurement contracts. Profile,
   session, target, revision, budget, and failure semantics remain owned by
   MiniCon Surf rather than any engine or CDP. The purpose of plurality is to
   compare, combine, and learn: capabilities that earn their place migrate
   toward a memory-bounded Rust browser core. It is not a commitment to ship a
   permanent generic browser launcher or to expose backend differences as the
   product model.
7. **The ecosystem surrounds the core.** Electron's durable application
   objects, Wry's thin embedding boundary, and Tauri's capability/permission
   model are references for a later developer platform, not candidate browser
   kernels. Embedding, SDK, plugin, packaging, and compatibility layers remain
   optional and pay near-zero resident cost when absent. They consume the same
   Agent-native authority and resource ledger; they cannot introduce an
   unbounded IPC escape hatch, bind target lifetime back to a window, or make
   full Node/Chromium compatibility a prerequisite for the native core.

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
│   ├── [~] frame/realm rules: target revision · frame identity · document generation · realm identity kept distinct; bounded per-target enumeration; foreign, ended and unknown ids refused alike; frames and realms never owners; synthetic court 28/28 with Page.getFrameTree adapter-scoped ids; realm projection, navigation events and nesting are recorded losses
│   ├── [~] native route carries the rules on real documents: one main frame and realm per target, link click = same-frame navigation built completely before the swap under the unchanged network policy, failed navigations leave the target untouched; court 62/62 under default and arena; no child frames, no capability on this host
│   ├── [~] native route exposes a bounded loopback CDP edge on the same live target: adapters registered in the host, Page.FrameId adapter-scoped and kept across navigation, puppeteer-core 24.15.0 observes Page.getFrameTree and drives the link click through createCDPSession; court 58/58; target.page() and realm projection are explicit losses
│   └── [-] no claim that Chromium-specific behavior exists when it does not
├── [H5] dynamic presentation surface
│   ├── browser session and page lifetime do not belong to the GUI
│   ├── show attaches a native surface to the existing live target
│   ├── hide releases presentation resources while page execution continues
│   ├── repeated hide/show preserves page · realm · profile · scroll · Agent target
│   ├── [~] synthetic buffer court proves ownership/state mechanics, not a native surface
│   ├── [~] macOS candidate court (direct Cocoa vs winit+softbuffer): real windows attach/detach with pixels read back, but AppKit keeps ~10 MB after hide plus a per-cycle residual; surface-process design recommended, pending ruling
│   ├── [~] surface process prototype (direct Cocoa child, bounded IPC): real input, CDP continuity, reaped detach, owners to zero on one live target (106/110); host post-hide footprint and slope over caps (spawn machinery ~1.1 MB)
│   ├── hibernate is distinct: discard reconstructible state under memory pressure
│   └── CLI, CDP and human input arbitrate focus and mutations deterministically
├── [P6] first-class profile system
│   ├── named persistent and ephemeral profiles in the first usable slice
│   ├── cookies · storage · cache · history · downloads · permissions · network policy
│   ├── single-writer ownership; multiple clients attach through the owning process
│   ├── profile-specific budgets and diagnostics
│   ├── [x] synthetic G4: two persistent + one ephemeral isolate storage/policy/locks
│   ├── [~] native-dom slice: keychain-envelope sealed store · RFC 6265 subset jar · localStorage · write-through with fault court (80/82; total-live criterion unmet)
│   ├── [x] native-dom opt-in HTTPS: pinned roots only · rustls + ring (C/perlasm inside) · Secure cookies over verified https · court 74/74
│   ├── [x] persistent Secure cookies across restart: sealed record · volatile never persisted · expiry by current clock · court 78/78
│   ├── later: readonly and copy-on-write task profiles with explicit commit/discard
│   └── corrupt, locked or incompatible profiles fail closed without harming others
├── [E7] bounded engine experiments
│   ├── multiple backends advance concurrently behind ↳ [A3] and ↳ [M2]
│   ├── MiniCon Surf owns identity, lifecycle, policy, budgets and failure semantics
│   ├── backend adapters translate capabilities; they never redefine the product model
│   ├── candidates declare total dependency/process cost and security-update owner
│   ├── independent labs/{techName} use the same workloads and receipt schema
│   ├── [~] Lightpanda 0.4.0: W1/W2/W3/W7-native observed; retention bounded at ~7 MB through 128 cycles; one target per server, so combine: process-per-target under a Rust control host gives 8 targets at 76 MB footprint (Servo 179 MB, Chrome 868 MB); the host now attributes every engine process per target (ME3)
│   ├── [~] Servo 0.5.0: W1/W3/W7; one target 37.7 MB footprint vs Chrome 597.6 MB · 8 concurrent 179 MB vs 868 MB (RSS 87.5/1,232 · 137/2,207); narrowed to bounded sessions — ~0.7–0.9 MB/cycle growth linear to 128 cycles (130.6 MB retained) and ~290 MB close spike owned by Apple GL-on-Metal driver, no CPU-only path in the pinned release
│   ├── [~] native bounded route measures HTML/DOM/layout/JS/Web API cost incrementally; DOM 21/27 · + QuickJS realm 27/27 · + bounded http fetch 35/35; post-close retention is consistent with libmalloc reservation of freed blocks (tracked owners and in-use return near empty; no continued growth across one reopen); zone-per-realm repair significant post-close but +1 MB/realm live; realm heap arena (macOS mmap, unmapped at close) repairs post-close without the live cost, holds a plateau through 128 single-target cycles and 32 concurrent eight-target rounds under frozen criteria, kept opt-in; G1 open
│   ├── compatibility route may evaluate a system engine without hiding its memory
│   ├── native bounded route is the browser-core convergence path, not merely another adapter
│   ├── Lightpanda may combine as a low-memory worker/reference while native capability grows
│   ├── Servo remains a rendering/surface research source unless its measured memory gate recovers
│   ├── Chrome remains a compatibility and memory baseline, never the product authority
│   ├── earned mechanisms may migrate into the native core only with their limits and courts
│   ├── JS candidates require heap/time/task/capability limits and teardown evidence
│   ├── representative journeys choose Web APIs; specification breadth alone does not
│   ├── a compatibility-only route is labelled and cannot set the product memory claim
│   └── default route survives only if it satisfies ↳ [N0] ↳ [H5] ↳ [P6]
├── [X9] optional developer ecosystem — follows the core, never defines it
│   ├── Electron reference: stable App · Window · WebContents · Session concepts
│   ├── Wry reference: small engine/view adapter and platform event-loop boundary
│   ├── Tauri reference: manifest · scoped commands · permissions · plugins · packaging
│   ├── Surf mapping: runtime · surface · target · profile/session · typed capability channel
│   ├── future layers: Agent runtime → embeddable SurfView → optional Surf App framework
│   ├── adapters and plugins expose owner · scope · deadline · budget · audit · teardown
│   ├── unloaded ecosystem features have near-zero resident/process/dependency cost
│   ├── [x] first research artifact is a concept/capability mapping, not API compatibility: labs/ecosystem-reference
│   ├── all three references bind page lifetime to a window and none measures retention after teardown
│   ├── [x] ME1 typed capability envelope: optional per-request attenuation keyed on profile/session/target with scope · deadline · result budget · audit; surface-located or off-chain owners are typed refusals; synthetic court 33/33
│   ├── [x] ME2 adapter teardown ordering: adapters hold weak handles only; teardown detaches adapters → releases surfaces → drops the target → releases the profile lock and reports any extended owner reference; CDP adapter calls are attenuated to their target; synthetic court 24/24
│   ├── [x] ME3 attributable process metrics: the Lightpanda per-target Rust host reports host + children by opaque child/target, pid, role, lifecycle state and generation with resident and physical footprint, private declared unavailable; reconciled with the shared sampler at empty · 1 · 8 · post-close within a fixed bracket
│   └── [-] no Node-in-page default, generic IPC, engine-specific public model or 0.0.x framework build
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
control desk. Backends sit below that authority and may be investigated in
parallel without exporting their object models upward. The Window Dock attaches
or detaches without owning the session. Every route passes through the Memory
Court; only earned mechanisms flow toward the native browser core.

```mermaid
flowchart LR
    U["Two required outcomes [N0]<br/>memory-optimized AND Agent-use oriented"]

    subgraph ENTRY["Control doors"]
        CLI["CLI door [A3]<br/>bounded JSON · waits · actions"]
        CDP["CDP door [D4]<br/>discovery · WebSocket<br/>qualified domains · adapter-scoped frame ids"]
    end

    subgraph ID["Profile Cabinet [P6]"]
        PP["persistent profile [G4 synthetic · native-dom keychain slice]<br/>named · locked · sealed · bounded"]
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
        NATIVE["bounded native route<br/>DOM + QuickJS realm + bounded http fetch<br/>2.4 MB one target · 4.7 MB eight (footprint)<br/>post-close = live: attributed to allocator reservation, bounded and reused<br/>zone and arena repairs opt-in only · arena returns it at close without the zone's live cost"]
        COMPAT["compatibility route<br/>total process cost visible"]
        DECIDE["G5 route verdict<br/>keep · narrow · combine · reject"]
        LEARN["earned mechanisms<br/>limits · lifecycle · compatibility lessons"]
    end

    CORE["native browser-core convergence<br/>Rust · bounded ownership<br/>capabilities absorbed incrementally"]

    subgraph ECO["Optional ecosystem [X9]"]
        EMBED["SurfView embedding<br/>Rust API · later C ABI / SDK<br/>typed capability envelope: attenuation only (ME1 synthetic)"]
        APP["Surf App layer<br/>manifest · scoped commands · plugins"]
        MIGRATE["concept migration<br/>Electron · Wry · Tauri"]
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
    CTRL -->|one authority; backend adapters| LP & SERVO & NATIVE & COMPAT
    LP & SERVO & NATIVE & COMPAT --> BOOK
    PRESS -->|yes| DECIDE
    PRESS -->|no| FAIL
    DECIDE -->|keep / combine evidence| LEARN
    LEARN -->|adopt only with courts and budgets| CORE
    CORE -->|implements the same authority| CTRL
    CTRL -->|stable bounded contract| EMBED --> APP
    MIGRATE -. concepts, not inherited engine .-> EMBED & APP
    APP -. optional; no authority bypass .-> CTRL
    AT -. same protocol .-> CTRL
    MINI -. product family only .-> CLI
```

### 3a. Parallel-backend research doctrine

Backend plurality is a research and delivery strategy, not the product's
identity. The control plane and profile system must be able to select or place
a target on an eligible backend without changing what a profile, session,
target, revision, wait, budget, or typed failure means. A capability absent on
a backend is reported explicitly; the authority never silently emulates it by
opening an unmeasured browser.

The current roles are deliberately asymmetric:

- **Native bounded Rust route — convergence path.** Grow a browser core through
  measured vertical slices: parse/DOM, realm, network, storage, layout,
  presentation and broader Web behavior. Preserve its memory advantage with a
  budget and lifecycle court at every slice.
- **Lightpanda worker route — combine path.** Supply a low-memory Web-capable
  worker and comparison point while native coverage grows. Process-per-target
  isolation is an earned deployment option, not permission to hide aggregate
  memory or inherit CDP as the internal model.
- **Servo route — selective research source.** Continue to study Rust layout,
  rendering, embedding and native surfaces, but do not promote the measured
  macOS route while its graphics lifecycle fails the memory gate.
- **Chrome/system route — baseline and compatibility oracle.** Use it to define
  named Web/CDP behavior and the same-machine memory comparison, never as the
  MiniCon Surf core or an invisible fallback.

Research may therefore proceed concurrently and may add new backends when they
test a distinct hypothesis. Shipping convergence is stricter: every adopted
mechanism must preserve the single authority, pass the Agent contract, expose
its total resource cost, and either strengthen the native core or have an
explicitly bounded `combine` role. Backend-specific shortcuts do not become
public semantics. Over time, the native route should absorb the best proven
mechanisms so that multi-backend research increases confidence in, rather than
postpones, a MiniCon Surf browser core of our own.

### 3b. Ecosystem reference doctrine

Electron, Wry and Tauri answer a different question from Servo, Lightpanda or
the native route. The latter group helps test browser engines; the former group
helps design how developers embed, extend, package and reason about a mature
runtime. MiniCon Surf should borrow their successful concepts without
inheriting their engine choice or compatibility burden:

| Reference | Borrow | Do not inherit |
|---|---|---|
| Electron | stable application/window/page/session objects, close-versus-destroy and quit sequence, per-process metrics, Chrome-extension surface | bundled Chromium/Node cost, page-wide host authority, generic unbounded IPC, utility processes with ambient Node/network, sessions that cannot be destroyed, window-owned page lifetime |
| Wry | thin WebView/engine adapter, custom protocol hooks and platform event-loop integration | opaque system-engine behavior, no headless mode (visibility is a view attribute, not detachment), window-bound view lifetime, backend identity leaking into the public contract |
| Tauri | capability/permission/scope vocabulary, default permission sets, plugin lifecycle hooks, isolation pattern, packaging ergonomics | authority keyed on window label and origin instead of profile/session/target, scopes enforced by each command, build-time-only capabilities without deadline/budget/audit, plugins holding the whole app handle, webviews dropped with their window |

The intended long-term layers are separable deliverables: the Agent browser
runtime remains useful alone; an embeddable `SurfView` may expose Rust first
and later a C ABI and language SDKs; only after the native core and surface
contracts are earned may an optional application framework add manifests,
plugins and packaging. Conceptual migration guides and narrow adapters precede
any Electron/Tauri API-compatibility claim. Every layer is measured both loaded
and absent, and its processes, allocations and capabilities remain attributable
to a profile, target, surface or plugin owner.

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
| Native bounded route: html5ever DOM, bounded QuickJS realm, bounded `http` fetch with fail-closed address policy | shared journey 27/27; network court 35/35 on a hermetic representative page (fetch-driven results, click-triggered fetch, nineteen typed policy negatives, concurrency and budget caps, cross-origin script refused); footprint beside Lightpanda single server: empty 1.3 vs 8.4 MB, one target 2.4 vs 9.1 MB, eight targets 4.7 MB vs one-target limit | post-close footprint equals live at every stage; retained above empty 1.9 MB on the fixture court (Lightpanda 1.5 MB) and 4.6 MB after eight representative pages (Lightpanda 1.0 MB after one); attribution court: libmalloc in-use returns to empty, owners zero, reopen reuses the reservation, no per-cycle growth; the retention is freed-but-reserved default-zone memory that pressure relief does not release; a zone-per-realm repair (macOS only, accounting proven, p = 0.00058) cuts it to 0.7–1.0 MB but lifts first-open live footprint to 9.3–12.8 MB and halves usable growth capacity under the cap, so it is opt-in and not adopted | no layout, https, cookies, storage, images, fonts or real timers; scripts run after parse; DOM shim covers the fixtures and instrumentation only, not Web compatibility | **keep** as the route's measured base with a named retention risk; next: allocator/realm retention repair measured by the same courts, then a bounded profile store, then https |

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
  claim (no layout, network, storage or timers in this slice; the shim covers
  the fixtures and instrumentation only). The following measured slice adds
  bounded network fetch; it does not retroactively broaden this result.
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
- [~] The native slice's post-close retention is now attributed. A
  fresh-process court (static fixture, interactive fixture, representative
  page; eight targets; empty → live → post-close → `memory.trim` → reopen →
  reclose; one warm-up plus seven runs per cell) records footprint, RSS,
  logical owners and libmalloc in-use versus reserved at every stage. Under
  the default allocator libmalloc in-use returns to within 4,096 bytes of
  empty after the closes, every owner is zero, and the 3,309,568 to
  3,981,312 bytes retained are freed blocks kept in the default zone's
  regions; `malloc_zone_pressure_relief` releases nothing, and reopening
  eight targets costs only 98,328 to 163,864 bytes over the first live
  stage, so the retention is consistent with a bounded reservation that is
  reused, with no continued growth across one reopen; leak absence beyond
  this court is not claimed. The only repair that returned it, one
  libmalloc zone per QuickJS realm destroyed at close (macOS only; the
  allocator carries checked accounting and the 16 MiB limit because rquickjs
  disables its own under a custom allocator; reallocation charges the
  replacement before releasing the old block so failures keep the old block
  valid; zero blocks leaked at every destruction), cut retention to 720,896
  to 966,656 bytes with U = 0 and p = 0.00058 in all three workloads and kept
  the journey at 27 of 27 and the network court at 35 of 35, but lifted
  first-open live footprint to 9,257,296 to 12,779,856 bytes and left RSS
  at 9.8 to 13.5 MB after the closes. It fails the live criterion, so it
  stays an opt-in knob and the default is unchanged. The hard cap is not a
  guaranteed usable capacity: a dense array growing until the realm throws
  reaches 0.7067 of 16 MiB under the default allocator and 0.4752 under the
  zone allocator, because the zone path holds old and new buffers during a
  growth step by design. G1, G3, P6 and G6 stay open.
- [~] The realm heap arena named above is now measured on the same court
  with three arms (default, zone, arena; three workloads; one warm-up plus
  seven runs; `native-dom-control-0.0.2-retention-attribution-arena`
  receipt). Each arena realm is one 32 MiB private anonymous mapping served
  by a portable boundary-tag heap and unmapped when the runtime and its
  allocator have both dropped; the mapping is shared through a reference
  count, so no QuickJS block can outlive it, and the heap contract
  (alignment, exact usable size, null on exhaustion with the old block kept
  on a failed reallocation, abort on foreign pointers) is unit-tested with a
  randomized model check. The 16 MiB cap is QuickJS's own: the pinned
  quickjs-ng checks it before calling any allocator, which corrects the
  earlier note that the zone had to carry it. Against the same-court
  default arm the arena cut retained footprint from 3.3 to 4.0 MB to 0.67
  to 0.90 MB (U = 0, p = 0.00058 in all three workloads) without the zone's
  live cost: first-open live was 4,489,720 and 4,538,872 bytes on the
  fixtures against 4,653,368 and 4,735,288 (lower, U = 0) and 5,390,840 on
  the representative page against 5,308,728 (U = 15, p = 0.243, not
  distinguishable), where the zone needed 11.8 to 16.1 MB; RSS after the
  closes was 4.2 to 4.5 MB against 6.8 to 7.6 MB; the dense-array capacity
  reached 0.6805 of the cap against 0.7067 (default) and 0.4752 (zone);
  sixteen arenas were unmapped per run with zero blocks leaked. The
  27-item journey and 35-item network court pass under the knob. The arena
  stays opt-in: one platform, three small workloads and one reopen are not
  enough to make it the default, interior trimming and a soak remain
  unmeasured, and leak absence is not claimed. G1, G3, P6 and G6 stay open.
- [~] The ecosystem reference map (`labs/ecosystem-reference`, design only,
  no runtime dependency) reads Electron `44.1.1` documentation, the pinned
  Wry `0.55.1` and Tauri `2.11.x` sources and maps them onto the control
  0.0.1 vocabulary. Findings that corrected section 3b: all three references
  bind page lifetime to a window (Wry builds a view only from a window
  handle; Tauri drops a window's webviews on close and gates reparenting
  behind `unstable`); Wry has no headless mode and visibility never detaches
  a page; Electron sessions cannot be destroyed; Tauri's authority is keyed
  on window label, webview label and origin with scopes enforced by each
  command and capabilities compiled at build time; Electron's utility
  process is ambient Node plus network; no reference measures retention
  after teardown, and only Electron exposes a per-process metric shape worth
  borrowing. Five micro-experiments are named (typed capability envelope,
  adapter teardown ordering, process-metric shape, no ambient capability for
  workers, visibility is not detachment). No gate moves: G1, G3, P6 and G6
  stay open.
- [~] ME1 is done on the synthetic court. Control 0.0.1 gains one optional
  `capability` field on the request envelope (schema, checker, four examples
  and five negative self-tests first). Existing requests without the field
  are wire-compatible; a request carrying it is supported only on a host
  that implements the extension, an older host fails closed with
  `invalid_request`, and a caller that requires attenuation must not strip
  the field and retry; feature negotiation does not exist yet and is a gap
  for a later handshake. It is attenuation only:
  the host resolves the operation's ownership chain from its own state,
  requires the named owner to be the target, its session or its profile,
  the operation to be in scope, the deadline and result inside the budget,
  and records every decision with actor and reason in a 64-record ledger
  readable through `session.inspect`; a surface, frame or realm is never an
  owner, host-wide operations cannot be attenuated, and a capability cannot
  make a reserved operation work. The synthetic capability court passes 33
  of 33 and the G2, G4 and native-dom network courts pass unchanged. This is
  the [X9] capability channel shape, not a plugin system, not a grant store
  and not a second authority; no engine host carries it yet. G1, G3, P6 and
  G6 stay open.
- [~] ME2 is done on the synthetic court. An adapter (today the loopback
  CDP edge; later an embedder or plugin) holds only a weak handle to a
  target anchor that carries names, never state; the host tears a target
  down in a fixed order (adapters detached, surfaces released, then the
  anchor dropped after checking that its strong count is one, and at
  `session.close` the profile writer lock last) and reports the order and
  any extended owner reference in the close results and `memory.report`.
  Every native call an adapter makes is attenuated with an ME1 capability
  owned by its target, so an adapter never holds more authority than the
  target it is attached to. The synthetic adapter court passes 24 of 24
  (attach accounting, teardown while attached, typed detachment, explicit
  detach, session close with a surface and lock release, capacity, sixteen
  adapters detached at one close, zero owner references extended) and unit
  tests cover the stored-reference violation, which is detected and counted
  while the ledger still drops the owner. Safe Rust only, no ecosystem
  dependency, one adapter kind; G1, G3, P6 and G6 stay open.
- [~] ME3 is done on the Lightpanda process-per-target Rust host. Its
  `memory.report` now returns the host and every engine child by opaque
  ordinal and target, pid, role, lifecycle state (`running`, `zombie`,
  `exited`, `pid_reused` by recorded start time, `unreadable`,
  `exited_during_sample`) and spawn generation, with resident set and the
  kernel's physical footprint per process, sums named as sums, private
  bytes declared unavailable, unattributed descendants walked and listed,
  and a completeness flag that turns false the moment any child cannot be
  measured; the report is read-only and no operation consults it. Public
  libproc interfaces only, no protocol change. A new court reconciles it
  with the shared sampler at empty, one target, eight targets and post-close
  inside a bracket fixed before the run: 28 of 28 reconciliations agreed with zero
  findings, the report and sampler summed footprints were identical at
  empty (1,048,888), one target (10,257,296) and post-close (1,671,480)
  and 49,152 bytes apart at eight targets (74,470,440 against 74,454,056),
  and the child closed first was absent from both sides afterwards. The 27-item journey
  stays 27 of 27 and the shared retention court reruns within noise. G1, G3,
  P6 and G6 stay open.
- [~] The realm heap arena has now been soaked. A court whose rules and
  adoption criteria were committed before the first run drives 128
  open → use → close cycles per host process on the interactive fixture and
  the representative page, one warm-up plus seven runs per arm, samples
  live and post-close footprint, RSS, libmalloc, realm bytes and the
  arena's used, blocks, high-water, decommit, unmapped and leaked counts at
  thirteen fixed cycles, and fails any run in which a close leaves an
  owner, arena, block, mapping or target behind
  (`native-dom-control-0.0.2-arena-soak` receipt). Retained footprint after
  128 closes was 1,048,576 bytes (interactive) and 1,196,032
  (representative) for the arena against 2,146,304 and 2,326,528 for the
  default (U = 0, p = 0.00058), both arms flat over the last 64 cycles
  (arena +16,384 and 0, default +49,152 and +32,768), slopes 1.1–1.3 KB per
  cycle against 1.6–1.9; first-open live 557,080 and 770,072 against
  1,277,952 and 1,376,256; RSS after the last close 4.59 and 4.83 MB
  against 5.67 and 5.96 MB; the arena's reopen cost is higher (311–573 KB
  of fresh pages per open against 0–147 KB) and falls over the soak; dense
  capacity unchanged at 0.6805 against 0.7067; zero teardown violations in
  28 runs. All seven frozen criteria hold, so the arena is court-eligible on
  this court. A separate
  allocator-stress fixture (interleaved small and 32 KiB blocks, holes,
  repeated growth) is judged apart from the pages: the arena ends
  the script with 2.5× more touched space than live bytes and costs 0.8 MB
  more live footprint than the default, while the default keeps the whole
  18.5 MB after the close and the arena returns to 0.29 MB above empty. The
  arena's `memory.trim` is tail-only (it marks the free span after the last
  live block reusable and, after a reporting fix, counts only touched
  pages) and is not a close recovery. The default allocator is unchanged,
  the arena stays opt-in, nothing here is a cross-platform result, and leak
  absence is not claimed for any arm. G1, G3, P6 and G6 stay open.
- [~] The arena has also been soaked concurrently, under a court committed
  before its first run: 32 rounds per host process of a 1/2/4/8 ladder of
  mixed interactive and representative targets, use, an interleaved partial
  close whose order changes every round, a survivor-state check, a refill
  and an all-close, with footprint, RSS, virtual size, owners, libmalloc and
  arena statistics at every stage (`native-dom-control-0.0.2-arena-concurrent-soak`
  receipt). Peak footprint with eight
  targets was 3,735,744 bytes for the arena against 3,784,704 for the
  default in the first round and 4,210,880 against 4,227,096 in the last;
  the arena pays about 358 KB of fresh pages per open in every round where
  the default reuses libmalloc's pages, and that cost is flat; closing four
  of eight in an interleaved order returned 1,409,120 bytes in the arena arm
  and nothing in the default arm, with every partial close removing exactly
  its owners, realms and mappings and every survivor keeping its state;
  retained after the all-close was 1,392,640 (flat from round 8, slope 0)
  against 4,227,096 (slope 2.6 KB per round), U = 0, p = 0.00058; RSS after
  the last all-close 4.98 MB against 7.83 MB; capacity unchanged; 384
  mappings unmapped per run; zero rule violations in 14 runs; the 27-item
  journey and 35-item network court pass under both arms. K1 to K9 all hold,
  so the arena is concurrent-court-eligible on this court. The 32 MiB address-space reservation per live
  realm is recorded beside touched and physical bytes rather than waved
  away: eight live realms reserve
  268,435,456 bytes of address space, visible as a 268 MB step in the host's
  virtual size, while touching 2,627,712 bytes at a physical footprint equal
  to the default's; that per-target address budget is recorded as a cost a
  later platform must re-derive. Interior trimming follows its pre-registered signal:
  at peak the arenas' high-water exceeded
  used by 138,944 bytes, 5.6% of used, below the 25% and 1 MiB signal, so
  interior trimming stays deferred and the 2.5× ratio under the adversarial
  script stays an allocator risk on record, not a browser cost. The default allocator is unchanged and the arena stays
  opt-in; nothing here is cross-platform and leak absence is not claimed.
  G1, G3, P6 and G6 stay open.
- [~] D4 now has its frame/realm rules, designed before any engine carries
  them. Control 0.0.1 keeps four concepts apart: the target revision
  advances on any observable change, a frame id is minted with its
  browsing-context node and survives same-frame navigation (the main frame
  lives with its target, a child ends when removed or when its parent's
  document is replaced), a document generation counts replacements per
  frame, and a realm id names one (frame, generation, world) and is retired
  with its document, never reused. Enumeration is bounded and only through
  the owning target (`target.inspect` `frames[]`/`realms[]`, main first);
  `target.snapshot` takes optional `frame` and `realm` and names what it
  observed; a foreign, ended or unknown id is the same `not_found`; frames
  and realms are never capability owners. No operation was added:
  navigation on the synthetic host is a link click, and hosts without the
  optional arguments fail closed. The synthetic court passes 28 of 28 with
  native stdio and the CDP edge observing the same frames before and after a
  navigation through adapter-scoped `Page.FrameId`s that are never the native
  ids; realm identity, navigation events, nested frames, isolated worlds and
  document generation are recorded losses in the CDP mapping. D4 stays open
  until an engine host exposes frames and a named external client observes
  them; G1, G3, P6 and G6 stay open.
- [~] The native route now carries the frame/realm rules on real documents.
  Each target is one main frame with one main-world realm; ids are
  host-wide and never reused; the revision the caller sees is monotonic
  across navigations. A click on a link is a same-frame navigation: the new
  document is fetched under the target's own policy and budget (origin
  allowlist, redirects, sizes, deadline, address rules unchanged), parsed,
  given a fresh realm and its scripts run, and only then swapped into the
  live target, so a refused or failed navigation leaves document, realm,
  generation and revision as they were and is charged as a denied attempt.
  The native court passes 62 of 62 (the same 31 checks under the default
  allocator and the opt-in arena): identity and enumeration, foreign and
  unknown ids refused alike, the in-court fixture link and the same-origin
  loopback link navigating (generation 2 and 3 on the same frame, realms
  retired, old references stale), `https`, private-address, 404 and
  non-HTML links failing typed with the target untouched, owners at zero
  after the closes; the 27-item journey and 35-item network court pass on
  the same binary under both allocators. Losses on this host are recorded:
  no child frames, no capability attenuation (fail-closed
  `invalid_request`), no CDP frame projection. D4 stays open until a named
  external client observes frames through CDP; G1, G3, P6 and G6 stay open.
- [~] D4 has its first engine-host observation by a named external client.
  The native route now serves a bounded loopback CDP edge (`--cdp-port`,
  `--ready-file`; header and message bounds, masked frames, one connection at
  a time, 30 s timeouts) whose qualified methods were frozen before the code:
  each method is a control 0.0.1 operation executed by the host's main loop
  against the same target, frame and revision the stdio door uses; every
  session is an adapter record the host counts and detaches at `target.close`
  and `session.close`; `Page.FrameId` is adapter-scoped, differs from the
  native id and survives a same-frame navigation; `Runtime.ExecutionContextId`
  is never emitted and `Page.enable`, `Page.navigate`, `Runtime.enable`,
  `Network.*` and the rest are explicit `-32601`. `puppeteer-core 24.15.0`
  on Node.js v26.7.0, through `connect`, `targets`, `createCDPSession`,
  `session.send`, `detach` and `disconnect` only, passes the court 58 of 58
  under the default allocator and the arena: it discovers exactly the native
  targets, reads the frame tree, clicks the in-court link, and stdio verifies
  revision +1, generation 2 and a new realm while the CDP frame id stays;
  sessions never see another target's frames; a target closed over stdio
  turns the session into a typed failure; adapters and owners are zero after
  detach, disconnect and close. Two post-freeze revisions (events before
  responses; adapter counts equal the client's sessions) are recorded in the
  matrix. `target.page()`, Playwright and any other engine or client remain
  outside the claim, so D4 stays open; G1, G3, P6 and G6 stay open.
- [~] The native route carries the first engine-backed profile slice, designed
  and decided (D1–D6) before the code: a persistent profile is one sealed
  record (XChaCha20-Poly1305, per-profile data key, identity-bound additional
  data) whose master key lives only in the macOS Keychain with user
  interaction disabled, failing closed as `unsupported_capability` when the
  keychain is unavailable; cookies follow an RFC 6265 subset with `Domain`
  equal to the request host only, no `Secure`/`SameSite=None` on the `http`
  cell, and a volatile session jar shared by a profile's sessions in sequence;
  `localStorage` is origin-keyed and budgeted; every committed mutation is
  written through (temp, fsync, rename, directory fsync) and a failed commit
  reports `internal`/`storage_commit_failed` and leaves the profile read-only.
  The frozen court, run with fake values only and a feature-off baseline,
  passes 80 of 82 under the default allocator and the arena: the store's
  empty-footprint and RSS deltas, the per-profile accounting, isolation,
  matrix negatives, HttpOnly, session-cookie lifetime, fault injection,
  budgets, at-rest absence of the value, permissions, restart, corrupt
  sibling and locking all hold; the "well below Lightpanda" total-live
  criterion (frozen as half of the single-server empty footprint) is unmet
  at 6,111,688 / 5,849,592 bytes because realm and target-churn cost, not
  the store, dominate the churned host. The cap does not move and the slice
  is `narrow`, not `observed`; the journeys (27/27, 35/35) and the frame and
  CDP courts (62/62, 58/58) stay green on the same binary. Second-platform
  key source, `https`, cache, history and permissions remain P6 work; G1,
  G3, P6 and G6 stay open.
- [~] Verdict on the unmet criterion: the v1 court, its 80/82 and the
  `failed`/`narrow` status stand, no v2 re-freeze. A read-only attribution
  court (fresh process per run, one warm-up plus seven, the same 28-open
  timeline, four arms, both allocators, samples after every open and close)
  splits the churned footprint: without the store and with equal churn the
  default-allocator host already ends at 4,489,552 bytes, above the half
  line, because freed blocks stay in the default zone's regions and no close
  in any run lowers the footprint (the arena releases at all 28); the store
  costs 262,144 at enable and about 2.1 MB once, at the first keychain call,
  542,560 of it heap that never returns, while records, jar and mirrors add
  245,760 (default) / 507,904 (arena). So the default cell of the criterion
  belongs to allocator retention (G3) and no store change can meet it; the
  arena cell is crossed by the one-time keychain cost. One fix candidate is
  recorded with pre-registered gain and regression criteria (keychain access
  through a short-lived helper process, wrap once at create/open); it is not
  implemented. Security review recorded: the master-key item's default ACL
  binds decrypt to the creating build's `cdhash`; a rebuilt host is refused
  unattended (`-25293`, no prompt, fail closed, item and record untouched),
  so the no-UI mode is a fail-closed guarantee and not an unattended
  deployment guarantee. G1, G3, P6 and G6 stay open.
- [x] The approved Keychain helper experiment (arena cell) was implemented
  under its fixed constraints (posix_spawn through the standard library,
  same signed binary, anonymous pipes with fixed-length versioned
  envelopes, descriptor whitelist, no core dumps, zeroized keys, wrap once
  and re-seal with the cached data key, reaped child, kilohertz sampling of
  the complete process tree) and measured against its frozen court: five of
  six criteria hold (step cost 81,920; in-use within 2,368; churned
  total-live down 1,802,360 / 2,064,504; no descendant, kill or failure;
  clean exits) and the arena cell of the v1 court passes on that build
  (81 of 82). The complete-tree peak criterion fails on both allocators
  (5,735,192 against 4,391,392 / 5,423,632): a second process of the same
  binary pays the Security framework cost plus its own runtime baseline,
  so the transient peak is structurally above the in-process peak. Per the
  approval the experiment changes nothing: the in-process host is restored,
  the implementation and receipts stay in history, the P6 slice stays
  `narrow`, and P6 work moves to another gap. G1, G3, P6 and G6 stay open.
- [~] HTTPS for the native route, design and measurement first: the
  pinned-roots-only slice, its cookie and redirect rules and the candidate
  selection criteria S1–S10 were frozen before any TLS dependency; the
  standalone `labs/tls-court` probes measured rustls+ring, rustls+aws-lc-rs
  and macOS SecureTransport against a hermetic loopback server with
  disposable, never-committed fixtures (60 of 65). rustls+ring meets every
  criterion (TLS 1.3 and 1.2 with pinned roots, names and IP SANs verified
  in process, negatives refused before HTTP, resumption with a finite
  per-process cache, 65,536 bytes idle, 32,768 first handshake, about 42 KB
  per live connection, heap returned after close, one process and thread,
  no dynamic library; 1.53 MB of binary and ring's C/perlasm crypto);
  aws-lc-rs keeps 153 KB of heap after close; SecureTransport is TLS
  1.2-only on this macOS, deprecated, two extra threads and system daemons
  outside the tree, and misses three memory criteria. Two mechanism
  amendments are recorded (rustls's cache needs 16 entries to resume;
  SecureTransport refuses an explicit 1.3 maximum). Recommendation rustls +
  ring, awaiting ruling; nothing merged into the native route; no public-web
  claim. G1, G3, P6 and G6 stay open.
- [x] The ruling adopted rustls + ring, and the native route now carries
  the pinned-roots HTTPS slice as an opt-in, explicit-policy capability:
  `--pinned-root` public certificates under fixed bounds, `https` allowed
  only for allowlisted origins and otherwise `unsupported_capability`, no
  system roots, TLS 1.3/1.2 with ALPN `http/1.1` only, names and IP SANs
  verified in process, exact-address authorization before the connect,
  per-hop re-authorization with downgrade refused, the http caps and
  deadline unchanged, a 16-entry per-profile session cache, `Secure` and
  `SameSite=None` cookie rules against the http origin of the same host,
  atomic failed navigation. The frozen native court passes 74 of 74 under
  both allocators with the pre-registered host increments at most 262,144
  bytes for the first https target and 79,872 per further target against
  caps of 1,048,576 and 131,072; its 40 KB header fixture exposed a
  chunk-granular response header cap, fixed to an exact bound (cap−1, cap,
  cap+1 covered by unit tests and the court, the request head bounded the
  same way) before any push; the binary grows by 1.5 MB; the P6 v1
  court stays 80 of 82 and every regression holds. The stack is Rust for
  TLS and verification with C and perlasm primitives inside `ring`: not
  pure Rust. Pinned loopback roots only, no public-web claim; G1, G3, P6
  and G6 stay open.
- [x] Persistent Secure cookies across a restart, court frozen first and
  passed 78 of 78 under both allocators: a verified https origin's
  persistent Secure cookie survives the restart through the keychain-sealed
  record and is sent again over https only, never over http nor to an http
  document nor to the same server under another host name; the volatile
  session cookie is never persisted, an expired cookie (injected clock
  offset, no sleeps) and a past `Expires` are dropped, `Max-Age=0` deletes;
  wrong-name, unpinned and failed-navigation negatives leave jar counts and
  record hashes unchanged; a second profile at the same URLs is isolated;
  no value, cookie name or storage marker appears at rest; without a pinned
  root the persisted Secure cookie stays locked. G1, G3, P6 and G6 stay
  open.
- [~] G3 native surface, design and measurement first (macOS only): the
  probe criteria S1–S9, the candidate matrix and the court stages were
  frozen before any probe; standalone probes for direct Cocoa (objc2) and
  winit + softbuffer, with a plain-buffer control, ran one warm-up plus
  seven runs of three show/hide rounds each over the complete process tree
  (34 of 42). Both candidates show a real OS window with a window number,
  read their own pixels back, keep the owner and backing at 0 → 1 → 0, stay
  one process and hide within milliseconds, and both fail the post-hide
  criteria: the first AppKit window costs about 10 MB of footprint and
  13.6 MB of heap on the direct path (17 MB and 16.7 MB on winit) that
  closing the window does not return, each further round leaves about
  0.15 MB (Cocoa) or 1.3 MB (winit), and Metal and OpenGL are AppKit's own
  link dependencies before any window exists. The direct Cocoa path is the
  smaller one on every differing axis. Recommendation for ruling: a surface
  process on the direct Cocoa path, spawned by show and ended by hide, so
  the host's post-hide footprint is headless by construction; no surface is
  merged into the native host. Wry/Tauri stay rejected on the X9 evidence.
  G1, G3, P6 and G6 stay open.
- [~] The ruling adopted the surface-process design and the native route
  now carries it as a macOS prototype: a separate minimal `native-dom-surface`
  binary (direct Cocoa, CPU bitmap, accessory policy) spawned by
  `surface.show` through `posix_spawn` and ended by `surface.hide`, bounded
  binary IPC over the child's stdio pipes (20-byte headers, generation and
  sequence on every message, per-kind bounds, deadlines, one frame in
  flight, kill and reap only as counted failure cleanup), a host-side
  bounded semantic painter with a hit map, human input as a third source of
  the host's multiplex loop applied while idle, engine-neutral public
  results with a court-only file for window facts, owners.surfaces with
  process counters and no pid or path in receipts. The frozen native court
  passes 106 of 110 under both allocators: real window, own-window capture,
  real click and scroll applied before any request, CDP session unchanged,
  protocol exit with the child reaped in about 10 ms, owners to zero,
  target, frame, realm and scroll continuity, kill and stop failure modes,
  no stale input. The post-hide host footprint (1.26 to 1.52 MB over
  headless) and the slope (147 to 262 KB) exceed the pre-registered caps,
  attributed to the spawn machinery's own 1.13 MB and the default zone's
  cache of the freed frame; every regression holds. Verdict `narrow`; G1,
  G3, P6 and G6 stay open.
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
   P6 work. [~] The native route's first engine-backed slice (keychain
   envelope, cookie jar, `localStorage`, write-through) passes its frozen
   court except the total-live footprint criterion.
6. [~] Run independent `labs/{techName}` spikes behind the established
   contracts concurrently where their hypotheses are independent, publish
   comparable memory and Agent-control evidence, and issue an explicit
   keep/narrow/combine/reject verdict for every route. Parallel labs share
   courts and product authority, not implementation dependencies. The G5
   ledger now holds Servo (narrow), Lightpanda (keep + combine), Chrome
   (baseline), synthetic (keep) and the native DOM slice (keep as floor);
   every route except Chrome runs the same control `0.0.1` journey and the
   same retention court.
7. [~] Continue the earned routes in order. The Rust process-per-target
   Lightpanda host is done at 1.9 MB empty. The native script-realm slice is
   done at 27/27 and 2.5 MB for one target; its bounded-network slice is done
   at 35/35. Its post-close retention is attributed to allocator reservation
   and returned by an opt-in arena per realm without the zone's live cost on
   one platform, and holds through a 128-cycle soak under frozen criteria.
   Next: a second platform behind the arena's region boundary and interior
   (not only tail) trimming; the D4 frame/realm rules hold on the synthetic
   (28/28) and native (62/62) hosts and `puppeteer-core 24.15.0` observes the
   native frame tree over a bounded loopback edge (58/58), so the next D4
   steps are a second named client and page-level APIs only if their events
   can be projected honestly; then bounded engine-backed profile work. Rerun Servo only when a driver-free rendering
   context exists. G1 closes only when one route is both materially below the
   baselines and low-slope on the shared court.
8. [~] Treat the native bounded route as the convergence path. After each
   backend experiment, record which mechanism or constraint was learned,
   whether it belongs in the native core, and which existing court prevents a
   memory, lifecycle or Agent-semantics regression. Keep a non-native backend
   in a shipped `combine` role only when that role is explicit, bounded and
   useful beyond what the current native slice can safely provide.
9. [~] Maintain an engine-neutral ecosystem concept map for Electron, Wry and
   Tauri, including lifecycle, capability and resource-ownership mappings.
   The first map (`labs/ecosystem-reference`, Electron 44.1.1, Wry 0.55.1
   source / 0.56.1 docs, Tauri 2.11.x, read 2026-09-03) names five
   micro-experiments; ME1 (a typed capability envelope on the synthetic
   host) is done at 33/33, ME2 (adapter teardown ordering) at 24/24 and
   ME3 (attributable process metrics on the Lightpanda per-target host)
   reconciled at every stage; ME5 waits for a real native surface. This is design
   input only during 0.0.x: do not build a plugin framework, Node
   compatibility layer or application packager before G1/G3/P6/G6 and the
   native embedding boundary have earned them.

The first code milestone is therefore not “render a website.” It is “one
bounded target has one identity and state while CLI, CDP, and an optional
window observe and control it without changing its lifetime.”
