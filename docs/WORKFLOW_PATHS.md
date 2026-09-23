# Workflow Paths

Narad exposes six predefined, durable workflows inside **Work > Paths**:
Career, Health, Travel, Teach Anything, Personal Finance, and Documents.
They use the same four-agent runtime as normal chat; no fifth agent or app
surface is introduced.

## Runtime Shape

Every run follows a declarative stage manifest and persists to
`~/.narad/workflows.db`. A stage is mirrored into the existing durable project
task store. The runtime keeps intake, stage outputs, artifacts, citations,
feedback, confirmation state, schedules, and an append-only event record.

The current stage is delivered to Narad as a compact context packet. A normal
chat remains unconstrained unless it carries `workflow_run_id`. External actions
such as applications, bookings, and calendar writes cannot complete before the
Dharma confirmation gate records explicit approval.

## Research Ladder

1. `web_search` uses Exa highlights by default and Tavily as a fallback.
2. `exa_search` adds deep search, output schemas, citations, and freshness controls.
3. `exa_contents` retrieves bounded full text for known URLs.
4. `search_last30days` adds current community signals when relevant.
5. Firecrawl is an optional difficult-page extraction fallback.
6. `computer_use` alone handles stateful pages, logins, forms, and confirmation-gated actions.

Exa replaces TinyFish for research and extraction, but intentionally does not
replace browser control. This separation prevents a search provider from gaining
unnecessary authority over authenticated sessions.

## Scheduling

Workflow recurrence is typed (`daily`, `weekly`, `monthly`, or bounded interval),
timezone-aware, and evaluated by Kala. Each occurrence has a deterministic event
id, so retries do not duplicate a nudge. Vahana delivers the reminder and the run
reopens the declared checkpoint stage. Missed frontend events reconcile through
focus refresh and 15-second polling while Paths is visible.

## HTTP Contracts

- `GET /workflows`
- `POST /workflows/{workflow_id}/runs`
- `GET /workflow-runs`
- `GET /workflow-runs/{run_id}`
- `GET /workflow-runs/{run_id}/context`
- `POST /workflow-runs/{run_id}/actions`
- `PATCH /workflow-schedules/{schedule_id}`

The guided `/teach` API creates or resumes the same Teach Anything workflow and
writes learning checkpoints into it. The exact lesson state remains in the learning workspace;
the workflow stores the macro progress and review rhythm.
