# Ecosystem reference lab

Status: `design-reference` (no runtime, no build, no downloaded artifact)
Decision: `keep` as the engine-neutral concept, capability and
resource-ownership map for [X9]; it is not a lab that competes for the default
route and it earns no gate.

## Hypothesis

Electron, Wry and Tauri each solve part of "how developers embed, extend,
package and reason about a runtime", and their concepts can be mapped onto the
control 0.0.1 vocabulary (`protocol/README.md`) without inheriting their engine,
their window-bound page lifetime, or their authority model. The map should
show, per concept, where the fit is real and where borrowing would silently
bind MiniCon Surf to Chromium, a system WebView, or a window.

## Scope

Design input only. This lab reads official documentation and the pinned crate
sources already in the local registry cache; it does not add Electron, Wry or
Tauri as dependencies, implement a plugin framework, a Node compatibility
layer or a packager, and it records no memory or performance figures for the
references (none of their marketing numbers are used).

| Reference | Version used | Basis |
|---|---|---|
| Electron | `44.1.1` (Chromium `152.0.7977.65`, Node.js `24.19.0`, released 2026-09-02) | `docs/latest` pages read on 2026-09-03; the pages carry no version stamp |
| Wry | docs.rs `0.56.1` (2026-08-13); pinned source `0.55.1` | crate documentation plus `src/lib.rs`, `src/web_context.rs`, `src/wkwebview/mod.rs` |
| Tauri | `tauri 2.11.5`, `tauri-runtime 2.11.3`, `tauri-runtime-wry 2.11.4`, `tao 0.35.3` | v2 site (capabilities, permissions, scope, IPC, plugins, architecture) read on 2026-09-03, plus the pinned sources |

## Reproduction

```sh
python3 labs/ecosystem-reference/check-references.py
```

The script validates
[`evidence/ecosystem-concept-map-0.0.1.json`](evidence/ecosystem-concept-map-0.0.1.json)
structurally and greps the pinned Wry and Tauri sources for every cited code
fact when the registry cache holds them; absent sources are reported as
`unverified`, never as a pass. Electron facts are documentation quotes and are
listed by URL and access date in the evidence file.

## Concept map

Each row names the Surf concept, its owner and lifetime in control 0.0.1, the
closest object in each reference, and the hazard of borrowing it. The full
table with source pointers is the evidence file; this is the reading of it.

| Surf concept | Electron | Wry | Tauri | Fidelity and hazard |
|---|---|---|---|---|
| host / runtime | main process, `app` lifecycle, `app.getAppMetrics()` over every process | none: the caller owns the event loop and the `WebView` values | `App`/`AppHandle` over the `Runtime` trait; `RunEvent::{Ready, ExitRequested, Exit, …}` | lifecycle shape borrowable from Electron and Tauri; the process model is not |
| profile | `Session` (`persist:` partitions durable, others in-memory); owns cookies, cache, storage, proxy, permission handlers; **cannot be destroyed** | `WebContext(data_directory)`; on macOS a `WKWebsiteDataStore` by identifier, non-persistent when incognito (**incognito ignores the WebContext**) | Wry's data directory/incognito per webview; policy is compiled per window label | storage identity maps; budgets, locks and explicit ephemeral teardown do not |
| session | no object (`app` + `Session`) | none | `AppHandle` plus label-keyed managers | Surf-native; mapping it to `app` imports one-authority-per-process and `window-all-closed` quit logic |
| target | `WebContents`, created and terminated with its `BrowserWindow`; `WebContentsView` can adopt one `WebContents` (one view at a time) | `WebView` built only with a `HasWindowHandle`; `Drop` removes the platform view | `Webview` by label; `WebviewWindow` merges window and webview; closing a window removes its webviews; reparent and multi-webview need `unstable` | **all three bind page lifetime to a window**; only Electron's `BaseWindow` + `WebContentsView` loosens it |
| frame | `mainFrame` / `WebFrameMain` | navigation URL only | `on_navigation` / `on_page_load` only; Linux and Android cannot tell an iframe from the window for capabilities | Electron only |
| realm | `executeJavaScriptInIsolatedWorld(worldId)`, preload with context isolation | `evaluate_script`, initialization scripts | `eval_script`, plugin initialization scripts, isolation iframe | injection points; Surf keeps snapshots and actions outside the page realm |
| surface (headed / headless) | `show: false` still creates and, after `ready-to-show`, paints the renderer; offscreen still needs a frameless window; `backgroundThrottling` | **no headless mode**; `set_visible` is a view attribute; throttling policy applies to a view not in a window | `Window::hide` = `set_visible(false)`; `close` emits `CloseRequested`, `destroy` does not | none separates presentation lifetime from page lifetime; visibility is not detachment |
| revision / node reference | none (`debugger` exposes CDP node ids) | none | none | Surf-native |
| typed capability channel | `contextBridge` + `invoke`/`handle`; structured clone; no core access control | one `ipc_handler(Request<String>)`; custom protocols | commands gated by `resolve_access(command, window, webview, origin)`, build-time capabilities, invoke key, scopes enforced by the command | Tauri's vocabulary is the closest; its keys are window labels and origins, with no deadline, budget or audit |
| budgets and deadlines | none (`backgroundThrottling`, Chromium switches) | throttling policy only | none | Surf-native; throttling is scheduling, not a budget |
| memory ledger | `ProcessMetric{pid, type, name, memory{workingSetSize, peakWorkingSetSize, privateBytes}}`; `getProcessMemoryInfo` gives no `residentSet` on macOS and recommends `private` | none | none | the per-process metric shape is borrowable; it is not owner attribution |
| teardown | `close()` (page may cancel) vs `destroy()`; `'destroyed'`, `'render-process-gone'`; `before-quit` → `will-quit`; `app.exit()` skips them | `Drop` | `close` vs `destroy`; webviews removed with their window; plugin `on_drop`; `ExitRequested` with an action sender | close-versus-destroy and the quit sequence are good vocabulary; nobody measures retention afterwards |
| plugin / extension | none in core: Chrome extensions on a `Session`, Node modules, and `utilityProcess.fork` (a child Node with `net` and MessagePorts) | none | `Plugin` trait (`initialize`, `initialization_script`, `on_navigation`, `on_page_load`, `on_event`, `extend_api`), `Builder` (`setup`, `on_webview_ready`, `on_drop`), `permissions/default.toml`; a plugin holds the whole `AppHandle` | Tauri's lifecycle and default-permission files are the reference; no owner ledger or budget anywhere; the utility process is ambient capability |
| manifest / packaging | external | none | `tauri.conf.json` and `capabilities/*` compiled at build time | reference shape; build-time capabilities cannot express per-profile or per-session runtime policy |

