# Copy-on-write profiles — design-only audit, 0.0.1

Read-only and design-only, from `6374d62`. Nothing implemented, no protocol
field added, no court frozen, no user data touched — every measurement ran in a
temporary profile root that was deleted after it. No visual run, no navigation
soak, no download. Host `8d5da2a7…`.

## 1. What was measured

| question | measured |
| --- | --- |
| an empty persistent record on disk | **602 bytes**, one file: `<name>/profile.v1.sealed` |
| after 24 storage puts of 900 bytes | **29,710 bytes** |
| writes those 24 puts cost | **25 writes, 379,190 bytes** — every mutation rewrites the whole record |
| one `profile.storage.put` | **~12 ms** (11.5 first, 12.0 median, 12.1 last — flat) |
| one `profile.create` (persistent) | ~12–31 ms, the same order |
| profiles a host may hold | **8**; the ninth is `resource_limit`, *"profile capacity reached"*, retryable |
| a second host on the same root | sees the profile, and `session.open` is `profile_locked`, retryable |
| a second session on one profile | `resource_limit`, *"this profile owns one live session; close it first"* |
| `profile.inspect` from a second host | **succeeds without the lock** — counts are readable while another host writes |
| `profile.delete` | removes the directory entirely; nothing is left behind |
| an ephemeral profile on disk | nothing at all |
| a profile name | 1–32 lowercase letters, digits or hyphens; a duplicate is `conflict` |

## 2. The finding that decides the shape

**A profile's identity is inside its seal, so a fork cannot be a copy of bytes.**

Two measurements, on a record built and then copied by hand:

```
same bytes, new directory name  -> corrupt or incompatible:
                                   "format, protocol or profile mismatch"
same bytes, same name, other host -> corrupt or incompatible:
                                   "record does not authenticate"
```

The first fails because the profile id is sealed into the file and checked
against the directory it was found in. The second fails because the DEK is
wrapped under a master key bound to that host's key account
(`key_id: envelope-keychain:…`), so the bytes are not portable even under their
own name.

Therefore a fork **must** happen inside a live host that already holds both the
master key and the source's DEK: open the source record, re-seal the plaintext
under the child's own id and its own fresh DEK. There is no cheaper path, and
no path at all outside the host.

## 3. The finding that decides whether it is worth it

**The store is already copy-on-write, on every single mutation.**

`commit_control_mutation` clones the whole jar and storage as its rollback copy,
applies the change, then rewrites and re-seals the *entire* record — temporary
file, fsync, atomic rename, directory fsync. The measurement above is that
shape: 24 puts, 25 writes, 379,190 bytes for a record that ends at 29,710.

So a fork costs exactly what one ordinary write already costs, and a
copy-on-write *optimisation* saves nothing at the record level: there is no
incremental write to defer. What deferring buys is **602 bytes and one 12 ms
write**, until the child's first mutation.

**Copy-on-write is therefore not a performance feature here. Its value is
isolation semantics** — a child that starts from a parent's cookies and storage
and cannot write back to it — and it should be argued and ruled on that ground
alone. If it is ruled in for cost, the measurement says the cost was already
paid.

## 4. The lifecycle a fork joins

```mermaid
flowchart TD
  A["profile.create — persistent"] --> B["adopted at startup: id is profile_&lt;dirname&gt;"]
  B --> C["session.open — takes the writer lock"]
  C --> D["mutation: clone jar+storage, re-seal whole record, atomic rename"]
  D -->|"write fails"| E["read_only latch: no further writes this host"]
  C --> F["session.close — lock released"]
  B --> G["profile.delete — directory removed entirely"]
  B -.->|"fork: the only legal path"| H["open source record with its DEK"]
  H --> I["re-seal plaintext under the child's id and a fresh DEK"]
  I --> J["child profile: own directory, own lock, own budgets"]
  B -.->|"impossible: identity is inside the seal"| K["copy the file"]
  K -.-> L["profile mismatch / does not authenticate"]
```

The tree a fork creates is a **DAG of one generation at a time**: each child is
sealed under its own id, so nothing links it to its parent on disk. Provenance,
if it is wanted, has to be recorded in the child's record as data.

## 5. Loss matrix — what a fork cannot carry

| carried | not carried | why |
| --- | --- | --- |
| cookies (jar) | the parent's **writer lock** | one lock per directory; the child has its own |
| local storage | the parent's **DEK** | the child seals under a fresh key |
| the policy (network, permissions) — *if ruled* | the parent's **live uncommitted state** | a fork reads the last committed record; another host's in-flight state is invisible |
| accounted byte counts | the **failed-commit latch** | it is live host state, not record state |
| — | the **download budget** | live counters, never persisted (`downloads`, `download_bytes`) |
| — | **history** | P6 is not persisted at all yet |
| — | **cache** | it does not exist |
| — | the parent's **name** | names are unique; a duplicate is `conflict` |

