# Chrome named baseline lab

## Hypothesis and scope

Chrome is a comparison baseline, not a proposed MiniCon Surf engine. The W1
court launches the installed executable directly with a fresh temporary
profile, loopback-only CDP, headless presentation and background features
reduced by documented command-line switches. It never reads or modifies the
user's normal Chrome profile.

Run `labs/court/run-w1-cdp-comparison-macos-arm64.sh` from the repository root.
The court gives Chrome and Lightpanda the same hermetic data-URL target, CDP
domains, semantic readiness condition and two-second live-target window. The
shared sampler observes the attributable descendant process tree and excludes
only the Python court orchestrator.

## Gaps and verdict

This remains an intentionally incomplete comparison. Summed `ps` RSS is not
private memory/PSS and may count shared pages more than once; engine feature
sets differ; a single static page says nothing about compatibility breadth,
post-close retention, soak behavior or marginal target cost. The installed
Chrome executable is named by version and digest in evidence but is not a
pinned downloadable artifact.

Verdict: **keep as a named contemporary comparison baseline**. This does not
select Chrome as a product route, and this single court cannot establish a
universal Chrome-to-Lightpanda ratio.
