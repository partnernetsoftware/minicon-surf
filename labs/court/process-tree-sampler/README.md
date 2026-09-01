# Process-tree sampler

This court utility launches one command in a new process group, periodically
samples the root and its recursively observed descendants, and emits one
sanitized JSON receipt. It is currently intended for macOS and other Unix
hosts whose `ps -axo pid=,ppid=,rss=` reports RSS in KiB.

```bash
cargo run --release -- \
  --deadline-ms 30000 \
  --interval-ms 25 \
  -- /path/to/candidate arg1
```

When a common wrapper launches the actual candidate, exclude the wrapper from
the memory total while retaining it as the attribution and cleanup root:

```bash
cargo run --release -- \
  --deadline-ms 30000 \
  --exclude-root \
  -- /path/to/wrapper candidate arguments
```

The receipt records either `root and recursive descendants` or `recursive
descendants only (root excluded)` in `measurement.scope`. `--exclude-root`
does not alter the deadline or process-group cleanup behavior.

The JSON includes only the executable basename and argument count. Arguments,
environment values, stdout, stderr, absolute paths, and host identity are not
recorded. Candidate output is inherited neither into the receipt nor the
sampler's output.

## Measurement semantics

Each sample parses the system process table, discovers descendants by PPID,
and sums RSS for the observed tree. `peak_tree_resident_bytes` is the maximum
sampled sum, not private memory, proportional set size, or live heap. Shared
pages can be counted in more than one process. Processes shorter than the
sampling interval and processes that reparent before observation can be
missed.

On deadline, the sampler sends `SIGKILL` to the dedicated process group and
reaps the root process. If the root exits by itself, the sampler preserves that
real exit code or signal and then performs a separate best-effort post-exit
`SIGKILL` of the group. The JSON distinguishes
`deadline_process_group_termination_requested` from
`post_exit_process_group_termination_requested`; post-exit cleanup does not
mean the candidate timed out.

Every loop takes its process-table sample before checking whether the root has
exited, so the final pre-exit attributable state is sampled. A child can still
exit too quickly to observe or be reparented between the process-table snapshot
and root exit observation. Process-group cleanup does not retroactively add
such a process to the memory measurement. A descendant that deliberately
creates a new session can escape process-group cleanup; browser candidates
under court must not daemonize or detach from the launched tree.

## Verification

```bash
cargo fmt --check
cargo clippy --all-targets --all-features -- -D warnings
cargo test --all-targets
```
