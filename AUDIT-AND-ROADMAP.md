# Narad — Audit & Roadmap

*2026-07-04. Full-code audit: backend harness, memory/learning stack, skills inventory, frontend UX, plus a concrete phone-access plan. Every major claim cites file:line. Companion to LAUNCH_CHECKLIST.md.*

---

## 1. Verdict

Narad is a real system, not a demo. The executor sandbox, the security floor, the context governor, the tool-result envelope, and the session plane are engineering most hobby harnesses never reach. The cultural identity is executed with craft, not clip-art — MahatiLogo is live system status drawn as an instrument.

It has three systemic diseases, all curable:

1. **Accretion.** Every phase added a layer; almost nothing was ever removed. Result: 7 memory stores where 3 planes suffice, 4 video pipelines, ~3,600 lines of orphaned frontend components, an 84MB template submodule feeding one tool that has a working fallback.
2. **Open loops.** Systems that record but never act: medication reminders that never fire, a Swapna inbox nothing consumes, an Andon gate that never blocks or retries, a Karma ledger nothing reads back, sutras promoted with no outcome verification. The learning moat is currently *unproven* — the sutras-on/off eval has never been run.
3. **Truth drift.** AGENTS.md (self-declared "source of truth") still documents **8 avatars** (AGENTS.md:12–21: Varaha, Narasimha, Buddha, Vamana included) while the build ships 4 (narad_agent.py:377). Notion sync is push-only, not "bidirectional." Checklist items E and F remain unchecked.

The strategy that follows: **close loops before adding surface. Delete before building. Prove the moat.**

---

## 2. What is working well (verified in code)

**Security floor — real, not aspirational.**
- Executor sandbox: AST-based import/call analysis, env scrubbed to an allowlist (no API keys in child), process-group kill, wall-clock + output-size limits, covered by phase-7/test_executor_sandbox.py.
- Auth: three modes (`local`/`strict`/`off`), token auto-generated chmod-600 at `~/.narad/config/api_token` (server.py:395–414), localhost pass-through, CORS extendable via `NARAD_ALLOWED_ORIGINS` (server.py:436). Token-bucket rate limiting (server.py:489).
- Dharma action gates are genuinely mandatory for side effects: `gate_action()` consulted by executor, email_skill, browser_act_skill; unknown actions **denied by default**; every verdict (allow *and* deny) written to Karma (dharma.py:143–191).

**Harness bones that are ahead of most.**
- Context governor: typed context planes with priorities, compaction strategies, epoch rollover, and model-escalation profiles (phase-1/context_governor.py), with tests.
- Run/stream decoupling: the agent run lives in `_active_tasks`, detached from the SSE connection — a dropped client can reconnect and re-attach mid-run (server.py:519–523), with 30s heartbeats (server.py:1453). *The frontend never uses this — see §6.*
- Retry with exponential backoff on transient LLM errors at the avatar level (avatar_agents.py:505–532).
- Tool-result envelope: typed `status/summary/artifacts/citations/ui/requires_confirmation` contract (tool_result.py:105) so the UI renders artifacts without scraping chat text.
- Session plane: catalog, fork, archive, compact, lineage (harness_contract.py) — real session management.

**Skill quality floor.** Dry-run-first defaults, trash-never-delete, a real SSRF guard in http_skill (phase-8/http_skill.py:31–116), SELECT-only SQL, and structured "not configured" responses instead of crashes. finance_skill is the crown jewel: 14 tools, HDFC/ICICI/Axis/SBI parsers, Markov spend patterns — clearly battle-tested on real data.

**Identity infrastructure.** One shared contract file (contracts/agent-contracts.json) drives names/Devanagari/colors in both backend and frontend; CSS tokens carry provenance comments ("samudra lapis — the fish of the deep"); motion vocabulary (string-pluck, breathing bindu) derives from the instrument. Sankalpa's style loop is real: every 5th session, LLM-extracted patterns, Jaccard dedup, 24h cooldown (phase-6/sankalpa.py:341–389).

**Process discipline.** CI runs ruff + 7 dependency-light suites on every push; pre-commit configured; 13 test files; ARCHITECTURE/AGENTS/WORKFLOWS docs exist at all — rare.

---

## 3. What is broken (with evidence)

