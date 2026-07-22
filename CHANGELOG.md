# Changelog

## [0.1.0] - 2026-07-22

Initial release.

### Added

- `baton.py` — single-file, dependency-free (stdlib-only) session-continuity
  tool for Claude Code. `pickup` / `status` / `now` / `trap` / `block` /
  `hook` / `pretool` / `recall` / `audit` subcommands.
- `DESIGN.md` — full v3.2 design spec: the three-tier architecture, the law
  of effects, negative-knowledge/trap mechanism, eviction policy, and the
  research basis behind each decision.
- `TEMPLATE.md` — per-project `BATON.md` seed file and conventions.
- `tests/selfcheck.sh` and `tests/pre-commit.sh` — acceptance floor and a
  git pre-commit gate that blocks broken `baton.py` or key-shaped strings
  in staged diffs.
- `docs/verified_hook_payloads.jsonl` — empirically-captured Claude Code
  hook payload shapes (SessionStart, Stop, PostToolUse, PreCompact).
- `prior_art_agents.md`, `prior_art_docs.md` — research notes on agent
  memory architectures and documentation-decay practices that informed the
  design.
- `LICENSE` (MIT).
