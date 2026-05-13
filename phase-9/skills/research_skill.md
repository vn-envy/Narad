# Buddha Research Skill — `buddha:research`

## Overview
A five-phase workflow for deep synthesis of academic and technical research.
Activated when TASK_TYPE = `research`.

Matsya gathers the raw sources; Buddha synthesises them.
When you reach Phase 2 (GATHER), your sources are already in context from Matsya —
tabulate them rather than re-querying. If sources are missing, request them from Matsya.

---

## Phase 1: FRAME

Before touching any sources, define the research question precisely:

- **Core question** (one sentence — not a topic, a question with a specific answer)
- **Sub-questions** (3–5) — if answered together, they answer the core question
- **What a good answer looks like**: empirical? comparative? theoretical? practical?
- **Scope boundaries**: time range (e.g. 2020–present), domain, depth required

Emit the frame as a numbered list.
Ask one clarifying question if the core question is genuinely ambiguous.

`CURRENT_PHASE: gather`

---

## Phase 2: GATHER

Organise all sources received from Matsya into a source table:

| # | Source | Title / ID | Year | Citations | Relevance |
|---|--------|-----------|------|-----------|-----------|
| 1 | arXiv  | ...        | 2024 | —         | answers sub-Q 2 |
| 2 | S2     | ...        | 2023 | 412       | background for sub-Q 1 |

Rules:
- Each sub-question from Phase 1 must have ≥ 2 distinct sources
- Prefer sources with: high citation counts, recent dates (≤ 2 years), open-access PDFs
- For model/implementation sub-questions: include search_hf_models and query_deepwiki results
- Do NOT synthesise yet — this phase ends when the source table is complete

If sources are thin for a sub-question, note it here (it becomes a gap in Phase 4).

`CURRENT_PHASE: triangulate`

---

## Phase 3: TRIANGULATE

Cross-check major claims across sources. Output a claims table:

| Claim | Sources | Status | Confidence |
|-------|---------|--------|------------|
| "Diffusion models outperform GANs on FID" | [A, B, C] | corroborated | high |
| "Classifier-free guidance improves quality" | [B] | single-source | medium |
| "DDPM is faster than score-based models" | [A] vs [C] | contested | low |

Status values: `corroborated` / `single-source` / `contested` / `assumed`

Name contradictions explicitly: "Source A says X; Source B says Y — unresolved."

`CURRENT_PHASE: gaps`

---

## Phase 4: GAPS

List what the sources do NOT answer. Rate each:

| Gap | Description | Rating |
|-----|-------------|--------|
| Scalability beyond 512px | No source addresses >512px resolution | critical |
| Inference cost comparison | Only 1 source has runtime benchmarks | moderate |
| Non-English text generation | No source covers multilingual | minor |

Rating: `critical` (synthesis is incomplete without it) / `moderate` / `minor`

Include:
- Sub-questions from Phase 1 that remain unanswered
- Unresolved contradictions from Phase 3
- Post-publication developments that may change conclusions
- Empirical claims without experimental validation in the sources

`CURRENT_PHASE: synthesise`

---

## Phase 5: SYNTHESISE

Structure:

1. **Direct answer** to the core question (paragraph 1 — no hedging preamble)
2. **Sub-question answers** — one section per sub-question, with evidence citations
   (arXiv ID, author/year, or paper title)
3. **Consensus view** — what the field broadly agrees on
4. **Dissenting positions** — name them fairly, with source
5. **Critical gaps** — restate from Phase 4; never omit
6. **What would change the picture** — what further research or evidence would most
   alter the current conclusions

Formatting: use headers for each section. Include inline citations: (Author et al., Year)
or [arXiv:XXXX.XXXXX]. End with a list of all cited sources.

NEVER omit gaps — a synthesis that hides uncertainty is a research failure.

`DONE`

---

## Notes

- All five research tools are available via Matsya: search_arxiv, search_papers,
  search_hf_papers, search_hf_models, query_deepwiki
- Maximum synthesis depth: if sources span >10 papers, group by theme rather than
  addressing each individually
- For SOTA model questions: always include a search_hf_models result in the source table
- For repo architecture questions: always include a query_deepwiki result
