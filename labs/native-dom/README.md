# Native DOM lab

Status: `exploring`
Decision: `keep` as the native bounded route measured slice by slice through
the shared control `0.0.1` host and courts. Slice 1 was HTML parsing and DOM
only; slice 2 added a bounded QuickJS script realm with a minimal DOM shim and
passes the full shared journey; slice 3 adds a bounded `http` fetch with a
fail-closed address policy, same-origin external scripts, `fetch()` in the
realm, and a hermetic representative page. There is still no layout, https,
storage or real timers, and the slice says so with typed failures or
documented gaps rather than emulating them. The DOM shim is not a
Web-compatibility claim.

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
- `url = 2.5.8` for URL parsing, joining and origin comparison.
- `Cargo.lock` is tracked and the lab builds with `--locked --offline` from
  the same registry cache the Servo lab uses.
- The Rust global allocator is the system allocator; `memory.report` exposes
  libmalloc zone statistics and logical document owners.

## Scope and reproduction

`native-dom-control serve --stdio --fixture-root DIR --config-dir DIR
[--allow-origin http://HOST:PORT]...` accepts the same arguments as
`servo-control` plus an explicit origin allowlist. `memory.trim` runs
`malloc_zone_pressure_relief` and reports released bytes; the environment
knob `MINICON_SURF_NATIVE_REALM_ZONE=1` selects the macOS zone-per-realm
experiment described under retention attribution. It offers ephemeral
profiles, one session, hermetic fixture targets of at most 1 MiB or `url`
targets fetched under the network policy, semantic snapshots, revision-scoped
click actions, `revision_at_least` waits and a memory report that includes
network owners and limits.

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

### Bounded network (slice 3)

[`src/net.rs`](src/net.rs) is the only network path. Its limits are fixed
in code and reported by `memory.report`:

| limit | value |
|---|---|
| scheme | `http` only; `https`, `file`, `ftp` and every other scheme are `unsupported_capability` |
| address policy | fail closed: every IANA special-purpose IPv4 range (loopback, private, link-local including metadata services, shared address space, IETF assignments, TEST-NETs, benchmarking, 6to4 relay, multicast, reserved, broadcast) and every IPv6 address outside 2000::/3 or inside Teredo, ORCHID, benchmarking, documentation, 6to4-with-private-embed or NAT64 blocks is `permission_denied`; `localhost` names and embedded credentials are refused; only an exact `--allow-origin` bypasses the address check |
| redirects | at most 3, each hop re-authorized against the policy |
| bytes | 16 KiB of headers and 1 MiB of body per fetch, 4 MiB per target |
| time | 3 s per fetch under the request deadline, 1.5 s connect |
| concurrency | 4 queued `fetch()` calls per evaluation turn, 32 fetches per target, 8 external scripts per document |
| framing | HTTP/1.0 `GET`, `Connection: close`; informational statuses, `Transfer-Encoding`, malformed or conflicting `Content-Length` and truncated bodies are refused; bodies without `Content-Length` are read to close under the cap and marked `until-close` |
| resolution | the client connects only to the addresses `authorize` vetted, so a name cannot be re-resolved between check and connect; no environment proxy is consulted |

`fetch()` in the realm queues requests; the host serves the queue between
evaluation turns and settles each promise with a bounded `Response` (`ok`,
`status`, `url`, `headers.get`, `text()`, `json()`) or a `TypeError`
carrying the policy code. External `<script src>` elements run in document
order only when same-origin; cross-origin sources are skipped and reported
in `target.inspect`.

The hermetic representative page lives in
`labs/court/fixtures/representative/`: a nav, a form, a results list filled
by `fetch("data.json")` from an external `app.js`, and a button whose click
fetches `status.json`. Three companion pages exercise the concurrency cap,
the per-target fetch budget and a cross-origin script.

```sh
python3 labs/native-dom/network-journey.py \
  --binary labs/native-dom/target/release/native-dom-control \
  --lightpanda target/labs/lightpanda/0.4.0/lightpanda-aarch64-macos \
  --receipt labs/native-dom/evidence/native-dom-control-0.0.2-network-court.json
```

The court starts a loopback HTTP/1.0 server on an ephemeral port and passes
exactly that origin to `--allow-origin`. It passes 34 of 34 checks:

- a host without the allowlist refuses the court's own loopback origin with
  `permission_denied`;
- the representative page opens with one external script and three fetches
  (`index.html`, `app.js`, `data.json`), the snapshot lists the nav, heading,
  form, `8 results` and the eight fetched links, the button click fetches
  `status.json` and the `Continued` text appears after a revision wait;
