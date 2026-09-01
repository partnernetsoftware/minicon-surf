# MiniCon Surf

**A memory-optimized, agent-use oriented browser.**

MiniCon Surf is an early-stage browser in the MiniCon product family. It has
two co-equal, non-negotiable product outcomes: use materially less memory for
named workloads, and make Agents first-class users rather than retrofit
automation onto a human-only browser. It is intended to be implemented in
Rust, but language choice is not accepted as memory evidence.

Neither outcome can compensate for failure of the other. A browser with a
good Agent API but ordinary browser memory use is not MiniCon Surf; neither is
a small browser that Agents can control only through pixels, sleeps, or an
aftermarket adapter. Web compatibility, implementation speed, framework
convenience, and binary size are subordinate when they conflict with these two
outcomes.

The browser session is independent from its presentation surface: the same
live page should be able to move between headless and headed operation without
reload or loss of page, profile, focus, or automation state. A native CLI is
the primary Agent interface; a CDP-compatible endpoint connects existing
automation clients to the same targets. Profiles are explicit, inspectable
product objects rather than anonymous data directories.

MiniCon Surf will work independently. It is not a plugin and does not require
MiniCon to be installed. Its place in the product family is deliberate:

- [MiniCon](https://github.com/partnernetsoftware/minicon) is the one-file
  local terminal.
- **MiniCon Surf** is the memory-optimized, Agent-use oriented browser surface.
- [AgenTerm](https://github.com/partnernetsoftware/agenterm) is the Agent-era
  workbench that can compose terminal and browser capabilities with durable
  identity, permissions, and workflows.

The project is in the `0.0.x` product-definition and feasibility phase. No Web
engine, compatibility level, memory number, or release platform is claimed
until it has a named court and reproducible evidence. See
[`plan/plan-0.0.x.md`](plan/plan-0.0.x.md). The first versioned vocabulary and
bounded JSON control-envelope candidate lives under [`protocol/`](protocol/);
it is a contract for experiments, not a released API or compatibility claim.

## License

Licensed under either of

- Apache License, Version 2.0 ([LICENSE-APACHE](LICENSE-APACHE)); or
- MIT License ([LICENSE-MIT](LICENSE-MIT))

at your option.
