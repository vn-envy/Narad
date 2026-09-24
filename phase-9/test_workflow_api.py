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


# ── Workflows v2: evidence, the person's own actions, chat-card starts ──────

_HEALTH = {"goal": "More energy", "baseline": "Desk job", "dietary_context": "Vegetarian", "activity_limits": "None"}


@pytest.fixture()
def evidence_client(client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    import path_fixtures

    path_fixtures.isolate(tmp_path, monkeypatch)
    return client


def test_a_free_complete_is_refused_with_what_is_missing(evidence_client: TestClient) -> None:
    run = evidence_client.post("/workflows/health/runs?user_id=miguel", json={"inputs": _HEALTH}).json()["run"]
    assert run["current_stage_id"] == "labs"
    labs = next(stage for stage in run["stages"] if stage["id"] == "labs")
    assert labs["done_when_text"][0]["text"] == "Lab values you confirmed are saved"
    assert run["next_action"]["can_skip"] is True and run["next_action"]["can_confirm"] is False

    refused = evidence_client.post(
        f"/workflow-runs/{run['run_id']}/actions?user_id=miguel",
        json={"action": "complete", "summary": "Synthetic validation completed Lab report"},
    )
    assert refused.status_code == 409
    assert "Lab values you confirmed are saved" in refused.json()["detail"]
    again = evidence_client.get(f"/workflow-runs/{run['run_id']}?user_id=miguel").json()
    assert again["current_stage_id"] == "labs"

    skipped = evidence_client.post(f"/workflow-runs/{run['run_id']}/actions?user_id=miguel", json={"action": "skip"})
    assert skipped.status_code == 200
    assert skipped.json()["run"]["current_stage_id"] == "safety"
    assert next(s for s in skipped.json()["run"]["stages"] if s["id"] == "labs")["status"] == "skipped"


def test_reading_a_run_settles_evidence_that_landed_since(evidence_client: TestClient) -> None:
    import document_review
    import path_fixtures

    import profile_context
    import workflow_engine

    run = evidence_client.post("/workflows/health/runs?user_id=miguel", json={"inputs": _HEALTH}).json()["run"]
    rid = path_fixtures.lab_review("miguel", save=False)
    workflow_engine.record_chat_stage_result(
        run["run_id"], user_id="miguel", session_id="thread-m",
        receipts=[path_fixtures.receipt("extract_fields", review_id=rid)],
    )
    pending = evidence_client.get(f"/workflow-runs/{run['run_id']}?user_id=miguel").json()
    assert pending["next_action"]["kind"] == "review" and pending["next_action"]["url"] == f"/?review={rid}"
    with profile_context.profile_scope("miguel"):
        document_review.save_review(rid, [{"id": "i1", "action": "confirm"}], document={"test_date": "2026-09-01"})
    listed = evidence_client.get("/workflow-runs?user_id=miguel").json()["runs"][0]
    assert listed["current_stage_id"] == "safety"
    done = next(stage for stage in listed["stages"] if stage["id"] == "labs")
    assert done["status"] == "done"
    assert done["output"]["evidence"][0]["url"] == f"/?review={rid}"


def test_a_chat_card_start_is_bound_to_its_thread_and_asks_two_questions(evidence_client: TestClient) -> None:
    intake = evidence_client.get("/workflows/travel/intake?user_id=priya").json()
    assert [item["key"] for item in intake["questions"]] == ["origin", "destination"]

    started = evidence_client.post(
        "/workflows/travel/runs?user_id=priya",
        json={"inputs": {"destination": "Goa"}, "partial": True, "session_id": "thread-goa"},
    )
    assert started.status_code == 200
    run = started.json()["run"]
    assert (run["current_stage_id"], run["status"], run["session_id"]) == ("intake", "waiting_for_user", "thread-goa")
    assert [item["key"] for item in run["intake_questions"]] == ["origin", "dates"]

    answered = evidence_client.post(
        f"/workflow-runs/{run['run_id']}/actions?user_id=priya",
        json={"action": "update_inputs", "payload": {"origin": "Delhi", "dates": "10-14 Dec"}},
    ).json()["run"]
    assert [item["key"] for item in answered["intake_questions"]] == ["travelers", "budget"]
    done = evidence_client.post(
        f"/workflow-runs/{run['run_id']}/actions?user_id=priya",
        json={"action": "update_inputs", "payload": {"travelers": "2", "budget": "INR 60,000"}},
    ).json()["run"]
    assert done["current_stage_id"] == "research"

    # Without partial, missing details are still a clear 422.
    assert evidence_client.post("/workflows/travel/runs?user_id=priya", json={"inputs": {}}).status_code == 422
    # A thread id must look like one.
    assert evidence_client.post("/workflows/travel/runs?user_id=priya",
                                json={"partial": True, "session_id": "../x"}).status_code == 422


def test_resume_here_binds_an_open_run_to_the_thread(evidence_client: TestClient) -> None:
    run = evidence_client.post("/workflows/health/runs?user_id=miguel", json={"inputs": _HEALTH}).json()["run"]
    bound = evidence_client.post(
        f"/workflow-runs/{run['run_id']}/actions?user_id=miguel",
        json={"action": "bind", "payload": {"session_id": "thread-9"}},
    )
    assert bound.status_code == 200 and bound.json()["run"]["session_id"] == "thread-9"
    assert evidence_client.post(
        f"/workflow-runs/{run['run_id']}/actions?user_id=someone-else",
        json={"action": "bind", "payload": {"session_id": "thread-9"}},
    ).status_code == 403
