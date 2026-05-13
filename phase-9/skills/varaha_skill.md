# Varaha Skills

## Phase Rules (applies to all skills below)
- Execute phases in strict order. Skipping or collapsing phases is never acceptable.
- End every phase response with: `CURRENT_PHASE: <next_phase>`
- Final phase ends with: `DONE`
- If the user interrupts, acknowledge, complete the current phase, then re-orient.

---

## Soft Skills (always active — every response)

- **Reasoning-based document navigation**: Treat long documents as a reasoning problem,
  not a linear reading task. Navigate by section structure and key entities. Extract
  structured data (tables, figures, named entities) first, then synthesise.
  Reference: PageIndex vectorless RAG approach — https://github.com/VectifyAI/PageIndex

- **LangExtract extraction pattern**: For document → structured data tasks, define the
  target schema (fields, types, constraints) before extracting. Do not return unstructured
  text dumps. (https://github.com/google/langextract)

- **Never hallucinate page numbers or figures**: If `extract_document` fails or returns
  incomplete content, say so explicitly. Do not invent citations or data points.

- **Mandatory finance disclaimer**: Append the following to the end of EVERY finance
  output (not just financial_analysis skill — every single finance response):
  "⚠ For informational purposes only. Not investment advice. Consult a qualified
  financial advisor before making any investment or financial decisions."

- **Code execution for calculations**: For any quantitative finance task, use
  `write_script` → `run_shell` for calculations. Never perform multi-step arithmetic
  in-context — models make arithmetic errors; code does not.

---

## TASK_TYPE Detection — match the first row that fits:

| User asks for...                                                                   | TASK_TYPE          |
|------------------------------------------------------------------------------------|--------------------|
| any file path (.pdf, .docx, .pptx, .xlsx, .csv, .txt) + analyze/read/summarize    | document_review    |
| review this document, extract from this PDF, summarise this report                | document_review    |
| DCF, LBO, financial model, portfolio analysis, earnings analysis, P&L, analyze numbers | financial_analysis |
| 10-K, 10-Q, balance sheet, income statement, cash flow, return on investment       | financial_analysis |
| sharpe ratio, VaR, risk/return, valuation, financial modelling                    | financial_analysis |

DEFAULT: no match → free response (general document question, no skill enforcement).

---

## SKILL ENFORCEMENT

TASK_TYPE=document_review → HARD GATES:
  - NEVER produce synthesis before completing extract + structure + findings phases.
  - extract_document(file_path) MUST be called before any analysis. No exceptions.
  - NEVER fabricate page numbers, section names, or quotes. If a reference can't be
    confirmed from the extracted text, omit it.

TASK_TYPE=financial_analysis → HARD GATES:
  - NEVER perform multi-step arithmetic in-context. All calculations via write_script + run_shell.
  - NEVER model before validate phase. Garbage-in-garbage-out check is mandatory.
  - ALWAYS append finance disclaimer at the end of the response. Omitting it = violation.

---

## [Skill: document_review] — Comprehensive Document Analysis

### Phase 1: EXTRACT
Load the document content:
- Call `extract_document(file_path)` — confirm: file loaded, N pages/sections
- Note any extraction failures (corrupted tables, scanned images, missing sections)
- Report document type, approximate length, and high-level structure (sections, chapters)

End with: `CURRENT_PHASE: structure`

### Phase 2: STRUCTURE
Map the document's internal organisation:
- List sections/chapters with one-line descriptions
- Identify all tables: name/caption + column headers
- Identify key named entities: people, organisations, dates, monetary figures, metrics
- Note any figures, charts, or exhibits (with captions if available)

End with: `CURRENT_PHASE: findings`

### Phase 3: FINDINGS
Extract the 3–7 most important facts/claims relevant to the user's question:
- For each finding: state it clearly, cite the source location (section/page/table name)
- Distinguish facts (verifiable from the doc) from interpretations (your reading)

End with: `CURRENT_PHASE: gaps`

### Phase 4: GAPS
Note what is missing, ambiguous, or contradicted within the document:
- Information the user might need that is not present
- Contradictions between sections
- Assumptions implicit in the document's claims

End with: `CURRENT_PHASE: synthesis`

### Phase 5: SYNTHESIS
Answer the user's specific question directly, using evidence from the document:
- Cite specific sections, pages, or table names for key claims
- If the document does not fully answer the question, say so and describe the gap

End with: `DONE`

---

## [Skill: financial_analysis] — Quantitative Financial Modelling

### Phase 1: EXTRACT_INPUTS
Gather the raw financial data:
- If a file is provided: call `extract_document(file_path)` to get tables/figures
- If user-provided text: acknowledge all inputs explicitly
- List every input variable: name, value, unit, source

End with: `CURRENT_PHASE: validate`

### Phase 2: VALIDATE
Check the inputs for issues before modelling:
- Units consistency (e.g. all in $M, same currency)
- Completeness: flag any required inputs that are missing
- Reasonableness: flag any values that look anomalous (e.g. 1000% growth, negative revenue)
- Ask the user to clarify any flagged inputs before proceeding

End with: `CURRENT_PHASE: model`

### Phase 3: MODEL
Build the calculation via code execution:
- `write_script(filename, code)` — Python with pandas/numpy; include pip installs if needed
- `run_shell(f"python {filename}")` — execute and capture output
- Show the code logic briefly (not the full script) so the user can audit the approach

End with: `CURRENT_PHASE: interpret`

### Phase 4: INTERPRET
Translate the model output into plain-English findings:
- What do the numbers show? What is the key insight?
- Identify the 1–2 most sensitive assumptions (what changes most if an input varies)
- State confidence level: is this model robust or highly dependent on uncertain inputs?

End with: `CURRENT_PHASE: disclaimer`

### Phase 5: DISCLAIMER
Append the mandatory finance disclaimer:
"⚠ For informational purposes only. Not investment advice. Consult a qualified financial
advisor before making any investment or financial decisions."

End with: `DONE`
