# MiniCon Surf

**A memory-first, agent-native browser.**

MiniCon Surf is an early-stage browser in the MiniCon product family. It is
intended to be implemented in Rust, account for and bound its memory use, and
make automation a first-class interface rather than an afterthought.

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
- **MiniCon Surf** is the memory-first browser surface for humans and Agents.
- [AgenTerm](https://github.com/partnernetsoftware/agenterm) is the Agent-era
  workbench that can compose terminal and browser capabilities with durable
  identity, permissions, and workflows.

The project is in the `0.0.x` product-definition and feasibility phase. No Web
engine, compatibility level, memory number, or release platform is claimed
until it has a named court and reproducible evidence. See
[`plan/plan-0.0.x.md`](plan/plan-0.0.x.md).

## License

Licensed under either of

- Apache License, Version 2.0 ([LICENSE-APACHE](LICENSE-APACHE)); or
- MIT License ([LICENSE-MIT](LICENSE-MIT))

at your option.
