# Narad — Guru Teach Panel v2 & First-Run Onboarding Plan

*Drafted 2026-07-08; revised 2026-07-09. Companion to AUDIT-AND-ROADMAP.md (slots into M5/M6). Four tracks: **Guru** (G1–G7) — the teach artifact panel reborn as an actual teacher (G1–G4 ✅ shipped); **Pratham** (O1–O7) — the first-install experience; **Sopan** (S1–S4) — spend- and hardware-tiered access so anyone can run Narad; **teach-skill completion** (Part D) — closing the gap between the Guru engine and Krishna's actual teaching in chat.*

---

## Part A — Guru: teach panel v2

### A.1 Honest gap analysis (verified in code)

| # | Gap | Evidence |
|---|-----|----------|
| 1 | **Artifacts are templates, not teaching.** Flashcards are generic stubs — the "back" of every card is an instruction to yourself ("Explain X in one precise sentence"), not an answer. Concept maps are a fixed 5-node skeleton (intuition/mechanics/examples/pitfalls/compare) regardless of topic. | `learning_workspace.py` `_flashcard_doc`, `_concept_map_doc` |
| 2 | **Iteration is regex, not understanding.** `update_learning_artifact` string-matches "remove/delete" and appends template cards via `_extract_focus_phrase` regex. No LLM in the loop. | `learning_workspace.py:529–569` |
| 3 | **No decomposition.** A topic is one monolith. Nothing breaks "attention mechanisms" into prerequisite atoms. | `ensure_workspace` — topic → single MISSION.md |
| 4 | **No learner model.** Nothing tracks what the user grasped, struggled with, or should review. Records are append-only lesson logs. | `learning-records/*.md` |
| 5 | **No panel.** Learning workspaces have a full REST API (`/learning/*`) but no dedicated UI surface. Teach detection is 8 regex patterns on the query. | `_TEACH_PATTERNS`; frontend grep: no consumer |

### A.2 Design principles

