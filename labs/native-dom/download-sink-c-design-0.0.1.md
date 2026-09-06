# Sink C — the bytes come back over the protocol, design-only 0.0.1

Design-only and read-only. Nothing implemented, no protocol changed, no court
frozen, D6 and G1 untouched, no navigation soak, no visual run, and nothing was
downloaded. Ruled: **sink C** — download bytes return through the existing
control protocol, the filename is a bounded reported string and **never** a
path, the request is an **action kind** on `target.act`, the permission is
checked **at use** with `permission_denied`, and `session_read_only` and
`unsupported_capability` stay distinct.

## 1. The bounds this has to live inside, measured

| bound | value | source |
| --- | --- | --- |
| control **request** | 65,536 bytes | the contract's `MAX_REQUEST_BYTES`, enforced by the host's line reader |
| control **response** | 4,194,304 bytes | the contract's and the host's `MAX_RESPONSE_BYTES` |
| network response body | 1,048,576 bytes | the network layer's own cap |
| a download today | refused after the fetch, so the host already buys up to the network cap | measured in the downloads audit |

So the transport ceiling is **4 MiB per answer**, and the network layer will
not hand over more than **1 MiB** in one fetch anyway. A design that pretends
to stream gigabytes through this protocol would be lying about both.

## 2. The shape

```
   target.act {kind: "download", reference}
        |
        +-- permission?  deny_by_default -> permission_denied (at use)
        +-- session readonly? -> session_read_only   (a different question)
        +-- capability absent for this vector? -> unsupported_capability
        |
        v
   host fetches under the profile's network policy and budgets
        |
        +-- over the per-profile download budget -> resource_limit
        |
        v
   answer: { kind: "download", bytes_base64, byte_count, sha256,
             reported_name, truncated: false }
                 |
                 +-- reported_name is BOUNDED, REDACTED and NEVER a path
                 +-- no file is written anywhere by the host
```

```mermaid
flowchart TD
  A["target.act kind: download"] --> P{"permission at use"}
  P -- deny --> PD["permission_denied"]
  P -- allow --> RO{"session readonly?"}
  RO -- yes --> SR["session_read_only"]
  RO -- no --> B{"per-profile budget"}
  B -- over --> RL["resource_limit"]
  B -- within --> F["fetch under the network policy"]
  F --> S{"size"}
  S -- "over the ceiling" --> TR["refused, or truncated:true — a ruling"]
  S -- within --> ANS["bytes_base64 + byte_count + sha256 + reported_name"]
  ANS --> NF["the host writes NO file"]
```

## 3. Size: a ceiling, not chunking

Chunking would mean the host holds a partial transfer across requests: a new
owner with a lifetime, a resumption token, an eviction rule, and a way for a
target or session to close underneath it. That is a stateful subsystem inside
a host whose whole design is that state is owned, bounded and visible.

A **single-shot ceiling** costs none of that. Concretely: refuse anything whose
body exceeds a per-profile `download_bytes` bound, answering `resource_limit`
with the measured size, and let the agent fetch large artefacts by its own
means. The recommendation is one call, one answer, no partial state.

If a later ruling wants chunking, it should arrive as its own design with its
own owner class and lifetime — not as a parameter added here.

## 4. The filename, which is the whole security story

`reported_name` comes from the page or the server: the `download` attribute or
`Content-Disposition`. Under sink C it is **never resolved, joined, or opened**
— it is a label the agent may use or ignore.

The rules the court should pin:

- bounded to a small length, and truncated rather than refused;
- carried through the same redaction discipline as every other page-derived
  string, so it never reaches the audit ledger or a host diagnostic;
- reported **as given**, without normalising away the evidence — an agent that
  sees `report .. /etc/passwd.txt` should see exactly that, because sanitising
  it silently would hide what the page tried;
- **never** used by the host to name anything.

That last point is what sink C buys: there is no path to traverse, so
traversal is not a bug class here. The probe in the downloads audit offered
precisely that name to make the difference concrete.

## 5. Budget, owners, lifecycle

- **Budget**: a per-profile `download_bytes` and `downloads` count, reported
  beside the existing profile budgets, so the limit is visible before it is
  hit.
- **Owner**: the bytes exist only inside one request — fetched, encoded,
  answered, dropped. **No new long-lived owner class**, which is the direct
  consequence of choosing a ceiling over chunking. `memory.report` should still
  show the transfer while it is in flight, as every other in-flight cost is
  shown.
- **Lifecycle**: nothing survives the answer. A target or session closing
  mid-transfer cancels the request the way any other in-flight operation is
  cancelled, and leaves no artefact, because there is no artefact to leave.
- **Ephemeral profiles**: nothing to clean up.
- **Retention**: one bounded buffer, returned when the request ends — the
  memory audits in this batch make that a requirement, not a nicety.

## 6. Court draft

1. Denied by policy, a download answers **`permission_denied`** at the point of
   use, and `permissions_effect` reads `enforced`.
2. From a readonly session, it answers **`session_read_only`** — a different
   question from a denied permission.
3. Permitted, a small hermetic fixture comes back with `byte_count` equal to
   the fixture's size and a `sha256` that matches it.
4. **The reported name is never a path**: a fixture offering
   `report .. /etc/passwd.txt` comes back reported verbatim and bounded, and
   the host has created **no file anywhere** — checked against the profile
   root and the working directory.
5. Over the per-profile byte budget, the answer is **`resource_limit`** naming
   the measured size, and nothing is transferred.
6. Over the transport ceiling, the answer is the ruled one — refusal or
   `truncated: true` — and never a silent short read.
7. A target closed mid-transfer answers typed and leaves no artefact.
8. The bytes are visible in `memory.report` while in flight and gone after.
9. The vectors that remain unserved — a cross-origin attachment, or anything
   over the ceiling — still answer **`unsupported_capability`**, so the three
   refusals stay distinct.
10. Nothing page-authored reaches the audit ledger or any host diagnostic.

Criteria 4 and 10 are the ones that make sink C worth choosing; criterion 9 is
what keeps today's honest refusal from being quietly replaced by a vaguer one.

## 7. Pending rulings

1. **The ceiling value**, and whether over-ceiling is a refusal or a truncated
   answer. This audit recommends refusal: a truncated download that looks
   successful is the same class of defect as a silent failure.
2. **The budget values** for `download_bytes` and `downloads` per profile.
3. **The `sha256`** in the answer — recommended, since the agent cannot verify
   an encoded blob otherwise, and it costs one hash of bytes already in memory.
4. Whether the action kind is `download` or something narrower like
   `download_link`, given it takes a node reference.
5. Chunking, explicitly deferred, to be designed with its own owner and
   lifetime if it is ever wanted.
