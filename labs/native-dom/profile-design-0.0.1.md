# Native engine-backed profiles: design, threat model and frozen court (P6)

Status: `design` — nothing below is implemented. The at-rest decision in
section 4 is open and must be settled before the first line of storage code.
This document and [`profile-court.py`](profile-court.py) are the pre-registered
target; the court fails today by construction.

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
| disk theft or backup leak | **open decision (section 4)**: plaintext is not acceptable for a product conclusion | receipt records the at-rest mode; envelope mode: the fake cookie value must not appear in any file under the root |
| two hosts writing one profile | advisory `writer.lock` (flock); second host `profile_locked`; released when the last session closes | concurrent-host negative |
| crash mid-write | write to a temporary file, fsync, rename; a partial file is never the record | corrupt sibling court |
| a corrupt or incompatible record | strict parse, format version, bounds, name match; unavailable, siblings healthy | corrupt sibling court |
| unbounded growth | per-profile budgets (below); overflow is `resource_limit` without partial writes | budget negatives |
| memory | profiles are owners in `memory.report` with accounted bytes; footprint measured empty, live and post-close under default and arena | footprint rows |
| real browser profiles | never read, never migrated, never referenced; the court uses fake `court=alpha`-style values only | by construction |

## 4. At-rest strategies (decision required)

| Option | What is on disk | Protects against | Cost | Verdict |
|---|---|---|---|---|
| A. experiment-only plaintext store (the synthetic lab's `profile.json`) | JSON with cookie and storage values, `0600` | other local users only | none | acceptable only as an explicitly labelled experiment (`MINICON_SURF_PROFILE_STORE=experiment-plaintext`); cannot support a P6 conclusion; fixtures must stay fake |
| B. envelope encryption: a random 32-byte data key per profile, sealed with a master key; record = XChaCha20-Poly1305 over the JSON with a fresh 24-byte nonce and AAD = profile name + format version | ciphertext record + metadata (name, format, key id, nonce); the data key never on disk in the clear | disk theft and backup leak, plus A's controls | crates offline in the registry: `chacha20poly1305 0.10.1` (aead, chacha20, poly1305, cipher, generic-array, subtle), `getrandom`, `zeroize`; measured empty/live memory before adoption | **recommended**, subject to the master-key source below |
| B1. master key in the macOS keychain (`security-framework 3.7.0` → Security.framework generic-password item per host installation, `kSecAttrAccessibleWhenUnlocked`) | | the keychain's protections; no key file on disk | crates `security-framework` + `-sys` + `core-foundation`; macOS only; an unattended session with a locked keychain fails closed | preferred on macOS; needs a court that runs with the login keychain unlocked |
| B2. master key file under `--config-dir`, `0600` | key file next to the profiles | other local users only (same class as A for disk theft) | none beyond B | fallback only behind an explicit experiment knob; never the default |
| B3. user passphrase → argon2 | nothing but ciphertext | strongest | `argon2` is a release candidate offline; interactive, not Agent-native | rejected for this slice |
| C. every cookie as its own keychain item | items | keychain | churn, prompts, size limits, no portability | rejected |

Proposal to settle before implementation: **B with B1 on macOS; without a
master-key source, persistent profile creation fails closed with
`unsupported_capability` while ephemeral profiles keep working; A only
behind the experiment knob, and every receipt records which mode ran.**
P6 does not close under A, and does not close under B either until a second
platform key source and broader behaviour exist.

## 5. Cookie semantics: supported and loss matrix

The jar follows RFC 6265 storage and matching rules where the bounded
network path can honour them, and fails closed everywhere else.

| Attribute or rule | Supported | Loss (fail closed) |
|---|---|---|
| `name=value`, size ≤ 4,096 bytes per cookie | yes | larger cookies rejected |
| `Domain` | only when it equals the request host (case-insensitive) | any other domain, including parent domains, is rejected: no public suffix list exists, so suffix matching cannot be made safe |
| `Path` | default-path rule and path-match | none |
| `Secure` | never accepted: every origin here is `http` | Secure cookies are rejected on receipt |
| `HttpOnly` | stored, sent, hidden from `document.cookie` | none |
| `SameSite=Strict` / `Lax` | sent on same-site requests only; all requests here are to the allowlisted origin(s), and a request whose origin differs from the document's is cross-site and does not carry the cookie | none |
| `SameSite=None` | requires `Secure`, which is unavailable | rejected |
| `Expires` / `Max-Age` | `Max-Age` wins; expired cookies deleted on receipt and on send | none |
| session cookies (no expiry) | live until the owning `session.close` | not kept across restart |
| `__Host-` / `__Secure-` prefixes | not supported | rejected |
| `Partitioned` | not supported | rejected |
| cookies per host / per profile | 32 / 256 | overflow is `resource_limit`, nothing evicted silently |
| `document.cookie` read | non-HttpOnly cookies matching the document origin | none |
| `document.cookie` write | same parser and rules as `Set-Cookie` | none |

## 6. localStorage semantics

Origin-keyed (`scheme`, `host`, `port`); 32 keys per origin, 1,024-byte
values, 64 KiB accounted bytes per profile; `getItem`, `setItem`,
`removeItem`, `clear`, `length`, `key(i)`; overflow throws in the realm and
the host reports `resource_limit`; persistent profiles write through on every
mutation (atomic replace), ephemeral ones stay in memory; fixture targets
have the opaque origin and never persist.

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

## 9. Dependencies and memory cost, to be recorded

Any crate added for section 4 is recorded with version, SHA-256 from the
registry cache, and the empty and live footprint delta it costs the host
under default and arena; the arena's advantage must not be spent on profile
bookkeeping (the court compares its footprints with the frame/realm court's).
