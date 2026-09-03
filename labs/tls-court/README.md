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

## Results (`evidence/tls-court-0.0.1.json`, 60 of 65 checks; the frozen S1–S10 per stack)

Recording cell: macOS on Apple silicon, Rust 1.97.0, `openssl` 3.6.4 for
fixture generation (0.092 s, before any probe started), Python `ssl` on
OpenSSL 3.6.4 as the loopback server (court infrastructure, never sampled),
one warm-up plus seven measured runs per stack, fresh probe process per
run, host-plus-descendants sampled at about one kilohertz. Certificates:
P-256, SHA-256, 30-day validity; their fingerprints are in the receipt, no
key ever left the temporary directory, which was gone after the run.

| criterion | rustls + ring | rustls + aws-lc-rs | SecureTransport |
|---|---|---|---|
| S1 TLS 1.3 with the pinned root; TLS 1.2 against a 1.2-only server | TLSv1_3 / TLSv1_2: yes | yes | **no**: TLS 1.2 only; `SSLSetProtocolVersionMax(kTLSProtocol13)` is refused with `-9830` on this macOS |
| S2 wrong name, unpinned issuer, TLS 1.1-only refused before HTTP | yes (`certificate not valid for name`, `UnknownIssuer`, `ProtocolVersion` alert) | yes | yes (`-67602`, `-25318`, `-9801`) |
| S3 ALPN `http/1.1`; h2-only server yields no `http/1.1` | yes | yes | yes |
| S4 second handshake resumed with a bounded cache | yes, 7 of 7 (16 entries; see amendment) | yes, 7 of 7 | not observable: the API exposes no resumption fact (recorded) |
| IP SAN (`127.0.0.1`) verifies | yes | yes | yes |
| S5 TLS-enabled idle over the plain probe (≤ 524,288) | 65,536 | 131,072 | **524,312** |
| S6 first handshake over idle (≤ 1,048,576) | 32,768 | 278,552 | **1,359,968** |
| S7 per extra live connection, eight over one (≤ 131,072) | 42,130 | 58,514 | 42,130 |
| S8 libmalloc in-use after closing all, over idle (≤ 65,536) | 3,072 | **153,088** | **185,552** |
| S9 one process, no descendant at any stage | yes | yes | yes (but two extra threads while TLS is live: 3 against 1) |
| S10 live connections zero after close | yes | yes | yes |
| eligible | **yes** | no | no |

Stage medians in bytes (physical footprint / RSS / libmalloc in-use):

| stage | plain | rustls + ring | rustls + aws-lc-rs | SecureTransport |
|---|---|---|---|---|
| empty | 1,032,504 / 1,638,400 / 20,656 | 1,081,656 / 1,703,936 / 20,672 | 1,147,192 / 1,851,392 / 20,672 | 1,556,816 / 6,045,696 / 89,376 |
| idle (config built) | 1,098,040 / 1,802,240 / 21,760 | 1,163,576 / 2,211,840 / 24,384 | 1,229,112 / 2,392,064 / 24,400 | 1,622,352 / 6,291,456 / 93,360 |
| first handshake | 1,114,424 / 1,818,624 / 21,840 | 1,196,344 / 2,883,584 / 36,000 | 1,507,664 / 3,866,624 / 186,128 | 2,982,320 / 9,404,416 / 314,864 |
| second handshake (resumed) | 1,114,424 / 1,818,624 / 21,840 | 1,229,112 / 2,916,352 / 35,840 | 1,589,584 / 3,948,544 / 185,968 | 3,047,856 / 9,469,952 / 310,144 |
| eight live | 1,114,424 / 1,818,624 / 22,016 | 1,524,024 / 3,211,264 / 95,040 | 1,999,184 / 4,358,144 / 245,840 | 3,342,768 / 9,764,864 / 529,280 |
| after every close | 1,114,424 / 1,818,624 / 21,888 | 1,540,408 / 3,227,648 / 27,456 | 1,999,184 / 4,358,144 / 177,488 | 3,342,768 / 9,764,864 / 278,912 |
| complete-tree peak | 1,114,424 | 1,573,176 | 2,015,568 | 3,342,768 |

`malloc_zone_pressure_relief` released nothing in any stack. The footprint
kept after the closes (rustls + ring: 376,832 over idle with in-use back
within 3,072) is the default zone's reservation, the retention already
attributed for the native host; it is not a TLS owner.

Builds and boundaries (release, `--locked --offline`):

| stack | binary bytes | delta over plain | crates | dynamic libraries beyond libSystem/libiconv | threads while TLS is live | verifier | platform services |
|---|---|---|---|---|---|---|---|
| plain | 579,408 | – | 8 | none | 1 | – | – |
| rustls + ring | 2,108,784 | +1,529,376 | 19 | none | 1 | rustls-webpki in process | none |
| rustls + aws-lc-rs | 3,646,656 | +3,067,248 (built offline in 18.4 s without cmake through aws-lc-sys's own C build) | 18 | none | 1 | rustls-webpki in process | none |
| SecureTransport | 689,296 | +109,888 (the stack is the system framework) | 13 | CoreFoundation, Security | 3 | `SecTrust` in process plus `trustd`/`securityd` | unattributed: not descendants, not measured |

Dependency closure, counted from the vendored sources: rustls + ring is
94,338 Rust lines with 279 `unsafe` occurrences plus 31,209 C and 199,234
perlasm/assembly lines inside `ring`; the SecureTransport bindings are
158,503 Rust lines (129,990 of them `libc`) with 1,305 `unsafe`
occurrences and every TLS byte handled by the closed system framework.
aws-lc-rs adds the aws-lc C library (not counted line by line here).

Amendments after the freeze, mechanism only, recorded in the receipt: the
rustls in-memory client cache sized at 8 sessions or fewer rounds to one
server slot that `LimitedCache` evicts right after the first insert
(`limited_cache.rs`, "ensure next insertion does not require a realloc"),
so `in_memory_sessions(4)` never resumed; 16 entries is the smallest bound
under which resumption is observable and the court uses it. The design's
"at most 8 entries" therefore has to read "16 entries, two server slots" in
rustls terms, still finite and per process. SecureTransport refuses an
explicit TLS 1.3 maximum, so the probe leaves the platform maximum in place
and records the negotiated version. Crate counts were recomputed from the
same lock after the run because the run's tree call carried an unsupported
flag.

Recommendation (for cdx-k68's ruling, nothing merged): rustls 0.23 with the
ring provider is the only candidate that meets S1–S10 on this cell. It
negotiates TLS 1.3 and 1.2 with pinned roots only, verifies names and IP
SANs in process, refuses the three negatives before HTTP, resumes with a
finite per-process cache, costs 65,536 bytes idle, 32,768 for the first
handshake and about 42 KB per live connection, returns its heap after
close, stays one process and one thread, and adds no dynamic library. Its
price is 1.53 MB of binary, 11 crates, and ring's C and perlasm crypto
inside an otherwise Rust closure. aws-lc-rs doubles the binary delta and
keeps 153 KB of heap after close. SecureTransport is TLS 1.2-only here,
deprecated, spends two threads and system daemons outside the tree, and
misses three memory criteria. None of this is a public-web claim: pinned
test roots on loopback only.
