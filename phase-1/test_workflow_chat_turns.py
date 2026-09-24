"""Workflow Paths in real chat turns (scripted models, no network).

Drives server._run_agent_task end to end, as test_streaming_fast_path does: a
stage-bound turn goes to the stage owner, whose tool results become evidence and
whose report_stage_result is the only thing that can finish the stage; an
unbound message that looks like a path gets a `path_suggestion` card, and
nothing starts until the person taps it.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

_r = next(p for p in Path(__file__).resolve().parents if (p / "narad_paths.py").exists())
sys.path[:0] = [str(_r)]  # narad root hop
import narad_paths  # noqa: F401  — registers all phase dirs; must precede phase imports

# isort: split
import avatar_agents
import pytest
import server
from google.adk.agents import LlmAgent
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.adk.tools import FunctionTool

# The streaming harness: scripted models, an isolated turn, and the SSE reader.
from test_streaming_fast_path import ANSWERS, _Scripted, _supervisor, _turn, harness  # noqa: F401

import profile_context
import workflow_engine
import workflow_tools

_TRAVEL = {"origin": "Delhi", "destination": "Goa", "dates": "10-14 December", "travelers": "2 adults",
           "budget": "INR 60,000", "timezone": "Asia/Kolkata"}

# Every test runs inside the streaming harness (no memory, no disk outside tmp).
pytestmark = pytest.mark.usefixtures("harness")


@pytest.fixture(autouse=True)
def isolated_profiles(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(profile_context, "PROFILES_DIR", tmp_path / "profiles")
    monkeypatch.setattr(workflow_engine, "_capability_flags", lambda: {
        name: True for name in ("planning", "learning", "search", "computer", "calendar", "email", "health",
                                "finance", "documents", "presentation")
    })


def exa_search(query: str) -> dict:
    """Stub search tool; its status is set per test."""
    return dict(_SEARCH_RESULT)


_SEARCH_RESULT: dict[str, Any] = {}


def _stage_runner(
    log: list, script: dict[str, list[tuple[str, list[tuple[str, dict]]]]], seen: list[str] | None = None,
) -> Runner:
    """Avatars that follow a script of (text, tool calls) steps, one per model call;
    ``seen`` collects the text of every request the supervisor receives."""

    def replies(name: str):
        steps = list(script.get(name) or [(ANSWERS[name], [])])

        def reply(_request):
            return steps.pop(0) if len(steps) > 1 else steps[0]

        return reply

    tools = [FunctionTool(workflow_tools.report_stage_result), FunctionTool(exa_search)]
    avatars = [
        LlmAgent(
            name=name,
            description=f"{name}, a scripted test avatar.",
            model=_Scripted(model=f"scripted-{name.lower()}", reply=replies(name), log=log),
            instruction=f"You are {name}.",
            tools=tools,
        )
        for name in ANSWERS
    ]
    route = _supervisor([("invoke_rama", {"task": "Help with the trip"})])

    def supervise(request):
        if seen is not None:
            seen.extend(part.text or "" for content in request.contents for part in content.parts or [])
        return route(request)

    narad = LlmAgent(
        name="Narad",
        model=_Scripted(model="scripted-narad", reply=supervise, log=log),
        instruction="Route every request.",
        tools=[avatar_agents._make_avatar_tool(avatar, user_id="asha") for avatar in avatars],
    )
    return Runner(agent=narad, app_name="avatara", session_service=InMemorySessionService())


def _teach_run() -> workflow_engine.WorkflowRun:
    return workflow_engine.start_workflow_run("teach", user_id="asha", inputs={
        "topic": "Photosynthesis", "outcome": "Explain it to a ten-year-old", "current_level": "New",
        "spaced_reviews": False, "timezone": "Asia/Kolkata",
    })


def test_the_stage_owners_report_with_its_fields_is_what_advances_the_stage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run = _teach_run()
    report = ("report_stage_result", {
        "status": "done", "summary": "Knows plants need light; unsure where the sugar is made.",
        "fields": {"starting_point": "Knows light matters; has not met chlorophyll"},
    })
    runner = _stage_runner([], {"Krishna": [("", [report]), ("Great start. Next: the leaf.", [])]})
    events = _turn(monkeypatch, runner, "I know plants need sunlight", workflow_run_id=run.run_id,
                   session_id="thread-teach")

    advanced = workflow_engine.get_workflow_run(run.run_id)
    assert advanced.current_stage_id == "lesson"
    assert advanced.state["stage_outputs"]["diagnostic"]["fields"]["starting_point"].startswith("Knows light")
    updated = [e["data"]["run"] for e in events if e["type"] == "workflow_updated"]
    assert updated and updated[-1]["current_stage_id"] == "lesson"
    assert "chat_turn" not in [e.event_type for e in workflow_engine.list_workflow_events(run.run_id)]


@pytest.mark.parametrize(("search_status", "stage_after"), [("error", "market_scan"), ("ok", "shortlist")])
def test_a_failed_search_leaves_the_stage_open_even_when_the_avatar_says_done(
    monkeypatch: pytest.MonkeyPatch, search_status: str, stage_after: str,
) -> None:
    monkeypatch.setitem(_SEARCH_RESULT, "status", search_status)
    monkeypatch.setitem(_SEARCH_RESULT, "summary", "Five results" if search_status == "ok" else "Exa is not set up")
    run = workflow_engine.start_workflow_run("career", user_id="asha", inputs={
        "target_role": "Product lead", "locations": "Remote India", "experience": "Eight years in SaaS",
    })
    report = ("report_stage_result", {"status": "done", "summary": "Found roles.",
                                      "fields": {"roles": ["Product lead at Example Co"]}})
    runner = _stage_runner([], {"Matsya": [
        ("", [("exa_search", {"query": "product lead remote india"})]),
        ("", [report]),
        ("Here is what I found.", []),
    ]})
    _turn(monkeypatch, runner, "Find me current roles", workflow_run_id=run.run_id, session_id="thread-career")

    after = workflow_engine.get_workflow_run(run.run_id)
    assert after.current_stage_id == stage_after
    receipts = after.state["stage_evidence"].get("market_scan", {}).get("receipts", [])
    if search_status == "error":
        assert [(item["tool"], item["ok"]) for item in receipts] == [("exa_search", False)]


def test_a_matching_message_gets_a_path_card_and_nothing_starts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    saved: dict[str, Any] = {}
    seen: list[str] = []
    monkeypatch.setattr(server, "_save_working_state", lambda **kwargs: saved.update(kwargs["state"]))
    runner = _stage_runner([], {}, seen)
    events = _turn(monkeypatch, runner, "Plan a trip to Goa in December", session_id="thread-goa")

    kinds = [event["type"] for event in events]
    card = next(event["data"] for event in events if event["type"] == "path_suggestion")
    assert kinds.index("path_suggestion") < kinds.index("done")
    assert (card["workflow_id"], card["action"], card["session_id"]) == ("travel", "start", "thread-goa")
    assert len(card["first_questions"]) == 2
    # Offered, never started, and not offered again in this thread.
    assert workflow_engine.list_workflow_runs(user_id="asha") == []
    assert saved["path_offers"] == ["travel"]
    request = server.ChatRequest(query="Plan a trip to Goa in December", user_id="asha")
    assert server._path_offer_for_turn(request, "thread-goa", [], {"path_offers": ["travel"]}) is None
    # The supervisor knew about the card (so it would not claim a path started).
    offer = next(text for text in seen if "[PATH OFFER]" in text)
    assert "start the Travel path" in offer and "Never say a path has started" in offer


def test_an_open_path_is_offered_to_resume_and_a_bound_thread_just_continues(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run = workflow_engine.start_workflow_run("travel", user_id="asha", inputs=_TRAVEL)
    runner = _stage_runner([], {})
    events = _turn(monkeypatch, runner, "Book flights to Goa for December", session_id="thread-new")
    card = next(event["data"] for event in events if event["type"] == "path_suggestion")
    assert (card["action"], card["run_id"], card["stage_title"]) == ("resume", run.run_id, "Live search")
    assert workflow_engine.get_workflow_run(run.run_id).session_id is None  # still the person's tap

    # The tap binds the run to this thread; the next turn needs no run id from the client.
    workflow_engine.bind_run_to_session(run.run_id, user_id="asha", session_id="thread-new")
    events = _turn(monkeypatch, runner, "Book flights to Goa for December", session_id="thread-new")
    assert not [event for event in events if event["type"] == "path_suggestion"]
    route = next(event["data"] for event in events if event["type"] == "route")
    assert (route["avatar"], route["reason"]) == ("Matsya", "workflow_stage_owner")
    assert next(e for e in events if e["type"] == "done")["data"]["workflow_run_id"] == run.run_id
