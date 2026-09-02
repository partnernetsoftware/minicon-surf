# Shared experiment court

The court makes different browser technologies answer the same questions. It
does not force unsupported capabilities to look alike.

## Workloads

| ID | Workload | Purpose |
|---|---|---|
| W0 | executable version/start/exit | artifact identity and empty command cost |
| W1 | one hermetic semantic document | first target plus Agent-visible structure |
| W2 | script mutates DOM before semantic dump | JS/DOM/Agent observation integration |
| W3 | repeated documents in one session | per-target and retained-memory growth |
| W4 | named Chrome/Brave/system-WebView equivalent | same-machine comparative baseline |
| W5 | shown → hidden → detached → shown | surface cost and state continuity |
| W6 | two isolated profiles | incremental cost and storage/policy isolation |
| W7 | CLI and CDP control one target | common target identity and protocol mapping |

W1 is [`fixtures/semantic-static.html`](fixtures/semantic-static.html). W2 is
[`fixtures/semantic-scripted.html`](fixtures/semantic-scripted.html). W7's
native half uses [`fixtures/semantic-interactive.html`](fixtures/semantic-interactive.html),
whose button click mutates the document so a revision advance is observable
without a sleep. A runner
may encode the exact bytes as a `data:` URL to remove server and network cost;
its receipt must record that transport.

## Measurement rules

- Pin OS, ISA, engine version, artifact SHA-256 and fixture SHA-256.
- Use at least seven measured repetitions after one unreported warm-up unless
  startup itself is the workload. Preserve every sample; report median and
  maximum rather than only the best run.
- Record wall time and the strongest memory semantic the platform actually
  exposes. `maximum resident set size` is not private memory or live heap.
- Measure the complete attributable process tree. A single-process candidate
  may use process maximum RSS when the absence of children is observed and
  reported. Otherwise a root-only sample is incomplete.
- Disable telemetry, updater, crash upload, extensions and external network
  traffic where supported, recording each switch.
- A comparison is valid only on the same machine, OS state, workload transport,
  repetition policy and measurement semantic.
- No universal claim is made from a single cell.

## Receipt status

- `observed`: command ran and raw facts were captured.
- `qualified`: reproduction, artifact identity, workload output and measurement
  semantics were reviewed.
- `incomplete`: a named missing dimension prevents the intended comparison.
- `rejected`: the evidence disproved the lab hypothesis for the named role.

The live-target W1 comparison now supplies a same-machine Chrome baseline and
recursive process-tree sampling, but remains `incomplete` because summed RSS
can double-count shared pages and the court covers only one small fixture and
platform. It is evidence for the next experiment, not a product memory claim.

W3 now keeps each browser server alive across eight sequential W1 target
create/observe/close cycles. It samples empty, first-live, first-closed,
eighth-live and eighth-closed process-tree RSS, then separately probes up to
eight concurrent targets. Run:

```sh
labs/court/run-target-retention-macos-arm64.sh \
  --receipt labs/court/evidence/macos-arm64-target-retention-lightpanda-0.4.0-vs-chrome-152.0.7977.75.json
```

Pass `--servo-control labs/servo/target/release/servo-control` to add the
Servo lab's native control `0.0.1` host as a third candidate driven through
NDJSON rather than CDP; the candidate order then rotates by repetition.
`--sequential-cycles N` changes the number of create/observe/close cycles
(stages are then `last_target` and `post_all_closes`), `--candidates`
selects a subset, and `retention-slope.py` fits retained-above-empty against
cycle count across receipts from several cycle counts. CDP
discovery requests to `127.0.0.1` deliberately bypass any `http_proxy`
environment: a proxy answering for loopback returned `503` for a healthy
Lightpanda endpoint and would have looked like an engine failure.

The three-way receipt
(`macos-arm64-target-retention-servo-0.5.0-lightpanda-0.4.0-chrome-152.0.7977.75`)
gives the Servo route its first same-machine named baseline on this shared
court. Median complete-tree RSS in bytes over seven rotating repetitions:

