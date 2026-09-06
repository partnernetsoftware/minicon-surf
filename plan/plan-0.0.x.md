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
  no stale input. The post-hide host footprint (1.2 to 2.6 MB over
  headless, one or two copies of the freed frame depending on the run)
  and the slope exceed the pre-registered caps, attributed by the paired
  court below to the default zone's cache of the freed frame; every
  regression holds. Verdict `narrow`; G1, G3, P6 and G6 stay open.
- [~] Paired attribution of that retention (read-only, twelve cells, eleven
  in-process stages): the retained bytes are one or two copies of the
  freed frame kept by the default zone plus small-block churn, scaling
  with the frame size (about 1.25 MB, 0.46 MB and 0.26 MB after one round
  for 1 MB, 256 KiB and 64 KiB frames) and independent of the child: a
  child that
  speaks the protocol without AppKit, one that keeps frames and one that
  exits at once leave the same host retention as the real window; the
  spawn itself costs 0 to 33 KB (the earlier 1.13 MB reading had already
  paid for the frame), the reader thread's stack returns at join, in-use
  returns to within 7 KB, `memory.trim` releases nothing. Candidate for
  ruling, not implemented: a dedicated `mmap` region for the frame released
  at hide, expected to bring the post-hide excess near the cap and remove
  the two-copy variance but not the small-block slope. The surface court
  stays 106 of 110, narrow; G1, G3, P6 and G6 stay open.
- [~] Implemented and qualified on the native route, court 50 of 50:
  page-initiated navigation
  (`labs/native-dom/page-navigation-design-0.0.1.md`). It closes a silent lie
  rather than an absence: today `location.href = "…"` succeeds, the page reads
  back the new value and believes it navigated, and the host commits nothing.
  The design covers the `href` setter, `assign` and `replace`, main frame
  only, and invents no history, `pushState` or hash semantics. Its core rule
  is that a realm evaluation never re-enters fetch, build or swap: the page
  records a **navigation intent** into a host-owned sink and the host consumes
  it at a boundary, never between an activation's two preflight phases and
  never inside a lifecycle step. One slot per realm, last write wins, with the
  browser-compatible reasoning and the deliberate divergence recorded — a
  browser may have begun a fetch this host never makes. A consumed intent
  reuses the existing typed preflight, policy, TLS, budget and atomic
  build-then-swap and adds no authority; `assign` adds a metadata-only history
  entry and `replace` replaces one without changing the ring's length. An
  intent raised while a document is still being built becomes a finite
  redirect-like chain, capped at 3 by analogy to `MAX_REDIRECTS`, under one
  deadline and one budget, with no intermediate document ever observable; from
  a live realm, a failed navigation keeps the handler's mutations and leaves
  every identity unchanged. Eleven criteria are pre-registered and three
  blockers go to the root: the cap itself, whether a host navigation should
  discard a pending intent, and whether `location.reload` belongs in scope.
  All three were ruled: the cap stands at 3, an explicit caller navigation
  discards a pending intent as `caller_override`, and `reload` is in scope.
  The implementation follows the design; five further corrections are
  recorded in it, each before the change it justified. Three were the
  court's own: a reload fixture that reloaded forever because a rebuilt
  document loses any in-page flag, a caller-override group that read the
  counters at an operation that is not a timer boundary, and a seam
  criterion that asked only for acceptance. The court-only hold seam is
  doubly constrained: `--court-hold-intent` is refused with exit 64 before
  the host serves anything unless the private `--surface-court-file` is also
  given, that file is created after every configuration exit so its
  destructor cannot be skipped, and it is gone when the host is; all three
  are criteria, not claims. The fifth is a memory correction: the slice
  cost about 7.7 KB per child realm and broke the frozen child-frame M1 cap
  at 262,970 against 262,144. The cap did not move. A single pre-registered
  narrowing — the accessor/intent form only on script-running realms, the
  plain object on script-free child frames, which no page code can tell
  apart because a child runs no script — brought it to 261,354 and M2 to
  1,827,196. That recovery is **partial**: a child still costs about 6.1 KB
  more than before this slice, because the shim source every realm compiles
  grew. A root code audit then found three things the 50-check court could
  not see, each recorded with its falsifier before the fix: a take that
  failed at the deadline or answered malformed output was indistinguishable
  from a page that raised nothing, so an operation reported success and the
  intent could commit at a later, unrelated boundary; the page's address was
  unbounded in the realm and crossed unbounded, skipping the byte, absolute
  and scheme checks every caller address passes; and the design promised the
  same audit every navigation gets while the code wrote no ledger record at
  all. A take now returns a typed failure or an intent, a failed take
  poisons the target so the stale slot is emptied and discarded before page
  code runs again, both ends bound the address with one fixed redacted
  reason, and every page-initiated navigation — live or during a build —
  writes one bounded record naming a kind, an outcome and at most an origin.
  The design also called the intent slot host-owned; it is realm-closure-owned
  and host-taken, which is exactly why a failed take can leave it stale, and
  the wording was corrected rather than the code. The court is now 76 of 76,
  30 of 68 against the build before the slice and 56 of 72 against the build
  the audit judged, where it fails every criterion the audit added and no
  other. A further audit found the ledger calling abandoned chain candidates
  committed: the link a page asks for was recorded as soon as its build
  returned, before the candidate was checked for another intent, so a
  three-link chain left three commits where exactly one document was ever
  observable. A link is now judged after its candidate is — abandoned is
  `superseded`, only the visible document is `committed`, a build failure
  keeps its typed outcome and a chain refused at its cap records no commit at
  all. The court is 80 of 80, and 76 of 80 against the build that ruling was
  found in. The receipts committed on that binary are the page-navigation and
  child-frame ones; the rest of the same-binary regression suite was run
  against it as scratch results that were not retained, and their committed
  receipts still name the earlier build. **M1 is left with 182 bytes of
  headroom**, 261,962 against an unmoved 262,144, so further growth of the
  shared shim is blocked until a separate architecture slice reduces what
  every realm compiles. G1, G3, P6 and G6 stay open.
- [~] Implemented and qualified on the native route, court 28 of 28: bounded
  `Event` fidelity (`labs/native-dom/event-fidelity-design-0.0.1.md`). Written
  from twenty-three measurements through the control door rather than from the
  source: the dispatch shape was already right, and what was wrong was the
  event object's integrity, three absent members and one dispatch rule. One
  finding had authority in it — the host reads `defaultPrevented` to decide
  whether an activation proceeds, and the field was plainly writable, so on a
  link a handler that assigned it cancelled a host-driven navigation without
  ever calling `preventDefault`. The court's dry run found a second: a handler
  that dispatched the event it was handed recursed until the engine raised
  `RangeError`. My own plan was overturned by the root's audit before any
  code: a main-only `Event` subclass would have missed the events the base
  itself raises from `click`, `submit` and `reset`, one of which reads
  `defaultPrevented` back — zero base growth was a hole, not a smaller fix.
  Ruled and built: one faithful `Event` and one dispatcher in the base, state
  closure-owned and keyed by the event, `preventDefault` the only door to
  `defaultPrevented`, the dispatcher the only writer of `target`,
  `currentTarget`, `eventPhase` and `dispatching`, a re-entrant dispatch
  refused with `InvalidStateError` before the outer dispatch is touched,
  cleanup on every path including a throwing one, a listener removed during a
  dispatch not called, `stopImmediatePropagation` honoured, `isTrusted` false
  everywhere and `timeStamp` from the clock the realm already inherited. This
  is the first slice to spend the shim split's margin deliberately: the base
  grew 3,230 source bytes, M1 moved to 232,938 and M2 to 1,628,284, both under
  the 245,760 and 1,720,320 floors, leaving 12,822 bytes of M1 headroom under
  the floor. A second root audit then found four more, each measured before
  its fix: a listener removed and re-added during a dispatch still ran,
  because the recheck asked the live list about a callback rather than asking
  a registration whether it was removed; a stop flag set before a dispatch was
  discarded and a completed event's flags were left set, both from clearing at
  the start instead of the end; dispatching a plain object answered true,
  reporting a dispatch that never happened; and a listener added under the
  number 1 never matched an event typed "1". A fix of mine was caught in the
  working tree before it was committed and is recorded as wrong: a per-dispatch
  set of removed callbacks suppresses that callback wherever else it is
  registered and broadcasts removals to unrelated dispatches, because identity
  is the registration and not the callback. As built: listener records with a
  removed bit, snapshotted per dispatch; flags cleared only at completion; a
  real `TypeError` for a non-event; listener types converted to strings. The
  court is **40 of 40** on `ec20ffab6af3…`, 8 of 28 against the build before
  the slice, 28 of 36 against round one and 38 of 40 against the build before
  the `TypeError` correction. M1 is **233,530** and M2 **1,632,428** against
  unchanged floors of 245,760 and 1,720,320.

- [ ] Design-only, nothing implemented and no court frozen: reclaiming
  main-extension slack (`labs/native-dom/main-slack-reclaim-audit-0.0.1.md`).
  The brief was 1,168 bytes. **Measured, the capability-free ceiling is 848**:
  removing **every full-line comment in the main extension — 8,063 source
  bytes, a third of the file — reclaims 832**, and consolidating three
  accessors into a table reclaims 16. Prose and structure are nearly free
  here; **runtime members are the whole cost**, at 512 to 832 bytes each, and
  ten trivial added members cost 8,320. Removing `CustomEvent` would reclaim
  1,856 and drop `event-fidelity` to 60 of 62, which is a capability ruling
  rather than slimming. **But the premise turned out to be wrong**, which is
  the useful result: T2's 1,168-byte overrun was one *implementation* of the
  quota, a counter object and a closure, not the quota itself. **T3b does the
  same job with no new state** — `timeout()` refuses when the page's existing
  timer table is fuller than the reserve — and measures **62,016 of 65,536,
  3,520 to spare**, with exactly T2's semantics: fourteen signals accepted,
  forty-eight timer slots kept for the page. `shim-footprint` 18/18,
  `child-frames` 82/82, M1 and M2 unmoved. So R6 needs no slimming at all, and
  the standing lesson is that **a shape's cost is not its source size**: a
  design that misses a bound should be re-shaped and re-measured before a
  capability is given up to fund it. G1, G3, P6 and G6 stay open.
- [ ] Design-only, nothing implemented: readonly profiles
  (`labs/native-dom/readonly-profile-audit-0.0.1.md`). **The field that made
  this look cheap is already taken and means something else**: `read_only` is a
  **fail-closed latch**, set when a write fails to commit to the sealed store,
  after which writes are refused `commit_failed: "storage is read-only after an
  earlier failed commit"` — and it is **court-pinned** at
  `profile-court.py:326`. A client reading it today learns *this profile
  broke*, not *you asked for a profile that does not write*. So the report
  exists but is spoken for, and the triage's "cheapest capability" framing was
  too quick. Measured protocol surface: `profile.create` accepts exactly
  `{persistence, name}` and `profile.policy.set` exactly
  `{session, network, permissions}`, both enforced by the host rather than by
  `check_contract.py`, which pins the operation enum and envelope — so a mode
  is a host argument change plus contract examples, not a schema-enum change.
  Recommended shape: a `mode` argument on `profile.create` reported as **its
  own field**, leaving `read_only` alone so the frozen criterion keeps passing;
  policy.set is the wrong scope (session, not profile) and a new operation
  grows the closed enum for no gain. Four further recommendations, each a
  ruling: the mode belongs to **the open and is not persisted**, so one
  mistaken call cannot make a profile permanently unwritable; `ephemeral` plus
  `readonly` is refused as `invalid_request`; the **writer lock is still
  taken**, because letting a readonly attach skip it would quietly add
  multiple-reader concurrency with its own consistency questions; and the two
  refusals — asked-for versus latched — must be **typed differently**, which is
  the constraint that makes this more than a flag. Memory, retention and
  redaction are all nil: profiles measure about 16 KB and the mode is a
  two-word closed vocabulary. A nine-criterion court draft is in §6, whose
  fifth criterion is the important one: the latch still behaves, so the old
  meaning was not eaten. **Ruled as Option A and then stopped before
  implementing**: the court and the contract were frozen against it — the
  contract gained its first profile coverage, an example pair and four negative
  cases, and its summary line was corrected from fixed numbers that had drifted
  to computed ones, 24 examples and 38 negative cases — and implementing then
  uncovered a **lifecycle hole the audit had not measured**. A persistent
  profile is **adopted at startup** and never passes through `profile.create`
  again: after a restart it is in `profile.list` as available, `profile.create`
  for that name answers **`conflict`**, and the way in is `session.open`. So a
  `mode` on create describes only a profile's first creation, and **a readonly
  open of an existing profile is unreachable** — exactly the case readonly
  exists for. Options measured in §8.1: **A′ `session.open {profile, mode}`**,
  recommended, the same size of change and per-client; B′ a host startup flag,
  no protocol cost but whole-host and invisible to a second client; C′ a new
  `profile.open` operation, which grows the closed enum. **Ruled A′ and
  built**: the mode is an argument of `session.open`, per session and not
  persisted, and `profile.create` does not take it — pinned by a contract
  negative. A readonly session refuses writes with `unsupported_capability` /
  `session_read_only`, **deliberately distinct from the latch's
  `commit_failed`**, so a client can tell *you asked* from *the store broke*;
  an ephemeral profile cannot be opened readonly; the writer lock is still
  taken. The court was refrozen against A′ and reads **28 of 28** against **4
  of 6** on the build before, the count growing once the argument exists
  exactly as its limitation said. **The shape moved twice and both moves are
  recorded**: a mode on `profile.create` cannot reach a profile adopted at
  startup, and a court written for two live sessions cannot pass a host that
  allows one per profile — §9.1. Both constraints appeared only when the code
  was written, not when the design was read. **The latch criterion passes on
  both arms**, which is what this slice existed to protect. Twenty-nine
  receipts rerun on the binary; `-profile` reads **90 of 94 and so does the
  pre-implementation binary**, so the two failures beyond the known D6 pair are
  machine drift in the store's `resident_delta` rather than this slice. G1, G3,
  P6 and G6 stay open.
- [ ] Design-only, nothing implemented: the page's own activation is silent
  (`labs/native-dom/page-activation-silence-audit-0.0.1.md`). **This corrects
  the downloads audit's framing**: the silent `link.click()` is not a download
  defect but an instance of a general one. Measured, a page navigating itself
  works through **`location` only** — `location.href` and `location.assign`
  both land on the new document — while **`link.click()` and `form.submit()`
  do nothing at all**: the click event dispatches normally, listeners run,
  `dispatchEvent` returns `true`, and then the target stays put and the server
  never sees the request. Recommendation, split by what a page is entitled to
  observe: **A for links and forms** — activation raises the same intent
  `location` already raises, which is a fidelity fix with **no new authority**,
  since a page can already navigate itself with one line of `location.href`;
  and **B for `a[download]`** — the page still observes nothing, because a
  browser page observes nothing there either, while the **agent** gains a
  closed-vocabulary record of the attempt. An eight-criterion court draft is in
  §5, whose fourth criterion writes the ruling's own constraint as a check: no
  host control error text reaches the page, `click()` still returns
  `undefined` and throws nothing. G1, G3, P6 and G6 stay open.
- [ ] Read-only locator check: G1's baseline binaries
  (`labs/native-dom/g1-locator-audit-0.0.1.md`). Nothing downloaded, built,
  compared or run. **Both baselines are absent, and the block is now priced.**
  Lightpanda 0.4.0 is missing from `target/labs/lightpanda/0.4.0/`, from
  anywhere in the repository (`labs/lightpanda/` holds evidence JSON only) and
  from `~/.local/bin`, `/usr/local/bin`, `/opt/homebrew/bin` and `~/bin`; the
  wider filesystem was deliberately **not** swept, since that is a different
  permission than a locator check. Everything else the runner needs is present:
  Chrome at the expected path, `cargo`/`gh`/`python3`/`shasum` all four, the
  fixture, and a `process-tree-sampler` that resolves offline. `servo-control`
  is a `[[bin]]` of `minicon-surf-servo-api-probe` and is **not built**, and the
  striking part is how close it is: **772 of 800 registry packages are already
  in the local cargo cache**, `servo 0.5.0` among them, but `cargo fetch
  --offline` still refuses — on `freetype 0.8.0`, and `cargo metadata` on
  `anstyle-wincon 3.0.11`. Most of the 28 missing are for **other platforms**
  (Windows `anstyle-wincon`/`dwrote`, OpenHarmony `ohos-*`, `hermit-abi`,
  `libfuzzer-sys`), which this build would never compile — cargo's resolver
  wants them present regardless. So the Lightpanda half needs **one artefact**
  whose digest the runner already pins (`840547bb…`, `exit 65` on mismatch, so
  a supplied file needs no trust), and the Servo half needs **28 crates**. The
  native-dom arm is built and current (`ba46420b…`) and is an optional
  argument, so the harness would run the moment the Lightpanda binary exists.
  Two independent authorisations and the exact follow-up commands are in §5.
- [ ] Design-only, nothing changed: what an element costs a realm
  (`labs/native-dom/element-scaling-audit-0.0.1.md`, receipts
  `evidence/native-dom-control-0.0.2-element-scaling{,-round-c,-pre-round-c}.json`
  and `-attribute-store.json`). No product code, court, criterion, cap, floor or
  protocol touched; no cap moved; two comparison binaries were rebuilt from
  committed commits in throwaway worktrees outside the checkout and left there.
  **The element-fact programme was priced where its cost is smallest.** Every
  round measured its fixed per-realm cost against a frozen ceiling and passed
  honestly; none measured the term that scales with the page. Measured on three
  builds, system arm: **1,097.4 → 1,217.6 → 1,329.6 bytes per element**, so
  round C bought **+120.1** and round D **+112.0**, a **+232.2 (21%)** total,
  while the per-attribute cost never moved (229.8 on all three, to the byte).
  The fixed term behaved exactly as `first-realm-engine-audit-0.0.1.md`
  predicted — 3.47, 4.30 and 3.97 bytes per byte of shim source against its
  ~3.6 — so **that yardstick is confirmed and prices only one of two terms**.
  The caps did not fail; they were never pointed at this: read on the
  child-frame court's ~8-element fixture, round D's per-element term is 896
  bytes against a fixed 2,560, but at 1,500 elements it is 65.6× it. The
  consequence with a name: the 16 MiB realm limit falls from **14,991 to 12,370
  elements (−17.5%)**, the price of closing F5, F1 and F2 — **possibly the right
  trade, but never stated as one**. A second question was asked and answered
  **no**: `Element.prototype.__attrs` hands the page the record's live `Map`,
  but in ten arms every write through it lands exactly where its honest
  `setAttribute`/`removeAttribute` twin lands, so **F1 and F2 stay closed** and
  the pairing is the only reason that is a result rather than an omission. One
  unmeasured difference is recorded as a question, not a claim: a direct write
  skips the mutation record, so whether a page can move an attribute without the
  revision moving needs a post-snapshot write this probe cannot produce.
  `open-goal-triage-0.0.1.md` §8 was amended chronologically, its original
  ordering kept: item 1 was struck, because
  `first-request-cost-audit-0.0.1.md` had already measured the first-profile
  cost out of existence. Not pushed. G1, G3, P6 and G6 stay open.
- [x] Ruled and recorded: a binary hash is a same-path provenance token
  (`AGENTS.md`, "Lab discipline"; linked from `labs/native-dom/README.md`'s
  receipt-provenance section and measured in
  `reproducible-build-audit-0.0.1.md`). **Nothing about the build changed** — no
  cargo setting, no build script, no product code, no court, no bound, and no
  historical receipt rewritten. The rule states what the audit measured: every
  ledger hash was produced by building in place in one checkout at one absolute
  path; rebuilding the same commit **at that path** reproduces it byte-exactly
  and rebuilding it anywhere else does not, because cargo derives `-C metadata`
  from the package's absolute path and rustc hashes that into every mangled
  symbol name. So a hash answers *which build produced this receipt* and is
  **not** a claim anyone else can check from source. The two options that look
  like fixes are named as measured failures so nobody adds them hopefully:
  `--remap-path-prefix` changes the output without making two paths agree, and
  `RUSTFLAGS="-Cmetadata=…"` loses to cargo's own flag. Cross-machine
  re-derivation would take a fixed build path shared by everyone who builds — a
  container — which is unmeasured and would be its own round. The rule went into
  the repository's actual tracked owner; **no external governance file was
  invented**, consistent with the ruling of `2c87f29`. Not pushed. G1, G3, P6
  and G6 stay open.
- [ ] Design-only, nothing changed: why two builds of one commit differ
  (`labs/native-dom/reproducible-build-audit-0.0.1.md`, receipt
  `evidence/native-dom-control-0.0.2-reproducible-build.json`). No build script,
  cargo setting, product code, court or bound touched; every option was tested
  through the environment for one build and left nowhere; two scratch worktrees
  were made and removed and the main worktree never modified. Commit `99187a1`
  built at two paths **eight characters apart** gives `7d31698c9d839c97` and
  `3e11814c36b06157`, **112 bytes apart**. **The obvious explanation is wrong**:
  `__TEXT.__cstring` is byte-identical in size, neither binary contains its own
  build path, and the only absolute paths are the toolchain's own already-remapped
  `/rust/deps/…`. **The cause is the crate disambiguator**: cargo derives
  `-C metadata` from the package's absolute path, rustc hashes it into the
  `Cs…_` field of every v0-mangled symbol, and **3,718 symbol names differ**,
  moving `__unwind_info` +16, `__eh_frame` +8 and `__DATA_CONST.__const` +32
  while `__text` is unchanged — the code is the same size, its names are not.
  `LC_UUID` differs as a consequence, and there is no build timestamp.
  **Only one option works and it works exactly**: a fixed build path — a clean
  rebuild of the same worktree is byte-identical. `--remap-path-prefix` **does
  not** fix it and is not a no-op either (it changes both outputs while leaving
  them unequal), and `RUSTFLAGS="-Cmetadata=…"` **does not** fix it because
  cargo passes its own after `RUSTFLAGS`; disambiguators still differ and 3,678
  names still differ. **The register comes out better than it looked**: every
  committed hash was produced at one path, so the ledger is internally
  consistent and its hashes *are* re-derivable there — corroborated by a
  measurement taken earlier in this line before the question was asked, when
  `ba46420b` was rebuilt in the main worktree and returned exactly the hash its
  receipt names. A register hash is a **provenance token re-derivable at the
  same path, not a portable digest of the source**, and §5 says so where a
  reader will find it. Recommendation: **change nothing about the build**, and
  write down the canonical path and the meaning — both fully measured and free.
  A container at a fixed path is named and **not measured**. Not pushed.
  G1, G3, P6 and G6 stay open.
- [x] Scoped provenance recovery, and the ruling it confirmed
  (`evidence/native-dom-control-0.0.2-provenance-recovery.json`, register note in
  `labs/native-dom/README.md`). Both receiptless commits were checked out into
  **isolated scratch worktrees**, built **offline** with the pinned toolchain
  (rustc 1.97.0, cargo 1.97.0), exercised with the harness as it existed at each
  commit, and the worktrees removed; the main worktree was never modified.
  **The control run first, and it decides the round**: commit `2c87f29`, whose
  shipped binary is `2d57ce864002…`, rebuilds at a different path to
  `6ce4567220e5…` — 144 bytes different, first differing byte 697. **The same
  source at the same toolchain does not reproduce the same artefact across
  paths**, so a rebuild cannot establish a historical hash. The rebuilds' own
  hashes (`a9bb6b51…` for `b00dd3a`, `714b9bc2…` for `a229c13`) are recorded as
  what they are and **are not** those rounds' hashes; **no register row is
  written for either** and the *unrecoverable* ruling stands, now for a measured
  reason rather than for want of trying. **What was recovered is behaviour, not
  provenance**, and only what was actually rerun is recorded: at `b00dd3a` the
  transport stress passes, with its largest protocol line at **46,514 bytes,
  1.109% of the response bound**, and an over-cap body refused `resource_limit`
  rather than a generic `internal`; at `a229c13` **`probe-truthfulness` reads
  25 of 25 with the court as it existed at that commit** — the first record
  anywhere of that court passing on the source that repaired it, which is
  exactly the gap the integration audit named. Nothing is inferred for anything
  not rerun, and no old receipt is fabricated. No product code, no court, no
  criterion, no bound, handle or base byte, no protocol change, nothing
  downloaded. Not pushed. G1, G3, P6 and G6 stay open.
