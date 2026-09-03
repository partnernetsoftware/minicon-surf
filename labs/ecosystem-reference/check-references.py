#!/usr/bin/env python3
"""Dependency-free check for the ecosystem concept map.

Validates the JSON evidence structurally and, when the pinned Wry and Tauri
sources are present in the local crates.io registry cache, greps them for the
facts the map cites. Missing sources are reported as `unverified`, never as
failures: the map is a design reference and this script adds no runtime
dependency. Electron facts come from documentation pages and are listed with
their URLs only.
"""

import glob
import json
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
EVIDENCE = HERE / "evidence" / "ecosystem-concept-map-0.0.1.json"

# (crate directory, relative file, substring that must exist, fact)
SOURCE_FACTS = [
    ("wry-0.55.1", "src/lib.rs", "pub fn build<W: HasWindowHandle>(self, window: &'a W)", "Wry WebView requires a window handle at build"),
    ("wry-0.55.1", "src/lib.rs", "pub fn build_as_child<W: HasWindowHandle>", "Wry child webview also needs a window handle"),
    ("wry-0.55.1", "src/lib.rs", "pub fn set_visible(&self, visible: bool)", "Wry visibility is a runtime view attribute"),
    ("wry-0.55.1", "src/lib.rs", "pub fn with_ipc_handler<F>", "Wry IPC is one handler"),
    ("wry-0.55.1", "src/lib.rs", "Note that WebContext will be ingored if incognito is", "Wry incognito ignores the WebContext"),
    ("wry-0.55.1", "src/lib.rs", "A policy where a web view that's not in a window fully suspends tasks.", "Wry throttling applies to a view not in a window"),
    ("wry-0.55.1", "src/web_context.rs", "pub fn new(data_directory: Option<PathBuf>) -> Self", "Wry WebContext is a data directory"),
    ("wry-0.55.1", "src/wkwebview/mod.rs", "WKWebsiteDataStore::nonPersistentDataStore(mtm)", "macOS incognito uses a non-persistent data store"),
    ("wry-0.55.1", "src/wkwebview/mod.rs", "self.webview.removeFromSuperview();", "Dropping a Wry WebView removes the platform view"),
    ("tauri-2.11.5", "src/manager/mod.rs", "pub(crate) fn on_window_close(&self, label: &str)", "Tauri removes a closed window's webviews"),
    ("tauri-2.11.5", "src/ipc/authority.rs", "pub fn resolve_access(", "Tauri authority resolves per command/window/webview/origin"),
    ("tauri-2.11.5", "src/ipc/authority.rs", "pub enum Origin {", "Tauri origin is Local or Remote"),
    ("tauri-2.11.5", "src/ipc/protocol.rs", "__TAURI_INVOKE_KEY__", "Tauri guards the IPC transport with an invoke key"),
    ("tauri-2.11.5", "src/plugin.rs", "pub trait Plugin<R: Runtime>: Send {", "Tauri plugin trait"),
    ("tauri-2.11.5", "src/plugin.rs", "on_drop: Option<Box<OnDrop<R>>>,", "Tauri plugin builder has on_drop"),
    ("tauri-2.11.5", "src/webview/mod.rs", "return Err(crate::Error::CannotReparentWebviewWindow);", "Tauri reparent is gated without unstable"),
    ("tauri-2.11.5", "src/window/mod.rs", "pub fn destroy(&self) -> crate::Result<()>", "Tauri window destroy"),
    ("tauri-2.11.5", "src/pattern.rs", "Isolation {", "Tauri isolation pattern"),
    ("tauri-runtime-2.11.3", "src/lib.rs", "ExitRequested {", "Tauri runtime exit request event"),
    ("tauri-runtime-wry-2.11.4", "src/lib.rs", "WindowMessage::Hide => window.set_visible(false),", "Tauri hide is set_visible(false)"),
]

REQUIRED_MAP_KEYS = {"surf", "surf_meaning", "electron", "wry", "tauri", "fidelity", "hazard"}


def registry_roots():
    home = Path(os.environ.get("CARGO_HOME", Path.home() / ".cargo"))
    return [Path(p) for p in glob.glob(str(home / "registry" / "src" / "*"))]


def main():
    data = json.loads(EVIDENCE.read_text(encoding="utf-8"))
    problems = []
    if data.get("runtime_dependencies") != []:
        problems.append("the map must declare no runtime dependencies")
    for row in data["map"]:
        missing = REQUIRED_MAP_KEYS - set(row)
        if missing:
            problems.append(f"map row {row.get('surf')!r} lacks {sorted(missing)}")
    for section in ("objections", "micro_experiments", "limitations"):
        if not data.get(section):
            problems.append(f"{section} is empty")
    for reference in data["references"].values():
        if not any(key.endswith("version") or key.endswith("versions") for key in reference):
            problems.append("every reference must record a version")
    roots = registry_roots()
    verified = unverified = 0
    for crate, relative, needle, fact in SOURCE_FACTS:
        paths = [root / crate / relative for root in roots if (root / crate / relative).exists()]
        if not paths:
            unverified += 1
            print(f"unverified (source not cached): {crate}/{relative}: {fact}")
            continue
        if any(needle in path.read_text(encoding="utf-8", errors="replace") for path in paths):
            verified += 1
        else:
            problems.append(f"{crate}/{relative} no longer contains: {needle!r} ({fact})")
    print(f"map rows: {len(data['map'])}; objections: {len(data['objections'])}; micro-experiments: {len(data['micro_experiments'])}")
    print(f"source facts verified: {verified}; unverified: {unverified}; access date: {data['access_date']}")
    for problem in problems:
        print(f"problem: {problem}")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
