# G1 comparison campaign — run report, 0.0.1

Headless and read-only. No code changed, no court frozen, D6 untouched, no
navigation soak, no visual or surface path. Nothing was downloaded.

**Verdict: BLOCKED on the baseline half, delivered on the route half.** The
route's own numbers are below in the shape G1 asks for; the named same-machine
comparison could not be run, and this report says so rather than substituting
a different measurement and calling it the gate.

## 1. What G1 asks for

> deterministic workloads report complete process-tree component, peak and
> post-close values against named same-machine baselines

Two halves: **the route's numbers**, and **the named baselines on the same
machine**. The first is done here. The second is blocked.

## 2. The route, both arms, eight targets

Same fixture, same sequence, headless, one hermetic loopback origin. `peak` is
the process's lifetime maximum physical footprint; `tracked` is the host's own
`script_realm_bytes`, reported beside the footprint so a saving that merely
stops being accounted would show.

**System allocator**

| step | footprint | peak | resident | tracked |
| --- | --- | --- | --- | --- |
| empty host, before any request | 213,064 | 213,064 | 4,800,512 | — |
| after the first request | 1,720,656 | 1,720,656 | 6,635,520 | 0 |
| profile + session | 2,081,104 | 2,081,104 | 7,553,024 | 0 |
| **1 live target** | **3,359,056** | 3,359,056 | 10,240,000 | 329,776 |
| **8 live targets** | **6,046,032** | 6,046,032 | 12,943,360 | 2,638,208 |
| all targets closed | 6,095,184 | 6,095,184 | 13,008,896 | 0 |
| session closed + `memory.trim` | 6,111,568 | 6,111,568 | 13,025,280 | 0 |

**Arena allocator**

| step | footprint | peak | resident | tracked |
| --- | --- | --- | --- | --- |
| empty host, before any request | 196,680 | 196,680 | 4,931,584 | — |
| after the first request | 1,802,600 | 1,802,600 | 7,045,120 | 0 |
| profile + session | 2,113,896 | 2,113,896 | 7,569,408 | 0 |
| **1 live target** | **2,818,432** | 2,818,432 | 9,666,560 | 319,616 |
| **8 live targets** | **6,488,616** | 6,488,616 | 13,238,272 | 2,556,928 |
| all targets closed | **2,720,104** | 6,505,000 | 9,617,408 | 0 |
| session closed + `memory.trim` | 2,736,488 | 6,505,000 | 9,633,792 | 0 |

Read together, as the ruling requires:

| | system | arena |
| --- | --- | --- |
| one live target | 3,359,056 | **2,818,432** |
| eight live targets | **6,046,032** | 6,488,616 |
| marginal target after the first | **383,854** | 524,312 |
| peak | 6,111,568 | 6,505,000 |
| **after close, over the empty host** | **5,898,504 retained** | **2,539,808 retained** |
| returned from peak at close | 0 | **3,768,512** |

The system arm is cheaper live and returns nothing; the arena arm is dearer
live and returns 3.77 MB. Reporting either alone would flatter one arm, which
is exactly what the pairing ruling exists to prevent.

## 3. Why the baseline half is blocked

The named baselines are Chrome, Lightpanda 0.4.0 and Servo, and the sanctioned
harnesses are `labs/court/run-w1-cdp-comparison-macos-arm64.sh` and
`run-target-retention-macos-arm64.sh`, which share one sampler, one fixture,
one readiness condition and one live-target window.

| prerequisite | state | consequence |
| --- | --- | --- |
| Google Chrome | **present** at the expected path | usable |
| Lightpanda 0.4.0 binary at `target/labs/lightpanda/0.4.0/…` | **absent**; the runner fetches it from GitHub releases with `gh` | **blocking** |
| `process-tree-sampler` | **not blocking** — it builds offline; I built it to check and left the source untouched | resolved |
| Servo control host | **not built** | blocking for the Servo row only |
| the courts' own interface | `--lightpanda` and `--lightpanda-sha256` are **required arguments**; `--native-dom` and `--servo-control` are optional | the harness will not run Chrome-only |

So even though Chrome is installed, the sanctioned comparison cannot run: the
court refuses without the Lightpanda binary, and obtaining it means an outward
network download of a third-party release, which I did not do.

**What I did not do instead:** hand-roll a Chrome-versus-native-dom comparison
outside the court. It would use a different sampler, a different readiness
condition and a different process-tree rule, and presenting it as G1 evidence
would be the "guess a pass" the brief warned against. The route numbers above
are labelled as the route's own, not as a comparison.

## 4. What the route numbers do and do not say

They **do** say: a marginal target costs 0.38–0.52 MB; eight targets live cost
about 6.0–6.5 MB; the empty host is about 0.2 MB; and closing returns
everything the arena mapped and nothing libmalloc holds.

They **do not** say the route is more efficient than any baseline. The ledger's
figures for Chrome are *summed process-tree RSS* (803 MB empty, 1,231 MB with
one target), Lightpanda's are 22.7 MB empty and 27.9 MB with one target, and
this host renders nothing — no layout, no paint, no compositor. Comparing a
single-process footprint against a rendering browser's process tree, on numbers
taken by different samplers on different days, is not the comparison G1 asks
for. **That comparison remains unrun.**

## 5. To unblock

1. A local Lightpanda 0.4.0 `lightpanda-aarch64-macos` matching the pinned
   digest `840547bb…`, or an explicit authorisation to download it.
2. For the Servo row, a built `servo-control`; without it the Servo column
   stays as previously recorded evidence rather than same-day measurement.
3. Then: `run-target-retention-macos-arm64.sh --native-dom <binary>` and the
   W1 comparison, both of which already accept the native host as an optional
   arm, so no code is needed.

Until then G1 stays open with the route half measured and the comparison half
absent — which is the honest state, and the same one the ledger has recorded
all along.