1. **Atomic decomposition.** Every topic becomes a small DAG of concept atoms with explicit prerequisites. You never teach atom N before its parents are mastered.
2. **The ELI5 ladder.** Every atom carries four rungs: 🧒 *analogy* (a five-year-old's world: sandwiches, playgrounds, mail carriers) → 📖 *plain English* (no jargon) → 🎯 *precise* (correct terms, one paragraph) → 🎓 *formal* (notation/definition). The learner climbs; the teacher never starts at the top.
3. **Teach like a teacher, not an encyclopedia.** One atom per exchange. Analogy first. End every explanation with one check question. Wrong answer → *different* analogy, never the same words louder.
4. **Grounded, gated.** Explanations cite workspace RESOURCES.md; Tapas hallucination gate reviews generated artifacts before they persist (reuse the existing critique path).
5. **Distill outward.** Smriti gets only outcomes ("user mastered backprop chain rule, struggled with Jacobians") — the workspace holds the mess, exactly as today's design intends.

### A.3 Milestones

*Status 2026-07-09: G1–G4 shipped (`guru_engine.py`, LLM artifact generation/revision with template fallback, learner_state + grading, GurukulTab + MaharishiAvatar). G5 spec'd below, unwired. What's still missing to make Krishna actually teach in chat is Part D.*

**G1 — Concept decomposition engine** ✅ (backend, ~2 days)
- New `guru_engine.py`: one Krishna LLM call: topic + workspace packet → schema-validated `syllabus.json`:
  ```json
  { "atoms": [ { "id": "dot-product", "name": "Dot product as similarity",
      "prerequisites": [], "eli5": "...", "plain": "...", "precise": "...", "formal": "...",
      "misconception": "...", "check": {"q": "...", "good_answer": "..."} } ] }
  ```
- Validation: DAG acyclic, ≤12 atoms, all four rungs non-empty; retry once on schema failure, fall back to single-atom syllabus.
- Stored in the existing workspace dir; `build_workspace_packet` extended to include syllabus summary.
- Endpoints: `POST /learning/workspaces/{id}/syllabus` (generate/regenerate), `GET` included in `load_workspace`.

**G2 — LLM-backed artifact generation & iteration** ✅ (backend, ~2 days — *do first, biggest payoff*)
- Replace `_seed_artifact_doc` templates: generation prompt = workspace packet + syllabus + artifact type → real cards (fronts *and* backs), real concept maps (nodes from atoms, labeled edges).
- Replace regex iteration: `update_learning_artifact` sends {current doc, instruction} → LLM returns full revised doc (JSON), diffed and versioned. Keep `version` increments; add `history/` per-version files so iteration is undoable.
- Tapas gate: generated/revised docs pass the existing critique before write; failures → `blocked_critique` karma event (schema already supports it).
- Keep template path as offline fallback when no provider is available (local-first honesty).

**G3 — Teacher persona & mastery loop** ◐ (backend, ~2 days — *engine half shipped: learner_state + grading exist; the Krishna prompt injection and in-chat loop did NOT land — completed by Part D/G6*)
- Teaching prompt fragment injected into Krishna for learning queries: current atom, its rung content, learner state, the three rules (one atom, analogy first, check question).
- New `learner_state.json` per workspace: `{atom_id: {status: untaught|shaky|mastered, attempts, last_reviewed}}`. Krishna's check-question outcomes update it (self-reported grading via a second cheap LLM call, DS_FLASH).
- `is_learning_query` upgraded: regex stays as fast path; router fallback asks Narad's dispatch model (already in the loop) to flag teach intent.

**G4 — Gurukul panel** ✅ (frontend, ~3 days)
- New dashboard tab **Gurukul** (गुरुकुल) beside Tapasya — Tapasya stays sutra/evolution; teaching gets its own home.
- Layout (reuses split-pane + existing visual language):
  - **Left — syllabus tree**: atoms as nodes, mastery-colored (untaught grey / shaky marigold / mastered green), prerequisite edges. Click = jump to atom.
  - **Center — lesson canvas**: current atom with rung selector (🧒 📖 🎯 🎓 toggle), check question, answer box, remediation thread.
  - **Right — artifact rail**: flashcards (flippable) and concept map (render nodes/edges; d3 or plain SVG), an "iterate" input posting to `/learning/artifacts/{id}/update`, version stepper (v3 ◂ ▸).
- All fields rendered defensively (lesson of the Karma panel): optional-chain everything, per-panel error boundary so one bad artifact never kills the harness.

**G5 — Review & spaced repetition** (~2 days)
- SM-2-lite scheduling on `learner_state` (`shaky` → 1d, `mastered` → 3d/7d/21d).
- `kala_scheduler` computes due reviews; Vahana inbox delivers "3 atoms due in *Transformers*". Tapping opens Gurukul in quiz mode (check questions from due atoms).
- Session end → distilled outcome record + Smriti episode capture (existing `capture_episode`).

**Eval (extends M4 CI):** golden tasks for: syllabus schema validity + acyclicity; every atom has 4 rungs; artifact update honors an add and a remove instruction; teach response contains exactly one check question. Add to `run_golden_tasks.py` as a `guru` group.

---

## Part B — Pratham: first-run onboarding

### B.1 Current state (verified)

Install = git clone + hand-edit `.env` + `dev.sh`. `capabilities` already reports `local_ready: {frontend_transport_agnostic: true, local_model_runtime: <ollama detected>, desktop_packaging: false}`. Provider availability = env-var key checks (`model_registry.provider_available_for_model`). Local models already route (`detect_provider` → "local" for `ollama/...`). No onboarding UI exists anywhere in the frontend. Cost ledger prices `ollama/` at $0.

### B.2 What ships in the box vs. optional

| In the installer | First-run download (optional but suggested) | Later / à la carte |
|---|---|---|
| Harness + 4 avatars + supervisor | **Gemma 3 QAT gguf** sized to RAM (see O2) | Larger local models |
| Frontend (built, served same-origin) | — | Voice (STT/TTS) |
| Embedded **llama.cpp server binary** (~20 MB, per-arch) | Small local embedding model (~80 MB) for Smriti | Vision model |
| SQLite/FTS5 stores, Smriti, Karma, Tapas | — | Extra tool packs (IMAP triage, calendar) |

### B.3 Milestones

**O1 — Desktop packaging** (~1 week)
- **Recommendation: Tauri over Electron** — ~10 MB shell vs ~200 MB, native webview, better signing story; frontend is already plain Vite/React. Backend frozen with PyInstaller (single `narad-server` binary), Tauri sidecar-launches it and the llama.cpp server, then loads `localhost:8000` same-origin (M1 work already made this transport-agnostic).
- Flip `desktop_packaging: true` in the runtime contract when running packaged; menu-bar tray icon, launch-at-login toggle.
- Auto-update channel (Tauri updater) + signed builds. Fresh-machine dry run (roadmap F1) becomes the packaging CI test.

**O2 — Bundled local brain (Gemma 4)** (~4 days) — *rewritten 2026-07-09 for the Gemma 4 family (released June 2026, QAT checkpoints 2026-06-05, **Apache 2.0** — the old license-acceptance blocker is gone).*
- **Still do not bundle weights in the installer** — not for license reasons anymore, but because 3–7 GB installers kill conversion. Bundle the llama.cpp runtime; download weights on first run with consent + progress + checksum + resume. Ollama tags exist for the dev path (`ollama/gemma4:12b-it-qat`, `ollama/gemma4:e2b-it-qat`).
- **Gemma 4 12B QAT is the flagship local brain** (~7 GB at Q4; runs on 8 GB RAM at 4-bit with reduced context; 256K context; encoder-free multimodal). Quantization ladder chosen by the S1 tier engine (Part C): Q4 default, Q8 (~14 GB) on big machines, tighter quants on small ones.
- **Edge deployment = Gemma 4 E-series.** E2B (~3 GB) / E4B (~5 GB) use Per-Layer Embeddings for on-device efficiency, run fully offline on phones/old laptops (Qualcomm/MediaTek-optimized; a mobile-specialized quant format ships with QAT). This is the official edge rung under the 12B — not a downgrade path we invent.
- New provider string `narad-local/<model>` → bundled llama-server port; `detect_provider` gains one branch; `AVATAR_MODELS` defaults to it when no cloud key exists. Everything else (context windows, cost ledger $0, capabilities flag) already handles "local"; pin `narad-local/` free in `cost_ledger.PRICES`.
- Honest expectation-setting in UI: "Local brain: private, free, slower. Add a cloud key anytime for the fast lane." Hybrid default once a key exists: Flash-tier avatars local, Pro-tier cloud (one-line env change already supported).

**O3 — Complementary web-search grounding tier** (~3 days + a tiny hosted service)
- Grounding is what makes a local Gemma trustworthy — Matsya must be able to cite even with zero keys.
- **Tier 1 (default): Narad search relay.** A minimal hosted proxy (Brave/Tavily under the hood) with per-install anonymous token, free quota (e.g. 50 searches/day), query-only (no page content through the relay — fetch happens locally). This is the only hosted component in the product; state that loudly.
- **Tier 2: BYO key removes the cap.** Brave free tier (2k/mo) is the easiest to guide users into; also accept Tavily/Serper. Key stored via O5.
- Tapas hallucination gate ties factual teach/answer claims to search citations when local model is active.

**O4 — First-run wizard** (frontend + small backend, ~1 week)
Five screens, served by the packaged app on first launch (state → `~/.narad/onboarding.json`; `capabilities` drives a post-setup checklist chip row on the dashboard):
1. **Namaste** — name, what Narad should call you, workspace folder pick.
2. **Choose your brain** — three cards: *Local only* (recommended, shows RAM-detected model + download size), *Hybrid*, *Cloud*. Download runs in background while wizard continues.
3. **Powers** — permission defaults (see O6), each a plain-language card with a toggle, everything off/read-only by default.
4. **Connections** (skippable) — the Kunji flow (O5) for any cloud keys or search key.
5. **First lesson** — three suggested starter prompts, one of which is a teach prompt that opens Gurukul ("teach me how Narad's memory works" — self-demonstrating).

**O5 — Kunji: key & subscription management for non-tech users** (~1 week)
The .env file is the single biggest non-tech blocker. Principles: never show a bare text field first; validate instantly; store in the OS keychain.
- **Guided connect per provider:** a "Connect DeepSeek/Gemini/Claude/OpenAI" card opens the provider's key page at the exact right URL, with a screenshot-annotated 3-step overlay; user pastes → we auto-detect provider from key prefix (`sk-ant-`, `AIza`, `sk-`, `dsk-`), fire a 1-token test call, show ✓ + available models + est. price/1M from the cost ledger table.
- **Subscription sign-in — updated 2026-07-09 to match current policy:** raw consumer OAuth tokens (Claude Free/Pro/Max) are **banned** in third-party apps; but since 2026-06-15 Claude Pro/Max/Team/Enterprise plans include a monthly **Agent SDK credit** that explicitly covers third-party apps built on the Claude Agent SDK. So Narad's "Sign in with Claude" = an Agent SDK-based provider adapter drawing on plan credits (ToS-compliant), not token reuse. Build the adapter abstraction so other vendors slot in as they open equivalent programs; ChatGPT subscription access has no equivalent sanctioned path today → OpenAI stays BYO-key only.
- **Storage:** macOS Keychain (`security`), Windows Credential Manager, libsecret on Linux — via `keyring` lib in the frozen backend. `.env` stays as the power-user escape hatch; a one-time importer migrates existing `.env` keys into the keychain.
- **Management UI:** Settings → *Connections*: one card per provider — status dot, masked key, month-to-date spend (cost ledger already tracks it), test button, disconnect. Key never rendered after save.

**O6 — Default permission rings for computer tasks** (~4 days)
Three rings, enforced in the ADK tool layer, every grant/deny audited to the karma ledger (`entity_type: "permission"` — schema already supports it):
- **Ring 0, always on:** read workspace folder, local inference, memory writes under `~/.narad`.
- **Ring 1, ask once per capability:** web fetch, calendar read, email read (BODY.PEEK triage — already read-only by design), file writes inside workspace.
- **Ring 2, ask every time with preview:** shell execution, writes outside workspace, sending email/messages, anything irreversible. Andon-style confirm card shows exactly what will run/send.
- Permissions page: every grant, when, by which avatar, one-click revoke. This is a trust feature, not a chore — surface it proudly.

**O7 — Setup health & recovery** (~2 days)
- "Doctor" screen (and `narad doctor` CLI): runs `startup_checks`, translates each failure into a fix-it button (re-download model, re-enter key, open firewall help).
- Demo content pack: one pre-seeded learning workspace + sample episodes so the dashboard is never empty on first open.
- Telemetry: **none by default**. A single opt-in "share anonymous crash reports" toggle. Local-first honesty is the marketing.

---

## Part C — Sopan (सोपान): spend- & hardware-tiered access

*Added 2026-07-09. Goal: anyone can run Narad, from a ₹0 phone-class device to a subscription power user. One tier engine decides the default; the user can always override. Grounded in the Gemma 4 release (Apache 2.0, QAT) and the June 2026 Claude Agent SDK subscription-credit policy.*

### C.1 The five tiers

| Tier | Who | Brain | Cost | Hardware floor |
|---|---|---|---|---|
| **T0 Kinara** (edge) | phones, old laptops, <8 GB RAM | Gemma 4 **E2B QAT** (~3 GB) / **E4B** (~5 GB), fully offline | ₹0 | 4–8 GB RAM |
| **T1 Sthanik** (local, default) | typical laptop/desktop | Gemma 4 **12B QAT Q4** (~7 GB); Q8 (~14 GB) on ≥32 GB machines; 26B-A4B (~15 GB) opt-in on big boxes | ₹0 | 8 GB RAM (reduced ctx) / 16 GB comfortable |
| **T2 Kunji** (BYO key) | pay-per-token users | Any cloud provider (DeepSeek/Gemini/Claude/OpenAI) via O5 keyring; cost ledger meters spend live | usage-based | any |
| **T3 Sadasya** (subscription) | Claude Pro/Max holders | "Sign in with Claude" → Agent SDK credit adapter (O5); monthly plan credit, no key handling | plan fee | any |
| **T4 Sangam** (hybrid) | anyone with T1 + (T2\|T3) | Flash-tier avatars local, Pro-tier cloud; auto-composed | minimal | 8 GB+ |

Tier names surface in the wizard as plain cards ("Free & private, on this device", "Bring your own key", "Use your Claude subscription", "Best of both"); Sanskrit names live in code/docs only.

### C.2 Milestones

**S1 — Tier engine** (backend, ~2 days)
- `tier_engine.py`: detect RAM, VRAM/Apple Silicon (mlx present), disk headroom, CPU class → recommend `{tier, model, quant, est_download, est_tokens_per_sec}`. Pure function + `GET /tiers` endpoint; wizard and doctor both consume it.
- Encodes the Gemma 4 ladder: <8 GB → E2B; 8–16 GB → E4B or 12B-Q4 (reduced context); ≥16 GB → 12B QAT Q4 (default); ≥32 GB → 12B Q8; ≥24 GB VRAM → offer 26B-A4B. User override always wins; choice persisted to `onboarding.json`.
- `narad-local/` provider branch in `detect_provider` + free pin in cost ledger land here (shared prereq for O2).

**S2 — Budget guardrails** (backend + settings UI, ~2 days)
- Monthly budget field (default: unset). Cost ledger already records per-source spend; add `GET /costs/monthly` rollup + soft-warning Vahana event at 80%, hard switch to T1/T4 local-first routing at 100% (never silently stop working — degrade to local).
- Per-avatar routing table surfaced in Settings: which avatar runs local vs cloud, one toggle each (writes AVATAR_MODELS overrides).

**S3 — Subscription adapter** (backend, ~3 days, pairs with O5)
- `narad-claude-sdk/` provider: wraps the Claude Agent SDK auth + completion path so plan credits are consumed; surfaces remaining credit in Settings → Connections card. Cost ledger records tokens with `source="subscription"` at $0 marginal.
- Abstraction: `subscription_providers.py` registry so future vendor programs (if/when OpenAI or Google open one) are one adapter file each.

**S4 — Edge build track** (later, after O1 ships)
- Same tier engine on-device; E2B + mobile quant format; PWA already works (M1) — a packaged mobile shell is a separate milestone gated on desktop packaging lessons.

### C.3 What this changes elsewhere
- **O4 wizard screen 2** becomes the tier picker: five cards fed by `GET /tiers`, with the S1 recommendation pre-selected and honest numbers (download size, est. speed) on each.
- **O7 doctor** gains "wrong tier" detection (e.g. 12B chosen on 8 GB → suggest E4B).
- **Kala/Vahana**: budget warnings ride the existing inbox/ntfy channel (`kind="system"`).

---

## Part D — Completing the teach skill (Guru mode of Krishna)

*Added 2026-07-09. Honest gap: G1–G4 built the engine and the panel, but Krishna's actual chat teaching never got connected to them. Verified in code: `build_workspace_packet` (what Krishna sees) contains MISSION/GLOSSARY/RESOURCES/RECORDS — **no syllabus, no current atom, no learner state**; no teaching-persona rules are injected anywhere in phase-1; `grade_check_answer` is reachable only via the REST API, so chat answers never update mastery; `due_reviews()` is unwired; `evals/golden_tasks.json` has zero guru tasks. Krishna currently teaches blind while a perfectly good syllabus sits on disk.*

### D.1 Milestones

**G6 — Guru mode in chat: the complete teach skill** (backend, ~3 days — *highest-value item in this whole plan*)
1. **Packet upgrade** (`build_workspace_packet`): append SYLLABUS (atom list + mastery status), CURRENT ATOM (the *frontier atom* — first non-mastered atom whose prerequisites are all mastered), its four rungs + misconception + check question, and a one-line learner-state summary. Cap total packet at ~4.5k chars (raise `max_chars`).
2. **Teaching-persona injection**: when `is_learning_query` (or an active learning workspace) triggers, append a Guru-mode fragment to Krishna's instruction for that turn: teach exactly one atom per exchange; analogy (🧒) first, climb rungs only on request or demonstrated grasp; end with exactly one check question (the atom's, or a fresh one); wrong answer → a *different* analogy, never the same words louder; name the misconception when the learner walks into it; cite RESOURCES when making factual claims.
3. **In-chat mastery loop**: when Krishna's teach turn ends with a check question, set working-state flag `awaiting_check = {workspace_id, atom_id}`. On the next user turn in that session: route the reply through `grade_check_answer` (GURU_GRADER_MODEL, heuristic fallback) → `record_check_result` updates learner_state/streak/next_review → verdict is prepended to Krishna's context so remediation or advancement is informed. Flag clears after grading or on topic change.
4. **Detection hardening**: regex fast path stays; add continuity (active `learning_workspace_id` in working state keeps Guru mode on without re-matching) and a dispatch-model fallback flag for teach intent (one extra label in the existing router call — no new LLM call).
5. **Session end** → distilled outcome record + `capture_episode` ("mastered X, shaky on Y"), exactly as A.2 principle 5 intends.

