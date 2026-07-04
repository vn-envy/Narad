"""
Phase-gated skill schemas for all Narad agents.

Each skill is a list of phase names executed in strict order.
Skipping or collapsing phases is never acceptable.

Agent → skill mapping:
  Parashurama  bug→diagnose, feature→tdd, scaffold→scaffold, refactor→refactor,
               prototype→prototype, review→review, migrate→migrate, ui→ui,
               security_audit→security_audit, data_pipeline→data_pipeline,
               perf→perf_audit, financial_model→financial_model
  Matsya       web_research→web_research, form_submit→form_submit,
               document_review→document_review, analysis→analysis,
               research→research, cleanup→file_cleanup
  Rama         project→project_plan, budget→budget_plan, calendar→schedule_event,
               import→finance_import, spending→spending_review,
               health_log→health_log, wellness→wellness_plan,
               financial_decision→financial_decision
  Krishna      email→email_send, teach→teach, content→content_create,
               presentation→presentation_create, video→video_create,
               health_guidance→health_guidance, symptom_check→symptom_check,
               mental_health→mental_health_check
"""

from __future__ import annotations

SKILLS: dict[str, list[str]] = {
    # ── Parashurama (code + craft) ────────────────────────────────────────────
    "tdd": [
        "plan",          # Understand the feature; design the interface + test cases on paper
        "tracer_bullet", # Minimal end-to-end working path, production-quality
        "red",           # Write failing tests (no implementation yet)
        "green",         # Minimum code to make tests pass
        "refactor",      # Clean up without breaking tests
    ],
    "diagnose": [
        "reproduce",     # Confirm the bug exists; produce minimal reproduction
        "hypothesize",   # List ≥2 root cause hypotheses ranked by probability
        "instrument",    # Add logging/assertions to prove/disprove top hypothesis
        "fix",           # Implement the fix
        "verify",        # Tests pass; original repro case no longer triggers
    ],
    "scaffold": [
        "spec",          # Define: language, runtime, deps, directory structure, entry point
        "manifest",      # Create package.json / pyproject.toml / Cargo.toml / go.mod
        "skeleton",      # Create empty modules with correct imports and exports
        "test_skeleton", # Stub test file with one passing smoke test
        "wire",          # Connect entry point → modules → smoke test passes
    ],
    "refactor": [
        "audit",         # List every change to be made; no code yet
        "isolate",       # Extract/move each piece one at a time; tests pass after each step
        "rename",        # Final naming pass (variables, functions, modules)
        "verify",        # Full test suite passes; no behavioural change
    ],
    "prototype": [
        "spike",         # Fastest path to something that runs — skip structure, skip tests
        "demo",          # Confirm it works; document what's missing for production
    ],
    "review": [
        "map",           # Generate repo map; identify scope of review
        "findings",      # List every issue with severity: critical / major / minor / nit
        "recommend",     # Prioritised fix list; highest severity first
    ],
    "migrate": [
        "inventory",     # List every construct being migrated (APIs, patterns, deps)
        "mapping",       # Explicit old → new translation table for each construct
        "migrate",       # Apply translations file by file
        "verify",        # Tests pass; behaviour matches original
    ],
    "ui": [
        "classify",         # Determine output type, tone, audience — ask one clarifying Q if ambiguous
        "select_template",  # Present 3 template / shadcn block options — wait for user selection
        "apply_tokens",     # Emit token summary table (colors, type, shape, elevation, spacing)
        "design_audit",     # Check for slop, hierarchy drift, weak visual contrast, or generic layout
        "design_redesign",  # Tighten the layout using explicit anti-slop design heuristics
        "add_interactions", # State layers, motion rules, breakpoints
        "deliver",          # Output complete file(s) + token map + "how to extend" notes
    ],
    "security_audit": [
        "enumerate_surfaces", # List input vectors, auth boundaries, data flows, trust boundaries
        "test_cases",         # Specific payloads/scenarios to test for each surface
        "classify",           # Severity (critical/high/medium/low) + CVSS reasoning per finding
        "remediate",          # Code fixes for each finding; highest severity first
        "verify",             # Confirm fixes don't introduce new issues; tests for each fix
    ],
    "data_pipeline": [
        "schema",    # Define input + output schema explicitly; types + constraints
        "extract",   # write_script for data reading/fetching; test on sample row
        "transform", # write_script for transformation logic; handle nulls, type coercions
        "validate",  # run_shell on full dataset; report row counts, null rates, anomalies
        "load",      # write_script for output writing; confirm destination
    ],
    "perf_audit": [
        "baseline",     # Current measured performance — ask if not provided
        "profile",      # Where does time/memory go? Code paths, queries, I/O
        "bottlenecks",  # Name ≤3 specific slow paths with estimated impact
        "optimize",     # Targeted changes for each bottleneck; no speculative rewrites
        "verify",       # Measure improvement; report before/after delta
    ],
    "financial_model": [
        "extract_inputs", # Gather raw numbers from user text, Matsya output, or repo data
        "validate",       # Check units, completeness, reasonableness; flag issues
        "model",          # write_script with code execution; run_shell to compute
        "interpret",      # Plain-English findings: what the numbers show, what matters
        "disclaimer",     # Append mandatory finance disclaimer before returning
    ],

    # ── Matsya (retrieval + web) ──────────────────────────────────────────────
    "web_research": [
        "formulate", # Define search queries, sub-questions, scope
        "search",    # web_search + browse_url across ≥2 distinct sources
        "verify",    # Cross-check key claims; flag contradictions
        "synthesize", # Structured answer with inline source URLs
    ],
    "form_submit": [
        "screenshot",  # browser_screenshot; list every visible field
        "map_fields",  # Propose value per field; browser_fill(dry_run=True)
        "confirm",     # Show preview to user; STOP and wait for explicit approval
        "submit",      # browser_fill(dry_run=False) or browser_upload_and_submit
    ],
    "document_review": [
        "extract",   # extract_document(file_path); confirm page/section count
        "structure", # Identify sections, tables, key entities, dates, figures
        "findings",  # 3–7 most important facts/claims with source locations
        "gaps",      # What is missing, ambiguous, or contradicted within the doc
        "synthesis", # Direct answer to user's specific question with evidence refs
    ],
    "analysis": [
        "steelman",    # State the strongest version of the argument/plan
        "assumptions", # List every assumption; rate each solid/shaky/untested
        "weaknesses",  # Specific logical gaps, missing evidence, quantified risks
        "verdict",     # sound / needs_revision / fundamentally_flawed — with reasoning
        "conditions",  # What specific evidence would change the verdict
    ],
    "research": [
        "frame",       # Define core question, sub-questions, scope boundaries
        "search",      # Collect structured sources via Matsya's research tools
        "triangulate", # Cross-check claims; identify consensus vs outlier vs contested
        "gaps",        # Name what the sources do NOT answer; rate each gap
        "synthesise",  # Final answer with evidence citations + mandatory gap disclosure
    ],
    "file_cleanup": [
        "scan",       # scan_directory + find_large_files + get_disk_info
        "categorize", # Group files by type, age (>90d), size (>100MB), duplicates
        "preview",    # organize_by_type/move_to_trash dry_run=True; list every file
        "confirm",    # STOP; "N files to move, M MB to free — proceed?"
        "execute",    # Run with dry_run=False only after confirmation
        "report",     # What was done, space freed, where files went
    ],

    # ── Rama (planning + calendar + budget) ──────────────────────────────────
    "project_plan": [
        "scope",      # Define deliverables, constraints, team, success criteria
        "milestones", # 3–7 top-level checkpoints with acceptance criteria
        "tasks",      # Decompose each milestone into tasks with owner/duration
        "schedule",   # get_upcoming_events(); assign dates; flag conflicts
        "export",     # Final plan in user's preferred format
    ],
    "budget_plan": [
        "assess",    # get_financial_context() + get_spending() + get_recurring_expenses()
        "goals",     # Define savings/spending targets explicitly
        "allocate",  # Distribute income across categories; highlight tradeoffs
        "timeline",  # Month-by-month milestones to reach goals
        "export",    # Table: category, current spend, target spend, delta
    ],
    "schedule_event": [
        "understand",       # Clarify event title, date/time, duration, attendees, location
        "check_conflicts",  # get_upcoming_events(days_ahead=30); report conflicts
        "propose",          # create_event(dry_run=True); show full preview
        "confirm",          # STOP; wait for explicit user approval
        "create",           # create_event(dry_run=False) only after confirmation
    ],
    "finance_import": [
        "import",     # import_csv(file_path) or sync_gmail_finance()
        "review",     # Show imported rows, top merchants, auto-categories; flag uncertain
        "reconcile",  # Let user correct miscategorizations before proceeding
        "baseline",   # get_spending("last_3_months") + get_budget_status()
        "goals",      # Offer add_goal()/set_budget(); ask about targets
    ],
    "spending_review": [
        "extract",          # get_spending(period) by category + get_recurring_expenses()
        "categorize",       # Fixed vs variable, essential vs discretionary
        "patterns",         # Month-over-month changes, top 3 categories, anomalies
        "recommendations",  # 2–3 specific actions ranked by impact
    ],
    "health_log": [
        "capture",  # Determine log/reminder/history action and gather required fields
        "confirm",  # Preview write operations; history queries can move straight through
        "store",    # log_symptom / set_medication_reminder / get_health_log / query_rxnorm
        "summary",  # Confirm what was written or summarize history data
    ],
    "wellness_plan": [
        "assess",   # Current activity, sleep, nutrition, constraints, real schedule
        "goals",    # Define measurable targets and flag conflicts/medical caveats
        "plan",     # Weekly exercise/nutrition/sleep structure
        "schedule", # Map the plan onto the user's calendar with dry-run previews
        "monitor",  # Check-ins, adjustment triggers, 4-week milestone
    ],
    "financial_decision": [
        "data",      # Pull current financial state from real account and spending data
        "steelman",  # State the strongest case for the decision
        "scenarios", # Bear/base/bull grounded in the user's actual baseline
        "verdict",   # Clear recommendation with conditions and mandatory disclaimer
    ],

    # ── Krishna (communication + education) ──────────────────────────────────
    "email_send": [
        "draft",   # Write full email: subject + body; audience-appropriate tone
        "review",  # Check tone, length, clarity, call-to-action
        "preview", # compose_email(to, subject, body) — structured preview, no send
        "confirm", # STOP; present to user: "Here's the draft — shall I send it?"
        "send",    # send_email(dry_run=False) ONLY after explicit user confirmation
    ],
    "teach": [
        "frame",     # State concept, prerequisites, learning outcome
        "explain",   # Core explanation with 1–2 analogies; no undefined jargon
        "examples",  # 2–3 concrete examples varying complexity (simple → complex)
        "check",     # Ask one targeted question to verify understanding; wait for answer
        "reinforce", # Address gaps; one-sentence summary; optional artifact offer
    ],
    "content_create": [
        "brief",   # Define purpose, audience, key message, tone, target length
        "outline", # Section headers + one-line description per section
        "draft",   # Full content; follow outline strictly
        "polish",  # Tighten language; check opening hook and closing CTA
        "deliver", # Final version with formatting notes
    ],
    "presentation_create": [
        "brief",     # Purpose, audience, slide count, tone, HTML/PDF export note
        "outline",   # Slide narrative arc
        "structure", # Slide-by-slide content and layout table
        "design_audit", # Check visual hierarchy and reject generic/sloppy deck structure
        "build",     # rank_ui_templates() then create_webpage(code)
    ],
    "video_create": [
        "brief",  # Topic, duration, scene count, style, platform
        "script", # Scene-by-scene content plan
        "design_redesign", # Refine the visual direction before rendering
        "build",  # Veo → moviepy fallback cascade
    ],
    "dogfood_ui": [
        "scope",       # Define the UI or flow to inspect
        "exercise",    # Drive the product through key user journeys
        "capture",     # Save screenshots, traces, and visible regressions
        "report",      # Summarize the UX failures and likely root causes
    ],
    "kanban_orchestrator": [
        "intake",      # Read the incoming goal or project state
        "structure",   # Create or reshape board columns and milestones
        "assign",      # Map tasks to avatars or owners
        "monitor",     # Update blocked/done state from live execution signals
    ],
    "kanban_worker": [
        "claim",       # Pick the next eligible card
        "execute",     # Perform the assigned work slice
        "handoff",     # Record outputs and update board status
    ],
    "native_mcp": [
        "inventory",   # Detect the server/tool surface and schemas
        "connect",     # Validate auth/transport assumptions
        "exercise",    # Run a minimal safe tool call
        "report",      # Summarize capability and integration risks
    ],
    "youtube_content": [
        "discover",    # Find candidate videos/channels for the topic
        "extract",     # Pull transcript highlights and notable comments
        "rank",        # Score relevance and novelty
        "synthesize",  # Convert creator signal into grounded findings
    ],
    "health_guidance": [
        "context",         # Understand the user's wellness context and constraints
        "evidence",        # Find credible support for recommendations
        "recommendations", # Specific, actionable guidance
        "disclaimer",      # General wellness, not medical advice
    ],
    "mental_health_check": [
        "screen",            # PHQ-4 intake
        "support",           # Score-based coping guidance or crisis response
        "resources",         # Appropriate follow-up resources
        "professional_gate", # Strong recommendation based on severity
    ],
    "symptom_check": [
        "collect",         # Structured symptom interview
        "red_flag_check",  # Emergency gate before any assessment
        "assessment",      # Associated-with framing only
        "triage",          # Appropriate level of care
        "disclaimer",      # Not a diagnosis
    ],
}


