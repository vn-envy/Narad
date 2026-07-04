# Narad — First Principles Architecture Document

> *"Narad moves between worlds. He carries nothing except information. That is the design."*

---

## Preface: Who This Is For

This document is for anyone who wants to understand what Narad is, how it works at every level, and why it was built the way it was. It assumes no prior knowledge of Indian mythology, no familiarity with multi-agent AI systems, and no assumption that you have read the code. If you have read the code, this document will tell you *why* it is written the way it is.

---

## Part I — The Problem Space

### What Most Multi-Agent AI Systems Get Wrong

Before explaining what Narad does, it is worth being precise about the failures of the category it belongs to.

**Multi-agent systems** are AI architectures where, instead of asking a single large language model to do everything, you have a coordinator that delegates different tasks to different specialist models. In theory, this should produce better results: a specialist trained or prompted to write code should write better code than a generalist. In practice, most multi-agent systems fail on seven predictable axes.

---

**Failure 1: The Identity Collapse Problem**

When you tell a generic language model "you are a code expert," that instruction does not actually constrain the model. The model was trained on everything; it will drift. It will hedge. It will try to be helpful about topics outside its designated role. "You are a code expert" is a costume, not a character.

The deeper issue is that the role has no *load-bearing structure*. There is no mythology, no history, no accumulated expectation behind the label. The model has no reason to stay within it.

**Failure 2: The Routing Hallucination Problem**

In most multi-agent systems, a supervisor LLM decides which specialist to call. That decision is a generation — the model produces words describing a routing choice. But the supervisor has no ground truth about what each specialist is actually good at. It routes based on surface keywords, not on deep understanding of capability boundaries.

"Fix my bug" → goes to the code agent. "My function returns None" → also should go to the debugger, but might go to a generic "code" agent, or worse, might go to a "research" agent because the supervisor saw "function" and associated it with documentation.

**Failure 3: The Amnesiac System Problem**

Most multi-agent systems are stateless. Each conversation turn starts fresh. The system does not remember that three sessions ago, you asked about FastAPI and got an excellent answer. It does not remember that last week you told it your company is called Lumina. Every turn, it starts from zero.

**Failure 4: The Frankenstein Problem**

This is the most structural failure. When you bolt together a GPT-4 wrapper, a Python tool runner, a retrieval API, and a web scraper, you get something that works — sometimes — but has no coherent design philosophy. You cannot explain it. You cannot trust it. You cannot improve it systematically.

The components were designed independently. The joints are patches. When it fails, you cannot tell if the failure was in the router, the specialist, the memory layer, or the tool. The system has functionality but no identity.

The Frankenstein problem is not about code quality. It is about the absence of a *unifying metaphor* — a frame that makes the parts feel like they belong together and that gives each component a clear reason for existing.

**Failure 5: The Evaluation Void**

Most multi-agent systems are shipped without a feedback loop. You do not know if the system is getting better. You do not know which agents are producing high-quality outputs and which are producing noise. There is no systematic way to take the good outputs and teach the system from them.

**Failure 6: The Single-Model Bottleneck**

Many systems use the same model for every task — routing, synthesis, code, prose, analysis. This ignores the fact that models have genuine capability differences across domains.

**Failure 7: The Explainability Gap**

When the system produces a bad output, you cannot reconstruct what happened. Which agent ran? How long did it take? What context did it receive? Without structured observability, every failure is a black box.

---

## Part II — The Cultural Foundation

### Why Mythology Is a Better Framework Than Generic Roles

The avatāras in this system are drawn from the **Dashavatara** — the ten descents of Vishnu. In the tradition, Vishnu does not interfere in the world directly. When the world needs something — when chaos needs to be contained, when knowledge needs to be retrieved, when something broken needs to be fixed — Vishnu takes a specific *form* for that purpose. Each form is an avatāra: a deliberate manifestation with a defined nature and a defined mission.

This is architecturally useful. The mythology pre-solves the identity collapse problem. The avatāras do not need to be constrained by a prompt. They are constrained by *accumulated narrative weight*.

**Narad** is the divine messenger and kalakar (musician). In the traditions, Narad is not a warrior or a scholar or a builder. Narad moves between worlds — between heaven and earth, between gods and humans, between knowledge and action. He holds the Mahati veena — gifted to him by Krishna — and decides which string to pluck. Narad's function is transmission: taking the right information to the right place at the right time. He does not act; he enables others to act. This is a perfect description of a router.

The product is called **Narad**. The specialist agents are called **avatāras** — the Sanskrit word that describes exactly what multi-agent AI always was: a specialized form that descends with purpose, completes its mission, and releases.

### The Frankenstein Rule

> **Every component must have a canonical identity that makes its presence in the system self-explanatory. If you cannot explain why a component exists in one sentence using the system's own metaphor, the component should not exist or should be renamed until it can.**

---

## Part III — The Architecture

### System Overview

```
┌─────────────────────────────────────────────────────────────┐
│                        FRONTEND                              │
│  ChatPanel · DarshanPanel · SutraPanel · KarmaSheet         │
│  ParashuramTerminal                                          │
└─────────────────────┬───────────────────────────────────────┘
                      │ POST /chat (SSE stream)
                      ▼
┌─────────────────────────────────────────────────────────────┐
│                    NARAD (Router)                             │
│  DeepSeek V3 · Google ADK Runner · InMemorySessionService   │
└──────────────────────────┬──────────────────────────────────┘
                           │ FunctionTool calls (parallel or sequential)
           ┌───────────────┼───────────────┐
           ▼               ▼               ▼
    ┌─────────────┐  ┌─────────────┐  ┌─────────────┐
    │  Avatāra 1  │  │  Avatāra 2  │  │  Avatāra N  │
    │  (LlmAgent) │  │  (LlmAgent) │  │  (LlmAgent) │
    └──────┬──────┘  └──────┬──────┘  └──────┬──────┘
           │                │                │
    ┌──────▼──────────────────────────────────▼──────┐
    │              PRE-RUN ENRICHMENT                  │
    │  Smriti.recall() → LanceDB vector search         │
    │  SutraEngine.get_active_sutras() → ranked inject │
    │  Sankalpa.get_active_sankalpas() → style inject  │
    └──────────────────────┬──────────────────────────┘
                           │ enriched_task
                           ▼
    ┌──────────────────────────────────────────────────┐
    │              POST-RUN PERSISTENCE                 │
    │  Smriti.remember() → embed + store               │
    │  Tapas.process_session() → score → promote       │
    │  Yantra → write trace JSONL                      │
    │  Sankalpa.observe_session() → style modeling     │
    └──────────────────────────────────────────────────┘
```

