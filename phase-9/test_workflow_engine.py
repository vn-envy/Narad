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

    with patch("dharma.gate_action", return_value=SimpleNamespace(allowed=True, reasons=[])):
        run = workflow_engine.approve_stage(run.run_id, approved_by="asha")
    assert run.status == "active"
    assert run.state["confirmation"]["status"] == "approved"

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


def test_due_schedule_is_idempotent_and_reopens_target_stage() -> None:
    run = workflow_engine.start_workflow_run("career", user_id="asha", inputs=_career_inputs())
    schedule = workflow_engine.list_workflow_schedules(run.run_id)[0]
    due = datetime(2026, 9, 11, 8, 0, tzinfo=timezone.utc)
    with sqlite3.connect(str(workflow_engine.WORKFLOW_DB)) as con:
        con.execute(
            "UPDATE workflow_schedules SET next_run_at=? WHERE schedule_id=?",
            (due.isoformat(), schedule.schedule_id),
        )

    with patch("vahana.deliver", return_value={"status": "delivered", "channel": "in_app"}) as deliver:
        first = workflow_engine.fire_due_workflow_schedules(due)
        second = workflow_engine.fire_due_workflow_schedules(due)

    assert first["fired"] == 1
    assert second["fired"] == 0
    deliver.assert_called_once()
    updated = workflow_engine.get_workflow_run(run.run_id)
    assert updated is not None
    assert updated.current_stage_id == "market_scan"
    assert updated.status == "waiting_for_user"
    assert updated.state["scheduled_prompt"]["schedule_id"] == schedule.schedule_id


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
