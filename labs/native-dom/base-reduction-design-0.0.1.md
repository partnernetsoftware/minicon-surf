# Buying back the child-realm floor (native-dom, control 0.0.2)

Design and measurement only. No product code on `main`, nothing pushed. Every
number here was measured in a **throwaway worktree with scratch builds that
are not committed and are not qualification**; they exist so the next ruling
is made against facts.


## 1. The standing blocker

The shim split left M1 with 40,486 bytes of headroom under its floor. The
`Event` slice spent it: M1 is now **243,130** against a floor of **245,760**,
which is **2,630 bytes**. The floor is a floor by ruling — "a standing floor,
not a feature budget" — so the next base change does not fit, and the browser
work the plan lists is blocked behind that again.

This record measures what a base member actually costs and what can honestly
be moved out of the base.


## 2. What a base member costs, measured

Two builds, same court, same fixtures:

| change to the base | source bytes | M1 | M1 change |
| --- | ---: | ---: | ---: |
| none (current `main`) | 28,049 | 243,130 | — |
| one getter moved to the extension | 27,982 | 242,170 | **−960** |
| eleven members moved to the extension | 27,410 | 236,426 | **−6,704** |

So a member of a shared prototype costs **roughly 600 to 960 bytes of M1 per
child**, and the eleven-member move bought 6,704 live bytes for 639 source
bytes — **ten times** the 3.4 live-bytes-per-source-byte the shim split
measured for bulk text.

That is the finding this record exists for: **the base's cost is dominated by
how many functions every realm compiles, not by how many bytes of source it
reads.** Any future ruling that prices a base change should price it in
members, not in kilobytes.


## 3. Candidate A: the page-facing view of an `Event` leaves the base

`Event` carries eleven page-facing members: `isTrusted`, `type`, `bubbles`,
`cancelable`, `composed`, `target`, `currentTarget`, `eventPhase`,
`dispatching` and `timeStamp`, plus `defaultPrevented`. The base itself reads
exactly one of them — `Element.reset` checks `defaultPrevented` — and the
host reads none: its action bridge answers from hidden state, and its own
scripts never touch an event's properties.

A child realm runs no page script, so nothing there can read any of the other
ten. Moved to the main extension — same class, accessors installed on
`Event.prototype`, hidden state reached through one accessor added to the
one-shot handle — and measured in the throwaway worktree:

| | current | candidate |
| --- | ---: | ---: |
| M1 (system) | 243,130 | **236,426** |
| M1 headroom under the floor | 2,630 | **9,334** |
| M2 (system) | 1,700,412 | **1,653,484** |
| M1 / M2 (arena) | 235,674 / 1,648,188 | 229,466 / 1,604,844 |

Courts on that scratch build: child-frames 82/82, event-fidelity 62/62,
frame-actions 182/182, form 179/179, element-api 28/28, page-navigation 80/80,
lifecycle 53/53.

**What is honest about it, and what is not.** No child realm can tell the
difference, because nothing in one can look. But `Event.prototype` would then
carry different members in a main realm and in a child realm, and that is a
real divergence — it is invisible rather than absent, which is exactly the
distinction the shim split's §15 asked to be stated plainly rather than
argued away. It also means any future host script that reads an event property
in a child realm would find `undefined`, so the rule comes with a standing
constraint: **the host reads hidden state through the bridge, never an event's
properties.**


## 4. Candidate B is not a candidate: children need the selector engine

The shim-split record left this open — the engine stayed in the base and no
measurement said whether it had to. It does: `snapshot_script` runs in child
realms and calls `document.body.querySelectorAll("*")` and
`document.querySelector('label[for="…"]')` to build a snapshot's labels. The
root's ruling to keep it is now confirmed by measurement rather than by
caution, and the 3,011 bytes it costs are not recoverable this way.


## 5. What this round did not measure

`Node`, `Text` and `Element` carry 10,042 bytes and by far the most members of
anything in the base, and some of them — DOM mutation a page performs, and
members no host script names — may be page-only by the same argument as §3.
Deciding that needs an audit of every host script against every member, which
is a slice of its own and is not guessed at here. At the measured price, ten
such members would be worth about 6 to 10 KB of M1.


## 6. What I need ruled

1. **Candidate A**, with the divergence in §3 stated as it is: main and child
   `Event.prototype` would differ, invisibly but really.
2. Whether the **`Element` audit** in §5 is the next slice, or whether the
   floor should instead be bought back some other way.
3. Whether the **per-member price** — 600 to 960 bytes of M1 — should be
   written into the shim-split record as the number future growth is priced
   in, replacing the bulk-text ratio that under-counts by ten.


## 7. Limits of these numbers

One court run per variant, one machine, in a throwaway worktree that has been
removed. Nothing here is committed as qualification and no receipt was
written. The navigation soak was not run at all, by standing ruling.


## 8. The rulings

**8.1 Candidate A is accepted**, with the divergence stated in §3 rather than
softened: `Event.prototype` carries the ten page-facing accessors in a main
realm and not in a child one — invisible there, because nothing in a child can
look, but real. `defaultPrevented` keeps its base path **only** because
`Element.reset` reads it; nothing else in the base may grow a dependency on an
event's properties. Host actions continue to answer through the capability
bridge and hidden state and never through an event's properties, and a child's
host actions must stay valid through that bridge even though the child's
prototype lacks the page-facing view.

**8.2 The next slice is an `Element` member audit**, design and measurement
first. No member of `Node`, `Text` or `Element` moves until every host-script
call site and every child-snapshot dependency is inventoried and a court is
frozen. Nothing is guessed at, and this record's §5 estimate is not a licence.

**8.3 The pricing rule is per member.** 600 to 960 bytes of M1 per member is
what future base growth is budgeted against. The shim split's 3.4 live-bytes
per source byte stays in that record as **historical context only** — it
under-counts by an order of magnitude and is not a justification for any cap
or for any change's size.
