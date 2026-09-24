# Workflow Paths

Narad exposes six predefined, durable workflows inside **Work > Paths**:
Career, Health, Travel, Teach Anything, Personal Finance, and Documents.
They use the same four-agent runtime as normal chat; no fifth agent or app
surface is introduced.

## Runtime Shape

Every run follows a declarative stage manifest (`workflow_packs/definitions.py`,
version 2) and persists to `~/.narad/workflows.db`. The runtime keeps intake,
stage outputs, per-stage evidence, artifacts and versions, citations, feedback,
confirmation state, schedules, path records (the application tracker and watch
findings), and an append-only event record.

The current stage is delivered to Narad as a compact context packet: purpose,
the finish line with what is already met, what the stage is waiting on (a
review, a task, an approval), the fields to report, the missing intake
questions, and earlier stages' structured results. External actions such as
applications, bookings, and calendar writes cannot complete before the
Anumati approval of the stage's exact preview.

## A Stage Completes Only On Evidence

Every stage declares `done_when`: structured conditions that must all hold
(`any` groups alternatives). The engine checks them against evidence it verifies
itself (`workflow_evidence.py`), never against what a model wrote:

| Condition | Holds when |
|---|---|
| `inputs_complete` | every required intake detail has a value (a reopened intake also needs a fresh look) |
| `reported(fields)` | the stage owner's `done` report carries these fields with real content |
| `tool_succeeded(tools)` | the server saw one of these tools return a success status in a turn bound to the run |
| `artifact_exists(kind)` | a generated file of that kind exists under the run owner's own `artifacts/runs/<profile>/` |
| `records_saved(table, min)` | confirmed rows exist in the owner's own `health.db` `lab_results` or `finance.db` `transactions` (through a saved review or an `import_csv` receipt), or in the path's application tracker |
| `task_done` | a Kriya task in the owner's own `kriya.db` finished (`done`) |
| `approval_executed` | the stage's own Anumati proposal was approved and ran |
| `user_confirmed` | the person tapped "I did this" on the Paths screen |
| `guided(event)` | the Gurukul loop graded an answer or finished the syllabus |

**How the owner reports.** Every avatar has `report_stage_result(status, summary,
fields, questions, evidence_ids, reason)` with status `done | needs_input |
blocked | in_progress`. It works only in a turn the server bound to a run
(`workflow_evidence.bind_turn`); it returns `completed`, `not_done` (with what is
missing), `approval_requested` (a gated stage's result before approval is its
preview, sent as an approval card) or `recorded`. `needs_input` keeps at most two
questions; `blocked` keeps the reason. A reply's text never completes a stage:
a turn without a report is recorded as a `chat_turn` and the stage stays open.

**Where evidence comes from.** The avatar tool wrapper records a receipt for
every real tool response of a bound turn (tool, status, ok, review/task/proposal
ids, artifacts, counts). Ids a model names in `evidence_ids` count only when they
resolve in the run owner's own stores and were created after the run started;
another profile's review, task, proposal or file is simply not found.

**Who needs to say "done".** A stage met only through outcomes (saved records,
a finished task, an executed approval, the person's tap, the guided loop)
settles by itself. A stage that relies on the model's own work (a report, a
tool run, a file) also needs the owner's `done` report.

**Settling later.** Evidence that lands after the turn settles the stage on the
next read (`GET /workflow-runs`, `GET /workflow-runs/{id}`), the next Kala tick
(`fire_due_workflow_schedules` ends with `settle_active_runs`), or at once when a
document review is saved (`POST /documents/reviews/{id}/save`).

## Per-Path Stages and Finish Lines