---

### Five-Layer Architecture

| Layer | What it is | Where it lives | Local? |
|---|---|---|---|
| **L1 — Narad (Supervisor)** | DeepSeek LlmAgent with routing instruction and 4 handoff tools. Sees full conversation history. Routes to 1–3 avatāras per turn. | `phase-1/narad_agent.py` | 🟡 Cloud |
| **L2 — Avatāras (Specialists)** | 4 LlmAgent instances (Matsya, Rama, Krishna, Parashurama), each with focused system prompt, tool set, and DeepSeek model. Wrapped in FunctionTool with Smriti + Sutra + Sankalpa injection. | `phase-1/avatar_agents.py` | 🟡 Cloud |
| **L3 — Smriti (Memory)** | LanceDB vector memory (Gemini `text-embedding-005`, 768-dim; OpenAI fallback). `@lru_cache(128)` on `_embed()` deduplicates Gemini calls within a session. Vismriti decay (90d TTL), dedup gate (L2 < 0.10). FTS5 exact-match at `~/.narad/memory_fts.db` — routed selectively to Parashurama. Smriti v2: project-scoped Markdown wiki + optional Graphiti graph; `get_project_context()` injected as fourth context layer for project-tagged sessions. | `phase-2/smriti.py`, `phase-9/smriti_v2.py` | ✅ Local only |
| **L4 — Darshan (Observability)** | Yantra JSONL tracer (session_start, avatar_start, avatar_done + trajectory + usage, phase_transition, plan_created, routing_decision, session_done, error + `error_type`). AwarenessBar (72 px right strip). DarshanDashboard 5-tab drawer (Live, Kanban, Sutras, Memory, Ops). Scribe post-session wiki compiler. Andon gate fires `andon_alert` + `andon_diagnosis` SSE events. | `phase-2/yantra.py`, `phase-4/frontend/`, `phase-9/scribe.py`, `phase-1/andon.py` | ✅ Local |
| **L5 — Tapas (Self-Evolution)** | Sutra extraction (per-avatāra, hallucination_free hard gate, sequence_correct penalty, CAI critique, DeepSeek R1 judge with 2-retry exponential backoff). Sankalpa user style modeling. Karma audit log. `tapas_skipped` Karma event when judge unavailable after retries. | `phase-3/tapas.py`, `phase-5/sutra_engine.py`, `phase-6/sankalpa.py` | ✅ Local only |
| **L6 — Six Sigma** | Kanban step lifecycle (Karyakrama) · Andon quality gate (Jaagruti) · 5S filesystem health (Shuddhi) · DMAIC reporting (Viveka). | `phase-1/kanban.py`, `phase-1/andon.py`, `phase-1/narad_5s.py` | ✅ Local |

### Canonical Storage (~/.narad/)

| Path | What it holds |
|---|---|
| `~/.narad/sessions/` | Yantra JSONL traces, one file per session |
| `~/.narad/memory/` | LanceDB vector store (Smriti v1) |
| `~/.narad/wiki/` | Project wiki pages (Smriti v2) — `{user_id}/{project_id}/entity.md` |
| `~/.narad/artifacts/` | Code executor outputs, keyed by run ID |
| `~/.narad/config/` | `sutras.jsonl`, `karma.jsonl`, `sankalpas.jsonl`, `andon_log.jsonl`, `5s_shine_log.jsonl`, override files |
| `~/.narad/finance.db` | SQLite finance database |
| `~/.narad/health.db` | SQLite health database — `symptom_log`, `medication_reminders` |
| `~/.narad/memory_fts.db` | SQLite FTS5 full-text search index (Vismriti layer) |
| `~/.narad/kanban.db` | SQLite Kanban board — all plan step states (Phase 13) |
| `~/.narad/plans/` | Rama project plan JSON (`{session_id}.json`) — one per multi-avatar plan |
| `~/.narad/manifest.json` | 5S Set-in-Order directory index (Phase 13) |
| `~/.narad/5s_policy.json` | 5S retention policy — TTL thresholds per data type (Phase 13) |

### Avatar Domain Quick-Reference

| Avatāra | Domain |
|---------|--------|
| **Matsya** | Web search, JS pages, REST API calls, interactive forms, document understanding, local information access, research synthesis |
| **Rama** | Structured planning, SOPs, calendar management, budget and savings plans, finance workflows, health logging |
| **Krishna** | Email drafting + sending, education, presentations, video, wellness and triage guidance |
| **Parashurama** | Code, shell, automation, SQL (read-only), React/shadcn UI, FastMCP server generation |

### Phase Structure

