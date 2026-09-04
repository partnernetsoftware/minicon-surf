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
knobs `MINICON_SURF_NATIVE_REALM_ZONE=1` and `MINICON_SURF_NATIVE_REALM_ARENA=1`
(mutually exclusive) select the macOS zone-per-realm and arena-per-realm
experiments described under retention attribution. It offers ephemeral
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
destroys the zone after the runtime drops. The zone allocator carries its
own accounting and enforces the 16 MiB limit on served sizes, and
`memory.report` reads that count; the hook is compiled only on macOS and
other targets get `unsupported_capability`. (An earlier revision of this
paragraph said rquickjs makes `set_memory_limit` a no-op under a custom
allocator. That is the rquickjs documentation, but the pinned quickjs-ng
checks `malloc_limit` in its own `js_malloc_rt`/`js_realloc_rt` wrappers
before any allocator is called, so the limit binds under every allocator;
`quickjs_enforces_its_limit_under_a_custom_allocator` proves it. The zone
accounting is therefore a second, tighter check rather than the only one;
the measurements above are unchanged.)
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

### Realm heap arena (macOS prototype)

`MINICON_SURF_NATIVE_REALM_ARENA=1` is the experiment the previous section
named: instead of a libmalloc zone, each QuickJS realm gets one private
anonymous mapping (`mmap`, 32 MiB of address space, pages cost nothing until
written) served by [`src/arena.rs`](src/arena.rs), and the mapping is
returned to the kernel in one `munmap` at close. The design separates a
portable part from the platform part:

- `Heap` is plain Rust over a caller-provided byte range: boundary tags
  (16-byte header holding the block size, an in-use bit and the previous
  block's size), 64 free bins (exact 16-byte classes up to 512 bytes,
  power-of-two bins above), first fit with splitting, coalescing with both
  neighbours on free, in-place shrink, in-place growth into a free
  successor, and otherwise allocate-copy-free. It never calls the operating
  system, so its contract is tested on every platform: 16-byte alignment
  (rquickjs requires `usize` alignment), exact and stable `usable_size`,
  minimal non-null blocks for zero sizes, overflow-checked `calloc` that
  zeroes the payload, null on exhaustion with nothing charged, the old
  block valid, readable, writable and counted after a failed `realloc`,
  abort rather than corruption on a pointer that is not a live block of
  this heap, and a 20,000-step randomized model check that walks every
  block and bin after each 32 operations and ends with the whole range
  coalesced into one free block.
- `Region`, `Arena` and `ArenaAllocator` are the macOS prototype:
  `mmap`/`munmap`, plus `madvise(MADV_FREE_REUSABLE)` on the whole pages of
  the free tail when `memory.trim` runs on a live realm and
  `madvise(MADV_FREE_REUSE)` before the heap grows back into them. Other
  targets compile without them and answer `unsupported_capability`.

Destruction order is structural rather than positional. The realm and the
allocator inside the QuickJS runtime each hold an `Rc` to the arena;
rquickjs drops the allocator after `JS_FreeRuntime`, and the mapping goes
away only when the last holder drops, so no allocator call and no QuickJS
block can outlive it whatever the field order or any stray runtime handle.
`realm_frees_every_block_before_its_arena_is_unmapped` checks that after the
realm drops the heap holds zero blocks and the realm's `Rc` is the last one;
`memory.report` counts `arenas_unmapped` and `arena_blocks_leaked_total`
(zero in every court run). The heap is single-threaded by construction: it
is reached only through the runtime that owns the allocator, and rquickjs
without the `parallel` feature keeps that runtime on one thread. The arena
carries no byte limit of its own: the 16 MiB cap is QuickJS's, proven to
bind under a custom allocator by the test above, and the reservation is
twice the cap so that a large reallocation which cannot grow in place can
hold old and new buffers at once, as the default allocator can.

Three-arm court (`native-dom-control-0.0.2-retention-attribution-arena`
receipt: default, zone and arena rerun together, three workloads, one
warm-up plus seven runs each), medians in bytes:

| static fixture | system | zone | arena |
|---|---|---|---|
| empty | 1,343,800 | 1,343,800 | 1,343,800 |
| live (8 targets) | 4,653,368 | 15,008,080 | 4,489,720 |
| post-close | 4,669,752 | 2,031,928 | 2,015,544 |
| post-trim | 4,669,752 | 2,031,928 | 2,015,544 |
| reopen live | 4,866,384 | 3,899,752 | 4,719,096 |
| post-reclose | 4,866,384 | 2,228,560 | 2,261,304 |
| retained fp | 3,325,952 | 688,128 | 671,744 |
| RSS live | 6,815,744 | 17,154,048 | 6,520,832 |
| RSS post-close | 6,832,128 | 15,613,952 | 4,177,920 |

| interactive fixture | system | zone | arena |
|---|---|---|---|
| empty | 1,343,800 | 1,343,800 | 1,343,800 |
| live (8 targets) | 4,735,288 | 11,829,584 | 4,538,872 |
| post-close | 4,735,288 | 2,130,232 | 2,064,696 |
| post-trim | 4,735,288 | 2,130,232 | 2,081,080 |
| reopen live | 4,931,920 | 3,948,904 | 4,751,864 |
| post-reclose | 4,931,920 | 2,294,096 | 2,277,688 |
| retained fp | 3,391,488 | 786,432 | 720,896 |
| RSS live | 6,897,664 | 13,975,552 | 6,569,984 |
| RSS post-close | 6,897,664 | 12,451,840 | 4,227,072 |

| representative page | system | zone | arena |
|---|---|---|---|
| empty | 1,343,800 | 1,343,800 | 1,343,800 |
| live (8 targets) | 5,308,728 | 16,105,808 | 5,390,840 |
| post-close | 5,308,728 | 2,326,840 | 2,244,920 |
| post-trim | 5,308,728 | 2,343,224 | 2,244,920 |
| reopen live | 5,488,976 | 4,112,744 | 5,571,064 |
| post-reclose | 5,488,976 | 2,441,552 | 2,425,144 |
| retained fp | 3,964,928 | 983,040 | 901,120 |
| RSS live | 7,602,176 | 18,382,848 | 7,553,024 |
| RSS post-close | 7,602,176 | 16,826,368 | 4,538,368 |

Judged on every axis at once, against the same-court default arm:

- post-close: retained footprint falls from 3.3 to 4.0 MB to 0.67 to
  0.90 MB (exact two-sided Mann-Whitney U = 0, p = 0.00058 in all three
  workloads), the same level the zone reached;
- first-open live: 4,489,720 and 4,538,872 on the fixtures against
  4,653,368 and 4,735,288 (U = 0, p = 0.00058, lower), and 5,390,840 on the
  representative page against 5,308,728 (U = 15, p = 0.243, not
  distinguishable); the zone's 11.8 to 16.1 MB live cost does not appear
  (zone versus arena U = 0, p = 0.00058 everywhere). The arenas' own
  touched extent at live is 2,331,264 bytes across eight fixture realms and
  2,903,808 on the representative page;
- RSS after the closes: 4.18 to 4.54 MB against 6.8 to 7.6 MB (U = 0,
  p = 0.00058) and the zone's 12.5 to 16.8 MB;
- capacity: the dense array reaches 11,417,184 bytes (0.6805 of 16 MiB)
  before the realm throws, identical in seven runs, against 0.7067 under the
  default allocator and 0.4752 under the zone; the arena grew the array in
  place and its touched extent peaked at 11,485,488 bytes, so old and new
  buffers were never both resident;
- reopen: eight targets reopened cost 212,992 to 245,760 bytes over the
  first live stage and the second close retained the same amount as the
  first; sixteen arenas were unmapped per run with zero blocks leaked.

`memory.trim` on the closed host releases nothing under any arm because no
realm is alive to trim; the arena tail trim is exercised by its unit test
and applies to live realms only. Under the arena the 0.67 to 0.90 MB that
remain are no longer QuickJS: libmalloc in-use is back within 4,096 bytes
of empty, so they are the default zone's reserved regions from the parsed
trees, network buffers and host containers, plus kernel accounting the
court does not split further. Under this knob the 27-item journey passes
27 of 27 and the network court 35 of 35.

No axis is clearly worse, so the arena passes the criteria this experiment
set. It nevertheless stays opt-in and the default allocator is unchanged:
this is one macOS arm64 machine, three small workloads and a single reopen;
interior trimming, a many-cycle soak, fragmentation under long-lived
realms and a second platform behind the `Region` boundary are unmeasured,
and the remaining retention is still not attributed to a tracked owner.
Leak absence is not claimed. G1 stays open.

### Long-cycle soak and fragmentation court

[`arena-soak-court.py`](arena-soak-court.py) fixes its rules and adoption
criteria before running (they are copied into the receipt and were committed
first): two browser workloads, the interactive fixture (open, snapshot,
revision-scoped click, revision wait, close) and the hermetic representative
page (open over the bounded network, snapshot, close), run 128 open → use →
close cycles in one host process per run, one warm-up plus seven runs per
arm; physical footprint, RSS, libmalloc in-use and allocated, realm bytes and
the arena's used, blocks, high-water, decommit mark, unmapped and leaked
counts are sampled live and post-close at cycles 1, 2, 4, 8, 16, 24, 32, 48,
64, 80, 96, 112 and 128; after every close a teardown check requires every
owner at zero, no live arena, no leaked block and (arena arm) exactly one
unmapped mapping per closed target, and any violation fails the run. The
retained value is post-close minus the run's own empty footprint, the slope
is a least-squares fit over the post-close samples from cycle 8, late growth
is cycle 128 minus cycle 64, and the reopen cost is a live sample minus the
previous post-close sample. A separate allocator-stress section opens
`labs/court/fixtures/allocator-stress.html` (interleaved small objects and
32 KiB buffers with every other one freed, mid-size arrays into the holes,
one array grown and emptied twenty times, string growth) once per run and
eight times in one process; it is an allocator microbenchmark, judged apart
from the browser workloads, and `memory.trim` on its live realm is reported
as what the arena path is, a tail-only mark of the free span after the last
live block, never a whole-realm close recovery.

Criteria fixed before the run, arena against the same-court default: C1
retained at cycle 128 lower with an exact Mann-Whitney p < 0.05 on both
workloads; C2 first-open live at most 1.10× the default; C3 slope at most
4,096 bytes per cycle, late growth at most 512 KiB, and slope at most the
default's plus 1,024; C4 reopen cost at cycle 128 at most 1.5× the cost at
cycle 8; C5 RSS after the close at cycle 128 at most the default's; C6 zero
teardown violations in every run; C7 dense-array capacity at least 0.90× the
default's. The arena is court-eligible only if all seven hold, and the
default stays unchanged and the arena opt-in either way.

Results (`native-dom-control-0.0.2-arena-soak` receipt; medians of seven
runs, bytes, empty footprint 1,343,800 in every run):

| measure | interactive default | interactive arena | representative default | representative arena |
|---|---|---|---|---|
| first-open live − empty | 1,277,952 | 557,080 | 1,376,256 | 770,072 |
| retained after cycle 1 | 1,277,952 | 262,144 | 1,425,408 | 409,600 |
| retained after cycle 8 | 1,867,776 | 770,048 | 2,064,384 | 933,888 |
| retained after cycle 32 | 2,064,384 | 1,032,192 | 2,211,840 | 1,163,264 |
| retained after cycle 64 | 2,097,152 | 1,032,192 | 2,310,144 | 1,196,032 |
| retained after cycle 128 | 2,146,304 | 1,048,576 | 2,326,528 | 1,196,032 |
| slope, cycles 8–128 (bytes/cycle) | 1,851.8 | 1,149.3 | 1,584.6 | 1,251.6 |
| late growth 64→128 | 49,152 | 16,384 | 32,768 | 0 |
| reopen cost at cycle 8 | 147,456 | 426,008 | 114,688 | 573,464 |
| reopen cost at cycle 128 | 0 | 311,320 | 0 | 393,240 |
| RSS live at cycle 128 | 5,668,864 | 4,882,432 | 5,963,776 | 5,210,112 |
| RSS after the close at cycle 128 | 5,668,864 | 4,587,520 | 5,963,776 | 4,833,280 |
| libmalloc in-use after the close at 128 | 212,448 | 49,168 | 229,056 | 65,792 |
| arena used / high-water, live at 128 | – | 278,240 / 291,408 | – | 343,952 / 362,976 |
| teardown violations (runs) | 0 | 0 | 0 | 0 |