| Path | Stage | Done when |
|---|---|---|
| Career | Career baseline | every required detail is filled in |
| | Market scan (Matsya) | a live job search ran (`exa_search`, `web_search` or `start_task`) + roles reported |
| | Ranked shortlist (Rama) | a role is in the application tracker (`track_application`) + ranking reported |
| | Application kit (Krishna) | a tailored resume or cover note file exists |
| | Application review (Matsya, approval) | the approval ran + (the application task finished, or you applied yourself) |
| | Application tracking (Rama) | a tracked application is marked applied (or later) since the stage opened |
| | Interview preparation (Krishna) | practice questions reported, or you feel prepared |
| | Outcome review (Rama) | outcome and next change reported |
| Health | Baseline and goal | every required detail is filled in |
| | Lab report (Matsya, optional) | lab values you confirmed on the crop review are saved |
| | Trend and safety boundary (Rama) | `get_lab_results` ran + boundaries reported |
| | Weekly food and movement plan (Rama) | plan reported |
| | Medicine reminders (Rama, optional) | `set_medication_reminder` succeeded (Kala fires it per profile) |
| | Schedule preview (Rama, approval) | the approval ran + (`create_event` succeeded, or you added them yourself) |
| | Daily tracking (recurring) | `log_symptom` succeeded or the check-in reported |
| | Weekly adaptation (recurring) | the adjustment reported |
| Travel | Travel brief | every required detail is filled in |
| | Live search (Matsya) | (a search task finished, or a web search ran) + options reported |
| | Option comparison (Rama) | recommendation and total cost reported |
| | Itinerary and budget (Krishna) | the itinerary file exists |
| | Booking desk (Matsya, approval) | the approval ran + (the booking task finished, or you booked it yourself) |
| | Trip pack (Krishna) | the trip pack file exists |
| | Change review (Rama) | changes reported, or nothing needs to change |
| Teach | Learning mission | every required detail is filled in |
| | Diagnostic | a guided check answered (or skipped), or the starting point reported |
| | Lesson, Check, Reinforce, Mastery review | the guided syllabus finished, or the stage's field reported |
| Finance | Financial baseline | every required detail is filled in |
| | Statement upload (Rama) | statement transactions are saved (CSV through `import_csv`; photo or PDF through `extract_fields` and a confirmed review) |
| | Cash-flow intelligence (Rama) | a spending read ran + findings reported |
| | Current intelligence and scenarios (Matsya) | a web search ran + dated assumptions reported |
| | Budget and goal (Rama) | `set_budget`, `add_goal` or `update_goal_progress` succeeded + plan reported |
| | Monthly review (recurring) | budget/goal status read + adjustment reported |
| Documents | Creative brief | every required detail is filled in |
| | Source ingestion (Matsya) | `extract_document` or `extract_fields` ran + evidence inventory reported |
| | Analysis and insights (Parashurama) | insights reported |
| | Narrative architecture (Krishna) | storyline reported |
| | Artifact production (Krishna) | a draft file exists (each new file is a numbered version) |
| | Design and truth audit (Krishna) | you say the draft is right, or the audit reported |
| | Final export | an export zip of the latest version with `provenance.md` exists (Export on the Paths screen) |

Optional stages (Health lab report and medicine reminders) can be skipped from
the Paths screen; a skipped stage shows as skipped, never as done.

## Intent To Path

