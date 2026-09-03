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
`target.inspect` lists `frames[]` (one, `parent` null) and `realms[]` with
`frame_limit` 1; `target.snapshot` takes optional `frame` and `realm` and
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

Losses, recorded rather than approximated: no child frames (`frame_limit`
1); no capability attenuation on this host, so a request carrying the field
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

### Receipt provenance

Each receipt records the SHA-256 of the host binary that produced it. The
binary is rebuilt from the commit that added the receipt, so the hashes
differ across receipts by design:

| receipt | host built from | note |
|---|---|---|
| `native-dom-control-0.0.2-network-court` | the bounded-network slice (before the allocator experiments) | unchanged since; the arena knob did not exist |
| `native-dom-control-0.0.2-retention-attribution-arena` | the arena commit (`4c4b519`, measured in `468b8a9`) | the later tail-trim reporting fix (`12de192`) changes no value in this receipt: no realm is alive at its trim stage, so `arena_released_bytes` was already zero |
| `native-dom-control-0.0.2-arena-soak`, `native-dom-control-0.0.2-arena-concurrent-soak` | the host after the tail-trim reporting fix (`12de192`) | both receipts carry the same hash and their embedded rules equal the committed court scripts |
| `native-dom-control-0.0.2-frame-realm` | the host with frames, realms and link navigation | the journeys were rerun on this build (27/27, 35/35 under both allocators) |

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
  page relying on layout, `XMLHttpRequest`, `localStorage` or timing fails
  explicitly. Navigation is a link click on an `<a href>` only.
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
- The next slice must add a bounded persistent-profile store (cookies and
  local storage) under the same journey and court, then `https` with pinned
  roots; each passes only if the 27-item journey and the 35-item network
  court stay green and the footprint court row stays below Lightpanda's
  single server at one target. The arena's next steps are a second platform
  behind the same `Region` boundary, interior (not only tail) trimming, and
  a soak of many open/close cycles before it can be proposed as a default.