| Phase | Name | What it solved |
|-------|------|----------------|
| 0a | Model Evaluation | Which LLM should route? (DeepSeek 93% vs GPT-4o 84%) |
| 0b | Proof of Concept | Does the ADK + SSE architecture work at all? |
| 1 | Live Agents | Replace stubs with real LLMs, FastAPI SSE server |
| 2 | Memory + Search + Observability | Agents with recall, Matsya with Tavily, traces |
| 3 | Tapas | Self-improvement feedback loop |
| 4 | Frontend | Visible system state, DarshanPanel call graph |
| 5 | Sutra Engine + Karma | Memory promotion → injection pipeline |
| 6 | Sankalpa | Per-user style and intent modeling |
| 7 | Code Executor + Media | Sandboxed execution, Gemini Veo/Imagen, video/audio |
| 8 | Tier 1 Skills | All four canonical agents fully tooled across their active disciplines |
| 9 | Project System + Memory UI | Scribe, Smriti v2, ProjectsPanel, canonical `~/.narad/` paths |
| 10 | Observability, Memory & Guardrail Refinements | Yantra v2 (token costs, phase_transition, result_digest); Smriti v1.5 (Vismriti decay, dedup, FTS5); Dharma Layer; Tapas (0.80 threshold, R1 judge, CAI critique); Karma enrichment; sutra sanitization |
| 11 | Project System + Memory UI | Scribe, Smriti v2, project detection, left panel |
| 12 | AssetOpsBench Integration | Typed trace models (`Trajectory`/`TurnRecord`/`ToolCall`); `_TokenMeter`; `_parse_json()`; `Plan`/`PlanStep` + `levels()` + plan-aware Narad dispatch; Gemini embeddings in Smriti; Markov spend patterns (Rama); health anomaly detection (z-score + Granite TTM); Tapas `hallucination_free` hard gate + `sequence_correct` penalty; FastMCP template in Parashurama |
| 13 | Six Sigma Quality Layer + Darshan Dashboard | Kanban step lifecycle (`kanban.py`); Andon quality gate (`andon.py`, 4 trigger classes); 5S filesystem health (`narad_5s.py`); DMAIC report via Parashurama (`POST /quality/report`); AwarenessBar + 5-tab DarshanDashboard (Live/Kanban/Sutras/Memory/Ops) |
| 14 | Notion Sync Bridge | ❌ Cut in M0 (2026-07-04) — was push-only with silent failures; removed entirely |
| Pre-15 | Production Hardening | Defensive `shell_skill` import fallback; FTS5 `recall_exact()` routed to Parashurama; Smriti v2 `get_project_context()` wired into pipeline; embedding LRU cache (`@lru_cache(128)` on `_embed()`); Tapas 2-retry backoff + `tapas_skipped` Karma event; `error_type` field on all Yantra error events; shared frontend constants (`avatara-constants.ts`, `format-time.ts`); clear chat, sutra confirmation, bulk accept, bounded `stepEvents`, native artifacts |
| 15 | Electron Desktop Packaging | Local Gemma 4 E4B, signed macOS installer, offline-first mode | ← Next |

---

## Part IV — Component-Level Breakdown

---

### `model_config.py` — The Model Assignment Table
**Location:** `phase-1/model_config.py`

A single dict mapping each avatāra name to a LiteLLM model string. Encodes the phase-0a benchmark result: DeepSeek outperformed GPT-4o on routing by 9 points (93% vs 84%). Domain-specific assignments: Rama and Parashurama use DeepSeek V4 Pro (planning + code reasoning), Matsya and Krishna use DeepSeek V4 Flash (retrieval/prose, cost matters). Per-avatar env overrides (`{AVATAR}_MODEL`) + vision/visual-output routing also live here.

**Data:** No persistent data. In-memory constants.

---

### `narad_agent.py` — The Router
**Location:** `phase-1/narad_agent.py`

A Google ADK `LlmAgent` — a language model with a system prompt and a set of callable tools (the four avatāras). Narad's system prompt defines routing rules: which avatāra handles which type of query, how to handle multi-deliverable queries (parallel routing), and how to use conversation history for follow-up resolution.

**Key design decision.** Each avatāra is wrapped as a `FunctionTool`, not an `AgentTool`. This is because LiteLLM + `AgentTool` has a serialization bug in ADK 1.32 that causes the supervisor to output the tool call as text instead of executing it. The FunctionTool workaround is a patch for a framework limitation, not an architectural preference.

**Data:** No persistent data. Runtime state only.

---

### `plan_models.py` — Typed Project Plan Model
**Location:** `phase-1/plan_models.py`

`PlanStep` and `Plan` dataclasses for Rama's structured multi-avatar project plans. `Plan.levels()` performs topological sort on `PlanStep.dependencies`, returning parallel-safe execution levels. The level-0 steps (no dependencies) with 2+ different owners trigger parallel avatar dispatch in Narad's `PLAN-AWARE DISPATCH` routing logic.

Rama emits a `PLAN_JSON:` block at the end of its response for qualifying project plans (3+ steps, 2+ owners, clear horizon). `_make_avatar_tool._run()` in `avatar_agents.py` extracts this block, calls `parse_plan()`, persists to `~/.narad/plans/{session_id}.json`, emits a `plan_created` Yantra event, and strips the block from `result_text` before it reaches the user.

**Data:** `~/.narad/plans/{session_id}.json`

---

### `avatar_agents.py` — The Four Specialists
**Location:** `phase-1/avatar_agents.py`

Four `LlmAgent` instances (Matsya, Rama, Krishna, Parashurama) plus the `_make_avatar_tool` function that wraps each one as a callable FunctionTool with memory enrichment, sutra injection, Sankalpa injection, and post-run scoring built in.

```
Before running (injection order — outermost to innermost):
  1. Sankalpa.get_active_sankalpas(user, avatāra) → per-user style addenda        [outermost]
  2. SutraEngine.get_active_sutras(avatāra, task)  → ranked, sanitized learned patterns
  3. Smriti.recall(task, user_id)                  → relevant prior conversations  [innermost]
  Final enriched_task = [SANKALPA] + [LEARNED PATTERNS] + [MEMORY] + original_task
  All three are prepended to the task before the avatāra sees it.

After running:
  4. Smriti.remember(task, result, avatāra, user_id) → dedup check → embed + store
  5. Tapas.process_session(...)                      → score (DeepSeek R1 judge) → CAI critique → promote (async)
  6. Sankalpa.observe_session(...)                   → style modeling (async)
  7. Yantra.span.finish(result, prompt_tokens, completion_tokens) → write trace JSONL
  8. Yantra.log_event("phase_transition")            → emitted per CURRENT_PHASE: X detected
```