| stage | Servo 0.5.0 (control) | Lightpanda 0.4.0 (CDP) | Chrome 152.0.7977.75 (CDP) |
|---|---|---|---|
| empty | 44,613,632 | 22,659,072 | 803,078,144 |
| one target | 87,457,792 | 27,934,720 | 1,232,109,568 |
| post one close | 86,851,584 | 27,967,488 | 917,159,936 |
| eighth target | 97,206,272 | 29,523,968 | 1,252,966,400 |
| post eight closes | 94,601,216 | 29,523,968 | 934,428,672 |
| retained above empty | 49,905,664 | 6,766,592 | 130,891,776 |
| eight concurrent targets | 136,953,856 (1 process) | one target only | 2,206,859,264 (9 processes) |

Servo held eight concurrent targets in one process at about one sixteenth of
Chrome's summed tree, and its single-target tree was about one fourteenth of
Chrome's; Lightpanda stayed about three times below Servo but still rejected a
second concurrent target. Servo's retention is the driver-owned growth
measured in its own lab, not Rust heap. The receipt stays `incomplete`: the
Servo candidate is driven natively rather than through CDP, renders through a
CGL context, and every candidate remains one fixture, platform and summed RSS.

The four-candidate receipt
(`macos-arm64-target-retention-native-dom-0.0.0-servo-0.5.0-lightpanda-0.4.0-chrome-152.0.7977.75`)
adds the native bounded route's first slice, an html5ever DOM host with no
layout, script or network, on the same eight-cycle court. Median summed
process-tree RSS in bytes:

| stage | native DOM slice | Servo 0.5.0 | Lightpanda 0.4.0 | Chrome 152.0.7977.75 |
|---|---|---|---|---|
| empty | 2,195,456 | 44,695,552 | 22,806,528 | 804,061,184 |
| one target | 2,539,520 | 87,752,704 | 28,049,408 | 1,229,062,144 |
| post all closes | 2,785,280 | 95,158,272 | 29,523,968 | 932,544,512 |
| retained above empty | 589,824 | 50,462,720 | 6,799,360 | 128,483,328 |
| eight concurrent targets | 3,063,808 | 137,805,824 | one target only | 2,202,714,112 |

The slice is the floor of the native route, not a browser: it answers static
semantic snapshots and refuses every action with a typed failure. Each later
slice (script realm, layout, network) must be measured against this row.

The five-candidate receipt
(`macos-arm64-target-retention-lightpanda-per-target-0.4.0-servo-0.5.0-native-dom-0.0.0-chrome-152.0.7977.75`)
adds Lightpanda behind its control host with one server process per target
(`lightpanda_control`), so the tree sampled is the Python host plus every live
engine process. Median summed RSS in bytes, eight cycles, seven rotating runs:

| stage | Lightpanda per-target (host + engines) | Lightpanda single server | Servo 0.5.0 | native DOM slice | Chrome 152.0.7977.75 |
|---|---|---|---|---|---|
| empty | 28,164,096 (1 process) | 22,691,840 | 44,613,632 | 2,195,456 | 803,176,448 |
| one target | 60,866,560 (2 processes) | 27,901,952 | 88,014,848 | 2,539,520 | 1,231,683,584 |
| post one close | 31,096,832 (1 process) | 27,934,720 | 87,392,256 | 2,555,904 | 917,192,704 |
| post all closes | 39,174,144 (1 process) | 29,442,048 | 94,863,360 | 2,768,896 | 932,167,680 |
| retained above empty | 10,993,664 (all inside the Python host) | 6,733,824 | 50,462,720 | 573,440 | 129,269,760 |
| eight concurrent targets | 279,855,104 (9 processes) | one target only | 137,101,312 (1 process) | 3,047,424 | 2,205,646,848 |

Process-per-target gives Lightpanda eight concurrent targets and makes
engine retention zero by construction: every byte retained after the closes
sits in the Python control host, whose own 28 MB empty footprint is a cost of
the court host, not of the engine. At eight concurrent targets the design
costs about twice Servo's single process and about one eighth of Chrome; at
one target it is still below Servo. This is the first measured `combine`
candidate: a low-memory engine per target under one native control
authority, with `terminate one target` available as a process boundary.

