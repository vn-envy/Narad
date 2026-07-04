# Narad: Agent Workflow Design — User Story Atlas

> **⚠️ Drift note (2026-07-04):** Parts of this atlas predate the 4-avatar consolidation and may still name **Varaha, Narasimha, Vamana, or Buddha**. Read those as their canonical successors: Varaha → Matsya (documents), Narasimha → Parashurama (debugging) / Krishna (symptom talk), Buddha → Matsya (analysis), Vamana → Matsya (filesystem) / Rama (finance + health). **AGENTS.md is the code-verified reference** for routing and tool rosters; where this file disagrees, AGENTS.md wins.

## Purpose

Step-by-step workflow design for every major Narad use case, from user intention to final execution. Covers:
- **Single-agent** — one avatāra handles end-to-end
- **Multi-agent linear (sequential)** — avatāra A feeds output to avatāra B
- **Multi-agent parallel (simultaneous)** — 2–3 avatāras run in the same turn

Each row maps: user intent → Narad routing → execution logic → tools → skills → I/O files → LLMs & APIs.

**Source:** `phase-1/narad_agent.py` (routing), `phase-1/avatar_agents.py` (execution), `phase-1/server.py` (SSE pipeline), `phase-8/` (skill implementations).

---

## Legend

| Column | Meaning |
|--------|---------|
| **User Intent** | What the user says / goal they have |
| **Narad Routing Logic** | How Narad supervisor decides which avatāra(s) to call and why |
| **Agent Execution Logic** | What the selected avatāra(s) do step-by-step internally |
| **Tools Used** | FunctionTool names registered to the avatāra |
| **Skills Used** | Underlying Python skill functions called |
| **Input Files** | Files read from disk / network |
| **Output Files** | Files written to disk / delivered to user |
| **LLMs & API Calls** | External model or API endpoints hit |

---

## 1. Simple Single-Agent Calls

### 1.1 Live Web Research

| Column | Detail |
|--------|--------|
| **User Intent** | "What are the latest benchmarks for LLaMA 4 vs Mistral 3?" |
| **Narad Routing Logic** | Live external lookup → `invoke_matsya`. Single-step, no code or planning required. No parallel avatāras needed. |
| **Agent Execution Logic** | 1. Smriti v1 semantic recall: inject any prior related memory. 2. Sutra injection: any learned search patterns. 3. Matsya calls `_web_search(query)` → gets top 5 results (title, URL, snippet). 4. For each result with a JS-rendered page: optionally calls `_browse_url(url)` to fetch full text. 5. Synthesises findings into a structured comparison. 6. Tapas scores. Yantra logs `avatar_done`. |
| **Tools Used** | `_web_search`, `_browse_url` |
| **Skills Used** | `matsya_search.web_search()`, `browser_skill.browse_url()` |
| **Input Files** | None (live web) |
| **Output Files** | None (inline text response) |
| **LLMs & APIs** | DeepSeek V4 Flash (Matsya), Tinyfish/Tavily (search API), Playwright Chromium (JS pages) |

---

### 1.2 Academic Paper Search

| Column | Detail |
|--------|--------|
| **User Intent** | "Find recent papers on RL from human feedback published after 2024" |
| **Narad Routing Logic** | Academic retrieval → `invoke_matsya`. Triggers: arXiv, Semantic Scholar, Hugging Face keywords. |
| **Agent Execution Logic** | 1. Matsya calls `_search_arxiv(query, date_from="2024-01-01")` → returns paper list (title, abstract, authors, PDF URL). 2. Optionally calls `_search_papers()` (Semantic Scholar) for citation counts. 3. Optionally `_search_hf_papers()` for ML-specific papers. 4. Formats into a ranked list with metadata. |
| **Tools Used** | `_search_arxiv`, `_search_papers`, `_search_hf_papers` |
| **Skills Used** | (inline FunctionTools in avatar_agents.py — ADK arXiv/SemanticScholar/HF API wrappers) |
| **Input Files** | None |
| **Output Files** | None (inline table) |
| **LLMs & APIs** | DeepSeek V4 Flash (Matsya), arXiv API, Semantic Scholar API, Hugging Face Papers API |

---

### 1.3 Document Extraction

| Column | Detail |
|--------|--------|
| **User Intent** | "Extract the key findings from this PDF research report" (file attached or path provided) |
| **Narad Routing Logic** | PDF/DOCX extraction → `invoke_varaha`. Triggered by file attachment or "extract from", "read this PDF", "what does this document say". |
| **Agent Execution Logic** | 1. Smriti recall for any prior context on this document. 2. Varaha calls `_extract_document(file_path)` → returns Markdown with tables preserved, page breaks linearised. 3. Varaha reads the full extracted Markdown and synthesises key findings. 4. If financial model: may write a Python script (`_write_script`) and run it (`_run_shell`) for numerical calculations. |
| **Tools Used** | `_extract_document`, `_write_script`, `_run_shell` |
| **Skills Used** | `docling_skill.extract_document()` |
| **Input Files** | `.pdf`, `.docx`, `.pptx`, `.html`, `.md` (local path or uploaded) |
| **Output Files** | None (inline summary); optionally `.txt` transcript |
| **LLMs & APIs** | DeepSeek V4 Flash (Varaha), Docling (IBM open-source), PyMuPDF (fallback) |

---

### 1.4 Code Writing

| Column | Detail |
|--------|--------|
| **User Intent** | "Write a Python function that parses JSONL files and computes session latency statistics" |
| **Narad Routing Logic** | Code/scripting → `invoke_parashurama`. Routing rule: never pre-solve; pass the user's goal verbatim. Do NOT mention tool names or file formats in the task. |
| **Agent Execution Logic** | 1. Smriti v1 recall + FTS5 exact-match (`recall_exact`) for any prior code related to this task. 2. Smriti v2 project context if session is project-scoped. 3. Active sutras with past code quality patterns injected. 4. Parashurama writes the function, optionally writes to disk via `_write_script(content, path)`. 5. If executable: runs via `_run_shell(command)` for validation. 6. Returns code in response. |
| **Tools Used** | `_write_script`, `_run_shell`, `_read_file` |
| **Skills Used** | `shell_skill.write_script()`, `shell_skill.run_shell()`, `shell_skill.read_file()` |
| **Input Files** | Optionally: existing code files read via `_read_file` |
| **Output Files** | Script file written to user-specified path (e.g. `parse_sessions.py`) |
| **LLMs & APIs** | DeepSeek V4 Pro (Parashurama) |

---

### 1.5 SQL Query

| Column | Detail |
|--------|--------|
| **User Intent** | "Query the orders database and show me total revenue by month for 2025" |
| **Narad Routing Logic** | Database query → `invoke_parashurama`. Trigger: "query the database", "SQL", "run a query against". Read-only enforced. |
| **Agent Execution Logic** | 1. Parashurama calls `_query_database(connection_string, sql, limit=200)` with a SELECT statement. 2. Validates SQL is SELECT-only (rejects DDL/DML). 3. Returns tabulated results as Markdown. 4. If complex: writes a Python analysis script and runs it to produce charts. |
| **Tools Used** | `_query_database`, `_write_script`, `_run_shell` |
| **Skills Used** | `sql_skill.query_database()` |
| **Input Files** | SQLite/PostgreSQL/MySQL database (connection string provided) |
| **Output Files** | None (inline table); optionally chart image |
| **LLMs & APIs** | DeepSeek V4 Pro (Parashurama), SQLAlchemy |

