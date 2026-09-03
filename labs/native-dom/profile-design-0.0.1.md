# Native engine-backed profiles: design, threat model and frozen court (P6)

Status: `implemented (court 80/82; D6 total-live criterion open)` — the at-rest and semantics decisions D1–D6 below were
settled before the first line of storage code; this document and
[`profile-court.py`](profile-court.py) are the pre-registered target, frozen
in a commit before the implementation commit.

## 1. Slice

One minimal, real vertical slice on the native bounded route:

- named persistent profiles and ephemeral profiles, created through the
  existing `profile.create {persistence, name?}`; `profile → session → target`
  identity is unchanged (`profile_<name>` for persistent, `profile_N` for
  ephemeral; a session opens against exactly one profile; every target belongs
  to that session);
- a real cookie jar per profile that the bounded network path honours
  (`Set-Cookie` on `target.open`, link navigation and `fetch()` responses;
  `Cookie` on the requests those paths send) and that the page realm sees
  through `document.cookie` under the HttpOnly rule;
- a real `localStorage` per profile, keyed by origin, that page scripts use;
- persistent profiles survive a host restart, ephemeral ones do not;
- single-writer ownership through an advisory lock; a second host is
  `profile_locked`;
- a corrupt, oversized, incompatible or over-permissive profile is listed
  unavailable and never prevents a healthy sibling from loading;
- per-profile entry and byte budgets, and `memory.report` owners for
  profiles with accounted bytes.

Out of scope for this slice: cache, history, downloads, permission prompts,
readonly/COW task profiles, `https`, third-party cookie policy, a public
suffix list, cookie prefixes, `Partitioned`, sessionStorage, IndexedDB.

## 2. Identity, lifetime and authority

| Object | Rule |
|---|---|
| profile id | `profile_<name>` for persistent (name: `[a-z0-9][a-z0-9-]{0,31}`), `profile_N` for ephemeral; never recycled within a host generation |
| session | one live profile authority; `session.open` acquires the writer lock of a persistent profile; `session.close` releases it after its targets closed |
| target | inherits its session's profile for cookies, storage and policy; a target never names a profile directly |
| cookie jar | owned by the profile; targets read and write only their own session's jar |
| localStorage | owned by the profile, keyed by origin `(scheme, host, port)`; a fixture target (no origin) gets the opaque origin `minicon-surf://court` and no persistence |
| capability | profile stays an owner kind; nothing here adds a new owner |

Restart semantics: a new host process with the same `--profile-root` lists
persistent profiles by directory name, validates each record, and marks the
invalid ones unavailable; ephemeral profiles never touch the root.

## 3. Threat model

Assets: cookie values and localStorage values (sensitive by nature even when
the court uses fake ones), profile policy, profile identity.

| Adversary / fault | Control | Court check |
|---|---|---|
| a page or script in one profile reading another profile's data | jars and storage are looked up by the session's profile; no operation takes a profile id from page content | cross-profile cookie and storage negatives |
| a page in one origin reading another origin's storage | storage keyed by origin; `document.cookie` filtered by domain, path and HttpOnly | origin negatives |
| another local user reading the files | directories `0700`, records and locks `0600`, permissions validated on load | permission check, over-permissive record → unavailable |
| disk theft or backup leak | envelope encryption with the master key in the macOS keychain (D1); no plaintext or key-file fallback | receipt records the at-rest mode; the fake cookie value must not appear in any file under the root |
| two hosts writing one profile | advisory `writer.lock` (flock); second host `profile_locked`; released when the last session closes | concurrent-host negative |
| crash mid-write | write to a temporary file, fsync, rename; a partial file is never the record | corrupt sibling court |
| a corrupt or incompatible record | strict parse, format version, bounds, name match; unavailable, siblings healthy | corrupt sibling court |
| unbounded growth | per-profile budgets (below); overflow is `resource_limit` without partial writes | budget negatives |
| memory | profiles are owners in `memory.report` with accounted bytes; footprint measured empty, live and post-close under default and arena | footprint rows |
| real browser profiles | never read, never migrated, never referenced; the court uses fake `court=alpha`-style values only | by construction |