`_http_session_id_ctx` (ContextVar) propagates the HTTP session_id into avatar execution
so all Narad-level and avatar-level trace events land in the same `~/.narad/sessions/{id}.jsonl`.

Token usage is tracked per avatar run and written to `avatar_done` trace events.
Phase transitions are logged to `phase_transition` trace events regardless of ADK session state.

The four canonical agents and their domains (code-verified tool rosters live in **AGENTS.md** — the single source of truth, kept in sync with `FunctionTool` registrations):

| Avatāra | Domain | Roster size |
|---------|--------|-------------|
| Matsya | Web search, browser automation, academic/ML paper search, document extraction, filesystem hygiene, 30-day social listening | 19 tools |
| Rama | Calendar, finance (budgets, goals, imports, spend patterns), health logging, RxNorm | 20 tools |
| Krishna | Email, webpages/decks, video (Veo → moviepy cascade), image generation, documents, shadcn/UI templates | 11 tools |
| Parashurama | Code, shell, SQL, cron, documents, webpages, shadcn/UI | 11 tools |

---

### `server.py` — The API + SSE Server
**Location:** `phase-1/server.py`

A FastAPI server with a single primary endpoint: `POST /chat`. Returns a **Server-Sent Events (SSE)** stream — a one-way HTTP connection that sends JSON events in real time as the system works.

```
avatar_start    → { "avatar": "Parashurama", "task": "..." }
avatar_done     → { "avatar": "Parashurama", "result": {...} }
narad_synthesis → { "text": "Here is the code..." }
done            → { "session_id": "..." }
error           → { "message": "..." }
```

Also exposes: `GET /sutras`, `POST /sutras/{id}/accept`, `POST /sutras/{id}/revert`, `GET /karma`, `GET /sankalpa`, `GET /trace/{session_id}`, `GET /plan/{session_id}` (Rama project plan JSON), `GET /media/` (static files for generated content).

**Phase 10 additions:**
- Structured JSON logging (replaces `basicConfig`) — all log lines are machine-readable JSON.
- `_dharma_gate(query)` — input-level blocking for injection patterns, PII, and crisis phrases; returns immediate SSE error if triggered (no avatar invoked).
- `_check_rate_limit(user_id)` — token bucket per user (10 req/min default, `NARAD_RATE_LIMIT` env); HTTP 429 with `Retry-After: 60` on excess.
- `_http_session_id_ctx.set(session_id)` propagates the HTTP session_id into avatar execution before `run_async()` so trace events from all avatars land in the correct JSONL.

**Key design decision.** The server maintains a global `_user_runners` dict mapping `user_id → Runner`. This is the fix for the amnesiac session problem: previously, every request created a fresh `InMemorySessionService`, destroying conversation history. Now, the same `Runner` (and its session service) is reused for the same user, so Narad receives the full conversation history on every turn.

**Session corruption recovery.** When `runner.run_async()` throws mid-stream (e.g., LLM malformed tool call after context explosion), the session is left with a half-open tool call. The server deletes the broken session on any exception so the next request creates a fresh one. The frontend (`useAvatara.ts`) rotates its stored session ID on every `error` event to stay in sync.

**Data:** Session state lives in `InMemorySessionService` (in-process RAM). Media files served from `phase-7/outputs/`.

---

### `smriti.py` — The Long-Term Memory Layer
**Location:** `phase-2/smriti.py`

**Sanskrit root:** *Smriti* (स्मृति) — "that which is remembered" — the class of Hindu texts preserved through tradition.

A vector database layer that stores every avatāra output and retrieves semantically relevant prior work at the start of each new run.

1. After every avatāra run, `remember()` runs a deduplication check (L2 < 0.10 → skip), then embeds the task text (Gemini `text-embedding-005`, 768 dimensions by default; OpenAI `text-embedding-3-small` fallback via `SMRITI_EMBEDDING_MODEL=openai`) and writes to LanceDB and the FTS5 SQLite index.
2. At the start of every run, `recall()` embeds the current task, runs ANN search, applies the Vismriti decay filter (`max_age_days`, default 90), discards records with L2 distance > 1.3, and prepends survivors as a `[MEMORY]` block.
3. `recall_exact(query, user_id)` provides FTS5 BM25 exact-match search — used by Parashurama (code snippets, error messages).

**Data persisted:** `~/.narad/memory/` (LanceDB), `~/.narad/memory_fts.db` (SQLite FTS5).

---

### `matsya_search.py` — Live Web Search
**Location:** `phase-2/matsya_search.py`

Calls Tavily (external search API optimized for LLM consumption) and returns structured results. Hallucinated sources become impossible: Matsya is instructed to only cite URLs returned by the tool.

---

### `yantra.py` — The Observability Layer
**Location:** `phase-2/yantra.py`

**Sanskrit root:** *Yantra* (यन्त्र) — "instrument" — a device that makes the invisible visible.

A structured logger that writes a JSONL trace file per session. Exposed via `GET /trace/{session_id}`. Uses `_AvatarSpan` context manager for per-avatar latency; `span.finish()` writes timing, token counts, and result digest automatically, even on exception.

**Trace event types:** `session_start`, `avatar_start`, `avatar_done` (+ `result_digest`, `usage`), `phase_transition`, `routing_decision`, `session_done`, `error`.

`_http_session_id_ctx` ContextVar ensures all Narad-level and avatar-level events land in the same file.

