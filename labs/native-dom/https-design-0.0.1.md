# HTTPS for the native route: design and measurement before any dependency

Status: `implemented, opt-in` (candidate court and selection criteria
pre-registered before any candidate was measured; results in
`labs/tls-court/README.md`; cdx-k68 approved rustls + ring, and the slice
lives in the native host only behind `--pinned-root` plus an explicit
`https` allow-origin; section 9 records the native court).

## 1. Why now, and what this is not

P6 needs `Secure` cookies, and `Secure` cookies need a verified `https`
origin. The `http` court cell recorded D3 as a cell limit. This document
freezes the smallest HTTPS slice the native route could carry, the court
that would observe it, and the criteria for choosing a TLS stack, before
any TLS dependency is added. The first slice is not public-web HTTPS: it
allows only explicitly pinned test roots, and it makes no claim about
system roots, certificate transparency, revocation, OCSP, HTTP/2 or QUIC.

## 2. The slice (frozen)

- Origins: `--allow-origin https://host:port` joins the existing allowlist;
  the SSRF authorization (public address classes refused, resolver bounded,
  allowlisted origins only) applies unchanged to `https` targets.
- Roots: `--pinned-root FILE` (PEM, repeatable) is the only trust source of
  the slice. No system roots, no bundled roots. A server chain that does not
  end in a pinned root is refused before any byte of HTTP is read.
- Hostname: the certificate must carry a subject alternative name that
  matches the URL host (DNS name or IP address); the subject common name is
  ignored. Wrong-name and unknown-issuer failures are typed
  `permission_denied` with `details.reason` `tls_untrusted_root` or
  `tls_hostname_mismatch`; the page sees the same failed fetch it sees for a
  refused origin.
- Versions: TLS 1.3 preferred, TLS 1.2 accepted; nothing below. ALPN offers
  `http/1.1` only; a server that negotiates anything else is refused.
- Session cache: bounded and per host process (at most 8 entries) or
  disabled by a knob; never persisted; gone at exit.
- Redirects: every hop is authorized again against the allowlist and the
  address classes; `https` → `http` is refused as `permission_denied`
  `redirect_downgrade`; a hop to another origin is a cross-origin hop and
  carries no cookies and no `https`-only state; the redirect cap (3) stays.
- Caps unchanged: connect and per-fetch deadlines, header and body bounds,
  pending-per-turn, fetches and bytes per target, external scripts.
- Cookies: `Secure` cookies are accepted only from a verified `https` origin
  and sent only to a verified `https` origin, never on `http`;
  `SameSite=None` still requires `Secure`; the broader same-site context
  (site-for-cookies across schemes, schemeful same-site) stays a recorded
  loss, not a claim.
- Owners: `memory.report.owners.network` gains `tls_connections`,
  `tls_sessions_cached`, `tls_handshakes_total`, `tls_resumed_total`,
  `tls_refused_total`; every TLS connection and certificate object is
  released at `target.close` and counted to zero.

## 3. Candidate matrix (frozen axes)

Candidates are measured as standalone probes in `labs/tls-court`, never
inside the native host, so the default route's dependency tree does not
move before a verdict.

| axis | rustls 0.23 + ring 0.17 | rustls 0.23 + aws-lc-rs 1.18 | macOS Security.framework SecureTransport (security-framework 3.7) | Network.framework |
|---|---|---|---|---|
| language boundary | Rust; ring carries C and perlasm crypto | Rust; aws-lc is a C library built by cmake | Rust bindings over a system C framework (FFI) | no Rust bindings in the offline registry |
| TLS 1.2 / 1.3 | both | both | measured (the API declares `kTLSProtocol13`; deprecated since macOS 10.15) | not measurable here |
| roots | `RootCertStore` with pinned PEM only | same | `anchor_certificates` + `trust_anchor_certificates_only(true)` | – |
| hostname | rustls-webpki SAN matching, DNS and IP | same | `SecTrust` policy with peer domain name | – |
| ALPN | `alpn_protocols` | same | `alpn_protocols` (feature `alpn`) | – |
| session cache | `Resumption::in_memory_sessions(n)` / `disabled()` | same | `enable_session_tickets`; store not exposed | – |
| verifier state | in-process, dropped with the config | same | `trustd` (system daemon) outside the process tree | – |
| platforms | any | any (needs cmake) | macOS only, deprecated API | Apple only |

Recorded per candidate: Rust lines, `unsafe` occurrences, C and asm lines of
the dependency closure; crates added; release binary delta against the
feature-off probe; negotiated version, cipher, ALPN, resumption on the
second handshake; refusal of a wrong name, of an unpinned issuer, and of a
TLS 1.1-only server; memory at every stage of section 5 over the complete
process tree; teardown (in-use bytes after close); who owns security
updates on each platform.

## 4. Selection criteria (pre-registered)

A candidate is eligible for the slice only if all of the following hold on
the recording machine; the caps do not move after measurement.

| criterion | limit |
|---|---|
| S1 TLS 1.3 and TLS 1.2 negotiated with pinned roots only | both observed |
| S2 wrong-name, unpinned-issuer and TLS ≤ 1.1 servers refused before HTTP | all three refused |
| S3 ALPN `http/1.1` negotiated; anything else refused | observed |
| S4 bounded session cache: second handshake resumed or resumption disabled by knob, entries countable | observed |
| S5 TLS-enabled idle footprint over the feature-off probe | ≤ 524,288 bytes |
| S6 first handshake over idle | ≤ 1,048,576 bytes |
| S7 eight live connections over one | ≤ 131,072 bytes per connection |
| S8 libmalloc in-use after closing all connections | within 65,536 bytes of idle |
| S9 complete process tree during every stage | one process, no descendants |
| S10 teardown | connection and certificate objects counted to zero |

