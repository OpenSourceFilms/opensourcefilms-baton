────────────────────────────────────────────────────────────────────────
ds_research: How do production LLM coding agents handle session continuit
────────────────────────────────────────────────────────────────────────

ANSWER:
The provided sources address only a fraction of the full research
question. Context‑rot / lossy‑compaction and its mitigations are well
covered; general memory‑layer products are mentioned; but the detailed
agent‑memory architectures (working/episodic/semantic/procedural),
MemGPT/Letta virtual‑context management, handoff/checkpoint patterns for
named coding agents, reasons for re‑attempting rejected approaches, and
the distinction between generated/derived and narrated state remain
absent from these three documents.

---

## What the Sources Say

### 1. Context Rot / Lossy‑Compaction and Known Mitigations
Source 3 (Tian Pan, 2026‑02‑26) describes the problem precisely:
- Context rot is not merely a token‑limit issue but a decay in model
accuracy as the effective context grows. It states: “GPT‑4's accuracy
drops from 98.1% to 64.1% based solely on where in the context window
information is positioned.” (URL 3)
- Compaction (lossy LLM‑driven summarisation) is a primary mitigation,
but it carries trade‑offs:
  - In one trial a 335K‑token context was compressed to ~2.8K tokens
(“roughly 120:1 compression”).
  - JetBrains Research found that summarisation “paradoxically
*lengthen* trajectories by 13–15%” because summaries obscure natural
stopping signals, and “Summarization costs … exceeded 7% of total
per‑instance expenses.” (URL 3)
  - Custom prompts are essential: “Custom instructions *replace* the
default prompt entirely, so they also bear full responsibility for
covering what the default would have caught.”
- Tool‑result clearing (mechanical pruning without LLM cost) can replace
old tool results with `[cleared to save context]` while preserving
tool‑call records. Critical design choice: which tools to exclude (e.g.,
memory, auth tokens).
- The article frames context management and cost management as
identical: “A 60% compression cut brings … $250K/year to $102K.” (URL 3)

### 2. Agent Memory Architectures
Only Source 1 (mem0ai/mem0 GitHub repo, fetched 2026‑07‑12) discusses a
general memory layer. It offers:
- “Multi‑Level Memory: Seamlessly retains User, Session, and Agent state
with adaptive personalization”
- Features: entity linking, multi‑signal retrieval (semantic + BM25 +
entity matching), temporal reasoning.
- It does **not** discuss working vs episodic vs semantic vs procedural
memory taxonomies, memory‑eviction/forgetting policies, or MemGPT/Letta
virtual‑context management beyond the product’s own “ADD‑only
extraction” where “memories accumulate; nothing is overwritten.”

Thus, the requested memory architectures are not covered.

### 3. Handoff/Checkpoint Patterns for Specific Coding Agents
None of the sources mentions Devin, Cursor, Aider, OpenHands, SWE‑agent,
Claude Code, or Cline or any session‑resumption patterns.

### 4. Failures: Re‑attempting Already‑Rejected Approaches
Not directly addressed. Source 2 discusses compounding error and the
half‑life of task‑length success, but not the specific behavioural
failure of repeating rejected solutions. Source 3 notes that compaction
can obscure stopping signals, causing agents to “keep attempting past
their optimal stopping point,” which hints at a failure mode but does
not explain why an agent would re‑attempt a previously rejected approach
or what stops it.

### 5. Generated/Derived State vs Narrated State in Agent Scratchpads
No source discusses this distinction.

---

## VERIFIED (Source‑Backed Claims)

| Claim | Source |
|-------|--------|
| GPT‑4 accuracy drops from 98.1% to 64.1% based on information position
in context window | URL 3, Tian Pan 2026‑02‑26 |
| Large contexts (330K+ tokens) can be compacted to ~2.8K tokens, but
with loss and inference cost | URL 3 |
| JetBrains Research: summarisation lengthens trajectories by 13‑‑15%
and costs >7% of per‑instance expenses | URL 3 |
| Forrester: ~65% of enterprise AI failures trace to context drift or
memory loss | URL 3 |
| Mem0 provides multi‑level memory (User, Session, Agent) and “ADD‑only
extraction” where “memories accumulate; nothing is overwritten” | URL 1
(GitHub repo readme) |
| Compounding error: per‑step reliability of 95% over 20 steps yields
only 36% overall success | URL 2 (buildmvpfast.com) and URL 3 |
| METR: frontier agents succeed on ~100% of tasks a human does in <4
min, but <10% on tasks taking >4 hours; model time horizon doubles
~every 7 months | URL 2 (citing METR report) |

---

## INFERRED (Reasonable Inference from Sources)

- The Mem0 memory layer could be used to persist session state for
coding agents, but no specific integration with the named tools is
present.
- The context‑rot and compounding‑error numbers likely apply to coding
agents, because the degradation is model‑agnostic.

---

## UNCERTAIN

- Whether the JetBrains trajectory‑lengthening effect applies only to
specific agent setups or is general.
- The precise mechanism by which compaction obscures stopping
signals—mentioned without deep elaboration.

---

## NOT FOUND (Explicit Gaps)

- **Working, episodic, semantic, procedural memory architectures** in
LLM agents (e.g., MemGPT/Letta paper, generative‑agents
reflection+retrieval).
- **Memory eviction/forgetting policies** (beyond Mem0’s “nothing is
overwritten”).
- **Handoff/checkpoint patterns** for Devin, Cursor, Aider, OpenHands,
SWE‑agent, Claude Code, Cline.
- **Why agents re‑attempt already‑rejected approaches** and what stops
it.
- **Generated/derived state vs narrated state** in agent scratchpads.

---

## PRIOR KNOWLEDGE

None used; all statements are derived from the three sources.

---

## SOURCES USED

1. `https://github.com/mem0ai/mem0` (fetched 2026‑07‑12T03:48:19Z) –
README snippet on memory layer, benchmarks, features.
2. `https://www.buildmvpfast.com/blog/why-long-horizon-ai-agents-fail-
compounding-error-memory-2026` (fetched 2026‑07‑12T03:48:31Z) –
compounding error, METR, tau‑bench.
3. `https://tianpan.co/blog/2026-02-26-context-engineering-memory-
compaction-tool-clearing` (fetched 2026‑07‑12T03:48:36Z) – context rot,
compaction, tool‑result clearing.

────────────────────────────────────────────────────────────────────────
search_provider:               ddgs, jina_search
fetch_provider:                jina_reader(keyed), direct_http
used_jina_key:                 True
used_jina_search:              True
cache_hit:                     False (0 source(s))
jina_search_requests_used:     1
sources_fetched:               3
────────────────────────────────────────────────────────────────────────
