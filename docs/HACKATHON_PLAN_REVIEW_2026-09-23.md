<!-- Independent three-lens review (hackathon judge, build feasibility, family-pilot safety) of HACKATHON_PLAN v1, consolidated with conflicts resolved. v2 of the plan applies these edits. -->

# Consolidated edits to HACKATHON_PLAN_2026-09-23.md

This merges three critiques: the **judge** lens (how the entry will score), the **feasibility** lens (whether it can be built in time) and the **pilot** lens (safety of the real four-person family pilot). The plan file was read and not changed.

I checked five claims against the code at 9faf37a:
- `family_profiles.py` records only `is_owner`.
- `cost_ledger.py:15` records unknown models at $0.
- `deepseek/deepseek-flash` is the default in `phase-6/sankalpa.py:161`, `guru_engine.py:63` and `kunji.py:60`.
- `<pilot hostname>` is written in `Start Family Pilot.command` and `OnboardingFlow.tsx`, and has been in git history since b39599c.
- `phase-1/server.py:889` hard-blocks "passport number", and the crisis rule on :890 is English-only.

---

## Conflicts between lenses, and how they are resolved

| # | Conflict | Decision | Why |
|---|---|---|---|
| C1 | **When family members join.** Judge: start now (owner + a parent by Oct 1, everyone by Oct 10). Feasibility: no one until the gateway passes (1–2 people Oct 10, everyone Oct 15). Pilot: owner Oct 1, one member Oct 8, everyone Oct 15. | The owner uses the new build first, on his own profile. The first non-owner member joins only when four things are green: the leak tests on every egress path (M5), the Phase 0 carryovers (M15), consent (M17) and the metric definitions (M18). Target Oct 6–8. All four people join on the tagged pilot build on Oct 15. | Family safety is a hard condition: the owner's own rule is that DeepSeek sees nothing unredacted. Moving the lean gateway into the Oct 5 slice (M2) gets back most of the data the judge lens wanted: about 3 weeks for the owner, about 2.5 weeks for owner + 1, about 9 days for all four. |
| C2 | **Streaming order.** Judge: build the crude slice first, streaming later. Feasibility: streaming has to land before the gateway, because the gateway restores placeholders in the streamed output. | Build the slice without streaming, with the gateway working on whole messages, where restoring placeholders is trivial. Streaming and `skip_summarization` then land as one PR that includes the streamed restore (40-character holdback), by Oct 10. | This meets both constraints: the demo runs end to end early, and the streamed restore is written against the real streaming path. |
| C3 | **Demo scope.** Judge: one lab-report errand. Feasibility: keep Travel, Health and Teach. Pilot: the family uses chat, Health/Docs and Travel search with a hand-off. | The judge lens wins for the video: one errand. The pilot lens wins for the family: Travel stops at search and a hand-off, and Teach gets no new work. Kriya's 9/10 gate moves from the Travel fixture to the clinic fixture. | This improves Design and Impact, and it also removes feasibility load: no booking at 9/10 on Travel and no Teach TTFT clip. |
| C4 | **Approval routing.** Judge: send approvals to Asha through care circles. Pilot: send them to the requester unless a rule both people can see says otherwise. | Both. The default approver is the requester. A care circle is exactly the visible rule. The person who delegates sets it, and it is shown to both people and logged. | The two-person story stays, and no one gets silent approval power over someone else. |
| C5 | **Judge access.** Feasibility: invite codes (already built), per-judge caps, a status page. Judge lens: no codes, one global cap. | No codes and no status page. Use a separate API key, one global rate limit and daily budget cap, and switch to replay only after the cap is hit, with a label on the page. | The rules say "free and unrestricted", so codes risk failing Stage 1 and add friction. The separate key and the cap handle abuse. |
| C6 | **Router replacement headline.** Feasibility: a measured Nemotron cascade. Judge: the split brain. | The split brain is the idea headline. The cascade is a technical detail for the README and the scorecard. | The split brain is visible on screen and hard to copy. A cascade is common. |
| C7 | **Router go/no-go date.** Feasibility: Oct 7, on vendor facts. Judge: Oct 12, on the slice being ready. | Both must pass. Facts by Oct 7, and the slice plus the receipt working by Oct 12. | Owner hours are the bottleneck, so the stricter gate wins. |
| C8 | **Safety Guard.** Judge: replace the regex gate. Feasibility: run it in parallel, or drop it. Pilot: shadow mode, add-on only, never fail open. | Never replace the local rule gate. If Safety Guard runs, it runs in parallel and in shadow mode first. | A cloud classifier can't be the only crisis check, and a serial call across continents hurts time to first token. |
| C9 | **Spend stop.** Plan: a hard stop. Pilot: never hard-fail the family. | Hard stop on the benchmark and judge keys (the judge key falls back to replay). The pilot key falls back to economy mode with a banner. | The family shouldn't lose Narad because a benchmark run spent the credit. |
| C10 | **Filming.** Judge: two Android phones mirrored with scrcpy. Pilot: never film family phones. | Two spare or freshly reset demo phones, pointed at the judge instance. | Keeps notifications, contacts, accounts and the pilot URL out of a video the sponsor gets rights to. |
| C11 | **Lab values.** Pilot (could): extract them locally so they never leave. Judge: Nemotron explains de-identified values. | The judge lens wins for the demo. Local-only extraction is kept as a possible per-profile pilot option after Oct 30. | Nemotron has to do the real work for Stage 1 and for the split brain. Honesty is handled by precise wording (M6). |
| C12 | **Where placeholders are restored.** Plan week 2: "in tool arguments". Pilot: never into outbound search. | Restore only into local tools and approved form fills. Never into Tavily, search or model calls. | Otherwise real names go to Tavily. |