A host-split receipt
(`macos-arm64-target-retention-lightpanda-per-target-0.4.0-host-split`) reruns
that candidate alone with the sampler also recording descendants-only RSS.
Engine processes measured 29,638,656 bytes with one target and 0 after every
close, and 237,027,328 bytes for eight concurrent targets; the remainder of
the tree (28,164,096 empty, 39,272,448 after eight closes) is the Python
court host itself. The engines-only figure is the number a Rust host would
approach: about 1.7× Servo's single process at eight targets and about one
ninth of Chrome.

The per-cycle slope receipt
(`macos-arm64-target-retention-slope-servo-0.5.0-lightpanda-0.4.0-chrome-152.0.7977.75`)
fits retained-above-empty summed RSS against 1, 8 and 32 sequential cycles
(seven runs per cycle count and candidate, one warm-up each):

| candidate | retained after 1 / 8 / 32 cycles | intercept (warm-up) | slope per cycle |
|---|---|---|---|
| Servo 0.5.0 (control) | 42,713,088 / 50,577,408 / 68,452,352 | 43,050,609 | 791,477 |
| Lightpanda 0.4.0 | 5,292,032 / 6,782,976 / 6,864,896 | 5,783,352 | 39,062 |
| Chrome 152.0.7977.75 | 112,738,304 / 126,189,568 / 139,280,384 | 115,322,685 | 799,476 |

A 128-cycle soak receipt
(`macos-arm64-target-retention-cycles-128-servo-0.5.0-lightpanda-0.4.0`,
seven runs each, Chrome not rerun) tests the linear assumption:

| candidate | retained after 128 cycles | linear prediction from 1/8/32 | reading |
|---|---|---|---|
| Servo 0.5.0 (control) | 130,613,248 (live 178,192,384 with the 128th target) | 43,050,609 + 128 × 791,477 = 144,359,665 | linear growth continues; refit over 1/8/32/128 gives 678,621 bytes per cycle with a −669,294-byte residual at 128 |
| Lightpanda 0.4.0 | 6,963,200 | 5,783,352 + 128 × 39,062 = 10,783,288 | growth stops: 6,782,976 at 8, 6,864,896 at 32, 6,963,200 at 128; retention is a bounded ~7 MB plateau, and the earlier 39 KB per cycle was warm-up spread over few cycles |

Servo's per-cycle term is therefore real and unbounded within 128 cycles, and
matches the 765,990 bytes per cycle its own lab measured; Chrome accumulated
at almost the same rate over 1/8/32. Lightpanda's retention is bounded rather
than linear, which is the property a long single-target Agent session needs.

The concurrency probe does not force candidates into an unsupported common
count. Lightpanda 0.4.0 is measured at its observed one-target limit and its
second-create error is retained; Chrome is measured with eight concurrent
targets. Sequential churn remains the shared comparison surface.

## Process-tree sampler

[`process-tree-sampler/`](process-tree-sampler/) is the first shared court
utility. It launches one command in a dedicated process group, recursively
attributes descendants by PPID, samples summed RSS, enforces a deadline and
emits argument-redacted JSON. `--exclude-root` permits two candidates to use
the same orchestration wrapper without charging that wrapper to either one.

The sampler also supports an explicitly reported startup warmup for lifecycle
state courts. It never extends the total deadline, and a candidate that exits
during warmup produces zero samples. Courts using it must record setup time and
reject any run whose setup crosses the sampling boundary.

The sampler does not make dissimilar lifecycles comparable. The initial short
`fetch` versus persistent Chrome attempt was rejected. The promoted W1 court
instead creates one live target through each CDP server, verifies the same
semantic condition, holds it for the same two-second window, then closes and
reaps it through one orchestration contract. See
[`evidence/`](evidence/) and the reproducible macOS runner.
