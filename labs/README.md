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
| `lightpanda` | low-memory/Agent reference and baseline | active |
| `servo` | Rust embeddable full-engine candidate | planned |
| `blitz` | modular Rust HTML/CSS/native-component candidate | planned |
| `wpe` | embedded low-consumption WebKit candidate | planned |
| `wry` | system-WebView compatibility candidate | planned |
| `cef` | Chromium/CDP compatibility control | planned |

`target/labs/` owns fetched binaries, builds, profiles and raw results and is
ignored. Only a reviewed, redacted, platform-qualified summary may move into a
lab's `evidence/` directory.