---

## Ranked edits

### MUST

**M1. §3 thesis and four bullets; §5 principle line. Rewrite around one errand and the split brain**
- **Replace the §3 blockquote with:**
  > Narad is one private AI for a whole family, built for the person who handles everyone's reports, bookings and forms. It uses a split brain. On the family Mac, NVIDIA gliner-PII, India-ID rules and the family name list decide *who* is involved, and that never leaves the house. On Nebius Token Factory, Nemotron 3 Super works out *how*. Tagline: "Nebius knows how. Only your home knows who." Care circles decide which family member approves what, on their own phone. Every message carries a privacy receipt. Stored memory, health and finance records stay on the Mac. On each turn, a pseudonymised excerpt goes to Nebius, and it can include lab values without names. Pariksha, a synthetic benchmark, supplies the before/after numbers. A staged family pilot supplies counts of real use.
- **Replace the four differentiator bullets with one line:** "Two people in one family, one errand, a privacy receipt anyone can check." Add: "The ~9 visible entries undercount the field. Expect many more by Oct 30."
- **Replace the §5 principle with:** "One errand filmed end to end beats three demo paths. The film is 'Papa's lab report': a photographed report, de-identified reading, a follow-up booking approved by his daughter on her phone, and a reminder. Travel is pilot-grade only: search → options → hand-off. Teach and Career/Finance get no new work."
- **Why:** Design and Impact are half the score, and three unrelated workflows read as a tech demo. This also shrinks the build (C3).

**M2. §5 preamble and all week headers. Build the whole flow crudely first, budget owner hours, add cut lines**
- **Add an owner-hours budget.** Roughly 100 hours of device testing are available. The plan as written needs about 55–60 owner-days. Tag each item that needs Mac or phone testing.
- **Vertical slice by Oct 5:**
  - the `nebius/` provider with Super;
  - the lean gateway (India-ID rules + family name list, on whole messages);
  - the egress chokepoint and its CI test (M5);
  - the receipt chip;
  - an approval push to a second profile (ntfy is fine for this cut);
  - Playwright on the clinic fixture.
- **Rough cuts.** Record a 3-minute cut on Oct 5, then every Sunday (Oct 11, Oct 18). Anything that isn't in a cut waits until after Oct 30.
- **Dated sequence:**

  | Date | What happens |
  |---|---|
  | Sep 24 | External facts check (M3) |
  | Sep 24–27 | Provider, reasoning control, prices, per-key budgets |
  | Sep 26 | Tool-calling gate |
  | Sep 29 | Artemis spike |
  | Sep 30 | Hinglish, time-to-first-token and ZDR gates |
  | Oct 1 | Pariksha baseline (12 tasks × 3 runs) |
  | Oct 1–5 | Vertical slice, Phase 0 carryovers, hostname fix |
  | Oct 6–7 | Judge instance skeleton |
  | Oct 6–8 | First non-owner member, if gated green (C1) |
  | Oct 6–10 | Streaming PR (C2), then gliner-PII |
  | Oct 7 | Router facts gate |
  | Oct 8–12 | Web Push approvals and care circles |
  | Oct 10 | Cut line 1 |
  | Oct 10–17 | Kriya on the clinic fixture, OCR, Health outcome contract |
  | Oct 12 | Router slice gate |
  | Oct 15 | Pilot build tagged, all four members |
  | Oct 17 | Kriya 9/10 gate |
  | Oct 17–20 | Reliability loop, judge hardening, after-scorecard |
  | Oct 20 | Cut line 2 |
  | Oct 21–22 | Stretch work only if every gate is green; write-up drafts |
  | Oct 23 / 24 / 25 / 28 | Freeze / dress rehearsal / video / submit |