Reading: both arms plateau, and the plateau is not an arena property. The
default allocator's retained footprint climbs from 1.3–1.4 MB after the
first cycle to 2.1–2.3 MB and is flat from cycle 80; the arena's climbs
from 0.26–0.41 MB to 1.05–1.20 MB and is flat from cycle 24 (interactive)
or 48 (representative), with the last 64 cycles adding at most one page.
What remains in the arena arm after 128 closes is the default zone's
reservation for the host's own parsed trees, network buffers and
containers, since libmalloc in-use is back to 49–66 KB above empty and no
arena, block or mapping survives a close (0 violations in 28 runs, 128
mappings unmapped per run). The arena's cost is per open, not per cycle:
each realm starts on fresh pages, so a reopen touches 311–573 KB against
the default's reuse of warm pages (0–147 KB), and that cost falls, not
rises, over the soak. The 128-cycle slopes over the sample cycles from 8
are 1.1–1.3 KB per cycle for the arena against 1.6–1.9 KB for the default,
both dominated by the early climb. Mann-Whitney on retained at cycle 128
gives U = 0, p = 0.00058 on both workloads. Dense-array capacity is
unchanged at 0.6805 of 16 MiB (default 0.7067). Under the frozen criteria
C1–C7 all hold, so the arena is **court-eligible on this court**; the
default allocator is nevertheless unchanged and the arena stays opt-in,
because one platform, two workloads and one target per cycle are not a
default decision, and the eligibility says nothing about other platforms.

Allocator stress, judged apart (`allocator-stress.html`, one realm):

| measure | default | arena |
|---|---|---|
| live − empty footprint | 18,481,176 | 19,316,784 |
| realm bytes (QuickJS count) | 6,378,224 | 7,160,176 |
| arena used / high-water / blocks | – | 7,701,296 / 19,359,024 / 33,820 |
| high-water ÷ used (fragmentation) | – | 2.51 |
| libmalloc allocated − in-use, live | 35,504,704 | 20,935,888 |
| `memory.trim` on the live realm: reported / footprint change | 0 / 0 | 1,048,576 tail-only / −802,816 |
| post-close − empty | 18,497,584 | 294,912 |
| eighth in-process cycle − first, post-close | +1,671,168 | +393,216 |

The adversarial shape does what it was designed to do: the boundary-tag
heap ends the script with 2.5× more touched address space than live
bytes, so the arena realm costs about 0.8 MB more footprint live than
the default. The tail-only trim is exactly that: it marks the 1 MiB free
span after the last live block reusable and the footprint falls by
0.8 MB; the other fragmented holes stay resident until the realm closes,
and this trim must not be read as a close recovery. On the other side of
the ledger the default allocator keeps the whole 18.5 MB after the realm
closes and grows a further 1.7 MB over eight in-process cycles, while the
arena arm returns to 0.29 MB above empty and grows 0.39 MB over the same
cycles. These are allocator numbers under one synthetic script and are not
browser results; they do not enter C1–C7.

An allocator reporting defect was found and fixed during this court and
committed separately: `Arena::trim` used to count the untouched remainder
of the 32 MiB reservation as released (15,253,504 bytes reported for a
0.8 MB footprint change). It now clamps to the heap's high-water mark and
reports only newly marked pages; the receipt above is from the fixed
binary. No safety invariant was affected: every teardown check passed in
both runs.

### Concurrent multi-target soak court

[`arena-concurrent-soak-court.py`](arena-concurrent-soak-court.py) was
committed with its rules and criteria before its first full run. One host
process per run repeats 32 rounds of an Agent-shaped concurrent pattern:
open targets up the ladder 1 → 2 → 4 → 8 with even slots on the interactive
fixture and odd slots on the representative page over the bounded network,
use every target (snapshot; revision-scoped click and revision wait on the
interactive ones), close four of the eight in an interleaved order that
changes every round (`0 2 4 6`, `1 3 5 7`, `7 5 3 1`, `2 3 4 5`), check that
every survivor keeps its revision and snapshot node count, refill the closed
slots and use them, then close all eight. Every stage of every round samples
physical footprint, RSS and virtual size from outside and owners, libmalloc,
realm bytes and the arena's used, blocks, high-water, reserved, decommit,
unmapped and leaked counts from inside. A partial close that does not remove
exactly its owners, realms and arenas, a survivor whose state changes, or
an all-close that leaves any owner, arena, block or mapping behind fails
the run; nothing is recovered by restarting the host. The criteria, fixed in
advance: K1 peak live with eight targets at most 1.10× the default in the
first and last round; K2 per-target marginal cost at most 1 MiB, at most
1.25× its own first-round value and at most 1.10× the default's cold
first-round marginal (the default's later marginal is libmalloc page reuse,
recorded but not a bound); K3 partial-close exactness; K4 survivor state;
K5 all-close zero owners, arenas, leaked blocks and every mapping unmapped;
K6 post-all-close slope at most 8 KiB per round, late growth at most
512 KiB and slope at most the default's plus 2 KiB; K7 retained after the
last all-close lower with p < 0.05; K8 dense capacity at least 0.90× the
default; K9 the 27-item journey and 35-item network court on the same
binary under both arms. The 32 MiB virtual reservation per realm is
recorded explicitly: reserved, touched (high-water) and host virtual size
sit beside the physical footprint. Interior trimming is decided by a
pre-registered signal, not by the verdict: it stays deferred unless the
arena's summed high-water minus used at peak exceeds both 25% of used and
1 MiB.

Results (`native-dom-control-0.0.2-arena-concurrent-soak` receipt; medians
of seven runs after one warm-up, bytes above the run's empty footprint of
1,343,800 unless stated; 384 targets opened per run):

| measure | default | arena |
|---|---|---|
| ladder 1 / 2 / 4 / 8, round 1 | 1,212,416 / 1,818,624 / 2,473,984 / 3,784,704 | 507,928 / 1,081,392 / 1,966,176 / 3,735,744 |
| ladder 1 / 2 / 4 / 8, round 32 | 4,210,712 / 4,210,712 / 4,210,712 / 4,227,096 | 1,703,960 / 2,080,816 / 2,768,992 / 4,210,880 |
| peak with eight targets, round 1 → 32 | 3,784,704 → 4,227,096 | 3,735,744 → 4,210,880 |
| marginal cost per target, round 1 → 32 | 372,151 → 0 | 461,117 → 358,131 |
| after the interleaved close of four, round 32 | 4,227,096 | 2,801,760 |
| reopen cost of the four slots, round 1 → 32 | 131,072 → 0 | 1,359,968 → 1,409,120 |
| retained after the all-close, round 1 / 8 / 16 / 32 | 3,915,776 / 4,177,944 / 4,194,328 / 4,227,096 | 1,015,808 / 1,392,640 / 1,392,640 / 1,392,640 |
| post-all-close slope (bytes/round) and late growth | 2,636.5 and 0 | 0.0 and 0 |
| RSS at peak, round 32 (absolute) | 7,831,552 | 7,667,712 |
| RSS after the all-close, round 32 (absolute) | 7,831,552 | 4,980,736 |
| libmalloc in-use at peak / after the all-close (above empty) | 2,220,912 / 98,400 | 54,032 / 33,296 |
| QuickJS realm bytes at peak (eight realms) | 2,133,312 | 2,087,808 |
| arena reserved / touched / used / blocks at peak | – | 268,435,456 / 2,627,712 / 2,488,768 / 25,060 |
| host virtual size, empty → peak (absolute) | 445,749,706,752 → 445,749,706,752 | 445,749,362,688 → 446,017,798,144 |
| mappings unmapped per run / targets opened | – | 384 / 384 |
| rule violations (14 runs) | 0 | 0 |