`phase-1/path_intent.py` is a table of English, Hinglish and Hindi (Devanagari)
phrases per path, with negatives for look-alikes ("budget airline", "power
trip", definition questions, someone else's reports). A message that looks like
two paths gets nothing. `prerouter.suggest_path` adds a bank-statement CSV as a
Personal Finance signal and stays quiet inside a bound stage or a lesson check.

For an unbound turn, the server emits a `path_suggestion` SSE event before
`done` (`workflow_id`, `title`, `action` start or resume, the open `run_id`, the
first two intake questions, the thread's `session_id`), and adds a `[PATH OFFER]`
line so the supervisor and avatars never claim a path started. One offer per
path per thread (`path_offers` in the working state). The app shows a small card
(`PathSuggestionCard.tsx`): **Start** creates the run with `partial: true` bound
to this thread; **Continue here** binds the open run to this thread
(`action: bind`); **Not now** hides it. Nothing starts without the tap.

A chat thread's turns belong to the open run bound to it, resolved on the server
(`bound_run_for_session`), so a path continues on every device without the client
sending its id. A stale id from another thread is still dropped for the turn.

## Mobile-First Intake

A chat start opens on the intake stage, prefilled from the phone's time zone
(notification preferences) and the fields marked `carry` in the person's last run
of the same path; the owner asks the missing details one or two at a time (each
field has a short `ask`) and reports answers as intake fields. The Paths screen
asks them two at a time too, then optional details, then the rhythm settings.
Files come from the phone as chat attachments: no intake field is a Mac path,
and the packet tells the avatar never to ask for one.

## Scheduling

Workflow recurrence is typed (`daily`, `weekly`, `monthly`, or bounded interval),
timezone-aware, and evaluated by Kala. Each occurrence has a deterministic event
id, so retries do not duplicate a nudge. A check-in never rewinds completed
stages and never clears a pending or approved confirmation; only a recurring
loop stage the run has reached is reopened (with fresh evidence).

Watch templates (`mode: watch`) never move the run at all; they append findings:
- **Travel price watch**: submits a search-only Kriya task (the cloud browser for
  public searches); its answer lands on the finding when the task finishes (the
  task sends `task_done`). Without a browser the person gets a reminder instead.
- **Career Gmail reply check** (read-only, only when Gmail is connected): searches
  the last 8 days for replies from companies marked applied or interview, records
  sender, subject and date (never the body), notes the reply on the tracker row,
  and notifies only when something new arrived.

## HTTP Contracts

- `GET /workflows`
- `GET /workflows/{workflow_id}/intake` (prefill values and the first questions)
- `POST /workflows/{workflow_id}/runs` (`inputs`, `title`, `session_id`, `partial`)
- `GET /workflow-runs` and `GET /workflow-runs/{run_id}` (settle first)
- `GET /workflow-runs/{run_id}/context`
- `POST /workflow-runs/{run_id}/actions`: `pause`, `resume`, `cancel`,
  `update_inputs`, `feedback`, `request_confirmation`, `approve`, `confirm`
  (alias `complete` / `advance`: the person's own "Done", which finishes only a
  stage that accepts it and otherwise answers 409 with what is missing), `skip`
  (optional stages), `bind` (`payload.session_id`), `export` (Documents)
- `PATCH /workflow-schedules/{schedule_id}`

A run payload carries, per stage, `done_when_text` and (for the current stage)
`check` with each condition's met state and detail; `evidence` (verified refs with
links: `/?review=`, `/?task=`, `/?approval=`, `/media/...`); `stage_result`;
`intake_questions`; `applications`; `findings`; `versions`; and `next_action`
(`chat`, `answer`, `blocked`, `review`, `wait`, `approve`, `export`, `resume`,
`complete`, `none`, with `url`, `detail`, `questions`, `can_confirm`, `can_skip`).

The guided `/teach` API creates or resumes the same Teach Anything workflow; its
graded answers and finished syllabus are the Teach stages' evidence
(`record_guided_progress`). The exact lesson state remains in the learning
workspace; the workflow stores the macro progress and review rhythm.

## Research Ladder

1. `web_search` uses Exa highlights by default and Tavily as a fallback.
2. `exa_search` adds deep search, output schemas, citations, and freshness controls.
3. `exa_contents` retrieves bounded full text for known URLs.
4. `search_last30days` adds current community signals when relevant.
5. Firecrawl is an optional difficult-page extraction fallback.
6. `start_task` (Kriya) handles multi-step searches and forms; `computer_use` a single look or action.

Exa replaces TinyFish for research and extraction, but intentionally does not
replace browser control. This separation prevents a search provider from gaining
unnecessary authority over authenticated sessions.
