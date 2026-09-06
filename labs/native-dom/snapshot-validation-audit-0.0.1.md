# Snapshot validation — design-only audit, 0.0.1

Read-only and design-only, from `a229c13`. Nothing implemented, no court
frozen, no handle, base or bound changed, no visual run, no navigation soak, no
download. Every probe is a hermetic local fixture.

**The shape check is not the weak point. The provenance of the instrumentation
object is** — and it is exploitable end to end: a page can make the agent
navigate somewhere the agent never chose.

## 1. What the host validates today

The snapshot answer is parsed in two places (`main.rs:3003`, `main.rs:6786`)
and the whole of the validation is:

- `revision` must be present and a `u64`, else `internal: "snapshot lacks a
  revision"`;
- an `error` field means `internal: "target lost its revision instrumentation"`;
- `nodes` is read with `as_array().unwrap_or_default()`, and every field inside
  each entry with `unwrap_or("node_0")`, `unwrap_or("")`, `unwrap_or(false)`;
- `truncated` is `as_bool().unwrap_or(false)`.

So a missing or mistyped field is silently defaulted, and only the revision is
required. After H1, though, the JSON itself is produced by `__mcsJson`, which a
page cannot replace — so the *serialiser* is trustworthy. The question is what
the script serialises.

## 2. Where the answer comes from

```mermaid
flowchart TD
  I["INSTALL_JS — runs AFTER the page's scripts"] --> G{"if (!window.__mcs)"}
  G -->|"absent"| H["host installs { revision, snapshot, nodes } and a MutationObserver"]
  G -->|"already there"| K["the host keeps what it finds"]
  K -.->|"the page put it there"| P["page-owned counter, no observer"]
  H --> S["snapshot script reads s.revision, refreshes s.nodes from the DOM"]
  P --> S
  S --> J["__mcsJson — page cannot replace it (H1)"]
  J --> V["host: revision must be a u64; everything else defaults"]
  V --> A["the agent's nodes and the revision its references carry"]
  A --> T["target.act compares the reference's revision against the current one"]
```

Two facts decide everything below, and both were measured:

1. **`window.__mcs` is `undefined` while the page's own scripts run.** The
   install happens after `__mcsComplete()`, so a page script runs first.
2. **`INSTALL_JS` keeps whatever it finds** — `if (!window.__mcs)` — and it is
   a plain assignment, not a defined property. The guard exists because the
   install runs again on the lifecycle path, and it cannot tell the host's
   object from a page's.

A page therefore only has to name the global first.

## 3. What that is worth to an attacker, measured

A page defines `window.__mcs` with a `revision` getter that always returns 0,
then swaps a link when a button is clicked:

| | honest instrumentation | page owns `__mcs` |
| --- | --- | --- |
| revision after the swap | 0 → **1** | 0 → **0** |
| the agent's stale reference | refused **`stale_revision`** | **accepted, `applied: true`** |
| what the click reached | nothing | the **swapped** element |
| the server received | nothing | **`GET /evil.html`** |

The agent held a reference to a link it had read as *"safe link"*, acted on it,
and navigated to an address chosen by the page after the agent looked. **The
staleness protection is the only thing standing between an agent and a
swapped-out node, and it rests on a counter the page can own.**

No observer is installed in that case either, so nothing else advances the
number.

## 4. What is *not* forgeable, and why it matters to the design

- **The node list.** Pre-defining `__mcs` with empty `nodes` changed nothing:
  the snapshot script rebuilds the registry from the DOM on every call, so
  `s.nodes` is an output, not an input. `target.act` then indexes the array the
  host has just refreshed.
- **The serialisation.** H1 closed it; a replaced `JSON.stringify` no longer
  reaches this path.
- **Types and counts.** After H1 the JSON is built by the host's serialiser over
  host-built objects; `max_nodes` and `MAX_SNAPSHOT_NODES` bound the count, and
  `truncated` is computed by the script rather than supplied.

