# Narad

> *"We didn't invent multi-agent AI. We remembered it."*

Four canonical agents. One sage who plays them. Cloud now, local later, yours forever.

---

## What this is

**Narad** is the product — the orchestrator. An AI system modelled on the kalakar Narad Muni, who holds the Mahati veena and decides which string to pluck for every task.

**Avatāra** (अवतार) is the concept — what Silicon Valley now calls "agents." A form that descends with purpose, completes its mission, and releases. The Bhagavad Gita described this API specification three thousand years ago.

The Mahati now has four canonical strings in the shipped build. Narad summons the right one for the work at hand.

---

## The four canonical agents

| Avatāra | Sanskrit | Domain |
|---|---|---|
| Matsya | मत्स्य | Web search, document understanding, research synthesis, local information access |
| Rama | राम | Structured planning, calendar management, finance workflows, health logging |
| Krishna | कृष्ण | Communication drafting, email, education, media generation, wellness guidance |
| Parashurama | परशुराम | Code, shell execution, SQL, automation, document output |

**Narad** routes. **Smriti** remembers. **Tapas** learns. **Sankalpa** adapts. **Yantra** observes. **Karma** records. **Sutras** compound.

---

## Architecture in one breath

```
User → Narad (supervisor) → 1–3 avatāras (specialists)
             ↑                        ↓
        Smriti (recall)        Tapas (score → sutra)
        Sutras (inject)        Yantra (trace)
        Sankalpa (style)       Karma (audit)
```

Full design documents: [Narad-Plan/](../Narad-Plan/)  
Full technical architecture: [ARCHITECTURE.md](./ARCHITECTURE.md)

---

## Repository layout

```
phase-1/        FastAPI SSE server · Narad router · canonical 4-agent build · Six Sigma · Notion sync
  server.py             POST /chat SSE stream + all endpoints (kanban, andon, 5s, quality, notion)
  narad_agent.py        Narad supervisor (DeepSeek V3)
  avatar_agents.py      4 LlmAgent specialists + _make_avatar_tool (Smriti/Sutra/Sankalpa/AndonGate/Kanban)
  model_config.py       Per-avatar model assignments (LiteLLM strings)
  plan_models.py        PlanStep/Plan dataclasses + levels() topological sort
  kanban.py             KanbanBoard — SQLite step lifecycle tracker (Phase 13)
  andon.py              AndonGate quality gate + diagnostic fire-and-forget (Phase 13)
  narad_5s.py           NaradShuddhi 5S filesystem health (Phase 13)
  notion_sync.py        NotionSync bidirectional push to 5 Notion databases (Phase 14)

phase-2/        Memory · Search · Observability
  smriti.py             LanceDB vector memory + FTS5; @lru_cache on _embed(); fallback logging
  matsya_search.py      Tavily web search
  yantra.py             JSONL session tracer; error_type classification
  yantra_models.py      Trajectory/TurnRecord/ToolCall typed dataclasses

phase-3/        Self-evolution
  tapas.py              Quality scoring + sutra promotion; 2-retry backoff; tapas_skipped event

phase-4/        Frontend
  frontend/src/
    hooks/useAvatara.ts            SSE state machine; clearSession(); bounded stepEvents
    lib/avatara-constants.ts       Single source of truth — AVATAR_COLOURS, AVATAR_RGB, DEVA
    lib/format-time.ts             Canonical relativeTime() utility
    components/ChatPanel.tsx       Conversation interface + clear button
    components/AwarenessBar.tsx    72 px right strip — pills + token counter (Phase 13)
    components/DarshanDashboard.tsx 5-tab full-screen drawer (Phase 13)
    components/DarshanPanel.tsx    Live four-agent call graph
    components/SutraPanel.tsx      Learned patterns — revert confirmation + bulk accept
    components/KarmaSheet.tsx      Audit trail sheet
    components/KanbanBoardView.tsx 4-column Kanban board (Phase 13)
    components/OpsView.tsx         Andon + 5S + DMAIC ops panel (Phase 13)
    components/ParashuramTerminal.tsx Dev terminal overlay

phase-5/        Sutra engine · Karma log
  sutra_engine.py       Sutra lifecycle (pending → active → reverted); injection sanitization
  karma_log.py          Append-only mutation audit trail

phase-6/        Sankalpa engine
  sankalpa.py           Per-user style and intent modeling

phase-7/        Code executor · Media generation
  executor.py           Sandboxed Python subprocess runner
  skills/
    video_skill.py      create_video() — moviepy + Pillow
    audio_skill.py      create_audio() — numpy + scipy

phase-8/        Tier 1 skills (all core domains fully tooled)
  local_skill.py        scan_directory, move_to_trash, organize_by_type (Matsya/Parashurama support)
  shell_skill.py        run_shell — sandboxed shell commands (Parashurama)
  sql_skill.py          query_database — read-only SQL (Parashurama)
  email_skill.py        compose_email, send_email via SMTP (Krishna)
  calendar_skill.py     get/create CalDAV events (Rama)
  docling_skill.py      extract_document — PDF/DOCX/PPTX (Matsya discipline)
  browser_skill.py      browse_url — JS-rendered pages (Matsya)
  document_skill.py     create_document() — DOCX via python-docx (Parashurama)
  browser_act_skill.py  browser_screenshot, browser_fill, browser_upload_and_submit (Matsya)
  finance_skill.py      import_csv, sync_gmail, budgets, goals, get_spend_patterns (Rama discipline)
  health_skill.py       log_symptom, set_medication_reminder, get_health_log (Rama/Krishna discipline)

phase-9/        Project system · Smriti v2
  scribe.py             Post-session wiki compiler
  smriti_v2.py          Project-scoped Markdown wiki + get_project_context()

narad_config.py         Canonical path constants (NARAD_HOME, TRACE_DIR, ARTIFACTS_DIR, CONFIG_DIR)
phase-0a/               Spike: routing accuracy on local 4B model
phase-0b/               Spike: ADK + SSE PoC
```