def get_skill_for_task_type(task_type: str) -> list[str] | None:
    """Return the phase list for a given TASK_TYPE label, or None if not found."""
    mapping = {
        # Parashurama
        "bug":            "diagnose",
        "feature":        "tdd",
        "scaffold":       "scaffold",
        "refactor":       "refactor",
        "prototype":      "prototype",
        "review":         "review",
        "migrate":        "migrate",
        "ui":             "ui",
        "security_audit": "security_audit",
        "data_pipeline":  "data_pipeline",
        "perf_audit":     "perf_audit",
        "financial_model": "financial_model",
        # Matsya
        "web_research":   "web_research",
        "form_submit":    "form_submit",
        "document_review": "document_review",
        "analysis":       "analysis",
        "research":       "research",
        "file_cleanup":   "file_cleanup",
        # Rama
        "project_plan":   "project_plan",
        "budget_plan":    "budget_plan",
        "schedule_event": "schedule_event",
        "finance_import": "finance_import",
        "spending_review": "spending_review",
        "health_log":     "health_log",
        "wellness_plan":  "wellness_plan",
        "financial_decision": "financial_decision",
        # Krishna
        "email_send":     "email_send",
        "teach":          "teach",
        "content_create": "content_create",
        "presentation_create": "presentation_create",
        "video_create":   "video_create",
        "health_guidance": "health_guidance",
        "symptom_check":  "symptom_check",
        "mental_health_check": "mental_health_check",
    }
    skill_name = mapping.get(task_type.lower())
    return SKILLS.get(skill_name) if skill_name else None


def build_skill_prompt_block() -> str:
    """Render all skill schemas as a [SKILLS] block for injection into agent prompts."""
    lines = ["[SKILLS]\n"]
    for skill_name, phases in SKILLS.items():
        lines.append(f"  {skill_name}: {' → '.join(phases)}")
    lines.append(
        "\nPhase rules:\n"
        "- Execute phases in order. Skipping is not allowed.\n"
        "- Each phase ends with CURRENT_PHASE: <next_phase_name>.\n"
        "- Final phase ends with DONE.\n"
        "- If the user interrupts mid-skill, acknowledge the interrupt,\n"
        "  complete the current phase, then re-orient to the new request.\n"
    )
    return "\n".join(lines)