**Memory: the consolidation added a 5th system instead of replacing 4.**
- One `capture_episode` fans out to 4 writes + 2 embeddings of the same content (smriti_core.py:194–238); `recall_context` queries the vector tier *and* legacy LanceDB *and* wiki, injecting the same episode up to twice (smriti_core.py:304–330).
- Worst bug: **every recall re-embeds every episode** — no content-hash skip (smriti_recall_ranker.py:163 → smriti_indexer.py:63–76). Memory gets slower with every conversation.
- Silent hash-bucket embedding fallback creates split-brain: records written under one embedding model are invisible to queries under another (smriti_vector_store.py:48–70).
- No relevance floor on recall — top-k injected regardless of how weak the match is.
- `forget()` writes a tombstone and deletes nothing (smriti_core.py:639). `weak_sessions.jsonl` is write-only; the prompt-revision loop promised at tapas.py:8 doesn't exist.
- **Scribe stores latency notes, not content**: wiki entries read "[Completed in 3.2s, response ~1200 chars]" (phase-9/scribe.py:113–118) — project memory recall returns timing metadata.
- **The supervisor has amnesia**: narad_agent.py imports zero recall/style — Narad itself never sees memory or Sankalpa. The router treats a 10-month user like a stranger.

**Learning loop: compounds cost, not capability (yet).**
- A sutra is the verbatim first 1,500 chars of any response scoring ≥ 0.80 — replayed few-shot, not a distilled rule (phase-3/tapas.py). One confidently-wrong answer poisons similar prompts for 90 days.
- CAI critique fails open (tapas.py:290); sanitization is a regex blocklist (sutra_engine.py:211–234); no outcome tracking, no demotion on bad downstream results, no A/B eval — 2–3 judge LLM calls per avatar run with unproven benefit.

**Router: a 5,500-token keyword wall.**
_NARAD_INSTRUCTION (narad_agent.py:21–375) is enumerated trigger phrases ("poke holes in this", "buy vs rent"…) burned into every turn on the Flash model, with no structured decision, no confidence, no misroute fallback — and the routing table exists in three drifted copies (narad_agent.py, AGENTS.md, WORKFLOWS.md).

**Open loops.**
- `set_medication_reminder` writes a DB row nothing ever fires (phase-8/health_skill.py:78–94).
- Swapna writes suggestions to an inbox with no consumer (smriti_core.py:571); manual POST only, no scheduler.
- Andon runs *after* the result is produced — it logs, emits SSE, marks kanban blocked, fires a diagnostic LLM call, but never retries, blocks, or amends what the user gets (avatar_agents.py:797–892). TOOL_ERROR fires even when the final answer recovered (andon.py:50).
- `schedule_cron` can create OS jobs but there is no path from a fired job back to chat or a notification. Proactivity dead-ends in SQLite.

**Frontend disconnects.**
- Session resume exists in hook + backend but the UI throws it away: `void onResumeSession` (NaradDashboard.tsx:117).
- `api.ts:7–8` hardcodes port **8010**; server and Vite proxy use **8000**; the proxy also omits /threads, /harness, /audit, /evolution, /swapna routes.
- `apiFetch` sends **no Authorization header anywhere** — remote (phone) access would 401 without the same-origin trick in §7.
- No SSE reconnect despite backend re-attach support; autoscroll yanks on every chunk (ChatPanel.tsx:275); attached images vanish from the sent bubble; hover-only affordances are dead on touch.
- Dark "lamp-lit night" tokens exist but nothing toggles `.dark`; three different hardcoded ink colors bypass the token system; fonts load from CDN (Devanagari dies offline); Anton loads unused; Geist is installed but never imported.

**Docs/state drift.** AGENTS.md documents 8 avatars vs 4 shipped; "bidirectional" Notion claim is push-only (send-only code in notion_sync.py); uncommitted changes sit on clean-main (avatar_agents.py modified; dev.sh, remotion untracked).

**Privacy.** Raw episodes stored unencrypted, embedded via cloud APIs, optionally pushed to Notion — zero redaction anywhere in the write path. For a "yours forever" product this is the biggest identity-vs-implementation gap.

---

## 4. Cut list — subtraction is the feature

| Cut | Why | Recovery |
|---|---|---|
| phase-9/templates submodule (84MB) | Feeds one Krishna ranker that already has a working fallback (avatar_agents.py:2364) | none needed |
| notion_sync.py + endpoints | Push-only mirror of logs a single user has locally; ceremony | keep behind optional plugin flag if you truly live in Notion |
| webwright_skill, ml_intern_skill | Wrappers around external checkouts/CLIs nobody will configure; browser_act already covers web actions | — |
| audio_skill, hyperframes_skill, remotion path | 4 video/audio pipelines → keep 2: Veo (AI) + moviepy (fallback) | — |
| Orphaned frontend components (~3,600 lines): SutraPanel, ProjectsView, HarnessWorkspaceTab, KanbanBoardView, EvolutionTab, SutrasTableView, MemoryGalleryView, KarmaSheet | Never imported | git history keeps them |
| LanceDB+FTS5 double store, rrf_recall, memory_schema.py, phase-9/project-memory legacy files, `_extract_commitments`, weak_sessions writes | Superseded by consolidation (§5) | after M2 only |
| docling as default extraction tier | Pulls torch; make pypdf/pymupdf the default, docling opt-in | env flag |
| `motion` dep (one border animation), @fontsource-variable/geist (unused), Anton font, CopilotKit .env stanza | Dead weight | CSS keyframes |
| Dashboard: 5 tabs → 3 | Darshan call-graph duplicates AwarenessBar; DivyaDrishti and Tapasya fetch the same /sutras + /swapna | merge |
| phase-0a, phase-0b | Spikes, done | archive branch |

