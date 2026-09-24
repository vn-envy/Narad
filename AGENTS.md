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
| `http_request` | Direct REST API / webhook calls; POST/PUT/PATCH/DELETE wait for an Anumati approval |
| `start_task` | Every multi-step web errand (search and compare, fill a form, book, find something on a long page). Returns a task id at once; Kriya runs it in the background, the person watches and stops it on the task card (see Kriya below) |
| `computer_use` | Single quick looks or actions: persistent isolated Playwright or profile-granted signed-in BrowserSkill sessions; semantic actions, batched execution, and trace artifacts; desktop is opt-in. Benign steps run; the first commit step returns `needs_approval` (Anumati) |
| `phone_use` | Optional profile-granted Android execution through Artemis; consequential tasks need verified mode and an Anumati approval |
| `browser_screenshot` / `browser_fill` / `browser_upload_and_submit` | Compatible form helpers over one shared session: screenshot → fill → approval card → Narad submits |
| `search_arxiv` / `search_papers` / `search_hf_papers` / `search_hf_models` | Academic + model discovery |
| `query_deepwiki` | GitHub repo architecture questions |
| `extract_document` | Lightweight PDF/DOCX/PPTX/HTML/CSV/text extraction; photos (jpg/png/webp/heic) and scanned PDF pages through local OCR, lines tagged `[p1-l3]` |
| `extract_fields` | Values from lab reports, statements, prescriptions, circulars, bills and forms → a pending document review; nothing is saved until the person confirms each value against its crop |
| `scan_directory` / `organize_by_type` / `move_to_trash` / `find_large_files` / `get_disk_info` | Filesystem hygiene — always dry-run before mutating. Moving files is owner-only; every path follows `host_access.path_access_error` (no secrets, no other profile's files, family members only their own) |
| `search_last30days` | Cross-source recency sweep (Reddit/HN/GitHub) |

Soft rules: primary sources over aggregators; cite every non-obvious claim; screenshot
before any form fill; every submit is approved per form on the person's approval card; quote source
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
| `compose_email` / `compose_rich_email` / `send_email` | Email — `send_email(dry_run=False)` puts the exact email on an approval card; Narad sends it (Dharma-gated) when the person approves |
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
Two layers. Input, in `/chat` before any avatar runs:
- **Crisis care** (`phase-1/crisis_care.py`): first-person suicidal intent or self-harm in English,
  Hindi (Devanagari) or Hinglish gets an immediate, warm reply in the person's language with
  Tele-MANAS 14416 (24x7), iCall 9152987821 and 112. It runs before consent, the rate limit and
  model checks; the message never reaches a model and is not written to the thread (a later turn
  would replay it to the brain); Karma gets an `input_gate` event with kind and language, never
  the text. Exaggeration, idioms and news questions do not match (`test_crisis_care.py` table).
- `_dharma_gate(query)` refuses prompt-injection markers (word-bounded). Identifiers such as
  passport numbers are not refused: Travel needs them, and the privacy gateway pseudonymises
  them for `redact` providers.

Side effects:
`dharma.gate_action()` gates `executor`, `email_send`, `browser_submit`, and `desktop_control` — unknown
actions are denied by default; every verdict lands in Karma. Policy file:
`~/.narad/config/dharma_policy.json`. Dharma decides whether an action may happen at
all; Anumati decides whether this person approved this exact one.

### Anumati (Approvals)
Commit-class side effects run only against an `ActionProposal` the person approved on
their phone (`anumati.py`). A model's `confirmed=True` or `dry_run=False` approves nothing.
- **Proposal**: surface (email / browser / signed_in_browser / desktop / phone / workflow / http / task),
  action, target, canonical args, and `args_hash` = sha256 over all four; a summary the tool
  builds from the args; risk class; preview (email fields or a screenshot); 15-minute expiry
  (24 h for path steps, `NARAD_APPROVAL_TTL_S`); decided by / at / device; result. Stored per
  profile in `profiles/<id>/anumati.db` (SQLite WAL). `scope` is reserved for standing envelopes.
- **Tools call `anumati.require(...)`**: an approved, unconsumed, unexpired proposal with the
  same hash is consumed once and the tool proceeds; otherwise the tool returns
  `needs_approval` with the pending proposal (an identical request reuses it, an executed
  one returns `already_done`). Covered: `send_email`, commit steps in `computer_use`
  (isolated, signed-in, desktop), `browser_fill` / `browser_upload_and_submit` submits,
  consequential `phone_use` tasks, workflow stage confirmations, `http_request` POST /
  PUT / PATCH / DELETE (GET, HEAD and OPTIONS run at once; secret headers are masked on the
  card), and commit steps inside Kriya tasks (surface `task`: no executor is registered, so
  approving leaves the proposal `approved` and the task's own loop consumes it and runs that
  one step). The owner-only shell tools keep their own allowlist gate. `create_event` stays
  model-confirmed on purpose: it only adds a private event to the person's own calendar
  (no attendees), which they can delete, so it is reversible input, not a commit.
- **Deciding**: `GET /approvals?status=pending`, `GET /approvals/{id}`,
  `POST /approvals/{id}/approve|reject|edit` (edit: email recipients, subject, body; it
  creates a new proposal with a new hash). Profiles only see their own (others get 404).
  Approving runs the stored args server-side through the surface's registered executor,
  never another model call; browser executors first check that the page URL and the
  target's label are unchanged. Every verdict goes to Karma with the hash; the result is
  noted in the originating chat thread and sent as `vahana.deliver(kind="approval_result")`.
  A new proposal sends `kind="approval_request"` (`data.url` = `/?approval=<id>`) and the
  chat stream emits `approval_requested`, which the app renders as an approval card.
- **Risk policy v2** (`risk_policy.py`, one ordered rule table, Hindi/Hinglish labels
  included): approval only for commit steps — send, pay, book, buy, apply, submit a form,
  upload, delete, account changes, public posts, typing a password/OTP/card/ID number,
  unlabelled or coordinate clicks, all desktop input, and any non-read step on a page with
  prompt-injection text. Reading, navigating, scrolling, typing into ordinary fields, search
  boxes, filters, sorting, paging, cookie/consent banners and sign-in pages run freely.

### Kriya (Task runtime)
Multi-step errands run in `phase-8/kriya/`, not in the avatar's context: `start_task(goal,
start_url, surface, done_when)` returns a task id at once, the chat stream emits
`task_started` (the app's task card), and a worker thread runs the loop with a dedicated
operator model (`NARAD_OPERATOR_MODEL`, default Matsya's worker model), always through
`NaradLiteLlm` and so the privacy gateway.
- **Loop**: perceive → decide → act → settle → verify. Perception is Playwright's AI
  accessibility snapshot (real ARIA roles, refs stable across steps, same- and
  cross-origin iframes, open shadow DOM), scoped to the viewport with "more below: N items"
  paging and a budget of about 2K tokens; password-field values and fields named like a
  secret are masked. Only the latest observation goes to the operator in full; earlier steps
  are one line each. Actions go by ref with 5 s actionability timeouts; the page then settles
  (navigation, a quiet DOM, no document/XHR/fetch in flight; at most 4 s) and the step is
  verified deterministically (field value, checked state, URL, text appeared or gone, "the
  page changed"); a miss re-grounds the target by role and name and retries once, then the
  operator is told. A screenshot goes to the operator only when the tree is poor and the
  model reads images at a `local` or `trusted` tier.
- **Safety**: every step is classified by `risk_policy`; a commit step becomes an Anumati
  proposal (surface `task`, screenshot preview) and the task waits in `waiting_approval`.
  Approve → that exact step runs once (never retried; page URL and target label rechecked;
  Dharma `browser_submit` gate); reject → the task stops cleanly; expiry → it pauses and asks
  again on Continue. Page-state rules from the DOM: a password field in view or a captcha →
  `waiting_help` and a `question` push; instruction-like page text is shown to the operator
  as untrusted and every non-read step there needs approval; the Phase 0 URL policy runs
  before every navigation and wherever a page lands.
- **Store and control**: `profiles/<id>/kriya.db` (SQLite WAL, 0600) with the task and its
  event log (the phone's step list); states queued / running / waiting_approval /
  waiting_help / done / failed / cancelled. One browser task per profile at a time (the
  isolated and cloud browser share the lock), desktop and phone exclusive, `NARAD_KRIYA_WORKERS`
  (3) at once, `NARAD_KRIYA_MAX_ACTIVE` (3) per profile, `NARAD_KRIYA_MAX_STEPS` (30). Cancel
  is checked before every action and while waiting (stops within one step). Server shutdown
  suspends tasks at a checkpoint; startup resumes unfinished ones from their last page.
- **Routes** (`kriya/api.py`, profile-scoped with `_assert_profile_match`; another profile's
  id is 404): `GET /tasks`, `GET /tasks/{id}` (with events, and the approval while one
  waits), `POST /tasks/{id}/cancel`, `POST /tasks/{id}/resume`, `POST /tasks/{id}/takeover`
  (click at a fraction of the frame, type, key, scroll, back; only while `waiting_help`;
  typed text is never logged or stored), `GET /tasks/{id}/frame` (latest viewport JPEG, kept
  in memory only, `Cache-Control: no-store`; the app polls it at about 1.5 fps).
- **Cloud browser** (owner decision 2): with `NARAD_CLOUD_BROWSER_URL` (CDP websocket of a
  self-hosted Steel or browserless; `NARAD_CLOUD_BROWSER_TOKEN` is added as `?token=`,
  `NARAD_CLOUD_BROWSER_TOKEN_PARAM` renames it) the runtime uses it only for tasks with no
  sign-in words and no personal data in the goal (`NARAD_KRIYA_CLOUD=off` disables). Each
  task gets a fresh context (no cookies, storage state, credentials or vault values, no
  downloads) on a CDP connection kept per profile; a sign-in wall or a step that would type
  personal data moves the task to the Mac's browser. Each session is one line in the egress
  ledger (`source=kriya_cloud_browser`, no content). Steel: create no `sessionContext`,
  `persist`, `userDataDir` or `credentials`, and run it with `ENABLE_CDP_LOGGING=false`,
  `LOG_CUSTOM_EMIT_EVENTS=false`, `ENABLE_VERBOSE_LOGGING=false`, `LOG_STORAGE_ENABLED=false`
  (the session event store behind replays) and no `CHROME_USER_DATA_DIR`; browserless: leave
  `record` off. Check these names against the version you deploy.
- **Pariksha**: `evals/pariksha/browser_fixtures.py` (fixture sites with server-side oracles and
  scripted operators), `phase-1/test_kriya_browser.py` (real Chromium; skipped without one;
  `NARAD_CHROMIUM_EXECUTABLE` names a pinned build) and `scripts/pariksha_browser.py`
  (scorecard with the real operator model on the Mac).

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
  Matsya's search and web tools log a `web` row too (`record_tool_egress`: provider, and how many
  placeholders stayed in the arguments; never the words).
- **Turn stamps and receipts.** `/chat` makes one `turn_id` per turn (shared with the pilot record
  and the `done` event) and sets it for the turn's task (`set_turn_id`); every ledger row the turn
  writes carries it, through avatar tools, `asyncio.to_thread`, streaming and pre-routed turns.
  Work that outlives the turn (Tapas, Sankalpa, the next lesson's syllabus) runs in
  `context_outside_turn()`; background index threads start with an empty context. Before `done`,
  the turn emits `privacy_receipt` (`privacy_receipt()`: providers, tiers, what for, replaced
  counts by kind; counts only), which is also stored on the assistant turn in the thread.
- Direct `litellm.completion`/`embedding` calls outside the gateway fail CI (`phase-1/test_privacy_gateway.py`).

### Runtime Quality
**AndonGate** (`andon.py`): fires on `EMPTY_RESULT` (<80 chars), `TIMEOUT` (>120s),
`CONNECTION`, `TOOL_ERROR`; logs to `~/.narad/config/andon_log.jsonl` + SSE alert.
Workflow stages provide durable progress directly; there is no parallel Kanban or Projects subsystem.

### Vahana (Delivery)
`vahana.deliver(user_id, kind, title, body, priority, data, summary)` is the one way anything reaches a person
(reminders, approvals, digests, Andon). It never raises and never waits on the network.
- **Inbox first**: every event lands in `~/.narad/inbox/<profile>.jsonl`. That copy is the source of truth, served by `GET /inbox` and shown on the Activity screen, grouped as Needs you, Running and Done.
- **Kinds**:
  - `medicine_reminder`, `health_alert`, `task_done` and `approval_request` can be shared with a carer;
  - `approval_result` and `question` are the other waits on a person;
  - `reminder`, `triage`, `andon`, `swapna`, `cron` and `system` stay with the profile.
  - Unknown kinds become `system`. `data.url` is the push deep link (same-origin only, e.g. `/?approval=<id>`); without one, the push opens the item in Activity (`/?activity=<id>`).
- **Web Push** (`vahana_push.py`):
  - Self-hosted VAPID keys live in `~/.narad/config/vapid.json` (0600) and devices in `profiles/<id>/push_devices.json`.
  - Only known push services are accepted as endpoints.
  - A device is bound to the profile's session epoch and moves profile if another person subscribes the same browser. A 404/410 from the push service drops it.
  - Sends run on a small thread pool.
  - ntfy is a fallback only for a profile with no Web Push device.
  - Routes: `GET /push/vapid-public-key`, `POST/DELETE /push/subscribe`, `GET /push/devices`, `POST /push/test`.
- **Preferences** (`profiles/<id>/notification_prefs.json`, `GET/PUT /notifications/preferences`):
  - Lock-screen details are off by default, so pushes use generic text per kind.
  - Quiet hours default to 22:00–07:00 in the phone's time zone. Only `urgent` pushes break them, plus the person's own on-time medicine reminders (which they can turn off). Everything else waits silently in the inbox.
- **Care circles** (`care_circle.py`, `profiles/<subject>/care_circle.json`):
  - The subject shares chosen kinds with a carer (`GET/PUT /care-circle`). Only the subject's own session can change it; owner and host credentials get 403.
  - The carer sees `GET /care-circle/shared-with-me` and leaves with `DELETE /care-circle/shared-with-me/{subject}`.
  - A carer's copy carries the title and a one-line summary only (`shared_from`, no data or links). It follows the carer's own quiet hours and lock-screen setting. An approval reaches them as an FYI they cannot decide.
- **Producers**:
  - Kala sends `medicine_reminder` per profile and one `health_alert` when a reminder is still unopened after `NARAD_DOSE_FOLLOWUP_MINUTES` (default 60).
  - A finished workflow path sends `task_done`.
  - A finished or failed Kriya task sends `task_done`; a task paused for a sign-in, a captcha or an expired approval sends `question` (both link to `/?task=<id>`).
  - The PWA service worker (`phase-4/frontend/public/sw.js`) shows pushes and caches only the app shell.

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
  - Changing your own PIN needs the current one. The owner can reset a member's PIN (`POST /profiles/{id}/reset-pin`) and sign them out. In the app: You (PIN and devices).
- **Server-derived identity**: the session decides the profile. `?user_id=` is rewritten to it; a different id in a header, query, body or path gets 403, and omitting it means your own.
- **Invite-only profiles**: `POST /profiles` needs the owner or a single-use invite code (72 h, stored hashed) from the owner's `POST /profiles/invites`. The owner's first PIN is set only on the Mac itself (`/profiles/bootstrap`, loopback only).
- **Owner-only**: provider keys (`/connections*`, Kunji), the local model, tier choice, device and browser grants (`/interaction-targets`), invites, PIN resets, sutra accept/revert, and the host shell tools.
- **Per-profile records**: sutras, andon, karma, sankalpa, costs, audit, search and provenance show each profile only its own records. The owner adds `?scope=all` to see everyone's as counts, kinds and times: other profiles' rows lose their text (task previews, details, the question a sutra was learned from), sankalpa gives totals, and provenance stays the caller's own. Records that name no profile predate profiles and are the owner's.
- **Media**: `/media` reads take the session, or the HttpOnly `narad_media_session` cookie mirrored from it (path `/media`, SameSite=Lax, Secure over HTTPS).
  - Captures and generated runs live under `computer-use/`, `phone-use/` and `runs/`, one folder per profile, and are served only to that profile. Older top-level run folders are served only to the owner.
  - Everything is served sandboxed (CSP `sandbox allow-scripts`, `nosniff`); downloads never render.
- **Cloudflare Access at the origin** (`phase-1/cf_access.py`, the `_cloudflare_access` middleware): with `NARAD_CF_ACCESS_TEAM_DOMAIN` and `NARAD_CF_ACCESS_AUD` set, every non-loopback request needs a valid Access JWT (RS256; audience and issuer checked). Anything else gets 403. `GET /health` stays open for uptime probes.
- **Browser reach**: computer use refuses loopback, LAN, metadata and credentialed URLs unless the owner names a host in `NARAD_BROWSER_PRIVATE_HOSTS`. The check runs before navigating and again wherever the page lands after a redirect, link or history move.
- **Consent**: with `NARAD_REQUIRE_CONSENT` on (the default), `/chat`, every `/voice/*` route and `/chat/attachments` answer 403 `{"code": "consent_required"}` until the profile accepts the current version of `docs/PILOT_CONSENT_AND_METRICS.md` (`POST /consent`). The owner is always considered consented. `GET /consent?part=a&lang=en|hi` serves Part A from the doc for the app's consent screen.
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

WEB ERRANDS: User → Narad → Matsya start_task() → task card in chat
        → Kriya [perceive → decide → act → settle → verify] in the background
        → commit step: approval card on the phone · sign-in: live view takeover
        → task_done push + a note in the chat thread
```

---

## Status

Shipped: 4-avatar runtime, SSE server with auth/rate-limit/CORS floor, Smriti/Sutra/
Tapas/Karma/Sankalpa/Yantra loops, Andon alerts, Dharma action gates, tool
result envelopes, context governor, session harness contract.

Removed in the M0 cut (2026-07-04): Notion sync, webwright, ml-intern, hyperframes,
audio, remotion skills, beautiful-html-templates submodule, phase-0a/0b spikes
(archived on branch `archive/spikes`).
