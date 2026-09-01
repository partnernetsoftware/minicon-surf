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

The first Lightpanda receipt intentionally remains `incomplete` until its
same-machine browser baseline and stronger process-tree sampling exist.
