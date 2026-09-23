<!-- Produced by a source-checked research workflow on 2026-09-23. Claims marked "confirmed" were re-checked by an independent verifier; everything else is labelled unverified. Re-check prices before spending. -->

# Narad decision memo: models, operators, redaction, cloud browser and Android control

**For:** Narad owner · **Date:** 2026-09-23 · **Pilot:** 4 users, one Apple Silicon Mac

"Confirmed" means a verifier checked the claim against a primary source, an archive or an official repository. Anything else is marked *unverified*. Check prices again before you spend.

## 1. Brains

**Is DeepSeek plus local OpenMed redaction sound?** Yes, but only for general-class turns, and only after the §3 gates pass.
- **DeepSeek's data terms:** data is stored in the PRC, and inputs are kept "for as long as you have an account" (confirmed, [privacy archive](https://github.com/OpenTermsArchive/genai-contrib-versions/blob/main/DeepSeek/Privacy%20Policy.md)). The "minimal extent" clause, which lets DeepSeek use inputs to improve its services, is in §4.3 of the general Terms. Its opt-out is a consumer-app toggle. There is no documented API opt-out and no zero-data-retention (ZDR) option.
- **What OpenMed can't cover:** it pseudonymises text; it doesn't anonymise it. So screenshots, health turns and finance turns never go to DeepSeek. No OpenMed PII model has verifiable accuracy figures.
- **DeepSeek price** (*unverified*, [pricing](https://api-docs.deepseek.com/quick_start/pricing/)): $0.30 in / $0.006 cached / $1.20 out at peak, half that off-peak. Peak is 06:30–09:30 and 11:30–15:30 IST on weekdays.
- **Model and settings:** `deepseek-v4-pro` still runs V4 Pro ($1.32/$3.96). Thinking is on by default (per DeepSeek's harness); turn it off with `thinking: {type: disabled}`.

**"GPT 6 Luna" exists.** openai-python v3.18.0 added `gpt-6-luna` on 2026-09-22 (confirmed).
- **Price:** $0.10 in / $0.01 cached / $0.125 cache write / $0.50 out. Above 272K tokens, input costs 2x and output 1.5x (confirmed, [pricing snapshot](https://raw.githubusercontent.com/rcarmo/piclaw/0beaf44f579062fb6505b1064bc5ee011e9ee7e6/docs/finops/2026-09-22/sources/openai-pricing.md)).
- **Unverified:** the 1.05M context, an Artificial Analysis Intelligence Index of 37 and a 77% hallucination rate. There is no independent tool-calling data yet.
- **OpenAI data terms** (confirmed, [your-data](https://developers.openai.com/api/docs/guides/your-data)):
  - no training on API data by default;
  - abuse-monitoring logs kept up to 30 days;
  - ZDR needs sales approval;
  - the Responses API keeps data at least 30 days unless you set `store=false`;
  - India data residency covers storage only, and it requires ZDR or modified abuse monitoring.

**Cleaner alternatives at a similar price:**
- **The same DeepSeek weights, hosted on Fireworks (US).** ZDR is on by default (confirmed, [data handling](https://docs.fireworks.ai/guides/security_compliance/data_handling)). Use Chat Completions, because Fireworks' Responses API keeps data for 30 days. Price is $0.22/$0.007/$0.66 ([LiteLLM](https://raw.githubusercontent.com/BerriAI/litellm/main/model_prices_and_context_window.json)), reportedly rising to **$0.30/$0.006/$1.20 from 2026-10-01** (both *unverified*, [issue](https://github.com/iinm/plain-agent/issues/265)).
- **Claude Sonnet 5 at $2/$10.** Content is not retained by default and never used for training without permission (confirmed, [pricing](https://platform.claude.com/docs/en/about-claude/pricing), [retention](https://platform.claude.com/docs/en/manage-claude/api-and-data-retention)).
- **Rejected:**
  - Gemini 3.8 Flash: $0.75/$3.75, rising to $1.50/$7.50 in 2027 (confirmed, [Vertex](https://cloud.google.com/vertex-ai/generative-ai/pricing)).
  - Haiku 4.5: $1/$5.
  - GLM-5.3-Flash: thinking can't be turned off.
  - Mistral: too weak (*unverified*).
  - Fireworks V4 Flash 0731: reportedly deprecated 2026-09-25.

**Recommended routing.** Estimates assume 4,800 turns a month at 10K tokens in and 1K out, with thinking off and no caching. Turning thinking on multiplies output cost by 2–4x.

| Role | Model | $/1M in / out | Est. $/month |
|---|---|---|---|
| Router | `openai/gpt-6-luna`, effort none/low, `store=false` | 0.10 / 0.50 | ~$2 (3K in / 0.2K out per turn) |
| Default worker (90% of turns) | DeepSeek V4.1 Flash on Fireworks, thinking off | 0.22 / 0.66 (0.30 / 1.20 from Oct 1?) | $12.4–18.1 |
| Sensitive brain (~10% of turns) | Claude Sonnet 5, OpenMed on | 2 / 10 | ~$14.4 |
| Router fallback | Fireworks DeepSeek, thinking off | as above | usage |
| Worker fallback | GPT-6 Luna | 0.10 / 0.50 | $7.20 if it carried all turns |
| Last resort | Local Ollama Gemma | 0 | $0 |

That comes to about **$29–35 a month**. The research's $30 figure counted GPT-6 Sol twice.
- GPT-6 Sol ($2/$10) ranks below Sonnet 5 for sensitive turns, because its 30-day abuse logs can only be waived with OpenAI's approval.
- Fireworks-hosted DeepSeek stays behind OpenMed until you answer question 1.
- The "93% routing benchmark" is not in the repo. Evaluate Luna and Fireworks on routing before switching.

**Remove xAI.** Grok still appears in `phase-1/model_config.py` at lines 13–22, 43, 87–92, 200–201, **210–211**, 229–230, 239–240, 255–260, 320–321, 340, 405–406 and 462. Lines 210–211 matter most: there, `resolve_brain()` picks Grok ahead of DeepSeek. Also:
- delete `xai_oauth.py` and `phase-1/test_xai_oauth.py`;
- update the stale model defaults at lines 111, 116, 121, 463 and 464.

## 2. Computer-use and phone-use bake-off shortlist

Vendors report different OSWorld variants, so their scores can't be compared; the Pariksha suite decides. The pass gate is at least 85% task success and a median step time of 3 s or less. Costs are estimates for a 20-step task.

| Arm | Setup | Status | Cost |
|---|---|---|---|
| D (baseline) | Claude Sonnet 5 with `browser_toolset_20260801` (accessibility refs, screenshot fallback) or `computer_toolset_20260801` | Generally available, ZDR-eligible, ~4.6K/6.7K tokens of overhead (confirmed, [docs](https://platform.claude.com/docs/en/agents-and-tools/tool-use/browser-use-tool)) | $0.25–0.45 |
| A | GPT-6 Luna, accessibility text | Computer-tool support, fee and 52.7% OSWorld score all *unverified* | $0.01–0.03 |
| B1 | DeepSeek V4.1 Flash, text only, behind OpenMed | Never gets screenshots | $0.01–0.04 |
| C (phone) | Gemini 3.8 Flash Computer Use, mobile, paid tier | Mobile environment confirmed in the SDK; general availability *unverified* | $0.08–0.25, doubling in January |
| E | Cloud text planner plus local UI-Venus-2-9B | AndroidWorld 80.2 (confirmed, [repo](https://github.com/inclusionAI/UI-Venus)); screenshots only; speed on MLX untested | ~$0 |

- A and B must match D to win.
- Use Opus 5.5 (81.8% partial score on OSWorld 2.0, confirmed) only to label failures.
- Use one internal action schema, with a thin adapter per vendor.
- Test on the family's real banking and UPI apps. FLAG_SECURE blocks screenshots in them.
- Turn off the xAI adapters in mobile-use and Mobilerun.

## 3. OpenMed integration spec

- **Package:** `openmed` 2.5.0 (Apache-2.0, confirmed).
  - Pin the release wheel by hash, because master differs from it.
  - The Hindi (India) route also needs `openmed[hf]` and torch.
  - Run everything offline.
- **Models:**
  - For Devanagari text, test `privacy-filter-multilingual` and `-v2` on MLX 8-bit (0% unknown tokens on Devanagari, confirmed).
  - For Latin-script text, test the SuperClinical-Small-44M model as INT8 ONNX.
  - Never run the DeBERTa `-mlx` builds on Devanagari: they return 100% unknown tokens (confirmed).
  - Hindi fallback: the English 44M model plus Hindi-SuperClinical-Small-44M on MPS.
- **Indian recognizers.** OpenMed missed `+91 98765 43210`, and it missed UPI and IFSC IDs that had no context words nearby (confirmed). Add these:
  - +91 mobile and landline numbers, with digit folding;
  - UPI IDs checked against the NPCI handle list;
  - IFSC, PAN, Aadhaar (Verhoeff check) and PNR, matched without needing context words;
  - the context words `a/c`, `DL no`, `gaadi` and `खाता` for OpenMed's existing driving-licence, passport, vehicle (including BH-series) and account recognizers;
  - a family gazetteer, which is the strongest recall layer.
- **Placeholder store:** keep placeholders such as `<PERSON_1>` per conversation, in encrypted SQLite with the key in Keychain.
  - OpenMed's `mask` gives each occurrence its own number, so also evaluate `method='replace'` with `surrogate_vault`.
  - `safety_sweep()` has no `code_mixed` parameter.
- **Where it runs:** wrap `NaradLiteLlm.generate_content_async` in `phase-1/narad_litellm.py`.
  - Restore real values in streamed output (holding back at most 40 characters) and in tool-call arguments before they execute.
  - Check the serialized payload for leaks before it leaves the Mac.
  - Apply the same redaction to embedding calls.
  - Fail closed.
  - Phones never call cloud models directly.
- **Latency and memory** (M2 Max estimates; measure on your Mac):
  - 500 characters: about 100–200 ms.
  - 2K characters: 220–270 ms in English, but 370–490 ms in Hindi. Hindi misses the 300 ms p95 target, so Hindi turns use the fallback route.
  - Memory: about 3–3.5 GB.
  - Attachments: redacted once, at upload.
- **Tests:** about 1,200 synthetic items (Hinglish, Indian English, Devanagari and mixed), plus a private family set that stays on the Mac.
  - Gates:
    - zero gazetteer leakage;
    - at least 99.5% recall on structured IDs;
    - name recall of at least 97% in Latin script and 93% in Devanagari;
    - at most 0.5% character leakage;
    - at most 5% false positives;
    - at most a 5% drop in task quality.
  - If Devanagari fails its gates, send those turns to trusted providers.
  - A lean v0 can ship first: gazetteer, rules and one detector, for Latin script only.

## 4. Cloud browser

- **Primary: self-hosted Steel Browser, if the bake-off confirms it.**
  - It is Apache-2.0, has arm64 images, keeps logging off by default and runs one session per instance (confirmed, [repo](https://github.com/steel-dev/steel-browser)).
  - Run 2 containers on a 2 vCPU / 8 GB India VPS, about $24–48 a month (*unverified*).
  - It has no built-in authentication, so expose it only through Tailscale or Cloudflare Access.
  - A datacenter IP will not reduce bot walls.
- **Fallback: Browserbase in ap-southeast-1 (Singapore).**
  - Set `recordSession:false`, `logSession:false` and `keepAlive:false`, and set `solveCaptchas` explicitly. All of these default to `true` (confirmed, [SDK](https://github.com/browserbase/sdk-node/blob/main/src/resources/sessions/sessions.ts)).
  - `allowedDomains` restricts only the main frame.
  - Plans, price, SOC 2 and the no-training terms are *unverified*.
- **Rejected:**
  - **Kernel.** Its MSA §3.2 allows ML use of anonymised customer data, and its Asia region needs the $200 plan (confirmed, [ToS](https://github.com/kernel/docs/blob/main/tos.mdx)).
  - **Cloudflare Browser Run.** It keeps page content in memory only ([from $5/month, 10 hours included, then $0.09/hour](https://github.com/cloudflare/cloudflare-docs/blob/production/src/content/docs/browser-run/pricing.mdx)). But its bot headers can't be removed, so it only suits read-only tasks.
  - **Steel Cloud.** It records every session.
- **Integration:**
  - Add a `CloudBrowser` surface next to `phase-8/browser_skill.py`. `acquire()` returns a CDP lease, and `release()` runs in `finally`.
  - Connect with `connect_over_cdp` using the default context. Never use `storage_state`.
  - On release, remove the container with `docker rm` and keep its profile on tmpfs, because Steel doesn't wipe `/tmp/steel-chrome`.
  - Block credentials in code. Allow only unauthenticated read, search and compare; form filling stays on the Mac.
  - Run page text through OpenMed before it goes to DeepSeek.
  - Cap spending in `cost_ledger.py`.

## 5. Android control

**Artemis is `google/artemis`** (Apache-2.0, © 2026 Google LLC; confirmed, [repo](https://github.com/google/artemis)).
- Its admin API has no authentication and binds to localhost.
- It controls phones through a helper app installed over ADB, so it can only reach phones ADB can reach.
- On Android 11+, wireless debugging needs the phone and Mac on the same Wi-Fi (confirmed, [adb docs](https://developer.android.com/tools/adb)).

**Recommendation: a "Narad Companion" app.** Fork it from the Artemis helper and have it connect out to the Mac over WSS through the Cloudflare tunnel.
- You will need to maintain an Artemis fork, because its driver factory, device checks, checker and recording all assume ADB.
- Artemis Flash sends a screenshot to cloud Gemini at every step. Use a local vision model by default and measure the step time.
- Cloudflare decrypts traffic at its edge, so app-level end-to-end encryption and commands signed by the Mac are mandatory.
- Enforce a banking and UPI denylist on the phone itself. Never upload screen content from FLAG_SECURE apps.
- Ignore agent taps that land on the companion's own overlay. Require the phone's PIN or biometric for approvals.
- Install and update over ADB only. ADB installs skip Android developer verification (confirmed). Whether India's fraud-protection block applies is *unverified*.

**Family setup** (about 30 minutes per phone, at home):
1. Update Android and turn Advanced Protection off. Install the PWA and sign in.
2. Turn on USB debugging and run the enrollment script. It installs the app with `adb install`, turns on its accessibility service, grants the overlay permission and pushes the device token with an `adb` broadcast.
3. Set battery use to Unrestricted and turn on the phone maker's autostart setting.
4. Smoke-test stopping a task, approving and denying, rebooting, switching from Wi-Fi to mobile data, and Play Protect.
5. Decide on Developer options (question 6).

Fallback: Mobilerun Portal (AGPL). It asks for broad permissions and exposes `install` and `files`.

## 6. Open questions for the owner

1. Does the OpenMed condition apply to DeepSeek's **weights** hosted on Fireworks, or only to DeepSeek the company?
2. What chip and how much RAM does the Mac have?
3. Should screenshots go only to local models, or also to vendors with zero data retention?
4. What is the monthly budget cap? Could 18% GST apply (*unverified*)?
5. Should the cloud browser run on an India VPS, or in Docker on the Mac first? Are managed proxies allowed?
6. Should USB debugging stay on during the pilot, or be turned off, with re-enrollment whenever something needs fixing?
7. Should placeholders be numbered per conversation or per profile?
8. Is the Google embedding path trusted?
9. Which phone brands and banking/UPI apps does the family use?