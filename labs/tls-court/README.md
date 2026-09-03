# tls-court: TLS candidate probes for the native route's HTTPS design

Standalone measurement lab for
[`labs/native-dom/https-design-0.0.1.md`](../native-dom/https-design-0.0.1.md).
Nothing here is linked into the native host; the design's candidate matrix
and selection criteria were frozen before any candidate was measured.

`tls-probe` is one binary shape with the TLS stack chosen at build time
(`--features rustls-ring`, `rustls-aws-lc`, `secure-transport`; no feature
is the plain-TCP control). `court.py` builds each probe, runs a hermetic
loopback TLS server, drives the probe over stdio through the frozen stages,
samples the complete process tree, runs the negatives and evaluates the
pre-registered criteria S1–S10.

## Key material

No private key is committed or kept. Before any probe starts, the court
generates a disposable test CA, a loopback leaf (`IP:127.0.0.1`,
`DNS:localhost`), a wrong-name leaf and a leaf from a second, unpinned CA
with the `openssl` command line into a private temporary directory (0700,
files 0600), and deletes that directory when the run ends, also on failure.
Only public evidence reaches the receipt: SHA-256 fingerprints, subjects,
SANs, key algorithm and validity. Generation is a separate, timed phase
that never overlaps client measurement. A fixed fixture set can be injected
only through an explicit `--tls-fixture-dir` outside the repository (the
expected file names are listed by `court.py --help`); without it the court
generates; if generation is impossible it fails closed and never downloads.
The receipt writer refuses any content with a private-key block or the
temporary path. `.gitignore` and `AGENTS.md` reject key files and
private-key blocks independently.

Results are recorded in the section below once cdx-k68 has approved the
measurement run.