- [x] Documentation-only cleanup: the last orphan is cited and the absent
  governance is ruled (`labs/native-dom/job-deadline-design-0.0.1.md` §12,
  `integration-consistency-audit-0.0.1.md` §1b). **The orphan is closed by
  citation, not by renaming**: `-job-deadline-falsification` — **12/42,
  `passed: false`, on `3b47966ece35e487…`**, the build before that slice — is now
  named beside its passing arm (**42/42** on `8ff70b9f26c1bdb3…`) in the design
  that owns them both, with the reason it is neither renamed nor removed: a
  committed receipt is history, and a falsification arm nobody points at is a
  citation gap rather than a defect in the evidence. Both parse and both state
  their binary. Neither binary gets a register row, and that is recorded rather
  than repaired: both predate the thirteen rows, and `3b47966ece35e487…` was
  never a shipped host. **Re-run of the orphan check: 112 receipts, zero
  unreferenced, all parsing.** **And the absent governance is now a ruling
  rather than a finding**: `PRD.md`, a `prd/` module, `evidence-registry.json`,
  `alignment-contract.json` and `release-policy.json` are external to this
  repository or do not exist, and are **intentionally not created here**; the
  tracked owners stay, and nothing is invented to fill a name. Two lines of that
  record are said out loud because silence would read as oversight: **release
  policy is held by nothing found** and is recorded as *unowned*, and **the
  evidence register is `labs/native-dom/README.md`**, prose and a table rather
  than a JSON file — a machine-readable registry would be a different artefact
  with a different owner and this ruling does not ask for one. The ownership
  table's own numbers are corrected while they are being relied on: the ledger is
  **47 rows** since `d5cede7`, not the 34 it had when the audit was written, and
  the evidence directory holds **112** receipts. **The unrecoverable binaries
  stay unrecoverable**: `b00dd3a` and `a229c13` committed no receipt, their
  hashes are written nowhere, and **no row is fabricated for either** —
  recovering them means rebuilding old commits, which is its own round. No
  product code, no court, no criterion, no bound, handle or base byte. Not
  pushed. G1, G3, P6 and G6 stay open.
- [x] Documentation-only evidence repair: the probe-truthfulness court has a
  passing receipt at last (`evidence/native-dom-control-0.0.2-probe-truthfulness-repaired.json`,
  register note in `labs/native-dom/README.md`). The maintenance round found
  that the realm-probe repair landed at `a229c13` and committed nothing, so the
  court's only committed receipt was the arm frozen **before** it, at **21 of
  25** on `05aa12f7cf6d…`, while every 25 of 25 since had been a scratch run.
  The court was run on the current binary and the result committed: **25 of 25
  on `2d57ce864002…`**. **The 21/25 receipt is not overwritten** and stays
  exactly where it is; the new file says in as many words that it is today's
  status and **not** the repair round's, because that round's binary hash is
  written nowhere and cannot be reconstructed honestly. **No verification
  receipt accompanies it**, and the file says why: this court carries no live
  guard — its criteria pin behaviour and the shape of the probe's source, not a
  number that tracks what the tree costs — and the convention in `AGENTS.md`
  asks for a verification receipt only for a live guard. One inversion is
  recorded for whoever reads the pair: here the **plainly named** receipt is the
  failing arm and the suffixed one passes, the opposite of the `-falsification`
  pattern used everywhere else. No probe code, no court, no product code, no
  bound, handle or base byte changed; the court re-read 25/25 after the register
  was updated, and the contract's 28 examples and 50 negative cases still pass.
  Not pushed. G1, G3, P6 and G6 stay open.
- [x] Documentation-only maintenance, from the integration audit's findings
  (`labs/native-dom/README.md`, `integration-consistency-audit-0.0.1.md`, and
  the four new verification receipts). **No product code, no criterion, no
  bound, handle or base byte changed**; the four governance documents that do
  not exist here were **not created**, and the record says those roles are
  external to this repository or absent. **Thirteen ledger rows were added, one
  per binary, each sourced by opening the receipt that names it** — no
  consolidation and nothing improvised: `8d5da2a706ae` downloads,
  `4e8f753817b9` copy-on-write, `952226eeb470` the deliberately rebuilt
  fallback, `e168722ca9b2` the no-fallback fork and the property-shape guard,
  `18f935f5fe3e` H1, `05aa12f7cf6d` the capture guard with the frozen 21/25
  probe arm, `ce371f78e38c` the registry brand, `ba46420bb1e6` the strict parse
  and the uncaptured probe, `0da1c6b11553` signature integrity,
  `e3cbf79eb64b` the discarded pricing build, `e9e071115461` the tag,
  `cc8ebfa4a4dc` the attributes, and `2d57ce864002` the text cut, now current.
  **Three things the register cannot carry are written beneath it instead of
  guessed**: two rounds changed the host and produced no receipt, so no hash of
  theirs exists anywhere (`b00dd3a` and `a229c13`); **the realm-probe repair has
  no passing receipt at all** — `-probe-truthfulness` is committed at 21/25, the
  arm frozen *before* the repair, and every 25/25 since has been a scratch run;
  and the receipt/court naming and the one orphaned receipt are recorded with
  **no renames**, because a committed receipt is history. **Two stale court
  statuses corrected**: `element-tag-court.py`'s `not_under_test`, which wrote
  "still open after this round" into every receipt, and
  `signature-integrity-court.py`'s docstring — both now say when F1, F2 and F5
  closed and that this court still does not test them. **The H1 label corrected
  in both places**, with the twist recorded rather than glossed: it was false
  when written and is true again since `958f5c0`. **The first four live-guard
  verification receipts exist**, for `element-tag`'s cost equalities,
  `signature-integrity`'s base pin, `registry-brand`'s N3 hashes and
  `property-shape`'s fingerprints, each labelled `receipt_kind: verification`,
  each naming the current binary `2d57ce864002…` and linking its historical arm,
  and each rewritable while the historical one never is. **One correction to my
  own audit, recorded rather than fixed silently**: it named the register's
  newest row as `420cdf5b82bf…`; that is the row below it, and the gap it
  reported is unchanged. Every court whose text moved was re-run: element-tag
  52/52, signature-integrity 34/34, host-answer 9/9, registry-brand 15/15,
  property-shape 22/22, attribute-fact 154/154, text-answer 29/29, form
  179/179, 58 tests, contract 28 examples and 50 negatives. Not pushed.
  G1, G3, P6 and G6 stay open.
- [ ] Read-only integration audit across the documents
  (`labs/native-dom/integration-consistency-audit-0.0.1.md`). No product code,
  no court criterion, no bound, handle or base byte changed. **Four of the seven
  documents it was asked to reconcile do not exist here**: there is no `PRD.md`,
  no `prd/` module, no `evidence-registry.json`, no `alignment-contract.json`
  and no `release-policy.json`, and none has ever existed — no deletion of any
  such path appears across 419 commits and `git ls-files` matches none of the
  names. What holds their roles today is listed in §1. **The largest real gap is
  archive work**: `labs/native-dom/README.md`'s 34-row binary ledger stops at
  `420cdf5b82bf…`, and **thirteen commits have changed `src/` since**, one
  binary each, covering the entire security-and-answer line — grepping that
  register for `signature-integrity`, `element-tag`, `attribute-fact`,
  `text-answer`, `registry-brand`, `__mcsTag` or `__mcsAttr` returns zero. The
  rows are not improvised here; the round that writes them needs a decision on
  shape and the receipts open. **Two courts state a status that is no longer
  true**: `element-tag-court.py`'s `not_under_test` calls F1 and F2 "still open
  after this round" **in every receipt it writes**, and
  `signature-integrity-court.py`'s docstring calls F1, F2 and F5 "still open" —
  all three closed at `958f5c0` and `a0482ed`. Both are reported with their
  exact edits and **not changed**, because this round may not touch courts. The
  H1 sentence corrected in `uncaptured-intrinsic-audit-0.0.1.md` §4 survives in
  two places, and there is a twist recorded rather than glossed: **it is true
  again today**, because round C made the download probe ask `__mcsTag`. The
  **receipt convention had no owning document** — it is now in `AGENTS.md`, and
  two consequences are recorded with it: no verification receipt exists yet, and
  the four live guards it governs are named. **One receipt of 107 is orphaned**
  (`-job-deadline-falsification`); a first pass flagged four more, all mine, all
  actually referenced through brace notation — the checker was wrong and that is
  recorded, because an audit that invents orphans is worse than one that finds
  none. `AbortSignal.any()` is decided out of scope in
  `abort-signal-surface-audit-0.0.1.md` but **appeared nowhere in this plan**;
  the line below fixes that. Open items are reconciled in §8 without changing a
  decision. **What is consistent is reported too**: all 102 document references
  resolve, nothing untracked or unpushed is implied, every settled ruling has one
  owning document with a court or an explicit statement that it needs none, and
  every frozen court in this line has a `passed: false` pre-change receipt and a
  `passed: true` post-change one, each naming its own binary. Not pushed.
  G1, G3, P6 and G6 stay open.
- [ ] Recorded, not started: `AbortSignal.any()` is **out of scope and never
  triaged** (`labs/native-dom/abort-signal-surface-audit-0.0.1.md` §§135, 149,
  175). It composes signals and would need its own design round. Noted here
  2026-09-06 because the decision existed only in that audit and a reader of
  this plan alone would not have known the item exists.
- [x] Ruled, nothing implemented: the option label/value asymmetry stays
  (`labs/native-dom/option-value-audit-0.0.1.md` §15, and the ruling recorded at
  the criterion itself in `form-court.py`). **The snapshot does not expose
  `options[].value`; no `value_differs` flag and no new request field are
  added; `form-court.py:405` and `form-interaction-design-0.0.1.md` §12.5 stand
  as they are.** All four candidates are declined, the free one included. What
  this is: **a deliberate preview gap**. What it is not: an
  information-integrity defect or an authority defect — the submitted query
  stays readable after the commit through `target.inspect`, and the approval
  signature covering the built query, with `preflight_mismatch` on any mutation
  between the two derivations, keeps the act bound to what was approved. The §6
  asymmetry — a textbox's `value` is in the snapshot and an option's is not — is
  **settled as intended** rather than left open: it is a narrower rule for
  options than §12.5 requires, kept on purpose. The ruling is written twice on
  purpose: in the audit, and as a comment above the criterion, so that whoever
  next reaches that line wondering why the value is hidden reads that it was
  ruled and not overlooked. **No product code, no criterion moved, no bound,
  handle or base byte changed**, and `form-court.py` still reads 179/179 with
  the comment in place. Not pushed. G1, G3, P6 and G6 stay open.
- [ ] Design-only, nothing implemented: an option's label and the value that
  reaches the server (`labs/native-dom/option-value-audit-0.0.1.md`, probe
  `option-value-probe.py`, receipt
  `evidence/native-dom-control-0.0.2-option-value.json`). Eighteen arms — eight
  select shapes through **both** act doors, plus two that mutate the chosen
  option between the snapshot and the act. **The gap is real in the three shapes
  an author can write** (`label` and no value → the text is sent; `label` and a
  value → the value; a value and no label → the value) and **absent in the
  plain control**, where what is read is what is sent. **But it is not an
  oversight**: `form-court.py:405` freezes *"the snapshot reports no option
  value"* in as many words, rooted in `form-interaction-design-0.0.1.md` §12.5,
  so any candidate that exposes the value is a request to reverse a deliberate
  decision rather than a bug fix — the most important thing this audit has to
  say. **And it is a preview gap, not an information gap**: the same §12.5 says
  `target.inspect` may report the committed URL *"query included, because that
  is the browser state an Agent must be able to read"*, and measured, it does —
  after the submit the agent can read the submitted option text and the typed
  textbox value, though never the label, which never goes on the wire.
  **Authority is bound throughout**: a form's approval signature includes the
  built query, so a page that rewrites the chosen option's value between the two
  derivations gets `preflight_mismatch` with **nothing fetched**, on both doors.
  Not a fail-open, not a wrong answer — every reported field is true. The other
  shapes: duplicate labels are indistinguishable from the answer but the host's
  own `index` is the discriminator and it works; a disabled option is flagged
  and refused `option_disabled`; a pre-selected one is reported truthfully, and
  a form submitted with no act at all sends a value the agent never saw; and a
  label of `"  X + emoji + "  "` is shown **trimmed** while the declared value
  reached the server with its spaces and emoji intact. **One asymmetry is named
  and not resolved**: a textbox's `value` **is** in the snapshot today and an
  option's is not, while §12.5 governs ledgers and diagnostics and explicitly
  exempts what an agent must read — so the option rule is stricter than the
  written rule requires, and line 405 is where the extra strictness lives. Four
  candidates in §9, **none of them a schema change** (the schema constrains a
  `result` only as `{"type":"object","maxProperties":64}` and pins no node
  field, and `check_contract.py` asserts none): expose the value (at most
  **+17,088 bytes** on a select node measured at **4,603** today, and it
  reverses line 405); expose a boolean (**≤1,408 bytes**, a smaller ruling in
  the same category); a new request field (the only one that touches the
  schema); or **document it**. `form-court.py:403`'s shape check is a subset
  test and would not obstruct either exposure — only the no-value criterion
  beside it would. **Recommended: do nothing and document it**, since authority
  is bound, every field is true, and the agent can read the committed query
  afterwards by a route the original design blessed. A court is drafted
  **conditionally** in §13 and deliberately not for the recommendation.
  Verification on `2d57ce86`: text-answer 29/29, form 179/179, attribute-fact
  154/154, element-tag 52/52, signature-integrity 34/34, property-shape 22/22,
  registry-brand 15/15, snapshot-schema 13/13, downloads 21/21, child-frame
  82/82; fmt, 58 tests, contract 28 examples and 50 negatives. Not pushed.
  G1, G3, P6 and G6 stay open.
- [x] Frozen, then implemented: an answer is never lost to half a character
  (`labs/native-dom/text-answer-court.py`, receipts
  `evidence/native-dom-control-0.0.2-text-answer-court{,-falsification,-falsification-amended}.json`).
  **Court frozen first at 13/29 on `cc8ebfa4`**, then **29/29 on `2d57ce86`**.
  The audit drafted sixteen criteria; the ruling asked for **all six** places the
  snapshot cuts a page-derived string, so the frozen court carries twenty-nine —
  a name, an input `value` and an option `label` cut at 256, and a `dom_id`, a
  control's name and a radio's `group` cut at 64, each with a surrogate pair
  straddling its own boundary. `snapshot_script` gains `cut(raw, n)`, built only
  from a string's own `length`, index reads and `+=`: it stops one character
  early rather than splitting a pair, and replaces an unpaired surrogate
  anywhere with U+FFFD. **The measured cost is zero on every axis**: shims
  unchanged at 33,886 and 26,485, and child-frame M1 and M2 identical on both
  allocators — the whole change lives in a host script, compiled per evaluation
  and not resident per realm. **No re-freeze of any settled court was needed**,
  the first slice in this line for which that is true. **Two scope notes,
  reported rather than absorbed.** The frozen criterion forbade `.slice(0,`
  anywhere in the script, which also caught two *array* slices bounding the
  option and control counts; rather than weaken a frozen criterion I made it
  true, adding a `take(list, n)` on the same index-only technique — which
  incidentally takes those two counts out of the page's hands as well. And one
  criterion was **amended after the freeze**, recorded in the file beside its old
  form: it checked every field against the single limit its document was built
  around, and a textbox's `name` falls back to the `name` attribute and is cut
  at 256 rather than 64, so it failed at 68 characters on a host that was right.
  It now checks each field against **its own** ruled limit, which is strictly
  more precise, and the amended court **still scores 13/29 on the pre-change
  binary** — identical to the freeze, recorded in its own falsification receipt
  rather than over the original. Regressions on `2d57ce86`, all green:
  attribute-fact 154/154, element-tag 52/52, signature-integrity 34/34,
  property-shape 22/22, registry-brand 15/15, capture-declaration 8/8,
  probe-truthfulness 25/25, host-answer 9/9, downloads 21/21, snapshot-schema
  13/13, copy-on-write 23/23, readonly-profile 28/28, form 179/179, frame-action
  182/182, page-navigation 80/80, lifecycle 53/53, job-deadline 42/42,
  element-api 28/28, dataset 15/15, event-fidelity 62/62, timer 68/68, and
  **child-frame 82/82** — the audit round's 81/82 was batch variance and did not
  recur. Navigation soak not rerun by standing rule. fmt, 58 tests, clippy
  `-D warnings`, contract 28 examples and 50 negatives. The option label/value
  exposure stays a **separate protocol question**, named in the court's own
  receipt. Not pushed. G1, G3, P6 and G6 stay open.
- [ ] Design-only, nothing implemented: the C class, what a page's own text
  reaches (`labs/native-dom/text-answer-audit-0.0.1.md`, probe
  `text-answer-probe.py`, receipt
  `evidence/native-dom-control-0.0.2-text-answer.json`). With every fail-open
  closed, this is the class the ruling left open: a node's `name` comes from the
  page's `textContent`. **It reaches nothing it was feared to reach**, measured
  over seven arms: node ids are positional and host-assigned; a text write moves
  the revision, so a reference taken before it answers `stale_revision` on every
  arm; no navigation, download or typed refusal reads text; and **no arm caused
  any request the baseline did not**. Even the size bound holds without help
  from the page: with `String.prototype.slice` replaced by the identity the
  answer came back **`truncated: true`, one node, 411 bytes**, because the Rust
  half counts each node's serialised bytes and stops at `max_bytes`
  (`main.rs:7029`). **But the cut can lose the whole answer.**
  `name.slice(0, 256)` cuts UTF-16 code units, so a surrogate pair straddling
  the cut leaves a lone surrogate, `serde_json` refuses it, and the host answers
  a bare `internal` — *"engine returned malformed snapshot JSON"*
  (`main.rs:4589`). **No intrinsic is replaced**: it is ordinary content at an
  unlucky offset, and it hits the 256-unit cut on a name, an input `value` and
  an option label, and the 64-unit cut on a `dom_id`. A legitimate page with an
  emoji 256 characters in loses its entire snapshot and is told `internal`
  rather than anything it can reason about. A second, self-inflicted route — a
  page writing `String.fromCharCode(0xD800)` — reaches the same crash.
  **A second finding, needing no patch at all**: `<option label="A">B</option>`
  shows the agent `A` and submits `B`, confirmed by marker at the server. That
  is conformant — a browser does the same — but **the snapshot never exposes the
  value that would be submitted**, so an agent choosing by label cannot know
  what goes on the wire. Named, not solved: it is an answer-shape question for
  the agent contract. **The candidate is free.** A `cut(raw, n)` built from
  `.length`, index reads and `+=` — the technique already ruled in for `urlOf`
  and the approval signature — stops rather than splitting a pair and replaces
  an unpaired surrogate with U+FFFD, and it replaces all six `.slice` calls on
  page-derived strings so the cut stops being the page's. Built as `6f44390e`
  and measured on both allocators: **base shim 0, main shim 0, child-frame M1
  and M2 0 on both arms** — every cost zero, because the edit lives entirely in
  a host script, compiled per evaluation and not resident per realm. It closes
  both routes (all eight cut documents return a snapshot) and moves nothing
  else: attribute-fact 154/154, element-tag 52/52, signature-integrity 34/34,
  property-shape 22/22, registry-brand 15/15, snapshot-schema 13/13, form
  179/179, frame-action 182/182, downloads 21/21, element-api 28/28, dataset
  15/15, child-frame 82/82. **It is the first candidate in this line that needs
  no re-freeze at all.** Loss matrix §9, court draft §12. Tree back at
  `cc8ebfa4` with the shims at 33,886 and 26,485. Verification on the restored
  tree: attribute-fact 154/154, element-tag 52/52, signature-integrity 34/34,
  property-shape 22/22, registry-brand 15/15, capture-declaration 8/8,
  probe-truthfulness 25/25, host-answer 9/9, downloads 21/21, snapshot-schema
  13/13, form 179/179, frame-action 182/182, page-navigation 80/80, element-api
  28/28, dataset 15/15, and child-frame **81/82** on its arena growth criterion
  — **batch variance on the same binary**, which scored 82/82 twice earlier in
  the same session. fmt, 58 tests, clippy `-D warnings`, contract 28 examples
  and 50 negatives. Not pushed. G1, G3, P6 and G6 stay open.
- [x] Frozen, then implemented: round D, candidate E — one record per element
  holds the tag and the attributes (`labs/native-dom/attribute-fact-design-0.0.1.md`
  §11b, court `labs/native-dom/attribute-fact-court.py`, receipts
  `evidence/native-dom-control-0.0.2-attribute-fact{,-falsification}.json`).
  **Court frozen first at 126/154 on `e9e07111`** — the unpatched arm passing in
  full, the three technique-and-cost criteria failing because only the code can
  satisfy them, the rest the defect — then **154/154 on `cc8ebfa4`**. F1 and F2
  are closed on every route: the two selective `toLowerCase` lies, a
  `Map.prototype.get` that lies about method and target, a direct write of
  `el.__attrs`, and an arm that replaces and deletes both readers. The snapshot
  reports `method: "post"` for a declared POST on every arm, and both submit
  doors — the form and its submitter — read the same refusal. **Two criteria
  were repaired before the freeze rather than after**: with `el.__attrs`
  replaced the *form's* activation reads `allowed` while the *submitter's* reads
  `form_method_unsupported`, so a court that acted only on the submitter would
  have passed on a page that still submits its POST; and the handle-widening
  check counted colons in a regex and failed for its own reasons, so it now
  names the thirteen handle keys and requires that **neither reader appears
  inside the handle**. **Both construction doors are criteria**: a cloned anchor
  is still a link and downloads, a cloned POST form keeps its method and is
  refused, and an anchor built with `createElement` downloads — the doors the
  audit's D3 candidate missed. **Page compatibility is kept** by an explicit
  no-op landing setter: `el.__attrs = …` is ignored rather than fatal, the field
  still reads back as a `Map`, and `getAttributeNames` still answers. **Costs,
  measured on both allocators against ceilings frozen before the code**: base
  shim 33,290 → **33,886** (+596, ceiling +600) with the main shim untouched at
  26,485; child-frame M1 +2,896 system and +2,736 arena (ceiling +3,200); M2
  +20,272 and +21,552 (ceiling +23,000). **Five frozen values were re-frozen,
  each recorded chronologically with its old value and reason**:
  `property-shape`'s `window` (`113:5899bf6e` → `114:5d05836a`) and
  `Element.prototype` (`40:26312e4` → `41:1c3f518d`, `__attrs` becoming a
  page-observable accessor — the one genuinely new cost of round D, and the row
  that deliberately did **not** move in round C); `signature-integrity`'s base
  pin; `registry-brand`'s N3 base-shim hash (`3561e774…` → `f420f901…`, main
  shim untouched); and `element-tag`'s cost group, **rebased before the code**
  from expiring deltas to equalities at round C's measured values and then
  re-frozen by round D — which is the group working as intended, a live guard
  every slice touching the shim must move deliberately. Regressions on
  `cc8ebfa4`: attribute-fact 154/154, element-tag 52/52, signature-integrity
  34/34, registry-brand 15/15, property-shape 22/22, capture-declaration 8/8
  (**no sixteenth capture**), probe-truthfulness 25/25, host-answer 9/9,
  downloads 21/21, copy-on-write 23/23, snapshot-schema 13/13, readonly-profile
  28/28, secure-cookie 78/78, https 74/74, form 179/179, frame-action 182/182,
  page-navigation 80/80, lifecycle 53/53, job-deadline 42/42, element-api 28/28,
  dataset 15/15, event-fidelity 62/62, timer 68/68, frame-realm 62/62,
  cdp-frame-tree 64/64, child-frame 82/82 — **no residual failures**. Navigation
  soak not rerun by standing rule. fmt, 58 tests, clippy `-D warnings`, contract
  28 examples and 50 negatives, `diff --check`, redaction scan. Not pushed.
  G1, G3, P6 and G6 stay open.
