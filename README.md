# BATON

A session-continuity system for Claude Code that survives context compaction, `/clear`, restarts, and model switches, preventing the “handover corruption” where a resumed agent acts on stale or hallucinated memories instead of what is actually true.

## Why

When a session is interrupted or compacted, an agent must reconstruct its world from truncated notes, partial transcripts, and ambient context. This reconstruction is unreliable: goals are doubled, environment state is misremembered, and already-failed approaches are re-attempted. BATON replaces that reconstruction with *mechanically regenerated environment facts*, a single hard-capped task file, and a path-keyed negative-knowledge store that prevents repetitive mistakes.

## How it works

BATON operates in three tiers:

- **Tier 0: Environment facts, regenerated fresh every session start.** Git branch/dirty state, per-venv Python interpreter resolution, GPU liveness via an actual compute operation (`torch.randn(4,4).to('cuda')`, never a presence-check like `torch.cuda.is_available()`), background job status checked by artifact effect, not exit code. These are never trusted from a stale write; they are always current.

- **Tier 1: `BATON.md`, a single project task-file hard-capped at 120 lines.** Sections: `MISSION`, `NOW` (exactly one next action), `MODEL/ROUTING`, `IN-FLIGHT`, `BLOCKED`, `OPEN`, `TRAPS`. Superseding-on-write, not append-only. TRAPS is deliberately the highest-value section.

- **Tier 1b: `traps.jsonl`, a path-keyed negative-knowledge store.** Each entry records an approach already tried and rejected for a specific file or command. Injected back into context *only* when that path is about to be touched again (via a `PreToolUse` hook); never dumped wholesale.

- **Tier 2: Archive (transcripts, decision logs).** Queried on demand with `baton recall`; never read in bulk.

A core design principle: *“A success signal is not evidence of work. Only an effect is.”* Probes must exercise the real system, because exit codes and presence-checks lie.

## Install

BATON is a single dependency-free Python 3 file (`baton.py`, stdlib only).

1. Drop `baton.py` somewhere persistent and put it on your `PATH`, e.g.:
   ```bash
   ln -s /path/to/baton.py /usr/local/bin/baton   # baton.py already has a #!/usr/bin/env python3 shebang
   ```
2. Seed your project's task file: `cp TEMPLATE.md <project>/BATON.md`, then fill in `MISSION`/`NOW`.
3. Wire the two hooks in `.claude/settings.json` (or your global `~/.claude/settings.json`), matching Claude Code's real hook schema: each event takes an array of matcher blocks:

   ```json
   {
     "hooks": {
       "SessionStart": [
         {
           "matcher": "startup",
           "hooks": [{ "type": "command", "command": "baton hook" }]
         },
         {
           "matcher": "resume",
           "hooks": [{ "type": "command", "command": "baton hook" }]
         },
         {
           "matcher": "clear",
           "hooks": [{ "type": "command", "command": "baton hook" }]
         },
         {
           "matcher": "compact",
           "hooks": [{ "type": "command", "command": "baton hook" }]
         }
       ],
       "PreToolUse": [
         {
           "matcher": "Edit",
           "hooks": [{ "type": "command", "command": "baton pretool" }]
         },
         {
           "matcher": "Write",
           "hooks": [{ "type": "command", "command": "baton pretool" }]
         },
         {
           "matcher": "Bash",
           "hooks": [{ "type": "command", "command": "baton pretool" }]
         }
       ]
     }
   }
   ```

   Hooks receive their payload on stdin as JSON (session id, transcript path, tool input, etc.); no environment variable plumbing needed; `baton hook`/`baton pretool` read stdin directly. `SessionStart` is intentionally wired to all four matchers (`startup`, `resume`, `clear`, `compact`): a pickup should fire on every kind of discontinuity, not just cold start.

4. Optional: create `<project>/.baton/probes.json` to point GPU checks at the correct venv interpreter and add arbitrary shell-command health probes.

Running on a Runpod pod (or anywhere with a persistent volume + ephemeral container root, pod
restarts, and per-project GPU venvs)? See [`RUNPOD.md`](RUNPOD.md) for where to put things so they
survive a rebuild.

## Usage

All commands are from `baton.py`'s subcommand table. No arguments implies `pickup`.

| Command | Purpose |
|---------|---------|
| `baton pickup` | Default action; generates the `SessionStart` payload: Tier 0 status, `BATON.md`, and a structured summary of an interrupted session (if the previous run died mid-task). |
| `baton status` | Tier 0 generated environment facts only. |
| `baton now` | Show or manage the single `NOW` item; auto-flags it `SUSPECT` if the transcript shows it was already executed. |
| `baton trap` | Record a new path-keyed negative-knowledge entry in `traps.jsonl`. |
| `baton block <description>` | Append an item to the `BLOCKED` section of `BATON.md`. |
| `baton hook` | The `SessionStart` hook entrypoint (used by `settings.json`, not typically run manually). |
| `baton pretool` | The `PreToolUse` hook entrypoint; injects matching traps for the turn. |
| `baton recall <query>` | Query the archive. Currently a thin wrapper over a local RTFM search index if one is present; degrades to `(no index)` otherwise; recall is not a bundled/required component. |
| `baton audit <days>` | Health-check BATON's own operation over the last N days: checks for truncation, stale `SUSPECT` markers, noisy traps, and swallowed exceptions. |

## Testing & safety

- `tests/selfcheck.sh` is the acceptance floor: it verifies `baton.py` compiles, both hooks return valid JSON and exit 0 even on garbage/empty/real stdin, and `pickup` stays under its token budget and latency ceiling.
- `tests/pre-commit.sh` can be installed as a git pre-commit hook (`cp tests/pre-commit.sh .git/hooks/pre-commit`). It blocks any commit where `baton.py` fails selfcheck **or** the staged diff contains an API-key-shaped string, a real safeguard that has caught a leaked-credential-shaped string in practice, not a decorative check.

BATON is designed to degrade safely: a hook failure, missing binary, or unexpected transcript format means the session proceeds entirely normally, just without the assist. It may never block, gate, or nag.

## Design philosophy

- **Net-subtractive**: if a project's other read surfaces don't shrink after adopting BATON, it failed.
- **Invisible when healthy**: no banners, no chatter, no nags.
- **Degrades to nothing**: a broken hook or missing tool never prevents the session from proceeding.
- **Negative constraints only in `BATON.md`**: positive directives like "handle edge cases" measurably degrade agent performance (checkbox mentality); TRAPS are deliberately the highest-value section.
- **Never re-inject a dead session's raw transcript tail**: measured to be 7.1× more likely to cause a repeat failure (see `DESIGN.md` §5). Instead, package it as a structured summary ("interrupted execution state").

See `DESIGN.md` for the full spec, including the research basis for each design decision, and `docs/verified_hook_payloads.jsonl` for empirically-captured Claude Code hook payload shapes.

## Acknowledgments

BATON was built and tested on GPU infrastructure provided in collaboration with Runpod. This
particular tool doesn't itself need a GPU to run, but it grew out of the same development
environment as the rest of Open Source Films' pipeline tooling, which does.

## License

MIT. © Open Source Films.