Reading. Live is a wash: with eight concurrent targets the two arms are
within 1.3% of each other in the first round and within 0.4% in the last,
so the arena's per-realm bookkeeping costs nothing visible at eight pages.
The default's marginal cost falls to zero after the first round only
because libmalloc keeps the pages of closed realms and hands them back to
the next open; the arena pays about 358 KB of fresh pages per open in every
round (the reopen of four slots costs 1.41 MB against the default's 0), and
that cost is flat, not growing. The partial close is where the arms part:
closing four of eight targets in an interleaved order returns 1.41 MB in the
arena arm and nothing in the default arm, and every one of the 224 partial
closes removed exactly its four owners, four realms and four mappings while
all survivors kept their revision and node count. After the all-close the
arena arm sits at 1.39 MB above empty from round 8 onward with a slope of
zero, the default at 4.2 MB with a slope of 2.6 KB per round and no late
growth; the arena's remainder is again libmalloc reservation for the host's
own trees and buffers (in-use 33 KB above empty, no arena, block or mapping
alive; 384 mappings unmapped per run). Mann-Whitney on the final retained
value gives U = 0, p = 0.00058; dense capacity is unchanged at 0.6805
against 0.7067; the 27-item journey and the 35-item network court pass
under both arms on this binary. K1–K9 all hold, so the arena is
**concurrent-court-eligible on this court**; the default allocator stays
unchanged and the arena opt-in.

The virtual reservation is real and is recorded as such: eight live realms
reserve exactly 268,435,456 bytes of address space (32 MiB each), which the
host's virtual size shows as a 268 MB step over its 445.7 GB macOS
baseline, while the arenas touch 2,627,712 bytes and the physical footprint
is 4.21 MB, the same as the default's. The reservation is a per-target
address-space budget (eight targets: 256 MiB; the host's `MAX_TARGETS` is
eight), not memory, but it is not free: it bounds how many realms a
32-bit-like or address-limited environment could hold and it is the number
a later platform must re-derive.

Interior holes did not show a physical cost on the concurrent browser
workload: at peak the eight arenas' summed high-water exceeds their summed
used by 138,944 bytes, 5.6% of used and well under the pre-registered
signal (25% and 1 MiB), so interior trimming stays deferred; the 2.5×
ratio seen under the adversarial allocator script remains an allocator
risk on record, not a browser cost.

### Frames, realms and link navigation (D4 on the native route)

Hypothesis: the control 0.0.1 frame/realm rules hold on real documents:
every native target exposes one main frame and one main-world realm with
host-wide monotonic ids, a real link click on a hermetic page is a
same-frame navigation that keeps the frame, increments the document
generation, retires the realm and keeps the target revision monotonic, and a
navigation that the bounded network policy refuses leaves the target
exactly as it was.

Scope: `Target` carries `frame_id` (minted with the target), `generation`,
`realm_id` (minted with each document, never reused) and a revision base so
the revision the caller sees is the realm's count plus everything before the
last navigation. `target.open` reports `frame`, `generation` and `realm`;
`target.inspect` lists `frames[]` (the main frame first, then bounded child
frames) and `realms[]` with `frame_limit` 8; `target.snapshot` takes optional `frame` and `realm` and
names what it observed, refusing a foreign, retired or unknown id with the
same `not_found`. A click on an `<a href>` node dispatches the click event
and, unless the page prevented the default, navigates: the new document is
built completely first (`build_target`, the same path `target.open` uses:
fetch under the target's own policy and budget with the same origin,
redirect, size, deadline and address rules, parse, a fresh realm, scripts,
instrumentation) and only then swapped into the live target, so a failed
navigation cannot half-update anything; the attempt is charged as a denied
network attempt and the error carries `navigation: failed` with the
untouched generation and realm. Fixture targets may follow links only to
court fixture files; the network boundary is not widened. Frames and realms
count as owners in `memory.report` with `retired_total` and
`navigations_total`.

Child frames (`child-frame-design-0.0.1.md`): a same-origin `<iframe src>`
becomes a bounded child frame with its own id, generation and realm, built
with its parent under the parent's own fetch and byte budget, at most 7 of
them, depth one. `target.snapshot` narrows to one and names what it observed;
node ids are target-scoped, so a reference taken in a child is refused by
`target.act` rather than resolved against the main frame. A child runs no
scripts. A parent navigation ends its children, and the click path names them
in `ended_frames`. One child costs about 247 KB of live owner bytes, seven
about 1.7 MB, and the whole cost returns on close.

Losses, recorded rather than approximated: no acting inside a child frame,
no child navigation, no nesting, no cross-origin or `srcdoc` children, no
scripts in a child, and a child frame projected through CDP carries its
parent's `url`; no capability attenuation on this host, so a request carrying the field
is refused `invalid_request` (fail-closed, no downgrade); no CDP projection
of frames or realms here; navigation is a link click only (no
`target.navigate`, history or form submission).

Reproduction:

```sh
python3 labs/native-dom/frame-realm-court.py \
  --binary labs/native-dom/target/release/native-dom-control \
  --receipt labs/native-dom/evidence/native-dom-control-0.0.2-frame-realm.json
```

Evidence (`native-dom-control-0.0.2-frame-realm` receipt, 62 of 62, the
same 31 checks under the default allocator and the opt-in arena): open
names frame, generation 1 and realm; inspect enumerates one frame and one
realm; ids are disjoint across targets; snapshots name frame, realm and
generation and accept the live pair; another target's frame and an unknown
frame are the same `not_found` with the same message, another target's
realm is `not_found` with realm scope; the out-of-court `https` link on the
fixture page fails `unsupported_capability` with `navigation: failed` and
leaves frame, realm, generation and revision untouched, the old realm still
serves snapshots and the button still acts; the in-court link
(`semantic-nav.html` → `semantic-static.html`) navigates: same frame,
generation 2, the old realm retired, the revision one above the previous,
the old node reference `stale_revision`, the retired realm `not_found` with
`realm_not_live_in_target`, the new document observable at generation 2 and
`revision_at_least` waiting on the absolute revision; over the bounded
network the representative `nav.html` follows its same-origin link to
`about.html` (one fetch charged to the target), back again to a third realm
on the same frame, and its `https`, private-address, `/notfound` and
`data.json` links fail as `unsupported_capability`, `permission_denied`,
`not_found` and `unsupported_capability` with the target untouched each
time; a capability-bearing request is `invalid_request`; owners count three
frames and realms with three retirements and three navigations, then zero
after the closes, and a closed target's frame is `not_found` at the target.
The 27-item journey and the 35-item network court pass under both
allocators on the same binary. Footprint with two fixture targets live was
2,900,280 bytes (default) and 2,212,200 (arena) against 1,360,184 empty,
and 3,785,016 / 2,113,848 after the closes: the identity bookkeeping is a
few strings per target and shows no measurable delta against the earlier
courts.

Gaps: one frame per target, so the child-frame half of the rules is
exercised only on the synthetic court; no engine-side iframe support; no
CDP `Page.getFrameTree` on this host; the failed-navigation rollback is
proven by construction and by the court, not by a fault-injection inside
the build.

Verdict: `keep`. D4 stays open until a named external client observes these
frames through CDP; G1, G3, P6 and G6 stay open.

### Qualified CDP frame tree with a named client (D4)

Hypothesis: the native route can expose the same live target through a
bounded loopback CDP edge without a second copy of state or a second
authority, project frame identity as adapter-scoped `Page.FrameId`s that are
one-to-one with the native frame while both live and survive a same-frame
navigation, and be driven by a pinned external client whose exact claim is
recorded, with every unsupported method an explicit loss.

Scope: `src/cdp.rs` (loopback `--cdp-port PORT --ready-file PATH`, header
16 KiB and message 64 KiB bounds, masked frames only, one connection at a
time, 30 s read timeout) translates each qualified method into a control
0.0.1 operation sent over a channel to the host's main loop, which now
multiplexes stdio lines and edge requests at operation boundaries; the edge
keeps target names, adapter ids and per-session node tables only. Every
session is an adapter record in the host (`adapter.attach`/`detach`/
`inspect` exist only on the bridge, never over stdio), `memory.report`
counts `owners.adapters`, and `target.close`/`session.close` detach the
adapters of the closed target and report `adapters_detached`; a command on
a session whose target closed is `-32000 target closed; adapter detached`.
The qualification matrix
([`cdp-qualification-0.0.1.json`](cdp-qualification-0.0.1.json)) was
committed before the edge: qualified methods are `Browser.getVersion`,
`Target.getBrowserContexts/setDiscoverTargets/setAutoAttach/getTargets/attachToTarget/detachFromTarget`,
`Page.getFrameTree`, `DOM.getDocument/querySelector/resolveNode` and
`Runtime.callFunctionOn` (click only); `Page.enable`, `Page.navigate`,
`Runtime.enable`, `Network.*`, `Fetch.*`, `Performance.enable`, `Log.enable`
and the rest are explicit `-32601`, and no `Runtime.ExecutionContextId` is
ever emitted. Two revisions after the freeze are recorded in the matrix:
events are written before their command's response (`targetCreated`,
`attachedToTarget`) because the client builds its target list and sessions
from them, and adapter counts equal the sessions the client holds (one per
auto-attached target plus one per explicit `createCDPSession`). The named
client is `puppeteer-core 24.15.0` on Node.js v26.7.0 (integrity in the
matrix), used only through `puppeteer.connect`, `browser.targets`/
`waitForTarget`, `target.createCDPSession`, `session.send`, `session.detach`
and `browser.disconnect`; `target.page()` and every puppeteer Page API are
outside the claim because their initialization needs the `-32601` methods.
No Chromium, Electron or Playwright statement follows from any of this.

Reproduction (the client package lives under the ignored `target/labs/d4`;
it is installed there from the pinned version and its integrity is the one
in the qualification matrix, so a machine that has the package in its npm
cache can restore it offline):

```sh
python3 labs/native-dom/cdp-frame-tree-court.py \
  --binary labs/native-dom/target/release/native-dom-control \
  --receipt labs/native-dom/evidence/native-dom-control-0.0.2-cdp-frame-tree.json
```

Evidence (`native-dom-control-0.0.2-cdp-frame-tree` receipt, 58 of 58, the
same 29 checks under the default allocator and the opt-in arena; the court
drives stdio itself with contract-validated requests and the client through
`puppeteer-frame-tree.mjs`): the client is the pinned version; `connect`
succeeds; `waitForTarget` finds the exact native target id and `targets()`
lists exactly the two native targets; `createCDPSession` attaches and the
host holds three adapters (two auto-attached, one explicit) with no new
owner; `Page.getFrameTree` returns one main frame with no children whose id
`cdp_frame_1` differs from the native `frame_1`; `DOM.getDocument` reports
the native node count; `querySelector('a')`, `resolveNode` and
`callFunctionOn` click the link and stdio sees the same-frame navigation
(revision +1, generation 2, new realm, same frame), the pre-navigation
native reference is `stale_revision`, the retired realm `not_found`, the
pre-navigation CDP object fails typed; the CDP frame id survives the
navigation; the re-fetched document has the new node count and a click on
its button is accepted without navigating; a session on B sees a different
adapter id and never A's; four adapters live with both explicit sessions;
`Page.navigate` and `Runtime.enable` are `-32601`; `target.close` over stdio
detaches both of A's adapters and the session's next command fails typed;
detaching B's explicit session and `browser.disconnect` bring adapters to
zero while the host keeps serving; after the closes every owner is zero.
Footprint (bytes) with the edge listening: empty 1,425,720 (default) and
1,442,104 (arena) against 1,343,800 without the edge, so the listener and
the stdin reader thread cost about 65–98 KB; two targets with sessions
attached 3,162,424 / 2,474,344; after the closes 3,457,336 / 2,130,232. A
client fact recorded by the court: this puppeteer-core version does not
populate `ProtocolError.code`, so typed failures are matched on the edge's
message text (`Method not found`, `target closed; adapter detached`).

Gaps: one client and version; loopback only, one connection at a time;
one main frame, no realm projection, no navigation events; `target.page()`
unsupported; a connection that dies mid-command releases its adapters only
when the read fails or times out (30 s).

Verdict: `keep`. D4 moves to "one engine host observed by a named external
client through `Page.getFrameTree`" on this route only; it stays open for
Playwright, for page-level APIs and for engine hosts other than this one.
G1, G3, P6 and G6 stay open.

### Engine-backed profiles (P6 slice: keychain envelope, cookie jar, localStorage)

Hypothesis: the native host can own a bounded persistent profile store
(cookies and `localStorage`) whose at-rest protection, cookie semantics and
crash behaviour were decided before the code (D1–D6 in
[`profile-design-0.0.1.md`](profile-design-0.0.1.md)), without moving the
default allocator, the network policy or the pre-registered memory caps.

Scope: `--profile-root DIR` enables the store. A persistent profile is a
0700 directory holding one sealed record (`profile.v1.sealed`) and a
`writer.lock`; the record is XChaCha20-Poly1305 with a per-profile random
32-byte data key, a fresh 24-byte nonce per write, and additional data that
binds the store format, the protocol tag and the canonical profile identity.
The data key is wrapped by a master key that lives only in the macOS
Keychain: generic password, service
`minicon-surf.native-dom.profile-master-key`, account = the first 32 hex
digits of SHA-256 of the canonical profile root, default keychain, no
iCloud sync, user interaction disabled (`SecKeychainSetUserInteractionAllowed`
false). A locked, denied or missing keychain makes persistent `profile.create`
and `session.open` fail closed with `unsupported_capability`; ephemeral
profiles keep working. Keys are zeroized on drop. Every committed mutation
is written through: temp file, `fsync`, atomic rename, directory `fsync`; a
failed commit rolls the working copy back, reports `internal` with
`storage_commit_failed` to the caller, and leaves the profile read-only for
the rest of the host lifetime. Cookies follow the RFC 6265 subset of the
design's matrix: `Domain` only when it equals the request host (D2), no
`Secure`/`SameSite=None` on this `http` cell (D3), session cookies in a
volatile jar shared by the profile's sessions in sequence and never written
(D4). Cookies are host-scoped, not port-scoped, as in RFC 6265. Each fetch
carries the jar's matching cookies and stores every `Set-Cookie`;
`document.cookie` and `localStorage` are mirrors seeded into the realm and
drained after each script turn. `profile.storage.*` writes go to a control
pseudo-host (`control`) and a control origin, never to a page's origin. One
live session per profile (the 0.0.1 journey budget); eight sessions per
host. `MINICON_SURF_PROFILE_STORE=envelope-keyfile-experiment` selects the
B2 keyfile source for tests without a keychain; its receipts are labelled
and never marked `observed`.

Reproduction (the court starts its own hermetic server, uses only fake
values such as `court-alpha-7f3a`, and never reads a real browser profile):

```sh
python3 labs/native-dom/profile-court.py \
  --binary labs/native-dom/target/release/native-dom-control \
  --receipt labs/native-dom/evidence/native-dom-control-0.0.2-profile.json
```

Evidence (`native-dom-control-0.0.2-profile` receipt, mode
`envelope-keychain`, 80 of 82, the same 41 checks under the default
allocator and the arena): the feature-off baseline is measured first; the
store costs 294,912 / 278,528 bytes of empty footprint and 868,352 / 851,968
bytes of empty RSS (caps 524,288 and 1,048,576); an empty persistent profile
accounts 0 bytes (cap 65,536); each of `alpha`, `beta` and ephemeral
`scratch` receives its own cookie and the echo endpoint sees only that
cookie; `localStorage` is per profile and per origin; an `HttpOnly` cookie
is sent and hidden from `document.cookie`; `Secure`, foreign `Domain`,
`SameSite=None`, `__Host-` and `Partitioned` are refused on receipt and
never sent (five rejections counted); `Max-Age=0` deletes; a session cookie
set by session A is sent by session B after A closes; one page-driven
persistent cookie is one record write of 910 bytes; with the profile
directory made unwritable the page's write reports `internal` /
`storage_commit_failed`, the record bytes are unchanged, the value is not
readable and the profile stays read-only; a 4,097-byte cookie and the 33rd
storage key are `resource_limit`; `profile.inspect` and `memory.report`
expose counts and budgets and never a value; no file under the root
contains the fake value; records and locks are 0600 in 0700 directories;
after a restart with `beta`'s record overwritten, `alpha` keeps its cookie
and storage, its session cookie is gone, `scratch` is gone, `beta` lists as
unavailable and opens as `not_found`; a second host gets `profile_locked`
until the owner closes. Footprint (bytes, default / arena): empty with the
store 2,277,712 / 2,261,328; three profiles with three open targets
5,521,864 / 5,538,296; with fixture data and budget negatives, after about
twenty target open/close cycles, 6,111,688 / 5,849,592; after every close
6,111,688 / 5,194,184; the restarted host holding the persisted `alpha` with
one open target 4,211,048 / 3,604,864.

