"""
Phase-gated skill schemas for all Narad agents.

Each skill is a list of phase names executed in strict order.
Skipping or collapsing phases is never acceptable.

Agent → skill mapping:
  Parashurama  bug→diagnose, feature→tdd, scaffold→scaffold, refactor→refactor,
               prototype→prototype, review→review, migrate→migrate, ui→ui, pptx→pptx,
               security_audit→security_audit, data_pipeline→data_pipeline
  Buddha       research→research, analysis→analysis
  Matsya       web_research→web_research, form_submit→form_submit
  Varaha       document_review→document_review, financial_analysis→financial_analysis
  Narasimha    bug/error→narasimha_diagnose, perf→perf_audit
  Rama         project→project_plan, budget→budget_plan, calendar→schedule_event
  Krishna      email→email_send, teach→teach, content→content_create
  Vamana       cleanup→file_cleanup, import→finance_import, spending→spending_review
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
        "add_interactions", # State layers, motion rules, breakpoints
        "deliver",          # Output complete file(s) + token map + "how to extend" notes
    ],
    "pptx": [
        "outline",    # Title, audience, purpose, slide count — confirm before proceeding
        "structure",  # Slide-by-slide table: title, key points, layout, speaker notes
        "design",     # Design token table: colours, fonts, mood
        "build",      # create_document(format="pptx") with all tokens applied
        "verify",     # Confirm file exists, report path + slide count + size
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

    # ── Buddha (analysis + research) ─────────────────────────────────────────
    "research": [
        "frame",       # Define core question, sub-questions, scope boundaries
        "gather",      # Collect structured sources via Matsya's research tools
        "triangulate", # Cross-check claims; identify consensus vs outlier vs contested
        "gaps",        # Name what the sources do NOT answer; rate each gap
        "synthesise",  # Final answer with evidence citations + mandatory gap disclosure
    ],
    "analysis": [
        "steelman",    # State the strongest version of the argument/plan
        "assumptions", # List every assumption; rate each solid/shaky/untested
        "weaknesses",  # Specific logical gaps, missing evidence, quantified risks
        "verdict",     # sound / needs_revision / fundamentally_flawed — with reasoning
        "conditions",  # What specific evidence would change the verdict
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

    # ── Varaha (documents + quant finance) ───────────────────────────────────
    "document_review": [
        "extract",   # extract_document(file_path); confirm page/section count
        "structure", # Identify sections, tables, key entities, dates, figures
        "findings",  # 3–7 most important facts/claims with source locations
        "gaps",      # What is missing, ambiguous, or contradicted within the doc
        "synthesis", # Direct answer to user's specific question with evidence refs
    ],
    "financial_analysis": [
        "extract_inputs", # Gather raw numbers from document or user text
        "validate",       # Check units, completeness, reasonableness; flag issues
        "model",          # write_script with pandas/numpy; run_shell to execute
        "interpret",      # Plain-English findings: what the numbers show, what matters
        "disclaimer",     # Append mandatory finance disclaimer before returning
    ],

    # ── Narasimha (debugging + performance) ──────────────────────────────────
    "narasimha_diagnose": [
        "symptoms",   # Restate exactly what is observed: error text, behaviour, context
        "hypothesize", # List ≥2 root cause candidates ranked by likelihood
        "root_cause", # Name the most probable cause with evidence; explicit declaration
        "fix",        # Copy-paste-ready fix steps; ONLY after root_cause is declared
        "verify",     # Confirm fix resolves original symptom; how to test
    ],
    "perf_audit": [
        "baseline",     # Current measured performance — ask if not provided
        "profile",      # Where does time/memory go? Code paths, queries, I/O
        "bottlenecks",  # Name ≤3 specific slow paths with estimated impact
        "optimize",     # Targeted changes for each bottleneck; no speculative rewrites
        "verify",       # Measure improvement; report before/after delta
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

    # ── Vamana (filesystem + personal finance) ───────────────────────────────
    "file_cleanup": [
        "scan",       # scan_directory + find_large_files + get_disk_info
        "categorize", # Group files by type, age (>90d), size (>100MB), duplicates
        "preview",    # organize_by_type/move_to_trash dry_run=True; list every file
        "confirm",    # STOP; "N files to move, M MB to free — proceed?"
        "execute",    # Run with dry_run=False only after confirmation
        "report",     # What was done, space freed, where files went
    ],
    "finance_import": [
        "import",     # import_csv(file_path) or sync_gmail_finance()
        "review",     # Show N transactions, top 5 merchants, auto-categories; flag uncertain
        "reconcile",  # Let user correct miscategorizations before proceeding
        "baseline",   # get_spending("last_3_months") + get_budget_status()
        "goals",      # Offer add_goal(); ask about budget/savings targets
    ],
    "spending_review": [
        "extract",          # get_spending(period) by category + get_recurring_expenses()
        "categorize",       # Fixed vs variable, essential vs discretionary
        "patterns",         # Month-over-month changes, top 3 categories, anomalies
        "insights",         # Compare to get_budget_status(); gap vs budget
        "recommendations",  # 2–3 specific actions ranked by impact
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
        "pptx":           "pptx",
        "security_audit": "security_audit",
        "data_pipeline":  "data_pipeline",
        # Buddha
        "research":       "research",
        "analysis":       "analysis",
        # Matsya
        "web_research":   "web_research",
        "form_submit":    "form_submit",
        # Varaha
        "document_review":    "document_review",
        "financial_analysis": "financial_analysis",
        # Narasimha
        "narasimha_diagnose": "narasimha_diagnose",
        "perf_audit":         "perf_audit",
        # Rama
        "project_plan":   "project_plan",
        "budget_plan":    "budget_plan",
        "schedule_event": "schedule_event",
        # Krishna
        "email_send":     "email_send",
        "teach":          "teach",
        "content_create": "content_create",
        # Vamana
        "file_cleanup":    "file_cleanup",
        "finance_import":  "finance_import",
        "spending_review": "spending_review",
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
