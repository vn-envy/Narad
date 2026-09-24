from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

_root = next(path for path in Path(__file__).resolve().parents if (path / "narad_paths.py").exists())
sys.path[:0] = [str(_root)]
from learning_workspace_api import learning_router

import learning_workspace
import narad_paths  # noqa: F401
import profile_context
import vahana
import workflow_engine


@pytest.fixture()
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setattr(workflow_engine, "WORKFLOW_DB", tmp_path / "workflows.db")
    # A finished path notifies its person (task_done): keep that inbox and ledger here.
    monkeypatch.setattr(vahana, "INBOX_DIR", tmp_path / "inbox")
    monkeypatch.setattr(profile_context, "PROFILES_DIR", tmp_path / "profiles")
    monkeypatch.setitem(sys.modules, "karma_log", SimpleNamespace(log_karma=lambda *args, **kwargs: None))
    monkeypatch.setattr(learning_workspace, "LEARNING_DIR", tmp_path / "learning")
    monkeypatch.setattr(
        workflow_engine,
        "_capability_flags",
        lambda: {
            "learning": True,
            "planning": True,
            "search": True,
            "tts": True,
        },
    )
    app = FastAPI()
    app.include_router(learning_router)
    return TestClient(app)


def _progress(mastered: int, total: int = 2) -> dict:
    return {"total": total, "mastered": mastered, "shaky": 0, "topic": "Attention", "mode": "teach"}


def test_direct_teach_creates_one_path_and_guided_progress_updates_it(client: TestClient) -> None:
    first_step = {
        "kind": "atom",
        "atom_id": "attention-1",
        "name": "Queries and keys",
        "narration": "One concept at a time.",
        "quiz": {"type": "free", "question": "What does a query seek?"},
        "progress": _progress(0),
        "avatar": "Krishna",
    }
    with patch("learning_workspace_api.guided_mode.start_session", return_value={
        "resumed": False,
        "session": {"workspace_id": "attention", "topic": "Attention", "mode": "teach", "status": "active"},
        "step": first_step,
    }):
        started = client.post(
            "/learning/guided/start?user_id=sam",
            json={"topic": "Attention", "mode": "teach"},
        )

    assert started.status_code == 200
    run = started.json()["workflow_run"]
    assert run["workflow_id"] == "teach"
    assert run["current_stage_id"] == "diagnostic"
    assert run["state"]["learning_workspace_id"]

    next_step = {**first_step, "atom_id": "attention-2", "name": "Values", "progress": _progress(1)}
    with patch("learning_workspace_api.guided_mode.submit_answer", return_value={
        "grade": {"correct": True, "feedback": "Correct", "remediation": ""},
        "advanced": True,
        "step": next_step,
    }):
        answered = client.post(
            "/learning/guided/answer?user_id=sam",
            json={
                "workspace_id": "attention",
                "answer": "It seeks relevant keys",
                "workflow_run_id": run["run_id"],
            },
        )

    assert answered.status_code == 200
    advanced = answered.json()["workflow_run"]
    assert advanced["current_stage_id"] == "lesson"
    assert advanced["progress_percent"] == 33

    with patch("learning_workspace_api.guided_mode.submit_answer", return_value={
        "grade": {"correct": True, "feedback": "Mastered", "remediation": ""},
        "advanced": True,
        "step": {"kind": "complete", "message": "Mastered", "progress": _progress(2)},
    }):
        completed = client.post(
            "/learning/guided/answer?user_id=sam",
            json={
                "workspace_id": "attention",
                "answer": "Values carry the selected information",
                "workflow_run_id": run["run_id"],
            },
        )

    final = completed.json()["workflow_run"]
    assert final["status"] == "completed"
    assert final["progress_percent"] == 100