- **Cut line Oct 10.** You are behind if streaming isn't merged, the leak tests aren't green on every egress path, or Pariksha can't produce a scorecard. Drop in this order:
  1. The router LoRA.
  2. OpenShell.
  3. Artemis controlling phones as an agent.
  4. Safety Guard.
  5. Loading skills on demand (keep the tool subsets).
  6. Edit on approvals.
  7. The Activity inbox (the montage becomes three notification cards).
  8. Live-view frames on the phone (the video uses a captioned Playwright recording instead).
  9. Any Hinglish claim.
- **Cut line Oct 20.** You are behind if the clinic fixture is below 9/10 or the judge instance isn't serving live. Then:
  - Health falls back to PDF, then health.db, then a Web Push reminder.
  - Pilot numbers are shown as raw counts with n, or left out.
  - The after-scorecard covers only the tasks actually run.
- **Never cut:** Nemotron as the default brain, the spend stop, the judge instance, the video, the README, the feedback section, the Aug 26 changelog, and synthetic-only public material.

**M3. §5 Week 1 (new first bullet); markers in §2 and §4. Day-1 facts check and week-1 gates**
- **Sep 24, checked in a browser and with curl. Record the results in VENDOR_RESEARCH and gate later items on them:**
  - `GET /v1/models`, for the exact IDs of Lightning, Super, Ultra, Safety Guard and the embedding models.
  - ZDR: whether an org-level switch exists, its regions, and any retention for abuse monitoring.
  - Whether Token Factory supports per-project keys and has a usage API.
  - Credit amounts, issue dates and expiry; whether the promo code applies.
  - AI Cloud credit.
  - LoRA: whether external adapters are allowed, which Nemotron bases, and whether it bills per token or hourly.
  - Whether the rules allow judge-access restrictions.
  - Whether Colab gives hours or compute units.
  - Tavily prize criteria.
- **Day-1 shortcut.** Run Nemotron through the existing `NARAD_ENDPOINT_URL`/`NARAD_ENDPOINT_MODEL` path until the proper provider lands.
- **Gates:**
  - **Tool calling, Sep 26.** Run 20 turns of supervisor → avatar FunctionTool → Matsya tools on Super and Lightning, with streaming on and off. The gate is at least 95% valid calls on Super. If it fails: pull the per-intent tool subsets into week 1, keep Lightning off tool paths, and give Kriya one constrained action tool.
  - **Hinglish, Sep 30.** Latin-script Hinglish on Super. Show a Hinglish exchange in the video only if it passes. Keep Devanagari out of the video.
  - **Time to first token, Sep 30.** Measure end to end over the whole turn, from a family phone on Jio or Airtel, through the tunnel, at 7–10 pm IST. Pin each model to the best-measuring region and confirm connection reuse. If p50 is above about 2.5 s, drop every speed claim. Keep Ultra off interactive family turns. Label every published latency with where it was measured.
  - **ZDR.** If it isn't confirmed, remove ZDR from all copy, including the §4 diagram label.
- **Why:** At least six week-sized items rest on claims marked unverified. A wrong assumption found in week 3 costs a week.

**M4. §4 model-roles table, router row. Fix reasoning control**
- **Replace** `Set reasoning_effort="none"` **with:** "Reasoning control goes through `extra_body` or `allowed_openai_params` in `completion_options()`, or `chat_template_kwargs.enable_thinking=false`, whichever Token Factory honours when tested with curl. Every direct call site goes through `completion_options()`."
- **Why:** LiteLLM 1.83.14 raises UnsupportedParamsError for `reasoning_effort` on `nebius/` and `openai/`, or drops it silently.

**M5. §4 diagram gateway line; §5 Week 2 gateway bullets. One egress chokepoint for everything**
- **One module.** All outbound model, embedding, TTS, search and image calls go through a single `narad_egress` module. That includes guru_engine, tapas, sankalpa, the kunji key test, and imagen/veo.
- **Switch off un-routed background calls.** Tapas and Sankalpa are off in pilot and demo mode until they are routed through the gateway.
- **Embeddings.** Smriti embeddings use a local multilingual model, and the index is rebuilt.
- **Images.** Image turns never go to a cloud multimodal model: they go to local OCR (S3) or are refused.
- **Remove DeepSeek's direct API and the closed fallbacks:**
  - Remove the `deepseek/deepseek-flash` defaults and `DS_FLASH`.
  - `detect_provider()` checks `nebius/` before the `deepseek` substring.
  - `unknown` no longer maps to `deepseek_defaults`, which falls back to Gemini, then Claude, then GPT-4o.
  - In pilot mode, `NARAD_CONTEXT_FALLBACKS` holds only Nebius-hosted models and local Gemma.
  - Artemis's own Gemini screenshot loop is never used. Set `NARAD_DISABLE_ARTEMIS=1` for family profiles unless S9 passes.