The third row is the hazard worth naming: `profile.inspect` **succeeds from a
second host without the lock**, so a fork of a profile that another host has
open would silently copy a stale record. Either the fork requires the source's
lock — which means no live session on it — or the answer must say plainly which
revision it copied.

## 6. Protocol candidates

| candidate | shape | contract impact |
| --- | --- | --- |
| **A. `profile.create` gains `from`** | `{persistence, name, from}` | no new operation; the closed enum stays at 26; one optional argument, one new refusal set. The create is already the moment a directory and a DEK are made, which is exactly when a re-seal must happen. |
| B. `session.open` gains a copy mode | `{profile, mode: "fork"}` | conflates a session with a profile's existence; the child would outlive the session that made it, which the mode's own name denies. Rejected on those grounds. |
| C. a new `profile.fork` operation | 27th operation | breaks a closed enum that has held at 26 through three capabilities; nothing here needs a verb the create does not already have. |
| D. copy at delete/export time | an export/import pair | two operations, a serialized plaintext record crossing the protocol, and a new way to leak everything a profile holds. Rejected. |

**Recommendation: A.** It adds no operation, and it lands the work where the
key material is already being minted.

## 7. Dependencies and safe failures

- **Permissions**: the parent's policy is `{network, permissions}`. Copying it
  silently would hand a child `allow_by_default` without anyone asking for it;
  not copying it makes a fork behave differently from its parent for no visible
  reason. Recommended: **copy the policy, and report it in the answer**, so the
  inheritance is stated rather than assumed.
- **Downloads**: budgets are live counters and are never persisted, so a child
  starts with a full allowance. That is the honest answer, but it means a fork
  is a way to buy 32 more downloads; worth ruling explicitly.
- **History (P6)** and **cache**: nothing to carry, and nothing to decide until
  they persist.
- **Readonly sessions**: a readonly session must not be able to fork *from* its
  profile if the fork touches the source at all. It does not — the source is
  only read — so a fork under a readonly session is defensible; but a fork
  *creates* a profile, which is a write to the root. Recommended: refuse with
  the existing `session_read_only`, and let the ruling overturn it.

| situation | answer |
| --- | --- |
| the source does not exist | `not_found` |
| the source is open in another host | `profile_locked`, retryable |
| the source has a live session in this host | `resource_limit`, one live session |
| the eighth profile already exists | `resource_limit`, *profile capacity reached* |
| the child's name is taken | `conflict` |
| the child's name is malformed | `invalid_request`, the existing name rule |
| the source is an unavailable directory | `unsupported_capability`, its adoption reason |
| the source's record fails to open | `internal`, the existing store error, and **no child is created** |
| a readonly session asks | `session_read_only` (pending ruling) |
| the fork is asked of an ephemeral source | ruling: allowed and memory-only, or refused |

## 8. Cost, in the units already measured

| item | cost |
| --- | --- |
| a fork of an empty profile | one create: ~12 ms, 602 bytes |
| a fork of a filled profile (24 keys) | one re-seal: ~12 ms, 29,710 bytes |
| a fork of the largest legal profile | bounded by `MAX_RECORD_BYTES` 4 MiB and 128 KiB of accounted data |
| what deferring the copy would save | the child's first write only — 602 bytes and ~12 ms |
| slots | one of **8**, permanently, until deleted |

## 9. Court draft

1. A fork of a filled profile reproduces every cookie and storage key, by
   count and by accounted bytes, and the child reads back the same values.
2. The child is sealed under **its own** id: its file names the child, and the
   parent's file is byte-for-byte unchanged after the fork.
3. Writing in the child changes nothing in the parent — counts and record bytes
   on both sides, before and after.
4. Writing in the parent after a fork changes nothing in the child.
5. The child gets its own writer lock: both can hold a live session at once.
6. A fork of a profile locked by another host is refused `profile_locked`, and
   creates nothing.
7. A fork at the eighth profile is refused `resource_limit`, and creates
   nothing — no directory, no partial record.
8. A failed re-seal leaves **no** child directory and does not latch the parent.
9. The child's download budget, and whether the policy is inherited, match the
   ruling, and the answer states what was inherited.
10. No value, key, cookie or name from either profile appears in the audit
    ledger, an error, or a receipt — only counts and bytes.
11. An ephemeral source behaves as ruled, and leaves nothing on disk either way.
12. `profile.delete` of a parent leaves the child intact and readable.

## 10. Pending rulings

1. Candidate **A** (`from` on `profile.create`) versus the alternatives.
2. Whether the policy is inherited, and whether the download budget resets.
3. Whether a fork requires the source to have no live session, or reports the
   committed revision it copied.
4. Whether a readonly session may fork.
5. Whether an ephemeral profile can be a source.
6. Whether provenance (the parent's name) is recorded in the child's record, or
   deliberately not — it is the only way a fork could leak one profile's
   identity into another's.