**Explicitly keep** (they pull weight): kanban.py (live plan-progress substrate), narad_5s.py (real hygiene + a user-facing tool), executor, all of phase-8 not named above, the Six Sigma *vocabulary* — but see Andon fix in §6.

---

## 5. Memory: 7 stores → 3 planes (M2)

Target architecture: `smriti_core` becomes the **only** module that touches storage.

1. **Source of truth** — `episodes.jsonl`, append-only, one write per episode. Scribe folds into `capture_episode` and stores *actual result text*, not timing strings.
2. **Index** — one vector index (keep the turbovec manifest store) + SQLite FTS5 over episodes for exact match. Content-hash incremental indexing kills the re-embed-everything bug. One embedding model, hard-fail visibly if unavailable — no silent hash fallback.
3. **Derived** — sutras, sankalpas, wiki summaries regenerated *from* episodes by a real nightly Swapna job that consumes its own inbox. `forget(id)` cascades across all three planes. Karma remains the single audit ledger (drop the dual karma/karma_mutations write).

Recall becomes one ranked query with a **relevance floor**, one injection block, and a provenance line the UI can show ("recalled from 12 Jun — budget planning"). And **give Narad itself recall + Sankalpa** — the supervisor is where a single user feels the memory magic first.

---

## 6. Harness: gaps vs SOTA, ranked