- **What gets scanned.** The rules and the family name list run on all outgoing text, including Kriya page observations (after a form fill they contain real names). The ML detector runs only on text people type and on uploads.
- **Restore rule.** Change the week-2 text "placeholder restore … in tool arguments" to: "restore only into local tools and approved form fills, never into search, Tavily or model calls" (C12).
- **Fail closed.**
- **CI.** One test patches httpx, requests and litellm and fails on any host not on the allowlist. A lint fails on `litellm.completion(`, `litellm.embedding(`, `OpenAI(` or `genai.Client(` anywhere outside the gateway module.
- **Why:** Today Tapas and Sankalpa send raw text to DeepSeek's own API on every turn, and Smriti embeds with Gemini. Until that changes, the thesis and the headline number are false.

**M6. §5 Week 2 bullet "egress ledger per turn, visible in System → Trust"; §1 Stage 1 row; §3. Privacy receipt, headline number, exact claims**
- **Receipt chip on every chat message, scoped to the profile.** Tapping it shows:
  - what the person sent, next to what Nemotron and Tavily actually saw;
  - the region;
  - ZDR status (only once verified);
  - the cost;
  - anything blocked;
  - what a site received with approval, for example "clinic received: name, phone (approved by Asha)".
- **Headline:** "Across N Nemotron calls, [date range], 0 carried a real name, phone number, address or ID number. M were caught and blocked before sending."
  - The number comes from the fail-closed pre-send check over all egress.
  - Count only from the date M5's CI test went green.
  - Put it on the judge demo's receipt page too.
- **Claim wording:** "Stored on the Mac. Per turn, only a pseudonymised excerpt, which can include lab values without names, goes to Nebius." Never "health data never leaves the house". Say pseudonymised, not anonymised.
- **Don't lead with** speed, router accuracy or a seven-metric table.

**M7. §2 Token Factory row; §5 Week 1 "hard spend stop"; §7 credits. Cost controls and separate keys**
- **Pricing.** Add Nemotron (and anything else running on Nebius) to `cost_ledger._DEFAULT_PRICES`. Count unpriced tokens at the highest known price, not $0. Record background calls.
- **Enforcement.** Check spend before each call, inside the chokepoint.
- **Keys.** Separate keys (and projects, if supported) for the pilot, the benchmark and the judge instance, each with its own budget. Reconcile against Nebius usage every day.
- **When a budget runs out (C9).** Benchmark and judge keys stop hard; the judge instance then switches to replay. The pilot key moves to a fallback that can be changed in Kunji, shows an "economy mode" banner and notifies the owner.
- **Decide by Oct 20** what the family uses after the credits run out.
- **Pariksha budget.** About 12 tasks × 3 runs. The "current stack" baseline runs on synthetic data only.
- **Why:** Unpriced models record $0 today (`cost_ledger.py:15`), so the stop would never fire. 10 runs of 30 tasks would spend about $36 before any "after" run.

**M8. §5 Week 2 fast-path bullet. Streaming and `skip_summarization` as one reviewed PR (after the slice, per C2)**
- Set `skip_summarization` only for single-avatar turns.
- Run the avatar's inner mini-runner with `RunConfig(streaming_mode=SSE)` and push `text_delta` events through `_step_queue_ctx`.
- In `_event_to_sse`, handle a final `function_response`: emit `avatar_done` and set `narad_response_text`.
- Drop thought parts and `<think>` from the deltas.
- Reconcile deltas with the final text in `useAvatara.ts`.
- Include the streamed restore with a 40-character holdback.
- Budget 3–4 owner-days.
- **Why:** Without the `_event_to_sse` fix, `avatar_done` never fires, and checkpoints, workflow completion and memory writes silently stop.

**M9. §3; §5 Week 2 Anumati bullet; move Web Push from Week 3 to Week 2. Care circles and hash-bound approvals**
- **Care-circle table:** delegator, delegate, action class, and a flag for whether the delegate can see the detail. The delegating person sets it (the owner can help). It is visible to both people, and changes are logged. The default approver is the requester (C4).
- **Approval delivery.** Cards go to the approver's phone by Web Push on Android Chrome, replacing ntfy. They are hash-bound. The lock screen shows only "Narad needs your OK". The card is filled by a fixed template in the approver's language from the hash-bound fields (amount in ₹, date, merchant or clinic, who asked), not by LLM text. The approver sees those fields, not the source document.
- **Buttons:** Approve / Reject. Edit is droppable (M2). An expired approval counts as a reject.
- **What needs approval.** Only commit-class actions, under the v2 risk policy: not search, Enter, "Sign in" or cookie banners. One plan envelope covers each task.
- **No self-confirmation.** Remove the LLM-reported `confirmed` from `phone_use` and use the proposal check there too.
- **Why:** The plan says approvals go "to the right person" but never defines who that is. This is the moment other entries can't copy.

