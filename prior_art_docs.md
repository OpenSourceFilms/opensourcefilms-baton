────────────────────────────────────────────────────────────────────────
ds_research: Proven practices for stopping engineering documentation from
────────────────────────────────────────────────────────────────────────

**ANSWER**  
Based on the provided sources, only the **Architecture Decision Records
(ADR) practice** can be addressed in detail. The sources describe a
specific discipline for managing ADRs at scale:

- **Superseding, not editing** – ADRs are treated as an *append-only
log*. When a decision changes, you write a new record that supersedes
the old one, link the two together, and never edit an accepted record.
- **Focused, single‑decision records** – Each ADR captures one decision;
if a decision has multiple phases, each phase gets its own record. This
granularity helps keep hundreds of records manageable.
- **Consistent anatomy and status tracking** – Every record uses a
consistent template (problem, options, decision, trade‑offs, confidence,
status like *Proposed*, *Accepted*, or *Superseded*) so that the current
state of each decision remains clear.

These mechanisms directly answer how decision ledgers stay useful as
entries grow, and they partially mitigate the “stale assertion” failure
mode by making changes explicit through new, linked records.

The remaining sub‑questions – documentation eviction/archival policies,
generated‑vs‑handwritten docs, SRE/on‑call handover practices, the
precise “stale assertion” pattern of silently false environment facts,
and why single‑source‑of‑truth architectures fail – are **not addressed
by any of the three sources**. No information is available about those
topics from this bundle.

---

**VERIFIED** (claims backed by source text and direct URL)

- On the **append‑only, supersede‑and‑link** approach:  
  *Source 3 – Microsoft Azure Well-Architected Framework*  
  > “The ADR serves as an append‑only log. Don't go back and edit
accepted records. If a decision changes, write a new record that
supersedes the original and link the two together. This approach
preserves the history of your thinking and makes it clear when and why
the direction shifted.”
  (https://learn.microsoft.com/en-us/azure/well-architected/architect-
role/architecture-decision-record, fetched 2026-07-12)

- On **breaking one decision into multiple records** (helps scale):  
  *Source 3*  
  > “Break one decision into multiple if an architectural decision is
going to result in multiple phases, such as short-term, mid-term, long-
term approaches. Log each phase as its own decision record.”

- On **superseding and linking** in practice:  
  *Source 1 – AWS Architecture Blog* (truncated, but the relevant line
is present)
  > “… If an ADR supersedes a previous one, document the change and link
…”
  (https://aws.amazon.com/blogs/architecture/master-architecture-
decision-records-adrs-best-practices-for-effective-decision-making/,
fetched 2026-07-12)

- On **single‑decision focus** (scalability tactic):  
  *Source 1*  
  > “Focus on a single decision – Keep ADRs concise by focusing on a
single decision. Don’t hesitate to split up decisions if necessary.”

- On **status tracking** to maintain clarity:  
  *Source 3*  
  > “Status, such as *Proposed*, *Accepted*, or *Superseded*. Tracking
status makes the current state of each decision clear, especially as the
number of decisions grows.”

- On the **problem the ADR solves** (knowledge transfer, avoiding
repeating debates – related to stale assertions):
  *Source 3*  
  > “A decision that's made but never recorded will likely be forgotten,
leading to repeated debates or later changes that unknowingly contradict
the original intent.”

---

**INFERRED** (reasonable connections beyond explicit text)

- The combination of an **append‑only log, explicit status
(`Superseded`), and linking** between records creates a form of built‑in
indexing: you can always find the current state of a decision by looking
at the latest record for a topic. This is an implicit answer to
“indexing” – the sources do not describe a separate catalogue or index,
but the discipline of replacing records with linked successors provides
discoverability.
- Splitting decisions into **small, single‑topic records** likely
prevents the “unreadable volume” problem by keeping each entry extremely
focused; with hundreds of records, readers can scan titles and statuses
rather than dense, multi‑topic documents.
- The emphasis on never editing accepted records but always documenting
a *new* decision when something changes directly counteracts the “stale
assertion” failure mode, at least for *architecture decisions* – the
history makes it clear when something is no longer true. However, this
only covers *decisions* that are architecturally significant, not all
environment facts.

---

**UNCERTAIN** (aspects the sources do not resolve)

- How teams **actually index or search** hundreds of ADRs beyond the
linking mechanism is not specified. No search tooling, tagging, or
folder‑structure practices are mentioned.
- Whether the “supersede and link” method **scales** to *hundreds* of
decisions without auxiliary tooling is unstated – the sources claim it
works based on experience with >200 ADRs, but no empirical data or
detailed organisational method is given beyond linking.
- The sources **do not quantify** the cut‑off between “two‑way door”
decisions that can be made quickly and those that merit an ADR; this
could affect how the ledger grows.

---

**NOT FOUND** (sub‑topics with zero coverage in these sources)

- **(2) Documentation eviction/archival policies** – No source discusses
pruning, retiring, or deleting old documentation; all guidance is about
*keeping* and superseding, not removing.
- **(3) Generated‑vs‑handwritten docs** – No mention of docs‑as‑code,
“if it can be checked, don't write it down”, or generation from
schemas/tests.
- **(4) SRE/on‑call handover practice** – Not mentioned in any source;
the sources are limited to architecture decisions, not operational
handover.
- **(6) Single‑source‑of‑truth doc architectures and why they fail** –
Source 3 *encourages* a single source of truth (“They also encourage the
teams' unified adoption of a single source of truth to be used for
reference, audits, and incident response”), but does not discuss failure
modes or challenges.
- **(5) The specific ‘stale assertion’ failure mode** – The concept of
an environment fact silently becoming false and misleading an operator
is not directly addressed. The ADR process mitigates this for
*decisions* by making changes visible, but the sources do not discuss
the broader problem of environment drift.

---

**PRIOR KNOWLEDGE** (anything beyond these sources – labelled
UNVERIFIED)

- No prior knowledge added; all above is derived strictly from the
provided sources.

---

**SOURCES USED**

1. https://aws.amazon.com/blogs/architecture/master-architecture-
decision-records-adrs-best-practices-for-effective-decision-making/
(fetched 2026-07-12T03:50:20.283688+00:00)
2. https://dev.fandom.com/wiki/User_blog:Ritaly-
dev/Keep_programmers%E2%80%99_decisions_documentation_up-to-
date_and_simple_with_Architecture_Decision_Records (fetched
2026-07-12T03:50:20.283730+00:00)
3. https://learn.microsoft.com/en-us/azure/well-architected/architect-
role/architecture-decision-record (fetched
2026-07-12T03:50:20.283643+00:00)

────────────────────────────────────────────────────────────────────────
search_provider:               ddgs, jina_search
fetch_provider:                jina_reader(keyed)
used_jina_key:                 True
used_jina_search:              True
cache_hit:                     False (0 source(s))
jina_search_requests_used:     1
sources_fetched:               3
────────────────────────────────────────────────────────────────────────
