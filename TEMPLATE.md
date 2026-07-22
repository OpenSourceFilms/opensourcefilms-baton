# BATON.md template — copy into your project root and fill in

Copy everything below the `---` into `<project>/BATON.md`. Rules first:

- **≤120 lines, always.** This file is *superseding-on-write*: rewrite sections, don't append.
  History lives in git, not here.
- **Intent only — no machine facts.** Never write "venvs healthy", "GPU present", "tree clean":
  Tier 0 generates those fresh every session and prose versions become confident lies.
  If you catch yourself writing a fact a script could check, delete it.
- **NOW is one item.** The single next thing. It gets auto-flagged ⚠ SUSPECT if the transcript
  shows it was already executed.
- **TRAPS are the highest-value section** — approaches tried and REJECTED, with why. Prefer
  `baton trap` (path-keys them for push-injection); use the section for project-wide standing ones.
- **MODEL/ROUTING is load-bearing**: a Sonnet session resuming a Fable-gated NOW will just do it
  unless this table says whose call it is.
- Update NOW/IN-FLIGHT **in the same commit as the work they describe** — this file has been
  caught stale twice by cold reads; same-commit is the only fix that sticks.
- The pickup hook injects this file automatically at every session start. You never need to
  tell anyone to read it.
- **Optional Tier-0 config** — `<project>/.baton/probes.json`:
  `{"gpu_python": "/abs/path/venv/bin/python", "jobs": [{"name": ..., "artifact": ..., "newer_than": ...}], "probes": [{"name": ..., "command": ..., "ttl_seconds": ..., "timeout": ...}]}`.
  Set `gpu_python` to a venv whose torch actually drives this pod's GPU (system python3's torch may
  lack the GPU's arch — you'll see "INCONCLUSIVE" until you set it). Heavy venv probes run in the
  background and cache; they never block session start.
  `probes` is for arbitrary shell-command checks (freshness pings, service health, anything a
  one-liner can answer): `name` and `command` are required; `ttl_seconds` (default 300) and
  `timeout` (default 10, seconds) are optional. Same cache-hit-instant / cache-miss-detached-worker
  pattern as `gpu_python` — never blocks session start. Output is the command's first stdout/stderr
  line, redacted, capped at 150 chars. A missing/garbage command reports one honest line, never a
  crash.
- **`VOLATILE (YYYY-MM-DD): <fact>` line prefix** — opt a fast-churning pointer-style fact (a file
  name, a gate verdict, anything that supersedes within days rather than weeks) into mechanical
  staleness checking instead of writing it as plain prose that silently rots. Tier 0 age-checks every
  `VOLATILE (...)` line on each pickup and appends `WARNING: stale volatile fact (Nd old): <fact>`
  once it's older than 2 days (override per-project via `volatile_threshold_days` in
  `.baton/probes.json`). No `VOLATILE` lines in a project's BATON.md = no output, same as every other
  probe here. Adopting the tag in any given project's BATON.md is that project's own call — the
  mechanism ships here, it isn't imposed.

---

# BATON — project: <name>

**Precedence:** Tier 0 (observed reality) > this file > any compaction summary > everything else.

## MISSION
<1-3 sentences: what this project is for. Changes rarely.>

## NOW
<The single next action, and whose call it is if gated.>

## MODEL / ROUTING
- **Fable/Opus** — <design judgment, reviews, final applies>
- **Sonnet/Haiku** — <mechanical builds, probes, batch work>
- **DeepSeek** — bounded text-gen only; never the canonical record.

## IN-FLIGHT
<Work started but not landed; artifacts and their trust level.>

## BLOCKED (needs external input)
<Only genuine external blocks.>

## OPEN
<Questions deliberately left open.>

## TRAPS — tried and REJECTED. Do not re-attempt.
- <what was tried — why it failed — what to do instead>