**M10. §5 Week 3 Kriya bullets and the §5 Week 4 outcome contract. Narrow Kriya v0**
- Isolated Playwright only, with Super as the operator.
- Call `BrowserSessionManager.open/observe/execute` directly.
- Viewport-scoped observations with real ARIA roles. Only the latest observation is kept in full.
- Settle and verify after each action, with 5 s timeouts. Cancel on the server.
- Durable resume is deferred.
- **Security.** A `page.route` abort blocks loopback and LAN addresses on every sub-request, not only on navigation; this protects the Artemis admin API on 127.0.0.1:8124 and the home network. A per-task domain allowlist (clinic only) is what blocks the injection in the video, with no OpenShell dependency.
- **Gate:** the clinic fixture passes 9/10 by Oct 17.

**M11. §5 Week 4 judge-instance bullets; §1 hosted-demo row; §7 "defaults to replay mode". Judge instance: earlier, and open**
- **Skeleton on Oct 6–7:**
  - a Dockerfile and a Nebius CPU VM;
  - its own `NARAD_HOME`, created by `init_workspace` plus a synthetic seed;
  - its own key, hostname and tunnel;
  - Nemotron only: no Claude, DeepSeek or Qwen.
- **Access (C5).** No invite codes, no per-judge caps, no status page. One global rate limit and daily budget cap. Replay only after the cap, with a label on the page.
- **Week-4 hardening: a guided two-pane page.** Papa's phone and Asha's phone side by side on a desktop, the sample report preloaded, and one Start button that runs the story live on Nemotron with the receipt visible.
- **Why:** It is a hard submission requirement and there is no Dockerfile today. A first Linux deploy 8 days before the freeze is the classic slip.

**M12. §2 LangSmith row; §6 closing rule; §7 privacy bullet. Keep synthetic work out of the pilot**
- **Separate homes.** Pariksha and the judge instance each have their own `NARAD_HOME`. Pariksha refuses to start if its home resolves to the pilot's home.
- **Tracing off in the pilot.** `Start Family Pilot.command` unsets `LANGSMITH_*`, `LANGCHAIN_TRACING*` and `OTEL_EXPORTER_*`. The server asserts at startup that no tracing callbacks are registered when `NARAD_AUTH=strict`.
- **Family name list.** A gitignored runtime lookup only. Never uploaded to Colab, Toloka or LangSmith, and never used as training data.
- **Other precautions:**
  - Colab runs under a separate Google account and never mounts the family's Drive.
  - A pre-push hook scans for family names and the pilot hostname.
  - Claude Code is denied read access to `~/.narad`. Debugging uses a redacted bundle.

**M13. §1 public-repo row (new pre-publication step). Stop publishing the family's address and names**
- Move the pilot hostname into `.env`, out of `Start Family Pilot.command:2,9` and `OnboardingFlow.tsx:504`.
- Switch to a new hostname that has never been committed; the old one has been in history since b39599c.
- `GET /profiles` returns initials only, or requires a device token.
- Consider Cloudflare Access with 30-day sessions in front of the pilot.
- The judge instance gets its own hostname.
- **Why:** Anyone can read the family members' names and keep them locked out with the PIN backoff.

**M14. §5 Week 2 (new bullet). Close the Phase 0 carryovers before any non-owner member joins**
- Scope `/karma`, `/karma/mutations`, `/andon/log`, `/sutras` and the audit part of `/search` to the caller's profile.
- Make `artifacts/<run_id>/` readable only by its owner.
- Build the new receipt, Activity inbox and live view scoped to the profile from the start.

**M15. §5 Week 4 "Pilot: all four family members on the new build"; §4 backup sentence. Staged rollout, pilot channel, backups**
- **Rollout (C1).** The owner's profile moves to the Nemotron build first; the exact date is an owner question. The first non-owner member joins around Oct 6–8, gated. All four join on the tagged build on Oct 15.
- **Logging.** From the first member onward, log errands, approvals, blocked leaks and cost, as counts only.
- **Pilot channel.** After Oct 15 the pilot build gets fixes only; features land on the dev or judge instance first. Every feature has a flag, and there is a one-command rollback to the last tag. OpenShell, the router and Safety Guard stay off for family profiles until each passes on the family-style test set.
- **Backups, replacing "revisit backup after the pilot" for the local part:**
  - an APFS snapshot of `~/.narad` before every deploy;
  - a nightly encrypted local backup;
  - one restore drill before Oct 15.

**M16. §8 Q3 and §5 Week 1 (new item). Written consent**
- **Form.** Hindi and English, in the app, versioned and timestamped, one per adult.
- **It covers:**
  - what is stored on the Mac;
  - the owner's admin reach, including the reset-pin route (owner actions on another person's profile are logged to that person's inbox);
  - every destination: Nebius EU and US (Ultra), Tavily, Cloudflare's TLS edge, any Google voice service still in use, and the fallback provider;
  - the scope of phone control;
  - what gets published;
  - how to pause, withdraw or have their data deleted.