---

### 1.6 Email Drafting (No Send)

| Column | Detail |
|--------|--------|
| **User Intent** | "Draft a cold email to a VC introducing our startup" |
| **Narad Routing Logic** | Email/prose composition → `invoke_krishna`. Trigger: "cold email", "write an email", "draft a message". No sending requested. |
| **Agent Execution Logic** | 1. Sankalpa style injection (user's preferred tone/format). 2. Krishna calls `_compose_email(to, subject, body)` → returns formatted preview. 3. Returns full draft with subject + body. |
| **Tools Used** | `_compose_email` |
| **Skills Used** | `email_skill.compose_email()` |
| **Input Files** | None |
| **Output Files** | None (inline email draft) |
| **LLMs & APIs** | DeepSeek V4 Flash (Krishna) |

---

### 1.7 Email Drafting + Send (Two-Phase Confirmation)

| Column | Detail |
|--------|--------|
| **User Intent** | "Send a meeting follow-up email to priya@example.com" |
| **Narad Routing Logic** | Email send → `invoke_krishna`. Routing rule: `send_email` always defaults to `dry_run=True` first; no email is sent without explicit user confirmation. |
| **Agent Execution Logic** | **Phase 1 (draft):** Krishna calls `_compose_email()` + `_send_email(dry_run=True)` → returns preview (to/subject/body), no SMTP connection. Krishna emits `CURRENT_PHASE: awaiting_confirmation`. **Phase 2 (on user "yes, send it"):** Narad detects SKILL_CONTINUATION (≤25 words, same topic). Calls Krishna again with `[CONTINUING SKILL]` prefix. Krishna calls `_send_email(dry_run=False)` → opens SMTP TLS, authenticates, delivers. Emits `DONE`. |
| **Tools Used** | `_compose_email`, `_send_email` |
| **Skills Used** | `email_skill.compose_email()`, `email_skill.send_email()` |
| **Input Files** | None |
| **Output Files** | None (email delivered via SMTP) |
| **LLMs & APIs** | DeepSeek V4 Flash (Krishna), Gmail SMTP (smtp.gmail.com:587, TLS) |

---

### 1.8 Filesystem Cleanup

| Column | Detail |
|--------|--------|
| **User Intent** | "Clean up my Downloads folder — organize files by type" |
| **Narad Routing Logic** | Local filesystem → `invoke_vamana`. Trigger: "clean up", "organize", "sort my files". Routing rule: always dry_run first; no deletion without confirmation. |
| **Agent Execution Logic** | **Phase 1 (scan):** Vamana calls `_scan_directory("~/Downloads")` → returns file tree with sizes/ages. `_find_large_files("~/Downloads")` for big items. `_get_disk_info()` for before-state. **Phase 2 (dry-run):** `_organize_by_type(source_dir, dry_run=True)` → shows what would move where. Emits `CURRENT_PHASE: awaiting_confirmation`. **Phase 3 (execute on confirm):** `_organize_by_type(source_dir, dry_run=False)` → physically moves files into type-named subfolders. |
| **Tools Used** | `_scan_directory`, `_find_large_files`, `_get_disk_info`, `_organize_by_type`, `_move_to_trash` |
| **Skills Used** | `local_skill.scan_directory()`, `local_skill.find_large_files()`, `local_skill.get_disk_info()`, `local_skill.organize_by_type()`, `local_skill.move_to_trash()` |
| **Input Files** | Local filesystem (~/Downloads or user-specified path) |
| **Output Files** | Reorganised directory tree; files moved into type subfolders |
| **LLMs & APIs** | DeepSeek V4 Flash (Vamana), send2trash (Python) |

---

### 1.9 Personal Finance Check

| Column | Detail |
|--------|--------|
| **User Intent** | "How much did I spend on food last month?" |
| **Narad Routing Logic** | Personal finance query → `invoke_vamana`. Trigger: "spend", "budget", "how much did I", personal bank/CSV context. |
| **Agent Execution Logic** | 1. Vamana calls `_get_financial_context()` → single-call summary (monthly estimate, top categories, goals, accounts). 2. Calls `_get_spending(period="last_month", category="Food")` for targeted breakdown. 3. Calls `_get_budget_status()` if budget exists to compare vs limit. 4. Synthesises answer with spend breakdown and budget delta. |
| **Tools Used** | `_get_financial_context`, `_get_spending`, `_get_budget_status` |
| **Skills Used** | `finance_skill.get_financial_context()`, `finance_skill.get_spending()`, `finance_skill.get_budget_status()` |
| **Input Files** | `~/.narad/finance.db` (SQLite) |
| **Output Files** | None (inline spend summary) |
| **LLMs & APIs** | DeepSeek V4 Flash (Vamana) |

---

### 1.10 Bank Statement Import

| Column | Detail |
|--------|--------|
| **User Intent** | "Import my HDFC bank statement from this CSV file" |
| **Narad Routing Logic** | Personal finance ingestion → `invoke_vamana`. Trigger: CSV + bank name, "import my statement". |
| **Agent Execution Logic** | 1. Vamana calls `_import_csv(file_path, bank="HDFC")` → auto-detects HDFC column layout. 2. Parses debit/credit amounts, dates, merchant descriptions. 3. Auto-categorises transactions (20+ keyword-mapped categories). 4. Writes to `finance.db`. 5. Returns import summary: N transactions, date range, total inflow/outflow, top categories. |
| **Tools Used** | `_import_csv` |
| **Skills Used** | `finance_skill.import_csv()` |
| **Input Files** | `.csv` bank statement (HDFC, ICICI, Axis, SBI format) |
| **Output Files** | `~/.narad/finance.db` (updated SQLite) |
| **LLMs & APIs** | DeepSeek V4 Flash (Vamana) |

---

### 1.11 Health Symptom Logging

| Column | Detail |
|--------|--------|
| **User Intent** | "I have a headache and mild fever, severity 4 out of 10" |
| **Narad Routing Logic** | Health symptom → `invoke_narasimha` (routing rule: physical symptoms ALWAYS go to Narasimha's `symptom_check` protocol — NEVER Krishna, Vamana, or Matsya). Emergency red flags (stroke, chest pain, loss of consciousness) → immediate emergency instructions before logging. |
| **Agent Execution Logic** | **5-phase symptom_check:** Phase 1 (collect): Narasimha calls `_log_symptom("headache", 4)` + `_log_symptom("fever", 4)` → written to `health.db`. Phase 2 (red_flag_check): checks against red flag patterns. Phase 3 (assessment): retrieves prior history via `_get_health_log(days=7, anomaly_detection=True)` to detect unusual patterns. Phase 4 (triage): provides recommendations. Phase 5 (disclaimer): emits safety disclaimer. |
| **Tools Used** | `_run_shell`, `_read_file`, `_write_script` (plus health tools via avatar_agents routing) |
| **Skills Used** | `health_skill.log_symptom()`, `health_skill.get_health_log()` |
| **Input Files** | `~/.narad/health.db` (symptom history) |
| **Output Files** | `~/.narad/health.db` (updated with new symptoms) |
| **LLMs & APIs** | DeepSeek V4 Pro (Narasimha), health_anomaly.py (local anomaly detection) |

---

### 1.12 Medication Lookup

| Column | Detail |
|--------|--------|
| **User Intent** | "What drug class does metformin belong to?" |
| **Narad Routing Logic** | Health + drug info → `invoke_vamana` (routine drug info lookup; not a symptom). If symptom context accompanies: `invoke_narasimha`. |
| **Agent Execution Logic** | 1. Vamana calls `_query_rxnorm("metformin")` → hits RxNorm REST API: drug name → RxCUI → drug properties → drug classes. 2. Returns drug class, synonyms, related drugs. |
| **Tools Used** | (health tools) |
| **Skills Used** | `health_skill.query_rxnorm()` |
| **Input Files** | None |
| **Output Files** | None (inline drug info) |
| **LLMs & APIs** | DeepSeek V4 Flash (Vamana), RxNorm REST API (rxnav.nlm.nih.gov — free, no auth) |

---

### 1.13 Decision Analysis (Tradeoff)

| Column | Detail |
|--------|--------|
| **User Intent** | "Should I use PostgreSQL or MongoDB for my new app?" |
| **Narad Routing Logic** | Architectural tradeoff → `invoke_buddha`. Triggers: "SQL vs NoSQL", "pros and cons", "should I use X or Y", "which is better". Buddha explicitly owns architecture reviews. |
| **Agent Execution Logic** | 1. Buddha calls `_get_financial_context()` if the decision has cost implications (optional). 2. Synthesises a structured tradeoff analysis: use case fit, performance characteristics, operational complexity, ecosystem, migration cost. 3. Returns recommendation with explicit assumption audit. |
| **Tools Used** | `_get_financial_context`, `_get_spending`, `_get_net_worth`, `_get_recurring_expenses` |
| **Skills Used** | `finance_skill.get_financial_context()` (optional, for cost context) |
| **Input Files** | `~/.narad/finance.db` (optional, if cost analysis needed) |
| **Output Files** | None (inline analysis) |
| **LLMs & APIs** | DeepSeek V4 Pro (Buddha) |

---

### 1.14 Task Planning / SOP

| Column | Detail |
|--------|--------|
| **User Intent** | "Create a step-by-step SOP for onboarding a new engineer to our team" |
| **Narad Routing Logic** | Structured sequential output → `invoke_rama`. Triggers: "step-by-step", "SOP", "checklist", "runbook", "how do I approach". |
| **Agent Execution Logic** | 1. Rama calls `_get_financial_context()` if any budget/resources mentioned. 2. Produces numbered SOP with sections: Pre-arrival → Day 1 → Week 1 → 30/60/90 days. 3. Each step has Owner and Time fields. 4. If 3+ steps + 2+ distinct owners + clear time horizon: emits `PLAN_JSON:` block. 5. Kanban populates from PLAN_JSON. Kanban `kanban_update` SSE events fire as steps complete. |
| **Tools Used** | `_get_upcoming_events`, `_create_event`, `_get_spending`, `_get_budget_status`, `_get_financial_context`, `_get_recurring_expenses`, `_get_goals` |
| **Skills Used** | `calendar_skill.get_upcoming_events()` (if scheduling involved) |
| **Input Files** | `~/.narad/finance.db` (if budget), CalDAV (if scheduling) |
| **Output Files** | `~/.narad/plans/{session_id}.json` (if PLAN_JSON emitted), `~/.narad/kanban.db` (if plan steps populated) |
| **LLMs & APIs** | DeepSeek V4 Pro (Rama), CalDAV (if calendar-linked) |

---

### 1.15 Video Creation (Full Pipeline — Phase-Gated)

| Column | Detail |
|--------|--------|
| **User Intent** | "Create a short explainer video about neural networks" |
| **Narad Routing Logic** | Video creation → `invoke_krishna`. Routing rule: Krishna owns the FULL video pipeline (brief → outline → HTML build). If Krishna returns no video URL: route BACK to Krishna, never to Parashurama. |
| **Agent Execution Logic** | **Phase 1 (brief):** Krishna synthesises the concept into a script/storyboard outline. Emits `CURRENT_PHASE: video_build`. **Phase 2 (build) — 2-step cascade:** (1) `_generate_video_clip(prompt)` — Gemini Veo AI video. (2) If Veo unavailable/errors: `_create_video(code)` — Python code in executor sandbox (moviepy + Pillow + matplotlib). Sandbox writes `.mp4` to `ARTIFACTS_DIR`. Krishna returns the `/media/…/video.mp4` URL. Frame images optionally via `_generate_image()`. |
| **Tools Used** | `_generate_video_clip`, `_create_video`, `_generate_image` |
| **Skills Used** | `veo_skill.generate_video_clip()`, `video_skill.create_video()` |
| **Input Files** | None (generative) |
| **Output Files** | `~/.narad/artifacts/{run_id}/output.mp4` |
| **LLMs & APIs** | DeepSeek V4 Flash (Krishna), moviepy + Pillow + matplotlib (executor sandbox), Gemini Veo 3.1 (if `generate_video_clip` called), Gemini Imagen (if `generate_image` called) |

---

### 1.16 Presentation Creation (Full Pipeline — Phase-Gated)

| Column | Detail |
|--------|--------|
| **User Intent** | "Create a pitch deck for my SaaS startup" |
| **Narad Routing Logic** | Presentation → `invoke_krishna`. Triggers: "slide deck", "pitch deck", "presentation". Visual output detected → model switches to Gemini 3 Flash automatically. Krishna owns the full pipeline (brief → outline → HTML build). Never Parashurama. |
| **Agent Execution Logic** | **Phase 1 (brief):** Krishna generates slide outline (title, 6–8 slide titles, key messages per slide). Emits `CURRENT_PHASE: slide_build`. **Phase 2 (build):** Krishna calls `_create_webpage(html_code)` → produces a self-contained HTML slideshow. Returns URL to served file. Alternatively calls `_create_document(code)` for PPTX-style DOCX output. |
| **Tools Used** | `_create_webpage`, `_create_document`, `_generate_image`, `_list_shadcn_components`, `_fetch_shadcn_component`, `_rank_ui_templates` |
| **Skills Used** | `document_skill.create_document()` |
| **Input Files** | None (generative) |
| **Output Files** | `~/.narad/artifacts/{run_id}/slides.html` or `.docx` |
| **LLMs & APIs** | **Gemini 3 Flash** (visual output route, auto-detected), Gemini Imagen (if images requested) |

---

### 1.17 Music / Audio Generation

**Removed in the M0 cut (2026-07-04).** `audio_skill` / `_create_audio` no longer exist; audio generation is not a supported workflow. Parashurama can still write audio-producing scripts via `_write_script` + `_run_shell` on explicit user request, but there is no first-class tool.

---

### 1.18 React UI Component Generation

| Column | Detail |
|--------|--------|
| **User Intent** | "Create a shadcn data table component for showing invoice history" |
| **Narad Routing Logic** | React UI → `invoke_parashurama`. Trigger: "React component", "shadcn", "UI". Parashurama auto-detects TASK_TYPE from goal; Narad never mentions specific tools in the task. |
| **Agent Execution Logic** | 1. Parashurama calls `_list_shadcn_components()` → gets component registry. 2. `_rank_ui_templates(task)` → ranks available templates by relevance. 3. `_fetch_shadcn_component("data-table")` → fetches component definition. 4. Writes custom component code via `_write_script`. 5. Returns full TSX code. |
| **Tools Used** | `_list_shadcn_components`, `_fetch_shadcn_component`, `_rank_ui_templates`, `_write_script` |
| **Skills Used** | `shell_skill.write_script()` |
| **Input Files** | None |
| **Output Files** | Component `.tsx` file |
| **LLMs & APIs** | DeepSeek V4 Pro (Parashurama), shadcn component registry API |

---

### 1.19 UI / Mockup / Dashboard Design

| Column | Detail |
|--------|--------|
| **User Intent** | "Design a dashboard UI for my analytics app" or "Create a landing page mockup" |
| **Narad Routing Logic** | Visual design output → `invoke_krishna` or `invoke_parashurama`. Visual output keywords detected ("dashboard design", "landing page", "mockup", "wireframe") → model switches to **Gemini 3 Flash** automatically in avatar_agents.py routing. |
| **Agent Execution Logic** | 1. Avatar calls `_create_webpage(html_code)` with a fully styled, self-contained HTML page. 2. For React: `_list_shadcn_components()` + `_fetch_shadcn_component()` for relevant components. 3. Returns URL to served HTML artifact. |
| **Tools Used** | `_create_webpage`, `_list_shadcn_components`, `_fetch_shadcn_component` |
| **Skills Used** | `document_skill.create_document()` |
| **Input Files** | None (generative) |
| **Output Files** | `~/.narad/artifacts/{run_id}/design.html` |
| **LLMs & APIs** | **Gemini 3 Flash** (visual output route, auto-detected) |

---

### 1.20 Calendar — View + Create Event

| Column | Detail |
|--------|--------|
| **User Intent** | "Schedule a team retrospective for next Friday at 3pm" |
| **Narad Routing Logic** | Calendar management → `invoke_rama`. Trigger: "schedule", "calendar", "add event", "what's on my calendar". |
| **Agent Execution Logic** | **Phase 1 (view):** Rama calls `_get_upcoming_events(days_ahead=14)` → returns next 2 weeks of events. **Phase 2 (dry-run create):** `_create_event(title, start, end, dry_run=True)` → shows preview without creating. **Phase 3 (confirm):** On user "yes" → `_create_event(dry_run=False)` → sends to CalDAV server. Returns confirmation. |
| **Tools Used** | `_get_upcoming_events`, `_create_event` |
| **Skills Used** | `calendar_skill.get_upcoming_events()`, `calendar_skill.create_event()` |
| **Input Files** | CalDAV server (remote) |
| **Output Files** | CalDAV server updated (event created) |
| **LLMs & APIs** | DeepSeek V4 Pro (Rama), CalDAV server (iCloud, Google, Nextcloud) |

---

### 1.21 Code Debugging / Root Cause

| Column | Detail |
|--------|--------|
| **User Intent** | "This Python script is throwing a KeyError — help me debug it" (paste stack trace) |
| **Narad Routing Logic** | Debugging → `invoke_narasimha`. Triggers: "debug", "exception", "error", "why is this failing", stack traces. |
| **Agent Execution Logic** | 1. Smriti FTS5 exact-match recall: searches prior sessions for similar errors. 2. Narasimha calls `_read_file(path)` to read the file. 3. `_run_shell(command)` to reproduce the error. 4. `_write_script(fix)` to apply the fix. 5. `_run_shell` again to verify fix works. 6. Returns root cause explanation + fixed code. |
| **Tools Used** | `_read_file`, `_run_shell`, `_write_script` |
| **Skills Used** | `shell_skill.read_file()`, `shell_skill.run_shell()`, `shell_skill.write_script()` |
| **Input Files** | Source file (path provided or pasted) |
| **Output Files** | Fixed source file (written in-place or new path) |
| **LLMs & APIs** | DeepSeek V4 Pro (Narasimha) |

---

### 1.22 Image-Attached Analysis (Multimodal)

| Column | Detail |
|--------|--------|
| **User Intent** | "What's wrong with this architecture diagram?" (image attached) |
| **Narad Routing Logic** | Images attached → any avatāra. `avatar_agents.py` detects `_images_ctx` is non-empty → routes to **MiMo v2.5** multimodal model regardless of which avatāra is selected. |
| **Agent Execution Logic** | 1. Avatar detects image context. 2. Model automatically switched to MiMo v2.5 (`openai/mimo-v2.5`, base: `https://token-plan-sgp.xiaomimimo.com/v1`). 3. Images sent as base64 in the LLM call alongside the task. 4. Avatar reasons about visual content and responds. |
| **Tools Used** | (any tools appropriate for the avatāra) |
| **Skills Used** | (any skills appropriate for the avatāra) |
| **Input Files** | Attached image(s) (PNG, JPG, etc.) |
| **Output Files** | None (inline analysis) |
| **LLMs & APIs** | **MiMo v2.5** (multimodal input route, auto-detected when images present) |

---

## 2. Multi-Agent Linear (Sequential) Calls

Sequential = avatāra A runs to completion, its output is passed as context to avatāra B.

---

### 2.1 Research → Blog Post

| Column | Detail |
|--------|--------|
| **User Intent** | "Research the current state of quantum computing and write a blog post about it" |
| **Narad Routing Logic** | Research THEN writing → `invoke_matsya` FIRST (gather sources), then `invoke_krishna` with Matsya's findings. Routing rule: for "research + write" patterns, sequential ordering is mandatory. |
| **Agent Execution Logic** | **Turn 1 — Matsya:** Calls `_web_search(query)` × 3–5 queries. `_browse_url(url)` on top sources for full text. Synthesises research summary. `avatar_done` SSE fires. **Turn 1 — Krishna (same turn, after Matsya):** Narad passes Matsya's complete output as context. Krishna composes a long-form blog post: title, intro, 4–5 sections, conclusion, with sources cited. |
| **Tools Used** | Matsya: `_web_search`, `_browse_url`. Krishna: `_create_webpage` (optional, for formatted output) |
| **Skills Used** | `matsya_search.web_search()`, `browser_skill.browse_url()` |
| **Input Files** | None (live web) |
| **Output Files** | Inline blog post text; optionally `~/.narad/artifacts/{run_id}/post.html` |
| **LLMs & APIs** | DeepSeek V4 Flash (Matsya) → DeepSeek V4 Flash (Krishna), Tinyfish/Tavily, Playwright |

---

### 2.2 Research → Analysis

| Column | Detail |
|--------|--------|
| **User Intent** | "Is now a good time to expand into the Southeast Asian market? Deep research please" |
| **Narad Routing Logic** | "Deep research" trigger → `invoke_matsya` FIRST to gather sources, then `invoke_buddha` with Matsya's findings for critical analysis. Explicit routing rule in narad_agent.py. |
| **Agent Execution Logic** | **Turn 1 — Matsya:** Searches for SEA market size, competitors, regulatory climate, recent M&A. Browses 5–7 sources. Returns structured research brief. **Turn 1 — Buddha (same turn):** Receives Matsya's research. Calls `_get_financial_context()` for user's current financial position (if relevant). Produces structured analysis: opportunity size, entry barriers, risks, timing, recommendation. |
| **Tools Used** | Matsya: `_web_search`, `_browse_url`. Buddha: `_get_financial_context`, `_get_net_worth` |
| **Skills Used** | `matsya_search.web_search()`, `browser_skill.browse_url()`, `finance_skill.get_financial_context()` |
| **Input Files** | `~/.narad/finance.db` (optional) |
| **Output Files** | None (inline analysis) |
| **LLMs & APIs** | DeepSeek V4 Flash (Matsya) → DeepSeek V4 Pro (Buddha), Tinyfish/Tavily |

---

### 2.3 Document Extraction → Planning

| Column | Detail |
|--------|--------|
| **User Intent** | "Read this project brief PDF and create a project plan from it" |
| **Narad Routing Logic** | Doc extraction THEN planning → `invoke_varaha` FIRST (read PDF), then `invoke_rama` with extracted content. |
| **Agent Execution Logic** | **Turn 1 — Varaha:** `_extract_document(file_path)` → full Markdown extraction of PDF. Returns structured brief. **Turn 1 — Rama (same turn):** Receives Varaha's extraction as context. Builds project plan: milestones, tasks, owners, timeline. Emits `PLAN_JSON:` → Kanban populated. Calendar events created if dates mentioned. |
| **Tools Used** | Varaha: `_extract_document`. Rama: `_get_upcoming_events`, `_create_event`, `_get_financial_context` |
| **Skills Used** | `docling_skill.extract_document()`, `calendar_skill.get_upcoming_events()` |
| **Input Files** | `.pdf` (user-provided path) |
| **Output Files** | `~/.narad/plans/{session_id}.json`, `~/.narad/kanban.db` |
| **LLMs & APIs** | DeepSeek V4 Flash (Varaha) → DeepSeek V4 Pro (Rama), Docling, CalDAV |

---

### 2.4 Research → Code Implementation

| Column | Detail |
|--------|--------|
| **User Intent** | "Find the latest LangGraph documentation and implement a simple graph agent based on it" |
| **Narad Routing Logic** | Research THEN code → `invoke_matsya` (find docs), then `invoke_parashurama` (implement from docs). |
| **Agent Execution Logic** | **Turn 1 — Matsya:** `_browse_url(langchain docs URL)` to read LangGraph API reference. `_search_hf_papers()` or `_query_deepwiki()` for examples. Returns API summary. **Turn 1 — Parashurama (same turn):** Receives API documentation. FTS5 recall for any prior LangGraph code. Writes implementation → `_write_script(content, path)`. Runs → `_run_shell()` to validate. Returns complete working code. |
| **Tools Used** | Matsya: `_browse_url`, `_web_search`, `_query_deepwiki`. Parashurama: `_write_script`, `_run_shell` |
| **Skills Used** | `browser_skill.browse_url()`, `shell_skill.write_script()`, `shell_skill.run_shell()` |
| **Input Files** | None (live docs) |
| **Output Files** | `agent.py` (or user-specified script path) |
| **LLMs & APIs** | DeepSeek V4 Flash (Matsya) → DeepSeek V4 Pro (Parashurama), Playwright |

---

### 2.5 Financial Import → Budget Setup

| Column | Detail |
|--------|--------|
| **User Intent** | "Import my ICICI statement and then set up a budget based on my spending" |
| **Narad Routing Logic** | Import (Vamana) THEN planning (Rama). Sequential: budgeting requires data to exist first. |
| **Agent Execution Logic** | **Turn 1 — Vamana:** `_import_csv(file_path, bank="ICICI")` → parses statement, writes `finance.db`. Returns category breakdown. **Turn 1 — Rama (same turn):** `_get_spending()` + `_get_budget_status()` on fresh data. Proposes budget limits per category based on 3-month spending averages. Calls `_set_budget()` via Vamana tool if user confirms. |
| **Tools Used** | Vamana: `_import_csv`, `_get_spending`, `_get_financial_context`, `_set_budget`. Rama: `_get_spending`, `_get_budget_status`, `_get_financial_context` |
| **Skills Used** | `finance_skill.import_csv()`, `finance_skill.get_spending()`, `finance_skill.set_budget()` |
| **Input Files** | `.csv` bank statement |
| **Output Files** | `~/.narad/finance.db` (transactions + budget records) |
| **LLMs & APIs** | DeepSeek V4 Flash (Vamana) → DeepSeek V4 Pro (Rama) |

---

### 2.6 Teaching Session (Multi-Phase Skill Continuation)

| Column | Detail |
|--------|--------|
| **User Intent** | "Teach me how transformers work" |
| **Narad Routing Logic** | Education → `invoke_krishna` in GURU MODE. Triggers: "teach me", "explain", "help me understand". Phase-gated skill: each user message continues to next phase without re-routing. |
| **Agent Execution Logic** | **Phase: frame** — Krishna assesses prior knowledge (Smriti recall for any past explanations). Sets learning objective. Emits `CURRENT_PHASE: explain`. **Phase: explain** — Provides conceptual explanation with analogies. Emits `CURRENT_PHASE: examples`. **Phase: examples** — Concrete code or diagram examples. Emits `CURRENT_PHASE: check`. **Phase: check** — Poses comprehension questions. Emits `CURRENT_PHASE: reinforce`. **Phase: reinforce** — Addresses gaps, provides summary + flashcards. Emits `DONE`. Each user message (≤25 words, same topic) → Narad detects SKILL_CONTINUATION → calls Krishna again with `[CONTINUING SKILL]` prefix and full prior output. |
| **Tools Used** | `_create_webpage` (for interactive flashcards), `_compose_email` (to email summary) |
| **Skills Used** | None (LLM-driven) |
| **Input Files** | Smriti memory (any prior explanations recalled) |
| **Output Files** | Optionally `flashcards.html` |
| **LLMs & APIs** | DeepSeek V4 Flash (Krishna across all phases) |

---

## 3. Multi-Agent Parallel (Simultaneous) Calls

Parallel = 2–3 avatāras invoked in the same turn, running concurrently. Hard cap: 3 avatāras. Narad synthesises all outputs into one reply.

---

### 3.1 GTM Strategy Package

| Column | Detail |
|--------|--------|
| **User Intent** | "I'm launching a product next month. I need a go-to-market plan, a launch announcement email, and a risk assessment" |
| **Narad Routing Logic** | Three distinct domains, no dependencies between them → `invoke_rama` + `invoke_krishna` + `invoke_buddha` simultaneously. Narad formulates three independent tasks and calls all three in same turn. |
| **Agent Execution Logic** | **Rama (parallel):** Reads `_get_financial_context()` for budget constraints. Produces GTM plan: channels, timeline, milestones, budget allocation. Emits `PLAN_JSON:` → Kanban populated. **Krishna (parallel):** Drafts launch announcement email: subject, pre-header, body, CTA. Optionally drafts LinkedIn post. **Buddha (parallel):** Audits assumptions in the GTM plan. Produces risk matrix: market timing risk, competitor response risk, resource risk. Recommends mitigations. **Narad synthesis:** Combines all three into one cohesive response: plan + comms + risk, cross-referenced. |
| **Tools Used** | Rama: calendar + finance tools. Krishna: `_compose_email`. Buddha: finance context tools |
| **Skills Used** | `finance_skill.get_financial_context()`, `email_skill.compose_email()` |
| **Input Files** | `~/.narad/finance.db` |
| **Output Files** | `~/.narad/plans/{session_id}.json`, `~/.narad/kanban.db` |
| **LLMs & APIs** | DeepSeek V4 Pro (Rama) + DeepSeek V4 Flash (Krishna) + DeepSeek V4 Pro (Buddha) — concurrent |

---

### 3.2 Savings Goal Setup

| Column | Detail |
|--------|--------|
| **User Intent** | "Help me save ₹50,000 by October" |
| **Narad Routing Logic** | Personal finance + savings planning → `invoke_vamana` + `invoke_rama` simultaneously. Vamana reads current state; Rama builds the savings roadmap. |
| **Agent Execution Logic** | **Vamana (parallel):** `_get_financial_context()` → current income estimate, recurring expenses, existing goals. `_get_net_worth()` for baseline. Returns current savings capacity. **Rama (parallel):** Receives Vamana's context (Narad includes it in Rama's task). Calculates monthly savings required (₹50k / months remaining). Identifies top spending categories to cut. Emits month-by-month savings milestone plan. Calls `_add_goal("₹50k by October", target=50000, target_date="2026-10-31")` via Vamana tool. **Narad synthesis:** "You need to save ₹8,333/month. Vamana found you currently spend ₹3,200/month on dining — Rama suggests reducing to ₹1,500 as first lever..." |
| **Tools Used** | Vamana: `_get_financial_context`, `_get_net_worth`, `_get_recurring_expenses`, `_add_goal`. Rama: finance reads |
| **Skills Used** | `finance_skill.get_financial_context()`, `finance_skill.get_net_worth()`, `finance_skill.add_goal()` |
| **Input Files** | `~/.narad/finance.db` |
| **Output Files** | `~/.narad/finance.db` (new goal record), `~/.narad/plans/{session_id}.json` |
| **LLMs & APIs** | DeepSeek V4 Flash (Vamana) + DeepSeek V4 Pro (Rama) — concurrent |

---

### 3.3 Career Decision Support

| Column | Detail |
|--------|--------|
| **User Intent** | "I got a job offer with a 40% pay raise but requires relocating to Bangalore. Should I take it?" |
| **Narad Routing Logic** | Financial context (Vamana) + tradeoff analysis (Buddha) → parallel. Neither depends on the other; Narad includes financial context in Buddha's task. |
| **Agent Execution Logic** | **Vamana (parallel):** `_get_financial_context()` → current income estimate, recurring expenses, goals. `_get_net_worth()`. Returns current financial state. **Buddha (parallel):** Receives Vamana's data. Produces multi-dimension analysis: financial impact (net gain after Bangalore COL), career trajectory, personal factors (family, lifestyle), relocation cost, opportunity cost of staying. Explicit recommendation with confidence level. **Narad synthesis:** Weaves financial data into the tradeoff narrative. |
| **Tools Used** | Vamana: `_get_financial_context`, `_get_net_worth`. Buddha: `_get_financial_context`, `_get_spending` |
| **Skills Used** | `finance_skill.get_financial_context()`, `finance_skill.get_net_worth()` |
| **Input Files** | `~/.narad/finance.db` |
| **Output Files** | None (inline recommendation) |
| **LLMs & APIs** | DeepSeek V4 Flash (Vamana) + DeepSeek V4 Pro (Buddha) — concurrent |

---

### 3.4 Competitive Intelligence Sprint

| Column | Detail |
|--------|--------|
| **User Intent** | "Research our top 3 competitors' pricing, their product positioning, and tell me if their recent funding rounds change our strategy" |
| **Narad Routing Logic** | Multi-track research → `invoke_matsya` (pricing + funding news) + `invoke_buddha` (strategy impact). Parallel since Buddha can pre-structure the framework while Matsya gathers data. |
| **Agent Execution Logic** | **Matsya (parallel):** `_web_search()` × 3 competitor names for pricing pages. `_browse_url()` on each pricing page. `_web_search()` for recent funding rounds. Synthesises: pricing tiers, feature gates, recent raises. **Buddha (parallel):** Receives Matsya's findings embedded by Narad in task. Applies strategic framework: pricing power implications, positioning white space, funding runway estimates, strategic implications for our roadmap. |
| **Tools Used** | Matsya: `_web_search`, `_browse_url`. Buddha: `_get_financial_context` |
| **Skills Used** | `matsya_search.web_search()`, `browser_skill.browse_url()` |
| **Input Files** | None (live web) |
| **Output Files** | None (inline intelligence report) |
| **LLMs & APIs** | DeepSeek V4 Flash (Matsya) + DeepSeek V4 Pro (Buddha) — concurrent, Tinyfish/Tavily, Playwright |

---

### 3.5 Full-Stack Feature Delivery

| Column | Detail |
|--------|--------|
| **User Intent** | "Build a REST endpoint for user authentication + write tests + update the API docs" |
| **Narad Routing Logic** | Multi-part code task → `invoke_parashurama` ONLY (one avatar handles all code subtasks). Routing rule: "Fix bug AND add tests AND update README" → single Parashurama call. All three are code tasks with same owner. |
| **Agent Execution Logic** | 1. FTS5 recall: any prior auth-related code in Smriti. 2. `_read_file()` on existing auth-adjacent files. 3. `_write_script()` for endpoint implementation. 4. `_run_shell()` to run existing test suite. 5. `_write_script()` for test file. 6. `_run_shell()` to run new tests. 7. `_write_script()` for updated API docs. 8. Returns all three as inline code blocks. |
| **Tools Used** | `_read_file`, `_write_script`, `_run_shell`, `_query_database` |
| **Skills Used** | `shell_skill.read_file()`, `shell_skill.write_script()`, `shell_skill.run_shell()` |
| **Input Files** | Existing source files (read via `_read_file`) |
| **Output Files** | `auth.py`, `test_auth.py`, `API.md` (all written to disk) |
| **LLMs & APIs** | DeepSeek V4 Pro (Parashurama) |

---

### 3.6 Health + Lifestyle Assessment

| Column | Detail |
|--------|--------|
| **User Intent** | "I've had a headache for 3 days, I'm not sleeping well, and I've been eating badly — what's going on?" |
| **Narad Routing Logic** | Symptom assessment (Narasimha) + health log analysis (Vamana). Parallel since Narasimha handles clinical assessment while Vamana reads historical health data. |
| **Agent Execution Logic** | **Narasimha (parallel):** Runs 5-phase `symptom_check`. Red flag check (3-day headache + sleep disruption). Assessment via `_get_health_log(days=30, anomaly_detection=True)` — checks for patterns. Provides triage. **Vamana (parallel):** `_get_health_log(days=30)` for full symptom history. `_get_spending(category="Food")` for dietary patterns. Identifies lifestyle correlations. **Narad synthesis:** "Your symptom log shows a pattern of headaches following late-night work sessions (Narasimha's assessment). Your food spend dropped 60% last week vs your normal pattern (Vamana's data). Consider..." |
| **Tools Used** | Narasimha: `_read_file`, `_run_shell`, `_write_script`. Vamana: `_get_health_log`, `_get_spending`, `_log_symptom` |
| **Skills Used** | `health_skill.get_health_log()`, `health_skill.log_symptom()`, `finance_skill.get_spending()` |
| **Input Files** | `~/.narad/health.db`, `~/.narad/finance.db` |
| **Output Files** | `~/.narad/health.db` (new symptom entries) |
| **LLMs & APIs** | DeepSeek V4 Pro (Narasimha) + DeepSeek V4 Flash (Vamana) — concurrent, health_anomaly.py |

---

### 3.7 Mental Health Support + Resources

| Column | Detail |
|--------|--------|
| **User Intent** | "I've been feeling really anxious and hopeless lately" |
| **Narad Routing Logic** | Mental health → `invoke_krishna` exclusively. Routing rule: anxiety, depression, hopelessness → PHQ-4 screen → support → resources. If PHQ-4 score ≥12: mandatory crisis resource (iCall: 9152987821) included before any other response. |
| **Agent Execution Logic** | 1. Krishna activates MENTAL HEALTH mode. 2. Screens with PHQ-4 questions (4 items: depressed mood, anhedonia, anxiety, worry). 3. Scores response. 4. If ≥12: includes iCall number in FIRST response. 5. Provides empathetic support response. 6. Lists local mental health resources. 7. `_log_symptom("anxiety", severity)` via Narasimha hand-off if user wants to track. |
| **Tools Used** | `_compose_email` (if user wants resources emailed) |
| **Skills Used** | None (LLM-driven empathy) |
| **Input Files** | Smriti recall (any prior mental health context) |
| **Output Files** | None (inline support) |
| **LLMs & APIs** | DeepSeek V4 Flash (Krishna) |

---

## 4. Plan-Driven Multi-Phase Execution (Kanban Dispatch)

The most complex workflow: Rama emits `PLAN_JSON:`, Kanban populates, subsequent turns dispatch avatāras based on plan step ownership.

---

### 4.1 Multi-Avatar Project Plan Execution

| Column | Detail |
|--------|--------|
| **User Intent** | "Build a complete product launch: competitive research, code the landing page, write the launch email, and create a project timeline" |
| **Narad Routing Logic** | Complex multi-owner project → `invoke_rama` to produce structured plan with PLAN_JSON. Subsequent turns: dispatch level-0 steps (no dependencies) in parallel across avatāras. |
| **Agent Execution Logic** | **Turn 1 — Rama:** Produces structured project plan. Emits `PLAN_JSON:` block (steps: research, code, email, timeline — each with OWNER field). Kanban populates all steps as `backlog`. `kanban_update` SSE fires. **Turn 2 — Narad reads plan levels:** Level-0 steps (no deps): research (Matsya) + email draft (Krishna) → dispatched simultaneously. Parashurama's landing page depends on research → Level 1 (deferred). **Turn 2 — Matsya + Krishna (parallel):** Matsya: `_web_search()` competitive research. Krishna: `_compose_email()` launch email draft. Both steps → `in_progress` → `done` in Kanban. `kanban_update` SSE events stream to UI. **Turn 3 — Parashurama:** Now dispatched with Matsya's research as context. Builds landing page HTML. `_create_webpage(html)`. Step → `done`. **Narad synthesis:** Integrates all outputs across turns into cohesive project deliverable. |
| **Tools Used** | Rama: calendar + finance. Matsya: search + browse. Krishna: email compose. Parashurama: `_create_webpage`, `_write_script` |
| **Skills Used** | `matsya_search.web_search()`, `browser_skill.browse_url()`, `email_skill.compose_email()` |
| **Input Files** | `~/.narad/finance.db` (budget context) |
| **Output Files** | `~/.narad/plans/{session_id}.json`, `~/.narad/kanban.db`, `~/.narad/artifacts/{run_id}/landing.html` |
| **LLMs & APIs** | DeepSeek V4 Pro (Rama) → DeepSeek V4 Flash (Matsya) + DeepSeek V4 Flash (Krishna) → DeepSeek V4 Pro (Parashurama) |

---

### 4.2 Browser Form Fill Workflow (Three-Phase Confirmation)

| Column | Detail |
|--------|--------|
| **User Intent** | "Fill out the job application form at careers.example.com with my resume details" |
| **Narad Routing Logic** | Interactive browser with form submission → `invoke_matsya`. Three-phase workflow with user confirmation gates before any submission. |
| **Agent Execution Logic** | **Phase 1 (screenshot):** Matsya calls `_browser_screenshot(url)` → navigates to URL, detects all form fields, returns screenshot + field map (labels + selectors). User sees the form. **Phase 2 (dry-run fill):** Matsya calls `_browser_fill(url, fields, dry_run=True)` → fills fields in memory, returns screenshot of filled form without submitting. Emits `CURRENT_PHASE: awaiting_submit_confirmation`. **Phase 3 (on user "submit it"):** Skill continuation → `_browser_fill(url, fields, dry_run=False)` → actually submits form. Returns confirmation or error page screenshot. Note: blocked domains (banks, irs.gov) refuse submission regardless of dry_run. |
| **Tools Used** | `_browser_screenshot`, `_browser_fill`, `_browser_upload_and_submit` |
| **Skills Used** | `browser_act_skill.browser_screenshot()`, `browser_act_skill.browser_fill()` |
| **Input Files** | Resume/portfolio files (for `_browser_upload_and_submit`) |
| **Output Files** | Screenshot PNGs (base64 in response) |
| **LLMs & APIs** | DeepSeek V4 Flash (Matsya), Playwright Chromium |

---

### 4.3 5S Filesystem Health + Cleanup

| Column | Detail |
|--------|--------|
| **User Intent** | "Run a health check on my Narad data files and clean up stale sessions" |
| **Narad Routing Logic** | System operations → `invoke_vamana`. Vamana owns `_narad_shuddhi`. |
| **Agent Execution Logic** | **Phase 1 (dry-run audit):** Vamana calls `_narad_shuddhi(dry_run=True)` → internally hits `/5s/shine?dry_run=true`. Returns: total files, stale sessions (>180 days), stale artifacts (>30 days), reclaimable MB, manifest staleness. **Phase 2 (report):** Vamana synthesises findings and asks for confirmation before deleting. **Phase 3 (execute on confirm):** `_narad_shuddhi(dry_run=False)` → deletes stale files, updates manifest, writes deletion log to `5s_shine_log.jsonl`. Returns: files deleted, MB reclaimed, new 5S score. |
| **Tools Used** | `_narad_shuddhi` |
| **Skills Used** | `narad_5s.NaradShuddhi.shine()` |
| **Input Files** | `~/.narad/sessions/`, `~/.narad/artifacts/`, `~/.narad/manifest.json` |
| **Output Files** | `~/.narad/config/5s_shine_log.jsonl` (deletion log), `~/.narad/manifest.json` (updated) |
| **LLMs & APIs** | DeepSeek V4 Flash (Vamana), local filesystem |

---

## 5. Post-Processing Pipeline (Every Request)

All requests — regardless of single/multi-agent — run through this pipeline after avatar completion:

| Stage | What Happens | File/System |
|-------|-------------|-------------|
| **Yantra** | Every tool call, latency, token usage, and error (with `error_type`) written to session JSONL | `~/.narad/sessions/{session_id}.jsonl` |
| **Tapas** | Fire-and-forget: judge scores response (0.0–1.0) with retry; if ≥0.75 → promotes to sutra; if <0.45 → flags weak session; if judge unreachable → emits `tapas_skipped` Karma event | `~/.narad/config/sutras.jsonl`, `~/.narad/config/karma.jsonl` |
| **Andon** | Checks result length (EMPTY_RESULT), latency (TIMEOUT), connection state (CONNECTION), tool errors (TOOL_ERROR); fires diagnostic if triggered | `~/.narad/config/andon_log.jsonl` |
| **Sankalpa** | Observes user phrasing + avatar style; updates per-user style model | `~/.narad/config/sankalpas.jsonl` |
| **Smriti** | Remembers (task, result, avatar) into LanceDB vector store for future recall | `~/.narad/memory/` (LanceDB) |
| **Scribe** | Compiles session into project wiki Markdown if a project is detected | `~/.narad/wiki/{user_id}/{project_id}/entity.md` |
| **Kanban** | If plan steps active: transitions step status → emits `kanban_update` SSE | `~/.narad/kanban.db` |

---

## 6. Model Routing — Three-Path Decision Tree

For every avatāra invocation, `avatar_agents.py` selects the LLM automatically:

```
Incoming task to an avatāra
│
├── images attached (from _images_ctx)?
│   └─ YES → MiMo v2.5 (openai/mimo-v2.5)
│             base: https://token-plan-sgp.xiaomimimo.com/v1
│             [multimodal input path]
│
├── visual output keywords detected? (no images)
│   └─ YES → Gemini 3 Flash (gemini/gemini-3-flash-preview)
│             keywords: slide deck, presentation, mockup, wireframe,
│             ui design, landing page, web page, dashboard design
│             [visual output generation path]
│
└── everything else → avatāra's assigned DeepSeek model
    ├── DS_PRO  (deepseek/deepseek-v4-pro):  Narad, Narasimha, Rama, Buddha, Parashurama
    └── DS_FLASH (deepseek/deepseek-v4-flash): Matsya, Varaha, Krishna, Vamana
```

---

## 7. Routing Decision Tree (Quick Reference)

```
User message arrives
│
├── Hard block? (injection, SSN, crisis pattern) → Dharma Gate rejects
├── Rate limit exceeded? → 429
│
└── Route to avatāra(s):
    │
    ├── Live web / research / APIs / arXiv → Matsya (DS_FLASH)
    ├── PDF / DOCX / PPTX extraction / quantitative finance → Varaha (DS_FLASH)
    ├── Debug / exception / health symptom / root cause → Narasimha (DS_PRO)
    ├── Plan / SOP / calendar / savings roadmap → Rama (DS_PRO)
    ├── Email / education / video / mental health → Krishna (DS_FLASH)
    ├── Tradeoff / red-team / architecture review / DMAIC → Buddha (DS_PRO)
    ├── Code / shell / SQL / React / audio → Parashurama (DS_PRO)
    └── Filesystem / personal finance / health log / 5S → Vamana (DS_FLASH)
    │
    ├── Research THEN write/analyse → Matsya FIRST, then Krishna/Buddha (sequential)
    ├── Independent multi-domain → parallel (up to 3 avatāras)
    └── Multi-owner project → Rama produces PLAN_JSON → Kanban dispatch
    │
    └── Visual output keywords detected (any avatāra) → Gemini 3 Flash
        Images attached (any avatāra) → MiMo v2.5
```

---

## 8. LLM & API Call Registry

| API / Service | Model / Endpoint | Who Uses It | Purpose |
|--------------|-----------------|------------|---------|
| DeepSeek V4 Flash | `deepseek/deepseek-v4-flash` | Matsya, Varaha, Krishna, Vamana | Fast retrieval, prose, extraction, data queries |
| DeepSeek V4 Pro | `deepseek/deepseek-v4-pro` | Narad, Narasimha, Rama, Buddha, Parashurama | Routing, reasoning, planning, code, analysis |
| MiMo v2.5 | `openai/mimo-v2.5` (base: `token-plan-sgp.xiaomimimo.com/v1`) | Any avatāra (images attached) | Multimodal image input understanding |
| Gemini 3 Flash | `gemini/gemini-3-flash-preview` | Any avatāra (visual output tasks) | UI/PPT/mockup/HTML deck generation |
| DeepSeek R1 | `deepseek/deepseek-r1` | Tapas judge | Quality scoring (independent judge) |
| Gemini Embedding | `text-embedding-005` | Smriti v1 | Semantic memory embedding (LanceDB) |
| Gemini Veo 3.1 | `veo-3.1-generate-preview` | Krishna | Photorealistic video clip generation |
| Gemini Imagen | `imagen-4.0-fast-generate-001` | Krishna | Image generation |
| Tinyfish API | — | Matsya | Primary web search |
| Tavily API | — | Matsya | Fallback web search |
| arXiv API | — | Matsya | Academic paper search |
| Semantic Scholar API | — | Matsya | Citation-aware paper search |
| Hugging Face Papers API | — | Matsya | ML papers + model search |
| Playwright Chromium | — | Matsya | JS-rendered page fetching + form automation |
| Gmail SMTP | smtp.gmail.com:587 (TLS) | Krishna | Email delivery |
| CalDAV | iCloud / Google / Nextcloud | Rama | Calendar read + create |
| RxNorm REST API | rxnav.nlm.nih.gov | Rama | Drug information lookup |
| SQLAlchemy | — | Parashurama | Database queries (SQLite/PostgreSQL/MySQL) |
| moviepy + Pillow | — | Krishna | Programmatic video generation, fallback after Veo (executor sandbox) |
| PyMuPDF + python-docx | — | Matsya | PDF/DOCX parsing (default extractors) |
| Docling (IBM) | — | Matsya | Opt-in rich extraction (`NARAD_USE_DOCLING=1`) |
| OpenAI `text-embedding-3-small` | — | Smriti v1 | Embedding fallback if Gemini unavailable |

---

*Last updated: 2026-07-04 (M0 truth-reconciliation pass — video cascade, audio removal, Notion removal, integration table). Older sections may retain pre-consolidation avatar names; see drift note at top. Source: live code analysis of `phase-1/narad_agent.py`, `phase-1/avatar_agents.py`, `phase-1/model_config.py`, `phase-1/server.py`, `phase-8/` skill files.*
