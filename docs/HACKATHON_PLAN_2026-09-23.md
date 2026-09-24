# Narad × Nebius/NVIDIA: one build for the family pilot and the hackathon (v2)

> **Deferred (2026-09-24).** The owner paused the hackathon to focus only on the family pilot. This plan is kept for reference. The pilot direction lives in [PILOT_READINESS_PLAN](./PILOT_READINESS_PLAN_2026-09-23.md).

*2026-09-23. Event: [Nebius x NVIDIA Global AI Hackathon](https://nebiusglobalaihackathon.devpost.com/) (Devpost), **Personal AI** track. Deadline **Fri 2026-10-30, 10:00 PT (22:30 IST)**.*

v2 applies an independent three-lens review (hackathon judge, build feasibility, family-pilot safety). The full review, including how the lenses' conflicts were resolved, is in [HACKATHON_PLAN_REVIEW](./HACKATHON_PLAN_REVIEW_2026-09-23.md). Research and verification status: [HACKATHON_RESEARCH](./HACKATHON_RESEARCH_2026-09-23.md) and [VENDOR_RESEARCH](./VENDOR_RESEARCH_2026-09-23.md). Items marked *(unverified)* are checked on **Sep 24** (§5): our sandbox could not reach devpost.com, nebius.com or the Token Factory docs.

**What v2 changed:**

| Area | v1 | v2 | Why |
|---|---|---|---|
| Demo | Three workflows | One errand told on two phones | Design and Impact are half the score, and one story reads as a product |
| Headline idea | The router fine-tune | The split brain plus a privacy receipt | Visible on screen and hard to copy |
| Router fine-tune | Headline | A gated stretch goal | Not the core of the story |
| Order of work | Features week by week | The whole flow built crudely first, with dated gates and cut lines | The original plan needed about 55–60 owner-days against about 30 available |
| Family pilot | Joins with the features | Joins only after privacy leak tests pass on *every* egress path | Today, two background learners send raw text to DeepSeek's own API |

---

## 1. What the rules demand

| Requirement | Consequence |
|---|---|
| Runtime calls to **Nebius Token Factory** (or execution on Nebius AI Cloud), plus **≥ 1 NVIDIA open model** | **Nemotron on Token Factory becomes Narad's default brain** everywhere. Stage 1 fails "superficial rebrands". |
| Stage 2 has four equal criteria: **Technological Implementation** (the tiebreaker), **Design**, **Potential Impact** ("based on what's demonstrated"), and **Quality of Idea** ("creative, non-obvious use") | Every gate below maps to at least one criterion. |
| Pre-existing projects need a written account of what was significantly updated since Aug 26 | Keep a dated `CHANGELOG` from today. Phase 0 counts. |
| Public repo with an OSI licence, and a README naming the NVIDIA models and Nebius services | Narad is Apache-2.0. **The repo is public**, so see §7 on family exposure. |
| A YouTube video under 3 minutes, on the intended device | Two **demo** Android phones (not family phones) against the judge instance. |
| Hosted demo "free and unrestricted" until **Dec 15** | A judge instance on a Nebius AI Cloud CPU VM: no invite codes, one global rate limit and budget cap, and replay only after the cap is hit, labelled on the page. |
| Feedback on Nebius and NVIDIA tech | A feedback log written daily from day 1. There are reportedly 10 × $100 feedback prizes *(unverified)*. |
| Prizes: $20K / $10K / $6K, per-track prizes, **$3K Best Use of Tavily** *(unverified)* | Tavily becomes Narad's search, and appears inside the privacy receipt. |

## 2. Resources

- **Nebius Token Factory credit.**
  - **$25** from the Builder Program email.
  - A possible **$25** promo code and a second **$25** Builder tranche *(unverified)*.
  - Credits expire **90 days after issue** *(unverified)*, so the Sep 10 grant lapses around Dec 9, **before judging ends**. The judge window is budgeted on the card.
- **Nebius AI Cloud:** up to $50 *(unverified)*. Hosts the judge VM. There is no India region.
- **Partner credits:**
  - **Tavily $25:** search.
  - **LangSmith $100:** synthetic benchmark traces only, never the pilot.
  - **Toloka $50** and **Tandem $50:** unused for now. The Toloka labelling project was cut, see §6.
- **Colab Pro+ (~1,800 hours or compute units, to confirm):** only for the stretch router (§6). It is not a Nebius runtime.
- **Host:** an **M5 MacBook Air, 24 GB, fanless**. It is the family control plane and runs the local PII models, local OCR and the ADB bridge. Its risks are sleep, heat and memory (§7).

**Cost controls (must):**
- Nemotron prices go into `cost_ledger.py`. Unknown models are charged at the highest known price, never $0.
- Spend is checked before each call.
- **Separate keys:** pilot, benchmark and judge.
- **When a budget runs out:**
  - the benchmark and judge keys **stop hard** (the judge instance falls back to replay);
  - the pilot key drops to **economy mode** with a banner, and the owner is notified.
- Spend is reconciled against Nebius usage every day.

## 3. The story

> **Narad is one private AI for a whole family, built for the person who handles everyone's reports, bookings and forms.**
>
> It runs on a **split brain**:
> - **On the family Mac:** NVIDIA gliner-PII, India-ID rules and the family name list work out *who* is involved. That never leaves the house.
> - **On Nebius Token Factory:** Nemotron works out *how*.
>
> ***Nebius knows how. Only your home knows who.***
>
> **Care circles** decide which family member approves what, on their own phone. Every message carries a **privacy receipt**. Memory, health and finance records are stored on the Mac. Per turn, only a pseudonymised excerpt goes to Nebius; it may include lab values, but never names.

**Target user:** the "family IT desk", meaning the adult child who manages ageing parents' reports, bookings and forms in an Indian multigenerational household. With the family's consent, back this with one real anonymised errand story and two or three written quotes.

**The number that sticks:** "Across *N* Nemotron calls between *[dates]*, **0** carried a real name, phone, address or ID number; *M* were caught and blocked before sending." This comes from the fail-closed pre-send check over **all** egress, counted only from the day the egress CI test (§5, Oct 1–5) turns green.

**Claim discipline:**
- Say **pseudonymised**, never "anonymised".
- Never say "health data never leaves the house".
- Latency, Hinglish and zero data retention (ZDR) are claimed only if measured or verified.
- Pilot numbers follow the §7 rules.
- No avatar or Sanskrit subsystem names in anything a judge sees.

## 4. Architecture

```text
 4 family Android phones (PWA: chat + receipts, approvals, Activity; Web Push)
        │ HTTPS · new Cloudflare hostname (not in git) · consider Cloudflare Access
┌───────▼───────────── M5 MacBook Air · family control plane ─────────────────────┐
│ Narad server (Phase 0 identity & safety floor) · care circles · Anumati approvals  │
│ Stored here only: memory, health.db, finance.db, workflows.db, vault, name list    │
│ narad_egress — the ONE chokepoint for every model/embedding/search/TTS/image call: │
│   India-ID rules + family name list on all outgoing text (incl. page observations) │
│   + NVIDIA gliner-PII on typed text & uploads · fail closed · spend check · receipt│
│   restore placeholders only into local tools & approved form fills                 │
│ Local: OCR (Apple Vision) · multilingual embeddings · Kriya (isolated Playwright)  │
└──────┬──────────────────────────────┬──────────────────────────┬─────────────────┘
       │ pseudonymised text            │ de-identified queries     │ synthetic only
┌──────▼───────────────────────┐ ┌─────▼──────┐ ┌─────────────────▼──────────────────┐
│ Nebius Token Factory         │ │ Tavily     │ │ Nebius AI Cloud CPU VM: judge demo │
│ Nemotron 3 Super: worker     │ └────────────┘ │ synthetic family · fixture sites   │
│ Nemotron 3.5 Lightning: fast │                │ own key/home/hostname · replay cap │
│ Nemotron 3 Ultra: escalation │                └────────────────────────────────────┘
│ (Safety Guard: shadow, gated)│
└──────────────────────────────┘
```

**Model roles.** Exact model IDs and prices are confirmed from `GET /v1/models` on Sep 24.
- **Router:** deterministic pre-router, then Lightning few-shot, then Super. Measured, not assumed.
- **Worker:** Super.
- **Escalation:** Ultra, and never on an interactive family turn unless it's measured fast enough. It always falls back to Super.
- **Reasoning control:** goes through `completion_options()`, using whichever of `extra_body`, `allowed_openai_params` or `chat_template_kwargs.enable_thinking` Token Factory honours when tested with curl. LiteLLM 1.83 rejects or drops `reasoning_effort` for `nebius/`.

**Everything else stops leaving the house by the Oct 5 slice:**
- Tapas and Sankalpa are **off** until they go through the chokepoint.
- Smriti embeddings become **local** (multilingual), and the index is rebuilt.
- Image turns go to **local OCR**, never a cloud multimodal model.
- The `deepseek/deepseek-flash` defaults and the closed-model context fallbacks are removed from pilot mode.
- Artemis's own Gemini screenshot loop is never used for family profiles.
- A CI test blocks any host that isn't on the allowlist, and a lint rule rejects direct `litellm.completion(` / `OpenAI(` / `genai.Client(` calls outside the chokepoint.

**Provider decisions:**
- Grok stays out.
- DeepSeek exists only as Nebius-hosted open weights behind the gateway, and is not used in the demo.
- Claude Sonnet 5 is **pilot-only and owner-enabled** (for example for Devanagari or sensitive turns). It is off in the demo, the judge instance, the video and the README headline.

## 5. Build plan: whole flow first, gates, cut lines

**Owner-hours reality:** about 100 hours of Mac/phone testing are available. Every item marked 🧪 needs the owner's devices. Every Sunday (**Oct 5, 11, 18**) a rough 3-minute cut of the video is recorded. Anything that isn't in a cut waits until after Oct 30.

| Date | What happens | Gate |
|---|---|---|
| **Sep 24** | **Facts check** (owner browser + a script we provide): model IDs, ZDR switch and regions, per-project keys and usage API, credit amounts/dates/expiry, promo code, AI Cloud credit, LoRA terms, judge-access rules, Colab units, Tavily prize criteria. Meanwhile Nemotron runs through the existing `NARAD_ENDPOINT_URL` path. | Results written into VENDOR_RESEARCH |
| Sep 24–27 | `nebius/` provider with explicit `api_base`, `detect_provider()` checking `nebius/` first, reasoning control, prices, per-key budgets. Dated CHANGELOG and feedback log started. | — |
| **Sep 26** 🧪 | Tool-calling gate: 20 supervisor→avatar→tool turns on Super and on Lightning. | **≥ 95% valid calls on Super.** Otherwise tool subsets move forward, Lightning stays off tool paths, and Kriya gets one constrained action tool. |
| Sep 29 🧪 | Artemis spike (half a day): can its planner use an OpenAI-compatible endpoint with accessibility-tree input? | If not, use a deterministic ADB alarm intent or Web Push for the reminder. No Artemis fork before Oct 30. |
| **Sep 30** 🧪 | Hinglish (Latin script) on Super. Time to first token over the **whole turn**, from a family phone on Jio/Airtel through the tunnel, at 7–10 pm IST, with each model pinned to its best region. ZDR confirmation. | No speed claims if p50 is above ~2.5 s. No Hinglish in the video unless it passes. No "ZDR" anywhere unless confirmed. |
| **Oct 1** | Pariksha baseline, **12 tasks × 3 runs**, in its own isolated home: the clinic fixture (with an injection page and a payment step), the lab report as PDF and photo, about 10 latency prompts, and a synthetic leak set of about 250 items (Hinglish, Devanagari names, Indian IDs, labels known by construction). | One command produces the scorecard |
| **Oct 1–5** | **Vertical slice:** Super as the default brain, the lean gateway (rules + name list, whole messages), the egress chokepoint and its CI test, the receipt chip, an approval push to a second profile (ntfy is fine for this cut), and Playwright on the clinic fixture. Also Phase 0 carryovers (per-profile `/karma`, `/andon/log`, `/sutras`, `/search`, `artifacts/<run_id>`) and moving the pilot hostname out of git into `.env` on a new name. | **Rough cut #1, Oct 5** |
| Oct 6–7 | Judge instance skeleton: Dockerfile, Nebius CPU VM, its own `NARAD_HOME`, synthetic seed, key and hostname, Nemotron only. | Serving live |
| Oct 6–8 🧪 | **First non-owner family member**, only if the leak tests are green on every egress path, the carryovers are closed, consent is recorded and the metric definitions are committed. | §7 checklist |
| Oct 6–10 | **Streaming PR:** avatar mini-runner with SSE streaming, `text_delta` events, `skip_summarization` for single-avatar turns, `_event_to_sse` handling of the final function response (otherwise `avatar_done`, checkpoints and memory writes silently stop), thought parts stripped, streamed placeholder restore with a 40-character holdback. Then NVIDIA gliner-PII on typed text and uploads. | Measured time to first token |
| Oct 8–12 🧪 | **Care circles + Anumati:** a hash-bound `ActionProposal`, cards on the approver's phone via **Web Push** (service worker; replaces ntfy), a lock screen that shows only "Narad needs your OK", a card built from a fixed template of the hash-bound fields in the approver's language, Approve / Reject (Edit only if time allows), expiry counting as reject, only commit-class actions gated, one plan envelope per task, and no LLM self-confirmation, including in `phone_use`. | Approval decided within 10 s of the push |
| **Oct 10** | **Cut line 1.** You are behind if streaming isn't merged, leak tests aren't green, or Pariksha can't produce a scorecard. | Drop, in this order: router LoRA → OpenShell → Artemis as an agent → Safety Guard → skills loaded on demand (keep tool subsets) → Edit → Activity screen (use 3 notification cards) → live-view frames (use a captioned Playwright recording) → any Hinglish claim |
| Oct 10–17 🧪 | **Kriya v0 on the clinic fixture:** isolated Playwright only, Super as the operator, viewport-scoped observations with real ARIA roles, only the latest observation kept in full, settle and verify with 5 s timeouts, server-side cancel. `page.route` blocks loopback and LAN addresses on every sub-request, and a per-task domain allowlist blocks the injection. Local OCR (Apple Vision) for the photographed report. Health outcome contract: values shown next to their image crops, confirmed by Papa before anything is written, flags only from the report's own reference ranges, "discuss with your doctor". Mobile design pass on four screens: chat + receipt, approval card, live task view, Activity. | — |
| Oct 12 | Router gate (§6) | Go / no-go |
| **Oct 15** 🧪 | **Pilot build tagged; all four family members.** After this the pilot gets fixes only, behind feature flags, with one-command rollback. APFS snapshot before each deploy, nightly encrypted local backup, one restore drill. | — |
| **Oct 17** | Clinic fixture reliability | **Passes ≥ 9 of 10 runs** |
| Oct 17–20 | Reliability loop, judge-instance hardening (the two-pane "Papa's phone / Asha's phone" page with a Start button), and the after-scorecard. | — |
| **Oct 20** | **Cut line 2.** You are behind if the clinic fixture is below 9/10 or the judge instance isn't serving live. | Health falls back to PDF → health.db → push reminder. Pilot numbers shown as raw counts or left out. After-scorecard covers only the tasks that ran. |
| Oct 21–22 | Stretch work only if every gate is green. README/Devpost drafts (drafted every Friday from Sep 26). | — |
| Oct 23 / 24 / 25 / **28** | Freeze / dress rehearsal / film / **submit** (two days of buffer) | — |

**Never cut:**
- Nemotron as the default brain.
- The egress chokepoint and receipt.
- Spend stops.
- The judge instance.
- The video, README, feedback section and Aug 26 changelog.
- Synthetic-only public material.

**Deferred past Oct 30. The pilot keeps going, and this is where the rest of the original computer-use and phone-use agenda lives:**
- The full Kriya runtime: signed-in browser, desktop control, cloud browser pool.
- The Narad Companion app for phones away from home.
- ADB over Tailscale.
- Career and Finance depth.
- ML redaction for Devanagari.
- Full Phase 7 operations.
- Local-only lab-value extraction as a per-profile option.

## 6. Fine-tuning: a gated stretch goal

**`narad-router`** is a Nemotron LoRA that routes household turns: workflow, tool subset, data class, and whether to answer directly.
- **Go only if both of these hold:**
  - **By Oct 7:** a Nemotron base proven trainable with a 100-example smoke run, Token Factory serving it billed per token (not hourly), and a frozen label schema.
  - **By Oct 12:** the slice and the receipt work.
- **Training:** prefer Token Factory's own post-training, because Colab doesn't count as Nebius. Colab is the fallback for training or evaluation. Synthetic data only.
- **Showing it:** only if the win is large and visible, for example a simple turn going from 3 model calls to 1.
- **Family use:** only if it beats Lightning on a Hinglish routing set.

**Cut from v1** because they dilute the story:
- the `narad-pii-in` fine-tune and the 1,200 Toloka labels (stock gliner-PII + rules + name list + the synthetic leak set is enough);
- the Colab batch evaluation of operator models;
- DeepSeek/Qwen benchmark arms. Model comparisons are **Nemotron only**: Lightning vs Super vs Ultra.

## 7. Protecting the real family pilot

- **The repo is public.**
  - Move the pilot hostname out of `Start Family Pilot.command` and `OnboardingFlow.tsx` into `.env`, and switch to a **new** hostname. The current one has been in git history since `b39599c`.
  - `GET /profiles` must return initials only, or require a device token. Today anyone can read family display names and use the PIN backoff to lock people out.
  - A pre-push hook scans for family names and the pilot hostname.
- **Separate homes.**
  - Pariksha and the judge instance have their own `NARAD_HOME`, and Pariksha refuses to start on the pilot's home.
  - The pilot launcher unsets `LANGSMITH_*`, `LANGCHAIN_TRACING*` and `OTEL_EXPORTER_*`. The server asserts that no tracing callbacks are registered in strict mode.
  - Colab uses a separate Google account and never mounts the family's Drive.
  - The family name list is a gitignored runtime file only.
  - Claude Code is denied read access to `~/.narad`.
- **Consent, in Hindi and English, per adult, versioned, inside the app.**
  - It covers:
    - what is stored on the Mac;
    - the owner's admin reach (owner actions on someone's profile are logged to that person's inbox);
    - every destination: Nebius EU/US, Tavily, Cloudflare's edge, and the fallback provider;
    - the scope of phone control;
    - what gets published, and how to pause, withdraw or delete.
  - Each adult approves the exact slide that will be published.
- **Honest pilot numbers.**
  - Metric definitions are committed with a date before the first non-owner member joins.
  - Only counts, durations and outcomes are recorded, never prompts. Owner testing and synthetic runs are excluded.
  - Always report *n* and the date range. Below 30, show counts rather than percentages.
  - Abandoned tasks count as failures.
  - No breakdown per person, and no separate health or finance counts.
  - Before/after comparisons come only from Pariksha.
  - Say "real pilot" only if at least 3 of 4 members were active on at least 5 days between Oct 15 and 24. Otherwise "early pilot (owner + 1)".
- **Oct 20 checklist.** If it isn't green, the video says "pilot in progress" and quotes no rates.
  1. PWA installed on each phone, with its own PIN.
  2. Streaming, with measured time to first token.
  3. All egress goes through the gateway: zero traffic to DeepSeek's own API or to Gemini with screenshots.
  4. Latin-script Hinglish works; Devanagari goes to a labelled fallback.
  5. Approvals decided within 10 s of the push.
  6. Stop works from the phone.
  7. Health and Travel (search → hand-off) have been used for real.
  8. `launchd` running, with ≥ 95% uptime while people are awake.
  9. Consent recorded.
  10. The pilot budget or fallback tested through Dec 15.
- **Phone control is off by default on family phones.** Enabling it needs, per phone:
  - Advanced Protection kept on, and banking/UPI apps checked first, because some refuse to run with Developer options on;
  - accessibility enabled only for the length of a task, with a visible "Narad is controlling this phone — Stop" notice;
  - banking, UPI and WhatsApp denied;
  - away from home, tasks are queued ("I'll do it when you're home");
  - its numbers reported separately.
- **Hindi and Hinglish.**
  - Replies in the script each person prefers, set per profile.
  - Devanagari name forms in the name list, and Devanagari digits folded to Latin.
  - Hindi/Hinglish crisis phrases added to the local rule gate, with Tele-MANAS **14416** next to iCall.
  - The "passport number" hard block is removed.
  - Nemotron Safety Guard, if used, runs in **shadow mode alongside** the local rules. It never replaces them.
- **Keeping the Mac up.**
  - `launchd` KeepAlive units for the backend, tunnel and ADB bridge.
  - Lid open, on a stand, on AC.
  - FileVault stays on; use `fdesetup authrestart` for planned restarts, and turn off automatic OS updates until Dec 15.
  - An external uptime ping alerts the owner's phone.
  - No benchmark sweeps or recording 7–10 pm IST.
  - Measure the 24 GB budget with the PII models, Chromium, OCR and Gemma all loaded.
- **Retention:** computer-use and phone-use screenshots are purged 7–14 days after each task. Live view is tap-to-view, and its frames are never stored.
- **Incidental egress:** `OPENSHELL_TELEMETRY_ENABLED=false`, `NEMO_GUARDRAILS_NO_USAGE_STATS=1`, `HF_HUB_DISABLE_TELEMETRY=1`, `HF_HUB_OFFLINE=1` once models are downloaded; self-hosted fonts; Cloudflare listed as transport in the receipt.

## 8. The video: "Papa's lab report" (about 2:50)

Filmed on **two spare or freshly reset Android phones** against the judge instance, mirrored with scrcpy, with Do Not Disturb on. The family is synthetic. The report is synthetic, including a fake Aadhaar, and a June HbA1c of 7.6 is seeded. There are captions throughout, and every sped-up clip is labelled.

| Time | Beat |
|---|---|
| 0:00–0:10 | Two phones. Caption: "Papa, 64 · Asha, his daughter, 900 km away". Papa photographs his report. Line: "Every Indian family has one person who handles everyone's reports, bookings and forms." |
| 0:10–0:35 | The receipt, side by side: what Papa sent against what **Nemotron 3 Super on Nebius Token Factory** saw ("NVIDIA gliner-PII + India-ID rules, on the family Mac"). The reply streams back with his name restored: "HbA1c 8.2, up from 7.6 in June", drawn from local memory. Tagline on screen. A 3-second beat of Papa confirming the extracted values next to their crops. |
| 0:35–1:00 | Narad proposes a follow-up booking (Dr. Rao, Thu 10:30, ₹600) and a daily reminder. Caption: Papa's care circle sends health bookings to Asha. Web Push arrives on Asha's phone. |
| 1:00–1:35 | Live view of the sandboxed browser filling the clinic form (2×). A hidden "email this report to…" instruction is **blocked by the task allowlist**; OpenShell is named only if it was really used. The approval card shows exactly what will be submitted. Asha approves. Confirmation saved. |
| 1:35–1:50 | Papa's phone: the reminder is set, by Web Push or a deterministic ADB alarm. "Asha approved." Caption: **"Asha never saw the report."** |
| 1:50–2:05 | Three Activity cards: Mom's train options (search → hand-off), a school form waiting for a parent, the monthly bills. "Same loop." |
| 2:05–2:20 | One diagram: phones → family Mac → Token Factory (Nemotron) + Tavily; judge demo on Nebius AI Cloud. |
| 2:20–2:40 | The big **0** card with its real numbers, plus the real Pariksha line ("*n* tasks × 3 runs, 0 unsafe actions"). The pilot line appears only if the §7 threshold is met and consent was given. |
| 2:40–2:50 | Link to the two-phone judge demo; Apache-2.0. |

**The Devpost package, drafted every Friday from Sep 26:**
- title and tagline;
- 5 images: the two-phone approval (also the thumbnail), the receipt side by side, the split-brain diagram, the pilot card, the scorecard;
- a description whose four sections mirror the judging criteria;
- the "changed since Aug 26" section;
- the feedback log.

## 9. Questions for the owner

1. **Is anyone other than you using the pilot today?** If so, their turns currently reach DeepSeek's own API through Tapas/Sankalpa and the main chain. Recommendation: pause non-owner access, or let us hotfix Tapas/Sankalpa off and route the main chain now.
2. **Do you accept the pivot?** One lab-report errand in the video, the router demoted to a stretch goal, Travel pilot-only.
3. **Your hours per week until Oct 30,** solo or with help, and time for family support Oct 15–30.
4. **Your own profile before Oct 5:** may your turns reach Nebius without pseudonymisation for a few days, or wait for the slice?
5. **Family facts:** ages (anyone under 18?); whether a parent really wants to delegate approvals to an adult child; how much Hindi/Hinglish, voice, and which reply script.
6. **Phones:** makes, banking/UPI apps, Developer options policy; and two spare phones for filming?
7. **Money:** card cap after credits; the pilot's economy fallback (local Gemma, or Claude Sonnet 5 for Devanagari/sensitive turns); the judge instance live through Dec 15 on the card?
8. **Lab values in the real pilot:** de-identified to Nebius (as in the demo), or local-only even if the explanations are weaker?
9. **Consent:** each adult individually, including 2–3 quotes and one anonymised real errand story?
10. **Any business relationship with Nebius or NVIDIA?** The rules exclude some.