**Data persisted:** `~/.narad/sessions/{session_id}.jsonl`

---

### `tapas.py` — The Self-Evolution Layer
**Location:** `phase-3/tapas.py`

**Sanskrit root:** *Tapas* (तपस्) — disciplined austerity that generates transformative power.

After every avatāra run (async, never blocking the user), Tapas scores the response using an avatāra-specific rubric (Parashurama: penalized for skeleton code and fixes without root-cause diagnosis; Matsya: penalized for uncited claims). Deduplicates against existing sutras by batch cosine similarity (threshold 0.92).

Phase 10 updates:
- Promotion threshold raised to **0.80** (from 0.75).
- Judge model: **DeepSeek R1** (`deepseek/deepseek-r1`) — independent of the avatāra being scored. Override via `TAPAS_JUDGE_MODEL` env var.
- **CAI self-critique pass** (Constitutional AI): after score ≥ 0.80, a second LLM call asks three questions (harm to vulnerable users, user autonomy, specificity). Only sessions passing all three are promoted.

Phase 12 additions:
- **`hallucination_free` hard gate**: boolean returned by judge; `false` → score zeroed, promotion blocked unconditionally, `blocked_hallucination` karma event emitted.
- **`sequence_correct` penalty gate**: boolean returned by judge; `false` → −0.20 penalty applied to score (recoverable, not a hard zero).
- `score_session()` now returns a 4-tuple: `(score, reason, hallucination_free, sequence_correct)`.

**Data persisted:** `~/.narad/config/sutras.jsonl`, `~/.narad/config/weak_sessions.jsonl`

---

### `sutra_engine.py` — The Learned Pattern Lifecycle Manager
**Location:** `phase-5/sutra_engine.py`

**Sanskrit root:** *Sutra* (सूत्र) — "thread" — compressed principles meant to be carried and applied.

Manages promoted sutras through: `PENDING → ACTIVE (injected into every matching run) → REVERTED (permanently suppressed)`. Ranking at injection time: `0.6 × tapas_score + 0.4 × keyword_overlap(task, sutra.query)`.

**Phase 10:** `_sanitize_sutra()` strips any sutra containing injection patterns before prompt injection. Blocked sutras are logged to karma with action `blocked_injection`.

**Data:** `~/.narad/config/sutras.jsonl`, `~/.narad/config/sutra_overrides.jsonl`

---

### `karma_log.py` — The Audit Trail
**Location:** `phase-5/karma_log.py`

**Sanskrit root:** *Karma* (कर्म) — action and its record.

Append-only log of every sutra lifecycle mutation. Called from Tapas on promotion, from sutra_engine on accept/revert/block. Read by `GET /karma`.

**Phase 10:** Each entry now includes `triggered_by` (session_id), `tapas_score`, `content_hash` (sha256[:12] of sutra text), `critique_passed` (bool). Every mutation is reversible forever.

**Phase 12:** `hallucination_free` (bool | null) added — records whether the scored session passed the hallucination gate. `blocked_hallucination` added as a new action type alongside `promote`, `accept`, `revert`, `expire`, `blocked_injection`.

**Data persisted:** `~/.narad/config/karma.jsonl`

---

### `sankalpa.py` — Per-User Style Modeling
**Location:** `phase-6/sankalpa.py`

**Sanskrit root:** *Sankalpa* (संकल्प) — intention; a conscious commitment to a way of being.

Tracks recurring patterns in how a specific user works with each avatāra — preferred tone, output format, domain context, style preferences. Distinct from Smriti (what the user asked) — Sankalpa is *how* the user works with the system. Injects per-user style blocks as the outermost context layer before every avatāra run.

**Data persisted:** `~/.narad/config/sankalpas.jsonl`, `~/.narad/config/sankalpa_overrides.jsonl`

---

### `executor.py` — The Sandboxed Code Runner
**Location:** `phase-7/executor.py`

Runs Parashurama-generated Python code in a subprocess with timeout enforcement, a dangerous-import blocklist (`os.system`, `subprocess`, `socket`, `requests`, network libraries, absolute path writes), and an isolated output directory. Generated files land in `phase-7/outputs/<run_id>/`.

Returns `output_files` — a list of paths to generated artifacts (`.mp4`, `.wav`, `.mp3`, `.png`, `.gif`, `.docx`, `.pdf`). The server mounts `outputs/` at `/media/` for static file serving.

---

### `skills/video_skill.py` — Media Generation
**Location:** `phase-7/skills/`

`create_video(code)` — Krishna writes moviepy v2 + Pillow code; the executor runs it; returns a playable URL. This is the fallback step of the 2-step video cascade (`generate_video_clip` Veo first). Error messages are capped at 300 chars with a "one retry only" instruction to prevent context explosion.

`audio_skill` was removed in the M0 cut (2026-07-04) — no first-class audio tool remains.

---

### Phase 8 Skills — All Four Avatāras Fully Tooled

**`local_skill.py` → Matsya**
Five tools: `scan_directory`, `move_to_trash` (dry_run=True default), `organize_by_type` (dry_run=True default), `find_large_files`, `get_disk_info`. Files go to macOS Trash (recoverable), never permanently deleted. Blocked on system paths.

**`shell_skill.py` → Parashurama**
`run_shell(command, working_dir, timeout_s)` — allowlist-based: git, npm, pytest, python, docker, cargo, curl, find, grep. Blocklist: `rm -rf`, `sudo`, pipe-to-shell, system dir writes.

**`sql_skill.py` → Parashurama**
`query_database(connection_string, sql, limit=200)` — SELECT only, SQLAlchemy backend. Supports SQLite, PostgreSQL, MySQL.

**`email_skill.py` → Krishna**
`compose_email` (preview, no network) and `send_email(dry_run=True)` — SMTP via `EMAIL_ADDRESS`/`EMAIL_APP_PASSWORD` env vars. Never sends without explicit user confirmation.

