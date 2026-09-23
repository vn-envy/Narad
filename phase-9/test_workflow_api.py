from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

_root = next(path for path in Path(__file__).resolve().parents if (path / "narad_paths.py").exists())
sys.path[:0] = [str(_root)]
from workflow_api import workflow_router

import narad_paths  # noqa: F401
import workflow_engine


@pytest.fixture()
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setattr(workflow_engine, "WORKFLOW_DB", tmp_path / "workflows.db")
    monkeypatch.setattr(
        workflow_engine,
        "_capability_flags",
        lambda: {
            name: True
            for name in (
                "planning", "learning", "health", "finance", "documents",
                "presentation", "filesystem", "sql", "tts", "search", "computer", "calendar", "email",
            )
        },
    )
    app = FastAPI()
    app.include_router(workflow_router)
    return TestClient(app)


def test_definition_start_read_and_status_contract(client: TestClient) -> None:
    definitions = client.get("/workflows")
    assert definitions.status_code == 200
    assert len(definitions.json()["workflows"]) == 6

    started = client.post(
        "/workflows/travel/runs?user_id=priya",
        json={
            "inputs": {
                "origin": "Delhi",
                "destination": "Japan",
                "dates": "10-18 November",
                "travelers": "2 adults",
                "budget": "INR 300,000",
                "price_watch": True,
            }
        },
    )
    assert started.status_code == 200
    run = started.json()["run"]
    assert run["workflow_id"] == "travel"
    assert run["current_stage_id"] == "research"
    assert run["next_action"]["kind"] == "chat"

    loaded = client.get(f"/workflow-runs/{run['run_id']}?user_id=priya")
    assert loaded.status_code == 200
    assert loaded.json()["title"].startswith("Travel:")
    assert client.get(f"/workflow-runs/{run['run_id']}?user_id=someone-else").status_code == 403

    paused = client.post(
        f"/workflow-runs/{run['run_id']}/actions?user_id=priya",
        json={"action": "pause"},
    )
    assert paused.status_code == 200
    assert paused.json()["run"]["status"] == "paused"
    resumed = client.post(
        f"/workflow-runs/{run['run_id']}/actions?user_id=priya",
        json={"action": "resume"},
    )
    assert resumed.status_code == 200
    assert resumed.json()["run"]["status"] == "active"
    cancelled = client.post(
        f"/workflow-runs/{run['run_id']}/actions?user_id=priya",
        json={"action": "cancel"},
    )
    assert cancelled.status_code == 200
    assert cancelled.json()["run"]["status"] == "cancelled"
    assert cancelled.json()["run"]["next_action"]["kind"] == "none"


def test_missing_intake_is_actionable_422(client: TestClient) -> None:
    response = client.post(
        "/workflows/health/runs?user_id=miguel",
        json={"inputs": {"goal": "Improve energy"}},
    )
    assert response.status_code == 422
    assert "baseline" in response.json()["detail"]
    assert "dietary_context" in response.json()["detail"]


def test_schedule_is_not_mutated_before_ownership_check(client: TestClient) -> None:
    started = client.post(
        "/workflows/career/runs?user_id=asha",
        json={
            "inputs": {
                "target_role": "Product lead",
                "locations": "Remote India",
                "experience": "Eight years in SaaS",
                "weekly_scan": True,
            }
        },
    ).json()["run"]
    schedule = started["schedules"][0]

    denied = client.patch(
        f"/workflow-schedules/{schedule['schedule_id']}?user_id=intruder",
        json={"enabled": False},
    )
    assert denied.status_code == 403
    unchanged = client.get(f"/workflow-runs/{started['run_id']}?user_id=asha").json()
    assert unchanged["schedules"][0]["enabled"] is True

    accepted = client.patch(
        f"/workflow-schedules/{schedule['schedule_id']}?user_id=asha",
        json={"enabled": False},
    )
    assert accepted.status_code == 200
    assert accepted.json()["schedule"]["enabled"] is False
