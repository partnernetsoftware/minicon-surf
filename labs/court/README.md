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
[`fixtures/semantic-scripted.html`](fixtures/semantic-scripted.html). A runner
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

## Process-tree sampler

[`process-tree-sampler/`](process-tree-sampler/) is the first shared court
utility. It launches one command in a dedicated process group, recursively
attributes descendants by PPID, samples summed RSS, enforces a deadline and
emits argument-redacted JSON. `--exclude-root` permits two candidates to use
the same orchestration wrapper without charging that wrapper to either one.

The sampler does not make dissimilar lifecycles comparable. The initial short
`fetch` versus persistent Chrome attempt was rejected. The promoted W1 court
instead creates one live target through each CDP server, verifies the same
semantic condition, holds it for the same two-second window, then closes and
reaps it through one orchestration contract. See
[`evidence/`](evidence/) and the reproducible macOS runner.
