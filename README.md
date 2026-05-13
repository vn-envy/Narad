# Narad

> *"We didn't invent multi-agent AI. We remembered it."*

Eight avatāras. One sage who plays them. Local-first, private, yours forever.

---

## What this is

**Narad** is the product — the orchestrator. An AI system modelled on the kalakar Narad Muni, who holds the Mahati veena and decides which string to pluck for every task.

**Avatāra** (अवतार) is the concept — what Silicon Valley now calls "agents." A form that descends with purpose, completes its mission, and releases. The Bhagavad Gita described this API specification three thousand years ago.

The Mahati has eight strings. Each string is an avatāra. Narad summons them.

---

## The eight avatāras

| Avatāra | Sanskrit | Domain |
|---|---|---|
| Matsya | मत्स्य | Web search, live retrieval, API calls, form interaction |
| Varaha | वराह | Deep document extraction (PDF, DOCX, PPTX) |
| Narasimha | नरसिंह | Debugging, root-cause diagnosis, system failures |
| Rama | राम | Structured planning, SOPs, calendar management |
| Krishna | कृष्ण | Communication drafting, email composition and sending |
| Buddha | बुद्ध | Critical analysis, red-teaming, tradeoff evaluation |
| Parashurama | परशुराम | Code, shell execution, media generation, document output |
| Vamana | वामन | Local filesystem — clean, organise, disk analysis |

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
phase-1/        FastAPI SSE server · Narad router · 8 avatar agents
  server.py         POST /chat SSE stream, /trace, /sutras, /karma, /sankalpa
  narad_agent.py    Narad supervisor (DeepSeek V3)
  avatar_agents.py  8 LlmAgent specialists + _make_avatar_tool wrapper
  model_config.py   Per-avatar model assignments

phase-2/        Memory · Search · Observability
  smriti.py         LanceDB vector memory (recall/remember)
  matsya_search.py  Tavily web search
  yantra.py         JSONL session tracer
  browser_skill.py  Playwright read-only browser
  http_skill.py     REST API / webhook client

phase-3/        Self-evolution
  tapas.py          Quality scoring + sutra promotion

phase-4/        Frontend
  frontend/src/
    hooks/useAvatara.ts       SSE state machine
    components/ChatPanel.tsx  Conversation interface
    components/DarshanPanel.tsx  Live octagonal call graph
    components/SutraPanel.tsx    Learned patterns UI
    components/KarmaSheet.tsx    Audit trail sheet
    components/ParashuramTerminal.tsx  Dev terminal overlay

phase-5/        Sutra engine · Karma log
  sutra_engine.py   Sutra lifecycle (pending → active → reverted)
  karma_log.py      Append-only mutation audit trail

phase-6/        Sankalpa engine
  sankalpa.py       Per-user style and intent modeling

phase-7/        Code executor · Media generation
  executor.py       Sandboxed Python subprocess runner
  skills/
    video_skill.py  create_video() — moviepy + Pillow
    audio_skill.py  create_audio() — numpy + scipy

phase-8/        Tier 1 skills (all avatāras fully tooled)
  local_skill.py    scan_directory, move_to_trash, organize_by_type (Vamana)
  shell_skill.py    run_shell — sandboxed shell commands (Parashurama)
  sql_skill.py      query_database — read-only SQL (Parashurama)
  email_skill.py    compose_email, send_email via SMTP (Krishna)
  calendar_skill.py get/create CalDAV events (Rama)
  docling_skill.py  extract_document — PDF/DOCX/PPTX (Varaha)
  browser_skill.py  browse_url — JS-rendered pages (Matsya)
  document_skill.py create_document() — DOCX via python-docx (Parashurama)
  browser_act_skill.py  browser_screenshot, browser_fill, browser_upload_and_submit (Matsya)

phase-0a/       Spike: routing accuracy on local 4B model
phase-0b/       Spike: ADK + SSE PoC
```

---

## Quick start (backend)

```bash
# From phase-1/
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# Required env vars (minimum)
export DEEPSEEK_API_KEY=sk-...
export TAVILY_API_KEY=tvly-...
export OPENAI_API_KEY=sk-...       # for Smriti embeddings

# Optional (unlock Krishna email + Rama calendar)
export EMAIL_ADDRESS=you@gmail.com
export EMAIL_APP_PASSWORD=xxxx
export CALDAV_URL=https://...

uvicorn server:app --port 8000 --reload
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
| 1 | Live LLM agents — Narad + 8 avatāras on DeepSeek, ADK runner | ✅ Done |
| 2 | Memory + Search + Observability — Smriti, Matsya search, Yantra | ✅ Done |
| 3 | Tapas — quality scoring, sutra promotion, avatar rubrics | ✅ Done |
| 4 | Frontend — React SSE UI, DarshanPanel call graph | ✅ Done |
| 5 | Sutra engine + Karma log — learned pattern lifecycle | ✅ Done |
| 6 | Sankalpa engine — per-user style modeling | ✅ Done |
| 7 | Code executor + media generation — video, audio via Parashurama | ✅ Done |
| 8 | Tier 1 skills — all 8 avatāras fully tooled | ✅ Done |
| 9 | Resume tailoring + job application — DOCX output + interactive browser | ✅ Done |
| 10 | Electron desktop packaging — local Gemma 4 E4B, signed installer | 🔨 Next |

---

## License

Apache 2.0. The OSS edition is fully featured — the moat is the compounding Tapas relationship, not artificial feature gates.