## Objections to the plan text

Read against `plan/plan-0.0.x.md` section 3b and [X9] at commit `e4f12a4`:

1. **Window-owned page lifetime is not an Electron-only trait.** Wry builds a
   `WebView` only from a window handle and drops the view with it; Tauri's
   manager removes every webview of a closed window and gates reparenting
   behind `unstable`. The "do not inherit" cell must apply to all three,
   otherwise a Wry- or Tauri-shaped `SurfView` would bind targets back to
   windows exactly as [X9] forbids.
2. **"Startup-only headed/headless" misdescribes Wry.** Wry has no headless
   mode; visibility is a runtime view attribute and never detaches the page.
   The correct statement is that no reference offers detachment, so H5 cannot
   borrow it and must measure its own `show`/`hide`.
3. **Electron sessions cannot be destroyed.** Borrowing "stable session
   objects" must exclude that property: Surf ephemeral profiles are destroyed
   explicitly and persistent ones carry writer locks (`profile.delete` conflicts
   with live sessions).
4. **Tauri's authority is keyed on window label, webview label and origin,**
   scopes are enforced by each command, and capabilities are compiled at build
   time. The vocabulary (capability, permission, scope, default permission set)
   is worth borrowing; the keying is not. A Surf capability channel must be
   keyed on profile/session/target, carry deadline, budget and audit, and refuse
   any request that names only a surface or window.
5. **"Large extension surface" conflates three things** in Electron: Chrome
   extensions on a `Session`, Node modules, and the utility process. Only the
   first is a page-facing extension model; the utility process is the concrete
   example of an ambient-capability escape hatch (Node plus `net`) that [X9]'s
   "no generic IPC" rule must also cover for workers.
6. **No reference measures retention after teardown** or attributes memory to
   owners. Electron's per-process metrics are the only borrowable ledger shape,
   and even they are Chromium's definitions, not the court's physical footprint.

What the plan gets right: Electron's `close`/`destroy` and quit sequence,
Tauri's plugin lifecycle hooks and default-permission files, Wry's thin
window-handle boundary and custom protocol hook, and Tauri's isolation pattern
(a keyed privileged iframe) are all accurate borrow targets.

## Micro-experiments (future, minimal, testable)

| Id | Experiment | Where | Gate touched |
|---|---|---|---|
| ME1 | typed capability envelope: add owner, scope, deadline, budget and audit fields to one reserved control 0.0.1 operation on the synthetic host; a request keyed only by a surface or window is a typed refusal. **Done**: implemented as one optional request-envelope field rather than a new operation; `labs/synthetic-control` capability court 33/33 | `labs/synthetic-control` | G2 mechanism |
| ME2 | adapter teardown ordering: an adapter holding a target reference must drop before `session.close`; reuse the arena's reference-count proof pattern. **Done**: weak adapter handles, fixed teardown order with an extended-owner detector, CDP adapter attenuated to its target; `labs/synthetic-control` adapter court 24/24 | `labs/synthetic-control` | [X9] teardown evidence |
| ME3 | process-metric shape: report `{pid, type, name}` per child in the process-per-target host and check the sum equals the court's descendants-only RSS | `labs/lightpanda` host | M2 vocabulary; G1 open |
| ME4 | no ambient capability for workers: any future worker path runs the 35-item network negatives unchanged | not yet planned | P6 policy |
| ME5 | visibility is not detachment: record realm, DOM, scroll and footprint across hide → show on the surface court; a route whose hide is only `set_visible(false)` fails the G3 shape | `labs/synthetic-control` | G3 open |

## Gaps

- Nothing here ran against Electron, Wry or Tauri; the lab is a reading of
  documentation and pinned sources, not a measurement.
- Electron `docs/latest` pages carry no version stamp; the map records the
  stable release listed on the access date.
- Wry facts were cross-checked between docs.rs `0.56.1` and the pinned
  `0.55.1` source; a later Wry may change the throttling or data-store
  behaviour.
- Tauri's `dynamic-acl` feature and mobile plugin parts were not reviewed.

## Verdict

`keep` as design input for [X9] and for the later `SurfView`/application
layers. It changes no gate: G1, G3, P6 and G6 stay open, and no ecosystem
layer may be built before the native embedding boundary has earned it.