**G5 — Review & spaced repetition wiring** (~1 day — engine exists, just connect it)
- `kala_scheduler.tick` gains `_fire_due_reviews`: iterate learning workspaces per user, `guru_engine.due_reviews()`, aggregate → one Vahana event per day per user (`kind="reminder"`, "3 atoms due in *Transformers*"), state-keyed like medication reminders.
- GurukulTab quiz mode: "Review due" button → check questions for due atoms, answers graded through the same G6 loop.

**G7 — Guru golden tasks** (~0.5 day)
- New `guru` group in `evals/golden_tasks.json` + runner support: syllabus schema-valid + acyclic; every atom has 4 non-empty rungs; artifact update honors one add + one remove; teach response contains exactly one check question; a graded answer mutates learner_state. Structural (no-LLM) where possible, consistent with the 48 existing tasks.

---

## Sequencing & dependencies

*Final integrated order, 2026-07-09 (G1–G4 ✅ done). Estimates in build-days.*

| Order | Item | Days | Why now | Depends on |
|---|---|---|---|---|
| 1 | **G6** complete teach skill in chat | 3 | The engine exists but Krishna teaches blind — highest value-to-effort in the plan | G1–G3 ✅ |
| 2 | **G5** spaced-repetition wiring | 1 | `due_reviews()`, Kala, Vahana all exist; pure connection work | G6 grading loop |
| 3 | **G7** guru golden tasks | 0.5 | Locks G5/G6 behavior into CI while fresh | G5, G6 |
| 4 | **M4.4** distilled-rule sutras | 2 | Closes M4 entirely before the packaging arc begins | — |
| 5 | **S1** tier engine (+ `narad-local/` provider) | 2 | Shared prereq for O2 and the wizard; useful in dev immediately | — |
| 6 | **O5 + S3** Kunji keyring + Claude-subscription adapter | 8 | Unblocks non-tech users pre-packaging; T2/T3 tiers go live | S1 |
| 7 | **O2** Gemma 4 local brain (12B QAT + E-series) | 4 | True out-of-box; T0/T1 tiers go live | S1 |
| 8 | **S2** budget guardrails | 2 | Spend safety before we invite non-tech users to add keys | O5, cost ledger ✅ |
| 9 | **O1** packaging (Tauri) | 5 | The installer | O2, O5 |
| 10 | **O4** wizard v2 (tier picker) | 5 | First-run flow, five-card brain chooser from `GET /tiers` | O1, S1–S3 |
| 11 | **O3** search relay | 3 | Needs the hosted component — build once packaging is real | O4 |
| 12 | **O6, O7** permission rings + doctor | 6 | Polish + trust ring | above |
| 13 | **S4** edge build (E2B mobile) | — | After desktop packaging lessons | O1 |

Roadmap mapping: G5–G7 = **M6 (Guru — completion)**; S1–S4 woven into **M5 (Pratham + Sopan)**. M4.4 remains the only open M4 item and slots before the packaging arc.

## Decisions log

1. **Tauri vs Electron** — plan recommends Tauri (size, signing); Electron only if we need Node-side integrations. *(open)*
2. **Search relay** — are you willing to run one small hosted service? If strictly zero-hosted, Tier 1 collapses to "guided Brave free-key signup" in the wizard. *(open)*
3. **Gurukul as new tab vs inside Tapasya** — ✅ decided: new tab (shipped in G4).
4. **Bundled default model** — ✅ decided 2026-07-09: **Gemma 4 12B QAT** flagship, E2B/E4B edge rung (Apache 2.0, QAT ggufs on ollama/llama.cpp — see O2).
5. **Subscription path** — ✅ decided by policy: Claude Agent SDK credit adapter only; no consumer-OAuth token reuse; OpenAI stays BYO-key.