- nineteen negatives return the expected typed code and reason: `https`,
  `file` and `ftp` schemes, 10/8, 169.254.169.254, 100.64/10, 192.0.0.170,
  `fd00::1`, `[::1]` on the court port, `localhost`, another loopback port,
  a `.invalid` name, more than three redirects, a redirect loop, a redirect
  into private space, a body over the cap, a slow body, a 404 document and a
  non-HTML document; exactly three redirects are followed;
- six concurrent fetches settle as four `ok 200` and two `rejected
  resource_limit`; sequential fetches stop at the 32-per-target budget
  (`ok=31 first_failure=resource_limit`); a cross-origin script is skipped,
  reported, and never requested from the server; fixture targets still open
  beside url targets and eight representative pages are live at once.

Footprint is reported stage by stage beside Lightpanda 0.4.0 as a single
server, never as one absolute number. Fixture court, eight sequential cycles,
seven runs, medians of summed physical footprint (summed RSS in brackets),
from `macos-arm64-target-retention-native-dom-0.0.2-network-slice-lightpanda-0.4.0`:

| stage | native slice 3 | Lightpanda single server |
|---|---|---|
| empty | 1,343,800 (2,342,912) | 8,356,392 (22,642,688) |
| one target live | 2,408,760 (4,587,520) | 9,077,336 (27,885,568) |
| after that close | 2,408,760 (4,587,520) | 9,077,336 (27,918,336) |
| eighth target live | 3,211,576 (5,390,336) | 9,912,920 (29,474,816) |
| after all closes | 3,211,576 (5,390,336) | 9,912,920 (29,474,816) |
| retained above empty | 1,867,776 (3,047,424) | 1,540,144 (6,799,360) |
| eight concurrent targets | 4,718,904 (6,897,664) | one target only |

Representative page, single readings after a 500 ms settle, both engines at
the same terminal state (`8 results`, eight links; the court strips inherited
proxy variables from Lightpanda's environment, the native host never reads
them):

| stage | native slice 3 | Lightpanda single server |
|---|---|---|
| empty (session open) | 1,343,800 | 8,487,464 |
| representative page live | 2,720,056 | 9,486,936 |
| eight representative pages live | 5,964,232 | one target only |
| after closing | 5,964,232 | 9,503,320 |
| retained above empty | 4,620,432 | 1,015,856 |

Two readings follow, and only together. The native slice's live footprint
is lower at every stage. Its lifecycle is not optimized: after every close
the footprint equals the live footprint, so nothing is returned to the OS,
and its retained-above-empty is larger than Lightpanda's on the fixture court
(1,867,776 against 1,540,144) and much larger on the representative page
(4,620,432 against 1,015,856). G1 stays open. The retention court below
attributes that retention.

### Post-close retention attribution

[`retention-court.py`](retention-court.py) runs every measurement in a fresh
host process (its `empty` and `live` stages are the fresh-process control),
then closes the eight targets, runs `memory.trim`
(`malloc_zone_pressure_relief`), reopens the same eight targets and closes
them again. Each stage records physical footprint and RSS from outside and,
from `memory.report`, the logical owners and libmalloc's `size_in_use`
against `size_allocated`, which splits retained bytes into still-allocated
and freed-but-reserved. Three workloads (static fixture, interactive fixture,
representative page over the network) × two allocators, one warm-up plus
seven measured runs each
(`native-dom-control-0.0.2-retention-attribution` receipt).

System allocator (the default), medians in bytes:

| stage | static fixture fp | interactive fixture fp | representative page fp |
|---|---|---|---|
| empty | 1,360,184 | 1,360,184 | 1,360,184 |
| live (8 targets) | 4,669,752 | 4,800,824 | 5,341,496 |
| post-close | 4,669,752 | 4,800,824 | 5,341,496 |
| post-trim | 4,669,752 (0 released) | 4,800,824 (0 released) | 5,341,496 (0 released) |
| reopen live | 4,833,616 | 4,981,072 | 5,472,592 |
| post-reclose | 4,850,000 | 4,981,072 | 5,472,592 |
| retained fp | 3,309,568 | 3,424,256 | 3,981,312 |
| retained libmalloc in-use | 4,096 | 4,096 | 20,416 |

Attribution: after the closes libmalloc's in-use bytes return to within
4,096 bytes of empty (20,416 on the representative page) in every run, every
logical owner is zero, and the per-realm QuickJS bytes (1.87 to 2.34 MB
across eight realms), the parsed tree and the network buffers are all
released. The 3.3 to 4.0 MB that stay in the footprint are freed blocks that
the default libmalloc zone keeps in its regions (`size_allocated` grows by 4
to 8 MB and never shrinks); `malloc_zone_pressure_relief` releases nothing.
Reopening eight targets costs only 98,328 to 163,864 bytes over the first
live stage, and the second close retains the same amount as the first, so
the retention is a bounded reservation that is reused, not per-cycle growth
and did not grow again across the measured reopen; this court does not prove
the absence of leaks in broader workloads. No QuickJS block was ever left in a dedicated zone at
destruction (leak counter 0 across every zone-cell close).