- [ ] Design-only, nothing implemented: round D, the attributes a decision
  reads (`labs/native-dom/attribute-fact-design-0.0.1.md`, receipt
  `evidence/native-dom-control-0.0.2-attribute-fact-candidates.json`). Four
  candidates built on the pushed round-C tree, measured on **both allocators**,
  and discarded; **no frozen value was re-frozen**, because the tree holds no
  implementation and moving a pin now would only make the court wrong about the
  tree it measures. **D1 — move the store only — is the worst option on the
  table**: it costs exactly what D2 costs and closes three routes of four,
  leaving the `toLowerCase` route open, so a round that shipped it would have
  reported F1 closed while a page could still submit a declared POST as a GET.
  **D3 — validate at write time — is broken, and instructively**: it fails on
  every arm including the unpatched one and reports `method: "get"` for every
  form, because the parser seeds attributes straight into the map
  (`dom_shim_base.js:479`) without going through `setAttribute`, so the folded
  copy is never written. That is round C's `cloneNode` lesson in a second
  dimension — *validation at write time misses whichever construction door does
  not go through the writer* — and it costs three times the cheapest option.
  **D2 and E both close everything**: every arm refuses, the snapshot reports
  `post` truthfully on all ten, every honest control still works, and the page's
  `__attrs` still reads as a `Map` because all four candidates carry the
  explicit **no-op landing setter** the previous round measured to be mandatory
  (without it a page's own write throws and answers `target_crashed`).
  **E — one record per element holding tag and attributes together — is the
  cheapest on every one of the five measures**: +518 base bytes against D2's
  +584, and +2,896/+20,272 system and +3,024/+21,904 arena against D2's
  +4,096/+28,736 and +3,152/+22,960, leaving 23,590 under M1 and 166,564 under
  M2. Nothing is extrapolated from round C or the earlier estimates: each row is
  its own build, probe run and child-frame run on both arms, and the previous
  round's C+D figures are superseded. **No authority expansion**, measured on
  every build — `javascript:`, `file://` at a real local file whose bytes never
  came back, and an unallowed origin refused identically, every refusal still
  the Rust half's — **no page value or query in the ledger**, and the typed
  vocabulary unchanged. **Five frozen values would move**, identified by running
  the frozen courts against the E build and left untouched: `property-shape`'s
  `window` (`113:5899bf6e` → `114:5d05836a`) and `Element.prototype`
  (`40:26312e4` → `41:1c3f518d`, `__attrs` becoming a page-observable accessor —
  the one genuinely new cost of D), `signature-integrity`'s base-byte pin,
  `registry-brand`'s N3 hash, and **`element-tag`'s whole cost group, which has
  expired**: its ceilings are deltas from the pre-round-C baseline, so under E
  it fails all five and reports C's cost plus D's as if they were C's. *A cost
  ceiling written as a delta from a fixed prior baseline expires when the next
  slice lands*; the recommendation is to re-express it as equalities at round
  C's measured values. Everything else holds on E: capture-declaration 8/8 (no
  sixteenth capture), form 179/179, frame-action 182/182, page-navigation 80/80,
  downloads 21/21, element-api 28/28, dataset 15/15, host-answer 9/9. Loss
  matrix §8, dependencies §9, safe failures §10, a court design §11.
  Recommendation: **E**. Tree back at `e9e07111` with the shims at 33,290 and
  26,485 bytes. Regressions read-only and green: element-tag 52/52,
  signature-integrity 34/34, registry-brand 15/15, property-shape 22/22,
  capture-declaration 8/8, probe-truthfulness 25/25, host-answer 9/9, downloads
  21/21, form 179/179, frame-action 182/182, page-navigation 80/80, child-frame
  82/82, element-api 28/28, dataset 15/15; fmt, 58 tests, clippy `-D warnings`,
  contract 28 examples and 50 negatives. Not pushed. G1, G3, P6 and G6 stay open.
- [x] Frozen, then implemented: round C, an element's tag is the host's
  (`labs/native-dom/element-tag-design-0.0.1.md`, court
  `labs/native-dom/element-tag-court.py`, receipts
  `evidence/native-dom-control-0.0.2-element-tag{,-falsification}.json`).
  **Court frozen first at 38/52 on `0da1c6b1`**, every control and every
  anti-vacuity criterion passing and the fourteen failures exactly the defect
  and the technique checks; then **52/52 on `e9e07111`**. The base shim keeps a
  closure-owned `WeakMap` from element to tag, written through the
  already-captured `weakMapSet`, read through a non-writable, non-configurable
  `__mcsTag`; all seven host-script tag reads ask it, `role()` included, so a
  `<div>` a page renames is **not offered as a node at all** rather than merely
  refused at the download. **Nothing is taken away from the page**: `tagName`
  and `localName` stay writable data properties, so `el.tagName = "A"` still
  succeeds and the page still reads `"A"` back — the court checks that by having
  the page echo the value into a paragraph the snapshot carries — and no
  ignoring setter is needed, unlike round D. A `cloneNode` of an anchor is still
  a link and still downloads, which guards the construction path the store must
  not miss. **Costs, measured on both allocators against ceilings frozen before
  the code**: base shim 32,898 → 33,290 (**+392**, ceiling +400) with the main
  shim untouched at 26,485; child-frame M1 +1,696 system and +1,952 arena
  (ceiling +2,048); M2 +11,936 and +13,040 (ceiling +14,336). The base ceiling
  held only after the comments were cut twice, from +920 to +445 to +392 —
  **the cap counts source bytes and a comment costs source bytes while costing
  nothing per realm**, which is recorded for round D rather than acted on here:
  the cap was frozen, so the code was made to fit it. **The two ruled re-freezes
  landed exactly as predicted**: `property-shape`'s `window` moved
  `112:156a0f8b` → `113:5899bf6e`, and **`Element.prototype` did not move**,
  which is the check that round C added no prototype member;
  `signature-integrity`'s base-byte pin moved 32,898 → 33,290. Both amendments
  keep the old values beside them with the date and the reason. **A third pin
  the ruling did not name is left failing rather than amended**:
  `registry-brand-court.py`'s N3 pins both shims' SHA-256 from that round and
  reads **14/15**, because round C is the first slice since to change the base
  shim. The amendment was reported rather than taken, then **ruled and
  applied**: the base-shim hash moves to `3561e774…` with the old one kept
  beside it, the main-shim hash `d319246e…` stays put because round C does not
  touch the main shim, and the court reads **15/15**. **F1 and F2 stay open**, are not folded in, and are named
  in the court's own receipt: a declared POST is still submitted as a GET and a
  named-target link is still activated. Regressions on `e9e07111`: element-tag
  52/52, property-shape 22/22, signature-integrity 34/34, probe-truthfulness
  25/25, capture-declaration 8/8 (no sixteenth capture), host-answer 9/9,
  downloads 21/21, copy-on-write 23/23, snapshot-schema 13/13, readonly-profile
  28/28, secure-cookie 78/78, https 74/74, form 179/179, frame-action 182/182,
  page-navigation 80/80, lifecycle 53/53, job-deadline 42/42, element-api 28/28,
  dataset 15/15, event-fidelity 62/62, timer 68/68, frame-realm 62/62,
  cdp-frame-tree 64/64, child-frame 82/82, and registry-brand 15/15 after the
  amendment above. Navigation soak not rerun by standing rule. fmt, 58 tests, clippy
  `-D warnings`, contract 28 examples and 50 negatives, `diff --check`,
  redaction scan. Not pushed. G1, G3, P6 and G6 stay open.
- [ ] Design-only, nothing implemented: host-owned element facts, with the
  ruling recorded (`labs/native-dom/element-fact-design-0.0.1.md`, receipt
  `evidence/native-dom-control-0.0.2-element-fact-candidates.json`).
  **The ruling**: an element's activation, method and tag are agent-facing facts
  and must be the host's; the typed refusal vocabulary and every Rust scheme,
  origin and bound guard stay exactly as they are; the three defects stay
  separate in behaviour. **Four candidates were built, measured on both
  allocators, and discarded.** C gives the base shim a closure-owned `WeakMap`
  of element to tag behind a non-writable `__mcsTag`, and closes F5 more than
  expected — with `role()` reading it, the div is not merely undownloadable, it
  is **not a node at all** on every arm. D moves the attribute map into a
  closure-owned store behind `__mcsAttr` — **and on its own does not close F1 or
  F2**, measured: `map_get_*` and `prop_attrs_*` close but `lower_post_get`
  survives, because the value was still normalised with `trim().toLowerCase()`.
  D only works with the normalisation rebuilt from index reads and `+=`, the
  technique already ruled in for `urlOf`. With that, **all ten arms are clean on
  the combined build**: every selective monkeypatch, every direct page write,
  both `Map.prototype.get` patches and an arm that tries to replace and delete
  the readers themselves; every honest control still works (`method="get"` form
  submits, untargeted link navigates, real `<a download>` delivers its bytes);
  the marker proves the page's script ran first; **no authority expansion** —
  `javascript:`, `file://` at a real local file whose bytes never came back, and
  an unallowed origin are refused identically as before, all by the Rust half —
  and the ledger mentions no method and leaks no form value. **Costs, measured,
  never derived** (caps M1 262,144 / M2 1,835,008): C +293 base bytes, system
  +1,776 M1 / +12,496 M2, arena +1,792 / +14,048; D +677, +3,536 / +24,816,
  +3,552 / +26,048; C+D +914, +5,904 / +41,456, +5,360 / +38,080, leaving 22,278
  and 157,316 of headroom. **Combining is 1,408 bytes cheaper than the two
  apart** — a cost argument, not a security one. No per-realm rate is offered:
  both stores hold one entry per element and the two points do not divide to the
  same number. **Three costs only a build could reveal.** The natural
  `removeAttribute` captures `Map.prototype.delete`, and
  `signature-integrity`'s group D caught the sixteenth capture; the measured
  build avoids it by leaving that `delete` on the prototype, and
  `capture-declaration` reads 8/8. `property-shape` moves four criteria on both
  allocators — `window` for any new global and `Element.prototype` for anything
  containing D — and **re-freezing it is the ruling's decision and a
  precondition, not part of the slice**. And taking `__attrs` away without an
  ignoring setter makes a page's own assignment throw, killing its script and
  answering `target_crashed` on three of ten arms; the setter restores it. Loss
  matrix in §9 with five options, dependencies in §10, safe failures in §11.
  Tree is back at `0da1c6b1` with the shims at 32,898 and 26,485 bytes.
  Regressions read-only and green: signature-integrity 34/34,
  probe-truthfulness 25/25, property-shape 22/22, capture-declaration 8/8,
  host-answer 9/9, downloads 21/21, form 179/179, frame-action 182/182,
  page-navigation 80/80, child-frame 82/82; fmt, 58 tests, clippy `-D warnings`,
  contract 28 examples and 50 negatives. Not pushed. G1, G3, P6 and G6 stay open.
- [ ] Design-only, nothing implemented: F1, F2 and F5, and who actually owns
  them (`labs/native-dom/fail-open-triage-audit-0.0.1.md`, probe
  `fail-open-triage-probe.py`, receipts
  `evidence/native-dom-control-0.0.2-fail-open-triage{,-priced-arm}.json`).
  **The framing was wrong twice, and measurement said so both times.** These
  three were carried as uncaptured-intrinsic defects; all three are reachable
  with **no intrinsic replaced at all**, by assigning an ordinary own property —
  `div.tagName = "A"` downloads the div, and replacing a form's or a link's
  `__attrs` map submits a POST as a GET and activates a named target.
  `dom_shim_base.js:315-318` keeps an element's tag and its whole attribute map
  as writable own properties of a page-reachable object. And the natural fix —
  let the realm report the raw attribute and have Rust decide — **also fails**,
  measured: `getAttribute` reads through `Map.prototype.get`, and patching that
  alone reproduces F1 and F2 with `toLowerCase` untouched. **Authority impact is
  none, and for F5 it is proven rather than argued**: four escalation pairs run
  the div and an honest anchor side by side, and `javascript:`, `file://` (a real
  local file written for the probe, whose bytes never came back) and a second
  loopback origin are refused identically for both, every refusal being the Rust
  half's scheme, URL-bound and address policy. The honest controls sit in the
  same document for all three and reach the identical outcome, so what the
  defects buy is not reach but that the agent's closed refusal vocabulary —
  `form_method_unsupported`, `target_named`, `not_a_link` — is the page's to
  write, and the snapshot tells the agent the wrong thing first. The ledger
  neither lies nor helps: the patched submit's entry is shape-identical to the
  honest one and records no method, and no arm leaked a form value or a built
  query. **Independent in effect, one owner in cause**: each selective patch
  moves only its own defect across all nine arms, so they can be ruled on
  separately in any order; but all three are the same sentence — the host reads
  a fact out of a field the page can write — which no court covers, since
  `property-shape-court.py` pins the internals handle, not an element's fields.
  **Priced by building it, not by extrapolating**: a throwaway arm gave the base
  shim a closure-owned `WeakMap` of element to tag and a non-writable `__mcsTag`
  reader on the `__mcsJson` pattern, and it closes F5 under both routes while
  the honest anchor still delivers its bytes — **+293 source bytes**, child-frame
  M1 233,962 → 235,738 and M2 1,636,236 → 1,648,732 against caps 262,144 and
  1,835,008, and 82/82 where the pushed build scores 81/82. **No per-realm figure
  is offered**: the store holds one entry per element, the two measured points do
  not divide to the same rate, and option D (the same shape for `__attrs`) is
  deliberately left unpriced rather than extrapolated — that was the C1 error.
  The build is gone: both files restored from copies and the binary rebuilt to
  `0da1c6b1` with the base shim back at 32,898 bytes. Loss matrix in §9, three
  separate court drafts in §12, each naming the honest control that must keep
  working. **The ruling this asks for is whether the `activation` vocabulary is
  worth defending at all**, since nothing here is a capability leak. Regressions
  read-only and green on `0da1c6b1`: signature-integrity 34/34,
  probe-truthfulness 25/25, host-answer 9/9, downloads 21/21, form 179/179,
  frame-action 182/182, page-navigation 80/80, property-shape 22/22,
  capture-declaration 8/8; fmt, 58 tests, clippy `-D warnings`, contract 28
  examples and 50 negatives. Not pushed. G1, G3, P6 and G6 stay open.
- [x] Frozen, then implemented: the approval binds, the fragment is seen, and
  the probe is the host's (`labs/native-dom/signature-integrity-design-0.0.1.md`,
  court `labs/native-dom/signature-integrity-court.py`, receipts
  `evidence/native-dom-control-0.0.2-signature-integrity{,-falsification}.json`).
  **Measuring before freezing changed the design.** The ruling proposed closing
  F4 by building the approval signature with concatenation instead of `join`;
  the same fixture patched at `String.prototype.replace` instead defeats the
  interlock exactly as well, because the signature is only as honest as its four
  inputs and one of them — `href`, built by `urlOf` — was itself made with a
  replaceable `replace`. A court frozen on the `join` route alone would have
  passed over a live F4. So the slice is **four edits, all inside host scripts**:
  the signature concatenates with a literal separator; `urlOf` is rebuilt from
  `"" + raw`, a string's own `length`, index reads and `+=`, with no `replace`
  and no global `String`; the fragment test is `value[0] === "#"`; and
  `REALM_PROBE_JS` concatenates its seven booleans with `":"` and calls neither
  `join` nor `String` — boolean-to-string conversion is the specification's and
  never consults `Boolean.prototype.toString`, so **no page-owned operation is
  left on the probe's path at all**. A correction recorded before it could become
  a criterion: the global `String` is replaceable and the host's scripts do use
  it, but in the F4 shape it is not a route, because `setAttribute` stores
  `String(value)` and a patch that lies about the moved address also stops the
  address moving; it stays a live route into the probe, and that is where the
  court puts it. **Court frozen first at 19/34 on `ba46420b`** — every control
  and every anti-vacuity criterion passing, the fifteen failures exactly the
  defects — then **34/34 on `0da1c6b1`**. Three fixture defects were caught in
  the freeze rather than after: a form is not focusable so a `submit` aimed at it
  opens no window for page code, a click on a submit button never submits, and
  the probe's `join` patch must delegate to the captured original or the
  snapshot throws and the anti-vacuity marker cannot be read. **One frozen court
  was amended, chronologically and in writing**: `probe-truthfulness-court.py`'s
  S2 counted `String(` occurrences as a proxy for "seven questions are still
  asked", and the repair had to remove every one of them; the criterion now
  names the seven questions and requires exactly six literal separators, which
  is stricter, and the old spelling is left in the record beside it. The
  independent audit probe, rerun on the new binary, confirms the scope exactly:
  the interlock's `join` arm goes from applied-and-fetched-`/moved.html` to
  `preflight_mismatch` with nothing fetched, the dictated probe vector's
  `main_present` goes from `true` to the truth, both fragment arms go from
  applied to `fragment_unsupported` — and **F1 `methodOf`, F2 `targetOf` and F5
  the download probe's node kind are unchanged and stay open**, deliberately not
  folded in. Regressions on `0da1c6b1`: signature-integrity 34/34,
  probe-truthfulness 25/25, host-answer 9/9, capture-declaration 8/8,
  property-shape 22/22, registry-brand 15/15, snapshot-schema 13/13, downloads
  21/21, copy-on-write 23/23, form 179/179, frame-action 182/182,
  page-navigation 80/80, lifecycle 53/53, job-deadline 42/42, element-api 28/28,
  event-fidelity 62/62, timer 68/68, frame-realm 62/62, cdp-frame-tree 64/64,
  and child-frame **81/82** whose one failure is the arena footprint-growth
  criterion — **proven machine variance by rebuilding `ba46420b` and rerunning:
  the pre-change binary fails the same criterion, 81/82, on the other allocator
  arm**. Child-frame's M1 233,962 of 262,144 and M2 1,636,236 of 1,835,008 show
  no per-realm growth, which follows from host scripts being compiled per
  evaluation rather than resident. Navigation soak not rerun by standing rule.
  Gates: fmt, 58 tests, clippy `-D warnings`, contract 28 examples and 50
  negatives, `diff --check`, redaction scan. No new capture (the fifteen declared
  are pinned by the court), no widened handle, no bound moved, and
  `dom_shim_base.js` 32,898 and `dom_shim_main.js` 26,485 bytes pinned unchanged
  so the slice cannot pay for itself with base bytes. Implementation not pushed.
  G1, G3, P6 and G6 stay open.
- [ ] Design-only, nothing implemented: the uncaptured intrinsic call sites
  (`labs/native-dom/uncaptured-intrinsic-audit-0.0.1.md`, probe
  `labs/native-dom/uncaptured-intrinsic-probe.py`, receipt
  `evidence/native-dom-control-0.0.2-uncaptured-intrinsic.json`). H2 left 71
  sites with no capture to route through; **today there are 121** (base 41,
  main 27, host scripts 53), and the growth is entirely in the host scripts,
  because `download_probe_script`, `form_action_script` and `act_script` landed
  after H2 was written — the uncaptured surface grows with every host script
  added. **The interesting part is not `toLowerCase`.** Blanket replacement of
  any intrinsic collapses the page's own document — every `toLowerCase` variant
  empties the agent's snapshot to **0 nodes**, because `getAttribute` and
  `hasAttribute` lowercase the attribute *name*, so `hasAttribute("href")` is
  false for everything — and **18 of the 43 patched runs changed nothing at all**,
  `JSON.stringify` replaced with a forgery among them, which is H1 holding,
  re-measured. Selective patches, which lie about one comparison and leave the
  document intact, are where everything is: **five fail-opens**. A `method=
  "post"` form is **submitted as a GET** and the server receives it (`methodOf`,
  `main.rs:591`); a `target="somewhere"` link is **activated in-frame**
  (`targetOf`, `main.rs:572`); a `#fragment` link **navigates** and the revision
  advances (`main.rs:582`); a `<div href=… download=…>` is **downloaded as a
  link** (`main.rs:775`); and, the one escalation a page cannot achieve
  honestly, **the two-phase approval stops binding**: the page moves an approved
  link's `href` from its own `focus` handler, which runs inside the window the
  act script opens, and one patched `Array.prototype.join(" ")` in the approval
  signature (`main.rs:558`) turns a `preflight_mismatch` refusal with nothing
  fetched into an applied act that fetches a URL the agent never approved,
  bounded only by the host's own scheme and origin re-decision. The court's own
  instrument is writable the same way: a page whose `join(":")` returns a chosen
  seven-field vector **dictates `owners.realm_probe` exactly**, reporting
  `main_present: true` where the truth is false. **The rule the measurements
  draw**: every decision the Rust half re-makes for itself (scheme, origin, URL
  bounds, snapshot shape) survives page ownership as answer corruption only;
  every decision it takes on the realm's word does not — and no quantity of new
  captures changes that. **A correction to the record**: H1 §3's "the host
  re-checks the node kind itself" is wrong as stated (`main.rs:7602-7604`
  refuses `not_a_link` only when the realm declines to supply an href); nothing
  frozen moves, the sentence does. Six court candidates and a loss matrix in
  §7-§8, whose shape is that **the free remedies close the most**: build the
  approval signature and the probe's answer by concatenation and test the
  fragment with `value[0]`, and F3-F4 and the probe close for no capture and no
  per-realm bytes; a new captured lowercase is the most expensive option and
  closes strictly less than surfacing method and target for the host to
  re-decide. Gates read-only and green: fmt, 58 tests, clippy `-D warnings`,
  and the contract's 28 examples and 50 negative cases. Not pushed. G1, G3, P6
  and G6 stay open.
- [ ] Design-only, nothing implemented: cache, P6
  (`labs/native-dom/cache-audit-0.0.1.md`). **One cache exists and it is not the
  one the question is usually about.** A per-profile TLS session cache:
  `ClientSessionMemoryCache` of **16 entries**, memory only, one client per
  profile, never shared, gone on restart — and a copy-on-write child mints its
  own, so it inherits no tickets. `secure-cookie-court.py:242` already pins that
  a restarted host's first https fetch is a full handshake. **There is no HTTP
  response cache at any scope, proven rather than assumed**: against an origin
  serving `Cache-Control: max-age=3600`, an `ETag` and a `Last-Modified`, the
  page was fetched again on reload, on navigate-away-and-back, in a second
  target, a second session, a second profile and after a restart — 7 fetches for
  7 asks. The host never revalidates either: across thirteen requests the only
  headers it sent were `accept`, `connection`, `host`, `user-agent` — no
  `If-None-Match`, no `If-Modified-Since`. Connections are not pooled: thirteen
  fetches, **thirteen TCP connections**, which follows from the
  `Connection: close` on every request. Nothing accumulates: twelve reloads left
  tracked realm bytes identical at 329,088. **What that buys for free**: no
  staleness, no cross-profile channel, no new retention class, and a readonly
  session that writes nothing because there is nothing to write. If a cache were
  added it would need its own budget (the 131,072-byte accounted budget is
  already tight), must be measured on the arena arm because D6 is RSS, would
  make G1 runs depend on whether the cache was warm, would re-seal the whole
  profile record per store at ~12 ms if persistent, and must exclude downloads.
  Eight-criterion court draft for a future cache in §7, four criteria pinning
  today's absence in §8. **Ruled (§10): the absence is the design** — no
  response cache is implemented, the per-profile 16-slot TLS session cache
  stays as it is, and §8's four criteria are adopted into the record as what
  must remain true: two profiles asking for one URL make two server requests, a
  restart re-fetches with a full handshake, the host sends no conditional
  headers, and twelve reloads leave tracked realm bytes unchanged. Should the
  requirement appear, it begins with its own round — protocol shape, its own
  budget, an isolation ruling, a court measured on the arena arm, downloads
  excluded by name, and the G1 warm-or-cold question settled in the same round.
- [x] Frozen, then implemented: the snapshot parse stops defaulting
  (`labs/native-dom/snapshot-schema-court.py`, receipt
  `evidence/native-dom-control-0.0.2-snapshot-schema.json`, record in
  `snapshot-defaulting-audit-0.0.1.md` §11). Frozen at **7/13**, now **13/13**
  on `ba46420b…`. Both parse sites require every field to be what it claims —
  a missing or mistyped `nodes`, `node`, `role`, `name` or `truncated` is
  refused `target_crashed` with `reason: "snapshot_schema"` and the **field
  named** — and the `node_0` default is gone, which was the one default that
  could hand an agent a reference resolving to nothing. The four act scripts
  guard the list before indexing, so `nodes = null` gets that typed refusal
  rather than `internal` / *a script threw*: the audit's blemish, a
  page-authored break reading as a host fault. **The court's shape was decided
  by a measurement**: a page cannot produce a malformed field at all —
  `textContent = 42`, `= {}`, `= null` and `setAttribute("id", 7)` all arrive
  as strings — so those criteria are pinned at the source and in
  `snapshot_schema_tests` rather than pretended through a fixture, and only the
  act path is driven live. **A court weakness was caught before the freeze
  stood**: the source criteria matched exact multi-line strings and passed
  because the formatting differed, not because the defaults were gone; they are
  now window-based and assert they found both sites. Regressions:
  registry-brand 15/15, host-answer 9/9, property-shape 22/22,
  capture-declaration 8/8, probe-truthfulness 25/25, downloads 21/21,
  copy-on-write 23/23, readonly 28/28, frame-action 182/182, child-frame 82/82,
  timer 68/68, contract 28/50, **58 tests**; navigation 89/90 on its known
  memory-variance check. Branding `snapshot`/`nodes` stays **not done** as
  ruled (+1,872 bytes per realm); the interlock carries it, and the court keeps
  it as a standing regression. Handle key set, enum at 26, both shims and every
  bound untouched.