Recorded, not gated: Rust share and `unsafe` count, binary delta, crate
count, system-daemon involvement, deprecation status, cross-platform
responsibility. Among eligible candidates the recommendation prefers the
smaller closure of non-Rust code, then the smaller steady footprint.

## 5. Candidate court stages (frozen)

`labs/tls-court/court.py`, fresh process per run, one warm-up plus seven
measured, the probe driven over stdio, footprint and RSS sampled from
outside with the host-plus-descendants sampler of the helper court:

| stage | what happened |
|---|---|
| `empty` | probe started, no TLS objects |
| `idle` | TLS config built: pinned root loaded, provider installed |
| `first_handshake` | one connection, full handshake, one GET |
| `second_handshake` | the first closed, a new one opened (resumption observed) |
| `targets_1` | one live connection after the GET |
| `targets_8` | eight live connections |
| `post_close` | every connection closed |
| `post_trim` | after `malloc_zone_pressure_relief` (diagnostic) |

The feature-off probe is the same binary shape without a TLS stack doing
the same GETs over plain TCP. Negatives run once per build after the
measured runs. The hermetic server is Python's `ssl` on loopback.

Fixtures are never committed. The court generates a disposable test CA, a
loopback leaf with IP and DNS names, a wrong-name leaf and a leaf from a
second, unpinned CA with the `openssl` command line into a private
temporary directory (mode 0700, files 0600) before any probe process
starts, hands only file paths to the server and the probe, and deletes the
directory when the run ends, also on failure. The public repository keeps
the generation logic and its non-secret parameters (P-256 keys, SHA-256,
the SAN lists, the validity period) and records public evidence only: each
certificate's SHA-256 fingerprint, subject, SAN, key algorithm and validity.
Generation is a separate phase, timed and recorded, and does not overlap
any client measurement. A fixed private fixture set, if ever needed to
reproduce a specific run, is injected only through an explicit
`--tls-fixture-dir` pointing outside the repository; without it the court
generates, and if generation is impossible it fails closed. Nothing is
downloaded. The court refuses to write a receipt that contains a private-key
block or the temporary path.

## 6. The native HTTPS court (frozen, unexecuted)

`labs/native-dom/https-court.py` pre-registers the checks the native slice
must pass once a candidate is chosen and approved: `https` origin allowed
only with a pinned root; the fixture pages served over TLS pass the same
document, script and cookie checks as over `http`; a `Secure` cookie set by
the `https` origin is sent back over `https`, hidden from the `http` origin
of the same host and never sent there; `SameSite=None; Secure` is accepted
over `https`; wrong-name, unpinned-issuer and downgrade redirects are typed
refusals with the record unchanged; the redirect cap, deadlines and byte
bounds are unchanged; owners are zero after close; footprint stages
feature-off, TLS idle, first and second handshake, one and eight targets,
post-close, all over the complete process tree, under the default allocator
and the arena. Its flags (`--pinned-root`, `--allow-origin https://…`) are
the design's; an implementation must honour them or the court is amended
with a recorded reason before it runs.

## 8. Result of the candidate court (recorded after the freeze)

`labs/tls-court/evidence/tls-court-0.0.1.json`, 60 of 65: rustls 0.23.43
with the ring provider meets S1–S10; rustls with aws-lc-rs fails S8 (153,088
bytes of heap kept after close); SecureTransport fails S1 (TLS 1.2 only:
the platform refuses a TLS 1.3 maximum with `-9830`), S5 (524,312, over by
24 bytes), S6 (1,359,968) and S8 (185,552), and its verification runs
partly in `trustd`/`securityd` outside the process tree. Two mechanism
amendments are recorded: the rustls client cache must hold 16 entries (two
server slots) for resumption to be observable, so section 2's "at most 8
entries" reads "16 entries, per process, finite" for that stack; the probe
leaves SecureTransport's platform maximum in place. Recommendation: rustls
+ ring for the pinned-roots slice, subject to ruling; the slice's rules in
section 2 are unchanged, and Network.framework stays unmeasured.

## 9. The native slice and its court (recorded after the verdict)

Implemented as approved (verdict boundaries 1–7): `--pinned-root` loads
public certificates under fixed bounds and selects the ring provider once;
`https` without a pinned root is `unsupported_capability`
`tls_no_pinned_roots`; TLS 1.3/1.2, ALPN `http/1.1` only, names and IP SANs
verified in process, exact-address authorization before the connect,
re-authorization per redirect hop with `https` → `http` refused as
`redirect_downgrade`, the http caps and deadline unchanged, a 16-entry
session cache per profile (section 2's "at most 8 entries" reads "16
entries, two server slots" for rustls), `Secure` and `SameSite=None` rules
as in section 2, atomic failed navigation. `labs/native-dom/https-court.py`
passes 74 of 74 under both allocators with H1–H4 at 0 / 32,768, 163,840 /
262,144, 32,768 / 79,872 and 44,848 / 12,896 bytes against caps of
524,288, 1,048,576, 131,072 and 65,536; the binary grows by 1,502,608
bytes; the court's 40 KB header fixture exposed a chunk-granular header
cap that was fixed to an exact bound with cap±1 checks before any push; the P6
v1 court and every regression hold. Recorded mechanism amendments: the
empty sample follows the first request; ephemeral profiles on both sides;
the click carries a node reference; redirect landing read from any role;
private-address redirect through an https target; a 40 KB header fixture;
the cross-profile check counts one full handshake among three fetches. The
verdict is `keep` as opt-in; ring's C and perlasm stay visible.

## 7. Out of scope of the slice

System roots, revocation, CT, client certificates, HTTP/2 and QUIC, mixed
content rules beyond the cookie rule above, HSTS, and any statement about
the public web.
