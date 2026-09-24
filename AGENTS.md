# Narad — Agent Skills & Tools Reference

> **Source of truth.** This document defines every agent's identity, tools, and routing
> rules — verified against `phase-1/avatar_agents.py` and `phase-1/narad_agent.py`.
> Last verified: 2026-09-16 (runtime cleanup pass).

---

## System Overview

```
User
 └── Narad (supervisor / router — connected endpoint, offline Gemma fallback)
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
Narad calls them as function tools. A single avatar hands off (its streamed answer is
the reply, no rewrite call); outputs of several avatars are synthesised into one response.
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
| File/doc analysis: PDF, DOCX, PPTX, HTML, CSV, transcript, document photos | Matsya |
| Save values from a lab report, statement, prescription, bill or circular (photo/PDF) | Matsya (`extract_fields`) |
| Deep research, literature review, SOTA, academic sources | Matsya |
| Critical analysis, tradeoff, red-team, "should I do X" | Matsya |
| Local filesystem: clean up, organise, disk analysis | Matsya |
| Structured plan, SOP, checklist, runbook, calendar event | Rama |
| Personal finance: import CSV, spending, budgets, goals, net worth | Rama |
| Log symptoms, medication reminders, query health history, lab result trends | Rama |
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
- **Hand off, don't rewrite.** Avatar tools take `hand_off` (default true): one avatar
  with one deliverable ends the turn with its own answer (`skip_summarization`).
  Pass `hand_off=false` for parallel or sequential avatars, video URL checks, Rama
  plans to dispatch, or any reply that combines results. Two or more calls in one
  response always get a synthesis, whatever `hand_off` says.
- **Pre-router (`phase-1/prerouter.py`, `NARAD_PREROUTER=off` disables).** Unambiguous
  turns skip Narad's routing call: a bound workflow stage → its owner (never
  Parashurama), `/teach` or "teach me" or a Gurukul check answer → Krishna, a
  bank-statement CSV → Rama, a document/photo with a summarise-style ask → Matsya,
  a bare URL → Matsya. Everything else goes to Narad.

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
| `web_search` | Exa search |
| `exa_search` | Exa auto/fast/deep research with highlights, schemas, grounding, and freshness controls |
| `exa_contents` | Bounded full-text extraction for known public URLs |
| `browse_url` | Playwright headless browser for JS SPAs and specific URLs |
| `http_request` | Direct REST API / webhook calls |
| `computer_use` | Persistent isolated Playwright or profile-granted signed-in BrowserSkill sessions; semantic actions, batched execution, and trace artifacts; desktop is opt-in |
| `phone_use` | Optional profile-granted Android execution through Artemis; preview-first, verified mode for sensitive work |
| `browser_screenshot` / `browser_fill` / `browser_upload_and_submit` | Compatible form helpers over one shared session: screenshot → preview → explicit confirmation → submit |
| `search_arxiv` / `search_papers` / `search_hf_papers` / `search_hf_models` | Academic + model discovery |
| `query_deepwiki` | GitHub repo architecture questions |
| `extract_document` | Lightweight PDF/DOCX/PPTX/HTML/CSV/text extraction; photos (jpg/png/webp/heic) and scanned PDF pages through local OCR, lines tagged `[p1-l3]` |
| `extract_fields` | Values from lab reports, statements, prescriptions, circulars, bills and forms → a pending document review; nothing is saved until the person confirms each value against its crop |
| `scan_directory` / `organize_by_type` / `move_to_trash` / `find_large_files` / `get_disk_info` | Filesystem hygiene — always dry-run before mutating |
| `search_last30days` | Cross-source recency sweep (Reddit/HN/GitHub) |

Soft rules: primary sources over aggregators; cite every non-obvious claim; screenshot
before any form fill; never submit without per-form user confirmation; quote source
location for document findings; extract health documents objectively — never diagnose.
After `extract_fields`, never say values are saved: give the review link it returns.

---

## Rama — Planning, Calendar, Personal Data

Structured-plan specialist and owner of the personal data lifecycle (finance + health).

| Tool | Purpose |
|---|---|
| `get_upcoming_events` / `create_event` | Connected Google Calendar |
| `get_spending` / `get_budget_status` / `get_financial_context` / `get_recurring_expenses` / `get_goals` / `get_net_worth` / `get_spend_patterns` | Personal finance reads |
| `import_csv` / `sync_gmail_finance` / `set_budget` / `add_goal` / `update_goal_progress` / `add_balance_snapshot` / `categorize_transaction` | Personal finance writes |
| `log_symptom` / `set_medication_reminder` / `get_health_log` | Health log |
| `get_lab_results` | Lab values confirmed from the person's reports, per test with a trend and a link back to each value's crop |
| `query_rxnorm` | Drug information (RxNorm REST, no auth) |

Plans emit `PLAN_JSON:` blocks that workflows can persist and execute.

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

### Streaming
Narad and every avatar run with `StreamingMode.SSE` (never `tool_thread_pool_config`:
sync tools are already offloaded). Partial text reaches the client as `text_delta`
`{source, text}` (avatars add `handoff`) after a per-source `<think>` filter;
`Part.thought` text is never sent; `text_reset {source}` drops chatter before a tool
call. `narad_synthesis` still carries the complete reply, and a pre-routed turn adds
a `route {avatar, reason, via}` event. For `redact`-tier providers,
`privacy_gateway.StreamRestorer` restores placeholders split across chunks.

### Dharma (Policy Gates)
Two layers. Input: `_dharma_gate(query)` in `server.py` blocks prompt injection, PII
collection, and crisis phrases (with resources) before any avatar runs. Side effects:
`dharma.gate_action()` gates `executor`, `email_send`, `browser_submit`, and `desktop_control` — unknown
actions are denied by default; every verdict lands in Karma. Policy file:
`~/.narad/config/dharma_policy.json`.

### Privacy Gateway
`privacy_gateway.py` is the single egress chokepoint:
- Every agent call (`NaradLiteLlm`), background learner (Tapas, Sankalpa, guru engine) and embedding goes through it.
- Destinations are tiered `local` / `trusted` / `redact` / `blocked`. DeepSeek and unknown hosts are `redact`; xAI is `blocked`.
- For `redact` destinations:
  - rules, the family name list and the local OpenMed model replace personal details with per-profile placeholders;
  - a leak check fails closed;
  - replies are restored on the Mac.
- Images, audio and text read aloud cannot be pseudonymised: they reach only `local` or `trusted` destinations (`allow_raw`). A plain `completion` call with an image part is gated the same way (document escalation uses it).
- Each cloud call is logged to `profiles/<id>/privacy/egress.jsonl`, served at `GET /privacy/egress`.
- Direct `litellm.completion`/`embedding` calls outside the gateway fail CI (`phase-1/test_privacy_gateway.py`).

### Runtime Quality
**AndonGate** (`andon.py`): fires on `EMPTY_RESULT` (<80 chars), `TIMEOUT` (>120s),
`CONNECTION`, `TOOL_ERROR`; logs to `~/.narad/config/andon_log.jsonl` + SSE alert.
Workflow stages provide durable progress directly; there is no parallel Kanban or Projects subsystem.

### Security Floor
Enforced in `phase-1/server.py`; regression tests in `phase-1/test_profile_security.py`
and `phase-1/test_profile_isolation.py`.
- **Auth modes** (`NARAD_AUTH`):
  - `local` (default): direct loopback requests pass as the host; a request relayed by a tunnel or proxy (any forwarding header) is not local.
  - `strict`: every request needs a profile session or the host bearer token (`~/.narad/config/api_token`, chmod 600). The exceptions are the sign-in gate, `/health`, the OAuth callbacks and the app shell.
  - `off`: tests only.
- **Profiles and PINs**: each person signs in with a 4–8 digit PIN (PBKDF2) and gets a signed 30-day session.
  - Wrong PINs lock the profile after 5 tries and the client IP (IPv6 per /64) after 10, backing off from 30 s to 15 min (HTTP 429 with `Retry-After`).
  - Each profile has a session epoch. A PIN change or "sign out everywhere" (`POST /profiles/{id}/revoke-sessions`) bumps it, and every older token stops working.
  - Changing your own PIN needs the current one. The owner can reset a member's PIN (`POST /profiles/{id}/reset-pin`) and sign them out. In the app: System → Profile.
- **Server-derived identity**: the session decides the profile. `?user_id=` is rewritten to it; a different id in a header, query, body or path gets 403, and omitting it means your own.
- **Invite-only profiles**: `POST /profiles` needs the owner or a single-use invite code (72 h, stored hashed) from the owner's `POST /profiles/invites`. The owner's first PIN is set only on the Mac itself (`/profiles/bootstrap`, loopback only).
- **Owner-only**: provider keys (`/connections*`, Kunji), the local model, tier choice, device and browser grants (`/interaction-targets`), invites, PIN resets, sutra accept/revert, and the host shell tools.
- **Per-profile records**: sutras, andon, karma, sankalpa, costs, audit, search and provenance show each profile only its own records. The owner adds `?scope=all` to see everyone's as counts, kinds and times: other profiles' rows lose their text (task previews, details, the question a sutra was learned from), sankalpa gives totals, and provenance stays the caller's own. Records that name no profile predate profiles and are the owner's.
- **Media**: `/media` reads take the session, or the HttpOnly `narad_media_session` cookie mirrored from it (path `/media`, SameSite=Lax, Secure over HTTPS).
  - Captures and generated runs live under `computer-use/`, `phone-use/` and `runs/`, one folder per profile, and are served only to that profile. Older top-level run folders are served only to the owner.
  - Everything is served sandboxed (CSP `sandbox allow-scripts`, `nosniff`); downloads never render.
- **Cloudflare Access at the origin** (`phase-1/cf_access.py`, the `_cloudflare_access` middleware): with `NARAD_CF_ACCESS_TEAM_DOMAIN` and `NARAD_CF_ACCESS_AUD` set, every non-loopback request needs a valid Access JWT (RS256; audience and issuer checked). Anything else gets 403. `GET /health` stays open for uptime probes.
- **Browser reach**: computer use refuses loopback, LAN, metadata and credentialed URLs unless the owner names a host in `NARAD_BROWSER_PRIVATE_HOSTS`. The check runs before navigating and again wherever the page lands after a redirect, link or history move.
- **CORS** via `NARAD_ALLOWED_ORIGINS`. Chat is rate-limited per profile (token bucket, 10 req/min, `NARAD_RATE_LIMIT`, HTTP 429).
- **Egress**: every model and embedding call leaves through the privacy gateway (see Privacy Gateway above).

### User Inputs & Visual-Output Routing
Chat uploads are stored privately under `~/.narad/attachments/`. Images enter the
multimodal input path; documents, data, archives, and folders enter a bounded
artifact plane with exact local reread paths. Live URLs are retrieved by Matsya
before page-specific claims are made. Source trees that require edits route to
Parashurama, and personal bank CSV ingestion routes to Rama. Visual output tasks
(decks, UI, pages) stay on the active Krishna worker model; video requests stay
on the Veo/moviepy path.

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

SAVING VALUES FROM A DOCUMENT (lab report, statement, prescription, circular, bill):
        photo / PDF → Matsya extract_fields
          → local OCR on the Mac (ocr_skill: PaddleOCR PP-OCRv5 or Surya), lines with ids
            and boxes; PDF text layers are used as-is
          → worker model, text only, through privacy_gateway (redact tier sees placeholders)
          → strict JSON per doc_type; repair; hallucination guard (numbers and dates must be
            printed in a cited or repaired source row)
          → pending review under profiles/<id>/documents/reviews/ + `document_review` SSE card
        → person on /?review=<id>: each value beside its crop; tick, edit or drop; save
          → lab values: health.db lab_results · debits: finance.db transactions (deduplicated
            with CSV imports) · medicines: reminders only if ticked · dates: calendar event or
            reminder only if ticked. Saved rows keep the review and item ids (crop provenance).
        Hard pages: the person may send that page image, per document, to a local or
        trusted vision model (privacy_gateway.allow_raw; logged in the egress ledger).
        Rama reads the history with get_lab_results ("how has my HbA1c changed?").
```

---

## Status

Shipped: 4-avatar runtime, SSE server with auth/rate-limit/CORS floor, Smriti/Sutra/
Tapas/Karma/Sankalpa/Yantra loops, Andon alerts, Dharma action gates, tool
result envelopes, context governor, session harness contract.

Removed in the M0 cut (2026-07-04): Notion sync, webwright, ml-intern, hyperframes,
audio, remotion skills, beautiful-html-templates submodule, phase-0a/0b spikes
(archived on branch `archive/spikes`).
