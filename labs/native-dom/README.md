# Native DOM lab

Status: `exploring`
Decision: `keep` as the native bounded route measured slice by slice through
the shared control `0.0.1` host and courts. Slice 1 was HTML parsing and DOM
only; slice 2 adds a bounded QuickJS script realm with a minimal DOM shim and
passes the full shared journey. There is still no layout, network, storage
or real timers, and the slice says so with typed failures or documented gaps
rather than emulating them.

## Hypothesis

The native bounded route should earn its place one measured slice at a time.
The first slice asks: what does a real HTML parser plus DOM cost as a complete
process tree when driven by the same control vocabulary and courts as the
engine routes, and exactly which Agent semantics does it fail without a script
realm?

## Pin

- `dom_query = 0.27.0` (default features disabled) over `html5ever 0.38.0`
  and `markup5ever 0.38.0`; the html5ever crate checksum recorded by Cargo is
  `1054432bae2f14e0061e33d23402fbaa67a921d319d56adc6bcf887ddad1cbc2`.
- `rquickjs = 0.12.2` (QuickJS bindings, default features); the crate
  checksum recorded by Cargo is
  `4e04e4eedfb060b503b5f0a2644abb890b0b3620d3fb674f9455f230014964e4`. Each
  target's realm is capped at 16 MiB of QuickJS heap and a 512 KiB stack,
  and every evaluation is interrupted at the request deadline.
- `Cargo.lock` is tracked and the lab builds with `--locked --offline` from
  the same registry cache the Servo lab uses.
- The Rust global allocator is the system allocator; `memory.report` exposes
  libmalloc zone statistics and logical document owners.

## Scope and reproduction

`native-dom-control serve --stdio --fixture-root DIR --config-dir DIR` accepts
the same arguments as `servo-control`. It offers ephemeral profiles, one
session, hermetic fixture targets of at most 1 MiB, semantic snapshots,
revision-scoped click actions, `revision_at_least` waits and a memory report.

Slice 2 mirrors the html5ever tree into a QuickJS realm through
[`src/dom_shim.js`](src/dom_shim.js), a deliberately small DOM: `Node`,
`Element`, `Text`, `Document`, `Event` with bubbling, `MutationObserver`
delivered as microtasks, attributes, `dataset`, `textContent`,
`append`/`replaceChildren`/`removeChild`, `getElementById`, and a selector
subset (`tag`, `*`, `#id`, `.class`, `[attr="value"]`, descendant
combinator); unsupported selectors throw. Inline `<script>` elements run
after parsing in document order (not at parse position), `setTimeout` runs as
a microtask, and the same instrumentation the engine hosts inject then runs
unchanged inside the realm.

```sh
cargo build --release --locked --offline --manifest-path labs/native-dom/Cargo.toml \
  --target-dir labs/native-dom/target
python3 labs/servo/control-journey.py \
  --binary labs/native-dom/target/release/native-dom-control \
  --technology native-dom --technology-version 0.0.1 \
  --artifact-sha256 "$(grep -A3 '^name = "rquickjs"' labs/native-dom/Cargo.lock | grep checksum | sed 's/.*"\(.*\)"/\1/')" \
  --receipt labs/native-dom/evidence/native-dom-control-0.0.1-journey-script-realm.json
labs/court/run-target-retention-macos-arm64.sh \
  --native-dom labs/native-dom/target/release/native-dom-control \
  --candidates native_dom --sequential-cycles 8 \
  --receipt labs/court/evidence/macos-arm64-target-retention-native-dom-0.0.1-script-realm.json
```

## Findings against product contracts

### Agent control

Slice 1 (`native-dom-control-0.0.1-journey` receipt) passed 21 of 27: every
static check matched the engine hosts and the six failures were the declared
boundary (click `unsupported_capability`, waits for revision 1
`deadline_exceeded`, no mutation so no `stale_revision`, W2 showing `Before
script`).

Slice 2 (`native-dom-control-0.0.1-journey-script-realm` receipt) passes 27
of 27 with the journey unchanged: the interactive fixture's click listener
runs, the `MutationObserver` revision advances to 1, the reused reference is
`stale_revision` with both revisions in details, the wait observes revision
≥ 1 without a sleep, the post-click snapshot shows `Clicked` and `Continued`,
the W2 scripted fixture shows `After script` and its button, and the second
concurrent target opens. Target open took 5.873 ms and every other operation
under 1 ms; the realm reported 227,920 QuickJS malloc bytes for the
interactive fixture.

### Memory

Slice 1 measured 2,195,456 bytes summed RSS (1,327,416 physical footprint)
empty, 2,539,520 (1,376,568) with one target and 3,014,656 (1,851,704) with
eight concurrent targets. Slice 2, on the same eight-cycle court, measured
2,260,992 (1,343,800) empty, 4,489,216 (2,457,912) with one target,
5,144,576 (3,113,272) after eight closes and 6,471,680 (4,440,376) with
eight concurrent targets; 1,769,472 bytes of footprint remained after eight
closes. A script realm therefore costs about 1.1 MB of footprint per live
target on these fixtures, and the slice with actions is still about four
times below Lightpanda's single server and fifteen times below Servo at one
target by footprint. Every later slice is measured against this row.

## Exact limitations and next experiment

- No layout, images, fonts, network, storage or real timers; scripts run
  after parsing rather than at parse position; only inline scripts run.
- The DOM shim implements what the court fixtures and instrumentation use.
  It is not a Web-compatibility claim: unsupported selectors throw, and any
  page relying on layout, `fetch`, `XMLHttpRequest`, `localStorage` or
  timing fails explicitly.
- One fixture set, one platform, summed RSS and physical footprint only.
- The next slice must add the first real-page dependency in measured order:
  a bounded network fetch for same-origin subresources and `fetch()`, then a
  representative page beyond the hermetic fixtures. It passes only if the
  journey stays 27 of 27 and the court row stays below Lightpanda's single
  server at one target.
