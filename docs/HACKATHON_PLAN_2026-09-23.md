# Narad × Nebius/NVIDIA: one build for the family pilot and the hackathon

*2026-09-23. Event: [Nebius x NVIDIA Global AI Hackathon](https://nebiusglobalaihackathon.devpost.com/) (Devpost). Track: **Personal AI**. Deadline: **Fri 2026-10-30, 10:00 PT (22:30 IST), 37 days from today**.*

This plan merges [PILOT_READINESS_PLAN](./PILOT_READINESS_PLAN_2026-09-23.md) (Phase 0 is done) with the hackathon. Research sources and verification status are in [VENDOR_RESEARCH](./VENDOR_RESEARCH_2026-09-23.md) and the hackathon research memo, which is summarised here. Anything marked *(unverified)* must be checked in a browser before we rely on it: the sandbox blocked devpost.com, nebius.com and docs.tokenfactory.nebius.com.

---

## 1. What the rules demand

| Requirement | Source | What it means for Narad |
|---|---|---|
| Must run on **Nebius Token Factory** (runtime inference calls) **or Nebius AI Cloud** (Serverless Jobs/Endpoints/DevPods), and use **at least one NVIDIA open model** | Rules digest (participant copy, verified 2026-09-13) | Nemotron on Token Factory becomes Narad's **default brain**, not a side feature. Stage 1 fails "superficial rebrands". |
| Stage 1 pass/fail: fits the track, meaningful use of the required APIs | same | The video, README and egress ledger must show Nemotron doing the real work. |
| Stage 2, four equal weights: **Technological Implementation** (the tiebreaker), **Design**, **Potential Impact**, **Quality of Idea** | same | Every week must move all four. |
| Existing projects are allowed if significantly updated after Aug 26, with a written account of the changes | same | Everything from Phase 0 onward counts. Keep a dated changelog. |
| Public repo with an OSI licence; a README naming the NVIDIA models and Nebius services | same | Narad is already Apache-2.0. |
| Public YouTube video under 3 minutes, showing the product on its intended device | same | Record on the Android phones, not only the Mac. |
| Hosted demo or test build, free and unrestricted until **Dec 15** | same | A separate judge instance with a synthetic family. Never the real pilot. |
| Feedback on the Nebius and NVIDIA technology | same | Collect it as we build. There are 10 × $100 "Most Valuable Feedback" prizes *(unverified)*. |
| Prizes: $20K / $10K / $6K, per-track prizes, **$3K Best Use of Tavily** | search excerpts *(unverified)* | Tavily becomes the primary search provider. |

**The Personal AI track brief:** "an always-on, private assistant that works for you while keeping your data under your control… persistent memory, reusable skills, access to the tools and information you choose, and the ability to carry out tasks across your daily workflows." It suggests NVIDIA NemoClaw, OpenShell, Hermes Agent and Nebius Serverless.

## 2. Resources

| Resource | Amount | Notes |
|---|---|---|
| Nebius Token Factory | $25 (Builder Program email) + $25 (hackathon promo code, *unverified*) + $25 second Builder tranche ~30 days later *(unverified)* | Credits expire **90 days after issue** *(unverified)*. The Sep 10 grant would lapse around **Dec 9, before judging ends**, so budget the judge window on the card. There is no hard spend cap: add a stop in `cost_ledger.py`. |
| Nebius AI Cloud | up to $50 via the Builder Program *(unverified)* | Hosts the judge demo on a CPU VM. There is no India region: the nearest are Finland, France, Israel and UK. |
| Tavily | $25 | Primary web search. Nebius reportedly acquired Tavily *(unverified)*. |
| LangSmith | $100 | Tracing for **synthetic** benchmark runs only. Family traffic stays out of it. |
| Toloka | $50 | Human labels for the Hinglish PII test set and for rating task quality in Pariksha. |
| Tandem | $50 | Not yet used. |
| Google Colab Pro+ | ~1,800 hours or compute units (to confirm which) | Fine-tuning and batch evaluation only. It can't serve the pilot or judges: sessions last at most 24 h and it doesn't count as "runs on Nebius". |
| Host Mac | M5 MacBook Air, 24 GB, fanless | Runs the family control plane, local PII models and the Android ADB bridge. Sleep and thermal throttling are the risks (see §7). |

## 3. The thesis

> **Narad is one private agent for a whole family.** It completes real errands (finding and booking, reading lab reports, planning) in a sandboxed browser and on the family's own Android phones. It sends each approval to the right person's phone and records what it did. Its brain is **open NVIDIA Nemotron models served by Nebius Token Factory with zero data retention**. Memory, health and finance records **stay on the family's Mac**. Only pseudonymised context leaves the house, and a per-message **egress ledger** shows where it went and what it cost. A **Nemotron router fine-tuned for household tasks**, trained on Colab and served on Token Factory, makes everyday turns faster and cheaper. **A live four-person pilot and the Pariksha benchmark** supply the before/after numbers.

**Against the ~9 visible Personal AI entries** (mostly chat, voice and memory assistants, or OpenClaw-on-Nemotron clones), Narad has four things they lack:
- It serves a whole household: isolated profiles, and approvals routed to the right person.
- It actually does things on screens: a browser and Android phones.
- Its privacy is measurable: pseudonymisation plus a ledger.
- It is backed by evidence from a pilot and a benchmark.

## 4. Target architecture

```text
 4 family Android phones (PWA: chat, Activity inbox, approvals, live view; Web Push)
        │ HTTPS via named Cloudflare tunnel
┌───────▼──────────── M5 MacBook Air · family control plane (always on) ──────────┐
│ Narad server (ADK supervisor + 4 avatars) · Phase 0 identity & safety floor       │
│ Family data stays here: Smriti memory, health.db, finance.db, workflows.db, vault │
│ Privacy gateway: India-ID rules + family gazetteer + NVIDIA gliner-PII / OpenMed  │
│   → pseudonymise before egress · restore placeholders on return · egress ledger   │
│ Kriya task runtime: sandboxed Playwright (OpenShell trial) · ADB → Android phones │
└──────┬───────────────────────────────┬───────────────────────────┬──────────────┘
       │ pseudonymised text             │ search queries             │ synthetic data only
┌──────▼─────────────────────────┐ ┌───▼─────────┐ ┌────────────────▼──────────────┐
│ Nebius Token Factory (ZDR on)  │ │ Tavily      │ │ Nebius AI Cloud (CPU VM)       │
│ narad-router: LoRA on Nemotron │ └─────────────┘ │ Judge demo: synthetic family,  │
│ Nemotron 3.5 Lightning: fast   │                 │ fixture sites, replay mode,    │
│ Nemotron 3 Super: worker/tools │                 │ per-judge rate + budget caps   │
│ Nemotron 3 Ultra: escalation   │                 └────────────────────────────────┘
│ Nemotron Safety Guard (Hindi)  │   Colab Pro+ (offline): LoRA training, PII model
│ Qwen/DeepSeek: benchmark arms  │   fine-tune, operator-model batch evals
└────────────────────────────────┘
```

**Model roles.** All prices are per 1M input/output tokens. They come from third-party price lists *(unverified)* and will be re-checked against `GET /v1/models`.

| Role | Model | Price | Notes |
|---|---|---|---|
| Router / fast path | `narad-router` (LoRA on Nemotron, trained on Colab), falling back to **Nemotron 3.5 Lightning** | $0.06 / $0.24 | Set `reasoning_effort="none"`. The router picks the workflow, avatar, tool subset and data class. |
| Default worker | **Nemotron 3 Super** | $0.30 / $0.90 | Tool calling and planning. |
| Escalation | **Nemotron 3 Ultra** (US region) | $1 / $3 | Rare. Always falls back to Super, because a Sep 12 report says Ultra was failing *(unverified)*. |
| Safety | **Nemotron Safety Guard 8B v3** | tbd | 23 categories, 9 languages including Hindi. Replaces the regex-only input gate. |
| Benchmark arms only | DeepSeek V4.1 Flash, Qwen on Token Factory | varies | Nebius runs these open weights itself, so nothing goes to DeepSeek the company. They sit behind the privacy gateway like every other cloud call. |

**Your provider decisions under this plan:**
- Grok stays out.
- DeepSeek runs only as Nebius-hosted open weights, behind local pseudonymisation. That meets your condition, with the stricter reading that *every* cloud model sits behind the gateway.
- Claude Sonnet 5 stays available as an owner-enabled sensitive-turn option in the pilot, and is switched **off** for the demo, so the "no closed-model vendor saw family data" claim is literally true.
- Hindi is a known gap: NVIDIA's docs list neither Lightning nor Super as supporting Hindi. Week 1 measures it. Hinglish turns fall back to Super, or to Claude in the pilot, if quality fails.

**Where family data lives: on the Mac.** A cloud copy would make memory available when the Mac is off, but it would turn health and finance history into a hosted dataset, which goes against the track's own wording. We revisit an encrypted off-site backup after the pilot (Phase 7).

## 5. Build plan, week by week

Principle: **three workflows done end to end beat six half-built ones.** The demo paths are **Travel**, **Health + Documents** (lab report → tracked follow-up) and **Teach Anything** (streaming polish). Career and Finance stay available in chat but get no new depth before Oct 30.

### Week 1 (Sep 24–30): Nemotron on Nebius, and measure before changing anything
- **Nebius provider:**
  - `nebius/` provider through LiteLLM with an explicit `api_base` (LiteLLM's default still points at the retired `api.studio.nebius.ai`);
  - a Kunji key entry, and model discovery via `GET /v1/models`;
  - `detect_provider()` checks the `nebius/` prefix first;
  - ZDR is switched on at the org level, with a screenshot kept.
- **Nemotron as the default brain:** the routing table above, with a Super fallback on every Ultra call. Hard spend stop in `cost_ledger.py`.
- **Search:** Tavily becomes the primary web-search provider, with Exa as fallback.
- **Pariksha v0:**
  - fixture sites for the Travel booking flow, the clinic appointment flow, and a lab-report PDF;
  - a chat latency set (simple / research / tool);
  - one command that produces a scorecard.
- **Baseline scorecards:** the current stack, and Nemotron with no other changes.
- **Measurements:** time to first token from each Token Factory region, as seen from India; Hindi and Hinglish quality on Lightning and Super.
- **Colab:** a synthetic household-task generator (six workflows × personas × Hinglish variants) produces the router training and evaluation sets.
- **Trials:**
  - Playwright inside NVIDIA OpenShell;
  - Artemis driving a phone from its accessibility tree with Nemotron.
  - Each trial either proves itself by **Oct 3** or gets dropped.

### Week 2 (Oct 1–7): fast path and privacy gateway
- **Fast path (Phase 2 core):**
  - token streaming, and `skip_summarization` so a single avatar hands its answer straight through;
  - the router answers trivial turns directly;
  - skills load on demand, with tool subsets per intent;
  - one capped recall per turn.
- **Privacy gateway v0 (Phase 3 subset):**
  - India-ID rules (Aadhaar with Verhoeff check, PAN, UPI, IFSC, +91, PNR);
  - a family gazetteer;
  - NVIDIA gliner-PII and/or OpenMed on the M5 via MPS;
  - placeholder restore in streamed output and in tool arguments;
  - fail closed;
  - an egress ledger per turn, visible in System → Trust.
- **Approvals (Anumati v0):** a hash-bound `ActionProposal`, and an approval card with Approve / Reject / Edit. The LLM can no longer self-confirm.
- **Colab:** train `narad-router` LoRA v1. Confirm how Token Factory hosts an externally trained adapter *(unverified)*. The fallback is Token Factory's own post-training on the same synthetic data.

### Week 3 (Oct 8–14): Kriya, and the phone as the control surface
- **Kriya v0 (Phase 4 subset):**
  - an inner perceive → act → settle → verify loop over Playwright accessibility snapshots with stable refs;
  - screenshots only when needed;
  - old observations pruned;
  - durable, cancellable tasks.
  - It runs in the OpenShell sandbox if the week-1 trial passed.
- **Android:** Artemis over ADB while phones are on home Wi-Fi, for tasks of about 8 steps or fewer. The Narad Companion app is deferred.
- **Cross-device (Phase 5 subset):**
  - Web Push per profile and device;
  - an Activity inbox;
  - live view: 1–2 fps frames of the running task on the phone;
  - server-side stop.
- **Router:** deploy `narad-router` on Token Factory, then compare it with Lightning and Super on the Pariksha router set.

### Week 4 (Oct 15–21): workflows that finish, pilot at full size, judge instance
- **Outcome contracts (Phase 6 subset)** for the demo paths: Travel (options → approval → booking evidence or a hand-off), Health/Documents (lab report → extracted values → a reminder or follow-up in health.db), Teach (mastery checkpoints).
- **Pilot:** all four family members on the new build. Collect task success, time to done and approvals, with the family's consent for aggregate numbers.
- **Judge instance** on a Nebius AI Cloud CPU VM:
  - a synthetic family, fixture sites, and replay mode;
  - invite codes for judges;
  - per-judge rate and budget caps;
  - a status page.
- **After scorecard:** run Pariksha and compare with the week-1 baseline.

### Week 5 (Oct 22–30): freeze, film, submit
- **Oct 23:** feature freeze. Bugs only after that.
- **Oct 25:** the video is recorded on the Android phones (scrcpy mirror), with two takes per segment.
- **Write-ups:**
  - README sections: NVIDIA models, Nebius services, how to run, judge access;
  - the "significantly updated since Aug 26" account;
  - the technology feedback section;
  - the Devpost description.
- **Oct 28:** submit, leaving two days of buffer.

### Deferred until after the hackathon (the pilot continues)
- Narad Companion app for phones away from home Wi-Fi.
- Cloud browser pool.
- cua desktop control upgrades.
- Career and Finance depth.
- Full Phase 7 operations.
- Devanagari redaction (Hindi-script turns route to the trusted fallback in the meantime).

## 6. Fine-tuning with the Colab hours

**1. `narad-router` (the headline).**
- *What:* a LoRA on a small Nemotron.
- *Trained on:* synthetic household turns labelled with workflow, avatar, tool subset, data class, and "answer directly vs delegate".
- *Measured against base Lightning:* routing accuracy, p50 latency, tokens per turn, cost per turn.
- *Served:* on Token Factory.
- *Why it matters:* this is the "creative, non-obvious use of Token Factory and Nemotron" the judges score. It also directly cuts pilot latency. The winner of Nebius's previous builders challenge was a LoRA project reporting a measured $3.64 bill.

**2. `narad-pii-in` (the privacy story).**
- *What:* fine-tune NVIDIA's open gliner-PII model on Indian identifiers, Indian names and Hinglish.
- *Labels:* a Toloka-labelled test set of about 1,200 items plus synthetic data.
- *Where it runs:* locally on the Mac.
- *Target gates:* ≥ 99.5% recall on structured IDs and ≥ 97% name recall in Latin script.

**3. Operator-model batch evaluation.**
- Score candidate GUI and vision models offline against Pariksha screenshots, so only the winners are wired in.

**Rule for all three:** Colab and Token Factory post-training see **synthetic data only**, never family data.

## 7. Risks and guards

- **The Mac sleeps or overheats.**
  - Use `sudo pmset -a disablesleep 1` plus AC power, and a watchdog.
  - Keep heavy local models off the hot path. The M5 Air hosts only the PII models, OCR and the control plane.
  - Judges never depend on the Mac.
- **Credits lapse before Dec 15.**
  - Keep a 40% reserve, and cap spend in code.
  - The judge instance defaults to replay mode after a daily budget is spent.
- **Alpha dependencies** (OpenShell, NemoClaw): trial them behind flags. Narad works without them.
- **Nemotron Ultra instability:** it always falls back to Super.
- **Hindi quality:** measure it in week 1 and route around it. Don't claim it until it's measured.
- **Demo flakiness:**
  - Show only tasks that pass at least 9 times in 10.
  - Use fixture sites for anything that commits an action.
  - Keep replay mode ready, and never show a cold start.
- **Privacy in public artefacts:** synthetic family only in the repo, video and judge instance. The sponsor gets promotional rights to likeness, so no real faces or voices.
- **Scope:** feature freeze on Oct 23 is non-negotiable.

## 8. Open questions for the owner

1. **Colab:** is the 1,800 figure hours or compute units?
2. **Team:** solo, or others? How many hours a week?
3. **Family consent:** OK to publish aggregate pilot numbers, but no faces or real names?
4. **Judge demo:** on Nebius AI Cloud (recommended), or on your Mac?
5. **Business relationship:** any with Nebius or NVIDIA? The rules exclude some.
6. **Hindi:** how much of the family's usage is Hindi or Hinglish?
