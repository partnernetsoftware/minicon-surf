# Native DOM lab

Status: `exploring`
Decision: `keep` as the first slice of the native bounded route: HTML parsing
and DOM only, measured through the shared control `0.0.1` host and courts.
It has no layout, script realm, event dispatch or network, and it says so
with typed failures rather than emulating them.

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
- `Cargo.lock` is tracked and the lab builds with `--locked --offline` from
  the same registry cache the Servo lab uses.
- The Rust global allocator is the system allocator; `memory.report` exposes
  libmalloc zone statistics and logical document owners.

## Scope and reproduction

`native-dom-control serve --stdio --fixture-root DIR --config-dir DIR` accepts
the same arguments as `servo-control`. It offers ephemeral profiles, one
session, hermetic fixture targets of at most 1 MiB, semantic snapshots with
the same role rules the engine hosts apply in-page (heading, button, link,
textbox with label/value, label, text), `revision_at_least` waits at revision
0, and a memory report. `target.act` validates the reference and then returns
`unsupported_capability` with `{"slice":"html-dom","missing":["script
realm","event dispatch","layout"]}`; the document never mutates, so its
revision is always 0.

```sh
cargo build --release --locked --offline --manifest-path labs/native-dom/Cargo.toml \
  --target-dir labs/native-dom/target
python3 labs/servo/control-journey.py \
  --binary labs/native-dom/target/release/native-dom-control \
  --technology native-dom --technology-version 0.0.0 \
  --artifact-sha256 "$(grep -A3 '^name = "html5ever"' labs/native-dom/Cargo.lock | grep checksum | sed 's/.*"\(.*\)"/\1/')" \
  --receipt labs/native-dom/evidence/native-dom-control-0.0.1-journey.json
labs/court/run-target-retention-macos-arm64.sh \
  --servo-control labs/servo/target/release/servo-control \
  --native-dom labs/native-dom/target/release/native-dom-control \
  --candidates native_dom,lightpanda,servo_control,google_chrome --sequential-cycles 8 \
  --receipt labs/court/evidence/macos-arm64-target-retention-native-dom-0.0.0-servo-0.5.0-lightpanda-0.4.0-chrome-152.0.7977.75.json
```

## Findings against product contracts

### Agent control

The shared journey (`native-dom-control-0.0.1-journey` receipt) passes 21 of
27 checks. Everything static passes: the revision-0 snapshot lists heading,
label, textbox with value, button and link exactly as the engine hosts do;
references are revision-scoped; `max_nodes` truncation, typed refusals,
capacity and lifecycle behave identically. The six failures are the slice's
honest boundary: the button click is `unsupported_capability`, a wait for
revision 1 is `deadline_exceeded`, no `stale_revision` can arise because
nothing mutates, the post-click snapshot is unchanged, `target.inspect` never
reports a revision above 0, and the W2 scripted fixture shows `Before script`
because no script runs. Target open took 0.675 ms and a snapshot 0.23 ms.

This is exactly the [E7] rule that the native route measures HTML/DOM, layout,
JS and Web API cost incrementally: the next slice must add a script realm and
event dispatch before the route can claim any action semantics.

### Memory

On the shared eight-cycle retention court beside Servo, Lightpanda and
Chrome (seven rotating runs each), the slice measured 2,195,456 bytes empty,
2,539,520 with one target, 2,785,280 after eight closes (589,824 retained
above empty) and 3,063,808 with eight concurrent targets, all in one process.
That is the floor of the native route on this court: Lightpanda's one-target
tree is about eleven times larger and Servo's about thirty-five times. Every
later slice is measured against this row, and the comparison is only fair
once a slice can pass the action checks the engines pass.

## Exact limitations and next experiment

- No layout, script, events, images, fonts, network or storage; W2 and W7
  actions fail by design.
- One fixture set, one platform, summed RSS; the binary is 1.6 MB and the
  parse is in-process, so process-tree memory here is the floor for the
  route, not a browser.
- The next slice adds a bounded script realm (candidate: a pinned JavaScript
  engine crate) and DOM event dispatch, then reruns this journey unchanged;
  it passes only if the six failing checks turn green while the retention
  court row stays materially below Servo's.
