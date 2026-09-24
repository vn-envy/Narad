# Narad Pilot Readiness Plan

*2026-09-23. Scope: make Narad reliably do work for a family across phones, laptops, and the host Mac, with faster turns, more correct actions, and data that stays under your control. Built phase by phase; each phase ends with a measurable exit gate.*

## Direction (2026-09-24): pilot first, hackathon deferred

The family pilot is the only goal. The hackathon documents stay in `docs/` for reference, but they are deferred and none of their constraints apply: no Nemotron-only rule, no filmed errand, no feature freeze. What carries over from their reviews is the pilot-safety work: one egress chokepoint, privacy receipts, care circles, staged rollout, consent, honest metrics, and host uptime.

**Pilot:** 4 people, all on Android phones, and one M5 MacBook Air (24 GB) as the host.

The build runs in four stages. Each stage has a rollout gate, and nobody new joins until that gate is green.

| Stage | Goal | Contents | Who joins |
|---|---|---|---|
| **A. Safe to invite** | Your data rules actually hold | **One egress chokepoint** for every model, embedding and search call. **Redaction gateway v0**: India-ID rules with checksums, the family name list, placeholders restored on the Mac, fail closed. **Provider trust tiers**: `local` / `trusted` / `redact` / `blocked`, with DeepSeek set to `redact` and xAI to `blocked`. Tapas and Sankalpa routed through the gateway. A per-turn egress ledger. Phase 0 carryovers. Pilot hostname moved out of git, with Cloudflare Access in front. `launchd` supervision, backups, an uptime ping. Consent and metric definitions. | Owner (already) |
| **B. Pleasant on phones** | Fast, and easy to use on Android | Token streaming, with the single-agent answer passed straight through (no second model call). A privacy receipt on each message. Hash-bound approvals delivered by Web Push. Care circles. An Activity inbox. A service worker with an offline shell. A mobile design pass. ML redaction (gliner-PII or OpenMed) on typed text and uploads. Hinglish handling and crisis phrases. | First family member |
| **C. Gets errands done** | Real agency | Kriya v0: a perceive → act → settle → verify loop over Playwright accessibility snapshots, with live view and stop from the phone. Outcome contracts for the paths the family actually uses. The cloud browser (self-hosted Steel) for unauthenticated tasks. Android control over ADB at home first; the Companion app for phones away from home comes after that. | All four |
| **D. Measure & iterate** | Evidence, then improvement | The Pariksha benchmark (fixture sites plus a synthetic leak set). Pilot counts and outcomes, never prompts. A weekly review. The remaining phases (6 and 7) of the plan below. | — |

The phases further down are still the backlog. The stages above set the order and the rollout gates.

**Stage A progress (2026-09-24):**
- The privacy gateway now covers every agent call, background learner, embedding, Jev decision and text-to-speech call. It uses the OpenMed and India-ID detectors. It fails closed, keeps an egress ledger at `/privacy/egress`, and has 30+ tests.
- The pilot hostname is out of the code.
- Browser-step and turn-routing Jev are off by default. They added 0.5–2 s per step for advisory output only.
- Logs and generated artifacts are scoped per profile, and the other Phase 0 carryovers are closed (see Phase 0 below).
- Pilot operations are built:
  - **Supervision.** `scripts/install_launchd.sh` installs launchd jobs for the backend, the tunnel, a 2-minute watchdog and `caffeinate -s`. The jobs share `scripts/pilot_env.sh` with `Start Family Pilot.command`.
  - **Backups.** Nightly AES-256-GCM backups of `~/.narad`; SQLite is copied through the online-backup API. The key is kept outside `~/.narad`. Retention is 14 daily plus 8 weekly, and a weekly restore drill checks the latest backup.
  - **Uptime.** A 5-minute check separates app-down from tunnel-down, with an optional healthchecks ping and a waking-hours report.
  - **Pilot metrics.** Local metrics hold counts only: per-turn records hooked in `/chat`, plus `POST /feedback`, `GET /pilot/metrics`, `GET/POST /consent`, voice use and the weekly scorecard.
  - **Consent and metric definitions.** They are in `docs/PILOT_CONSENT_AND_METRICS.md`.
- Cloudflare Access is verified at the origin (`phase-1/cf_access.py`); only the owner's Zero Trust setup remains.

Still open in Stage A:
- the Cloudflare Access application and `.env` values on the owner's side;
- the first green week on the Mac: jobs installed, 99% uptime in waking hours, a passing restore drill and signed consent sheets.