The two failing checks are the frozen "live footprint stays well below the
Lightpanda single-server empty footprint" criterion, which the court froze
as "below half of 8,356,392": the churned host measures 6,111,688 (default)
and 5,849,592 (arena). A separate probe attributes the number: a target
costs about 1.0 MB for the first realm and about 0.43 MB for each further
one on this fixture, with or without the store, and the default allocator
keeps closed targets' memory (the retention already attributed above), so
the excess is realm and churn cost rather than store cost. Per D6 the cap
does not move; the slice is not marked `observed`.

Court amendments after the freeze, all mechanism fixes recorded in the
script: the cookie fixture decodes every percent-escape (the first version
left `%3D` literal, so `Max-Age` and `Path` never reached the jar); the echo
page's fetch settles during load, so the court reads the snapshot before
waiting for a later revision; the write-amplification step uses a page whose
response sets a cookie instead of a page that wrote nothing; the
`profile.inspect` count follows the D4 step (four cookies, two persistent);
the restarted host's footprint is recorded as a supplementary number, not a
gate.

Dependencies added for this slice (versions and registry checksums are
pinned in `Cargo.lock`; security updates are the lab owner's responsibility
and no automated advisory check runs in this repository yet, which is
recorded as a gap; the release binary grows from 3,462,352 to
3,764,000 bytes and the lock from 100 to 122 packages, four of which are
Windows/WASI-only entries that do not compile here; recorded, not a memory
gate):

| crate | version | license | role |
|---|---|---|---|
| `chacha20poly1305` (+ `aead`, `chacha20`, `poly1305`, `cipher`, `universal-hash`, `inout`, `opaque-debug`, `subtle`) | 0.10.1 | Apache-2.0 OR MIT (`subtle` BSD-3-Clause) | XChaCha20-Poly1305 record and key wrapping |
| `getrandom` (+ `rand_core`) | 0.2.17 | MIT OR Apache-2.0 | data keys, nonces, master key |
| `sha2` | 0.10.9 | MIT OR Apache-2.0 | keychain account and identity binding |
| `zeroize` | 1.9.0 | Apache-2.0 OR MIT | key material cleared on drop |
| `fs2` | 0.4.3 | MIT/Apache-2.0 | `flock` writer lock |
| `security-framework` (+ `security-framework-sys`, `core-foundation`, `core-foundation-sys`) | 3.7.0 | MIT OR Apache-2.0 | macOS Keychain generic password (macOS target only) |
| `libc` (helper experiment only, commit `906884b`; not a dependency of the restored host) | 0.2.189 | MIT OR Apache-2.0 | `setrlimit(RLIMIT_CORE, 0)` and the helper's descriptor whitelist check |
| `rustls` (+ `rustls-webpki`, `rustls-pki-types`, `untrusted`, `once_cell`) | 0.23.43 | Apache-2.0 OR ISC OR MIT (webpki, untrusted ISC) | TLS 1.3/1.2 client, certificate path and SAN verification in Rust (pinned-roots HTTPS slice) |
| `ring` | 0.17.14 | Apache-2.0 AND ISC | cryptographic primitives for rustls: C and perlasm inside, not pure Rust |

Keychain ACL and the no-UI mode, reviewed after the verdict
(`native-dom-control-0.0.2-keychain-acl-probe` receipt): the item the host
creates carries the Security framework's default ACL for an ad-hoc,
linker-signed binary: one application entry whose requirement is the
creating build's `cdhash`, a `partition_id` of that same `cdhash`, and no
ACL edits by the host. Probes with user interaction disabled, one profile
root, two builds of the committed source that differ in one string (so in
`cdhash`): the creating build reads the key again after a restart, and so
does a copy of it at another path (the requirement is the code hash, not
the path); the rebuilt binary at the same path gets `-25293`
(`errSecAuthFailed`) with no prompt, lists the profile as unavailable with
reason `keychain unavailable`, refuses `session.open` as `not_found` and
refuses a new persistent profile as `unsupported_capability`, and leaves
the item and the sealed record untouched; the original build then works
again. The no-UI mode is therefore a fail-closed guarantee, not an
unattended-deployment guarantee: a locked keychain, another user session,
or any rebuilt or re-signed host refuses. Unattended use across rebuilds
would need a signing identity with a stable designated requirement or a
one-time interactive grant outside the host; neither is claimed. Court
hygiene recorded at the same time: every court run created one keychain
item per temporary profile root and never deleted it; the sixteen stale
items on the recording machine were deleted by hand, the attribution court
deletes its own, and the frozen v1 court is left as it is.

Attribution of the churned footprint (verdict: keep v1 and its 80/82,
attribute before any fix; `native-dom-control-0.0.2-profile-attribution`
receipt, read-only, no gate). [`profile-attribution-court.py`](profile-attribution-court.py)
replays the profile court's target sequence (28 opens, 28 closes, the fault
injection left out) in a fresh process per run, one warm-up plus seven
measured, under the default allocator and the arena, and samples physical
footprint, RSS and `memory.report` (owners, libmalloc in-use and allocated)
after every open and close. Four arms: `off-equal-churn` (no
`--profile-root`, three ephemeral profiles, the same pages), `store-no-data`
(the store on, the same number of opens but every page the cookie-free echo
page, no control writes), `store-data` (the profile court's pages, cookies,
storage and budget writes) and `restart-steady` (a fresh host opening the
persisted `alpha` and one storage page). Medians in bytes, default
allocator / arena:

| stage | off-equal-churn | store-no-data | store-data | restart-steady |
|---|---|---|---|---|
| empty | 1,917,264 / 1,917,264 | 2,179,408 / 2,179,432 | 2,179,408 / 2,163,024 | 3,031,400 / 3,015,016 |
| profiles created (first keychain call in the store arms) | 1,999,184 / 1,999,184 | 4,276,680 / 4,276,680 | 4,276,680 / 4,260,296 | – |
| churned final | 4,489,552 / 3,572,096 | 5,980,616 / 5,210,616 | 6,226,376 / 5,718,520 | 3,998,056 / 3,555,712 |
| after every close | 4,489,552 / 2,916,688 | 5,980,616 / 4,555,208 | 6,226,376 / 5,063,112 | 4,014,440 / 3,309,928 |
| libmalloc in use, empty → after every close | 105,440 → 123,296 | 122,880 → 688,448 | 122,880 → 693,184 | 388,064 → 395,104 |
| closes that released footprint, of 28 (restart: of 1) | 0 / 28 | 0 / 28 | 0 / 28 | 0 / 1 |

Where the bytes are, default allocator, at the churned point:

- The named owners at the churned point are 450,609 bytes (QuickJS
  449,440 for the three live realms, host-accounted profile bytes 1,023,
  document bytes 146); after the closes every owner is zero and stays zero
  in all 64 runs.
- Lifecycle retention without the store: the feature-off arm ends the
  timeline at 4,489,552 and keeps it after every close while libmalloc's
  in-use bytes return to within 17,856 of empty. The 2,572,288 retained
  bytes are freed blocks the default zone keeps in its regions
  (`size_allocated` 12,582,912 → 25,165,824, never shrinking; `memory.trim`
  releases 0), the attribution already recorded above. Under the default
  allocator no close in any arm or run ever lowered the footprint (the
  first non-releasing close is close 1 in all seven runs of every arm),
  so the floor is set at the first close and rises with each realm's
  high-water. Under the arena every one of the 28 closes releases.
