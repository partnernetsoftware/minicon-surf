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

## 7. Result of the one run (recorded mechanism, no verdict)

Build `3089ab5d…`, one run, one warm-up plus seven measured runs, both
allocators, four cells each. Receipt
`native-dom-control-0.0.2-navigation-attribution`. This is a mechanism, not a
judgement: the court has no pass and no fail, and no prior receipt changed.

**Where the bytes appear.** Almost all of the differential is one stage, the
step from the candidate's bytes having arrived to its document existing:
parsing it and seeding and running its realm. Summed across 128 navigations
under the default allocator that stage grows the footprint by 999,424 bytes,
on 22 of the 128 navigations, while the whole navigating arm's excess over
the control arm is about 1.0 to 1.15 MB. Nothing measurable appears at the
swap itself, at the history and audit write, or at serialising the result.

**What the navigation actually holds.** After 128 navigations the owners this
slice added hold 264 bytes of history and 5,150 bytes of audit ledger, of
which 5,120 is the reserved ring. That is roughly half a percent of the
differential. libmalloc's in-use rises about 29.7 MB across the run at the
parse stage and falls about 29.6 MB at the swap, so each document's
allocations are made and the previous document's are freed every time and
almost nothing accumulates in the allocator's own accounting.

**What is left is unowned residue.** After `target.close` and
`session.close` every navigation owner reads zero, yet the process is still
1,081,344 bytes above its base under the default allocator and 442,344 under
the arena. The arena's own figures show why: the same parse stage adds
45,681,664 bytes across the run and the drop of the replaced realm returns
45,075,456 of them, so the pages a realm uses come back when an arena owns
them and stay resident when the default zone does.

**So the differential is page-granular allocator retention of a realm that is
built and destroyed once per navigation**, not a structure this slice keeps.
That also explains the instability across batches: three batches read
1,064,960, 1,048,576 and 1,163,264, because what is being counted is how many
freed pages a zone happens to hold, not a quantity the code controls.

**Observer effect, measured not assumed.** The differential with the
in-process sampling on was lower than with it off, by 131,072 bytes under the
default allocator and 49,152 under the arena. The sampling does not inflate
the result; the sign is negative and the size is inside the run-to-run
spread, which is itself further evidence that this measurement is noisy at
this granularity.

## 8. Limitations

- One hermetic origin on loopback and one page pair; macOS only; one build.
- The two arms differ in the operation under test, which is the question; the
  request count, the deadline and the target are identical.
- Per-stage figures are sums across a run of medians across runs, so a single
  navigation's cost is an average, not a distribution.
- The court says where the bytes are, not what to do about them. No
  optimisation follows from it in this increment, and none was attempted.