---

## Quick start (backend)

```bash
# From repo root
python3.12 -m venv .venv
.venv/bin/pip install -r phase-1/requirements.txt \
                      -r phase-2/requirements.txt \
                      -r phase-3/requirements.txt

# Required env vars (minimum)
export GEMINI_API_KEY=...          # Smriti embeddings + Veo video
export DEEPSEEK_API_KEY=sk-...     # router + avatāras + Tapas judge

# Optional (unlock full feature set)
export TAVILY_API_KEY=tvly-...     # Matsya web search
export OPENAI_API_KEY=sk-...       # Smriti fallback embeddings
export NOTION_API_TOKEN=secret_... # Phase 14 Notion sync
export EMAIL_ADDRESS=you@gmail.com
export EMAIL_APP_PASSWORD=xxxx
export CALDAV_URL=https://...

cd phase-1 && ../.venv/bin/python3.12 -m uvicorn server:app --host 0.0.0.0 --port 8000
```

## Quick start (frontend)

```bash
cd phase-4/frontend
npm install
npm run dev        # → http://localhost:5173
```

---

## Build phases

| Phase | Name | Status |
|---|---|---|
| 0a | Model evaluation spike — routing accuracy on local 4B model | ✅ Done |
| 0b | ADK + SSE PoC — architecture validated | ✅ Done |
| 1 | Live LLM agents — Narad + canonical 4-agent build on DeepSeek, ADK runner | ✅ Done |
| 2 | Memory + Search + Observability — Smriti, Matsya search, Yantra | ✅ Done |
| 3 | Tapas — quality scoring, sutra promotion, avatar rubrics | ✅ Done |
| 4 | Frontend — React SSE UI, DarshanPanel call graph | ✅ Done |
| 5 | Sutra engine + Karma log — learned pattern lifecycle | ✅ Done |
| 6 | Sankalpa engine — per-user style modeling | ✅ Done |
| 7 | Code executor + media generation — video, audio via Parashurama | ✅ Done |
| 8 | Tier 1 skills — all 4 canonical agents fully tooled | ✅ Done |
| 9 | Resume tailoring + job application — DOCX output + interactive browser | ✅ Done |
| 10 | Observability v2 + memory refinements + Dharma guardrails | ✅ Done |
| 11 | Project detection, Scribe wiki compiler, left-panel UX | ✅ Done |
| 12 | AssetOpsBench integration, typed traces, Markov spend patterns, health anomaly detection | ✅ Done |
| 13 | Six Sigma quality layer — Kanban, Andon, 5S, DMAIC + Darshan Dashboard overhaul | ✅ Done |
| 14 | Notion sync bridge — bidirectional push of memories, kanban, andon, sutras, sankalpas | ✅ Done |
| 15 | Electron desktop packaging — local Gemma 4 E4B, offline-first, signed installer | 🔨 Next |

---

## License

Apache 2.0. The OSS edition is fully featured — the moat is the compounding Tapas relationship, not artificial feature gates.