That is why the answer is not "validate the shape harder": the shape is already
the host's own. **The one field the page can choose is the one the host trusts
most.**

## 5. Owners, invariants, evidence, safe failure

- **Owner**: the revision counter is owned by the realm's instrumentation, which
  is *supposed* to be the host's. Today ownership is decided by whoever names
  the global first.
- **Invariant at stake**: a node reference means the same node it meant when the
  agent read it, or the act is refused.
- **Evidence**: §3, one hermetic page, two runs, the server's request log as the
  witness.
- **Safe failure today**: none on this path — the act was *allowed*. Every other
  tampering measured in earlier audits failed closed; this one fails **open**,
  which is what distinguishes it from the H2 findings.

## 6. Candidates

| candidate | what it changes | cost | verdict |
| --- | --- | --- | --- |
| **A. Brand the registry** — install `{brand: <host-minted token>, …}` as a non-writable, non-configurable, non-enumerable property; every reader verifies the brand, and a mismatch refuses the realm | `INSTALL_JS`, the revision and snapshot scripts, a token per realm | host scripts are compiled per call, so ~0 per realm; the object gains one string field | **Recommended.** The host already mints per-realm capabilities for dispatch and lifecycle; this is the same pattern applied to the registry, and it fails closed. |
| B. Install before page scripts, non-configurable | ordering, plus the observer attached later | ~0 | Good, and complementary; but the observer needs `documentElement`, so it splits the install in two, and a second install still needs to recognise its own object — which is A. |
| C. Refuse if the name is already taken | one line | ~0 | Simplest, but the lifecycle path installs a second time, so "already taken" cannot be distinguished from "mine" without A's brand. |
| D. Derive the revision host-side | — | — | Not possible: the DOM lives in the realm, and the host has no independent view of its mutations. |
| E. Harder shape validation of the answer | the two parse sites | small | **Does not address this**: the shape is already the host's own. Worth doing for defaulting hygiene, not for this defect. |

## 7. Loss matrix

| | do nothing | A (brand) | E (schema only) |
| --- | --- | --- | --- |
| a page can own the revision | **yes** | no | **yes** |
| a stale reference can act on a swapped node | **yes, measured** | no | **yes** |
| failure direction | **open** | closed | open |
| per-realm cost | none | ~one string field | none |
| touches the protocol | no | no | possibly |
| touches `SEAL_JS`, handle, base, bounds | no | no | no |

## 8. Dependencies and non-goals

- **Dependencies**: none frozen. A brand changes no bound, no handle key, no
  protocol field, and no shim; it lives in the host's own scripts.
- **Regressions verified unchanged by this audit** (no code was touched):
  host-answer **9/9**, capture-declaration **8/8**, probe-truthfulness
  **25/25**.
- **Non-goals**: H2; changing the snapshot's node model; making a shared realm
  hostile-page-proof; any byte target.

## 9. Court draft

1. A page that defines `window.__mcs` before the host does cannot make the host
   adopt it: the realm is refused, or the host's own registry wins.
2. With such a page, a reference taken before a DOM swap is refused
   `stale_revision` exactly as it is with an honest page.
3. The server receives no request the agent did not ask for, in either case.
4. The revision the agent sees advances when the DOM changes, whatever the page
   has defined.
5. The lifecycle path's second install still finds its own registry and does not
   reset the counter.
6. Nothing in this changes the snapshot's node model, the handle key set, or any
   bound; per-realm tracked bytes are recorded before and after.

## 10. Pending rulings

1. Whether candidate **A** proceeds — freeze a court first, as with H1.
2. Whether the defaulting hygiene of §1 (silent `unwrap_or` on every field) is
   worth a separate, smaller round; it is not what makes §3 possible.
3. Whether this finding changes the standing of the earlier "everything fails
   closed" conclusion. It should: that conclusion was measured over intrinsic
   replacement, and this defect is reached by naming a global instead.
