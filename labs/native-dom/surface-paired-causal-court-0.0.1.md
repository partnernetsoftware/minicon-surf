# Paired causal G3 court 0.0.1 (pre-registered design; no run, no receipt)

Status: design accepted by cdx-k68 for headless implementation only.
The counterfactual child mode (§4) and the harness
(`surface-paired-causal-court.py`) exist; arm B has run headless (§9);
arm A has not run and is refused by the harness until the OWNER's
explicit permission for a run exists. No paired differential has been
computed and nothing here is a pass. It does not replace the frozen
native court (106 of 110, `narrow`) and moves no cap: the absolute S2
and S3 of `surface-ipc-0.0.1.md` §6 stay in force and are reported by
reference wherever this court reports.

## 1. Question

The frozen court's S2 and S3 measure the host's absolute post-hide
footprint on a live target. After the frame region and two attributions
(§8, §9, §11 of the IPC design; the control-churn receipt) that
residual is page-granular churn of the whole control plane: every
control request grows the host by 0.1 to 1.5 KB with no plateau by 128
requests, born in the realm evaluations and the response serialization,
with libmalloc in-use returning each time. That churn exists with no
surface at all (M2 / G1: generic allocator retention). The G3 question
is different: can the same live target be attached to a real presentation
and detached again with its state kept, and does the presentation leave
anything of its own behind? This court answers the second half causally:
it measures the presentation's own retention as the difference between
two arms that are identical in everything except the presentation.

## 2. Arms

- **A, real**: the surface child in window mode (AppKit, a real floating
  window that never becomes key). Runs only with `--visual`,
  `MINICON_SURF_ALLOW_VISIBLE_COURT=1` and the OWNER's permission for
  that run, once, by hand, in the foreground.
- **B, counterfactual**: the same surface binary in a court-only
  no-AppKit mode that replays the arm's input script (see §4). Headless;
  may run at any time; alone it yields `unverified` for the paired
  result, never a pass.

Both arms use the same host binary, the same fixture (the representative
page over the hermetic loopback server, as the frozen court), the same
allocator setting per cell (default and arena), a fresh host per run,
one warm-up plus seven runs, three show/hide rounds per run, and the
runs interleaved A, B, A, B … so drift affects both alike.

## 3. Identical sequence (per round, both arms, no exceptions)

1. `surface.show {target}`; wait for the court-only `shown` event.
2. Input 1: a click at the button's row (coordinates from the `shown`
   event's layout of a probe run on the same page, identical in both
   arms; every run checks its own `shown` layout has the same rows),
   bound to the acknowledgement of frame 1; wait for `input_applied` and
   `repainted`; `target.inspect`.
3. Input 2: a scroll of one row, bound to frame 2 (the repaint after the
   click); wait; `target.inspect`.
4. Input 3: a scroll back, bound to frame 3; wait; `target.inspect`;
   `memory.report`.
5. `surface.hide {surface}`; wait for `hidden`; `target.inspect`;
   `memory.report`.

Amendment (mechanism, recorded at implementation): the inputs are bound
to frame acknowledgements because the counterfactual child can only act
on what it receives; arm A therefore posts its real events at the same
points (right after `shown` and after each `repainted`), so the host
sees the same order in both arms. Per round both arms issue 1 show, 4
inspects, 2 memory reports and 1 hide (12 inspects, 6 reports, 3 shows
and 3 hides per run, recorded in the receipt).

Every operation, its arguments, the order, the number of snapshots,
inspects and memory reports, and the revision changes the inputs cause
are the same in A and B. In A the inputs are real CoreGraphics events
posted by the court at the window's coordinates after confirming the
topmost window there is the surface child (as the frozen court does);
in B the same coordinates are replayed by the child (§4). The host's
path from `INPUT` onward (hit map, `act_script`, repaint) is the same
code in both arms. Nothing the court does in A that touches the host
differently is allowed: the own-window capture stays in the court
process and is not performed in B, and it is not counted in either arm.

## 4. The counterfactual child mode (court-only, implemented)

