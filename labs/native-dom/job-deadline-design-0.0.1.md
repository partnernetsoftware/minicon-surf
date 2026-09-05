# The pending-job deadline escape — decision record, design only

Status: **design only. No product code, no court run, no protocol change.**
The behaviour at `cbf704e` is unchanged by this file. §7 lists what the root
must decide.

## 1. The mechanism, established rather than guessed

`Realm::eval_staged` does this, in this order:

```rust
self.runtime.set_interrupt_handler(Some(Box::new(move || Instant::now() >= deadline)));
let outcome = self.context.with(|ctx| ctx.eval(script) …);
self.runtime.set_interrupt_handler(None);   // ← the handler is removed here
…
self.drain_jobs(deadline);                  // ← and the jobs run with none
```

and `drain_jobs` is

```rust
while Instant::now() < deadline {
    match self.runtime.execute_pending_job() { Ok(true) => continue, _ => break }
}
```

So the deadline is checked **between** jobs and nothing at all checks it
**inside** one. A single queued job that never returns runs forever: the loop
never gets back to its own condition, and the interrupt that would have
stopped it was uninstalled two statements earlier. That is the whole
mechanism, and it is the second of the two candidates the ruling named — the
host drains outside the guard, rather than the engine ignoring an installed
interrupt.

**The engine honours the interrupt during a job.** A scratch build that moved
the uninstall to *after* the drain was measured against a page whose only
script is `Promise.resolve().then(function(){for(;;){}})`: `target.open`
returned in 3.0 s, exactly its 3,000 ms deadline, and the host answered the
next request normally. The same page hangs the shipped host indefinitely — the
root observed six minutes at full CPU. So `JS_ExecutePendingJob` runs the
runtime-level interrupt callback, and no API constraint stands in the way.
That scratch build was built to a scratch path, measured, and the source
restored; nothing of it is committed.

**A second defect the same probe exposed.** With the handler kept installed,
the interrupted job made `execute_pending_job` return an error, `drain_jobs`
swallowed it through `_ => break`, and `target.open` answered **`ok: true`**.
The page's own code was cut off in the middle and the caller was told the
document was built normally. Restoring the interrupt is therefore necessary
and not sufficient: the drain has to report what happened to it.

## 2. What the fix is

1. **The handler stays installed across the drain** and is removed after it,
   so every job shares the request's absolute deadline with the evaluation
   that queued it. One deadline per request, no second clock, no background
   thread, no virtual time.
2. **`drain_jobs` distinguishes its three outcomes** instead of one `break`:
   the queue emptied; a job threw; a job was interrupted at the deadline. Only
   the third changes what the caller is told.
3. **An interrupted drain fails its operation** with `deadline_exceeded`,
   which is what the same page's inline `for(;;){}` already answers today, and
   what a timer callback interrupted at the deadline already answers since the
   timer slice. A job that merely **threw** does not fail the operation: an
   unhandled rejection is the page's business, is counted, and the drain
   continues, which is what a browser does.
4. **The counters are attributed**, in the same style as the timer owner:
   jobs run, jobs that threw, and drains interrupted, each saturating and each
   a host-minted integer. No job source, no rejection value.

## 3. What it must not change

- **Promise and `queueMicrotask` stay.** Disabling either to make a court pass
  would be trading a correctness bug for a compatibility one, and the ruling
  forbids it explicitly.
- **Ordering stays.** While there is time, the drain runs jobs to completion
  in the order the engine queues them, so ordering within a turn is what it is
  today. Only the end of the drain changes: it can now end because the
  deadline arrived.
- **No bound on the number of jobs**, and this is a deliberate choice worth
  its own line: a count bound would cut a legitimate long microtask chain that
  finishes inside its deadline, which is a compatibility loss the deadline
  itself does not impose. The request's deadline is the bound, and a chain
  that outlives it is interrupted exactly like a single job that does. §7.2
  puts this to the root, because it is the one place a bound could reasonably
  be added.
- **`Date` and `performance` stay out of scope**, as ruled.

## 4. What every caller inherits

The same deadline already reaches all four paths, so the fix reaches them
without a new argument anywhere:

| Path | Where its jobs come from |
|---|---|
| document build | the page's own top-level scripts |
| timer callback | `timer_fire_script`, and anything the callback queues |
| action handler | the activation's events and their handlers |
| observation | a snapshot or revision read that runs page code at all |

## 5. The frozen court

`labs/native-dom/job-deadline-court.py`, hermetic, headless, both allocators,
frozen before the fix and failing until it exists.

**Safety first, because this court can meet a host that hangs.** Every host it
starts is supervised: the court sends each request on a worker with an
absolute wall-clock limit; when a host misses it, the court **kills that host
by pid, reaps it, and records the timeout as the falsification** — a check
that fails with a fixed reason, never a wait that continues. No group waits
unbounded, and the receipt says which hosts were killed.

Groups:

1. **An infinite job during `target.open`.** The open answers
   `deadline_exceeded` within its deadline plus a margin; the host answers the
   next request; no target is left behind.
2. **A finite chain.** A page queuing 1,000 jobs that each queue the next
   completes inside the deadline and the document is committed normally, so
   the fix does not cut short work that fits.
3. **A job queued by a timer callback.** The callback returns, the job it
   queued runs at that boundary, and an infinite one is interrupted with the
   timer counters and the job counters both accounting for it.
4. **A throwing job.** The rejection is counted, the drain continues, the
   operation succeeds, and the next observation is normal.
5. **Deadline typing.** The failure is `deadline_exceeded`, retryable, scoped
   to the target, with no job source or page text in its details.
6. **Atomicity and handler honesty.** A build interrupted in its drain commits
   no target and leaves no realm; an action interrupted in its drain keeps
   whatever its handlers completed, exactly as the form slice already records.
7. **Usability after interruption.** The same target answers `target.inspect`,
   `target.snapshot` and an action afterwards, and its revision is intact.
8. **Owners return.** After the interrupted operations and their closes, the
   realm and document owners return to the empty-host baseline and the job
   counters account for what ran, threw and was interrupted.
9. **Secrecy.** No job source, rejection value or page text in the ledger, the
   court log or the receipt.

## 6. Pre-registered criteria

| # | Criterion |
|---|---|
| J1 | an infinite job's operation answers within its deadline + 500 ms |
| J2 | a 1,000-job finite chain completes inside a 5,000 ms deadline and commits |
| J3 | after any interruption the next request answers within 1,000 ms |
| J4 | owners return to the empty-host baseline within 65,536 bytes |
| J5 | every host the court starts is either answered or killed and reaped; the receipt names any that were killed |

No cap here moves once frozen.

## 7. The decisions for the root

1. **Failing the operation on an interrupted drain.** §2.3 makes an
   interrupted drain answer `deadline_exceeded`. The alternative is to succeed
   and report the interruption only in the counters, which hides from the
   caller that the page's code was cut off. I recommend failing, because it
   matches what an interrupted inline script and an interrupted timer callback
   already answer.
2. **Whether to bound the number of jobs per drain at all.** I recommend no
   count bound, with the request deadline as the only bound, and the
   compatibility loss of a count bound recorded as the reason. If the root
   wants one, it needs a number and it needs recording as a loss.
3. **Whether an unhandled rejection should ever fail an operation.** I
   recommend never: it is the page's error, it is counted, and the drain
   continues.
