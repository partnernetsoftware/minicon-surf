# Navigation differential attribution 0.0.1 (frozen before any instrumentation)

Status: **design only.** Nothing is instrumented and nothing has run. This
court is authorised to answer one question and nothing else. It cannot
produce a pass, it replaces no receipt, and it may not trigger another
optimisation.

## 1. The one question

The 128-navigation differential soak is `narrow` on the default allocator.
Three batches on three builds read 1,064,960, 1,048,576 and 1,163,264 bytes
of excess over the control arm, a spread wider than the one repair tried
could shift, and the arena arm moved with it. **Where in a navigation do
those bytes appear, and how much of the excess is the navigation's own live
or retained state rather than the allocator's and the control plane's
general churn?**

That is all. The court does not propose a fix, does not evaluate a budget and
has no pass or fail.

## 2. Stages, frozen

Each navigation is sampled at these points, in this order, inside the host:

| Stage | What has happened by then |
|---|---|
| `request_parsed` | the request is decoded and the target resolved |
| `after_sync_io` | the profile's working copy is synced onto the target |
| `candidate_fetched` | the candidate document's bytes are fetched under its own fresh budget |
| `candidate_parsed` | it is parsed and its realm is seeded and run |
| `after_swap` | the candidate has replaced the live document and the old realm is dropped |
| `after_history_audit` | the history entry and the audit record are written |
| `response_serialized` | the result is serialised |
| `response_dropped` | the response bytes are written and freed |

The control arm is sampled at the stages it shares, so a differential exists
per stage rather than only per request.

## 3. What is measured

At every stage: the kernel's physical footprint and resident size, libmalloc's
in-use and allocated bytes and block count, the realm's arena statistics when
an arena serves it (used, blocks, high water, decommitted-from), and the
owners `memory.report` already reports, including the target's document and
lifetime counters, the history bytes and the audit ledger's entries, bytes and
reserved capacity.

The separation the court must make explicit:

- **navigation-specific live bytes**: what the new document, its realm, its
  history entry and its audit record hold while they are alive, which
  `owners` accounts directly;
- **navigation-specific retained bytes**: what is still resident after the old
  realm is dropped and its budget retired, that the owners no longer account;
- **general churn**: what the control arm shows at the same request count,
  which the control-plane churn court already attributed to freed small blocks
  the default zone keeps.

## 4. Method, frozen

- Fresh host process per run, one warm-up plus seven measured runs, both
  allocators, the same hermetic loopback origin and page set as the
  navigation court.
- Two arms with identical request counts, deadlines and target, exactly as
  the soak defines them: one navigating, one not.
- Per-run distributions are reported, not only medians: every run's value at
  every sampled point.
- **Observer effect** is measured, not assumed: each arm runs once with the
  in-process stage sampling on and once with it off, and the difference of
  the outside footprint readings is reported beside the results.
- Outside readings come from `proc_pid_rusage` on the host process, taken
  between requests, never from the host's own report.

## 5. Instrumentation rules

- Court-only and **disabled by default**: the stage sampling exists only when
  the court passes the existing court-only file and stage flags, exactly as
  the surface and control-churn courts already do.
- It adds no visual path, no AppKit, no window and no surface child.
- It changes no product behaviour: the same operations, the same budgets, the
  same policy, the same results.
- No pid, path, window or desktop fact is recorded.

## 6. What this court may not do

- It may not produce a pass or a fail, and its receipt says so.
- It may not overwrite or amend any prior receipt: the original failure, the
  replication and the rejected repair keep their history.
- It may not be followed by another optimisation in this increment. Its
  output is a recorded mechanism with its limitations, and then the work
  stops.