- [ ] Design-only, nothing implemented: snapshot defaulting
  (`labs/native-dom/snapshot-defaulting-audit-0.0.1.md`). **The defaults are
  hygiene, not a hole**: `error` and `revision` refuse, and everything else —
  `truncated`, `nodes`, each entry's `node`, `role`, `name` — defaults in
  silence, with `node_0` the worst of them because the parser invents a
  reference id. Almost none of it is page-reachable: after H1 the answer is
  serialised by `__mcsJson` over values the host's own script builds, and the
  count is bounded. What remains page-writable after the brand is the
  registry's `snapshot` and `nodes` fields. **Measured, one timer per run, in
  the window `target.act` opens by running due timers before resolving a node**:
  a disconnected poison answers `not_found`, a connected one answers
  `stale_revision`, a bogus `snapshot` marker answers `not_found`, and
  `nodes = null` answers `internal` — the server received nothing in any case.
  **The interlock is why**: an act needs a matching revision *and* a connected
  element, and connecting one is a mutation that advances the branded counter,
  so a page cannot hold both. The blemish is naming, not direction —
  `nodes = null` reads as a host fault when a page caused it. Recommended:
  **strict parse plus that relabel, at zero per-realm cost**; **not**
  recommended is branding `snapshot` and `nodes` as well, measured at
  **+1,872 bytes per realm** (a registry of today's shape costs +944, the
  guarded one +2,816) to convert a denial into a slightly earlier denial.
  Eight-criterion court draft in §9; three rulings pending in §10. Regressions
  verified unchanged: registry-brand 15/15, host-answer 9/9,
  probe-truthfulness 25/25.
- [x] Frozen, then implemented: the revision registry is the host's
  (`labs/native-dom/registry-brand-court.py`, receipt
  `evidence/native-dom-control-0.0.2-registry-brand.json`, record in
  `snapshot-validation-audit-0.0.1.md` §11). Frozen first at **10/15** — the
  page that names `__mcs` first had no revision advance, an **accepted** stale
  reference and `/evil.html` in the server log — and now **15/15** on
  `ce371f78…`. Every realm mints a brand at birth; the installer recognises its
  own and answers `occupied` to anything else, which the host turns into
  `internal` / `registry_occupied`; the registry is non-writable,
  non-configurable and non-enumerable, and its counter is a **closure** whose
  `revision` is a getter with no setter, so a page cannot reset it. The two host
  scripts that must move it present the brand — `bump(brand)` for a settled
  action or host scroll, `setTo(brand, n)` for the court-only seam. **Three
  implementation faults are recorded rather than smoothed over**: an unminted
  brand interpolated as a hole and made every page fail to open; installing
  earlier looked tidier and silently stopped the `MutationObserver` attaching,
  caught by the frames court; and the main realm is built at a third site the
  installer calls did not cover, so its actions bumped with an empty brand —
  fixed by minting in `Realm::new`, which covers every realm by construction
  rather than by enumeration. **Two court amendments recorded**: the
  pre-empting-page criteria now expect the ruled typed refusal rather than a
  working snapshot, and the source extraction follows the installer from a
  constant to a function. Regressions: host-answer 9/9, property-shape 22/22,
  capture-declaration 8/8, probe-truthfulness 25/25, downloads 21/21,
  copy-on-write 23/23, readonly 28/28, frame-action 182/182, child-frame 82/82,
  timer 68/68, contract 28/50, 56 tests; navigation 89/90 and profile 92/94 fail
  only their known memory-variance and D6 checks. Handle key set, protocol, both
  shims (digests pinned in the court) and every bound untouched.
- [ ] Design-only, nothing implemented: snapshot validation
  (`labs/native-dom/snapshot-validation-audit-0.0.1.md`). **The shape check is
  not the weak point — the provenance of the instrumentation object is, and it
  fails open.** Measured: `window.__mcs` is `undefined` while a page's own
  scripts run, because `INSTALL_JS` runs after `__mcsComplete()`; and
  `INSTALL_JS` keeps whatever it finds (`if (!window.__mcs)`, a plain
  assignment), a guard that exists for the lifecycle path's second install and
  cannot tell the host's object from a page's. So a page only has to name the
  global first. **Exploit measured end to end**: a page that defines `__mcs`
  with a `revision` getter returning 0, then swaps a link on click — with honest
  instrumentation the agent's stale reference is refused `stale_revision` and
  the server sees nothing; with the page owning the counter the reference is
  **accepted**, the click reaches the **swapped** element, and the server
  receives **`GET /evil.html`**, a navigation the agent never chose. Unlike
  every earlier tampering finding, **this one fails open**. Not forgeable, and
  the reason the answer is not "validate harder": the node list is rebuilt from
  the DOM each call (an output, not an input), the serialiser is already closed
  by H1, and counts and `truncated` are host-computed. Recommended candidate:
  **brand the registry** with a per-realm host-minted token installed
  non-writable and non-configurable, verified by every reader, mismatch
  refusing the realm — the pattern the host already uses for dispatch and
  lifecycle capabilities, at ~0 per-realm cost. Six-criterion court draft in §9;
  three rulings pending in §10, including that this finding revises the earlier
  "everything fails closed" conclusion. Regressions verified unchanged:
  host-answer 9/9, capture-declaration 8/8, probe-truthfulness 25/25.
- [x] Implemented behind the frozen court: the probe's evidence chain
  (`labs/native-dom/src/main.rs`, record in `realm-probe-audit-0.0.1.md` §9).
  `REALM_PROBE_JS`'s enumerability question is now a syntax-only `for…in`, and
  `Object.keys` and `.indexOf` are gone from it; the `present` question was
  already `typeof` and truthful in all seven scenarios, so it is unchanged —
  the repair is exactly the two replaceable calls. **The frozen court reads
  25/25** on `aa30da2b…`, up from 21/25, the four movers being the two masking
  scenarios, the false alarm and the source rule. **Per-realm cost: none** —
  system 327,424 and arena 317,360, identical before and after, since the probe
  is compiled per call and never retained; the price is the +3.8 µs per
  evaluation measured beforehand, paid once per realm per `memory.report` under
  two court flags. Regressions: host-answer 9/9, property-shape 22/22,
  capture-declaration 8/8, downloads 21/21, copy-on-write 23/23, readonly 28/28,
  frame-action 182/182, child-frame 82/82, contract 28/50, 56 tests, fmt and
  clippy clean. **`shim-footprint-court` gets a note, not a tick**: it asks
  whether a candidate *recovers* 16 KiB against a `--baseline`, reads 15/18
  here, and its three failures are structural — the baseline binary compared
  with itself reads 14/18 on the same criteria. Its one criterion that bears on
  this work, *the handle is gone and not enumerable*, **passes**, now backed by
  a probe a page cannot blind. `SEAL_JS`, host answers, the protocol, the handle
  key set, both shims and every bound are untouched.
- [ ] Frozen before any repair: the probe-truthfulness court
  (`labs/native-dom/probe-truthfulness-court.py`, receipt
  `evidence/native-dom-control-0.0.2-probe-truthfulness.json`, record in
  `realm-probe-audit-0.0.1.md` §8). For each of seven pages it states **what is
  actually true** about a `__mcsInternals` property and requires the probe to
  say it, rather than comparing runs. **21/25 on the shipped binary**, and the
  four failures are exactly the repair's targets: two masking scenarios where
  the probe calls an enumerable property hidden, one false alarm where it calls
  a gone handle enumerable, and the source rule. Two groups pass today and are
  worth keeping: **every `present` criterion** (a `typeof`, truthful in all
  seven scenarios) and **every containment criterion** — in no scenario did the
  pair read `false, false` while a property of that name existed, so the audit's
  fail-safe finding now stands as a criterion. **Repair priced by direct
  measurement, not extrapolation**: both forms evaluated 2,000 times in a
  shim-installed realm across six runs — `Object.keys(...).indexOf(...)`
  18,136–18,790 ns per evaluation against the syntax-only `for…in` at
  21,999–22,965, so **+3.8 µs, about +21%**, with retention indistinguishable.
  It is not zero, unlike H1's substitution, because this trades a native call
  for an interpreted walk over `window`; the cost is the walk, not the compile.
  The probe runs once per realm per `memory.report` and only under two court
  flags. No implementation, no source change, `SEAL_JS` untouched.
- [ ] Design-only, nothing implemented: the court realm probe
  (`labs/native-dom/realm-probe-audit-0.0.1.md`). **The diagnostic can be made
  to lie, but only in the fail-safe direction, and no host answer or security
  decision depends on it.** `REALM_PROBE_JS` runs only from `realm_probe()`,
  which needs `--court-realm-probe` *and* `--surface-court-file` (the host exits
  64 without the second); its only consumer is `memory.report`'s court section,
  and its only readers are two criteria in `shim-footprint-court.py`. The
  enforcement is elsewhere and is page-proof: `SEAL_JS` is `delete` plus
  `typeof`, pure syntax, run before any page script. Measured, one tampering per
  run: `present` (a `typeof`) **never** reported false while a property existed;
  `enumerable` (`Object.keys(...).indexOf(...)`) was controllable both ways —
  a false alarm when `indexOf` always finds, and masking when the page re-adds
  the name and hides it. **Every tampering yields a court failure, never a false
  pass**, since the criterion demands both fields false. So the residual risk is
  false alarms discrediting the evidence chain, not a bypass. **Fix verified
  rather than argued**: a syntax-only `for…in` told the truth under all three
  tamperings where the method form was wrong under two; it is a host-script
  change of H1's class with no per-realm retention. Six-criterion court draft
  in §6; three rulings pending in §7, including the defensible option of leaving
  it alone. H3's declared set untouched, H2 not done.
- [x] Frozen guard, and the one violation it found:
  `labs/native-dom/capture-declaration-court.py`, receipt
  `evidence/native-dom-control-0.0.2-capture-declarations.json`, record in
  `h3-reserved-captures-design-0.0.1.md` §10. H3 in its declared-set form: every
  capture not on the reserved list is referenced, the reserved list is exactly
  `["arrayIndexOf"]`, each exception carries a written reason, and a reserved
  capture staying at **zero** uses is itself a criterion — so the rule cannot be
  satisfied by adding a call. Run against three shim revisions before any change
  (`9b5e390`, `900b111`, `4b7d42c`): identical each time — **11 captures, exactly
  one at zero uses, 7/8 passing** — so no new dead capture has ever crept in.
  The single failure everywhere was the missing written reason, a real violation
  of the frozen rule rather than a flaw in it; the reason is now written beside
  the declaration and the court reads **8/8**. The comment's cost, measured
  twice per arm and identical: **system 327,456 → 327,424 (−32), arena
  317,232 → 317,360 (+128)** — both directions at once from a change that
  creates no object, the same packing artefact the C2 audit measured, reported
  rather than rounded away. Guards re-run: property-shape 22/22, host-answer
  9/9. No call added, nothing deleted or restored, H2 untouched, and
  `REALM_PROBE_JS` left for its own audit.
- [ ] Design-only, nothing implemented: H3 versus the closed C2
  (`labs/native-dom/h3-reserved-captures-design-0.0.1.md`). The conflict is
  exact: H3 as worded says every capture is referenced, C2 permanently forbids
  deleting `arrayIndexOf`, and `arrayIndexOf` has zero references.
  **Recommended: quantify H3 over a declared set** — every capture *not* on a
  reserved list must be referenced, and the reserved list is pinned to exactly
  `["arrayIndexOf"]` with a written reason beside it. **Cost: zero** — no
  runtime change, no bytes, no per-child effect, nothing touching M1/M2, D6 or
  G1 — and it stays falsifiable, because a *new* dead capture still fails and
  neither rule can be satisfied by writing a call. **Proving a host invariant
  needs the capture is impossible as scoped**: the seven direct `.indexOf`
  sites are four `classList` (page-internal — `class` is not in the snapshot),
  two host-owned bookkeeping paths that fail closed, and one option comparison
  with no measured effect; the enforcement path `SEAL_JS` uses **no**
  replaceable call at all; and the capture lives in the base's closure, out of
  reach of host scripts unless a new global is exposed, which would grow the
  base. **Separate small finding**: `REALM_PROBE_JS`, the court-only realm
  diagnostic, reads `Object.keys(window).indexOf(...)` and can be lied to by a
  page — it cannot affect the seal, but it is the courts' own instrument, and
  it is recorded unfixed for its own ruling. Five-criterion court draft in §8.
  §9 also flags a tension in the instruction — evaluate removal, yet do not
  reinstate C2's deletion — and states which reading was taken. H2 stays not
  done; the 71 uncapturable sites stay a future candidate; snapshot shape
  validation stays separate.
- [ ] Design-only, nothing implemented: H2 and H3
  (`labs/native-dom/intrinsic-hardening-h2-audit-0.0.1.md`). **Recommendation:
  H3 yes, H2 no — on evidence, not on cost.** After H1, every monkeypatch
  probed makes a page *invisible* to the agent rather than making it *lie*:
  replacing `String.prototype.toLowerCase` empties the snapshot (0 nodes),
  `Array.prototype.push` makes it refuse `internal`, `Map.prototype.get` hides
  the link node, `Array.prototype.indexOf` hides one div, and
  `Array.prototype.splice` changes nothing — while downloads still fetch the
  right URL and a non-link is still refused `not_a_link`. **All fail closed**,
  and each is something a page could already do by not rendering. The
  `classList` corruption persists but is confined: `class` is not part of the
  snapshot. (Superseded count, recorded 2026-09-06: the uncaptured half is
  **121 today** under a wider method list —
  `uncaptured-intrinsic-audit-0.0.1.md` §1 measures it and reconciles the two.)
  Surface: **85 of 156 sites have a capture to route through; 71 do
  not**, `toLowerCase` alone being 20 of them, seven in host decision scripts
  including the download probe's `tagName.toLowerCase() !== "a"` — those need
  new captures, which would grow the base and are out of scope. Cost of
  routing, measured: **48.6 bytes per site per realm** — ~1,507 in every realm
  for the base's 31 sites, ~1,798 per main realm for main's 37, ~0 for host
  scripts, ~26 KB across eight targets. Binding dependency: base-side H2 adds
  to every child realm and must be measured against `shim-footprint-court`'s
  **frozen** M1/M2 floors (245,760 / 1,720,320) before any implementation.
  H3's rule — every captured intrinsic is referenced at least once — is one
  line of court and is what surfaced `arrayIndexOf` (still the only capture at
  zero references). Loss matrix, DAG and four pending rulings in the audit. D6,
  G1, the handle key set and the snapshot's shape validation untouched.