Repair candidate, macOS only: `MINICON_SURF_NATIVE_REALM_ZONE=1` gives each
QuickJS realm its own libmalloc zone through rquickjs's allocator hook and
destroys the zone after the runtime drops. Because rquickjs makes
`set_memory_limit` a no-op under a custom allocator, the zone allocator
carries its own accounting and `memory.report` reads that count; the hook
is compiled only on macOS and other targets get `unsupported_capability`.
The accounting contract, each point covered by a test: every block is
charged by the size libmalloc actually served (a request that passes the
pre-check but rounds over the limit is freed again and reported as out of
memory); `calloc` multiplies with overflow checks; the count is updated with
compare-and-swap loops; `realloc` allocates and charges the replacement
first, copies `min(old, new)` bytes, and only then releases and frees the old
block, so on any failure the old block stays valid, readable, writable and
counted; a zero new size yields a minimal block like this platform's
`realloc`; null is safe in `dealloc` and `usable_size`; and the zone is
destroyed only after the runtime dropped, with the blocks still in use at
that moment counted (zero in every run). Medians in bytes:

| stage | static fixture | interactive fixture | representative page |
|---|---|---|---|
| empty | 1,376,568 | 1,360,184 | 1,360,184 |
| live (8 targets) | 11,764,048 | 9,257,296 | 12,779,856 |
| post-close | 2,097,464 | 2,081,080 | 2,326,840 |
| reopen live | 3,866,984 | 3,883,368 | 4,112,744 |
| post-reclose | 2,212,176 | 2,261,328 | 2,474,320 |
| retained fp | 720,896 | 720,896 | 966,656 |
| RSS after the closes | 12,386,304 | 9,846,784 | 13,484,032 |

Retained footprint drops from 3.3 to 4.0 MB to 0.72 to 0.97 MB; the exact
two-sided Mann-Whitney test against the system cells gives U = 0 and
p = 0.00058 for all three workloads, and the 27-item journey and 35-item
network court pass under the knob. The cost is the live axis: the first
eight zones lift the live footprint to 9.3 to 12.8 MB (about 0.6 to 0.9 MB
per realm of zone bootstrap, mostly not repeated on reopen, where live is
3.9 to 4.1 MB) and RSS stays at 9.8 to 13.5 MB after the closes even though
footprint falls. The repair therefore passes the post-close criterion and
fails the live criterion, so it stays an opt-in experiment and the default
allocator is unchanged; the retention is recorded as bounded allocator
reservation rather than a tracked live owner in this court; broader leak
absence is not claimed, and G1 stays open.

The hard cap is not a guaranteed usable capacity. `capacity-growth.html`
grows one dense array until the realm throws; the realm's live bytes read
afterwards were 11,856,928 (0.7067 of 16 MiB) under the default allocator
and 7,972,784 (0.4752) under the zone allocator, identical in all seven runs
each. A growing array reallocates its backing store by about 1.5×, and the
zone allocator holds old and new buffers at once while the replacement is
charged, so the largest single growth step fails at roughly half the cap;
QuickJS's default path reallocates in place and fails only when the new
buffer itself exceeds the cap. This is the deliberate price of the failure
contract; an atomic replace accounting with a separate transient-peak bound
would be a further experiment, not a shortcut through that contract.

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

- No layout, images, fonts, https, cookies, storage or real timers; scripts
  run after parsing rather than at parse position; only inline and
  same-origin external scripts run.
- The DOM shim implements what the court fixtures and instrumentation use.
  It is not a Web-compatibility claim: unsupported selectors throw, and any
  page relying on layout, `fetch`, `XMLHttpRequest`, `localStorage` or
  timing fails explicitly.
- One fixture set, one platform, summed RSS and physical footprint only.
- Public-address negatives are refused before any connection, so they
  exercise policy rather than reachability; the `.invalid` negative depends
  on the system resolver returning no address.
- Memory freed by closing realms stays in libmalloc as reserved regions:
  attributed above; the only measured way to return it (a zone per realm)
  costs live footprint and is not the default.
- The next slice must add a bounded persistent-profile store (cookies and
  local storage) under the same journey and court, then `https` with pinned
  roots; each passes only if the 27-item journey and the 35-item network
  court stay green and the footprint court row stays below Lightpanda's
  single server at one target. A repair that returns reserved memory without
  the zone's live cost (for example a realm heap arena unmapped at close)
  is a separate experiment against the same retention court.
