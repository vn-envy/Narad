# Narad Architecture

Narad is a local-first personal AI harness with one supervisor and four specialists. The UI exposes four surfaces: Chat, Workflows, Memory, and System.

## Runtime

```text
User / attachments / URLs
        |
        v
Narad supervisor
  |-- Matsya: research, browser/computer use, documents, local files
  |-- Rama: planning, finance, health tracking, Google Calendar
  |-- Krishna: teaching, writing, email, presentations, media
  `-- Parashurama: code, shell, SQL, automation, engineering artifacts
        |
        v
Context governor -> model endpoint -> SSE response
```

- `phase-1/server.py` owns the FastAPI/SSE boundary, auth, uploads, and runtime lifecycle.
- `phase-1/narad_agent.py` routes work to the four canonical avatars.
- `phase-1/avatar_agents.py` defines specialist prompts and registered tools.
- `phase-1/model_registry.py` and `phase-1/context_governor.py` choose model-safe context budgets.
- The user-facing session id stays stable while internal runtime epochs compact long conversations.

## Product Surfaces

- **Chat** is the primary interaction and native artifact surface.
- **Workflows** runs six durable paths: Career, Health, Travel, Teach, Finance, and Documents.
- **Memory** shows recall and user commitments without exposing an internal project-management system.
- **System** shows runtime health, traces, models, and connections.

Workflow stages are the canonical progress model. There is no separate Projects or Kanban database.

## Local Data

All live user data is stored under `~/.narad/`, never in tracked phase directories.

| Path | Purpose |
|---|---|
| `sessions/` | Yantra JSONL traces |
| `threads/` | Durable conversation turns and working state |
| `memory/` | Tiered local semantic memory |
| `learning/` | Teaching workspaces and native learning artifacts |
| `artifacts/` | Generated documents, media, scripts, and reports |
| `attachments/` | Private uploaded files and folder manifests |
| `config/` | Runtime policy, provider, and onboarding state |
| `finance.db` | Personal finance records |
| `health.db` | Health logs and reminders |
| `workflows.db` | Workflow runs, stages, schedules, and events |

Smriti uses dependency-light local indexing with optional TurboVec acceleration. Exact files and tool outputs remain artifact references and are reread on demand rather than copied into every prompt.

## Models

- Connected healthy endpoints become the default for text and multimodal work.
- The configured orchestrator handles routing when available.
- Managed Ollama provides the zero-key local fallback selected by detected RAM.
- Model profiles reserve output capacity and compact at a soft threshold before escalation.

## Tools

- Browser use has two explicit contexts: isolated Playwright by default and profile-granted signed-in Chromium through optional BrowserSkill.
- Android phone use is an optional lazy Artemis sidecar; device grants are profile-scoped and sensitive tasks require verified mode plus confirmation.
- Search uses Exa plus free direct source channels inspired by Agent Reach.
- Gmail, Calendar, Drive, and Photos share one scoped Google OAuth connector.
- Documents use lightweight format-specific readers; Docling is not a dependency.
- Native flashcards and concept maps render in the first-party artifact panel.
- Shell and code execution remain Parashurama-only and policy-gated.

## Safety

- Dharma gates external side effects and fails closed.
- Email, calendar, browser submission, uploads, desktop control, and consequential phone actions are preview-first.
- Andon records runtime quality failures without maintaining a parallel task board.
- Local auth, CORS restrictions, request limits, and private attachment storage are enabled at the server boundary.

## Frontend

The React/Vite frontend loads Chat first. Dashboard, voice, and artifact panels are lazy chunks. Tool state is fetched only when its surface is opened; no Copilot sidecar or second agent runtime is required.

## Source Of Truth

- Agent identity and tool families: `contracts/agent-contracts.json`
- Detailed tool ownership: `AGENTS.md`
- Workflow definitions: `workflow_packs/definitions.py`
- Runtime capability report: `GET /capabilities`
- Current workflow guide: `docs/WORKFLOW_PATHS.md`
