"""Narad's built-in workflow definitions.

Definitions describe state and policy only. They never contain executable code;
the workflow engine resolves stage kinds through an allow-listed handler map.

Every stage declares ``done_when``: structured conditions the engine checks
against evidence it can verify itself (workflow_evidence.py). Free text never
finishes a stage. Intake fields carry a short conversational ``ask`` (the
stage owner asks one or two at a time in chat) and ``carry`` (prefilled from the
person's previous run of the same path). Files come from the phone as chat
attachments, so no intake field is a Mac path.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any


def _field(
    key: str,
    label: str,
    *,
    kind: str = "text",
    required: bool = False,
    placeholder: str = "",
    default: Any = None,
    options: list[str] | None = None,
    help_text: str = "",
    ask: str = "",
    carry: bool = False,
) -> dict[str, Any]:
    return {
        "key": key,
        "label": label,
        "kind": kind,
        "required": required,
        "placeholder": placeholder,
        "default": default,
        "options": options or [],
        "help": help_text,
        "ask": ask,
        "carry": carry,
    }


# ── done_when conditions (see workflow_evidence.py for how each is checked) ──


def _inputs_complete() -> dict[str, Any]:
    return {"kind": "inputs_complete", "label": "Every required detail is filled in"}


def _reported(*fields: str, label: str) -> dict[str, Any]:
    return {"kind": "reported", "fields": list(fields), "label": f"Narad reports {label}"}


def _tool(*tools: str, label: str, minimum: int = 1) -> dict[str, Any]:
    return {"kind": "tool_succeeded", "tools": list(tools), "min": minimum, "label": label}


def _artifact(kind: str, label: str) -> dict[str, Any]:
    return {"kind": "artifact_exists", "artifact": kind, "label": label}


def _records(table: str, label: str, *, minimum: int = 1, status: str = "") -> dict[str, Any]:
    return {"kind": "records_saved", "table": table, "min": minimum, "status": status, "label": label}


def _task_done(label: str) -> dict[str, Any]:
    return {"kind": "task_done", "surface": "kriya", "label": label}


def _approved(label: str = "You approved this step on your phone") -> dict[str, Any]:
    return {"kind": "approval_executed", "surface": "workflow", "label": label}


def _confirmed(label: str) -> dict[str, Any]:
    return {"kind": "user_confirmed", "label": label}


def _guided(event: str, label: str) -> dict[str, Any]:
    return {"kind": "guided", "event": event, "label": label}


def _any(*conditions: dict[str, Any]) -> dict[str, Any]:
    return {"kind": "any", "of": list(conditions), "label": " or ".join(item["label"] for item in conditions)}


_SEARCH_TOOLS = ("exa_search", "web_search")


def _stage(
    stage_id: str,
    title: str,
    owner: str,
    kind: str,
    purpose: str,
    *,
    done_when: list[dict[str, Any]],
    skill: str = "",
    tools: list[str] | None = None,
    confirmation_action: str | None = None,
    recurring: bool = False,
    optional: bool = False,
) -> dict[str, Any]:
    return {
        "id": stage_id,
        "title": title,
        "owner": owner,
        "kind": kind,
        "purpose": purpose,
        "skill": skill,
        "tools": tools or [],
        "requires_confirmation": confirmation_action is not None,
        "confirmation_action": confirmation_action,
        # Only recurring loop stages may be reopened by a schedule; every other
        # scheduled prompt is recorded as a check-in without moving the run.
        "recurring": recurring,
        # The person may skip an optional stage from the Paths screen; a
        # skipped stage is shown as skipped, never as done.
        "optional": optional,
        "done_when": done_when,
    }


def _intake(title: str, owner: str, skill: str, purpose: str) -> dict[str, Any]:
    return _stage("intake", title, owner, "intake", purpose, skill=skill, done_when=[_inputs_complete()])


PACKS: dict[str, dict[str, Any]] = {
    "career": {
        "id": "career",
        "version": 2,
        "title": "Career",
        "eyebrow": "Find, tailor, apply, improve",
        "description": "A closed-loop job search from market research through interview feedback.",
        "accent": "#b45309",
        "owner": "Rama",
        "required_capabilities": ["planning"],
        "optional_capabilities": ["search", "computer", "documents", "email", "calendar"],
        "intake": [
            _field("target_role", "Target role", required=True, placeholder="Senior product manager",
                   ask="Which role are you aiming for?", carry=True),
            _field("locations", "Locations", required=True, placeholder="Bengaluru, remote India",
                   ask="Which cities work for you, or remote?", carry=True),
            _field("experience", "Experience snapshot", kind="textarea", required=True,
                   placeholder="8 years in B2B SaaS...", ask="In two lines, what is your experience so far?",
                   carry=True),
            _field("strengths", "Distinct strengths", kind="textarea", placeholder="0-to-1, analytics, fintech",
                   ask="What are you known for doing well?", carry=True),
            _field("constraints", "Constraints", kind="textarea",
                   placeholder="Remote only, no relocation, 45-day notice",
                   ask="Anything that rules a job out, like notice period or relocation?", carry=True),
            _field("weekly_scan", "Weekly role scan", kind="boolean", default=True),
            _field("reply_watch", "Watch Gmail for replies", kind="boolean", default=True,
                   help_text="Read-only; only when Gmail is connected."),
            _field("nudge_time", "Nudge time", kind="time", default="09:00"),
            _field("timezone", "Timezone", default="Asia/Kolkata"),
        ],
        "stages": [
            _intake("Career baseline", "Rama", "project_plan", "Lock the target, evidence, constraints, and success measure."),
            _stage(
                "market_scan", "Market scan", "Matsya", "research",
                "Find current, credible roles and employer signals.",
                skill="research", tools=["exa_search", "exa_contents", "search_last30days"],
                done_when=[
                    _tool(*_SEARCH_TOOLS, "start_task", label="A live job search ran"),
                    _reported("roles", label="the current roles found, with links"),
                ],
            ),
            _stage(
                "shortlist", "Ranked shortlist", "Rama", "plan",
                "Score opportunities against fit, constraints, and upside, and put the best in the tracker.",
                skill="project_plan", tools=["track_application"],
                done_when=[
                    _records("applications", "At least one role is in your application tracker"),
                    _reported("ranking", label="a ranked shortlist with reasons"),
                ],
            ),
            _stage(
                "tailor", "Application kit", "Krishna", "artifact",
                "Tailor the resume, cover note, and evidence for one selected role. Send your current "
                "resume in this chat.",
                skill="content_create", tools=["create_document"],
                done_when=[_artifact("document", "A tailored resume or cover note file was created")],
            ),
            _stage(
                "apply", "Application review", "Matsya", "action",
                "Preview and submit the selected application in the user's browser.",
                skill="form_submit", tools=["start_task", "computer_use"], confirmation_action="browser_submit",
                done_when=[
                    _approved(),
                    _any(_task_done("The application task finished"), _confirmed("You applied yourself")),
                ],
            ),
            _stage(
                "track", "Application tracking", "Rama", "track",
                "Record state, dates, follow-ups, and missing evidence in the tracker.",
                skill="project_plan", tools=["track_application"],
                done_when=[_records("applications", "An application in your tracker is marked applied",
                                    status="applied")],
            ),
            _stage(
                "prepare", "Interview preparation", "Krishna", "teach",
                "Build a paced interview loop grounded in the role and company.",
                skill="teach",
                done_when=[_any(_reported("practice_questions", label="practice questions for this role"),
                                _confirmed("You feel prepared"))],
            ),
            _stage(
                "review", "Outcome review", "Rama", "review",
                "Use outcomes to update targeting, application evidence, and preparation.",
                skill="project_plan",
                done_when=[_reported("outcome", "next_change", label="the outcome and what to change next")],
            ),
        ],
        "schedule_templates": [
            {"id": "weekly_scan", "title": "Weekly role scan", "enabled_by": "weekly_scan", "cadence": "weekly", "weekdays": [0], "time_field": "nudge_time", "target_stage": "market_scan", "new_cycle_when_complete": True},
            # Read-only: looks for replies from tracked companies and only appends findings.
            {"id": "reply_watch", "title": "Gmail reply check", "enabled_by": "reply_watch", "cadence": "daily", "time": "19:00", "mode": "watch", "watch": "gmail_replies"},
        ],
        "feedback_routes": {"rejected": "market_scan", "no_response": "track", "interview": "prepare", "offer": "review"},
    },
    "health": {
        "id": "health",
        "version": 2,
        "title": "Health",
        "eyebrow": "Plan gently, track honestly",
        "description": "Your lab values, medicines, food, movement, and adherence in one plan with safety boundaries.",
        "accent": "#047857",
        "owner": "Rama",
        "required_capabilities": ["health", "planning"],
        "optional_capabilities": ["search", "calendar", "documents"],
        "intake": [
            _field("goal", "Primary goal", required=True, placeholder="Improve energy and lose weight gradually",
                   ask="What would you most like to improve?", carry=True),
            _field("baseline", "Current baseline", kind="textarea", required=True,
                   placeholder="Typical meals, sleep, movement, work pattern",
                   ask="What does a usual day look like: meals, sleep and movement?"),
            _field("dietary_context", "Food context", kind="textarea", required=True,
                   placeholder="Vegetarian, Indian meals, allergies, dislikes",
                   ask="What do you eat, and anything you avoid or are allergic to?", carry=True),
            _field("activity_limits", "Physical limits", kind="textarea", required=True,
                   placeholder="Knee pain; cleared for walking",
                   ask="Anything your body or your doctor says to avoid?", carry=True),
            _field("available_days", "Available days", placeholder="Mon, Wed, Fri, Sat",
                   ask="Which days can you set time aside?", carry=True),
            _field("daily_checkin", "Daily check-in", kind="boolean", default=True),
            _field("nudge_time", "Check-in time", kind="time", default="20:30"),
            _field("timezone", "Timezone", default="Asia/Kolkata"),
        ],
        "stages": [
            _intake("Baseline and goal", "Rama", "wellness_plan", "Capture current routines, limits, preferences, and the intended outcome."),
            _stage(
                "labs", "Lab report", "Matsya", "track",
                "Send a recent lab report photo or PDF in this chat. Its values are read on the Mac and "
                "saved only after you confirm each one against its crop.",
                skill="document_review", tools=["extract_fields"], optional=True,
                done_when=[_records("lab_results", "Lab values you confirmed are saved")],
            ),
            _stage(
                "safety", "Trend and safety boundary", "Rama", "safety",
                "Read your confirmed lab trend, then name red flags, clinician constraints, and what Narad "
                "must not infer.",
                skill="wellness_plan", tools=["get_lab_results"],
                done_when=[
                    _tool("get_lab_results", label="Your confirmed lab history was read"),
                    _reported("boundaries", label="the safety boundaries and red flags"),
                ],
            ),
            _stage(
                "weekly_plan", "Weekly food and movement plan", "Rama", "plan",
                "Create a realistic seven-day plan with substitutions and recovery room.",
                skill="wellness_plan",
                done_when=[_reported("plan", label="a seven-day plan with substitutions")],
            ),
            _stage(
                "reminders", "Medicine reminders", "Rama", "track",
                "Set a reminder for each medicine you take; it reaches your phone, and your carer if you "
                "share medicine reminders with them.",
                skill="health_log", tools=["set_medication_reminder"], optional=True,
                done_when=[_tool("set_medication_reminder", label="A medicine reminder is set for you")],
            ),
            _stage(
                "schedule", "Schedule preview", "Rama", "action",
                "Preview calendar blocks and check-ins before creating them.",
                skill="schedule_event", tools=["get_upcoming_events", "create_event"],
                confirmation_action="calendar_create",
                done_when=[
                    _approved(),
                    _any(_tool("create_event", label="The calendar blocks were created"),
                         _confirmed("You added them to your calendar yourself")),
                ],
            ),
            _stage(
                "daily_track", "Daily tracking", "Rama", "track",
                "Collect lightweight adherence, energy, sleep, and discomfort signals.",
                skill="health_log", recurring=True,
                done_when=[_any(_tool("log_symptom", label="Today's check-in was logged"),
                                _reported("checkin", label="today's check-in"))],
            ),
            _stage(
                "weekly_review", "Weekly adaptation", "Rama", "review",
                "Compare plan with lived reality and make the smallest useful adjustment.",
                skill="wellness_plan", recurring=True,
                done_when=[_reported("adjustment", label="the one adjustment for next week")],
            ),
        ],
        "schedule_templates": [
            {"id": "daily_checkin", "title": "Daily health check-in", "enabled_by": "daily_checkin", "cadence": "daily", "time_field": "nudge_time", "target_stage": "daily_track"},
            {"id": "weekly_review", "title": "Weekly health review", "enabled_by": "daily_checkin", "cadence": "weekly", "weekdays": [6], "time": "18:00", "target_stage": "weekly_review"},
        ],
        "feedback_routes": {"new_lab_report": "labs", "off_track": "weekly_plan", "pain_or_red_flag": "safety", "goal_changed": "intake", "week_complete": "weekly_review"},
    },
    "travel": {
        "id": "travel",
        "version": 2,
        "title": "Travel",
        "eyebrow": "Research deeply, book deliberately",
        "description": "Constraint-aware discovery, itinerary design, and confirmation-gated booking help.",
        "accent": "#0369a1",
        "owner": "Rama",
        "required_capabilities": ["planning"],
        "optional_capabilities": ["search", "computer", "calendar"],
        "intake": [
            _field("origin", "Starting from", required=True, placeholder="Delhi",
                   ask="Where will you start from?", carry=True),
            _field("destination", "Destination", required=True, placeholder="Japan", ask="Where would you like to go?"),
            _field("dates", "Dates or flexibility", required=True, placeholder="10-18 November, +/- 2 days",
                   ask="Which dates, and how flexible are they?"),
            _field("travelers", "Travelers", required=True, placeholder="2 adults",
                   ask="Who is travelling?", carry=True),
            _field("budget", "Total budget", required=True, placeholder="INR 300,000",
                   ask="What is the total budget?"),
            _field("preferences", "Trip style", kind="textarea", placeholder="Food, design, slower pace, no nightlife",
                   ask="What kind of trip do you enjoy?", carry=True),
            _field("accessibility", "Limits or accessibility", kind="textarea",
                   placeholder="Avoid long climbs; vegetarian food",
                   ask="Anything to plan around, like food or long walks?", carry=True),
            _field("price_watch", "Price watch", kind="boolean", default=False,
                   help_text="A daily fare check that only adds findings; it never changes your plan."),
            _field("nudge_time", "Update time", kind="time", default="10:00"),
            _field("timezone", "Timezone", default="Asia/Kolkata"),
        ],
        "stages": [
            _intake("Travel brief", "Rama", "project_plan", "Lock dates, people, budget, pace, and hard constraints."),
            _stage(
                "research", "Live search", "Matsya", "research",
                "Search live fares, stays, and conditions with a web task (the cloud browser for public "
                "searches) and report the options found.",
                skill="research", tools=["start_task", "exa_search", "exa_contents"],
                done_when=[
                    _any(_task_done("A live search task finished"),
                         _tool(*_SEARCH_TOOLS, label="A live web search ran")),
                    _reported("options", label="the options found, with prices and links"),
                ],
            ),
            _stage(
                "compare", "Option comparison", "Rama", "plan",
                "Compare routes and stays using consistent criteria and total cost.",
                skill="project_plan",
                done_when=[_reported("recommendation", "total_cost", label="a recommended option with its total cost")],
            ),
            _stage(
                "itinerary", "Itinerary and budget", "Krishna", "artifact",
                "Produce a coherent day-by-day plan with budget and alternatives.",
                skill="content_create", tools=["create_document", "create_webpage"],
                done_when=[_artifact("document", "The itinerary file was created")],
            ),
            _stage(
                "booking", "Booking desk", "Matsya", "action",
                "Recheck availability and preview each reservation before commitment.",
                skill="form_submit", tools=["start_task", "computer_use"], confirmation_action="booking_commitment",
                done_when=[
                    _approved(),
                    _any(_task_done("The booking task finished"), _confirmed("You booked it yourself")),
                ],
            ),
            _stage(
                "trip_ready", "Trip pack", "Krishna", "artifact",
                "Prepare one trip pack: confirmations, checklist, maps, and critical reminders.",
                skill="content_create", tools=["create_document", "create_webpage"],
                done_when=[_artifact("document", "The trip pack file was created")],
            ),
            _stage(
                "review", "Change review", "Rama", "review",
                "Re-plan only the portions affected by a price, weather, or availability change.",
                skill="project_plan",
                done_when=[_any(_reported("changes", label="what changed and what was re-planned"),
                                _confirmed("Nothing needs to change"))],
            ),
        ],
        "schedule_templates": [
            # A watch never moves the run: it starts a fare check and appends what it finds.
            {"id": "price_watch", "title": "Travel price and availability check", "enabled_by": "price_watch", "cadence": "daily", "time_field": "nudge_time", "target_stage": "research", "mode": "watch", "watch": "prices"},
        ],
        "feedback_routes": {"price_changed": "compare", "availability_changed": "itinerary", "dates_changed": "intake", "booking_complete": "trip_ready"},
    },
    "teach": {
        "id": "teach",
        "version": 2,
        "title": "Teach Anything",
        "eyebrow": "One concept, one check, durable mastery",
        "description": "A paced learning path backed by Gurukul workspaces and spaced review.",
        "accent": "#1d4ed8",
        "owner": "Krishna",
        "required_capabilities": ["learning"],
        "optional_capabilities": ["search", "tts"],
        "intake": [
            _field("topic", "Topic", required=True, placeholder="Transformer attention", ask="What would you like to learn?"),
            _field("outcome", "Learning outcome", required=True, placeholder="Explain it confidently in an interview",
                   ask="What should you be able to do once you have learned it?"),
            _field("current_level", "Current level", required=True, options=["New", "Some familiarity", "Working knowledge", "Advanced"], kind="select", default="New",
                   ask="How much do you know about it already?"),
            _field("minutes_per_session", "Minutes per session", kind="number", default=20),
            _field("mode", "Learning mode", kind="select", options=["Hybrid", "First principles", "Q&A"], default="Hybrid"),
            _field("spaced_reviews", "Spaced reviews", kind="boolean", default=True),
            _field("nudge_time", "Review time", kind="time", default="09:00"),
            _field("timezone", "Timezone", default="Asia/Kolkata"),
        ],
        "stages": [
            _intake("Learning mission", "Krishna", "teach", "Define the outcome, starting point, pace, and evidence of mastery."),
            _stage(
                "diagnostic", "Diagnostic", "Krishna", "teach",
                "Ask the smallest useful question to locate the learner's current model.", skill="teach",
                done_when=[_any(_guided("answer", "You answered the first check"),
                                _reported("starting_point", label="where you are starting from"))],
            ),
            _stage(
                "lesson", "Paced lesson", "Krishna", "teach",
                "Teach one frontier concept with an analogy and one concrete example.", skill="teach",
                done_when=[_any(_guided("complete", "You finished the guided lesson"),
                                _reported("concept", label="the concept taught, with one example"))],
            ),
            _stage(
                "check", "Understanding check", "Krishna", "teach",
                "Use one question to test transfer rather than recognition.", skill="teach",
                done_when=[_any(_guided("complete", "You finished the guided lesson"),
                                _reported("check_result", label="how you did on a transfer question"))],
            ),
            _stage(
                "reinforce", "Targeted reinforcement", "Krishna", "teach",
                "Correct the named misconception with a different representation.", skill="teach",
                done_when=[_any(_guided("complete", "You finished the guided lesson"),
                                _reported("correction", label="the misconception corrected"))],
            ),
            _stage(
                "review", "Mastery review", "Krishna", "review",
                "Record mastery, schedule the next review, and choose the next atom.", skill="teach", recurring=True,
                done_when=[_any(_guided("complete", "You finished the guided lesson"),
                                _reported("mastery", label="what you have mastered and the next review"))],
            ),
        ],
        "schedule_templates": [
            {"id": "spaced_reviews", "title": "Spaced learning review", "enabled_by": "spaced_reviews", "cadence": "weekly", "weekdays": [1, 4], "time_field": "nudge_time", "target_stage": "review"},
        ],
        "feedback_routes": {"incorrect": "reinforce", "shaky": "reinforce", "mastered": "review", "new_goal": "intake"},
    },
    "finance": {
        "id": "finance",
        "version": 2,
        "title": "Personal Finance",
        "eyebrow": "See clearly, model choices, stay in control",
        "description": "Your statements in a local ledger, grounded scenarios, budgets and goals, and monthly reviews.",
        "accent": "#7c3f14",
        "owner": "Rama",
        "required_capabilities": ["finance", "planning"],
        "optional_capabilities": ["search", "documents"],
        "intake": [
            _field("currency", "Currency", required=True, default="INR", ask="Which currency do you use?", carry=True),
            _field("goal", "Primary financial goal", required=True, placeholder="Build a 9-month emergency fund",
                   ask="What is the one money goal that matters most right now?", carry=True),
            _field("monthly_context", "Monthly cash-flow context", kind="textarea", required=True,
                   placeholder="Approximate income, fixed costs, variable costs",
                   ask="Roughly what comes in and goes out each month?"),
            _field("debts_and_commitments", "Debts and commitments", kind="textarea",
                   placeholder="Loan balances, rates, recurring obligations",
                   ask="Any loans, EMIs or fixed commitments?", carry=True),
            _field("risk_comfort", "Risk comfort", kind="select", options=["Low", "Moderate", "High"], default="Moderate", carry=True),
            _field("monthly_review", "Monthly review", kind="boolean", default=True),
            _field("nudge_time", "Review time", kind="time", default="18:30"),
            _field("timezone", "Timezone", default="Asia/Kolkata"),
        ],
        "stages": [
            _intake("Financial baseline", "Rama", "budget_plan", "Define goals, cash-flow assumptions, obligations, and risk boundaries."),
            _stage(
                "ingest", "Statement upload", "Rama", "track",
                "Send your bank statement in this chat: a CSV imports directly; a PDF or photo is read on "
                "the Mac and saved only after you confirm each line.",
                skill="finance_import", tools=["import_csv", "extract_fields", "get_financial_context"],
                done_when=[_records("transactions", "Statement transactions are saved in your ledger")],
            ),
            _stage(
                "analyze", "Cash-flow intelligence", "Rama", "analysis",
                "Measure spending, recurring costs, anomalies, and goal trajectory.",
                skill="spending_review", tools=["get_spending", "get_recurring_expenses", "get_spend_patterns"],
                done_when=[
                    _tool("get_spending", "get_financial_context", "get_recurring_expenses", "get_spend_patterns",
                          label="Your saved transactions were analysed"),
                    _reported("findings", label="where the money goes, with recurring costs"),
                ],
            ),
            _stage(
                "scenarios", "Current intelligence and scenarios", "Matsya", "research",
                "Ground rates, rules, and market assumptions in dated sources.",
                skill="research", tools=["exa_search", "exa_contents"],
                done_when=[
                    _tool(*_SEARCH_TOOLS, label="Current rates and rules were searched"),
                    _reported("assumptions", label="dated assumptions with sources"),
                ],
            ),
            _stage(
                "plan", "Budget and goal", "Rama", "plan",
                "Compare choices and tradeoffs, then save a budget or goal with a reversible next step.",
                skill="financial_decision", tools=["set_budget", "add_goal"],
                done_when=[
                    _tool("set_budget", "add_goal", "update_goal_progress", label="A budget or goal was saved"),
                    _reported("plan", label="the plan and its next reversible step"),
                ],
            ),
            _stage(
                "review", "Monthly review", "Rama", "review",
                "Compare plan with actuals and adapt budgets or goal timing.",
                skill="spending_review", tools=["get_budget_status", "get_goals"], recurring=True,
                done_when=[
                    _tool("get_budget_status", "get_goals", label="Budget and goal status were checked"),
                    _reported("adjustment", label="the adjustment for next month"),
                ],
            ),
        ],
        "schedule_templates": [
            {"id": "monthly_review", "title": "Monthly finance review", "enabled_by": "monthly_review", "cadence": "monthly", "day": 1, "time_field": "nudge_time", "target_stage": "review"},
        ],
        "feedback_routes": {"new_statement": "ingest", "over_budget": "analyze", "income_changed": "intake", "goal_changed": "intake", "month_closed": "review"},
    },
    "documents": {
        "id": "documents",
        "version": 2,
        "title": "Documents and Insights",
        "eyebrow": "Evidence into a story people can use",
        "description": "Analyze source material and produce presentations, insight reports, or polished narratives.",
        "accent": "#9f1239",
        "owner": "Krishna",
        "required_capabilities": ["planning"],
        "optional_capabilities": ["documents", "presentation", "sql", "filesystem"],
        "intake": [
            _field("deliverable", "Deliverable", kind="select", required=True, options=["Presentation", "Data insights", "Narrative", "Creative writing"], default="Presentation",
                   ask="What should we make: a presentation, insights, a narrative or creative writing?"),
            _field("objective", "Objective", kind="textarea", required=True,
                   placeholder="What should the audience understand or decide?",
                   ask="What should the audience understand or decide?"),
            _field("audience", "Audience", required=True, placeholder="Board, customers, classroom, general readers",
                   ask="Who is it for?", carry=True),
            _field("tone", "Tone", placeholder="Editorial, decisive, warm", ask="What tone should it have?", carry=True),
            _field("constraints", "Format constraints", kind="textarea", placeholder="10 slides, 16:9, speaker notes",
                   ask="Any format limits, like length or slide count?"),
            _field("recurring_report", "Recurring report", kind="boolean", default=False),
            _field("nudge_time", "Report time", kind="time", default="09:30"),
            _field("timezone", "Timezone", default="Asia/Kolkata"),
        ],
        "stages": [
            _intake("Creative brief", "Krishna", "content_create", "Lock the audience, decision, source material, format, and constraints."),
            _stage(
                "ingest", "Source ingestion", "Matsya", "analysis",
                "Send the source files in this chat, then extract them and list exactly what evidence they hold.",
                skill="document_review", tools=["extract_document"],
                done_when=[
                    _tool("extract_document", "extract_fields", label="Your source files were read"),
                    _reported("sources", label="the evidence inventory"),
                ],
            ),
            _stage(
                "analyze", "Analysis and insights", "Parashurama", "analysis",
                "Read structured sources and find defensible patterns, caveats, and decision-relevant insights.",
                skill="data_pipeline", tools=["read_file", "query_database"],
                done_when=[_reported("insights", label="the insights, with caveats")],
            ),
            _stage(
                "story", "Narrative architecture", "Krishna", "artifact",
                "Turn evidence into one coherent argument or creative arc.", skill="content_create",
                done_when=[_reported("outline", label="the storyline")],
            ),
            _stage(
                "create", "Artifact production", "Krishna", "artifact",
                "Create the requested deck, report, chart set, or narrative. Each new file is a new version.",
                skill="presentation_create", tools=["create_document", "create_webpage"],
                done_when=[_artifact("document", "The draft file was created")],
            ),
            _stage(
                "review", "Design and truth audit", "Krishna", "review",
                "Audit clarity, evidence, pacing, visual hierarchy, and unsupported claims.",
                skill="presentation_create",
                done_when=[_any(_confirmed("You say the draft is right"),
                                _reported("audit", label="the truth and design audit"))],
            ),
            _stage(
                "export", "Final export", "Krishna", "artifact",
                "Export the latest version with its sources and provenance (Export on the Paths screen).",
                skill="content_create",
                done_when=[_artifact("export", "An export of the latest version with its sources was made")],
            ),
        ],
        "schedule_templates": [
            {"id": "recurring_report", "title": "Recurring document refresh", "enabled_by": "recurring_report", "cadence": "weekly", "weekdays": [0], "time_field": "nudge_time", "target_stage": "ingest", "new_cycle_when_complete": True},
        ],
        "feedback_routes": {"revision_requested": "story", "data_changed": "ingest", "claim_challenged": "analyze", "approved": "export"},
    },
}


def get_pack(workflow_id: str) -> dict[str, Any] | None:
    pack = PACKS.get((workflow_id or "").strip().lower())
    return deepcopy(pack) if pack else None


def list_packs() -> list[dict[str, Any]]:
    return [deepcopy(PACKS[key]) for key in ("career", "health", "travel", "teach", "finance", "documents")]
