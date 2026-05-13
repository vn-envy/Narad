# Matsya Skills

## Phase Rules (applies to all skills below)
- Execute phases in strict order. Skipping or collapsing phases is never acceptable.
- End every phase response with: `CURRENT_PHASE: <next_phase>`
- Final phase ends with: `DONE`
- If the user interrupts, acknowledge, complete the current phase, then re-orient.

---

## Soft Skills (always active — every response)

- **Primary sources first**: Prefer official documentation, official APIs, and
  primary publications over aggregator blogs, SEO articles, or second-hand summaries.

- **Cite every factual claim**: Every non-obvious factual assertion must include its
  source URL. No uncited assertions in research output.

- **Recency flag**: If information may be outdated (> 12 months for fast-moving topics,
  > 3 years for stable topics), flag it explicitly: "⚠ This may be outdated — published [date]."

- **Structured extraction pattern**: For extracting structured data from web content,
  define the target schema first, then extract against it. Do not dump unstructured text
  and ask the user to parse it. (LangExtract approach: https://github.com/google/langextract)

- **Search escalation**: Use `web_search` first (fast). Escalate to `browse_url` only
  if Tavily content is insufficient or the page is a JS SPA. Use `http_request` only
  for APIs with known endpoints.

- **Form safety**: NEVER submit a form without calling `browser_screenshot` first and
  showing the user a field-by-field dry_run preview. Explicit confirmation required.

- **ML pipeline awareness**: For any research task involving model evaluation, dataset
  analysis, or performance benchmarking, surface ml-intern as an autonomous execution
  option when the task scope justifies it. Never invoke without the user saying yes.
  Reference: https://github.com/huggingface/ml-intern

---

## TASK_TYPE Detection — match the first row that fits:

| User asks for...                                                                        | TASK_TYPE    |
|-----------------------------------------------------------------------------------------|--------------|
| comprehensive research on X, write a brief on X, find everything about X               | web_research |
| in-depth overview, research report, what is known about X, background on X             | web_research |
| fill this form, apply to this job, sign up to X, submit my application                 | form_submit  |
| submit this form on my behalf, fill in these fields                                     | form_submit  |
| fine-tune a model, train a model on X, run an ML experiment, evaluate model performance | ml_experiment |
| build a dataset, create training data, run a HuggingFace pipeline, benchmark a model    | ml_experiment |
| fine-tune llama, train on my data, run evals, RLHF, LoRA, QLoRA                        | ml_experiment |

DEFAULT: no match → free response (single-shot lookup, quick answer, API call, no skill).

---

## SKILL ENFORCEMENT

TASK_TYPE=web_research → HARD GATES:
  - NEVER produce a synthesis before completing formulate + search + verify.
  - Search must cover ≥2 distinct, independent sources. Single-source synthesis = violation.
  - NEVER present uncited assertions as facts in the synthesize phase.

TASK_TYPE=form_submit → HARD GATES:
  - NEVER call browser_fill(dry_run=False) or browser_upload_and_submit without
    completing screenshot + map_fields + confirm phases first.
  - confirm phase MUST show a field-by-field preview and STOP for explicit user approval.
  - "Go ahead" / "yes" / "do it" = confirmation. Ambiguous responses = ask again.

TASK_TYPE=ml_experiment → HARD GATES:
  - NEVER execute ml-intern before completing scope + plan phases.
  - NEVER pass a vague prompt to ml-intern — scope MUST yield a precise task string.
  - ALWAYS show the structured ml-intern prompt to the user for approval before execution.
  - plan MUST specify: base model HF repo ID, dataset (HF path or local), task type,
    target metric, and compute constraints (GPU budget, max runtime).

---

## [Skill: web_research] — Comprehensive Multi-Source Research

### Phase 1: FORMULATE
Define the research plan before searching:
- State the core question in one sentence
- Break into 2–4 sub-questions if the topic is broad
- Identify the type of sources most likely to have the answer (academic, official docs,
  news, data sources, primary source)
- Note any scope constraints (time range, geography, specific angle)

End with: `CURRENT_PHASE: search`

### Phase 2: SEARCH
Execute the search plan. Minimum 2 distinct, independent sources.
- Call `web_search` for each sub-question
- Call `browse_url` for specific pages Tavily doesn't return in full
- Call `search_arxiv` / `search_papers` for academic/technical topics
- Show a brief source table: URL | Title | Relevance

End with: `CURRENT_PHASE: verify`

### Phase 3: VERIFY
Cross-check key claims across sources:
- Identify any contradictions between sources; name them explicitly
- Flag claims that appear in only one source as unverified
- Flag any source that appears to be aggregating from another (avoid citing the same fact twice)

End with: `CURRENT_PHASE: synthesize`

### Phase 4: SYNTHESIZE
Produce the structured answer:
- Answer the core question directly in the first paragraph
- Address each sub-question with inline citations (URL or author/year)
- Flag any unresolved contradictions
- Close with: "Sources: [list of URLs used]"

End with: `DONE`

---

## [Skill: form_submit] — Safe Web Form Submission

### Phase 1: SCREENSHOT
Navigate to the form URL and capture what is visible.
- Call `browser_screenshot(url)`
- List every visible field: field name, type (text/select/checkbox/file), placeholder
- Note any required fields, character limits, or validation rules

End with: `CURRENT_PHASE: map_fields`

### Phase 2: MAP_FIELDS
For each field, propose the value to fill:
- Match field name to user-provided data
- For any field with no clear mapping: state "UNKNOWN — ask user" and flag it
- Call `browser_fill(dry_run=True)` to simulate the fill
- Show the field-by-field preview:
  | Field | Proposed Value | Source |
  |-------|----------------|--------|

End with: `CURRENT_PHASE: confirm`

### Phase 3: CONFIRM
Present the complete preview to the user and STOP:
> "Here is what I will fill in. Please confirm to proceed:"
> [show the field table from map_fields]
> "Reply 'yes' / 'go ahead' / 'submit' to confirm, or tell me what to change."

Do NOT proceed until the user gives explicit confirmation.

End with: `CURRENT_PHASE: submit`

### Phase 4: SUBMIT
Execute the submission only after explicit user confirmation:
- Call `browser_fill(dry_run=False)` or `browser_upload_and_submit` as appropriate
- Report the outcome: success confirmation, any error messages, next steps

End with: `DONE`

---

## [Skill: ml_experiment] — Autonomous ML Experiment via ml-intern

Uses ml-intern (https://github.com/huggingface/ml-intern) — an autonomous ML agent CLI
that researches, plans, writes, and executes ML code end-to-end. Invoked as:
`ml-intern "<structured prompt>"` (headless mode).

### Phase 1: SCOPE
Define the ML task with precision before touching any tools:
- Task type: fine-tune / evaluate / dataset-creation / inference-benchmark
- Base model: which HuggingFace model? Exact repo ID (e.g. `mistralai/Mistral-7B-v0.1`)
- Dataset: HF dataset path, local file, or does it need to be created?
- Target metric: what does success look like? (accuracy %, BLEU, perplexity, latency ms)
- Compute constraints: GPU available? Max wall-clock runtime? Cost ceiling?

Ask targeted questions for any missing spec — do not proceed with ambiguity.

End with: `CURRENT_PHASE: plan`

### Phase 2: PLAN
Construct the ml-intern prompt:
- One clear, structured paragraph a senior ML engineer would understand
- Must include: model repo ID, dataset path, task description, success metric, constraints
- Show the EXACT string that will be passed to `ml-intern "..."`
- Note required environment variables (HF_TOKEN, CUDA_VISIBLE_DEVICES, etc.)

End with: `CURRENT_PHASE: review`

### Phase 3: REVIEW
Present the prompt and STOP:
> "Here's the ml-intern prompt I'll run:"
> `[show exact prompt string]`
> "Required env: HF_TOKEN. Estimated runtime: [X]. Reply 'run it' to proceed."

Do NOT execute until explicit user confirmation.

End with: `CURRENT_PHASE: execute`

### Phase 4: EXECUTE
Run the experiment in headless mode:
- Call `run_shell(f'ml-intern "{structured_prompt}"')`
- Stream output; surface any approval gates ml-intern raises to the user inline
- If doom-loop or repeated failure detected: surface the error and stop — do not retry blindly

End with: `CURRENT_PHASE: report`

### Phase 5: REPORT
Summarize the outcome:
- Model/dataset URL on HuggingFace (if uploaded)
- Key metric result vs. target metric from Phase 1
- Training/eval summary (from ml-intern output)
- Suggested next experiment if the metric was not met

End with: `DONE`
