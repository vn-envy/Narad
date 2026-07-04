# Narad — Agent Skills & Tools Reference

> **Source of truth.** This document defines every agent's identity, tools, and routing
> rules — verified against `phase-1/avatar_agents.py` and `phase-1/narad_agent.py`.
> Last verified: 2026-07-04 (M0 truth-reconciliation pass).
> Roadmap and open work: see `AUDIT-AND-ROADMAP.md`.

---

## System Overview

```
User
 └── Narad (supervisor / router — DeepSeek V4 Flash)
       ├── Matsya       — retrieval, documents, critical analysis, local filesystem   (Flash)
       ├── Rama         — planning, calendar, personal finance, health data           (Pro)
       ├── Krishna      — communication, media creation, education, wellness          (Flash)
       └── Parashurama  — code, systems, automation, quantitative modeling            (Pro)
```

Four avatars, not eight: the earlier Varaha / Narasimha / Buddha / Vamana roles were
consolidated. Documents, analysis, and filesystem went to Matsya; personal finance and
health logging to Rama; symptom triage and mental-health support to Krishna; debugging
to Parashurama. `runtime_contract.py` records this as `stale_agents_removed`.

All avatars are `LlmAgent` instances wrapped in `FunctionTool` via `_make_avatar_tool()`.
Narad calls them as function tools and synthesises their outputs into one response.
Each invocation enriches the task with Smriti memories, active Sutras, and per-user
Sankalpas before running the inner agent. Models are assigned in
`phase-1/model_config.py` and overridable per avatar via env vars
(`MATSYA_MODEL`, `RAMA_MODEL`, `KRISHNA_MODEL`, `PARASHURAMA_MODEL`, `NARAD_MODEL`).

---

## Routing Rules (Narad)

### One-line decision table

| User intent | Route to |
|---|---|
| Live lookup, current data, URL scrape, REST API, web form | Matsya |
| Medical literature, drug info, clinical research, nutrition data | Matsya |
| File/doc analysis: PDF, DOCX, PPTX, HTML, CSV, transcript | Matsya |
| Deep research, literature review, SOTA, academic sources | Matsya |
| Critical analysis, tradeoff, red-team, "should I do X" | Matsya |
| Local filesystem: clean up, organise, disk analysis | Matsya |
| Structured plan, SOP, checklist, runbook, calendar event | Rama |
| Personal finance: import CSV, spending, budgets, goals, net worth | Rama |
| Log symptoms, medication reminders, query health history | Rama |
| Email, announcement, LinkedIn post, client memo | Krishna |
| Explain, teach, quiz, flashcards, study plan, curriculum | Krishna |
| Slide deck, presentation, pitch deck | Krishna (direct — never Parashurama) |
| Video creation, explainer video, animation | Krishna (direct — never Parashurama) |
| Health guidance, symptom triage, mental-health support | Krishna |
| Any code/engineering task, scripting, automation, databases | Parashurama |
| Bug, error, crash, wrong output, performance, slow query | Parashurama |
| Quantitative modeling: DCF, portfolio, statistics via code | Parashurama |

### Hard routing rules

- **Presentations and videos are owned end-to-end by Krishna** — brief, narrative,
  AND build. Krishna calls `create_webpage` (slides) or the video tools directly.
  Never route slide/video requests to Parashurama.
- **Video recovery cascade:** if Krishna returns no video URL, route back to Krishna
  with: (1) `generate_video_clip()` (Veo) first, (2) if Veo unavailable or errors:
  `create_video()` (moviepy). Never to another avatar.
- **Debugging is Parashurama.** It has `read_file` to inspect actual code and logs.
- **Health maps by function** — no dedicated health agent:
  education → Krishna · emotional support / PHQ-4 → Krishna · symptom triage →
  Krishna · wellness plans → Krishna · symptom/medication logging → Rama ·
  medical research + `query_rxnorm` → Matsya (research) / Rama (personal log).
- **Numbered step outputs go to Rama, not Krishna.** Krishna is for prose.
- **Never pre-solve for Parashurama.** Describe only the user's goal and task type;
  its phase-gated skills pick tools. Pre-specifying tool names or file formats
  bypasses skill enforcement.
- **Mental health crisis:** PHQ-4 score ≥ 12 → mandatory crisis resources
  (iCall: 9152987821). Never route mental health to Parashurama or Matsya.

### Parallel routing patterns

```
"GTM plan + launch email"            → invoke_rama + invoke_krishna       (parallel)
"Research X then write a blog post"  → invoke_matsya, then invoke_krishna (sequential)
"Help me save ₹50k by October"       → invoke_rama (financial context + savings plan)
"Should I take this lower-salary job?" → invoke_rama (finances) + invoke_matsya (tradeoff)
"Presentation on X"                  → invoke_krishna (BRIEF → OUTLINE → STRUCTURE → BUILD)
"What does my blood report say?"     → invoke_matsya (extract_document, objective extraction)
```

