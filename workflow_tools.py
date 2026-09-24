"""The avatars' path tools: report a stage result, and keep the application tracker.

Both act only inside a chat turn the server bound to a workflow run
(``workflow_evidence.bind_turn``), for that run's own profile. A stage result is
a claim; the engine completes the stage only when its done_when holds on
evidence it verifies itself.
"""

from __future__ import annotations

from typing import Any

import workflow_evidence
from profile_context import current_profile_id
from tool_result import envelope


def report_stage_result(
    status: str,
    summary: str,
    fields: dict[str, Any] | None = None,
    questions: list[str] | None = None,
    evidence_ids: list[str] | None = None,
    reason: str = "",
) -> dict[str, Any]:
    """Report where the current path stage stands. Use it only in turns that start
    with [NARAD WORKFLOW CONTEXT]; it tells you whether the stage is now done.

    The stage finishes only when its finish line (listed in the context) is met
    by evidence Narad checks itself: tool results from this chat, a confirmed
    document review, a finished task, an approval, a file you created. Saying a
    step is done never finishes it.

    Args:
        status: "done" when the stage's work is finished; "needs_input" when you
            need an answer from the person; "blocked" when something outside your
            control stops you; "in_progress" for partial progress.
        summary: One or two plain sentences on what was done or what is missing.
            On a step that needs approval, this is the exact preview the person
            approves.
        fields: The result fields the context asks for, with real content, e.g.
            {"roles": [...]} or {"plan": "..."}. In the intake stage: the answers
            you learned, keyed by the intake field names in the context.
        questions: With "needs_input": at most two short questions.
        evidence_ids: Ids from this conversation that show the work: a review id
            (rev_...), a task id (tsk_...), an approval id (apr_...) or a file URL.
        reason: With "blocked": why, in one sentence.

    Returns:
        status "completed" (the stage is done; the next stage is named),
        "not_done" (what is still missing), "approval_requested", or "recorded".
        Tell the person the outcome in plain words; never claim a step is done
        unless the status is "completed".
    """
    turn = workflow_evidence.current_turn()
    if turn is None:
        return envelope(
            status="error",
            summary="This chat is not inside a path stage, so there is nothing to report; answer normally.",
            error="no_bound_path",
        )
    import workflow_engine

    try:
        verdict = workflow_engine.submit_stage_result(
            turn.run_id,
            user_id=turn.user_id,
            session_id=turn.session_id,
            status=status,
            summary=summary,
            fields=fields,
            questions=questions,
            reason=reason,
            evidence_ids=evidence_ids,
            receipts=list(turn.receipts),
        )
    except (KeyError, ValueError, PermissionError) as exc:
        return envelope(status="error", summary=str(exc).strip("'"), error="stage_result_refused")
    turn.reported = True
    return envelope(
        status=verdict["status"],
        summary=verdict["summary"],
        stage=verdict.get("stage"),
        next_stage=verdict.get("next_stage"),
        missing=verdict.get("missing", []),
        run_status=verdict.get("run_status"),
    )


def track_application(
    company: str,
    role: str,
    status: str = "shortlisted",
    link: str = "",
    next_step: str = "",
    date: str = "",
    notes: str = "",
) -> dict[str, Any]:
    """Add or update one role in the person's Career path application tracker.

    Use for every role on a shortlist and whenever an application changes
    (applied, interview, offer, rejected). The tracker is shown on the Paths
    screen; a later Gmail check (read-only, if connected) looks for replies from
    the companies marked applied.

    Args:
        company: Employer name as the person would recognise it.
        role: Job title.
        status: shortlisted | applied | interview | offer | rejected | withdrawn | no_response.
        link: The job posting URL (https://...), if known.
        next_step: The next action in a few words, e.g. "follow up on 3 Oct".
        date: When the status last changed (YYYY-MM-DD), if known.
        notes: One short line of context.

    Returns:
        The stored tracker row (record_id, company, role, status, ...).
    """
    import workflow_engine

    turn = workflow_evidence.current_turn()
    # The server bound the turn with the caller's own profile; outside a bound
    # turn the request's profile scope decides.
    profile_id = turn.user_id if turn is not None else current_profile_id()
    run_id = ""
    if turn is not None:
        bound = workflow_engine.get_workflow_run(turn.run_id)
        if bound and bound.workflow_id == "career":
            run_id = bound.run_id
    if not run_id:
        open_runs = [
            run for run in workflow_engine.list_workflow_runs(user_id=profile_id, workflow_id="career", limit=10)
            if run.status not in {"completed", "cancelled"}
        ]
        run_id = open_runs[0].run_id if open_runs else ""
    if not run_id:
        return envelope(status="error", summary="Start the Career path first; the tracker belongs to it.",
                        error="no_career_path")
    try:
        record = workflow_engine.upsert_application(
            run_id, user_id=profile_id, company=company, role=role, status=status,
            link=link, next_step=next_step, date=date, notes=notes,
        )
    except (KeyError, ValueError, PermissionError) as exc:
        return envelope(status="error", summary=str(exc).strip("'"), error="tracker_refused")
    return envelope(
        status="ok",
        summary=f"Tracker: {record['company']}, {record['role']} is {record['status'].replace('_', ' ')}.",
        record_id=record["record_id"],
        application=record,
    )
