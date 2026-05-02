# Avatara / Narad

> *"We didn't invent multi-agent AI. We remembered it."*

Seven avatars. One sage who plays them. Local-first, private, yours forever.

---

## What this is

Narad is the orchestrator — a supervisor agent (modelled on the kalakar Narad Muni and his Mahati veena) that routes every user query to 1–3 specialised avatars, each grounded in the mythological lineage of Vishnu's dashavatara.

**Seven avatars:**
| Avatar | Domain |
|---|---|
| Matsya | Web search, knowledge retrieval, RAG |
| Varaha | Deep extraction, long-form research |
| Narasimha | Debugging, edge cases, breaking stuck states |
| Rama | Structured workflows, SOPs, sequential tool calls |
| Krishna | Drafting, communication, multi-stakeholder coordination |
| Buddha | Reasoning, critique, evaluation |
| Parashurama | Coding, refactoring, multi-file edits |

**Architecture:** Local-first. Gemma 4 E4B runs on your device. No data leaves. No subscription. Pay once.

Full design docs: [Narad-Plan/](../Narad-Plan/)

---

## Repository layout

```
avatara/          Core source (populated Phase 1+)
phase-0a/         Spike: validate Narad routing on local 4B model
  narad_schema.py     Routing schema (Pydantic)
  narad_claude.py     Claude Sonnet baseline evaluator
  narad_local.py      mlx-lm + outlines local model evaluator
  test_prompts.json   50 test prompts with ground-truth labels
  run_evaluation.py   Test harness + accuracy report
  requirements.txt    Python deps for this phase
  results/            Output (git-ignored)
```

---

## Phase 0a — Quick start

```bash
# 1. Create venv (Python 3.12 required)
python3.12 -m venv .venv && source .venv/bin/activate

# 2. Install deps
pip install -r phase-0a/requirements.txt

# 3. Set your Anthropic key (for Claude baseline)
export ANTHROPIC_API_KEY=sk-...

# 4. Run Claude baseline (ground truth)
python phase-0a/run_evaluation.py --model claude

# 5. Download local model and run
python phase-0a/run_evaluation.py --model local

# 6. Compare results
python phase-0a/run_evaluation.py --compare
```

**Definition of done (Phase 0a):**
- Claude Sonnet baseline routing accuracy ≥ 95%
- Gemma 4 E4B local routing accuracy ≥ 80% (if < 70%, escalate to 27B MoE)
- Zero JSON parse failures (llguidance / outlines token masking)

---

## Build phases

| Phase | Scope | Status |
|---|---|---|
| 0a | Local runtime spike: routing accuracy on Gemma 4 E4B | 🔨 In progress |
| 0b | ADK + LibreChat SSE + Parashurama sidecar PoC | Pending |
| 1 | Six native avatars, local E4B default | Pending |
| 1.5 | Parashurama: local sidecar + Aider + block log | Pending |
| 2 | Multi-avatar orchestration (sequential/parallel) | Pending |
| 3 | Darshan: Yantra live call graph | Pending |
| 4 | Mandala trace (replay/inspection) | Pending |
| 5 | Smriti: mem0 + LanceDB + encryption | Pending |
| 6 | Tapas v1: Sutra engine + replay test + Karma Log | Pending |
| 7 | Sankalpa engine | Pending |
| 8 | Tapas Yantra (nightly cycle + visualisation) | Pending |
| 9 | Electron desktop packaging | Pending |

---

## License

Apache 2.0. The OSS edition is fully featured — the moat is convenience and the compounding Tapas relationship, not artificial gates.