Hard cap: 3 avatars per turn. Default to 1.

---

## Matsya — Retrieval, Documents, Analysis, Local Access

Retrieval and synthesis specialist: general web, academic literature, REST APIs,
document extraction, critical analysis (steelman + red-team), and the local filesystem.

| Tool | Purpose |
|---|---|
| `web_search` | Tinyfish search (primary) with Tavily fallback |
| `browse_url` | Playwright headless browser for JS SPAs and specific URLs |
| `http_request` | Direct REST API / webhook calls |
| `browser_screenshot` / `browser_fill` / `browser_upload_and_submit` | Form workflow: screenshot → dry-run fill → explicit user confirmation → submit |
| `search_arxiv` / `search_papers` / `search_hf_papers` / `search_hf_models` | Academic + model discovery |
| `query_deepwiki` | GitHub repo architecture questions |
| `extract_document` | PDF/DOCX/PPTX/HTML/CSV/text extraction (pymupdf + python-docx by default; Docling opt-in via `NARAD_USE_DOCLING=1`) |
| `scan_directory` / `organize_by_type` / `move_to_trash` / `find_large_files` / `get_disk_info` | Filesystem hygiene — always dry-run before mutating |
| `narad_shuddhi` | 5S filesystem health report |
| `search_last30days` | Cross-source recency sweep (Reddit/HN/GitHub) |

Soft rules: primary sources over aggregators; cite every non-obvious claim; screenshot
before any form fill; never submit without per-form user confirmation; quote source
location for document findings; extract health documents objectively — never diagnose.

---

## Rama — Planning, Calendar, Personal Data

Structured-plan specialist and owner of the personal data lifecycle (finance + health).

| Tool | Purpose |
|---|---|
| `get_upcoming_events` / `create_event` | CalDAV calendar |
| `get_spending` / `get_budget_status` / `get_financial_context` / `get_recurring_expenses` / `get_goals` / `get_net_worth` / `get_spend_patterns` | Personal finance reads |
| `import_csv` / `sync_gmail_finance` / `set_budget` / `add_goal` / `update_goal_progress` / `add_balance_snapshot` / `categorize_transaction` | Personal finance writes |
| `log_symptom` / `set_medication_reminder` / `get_health_log` | Health log |
| `query_rxnorm` | Drug information (RxNorm REST, no auth) |

Plans emit `PLAN_JSON:` blocks that persist and drive the Kanban board.

---

## Krishna — Communication, Creation, Wellness

Prose, education, media creation, and health guidance (education, PHQ-4 mental-health
screen, physical symptom triage with emergency red-flag halt).

| Tool | Purpose |
|---|---|
| `compose_email` / `compose_rich_email` / `send_email` | Email — send is Dharma-gated and preview-first |
| `create_webpage` | HTML slide decks and pages → `/media/…/index.html` |
| `generate_video_clip` | Veo AI video (needs `GEMINI_API_KEY`) |
| `create_video` | Programmatic video via moviepy v2 (fallback + stitching) |
| `generate_image` | Image generation |
| `create_document` | .docx generation |
| `rank_ui_templates` / `list_shadcn_components` / `fetch_shadcn_component` | Design references for decks/pages |

Video cascade: Veo first, moviepy fallback. Never describe a video without rendering it.

---

## Parashurama — Code, Systems, Quantitative Modeling

Engineering only: write/refactor/review/migrate code, debugging (owns all
broken-behavior reports), automation, read-only SQL, quantitative modeling via code —
never in-context arithmetic.

| Tool | Purpose |
|---|---|
| `read_file` | Inspect code and logs during diagnosis |
| `write_script` / `run_shell` | Write and execute scripts (sandboxed executor, Dharma-gated) |
| `query_database` | Read-only SQL against local engineering databases |
| `schedule_cron` / `list_cron_jobs` / `remove_cron_job` | Recurring task automation |
| `create_webpage` / `create_document` | Engineering dashboards, technical .docx |
| `list_shadcn_components` / `fetch_shadcn_component` | React/shadcn UI building |

NOT for content creation (→ Krishna), personal data (→ Rama), or live web (→ Matsya).

---

## Shared Infrastructure

### Smriti (Memory)
Every invocation is enriched with relevant memories before running; results are stored
after. Scoped per `user_id`. Storage: `~/.narad/memory/` (LanceDB) +
`~/.narad/memory_fts.db` (SQLite FTS5, `recall_exact` for code/error phrases).
Vismriti decay (`max_age_days`, per-avatar TTLs), L2 < 0.10 dedup, probabilistic size
guard, embedding LRU cache, Smriti v2 project context as outermost layer.

