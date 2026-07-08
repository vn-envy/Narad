# Narad — Guru Teach Panel v2 & First-Run Onboarding Plan

*Drafted 2026-07-08. Companion to AUDIT-AND-ROADMAP.md (slots into M5/M6). Two tracks: **Guru** (G1–G5) — the teach artifact panel reborn as an actual teacher; **Pratham** (O1–O7) — the first-install experience that makes Narad truly out-of-the-box.*

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

**G1 — Concept decomposition engine** (backend, ~2 days)
- New `guru_engine.py`: one Krishna LLM call: topic + workspace packet → schema-validated `syllabus.json`:
  ```json
  { "atoms": [ { "id": "dot-product", "name": "Dot product as similarity",
      "prerequisites": [], "eli5": "...", "plain": "...", "precise": "...", "formal": "...",
      "misconception": "...", "check": {"q": "...", "good_answer": "..."} } ] }
  ```
- Validation: DAG acyclic, ≤12 atoms, all four rungs non-empty; retry once on schema failure, fall back to single-atom syllabus.
- Stored in the existing workspace dir; `build_workspace_packet` extended to include syllabus summary.
- Endpoints: `POST /learning/workspaces/{id}/syllabus` (generate/regenerate), `GET` included in `load_workspace`.

**G2 — LLM-backed artifact generation & iteration** (backend, ~2 days — *do first, biggest payoff*)
- Replace `_seed_artifact_doc` templates: generation prompt = workspace packet + syllabus + artifact type → real cards (fronts *and* backs), real concept maps (nodes from atoms, labeled edges).
- Replace regex iteration: `update_learning_artifact` sends {current doc, instruction} → LLM returns full revised doc (JSON), diffed and versioned. Keep `version` increments; add `history/` per-version files so iteration is undoable.
- Tapas gate: generated/revised docs pass the existing critique before write; failures → `blocked_critique` karma event (schema already supports it).
- Keep template path as offline fallback when no provider is available (local-first honesty).

**G3 — Teacher persona & mastery loop** (backend, ~2 days)
- Teaching prompt fragment injected into Krishna for learning queries: current atom, its rung content, learner state, the three rules (one atom, analogy first, check question).
- New `learner_state.json` per workspace: `{atom_id: {status: untaught|shaky|mastered, attempts, last_reviewed}}`. Krishna's check-question outcomes update it (self-reported grading via a second cheap LLM call, DS_FLASH).
- `is_learning_query` upgraded: regex stays as fast path; router fallback asks Narad's dispatch model (already in the loop) to flag teach intent.

**G4 — Gurukul panel** (frontend, ~3 days)
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

**O2 — Bundled local brain (Gemma)** (~4 days)
- **Do not bundle weights in the installer.** Gemma's license requires terms acceptance, and 3 GB installers kill conversion. Instead: bundle the runtime, download weights on first run with consent + progress + checksum + resume.
- RAM-tiered default: ≥16 GB → Gemma 3 12B QAT Q4 (~7 GB); 8–16 GB → Gemma 3 4B (~3 GB); <8 GB → Gemma 3 1B (~0.8 GB). Detect at wizard time; user can override.
- New provider string `narad-local/<model>` → bundled llama-server port; `detect_provider` gains one branch; `AVATAR_MODELS` defaults to it when no cloud key exists. Everything else (context windows, cost ledger $0, capabilities flag) already handles "local".
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
- **Subscription OAuth where it exists:** Claude and ChatGPT consumer plans expose OAuth device flows (Claude Code-style login). Where available, "Sign in with Claude/ChatGPT" beats key-paste entirely. Build the abstraction so providers can be added as they open this up.
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

## Sequencing & dependencies

| Order | Item | Why now | Depends on |
|---|---|---|---|
| 1 | **G2** LLM artifacts | Biggest quality jump, no UI needed, 2 days | — |
| 2 | **G1 + G3** decomposition + mastery loop | Core pedagogy | G2 prompts |
| 3 | **G4** Gurukul panel | Makes it visible | G1–G3 |
| 4 | **O5** Kunji keyring | Unblocks non-tech users even pre-packaging (works in dev too) | — |
| 5 | **O2** local brain | True out-of-box | — (runtime works in dev via ollama today) |
| 6 | **O1** packaging | The installer | O2, O5 |
| 7 | **O4** wizard | First-run flow | O1, O2, O5 |
| 8 | **O3** search relay | Needs a hosted component — build once packaging is real | O4 |
| 9 | **G5, O6, O7** | Polish ring | above |

Roadmap mapping: G1–G5 = **M6 (Guru)**; O1–O7 = **M5 expanded (Pratham)**. M4's distilled-rule sutras item remains open and is unaffected.

## Open decisions (need your call)

1. **Tauri vs Electron** — plan recommends Tauri (size, signing); Electron only if we need Node-side integrations.
2. **Search relay** — are you willing to run one small hosted service? If strictly zero-hosted, Tier 1 collapses to "guided Brave free-key signup" in the wizard.
3. **Gurukul as new tab vs inside Tapasya** — plan says new tab; Tapasya stays the self-evolution console.
4. **Gemma vs Qwen/Llama for the bundled default** — Gemma 3 QAT is the current best size/quality at 4B; revisit at O2 build time.
