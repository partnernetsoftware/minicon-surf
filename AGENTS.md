# MiniCon Surf agent guide

Start every product or implementation decision at `plan/plan-0.0.x.md`. The
Markdown-tree DAG is the owner/decision index; the Mermaid flowchart is the
memory palace for dependency and gate flow. Update both when a durable outcome,
dependency, gate, or rejection changes. A lab README records technology-local
facts; it must not silently redefine the product.

## Non-negotiable product outcomes

MiniCon Surf is both **memory-optimized** and **Agent-use oriented**. Neither
outcome compensates for failure of the other. Compatibility, delivery speed,
framework convenience, binary size, or a persuasive demo cannot waive either
gate. Read `[N0]`, `[M2]`, and `[A3]` in the plan before changing a lab or
claim.

## Redaction

The repository is public. Never commit or paste into tracked files, commit
messages, screenshots, fixtures, receipts, or examples:

- an expanded home directory, repository absolute path, account name, email,
  phone number, token, credential, personal hostname, IP or MAC address;
- a real profile, cookie database, browsing history, downloaded page, URL with
  private query parameters, or environment-variable value;
- raw command output before checking it for the above.
- any private key or reusable credential material, including keys labelled as
  test-only. Generate disposable TLS keys under ignored `target/` or a temporary
  directory at court runtime; if a fixed private fixture is unavoidable, inject
  it explicitly from an untracked directory outside the repository.

Use repository-relative paths for files in the clone and `~/...` for generic
paths under a user home. Use RFC 2606 domains, loopback, `data:` URLs, or
committed hermetic fixtures for tests. Record OS version, architecture, engine
version, artifact digest, workload identity and measurement semantics; omit
host identity.

Before committing documentation or evidence, run:

```bash
rg -n '/Users/|/home/|[A-Za-z]:\\\\Users\\\\|@[^ ]+\.|(token|password|secret|api[_-]?key)[=:]' \
  --glob '*.md' --glob '*.json' --glob '*.jsonl' --glob '*.txt' .
```

Review every hit; URLs and explanatory placeholder text may be legitimate,
but host/user paths and credentials are not.

Also reject private-key material independently of its filename:

```bash
git grep -n -I -E 'BEGIN (RSA |EC |OPENSSH |ENCRYPTED )?PRIVATE KEY'
```

Public certificates may be committed when needed, but a PEM containing a
private-key block may not. Never print private fixture contents into logs or
receipts.

## Lab discipline

- Each `labs/{technology}/` is isolated and has a hypothesis, exact scope,
  reproduction command, evidence, gaps, and `keep|narrow|combine|reject`
  verdict.
- Labs share workloads and receipt vocabulary from `labs/court/`; they do not
  share a dependency graph merely for convenience.
- Downloaded SDKs, binaries, profiles, caches, raw traces, and local receipts
  live under ignored `target/` unless a small sanitized result is deliberately
  promoted.
- Pin external artifact version and SHA-256. Disable telemetry and crash dumps
  where the candidate permits it.
- Memory means the complete attributable process tree. If a platform service
  cannot be attributed, report the gap; never substitute root-process memory.
- A result from one OS/ISA and workload is evidence only for that cell and
  workload.
- `memory-optimized` requires a named same-machine baseline. Attribution and a
  hard limit alone are not optimization evidence.
- Unsupported behavior is a valid result. Do not emulate support in the court
  or weaken a workload to manufacture a pass.
- Automated courts, regressions, `cargo test` and every default command are
  strictly headless: they never create or activate a desktop window. A real
  window needs the double opt-in `--visual` plus
  `MINICON_SURF_ALLOW_VISIBLE_COURT=1` in the same run, is documented as
  showing windows, is run once by hand (never repeated in the background),
  and must not steal focus. Missing either half fails closed and reports
  `unverified`. `labs/native-dom/surface-headless-court.py` is the
  falsifiable check.

## Change hygiene

Keep experiments out of future product crates until at least two real routes
prove the shared boundary. Preserve rejected labs and their small reviewed
evidence when they explain a durable decision; remove downloaded artifacts and
bulk build state. Use focused commits and keep the worktree clean after every
reviewed increment.