- **Publishing.** Each adult approves the exact slide before it goes out.
- **Phones.** Each phone is granted to exactly one profile, its holder.

**M17. §3 last thesis sentence; §5 Week 4 pilot bullet. Honest pilot numbers and the Oct 20 checklist**
- **Metric rules:**
  - Commit the metric definitions, with a date, before the first non-owner member joins.
  - Collect counts, durations and outcomes only, never prompts.
  - Exclude owner testing, benchmark and synthetic runs.
  - Report n and the date range. When n is under 30, show counts, not percentages.
  - Count abandoned tasks as failures.
  - No per-person breakdown and no separate health or finance counts.
- **Before/after numbers come only from Pariksha.** The pilot has no "before" period.
- **Threshold.** Say "real pilot" only if at least 3 of 4 members were active on at least 5 days between Oct 15 and 24. Otherwise say "early pilot (owner + 1)".
- **Oct 20 checklist** (the pilot lens's 10 items, trimmed to this scope):
  1. The installed PWA on every member's phone, with their own PIN.
  2. Streaming, with measured time to first token.
  3. All family egress goes through the gateway and appears in each person's receipt, with zero traffic to DeepSeek's own API or Gemini screenshots.
  4. Latin-script Hinglish works, and Devanagari turns go to a labelled fallback.
  5. Hash-bound approvals decided within 10 s of the push.
  6. Stop works from the phone.
  7. Health (PDF, then confirmed values, then a push reminder) and Travel (search, then a hand-off) used for real.
  8. `launchd` running and at least 95% uptime while people are awake.
  9. Consent recorded.
  10. The pilot key's budget or fallback tested through Dec 15.
- **If the checklist isn't green:** the video says "pilot in progress" and cites no success rates.

**M18. §7 first bullet. Keep the Mac host up**
- **Replace** `pmset disablesleep` + watchdog **with:**
  - `launchd` KeepAlive units for the backend, tunnel and Artemis, with the launcher's environment. Today `caffeinate -i` doesn't stop sleep when the lid is closed, and the launcher shuts everything down when any one service exits.
  - FileVault kept on. Use `fdesetup authrestart` for planned restarts, and turn off automatic macOS updates until Dec 15.
  - An external uptime ping that alerts the owner's phone.
  - Lid open, the Mac on a stand. The router and ONT on the inverter or UPS.
  - No sweeps, model downloads or recording on the host between 7 and 10 pm IST. Run sweeps on the VM or overnight, and log thermal state with the results.
  - Measure the 24 GB memory budget with the PII models, Chromium, Artemis and Gemma all loaded.

**M19. §6 (all three items); §4 diagram and table; the Colab bullets in weeks 1–3; §2 Toloka row. Cut what dilutes the story**
- **Cut:**
  - `narad-pii-in` and the 1,200 Toloka labels. Replace them with stock gliner-PII, the India-ID rules, the family name list, and a synthetic leak set of about 250 items whose labels are known by construction (Hinglish, Devanagari names, Indian IDs).
  - The operator-model batch evaluation.
  - The Qwen and DeepSeek benchmark arms.
  - The week-1 Colab generator. The label schema isn't frozen yet.
- **Keep Claude Sonnet pilot-only.** It is off in the demo, the judge instance, the video and the README headline.
- **Model comparisons are Nemotron-only:** Lightning vs Super vs Ultra.
- **Out of the video:** the Teach clip, flight booking as its own segment, and the seven-metric scorecard. The scorecard becomes one Devpost image and a README section.
- **Out of anything judges see:** the avatar and Sanskrit subsystem names.

**M20. §6.1 "the headline"; §4 router row; Week 2/3 router bullets. Demote `narad-router` to a gated stretch goal (C6, C7)**
- **Go** only if both hold:
  - By Oct 7, the facts check out: a Nemotron base proven trainable with a 100-example smoke run, or Token Factory's own fine-tuning supporting a Nemotron base; Token Factory serving it billed per token, not hourly; and a frozen label schema.
  - By Oct 12, the slice and the receipt work.
- **Prefer Token Factory's own fine-tuning** over Colab, because Colab doesn't count as Nebius. If the weights can be exported, keep them.
- **Show it only** if the difference is large and visible (for example, 3 calls → 1).
- **Family use** only if it beats Lightning on a Hinglish routing set.
- **In the §4 router row,** replace the LoRA with: "deterministic pre-router → Lightning few-shot → Super (measured)".

**M21. §5 Week 5 video bullet; §1 video row. Adopt the judge lens's 2:50 storyboard, with these changes**
- **Devices and setup.** Two spare or reset demo phones pointed at the judge instance (C10), mirrored with scrcpy, Do Not Disturb on.
- **Assets:**
  - a synthetic family;
  - a fully synthetic lab report (PDF and photo) with a fake Aadhaar;
  - seeded June HbA1c in the synthetic health.db.
- **Editing.** Captions throughout. Label every sped-up clip.
- **Shot order:**
  - Nebius and Nemotron named on screen by 0:10–0:35.
  - Add a 3-second beat of Papa confirming the extracted values next to their crops (S4).
  - The injection block is captioned "blocked by the task allowlist". Say OpenShell only if it was actually used.
  - The reminder arrives by Web Push, or by a deterministic ADB alarm intent on the demo phone.
  - No architecture diagram before 2:05.
- **Numbers on screen.** The test-suite line uses the real Pariksha numbers ("n tasks × 3 runs, 0 unsafe actions"), not the placeholder 27/30. The pilot line appears only if M17's threshold is met and consent is given. Show a Hinglish exchange only if its gate passed.

**M22. §3 second bullet; §5 Week 3 Android bullet. Phone control is off for family phones by default**
- **Enabling it needs all of these, per phone:**
  - Advanced Protection stays on.
  - Banking and UPI apps are checked first, because some refuse to run with Developer options on. If that's a problem, limit control to the owner plus one volunteer.
  - The accessibility service is on over ADB only for the length of a task, with a visible "Narad is controlling this phone — Stop" notice.
  - Banking, UPI and WhatsApp packages are denied.
  - When the phone is away from home, the task is queued ("I'll do it when you're home") instead of blocking.
  - Its numbers are reported separately, with their own n.
- **Remove "drives your phones" as a pillar.**

### SHOULD

**S1. New §5 design line (Week 2–3). One mobile-first design pass on exactly four screens**
- Chat with the receipt chip, the approval card, the live task view and the Activity inbox.
- Plain English labels (Approvals, Activity, Privacy receipt). Keep the Madhubani borders and Devanagari lettering as texture.
- Add a service worker for Web Push.
- **Why:** Design is 25% of the score, the current screenshots are desktop views, and the plan has no design work.

**S2. §5 Week 2 Anumati. Refine approvals further**
- Quiet hours from 22:00 to 07:00 IST, which the approver can override.
- Payment-class approvals need the PIN or a WebAuthn fingerprint.

**S3. §4 diagram ("OCR"); §5 Week 3. Local OCR for photos**
- Apple Vision through ocrmac, about half a day to a day. Fall back to a shared PDF.
- The first beat of the video depends on it, and without it image turns would go to the cloud.

**S4. §5 Week 4 outcome contracts. Health and payment safety in the real pilot**
- Extracted values appear next to their crops, and the person confirms them before anything is written to health.db.
- Out-of-range flags use only the report's own reference ranges. Advice goes no further than "discuss with your doctor".
- Reminders go by Web Push.
- On real sites the agent never enters payment details. Bookings end at a UPI deep-link hand-off to the approver.

**S5. §5 Week 4 (demo seed). Show memory as a health trend**
- Seed June HbA1c 7.6 so the reply says "up from 7.6 in June".
- Only the change, without a name, goes to Nemotron.

**S6. §5 Week 1 Tavily bullet. Put Tavily inside the receipt**
- De-identified queries appear as their own receipt line.
- Use Tavily to cite the guideline in Papa's answer.
- This is a bid for the $3K Tavily prize (criteria checked in M3).

**S7. §4 Hindi line; §5 Week 1 measurements; §5 deferred list. Hindi and Hinglish for the family**
- A family-style test set: Latin-script variants, Devanagari from Gboard voice input, and code-mixed health terms. Hindi-speaking family members rate it.
- Replies in each person's script, set per profile. The default model is chosen per profile.
- Devanagari forms of family names in the name list, and Devanagari digits folded to Latin. ML-based Devanagari redaction stays deferred.
- Add Hindi and Hinglish phrases to the local crisis rule, and Tele-MANAS 14416 alongside iCall.
- Remove the "passport number" hard block (`phase-1/server.py:889`).
- Voice uses local faster-whisper, or browser speech-to-text is listed in the receipt.

**S8. §4 Safety Guard row. Decide by Oct 10 (C8)**
- Either schedule it (in parallel, shadow mode, never fail-open) or remove it from the table and the diagram.
- Change "Replaces the regex-only input gate" to "Runs alongside the local rule gate, adding to it only."

**S9. §5 Week 1 trials (Artemis). Replace the trial with a half-day spike on Sep 29**
- The question: can Artemis's planner take an OpenAI-compatible endpoint and accessibility-tree input?
- If not, use a deterministic ADB intent (SET_ALARM) or Web Push. Don't fork Artemis before Oct 30.
- Decide on Oct 3.

**S10. §5 Week 1 trials (OpenShell). Move it to a one-day timebox in Week 3, only if Kriya is green**
- Check first whether it needs Linux, which would mean Docker or colima on the 24 GB Mac.

**S11. §5 Week 1 Pariksha. Narrow it to what the story needs**
- The clinic fixture, with an injection page and a payment step.
- The lab report as PDF and photo.
- About 10 latency prompts and the synthetic leak set.
- One command that produces a scorecard, running in its own isolated home.
- The flight fixture only for pilot Travel.

**S12. §5 Week 5 write-ups, moved to day 1. The Devpost package**
- A dated CHANGELOG, and a Nebius/NVIDIA feedback log written daily.
- README and Devpost drafts every Friday.
- Five images: the two-phone approval (also the thumbnail), the receipt side by side, the split-brain diagram, the pilot numbers, the scorecard.
- A description whose sections mirror the four judging criteria.

**S13. §5 Week 3 live-view bullet. Retention**
- Purge phone-use and computer-use screenshots 7–14 days after each task. They pile up today (`artemis_adapter.py:378`).
- Never store live-view frames. Live view is tap-to-view, not an automatic stream.

**S14. §5 Week 1 (launcher). Close incidental egress**
- Set `OPENSHELL_TELEMETRY_ENABLED=false`, `NEMO_GUARDRAILS_NO_USAGE_STATS=1` and `HF_HUB_DISABLE_TELEMETRY=1`, plus `HF_HUB_OFFLINE=1` once the models are downloaded.
- Self-host the fonts (`index.css:1` loads them from Google).
- List Cloudflare, as transport, in the receipt.

**S15. §3 (new paragraph). A problem statement and a target user**
- The "family IT desk": the adult child who manages ageing parents' reports, bookings and forms in an Indian multigenerational household.
- Back it with one real, anonymised errand story and 2–3 written quotes from family members, with consent.

### COULD
- **C-a (§4 diagram).** Show Safety Guard as a second NVIDIA model with a real job, but only if S8 schedules it and it runs.
- **C-b (§5 Week 3).** A full Activity inbox screen, beyond the three montage cards.

### Rejected or deferred (deduplicated away)
- **Local-only extraction of lab values** (pilot, could): deferred as a per-profile pilot option after Oct 30 (C11).
- **ADB over Tailscale** (pilot, could): after Oct 30. Owner hours are too scarce.
- **Invite codes, per-judge caps, status page** (plan, feasibility): dropped (C5).
- **Safety Guard replacing the regex gate** (judge): rejected (C8).
- **Router portability** (pilot) and **the day-1 custom endpoint** (feasibility): folded into M20 and M3.

---

## Questions only the owner can answer

1. **Is `vn-envy/Narad` already public?** If so, the hostname and the family-name exposure (M13) are live now, and the hostname change is urgent.
2. **Is any family member using the pilot today?** If so, their turns reach DeepSeek's own API through the main chain and Tapas/Sankalpa right now. Pause them, or switch them to the owner-only build?
3. **Your hours.** How many per week until Oct 30? Solo, or with others? How much time can you give to family support between Oct 15 and 30?
4. **Do you accept the pivot?** One lab-report errand in the video instead of Travel/Health/Teach (M1), and the router moving from headline to stretch goal (M20)?
5. **Your own profile before the gateway.** Is it OK for your own turns to reach Nebius un-pseudonymised for a few days before Oct 5, or should you wait for the slice?
6. **Family facts.** Ages (anyone under 18?). Real relationships, and whether any parent actually wants to delegate approvals to an adult child (care circles in the pilot, not just the demo). How much of their use is Hindi or Hinglish, who uses voice, and which script they prefer for replies.
7. **Phones.** Maker, and banking and UPI apps, per family phone. Will you allow Developer options on any of them, or limit phone control to you plus one volunteer?
8. **Filming.** Do you have two spare Android phones to reset for filming? If not, what is available?
9. **Money.** Your monthly card cap after the credits run out, and the pilot's economy-mode fallback (local Gemma? Claude Sonnet 5 at $2/$10 for Devanagari and sensitive turns?). Is the judge instance's live mode through Dec 15 budgeted on the card?
10. **Lab values in the real pilot.** De-identified to Nebius (as in the demo), or local-only even if the explanations are weaker?
11. **Claude Code.** Does it run on the host Mac? If so, it can read `~/.narad` unless denied.
12. **The house.** Is there inverter or UPS backup for the router and ONT? Where does the Mac sit, and how hot does that spot get?
13. **Before the build.** Is there any record of the family's usage before this build? Without one, all before/after numbers must come from Pariksha.
14. **Consent.** Will each adult individually consent, including to 2–3 written quotes and one anonymised real errand story?
15. **Business relationship.** Any with Nebius or NVIDIA? The rules exclude some.
16. **Colab.** Hours or compute units? This matters only if the router goes ahead.