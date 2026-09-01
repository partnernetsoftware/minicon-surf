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
│   ├── independent MiniCon-family product; MiniCon installation not required
│   ├── AgenTerm consumes a versioned contract, never CLI prose or UI internals
│   ├── separate repository, binary, profiles, versioning and release cadence
│   └── [-] no browser engine linked into the MiniCon terminal executable
├── [M2] accountable memory model
│   ├── comparative baselines: named Chrome/Brave/system-WebView versions and modes
│   ├── workloads: empty host · first target · representative pages · per-target delta
│   ├── owners: DOM · JS heap · network · decoded images · fonts · render · storage
│   ├── measures: complete process tree · live · retained · resident/private · peak
│   ├── lifecycle: steady · post-close reuse · navigation soak · profile/target growth
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
│   └── local authority and authentication are explicit before remote exposure
├── [D4] CDP compatibility adapter
│   ├── ↳ [A3] maps onto the same profile/session/target authority
│   ├── discovery endpoint · WebSocket transport · attach/detach lifecycle
│   ├── first candidate domains: Target · Page · Runtime · DOM · Network · Input
│   ├── domain/method/version matrix names exact supported behavior
│   ├── tool journeys qualify selected Playwright/Puppeteer clients
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
│   ├── later: readonly and copy-on-write task profiles with explicit commit/discard
│   └── corrupt, locked or incompatible profiles fail closed without harming others
├── [E7] bounded engine experiments
│   ├── candidates declare total dependency/process cost and security-update owner
│   ├── independent labs/{techName} use the same workloads and receipt schema
│   ├── [~] Lightpanda 0.4.0: W1/W2 + same-CDP-live-target Chrome court observed
│   ├── [~] Servo 0.5.0: public API + software-rendered W1 runtime observed
│   ├── native bounded route measures HTML/DOM/layout/JS/Web API cost incrementally
│   ├── compatibility route may evaluate a system engine without hiding its memory
│   ├── JS candidates require heap/time/task/capability limits and teardown evidence
│   ├── representative journeys choose Web APIs; specification breadth alone does not
│   ├── a compatibility-only route is labelled and cannot set the product memory claim
│   └── default route survives only if it satisfies ↳ [N0] ↳ [H5] ↳ [P6]
├── [G8] 0.0.x decision gates
│   ├── [x] G0 terminology: versioned vocabulary/schema/mappings share one meaning
│   ├── G1 memory court can attribute and cap a synthetic target
│   ├── [x] G2 synthetic target is controlled interchangeably by CLI and a named CDP client
│   ├── G3 a live stateful page crosses headless → headed → headless without reload
│   ├── G4 two profiles prove storage and policy isolation under one host
│   ├── G5 route decision records measured wins, costs, gaps and rejected routes
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
        PP["persistent profile<br/>named · locked · budgeted"]
        EP["ephemeral profile<br/>isolated · discardable"]
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
        LIMIT["budget judge<br/>process · profile · target · resource"]
        BASE["comparative baseline<br/>same workload · machine · mode"]
        PRESS{"bounded AND materially<br/>below named baseline?"}
    end

    subgraph LAB["Engine Lab [E7]"]
        LP["Lightpanda 0.4.0<br/>W1/W2 · CDP action observed<br/>Chrome W1 court incomplete"]
        SERVO["Servo 0.5.0<br/>software W1 rendered<br/>comparison · Agent edge open"]
        NATIVE["bounded native route<br/>measured feature slices"]
        COMPAT["compatibility route<br/>total process cost visible"]
        DECIDE["G5 route verdict<br/>keep · narrow · combine · reject"]
    end

    SYN["synthetic control court<br/>one shared ControlState<br/>native + CDP + bounded surface mechanics"]

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
    PAGE --> BOOK --> LIMIT --> PRESS
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
  oversized-line drain prevent unbounded request/state growth. Eight library,
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
- [~] The synthetic surface mechanics court holds one CDP attachment while
  native stdio performs three headless → headed → headless cycles. Target,
  native session, realm, clicked DOM, revision 2 and scroll position 240 remain
  unchanged. Each show creates one bounded 65,536-byte presentation owner;
  each hide removes it and returns logical accounting to the live baseline.
  This does not pass G3: the attachment is a synthetic buffer, not a native
  window/rendering context, and therefore cannot prove GUI resource teardown.
- [~] The expanded synthetic lifecycle court separates empty, live, headed,
  post-hide and post-close steady windows with a 300 ms sampler warmup; every
  measured setup completed within 3.132 ms. Across seven runs per state, median
  complete-tree RSS was 1,900,544, 1,933,312, 1,949,696, 1,966,080 and
  1,933,312 bytes. Headed was +16 KiB versus live, but post-hide retained
  +32 KiB versus live even though logical surface ownership returned to zero.
  Logical bytes were 0, 462, 66,079, 462 and 107. This exposes a real retained-
  RSS gap and still does not pass G1: modes are separate fresh processes,
  maximum-capacity RSS and private/PSS are absent, and the synthetic state has
  no meaningful external browser-efficiency baseline.
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
  retention, soak and marginal target cost are open. The 800 locked packages
  and about 1.5 GiB cold build state remain integration-cost facts, not RSS.
  Public `show`/`hide` still does not prove live rendering-context detach,
  profiles are not MiniCon Surf profile objects, and Servo devtools must not be
  called CDP.
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

## 6. First sequencing

1. Write the vocabulary and protocol sketch for profile, browser session,
   target, frame, execution realm, surface, node reference and revision.
2. Build the memory-court harness before selecting data structures or embedding
   an engine; fix workloads, named baselines and OS measurement semantics.
3. [x] Prove one in-memory synthetic target through native CLI and CDP transport.
4. [~] Prove surface attachment/detachment against that target without giving
   the surface ownership of page lifetime; synthetic mechanics pass, native
   presentation resources remain open.
5. Prove persistent and ephemeral profile isolation with a deliberately small
   storage model.
6. Run independent `labs/{techName}` spikes behind the established contracts,
   publish comparable memory and Agent-control evidence, and issue an explicit
   keep/narrow/combine/reject verdict for every route.

The first code milestone is therefore not “render a website.” It is “one
bounded target has one identity and state while CLI, CDP, and an optional
window observe and control it without changing its lifetime.”