**Stage B progress (2026-09-24), the fast path core:**
- **Token streaming.** Narad and every avatar run with `StreamingMode.SSE`. Text reaches the phone as `text_delta` events, each source through its own `<think>` filter. Thought parts are never sent. `text_reset` drops routing chatter that came before a tool call. `narad_synthesis` still carries the complete reply, so persistence, workflow stages, learning records and Tapas are unchanged.
- **Hand-off, not rewrite.** A single-avatar turn ends with the avatar's own streamed answer (`skip_summarization`): 2 model calls instead of 3. Parallel turns always get a synthesis (guarded in the avatar tool). The `usage` event and the `turn` ledger row fire once per turn and now include the routing call.
- **Deterministic pre-router** (`phase-1/prerouter.py`, `NARAD_PREROUTER=off` disables). It sends five kinds of turn straight to their avatar: bound workflow stages (except Parashurama's), `/teach`, Gurukul check answers, bank-statement CSVs, and document-summary or bare-URL turns. That is 1 model call, with a `route` event in the trace.
- **Streaming-safe privacy.** `StreamRestorer` holds back a placeholder split across chunks. Failover still happens only before the first byte.
- **PWA.** The answer renders as it streams, throttled to animation frames. The final reply replaces it in place.
- **Evidence** (stubbed models, `test_streaming_fast_path.py`):

  | Turn | Model calls | Serial |
  |---|---|---|
  | Hand-off | 2 | 2 |
  | `hand_off=false` | 3 | 3 |
  | Two avatars in parallel | 4 | 3 |
  | Pre-routed | 1 | 1 |

- **Still open:** live time-to-first-token on the Mac (Pariksha), the prompt diet, and caching.

**Stage B progress (2026-09-24), voice:**
- **Sentence-streamed TTS.** Voice mode speaks the streaming answer as it arrives, and the 500-character cap is gone.
  - `src/lib/speech-segments.ts` splits the text at `.` `?` `!` `।` `॥`, line ends and list items. It never splits decimals, times, amounts, common English and Hindi abbreviations, or URLs. A long first sentence may end at a comma after about 120 characters.
  - Code, tables and links become a short "on screen" note. After about 3,000 characters, voice says the rest is on screen.
  - `src/lib/speech-queue.ts` fetches at most two segments ahead and schedules them back to back on one Web Audio clock. The final reply adds only what wasn't spoken, and `text_reset` drops the unspoken segments of that stream.
  - Speaking, tapping the orb or asking again stops playback and aborts in-flight requests. A question asked while the last reply is still streaming stops that reply first.
- **`/voice/tts` for segments.** It keeps a per-profile LRU cache of recent segments and returns raw WAV (`Accept: audio/wav`), a `Server-Timing` header and a 413 above 1,000 characters.
  - Sarvam uses one pooled client with a 5 s read timeout. After a failure it is skipped for 30 s, so the local voice answers at once.
  - `allow_raw` and the egress ledger still see every Sarvam call; cache hits send nothing.
- **Per-profile voice settings** (`profiles/<id>/voice.json`, `GET`/`PUT /voice/preferences`, in voice mode's settings sheet): reply language (en / hi / auto), Hindi script (Devanagari / Roman), and "keep my voice on this Mac".
  - The chat's `reply_language` now carries the script as a subtag: `hi-Latn`, `auto-Latn`.
  - With the Mac-only setting, Sarvam is skipped for that person and the browser's speech recognition is never used.
- **Local STT on the Mac.** The order is Sarvam, then mlx-whisper (`whisper-large-v3-turbo`, Apple Silicon only), then faster-whisper. The language hint is passed through. Local models load on first use and unload after 5 minutes idle.
- **Evidence** (stubs; `test_voice_segments.py`, `test_voice_engine.py`, `test_voice_api.py`):
  - At 40 tokens/s, the first segment is ready 0.3–0.4 s into a typical English, Hindi or Hinglish answer, and 0.75 s into one with a long first sentence.
  - A `/voice/tts` miss adds about 7 ms to the engine's time. A hit returns in about 1 ms.
  - Expected time to first audio on the phone is about 2.5–3 s: speech-to-text 0.5–1 s, the model's first token 1–1.5 s, the first sentence 0.3–0.5 s, and Sarvam TTS 0.3–0.6 s. The settings sheet shows the measured split for the last reply.
- **Still open:** measuring on the Mac and a real Android phone (checklist below), Roman-script Hindi quality in Bulbul and Kokoro (unverified), and a Saaras streaming STT for live partial transcripts.

**Voice check on a phone** (Android Chrome, installed PWA, a trusted Sarvam key, then again with "keep my voice on this Mac"):
1. **Time to first audio.** Ask a one-line question. The first words should play within 3 s of when you stop speaking. Open voice settings and note the split under "Last reply".
2. **Long reply.** Ask for a 10-step plan. Voice starts before the text finishes, plays with no audible gaps, skips any table or code with a short note, and ends with "the rest is on screen" if the reply is very long.
3. **Barge-in.** While Narad is speaking, talk over it. Playback stops within half a second and it listens. Tap the orb mid-reply: it stops. Ask a new question before the old reply finishes: only the new answer is spoken.
4. **Hindi.** Switch the language button to हिन्दी and ask in Hinglish. The reply is written and spoken in Hindi. Switch the script to Roman: the reply is Hinglish in Latin letters. With Auto, answer in the language you spoke.
5. **Autoplay.** Close and reopen the app, open voice mode and ask something without touching anything else. The reply must still play.
6. **Replay.** Tap the speaker on an old reply. It plays at once, and a second tap stops it.

## Stack decisions (2026-09-24): Indic voice, Indic documents, local decision models

These decisions come from two source-checked research passes, one on Sarvam and Indic open models, one on Jev, CUA-S1 and Laya. They put experience and functionality first, then privacy, then cost. Sarvam and Laya numbers are vendor-reported unless marked otherwise. Every Mac figure is an estimate until `scripts/bench_local_stack.py` has been run on the M5 Air.

### Voice (Hindi, Hinglish, English)

| Job | Decision | Why |
|---|---|---|
| **Reply language** | Add a per-profile `reply_language` and `script`, and have the LLM write Hindi (Devanagari or Roman) directly. This fixes the हिन्दी toggle, which today reads *English* text in a Hindi voice. | This is the most audible bug. It needs no new vendor. |
| **TTS** | Stream sentence by sentence: the first sentence plays while the reply is still streaming, and the 500-character cap is removed. Hindi and regional languages use **Sarvam Bulbul v3** once Sarvam is `trusted`. English uses Smallest.ai once it is `trusted`. The offline fallback is Kokoro/VoxCPM, with IndicF5 replacing Kokoro for Hindi. | Voice quality and time to first audio decide whether the family keeps using voice. Speech can't be pseudonymised, so it may only go to `local` or `trusted` providers (enforced since `fd828e6`). |
| **STT** | **Local by default.** Replace CPU faster-whisper `small` with the winner of a bake-off between whisper-large-v3-turbo (MLX/whisper.cpp, Metal), AI4Bharat IndicConformer-600M, and NVIDIA Nemotron 3.5 ASR. Pass a language hint. **Opt-in per profile:** Sarvam **Saaras v4** streaming in `codemix` mode, once `trusted`. Set the browser fallback to `hi-IN`, with `processLocally` where supported. | This closes the biggest accuracy gap while audio stays home. Saaras adds Hinglish-faithful transcripts and live partial results. |
| **Translation** | Local IndicTrans2-dist-200M, only for regional-language documents. No Mayura API. | The LLM already writes Hindi, and the local model is small, fast and private. |
| **Indic LLM** | **No Sarvam LLM for Hindi turns.** | Independent benchmarks place Sarvam-30B and 105B behind open peers of similar size in Hindi, and 30B doesn't fit on the Mac. *Worth benchmarking:* Sarvam Inference serves DeepSeek V4 Flash from India, which could cut round-trip time from India once Sarvam is `trusted` (unverified). |

**Sarvam's trust status.** Its Privacy Policy trains on inputs unless you opt out, and its published terms contradict each other. Sarvam therefore starts in the `redact` tier. You can promote it product by product in `~/.narad/config/provider_tiers.json` once you have done all three of these:
1. opted out of training;
2. set retention to the minimum in the dashboard;
3. received written confirmation that Doc AI files are deleted after each job.

### Documents in Indian languages (lab reports, statements, prescriptions, circulars, forms)

The pipeline has five steps:
1. **Local OCR** with Surya, or PaddleOCR PP-OCRv5 as the lighter alternative. The plan's earlier "Apple Vision" choice doesn't work here, because Apple Vision reportedly can't read Devanagari.
2. **Pseudonymisation** in the privacy gateway.
3. **Typed extraction** by the worker LLM, through a new Matsya tool `extract_fields(path, doc_type)`. It returns each value with its unit, reference range, confidence and bounding box.
4. **Crop confirmation in the UI:** each value appears next to its image crop, and a person confirms it before Rama writes to `health.db` or `finance.db`.
5. **Escalation, per document and with consent:** Sarvam Doc AI `extract` for handwriting and complex tables (about ₹0.5/page) once `trusted`, or Claude/Gemini, which are already `trusted`.

`extract_document` and chat attachments will accept jpg/png/heic/webp and OCR scanned PDFs. The image itself is inlined only for `local` or `trusted` models.

### Local decision layer (replacing cloud Jev)

**Jev today is almost entirely advisory.** Only Tapas scoring and Android admission ever act on its answers. It also cost a cloud round trip on every browser step.

| Contract | Decision |
|---|---|
| `route_turn_v1` | Cloud call off (done). Add a deterministic pre-router now. Later, a fine-tuned **Laya-multilingual** classifier head runs in shadow mode and becomes a "skip the supervisor" fast path only at ≥95% accuracy with ≥50% coverage. |
| `browser_step_v1` | Off the synchronous path (done). **Page state** comes from DOM rules: password fields, captcha frames, dialogs, HTTP errors. **Injection** is caught by **Prompt Guard 2 86M** plus the existing regex. Next-action suggestions are dropped, because the operator model already plans. |
| `browser_verify_v1` | Dead code: delete it. Use deterministic postconditions instead: URL, field value, toast, download. A small VLM yes/no check runs only for high-risk final states. |
| `desktop_admission_v1` / `desktop_verify_v1` | Admission is dropped, since desktop is always confirmation-gated. Verification uses the cua-driver Effect contract plus `verify_state`. |
| `android_admission_v1` | Keep, but run it locally: the existing regex, extended with Hinglish and Hindi side-effect phrases, plus a Laya-multilingual check that can only **tighten** gating. |
| `android_verify_v1` | Drop it. Use Artemis's own verified-mode result instead. |
| `tapas_score_v1` | A local idle-time judge (Gemma E4B), or the cloud through the gateway (already enforced). |

**Plumbing.** Keep the `decision_engine.py` boundary. A Jev-compatible Laya server on loopback is treated as `local` by the gateway. **CUA-S1-4B** enters the Kriya bake-off as a fast action selector over 20 options or fewer. It is research-grade, so it doesn't replace anything yet.

### Performance budget: M5 MacBook Air, 24 GB, fanless

| Component | Residency | Memory (est.) | Target latency (p95) |
|---|---|---|---|
| macOS, apps, Narad backend | always | ~6.5 GB | — |
| OpenMed PII + rules | always | 3–3.5 GB | ≤ 150 ms per new message (cached per line) |
| Laya-multilingual (MLX) | always | ~1 GB | ≤ 20 ms per decision |
| Prompt Guard 2 86M | always | ~0.35 GB | ≤ 50 ms per page |
| STT engine (bake-off winner) | on demand, 5 min idle unload | 0.7–1.6 GB | final transcript ≤ 1 s after speech ends |
| OCR (Surya/Paddle) | on demand | 0.2–3 GB | ≤ 10 s per page |
| Chromium | while browsing | 1–2 GB | — |
| **Heavy slot, one at a time:** Gemma 4 E4B, Qwen3-VL-8B (hard OCR / VLM verify) or Qwen3.5-4B + CUA-S1 | on demand | 3–8 GB | — |
| Free headroom | — | ≥ 3 GB | — |

**Experience targets:**
- Before the LLM starts, a turn's added overhead is ≤ 350 ms.
- A browser step's added overhead is ≤ 150 ms (it was 0.5–2 s).
- Time to first audio on the phone is ≤ 3 s.

**Eviction policy:**
- `OLLAMA_MAX_LOADED_MODELS=1` and `OLLAMA_NUM_PARALLEL=1`.
- Gemma context capped at 16K, with `keep_alive` at 5 m.
- MLX sidecar models unload after 120 s idle.
- A model is refused if free memory is less than its size plus 2 GB, and the call degrades to deterministic code or a `trusted` cloud provider.

**Thermals:**
- Background jobs (Tapas, embeddings, consolidation) run only on AC power, with nominal `pmset -g therm` and no turn in the last 60 s.
- Under thermal pressure, `max_tokens` is capped and fallbacks move to the cloud where policy allows.

**How these land in the stages:**
- **Stage A (done):** Jev, TTS, Sarvam and Smallest behind the gateway.
- **Stage B:**
  - reply language/script (done);
  - sentence-streamed TTS (done);
  - STT bake-off and swap (mlx-whisper tier done; the bake-off is still open);
  - the deterministic pre-router;
  - the `bench_local_stack.py` run on the Mac.
- **Stage C:**
  - local OCR → `extract_fields` → crop confirmation;
  - Laya and Prompt Guard as the local decision layer;
  - CUA-S1 in the Kriya bake-off;
  - Sarvam Doc AI escalation.
- **Stage D:** evaluation sets:
  - about 150 family audio clips (WER/CER, names/numbers/medicines, latency);
  - 40 TTS texts (time to first audio, blind rating);
  - 50 document photos with labelled fields (exact match per field, CER per script, zero PII leaks after the gateway).

Evidence for every finding below was read from the code at `98c23c5` and spot-checked. Latency figures marked *est.* are code-reading estimates; Phase 1 replaces them with measurements.

---

## 1. Where Narad stands

### Keep (strong foundations)

| Strength | Where |
|---|---|
| Runs decoupled from the SSE connection: reattach, heartbeats, thread recovery | `phase-1/server.py` `/chat`, `/chat/attach`; `useAvatara.ts` |
| Durable, declarative workflow engine: SQLite WAL, event log, idempotent schedule events, blocks action stages until approved | `workflow_engine.py` |
| Playwright runtime on its own loop thread, per-profile session ownership, text-first semantic refs, action batching | `phase-8/computer_use_skill.py` |
| Layered safety plumbing: URL policy, target-element risk check, profile device grants, Dharma audit to Karma, `effect_state` for unknown outcomes | `computer_use_skill.py`, `interaction_targets.py`, `dharma.py` |
| Provider failover only before first byte with a circuit breaker (no duplicated side effects); context governor with epochs and rehydration | `phase-1/narad_litellm.py`, `phase-1/context_governor.py` |
| Teach Anything is a real deterministic loop with grading and spaced review | `guru_engine.py`, `learning_workspace.py` |
| Clean baseline: 226 tests pass, ruff clean | `pytest`, `ruff check .` |

### Fix (ranked by impact on task completion, latency, and pilot safety)

**A. Family isolation is not enforced on the server (pilot blocker).**
- `POST /profiles` is unauthenticated and returns a live session, so anyone who finds the public URL can register (`server.py:506`, `server.py:2411`).
- Routes default `user_id="default"` (the owner). The auth middleware only rejects an *explicit* mismatch, so a signed-in family member who omits `user_id` reads the owner's workflow runs, threads, inbox, and attachments (`phase-9/workflow_api.py:84`, `server.py:529-537`).
- Any profile can grant itself desktop control and edit the owner's provider keys. There is no login lockout, tokens cannot be revoked, and `/media/*` (computer-use screenshots) is public.

**B. One person's task freezes everyone.**
- No `tool_thread_pool_config` is set, so ADK 1.32 runs synchronous tools inline on the event loop (`google/adk/flows/llm_flows/functions.py:970`). `computer_use` and `phone_use` block for up to 180 s and 600 s.
- Tapas and Sankalpa are `async def` functions that call synchronous `litellm.completion`, and recall makes 3 blocking embedding calls per turn.

**C. Turns are slow by construction** *(est. 12–45 s to first visible text for a simple question)*.
- There is no token streaming (`runner.run_async` has no `RunConfig`, so streaming is off).
- Even "hi" takes 3 serial LLM calls: route, then avatar, then a supervisor rewrite of the avatar's already-final answer.
- A simple turn costs about 28–35K input tokens: system prompts are 4–12K tokens each because skill markdown is inlined, and Matsya always carries 29 tools.
- The avatar recall budget is 25% of the model's soft target (about 79K tokens on Grok), which is effectively uncapped.
- Uncached status probes spawn subprocesses on every avatar run.

**D. Computer use has low agency.**
- The model sees only the first 7K characters and the first 140 DOM elements of the document. Scrolling never reveals new content. There are no iframes, no shadow DOM, and no screenshots.
- Each action is a full round-trip through Matsya's growing context (5–8K tokens per observation, never pruned).
- `dry_run=True` is the default, which doubles the round-trips.
- There is no settle or verify after a click, and a failed click waits 30 s.
- Grounding sends tag names where Playwright expects ARIA roles.

**E. Consent adds friction without adding safety.**
- `confirmed=True` is self-reported by the LLM and not bound to what the user previewed.
- Confirmation fires on Enter, search submits, "Sign in", and cookie "confirm" buttons. The user approves by typing "yes", which costs a full turn.
- Meanwhile real risks slip through:
  - signed-in `@eN` ref clicks bypass the target check;
  - signed-in `navigate` skips URL validation;
  - `browser_upload_and_submit` hard-codes `confirmed=True`;
  - `_domain_is_blocked` uses `lstrip("www.")`, so `wellsfargo.com` is never blocked.

**F. Privacy is aimed at the wrong layer.**
- Local "PII" work is regex-only (under 50 ms per turn), so it is not what makes Narad slow. It also blocks legitimate work: the input gate refuses any message containing "passport number" (which breaks Travel) or "className" (no word boundaries, which breaks coding).
- The real exposure is where data goes. By default every query, 48K characters of attachments, memory, and health and finance tool output go to DeepSeek (orchestrator and judge) and xAI (workers), and all of it is embedded by Google. No routing decision considers what kind of data a turn carries.

**G. Workflow progress is fiction.**
- A stage completes on any non-empty reply, including "I couldn't do that" (`server.py:2041`).
- The active path is sticky per device in localStorage, so an unrelated question completes the current stage.
- Scheduled check-ins rewind runs and clear pending approvals (`workflow_engine.py:979-995`).
- Chat never suggests or starts the matching path, except Teach.

**H. Cross-device UX is missing its core loop.**
- Approvals appear only on the Paths screen, with no reject or edit option and no notification.
- There is no inbox UI, although Vahana writes one.
- ntfy uses one shared topic, so family members see each other's reminders.
- There is no live view or takeover for computer use from a phone.
- Stop is client-side only, there is no service worker, and iOS voice playback likely fails.

**I. Nothing measures task success.**
- Evals check routing and structure only. There is no multi-step success-rate or latency benchmark.
- CI references a deleted test file, and the full pytest suite is `continue-on-error`.

**J. Adapter bugs against the actual vendor APIs.**
- BrowserSkill: `effect_state` lives under `data.effect_state`, so every uncertain write is read as `none`, and auto-update is on.
- cua-driver:
  - scroll sends `delta_*` where the tool requires `direction`/`amount`;
  - the permission check passes on "❌ not granted";
  - telemetry is on by default;
  - it spawns one process per action.

---

## 2. Design principles

1. **Keep the four-avatar contract; change the execution topology.** The avatars stay as identity, tool scope, and skills. The supervisor becomes a dispatcher that hands off, not a rewriter.
2. **The fastest call is the one you don't make.** Aim for one LLM call for simple turns, stream everything, and keep prompts small with skills loaded on demand.
3. **Agentic work runs in a dedicated task runtime, not in chat.**
   - Each task is durable, streamed, cancellable, resumable, and runs its own perceive → act → settle → verify loop.
   - Chat starts and watches tasks; it does not drive every click.
4. **Protect data by where it goes, not by scanning every message.**
   - Each turn gets a cheap data-class tag based on its context: the workflow, the tools used, the attachment tags.
   - A provider trust tier decides which model may see that turn.
   - Secrets are passed by reference and substituted at the moment of typing. No local ML runs on the hot path.
5. **Consent is a durable object, not a chat message.**
   - An approval is a hash-bound proposal with a visual preview, delivered to whichever device the person holds.
   - A standing approval covers an approved plan envelope ("book ≤ ₹6k, IndiGo, 12 Oct"), so work proceeds without a prompt per click.
6. **No lock-in.** Use open protocols (AG-UI event names over our SSE, MCP for tools), LiteLLM for models, and self-hosted Web Push. Every vendor sits behind a Narad interface and must win a benchmark before it becomes a default.
7. **Measure before and after every phase.** Pariksha scorecards gate each phase.

---

## 3. Target architecture

```text
 Phone / laptop PWA ── SSE (AG-UI-compatible events) + Web Push ──┐
                                                                  v
 FastAPI boundary: server-derived profile identity, owner roles, signed media
        |
        v
 Turn planner (fast path) ── pre-router (attachments, URLs, /teach, bound path)
   |  direct answer (1 call, streamed)
   |  hand off to one avatar (streamed; supervisor skips summarization)
   |  fan out ≤3 avatars + synthesis (only when needed)
   `  start a Kriya task (agentic work) ───────────────┐
                                                        v
 Trust router: data class x provider tier    Kriya task runtime (durable, per-surface locks)
   local | trusted | open                      perceive -> decide -> act -> settle -> verify
   egress ledger per turn                       |  Surfaces (one protocol, one Effect contract):
                                                |   isolated browser (Playwright, a11y refs)
 Anumati approvals: hash-bound proposals,       |   signed-in browser (BrowserSkill daemon)
   envelopes, preview frames, push to device    |   desktop (cua-driver MCP session)
                                                |   Android (Artemis async task)
 Kosha vault: secrets by reference,             |   [optional] cloud browser, benchmarked
   substituted inside surfaces                  `-> live frames + takeover + handoff to phone

 Workflows v2: outcome contracts + evidence, intent -> path, per-thread binding
 Pariksha: fixtures + oracles + scorecards for latency, success, friction, safety, egress
```

New names follow the house convention. Each is always shown with its English gloss:
- **Pariksha**: benchmark
- **Kriya**: task runtime
- **Anumati**: approvals
- **Kosha**: vault

---

## 4. Reference repos: verdicts

| Repo | Verdict | What we take |
|---|---|---|
| **CopilotKit/openmuse** (MIT, 0.1.0-alpha, open alternative to Meta's Muse) | **Borrow patterns; no dependency.** Since 2026-09-23 it refuses to start without a `CPK_INTELLIGENCE_API_KEY` (CopilotKit cloud thread persistence), which is vendor lock-in. Its browser agent only reads pages. | Approval records bound to a hash; idempotency keys from thread, message, and args; durable task states including `waiting_approval`; tool-name → UI card rendering; the phone-friendly screenshot takeover console (`apps/server/src/browser-console.ts`); SSRF guard and pinned-IP egress; AG-UI event vocabulary. |
| **Tencent/BrowserSkill** (MIT, v0.3.1, local-only, no telemetry) | **Keep as the signed-in surface; fix the adapter; adopt its eval corpus.** | Read `data.effect_state`. Set `BSK_AUTO_UPDATE=off` and pin the version. Talk to the daemon socket instead of spawning N+4 processes per call. Use `final_url`, `next_cursor` paging, and `capture_id` point-clicks. Route `request-help` to phone takeover. Run `evals/browser` against Narad through a CLI shim. |
| **trycua/cua** (MIT, cua-driver 0.28.x) | **Keep cua-driver, switch to a persistent `cua-driver mcp` session, and turn telemetry off.** Put cua-agent loops, including open-weights UI-TARS and OpenCUA, into the bake-off. | Accessibility tree plus `element_token` grounding; background delivery (no focus stealing); `verify_state`; the canonical Effect contract (`confirmed / partial / unverifiable / suspected_noop / refused`); cua-bench (`cua-bench-basic`) for desktop evals. |

---

## 5. Phases

Each phase is one reviewable PR (or a small stack). Exit gates are hard: a phase is done only when its gate passes.

### Phase 0: Pilot safety floor *(blockers; no product decisions required)*

> **Status: done (`e4c8d9a`).** 329 tests pass (baseline 226), 1 is skipped; ruff is clean; the frontend builds. A live strict-mode smoke test confirmed the auth boundaries. An adversarial review found 24 issues and all 24 were fixed. The most important was a pre-existing hole: family profiles could reach host secrets through the shell tools, which are now owner-only.
>
> **Carryovers (not blockers): all closed (2026-09-24).**
> - *Closed:* executor, Imagen and Veo output was readable by any signed-in profile that knew the run id. New output lands in `artifacts/runs/<profile_id>/<run_id>/`, and `/media` serves it only to that profile. Top-level run folders from before are served only to the owner.
> - *Closed:* `/sutras`, `/andon/log` and `/karma` were not filtered by profile, nor was the audit part of `/search`. Records now name their profile when written. These endpoints show each profile only its own records, and so do `/sankalpa`, `/costs`, `/audit` and `/provenance`. The owner sees everyone's with `?scope=all`, without other profiles' text (counts, kinds, times, and learned rules without the questions behind them), and sutra accept/revert is owner-only. An audit of query, body and path ids found one more leak, now closed: a learning workspace id such as `../<profile>/<id>` could open another profile's learning folder.
> - *Closed:* there was no UI for changing your own PIN or signing out all devices. System → Profile in the app now does both. The owner can also reset a member's PIN there and sign them out.
> - *Closed:* the Security Floor section of `AGENTS.md` was out of date; it now describes the current floor.
> - *Closed:* the signed-in browser did not re-check the URL after redirects. Both browsers now check where a page lands after every navigation, click or history move. On a refused address the signed-in browser goes back, or closes the session if it cannot; the isolated browser blanks the page or closes the tab. Either way the result says it was refused.

- **Identity**
  - Every route derives `user_id` from the authenticated session through one FastAPI dependency. Remove all `user_id="default"` defaults.
  - Profile creation requires the owner or a single-use invite code.
  - `/profiles/bootstrap` works only when zero profiles exist and only from loopback.
  - Owner-only guards on `/connections`, Kunji, and desktop and phone grants.
  - Login lockout with backoff.
  - A per-profile session epoch, bumped when the PIN changes, revokes old tokens.
  - `/media` uses signed, profile-scoped URLs.
- **Event loop**
  - Add `RunConfig(tool_thread_pool_config=…)` to the supervisor and avatar runners.
  - Move Tapas and Sankalpa off the loop.
  - Make embeddings non-blocking and share one client.
  - Cache the runtime status probes with a TTL.
- **Action-safety bugs**
  - Fix the `lstrip("www.")` domain check.
  - Validate URLs on signed-in navigate, and confine download paths.
  - Remove the hard-coded `confirmed=True`.
  - Add word boundaries to the Dharma input regex so "className" stops tripping it; the "passport number" block is removed in Phase 3.
- **Vendor correctness**
  - bsk: read `data.effect_state`; set `BSK_AUTO_UPDATE=off`.
  - cua: fix scroll arguments; use `permissions status --json`; send an explicit `delivery_mode`; set `CUA_DRIVER_RS_TELEMETRY_ENABLED=false`; report versions in `/capabilities`.
- **Workflow integrity**
  - Scheduled check-ins never rewind completed stages or clear pending approvals.
  - Workflow binding is per thread, not sticky per device.
- **Privacy leak:** give each profile its own ntfy topic until Web Push lands.
- **CI:** remove the reference to the deleted test and make the full pytest suite blocking.

**Gate:**
- A regression test for every hole above: 401/403 cases, profile-omission cases, and the domain and URL cases.
- A concurrency test: a second chat keeps streaming heartbeats while a slow sync tool runs.
- CI green.

### Phase 1: Pariksha, the benchmark harness and baseline

- **Task schema:** `workflow, surface, prompt, fixture, oracle, safety_expectations, data_class`.
- **Runner:** drives `/chat` over HTTP and records:
  - time to first event and first token, and total time;
  - LLM calls and input/output tokens;
  - tool calls;
  - confirmations requested versus required;
  - unsafe actions;
  - egress by provider tier.
- **Local fixture sites** (deterministic, side-effect-free, with server-side oracles):
  - flight search → booking with a payment step
  - job application with resume upload
  - clinic appointment
  - bank statement download
  - visa/document form
  - cookie walls, infinite scroll, iframes, and a prompt-injection page
- **Coverage:** about 30 tasks across the six workflows, plus a chat latency set (simple, research, tool).
- **Adapters:**
  - BrowserSkill `evals/browser` through a `narad-eval` CLI shim.
  - A cua-bench `NaradAgent` for `cua-bench-basic`.
- **Output:** a committed baseline scorecard. The offline, stubbed-model subset runs in CI; live runs happen on your Mac with your keys.

**Gate:** one command produces a scorecard for today's stack. That becomes the baseline every later phase is measured against.

### Phase 2: Fast path

- **Streaming**
  - Run avatars with `StreamingMode.SSE` and forward `text_delta` events.
  - The chat renders tokens as they arrive, with an inline activity timeline.
- **Handoff, not rewrite**
  - A single-avatar turn sets `skip_summarization`, so the avatar's streamed answer is the final answer.
  - Synthesis runs only when more than one avatar is involved.
  - The planner answers trivial turns directly.
- **Deterministic pre-router** skips the supervisor call for attachments by type, URLs, `/teach`, and path-bound turns.
- **Prompt diet**
  - Skills load on demand: the prompt carries an index, and a `load_skill` tool fetches the rest.
  - Tools are subset per intent.
  - Target: system prompts of 4K tokens or less.
- **Caching:** use explicit prompt caching where the provider supports it (Anthropic `cache_control`, Gemini cached content), and keep a stable prefix for implicit caches.
- **Memory**
  - One recall per turn, shared between supervisor and avatar.
  - A cap of about 1.5K tokens.
  - Drop keyword-picked "commitments", zero-relevance sutras, and duplicated rows.
  - A persisted vector index, so no JSON manifest is re-parsed on each call.
- **Hygiene**
  - Tighten learning-mode detection: "what is …" alone should not open a workspace.
  - Set low reasoning effort for the router.
  - Sample Tapas judging and run it in the background.

**Gate** (Pariksha, against the baseline):

| Measure | Target |
|---|---|
| Simple question: time to first token, p50 | 2 s or less |
| Simple question: LLM calls | 1 |
| Input tokens per simple turn | 8K or less |
| Research question: first progress event | 1 s or less |
| Quality score | no regression |

### Phase 3: Trust, consent, and the vault (trust router, Anumati, Kosha)

- **Provider trust registry (in Kunji)**
  - Each endpoint records retention, training use, jurisdiction, and contract type.
  - Tiers are `local`, `trusted`, and `open`. You confirm the classification.
- **Data-class router**
  - Each turn is tagged from its context: Health or Finance workflow, finance.db/health.db/Gmail tools, attachments marked private, profile policy.
  - Sensitive classes route only to `trusted` or `local`.
  - Fallback chains are filtered by tier, so a sensitive turn never silently falls back to an `open` provider.
  - A per-turn egress ledger is visible in System → Trust.
- **Kosha vault**
  - An encrypted, per-profile store for identity, address, and payment items.
  - The model sees handles like `{{kosha:passport.number}}`. The surface substitutes the real value when typing, and observations mask it by exact match.
  - The Dharma PII input block is removed: Narad offers to save the value to the vault instead of refusing.
- **Anumati approvals**
  - A durable `ActionProposal` holds a hash of surface, action, target, and arguments, plus a screenshot, a plain-language summary, a risk class, and an expiry.
  - Proposals are approved, rejected, or edited through the API.
  - Executors refuse unless the proposal is approved and its hash matches. The LLM can no longer self-confirm.
- **Risk policy v2**
  - Semantic classes decide what needs approval:
    - read: no approval
    - reversible input: no approval
    - benign submit (search, filters, cookie banners): no approval
    - commit (send, pay, book, apply, delete, account change): needs approval
  - A standing envelope lets in-plan steps proceed without a prompt each time.

**Gate:**
- Pariksha safety suite: 0 unsafe commits, at most 1 approval per committing task, 0 approvals on benign tasks.
- Egress policy tests show that no sensitive-class turn reaches an `open` provider.

### Phase 4: Kriya, the computer and phone use engine

- **One Surface protocol** (`observe / act / verify / frame / handoff`) with the canonical Effect contract.
- **Task runtime**
  - An avatar calls `start_task(goal, surface, envelope)`.
  - Kriya runs the inner loop with a dedicated operator model and streams progress.
  - Tasks are durable, resumable, and cancellable.
  - Per-surface locks and a queue: desktop and phone are exclusive; browsers run per profile.
- **Perception v2 (Playwright)**
  - Viewport-scoped accessibility snapshot with stable refs and real ARIA roles.
  - Scroll paging, and coverage of iframes and shadow DOM.
  - A screenshot goes to the vision model only when the accessibility tree is poor or verification is ambiguous.
- **Act well**
  - Wait for navigation or a quiet DOM instead of fixed sleeps.
  - 5 s actionability timeouts instead of 30 s.
  - Check expectations after each action, then re-ground and retry once.
  - Keep only the latest observation in full; older ones become one-line summaries.
  - Persist per-profile `storage_state` (with consent) and use a real Chrome user agent.
  - `browse_url` reuses the shared runtime.
- **Signed-in:** talk to the bsk daemon over its socket; handle `request-help` as phone takeover.
- **Desktop:** a persistent `cua-driver mcp` session using the accessibility tree plus `element_token`, background delivery, and `verify_state`.
- **Android:** Artemis becomes an async task handle with progress, cancel, and screenshots, and no more orphaned `running` tasks.
- **Bake-off in Pariksha**
  - Backends: Narad Kriya / BrowserSkill / cua-driver / cua-agent.
  - Operator models, all through LiteLLM: Claude, OpenAI, Gemini, and Grok computer-use-capable models, plus open-weights UI-TARS/OpenCUA.
  - The winners become the defaults for each surface.

**Gate:**

| Measure | Target |
|---|---|
| Fixture-suite success rate | 85% or more |
| Step latency, p50 | 3 s or less |
| Tokens per task versus baseline | down 50% or more |
| Unsafe actions | 0 |

### Phase 5: Cross-device experience

- **Chat**
  - An activity timeline plus generative tool cards: a browser card with a live frame, an approval card with Approve/Reject/Edit, and result cards.
  - AG-UI-compatible event names over our SSE. This uses the protocol without the CopilotKit runtime.
- **Activity surface:** a per-profile inbox of running, waiting, and done tasks, with badges.
- **Web Push:** self-hosted VAPID keys and a service worker, subscribed per profile and per device, for approvals, handoffs, and nudges. It replaces ntfy.
- **Live view and takeover:** a 1–2 fps frame stream plus tap/type/scroll forwarding, so login, 2FA, and CAPTCHA can be finished from the phone.
- **Handoff to device:** where policy says human-only (for example payment), Narad prepares everything and sends a deep link or UPI intent for the person to finish on their own phone.
- **Stop and queue:** stop cancels on the server; follow-ups queue.
- **PWA shell**
  - Offline "host asleep" page.
  - Self-hosted fonts.
  - iOS audio unlock on user gesture.
  - Fixes to mobile typography and scroll-to.

**Gate:**
- Approve-from-phone round trip of 10 s or less after the push arrives.
- Takeover works on iOS Safari and Android Chrome over the tunnel.
- Installable PWA with a working offline shell.

### Phase 6: Workflows v2, outcome-driven paths

- **Real stage completion**
  - Every stage declares `done_when`.
  - The agent returns a structured `stage_result` (`done | needs_input | blocked | in_progress` plus evidence). Free text never completes a stage.
- **Intent → path:** the planner recognises the six paths and suggests or resumes the right run, bound to the thread.
- **Mobile-first intake:** conversational, one or two questions at a time, prefilled from profile, memory, and Kosha. Files are uploaded from the phone, not typed as Mac paths.
- **Per-path tooling:**

| Path | What gets built |
|---|---|
| Career | Application tracker table and a Gmail reply watch |
| Health | Lab-report extraction into health.db, per-profile reminders (today they fire only for the owner), and the logging tools wired to stages |
| Travel | Structured search, a price watch that does not rewind the run, and a trip pack |
| Finance | Statement upload, and budget and goal tools on stages |
| Documents | Versioned artifacts and export |
| Teach | Mostly unchanged |

**Gate:**
- Each path completes end-to-end on its fixtures with evidence.
- 0 false stage completions.

### Phase 7: Pilot operations and rollout

> **Status (2026-09-24): operations and metrics backend built.**
> - **Built:** supervision (backend, tunnel, watchdog, the `pmset` advice), encrypted nightly backups with a weekly restore drill, the uptime check and report, and private pilot metrics with feedback, consent and the weekly scorecard. See README, "Running the pilot day to day", and `docs/PILOT_CONSENT_AND_METRICS.md`.
> - **Still open:**
>   - the feedback control and the consent screen in the app;
>   - launchd units for cua-driver and Artemis;
>   - a self-service export and delete for each person;
>   - the first 7-day window on the Mac.

- **Supervision:** launchd KeepAlive units for the backend, tunnel, cua-driver, and Artemis, plus a watchdog and `pmset` sleep policy.
- **Backups:** encrypted nightly backups of `~/.narad`, with a restore drill.
- **Family onboarding:** invite links, and a device list with revoke.
- **Private pilot metrics (local only):** task success, time-to-done, approvals, abandonment, and a feedback control on every task card. They feed a weekly scorecard.
- **Staged rollout:** you in week 1, one or two family members in week 2, then everyone.

**Gate:**
- 99% or more uptime during waking hours over 7 days.
- The restore drill passes.
- The weekly scorecard is trending up.

---

## 6. Headline targets

| Metric | Today (audit) | Target |
|---|---|---|
| Serial LLM calls, simple question | 3 + background judge | 1 |
| Input tokens, simple question | ~28–35K (est.) | ≤ 8K |
| First visible answer text | after the full pipeline (no streaming) | ≤ 2 s p50 |
| Computer use per action | full avatar round-trip, 5–8K-token observations, unpruned | inner loop, ≤ 2K-token observations, pruned |
| What the operator can see | first 7K chars + first 140 elements | viewport-scoped a11y with paging + vision on demand |
| Approvals | self-reported by the LLM; fire on search/Enter/"Sign in" | hash-bound; only commit-class actions |
| Workflow stage completion | any non-empty reply | evidence-backed |
| Family isolation | owner data reachable by omitting `user_id` | identity derived on the server everywhere |
| Sensitive-data egress | health and finance go to whichever brain is configured | only `trusted` or `local` tiers, logged per turn |

## 7. Owner decisions (2026-09-23)

1. **Providers.**
   - **xAI/Grok is out.** It is removed from every routing and fallback chain in Phase 0.
   - **DeepSeek stays, on one condition: a local OpenMed model strips PII before any turn reaches it.** Phase 3 adds this as a pseudonymisation gateway:
     - It runs only for providers marked `redact-required`. It does not run on every message.
     - Attachments are redacted once, when they are uploaded.
     - Placeholders such as `<PERSON_1>` are mapped back to the real values on the Mac.
     - India-specific identifiers need their own recognisers: Aadhaar (with its Verhoeff checksum), PAN, UPI, IFSC, and +91 phone numbers.
   - **All other vendors must publish clear data-handling terms:** no training on your data, bounded retention, and zero retention where available.
   - **Alternatives to DeepSeek with better data handling at similar prices** (including whether "GPT 6 Luna" exists) are being researched with source-checked pricing and policies. The routing table will be filled in from that research.
2. **Cloud browser: yes, for unauthenticated tasks.** Phase 4 adds a `CloudBrowser` surface with these rules:
   - It never receives credentials, cookies, or vault values.
   - Session recording is off.
   - Each profile gets its own isolated session.
   - The vendor is chosen by the research and confirmed in the Pariksha bake-off, with a self-hostable option preferred.
3. **Pilot: 4 people, all on Android phones, plus the host Mac.**
   - Controlling Android phones is a first-class part of Phase 4. The phones are usually away from the Mac's Wi-Fi, so the transport (an outbound on-device companion app, or ADB over a private network) is chosen from the research.
   - Web Push and PWA install target Android Chrome first.
   - A single non-blocking server process is enough at this size.