**`calendar_skill.py` → Rama**
`get_upcoming_events` (read-only) and `create_event(dry_run=True)` — CalDAV via `CALDAV_URL`/`CALDAV_USERNAME`/`CALDAV_PASSWORD`. Never creates without user confirmation.

**`docling_skill.py` → Matsya**
`extract_document(file_path)` — pymupdf (PDF) + python-docx (Word) + plain-text read by default; set `NARAD_USE_DOCLING=1` to opt into IBM Docling for richer table/layout extraction.

**`browser_skill.py` → Matsya** (read-only)
`browse_url(url, extract)` — Playwright headless Chromium for JS-rendered SPAs. Text, structured, or links extraction modes.

**`document_skill.py` → Parashurama**
`create_document(code)` — Parashurama writes python-docx code; executor runs it; returns a downloadable `.docx` URL. Used for resumes, reports, letters.

**`browser_act_skill.py` → Matsya** (interactive)
Three functions with a screenshot-first safety model:
- `browser_screenshot(url)` — always safe, read-only, returns detected form fields
- `browser_fill(url, fields, dry_run=True)` — fill form fields; default is preview-only
- `browser_upload_and_submit(url, fields, file_uploads)` — fill + upload + submit; requires explicit user confirmation before calling

---

### Phase 13 — Six Sigma Quality Layer

**`kanban.py` — Karyakrama (step lifecycle)**
`KanbanBoard` is a SQLite-backed step tracker at `~/.narad/kanban.db`. Every `PlanStep` from Rama is upserted on plan creation. `_make_avatar_tool` calls `transition()` as avatāras start and finish: `backlog → in_progress → review → done | blocked`. `GET /kanban/{session_id}` and `GET /kanban` (active sessions). `kanban_update` SSE events update `KanbanBoardView` in the DarshanDashboard Kanban tab.

**`andon.py` — Jaagruti (quality gate)**
`AndonGate.check(result_text, latency_ms, retries_exhausted, tool_error)` is a pure function returning `(should_fire, reason)`. Four trigger classes: `EMPTY_RESULT` (< 80 chars), `TIMEOUT` (> 120 000 ms), `CONNECTION` (retries exhausted), `TOOL_ERROR`. When triggered: `log_andon()` → `andon_alert` SSE → fire-and-forget Parashurama `ANDON_DIAGNOSTIC` → `andon_diagnosis` SSE. `GET /andon/log`, `GET /andon/stats`. Env: `ANDON_MIN_LENGTH`, `ANDON_LATENCY_MS`.

**`narad_5s.py` — Shuddhi (filesystem health)**
`NaradShuddhi` implements Toyota 5S over `~/.narad/`: Sort (identify stale files) → Set-in-Order (write `manifest.json`) → Shine (delete stale, log to `5s_shine_log.jsonl`) → Standardize (write `5s_policy.json`) → Sustain (run full cycle, emit Yantra `shuddhi_run`). `report()` returns `5s_score` (0.0–1.0). `_daily_shuddhi_loop` in `server.py` runs `sustain()` every 24 h. `POST /5s/shine?dry_run=true` · `GET /5s/report`.

**DMAIC (Viveka) — `POST /quality/report`**
Assembles 7-day metrics (andon_stats, andon_log, session count) and passes to Parashurama with a structured DMAIC prompt. Report saved to `~/.narad/wiki/{user_id}/quality/DMAIC_{date}.md`. Cached for `GET /quality/report`.

---

### Phase 14 — Notion Sync Bridge (removed)

Cut in M0 (2026-07-04). The push was one-way (never bidirectional as previously claimed) and failed silently. `notion_sync.py`, the `/notion/*` endpoints, and all fire-and-forget hooks were removed.

---

### The Frontend — Components

**Technology stack:** React 19, Vite, Tailwind v4 (CSS-first), base-ui, TypeScript.

**Design system:** Glass morphism — `backdrop-filter: blur()` + rgba transparency. Three tiers: `.glass-panel` (24px blur), `.glass-card` (12px blur), `.glass-dark` (16px blur). Each avatāra has a `.avatar-glass-{name}` tint class.

**`useAvatara.ts`** — The State Machine
`phase-4/frontend/src/hooks/useAvatara.ts`

Single source of truth for all frontend state. Maintains `messages[]`, `avatars: Record<AvatarName, AvatarStatus>`, `streaming`, `naradActive`, `currentSession`, `stepEvents` (capped at last 200 entries). Stores a stable `convoSessionId` in `sessionStorage` so Narad receives the full conversation history on every turn. Rotates the session ID on `error` events. Exposes `clearSession()` to reset messages, stepEvents, and sessionStorage for a clean slate.

Handles Phase 13 SSE events: `kanban_update` → stores `KanbanUpdatePayload`; `andon_alert` → stores `AndonAlertPayload`; `andon_diagnosis` → appended to alert.

**Shared constants:** `src/lib/avatara-constants.ts` exports `AVATAR_NAMES`, `AVATAR_COLOURS`, `AVATAR_RGB`, `DEVA`, `AVATAR_ABBREV` as the single source of truth used by all components. `src/lib/format-time.ts` exports canonical `relativeTime()` utility.

**`ChatPanel.tsx`** — Conversation Interface
Standard chat UI. User messages use olive-glass. Avatar responses use `glass-card` + avatāra-specific tint. Avatar name chips above each response. "Clear conversation" button resets local state via `clearSession()` without touching immutable server-side session JSONLs.

**`AwarenessBar.tsx`** — Right-side session strip (72 px)
Four canonical agent pills colour-coded by state. Token counter. Active-step count. Button to open DarshanDashboard full-screen drawer.