- [x] Implemented behind a frozen court: H1, the host's answers stop passing
  through page-replaceable functions (`labs/native-dom/src/main.rs`,
  `host-answer-court.py`, receipt
  `evidence/native-dom-control-0.0.2-host-answers.json`, record in
  `intrinsic-hardening-audit-0.0.1.md` §8). The court was frozen first and read
  **3/9** on the shipped binary; it now reads **9/9**. The twenty-four
  `JSON.stringify` calls in the host's in-realm scripts serialise through
  `__mcsJson` — twenty directly, and the four `{"error":"uninstrumented"}`
  branches became **literal strings**, because that branch runs exactly when the
  shim is absent and must not depend on a shim-installed capture. **Closed**: a
  tampering page can no longer put `FORGED-BY-PAGE` into the agent's snapshot,
  and a download that the page rewrote to `/never-asked.bin` now fetches only
  the `/asked.bin` the agent's reference names, proven by the server's request
  log. **Unchanged**: the activation refusal still refuses a forged decision (a
  standing regression of the audit's negative result), the handle key set, and
  both shims byte-for-byte. Two criteria were gated before freezing because a
  forgeable snapshot hides the link, so they would have passed for want of
  anything happening. Regressions: property-shape 22/22, timer 68/68,
  frame-action 182/182, downloads 21/21, copy-on-write 23/23, child-frame
  82/82, navigation 90/90, readonly 28/28, contract 28/50, 56 tests. H2 and H3
  stay independent and outside any byte target. D6 and G1 untouched.
- [ ] Design-only, nothing implemented: intrinsic hardening
  (`labs/native-dom/intrinsic-hardening-audit-0.0.1.md`). **Three of four host
  paths probed are page-controllable.** 186 direct calls of replaceable methods
  across the base shim (65), the main shim (60) and the host's own in-realm
  scripts (61); the shims capture eleven intrinsics and use them at 30 sites.
  **The serious one: a page that replaces `JSON.stringify` forges the agent's
  entire snapshot** — the agent received `nodes: [{name: "FORGED-BY-PAGE"}]` and
  none of the real document, because the snapshot script ends
  `return JSON.stringify(...)`; the host checks only that a `revision` field
  exists (a probe omitting it was refused `internal: "snapshot lacks a
  revision"`), so shape is checked and provenance is not. **Second: a download
  fetched a URL the agent never referenced** — the agent's reference named
  `/visible.bin`, the server logged `GET /secret.bin`, and the agent received
  those bytes under the profile's cookies, in the path landed earlier today.
  Third: `classList` corruption via `Array.prototype.indexOf` (from the C2
  audit). **Negative result recorded**: a forged `{decision: "allowed"}` did
  *not* flip an activation refusal, because the host re-checks the node kind —
  the pattern is not uniform, which is where the audit's value lies.
  Candidates: **H1**, route the 24 `JSON.stringify` sites in the host's script
  constants through the already-installed, non-writable `__mcsJson` — nearly
  free, closes both agent-facing paths, extends no handle; **H2**, the shim's
  own ~90 direct calls, which *costs* bytes and must never be traded against a
  slimming target; **H3**, the rule that every captured intrinsic is referenced
  and every direct call justified. Eight-criterion per-site court draft in §5;
  four rulings pending in §7. D6 and G1 untouched; no shared runtime, no lazy
  install, no base or handle growth.
- [ ] Design-only, nothing implemented: the dead `arrayIndexOf` capture, C2
  (`labs/native-dom/dead-capture-audit-0.0.1.md`). **HOLD the deletion — and
  the reason is not the bytes.** It is confirmed dead (referenced nowhere in
  the repo), and removing it is a wash: **−112 per realm on the system arm,
  +96 on the arena arm** (a packing artefact, reproducible), with no RSS effect
  at all. The finding that matters is *why* it is dead: the shim captures
  intrinsics so a page cannot change what host code does, and **the pattern is
  about a third applied** — `arrayPush` 7 captured uses against 26 direct
  calls, `weakMapGet` 7 against 11, `mapGet` 3 against 11, `mapHas` 1 against
  6, `arraySplice` 1 against 4, and `arrayIndexOf` **0 against 7**. **The gap
  is exploitable, and was measured**: a hermetic page that assigns
  `Array.prototype.indexOf = () => -1` gets the shim to call its replacement
  six times, makes `classList.contains("alpha")` return false on
  `class="alpha beta"`, and makes `classList.add` write
  `class="alpha alpha alpha"` — duplicate tokens the spec forbids, in the
  attribute the **agent** then reads through a snapshot or selects on. Same
  shape reaches `MutationObserver.disconnect`, `__detach` and the
  selected-option path. So the capture should not be deleted (it is the last
  evidence of an intent the code still needs) and not quietly left either; the
  fix is to route the direct calls through the captures already present, which
  is a behaviour-preserving change with a security purpose that will *cost*
  bytes and needs its own ruling, court and per-site measurement — explicitly
  not bundled with any byte-reduction programme. Six-criterion court draft in
  §6; three rulings pending in §7. D6 and the slack bound untouched.
- [ ] Frozen guard kept, C1 withdrawn on measurement
  (`labs/native-dom/property-shape-court.py`, receipt
  `evidence/native-dom-control-0.0.2-property-shape.json`, record in
  `shim-reduction-audit-0.0.1.md` §9). The court was frozen first and reads
  **22/22** on the shipped binary: it pins every own property of `window`,
  `Node.prototype`, `Element.prototype`, `Document.prototype`,
  `Event.prototype` and `document` — names, **creation order**, kind and all
  three flags — reported by a page and read back through a snapshot, on both
  arms. C1 was then written exactly as ruled and **it does not pay**. Order
  preservation limits it to three adjacent groups of 4, 3 and 2 members, not the
  27 sites the audit counted; with the refactor in place the guard still passed
  22/22, but tracked bytes went **327,456 → 327,456** on system and
  **317,232 → 317,872 (+640)** on arena. A group-size sweep shows why: batching
  saves the call site and pays for its own descriptor container, so it breaks
  even between **three and four members** (2: +48, 3: +16, 4: −16, 8: −144,
  32: −560). §5's ~31-bytes-per-member price came from a 200-member experiment
  and was extrapolated to a 3-member regime — **the same extrapolation error
  this project has recorded twice before**. The shims are back as shipped, the
  guard court stays, and C1 is withdrawn. D6 untouched; no lazy install, no
  comment stripping, no member semantics changed, no shared runtime.
- [ ] Design-only, nothing implemented: shim reduction
  (`labs/native-dom/shim-reduction-audit-0.0.1.md`). **The 3.6 bytes-per-source-byte
  yardstick is an average and a false guide, and this audit replaces it.**
  Measured per marginal realm, appending each construct 200 times to the real
  base shim: a closure 502, a `defineProperty` 474, a class-body method 445, a
  batched `defineProperties` entry 443, a plain prototype assignment 442, an
  object literal 150, 100 bytes of string data 27, and **a comment 0** — 21,290
  bytes of comments moved the realm by exactly nothing, so the shims' 17,329
  bytes of comment are not a target at all. The unit of reduction is a
  **member**, at ~442–474 bytes in every realm that evaluates it. **The decisive
  finding**: the cost is the *compiled bytecode*, not the installed object — 200
  members behind a lazy accessor that nothing touches still cost **372 each**,
  so deferral buys only 16–21% and lazy installation is not a reduction
  technique. Split by payer: engine floor 103,856 every realm, base shim
  **122,800** every realm including child frames, main shim **87,296** main
  realms only (already exempt for children, not double-counted). Named members
  are only ~24% of the base shim's cost, so member removal will not return
  linearly and the structural remainder needs its own measurement. Viable and
  semantics-neutral: batching the ~27 separate `defineProperty` sites (~840
  bytes per realm) and deleting the dead `arrayIndexOf` capture. Rejected on
  measurement: lazy installation, comment stripping, accessor-to-method
  conversion (~32 bytes and a page-visible API change), and base/main
  de-duplication (the shared names are loop variables and handle imports).
  Six-criterion court draft in §7; three rulings pending in §8. D6 untouched;
  no shared runtime, no page capability removed.
- [ ] Design-only, nothing implemented: the first realm at engine level
  (`labs/native-dom/first-realm-engine-audit-0.0.1.md`). Read-only; candidates
  measured in a **scratch crate outside the repository** linking the same
  pinned `rquickjs =0.12.2`. **The tracked per-realm cost is exactly constant**
  — 327,456 system, 317,232 arena — released in full on close, so there is *no
  first-realm premium in the tracked number*. The premium is real but untracked:
  **14,416 malloc bytes that are never released** (identical on both arms, so
  host state rather than realm state) and ~2.1 MB of RSS over a marginal realm.
  Decomposed: `Runtime::new` 27,344 (8.4%), `Context::full` 76,640 (23.4%, of
  which intrinsics 41,568 — TypedArrays alone 16,288), base shim 155,056, main
  shim 87,040. **Two thirds of a realm is the shim**, and its 58,957 source
  bytes cost 210,096 live: **an exchange rate of ~3.6 bytes of realm memory per
  byte of shim source**, which turns the existing main-slack work into a
  measurable programme. Candidates judged: shrinking the shim is the only large
  semantics-neutral lever; one shared runtime would save 49,872 per realm
  (15.2%) but collapses the per-realm memory limit and the very zone/arena
  instrument D6 uses; trimming intrinsics buys under an eighth of a realm and
  changes what a page may do; bytecode addresses parse, not the objects that
  dominate; and skipping the main shim in child realms is **already done**
  (87,040 per child realm). **D6 note**: D6 is measured in RSS and the system
  arm returns none of it on close — RSS even grows 32,768 — so a saving shows
  up only on the arena arm, which returned 901,120. Eight-criterion court draft
  in §7; four rulings pending in §8. D6's criterion and bound untouched; G1
  unaffected.
- [ ] Design-only, nothing implemented: history persistence, P6
  (`labs/native-dom/history-persistence-audit-0.0.1.md`). Read-only, headless,
  no user data touched. **Confirmed first**: history is one ring per target,
  in memory, keyed by a target id that is never reused — `length 1` after a
  reopen and after a restart, **no URL anywhere in the sealed record**, and
  nothing of it crosses a copy-on-write fork. The protocol exposes `position`,
  `length` and `can_go_*` and **never the URLs**, while `target.inspect`'s
  `url` field does carry the current query and the ledger carries origins only.
  **Two costs shape any design**: a single target's full window of maximal URLs
  measures **15,416 bytes**, so eight targets is ~123,328 against a profile's
  131,072-byte accounted budget — up to 94% of it — and every record mutation
  is a full re-seal at ~12 ms, so persisting per navigation makes every
  navigation a whole-profile commit. **The audit's main finding is that "history
  persistence" is two features wearing one name**: letting the agent see where a
  profile has been needs a *disclosure* ruling, because the URLs are structurally
  invisible today; restoring a reopened target's back/forward needs an *identity*
  that does not exist, since target ids are never reused. Four protocol
  candidates are enumerated with their contract impact, and the recommendation
  is **not to pick one yet** — the next ruling should be which feature is
  wanted. Recommended: history not inherited by a fork, a 8-entry / 16 KiB
  budget, joining the existing atomic commit rather than getting a second write
  path, and nothing written under a readonly session. Twelve-criterion court
  draft in §9; five rulings pending in §10. **Ruled: deferred deliberately**
  (§11) — no candidate is taken, and the next round must first say whether it
  is agent disclosure or reopen-target restoration before designing either.
  The status quo is now a decision rather than an absence: a per-target
  in-memory ring of 8, no URL persisted, no history inherited by a fork, no
  download in history, a readonly session's ring moving without writing, and a
  no-op on an ephemeral profile. Carried forward as its own disclosure question
  (§12): `target.inspect` reports the current `url` **with its query**, the one
  place a query crosses the protocol, deliberately not fixed this round.
  G1, G3, P6 and G6 stay open; D6 untouched.
- [x] Fixed before the implementation was pushed: the fork's re-read has no
  fallback (`copy-on-write-audit-0.0.1.md` §14, falsification receipt
  `evidence/native-dom-control-0.0.2-copy-on-write-falsification.json`).
  Review caught what §13 had described without recognising it: when the source's
  record could not be re-read under the lock, the first build **fell back to
  this host's startup state**, so a corrupt or unverifiable source would have
  produced a child from unverified memory while the caller was told the copy
  succeeded. The lock excludes a racing writer; it says nothing about why a
  record cannot be read. There is now **no fallback** — a refusal in the store's
  existing vocabulary (`not_found`, `internal`, `unsupported_capability`), each
  with its reason, no child directory, no latch on the parent. Two criteria
  added: **F20/F20b** makes the record unreadable and demands a typed refusal,
  and **F21** has a second host commit a key after adoption and demands the
  child carry it. **The court proves the fix**: against a rebuilt fallback
  binary (`952226ee…`) it reads 22/23, F20 failing because the fork *succeeded*
  on an unreadable source and wrote a child from memory. On `e168722c…` it
  reads **23/23**; downloads 21/21, readonly 28/28, profile 92/94 with its two
  known D6 checks. No bound moved and no criterion was weakened.
- [x] Implemented behind the frozen court: copy-on-write profiles
  (`labs/native-dom/src/main.rs`, `protocol/check_contract.py`,
  `copy-on-write-court.py`, landing record in `copy-on-write-audit-0.0.1.md`
  §13). **The court reads 20/20** on `4e8f7538…`. `profile.create` gains a
  `from` argument and the operation enum stays at **26**; the fork takes the
  source's writer lock and **re-reads the sealed record from disk under it**,
  rather than copying whatever this host adopted at startup, so the copy is a
  committed one and not a race. The parent is only read and stays
  byte-for-byte identical; cookies, storage and the policy are inherited and
  the answer names what it carried; the download counters reset; an ephemeral
  source is refused with its own `ephemeral_source` reason for either
  persistence asked for; a persistent source may fork an **ephemeral** child
  that inherits in memory and leaves the disk untouched; and the child's record
  never names its parent. **F12 is no longer a stub**: the parent spends its
  whole download allowance until the count refuses, is forked, and the child
  downloads on its first try — the ruling's consequence made falsifiable.
  Regressions on the same binary: downloads 21/21, readonly 28/28, profile
  92/94 with only its two known D6 checks failing, which fail on the pre-fork
  binary too. G1, G3, P6 and G6 stay open; D6 untouched.
- [ ] Ruled and frozen before the host changes: the copy-on-write court
  (`labs/native-dom/copy-on-write-court.py`, receipt
  `evidence/native-dom-control-0.0.2-copy-on-write.json`, ruling and freeze in
  `copy-on-write-audit-0.0.1.md` §§11–12). Ruled shape: `profile.create` gains
  a `from` argument, the operation enum stays at **26**; the fork opens the
  source inside the host and **takes its writer lock**, so any live session —
  readonly included — refuses rather than copying a stale commit; the parent is
  read only and stays byte-for-byte unchanged; cookies, storage and the policy
  are inherited; the download counters reset because they are never persisted;
  an ephemeral profile cannot be a source; and the child's record does not name
  its parent, so a fork cannot leak one profile's identity into another's.
  **Eighteen criteria, none passing** — the honest score for a host that cannot
  fork. The first run scored 6, and all six were **vacuous**: the parent was
  unchanged because nothing happened, the child's record named no parent
  because it had zero bytes, and the ephemeral refusal shared its code with an
  unknown-argument error. They are now gated on a child actually existing.
  Sixth occurrence of this trap here, and the first where the whole passing set
  was vacuous: *a criterion that cannot fail on a host without the capability
  is not a criterion.* F3 and F10 remain the ones that matter — a fork that
  damaged the profile it copied from would satisfy every other criterion.
  Headless, hermetic, no user data touched. G1, G3, P6 and G6 stay open; D6
  untouched.
- [ ] Design-only, nothing implemented: copy-on-write profiles
  (`labs/native-dom/copy-on-write-audit-0.0.1.md`). Read-only, headless, no
  user data touched — every measurement ran in a temporary profile root.
  **Two measurements decide the shape.** First, a profile's identity is inside
  its seal: the same bytes under a new name answer *"format, protocol or
  profile mismatch"*, and under the same name on another host *"record does not
  authenticate"*, because the DEK is wrapped under that host's key account. So
  a fork can never be a copy of bytes — it must re-seal inside a live host
  holding both the master key and the source's DEK. Second, **the store is
  already copy-on-write on every mutation**: `commit_control_mutation` clones
  jar and storage as its rollback copy and rewrites the whole sealed record —
  measured as 24 puts costing 25 writes and 379,190 bytes for a record ending
  at 29,710, each put a flat ~12 ms. So deferring the copy saves only the
  child's first write, 602 bytes and ~12 ms: **copy-on-write here is an
  isolation feature, not a performance one**, and should be ruled on that
  ground. Recommended protocol shape is a `from` argument on `profile.create`
  — no new operation, the closed enum stays at 26, and the work lands where the
  DEK is already minted. The hazard to rule on: `profile.inspect` succeeds from
  a second host **without** the writer lock, so a fork of a profile another
  host has open would silently copy a stale record. Twelve-criterion court
  draft in §9; six rulings pending in §10. G1, G3, P6 and G6 stay open; D6
  untouched.
- [x] Implemented behind the frozen court: downloads, sink C, action kind
  `download` (`labs/native-dom/src/main.rs`, `net.rs`,
  `protocol/check_contract.py`, `downloads-court.py`, freeze and landing
  records in `download-sink-c-design-0.0.1.md` §§8–9). **The court reads 21/21
  on `8d5da2a7…`, with T1 green**: a near-cap download through the real host,
  byte_count 1,000,000, sha256 matching after decoding, one line of 1,333,615
  bytes with exactly one newline — the join the two-half transport stress
  could not reach. Over the cap stays a typed `resource_limit` /
  `response-bytes`, never the generic `internal`. Bytes come back over the
  protocol and touch no disk; the download dispatches nothing into the page,
  advances no revision, writes no cookie back, and spends no document fetch
  allowance. The record carries `target.act:download`, outcome `served` and a
  byte count — never the name, never the bytes. Three finds the design did not
  have: **the byte budget cannot bind alone** (1,048,576 × 32 is exactly
  32 MiB, so the two budgets are coincident, not independent — ruled to keep
  all three values and restate the criterion), a quoted filename may contain
  the `;` separator (the parser now honours the quoted string), and a name is
  one line (an injected break yields no name rather than a piece of one).
  `frame-action-court.py` moved by eight checks, all the same word —
  `download_available` for `a[download]` per V3 — with **no behaviour change**,
  and reads 182/182 after its amendment. The navigation court's two memory
  checks were shown to be variance, not regression: both fail on the
  pre-implementation binary too, at the same 16 KB-step values straddling the
  cap, across five paired runs. G1, G3, P6 and G6 stay open; D6 untouched.
- [ ] Frozen before the host changes, nothing implemented: the downloads court
  (`labs/native-dom/downloads-court.py`, receipt
  `evidence/native-dom-control-0.0.2-downloads.json`, freeze recorded in
  `download-sink-c-design-0.0.1.md` §8). **Twenty criteria, five passing** on
  the shipped `8ff70b9f26c1…`. The five are guards that must keep passing:
  the enum stays at 26 operations, the page's own `link.click()` on
  `a[download]` observes `dispatched,returned:undefined` and nothing else,
  navigating at an attachment keeps its typed `unsupported_capability`, no
  filename reaches the agent's record, and nothing is written to any disk.
  **T1 is the gate** — a near-cap download through the real host with
  `byte_count` and sha256 verified after decoding and exactly one newline,
  the join the two-half stress could not test; a failure there stops the work.
  Two criteria were tightened before freezing because they measured the
  fixture rather than the rule (C1 leaned on a substring the contract already
  contains via `download_unsupported`; B3 matched the bare number 32), and
  three (B1, B2, L2) are recorded as failing rather than omitted, so the count
  cannot quietly grow. Frozen values: ceiling 1,048,576 inherited from the
  network cap, name bound 255 bytes, `downloads` 32, `download_bytes` 32 MiB.
  Headless, hermetic, nothing downloaded. G1, G3, P6 and G6 stay open; D6
  untouched.
- [ ] Required before implementation, nothing implemented: the download
  transport stressed (`labs/native-dom/download-transport-stress-0.0.1.md`,
  `transport-line-stress.py`, `transport_tests` in `src/main.rs`). Hermetic and
  headless; nothing downloaded. **The stress had to be two halves**, because no
  shipped operation can emit a large line: measured across a 400-node document,
  the ceiling is `target.snapshot` at **46,515 bytes** — 1.109% of the response
  bound, a thirtieth of a download line — held there by 256-character node
  text, `MAX_SNAPSHOT_NODES` 128 and `MAX_PROFILES` 8. So the writer half runs
  the real `envelope()` and the real write_all/newline/flush over a real pipe
  at **1,398,652 bytes**, and the reader half runs the courts' own
  `readline()` at 1,398,624; the bytes are identical, `byte_count` matches, and
  the sha256 recomputed **after** base64 decoding matches the host's. Over the
  cap, `target.open` is refused `resource_limit` with
  `reason: "response-bytes"` — typed, before serialization, never the generic
  `internal`, which a companion test pins as carrying no details at all.
  Candidate budgets: `downloads` 32 and `download_bytes` 32 MiB, so neither
  limit makes the other unreachable. `sha2` and `base64` are already
  dependencies. **Still open**: the two halves joined through the real host,
  which cannot be measured until a capability can emit such a line — it stays
  the court's first criterion. G1, G3, P6 and G6 stay open; D6 untouched.
- [ ] Read-only measurement, nothing implemented: the download envelope
  (`labs/native-dom/download-envelope-audit-0.0.1.md`). Nothing was
  downloaded — the fixture is local. **The envelope is not the binding
  constraint**: a network-cap payload serializes to 1,398,652 bytes against the
  4,194,304 bound, 2,795,652 spare, and the envelope could carry 3,145,317
  bytes of payload — three times what the network layer will fetch. So the
  ceiling can simply **inherit the network cap**, whose over-ceiling refusal
  already exists and is already typed: a 1.4 MB document is refused
  `resource_limit` / *"network policy: response-bytes"*, while 900 KB opens
  fine. The measurement's real find is a **risk nobody had looked for**: no
  path in this host emits a large line today — that same 900 KB document
  snapshots to **666 bytes** at every `max_bytes`, because node text truncates
  at 256 characters — so the 4 MiB transport bound is **declared but
  unexercised**, and the court draft grows a criterion that a ~1.4 MB answer
  round-trips as one line. A second edge: an oversized response becomes a
  **generic** `internal`, so the download must refuse before serializing, which
  §2 shows it already does. `download` is recommended over `download_link`,
  permission is checked at use, and `reported_name` stays bounded, verbatim and
  out of every ledger. G1, G3, P6 and G6 stay open, and D6 is untouched.
- [ ] Design-only, nothing implemented: sink C
  (`labs/native-dom/download-sink-c-design-0.0.1.md`), the ruled shape —
  download bytes return through the control protocol, the filename is a
  reported string and never a path, the request is an action kind on
  `target.act`, the permission is checked at use. Bounds measured first: the
  control request is 65,536 bytes, the **response 4,194,304**, and the network
  layer will not hand over more than 1,048,576 in one fetch, so the transport
  ceiling is real and a design that promised streaming would be lying about
  both. **A single-shot ceiling is recommended over chunking**: chunking means
  a partial transfer held across requests — an owner with a lifetime, a
  resumption token, an eviction rule and a target that can close underneath it
  — inside a host whose whole design is that state is owned, bounded and
  visible. **The filename is the whole security story, and sink C deletes it**:
  the name is never resolved, joined or opened, so traversal is not a bug class
  here; it is reported **verbatim and bounded** rather than silently sanitised,
  because normalising it would hide what the page tried. No new long-lived
  owner, nothing to clean up on close, nothing for an ephemeral profile to
  leave behind. A ten-criterion court draft is in §6; criteria 4 and 10 are why
  sink C is worth choosing, and criterion 9 keeps today's honest
  `unsupported_capability` from being quietly replaced by something vaguer.
  G1, G3, P6 and G6 stay open.
- [ ] Design-only, nothing implemented: downloads
  (`labs/native-dom/downloads-audit-0.0.1.md`). Every vector measured, and
  **nothing was downloaded** — the fixtures are hermetic. An agent clicking
  `a[download]` is refused `unsupported_capability` / `download_unsupported`; a
  plain link whose response is `Content-Disposition: attachment` is refused
  after the fetch, with `content_type` and `navigation: "failed"` in the
  details; a direct `target.navigate` at an attachment is refused too. **The
  fourth vector is the defect**: a page's own `link.click()` on a download link
  returns normally and does nothing — no download, no error — the same silent
  shape as `handleEvent` and `onabort`, and reachable today whatever is decided
  about downloads. One mechanical detail shapes the design: the attachment
  refusal happens **after** the fetch, so the host already buys a bounded
  download before refusing it, against a 1 MiB network response bound far below
  a real one. Three sink shapes: **(c) the bytes come back over the control
  protocol, recommended** — the host keeps writing only its own sealed records,
  and a page-authored filename becomes **a reported string rather than a path**,
  which deletes the traversal surface entirely; (a) a per-profile sink
  directory, which would be the host's first arbitrary file writes; (b) a
  shared root, argued against because it mixes profiles. The probe deliberately
  offered the name `report .. /etc/passwd.txt` to make that concrete. Downloads
  are also **where the first permission question lands** by the previous
  ruling: `permission_denied` at the point of use, distinct from
  `session_read_only` and from `unsupported_capability` — three questions,
  three answers — and it is what would move `permissions_effect` off
  `recorded_only`. Protocol shape proposed: an **action kind on `target.act`**
  rather than growing the operation enum. A nine-criterion court draft is in
  §7, whose third criterion — the name never becomes a path — is nearly free
  under (c) and is the audit's argument for it. G1, G3, P6 and G6 stay open.
- [ ] Design-only, nothing implemented: permissions enforcement
  (`labs/native-dom/permissions-enforcement-audit-0.0.1.md`). **The gap is real
  and it is not a lie.** Measured: `network` **is** a capability — `offline`
  makes `target.open` answer `permission_denied` with `network policy:
  network_offline` — while `permissions` is a record: under `deny_by_default` a
  page's own `fetch` still succeeds and `profile.storage.put` still succeeds,
  and the value round-trips, persists on the **profile** (the session is only
  the handle) and is counted in `memory.report`. The host labels it
  `permissions_effect: "recorded_only"` in **both** `profile.inspect` and the
  memory report, so a client discovers the truth without reading the plan —
  unlike the silent failures this batch found elsewhere. **What is missing is
  not enforcement machinery but anything to enforce**: network already has its
  own enforced field, downloads are refused wholesale, and geolocation, camera,
  microphone, notifications and clipboard have no APIs at all, so the set of
  permission-bearing capabilities is **empty**. Per-session and profile-level
  enforcement are therefore the same work in two scopes and both are
  **unfalsifiable today** — a court could only pass vacuously, which this
  host's discipline forbids. Recommendation: **keep `recorded_only`, change no
  host code**, and bind permissions to **downloads** whenever downloads are
  ruled in, taking the seven-criterion court draft with that slice. Its fourth
  criterion is the one that keeps the label honest: `permissions_effect` stops
  saying `recorded_only` only when it stops being true. Recorded dependency:
  a readonly session already refuses `profile.policy.set` with
  `session_read_only`, and the two refusals must stay distinct from
  `permission_denied`. G1, G3, P6 and G6 stay open.
- [ ] Design-only host-side triage of P6's six remaining capabilities
  (`labs/native-dom/p6-host-triage-0.0.1.md`), each measured black-box rather
  than read off the plan. **Permissions**: the host already reports
  `permissions_effect: "recorded_only"` — it says out loud that it records and
  does not enforce — and `profile.policy.set` accepts exactly
  `{session, network, permissions}`, refusing any other field. **Downloads**:
  clicking a `download` link answers `unsupported_capability` with
  `reason: download_unsupported`, a typed refusal rather than a silent failure.
  **History**: per target, bounded to eight, and measured after one link click
  as `{can_go_back: false, length: 1, position: 0}` with `traverse -1`
  answering `not_found` / `history_offset_out_of_window`; there is no
  profile-level or persisted history at all. **Cache**: none — the network
  layer's "cache" is the bounded per-profile TLS session cache. **Readonly**:
  `profile.inspect` already reports `read_only: false` and **nothing can set
  it**, so the report exists without the capability. **Copy-on-write**:
  nothing. Ordering by cost and dependency: readonly first (the field exists,
  smallest protocol change, precondition for COW), then permissions — whose
  honest version may be to keep `recorded_only` until a permission-bearing
  capability exists to enforce against — then history persistence, downloads,
  copy-on-write, and **cache last and only with a memory ruling, since it is
  the one item that makes G1 and D6 worse**. None of the six moves D6, whose
  gap is the first-request constant and the realms; profile machinery measures
  about 16 KB. G1, G3, P6 and G6 stay open.
- [~] Run report, headless and read-only, **BLOCKED on the baseline half**:
  the G1 comparison campaign (`labs/native-dom/g1-campaign-0.0.1.md`). The
  route half is measured in the shape the gate asks for, both arms, eight
  targets, with peak and post-close and with the host's tracked realm bytes
  reported beside every footprint. System: empty **213,064**, one target
  **3,359,056**, eight **6,046,032**, peak 6,111,568, and **5,898,504 retained**
  after close — nothing returned. Arena: empty 196,680, one target
  **2,818,432**, eight 6,488,616, peak 6,505,000, **2,539,808 retained** and
  **3,768,512 returned** at close. Marginal target 383,854 system against
  524,312 arena. Reported as a pair per the standing ruling, since either alone
  flatters one arm. **The named same-machine comparison could not be run**: the
  sanctioned courts require a Lightpanda 0.4.0 binary that is absent and would
  be fetched from GitHub releases, and their `--lightpanda` argument is
  required, so they will not run Chrome-only even though Chrome is installed;
  `servo-control` is not built. The `process-tree-sampler` is **not** a blocker
  — it builds offline, which I verified without touching its source. **I did
  not hand-roll a Chrome-versus-route comparison outside the court**: a
  different sampler and readiness condition presented as G1 evidence would be
  precisely the guessed pass the brief forbade, so the route numbers are
  labelled as the route's own and the comparison is recorded as unrun. To
  unblock: a local Lightpanda binary matching the pinned digest or explicit
  authorisation to download it, and a built `servo-control` for that row; both
  courts already accept the native host as an optional arm, so no code is
  needed. G1, G3, P6 and G6 stay open.
- [ ] Design-only, nothing implemented: the 720 KB allocator delta
  (`labs/native-dom/allocator-delta-audit-0.0.1.md`). **There is exactly one
  product-controllable path and it is already built and opt-in.** Measured with
  libmalloc's own accounting: on the system arm a realm's `in_use` rises about
  330 KB — exactly the host's tracked `script_realm_bytes` — while the
  footprint rises **1,458,176** for the first realm and 360-606 KB after, so
  the difference is **pages touched across libmalloc's magazines, not engine
  demand**. On the arena arm `in_use` rises about **4.5 KB** per realm, because
  the realm never enters libmalloc at all, and the footprint rises 524-786 KB
  inside its own mapping. Two details recorded rather than smoothed: libmalloc's
  8 MiB reservation lands on realm 3 in one run and at a different starting
  point in another, so **reservation timing varies between runs while the
  footprint deltas do not**; and the arena is not thrift either — ~557 KB per
  realm against ~317 KB tracked, so ~240 KB is its own page granularity. On
  recovery the arms diverge completely: closing four targets returns nothing on
  the system arm and 1,851,488 on the arena, and `memory.trim` — implemented as
  `malloc_zone_pressure_relief` plus an arena tail `madvise` — reports
  `released_bytes: 0` on both, so **the host already asks and libmalloc already
  refuses**. The conclusion is that **D6 is not an allocator problem**: even the
  arena arm measures 6,046,200 against the 4,178,196 threshold, so the arena
  makes it slightly better and nowhere near passing. This ends that line of
  enquiry; the remaining honest candidates are the engine's own ~330 KB per
  realm, the first-request constant, or the G1 comparison campaign the gate
  actually asks for. G1, G3, P6 and G6 stay open.
- [ ] Design-only, nothing implemented: the first realm's fixed cost
  (`labs/native-dom/first-realm-cost-audit-0.0.1.md`), measured after paying
  the first-request constant so it is not double-counted. **The first realm is
  not one realm's worth of memory**: on the system arm it costs **1,490,944**
  while the host accounts only **327,456**, and by the third and fourth realm
  the marginal cost has fallen to ~300,000, so there is a one-time engine cost
  of about **1.16 MB** on top of ~0.3 MB per realm. **Most of that one-time
  cost is the allocator, not the engine**: the same first realm on the arena
  arm costs **770,072** with nearly identical tracked bytes, so roughly **720
  KB is libmalloc page behaviour** rather than QuickJS demand. **The two arms
  trade live cost against recovery**: system is cheaper per live realm and
  returns nothing on close (+32,768), arena is dearer per realm but returns
  **1,851,488** when four targets close and ends 2.13 MB lower. D6's numbers
  are untouched — 6,619,592 system, 6,046,200 arena, target 4,178,196 — and
  decompose as the first-request constant (~1.77 MB), profile machinery
  (~0.02 MB), the first realm's one-time cost, and ~0.3-0.54 MB per realm
  after. So a D6 repair has three honest shapes and none is profile work:
  attack the 720 KB allocator delta, attack the first-request constant, or
  **re-derive what D6 measures** — because a live-only criterion rewards the
  arm that never gives memory back, which is a property of the criterion
  rather than of the route, and that is a ruling rather than a repair. For G1
  the shape is favourable and now measured end to end: profiles free, marginal
  target 0.3-0.5 MB, two one-time constants that a campaign should report
  separately. G1, G3, P6 and G6 stay open.
- [ ] Design-only, nothing implemented: the host's fixed first-request cost
  (`labs/native-dom/first-request-cost-audit-0.0.1.md`), which was meant to be
  the first-profile audit and **dissolved its own premise**. Creating a profile
  costs **nothing measurable**: the ~1.77 MB step belongs to the host serving
  its **first line of any kind**, and the strongest form of that measurement is
  that a **malformed, unparseable line answered `invalid_request` pays it in
  full** — before any dispatch. It is allocator-independent (system +1,736,968,
  arena +1,769,736), it corresponds to **no host-tracked owner** (every owner
  class reports zero bytes across the jump; the only non-zero figure is a 16
  MiB *limit*), it is working set rather than heap (footprint 196,680 to
  2,097,488 while resident goes 4.8 MB to 7.5 MB), and it does not come back:
  `memory.trim` releases 0 and costs 65,536 of its own. **This corrects the
  attribution in the open-goal triage**, which put the step next to
  `profile.create` only because that was the first request it sent. The
  consequence for **P6's D6** is sharp: of the 6,619,592 it measures against a
  4,178,196 target, roughly 1.9 MB is this process constant and 1.41 MB is the
  first realm, while profile machinery is about 16 KB — so **D6 cannot be met
  by working on profiles**, and either the baseline shrinks, the realm shrinks,
  or the criterion is re-derived by ruling. For **G1** the correction helps:
  profiles are free and the marginal target is 0.33 MB, which is the shape an
  efficiency argument wants, with the fixed cost measured as a one-time
  constant rather than folded into per-target figures. The triage's ordering is
  amended accordingly: the first-realm audit becomes the next candidate. G1,
  G3, P6 and G6 stay open.
- [ ] Design-only triage of the four open goals
  (`labs/native-dom/open-goal-triage-0.0.1.md`). **None of G1, G3, P6 or G6 is
  limited by main-extension slack**, so the 2,944 bytes left there buy nothing
  for any of them and the brief's filter yields **no candidate to take**. The
  host's memory was decomposed live on the shipped binary: empty host 196,680,
  **first profile +1,769,736** — *later corrected: that step is the first
  served line of any kind, not the profile; see
  `first-request-cost-audit-0.0.1.md`* — session +0, **first realm
  +1,409,024**, second target +327,680, and closing returns nothing — so two fixed costs dominate
  while a marginal target is only 0.33 MB. That localises both G1's efficiency
  question and P6's **D6**, which wants live footprint under 4,178,196 and
  measures 6,619,592. G3's open item is read from its receipts rather than
  rerun: `-surface` is 106 of 110, failing only the retention pair, post-hide
  footprint per round `278,528 · 425,984 · 458,752` with a slope of 180,224 —
  and every G3 candidate is deferred because measuring further needs a visual
  run the standing rule forbids. P6's remaining capabilities — cache, history,
  downloads, permissions, readonly, copy-on-write — each need a new protocol
  operation against a closed enum. **G6 is not a work item**: it is the
  conjunction that closes when the others do, with G2 already green. Suggested
  order if one is wanted: the first-profile step, then the first-realm step,
  then the comparison campaign the G1 gate actually asks for, then P6's
  capabilities behind protocol rulings, and G3 last with explicit visual
  authorisation. G1, G3, P6 and G6 stay open.
- [ ] Design-only, nothing implemented and **HOLD is the answer**:
  architecture-level main-slack recovery
  (`labs/native-dom/main-slack-recovery-audit-0.0.1.md`). The target was 1,408
  bytes so `getElementsByClassName` could be verified; **the best measured
  recovery is 304, and nothing compounds**. Five candidates were built against
  the shipped line at 62,592: removing the `quota` helper outright **+304**;
  that plus `writeTokens` inlined **+208** — strictly more removed and *less*
  reclaimed, because the block boundary sits between them; `quota` delegating
  instead of deleting **+48**; merging the two signal `WeakMap`s **−304**; and
  giving `dataset` and `classList` a shared per-element record **−6,112**.
  Inlining `kebab` cost 448 because it replaced one closure with three arrows
  at the call sites. **Every indirection lost.** The rule this establishes,
  now measured twice: **main slack is closure count** at roughly 300 bytes
  each, so the way to spend less is to write fewer live functions rather than
  fewer bytes. The only bigger lever anywhere is the prose strip at 832, which
  an earlier ruling declined and which reaches just 1,136 even taken with the
  best candidate. C7 is also recorded as the one that trades **review safety**
  for bytes — it puts one invariant into three copies that no criterion would
  keep in step — which is a cost the courts cannot catch. Nothing was
  implemented; `getElementsByClassName` stays unimplemented with its frozen
  36-criterion court as the gate. G1, G3, P6 and G6 stay open.
- [~] Implemented and qualified on the native route, court 37 of 37: the
  smallest main-only method that fits
  (`labs/native-dom/smallest-method-audit-0.0.1.md`). Every price measured for
  the actual member, never extrapolated — the previous round's mistake.
  Against the shipped line at 62,016: `hasAttributes` **+464**,
  `hasChildNodes` **+464**, `getElementsByTagName` +496 and **+576 with the
  never-throws guard**, all fitting; **any two of them +5,056 and all three
  +5,520, both over the bound**. So exactly one member fits, whichever it is,
  and the wall this time sits between the first and the second rather than
  inside the first. The guard that sank `getElementsByClassName` costs 80
  bytes here, because the body is a single delegation rather than token
  joining. Measured behaviour of the guarded method: document and element
  scope in document order, the element itself excluded, case-insensitive
  matching, `'*'` equal to `querySelectorAll('*')`, empty and unparseable
  arguments answering empty without throwing, a plain non-live array, working
  in a detached subtree, and inert inside a dispatch. **It adds no authority,
  no reentrancy and no lifecycle**: it calls the base's `querySelectorAll`,
  which a page can already call, allocates one array per call and owns nothing
  after. No child cost, no handle change. Two losses recorded: the live
  `HTMLCollection` inherited from `querySelectorAll`, and a namespace-ish name
  such as `a:b` answering empty where a browser matches the literal name.
  **Ruled and built**: the guarded `getElementsByTagName`, one member on
  `Node` serving both call surfaces, at the measured **+576** — main slack
  62,016 to 62,592 with 2,944 left, M1 and M2 unmoved, no child cost. The
  court was frozen one commit ahead and reads **37 of 37** against **6 of 37**,
  pinning both scopes, the element's own exclusion, document order,
  case-insensitivity, `'*'`, detached subtrees, the empty and unparseable
  names answering empty without throwing, a fresh array per call, dispatch
  inertness, child absence and owner release — and **both losses as criteria**:
  the plain non-live array, and `a:b` answering empty where a browser matches
  it literally. `hasAttributes` and `hasChildNodes` were not taken; they do not
  fit alongside it. **`getElementsByClassName` stays on hold**, its frozen
  court reading 5 of 36 by design and rerun on this binary as
  `-class-name-query-hold` so the hold is visible in the evidence rather than
  only in prose. Twenty-eight receipts rerun on the binary. G1, G3, P6 and G6
  stay open.
- [ ] Design-only triage, nothing implemented and no court frozen:
  page-observable gaps under the remaining slack
  (`labs/native-dom/browser-gap-triage-0.0.3.md`). Sixty-nine names probed
  mechanically; the missing ones sort into main-only methods, main-only
  accessors, base work, parser work, layout work and host-authority work.
  **The headline is the budget, not the inventory**: at 62,016 of 65,536,
  **one plain prototype method costs 464 and fits with 3,056 left, while two
  cost 5,056 and a single `defineProperty` accessor costs 4,880** — over the
  bound on its own. So **accessors are about ten times a method here**, and
  the price is block-quantized with the current fill sitting near a boundary,
  which is why five accessors and five methods land within 300 bytes of each
  other while one of each differs tenfold. Earlier calibration on a less-full
  extension read 512 to 832 per method, so **the per-member price is a
  function of the current fill and must be re-measured per slice, never
  extrapolated**. Recommendation: take at most one method — I would argue for
  `getElementsByClassName`, which older pages actually call, reuses the
  selector engine, is a method rather than an accessor and carries no
  authority, reentrancy or lifetime question — or hold the reserve.
  **Ruled, frozen, built and then stopped at its own gate**: the court is
  frozen at 36 criteria and reads 5 of 36 on the tree, but the method
  **does not fit** — as designed 67,552, lean 67,040, and even stripped of its
  never-throws guarantee 66,944, against a 65,536 bound that this ruling
  forbids moving. `shim-footprint` reads 17 of 18 on every shape and the
  implementation was reverted, so nothing shipped and the frozen court
  describes a method this host does not have. **The recommendation that led
  there was mine and it was wrong**: the `+464` in §2 was a one-line
  `hasAttributes` and was really the remainder of the current allocation
  block, not the price of a method — the triage's own rule about not
  extrapolating applied to the triage, and the gate caught it. Everything
  else is priced out for reasons other than cost: the base group needs
  insertion primitives and would touch `cloneNode`'s frozen closed set;
  parser, layout and host-authority groups each need a design and a ruling
  first. `setInterval` is called out for its own audit because it would sit
  inside the timer reserve. G1, G3, P6 and G6 stay open.
- [~] Implemented and qualified on the native route, court 25 of 25:
  `AbortSignal.timeout()` under the slack guard
  (`labs/native-dom/abort-signal-timeout-audit-0.0.1.md`). **The short answer
  is that the remaining slack fits the version that misbehaves and not the
  version that behaves**, and both halves are measured. `timeout()` would own
  an entry in the page's own timer table — one `Map` with `limit: 64`, shared
  with every `setTimeout` — and it does fire, inside the host's job drain under
  the request's deadline, with `reason.name` `TimeoutError`. But **62 timeout
  signals left the page's `setTimeout` refusing immediately**, which is what
  the ordinary idiom `fetch(url, {signal: AbortSignal.timeout(5000)})` would do
  to a page after sixty-four in-flight requests. The variant that fixes it — a
  quota of its own, measured at 16, leaving 48 for the page — **fails the
  frozen slack check outright**: `shim-footprint` reads 17 of 18 with the
  main-only bound failing at **66,704 against 65,536**. The cheap variant fits
  at 61,504 with 4,032 to spare. The child floor is not the constraint: it has
  13,462 bytes and neither shape threatens it. Retention is clean either way —
  60 long timeouts held, target closed, owners returned to **exactly the
  baseline**. No guard was touched: `timeout` stays absent in the tree, the
  handle's key set is unchanged, and no host path aborts a page's signal;
  moving the slack bound is listed as a trade the ruling can see, not as a
  proposal. **Ruled and built as T3b**: the threshold reads the page's own
  timer table and refuses at 16, so the page keeps **48 slots for
  `setTimeout`**, the refusal is a `RangeError`, and the quota carries no state
  of its own — the shape that made it fit at **62,016 of 65,536**, 3,520 to
  spare, with M1 and M2 unmoved. **The standing guard was amended by ruling**:
  the only host path that may abort a page's signal is the timer `timeout()`
  minted for that signal, and it reaches nothing else. The surface court's
  `timeout`-absent pin was amended to require it, which is what the pin was
  for. Court **25 of 25** against **6 of 25**; two of its criteria were amended
  after their first run because they measured the fixture rather than the rule,
  recorded in §7.3. Twenty-six receipts rerun on the binary. G1, G3, P6 and G6
  stay open.
- [~] R1-R5 implemented and qualified on the native route, court 37 of 37:
  the rest of `AbortSignal` (`labs/native-dom/abort-signal-surface-audit-0.0.1.md`), split
  into R1 the signal as an `EventTarget` with an `abort` event, R2 `reason`,
  R3 `throwIfAborted`, R4 the `abort` static, R5 `onabort`, R6 `timeout`.
  **Two of the gaps fail silently today**, the same shape as the `handleEvent`
  defect: `controller.abort("because")` accepts the argument and discards it,
  and `signal.onabort = fn` sticks and never fires. **Nothing here needs the
  base, the handle or the host**: `reason` and the `onabort` handler live in
  `WeakMap`s inside the main extension's own closure, so the frozen
  exact-key-set criterion still passes on both candidates, as do
  `listener-options`, `capture-phase`, `passive-listener` and `event-target`.
  The reentrancy shape is what changes: today `abort()` flips a `WeakSet` and
  runs no page code, while under R1 it **dispatches** — measured as
  `first > abort listener > back > third`, the signal's listener running
  synchronously inside the element's dispatch — which is not new authority,
  since a page can already dispatch inside a listener, but it means **no host
  path may abort a page's signal** until that is ruled again. Cost: R1-R5 cost
  **no per-child bytes** and 5,472 of main-only slack; R6 adds 1,472 more and
  owns a timer from the page's existing budget. **The binding constraint is now
  the slack, not the child floor**. **Ruled and built**: R1-R5 taken, R6
  `timeout()` deferred and **pinned absent** by the court so taking it later
  amends a criterion. The court was frozen one commit ahead and reads **37 of
  37** against **13 of 37**, pinning the two silent failures by value, the
  ruled reentrancy shape, the closure-owned state and the unchanged handle key
  set, and re-running four L5 guarantees. **The child delta is 0** — M1 stays
  232,298 — and the whole price is main-only slack, 54,560 to 60,032, leaving
  5,504 of 65,536 against an M1 headroom of 13,462. The standing constraint
  goes with it: no host path may abort a page's signal. Twenty-five receipts
  rerun on the binary. G1, G3, P6 and G6 stay open.
- [~] Implemented and qualified on the native route, court 27 of 27: `signal`
  and `AbortController` (`labs/native-dom/abort-signal-audit-0.0.1.md`), the last
  rung of the listener ladder and the only one whose obvious implementation is
  unsafe. **The refusal is now measured, not argued**: on the naive candidate a
  page passing `{get aborted(){…}}` gets its own code executed **inside the
  host's dispatch walk**, and in the third probe that getter used the window to
  `removeEventListener` a listener the same dispatch was about to run — which
  worked, so a page could rewrite which listeners a dispatch delivers to, from
  inside the dispatch. The branded design refuses it: a closure-owned `WeakSet`
  of host-minted signals, a second for the aborted ones, both read through
  captured methods, `aborted` as a host getter, and a `TypeError` for any
  signal the host did not mint — measured to refuse both the page object and
  the getter while keeping correct abort semantics. **Cost gradient**: naive
  +1,504 per child (refused), branded with the classes in the base **+5,952**,
  branded with the classes in the main extension **+2,976** but needing one new
  entry in the one-shot handle. Safety costs two to four times the unsafe
  version, and the base variant makes every child pay 2,976 more for two
  classes no child can construct. **A dependency worth fixing regardless**: the
  split candidate widens the handle and **every frozen court still passes** —
  the only criterion that reads the handle asks whether it names `EventTarget`
  — so the handle's shape is guarded by nothing today, and a court should pin
  its exact key set whatever is ruled here. Also recorded: none of the
  candidates gives an `EventTarget` signal, `reason`, `throwIfAborted` or the
  statics, and one `child-frames` M4 footprint-acceleration failure on the base
  variant did not reproduce in two reruns. **Ruled and built**: S1 refused, S3
  taken — classes in the main extension, brand and flags in the base, one new
  handle entry named `signals`. The court was frozen one commit ahead, **pins
  the handle's exact key set** whatever happens to this rung, and reads **27 of
  27** against **4 of 27** on the pre-implementation tree. M1 232,298, M2
  1,624,588, headroom 13,462, slack 54,560 — the ruled +2,976 per child
  exactly. **One criterion was amended after its first run against the
  implementation and the amendment is recorded in §9.1**: S8's expected
  sequence was written wrong — the plain listener runs in both dispatches — so
  the code was right and the criterion was not. Twenty-four receipts rerun on
  the binary. The listener ladder is complete; the signal's own event model,
  `reason`, `throwIfAborted` and the statics stay separate candidates. G1, G3,
  P6 and G6 stay open.
- [~] Implemented and qualified on the native route, court 30 of 30: passive
  listeners
  (`labs/native-dom/passive-listener-audit-0.0.1.md`). This is the rung that
  **takes power away from the page**, and the finding is an authority one:
  measured through a real `target.act` on both builds, a link whose own click
  listener is registered `{passive: true}` and calls `preventDefault()`
  **refuses the agent's navigation today** and does not on the candidate. A
  page can currently block an agent's action through a route the standard
  calls inert — declaring a listener passive is a promise not to cancel, and
  this host honours the cancel anyway. A second escape closes with it: a
  passive listener can today dispatch a nested event and cancel the **outer**
  one from inside it, which the candidate refuses while leaving the nested
  event cancellable. The model is a flag per record and a window opened and
  closed around each invocation, so passivity never leaks between listeners,
  phases or dispatches. **+400 bytes per child** (M1 229,322, M2 1,603,756),
  the cheapest rung in the ladder, headroom 16,438, floors unmoved, and every
  court passes on the candidate. Recorded divergence: a **late**
  `preventDefault` after the dispatch still sets the flag on both builds, but
  the host has already read its answer, so a page can only mislead itself.
  Also recorded, because it nearly became a false finding: `form` and
  `child-frames` first read 176/177 and 79/80 on the candidate with checks
  *missing*, which was the pinned CDP client having vanished from the ignored
  `target/labs/d4` — the **shipped** binary read the same, and restoring the
  client from the local npm cache brought both builds back to 179/179 and
  82/82. L5 `signal` stays deferred, and no page getter goes into the walk.
  **Ruled and built**: the flag on the record, the window saved and restored
  around each invocation, `preventDefault` gated by it. The court was frozen
  one commit ahead and reads **30 of 30** against **16 of 30**, with the
  authority pair run through a real `target.act` — a passive listener can no
  longer refuse the agent's navigation, and a plain listener still can, so
  exactly one route closed. Also pinned by ruling: a late `preventDefault`
  still writes the flag, and no event type is passive unless the page says so.
  M1 229,322, M2 1,603,756, headroom 16,438, slack 46,864 — **the ruled +400
  per child exactly**, with no base comments added this time, the lesson of
  the capture rung. Twenty-three receipts rerun on the binary. G1, G3, P6 and
  G6 stay open.
- [~] Implemented and qualified on the native route, court 36 of 36: the
  capture phase
  (`labs/native-dom/capture-phase-audit-0.0.1.md`), deepening the deferred L4
  row. Measured on the shipped build against a candidate: today the order is
  `target > box-cap > box-bub > doc-cap > win-bub` and would become
  `doc-cap > box-cap > target > box-bub > win-bub`; `eventPhase` would read
  1,2,3 instead of 2,3,3; a **non-bubbling event never leaves its target
  today** and would reach ancestor capture listeners, which is a real
  behavioural expansion and not just a phase; and `removeEventListener` with
  the wrong flag stops removing a listener it must not. **The authority
  boundary is measured on both builds and separates cleanly**: a page's
  capture listener would run before the target's own on the host's synthesized
  click and could suppress it with `stopPropagation` — the target's listener
  ran on the shipped build and does not on the candidate — while the host's
  own decision does not move at all: with `stopPropagation` the navigation
  happens on both builds, with `preventDefault` it is refused on both, and
  `target.act` answers ok either way. So the phase is a new place for a page
  to stand, not new power, and what it suppresses is the page's own listeners.
  Cost **+1,664 bytes per child** (M1 228,922, M2 1,600,956), the **third
  independent measurement of that same figure**, leaving 16,838 bytes of M1
  headroom against unchanged floors, slack 46,464. Every court that could feel
  an ordering change passes on the candidate, `lifecycle` and `page-navigation`
  included. It is base-only, so every child pays and no child can use it. L3
  `passive` and L5 `signal` stay deferred. **Ruled and built**: capture, target,
  bubble; a non-bubbling event captures down without bubbling; capture is
  listener identity so the wrong-flag removal is gone; the stop flags, `once`
  and `handleEvent` compose as audited. The court was frozen one commit ahead
  and reads **36 of 36** against **16 of 36**, and its authority pair is
  measured through a real `target.act`. M1 228,922, M2 1,600,956, headroom
  16,838, slack 46,464 — **the ruled +1,664 per child exactly**, which took a
  correction: the first build carried four base comments and cost +2,944, so I
  measured the split (code +1,664, prose +1,280), found trimming gives back
  bytes in **quantized steps rather than linearly** — four blocks cut to one
  line each returned only 256 — and moved those invariants into the audit and
  the court, where a page pays nothing for them. Twenty-two receipts rerun on
  the binary. G1, G3, P6 and G6 stay open.
- [~] First rung implemented and qualified on the native route, court 30 of
  30: listener options, `handleEvent` and `AbortController`
  (`labs/native-dom/listener-options-audit-0.0.1.md`). Two of the gaps are
  worse than unimplemented, measured: `removeEventListener(t, f, false)`
  **removes** a listener added with `capture: true`, which the standard
  forbids, and an object with `handleEvent` is dropped **silently at
  registration**, so nothing runs and nothing says why. `CAPTURING_PHASE` is
  not even a constant in the base. The options are not lost in the store —
  `addListener` never receives them, because **three call sites forward two
  arguments**: the base's `Node` methods, the window's arrows and the new
  `EventTarget` class. That prerequisite, L0, is worth nothing alone and is
  required by all five candidates: L1 `once`, L2 `handleEvent`, L3 `passive`,
  L4 `capture`, L5 `signal`/`AbortController`. A cumulative ladder was built
  and priced: **+1,392 bytes per child** for L1-L3 (M1 227,018), **+1,664**
  more for capture (228,682), **+1,376** more for the signal (230,058) —
  **+4,432 in total**, which leaves M1 headroom at 15,702 against unchanged
  floors, and main slack 49,760 inside 65,536. Every existing court passes at
  every rung. The authority note is the one to read: L3 **reduces** page power,
  since a passive listener can cancel today and the host reads that same flag
  for navigation; L4 lets a page stop the host's own synthesized event before
  the target sees it; and L5 as measured **puts a page object inside the
  dispatch loop**, so a page could pass `{get aborted(){…}}` and run its own
  code inside the host's walk — the audit measures the naive version and
  recommends branding host-minted signals through a closure-owned `WeakSet`
  instead. Window divergence, C2b and the attribute-name and selector losses
  stay as they are. **Ruled and built, first rung only**: L0 forwards the
  options at all three call sites, L1 spends a `{once:true}` registration after
  one run — including under two of the agent's own clicks — and L2 calls an
  object with `handleEvent` with itself as the receiver. The handler is
  resolved **at registration**, so the walk reads no page property while
  dispatching; a page that swaps the method keeps what it registered, recorded
  as a deliberate divergence from the standard's re-read. `capture`, `passive`
  and `signal` stay deferred and are deliberately unpinned by the court, so
  they can land without amending it. The court was frozen one commit ahead and
  reads **30 of 30** against **14 of 30**. M1 225,626 to 227,258, M2 1,589,308,
  slack 44,768, headroom 18,502 — all inside unchanged floors. **The rung cost
  more than the audit priced it**, +1,632 against +1,392, and the difference
  was measured rather than assumed: the three explanatory comments I added to
  the base cost **384 bytes per child** by themselves, so the code is +1,248
  and my prose is the rest. Prose in the base is priced per child like
  everything else there. Twenty-one receipts rerun on the binary. G1, G3, P6
  and G6 stay open.
- [~] Implemented and qualified on the native route, court 24 of 24: an
  `EventTarget` constructor (`labs/native-dom/event-target-audit-0.0.1.md`). Measured on the
  shipped build: `EventTarget` is **`undefined`** and an element's chain is
  `Element > Element > Node > Object`, but **the behaviour is already there** —
  the base keys listeners by object in a `WeakMap`, so borrowing
  `addEventListener` onto a plain object works, and a detached element is a
  working bus today. Three containment probes hold and are recorded: a forged
  `{parentNode: realElement}` does **not** reach the real element's listeners,
  a page dispatching the reserved focus type moves nothing, and a page that
  overwrites `dispatchEvent` on the prototype does **not** intercept the
  host's own `target.act` — measured on both builds. The candidate is
  **main-only and widens nothing**, because the handle already hands over
  `addListener`, `removeListener`, `dispatchOn` and `Node`: a three-method
  class plus one `setPrototypeOf`. It costs **nothing per child** — M1 225,626
  and M2 1,577,884 do not move — and 2,352 bytes of main-only slack (40,576 to
  42,928, 22,608 left), the exact inverse of the error-class slice where every
  child paid for something no child could observe. The loss matrix records
  what the constructor does **not** fix, all measured: `{once:true}` ignored
  so the listener ran twice, `capture` accepted and ignored, `AbortController`
  undefined, and a `handleEvent` object registered and **silently never
  called**. My recommendation is written into the audit: the cost is the
  lowest of the batch and so is the value, so rule on `EventTarget` and the
  listener options together, or take it knowing it is a name and not a
  capability. C2b stays scope-closed and the selector error names stay as
  built. **Ruled and built**: the class, the chain, `new EventTarget()`, and
  subclassing, all in the main extension, with the base untouched and the
  handle not asked for anything new. The court was frozen one commit ahead and
  reads **24 of 24** against **11 of 24** on the build before, where the name
  was a `ReferenceError`. **M1 225,626 and M2 1,577,884 did not move**, so a
  child pays nothing; the price is 2,352 bytes of main-only slack, 40,576 to
  42,928. `window instanceof EventTarget` stays `false` by ruling. The three
  authority containment properties were re-measured under the constructor and
  hold. One implementation note worth keeping: the first attempt put the class
  above the extension's `Node` binding and every document failed to build, a
  temporal-dead-zone error that the court caught on its first run rather than a
  reviewer catching later. Twenty receipts rerun on the binary. G1, G3, P6 and
  G6 stay open.
- [~] Implemented and qualified on the native route, court 34 of 34:
  attribute-name validation, re-measured
  (`labs/native-dom/attribute-name-validation-audit-0.0.2.md`, superseding
  `-0.0.1`). Two earlier slices changed the answer: the clone copies
  internally, so the cloning regression that stopped this in `0.0.1` is no
  longer reachable, and the base captures a `DOMException`, so the vocabulary
  is already there. **Six authoring surfaces funnel through one
  `setAttribute`** — `id`, `className`, `dataset`, `classList`,
  `toggleAttribute` and the method itself — so the slice is one guard in the
  base, not six. Measured on the shipped build: the parser produces ten
  awkward names, `-lead .dot 1bad aé id ok-name under_score upper weird:name
  x.y`, and **the deep clone carries all ten**. Candidate V1 rejects `1bad`,
  `a b`, the empty name and `a"b` with `InvalidCharacterError` code 5, keeps
  `ns:x`, still lowercases, leaves `removeAttribute` lenient, and costs **+864
  bytes per child** (M1 225,626, M2 1,577,884) — a third of the `0.0.1`
  estimate, because it throws the constructor the base already captured —
  leaving 20,134 bytes of M1 headroom against unchanged floors. Candidate V2
  adds the one rule the base cannot see, a dataset key with a dash before a
  lowercase letter throwing `SyntaxError`, for **800 bytes of main-only slack
  and nothing per child**. Every court that could notice passes on both,
  `element-view` 23/23 with its clone criterion included. The recorded loss is
  the ASCII approximation of the `Name` production: a page will be unable to
  author `aé` while the parser still produces it and the clone still carries
  it. **Ruled and built**: both candidates
  taken, the guard running before the lowercasing, `ns:x` accepted, the ASCII
  approximation accepted as an explicit loss, the parser and the copy
  untouched, `removeAttribute` and `toggleAttribute(false)` lenient,
  `classList` unchanged, and the thrown message carrying neither the offending
  name nor the value. The court was frozen one commit ahead of the code and
  reads **34 of 34** against **12 of 34** on the build before, where every bad
  name — including one with a space, an empty one and one with a quote — was
  accepted. M1 225,626, M2 1,577,884, main-only slack 40,576, all inside the
  unchanged floors with 20,134 bytes of M1 headroom, so nothing had to stop.
  Nineteen receipts rerun on the binary. **One unreproduced test failure is on
  the record**: a single `cargo test` run reported 53 passed and 1 failed in
  0.42 s right after a rebuild, and its name was lost to the filter I ran it
  through; a stability review of **72 runs since, none failing** — 12 plain, 10 serial
  under `--no-fail-fast -- --test-threads=1`, 5 build-then-test, 5 parallel,
  20 of the single suspect and 20 of it four ways concurrent — did not
  reproduce it. The suspect is named by timing, not by a reproduction: the
  suite's entire 2.02 s lives in
  `surface::tests::failed_shows_unmap_the_frame_and_reap_the_child`, every
  other group being 0.40 s or less, so a run ending in 0.42 s is what an early
  failure there looks like — its last arm, `/bin/sleep`, is the two seconds,
  and its `/usr/bin/yes` arm is a genuine race between a child's first bytes
  and the parent's read. Inference from a duration, not a diagnosis: the
  failure stays on the record as unexplained, and this is not a claim that the
  suite is clean. G1, G3, P6 and G6 stay open.
- [~] Implemented and qualified on the native route, court 27 of 27: standard
  error classes for the base's own throws
  (`labs/native-dom/error-class-audit-0.0.1.md`), the deferred half of the
  error-name slice. Candidate A measured on top of `d46ee3c`: the selector
  engine and `removeChild` throw the engine's own `DOMException` with the
  standard name, `code` 12 and 8, and the `[object DOMException]` tag, at
  **+304 bytes per child** (M1 224,762, M2 1,571,836) against unchanged floors
  — the same price as on the earlier baseline, so it is stable across two
  builds. **The capture holds, measured**: a page that installs its own `Fake`
  over `globalThis.DOMException` and then trips the engine still catches a real
  `DOMException`, with `e instanceof Fake` false. **The one measured objection
  is gone**: `as_exception()` returning `None` used to blind the host's
  diagnostic, but since the redaction the host reads no message at all and an
  uncaught `DOMException` produces byte-identical details to an uncaught
  `Error` — the order those two slices were ruled in turned out to matter. The
  finding worth the ruling's attention is where the cost falls: the selector
  engine is in the base, so **every child pays 304 bytes for an error no child
  can ever observe**, since a child realm runs no scripts — the frozen
  child-frame court says so on this binary — and the host never passes a
  selector into any realm. The loss matrix records what stays unserved:
  `classList`, the dispatch guard and storage keep named plain `Error`s with no
  `code`, so after A this host has two vocabularies instead of three, and
  `e.name` remains the portable thing. Existing courts on the candidate:
  redaction 23/23, element-view 23/23, element-api 28/28, event-fidelity 62/62,
  dataset 15/15, child-frames 82/82. The class still never reaches `details`,
  by ruling. **Ruled and built**: the four selector entry points throw the
  captured `DOMException` named `SyntaxError` with `code` 12, `removeChild`
  one named `NotFoundError` with `code` 8, and the scope stops there —
  `classList`, the dispatch guard, storage, `cloneNode` and the timers keep
  what they had, so this host carries two error vocabularies and not three.
  The court was frozen one commit ahead of the code and reads **27 of 27**
  against **9 of 27** on the build before. Two of those failures are the ones
  worth having frozen: E5 answered `undefined|undefined|[object Object]`,
  because with `Error` replaced by the page the base's own `new Error` built
  the page's constructor, and E8 answered 0, the index of the name inside the
  message. M1 224,762, M2 1,571,836, main-only slack 38,800 — the 304 bytes
  per child the audit priced on two earlier baselines, against unchanged
  floors, with 20,998 bytes of M1 headroom. The redaction still holds over the
  new class: its own court is 23/23 here and E10 re-checks it inside the error
  court, so a class change cannot pass itself off as a leak repair. Eighteen
  receipts rerun on the binary. G1, G3, P6 and G6 stay open.
- [~] Implemented and qualified on the native route, court 23 of 23:
  page-authored text in a host error
  (`labs/native-dom/page-error-redaction-design-0.0.1.md`). The defect is one
  site, `src/main.rs:2020`: the realm's `eval` copies an exception's message
  into `details.engine_error`, so an uncaught throw in a page's own top-level
  script hands the caller whatever the page put in it — a value read from an
  input, measured, verbatim. The eleven-criterion court reads **17 of 23,
  `passed: false`** on `4a5836f43b38…`, failing R1, R2 and R8 on both
  allocators and nothing else, which is narrower than the audit assumed: a
  listener throwing in the lifecycle, a timer callback, a listener under
  `target.act` and an unhandled rejection are all **contained today**, and the
  session's ledger is clean. R8 is the criterion with teeth — two pages
  throwing one class with two values must produce byte-identical details, so a
  hash, a length or a truncation fails it too. The first run of the court
  failed R11 **on itself**, because the synthetic values were readable and
  shared eight characters with the criteria that report them; the values are
  now opaque. Recommended repair: `engine_error` stops carrying an exception
  message at all and says one of a closed host vocabulary, keeping the typed
  code, the retryable bit, the scope and the fixed reason. Explicitly **not**
  the repair: candidate A from the error-name audit blanks the message as a
  side effect, which hides this instance without fixing it, so the redaction
  must be verified on a build without candidate A. Ruled and built:
  `details.engine_error` no longer carries an exception message at all. It
  says one of exactly two host-authored words — one for a script that threw,
  one for a deadline that expired — chosen from what the host knows rather
  than from what the page said, and the message, the class, the length and any
  digest stop at the catch site rather than being filtered somewhere
  downstream that a later path could forget. The court reads **23 of 23** on
  `30004da4d050…` against **17 of 23** on the build before, and the fix
  **costs nothing**: M1 224,458, M2 1,569,708 and main-only slack 38,496 are
  byte-identical to the previous binary, because no shim source changed.
  Verified with the selector engine exactly as it is, without the
  `DOMException` candidate, so the values are gone because of the redaction
  and not as a side effect of an unrelated slice. Seventeen receipts were
  rerun on the binary. Still pending and deliberately untouched: whether
  details may name the exception's class, and `details.script` carrying an
  external script's `src`, which stays as it is and is written down as a
  page-derived diagnostic rather than widened into this round. G1, G3, P6 and
  G6 stay open.
- [ ] Design-only, nothing implemented and no court frozen: the selector
  engine's error names (`labs/native-dom/selector-error-name-audit-0.0.1.md`).
  Measured, not read: a page catching a selector refusal reads `e.name ===
  "Error"`, because the word `SyntaxError` is only a message prefix, while
  `classList`, the dispatch guard and `localStorage` in the same host set
  `e.name` properly — three conventions, one host. The engine already ships a
  real `DOMException` with the standard `name`, the legacy `code` (12 for
  `SyntaxError`, 8 for `NotFoundError`), a read-only `name` and its own
  `[object DOMException]` tag, so the standard option costs **+304 bytes per
  child** (M1 224,762, M2 1,571,836) against **+640** for a hand-rolled named
  `Error` — the standard shape is the cheaper one, and both sit ~21,000 bytes
  under the unchanged floors. Nothing in the host depends on the shape: no
  `.rs` file reads `.name`, and no selector crosses the boundary at all. Two
  risks recorded: the global constructor is page-replaceable and must be
  captured at base load (today's `new Error` is exposed the same way), and a
  `DOMException` blinds the host's own diagnostic, because rquickjs's
  `as_exception()` returns `None` and `engine_error` falls back to a
  contentless string. **A separate and more urgent finding came out of the
  same measurement** and is recorded unfixed in §7: an uncaught page throw puts
  its message verbatim into `details.engine_error`, so a page that builds a
  string from a form value and throws leaks that value into a control error —
  measured, general to any `throw`, and against the standing rule. It needs a
  host-side ruling of its own and must not be repaired as a side effect of the
  error class. Both throwaway candidate builds were discarded with their
  worktree. G1, G3, P6 and G6 stay open.
- [~] Implemented and qualified on the native route, court 23 of 23: the
  clone copies internally (`labs/native-dom/clone-node-audit-0.0.1.md` §11),
  the first half of the attribute-name validation ruling. **Copying is not
  authoring**: the clone writes the element's own attribute map directly, the
  way the parser's own build does, instead of replaying every attribute
  through `setAttribute`. It is taken before the validator exists rather than
  after it breaks something — the validation audit measured that a validating
  `setAttribute` makes an ordinary parsed element **throw on clone**, because
  a parsed document legitimately holds names like `1bad` and `weird:name` that
  authoring would reject. The fix costs nothing; it removes a call. M1 is
  unchanged at **224,457**, M2 **1,569,707** against unchanged floors of
  245,760 and 1,720,320, and main-only slack is 38,496 inside 65,536, with
  sixteen receipts rerun on the binary. The criterion was frozen one commit
  ahead of the code and **passes on every committed build by construction**,
  since nothing validates yet, so I checked that it discriminates rather than
  assuming: against a throwaway build carrying a validator beside the old
  re-authoring clone it fails 21 of 23 on both allocators, and that receipt is
  labelled as belonging to no commit. Provenance correction, kept because the
  record should show it: the approval for this push named the range
  `d730734..8cc0200`, but `d730734` was already eighteen commits back on the
  remote; `origin/main` was verified at `78bd78a` immediately before the push,
  so the fast-forward that ran was `78bd78a..8cc0200` — the same three
  reviewed commits, with nothing extra re-pushed — and the reviewer confirmed
  that reading. The unified validator itself stays
  **design-pending** — no `setAttribute`, `dataset` or `classList` vocabulary
  moved in this slice. G1, G3, P6 and G6 stay open.
- [~] Implemented and qualified on the native route, court 23 of 23:
  `cloneNode` (`labs/native-dom/clone-node-audit-0.0.1.md`), the last of the
  five page-only additions and the only one whose failure mode was silence.
  The audit found the good news first: this host already keeps an element's
  IDL state apart from its attributes — a typed `value` reads back typed while
  the attribute stays `orig` — so a copy built from attributes carries the
  right things by construction, and listeners are not carried because they
  live keyed by node in a closure-owned map. Ruled: shallow by default, pinned
  by a criterion; and a node kind the walk does not model **fails the whole
  call** with a typed error rather than being skipped, with text and element
  named as a closed set that a future kind may not join without updating the
  record and the court first. A detached copy moves no revision until the page
  appends it, which the court reads from outside. **4,080 bytes of main and
  nothing per child**: M1 unchanged at 224,458, main slack 38,240. The court
  is 23 of 23 and 19 of 23 against the build before it, and its page fixture
  now asserts a count of its own checks as well as their values — which is how
  I found my expected string was short by eight. G1, G3, P6 and G6 stay open.
- [~] Implemented and qualified on the native route, court 21 of 21:
  `toggleAttribute` (`labs/native-dom/toggle-attribute-audit-0.0.1.md`), the
  thinnest compatibility case of the series at **848 bytes of main and nothing
  per child**. Its audit found the sharper thing underneath: **this host
  validates no attribute name at all** — `setAttribute("a b", …)` and
  `setAttribute("", …)` are accepted where every browser throws
  `InvalidCharacterError`, next to a `classList` that does validate its tokens
  and throws with the standard's names. Ruled boundary B: the new method
  validates nothing either, because a method disagreeing with its neighbours
  about what a name is would be worse than both, and the divergence is
  recorded as its own base candidate beside the selector engine's error name.
  The revision follows the members it calls — once for a change, not at all
  for a no-op — and the court reads it from **outside** through
  `target.inspect`, because `window.__mcs` is installed after a document's
  inline scripts and a page cannot see the counter at parse time. The court is
  21 of 21 and 17 of 21 with `passed: false` against the build before it.
  G1, G3, P6 and G6 stay open.
- [~] Implemented and qualified on the native route, court 19 of 19:
  `getAttributeNames` (`labs/native-dom/get-attribute-names-audit-0.0.1.md`),
  a compatibility fix at **784 bytes of main and nothing per child**. The
  audit's larger finding was underneath it: `element.attributes` here is a
  fresh plain array of plain objects, not a live `NamedNodeMap` of `Attr`
  nodes — no `item`, no `getNamedItem`, no identity across reads, and a page
  holding one sees a list that never updates — and the trap in that shape is
  that `attributes.map(…)` works here and throws in a browser while
  `Array.from` works in both. That divergence is now in the README's losses on
  its own account, the second of its kind found by probing rather than
  reading. The method reads the element's own attribute map rather than the
  view, so it allocates nothing per attribute, and it returns a new array each
  call while claiming to be nothing more. Its criteria joined
  `element-view-court`, which is 19 of 19 and 17 of 19 with `passed: false`
  against the build before it. G1, G3, P6 and G6 stay open.
- [~] Implemented and qualified on the native route, court 16 of 16:
  host-driven focus (`labs/native-dom/active-element-audit-0.0.1.md`). The
  audit found focus absent everywhere — no `activeElement`, no events from
  `focus()`, no `tabIndex`, and a host click that changed nothing — and named
  the cheap fix as the one to avoid: tracking focus inside a page's own
  `focus()` calls would have made the host lie louder, because a page that
  clicks a field through the agent and reads `activeElement` would see
  whatever it last focused itself. Ruled narrow instead: **only a host-driven
  action moves focus**, the state is hidden in the base's closure, the
  extension reads it through a getter that cannot move it, and a page's
  `focus()` moves nothing and raises nothing — a divergence recorded rather
  than hidden. Three faults surfaced, two of them mine: my fixture clicked
  text inputs, which this host refuses, and reported only from click
  listeners, so a `set_value` criterion read a stale value; the host's was
  real — the button path ends in the DOM's own `click()`, so a hook in the
  dispatcher would have missed it while hooking `dispatchOn` would have let a
  page's synthetic click forge focus. The host asks explicitly through the
  bridge instead. This slice costs child realms, because the state must be
  base-side to be unforgeable: M1 221,514 to **224,458** against an unchanged
  floor, leaving 21,302 bytes of headroom, with main slack 31,872. The court is
  16 of 16 and **2 of 16** against the build before it. G1, G3, P6 and G6 stay
  open.
- [~] Implemented and qualified on the native route, court 19 of 19:
  `Element.closest` (`labs/native-dom/closest-audit-0.0.1.md`), taken as a
  **compatibility fix rather than a capability** and recorded on that ground.
  The audit measured what the door already answers: `matches` is present and
  handles tag, id, class, attribute and descendant selectors while throwing on
  child combinators, commas and pseudo-classes, and the four-line walk a page
  could write over it works today — the probe wrote it. What was missing is
  that a real page calls `closest` and its own script died there, leaving an
  agent reading a DOM no browser would have produced. It lives in the main
  extension, built from members the base keeps, refusing exactly what `matches`
  refuses, at **608 bytes of main and nothing per child**: M1 is unchanged at
  221,514. Its criteria joined `element-view-court` rather than earning a
  second file, and that court is 19 of 19 and 17 of 19 with `passed: false`
  against the build before it, where the fixture's own script dies on both
  allocators. The selector engine's error name — a plain `Error`, not a
  `SyntaxError` `DOMException` — is deferred as its own base candidate.
  G1, G3, P6 and G6 stay open.
- [~] Implemented and qualified on the native route, court 15 of 15: `dataset`
  built when a page reads it (`labs/native-dom/gap-triage-0.0.2.md`). A
  read-only triage of forty capabilities through the existing control door
  found the cost that mattered was not an absence at all: `dataset` was a
  `Proxy`, its handler and three closures allocated in **every `Element`
  constructor in every realm**, for an API no host script names. Measured
  directly with 16-node and 112-node child documents — after a figure of mine
  that was wrong by an order and is corrected in the record — a node costs
  about 2,082 bytes today of which `dataset` is about **832**. The lazy
  accessor moved to the main extension with the `kebab` helper that serves
  nothing else, so the one-shot handle did not widen and a child realm has no
  `dataset` at all. The marginal bytes per child node fall to 1,253 (system)
  and 1,202 (arena) against a frozen gate of 1,600; M1 is **221,514** with
  **24,246 bytes of headroom**, and a main-only page is *cheaper* than before,
  because a page now pays only on the elements it touches. The court is 15 of
  15 and 12 of 15 against the build before it, failing the per-element gate on
  both allocators. The triage also recorded that `querySelectorAll` answers a
  plain array rather than a live `NodeList`, and left the five page-only
  additions — `closest` first — as individual candidates rather than a bundle.
  G1, G3, P6 and G6 stay open.
- [~] Implemented and qualified on the native route, court 17 of 17: the
  `Element` member audit (`labs/native-dom/element-audit-design-0.0.1.md`).
  Fifty-six members of `Node`, `Text` and `Element` were matched mechanically
  against every host script, the extension and the base's own code: 24 are
  reached by child-capable host scripts, 13 by the base itself, 2 only by the
  extension, and 14 by nothing but page script. That also settled by
  measurement what the shim split left open — `snapshot_script` uses selectors
  in child realms, so the selector engine stays. Ten of the fourteen moved to
  the main extension, each the base's own implementation rather than a
  rewrite: M1 236,938 to **230,506** and M2 to 1,612,044 against unchanged
  floors, leaving **15,254 bytes of M1 headroom**, with a main-only page 32,032
  above the baseline inside its 65,536 slack. The other four stay in the base
  by ruling, because they are written in terms of mutation recording, the
  selector engine's internals and `__attrs`/`__detach`, and moving them would
  widen the one-shot handle into a second coupling for about 2.6 KB;
  `addEventListener` and `removeEventListener` stay with the dispatcher for
  the same kind of reason. The court re-derives the call-site inventory from
  the shipped sources so the audit cannot go stale in silence — and it caught
  its own first version reading the Rust around a script and reporting a host
  call to `.remove` that was `self.entries.remove(0)`. A follow-up dependency
  audit then priced the four remaining page-only members: `contains` is 400
  bytes and needs nothing, because what the base uses is the free helper for
  observer scope while the method is one `parentNode` walk — a correction to
  what that record first claimed — and the other three would cost four handle
  identifiers for about 3,952 bytes, one of them `record`, which is how a
  mutation moves the revision that gates every action. Ruled and built:
  `contains` moves and calls the base's own helper through the handle, so the
  walk exists once; the other three stay in the base, scope-closed, and the
  handle does not widen. M1 is **230,106** with **15,654 bytes of headroom**
  under an unchanged floor. That court is 19 of 19, and against the build
  before it reads 19 of 19 as well, which separates nothing. That run is kept
  as `-element-view-audit-comparison` with `passed: null` and
  `verdict: non_discriminating` rather than as a falsification receipt, so no
  reader or script can take a passing run for a falsified one; **this round has
  no falsification receipt** and the record says why. G1, G3, P6 and G6
  stay open.
  The `Event` slice left M1 with 2,630 bytes under its floor, so the next
  base change did not fit. A measured base-reduction round
  (`labs/native-dom/base-reduction-design-0.0.1.md`) priced the problem: a
  member of a shared prototype costs **600 to 960 bytes of M1 per child**, ten
  times what the same source weighs as bulk text, so base growth is budgeted
  per member from now on and the shim split's 3.4 ratio stays only as
  historical context. Candidate A was ruled and built: the ten page-facing
  `Event` accessors moved to the main extension, where the only realm that can
  read them lives, leaving `defaultPrevented` in the base solely because
  `Element.reset` reads it. M1 is **236,938** and M2 1,657,068, so the headroom
  under the unchanged floors went from 2,630 to **8,822 bytes**, and a
  main-only page costs 33,744 above the `origin/main` baseline, inside its
  65,536 slack. The court holds the whole trade rather than the saving — a
  main realm keeps the view and its values, a child still answers a snapshot
  built with selectors, still applies a host action through the capability
  bridge and still runs the DOM's own reset — and it is 11 of 11, failing on
  the build before it exactly where every child realm still carried the ten
  accessors it can never read. A second candidate was closed by measurement
  instead of caution: `snapshot_script` uses selectors in child realms, so the
  selector engine must stay in the base. The next slice is an `Element` member
  audit, design and measurement first.

  A further audit found the privileged path was built from page-mutable tools:
  the `WeakMap` and `Map` prototype methods the hidden state and the listener
  store use, the array iterator the dispatch walked with, `.call` read off a
  page-owned function, and the global `String` and `JSON` the host reads its
  answers through. Measured on the build before the fix, from inside a click
  handler while the host's own dispatch was in flight: a patched `WeakMap`
  reached the hidden state and forced `applied: false`, a patched `Map` hid the
  ancestor's listener from the walk, a replaced array iterator did the same to
  the path, and a replaced `JSON.stringify` made the host report an action
  result the page had written. The privileged path now captures its intrinsics
  before any page script and walks everything by index; the host reads realm
  answers and serialises action results through captured intrinsics, and
  nothing else was hardened, because a typed failure outside the action path is
  an acceptable outcome and a fabricated result is not. Two corrections of mine
  came first: a mechanical replacement that turned `toString(16)` into
  `to__mcsString(16)` and would have broken GET form submission, and criteria
  whose attacks ran at load time, where they broke the page's own build and
  could not tell two builds apart. The court is **62 of 62** and 56 of 62
  against the build before it. **M1 is 243,130 against the 245,760 floor —
  2,630 bytes of headroom**, so this slice has spent nearly all of the shim
  split's margin.

  A final root audit then found the authority claim was not closed at all:
  hidden state stopped assignment, while the host's action scripts still
  constructed through the global `Event`, dispatched through an element's own
  `dispatchEvent` and read a public property — so a page could shadow the
  property on the event it was handed, redefine the prototype getter, replace
  the class, or replace `dispatchEvent` and never run the real listener model.
  Measured on the build before the fix: the first two cancelled a host action
  outright, the third broke it, and the fourth let the host report an applied
  action whose handler never ran. Every host action path that decides
  `applied`, `default_prevented`, a navigation, a reset or a submit now goes
  through one capability-guarded bridge that mints the base's own `Event`,
  walks the closure-owned dispatcher and answers from hidden state, armed in
  every realm including children before any page script runs, with a typed
  refusal and no fallback. The court is **54 of 54** and 46 of 54 against the
  build before the bridge; M1 is 236,074 and M2 1,650,236, still under the
  unchanged floors.

  The navigation court's differential soak failed repeatedly on
  `a91bdf2c85b7…` — 88, 90 and 89 of 90 — and I stopped on it rather than
  moving anything. A pre-registered read-only attribution then compared round
  one and round two: owner growth across 128 navigations is identical field
  for field on both builds and both allocators, `realm_malloc_bytes` grows by
  zero, every owner is zero after close, and the divergence appears during the
  candidate build and is released at the swap. So `Event` is qualified under
  its own unchanged floors while navigation stays the cross-batch,
  default-allocator narrow it already was — now with a measured reason rather
  than a shrug. **No cap was moved and navigation was not rerun.** The
  attribution could not report live `Event` or listener owner bytes, because
  no such owner exists, and did not sample libmalloc allocated or resident or
  the arena counters; both gaps are named in the design record rather than
  filled. G1, G3, P6 and G6 stay open.
- [~] Implemented and qualified on the native route, court 28 of 28:
  `classList` and `CustomEvent` (`labs/native-dom/element-api-design-0.0.1.md`),
  the last of the three candidates the browser-gap triage proposed. Probed on
  the previous build, the class attribute was fully there — `className`
  reflected it and the selector engine matched it — and `classList`,
  `CustomEvent` and `DOMTokenList` were all undefined, so a page that toggles
  a class to express state threw. Both now live in the main extension, which
  is what the shim split made cheap: a script-free child realm has neither,
  proven through the court-only realm probe rather than asserted, and M1 is
  unchanged at 221,657 because a child compiles none of it. The list holds no
  tokens; the attribute is the state. One divergence is recorded rather than
  hidden: a call that changes nothing writes nothing and does not advance the
  revision, because the revision gates a caller's action and a spurious one
  costs a re-snapshot. Two of my own criteria measured the wrong turn and were
  corrected first — both read the revision through the observation that was
  itself the timer boundary, and the no-op page wrote its result in the same
  turn as its no-op calls, so it passed while seeing nothing. The court is 18
  of 18 and 2 of 18 against the build before it. A root audit then found four
  things in it, each recorded with its falsifier before the fix: the list
  captured the attribute once, so a held list answered about a stale
  attribute and a mutation through it dropped what had been written directly;
  it was a new object on every read where the standard says `SameObject`; its
  `value` was a getter over the normalized token set rather than the
  standard's raw getter and setter; and an explicit `null` dictionary crashed
  `CustomEvent`. The list is now a live view that reparses on every call and
  the same `WeakMap`-backed object, holding no tokens and dying with the
  element. One frozen criterion of mine went with them: it asserted the
  normalized `value`, freezing a divergence I had never declared. The court is
  28 of 28 and 16 of 28 against the build the audit judged, where it fails
  every criterion the audit added and no other; M1 is unchanged at 221,657 and
  a main-only page costs 286,488 against 273,512. This slice and the shim split
  were designed, ruled and qualified by me while the root was unavailable, and
  every ruling is marked as mine in the records for review. G1, G3, P6 and G6
  stay open.
- [~] Implemented and qualified on the native route, court 18 of 18 against
  the exact `origin/main` baseline: the per-realm shim split
  (`labs/native-dom/shim-split-design-0.0.1.md`). Every realm compiled the
  whole 29,930-byte shim, and 11,239 bytes of it was page surface a
  script-free child can never reach, which is why M1 had 182 bytes of
  headroom and the browser-API work was blocked. The base every realm
  compiles is now the tree, its events, the selector engine and the seed; the
  page surface a script needs — fetch, cookies and `localStorage`, the
  location accessors and the intent slot, the window as an event target with
  the lifecycle bridge, `queueMicrotask`, timers, `console` and `navigator` —
  is a main-only extension that reaches the base through a one-shot
  non-enumerable handle deleted as it hands its internals over. A child realm
  gets no extension and is sealed by the host, which refuses the realm if the
  handle survives; a court-only probe, refused before the host serves without
  the private court file, proves it is present and enumerable nowhere. Two
  candidates were rejected on measurement rather than taste: a shared runtime
  would dissolve the per-realm allocator accounting these caps are written in,
  and precompiled bytecode would not move M1 at all because bytecode still
  deserializes per runtime. Writing the court found two faults in the court —
  it restated caps proven on another court's fixtures, and its first
  footprint criterion reported a 131,072-byte recovery when run against the
  same binary twice — and the implementation's first build failed
  page-navigation at 38 of 80 because the moved location block still carried
  a parameter the host no longer passes. Measured against the `origin/main`
  binary in one run: M1 261,961 to 221,657, M2 1,831,451 to 1,549,323, a
  main-only page 288 bytes cheaper, 28 child realms about 1.0 MB (system) and
  2.6 MB (arena) smaller in process footprint, binary +1,472 bytes,
  incremental build 5.46 s to 6.12 s. **M1 has 40,486 bytes of headroom**,
  which is a floor to hold rather than a budget to spend. G1, G3, P6 and G6
  stay open.
- [~] Implemented and qualified on the native route, court 53 of 53: the
  bounded document lifecycle (`labs/native-dom/lifecycle-design-0.0.1.md`).
  Four observable steps after the document's own scripts, each its own
  evaluation so a handler's jobs drain before the next: interactive with a
  `readystatechange`, `DOMContentLoaded` at the document which bubbles to the
  window, complete with another `readystatechange`, and `load` at the window
  which does not. The window becomes a bounded event target sharing one
  listener model with every node, held in a closure a page cannot reach, with
  `onload` as one accessor over it. Six corrections were recorded in order,
  each before the change it justified: `DOMContentLoaded` bubbles, which an
  earlier ruling and my own text had frozen as a loss; the cost is a fixed
  infrastructure plus page-owned listeners bounded only by the realm limit,
  not one "small and fixed" number; the infrastructure figure is a diagnostic
  because two arms cannot isolate it; four criteria could not prove what they
  claimed, including a duplicate-listener check that never registered a
  duplicate and a not-inert check that passed on the old host; the bridge was
  a forgeable global, now a non-writable property behind a per-realm
  capability with a phase machine, falsified by a court-only interleaved
  replay; and the event path reached the window from a detached subtree. A
  final ruling reversed a divergence rather than freezing it: a duplicate
  listener is de-duplicated, which the pushed build falsifies at three calls
  against one. The court passes 53 of 53 and fails 43 of 53 against the
  pushed build. G1, G3, P6 and G6 stay open.
- [ ] Triage only, read-only, nothing implemented and nothing measured
  beyond one probe: standard-browser gap triage
  (`labs/native-dom/browser-gap-triage-0.0.1.md`). Three candidates, each
  measured against the current build rather than inferred: the document
  lifecycle, where `DOMContentLoaded` reaches no listener and
  `window.addEventListener` does not exist, so a page that builds itself on
  load stays as the server sent it; page-initiated navigation, where
  `location.href = "…"` succeeds silently and the page believes it navigated
  while the host committed nothing, which is a silent approximation rather
  than an honest absence; and `classList`, absent while the snapshot's
  selector engine already matches `.x`, so this host can query a class a page
  cannot idiomatically change. The lifecycle was recommended first: most
  real-web behaviour per line, no authority, no protocol change, and it composes with the timer and job bounds already landed rather
  than needing its own. **Superseded by the implemented node above:** this
  triage said the lifecycle carried "no resident memory", and the slice as
  built carries a fixed small per-realm infrastructure plus page-owned
  listeners bounded only by the realm limit and the request deadline.
  Page-initiated navigation is second and needs a re-entrancy ruling first,
  because a page may assign during its own build.
  G1, G3, P6 and G6 stay open.
- [~] Implemented and qualified on the native route, court 42 of 42:
  closing the pending-job deadline escape
  (`labs/native-dom/job-deadline-design-0.0.1.md`). The mechanism is
  established rather than guessed: `eval_staged` removes the interrupt
  handler **before** it drains the queued jobs, and `drain_jobs` checks the
  deadline only between jobs, so a single job that never returns runs forever
  with nothing to stop it. The engine is not the problem — a scratch build
  that moved the uninstall to after the drain interrupted the same page's
  runaway job at exactly its deadline and answered the next request normally,
  where the shipped host hangs indefinitely, so `JS_ExecutePendingJob` does
  honour the runtime interrupt and no API constraint stands in the way. That
  probe exposed a second defect: with the interrupt restored, the drain
  swallowed the interrupted job's error and the operation answered `ok`, so
  the caller was told a document built normally while the page's own code had
  been cut off mid-run. The design keeps the handler installed across the
  drain, distinguishes the drain's three outcomes, fails the operation with
  `deadline_exceeded` only when a job was interrupted, counts a job that
  merely threw and continues, and adds no count bound because the request's
  deadline is the bound and a count would cut legitimate chains. Promise and
  queueMicrotask stay, ordering stays, no background thread and no virtual
  time. The frozen court supervises every host it starts, killing and reaping
  one that misses a wall-clock limit and recording that timeout as the
  falsification rather than waiting. The root ruled all three and it is built:
  an interrupted drain fails with a retryable `deadline_exceeded`, there is no
  job-count cap, and a job that raises is page-owned. Four findings are
  recorded in order, each before its fix: the handler-hang fixture used a
  fragment link the frame-action rules refuse before dispatch, so the handler
  never ran; an interrupted build reported `target_crashed` because
  `build_target` re-types every script error; a job's exception is **not**
  observable through this engine's drain, measured across a throwing `then`, a
  throwing `queueMicrotask` and an unhandled rejection, so no counter claims
  one; and the counters lived on the realm, which loses them wherever a realm
  is replaced or never committed, so they moved to one host-owned sink every
  realm shares. The court passes 42 of 42 and fails 12 of 42 against the
  pushed build, killing and reaping eight hosts there by exact pid. G1, G3,
  P6 and G6 stay open.
- [~] Implemented and fully qualified on the native route, court 68 of 68
  against every frozen group and criterion: a bounded timer slice
  (`labs/native-dom/timer-design-0.0.1.md`). The audit's finding is that the
  current shim is worse than an absence: `setTimeout` discards its delay and
  runs the callback at the next job drain, `clearTimeout` cannot cancel
  anything, and every handle is `0`, so a page that debounces runs
  immediately and a page that cancels runs what it canceled — none of it
  recorded as a loss. The design proposes `setTimeout` and `clearTimeout`
  only, in the main frame only, with `setInterval`, animation frames, idle
  callbacks, workers, background threads, child-frame timers and any
  realm-readable clock all refused rather than approximated. Timers are owned
  by a realm and destroyed with it, so navigation, reload, traverse and close
  need no separate teardown; handles are per-realm monotonic integers; due
  callbacks run only at operation boundaries, bounded at 32 per boundary and
  64 pending per realm, ordered by due time then handle; `target.wait` sleeps
  to the next due timer instead of a fixed interval. A due callback's
  mutations move the existing global revision through the same checked
  helper, and two consequences are recorded rather than left implicit: the
  child-counter cache proof holds only because children stay script-free, and
  the activation preflight's signature comparison stops being theoretical
  because a due timer is a second way for a document to change between the
  two phases. A throwing callback is counted and discarded without crashing
  the target, a callback past the deadline answers `deadline_exceeded`, and
  `memory.report` gains a timer owner of integers only. A fourteen-group
  hermetic headless court and five pre-registered memory and latency criteria
  are frozen in the record. Five decisions wait on the root, including
  whether to implement at all rather than refuse honestly, and the hazard
  that observation boundaries can now change the document a snapshot is about
  to report. **The root ruled all five and it is built.** Before the code the
  frozen court was run against the pushed build and failed 13 of its 16 checks
  per arm, the three named ones among them, so it describes a defect rather
  than an absence. Five implementation audit points were then answered in the
  record and the code: the clock is read after the collecting turn so a delay
  is never shorter than asked, the reported owner limit is per timer-owning
  realm, a failed collect is attributed instead of silently losing a schedule,
  retirement is counted at every realm replacement, and the handle boundary is
  frozen exclusive with the alternative recorded. Seven attribution counters
  replace one bucket. Not qualified here: the timer CDP group, because the
  pinned client is absent from the ignored lab directory in this working copy,
  which was then restored offline from the local npm cache, verified against
  the committed qualification, and the four affected courts rerun in full.
  A second audit found the first court was a subset of what was frozen — the
  wait, deadline and CDP groups and the T1 to T5 criteria were missing — and
  five more defects behind it: a deadline discarded the due timers queued
  behind the callback that hit it, a clear from inside a running callback went
  uncounted, the build path ran a second unvalidated bridge, a malformed entry
  inside a well-formed list was skipped, and the bridge was a page-replaceable
  global. All are fixed, the frozen groups and criteria are implemented, and
  the design corrects a false claim of its own: the realm has `Date` and
  `performance`, which are shipped behaviour this slice neither uses nor
  touches, recorded as a separate gap. A hang the extended court caused on the
  pushed build is recorded with it, along with the shipped job-drain deadline
  gap it exposed, which this slice narrows by construction and does not close.
  The last frozen group, the CDP one, is closed with the restored pinned
  client, and it corrected an assumption of the record's own: `Runtime.evaluate`
  and `Emulation.setVirtualTimePolicy` are absent methods and answer `-32601`,
  while `Runtime.callFunctionOn` exists and is qualified for exactly one
  declaration, so a timer declaration is refused as an unaccepted parameter
  rather than a missing method. Both mappings record that split, and no source
  text is parsed to choose a code. No protocol
  shape moves beyond the additive `target.inspect` timers field. G1, G3, P6 and
  G6 stay open.
- [~] Implemented and qualified on the native route: frame-aware actions and
  child-local navigation
  (`labs/native-dom/frame-action-design-0.0.1.md`). The record compared two
  models and the root ruled model A: one target-global observable revision,
  the node band naming the frame, and no protocol expansion — no schema,
  mapping, example, request or result shape moved. A live same-origin
  script-free child is now actionable; an action is served only when that
  frame's own last observation authorises it; a link or a GET submit inside a
  child replaces that child's document out of the parent document's remaining
  aggregate allowance, keeping the parent's identity and the target's history;
  activations this host does not model fail closed before any event with a
  closed-vocabulary `activation` fact that predicts them; a sandboxed iframe is
  no longer built; and every action record names its frame. Five audit
  blockers were found against the first implementation and each was recorded
  before its fix: the saturation rule was specified and never implemented, and
  the first limit turned out to be the realm's Number at 2^53−1 rather than
  `u64`; the two surface paths computed a second revision that omitted the
  cached child counters; an explicitly empty `target` was conflated with an
  absent one under a `<base target>`; the effective action was never
  preflighted, so a scheme, a bound or a cross-origin child action was judged
  only after the page's handlers had run; and the preflight had a
  time-of-check gap, because a queued job can rewrite a control between two
  host evaluations without moving the revision, which is now closed by
  re-deriving and comparing the whole effective activation before dispatch.
  Two later findings were fixed the same way: both stale paths reported one
  frame's counter as the target's revision, and a scroll changed the offset
  and advanced the realm's counter before checking either limit. The court
  passes 182 of 182 under both allocators including three revision-boundary
  groups; against the shipped host it fails 155 of 173. A submit that
  navigates was also found to advance the revision twice for one observable
  consequence and now advances once. This is qualification of a bounded typed
  surface, not a gate: G1, G3, P6 and G6 stay open, the navigation route stays
  cross-batch narrow on the default allocator, and profile stays 90 of 94.
- [~] Implemented and measured on the native route: bounded child frames
  (`labs/native-dom/child-frame-design-0.0.1.md`). The audit found the frame
  contract already written and already executable on the synthetic host, so
  the increment added no operation and no request or result shape: a
  same-origin `<iframe src>` becomes a child frame with its own id,
  generation and realm, built with its parent under the parent's own budget,
  at most seven, depth one, script-free, enumerated by `target.inspect` and
  observed through the `frame`/`realm` narrowing `target.snapshot` already
  had. Node ids became target-scoped so a reference taken in a child cannot
  resolve against the main frame, and acting there is refused typed: that
  hazard was found by auditing the frozen design against the pre-implementation
  host, before any code. A child costs 247,000 live owner bytes, seven cost
  1,726,710 with no super-linear term, 64 parent navigations return to the
  one-child level and retire exactly two realms each, and open-and-close
  returns every owner byte while retaining 32 KB more than the identical
  childless arm. The root then ruled: no actions in a child, the two
  questions that keep it refused recorded for a later design (what a child's
  own revision means to `target.wait`, and whether a navigation inside a
  child replaces the child or the target); every frame stores and reports the
  final URL of the response that built it as an optional additive
  `frames[]` field, so CDP projects a child's own address; and the pinned
  navigation result keeps its field set. Its review also found five blockers,
  each fixed: same-origin now holds after the redirects as well as before,
  only `text/html` is parsed, a refused child rolls back the cookies its
  attempt set, a child that cannot be built is skipped rather than failing
  its parent, and a refused frame is no longer reported as a refused script
  but as a bounded tally over a closed set of fixed reasons that never
  carries a URL. The court passes 80 of 80 under both allocators including
  its CDP group and a court-only forced construction failure; frame-realm and
  the CDP court were amended where they encoded the old one-frame limitation
  and still pass. Losses: no acting in a child, no child navigation, no
  nesting, no cross-origin or `srcdoc` children, no scripts in a child. G1,
  G3, P6 and G6 stay open.
- [~] Implemented and measured on the native route: the agent-native form
  interaction slice (`labs/native-dom/form-interaction-design-0.0.1.md`). The
  realm-shim gained exactly the enumerated model, checked with radio grouping,
  selection and options, disabled and read-only reflection, form association
  and submit, and the realm stays the only authority: the host keeps no form
  state. The snapshot names checkbox, radio, select and form with bounded
  facts and excludes credential and file sources. `target.act` takes the five
  0.0.2 actions beside the unchanged click, with every refusal before any
  mutation, one revision per successful action, a GET-only submit that
  serialises inside the realm and navigates atomically, and an audit that
  records the kind, the outcome and a value's byte length and never a value.
  Two root audits then found nine defects the first court did not look for,
  each recorded in the design's sections 13 and 14 before any code moved: a
  press that claimed to apply what it had not, a byte length counted in the
  wrong units, a built query inside an error, an impossible rollback claim,
  events dispatched in the wrong order, a canceled key that activated
  anyway, four button subtypes treated as one with the submit skipping its
  click, a radio that toggled off and a canceled change that left its
  sibling cleared, and a reset that mutated before it asked. All are fixed
  and the court, extended to them, passes 179 of 179 under both allocators
  including its memory criteria, the plateau over 128 edit and reset cycles
  among them, and no typed value, option label or query appears in the
  ledger, the court log or the receipt. The pre-fix host was rebuilt and run
  against the extended court to show the new checks bite. A ruling then
  moved the declared press model one step toward a browser, recorded as
  section 15 before the code: a canceled keydown suppresses keypress, keyup
  arrives in every case, activation waits for the whole sequence, and the
  court now observes the order rather than inferring it, with the section
  still saying plainly that this is no hardware timing model. `control-0.0.1`
  request and result shapes are unchanged byte for byte; the behaviour
  behind a click on a reset control is corrected, recorded as a bug fix.
  The slice adds no CDP surface, recorded as a loss and proven with the
  pinned client. G1, G3, P6 and G6 stay open.
- [ ] Superseded description of the same node, kept for the record: proposed,
  design only, nothing implemented and nothing measured: an
  agent-native form interaction slice
  (`labs/native-dom/form-interaction-design-0.0.1.md`). `target.act` offers
  exactly one action today, a click on a link, a button or a button-like
  input, and the semantic snapshot cannot even name a checkbox, a radio, a
  select or a form. The design proposes five typed action shapes beside the
  existing click, in `0.0.2` alone, with `0.0.1` unchanged byte for byte: set
  value, set checked, choose option, submit and press, each a closed shape
  with bounded arguments and no script. It states the exact event order for
  each, refuses disabled and read-only controls typed, excludes constraint
  validation, input methods, contenteditable, files, autofill, password
  managers, arbitrary keys and POST, keeps the node revision and stale rules,
  and forbids the audit ledger from ever recording a value. It adds no CDP
  surface and records that as a deliberate loss rather than inventing an
  adapter-side form model. Four decisions wait on the root, the first being
  that the slice needs the document shim and the snapshot's roles extended
  before any action vocabulary can mean anything. The memory court is
  pre-registered as a reported differential rather than a gated cap, because
  the navigation increment showed that instrument counts page-granular
  allocator retention that moved 114 KB between builds. G1, G3, P6 and G6
  stay open.
- [~] Implemented and measured, one pre-registered cap still failing: the
  agent-native navigation slice (`labs/native-dom/navigation-design-0.0.1.md`).
  `control-0.0.2` is a separate schema served beside an unchanged `0.0.1`,
  carrying `target.navigate`, `target.reload` and `target.traverse`; history
  is metadata only, eight committed URLs and a position, so going back
  refetches and restores no page state. The native host implements all three
  by reusing its atomic same-frame navigation, `session.inspect` reports
  advisory discovery and a bounded 64-record audit ledger, `profile.policy.set`
  is implemented with the network switch enforced before any name or socket,
  and the CDP adapter maps navigate and reload while both history methods stay
  `-32601` because the host is the only history authority. The fetch and byte
  limits are scoped to one document by ruling, with saturating lifetime
  diagnostics that never gate. The court reaches 89 of 90 with nothing
  unverified; the one failure is the 128-navigation differential soak under
  the default allocator, 1,064,960 bytes against the frozen 1,048,576, after a
  ledger representation repair had brought it to 983,040. One replication on
  the same build measured exactly the cap with two of seven runs above it, so
  the batches disagree rather than agreeing on a pass. One further candidate,
  sharing the immutable allowlist instead of copying it per operation, was
  frozen, implemented, measured, and failed every acceptance threshold; it was
  recorded rejected and reverted, since its allocation proof was true but it
  bought no measured benefit. Verdict on the soak: **narrow on the default
  allocator**, with the arena inside every budget and every other check green.
  No cap was moved at any point. G1, G3, P6 and G6 stay open.
- [ ] Superseded description of the same node, kept for the record: proposed,
  nothing implemented and nothing measured
  (`labs/native-dom/navigation-design-0.0.1.md`).
  The control operation enum of `0.0.1` is closed and stays closed, so the
  design proposes `target.navigate`, `target.reload` and `target.history`
  under a `control-0.0.2` version bump with discovery through the existing
  `session.inspect`, rather than a negotiation handshake or an overloaded
  `target.open`; history is proposed as metadata only, capped at eight bounded
  entries, so going back refetches instead of restoring a cached document, and
  that loss is recorded rather than emulated. Identity follows the existing
  rule (same target and frame, new generation and realm, revision advances,
  prior references stale), a failed navigation rolls back atomically, and the
  profile's cookies, storage, network policy and pinned-root TLS are reused
  with no new authority. Memory budgets are pre-registered, with the
  128-navigation soak defined as the difference between a navigating and a
  non-navigating arm of identical request count, because the control-churn
  court showed every request grows the host without a plateau. The multi-route
  matrix expects the native route to serve it, Lightpanda to need
  qualification, and Servo to fail the soak on the pinned release. Two
  decisions wait on the root: how the enum grows, and whether metadata-only
  history is acceptable. G1, G3, P6 and G6 stay open.
- [ ] Suggestion for the root's review, not a gate change: read G3 as
  "can the same live target be attached to a real presentation and
  detached again with its state kept", with the presentation-specific
  reclamation shown by paired causal evidence (a real surface arm against
  a headless replaying counterfactual with an identical operation and
  input sequence, `surface-paired-causal-court-0.0.1.md`, design only),
  while the generic per-request control-plane churn measured by the
  churn court stays under G1 / M2 and is not called a surface leak. The
  frozen court's absolute S2 and S3 keep failing and stay quoted; whether
  this reading is adopted is the root's decision. The realm-side snapshot
  memo is a rejected idea. The counterfactual child mode and the harness
  exist and arm B has run headless (7 of 7 valid, both allocators); arm
  A waits for the owner's permission and no differential exists.
- [~] Control-plane churn attribution (headless, read-only, 128 requests
  per arm, fresh host, one warm-up plus seven, both allocators): every
  operation grows the host by 0.1 to 1.5 KB per request with no plateau
  by 128, born in the realm evals of the dispatch and in the response
  serialization; in-use returns each time, so the growth is freed
  small-block pages the default zone keeps (arena arms grow more, not
  less); the surface's path is one contributor among snapshot, memory
  report and the court's own inspect calls, so no single-operation host
  change can pass the frozen S2 and S3. Authority closed: the realm's
  shim DOM is the only document state after open; a host-native
  traversal would be a second authority. One pre-registered candidate
  for ruling, not implemented: a realm-side snapshot memo keyed by the
  revision (partial reduction expected, not a pass). G1, G3, P6 and G6
  stay open.
- [~] Headless by default (owner rule after windows flashed from automated
  attribution runs): every court, regression and default command is
  strictly headless; a real window needs `--visual` plus
  `MINICON_SURF_ALLOW_VISIBLE_COURT=1` (host flag, child environment gate,
  court flag), never steals focus, runs once by hand. Host refuses
  `surface.show` without the opt-in, the child exits before AppKit, the
  visual surface court reports `unverified` and writes nothing; the
  attribution courts run no-AppKit cells by default.
  `surface-headless-court.py` proves it (17/17: no window at any 50 ms
  sample, no AppKit or CoreGraphics mapped in the child, window list
  unchanged, kill and SIGINT leave nothing). The read-only snapshot and
  serde attribution (a visual run recorded before the rule, kept, not
  rerun) found the host side after the realm costs 0 footprint, drops and
  GC release nothing, the realm eval and the control plane's per-request
  churn are what remain; no candidate proposed yet. G1, G3, P6 and G6
  stay open.
- [~] The frame-region candidate was approved, frozen with its criteria
  before the code (`surface-frame-region-0.0.1.md`), implemented (one
  anonymous `mmap` region per surface record, painted in place, borrowed
  by the pipe write, unmapped exactly once on every path, reported under
  `owners.surfaces.frame`) and measured against the unchanged court: the
  frame's pages return exactly at drop and the one-or-two-copies variance
  is gone in two court runs and seven attribution runs, but the residual
  small-block churn (0.2 to 0.5 MB after three rounds, mostly the script
  realm's snapshot evaluation and control-plane JSON) still fails S2 from
  round 2 and S3 under both allocators. Rejected for G3 under the
  pre-registered outcomes; no cap moves; the region stays as the frame's
  backing; next is the read-only attribution of that churn before a
  second candidate. The surface court stays 106 of 110, narrow; G1, G3,
  P6 and G6 stay open.
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