### Sutras (Learned Patterns) + Tapas (Self-Evolution)
Tapas scores each session with an avatar-specific rubric (DeepSeek R1 judge,
`TAPAS_JUDGE_MODEL` override; 2 retries with backoff, `tapas_skipped` on failure).
Promotion threshold 0.80 + CAI self-critique pass + `hallucination_free` hard gate +
`sequence_correct` −0.20 penalty. Active sutras are sanitized (`_sanitize_sutra`)
before injection; injection signals are blocked and logged to Karma.
Storage: `~/.narad/config/sutras.jsonl`.

### Karma (Audit Ledger)
Append-only log of every sutra lifecycle mutation AND every Dharma-gated side-effect
verdict (allowed and denied). Storage: `~/.narad/config/karma.jsonl`.

### Sankalpa (Per-User Style)
Style preferences injected as the outermost context layer; evolves per session.
Storage: `~/.narad/config/sankalpas.jsonl`.

### Yantra (Observability)
Tracer span around every invocation; live step events on the SSE stream; traces at
`GET /trace/{session_id}` and `~/.narad/sessions/{session_id}.jsonl`. Error events
carry `error_type`: `tool_not_found` | `import_failed` | `timeout` | `model_error` |
`json_parse` | `event_loop`.

### Dharma (Policy Gates)
Two layers. Input: `_dharma_gate(query)` in `server.py` blocks prompt injection, PII
collection, and crisis phrases (with resources) before any avatar runs. Side effects:
`dharma.gate_action()` gates `executor`, `email_send`, `browser_submit` — unknown
actions are denied by default; every verdict lands in Karma. Policy file:
`~/.narad/config/dharma_policy.json`.

### Six Sigma Layer
**AndonGate** (`andon.py`): fires on `EMPTY_RESULT` (<80 chars), `TIMEOUT` (>120s),
`CONNECTION`, `TOOL_ERROR`; logs to `~/.narad/config/andon_log.jsonl` + SSE alert.
**KanbanBoard** (`kanban.py`): `PlanStep` lifecycle `backlog → in_progress → review →
done | blocked` in `~/.narad/kanban.db`, streamed as `kanban_update` SSE.
**5S/DMAIC** (`narad_5s.py` + server): daily hygiene loop, `POST /quality/report`.

### Security Floor
`NARAD_AUTH` modes local/strict/off; bearer token at `~/.narad/config/api_token`
(chmod 600); localhost pass-through; CORS via `NARAD_ALLOWED_ORIGINS`; token-bucket
rate limiting per user (10 req/min default, `NARAD_RATE_LIMIT` override, HTTP 429).

### Vision & Visual-Output Routing
Image attachments → best available vision model (MiMo > OpenAI > Anthropic, per
`model_config.py`; `VISION_MODEL` / per-avatar overrides). Visual OUTPUT tasks
(decks, UI, pages) stay on DeepSeek V4 Pro — no cross-provider swap mid-turn.
Video keywords override deck keywords and stay on the Veo/moviepy path.

### Session Persistence
Avatar sessions cached per `{user_id}:{narad_session_id}:{agent_name}:{model_id}` so
multi-phase skills keep state across turns. Phase state tracked per
`{narad_session_id}:{agent_name}`, evicted at session end. Background tasks are
tracked in a server-side registry decoupled from the SSE connection.

### Format Rules
All avatars: no emojis, no decorative symbols, prose over bullets, minimal bold,
sparse headers, full markdown tables, code blocks always for code.

---

## Content Pipelines

```
SLIDES: User → Narad → Krishna [BRIEF → OUTLINE → STRUCTURE, confirmed]
        → rank_ui_templates() → create_webpage() → /media/…/index.html

VIDEO:  User → Narad → Krishna [BRIEF → SCRIPT, confirmed]
        → generate_video_clip() (Veo) → create_video() stitch/fallback
        → /media/…/video.mp4

APPS:   User → Narad → Parashurama [CLASSIFY → BUILD → DELIVER]
        → /media/…/index.html

SYMPTOM TRIAGE: User → Narad → Krishna [red-flag check → structured assessment
        → severity-tiered guidance]. Emergency signs → halt + emergency message only.

HEALTH DOCUMENTS: file path → Matsya [extract_document] — objective extraction,
        out-of-range flagging, no clinical interpretation.
```

---

## Status

Shipped: 4-avatar runtime, SSE server with auth/rate-limit/CORS floor, Smriti/Sutra/
Tapas/Karma/Sankalpa/Yantra loops, Andon + Kanban + 5S, Dharma action gates, tool
result envelopes, context governor, session harness contract.

Removed in the M0 cut (2026-07-04): Notion sync, webwright, ml-intern, hyperframes,
audio, remotion skills, beautiful-html-templates submodule, phase-0a/0b spikes
(archived on branch `archive/spikes`).

Current roadmap, known gaps, and milestone sequencing: `AUDIT-AND-ROADMAP.md`.