**`DarshanDashboard.tsx`** — Five-tab full-screen drawer (Phase 13)
| Tab | Content |
|-----|---------|
| Live | DarshanPanel call graph + ParashuramTerminal |
| Kanban | KanbanBoardView — four-column board from last `kanban_update` SSE payload |
| Sutras | SutraPanel with two-step revert confirmation + "Accept all pending" bulk action |
| Memory | ProjectsPanel (Smriti v2 wiki) |
| Ops | OpsView — Andon log, 5S health score, DMAIC report trigger |

**`DarshanPanel.tsx`** — Live Call Graph
An SVG **four-agent constellation** with Narad at the centre. Narad-to-agent lines animate on active state. Node fills change by state: idle → active (pulsing glow) → done (latency badge).

**`SutraPanel.tsx`** — Memory Bank Interface
Sutras grouped by Pending / Active / Reverted. Revert requires two-step confirmation (4-second auto-cancel). "Accept all (N)" bulk button when multiple pending. Polls every 30 seconds.

**`KarmaSheet.tsx`** — Audit Trail Interface
Slide-out panel showing the sutra mutation timeline.

**`ParashuramTerminal.tsx`** — Dev Mode Terminal
macOS traffic-light aesthetic, JetBrains Mono. Shows query as shell command, output as terminal. Compresses DarshanPanel height when visible.

---

## Part V — The Complete Data Flow

### From Keypress to Response: Step by Step

```
USER TYPES A MESSAGE AND PRESSES SEND
        │
        ▼
[1] ChatPanel.tsx handleSend()
    → calls onSend(query) from useAvatara hook

[2] useAvatara.ts send(query)
    → append user message to messages[] state
    → set streaming = true
    → POST /chat with { query, session_id: convoSessionId, user_id }
      session_id is stable for the whole browser session (sessionStorage)

[3] server.py POST /chat
    → runner = _get_runner_for_user(user_id)  [cached — preserves history]
    → ensure session exists
    → begin streaming EventSourceResponse

[4] ADK runner.run_async(user_id, session_id, new_message)
    → loads full conversation history from InMemorySessionService
    → sends [history] + new_message to Narad (DeepSeek)

[5] Narad (DeepSeek, 93% routing accuracy)
    → reads full conversation history
    → applies routing rules from _NARAD_INSTRUCTION
    → decides: invoke_parashurama("Write a FastAPI health check endpoint")

[6] server.py _event_to_sse() detects function_call
    → yields: data: {"type": "avatar_start", "data": {"avatar": "Parashurama", ...}}

[7] useAvatara.ts → avatars.Parashurama.state = "active"

[8] DarshanPanel.tsx re-renders: Parashurama node pulses, Narad line animates

[9] ParashuramTerminal.tsx mounts

[10] avatar_agents.py _run(task)
    → Smriti v2.get_project_context(user_id, task) → [PROJECT CONTEXT] block (if project session)
    → Smriti.recall(task, user_id)             → [MEMORY] block (vector ANN)
    → if Parashurama: recall_exact()           → [EXACT-MATCH MEMORY] block (FTS5 BM25)
    → SutraEngine.get_active_sutras()          → [LEARNED PATTERNS] block
    → Sankalpa.get_active_sankalpas()          → [USER STYLE] block
    enriched_task = [PROJECT CONTEXT] + [LEARNED PATTERNS] + [EXACT-MATCH MEMORY] + [MEMORY] + task

    → Parashurama (DeepSeek flagship) runs on enriched_task
    → calls create_document(python_docx_code) if needed
    → or calls run_shell("pytest ...") if needed

[11] Post-run:
    → Smriti.remember(task, result) → dedup check → LanceDB + FTS5
    → AndonGate.check(result, latency_ms) → if fires: andon_alert SSE + fire-and-forget Parashurama diagnosis
    → KanbanBoard.transition(step) → kanban_update SSE event
    → Tapas.process_session() (async) → DeepSeek R1 judge (2-retry backoff) → CAI critique → sutra → karma
      (if judge unreachable after retries → tapas_skipped karma event, session continues)
    → Sankalpa.observe_session() (async) → style update
    → Yantra span.finish(result, tokens) → error_type on exception → ~/.narad/sessions/{session_id}.jsonl

[12] server.py
    → yields: avatar_done event

[13] useAvatara.ts
    → avatars.Parashurama.state = "done", latencyMs calculated

[14] Narad synthesises result, streams narad_synthesis events

[15] useAvatara.ts receives narad_synthesis → builds assistant message

[16] server.py
    → yields: done event with session_id

[17] useAvatara.ts
    → streaming = false
    → currentSession set with avatarsFired and totalMs
```

---

## Part VI — The Numbers

| Metric | Value | Source |
|--------|-------|--------|
| Routing accuracy (DeepSeek) | 93.0% weighted | phase-0a eval |
| Routing accuracy (GPT-4o) | 84.0% weighted | phase-0a eval |
| Tapas promotion threshold | **0.80** | `TAPAS_PROMOTE_THRESHOLD` env (raised from 0.75 in Phase 10) |
| Tapas judge model | DeepSeek R1 | `TAPAS_JUDGE_MODEL` env (independent of scored avatar) |
| Sutra cooldown before injection | 24 hours | `SUTRA_COOLDOWN_HOURS` env |
| Sutra TTL | 90 days | `TAPAS_SUTRA_TTL_DAYS` env |
| Max sutras injected per avatāra per run | 5 | `SUTRA_MAX_ACTIVE` env |
| Duplicate suppression cosine threshold | 0.92 | `TAPAS_SIM_THRESHOLD` env |
| Smriti recall limit | 3 memories | hardcoded in `recall()` |
| Smriti L2 recall threshold | 1.3 | `_DISTANCE_THRESHOLD` constant |
| Smriti dedup L2 threshold | 0.10 | skip insert if nearest memory < 0.10 |
| Smriti memory decay | 90 days default | `max_age_days` param in `recall()` |
| Rate limit per user | 10 req/min | `NARAD_RATE_LIMIT` env |
| Embedding model | `text-embedding-005` | Gemini default, 768 dimensions; OpenAI `text-embedding-3-small` fallback via `SMRITI_EMBEDDING_MODEL=openai` |
| Executor timeout | 90s | `EXECUTOR_TIMEOUT` env |
| Error message cap in skills | 300 chars | video_skill, document_skill |

