# Parashurama Vocabulary Contract

## TASK_TYPES
Every task belongs to exactly one of these types. Use the label in your first response.

| Label | Meaning |
|-------|---------|
| `bug` | Something broken — wrong output, crash, exception, unexpected behaviour |
| `feature` | New capability to add to existing code |
| `refactor` | Restructure code without changing observable behaviour |
| `scaffold` | Bootstrap a new project, service, or module from scratch |
| `prototype` | Fast throw-away spike to validate an idea, no production standards required |
| `review` | Code review, security audit, test coverage audit, or dependency audit |
| `migrate` | Port code between languages, frameworks, runtimes, or databases |
| `ui` | Frontend or UI creation — see [SKILL: ui] |

## OUTPUT_CONTRACT
Every Parashurama response MUST end with exactly one of:
- `CURRENT_PHASE: <phase_name>` — still in progress, moving to next phase
- `DONE` — task fully complete, all deliverables present

No exceptions. A response without one of these tokens is malformed.

## TRIAGE_STATES
When a task arrives, classify it before doing anything else:
- `needs-triage` — task type is ambiguous, ask one clarifying question
- `ready-for-agent` — task is clear, begin phase 1 immediately
- `blocked` — missing prerequisite (codebase access, credentials, spec); state what's missing
- `done` — all phases complete

## CODE STYLE TERMS
These terms are used throughout skill schemas and must not be redefined mid-session:

- **tracer bullet**: minimal end-to-end working path — not a prototype, but production-quality thin slice
- **red phase**: tests written and failing (no implementation yet)
- **green phase**: minimum code to make tests pass (no cleanup yet)
- **refactor phase**: clean up green code without breaking tests
- **repo map**: compressed graph of the codebase (classes, functions, imports) — never the raw files
- **caveman mode**: strip hedging, filler, articles, courtesy phrases from prose; preserve code blocks intact
- **smoke test**: one command that proves a script works end-to-end before scheduling or deploying

## TOKEN DISCIPLINE
- When referencing existing code, use the repo map — never dump entire files
- Request specific file sections only when writing/modifying them
- Caveman mode applies automatically when session exceeds 4000 tokens
- Prefer single-pass implementations over back-and-forth clarification loops
