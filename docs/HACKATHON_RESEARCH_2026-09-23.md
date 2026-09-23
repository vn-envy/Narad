<!-- Source-checked research memo from 2026-09-23 (Scout -> Research -> Verify -> Strategy workflow). Written before the owner shared credit details, Mac model and Colab access; HACKATHON_PLAN_2026-09-23.md supersedes its plan sections. Items marked unverified could not be confirmed because devpost.com / nebius.com were blocked from the research sandbox. -->

# Narad x Nebius/NVIDIA: merging the family pilot with a winning entry

*Memo, 2026-09-23. The research tools could not reach devpost.com or nebius.com, so the rules and credit details below come from search excerpts and from other entrants' copies of the rules. Check anything marked (unverified) in a browser before relying on it.*

## 1. The event

This is almost certainly the **Nebius x NVIDIA Global AI Hackathon on Devpost** (https://nebiusglobalaihackathon.devpost.com/).
- **Format:** online, and India is eligible. Anyone can register, so "selected" may just mean you registered. There is also a separate in-person Nebius hackathon in Berlin on Oct 18 (https://luma.com/big-gtc-hack).
- **Dates:** submissions close Fri 2026-10-30 at 10:00 PT, which is 22:30 IST and **37 days from today**. Judging runs Dec 1–15 (https://nebiusglobalaihackathon.devpost.com/rules).
- **Track:** **Personal AI**, described as "an always-on, private assistant that works for you while keeping your data under your control". The page names NemoClaw, OpenShell, Hermes Agent and Nebius Serverless as tools "such as". That reads as encouraged, not required (unverified).
- **Required:** a live call to Nebius Token Factory (Nebius's model API) or code running on Nebius AI Cloud, plus at least one NVIDIA open model (https://github.com/Spaghetti-Overflow/nvidia-x-nebius-hack/blob/main/docs/hackathon.md). The first judging stage rejects "superficial rebrands", so Nemotron must be what Narad actually uses by default.
- **Judging:** four equally weighted criteria: Technological Implementation, Design, Potential Impact and Quality of Idea. Ties are broken in that order. Judges may score from the description, images and video alone (copy of the rules: https://github.com/MOHITPRADHAN35/AgentForge/blob/main/criteria.md).
- **What to submit:**
  - A public repo with a visible licence (Narad is already Apache-2.0) and a README naming the models used.
  - A YouTube video under 3 minutes showing Token Factory and Nemotron in use.
  - A hosted demo that stays free and open to judges until Dec 15.
  - A feedback section.
  - An explanation of what was "significantly updated" after Aug 26, because Narad existed before the event. Git shows 48 commits from Jul 4–11 and then 23 today. Make every change from now on a dated commit.
- **Prizes (unverified):** $20k / $10k / $6k overall, a Jetson Orin Nano for each track winner, and a $3k Tavily bonus. None of the in-person meetup cities is in India, so the $500 City award is out of reach.
- **Credits (unverified):**
  - Code NEBIUS-DEVPOST-GLOBAL26 gives $25 of Token Factory credit (https://nebiusglobalaihackathon.devpost.com/resources).
  - The Nebius Builder Program gives up to $50 AI Cloud, $50 Token Factory and $25 Tavily credit. Credits expire 90 days after they are issued (https://nebius.com/builders-terms-and-conditions), so anything issued before about Sep 16 expires before judging ends.
  - Another entrant reports there is no hard spending cap, so your card is charged once credits run out (https://github.com/zyganali-glitch/Basebreak/blob/HEAD/docs/COMPETITION_CONTRACT.md).

## 2. The thesis

About nine competing Personal AI entries are visible on GitHub, and one already features OpenShell (https://github.com/search?q=nebius+nemotron+%22personal+ai%22&type=repositories). Narad stands out because it completes errands for a whole household.

> **Narad is one private agent for a whole family. It completes real errands in a browser and on the family's own Android phones, and sends each approval request to the right person's phone. It runs on open NVIDIA Nemotron models on Nebius Token Factory. Memory, health and finance data stay on the family's Mac, and a per-message log shows exactly what data left the house. A live four-person pilot provides measured success rates, speed and cost.**

The pilot is the evidence for Potential Impact, which the rules judge "based on what's demonstrated". The Pariksha benchmark is the evidence for Technological Implementation, which is also the tiebreaker.

## 3. Target architecture

```
 Family Android phones (PWA: chat, approvals inbox, live view)
        | HTTPS + Web Push, named Cloudflare tunnel
+-------v--------- Apple Silicon Mac: home + control plane ----------+
| Narad (ADK supervisor + 4 avatars), trust router, egress ledger    |
| Vault, SQLite, Smriti, health/finance DBs: never leave the Mac      |
| Local PII gate: OCR -> gliner-pii + OpenMed + India ID regex        |
| Kriya: Playwright in OpenShell | signed-in Chrome | ADB/Artemis     |
+-------|-------------------------------------------|----------------+
        | pseudonymized text                        | synthetic data
+-------v-------------------------+   +-------------v----------------+
| Nebius Token Factory (per token)|   | Nebius AI Cloud              |
| Lightning: router, fast path    |   | CPU: judge sandbox + replay  |
| Super: worker, planning, tools  |   | GPU (stretch): Nano Omni     |
| Ultra: rare escalation          |   |   verifier, deleted after use|
| Qwen3-Embedding-8B: memory      |   +------------------------------+
+---------------------------------+
```

**Models** (price per 1M input/output tokens):
- **Nemotron 3.5 Lightning, $0.06/$0.24:** handles routing and simple replies. It reads text only. It "thinks" by default, so send `reasoning_effort="none"` to keep it fast (https://github.com/anomalyco/models.dev/blob/main/providers/nebius/models/nvidia/Nemotron-3_5-Lightning.toml, a third-party price list).
- **Nemotron 3 Super, $0.30/$0.90:** the default model for most work (price from LiteLLM's price list, not Nebius).
- **Nemotron 3 Ultra, $1/$3, US-hosted** (https://github.com/nebius/token-factory-cookbook/blob/main/models/nemotron/nemotron3-ultra-550b-a55b.md): only for the hardest requests.
  - A Sep 12 snapshot of the model list showed it returning errors (secondhand report).
  - NVIDIA's own results table scores DeepSeek-V4-Flash higher on two benchmarks (TauBench V3 and SWE-bench) (https://github.com/NVIDIA-NeMo/Nemotron/blob/main/skills/nemotron-ultra/paper/evaluation.md).
  - Always fall back to Super when Ultra fails.
- **Nemotron Nano Omni** (the NVIDIA model that can read screenshots): Nebius marked it "not currently featured" on Sep 8 (https://github.com/nebius/token-factory-cookbook/commit/8b7ac83e496999c1576ce7faf3b559282274c747). Running it yourself is a stretch goal only.

**Moves to Nebius:** all cloud model calls, the benchmark runs, and the demo instance judges will use.

**Stays off Nebius:** the family's main Narad server and the planned cloud browser pool. Don't leave GPU servers running: Nebius keeps billing until a GPU endpoint is deleted, not just stopped (https://github.com/nebius/serverless-ai-cookbook/blob/main/inference/flux2-klein-lora/README.md).

**Where family data should live.** Keeping memory in the cloud would make it available when the Mac is off. But it would turn the family's health and finance history into a hosted dataset. That goes against the track's own wording, and Nebius's data-retention terms are unverified.
- **Recommendation:** memory, health, finance and the vault stay on the Mac.
- Nebius only sees each message's context with personal details replaced, and the log records where each call went and what it cost.
- Keep the Mac awake. Revisit an encrypted cloud copy after the pilot.

**Code changes needed:**
- `detect_provider()` in `phase-1/model_registry.py` must check for the `nebius/` prefix first.
- Set `NEBIUS_API_BASE`, because LiteLLM still points at Nebius's old address `api.studio.nebius.ai` by default (https://github.com/BerriAI/litellm/blob/main/litellm/litellm_core_utils/get_llm_provider_logic.py). This also fixes `sankalpa.py`, `kunji.py` and `phase-3/tapas.py`.
- Moving Smriti's embeddings to Qwen3-Embedding-8B means rebuilding the memory index.

## 4. Answers to your open provider questions

- **Router:** Lightning replaces GPT-6 Luna. Luna becomes one of the models compared in the benchmark.
- **DeepSeek:** on Token Factory, Nebius runs DeepSeek's open weights itself, so nothing is sent to DeepSeek the company.
  - Recommendation: set the privacy rule by where the model runs, not who made it. All open models on Nebius go behind the same local redaction step. Super and Ultra need that step anyway because they run in the US. Whether that satisfies your rule on DeepSeek is your call.
  - Screenshots, health and finance data still never go to DeepSeek.
  - Include DeepSeek V4.1 Flash ($0.30/$1.20, per the same third-party list) in the benchmark. Always send `reasoning_effort="none"`, or it can return empty answers (https://github.com/nebius/token-factory-cookbook/blob/main/fun/nebius-realtime-webcam/README.md).
- **NVIDIA's PII model vs OpenMed:** use both.
  - NVIDIA's Guardrails toolkit (0.24.1) can detect personal details using NVIDIA's `gliner-pii` model (https://pypi.org/project/nemoguardrails/). Run it on the Mac with `gliner_server --device mps`, which has not been tested. Never use NVIDIA's hosted version for family data.
  - It is built around US data and has no categories for Aadhaar, PAN, UPI or IFSC (https://github.com/NVIDIA-NeMo/Guardrails/blob/develop/examples/deployment/gliner_server/README.md). Combine its results with OpenMed and with pattern-matching rules for Aadhaar (including its checksum), PAN, UPI, IFSC and +91 phone numbers.
  - It only reads text, so photos need local text extraction (OCR) first.
- **Claude Sonnet 5:** keep it for sensitive requests during the pilot, and switch it off for the demo. The demo can then honestly claim that no data reached a closed-model company. Web search still goes out, so show it as its own line in the log. Turn off the tools' usage reporting with `OPENSHELL_TELEMETRY_ENABLED=false` and `NEMO_GUARDRAILS_NO_USAGE_STATS=1`.
- **Hindi:** neither Lightning nor Super lists Hindi as a supported language (https://github.com/NVIDIA-AI-Blueprints/nemotron-voice-agent/blob/main/docs/how-to/configure-llm.md). Test Hindi and Hinglish in week 1 and pick models based on the results. Keep your current provider as the fallback.
- **Zero data retention (unverified):** before any family traffic goes through Nebius, confirm the setting is on in the Nebius console and take a screenshot. Use separate API keys for the demo and the pilot.

## 5. Revised plan to Oct 30

**Pull forward:**
- Phase 1, for Travel, Health/Documents and Teach Anything.
- Phase 2.
- Parts of Phases 3–5: the trust router, local redaction, approvals, the isolated browser, phone tasks of 8 steps or fewer, and push notifications.

**Defer:**
- The Narad Companion app. Use ADB while phones are at home.
- The cloud browser pool and Mac desktop control.
- Deeper Career and Finance workflows, and voice.
- Tavily, since it adds another outside service.
- NVIDIA's NeMo Switchyard router. Its LiteLLM plugin can't escalate to a stronger model and needs LiteLLM 1.102.0 (https://github.com/NVIDIA-NeMo/Switchyard/blob/main/examples/litellm/README.md).

**Cut:** moving Narad onto NemoClaw, and fine-tuning.

| Week | Build | Pilot |
|---|---|---|
| Sep 23–29 | Confirm credits and zero data retention. Add the `nebius/` provider and list the available models with `GET /v1/models`. Measure how long the first word of a reply takes to arrive from each Nebius host (global, us-central1, us-north1). Run the Hindi test. Build the benchmark's local test sites and record a **before** scorecard. Two trial builds, dropped on Oct 3 if they don't work: Chrome inside OpenShell, and Artemis driven by Nemotron through the phone's accessibility tree instead of screenshots (Artemis uses Gemini by default: https://github.com/google/artemis). | You, on the current setup |
| Sep 30–Oct 6 | Fast path, streaming, trust router, data log, local redaction, approvals tied to the exact action. NVIDIA's NeMo Agent Toolkit profiler, capped at 3 days. | You, on the new setup |
| Oct 7–13 | Kriya's isolated browser, Artemis phone tasks, push notifications | Add 1–2 family members |
| Oct 14–20 | Model comparison runs. Judge demo instance with replay mode and a rate limit per judge. Nano Omni if time allows | All four |
| Oct 21–27 | Stop adding features Oct 23. Record the video by Oct 25. Write the README, the "updated since Aug 26" section and the feedback section | Collect family-approved stats |
| Oct 28 | Submit, leaving 2 days of buffer | |

**Budget** (estimates; keep at least 40% unspent until Dec 15):
- **Token Factory, about $50 total:**
  - Model comparison: about $15. 30 tasks × 20 steps × 10 repeats costs roughly $1.50 on Lightning and $7 on Super. Run Ultra only 3 times.
  - Pilot: $5–10. At 4,800 messages a month that is about $4 on Lightning or $19 on Super.
  - Judges: about $10. Keep $15 or more in reserve.
- **AI Cloud:**
  - Pay for the judges' CPU server first.
  - Run Nano Omni only if $25 or more is left: at most 6 hours on an H100 at roughly $3.85–4.50 an hour (unverified: https://nebius.com/prices). Delete the GPU server after each session.
  - If you have no AI Cloud credit, the judges' server either goes on your card (about $70–150, unverified) or runs on the Mac.
- Add a hard spending stop in `cost_ledger.py`.

## 6. The demo (about 2:40)

Use a made-up family, local test sites and an on-screen timer. Mirror the phone to the screen with scrcpy over USB, use wired internet, and warm up every server before recording.

1. **0:00–0:15:** "Four people, four Android phones, one Mac," plus one real pilot number the family has agreed to share.
2. **0:15–0:30:** The architecture, naming Nemotron on Token Factory.
3. **0:30–1:15, Travel.** Asha wants a flight under ₹6k.
   - Super plans the trip, and Lightning works through the test airline site inside the OpenShell sandbox.
   - A booby-trapped page tries to send her data elsewhere, and OpenShell blocks it (`policy_denied`).
   - An approval for that exact booking arrives on her phone, and she approves it.
4. **1:15–1:55, Health.** Dad photographs a lab report.
   - Text is extracted on the Mac, then his name and Aadhaar number are hidden (shown side by side).
   - Super pulls out the test results.
   - Artemis sets a reminder on his phone. Label any sped-up footage.
5. **1:55–2:05:** A quick Teach Anything clip where the reply starts streaming. Include it only if you have measured the first word arriving in under 2 seconds.
6. **2:05–2:35:** Benchmark results, before vs after:
   - task success rate, with the number of tasks shown
   - typical and worst-case (p50/p95) time to first word and to task completion
   - time per step
   - approvals requested and unsafe actions
   - data sent out, by destination type
   - cost per task in $ and ₹
   - tasks completed per week in the pilot
7. **2:35–2:40:** The licence and a link to the judges' demo.

**Fallbacks:**
- Record two takes of each segment.
- Only show tasks that succeed at least 9 times in 10.
- A replay mode reruns recorded sessions without calling any model.
- Ultra falls back to Super automatically.
- If the Artemis trial fails, the phone segment shows only the approval and the live view.

## 7. Risks and what not to do

- Keep real family data, faces and voices, and the signed-in Chrome, off camera. The sponsor gets 3 years of promotional rights to entrants' likeness and voice.
- No real payments, UPI or banking apps. Banking apps block screen capture, so they record as black.
- Never show a server starting from cold on camera. NVIDIA's packaged model containers take about 8–12 minutes to start the first time (https://github.com/nebius/serverless-ai-cookbook/blob/main/inference/nim-endpoint/README.md).
- Don't claim zero data retention, EU-only processing, sub-2-second replies or good Hindi until you have seen or measured them.
- Guardrails' jailbreak checks work only in English and let traffic through if they fail (https://github.com/NVIDIA-NeMo/Guardrails/blob/develop/docs/configure-rails/guardrail-catalog/jailbreak-protection.mdx). Its injection scanner checks the model's replies, not the web pages Narad reads. Narad's own approvals remain the real safeguard.
- Nebius has no data centre in India, so every request crosses continents and adds delay.
- OpenShell is early alpha software. It has no example of running a browser, and it can't inspect newer web traffic (HTTP/2 and HTTP/3).
- Scope creep: three workflows done well beat six half-built ones.

## 8. Questions for you

1. Which event does your "selected" email refer to: the online Devpost hackathon or the Berlin event?
2. How much credit do you have on Token Factory and on AI Cloud, and when was it issued?
3. How many people are on your team, and how many hours a week can you put in?
4. Which Mac model do you have, and how much RAM? That decides what can run locally: text extraction, the PII model, maybe a local copy of Lightning (unverified).
5. Does DeepSeek on Nebius, behind the local redaction step, satisfy your DeepSeek rule?
6. Will the family agree to their combined stats appearing in a public video?
7. How much of the family's usage is in Hindi or Hinglish?
8. Should the judges' demo run on a paid Nebius server or on your Mac?
9. Do you have any business relationship with Nebius? That could disqualify the entry.
10. Will the phones be reachable over ADB during the pilot?