---

## Part VII — What Each Sanskrit Name Means Technically

| System Name | Literal Sanskrit Meaning | Technical Function |
|-------------|--------------------------|-------------------|
| **Narad** | The divine messenger-kalakar who holds the Mahati | Router + multi-turn supervisor: reads history, routes to avatāras, synthesizes outputs |
| **Matsya** | Fish (retrieved submerged scriptures from the flood) | Research, retrieval, document extraction, API calls, interactive form filling, filesystem hygiene — absorbed Varaha (documents), Buddha (critical analysis), Vamana (filesystem) in the 4-avatar consolidation |
| **Rama** | The righteous king who accomplished his mission step by step | Structured planning: SOPs, checklists, runbooks, calendar management, finance + health data — absorbed Vamana's finance/health tooling |
| **Krishna** | The diplomat, storyteller, teacher, and communicator of the Mahabharata | All human-facing content end-to-end: email, teaching (multi-phase Socratic), HTML slide decks, video creation, mental health support, health guidance, finance advisory |
| **Parashurama** | The warrior who cleared and rebuilt | Code, shell execution, engineering artifacts, SQL, debugging/root-cause diagnosis (absorbed Narasimha) — no content/media production |
| **Smriti** | "That which is remembered" | Vector-DB long-term memory across sessions |
| **Tapas** | Disciplined austerity that generates transformative power | Self-evolution: scores outputs, promotes high-quality ones to sutras |
| **Sutra** | Thread, aphorism — minimum representation of maximum wisdom | Promoted learned patterns injected into future runs |
| **Karma** | Action and its record | Append-only audit trail of every sutra lifecycle mutation |
| **Yantra** | Instrument that makes invisible forces visible | Observability: JSONL trace per session |
| **Darshan** | The auspicious act of beholding a deity | Live call graph: which avatāras are active right now |
| **Sankalpa** | Intention; a conscious commitment to a way of being | Per-user style and intent modeling |
| **Karyakrama** | Schedule, programme | Kanban step lifecycle tracker — maps Rama plan steps to Kanban board columns |
| **Jaagruti** | Awakening, vigilance | Andon quality gate — fires on empty result, timeout, connection failure, or tool error |
| **Shuddhi** | Purification | 5S filesystem health — Sort/Set-in-Order/Shine/Standardize/Sustain over `~/.narad/` |
| **Viveka** | Discernment | DMAIC quality reporting — Parashurama synthesizes 7-day metrics into Define/Measure/Analyze/Improve/Control |
| **Avatāra** | Descent — a specialized form for a specific purpose | What Silicon Valley calls "agents" — the concept, not the product name |

---

## Part VIII — The Philosophy in One Paragraph

Most multi-agent systems fail because they are built bottom-up: you have an LLM, you add a tool, you add another LLM, you add a router, and eventually you have a pile of components that works but cannot be explained or trusted. Narad was designed top-down: the mythology came first, the technical architecture was derived from it. Narad is the router because that is what Narad does in the tradition — he holds the Mahati, and he decides which string to pluck. Each live agent has a load-bearing cultural identity that prevents role drift. The system can be explained in one sentence per component to anyone, technical or not, because the names carry the meaning. The Frankenstein rule enforces this: every component earns its place by fitting the metaphor, not just the requirement. The result is a system where the assembled parts have coherence — where the joints are load-bearing, not patches. The sutras accumulate, the karma is recorded, the darshan makes the invisible visible. The system is not trying to pretend it is a single intelligent entity. It is a disciplined quartet: four specialists, one messenger, one memory, one conscience. The Mahati has four canonical strings in the shipped build, and Narad plays them all.

---

---

## Part IX — Locked Decisions

| Decision | Locked value |
|---|---|
| Product name | **Narad** — the sage, the kalakar, the router |
| Agent concept | **Avatāra** — the Sanskrit word for what Silicon Valley calls "agents" |
| Logo | **Mahati veena** — one string per avatāra, bindu for Narad |
| Routing model default | **DeepSeek V3** (93% routing accuracy) |
| Memory | **Local LanceDB + project wiki** — on user's disk, never transmitted |
| Self-evolution | **Karma-log-gated** — auto-applied with 24h cooldown, always reversible |
| OSS license | **Apache-2.0** — fully featured, no artificial gates |

---

*Document version: M0 truth-reconciliation pass, 2026-07-04*
*Architecture current as of: 4-avatar consolidation + M0 cut (Notion sync, webwright, ml-intern, hyperframes, audio, remotion removed; docling now opt-in via `NARAD_USE_DOCLING=1`) — Phase 13 Six Sigma layer (Kanban/Andon/5S/DMAIC + DarshanDashboard overhaul); defensive `shell_skill` import fallback; FTS5 `recall_exact()` routed to Parashurama; Smriti v2 `get_project_context()` wired into pipeline; embedding `@lru_cache(128)` on `_embed()`; Tapas 2-retry exponential backoff + `tapas_skipped` karma event; `error_type` classification on all Yantra error events; shared frontend constants (`avatara-constants.ts`, `format-time.ts`); clear chat, sutra confirmation, bulk accept, bounded `stepEvents` (200), native artifact side panel. All data canonical at `~/.narad/`. See AGENTS.md for the code-verified tool rosters and AUDIT-AND-ROADMAP.md for the forward plan.*
