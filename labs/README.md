# MiniCon Surf labs

Labs are competing technical experiments, not product implementations. Every
route answers the same two independent questions:

1. Can it produce materially lower complete-process-tree memory on a named
   workload than a named browser/system-WebView baseline?
2. Can it expose native semantic Agent control, including stable target state,
   waits and a qualified CDP projection?

Passing only one question can earn a narrow role, never the default engine.
Shared workload and receipt definitions live in [`court/`](court/). Technology
directories own their dependencies, downloaded-artifact instructions and
verdicts. They must not redefine court semantics locally.

Planned first landscape:

| Lab | Initial role | Status |
|---|---|---|
| `lightpanda` | low-memory/Agent reference and process-per-target combine candidate | active — W1/W2/W3/W7; Rust control host with one engine process per target; attributable per-target process metrics reconciled with the shared sampler (X9 ME3) |
| `chrome` | named contemporary comparison baseline | active — same-CDP-live-target W1 observed |
| `servo` | Rust embeddable full-engine candidate | active — API + software W1 runtime observed |
| `synthetic-control` | engine-neutral identity/control/memory court | active — native stdio + loopback CDP on one target; profile isolation; lifecycle RSS; capability attenuation court 33/33 (X9 ME1); adapter teardown court 24/24 (X9 ME2); frame/realm court 28/28 (D4 rules) |
| `native-dom` | native bounded route: html5ever DOM, bounded QuickJS realm, bounded `http` fetch | active — control 0.0.1 journey 27/27; network court 35/35 on a hermetic representative page; footprint lower than Lightpanda at every live stage; post-close retention attributed to allocator reservation and returned by an opt-in macOS arena per realm that holds a plateau through a 128-cycle soak and a 32-round concurrent eight-target soak under frozen criteria; default unchanged; frame/realm rules with link-click navigation, court 62/62 (D4); loopback CDP edge observed by puppeteer-core 24.15.0, court 58/58; engine-backed profile slice (macOS Keychain envelope store, RFC 6265 subset jar, `localStorage`, write-through with fault injection), court 80/82 with the frozen total-live criterion unmet, so P6 stays open; attribution court splits the churned footprint into allocator retention (feature-off equal churn already above the line under the default allocator) and a one-time keychain first-use cost; keychain ACL probe shows a rebuilt host is refused unattended; the approved out-of-process Keychain helper experiment failed its frozen complete-tree peak criterion and was not adopted; opt-in pinned-roots HTTPS with rustls + ring (C/perlasm inside ring), court 74/74 with an exact header cap, Secure cookies over verified https only; persistent Secure cookies survive a restart through the sealed record with volatile and expired ones dropped, court 78/78 |
| `tls-court` | standalone TLS candidate probes for the native route's HTTPS design (plain control, rustls+ring, rustls+aws-lc-rs, macOS SecureTransport) against a hermetic loopback server with disposable fixtures | measured — frozen S1–S10: rustls+ring meets all ten; aws-lc-rs fails teardown heap; SecureTransport is TLS 1.2-only here and fails three memory criteria; rustls+ring adopted for the native host's opt-in pinned-roots slice |
| `surface-court` | standalone macOS native-surface probes for G3 (plain control, direct Cocoa via objc2, winit + softbuffer) with a colour-bar fixture read back from the own window | measured — frozen S1–S9: real windows attach and detach with the owner at 0 → 1 → 0, but AppKit keeps about 10 MB after hide plus a per-cycle residual, so no in-process candidate is eligible; surface-process design recommended, pending ruling; nothing in the native host |
| `ecosystem-reference` | engine-neutral concept/capability/resource-ownership map of Electron, Wry and Tauri for [X9] | design-reference — no runtime dependency; six objections to plan 3b recorded; five micro-experiments named |
| `blitz` | modular Rust HTML/CSS/native-component candidate | planned |
| `wpe` | embedded low-consumption WebKit candidate | planned |
| `wry` | system-WebView compatibility candidate | planned |
| `cef` | Chromium/CDP compatibility control | planned |

`target/labs/` owns fetched binaries, builds, profiles and raw results and is
ignored. Only a reviewed, redacted, platform-qualified summary may move into a
lab's `evidence/` directory.