- The store: enabling it costs 262,144 at empty. The first keychain call
  (the first persistent `profile.create`, before any page) costs 2,097,272
  of footprint in one step (2,277,496 over the feature-off arm at the same
  stage), 542,560 of it libmalloc in-use that never
  returns (688,448 after every close against 123,296 without the store)
  and the rest non-heap pages (the Security and CoreFoundation frameworks'
  data, securityd's XPC and caches); later keychain calls add about 5 KB
  each. The records, jar and realm mirrors are the small part: the data
  arm differs from the no-data arm by 245,760 (default) / 507,904
  (arena) at the churned point, and the profile owner accounts 1,023.
- Sum at the churned point, default allocator: store-data 6,226,376 =
  feature-off equal churn 4,489,552 + store 1,491,064 (of which 262,144
  enable and about 1.2 MB the one-time keychain first use as it overlaps
  with churn reservation) + data 245,760. After every close the same
  6,226,376 remains: 4,489,552 lifecycle reservation, 1,736,824 store and
  data.
- The restarted host pays the keychain first use at `--profile-root`
  enable (empty 3,031,400 against 2,179,408) and then holds one realm; its
  3,998,056 is a diagnostic, not the gate.

Consequences for the unmet criterion (half of 8,356,392 = 4,178,196):
under the default allocator the feature-off host with equal churn already
measures 4,489,552, so no change to the store can bring v1's churned
total-live point under the line; that part belongs to the allocator
retention (G3). Under the arena the churn side is 3,572,096 and the store's
one-time keychain cost is what crosses the line. The single fix candidate
this attribution supports, not implemented here, is to take the Security
framework out of the host process: wrap the data key once at create/open
(the wrapped blob is stable, so committed mutations re-seal the record with
the cached data key and never touch the master key), and fetch the master
key at create/open through a short-lived helper process of the same binary
that exits after handing the 32 bytes over a pipe. Pre-registered criteria
for that candidate: the `profiles created` step must cost at most 524,288
over the feature-off arm and libmalloc in-use after every close must
return to within 65,536 of the feature-off arm; v1's churned total-live
point must fall by at least 1 MB on both allocators; the profile court's
other 80 checks, the at-rest scan, the write-through and fault-injection
checks, the keychain ACL probe, 27/27, 35/35, 62/62 and 58/58 must stay
green under both allocators; no allocator micro-benchmark counts as a
reason. Even then the default-allocator cell stays above the line, so the
candidate can only close the arena cell of the criterion; if the helper
turns out to need more than a fork/exec of the existing binary and one
pipe, the slice stays `narrow` and P6 work moves to another gap.

Reproduction:

```sh
python3 labs/native-dom/profile-attribution-court.py \
  --binary labs/native-dom/target/release/native-dom-control \
  --receipt labs/native-dom/evidence/native-dom-control-0.0.2-profile-attribution.json
```

The approved experiment: a bounded Keychain helper (arena cell). After the
attribution, one candidate was approved with fixed constraints (design
section 8c): the host generates the data key and a short-lived helper
process of the same signed binary, spawned through `std::process::Command`
with an absolute program path and no pre-exec closure (the conditions under
which Rust 1.97's standard library uses `posix_spawnp` on Apple targets and
never falls back to `fork`), fetches the master key and returns the
authenticated wrapped data key over an anonymous pipe in a fixed-length,
versioned envelope; the wrapped key is stored unchanged, so committed
mutations never touch the Keychain; the helper refuses to serve if any
descriptor beyond stdio is open, both sides refuse core dumps and zeroize,
a 10 s deadline kills and reaps as failure cleanup, and any deviation fails
closed. [`profile-helper-court.py`](profile-helper-court.py) was frozen
before the code with six criteria and samples the complete process tree at
about one kilohertz through every run. Result
(`native-dom-control-0.0.2-profile-helper` receipt, 58 of 60, default
allocator / arena, medians of seven):

| criterion | measured | limit | holds |
|---|---|---|---|
| C1 `profiles_created` step over feature-off | 81,920 / 81,920 | ≤ 524,288 | yes |
| C2 libmalloc in-use after every close over feature-off | 2,272 / 2,368 | ≤ 65,536 | yes |
| C3 churned total-live drop against the in-process build | 1,802,360 (6,275,528 → 4,473,168) / 2,064,504 (5,767,672 → 3,703,168) | ≥ 1,048,576 | yes |
| C4 complete-tree peak while a helper is alive vs the in-process peak | 5,735,192 vs 4,391,392 / 5,735,168 vs 5,423,632 | not above | **no** |
| C5 descendants after any operation; timeout kills; failures; counters vs sightings | 0; 0; 0; two helpers per run, role `keychain-helper`, same binary, lifetime median 3 ms (max 11.2 ms) | all zero and consistent | yes |
| C6 clean exits, owners at zero | 48 of 48 runs | all | yes |

The helper build also passes the v1 profile court 81 of 82 (the arena
total-live check now holds at 3,801,472; the default cell stays at
4,440,400 against the 4,178,196 line, as the attribution predicted), the
journeys 27/27 and 35/35 under both allocators, the frame-realm court
62/62, the CDP court 58/58, and the Keychain ACL probe repeated on two
builds of it (a rebuilt `cdhash` is refused with `-25293`, fail closed).

Why C4 fails, and why it cannot pass in this shape: the in-process peak is
the host plus the Security framework's first use (about 2.1 MB); the tree
peak is the host without that cost (2,179,408) plus a whole second process
of this binary that pays the same framework cost on top of its own runtime
baseline (helper peak 3,367,320 / 3,096,984). The difference, about 1.35
MB, is the helper's process baseline, which a helper that must be the same
signed binary cannot shed. Transient peak and recovered steady state are
therefore both recorded: the steady state improves by 1.8 to 2.1 MB and the
transient peak worsens by 0.3 to 1.3 MB for about 3 ms per create or open.

Verdict: the experiment fails its frozen C4, so per the approval it changes
nothing: the in-process path stays the default, the implementation and its
evidence are kept in history (commit `906884b` carries the helper build;
the next commit restores the in-process host and the receipts made with
it), and P6 work moves to another gap. The arena cell of
the P6 slice therefore stays `failed`/`narrow`. The measurements of
constraints 2 and 5 (kilohertz tree sampling, helper lifetime and
descendant checks) are reusable for any later out-of-process design.

Gaps: macOS Keychain only (a second platform key source is a P6 gap); the
`http` cell refuses `Secure` and `SameSite=None` as a cell limit, not a
design limit; no public suffix list; no cache, history, downloads,
permissions or readonly/COW profiles; the keychain item is created on first
use by the current user with no access-control prompt and no ACL beyond the
default; the D6 total-live criterion is unmet on the churned court host.

Verdict: `narrow`. The store's own caps hold and every semantic check
passes, but the frozen total-live criterion fails, so the slice is recorded
as `failed` rather than `observed` and P6 stays open with G1, G3 and G6.

### Pinned-roots HTTPS (rustls + ring, opt-in)

Hypothesis: the native host can serve `https` origins under an explicit
pinned-root policy with the TLS stack the candidate court selected
(`labs/tls-court`, S1–S10: rustls 0.23.43 with the ring provider), without
moving any existing cap, without a system root store, and without breaking
the P6 v1 court. The stack is Rust for the TLS state machine, the
certificate path and name verification (`rustls`, `rustls-webpki`,
`rustls-pki-types`) and C plus perlasm for the primitives inside `ring`
(31,209 C and 199,234 perlasm/assembly lines in the vendored source); it is
not pure Rust and this README and the plan say so wherever the route is
described.

Scope (design section 2, cdx-k68's verdict boundaries 1–4): HTTPS exists only
when `--pinned-root FILE` (public certificate PEM, repeatable) is given and
the origin is on the explicit allowlist (`--allow-origin https://host:port`).
Without a pinned root every `https` fetch is `unsupported_capability`
`tls_no_pinned_roots`; no system or bundled roots are ever consulted and no
public-web claim is made. Pinned-root input is bounded: at most 8 files, 16
KiB per file, 64 KiB in total, 16 certificates; a file carrying a
private-key block is refused, and errors name counts, never paths or
contents. The ring provider is selected once, where the roots load, and
nowhere else. TLS 1.3 preferred and 1.2 accepted, ALPN `http/1.1` only (a
server that negotiates nothing or `h2` is refused `tls_alpn`), the
certificate must match the URL host by DNS or IP SAN (`tls_hostname_mismatch`
otherwise, `tls_untrusted_root` when the chain does not end in a pinned
root, `tls_protocol` for TLS ≤ 1.1), SNI and the verified name are the
original URL host, the exact address is authorized before the connect as on
`http`, every redirect hop is authorized again and `https` → `http` is
refused as `redirect_downgrade` before the plain target is even looked at.
The handshake, reads and writes run under the same absolute deadline through
the socket timeouts, and the framing, header, body, redirect, pending and
per-target caps are the `http` ones. Each profile owns its session cache
(`ClientSessionMemoryCache` with 16 entries: rustls rounds 8 or fewer to a
single slot that its eviction empties at once, as the candidate court
found), never shared across profiles, gone at exit. `Secure` cookies are
accepted only from a verified `https` origin and sent only to one, hidden
from `http` documents of the same host; `SameSite=None` still needs
`Secure`; `Domain` stays exact-host; the broader same-site context stays a
recorded loss. A failed https navigation is a typed failure that leaves
frame, generation, realm, revision and jar untouched. Owners:
`memory.report.owners.network.tls` (enabled, pinned roots, provider,
cache bound, live connections, handshakes, resumed, refused, TLS 1.3 and
1.2 counts, sums of live and retired targets) and `target.inspect
network.tls` when the slice is enabled; the feature-off shapes are
unchanged.

Reproduction (disposable fixtures generated per run, nothing committed):

```sh
python3 labs/native-dom/https-court.py \
  --binary labs/native-dom/target/release/native-dom-control \
  --receipt labs/native-dom/evidence/native-dom-control-0.0.2-https.json
```

Evidence (`native-dom-control-0.0.2-https` receipt, 74 of 74, the same 37
checks under the default allocator and the arena, feature off against
enabled): the feature-off host reports TLS disabled and refuses an https
target as `tls_no_pinned_roots`; the enabled host loads the representative
page and its script over TLS 1.3 from the pinned origin and negotiates TLS
1.2 with a 1.2-only origin; wrong name, unpinned issuer, ALPN h2-only,
downgrade redirect, private-address redirect, redirect loop, body cap,
header cap and deadline are typed refusals with no path, certificate or
crypto internal in the error, and a header section of exactly the cap
(16,384 bytes) is accepted while one byte more is refused; TLS 1.1-only is refused `tls_protocol`
(the local OpenSSL still serves it at security level 0); https redirects
within the cap are followed; the second https fetch of a profile resumes
and another profile's first fetch is a full handshake; a `Secure` cookie
set over https returns over https, never reaches the http origin of the
same host and is invisible to an http document; `SameSite=None; Secure` is
accepted over https while `SameSite=None` alone, `Secure` over http and a
foreign `Domain` are refused; a link click to a wrong-name origin fails
typed and leaves frame, generation, realm, revision and jar unchanged;
owners are zero after every close and the host stays one process. Host
increments against the feature-off host (bytes, default / arena):

| increment | measured | cap |
|---|---|---|
| H1 enabled empty over feature-off empty | 0 / 32,768 | ≤ 524,288 |
| H2 first https target over first http target of the same page | 163,840 / 262,144 | ≤ 1,048,576 |
| H3 eight https targets over eight http targets, per target | 32,768 / 79,872 | ≤ 131,072 |
| H4 post-close libmalloc in-use, enabled over feature-off | 44,848 / 12,896 | ≤ 65,536 |

Footprint (bytes, default / arena): feature-off empty 2,048,336 / 2,048,336
and enabled empty 2,048,336 / 2,081,104; first http target 3,211,600 /
2,670,952 and first https target 3,375,440 / 2,933,096; eight http targets
5,980,496 / 5,866,000 and eight https targets 6,242,640 / 6,504,976; after
every close 5,996,880 / 2,883,920 (off) and 6,242,640 / 3,490,128 (on),
the default-allocator numbers being the zone reservation attributed earlier.
Binary: 3,764,000 → 5,266,608 bytes (+1,502,608); lock 122 → 138 entries,
six compiled here (`rustls` 0.23.43 Apache-2.0 OR ISC OR MIT, `ring` 0.17.14
Apache-2.0 AND ISC, `rustls-webpki` 0.103.15 ISC, `rustls-pki-types` 1.15.1
MIT OR Apache-2.0, `untrusted` 0.9.0 ISC, `once_cell` 1.21.4 MIT OR
Apache-2.0) and ten Windows-only lock entries that do not compile on this
cell; security updates of these crates are the lab owner's responsibility
and no automated advisory check runs yet (gap). The candidate court's
closure count applies: 94,338 Rust lines with 279 `unsafe` occurrences plus
ring's C and perlasm.

Regressions on the same binary: the P6 v1 profile court stays 80 of 82
with the same two total-live failures, the journeys 27/27 and 35/35 under
both allocators, the frame-realm court 62/62 and the CDP court 58/58. The
network court carries one recorded amendment: the https negative keeps its
code `unsupported_capability` and now expects the reason
`tls_no_pinned_roots` instead of `scheme`.

Court amendments after the freeze, mechanism only, recorded in the script:
the empty sample follows the host's first request (a host still starting
measured 81,992 bytes); both hosts use ephemeral profiles so the keychain's
one-time first-use cost cannot enter the H deltas; the click carries the
node reference as the contract requires; the redirect landing is read from
any node role; the private-address redirect uses an https target because an
https origin refuses the fixture's http target as a downgrade first; the
header-cap fixture sends 40 KB, which exposed a real bound defect: the cap was checked
per 8 KiB chunk only while the header end was not yet seen, so a 20 KB
header block whose terminator arrived in the same chunk passed. Fixed
before any push (cdx-k68's condition): the header section up to and
including the terminator is compared with the cap once the terminator is
found, whatever the chunking, and the host-built request head is bounded
by the same cap (`request-header-bytes`); unit tests cover cap−1, cap and
cap+1 with the terminator in the same chunk, across chunks, in many small
chunks and a single over-long header line, and the court probes cap−1, cap
and cap+1 through `/headers?section=N`. The cross-profile check counts
exactly one full handshake among a target's three fetches. One unit test
(`arena_is_unmapped_only_when_the_last_holder_drops`) failed once during
a parallel `cargo test` with `left: 2, right: 1`: it asserted that a
process-global unmap counter grew by exactly one while other arena tests
drop arenas in parallel, a test-isolation defect and not an allocator
invariant. `stress-tests.sh` repeats the suite; the pre-fix binary did not
reproduce it in 300 parallel and 100 single-threaded runs, so the rate is
below 1 in 400 here and the root cause rests on inspection; the test now
watches its own arena through a weak handle and reads the global counter
as a lower bound only, the zone test got the same treatment, and the
fixed suite is green in 300 parallel and 100 single-threaded runs. No
rustls provider is installed globally (each client carries its provider),
tests read no environment variable, and no random seed is involved.

Gaps: pinned test roots on loopback only; no system roots, revocation, CT,
client certificates, HTTP/2, HSTS or public-web statement; ring's C and
perlasm remain inside the closure; one platform and fixture set; leak
absence is not claimed.

Verdict: `keep` as an opt-in, explicit-policy slice. The P6 v1 court and
its verdict are unchanged; G1, G3, P6 and G6 stay open.

### Persistent Secure cookies across a restart (P6 × HTTPS)

Hypothesis: a `Secure` cookie set by a verified https origin into a
persistent profile survives a host restart with every rule intact, while
volatile session cookies and expired cookies do not, and nothing about a
send decision is stored in the record. Court frozen before it ran:
[`secure-cookie-court.py`](secure-cookie-court.py) (disposable fixtures,
fake values, keychain items deleted per run, default allocator and arena).

Mechanism added for the court: `MINICON_SURF_CLOCK_OFFSET_SECONDS`, a fixed
clock offset in seconds read once at start (default 0), so expiry and
deletion are observed under an injected clock instead of sleeps.

Evidence (`native-dom-control-0.0.2-secure-cookie` receipt, 78 of 78): in
host A persistent `alpha` sets over https a persistent Secure cookie, a
persistent plain cookie, a volatile Secure session cookie, a 60-second
Secure cookie and a `Path=/other` cookie; a cookie with `Expires` in the
past is deleted on receipt and `Max-Age=0` deletes an existing one; the
https echo carries the four matching cookies and never the path-scoped, the
past-expired or the deleted one, the http echo of the same host carries only
the plain one; `profile.inspect` counts four persistent and one volatile
cookie and shows no value; the record's clear-text envelope holds only the
eight sealed fields. A wrong-name origin and an unpinned origin are
`permission_denied`, `Secure` over http is dropped on receipt and a link to
a wrong-name origin fails typed: after all of them the jar counts, the
record bytes and their hashes are unchanged and the https echo still sends
the same cookies. `beta` sets its own cookies at the same URLs and neither
profile ever carries the other's. At rest no file under the root contains
any fixture value, any cookie name or the localStorage marker. Host B, the
same root and pinned root at clock +120 s, unseals the record through the
keychain again, lists both profiles, and alpha's https echo carries the
persistent Secure and plain cookies only: the volatile cookie was never
persisted, the 60-second cookie is expired and the `Path=/other` cookie
does not match; the http echo carries only the plain one; an http document
does not see the Secure cookie; the same server under its `localhost` name
receives none of the `127.0.0.1` cookies, so host, path and `Secure` are
matched by the current rules at send time; beta stays isolated; the
restarted host's first https fetch is a full handshake. Host C, the same
root without a pinned root, gets `unsupported_capability
tls_no_pinned_roots` for https and sends only the plain cookie over http,
so a persisted Secure cookie stays locked without TLS; it counts three
persistent cookies because host B's http storage write committed the record
after dropping the expired one, and zero volatile. Owners are zero after
every close, no host spawns a descendant. Footprints as diagnostics
(default / arena): host A live 5,521,864 / 4,948,448, host B live
4,637,032 / 3,948,904.

Court amendment after the freeze (a stale count): the host C expectation
was written for two cookies before the persistent/volatile, short-lived and
path-scoped cookies were added; the recorded semantics are the ones above.

Gaps: pinned loopback roots only; the profile-court cell that refuses
`Secure` over `http` (D3) is unchanged and is now a scheme rule rather
than a cell limit; the same-site context across schemes stays a recorded
loss; one platform and fixture set.

Verdict: `keep`. G1, G3, P6 and G6 stay open.

### G3 surface process (macOS prototype, direct Cocoa child)

**Headless by default.** Nothing in this lab creates a window unless a run
opts in twice: the court flag `--visual` and the environment
`MINICON_SURF_ALLOW_VISIBLE_COURT=1`. The host takes `--visual 1` and
refuses to start when the environment is missing; without the opt-in
`surface.show` is refused as `unsupported_capability`
(`visible_surface_not_enabled`) unless a court-only no-AppKit child mode
is set; the child in window mode exits 68 before touching AppKit when the
environment is missing; an opted-in window is ordered front without
becoming key (`orderFrontRegardless`, accessory policy) so it never steals
focus. `surface-court.py` is entirely visual and does not run without the
opt-in (exit 3, `unverified`, no receipt); `surface-attribution-court.py`
and `surface-snapshot-attribution-court.py` run their no-AppKit cells by
default and add or switch to the real-window child only under the opt-in.
Run a visual court once by hand, never repeatedly in the background.
[`surface-headless-court.py`](surface-headless-court.py) proves the rule
(receipt `native-dom-control-0.0.2-surface-headless`, 17 of 17): the
default snapshot court runs under a 50 ms window-list watch with no window
owned by the child at any sample, the child maps neither AppKit nor
CoreGraphics (`lsof`), the window list after equals the one before, the
fail-closed paths above hold, a host killed while a headless surface is
shown leaves no child within two seconds, and a court interrupted with
SIGINT exits 130 with no residual process, window or receipt. The rule
was added after the owner saw windows flashing from automated runs of the
attribution courts (which spawned the real child by default and were
rerun in the background); the receipts recorded before it are marked
below as visual runs and are not rerun.

Hypothesis (design and court frozen first, `surface-design-0.0.1.md` and
`surface-ipc-0.0.1.md`): a separate minimal window process attached to one
live target can make headed and headless runtime states of that target,
with the host keeping profile, session, target, frame, realm, revision,
DOM and network, the child receiving only bounded frames and returning
only bounded input, `hide` ending the child so the host returns to its
headless footprint, and real human input reaching the page while the CLI
and a CDP session keep observing it.

Scope: `surface.show {target}` spawns `native-dom-surface` (`labs/native-dom-surface`,
absolute path given once through `--surface-binary`; `std::process::Command`
with no pre-exec closure, cwd, uid or gid, so the standard library uses
`posix_spawn` and never forks the multi-threaded host); the host paints
the target's semantic snapshot as rows (role bar, a 5 × 7 bitmap-font
name, `scroll_y` offset, a row → node hit map) into a 640 × 400 BGRA frame
labelled `bounded-semantic-painter`, which is not a layout or CSS renderer;
`HELLO`, `FRAME`, `CLOSE` and `READY`, `FRAME_ACK`, `INPUT`, `ERROR`,
`CLOSED` travel over the child's stdin and stdout pipes in 20-byte-header,
bounded, generation- and sequence-stamped messages with a 2,000 ms ready,
1,000 ms ack and 1,000 ms close deadline, one frame in flight, and a kill
plus reap counted as failure cleanup after a missed deadline. Input from the
child is a third source of the host's multiplex loop next to the stdio door
and the CDP bridge: applied FIFO per surface while the host is idle,
dropped and counted when its spawn generation is no longer current, a click
resolved through the hit map to the existing click path (valid only against
the frame's revision), a scroll moving the host-owned `scroll_y` and
advancing the revision as the synthetic host does. `surface.hide` answers
`teardown {exit: protocol|killed|gone, reaped, ms}`. Public results carry
no window number, coordinate, capture, hit map, pid or handle; a court-only
`--surface-court-file` (0600, under the court's mktemp, removed at exit)
receives those plus `input_applied`, `repainted`, `hidden` and `child_exit`
events. `memory.report.owners.surfaces` reports objects, frame bytes
(the whole mapping while it exists, 0 after hide), the process counters
and `frame` (backing `anonymous_mmap`, reserved, touched and live bytes,
regions mapped and unmapped and the bytes returned so far, the host's
virtual size and physical footprint); `target.inspect` gains `surface`
and `scroll_y`.
Teardown order at `target.close` and `session.close` is adapters →
surfaces → target. The host itself links no AppKit: `native-dom-surface`
is a path dependency without its `window` feature, so the host gets the
codec only.

Reproduction (the court posts real CoreGraphics input only after it has
confirmed that the topmost window at the point belongs to the surface
child, captures the child's own window only, and needs Accessibility trust
for the terminal; the window shows the painter's rows, never desktop
content):

```sh
cargo build --release --locked --offline --manifest-path labs/native-dom-surface/Cargo.toml --features window
# MANUAL, VISUAL: this shows real windows. Only with the owner's permission
# for this run, once, in the foreground, never from a chain or a script.
MINICON_SURF_ALLOW_VISIBLE_COURT=1 python3 labs/native-dom/surface-court.py --visual \
  --binary labs/native-dom/target/release/native-dom-control \
  --surface-binary "$PWD/labs/native-dom-surface/target/release/native-dom-surface" \
  --receipt labs/native-dom/evidence/native-dom-control-0.0.2-surface.json
```

The recorded receipt is a historical manual measurement (host
`32343eb5…`); the default reproduction commands of this lab never run it.

Evidence (`native-dom-control-0.0.2-surface` receipt, 106 of 110, the same
55 checks under the default allocator and the arena, one CDP session held
throughout): the headless page clicks its button (revision 1) and the CDP
session reads frame `cdp_frame_1`; in each of three rounds `surface.show`
answers engine-neutral fields only, the court-only log names a real window
number with one child process, the court's own-window capture matches the
painter's frame in 19 of 19 samples, a real click posted at the painter's
button row is applied by the idle host with no control request in flight
(the `input_applied` event arrives 0.3 to 1.6 ms after the post) and
advances the revision, a real scroll of 240 moves `scroll_y` and advances
the revision (6 to 13 ms), the CDP session still answers with the same
frame id, a second `show` is `conflict`, `surface.hide` ends the child by
protocol in 8 to 12 ms with the child reaped and no descendant left,
`owners.surfaces` returns to zero objects and zero bytes, the target,
frame, generation, realm and `scroll_y` survive with the revision advanced
only by the explicit actions, headless script, wait and network still run,
and the CDP session is unchanged after the hide; later rounds scroll back
up by real input before clicking. A child killed with `SIGKILL` while shown
is noticed, the surface is gone, owners are zero and the target is
untouched; a child stopped with `SIGSTOP` makes `hide` time out at 1,000
ms, kill and reap, counted (`kills_total` 1, `timeouts_total` 1); no stale
input was applied; the host stayed one process except for its surface
children and exited cleanly, removing the court-only file.

Latencies (medians of the rounds, on the frame-region build below):
show 108 to 120 ms (default) and 107 to 131 ms (arena) of which the
child's `READY` takes 87 to 112 ms and the first frame 12 to 17 ms; hide
9.6 to 11.0 ms. Before the frame region the same court read show 95 to
120 ms and hide 8.8 to 19.8 ms.

Memory, complete process tree (bytes, default / arena), on the
frame-region build (`native-dom-control-0.0.2-surface` receipt, host
`470c855f…`):

| stage | default | arena |
|---|---|---|
| headless host | 3,539,304 | 2,982,248 |
| spawn peak, host plus child (rounds 1–3) | 27,771,936 / 28,279,840 / 28,165,152 | 27,313,208 / 27,313,208 / 27,542,584 |
| shown steady, host only | 4,735,336 / 5,013,864 / 5,079,400 | 4,211,072 / 4,325,760 / 4,440,448 |
| post-hide host, over headless | +344,064 / +458,752 / +524,288 | +196,608 / +360,448 / +425,984 |
| post-hide libmalloc in-use over headless | within the 65,536 cap | within the cap |
| slope, round 3 − round 1 | 180,224 | 229,376 |

Before the frame region (the `Vec` frame in the default zone, host
`55fd1030…`, receipt superseded) the same court read post-hide
+1,196,032 / +2,424,856 / +2,506,800 (default) and +1,245,184 /
+1,425,432 / +2,572,312 (arena) with slopes of 1,310,768 / 1,327,128,
peaks of 27,870,216 to 29,246,544 and shown-steady of 4,440,400 to
5,833,088. The two pre-registered tolerances that fail are the post-hide
host footprint (cap 262,144 over headless) and the slope (cap 65,536).
Their attribution is the paired court below, measured on the `Vec`
build: the retained bytes were one or two copies of the freed 1 MB frame
kept by the default zone plus small-block churn, and the in-use heap
returns to baseline. How many copies a run kept varied between runs of
the same binary (two from round 2 under the default allocator and from
round 3 under the arena in the superseded receipt; one under the default
allocator and two under the arena in a second run; one under both on the
pre-instrumentation build). An earlier probe that spawned `/usr/bin/true`
read the spawn machinery as 1,130,496 bytes; the paired court corrects
that reading to 0 to 33 KB (that probe had already allocated the frame
before spawning). Two host-side reductions are already in: frames are
written to
the pipe from the painting itself (no encode or queue copy; before this the
post-hide excess was 6.5 MB) and repaints paint into the frame the surface
already owns. The child while shown costs about 23 MB in the tree
(AppKit's one-time attachment measured in the candidate court plus the
window and its bitmap), all of which the OS reclaims at hide.

Court amendments after the freeze, mechanism only, recorded in the script:
the page's load-time fetch settles its DOM mutation on the first
evaluation after open, so the court evaluates twice before the first
snapshot and retries a stale click reference once; the own-window capture
is taken by the court rather than the host (a host-side capture cost the
host 6 MB of CoreGraphics state); later rounds scroll back up by real input
before clicking; the court waits for the host's `repainted` event to read
the new hit map. Host-side mechanism notes: the child leaves through
`_exit` after `CLOSED` because AppKit's exit handlers otherwise hang; the
window floats (level 3) so it stays above ordinary windows without
activation; role-bar colours keep every channel far from the classifier's
midpoint.

Paired attribution of the post-hide retention (verdict: keep 106 of 110,
attribute before any fix; read-only, no cap moves; the numbers in this
section are the `Vec`-frame build's, kept as the record that led to the
candidate; the receipt of the same name now carries the frame-region
build's rerun, summarised in the next section).
[`surface-attribution-court.py`](surface-attribution-court.py) runs a fresh
host per run, one warm-up plus seven, three show/hide rounds, default
allocator and arena, twelve cells: the real child with frames of 640 × 400
(1,024,000 bytes), 256 × 256 (262,144) and 128 × 128 (65,536), the same
binary in lab-local modes that speak the protocol without AppKit
(`protocol`, frames discarded; `drain`, latest frame kept), and a child
that exits at once (`exit`: the failed protocol path, reported apart).
The host samples itself inside `surface.show` and `surface.hide` at eleven
stages (`show_entry`, `after_snapshot`, `after_painter`,
`after_command_spawn`, `after_reader_thread`, `after_hello_ready`,
`after_first_frame_ack`, `shown`, `hide_entry`, `after_close_reap_join`,
`after_frame_drop`: kernel footprint and RSS, libmalloc in-use and
allocated, thread count) only when the court-only file and
`--surface-court-stages 1` are given, so the frozen surface court's path
is unchanged; the court samples footprint from outside as a cross-check.
Medians, default / arena:

| cell | post-hide over headless, rounds 1 / 2 / 3 | in-use over headless after round 3 |
|---|---|---|
| real child, 640 × 400 | 1,245,184 / 1,441,816 / 2,424,856 — 1,277,952 / 1,425,432 / 1,458,200 | 7,040 / 3,936 |
| real child, 256 × 256 | 458,752 / 589,848 / 917,528 — 491,520 / 884,736 / 933,912 | 7,040 / 3,936 |
| real child, 128 × 128 | 262,144 / 442,368 / 507,928 — 278,528 / 393,240 / 458,776 | 7,040 / 3,936 |
| protocol child, no AppKit, 640 × 400 | 1,228,800 / 1,376,280 / 2,441,240 — 1,261,568 / 1,474,584 / 2,506,776 | 7,040 / 3,936 |
| drain child, no AppKit, 640 × 400 | 1,228,800 / 1,392,664 / 2,457,648 — 1,261,568 / 1,409,048 / 2,490,392 | 7,040 / 3,936 |
| exit child (failed path), 640 × 400 | 1,277,952 / 2,408,472 / 2,539,568 — 1,228,800 / 1,425,432 / 2,408,472 | 3,456 / 352 |

Stage deltas of the first round on the product cell (footprint / in-use,
default allocator): snapshot +65,536 / +3,744; painter +1,048,576 /
+1,032,864 (the frame; the zone's allocated bytes grow by 4,194,304);
`Command` spawn (pipes and child) +16,384 / +256; channel and reader
thread +16,384 / +816; `HELLO`/`READY` 0 / +2,832; first `FRAME_ACK` 0 /
0; `hide_entry` +16,384 / −784; `CLOSE`, `CLOSED`, reap and join −16,384
/ −3,328 (the reader thread's stack, threads 3 → 2); frame drop 0 /
−1,032,864. In the second round the painter costs +81,944 of footprint
(the freed block is reused) while in-use again rises and falls by
1,032,864. In the third round the painter costs +966,656 of footprint
with the zone's allocated bytes unchanged: the zone hands the painter a
block it already owns whose pages were not resident, and after hide both
blocks stay resident, so the host holds two copies of the freed frame
(the exiting child's cell shows the same from round 2, the 256 × 256 cell
from round 3). In all seven runs of every cell the first stage of the
hide sequence that stops releasing is `hide_entry`: after the child is
gone only the reader thread's 16 KB comes back, and the frame's pages stay
resident although in-use returns to within 7,040 bytes of headless.
`memory.trim` releases 0 in every run.

What this settles: the retained bytes are one or two copies of the frame
the host freed, kept by the default zone (the retention already
attributed on this route), and scale with the frame size; the child costs
the host nothing (the three
child modes and the failed spawn are indistinguishable from the real
window); the spawn machinery is 0 to 33 KB, not the 1.1 MB the earlier
`/usr/bin/true` probe suggested, because that probe had already allocated
the frame before spawning; the reader thread's stack is recovered; no
`mpsc`, `Command`, environment or exit-status allocation stays (in-use
returns); and the slope is the second copy of the frame when the zone
keeps one (about 1 MB) plus small-block churn of the snapshot and the
control-plane JSON (the 128 × 128 cell's slope of 245,784 / 180,248
bounds the churn from above). The court fixture is a fixture page, so the
painter's rows are fewer than on the representative page; the numbers are
otherwise the surface court's.

Candidate (pre-registered, not implemented, for ruling): give the frame a
dedicated anonymous `mmap` region owned by the surface record and
`munmap` it at hide, so the frame's pages return to the kernel with the
child instead of staying in the default zone. Expected from the ladder:
post-hide over headless falls by one or two frame sizes to about 200 to
300 KB in round 1 (the 128 × 128 cell's residual is 262,144 / 278,528 in
round 1), which sits at the 262,144 cap, the two-copy variance
disappears, and the slope stays about 180 to 250 KB unless the snapshot
churn is also bounded; so the candidate is expected to satisfy or nearly
satisfy S2 and not S3 on its own. Criteria if approved:
S2 and S3 of the surface court hold, the 106 mechanics checks, 40 + 2 unit
tests and every journey stay green, the complete-tree headed peak does not
worsen, show and hide latency stay within 10 percent, no global allocator
hack, no resident broker, no pre-warmed baseline. A second, separate
candidate for the slope would bound the snapshot's allocations (parse the
rows into reused buffers instead of `serde_json` values); it is not
proposed until the first is measured. Direct `posix_spawn` FFI is not
proposed: the data show the spawn costs nothing that stays.

Frame region candidate, approved, implemented and measured
([`surface-frame-region-0.0.1.md`](surface-frame-region-0.0.1.md): the
frozen criteria first, then the code, then the receipts). Each surface
record owns one anonymous `mmap` region of the frame's page-rounded
length (1,032,192 bytes for 640 × 400; checked lengths, refused above the
protocol's 3 MiB as `resource_limit`, an `mmap` failure as `internal`,
both before any child exists); the painter writes the mapping in place;
the pipe write borrows it for a synchronous `write_all`; the queue that
copied a frame while one was in flight is a resend flag; `Drop` unmaps
exactly once on every path (hide, failed show, child gone, target and
session close, host exit). Unit tests (43): checked lengths and the
bound, map, write and unmap counters, and three failed shows (a child
that exits at once, one that answers garbage, one that never answers)
each reaped with the region unmapped. `memory.report.owners.surfaces`
gains `frame` (above); a probe of one show and hide read reserved
1,032,192 / touched 1,032,192 / live 1,024,000 while shown and 0 / 0 / 0
with `unmapped_bytes_total` 1,032,192 after hide; a second hide is
`not_found`.

Result against the frozen criteria (both allocators, the frozen court
unchanged, run twice on the same build): the frame's contribution is
gone and the variance with it. The attribution court on this build reads
the painter at exactly +1,032,192 of footprint and the frame drop at
exactly −1,032,192 in every round of every cell (seven runs each; the
128 × 128 cell +65,536 / −65,536), so the post-hide excess no longer
depends on the frame size (medians after rounds 1 / 2 / 3, default:
196,608 / 278,528 / 311,296 for 640 × 400, 180,224 / 278,528 / 311,296
for 256 × 256, 163,840 / 245,760 / 311,296 for 128 × 128; the exiting
child's cell 163,840 / 229,376 / 294,912). The surface court read
post-hide +344,064 / +458,752 / +524,288 (default) and +196,608 /
+360,448 / +425,984 (arena) in the recorded run and +327,680 / +442,368 /
+491,520 and +131,072 / +344,064 / +442,368 in the second run: no
1 MB step in any round of either run. S2 (≤ 262,144 every round) fails
from round 2 under both allocators (round 1 passes under the arena
only), S3 (≤ 65,536) fails with slopes of 180,224 / 229,376 and 163,840
/ 311,296. Mechanics 106 of 106, every regression at its count, the
complete-tree peak 27,313,208 to 28,279,840 (not worse than 27,460,640
to 29,246,544), hide latency 9.6 to 11.0 ms (was 8.8 to 19.8), show
medians 110.1 ms (default, was 100.1) and 108.7 / 110.2 ms in the two
runs (arena, was 100.0): at the 10 percent edge, dominated by the
child's `READY`.

Verdict under the pre-registered outcomes: both S2 and S3 fail, so the
candidate is rejected for G3 and no cap moves; the frame region stays as
the frame's backing because it removes one to two megabytes of zone
retention and the variance, and every criterion other than S2 and S3
holds. What remains is small-block churn that the frame never was: per
round the stage deltas read snapshot +16,384 to +65,536, spawn pipes 0
to +32,768, the reader thread's stack +16,384 (returned at join), the
shown and hide entries 0 to +32,768; in-use returns to within 7,040
bytes. After `target.close` and `session.close` the default allocator
still holds 344,064 over headless while the arena holds 16,360: the
churn is mostly the script realm's allocations during the snapshot
evaluation (arena-backed under the arena and released with the target),
plus control-plane JSON. The next step is the ruling's read-only
attribution of that snapshot and `serde_json` churn before a second
candidate is proposed.

Snapshot and serde attribution (read-only, ruling after the frame region;
`native-dom-control-0.0.2-surface-snapshot-attribution` receipt, host
`32343eb5…`; **a visual run recorded before the headless rule, kept as
facts and not rerun**; [`surface-snapshot-attribution-court.py`](surface-snapshot-attribution-court.py)
now runs headless by default). The host samples eleven more stages
inside the path shared by the CDP snapshot, the Agent's `target.snapshot`
and the surface (`snapshot_script` → realm eval → `String` crossing →
`serde_json` parse → rows; `target.inspect` reads only the revision;
surface input runs `act_script` through the same eval): before and after
the realm eval, after the string crossing, after the realm value drop,
after the queued jobs, after the network pump, before and after the parse,
after the string drop, after the rows extraction, after the `Value` drop,
optionally after an explicit realm GC; each with the realm's arena
statistics. Twelve surface cells (three shapes × the product path,
`evaluate_only`, `parse_drop`; the product path plus GC; two lab-only
equal-byte microbench shapes, 16 KiB, not a browser result) and a
seven-snapshot plateau cell, one warm-up plus seven runs, both
allocators. Findings (medians): the host side after the realm costs 0
footprint in every product cell and round (crossing, parse, string drop,
rows, `Value` drop: in-use rises 4,736 at the parse and falls 4,736 at
the drop, footprint unchanged); the realm eval is where the in-process
growth is born under the default allocator (+49,152 / +32,768 / +32,768
footprint in rounds 1 to 3 on the product cell, 0 under the arena, whose
used bytes rise 4,544 and return); no drop stage and no explicit GC ever
lowers the footprint (0 of 7 runs at every release stage; the GC cell is
indistinguishable from the product cell); `evaluate_only` and
`parse_drop` retain the same as the product path (so nothing the host
holds after the realm matters); the in-process retention over
`show_entry` is 98,304 / 65,536 / 32,768 per round (default) and 65,536
/ 98,304 / 32,768 (arena) against outside post-hide readings of 180,224
/ 278,528 / 311,296 and 163,840 / 278,528 / 344,064: the difference is
the control plane between stages, which the plateau cell shows alone
(seven snapshots with no surface: +147,456 → +360,448 default, +114,688
→ +311,296 arena, converging, in-use flat at 3,104 / 0, realm malloc
flat at 248,768 / 242,688: no plateau within seven, no reuse of pages);
after `target.close` the default allocator keeps 344,064 over headless
and the arena 16,360 (the realm's pages return with the target under the
arena and stay cached in the default zone), recorded and never counted
for the hide gate. The microbench (equal bytes) shows the realm string
build at +245,760 (flat) and +180,224 (nested) with the host parse at 0
(flat) and +81,920 (nested): for a 16 KiB nested result the host `Value`
costs pages, for the product's snapshot (a few hundred bytes, five rows)
it costs none. What this rules out: a reused `serde_json` buffer or a
rows-without-`Value` extraction would change nothing measurable. What it
leaves: (a) the realm's own allocations during the snapshot evaluation,
freed but kept resident by the default zone (bounded and returned by the
arena at target close), and (b) the control plane's per-request churn,
which grows over seven calls without a surface. Candidate for ruling, at
most one, not implemented: none proposed yet — (a) needs a host-native
bounded semantic traversal that keeps the Agent snapshot's semantics
without a second DOM authority (open design question: the realm holds the
node references `act` relies on) and (b) is not surface-specific; the
next step is a read-only measurement of (b) per control operation
(`plateau-inspect` and `plateau-idle` cells are in the court for that
and run headless) before any candidate is pre-registered.

Control-plane churn attribution (headless, read-only, ruling after the
snapshot attribution; `native-dom-control-0.0.2-control-churn` receipt,
host `d41d5be2…`; [`control-churn-court.py`](control-churn-court.py),
which refuses to run with the visible-court variable set and checks
before and after that no surface child and no window owned by it exist;
the surface arm uses the drain child that maps no AppKit). One operation
repeated 128 times on one live target, fresh host, one warm-up plus
seven runs, both allocators, the footprint read from outside
(`proc_pid_rusage`, no observer effect) after each of the first seven
requests and every eighth after; the same arms again with court-only
request stages inside the host (`request_read`, `request_parsed`,
`after_sync_io`, `after_dispatch`, `after_commit_io`, `after_execute`,
`response_serialized`, `request_dropped`, `response_written`,
`response_dropped`), whose observer effect is the difference of the
outside readings at request 128 (−65,536 to +65,536: one to four pages,
within the run-to-run spread). Footprint over headless after request 7
and after request 128, medians, default / arena:

| arm | after 7 | after 128 | per request over 8..128 |
|---|---|---|---|
| `profile.list` (not target-scoped, 217-byte response) | 0 / 16,384 | 0 / 81,920 | 0 / 273 |
| `target.inspect` (revision read; working-copy sync and commit) | 65,536 / 114,688 | 81,920 / 180,224 | 137 / 546 |
| `target.snapshot` (the shared snapshot path, 813-byte response) | 245,760 / 180,224 | 344,064 / 278,528 | 683 / 683 |
| `memory.report` (the self-measuring request, 2,721-byte response) | 180,224 / 245,760 | 212,992 / 393,216 | 273 / 1,229 |
| `surface.show` + `surface.hide` (drain child) | 327,680 / 425,984 | 425,984 / 606,208 | 683 / 1,502 |
| CDP `Page.getFrameTree` over an attached session | 32,768 / 81,920 | 65,536 / 180,224 | 273 / 683 |

The first request after which the outside footprint never returned to
its pre-request value is request 1 in every run of the snapshot,
memory.report and surface arms, and request 1 or 2 for `target.inspect`
and the CDP arm. No arm reaches a plateau by request 128 except
`profile.list` under the default allocator (0 throughout). Where the
growth is born (stage sums over all 896 to 903 requests of the seven
runs, default allocator): for `target.inspect`, the dispatch (the
revision eval in the realm and the cookie and storage drains that follow
every eval) 393,216 over 20 requests and the response serialization
212,992 over 12; for `target.snapshot`, the dispatch 1,933,312 over 70
requests and the serialization 163,840 over 10; for `memory.report`, the
dispatch 409,600 over 21 and the serialization 573,440 over 26 (its own
result is the largest response); for `surface.show`, the dispatch maps
the frame (+1,032,192 each) and `surface.hide` returns it (−1,032,192
each, 896 of 896), with the residual of the pair born in the show's
snapshot eval and the hide's serialization. Request parsing, the
working-copy sync and commit, and every drop stage grow nothing;
libmalloc in-use returns within each request (parse +720, dispatch
+4,656, serialization −3,632, drops −896 and −832 for `target.inspect`:
the sums cancel), so nothing is retained by the host and every byte of
growth is a freed small-block page the default zone keeps resident (and,
under the arena, the realm's arena pages plus the host's own default-zone
pages, which is why the arena arms grow more, not less). The `memory.report`
figures in this lab's other courts therefore carry their own churn: one
extra page every three to four calls.

What this settles for the G3 gate: after the frame region, the surface
court's post-hide excess and slope are this per-request page churn of
the whole control plane (about 0.1 to 1.5 KB per request, page-granular,
not converging by 128 requests), of which the surface's own path is one
contributor among `target.snapshot`, `memory.report` and the court's own
`target.inspect` calls between rounds. A host-side change to one
operation cannot bring the frozen S2 and S3 under the caps on its own.

Authority (closed before any traversal candidate): at `target.open` the
host parses with html5ever once, serializes the tree into the realm
(`__mcsSeed`) and drops the parsed document; from then on the realm's
shim DOM is the only document state (mutations by page scripts, the
revision counter `window.__mcs.revision`, the snapshot's node array
`s.nodes` that `act` indexes by `node_N` at the snapshot's revision). The
host keeps only static numbers (fixture bytes, element count) and the
profile mirrors (cookies, storage). A host-native semantic traversal
would need a second DOM that the page's scripts do not mutate: a second
authority, rejected. The realm-side direction is the coherent one.

Candidate for ruling (at most one, pre-registered, not implemented): a
realm-side snapshot memo — `snapshot_script` returns the previous JSON
string when `s.snapshot === s.revision` and the node budget is
unchanged, so an unchanged page costs no realm allocation on repeated
snapshots (the cached string is realm-owned and bounded by the snapshot
budget; the result for a given revision is identical by construction,
so the Agent's snapshot semantics and the single authority are kept).
Expected effect from the stage sums: the snapshot's realm-side growth
(about 2 KB per request on `target.snapshot`, the largest single
contributor) disappears for same-revision snapshots; the surface court's
rounds change the revision by real input, so its show-time snapshots
would still miss, and the control plane's serialization churn stays.
Honest expectation: a partial reduction of the per-round growth, not a
pass of S2 or S3; proposed for a decision on whether a partial reduction
is worth a change to the snapshot script at all. Ruling: rejected (the
real surface changes the revision by input every round, so the memo
misses where it matters, and it would keep a JSON string resident in the
realm with invalidation to maintain); it stays a rejected idea.

Next, design only (no run, no receipt):
[`surface-paired-causal-court-0.0.1.md`](surface-paired-causal-court-0.0.1.md)
pre-registers a paired causal court whose two arms, a real surface child
and a headless replaying counterfactual, run an identical operation and
input sequence on the same live target, so the presentation's own
retention is the per-stage differential between the arms while the
absolute S2 and S3 of the frozen court stay in force by reference. Its
visual arm runs only under the three-part opt-in and the owner's
permission for that run. Ruling: implemented headless-only — the
child's bounded `replay:<script>` mode (≤ 256 bytes, ≤ 16 events, bound
to frame acknowledgements, no AppKit) and the harness
[`surface-paired-causal-court.py`](surface-paired-causal-court.py), which
ran arm B only (`native-dom-control-0.0.2-surface-paired-causal`
receipt, status `unverified-headless-counterfactual`, arm A
`not_observed`, no differential computed, evaluation
`pending-owner-authorized-visual`; §9 of the design records arm B's
absolute numbers). The harness refuses to start with the visible-court
variable set and refuses `--visual` in this revision.

Gaps: post-hide host footprint and slope over the pre-registered caps
(page churn of the whole control plane, see the attribution above; the
frame itself now returns exactly at hide, and the spawn costs nothing
that stays); macOS only; one window size; keys are recorded
and ignored; the painter is semantic rows, not layout; the WindowServer
and compositor stay unattributed; Accessibility trust is required for the
court's input.

Verdict: `narrow`. The G3 mechanics (attach, real input, detach with a
reaped child, owners to zero, target and CDP continuity, failure modes)
are observed on this cell; the memory tolerances are not, so G3 stays
open, as do G1, P6 and G6.

### Receipt provenance

Each receipt records the SHA-256 of the host binary that produced it. The
binary is rebuilt from the commit that added the receipt, so the hashes
differ across receipts by design:

| receipt | host built from | note |
|---|---|---|
| `native-dom-control-0.0.2-network-court` | the bounded-network slice (before the allocator experiments) | unchanged since; the arena knob did not exist |
| `native-dom-control-0.0.2-retention-attribution-arena` | the arena commit (`4c4b519`, measured in `468b8a9`) | the later tail-trim reporting fix (`12de192`) changes no value in this receipt: no realm is alive at its trim stage, so `arena_released_bytes` was already zero |
| `native-dom-control-0.0.2-arena-soak`, `native-dom-control-0.0.2-arena-concurrent-soak` | the host after the tail-trim reporting fix (`12de192`) | both receipts carry the same hash and their embedded rules equal the committed court scripts |
| `native-dom-control-0.0.2-frame-realm`, `native-dom-control-0.0.2-cdp-frame-tree`, `native-dom-control-0.0.2-profile` | the host with the profile store (keychain envelope, cookie jar, `localStorage`, one live session per profile) | the frame-realm (62/62) and CDP (58/58) courts and the journeys (27/27, 35/35 under both allocators) were rerun on this build and all three receipts carry its hash |
| `native-dom-control-0.0.2-profile-attribution`, `native-dom-control-0.0.2-keychain-acl-probe` | the same profile-store host | read-only diagnostics after the P6 verdict; the ACL probe used two scratch builds of the same source (their `cdhash` values are in the receipt) and records the committed host's hash for reference |
| `native-dom-control-0.0.2-profile-helper` | the helper build (commit `906884b`; `host_sha256` in the receipt) against the in-process build as `baseline_sha256` | the experiment failed its frozen C4 and the in-process host was restored in the following commit; the receipt stays as the record |
| `native-dom-control-0.0.2-https`, `native-dom-control-0.0.2-secure-cookie`, and the rerun `native-dom-control-0.0.2-profile`, `-frame-realm`, `-cdp-frame-tree` | the host with the pinned-roots HTTPS slice, the exact header cap and the court clock offset |
| `native-dom-control-0.0.2-surface`, `native-dom-control-0.0.2-surface-attribution`, `native-dom-control-0.0.2-surface-snapshot-attribution` | the host with the G3 surface process, the surface-owned mmap frame region and the court-only stage log, host `32343eb5…` (`surface_sha256` names the child binary); these three are visual runs recorded before the headless rule and are not rerun | builds, newest first. `3089ab5d…` (the court-only navigation stage samples) carries `-navigation-attribution`, which has no pass or fail by design. `17f5284f…` (the allowlist sharing candidate, since reverted) carries `-navigation-repair`, recorded rejected. `720656ce…` carries `-navigation` (89 of 90, the differential soak narrow on the default allocator), `-navigation-replication` (cross-batch unstable, not a pass), `-profile` and `-cdp-frame-tree` (64 of 64, its pinned `puppeteer-core 24.15.0` client restored offline from the local npm cache under the ignored `target/labs/d4`, integrity equal to `cdp-qualification-0.0.1.json`, on Node.js v26.7.0). `925d062b…` with child `8e9e51e2…` carries `-surface-headless` (17 of 17) and `-surface-paired-causal` (arm B only). `69778384…` carries `-frame-realm`, `-https` and `-secure-cookie`, which are older-build evidence and are not presented as current. The surface court is visual and waits for a by-hand opted-in run | the journeys (27/27, 35/35 under both allocators) were rerun on the headless rule build |

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

- No layout, images, fonts or real timers; scripts run after parsing
  rather than at parse position; only inline and same-origin external
  scripts run. Cookies and `localStorage` exist only as the bounded profile
  store above (macOS Keychain, no cache or history); `https` exists only
  under explicitly pinned roots (rustls + ring, opt-in), never against
  system roots or the public web.
- The DOM shim implements what the court fixtures and instrumentation use.
  It is not a Web-compatibility claim: unsupported selectors throw, and any
  page relying on layout, `XMLHttpRequest`, `localStorage` or timing fails
  explicitly (`localStorage` exists only with `--profile-root`). Navigation
  is a link click on an `<a href>` only.
- One fixture set, one platform, summed RSS and physical footprint only.
- Public-address negatives are refused before any connection, so they
  exercise policy rather than reachability; the `.invalid` negative depends
  on the system resolver returning no address.
- Memory freed by closing realms stays in libmalloc as reserved regions
  under the default allocator: attributed above. The zone per realm returns
  it at a live-footprint cost; the arena per realm returns it without that
  cost on this court, but both are macOS opt-in experiments and the default
  is unchanged until the arena has been measured on more workloads and a
  second platform.
- The profile store's attributed fix candidate (keychain access outside
  the host process) was tried under frozen criteria and failed its
  complete-tree peak criterion; `https` with pinned roots is now an opt-in
  slice; the next P6 steps are a second platform key source and the
  Secure-cookie court on a persisted profile; each passes only if the 27-item journey and the 35-item
  network court stay green and the footprint court row stays below
  Lightpanda's single server at one target. The arena's next steps are a second platform
  behind the same `Region` boundary, interior (not only tail) trimming, and
  a soak of many open/close cycles before it can be proposed as a default.