1. **Prove or kill the moat.** Weekly sutras-on/off A/B over a fixed golden-task set; sample Tapas scoring at ~20% instead of every run; sutras become distilled rules ("for HDFC CSVs, map narration column X") rather than verbatim replay; demotion on negative outcomes; CAI critique fails *closed*.
2. **Router rebuild.** Replace the keyword wall with a compact router prompt + per-avatar capability descriptions generated *from* agent-contracts.json (one source of truth, three consumers). Structured routing output with confidence; low confidence → clarify instead of misroute. This also cuts ~5k tokens/turn on the most-called model.
3. **Proactivity plumbing — the single highest-leverage feature gap.** One delivery channel: fired cron/reminder/Swapna/Andon events → (a) a chat inbox turn and (b) an ntfy push (§7). This single pipe makes med reminders fire, budget alerts land, Swapna suggestions surface, and "task done" reach your phone. Fits Rama's identity perfectly.
4. **Email reading.** IMAP triage for Krishna (the code half-exists in finance_skill's Gmail sync) — send-only is half a comms agent.
5. **Streaming resilience end-to-end.** Frontend re-attach to `_active_tasks` on drop + timeout watchdog. Backend already supports it; this is a frontend-only fix and it is *mandatory* for phone use.
6. **Per-tool wall-clock timeouts + provider circuit breaker.** Retries exist for LLM calls; a hung tool call or a down DeepSeek still stalls the turn. Fail fast with a user-visible fallback ("Parashurama unreachable — retry or switch model?").
7. **Cost ledger.** Usage events already stream (server.py:2246); aggregate per turn/avatar/day and surface in AwarenessBar. Tapas judge calls should appear in it — learning has a price tag.
8. **Golden-task CI.** 10–15 canonical tasks per avatar asserted on structure (not exact text), run nightly — the routing spike (93% accuracy) is the only eval that has ever existed.

What NOT to build: more avatars (the four cover the taxonomy — new capability slots into existing strings), multi-user tenancy, plugin marketplaces, more dashboards.

---

## 7. Phone access — private tunnel, zero cloud (M1)

Your pick: Tailscale to your Mac. The design below needs **no new infrastructure and respects "local, yours forever."**

**Phase 0 — working today (~30 min, no code):**
1. Install Tailscale on Mac + phone, same tailnet.
2. Serve the built frontend from FastAPI same-origin: `app.mount("/", StaticFiles(directory="phase-4/frontend/dist", html=True))` after API routes. This kills the CORS problem, the 8010/8000 confusion, and the missing-auth-header problem in one move.
3. `tailscale serve --bg 8000` → `https://<mac>.<tailnet>.ts.net` with a real TLS cert. Tailscale serve proxies from loopback, so requests arrive as 127.0.0.1 and `NARAD_AUTH=local` passes — encrypted end-to-end by WireGuard, no token juggling, nothing exposed to the public internet. (If you ever bind 0.0.0.0 instead: switch to `NARAD_AUTH=strict` and add header injection in `apiFetch`.)
4. Keep the Mac awake: `caffeinate -s` wrapped in dev.sh (or Amphetamine / `pmset -c sleep 0`). Long-term: the Phase-15 Electron/menubar app or a Mac mini.
5. Never use Tailscale Funnel (public internet) — Dharma gates guard side effects, but the surface isn't worth it.

**Phase 1 — actually pleasant on a phone (one weekend, frontend-only):**
- Responsive pass at 390px: AwarenessBar → bottom bar; artifact panel → full-screen sheet; dashboard tabs scrollable; SplitPane stacked + pointer events (currently mouse-only); kill the fixed 432px artifact grid.
- PWA: manifest + icons + theme-color → "Add to Home Screen" gives an app-feel with the Mahati as the icon.
- Self-host fonts (Devanagari currently dies without CDN).
- SSE re-attach + "↓ new" scroll pill + dismissible errors (§6.5) — cellular drops are the norm, and the backend already keeps the run alive.

**Phase 2 — Narad reaches out (pairs with §6.3):**
- ntfy (self-hostable, fits the ethos; or ntfy.sh topic) — server POSTs on task-done/reminder/Andon; ntfy app on the phone delivers push. iOS PWA push (16.4+) can come later; ntfy is 20 lines and works today.

---

## 8. UX frailties → UI fixes, ranked by pain

| # | Frailty | Fix |
|---|---|---|
| 1 | Past threads unreachable (resume wired backend-side, discarded in UI) | Sessions drawer in ChatPanel header off `/harness/sessions`: resume, fork, archive |
| 2 | Autoscroll yanks while streaming | Stick only when near bottom; "↓ new" pill otherwise |
| 3 | Stream drop = dead truncated message | Re-attach via session_id; watchdog; dismissible error strip |
| 4 | Sent images vanish | Thumbnails in the user bubble |
| 5 | 5-tab world behind one 28px "⊞" glyph | Labeled rail icons (veena glyph, not ⊞) + keyboard shortcuts |
| 6 | Hover-only actions dead on touch | Always-visible compact actions on mobile |
| 7 | Port/auth config split-brain | Same-origin serving (§7) + delete the 8010 special case |
| 8 | Dark mode dead code | Ship the toggle; sweep the three hardcoded inks to `--ink-*` tokens |
| 9 | Devanagari matras clip at 7–15px | Raise glyph sizes; test क्ष्म renders |
| 10 | Empty states are blank | Madhubani vignette + one-line prompt suggestions per avatar |

---

## 9. Identity: keep, deepen, and make true

The cultural frame is Narad's differentiation and it is *earned* in code — keep it. Three rules going forward:

1. **Identity must be executed, not claimed.** Finish the half-shipped pieces: lamp-lit dark mode toggle, token sweep, self-hosted Devanagari, veena glyph replacing "⊞". The frame degrades fast when a Madhubani border sits next to a hardcoded gray.
2. **Identity must be true.** Fix AGENTS.md to the canonical four; "local-first" stays roadmap language until Phase 15 ships; Notion is "push." The tradition you're drawing on values satya — the docs should too.
3. **New capability joins an existing string.** Proactivity → Rama. Email triage → Krishna. File watching → Matsya. Scheduled delivery of what Swapna dreamed → the loop the names promised all along. No fifth avatar; the Mahati has four strings and restraint is the aesthetic.

---

## 10. Sequenced roadmap

- **M0 — Truth & hygiene (1–2 days).** Commit the dirty tree. Execute the cut list (§4). Reconcile AGENTS.md/WORKFLOWS.md/README to 4 avatars + honest claims (checklist E1/E2). Fresh-machine dry run (F1).
- **M1 — Phone (1 weekend).** §7 Phase 0 + Phase 1. You use Narad from your pocket by Sunday night.
- **M2 — Memory consolidation (1 week).** §5. Fix re-embed bug first (day one, it's isolated), then the 3-plane merge, then Narad-level recall.
- **M3 — Close the loops (1 week).** Delivery channel + ntfy (§6.3), reminders fire, Swapna consumed nightly, Andon feeds the retry path instead of just logging, IMAP triage for Krishna.
- **M4 — Prove the moat (ongoing).** Sutra A/B eval, golden-task CI, cost ledger, distilled-rule sutras.
- **M5 — Phase 15 as planned.** Electron + local model. M0–M4 make it a stronger foundation to package.

*The through-line: Narad already has more built than it has true. Make it true, make it reachable, make it learn for real — in that order.*
