"""A durable path's context reaches only the chat thread the path is bound to.

Integration of two Phase 0 fixes: the workflow engine binds a run to one chat
thread, and the server resolves the caller's profile itself. A stale client can
still send another thread's workflow_run_id; that turn must get neither the
path's context nor its stage.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import server

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


def _career_run(user_id: str = "asha"):
    return workflow_engine.start_workflow_run(
        "career",
        user_id=user_id,
        inputs={
            "target_role": "Senior product manager",
            "locations": "Remote India",
            "experience": "Eight years in B2B SaaS",
            "weekly_scan": False,
            "timezone": "Asia/Kolkata",
        },
    )


def test_context_is_refused_to_a_thread_the_run_is_not_bound_to() -> None:
    run = _career_run()
    # Unbound: any thread may pick the path up (it binds on the first result).
    assert "[NARAD WORKFLOW CONTEXT" in workflow_engine.build_workflow_context(
        run.run_id, user_id="asha", session_id="thread-b"
    )
    workflow_engine.record_chat_stage_result(
        run.run_id, user_id="asha", session_id="thread-a", response_text="Five credible roles found."
    )

    with pytest.raises(workflow_engine.WorkflowSessionMismatch):
        workflow_engine.build_workflow_context(run.run_id, user_id="asha", session_id="thread-b")
    # Still a PermissionError, so existing handlers keep ignoring it.
    assert issubclass(workflow_engine.WorkflowSessionMismatch, PermissionError)
    assert "Current stage" in workflow_engine.build_workflow_context(
        run.run_id, user_id="asha", session_id="thread-a"
    )
    # Callers outside chat (the /context API) do not pass a thread.
    assert workflow_engine.build_workflow_context(run.run_id, user_id="asha")


def test_chat_turn_from_another_thread_drops_the_stale_workflow_id() -> None:
    run = _career_run()
    workflow_engine.record_chat_stage_result(
        run.run_id, user_id="asha", session_id="thread-a", response_text="Five credible roles found."
    )

    stale = server.ChatRequest(query="how is my blood report?", user_id="asha", workflow_run_id=run.run_id)
    assert server._workflow_context_for_turn(stale, "thread-b") == ""
    # Cleared for the rest of the turn: no stage result, no working-state carryover.
    assert stale.workflow_run_id is None

    bound = server.ChatRequest(query="rank them", user_id="asha", workflow_run_id=run.run_id)
    context = server._workflow_context_for_turn(bound, "thread-a")
    assert run.run_id in context
    assert bound.workflow_run_id == run.run_id

    # A turn that names no path gets the open path bound to its own thread (the
    # server keeps the binding, so every device continues it) and nothing elsewhere.
    unnamed = server.ChatRequest(query="and the next one?", user_id="asha")
    assert run.run_id in server._workflow_context_for_turn(unnamed, "thread-a")
    assert unnamed.workflow_run_id == run.run_id
    elsewhere = server.ChatRequest(query="hi", user_id="asha")
    assert server._workflow_context_for_turn(elsewhere, "thread-c") == ""
    assert elsewhere.workflow_run_id is None
    # Another profile's thread of the same name never reaches Asha's path.
    assert server._workflow_context_for_turn(server.ChatRequest(query="hi", user_id="bob"), "thread-a") == ""


def test_another_profiles_run_still_fails_loudly() -> None:
    run = _career_run(user_id="asha")
    request = server.ChatRequest(query="continue", user_id="bob", workflow_run_id=run.run_id)
    with pytest.raises(PermissionError, match="another user"):
        server._workflow_context_for_turn(request, "thread-a")
