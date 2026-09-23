"""Narad's built-in workflow definitions.

Definitions describe state and policy only. They never contain executable code;
the workflow engine resolves stage kinds through an allow-listed handler map.
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
    }


def _stage(
    stage_id: str,
    title: str,
    owner: str,
    kind: str,
    purpose: str,
    *,
    skill: str = "",
    tools: list[str] | None = None,
    confirmation_action: str | None = None,
    recurring: bool = False,
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
    }


PACKS: dict[str, dict[str, Any]] = {
    "career": {
        "id": "career",
        "version": 1,
        "title": "Career",
        "eyebrow": "Find, tailor, apply, improve",
        "description": "A closed-loop job search from market research through interview feedback.",
        "accent": "#b45309",
        "owner": "Rama",
        "required_capabilities": ["planning"],
        "optional_capabilities": ["search", "computer", "documents", "email", "calendar"],
        "intake": [
            _field("target_role", "Target role", required=True, placeholder="Senior product manager"),
            _field("locations", "Locations", required=True, placeholder="Bengaluru, remote India"),
            _field("experience", "Experience snapshot", kind="textarea", required=True, placeholder="8 years in B2B SaaS..."),
            _field("strengths", "Distinct strengths", kind="textarea", placeholder="0-to-1, analytics, fintech"),
            _field("constraints", "Constraints", kind="textarea", placeholder="Remote only, no relocation, 45-day notice"),
            _field("resume_path", "Resume path", placeholder="/Users/me/Documents/resume.docx"),
            _field("weekly_scan", "Weekly role scan", kind="boolean", default=True),
            _field("nudge_time", "Nudge time", kind="time", default="09:00"),
            _field("timezone", "Timezone", default="Asia/Kolkata"),
        ],
        "stages": [
            _stage("intake", "Career baseline", "Rama", "intake", "Lock the target, evidence, constraints, and success measure.", skill="project_plan"),
            _stage("market_scan", "Market scan", "Matsya", "research", "Find current, credible roles and employer signals.", skill="research", tools=["exa_search", "exa_contents", "search_last30days"]),
            _stage("shortlist", "Ranked shortlist", "Rama", "plan", "Score opportunities against fit, constraints, and upside.", skill="project_plan"),
            _stage("tailor", "Application kit", "Krishna", "artifact", "Tailor the resume, cover note, and evidence for one selected role.", skill="content_create", tools=["create_document"]),
            _stage("apply", "Application review", "Matsya", "action", "Preview and submit the selected application in the user's browser.", skill="form_submit", tools=["computer_use"], confirmation_action="browser_submit"),
            _stage("track", "Application tracking", "Rama", "track", "Record state, dates, follow-ups, and missing evidence.", skill="project_plan"),
            _stage("prepare", "Interview preparation", "Krishna", "teach", "Build a paced interview loop grounded in the role and company.", skill="teach"),
            _stage("review", "Outcome review", "Rama", "review", "Use outcomes to update targeting, application evidence, and preparation.", skill="project_plan"),
        ],
        "schedule_templates": [
            {"id": "weekly_scan", "title": "Weekly role scan", "enabled_by": "weekly_scan", "cadence": "weekly", "weekdays": [0], "time_field": "nudge_time", "target_stage": "market_scan", "new_cycle_when_complete": True},
        ],
        "feedback_routes": {"rejected": "market_scan", "no_response": "track", "interview": "prepare", "offer": "review"},
    },
    "health": {
        "id": "health",
        "version": 1,
        "title": "Health",
        "eyebrow": "Plan gently, track honestly",
        "description": "Personal-context food, movement, and adherence planning with safety boundaries.",
        "accent": "#047857",
        "owner": "Rama",
        "required_capabilities": ["health", "planning"],
        "optional_capabilities": ["search", "calendar"],
        "intake": [
            _field("goal", "Primary goal", required=True, placeholder="Improve energy and lose weight gradually"),
            _field("baseline", "Current baseline", kind="textarea", required=True, placeholder="Typical meals, sleep, movement, work pattern"),
            _field("dietary_context", "Food context", kind="textarea", required=True, placeholder="Vegetarian, Indian meals, allergies, dislikes"),
            _field("activity_limits", "Physical limits", kind="textarea", required=True, placeholder="Knee pain; cleared for walking"),
            _field("available_days", "Available days", placeholder="Mon, Wed, Fri, Sat"),
            _field("daily_checkin", "Daily check-in", kind="boolean", default=True),
            _field("nudge_time", "Check-in time", kind="time", default="20:30"),
            _field("timezone", "Timezone", default="Asia/Kolkata"),
        ],
        "stages": [
            _stage("intake", "Baseline and goal", "Rama", "intake", "Capture current routines, limits, preferences, and the intended outcome.", skill="wellness_plan"),
            _stage("safety", "Safety boundary", "Rama", "safety", "Identify red flags, clinician constraints, and what Narad must not infer.", skill="wellness_plan"),
            _stage("weekly_plan", "Weekly food and movement plan", "Rama", "plan", "Create a realistic seven-day plan with substitutions and recovery room.", skill="wellness_plan"),
            _stage("schedule", "Schedule preview", "Rama", "action", "Preview calendar blocks and check-ins before creating them.", skill="schedule_event", tools=["get_upcoming_events", "create_event"], confirmation_action="calendar_create"),
            _stage("daily_track", "Daily tracking", "Rama", "track", "Collect lightweight adherence, energy, sleep, and discomfort signals.", skill="health_log", recurring=True),
            _stage("weekly_review", "Weekly adaptation", "Rama", "review", "Compare plan with lived reality and make the smallest useful adjustment.", skill="wellness_plan", recurring=True),
        ],
        "schedule_templates": [
            {"id": "daily_checkin", "title": "Daily health check-in", "enabled_by": "daily_checkin", "cadence": "daily", "time_field": "nudge_time", "target_stage": "daily_track"},
            {"id": "weekly_review", "title": "Weekly health review", "enabled_by": "daily_checkin", "cadence": "weekly", "weekdays": [6], "time": "18:00", "target_stage": "weekly_review"},
        ],
        "feedback_routes": {"off_track": "weekly_plan", "pain_or_red_flag": "safety", "goal_changed": "intake", "week_complete": "weekly_review"},
    },
    "travel": {
        "id": "travel",
        "version": 1,
        "title": "Travel",
        "eyebrow": "Research deeply, book deliberately",
        "description": "Constraint-aware discovery, itinerary design, and confirmation-gated booking help.",
        "accent": "#0369a1",
        "owner": "Rama",
        "required_capabilities": ["planning"],
        "optional_capabilities": ["search", "computer", "calendar"],
        "intake": [
            _field("origin", "Starting from", required=True, placeholder="Delhi"),
            _field("destination", "Destination", required=True, placeholder="Japan"),
            _field("dates", "Dates or flexibility", required=True, placeholder="10-18 November, +/- 2 days"),
            _field("travelers", "Travelers", required=True, placeholder="2 adults"),
            _field("budget", "Total budget", required=True, placeholder="INR 300,000"),
            _field("preferences", "Trip style", kind="textarea", placeholder="Food, design, slower pace, no nightlife"),
            _field("accessibility", "Limits or accessibility", kind="textarea", placeholder="Avoid long climbs; vegetarian food"),
            _field("price_watch", "Price watch", kind="boolean", default=False),
            _field("nudge_time", "Update time", kind="time", default="10:00"),
            _field("timezone", "Timezone", default="Asia/Kolkata"),
        ],
        "stages": [
            _stage("intake", "Travel brief", "Rama", "intake", "Lock dates, people, budget, pace, and hard constraints.", skill="project_plan"),
            _stage("research", "Live destination research", "Matsya", "research", "Ground options in current transport, seasonality, closures, and local evidence.", skill="research", tools=["exa_search", "exa_contents", "search_last30days"]),
            _stage("compare", "Option comparison", "Rama", "plan", "Compare routes and stays using consistent criteria and total cost.", skill="project_plan"),
            _stage("itinerary", "Itinerary and budget", "Krishna", "artifact", "Produce a coherent day-by-day plan with budget and alternatives.", skill="content_create"),
            _stage("booking", "Booking desk", "Matsya", "action", "Recheck availability and preview each reservation before commitment.", skill="form_submit", tools=["computer_use"], confirmation_action="booking_commitment"),
            _stage("trip_ready", "Trip-ready pack", "Krishna", "artifact", "Prepare confirmations, checklist, maps, and critical reminders.", skill="content_create"),
            _stage("review", "Change review", "Rama", "review", "Re-plan only the portions affected by a price, weather, or availability change.", skill="project_plan"),
        ],
        "schedule_templates": [
            {"id": "price_watch", "title": "Travel price and availability check", "enabled_by": "price_watch", "cadence": "daily", "time_field": "nudge_time", "target_stage": "research"},
        ],
        "feedback_routes": {"price_changed": "compare", "availability_changed": "itinerary", "dates_changed": "intake", "booking_complete": "trip_ready"},
    },
    "teach": {
        "id": "teach",
        "version": 1,
        "title": "Teach Anything",
        "eyebrow": "One concept, one check, durable mastery",
        "description": "A paced learning path backed by Gurukul workspaces and spaced review.",
        "accent": "#1d4ed8",
        "owner": "Krishna",
        "required_capabilities": ["learning"],
        "optional_capabilities": ["search", "tts"],
        "intake": [
            _field("topic", "Topic", required=True, placeholder="Transformer attention"),
            _field("outcome", "Learning outcome", required=True, placeholder="Explain it confidently in an interview"),
            _field("current_level", "Current level", required=True, options=["New", "Some familiarity", "Working knowledge", "Advanced"], kind="select", default="New"),
            _field("minutes_per_session", "Minutes per session", kind="number", default=20),
            _field("mode", "Learning mode", kind="select", options=["Hybrid", "First principles", "Q&A"], default="Hybrid"),
            _field("spaced_reviews", "Spaced reviews", kind="boolean", default=True),
            _field("nudge_time", "Review time", kind="time", default="09:00"),
            _field("timezone", "Timezone", default="Asia/Kolkata"),
        ],
        "stages": [
            _stage("intake", "Learning mission", "Krishna", "intake", "Define the outcome, starting point, pace, and evidence of mastery.", skill="teach"),
            _stage("diagnostic", "Diagnostic", "Krishna", "teach", "Ask the smallest useful question to locate the learner's current model.", skill="teach"),
            _stage("lesson", "Paced lesson", "Krishna", "teach", "Teach one frontier concept with an analogy and one concrete example.", skill="teach"),
            _stage("check", "Understanding check", "Krishna", "teach", "Use one question to test transfer rather than recognition.", skill="teach"),
            _stage("reinforce", "Targeted reinforcement", "Krishna", "teach", "Correct the named misconception with a different representation.", skill="teach"),
            _stage("review", "Mastery review", "Krishna", "review", "Record mastery, schedule the next review, and choose the next atom.", skill="teach", recurring=True),
        ],
        "schedule_templates": [
            {"id": "spaced_reviews", "title": "Spaced learning review", "enabled_by": "spaced_reviews", "cadence": "weekly", "weekdays": [1, 4], "time_field": "nudge_time", "target_stage": "review"},
        ],
        "feedback_routes": {"incorrect": "reinforce", "shaky": "reinforce", "mastered": "review", "new_goal": "intake"},
    },
    "finance": {
        "id": "finance",
        "version": 1,
        "title": "Personal Finance",
        "eyebrow": "See clearly, model choices, stay in control",
        "description": "Local ledger intelligence, grounded scenarios, and recurring financial reviews.",
        "accent": "#7c3f14",
        "owner": "Rama",
        "required_capabilities": ["finance", "planning"],
        "optional_capabilities": ["search", "documents"],
        "intake": [
            _field("currency", "Currency", required=True, default="INR"),
            _field("goal", "Primary financial goal", required=True, placeholder="Build a 9-month emergency fund"),
            _field("monthly_context", "Monthly cash-flow context", kind="textarea", required=True, placeholder="Approximate income, fixed costs, variable costs"),
            _field("debts_and_commitments", "Debts and commitments", kind="textarea", placeholder="Loan balances, rates, recurring obligations"),
            _field("risk_comfort", "Risk comfort", kind="select", options=["Low", "Moderate", "High"], default="Moderate"),
            _field("statement_path", "Statement CSV path", placeholder="/Users/me/Downloads/statement.csv"),
            _field("monthly_review", "Monthly review", kind="boolean", default=True),
            _field("nudge_time", "Review time", kind="time", default="18:30"),
            _field("timezone", "Timezone", default="Asia/Kolkata"),
        ],
        "stages": [
            _stage("intake", "Financial baseline", "Rama", "intake", "Define goals, cash-flow assumptions, obligations, and risk boundaries.", skill="budget_plan"),
            _stage("ingest", "Local data intake", "Rama", "track", "Import or update transactions and balances without exposing credentials.", skill="finance_import", tools=["import_csv", "get_financial_context"]),
            _stage("analyze", "Cash-flow intelligence", "Rama", "analysis", "Measure spending, recurring costs, anomalies, and goal trajectory.", skill="spending_review"),
            _stage("scenarios", "Current intelligence and scenarios", "Matsya", "research", "Ground rates, rules, and market assumptions in dated sources.", skill="research", tools=["exa_search", "exa_contents"]),
            _stage("plan", "Decision plan", "Rama", "plan", "Compare choices, tradeoffs, buffers, and reversible next steps.", skill="financial_decision"),
            _stage("review", "Monthly review", "Rama", "review", "Compare plan with actuals and adapt budgets or goal timing.", skill="spending_review", recurring=True),
        ],
        "schedule_templates": [
            {"id": "monthly_review", "title": "Monthly finance review", "enabled_by": "monthly_review", "cadence": "monthly", "day": 1, "time_field": "nudge_time", "target_stage": "review"},
        ],
        "feedback_routes": {"over_budget": "analyze", "income_changed": "intake", "goal_changed": "intake", "month_closed": "review"},
    },
    "documents": {
        "id": "documents",
        "version": 1,
        "title": "Documents and Insights",
        "eyebrow": "Evidence into a story people can use",
        "description": "Analyze source material and produce presentations, insight reports, or polished narratives.",
        "accent": "#9f1239",
        "owner": "Krishna",
        "required_capabilities": ["planning"],
        "optional_capabilities": ["documents", "presentation", "sql", "filesystem"],
        "intake": [
            _field("deliverable", "Deliverable", kind="select", required=True, options=["Presentation", "Data insights", "Narrative", "Creative writing"], default="Presentation"),
            _field("objective", "Objective", kind="textarea", required=True, placeholder="What should the audience understand or decide?"),
            _field("audience", "Audience", required=True, placeholder="Board, customers, classroom, general readers"),
            _field("source_paths", "Source files", kind="textarea", placeholder="One local path per line"),
            _field("tone", "Tone", placeholder="Editorial, decisive, warm"),
            _field("constraints", "Format constraints", kind="textarea", placeholder="10 slides, 16:9, speaker notes"),
            _field("recurring_report", "Recurring report", kind="boolean", default=False),
            _field("nudge_time", "Report time", kind="time", default="09:30"),
            _field("timezone", "Timezone", default="Asia/Kolkata"),
        ],
        "stages": [
            _stage("intake", "Creative brief", "Krishna", "intake", "Lock the audience, decision, source material, format, and constraints.", skill="content_create"),
            _stage("ingest", "Source ingestion", "Matsya", "analysis", "Extract files and establish an exact evidence inventory.", skill="document_review", tools=["extract_document"]),
            _stage("analyze", "Analysis and insights", "Parashurama", "analysis", "Read structured sources and find defensible patterns, caveats, and decision-relevant insights.", skill="data_pipeline", tools=["read_file", "query_database"]),
            _stage("story", "Narrative architecture", "Krishna", "artifact", "Turn evidence into one coherent argument or creative arc.", skill="content_create"),
            _stage("create", "Artifact production", "Krishna", "artifact", "Create the requested deck, report, chart set, or narrative.", skill="presentation_create", tools=["create_document", "create_webpage"]),
            _stage("review", "Design and truth audit", "Krishna", "review", "Audit clarity, evidence, pacing, visual hierarchy, and unsupported claims.", skill="presentation_create"),
            _stage("export", "Final export", "Parashurama", "artifact", "Version and expose the final artifact with source and provenance links.", skill="data_pipeline"),
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
