# BATON — continuity across compacts, clears, restarts, pods, and models

**Status:** DESIGN v3 — post Fable adversarial review, post Deep Research. Ready to implement.
**Author:** Claude (Opus 4.8) with the project owner, 2026-07-12.
**Scope:** workspace-wide, project-agnostic. NOT wired into `/workspace/ProjectA` yet (Project A is mid-streamline
in another session; adoption is a separate, later, deliberate step).

> **Revision history — what was WRONG and got fixed.** Kept deliberately: these are traps.
> - **v1** — over-built (7 hooks, 7 verbs, a bespoke event journal). Central claim ("no LLM authors
>   the record") was **false**: `BATON.md`, the only file anyone reads, was LLM-authored.
> - **v2** — cut to one hook after Fable's review; realised **the transcript already IS the journal**.
> - **v3** — Deep Research **contradicted v2's headline feature**: raw transcript-tail re-injection is
>   *actively harmful* (§5). Traps get a real mechanism (§6). Eviction gets a real policy (§7).

---

## 1. The problem

The projects do not lose state. They **drown** in it.

| Surface | Size | Actually read? |
|---|---|---|
| `ProjectA/DECISIONS.md` | 279 KB / 2,841 lines | No — too expensive |
| `ProjectA/MASTER.md` | 81 KB | No |
| `ProjectA/GAMEPLAN.md` handover block | one ~1,200-word paragraph | Skimmed, misread |
| session transcripts | **233 MB, 132 files, back to 2026-06-11** | **Never, not once** |

**Compaction was never lossy. The archive was unindexed, and nobody built a door to it.**

Two structural errors:

**(a) Machine-observable facts written as prose.** Half the GAMEPLAN handover block is a past
session's assertions about the *machine* ("venvs healthy", "GPU present 97 GB"). No expiry ⇒ when the
pod changes they become **confident lies**. Project A run 1 died exactly this way: `/usr/bin/python3` drifted
3.11 → 3.8, silently breaking a venv — a one-second check would have caught it.

**(b) Nothing is ever evicted.** Append-only surfaces grow until reading costs more than they're worth;
sessions stop reading them; intent corruption becomes certain.

*(Live proof of (a), found while designing this: `ds_research` was broken — missing deps — and **exits 0
on crash**, writing empty files that look like successes, while the health banner said `OK` because it
checks that a **symlink exists**, not that the tool **works**. The health check was itself a stale
assertion.)*

**The cost is now quantified.** *ConstraintRot*: repeated compaction drives constraint-violation rates
from **0% → 30–59%**. Compactors treat standing invariants as transient chat and drop them. This is
"governance decay", and it is the mechanism by which "the gate is mandatory" quietly stops happening.

## 2. Principles

> **Storage is free. Attention is the only scarce resource.**
> 1. Never make a model **read** what a machine can **generate**.
> 2. Never **narrate** what is already **recorded**.
> 3. Never hold in **context** what can be **pushed to you at the moment you need it**.
> 4. **Negative knowledge is the most valuable and most-lost category. Protect it structurally.**
> 5. **Verify effects, not statuses.** *(added v3.1 — see below; this is the deepest law here.)*

### The law of effects (v3.1 — the most important correction in this document)

> **A success signal is not evidence of work. Only an effect is.**

Three independent instances, all found within 24 hours, by two sessions that weren't looking for each
other's bugs:

| Thing | Claimed | Reality |
|---|---|---|
| `ds_research` | exit **0** | **crashed** (missing deps); wrote an empty file that looked like a success |
| Project A adjudication scripts | **rc=0**, healthy, rejection counter populated | every `Inference.crop()` raised, swallowed by `except Exception: reject` — **did nothing corpus-wide** (D-Log-15) |
| `session-health-check.sh` | `[tools] … OK` | checked that a **symlink existed**, not that the tool worked — *and* was hardcoded to report **a downstream project's** RTFM health regardless of the active project |

This generalises §1(a) beyond prose. It is not merely that *written assertions* rot; it is that
**every proxy for health is a lie waiting to happen** — exit codes, banners, counters,
`pip check`, `torch.cuda.is_available()`.

**Design consequences (binding):**
- Tier 0 must **never** report "job succeeded" from an exit code. It reports **the effect**: did the
  expected artifact appear, is it non-empty, is its mtime newer than the inputs?
- Any probe of a tool must **exercise the tool**, not assert its presence. GPU ⇒ `torch.randn(4,4).to('cuda')`,
  not `is_available()`. Packages ⇒ run the real op, not `pip check` (`pip` reports a wrong-CUDA-build
  package as *"already satisfied"*: `torchaudio==2.11.0+cu130` satisfies a `2.11.0` pin).
- **BATON's own probes are subject to this law.** A probe that can only fail by exiting non-zero is a
  probe that will one day lie. Self-check: `baton status` reports its own liveness and staleness.

**Authorship, stated honestly.** The canonical *event* record is mechanical and complete (the
transcript). The *intent* layer is LLM-assisted, hard-capped, superseding, and always cross-checked
against the mechanical record — **never trusted alone**.

**Enforcement, not goodwill.** Deep Research is unanimous: *any* discipline requiring the model to
remember to record something **will decay**, and prompt-reminders produce linter-fatigue that gets
ignored. Recording must live in the **harness**, asynchronously, outside the agent's loop. We have
direct local evidence: standing instructions already mandate a MemPalace diary and same-session
DECISIONS commits. Both were routinely skipped. The 1,200-word paragraph happened anyway.

**Elegance (a requirement, not a preference):**
- **Net-subtractive** — if GAMEPLAN's handover block and DECISIONS' read surface don't *shrink*, this
  failed. See kill-list §9.
- **Invisible when healthy** — no banners, no chatter, no nags.
- **Degrades to nothing** — hook failure, missing binary, changed transcript format ⇒ session proceeds
  **entirely normally**, minus the assist. **It may never block, gate, or nag.**

## 3. The transcript IS the journal

Claude Code already writes, continuously, surviving abrupt death: every user prompt **verbatim**; every
assistant message **including reasoning**; every tool-use block (written *before* execution ⇒ the dying
call is captured); every result; timestamps; `cwd`; `gitBranch`; `sessionId`.

So we build **no journal**. Deleted from v1: the `PostToolUse` journal hook (⇒ zero per-tool-call
latency), `PreToolUse` attempt records, the journal file, `flock`, the 4 KB append-atomicity bug, and
per-session journal concurrency (transcripts are already per-session).

## 4. Architecture

```
TIER 0  GENERATED       ~40 lines   from the machine, every session. Cannot rot.
TIER 1  BATON.md       ≤120 lines   intent. Superseding-on-write. Constraint-heavy.
TIER 1b traps.jsonl     unbounded   path-keyed negative knowledge. PUSHED, never read wholesale.
TIER 2  ARCHIVE         unbounded   transcripts + DECISIONS + MemPalace. Queried, never read.
```

**Tier 0 — `baton status`.** Generated; replaces every prose assertion about the machine. Subject to the
law of effects above: probes observe **products**, not claims.

Costs below are **measured by the Project A session (2026-07-12), not estimated** — my own estimates were
wrong (I guessed the gate was "minutes"; it is 2.2s):

| Probe | Measured cost | Verdict |
|---|---|---|
| git (branch/HEAD/dirty/stash/unpushed) | instant | live |
| per-venv interpreter vs `pyvenv.cfg` | near-instant | live — *this is the check that would have saved Project A run 1* |
| **attribution gate** | **2.2s** | **live** (I guessed "minutes". Wrong. It's cheap.) |
| GPU — **real op**, `torch.randn(4,4).to('cuda')` | cheap | live. **Never** `torch.cuda.is_available()` |
| background/finished jobs | cheap | live — report **the effect** (artifact present? non-empty? mtime > inputs?), **never the exit code** |
| `journal.db` drift count | **80s** (I/O-bound, FUSE) | **cached**, with timestamp |
| test suite (184 tests) | 37s | on-demand, not at session start |

Cached probes always print their **age**. A stale cached value shown without its timestamp is exactly
the confident lie this system exists to kill.

**Tier 1 — `BATON.md`.** `MISSION` · `NOW` (one item) · `IN-FLIGHT` · `BLOCKED` · `OPEN` ·
`MODEL/ROUTING` (routing is load-bearing: a Sonnet session resuming a Fable-gated `NOW` will just do
it). **Written constraint-first**: negative constraints are the *only* rules empirically shown to
improve agent performance; positive directives ("handle edge cases") measurably **degrade** it via
checkbox mentality.

**Mechanical NOW-suspect marking:** when the transcript shows the command named in `NOW` was executed,
`NOW` is auto-flagged `⚠ SUSPECT (executed <ts>, unverified)`. No LLM, no discipline. Kills the
stale-`NOW` confident lie.

## 5. The pickup — and the correction that matters most

```
  Tier 0 status        REALITY    (generated; what is actually true right now)
+ BATON.md             INTENT     (NOW auto-flagged if suspect)
+ INTERRUPTED STATE    ORIENTATION (structured, packaged — NOT raw transcript)
```

> ### ⚠ v2 was WRONG: **never re-inject the raw transcript tail.**
> **Context-Contaminated Restart Model (CCRM):** keeping a failed attempt in context makes the retry
> **7.1× more likely to fail** (measured, SWE-bench Verified; cascade ratio ε₁/ε₀ = 7.1). A session that
> died in a loop, re-injected raw, **propagates the loop**. Failed reasoning acts as a semantic anchor.
> Clean restart dominates contaminated resume.

**Therefore: structured tail packaging — done mechanically.** Strip tool chatter and stack traces;
middle-truncate; emit a compact block, not a chat log:

```
[STATE: INTERRUPTED EXECUTION]
  Goal:         <last NOW>
  Active file:  <last file edited>
  Last action:  <last tool call>  → <first line of its error, if any>
  Died during:  <tool_use with no matching tool_result>   ← mechanical death-point detection
  Note:         previous attempt failed. Do NOT resume its approach uncritically.
```

Everything here is derivable **mechanically** from the transcript — no LLM, no summarisation. Orientation
without contamination.

Defensive: transcript format is *internal, subject to change*. All parsing sits behind one adapter
function and **degrades to empty, never to wrong**.

## 6. Negative knowledge — the core mechanism

**"Push, not pull" is confirmed correct.** Retrieval-based memory *structurally cannot* surface a
rejected approach: the agent doesn't know it was ever tried, so it never formulates the query. Vector
search matches the *positive* semantics of the approach it's about to retry. This is the **Query
Formulation Paradox**, and it is why v1's "just archive it, it's queryable" was wrong.

**Mechanism: path-bound failure cache with contextual push-reinjection.**

```
key = hash(file_path ⊕ command_signature)  →  trap record {what, why, when, session}
```

`PreToolUse` intercepts a tool call, extracts the target path/command, looks up matching traps, and
**injects them as a constraint for that turn only** — non-blocking, `additionalContext` only.

This **dissolves the eviction problem instead of patching it**: traps are *never all in context* — only
those relevant to the action about to be taken. Context cost is **O(traps for this action)**, not
O(all traps ever). v2's "exempt traps from eviction, keep them all in the file forever" is superseded.

**Capture (the project owner's call, 2026-07-12): async background extractor.** A `Stop` hook fires a background
worker (cheap model) that reads the completed turn, extracts candidate path-keyed traps, and writes them
marked `unratified`. Off the main loop ⇒ zero latency, zero nag, **zero reliance on model discipline** —
the only approach the research says works. The raw transcript remains authoritative; unratified traps are
reviewed cheaply at session start.

## 7. Eviction — the real policy

"Accumulate-forever" (Mem0, Letta, Zep) is **not a considered choice; it is an unsolved problem teams
endure.** The policy that survives production:

> **Subject–attribute entity resolution on write + step-function decay.**
> Don't append a fact — resolve it against the existing `(subject, attribute)`; mark the old record
> `superseded` (excluded from retrieval, retained for history); write the new one. Validity holds at 1.0
> until an event invalidates it, then drops to 0. **No gradual decay** — correct for technical config
> facts, which are true until they abruptly aren't.

Independently corroborated by the ADR literature (append-only log; supersede-and-link; never edit an
accepted record; carry explicit `Accepted`/`Superseded` status so the *live* subset is cheaply
filterable). Two unrelated sources converging.

Applies to `BATON.md` (superseding-on-write) and `DECISIONS.md` (gains status fields). **Traps are
exempt** — they are bounded by push-injection (§6), not by eviction.

## 8. Hooks — two, both non-blocking

| Hook | Job | Cost |
|---|---|---|
| `SessionStart` (startup·resume·clear·compact) | inject Tier 0 + Tier 1 + packaged INTERRUPTED STATE | ~3k tok, capped |
| `PreToolUse` (Edit·Write·Bash) | inject matching path-keyed traps **for that turn only** | ~0 unless a trap matches |
| *(`Stop` → async background trap extractor)* | fires and forgets; off the main loop | ~0 in-loop |

`PreCompact` **cut**: it can *block compaction* ⇒ context overflow ⇒ session death mid-work — BATON
causing the disaster it exists to prevent. `PostCompact` **cut**: empirically **never fired** (18 hook
events, two `/compact` calls, v2.1.207) — may not exist. `SessionEnd` **cut**: no guaranteed final turn,
so it can never be load-bearing.

**Precedence, stated in the injected preamble** (after compaction, context holds both the compactor's
summary *and* our injection; they will disagree):
> **Tier 0 (observed) > BATON.md (intent) > compaction summary > everything else.**

**Token budget:** Tier 0 ≤ 40 lines · Tier 1 ≤ 120 lines · interrupted-state ≤ 2k tok ⇒ **~3k tokens,
hard-capped, once per session start.** Today's nominal README → GAMEPLAN → MASTER read is ~30k, and is
skipped *because* of that cost.

## 8b. VERIFIED hook facts (empirical — from live hook fires, not inferred)

Captured 2026-07-12 against Claude Code **2.1.207**; raw payloads in `scratchpad/hookprobe/captured.jsonl`
(18 records). **Do not re-derive.**

| Fact | Status |
|---|---|
| `SessionStart` stdin: `session_id`, **`transcript_path`**, `cwd`, `hook_event_name`, `source` | **VERIFIED** |
| → transcript is **handed to us**; previous session = most recent *other* `.jsonl` in that dir | **VERIFIED** |
| `hookSpecificOutput.additionalContext` genuinely injects into model context | **VERIFIED** (marker phrase planted, read back by a fresh model) |
| `Stop` stdin: `transcript_path`, **`last_assistant_message`**, `stop_hook_active`, `background_tasks` | **VERIFIED** |
| `PostToolUse` stdin: `tool_name`, `tool_input`, `tool_response`, `tool_use_id`, `duration_ms` | **VERIFIED** |
| `PreCompact` stdin: `trigger`, `custom_instructions` — **this is the user's `/compact <text>`, not hook-supplied** | **VERIFIED** |
| **`PostCompact` NEVER FIRED** | **NOT CAPTURED** — do not build on it |
| `SessionStart.source` values beyond `"startup"`; `PreCompact trigger="auto"` | **UNVERIFIED** — handle defensively |

## 9. Kill-list — what BATON REMOVES

Non-negotiable. Without this it is a fourth memory system on the pile — the exact pathology of §1.

| Surface | Fate |
|---|---|
| `GAMEPLAN.md` handover block (1,200-word paragraph) | **replaced** by `BATON.md` + pointer |
| `DECISIONS.md` as a read surface | **Tier 2 only** — queried, never read; gains ADR status fields. **⚠ SEQUENCING (Project A): DECISIONS is currently read by *tailing* it, because no query tool exists. `baton recall` over DECISIONS must ship BEFORE the read path is removed, or sessions will keep tailing it out of necessity.** Do not close the door before opening the window. |
| Mandatory MemPalace session diary | **optional** — transcript covers it |
| Ad-hoc `STATE_*.md` / `OPUS_STATE_DUMP_*.md` (a downstream project ×7) | **abolished** (confirmed: **none** in Project A) |
| **Claude Code per-project auto-memory** — `/workspace/.claude/projects/-workspace-ProjectA/memory/*.md`, **56 files** for Project A alone | **RECONCILE — newly discovered, was not in the design.** A *harness-level* 5th memory system, written every session per standing instructions, holding session-continuity-shaped facts. **Overlapping purpose with BATON.** Must be reconciled (absorb, subordinate, or kill) — shipping BATON alongside it unexamined would be the §1 pathology exactly. |
| Global `CLAUDE.md` (~5k tokens **every session**) | **audit** — it is almost entirely *positive directives*, which the research shows **degrade** performance. Separate task. |

## 10. Security

`settings.json` holds `DEEPSEEK_API_KEY` and `SCRAPFLY_API_KEY` **in plaintext**; they reach command
lines and therefore transcripts. v1 would have piped that archive **to DeepSeek's API** — a credential
exfiltration path.

- **`recall` runs on RTFM, locally.** No third party.
- **Anything leaving the box is redacted first** (`sk-`, `key=`, bearer patterns).
- Tier 0 never dumps env.

## 11. Honest limits

- **Not "the gap, closed"** (v1's claim — struck). Turns *catastrophic* into *oriented*: reality verified,
  actions known, death point identified, traps pushed at the moment of danger. **Some conclusions will
  still need re-deriving.** That is the honest ceiling.
- **Two live sessions in one project** still contend for `BATON.md`. Each write stamps `session_id` +
  timestamp; on conflict **both are shown** rather than one silently clobbering the other. Not solved —
  *made visible*.
- **Transcript format may change between releases.** Best-effort; degrades to empty.
- **The trap extractor is an LLM** writing candidate traps. Bounded: candidates are `unratified`, the raw
  transcript stays authoritative, and a wrong trap costs a wasted line — not a wasted run.

## 12. Deliverable

**Two hooks. One file. One command.** No daemons, no per-tool-call latency, no second journal, nothing
that can block a session. *(Background trap extractor: deferred to Phase 3 — see §13.4.)*

---

## 13. v3.2 — Fable audit deltas (2026-07-12). BINDING on implementation.

Audited as Fable at the project owner's request. The v3.1 frame survives. Four corrections:

### 13.1 Trap classes — the seeded trap store violated its own design
As seeded, T-PROJA-04 triggered on `**/*.py` + all commands and T-PROJA-08 on `**` + all commands — they
would inject on **every tool call**, reproducing exactly the linter-fatigue failure the research warns
about (a constraint repeated every turn becomes wallpaper). **Fix (applied to `traps.jsonl`):**
- `class: "path"` — narrow triggers; matched by `PreToolUse`; injected **at most once per session per
  trap** (dedup marker in the session scratchpad). The wide-net mechanism.
- `class: "meta"` — principles, not path-bound. **Never** matched per-call. Rendered as one line each in
  the SessionStart pickup's TRAPS section — always in context, never repeated.
A trap that fires constantly protects nothing.

### 13.2 Tool health is NOT baton's job — compose, don't duplicate
`session-health-check.sh` has been rebuilt (verified by inspection, 2026-07-12) as **real functional
round-trips** — live API smoke calls, project-root-resolved RTFM, MemPalace `repair-status` — and
already runs on all four SessionStart matchers. It owns tool health. `baton status` owns: git · venv
interpreters · GPU (real op) · jobs-by-effect · project probes (cached, with age). The two compose in
the SessionStart hook chain. Baton re-implementing tool health would be the §1 pathology.

### 13.3 Auto-memory verdict: SUBORDINATE (the kill-list's open "RECONCILE")
Claude Code auto-memory stays, scoped to what it is structurally good at: **durable user/feedback facts**
(who the project owner is, how they work, corrections) + **pointers**. It auto-loads via MEMORY.md, survives with
zero hooks, and is the natural home for exactly the fact-class BATON doesn't carry. Everything
project-volatile in it moves out: intent → `BATON.md`, negative knowledge → `traps.jsonl`, decisions →
DECISIONS, stale machine-assertions → deleted. One home per fact-class:

| Fact-class | Home |
|---|---|
| Volatile project intent (NOW/IN-FLIGHT/BLOCKED/OPEN) | `BATON.md` (per project) |
| Negative knowledge | `traps.jsonl` |
| Decisions + rationale | project `DECISIONS.md` (Tier 2, ADR-flagged) |
| Durable user/feedback facts, pointers | auto-memory |
| Deep archive / cross-project episodic | MemPalace + transcripts |

A Haiku pass is classifying Project A's 56 files against this rubric (manifest → `automem_manifest.csv`);
**reviewed before anything moves** — spot-check discipline applies.

### 13.4 The async trap extractor is deferred to Phase 3 — sequenced, not reversed
It is the only speculative component (only LLM-in-the-loop, needs headless API calls from a hook,
per-turn cost, unproven yield). The project owner's decision to build it stands; it goes **after** the substrate
proves itself. Until then, capture = `baton trap` (one line, manual) + a mechanical
failure-candidate line in the pickup ("last session: N commands failed repeatedly — untrapped").
Ship the floor, then buy the ceiling.

### 13.5 Confirmed at audit
DECISIONS.md/GAMEPLAN.md/MASTER.md are **already in Project A's RTFM index** (checked live) ⇒ `baton recall`
is a thin `rtfm search` wrapper and the §9 sequencing constraint is cheap to satisfy. Project A is done
(W3a closed, D-Log-16) ⇒ adoption unblocked. Implementation game plan: **`GAMEPLAN.md`** (this dir).

---

## 14. Self-governance & bake-in audit (added 2026-07-15, BINDING — the post-Fable constitution)

Fable access ends ~2026-07-17. The system must not lean on any model tier existing.

### 14.1 No privileged model
Worklists (GAMEPLAN Phase-2b style: rulings pre-made, acceptance criteria stated, stop conditions
explicit) are executable by **any capable session**. Judgment a worklist doesn't cover is never
improvised: it goes to `BATON.md → BLOCKED/OPEN` and waits for **the project owner** (with whatever model they have —
Opus, Sonnet, anything). Standing Fable rulings remain binding until the project owner supersedes them. A missing
judgment tier stalls a *task*, never the *system* — everything degrades to "the session proceeds
normally."

### 14.2 The law of auditable degradation
> **Invisible in-loop, visible post-hoc.** Degrading silently to the user is the requirement;
> degrading untraceably is a defect.

- `safe()` counts every swallowed exception; the count rides on each audit record.
- Every `hook` fire writes one JSONL record (project, chars vs budget, truncation, SUSPECT,
  no-BATON.md, DEGRADED, swallowed, ms). `pretool` writes a record **only** on trap-fire or error —
  silence is not an event.
- `logs/audit.jsonl`, append-only, 5 MB rotation keeping one predecessor. Never blocks: the write is
  `safe()`-wrapped, and **a gap in the log is itself the finding** (hooks demonstrably firing with no
  records ⇒ audit path broken).

### 14.3 The bake-in ritual (~2 weeks from go-live, then ad hoc)
`baton audit 7`, weekly, by the project owner or any session. What GOOD looks like: pickups present for every
active project, chars well under budget, `swallowed: none`, traps firing occasionally and deduped,
zero pretool errors, zero DEGRADED. What BAD looks like (each is actionable): **truncated×N** (a
BATON.md is bloating — supersede it) · **suspect×N persistent** (a NOW is stale — fix the file, not
the marker) · **NOISY trap** (trigger too broad or dedup broken — narrow it) · **swallowed clusters**
(a probe rotted — find it before it matters) · **DEGRADED** (pickup itself failed — read the exception
name in the record) · **no records at all** (audit path broken). Selfcheck runs leave deliberate
garbage-stdin records; they are expected noise with `session_id: "sc"`/unknown.

### 14.4 Mechanical change control
`tests/selfcheck.sh` **is** the 1.3 acceptance floor, executable in one command: compile, garbage/
empty/real stdin → valid JSON + exit 0 (both hooks), pickup ≤ budget, gross-latency ceiling, tracked-
file key scan, audit-path liveness. Installed as the repo's pre-commit (`cp tests/pre-commit.sh
.git/hooks/pre-commit` — re-run after any fresh clone/pod). **Proven blocking 2026-07-15**: a
syntax-broken baton.py and a staged key-shaped string were both refused. A broken baton.py is
physically uncommittable; T-INFRA-04 is now enforced by the repo, not by memory.

### 14.5 Bounded surfaces
Pickup: hard char cap, truncation self-announcing. BATON.md: 120-line clamp, visible in pickup.
Audit log: rotation. Trap *pushes*: bounded by path-matching + per-session dedup (§6) — `traps.jsonl`
itself may grow; that is by design and §13.1 keeps it out of context. Nothing else may grow unbounded;
any new surface must state its bound here before it ships.
