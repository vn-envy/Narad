from __future__ import annotations

import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

_root = next(path for path in Path(__file__).resolve().parents if (path / "narad_paths.py").exists())
sys.path[:0] = [str(_root)]
import kala_scheduler
import narad_paths  # noqa: F401
import workflow_engine


@pytest.fixture(autouse=True)
def isolated_workflows(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(workflow_engine, "WORKFLOW_DB", tmp_path / "workflows.db")
    monkeypatch.setattr(
        workflow_engine,
        "_capability_flags",
        lambda: {
            "planning": True,
            "learning": True,
            "health": True,
            "finance": True,
            "documents": True,
            "presentation": True,
            "filesystem": True,
            "sql": True,
            "tts": True,
            "search": True,
            "computer": True,
            "calendar": True,
            "email": True,
        },
    )


def _career_inputs() -> dict:
    return {
        "target_role": "Senior product manager",
        "locations": "Remote India",
        "experience": "Eight years in B2B SaaS and analytics",
        "weekly_scan": True,
        "timezone": "Asia/Kolkata",
    }


def test_six_declarative_paths_are_available_and_exa_backed() -> None:
    definitions = workflow_engine.list_workflow_definitions()
    assert [item["id"] for item in definitions] == [
        "career",
        "health",
        "travel",
        "teach",
        "finance",
        "documents",
    ]
    research_tools = {
        tool
        for definition in definitions
        for stage in definition["stages"]
        if stage["kind"] == "research"
        for tool in stage["tools"]
    }
    assert "exa_search" in research_tools
    assert "exa_contents" in research_tools
    assert all("tinyfish" not in tool.lower() for tool in research_tools)


def test_run_persists_stages_and_recurring_schedule() -> None:
    run = workflow_engine.start_workflow_run("career", user_id="asha", inputs=_career_inputs())
    payload = workflow_engine.workflow_run_payload(run)

    assert run.current_stage_id == "market_scan"
    assert run.project_id is None
    assert payload["progress_percent"] == 12
    assert len(payload["tasks"]) == 8
    tasks_by_stage = {item["workflow_stage_id"]: item for item in payload["tasks"]}
    assert tasks_by_stage["intake"]["status"] == "done"
    assert tasks_by_stage["market_scan"]["status"] == "active"
    assert len(payload["schedules"]) == 1
    assert payload["schedules"][0]["payload"]["target_stage"] == "market_scan"

    reloaded = workflow_engine.get_workflow_run(run.run_id)
    assert reloaded is not None
    assert reloaded.inputs["target_role"] == "Senior product manager"
    assert len(workflow_engine.list_workflow_events(run.run_id)) >= 3


def test_external_action_requires_preview_and_approval() -> None:
    run = workflow_engine.start_workflow_run("career", user_id="asha", inputs=_career_inputs())
    for stage in ("market_scan", "shortlist", "tailor"):
        assert run.current_stage_id == stage
        run = workflow_engine.complete_current_stage(run.run_id, summary=f"Completed {stage}")
    assert run.current_stage_id == "apply"

    with pytest.raises(PermissionError):
        workflow_engine.complete_current_stage(run.run_id, summary="Submitted")

    run = workflow_engine.request_stage_confirmation(
        run.run_id,
        summary="Submit application to Example Co",
        details={"url": "https://example.com/jobs/42", "fields": ["name", "resume"]},
    )
    assert run.status == "waiting_confirmation"
    assert run.state["confirmation"]["status"] == "pending"
    # The stage approval is an Anumati proposal: nothing else can approve it.
    proposal_id = run.state["confirmation"]["proposal_id"]
    with pytest.raises(PermissionError):
        workflow_engine.approve_stage(run.run_id, approved_by="asha")

    with patch("dharma.gate_action", return_value=SimpleNamespace(allowed=True, reasons=[])):
        run = workflow_engine.approve_pending_stage(run.run_id, approved_by="asha")
    assert run.status == "active"
    assert run.state["confirmation"]["status"] == "approved"
    import anumati

    assert anumati.get(proposal_id, profile_id="asha").status == "executed"

    run = workflow_engine.complete_current_stage(run.run_id, summary="Application submitted after approval")
    assert run.current_stage_id == "track"


def test_feedback_reopens_the_declared_stage_and_cycle() -> None:
    run = workflow_engine.start_workflow_run("career", user_id="asha", inputs=_career_inputs())
    run = workflow_engine.complete_current_stage(run.run_id, summary="Market scan complete")
    assert run.current_stage_id == "shortlist"

    run = workflow_engine.record_workflow_feedback(
        run.run_id,
        "rejected",
        details={"company": "Synthetic Labs", "reason": "role scope"},
    )
    assert run.current_stage_id == "market_scan"
    assert run.state["cycle"] == 2
    assert "market_scan" not in run.state["completed_stage_ids"]
    assert run.state["feedback"][-1]["routed_to"] == "market_scan"


def _make_due(run_id: str, template_id: str, due: datetime) -> workflow_engine.WorkflowSchedule:
    schedule = next(
        item for item in workflow_engine.list_workflow_schedules(run_id)
        if item.payload["template_id"] == template_id
    )
    with sqlite3.connect(str(workflow_engine.WORKFLOW_DB)) as con:
        con.execute(
            "UPDATE workflow_schedules SET next_run_at=? WHERE schedule_id=?",
            (due.isoformat(), schedule.schedule_id),
        )
    return schedule


def _fire(run_id: str, template_id: str, due: datetime) -> workflow_engine.WorkflowRun:
    _make_due(run_id, template_id, due)
    with patch("vahana.deliver", return_value={"status": "ok", "pushed": False}):
        assert workflow_engine.fire_due_workflow_schedules(due)["fired"] == 1
    run = workflow_engine.get_workflow_run(run_id)
    assert run is not None
    return run


def _approve_and_complete(run_id: str, summary: str) -> workflow_engine.WorkflowRun:
    workflow_engine.request_stage_confirmation(run_id, summary=f"Preview: {summary}")
    with patch("dharma.gate_action", return_value=SimpleNamespace(allowed=True, reasons=[])):
        workflow_engine.approve_pending_stage(run_id, approved_by="user")
    return workflow_engine.complete_current_stage(run_id, summary=summary)


def test_due_schedule_is_idempotent_and_records_check_in() -> None:
    run = workflow_engine.start_workflow_run("career", user_id="asha", inputs=_career_inputs())
    due = datetime(2026, 9, 11, 8, 0, tzinfo=timezone.utc)
    schedule = _make_due(run.run_id, "weekly_scan", due)

    with patch("vahana.deliver", return_value={"status": "delivered", "channel": "in_app"}) as deliver:
        first = workflow_engine.fire_due_workflow_schedules(due)
        second = workflow_engine.fire_due_workflow_schedules(due)

    assert first["fired"] == 1
    assert second["fired"] == 0
    deliver.assert_called_once()
    updated = workflow_engine.get_workflow_run(run.run_id)
    assert updated is not None
    assert updated.current_stage_id == "market_scan"
    assert updated.status == "active"
    assert updated.state["scheduled_prompt"]["schedule_id"] == schedule.schedule_id
    assert updated.state["scheduled_prompt"]["mode"] == "check_in"
    check_ins = [
        event for event in workflow_engine.list_workflow_events(run.run_id)
        if event.event_type == "scheduled_check_in"
    ]
    assert [event.event_id for event in check_ins] == [f"wfe_schedule_{schedule.schedule_id}_20260911080000_checkin"]


def test_scheduled_check_in_never_drops_a_pending_or_approved_confirmation() -> None:
    run = workflow_engine.start_workflow_run("career", user_id="asha", inputs=_career_inputs())
    for stage in ("market_scan", "shortlist", "tailor"):
        run = workflow_engine.complete_current_stage(run.run_id, summary=f"Completed {stage}")
    workflow_engine.request_stage_confirmation(run.run_id, summary="Submit application to Example Co")

    run = _fire(run.run_id, "weekly_scan", datetime(2026, 9, 14, 3, 30, tzinfo=timezone.utc))
    assert run.current_stage_id == "apply"
    assert run.status == "waiting_confirmation"
    assert run.state["confirmation"]["status"] == "pending"
    assert run.state["confirmation"]["summary"] == "Submit application to Example Co"
    assert run.state["scheduled_prompt"]["mode"] == "queued"
    assert run.state["scheduled_prompt"]["stage_id"] == "market_scan"

    with patch("dharma.gate_action", return_value=SimpleNamespace(allowed=True, reasons=[])):
        run = workflow_engine.approve_pending_stage(run.run_id, approved_by="asha")
    run = _fire(run.run_id, "weekly_scan", datetime(2026, 9, 21, 3, 30, tzinfo=timezone.utc))
    assert run.current_stage_id == "apply"
    assert run.state["confirmation"]["status"] == "approved"
    assert run.state["scheduled_prompt"]["mode"] == "queued"

    run = workflow_engine.complete_current_stage(run.run_id, summary="Application submitted after approval")
    assert run.current_stage_id == "track"


_TRAVEL_INPUTS = {
    "origin": "Delhi",
    "destination": "Japan",
    "dates": "10-18 November",
    "travelers": "2 adults",
    "budget": "INR 300,000",
}


def test_price_watch_never_rewinds_a_booked_trip() -> None:
    run = workflow_engine.start_workflow_run(
        "travel",
        user_id="priya",
        inputs={
            "origin": "Delhi",
            "destination": "Japan",
            "dates": "10-18 November",
            "travelers": "2 adults",
            "budget": "INR 300,000",
            "price_watch": True,
        },
    )
    for stage in ("research", "compare", "itinerary"):
        run = workflow_engine.complete_current_stage(run.run_id, summary=f"Completed {stage}")
    run = _approve_and_complete(run.run_id, "Booked flights and hotel")
    assert run.current_stage_id == "trip_ready"

    run = _fire(run.run_id, "price_watch", datetime(2026, 9, 12, 4, 30, tzinfo=timezone.utc))
    assert run.current_stage_id == "trip_ready"
    assert run.status == "active"
    assert "booking" in run.state["completed_stage_ids"]
    assert run.state["scheduled_prompt"]["mode"] == "check_in"
    assert run.state["scheduled_prompt"]["stage_id"] == "research"

    for stage in ("trip_ready", "review"):
        run = workflow_engine.complete_current_stage(run.run_id, summary=f"Completed {stage}")
    assert run.status == "completed"
    run = _fire(run.run_id, "price_watch", datetime(2026, 9, 13, 4, 30, tzinfo=timezone.utc))
    assert run.status == "completed"
    assert run.current_stage_id is None
    assert run.completed_at is not None


def test_weekly_scan_starts_a_new_cycle_once_the_career_path_is_complete() -> None:
    run = workflow_engine.start_workflow_run("career", user_id="asha", inputs=_career_inputs())
    for stage in ("market_scan", "shortlist", "tailor"):
        run = workflow_engine.complete_current_stage(run.run_id, summary=f"Completed {stage}")
    run = _approve_and_complete(run.run_id, "Application submitted")
    while run.current_stage_id:
        run = workflow_engine.complete_current_stage(run.run_id, summary=f"Completed {run.current_stage_id}")
    assert run.status == "completed"

    due = datetime(2026, 9, 14, 3, 30, tzinfo=timezone.utc)
    _make_due(run.run_id, "weekly_scan", due)
    with patch("vahana.deliver", return_value={"status": "delivered"}) as deliver:
        workflow_engine.fire_due_workflow_schedules(due)
    run = workflow_engine.get_workflow_run(run.run_id)
    assert run is not None
    assert run.status == "active"
    assert run.current_stage_id == "market_scan"
    assert run.completed_at is None
    assert run.state["cycle"] == 2
    assert "market_scan" not in run.state["completed_stage_ids"]
    assert run.state["scheduled_prompt"]["mode"] == "new_cycle"
    assert "ready for its next checkpoint" in deliver.call_args.kwargs["body"]
    stage = workflow_engine._stage(workflow_engine.get_pack("career") or {}, run.current_stage_id)
    assert workflow_engine._next_action(run, stage)["label"] != "Path complete"


def test_schedule_pushes_match_what_the_schedule_did() -> None:
    run = workflow_engine.start_workflow_run(
        "travel",
        user_id="priya",
        inputs={**_TRAVEL_INPUTS, "price_watch": True},
    )
    due = datetime(2026, 9, 12, 4, 30, tzinfo=timezone.utc)
    _make_due(run.run_id, "price_watch", due)
    with patch("vahana.deliver", return_value={"status": "delivered"}) as deliver:
        workflow_engine.fire_due_workflow_schedules(due)
    body = deliver.call_args.kwargs["body"]
    assert "ready for its next checkpoint" not in body
    assert body.startswith("Reminder for ")

    for stage in ("research", "compare", "itinerary"):
        run = workflow_engine.complete_current_stage(run.run_id, summary=f"Completed {stage}")
    workflow_engine.request_stage_confirmation(run.run_id, summary="Book flights")
    later = datetime(2026, 9, 13, 4, 30, tzinfo=timezone.utc)
    _make_due(run.run_id, "price_watch", later)
    with patch("vahana.deliver", return_value={"status": "delivered"}) as deliver:
        workflow_engine.fire_due_workflow_schedules(later)
    assert "waiting for your approval" in deliver.call_args.kwargs["body"]

    with patch("dharma.gate_action", return_value=SimpleNamespace(allowed=True, reasons=[])):
        workflow_engine.approve_pending_stage(run.run_id, approved_by="priya")
    run = workflow_engine.complete_current_stage(run.run_id, summary="Booked")
    while run.current_stage_id:
        run = workflow_engine.complete_current_stage(run.run_id, summary=f"Completed {run.current_stage_id}")
    finished = datetime(2026, 9, 20, 4, 30, tzinfo=timezone.utc)
    _make_due(run.run_id, "price_watch", finished)
    with patch("vahana.deliver") as deliver:
        fired = workflow_engine.fire_due_workflow_schedules(finished)
    deliver.assert_not_called()  # a finished trip is not "ready for a checkpoint"
    assert fired["items"][0]["delivery"]["status"] == "skipped"
    run = workflow_engine.get_workflow_run(run.run_id)
    assert run is not None and run.status == "completed"


def test_recurring_loop_stage_reopens_only_after_the_run_reaches_it() -> None:
    run = workflow_engine.start_workflow_run(
        "health",
        user_id="miguel",
        inputs={
            "goal": "Improve energy",
            "baseline": "Irregular sleep",
            "dietary_context": "Vegetarian",
            "activity_limits": "Flat walking only",
            "daily_checkin": True,
        },
    )
    assert run.current_stage_id == "safety"
    run = _fire(run.run_id, "daily_checkin", datetime(2026, 9, 11, 15, 0, tzinfo=timezone.utc))
    assert run.current_stage_id == "safety"
    assert run.status == "active"
    assert run.state["scheduled_prompt"]["mode"] == "check_in"

    for stage in ("safety", "weekly_plan"):
        run = workflow_engine.complete_current_stage(run.run_id, summary=f"Completed {stage}")
    run = _approve_and_complete(run.run_id, "Calendar blocks created")
    for stage in ("daily_track", "weekly_review"):
        run = workflow_engine.complete_current_stage(run.run_id, summary=f"Completed {stage}")
    assert run.status == "completed"

    run = _fire(run.run_id, "daily_checkin", datetime(2026, 9, 12, 15, 0, tzinfo=timezone.utc))
    assert run.current_stage_id == "daily_track"
    assert run.status == "waiting_for_user"
    assert run.completed_at is None
    assert run.state["scheduled_prompt"]["mode"] == "reopened"
    assert "schedule" in run.state["completed_stage_ids"]


def test_chat_turn_advances_only_the_thread_bound_to_the_run() -> None:
    run = workflow_engine.start_workflow_run("career", user_id="asha", inputs=_career_inputs())
    assert run.session_id is None

    run = workflow_engine.record_chat_stage_result(
        run.run_id, user_id="asha", session_id="thread-a", response_text="Five credible roles found."
    )
    assert run.current_stage_id == "shortlist"
    assert run.session_id == "thread-a"

    with pytest.raises(PermissionError, match="another chat session"):
        workflow_engine.record_chat_stage_result(
            run.run_id, user_id="asha", session_id="thread-b", response_text="Your blood report looks fine."
        )
    unchanged = workflow_engine.get_workflow_run(run.run_id)
    assert unchanged is not None
    assert unchanged.current_stage_id == "shortlist"
    assert "shortlist" not in unchanged.state["completed_stage_ids"]

    run = workflow_engine.record_chat_stage_result(
        run.run_id, user_id="asha", session_id="thread-a", response_text="Ranked shortlist ready."
    )
    assert run.current_stage_id == "tailor"


def test_checkpoint_does_not_advance_current_stage() -> None:
    run = workflow_engine.start_workflow_run(
        "teach",
        user_id="sam",
        inputs={"topic": "SQL window functions", "outcome": "Use them in interviews", "current_level": "New"},
    )
    stage = run.current_stage_id
    updated = workflow_engine.record_workflow_checkpoint(
        run.run_id,
        summary="Learner answered the first check.",
        details={"correct": False},
        event_type="guided_learning_checkpoint",
    )
    assert updated.current_stage_id == stage
    assert updated.state["last_checkpoint"]["details"]["correct"] is False


def test_kala_tick_includes_workflow_schedule_pass() -> None:
    now = datetime(2026, 9, 11, 8, 0)
    with patch.object(kala_scheduler, "_load_state", return_value={}), patch.object(
        kala_scheduler, "_save_state"
    ), patch.object(kala_scheduler, "_fire_due_reminders", return_value=0), patch.object(
        kala_scheduler, "_fire_due_reviews", return_value=0
    ), patch.object(
        workflow_engine, "fire_due_workflow_schedules", return_value={"fired": 2}
    ) as workflow_pass:
        result = kala_scheduler.tick(now)

    workflow_pass.assert_called_once_with(now)
    assert result["workflow_fired"] == 2