`native-dom-surface <generation> replay:<script>` answers `HELLO` with
`READY` (window number 0, the frame's size as content size), acknowledges
every frame, and after the acknowledgement of frame *k* sends the
scripted `INPUT` events bound to *k* (kind, x, y, delta), within the
existing 64-per-second limiter and bounds; it never touches AppKit, maps
neither AppKit nor CoreGraphics, and exits by `CLOSE`/`CLOSED` as the
other modes do. The script is a bounded ASCII string (≤ 256 bytes, ≤ 16
events). Its coordinates are derived by the court from the same layout
the real arm uses, so the events the host receives are byte-identical
to what the real child sends for the real events at those coordinates.

## 5. Measurements (both arms, same instruments)

- Outside: footprint and RSS of the host (`proc_pid_rusage`), and the
  complete tree peak while shown (the sampler of the frozen court).
- Inside, court-only (`--surface-court-stages 1`): the show and hide
  stages of §8, the snapshot stages of §9 and the request stages of §11
  with the operation name, each with libmalloc in-use and allocated and
  the realm's arena statistics; `owners.surfaces` (objects, bytes,
  `frame`, `process`) at headless, shown and post-hide.
- Latencies: show, `READY`, first frame, hide.

## 6. Analysis (pre-registered)

For every quantity *q* sampled at a stage *s* in round *r*, the paired
differential is `D(q, s, r) = median_A − median_B` over the seven runs.

- **Presentation-specific retention**: `D(footprint, post_hide, r)` for
  r = 1..3, and its slope `D(footprint, post_hide, 3) − D(…, 1)`.
- **Presentation-specific owners**: `D(owners.surfaces.bytes, post_hide)`
  must be 0 (both arms unmap the frame), `D(frame.touched_bytes, shown)`
  must be 0 (both paint the same mapping).
- **Generic churn**: arm B's absolute post-hide over headless and slope,
  reported beside the frozen S2 and S3 readings of arm A; both are
  expected to fail the absolute caps as before, and that failure is
  attributed to M2 / G1 only if arm B shows it alone.
- **Detach**: in A, the window count owned by the child is 0 after every
  hide (court-side window list), the child is reaped, `owners.surfaces`
  is 0/0; in B the same minus the window.

Pre-registered thresholds for the paired result (not caps on the frozen
court; they bind only this court): `D(footprint, post_hide, r)` ≤ 65,536
in every round (four pages: the pipes, the reader thread's stack and the
window's `READY` facts, all returned), the slope of D ≤ 32,768, and the
owner differentials exactly 0. If they hold, the presentation is shown to
leave nothing of its own in the host and the absolute residual is the
control plane's; if `D` exceeds them, the excess is a surface-specific
retention to attribute by stage before any candidate.

## 7. Confounders and rules

- Run order interleaved; the same machine, session and allocator per
  pair; both arms in one invocation of the court so the pairing is real.
- No cap moves; the frozen court is not rerun by this court; its
  numbers are quoted from its receipt.
- The visual arm's rules are AGENTS.md's: three-part opt-in, once, by
  hand, no focus stealing, cleanup on SIGINT and SIGTERM, window-list
  hygiene before and after; the court refuses to start the visual arm
  when any part is missing and then runs arm B only, reporting
  `unverified` for the paired result.
- No pid, path, window number, coordinates, capture or desktop content
  in the receipt; court-only facts stay in the court-only file.

## 9. Arm B, observed (headless; no pass, no differential)

`native-dom-control-0.0.2-surface-paired-causal` receipt, status
`unverified-headless-counterfactual`, evaluation
`pending-owner-authorized-visual`, arm A `not_observed`, D not
computable. Arm B (replay child, 55-byte script of three events), one
warm-up plus seven runs, three rounds, all 7 of 7 runs valid under both
allocators (layout matched the probe, all three inputs applied and
repainted every round, revision advanced, child reaped by protocol,
owners 0/0 after every hide, the frame's 1,032,192 touched while shown
and 0 after). Post-hide over headless, rounds 1 / 2 / 3, slope, and the
in-process retention over `show_entry`:

| allocator | post-hide over headless | slope | in-process retained |
|---|---|---|---|
| default | 294,912 / 409,600 / 442,368 | 147,456 | 294,912 / 114,688 / 32,768 |
| arena | 294,912 / 475,136 / 524,288 | 229,376 | 262,144 / 147,456 / 32,768 |

These are the counterfactual's absolute numbers under the frozen
court's own sequence shape (with inputs), for comparison with arm A when
it is authorized; they are not evaluated against any threshold here.
Hygiene: no window owned by the child and no surface process before or
after; the harness refuses to start when the visible-court variable is
set and refuses `--visual` in this revision.

## 8. Rejected idea, recorded

A realm-side snapshot memo keyed by the revision was proposed after the
churn attribution and rejected by ruling: the real surface changes the
revision by input every round, so the memo would miss where it matters,
and it would keep a JSON string resident in the realm with invalidation
to maintain, against memory-first. It stays a rejected idea.