## 4. At-rest strategies (decided: D1)

| Option | What is on disk | Protects against | Cost | Verdict |
|---|---|---|---|---|
| A. experiment-only plaintext store (the synthetic lab's `profile.json`) | JSON with cookie and storage values, `0600` | other local users only | none | acceptable only as an explicitly labelled experiment (`MINICON_SURF_PROFILE_STORE=experiment-plaintext`); cannot support a P6 conclusion; fixtures must stay fake |
| B. envelope encryption: a random 32-byte data key per profile, sealed with a master key; record = XChaCha20-Poly1305 over the JSON with a fresh 24-byte nonce and AAD = profile name + format version | ciphertext record + metadata (name, format, key id, nonce); the data key never on disk in the clear | disk theft and backup leak, plus A's controls | crates offline in the registry: `chacha20poly1305 0.10.1` (aead, chacha20, poly1305, cipher, generic-array, subtle), `getrandom`, `zeroize`; measured empty/live memory before adoption | **recommended**, subject to the master-key source below |
| B1. master key in the macOS keychain (`security-framework 3.7.0` → Security.framework generic-password item per host installation, `kSecAttrAccessibleWhenUnlocked`) | | the keychain's protections; no key file on disk | crates `security-framework` + `-sys` + `core-foundation`; macOS only; an unattended session with a locked keychain fails closed | preferred on macOS; needs a court that runs with the login keychain unlocked |
| B2. master key file under `--config-dir`, `0600` | key file next to the profiles | other local users only (same class as A for disk theft) | none beyond B | fallback only behind an explicit experiment knob; never the default |
| B3. user passphrase → argon2 | nothing but ciphertext | strongest | `argon2` is a release candidate offline; interactive, not Agent-native | rejected for this slice |
| C. every cookie as its own keychain item | items | keychain | churn, prompts, size limits, no portability | rejected |

### Decision D1 (recorded): B with B1 on macOS, the first real cell

- Data key: a random 32-byte DEK per profile from the system entropy source
  (`getrandom`); the record is XChaCha20-Poly1305 over the JSON with a fresh
  24-byte nonce per write; the DEK itself is sealed by the master key with
  XChaCha20-Poly1305 and its own fresh nonce.
- AAD binds both seals to `minicon-surf.profile-store/1` (store format),
  `minicon-surf.control/0.0.1` (protocol) and the canonical profile identity
  `profile_<name>` plus the record kind (`dek` or `record`), so a record or
  a sealed key moved between profiles, formats or versions fails to open.
- Master key: 32 random bytes that live only in the macOS keychain as a
  generic password item: service `minicon-surf.native-dom.profile-master-key`,
  account = the SHA-256 (first 32 hex digits) of the canonical `--profile-root`
  path, so each profile root has its own key; label
  `MiniCon Surf native-dom profile master key`; not cloud-synchronized
  (`kSecAttrSynchronizable` unset); accessibility is the login keychain's
  default (`kSecAttrAccessibleWhenUnlocked` semantics for legacy items); no
  access group (the binary is unsigned, so the data-protection keychain and
  access groups are unavailable). The item's ACL is bound to the creating
  application identity as the keychain sees it; a differently built or
  signed binary that cannot read it fails closed, it never prompts.
- No UI: `SecKeychainSetUserInteractionAllowed(false)` is set for the whole
  host lifetime, so any keychain operation that would prompt returns
  `errSecInteractionNotAllowed` and the host answers
  `unsupported_capability` (`keychain unavailable`). Keychain locked, denied,
  missing or interactive → the same typed refusal; persistent
  `profile.create` and `session.open` fail closed and ephemeral profiles keep
  working. There is no plaintext or key-file fallback of any kind.
- Key lifetime: the master key is fetched for one seal or open and zeroized
  immediately; the DEK lives in memory only while its profile is loaded and
  is zeroized when the profile is dropped or the host exits.
- Failure never overwrites: authentication failure, a corrupt record or a
  failed write leaves the existing files untouched; the profile is listed
  unavailable or the write reports a typed failure (section 7b).
- B2 (`MINICON_SURF_PROFILE_STORE=envelope-keyfile-experiment`) exists only
  as an explicitly labelled experiment knob for tests without a keychain; its
  receipts carry the mode `envelope-keyfile-experiment`, are never marked
  `observed`, and are never combined with B1 numbers. A second platform key
  source stays a P6 gap.

## 5. Cookie semantics: supported and loss matrix

The jar follows RFC 6265 storage and matching rules where the bounded
network path can honour them, and fails closed everywhere else.

| Attribute or rule | Supported | Loss (fail closed) |
|---|---|---|
| `name=value`, size ≤ 4,096 bytes per cookie | yes | larger cookies rejected |
| `Domain` | only when it equals the request host (case-insensitive) (D2) | any other domain, including parent domains and anything that would need a public suffix list, is rejected on this cell |
| `Path` | default-path rule and path-match | none |
| `Secure` | not on this cell: the court's transport is `http` only (D3) | Secure cookies are rejected on receipt here; this is a statement about this transport and cell, not a claim that Secure is unsupported by the design |
| `HttpOnly` | stored, sent, hidden from `document.cookie` | none |
| `SameSite=Strict` / `Lax` | sent on same-site requests only; all requests here are to the allowlisted origin(s), and a request whose origin differs from the document's is cross-site and does not carry the cookie | none |
| `SameSite=None` | requires `Secure`, unavailable on this `http` cell (D3) | rejected here |
| `Expires` / `Max-Age` | `Max-Age` wins; expired cookies deleted on receipt and on send | none |
| session cookies (no expiry) | live in the profile's volatile jar (D4): shared by every session of the profile, surviving a single `session.close`, discarded when the host's profile writer lifetime ends | never written to the persistent record; gone after a restart |
| `__Host-` / `__Secure-` prefixes | not supported | rejected |
| `Partitioned` | not supported | rejected |
| cookies per host / per profile | 32 / 256 | overflow is `resource_limit`, nothing evicted silently |
| `document.cookie` read | non-HttpOnly cookies matching the document origin | none |
| `document.cookie` write | same parser and rules as `Set-Cookie` | none |

### Jar layout (D4)

Each profile holds two jars with one matching rule: the persistent jar
(cookies with `Expires`/`Max-Age`, written through to the record) and the
volatile jar (session cookies, memory only). Both are consulted for every
request of every session of the profile; only the persistent jar reaches
disk. Ephemeral profiles have both jars in memory. The court sets a session
cookie in session A, closes A, opens session B on the same profile and sees
it sent; after a restart it is not sent while the persistent cookie is.

## 6. localStorage semantics

Origin-keyed (`scheme`, `host`, `port`); 32 keys per origin, 1,024-byte
values, 64 KiB accounted bytes per profile; `getItem`, `setItem`,
`removeItem`, `clear`, `length`, `key(i)`; overflow throws in the realm and
the host reports `resource_limit`; persistent profiles write through on every
mutation (atomic replace), ephemeral ones stay in memory; fixture targets
have the opaque origin and never persist.

## 7a. Write ordering (D5)

Order for a committed mutation of a persistent profile: the realm mirror
(what the page sees synchronously) → the host's in-memory profile → the
disk record (temporary file, `fsync`, atomic rename over the previous
record, directory `fsync`) → the operation result. Because `localStorage`
and `document.cookie` are synchronous inside the realm, the page observes
the mirror before the host can commit; therefore the operation that ran the
script (`target.open`, `target.act`, the fetch turn) is what reports the
commit: on a disk failure the host rolls the realm mirror and its own memory
back to the committed state, marks the profile's storage read-only for the
rest of the host lifetime so later `setItem`/`document.cookie` writes throw
in the realm, keeps the previous record untouched, and answers the operation
with `internal` (`storage_commit_failed`). Write amplification per
`localStorage` mutation is recorded by the court (writes per mutation, bytes
per write); a bounded batch may be designed later without weakening these
crash semantics.

## 7b. Pre-registered memory and cost caps (D6)

Baseline: the same binary with the store feature off (no `--profile-root`;
the keychain is never touched). With the keychain-backed store enabled:

| Cap | Limit |
|---|---|
| empty physical footprint delta | ≤ 524,288 bytes |
| empty RSS delta | ≤ 1,048,576 bytes |
| host-accounted live bytes per empty persistent profile | ≤ 65,536 bytes |
| two profiles with the court's fixture data | the accounted owner bytes equal the sum of what was stored, and live footprint stays well below the same-machine Lightpanda single-server empty footprint (8,356,392 bytes on the recorded court) |
| release binary size and dependency tree | recorded as deltas against the previous build; not a memory gate |

Exceeding a cap narrows or optimizes the slice; the cap does not move.

## 7. Budgets and owners

| Budget | Value |
|---|---|
| profiles per host | 8 |
| cookies per host / per profile | 32 / 256 |
| cookie bytes per cookie | 4,096 |
| storage keys per origin / values | 32 / 1,024 bytes |
| accounted bytes per profile (cookies + storage) | 128 KiB |
| record bytes on disk | 4 MiB |

`memory.report.owners.profiles` reports `objects`, accounted `bytes`,
`cookies`, `storage_keys` and the mode of the store; `profile.inspect`
reports counts and budgets, never values.

## 8. Frozen court

[`profile-court.py`](profile-court.py) pre-registers every check: two named
persistent profiles and one ephemeral, a page in each that receives a
`Set-Cookie` from the hermetic server and writes `localStorage`, an echo
endpoint that shows which cookies a request carried, cross-profile and
cross-origin negatives, the matrix negatives of section 5, budget negatives,
restart (persistent kept, ephemeral gone), a second host `profile_locked`, a
corrupt sibling unavailable while the healthy profile loads, `memory.report`
owners, at-rest verification (envelope mode: the fake value is absent from
every file under the root; plaintext mode: recorded as experiment), and
footprint at empty, live and post-close under the default allocator and the
arena. Its receipt names the at-rest mode and cannot pass in plaintext mode
with the status `observed`; it passes as `experiment-plaintext`.

### 8a. Court amendments (post-freeze, mechanism only)

Recorded in the script and in the lab README: the cookie fixture decodes
every percent-escape (the frozen fixture URLs escape `=` as `%3D`); the
echo page settles during load, so the court reads the snapshot before
waiting for a later revision; the write-amplification step opens a page
whose response sets a persistent cookie; the `profile.inspect` count
follows step 4b (four cookies, two persistent); the restarted host's
footprint is recorded as a supplementary number. No cap and no pass
criterion changed. Outcome on the recorded run: 80 of 82; the "well below
Lightpanda" criterion, frozen as below half of the single-server empty
footprint, is unmet on both allocators (6,111,688 / 5,849,592 bytes on the
churned host), so the receipt is `failed` and the slice is not `observed`.

Attribution after the verdict (`profile-attribution-court.py`, read-only,
no gate): under the default allocator the feature-off host with equal
churn already ends at 4,489,552 bytes, above the half line, from freed
blocks the default zone keeps (no close ever lowers the footprint); the
store adds 262,144 at enable and about 2.1 MB once at the first keychain
call (542,560 of it heap that never returns), while records, jar and
mirrors add 245,760 (default) / 507,904 (arena). The one fix candidate the
attribution supports, with pre-registered criteria in the lab README, is
keychain access outside the host process; it can only close the arena cell
of the criterion. Verdict on the cap: unchanged.

### 8c. Approved experiment: the bounded Keychain helper (arena cell)

Approved after the attribution with these fixed constraints, all recorded
in the code and the frozen `profile-helper-court.py`:

1. No bare `fork` in the multi-threaded host: the helper is spawned through
   `std::process::Command` with an absolute program path (the host's own
   executable), no pre-exec closure, no uid/gid/groups/chroot and no working
   directory, which is exactly the set of conditions under which Rust 1.97's
   standard library uses `posix_spawnp` on Apple targets and never falls
   back to `fork` (`library/std/src/sys/process/unix/unix.rs`, `spawn` and
   `posix_spawn`); the hidden subcommand `keychain-helper` of the same
   signed binary keeps the keychain ACL identity.
2. The helper is part of the complete process tree: the court samples host
   plus descendants at about one kilohertz through the whole run and reports
   the transient peak next to the recovered steady state, with every
   helper's pid, role, parent and lifetime.
3. Secrets travel only inside the two anonymous pipes: a fixed-length,
   versioned binary request (428 bytes: magic, version, op, account, bounded
   AAD, bounded payload) and response (144 bytes: status, OSStatus code,
   descriptor count, bounded payload); the host writes the request and
   closes stdin, reads exactly one response and requires EOF; a 10 s
   deadline kills and reaps the child as failure cleanup; both sides zeroize
   buffers and keys; both refuse core dumps; the helper refuses to serve if
   any descriptor beyond stdio is open; any extra output, short read,
   non-zero exit or malformed envelope fails closed. Nothing reaches argv,
   the environment, files or logs.
4. Wrap flow: the host generates the DEK; the helper fetches the master key
   and returns the authenticated wrapped DEK (wrap) or the DEK (unwrap); the
   master key never leaves the helper. The wrap AAD binds store format,
   protocol version, the canonical root (as its keychain account) and the
   profile, so a wrapped key swapped from another root or profile does not
   authenticate; the wrapped DEK is stored unchanged in the record and every
   committed mutation re-seals the record with the cached DEK, so writes
   never touch the keychain. Replay of an entire earlier record by a writer
   with directory access is outside the threat model's local-user boundary
   and is not detected.
5. Child lifecycle: one helper per persistent `profile.create` and per
   record open at store enable; always reaped; counters
   (`owners.profiles.keychain_helper`: spawns, failures, timeout kills,
   last pid, last lifetime, live) are the host's side of the attribution.
   Keychain refusals, a different `cdhash` and a locked keychain keep the
   fail-closed behaviour and never rewrite the record.
6. The frozen caps do not move; added: the complete-tree peak while a
   helper is alive must not exceed the in-process build's peak over the
   same operations, and no descendant may remain after any operation.
7. Verdict boundary: success can only move the arena cell of the P6 slice
   to an observed/keep candidate on this macOS cell; the default cell stays
   failed because feature-off churn alone crosses the line.

### 8b. Post-verdict security note: keychain ACL and the no-UI mode

The master-key item carries the Security framework's default ACL for an
ad-hoc, linker-signed binary: one application entry with a `cdhash`
requirement and a `partition_id` of the same `cdhash`. With user interaction
disabled, the creating build and any copy of it read the key again; a build
with a different `cdhash` gets `-25293` with no prompt and the host fails
closed (profile unavailable, `session.open` `not_found`, new persistent
profile `unsupported_capability`), leaving item and record untouched. D1's
no-UI mode is a fail-closed guarantee only; unattended use across rebuilds,
locked keychains or other user sessions is not claimed and needs a stable
designated requirement or an interactive grant outside the host
(`native-dom-control-0.0.2-keychain-acl-probe` receipt).

## 9. Dependencies

Every crate added for D1 is recorded in the lab README with version,
license, the registry checksum and who owns its security updates; the court
records the empty and live footprint deltas of section 7b under the default
allocator and the arena, and the arena's advantage must not be spent on
profile bookkeeping.
