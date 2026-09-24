"""Stage B fast path: streamed turns, hand-off instead of rewrite, the pre-router.

Drives server._run_agent_task end to end with scripted models (no network, no
LLM) and reads the SSE queue exactly as the client would. Model calls are
counted per agent, which pins the cost of each turn shape:

  single avatar, hand-off       2 calls  (route, avatar)
  single avatar, hand_off=false 3 calls  (route, avatar, rewrite)
  two avatars in parallel       4 calls, 3 serial (route, both avatars at once, synthesis)
  pre-routed                    1 call   (avatar)
"""
from __future__ import annotations

import asyncio
import json
import sys
import time
import uuid
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

_r = next(p for p in Path(__file__).resolve().parents if (p / "narad_paths.py").exists())
sys.path[:0] = [str(_r)]  # narad root hop
import narad_paths  # noqa: F401  — registers all phase dirs; must precede phase imports

# isort: split
import avatar_agents
import model_config
import server
import yantra
from google.adk.agents import LlmAgent
from google.adk.models.base_llm import BaseLlm
from google.adk.models.llm_response import LlmResponse
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types
from text_stream import DeltaStream

import cost_ledger
import smriti_core
import workflow_engine

ANSWERS = {
    "Matsya": "Pune had 12 mm of rain today, mostly after lunch; the evening should stay dry and cool.",
    "Rama": "Leave at 8 with an umbrella, and keep the afternoon clear for the 3 pm school pickup.",
    "Krishna": "Photosynthesis turns light, water and carbon dioxide into sugar and oxygen in the leaf.",
    "Parashurama": "The failing test expects UTC timestamps; the fixture builds local ones, so compare in UTC.",
}
SYNTHESIS = "Rain eased by evening, so the 8 am plan with an umbrella works and the pickup stays free."
CHATTER = "Let me ask Matsya."
_EMPTY_BUNDLE = {
    "context": "", "attachments": [], "durable_refs": [], "missing": [], "urls": [], "image_data_uris": [],
}


class _Scripted(BaseLlm):
    """Streams like ADK's LiteLlm: text chunks (partial), then one aggregated response."""

    reply: Any = None  # (llm_request) -> (text, [(tool_name, args), ...])
    log: Any = None  # shared list of (model, started, finished)

    async def generate_content_async(self, llm_request, stream: bool = False):
        started = time.monotonic()
        await asyncio.sleep(0.05)
        text, calls = self.reply(llm_request)
        if stream:
            for index in range(0, len(text), 16):
                yield LlmResponse(
                    content=types.Content(role="model", parts=[types.Part(text=text[index:index + 16])]),
                    partial=True,
                )
        parts = [types.Part(text=text)] if text else []
        parts += [types.Part(function_call=types.FunctionCall(name=name, args=args)) for name, args in calls]
        self.log.append((self.model, started, time.monotonic()))
        yield LlmResponse(
            content=types.Content(role="model", parts=parts),
            usage_metadata=types.GenerateContentResponseUsageMetadata(
                prompt_token_count=100, candidates_token_count=10, total_token_count=110,
            ),
        )


def _supervisor(calls: list[tuple[str, dict]], chatter: str = CHATTER):
    """Routes on the first call; synthesises once function responses are back."""

    def reply(llm_request):
        last = llm_request.contents[-1]
        if any(part.function_response for part in last.parts or []):
            return SYNTHESIS, []
        return chatter, calls

    return reply


@pytest.fixture
def harness(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> dict[str, list]:
    """Isolate a chat turn: no memory, no disk writes outside tmp, a fake ledger."""
    record: dict[str, list] = {"log": [], "ledger": [], "thread": []}
    monkeypatch.setenv("NARAD_SUPERVISOR_RECALL_BUDGET", "0")
    monkeypatch.setenv("NARAD_JEV_ROUTE_MODE", "off")
    monkeypatch.setenv("NARAD_LEARNING_FREEZE", "1")
    monkeypatch.delenv("NARAD_PREROUTER", raising=False)
    monkeypatch.setattr(yantra, "_TRACE_DIR", tmp_path / "traces")
    monkeypatch.setattr(avatar_agents, "_avatar_session_cache", {})
    monkeypatch.setattr(avatar_agents, "_phase_state", {})
    monkeypatch.setattr(workflow_engine, "WORKFLOW_DB", tmp_path / "workflows.db")
    monkeypatch.setattr(workflow_engine, "_capability_flags", lambda: {"learning": True, "planning": True})

    async def _no_recall(*_args, **_kwargs):
        return {"context": ""}

    async def _no_status(*_args, **_kwargs):
        return None

    def _ledger(**kwargs):
        record["ledger"].append(kwargs)
        return {"cost_usd": 0.001}

    monkeypatch.setattr(smriti_core, "recall_context", _no_recall)
    monkeypatch.setattr(smriti_core, "capture_episode", lambda **_kwargs: None)
    monkeypatch.setattr(avatar_agents, "_avatar_runtime_status", _no_status)
    monkeypatch.setattr(model_config, "get_avatar_model", lambda name: f"scripted-{name.lower()}")
    monkeypatch.setattr(cost_ledger, "record", _ledger)
    for name, stub in {
        "_load_working_state": lambda *_args, **_kwargs: None,
        "_load_thread": lambda *_args, **_kwargs: [],
        "_append_thread_turn": lambda **kwargs: record["thread"].append(kwargs),
        "_save_working_state": lambda **_kwargs: None,
        "_summarize_thread": lambda **_kwargs: "",
        "_record_harness_session_state": lambda **_kwargs: None,
    }.items():
        monkeypatch.setattr(server, name, stub)
    try:
        import audit_trail

        monkeypatch.setattr(audit_trail, "_write", lambda *_args, **_kwargs: None)
    except ImportError:
        pass
    return record


def _runner(log: list, supervisor_reply) -> Runner:
    avatars = [
        LlmAgent(
            name=name,
            description=f"{name}, a scripted test avatar.",
            model=_Scripted(model=f"scripted-{name.lower()}", reply=lambda _req, n=name: (ANSWERS[n], []), log=log),
            instruction=f"You are {name}.",
        )
        for name in ANSWERS
    ]
    narad = LlmAgent(
        name="Narad",
        model=_Scripted(model="scripted-narad", reply=supervisor_reply, log=log),
        instruction="Route every request.",
        tools=[avatar_agents._make_avatar_tool(avatar, user_id="asha") for avatar in avatars],
    )
    return Runner(agent=narad, app_name="avatara", session_service=InMemorySessionService())


def _turn(
    monkeypatch: pytest.MonkeyPatch,
    runner: Runner,
    query: str,
    *,
    bundle: dict | None = None,
    workflow_run_id: str | None = None,
    session_id: str | None = None,
) -> list[dict]:
    monkeypatch.setattr(server, "_get_runner_for_user", lambda *_args, **_kwargs: runner)
    monkeypatch.setattr(server, "_build_attachment_bundle", lambda *_args, **_kwargs: bundle or _EMPTY_BUNDLE)
    request = server.ChatRequest(query=query, user_id="asha", workflow_run_id=workflow_run_id)

    async def scenario() -> list[dict]:
        queue: asyncio.Queue = asyncio.Queue()
        await server._run_agent_task(request, session_id or f"thread-{uuid.uuid4().hex[:8]}", queue)
        events = []
        while not queue.empty():
            item = queue.get_nowait()
            if item is not None:
                events.append(json.loads(item))
        return events

    return asyncio.run(scenario())


def _calls(log: list) -> dict[str, int]:
    counts: dict[str, int] = {}
    for model, _started, _finished in log:
        counts[model] = counts.get(model, 0) + 1
    return counts


def _serial_depth(log: list) -> int:
    """Longest chain of model calls that ran one after another."""
    calls = sorted(log, key=lambda item: item[1])
    depth = [1] * len(calls)
    for i, (_model, started, _finished) in enumerate(calls):
        for j in range(i):
            if calls[j][2] <= started:
                depth[i] = max(depth[i], depth[j] + 1)
    return max(depth, default=0)


_SHAPE = {"route", "text_delta", "text_reset", "avatar_start", "avatar_done", "narad_synthesis", "usage", "done"}


def _shape(events: list[dict]) -> list[str]:
    """The event sequence with consecutive deltas of one source collapsed."""
    shape: list[str] = []
    for event in events:
        if event["type"] not in _SHAPE:
            continue
        label = event["type"]
        if label in {"text_delta", "text_reset"}:
            label = f"{label}:{event['data']['source']}"
        elif label in {"avatar_start", "avatar_done", "route"}:
            label = f"{label}:{event['data']['avatar']}"
        if not shape or shape[-1] != label or not label.startswith("text_delta"):
            shape.append(label)
    return shape


def _streamed(events: list[dict], source: str) -> str:
    return "".join(e["data"]["text"] for e in events if e["type"] == "text_delta" and e["data"]["source"] == source)


def test_single_avatar_hand_off_costs_two_calls_and_streams_the_answer(
    monkeypatch: pytest.MonkeyPatch, harness: dict[str, list]
) -> None:
    runner = _runner(harness["log"], _supervisor([("invoke_matsya", {"task": "Rain in Pune today"})]))
    events = _turn(monkeypatch, runner, "How much did it rain in Pune today?")

    assert _calls(harness["log"]) == {"scripted-narad": 1, "scripted-matsya": 1}
    assert _serial_depth(harness["log"]) == 2
    assert _shape(events) == [
        "text_delta:narad",       # routing chatter streams first...
        "text_reset:narad",       # ...and is dropped once the supervisor calls a tool
        "avatar_start:Matsya",
        "text_delta:Matsya",      # the answer being written
        "avatar_done:Matsya",
        "narad_synthesis",        # the same text, complete, for persistence
        "usage",
        "done",
    ]
    assert _streamed(events, "narad") == CHATTER
    assert _streamed(events, "Matsya") == ANSWERS["Matsya"]
    deltas = [e for e in events if e["type"] == "text_delta" and e["data"]["source"] == "Matsya"]
    assert all(e["data"]["handoff"] is True for e in deltas)
    synthesis = [e["data"]["text"] for e in events if e["type"] == "narad_synthesis"]
    assert synthesis == [ANSWERS["Matsya"]]
    # Post-processing sees a normal reply.
    assert harness["thread"][-1]["role"] == "assistant"
    assert harness["thread"][-1]["text"] == ANSWERS["Matsya"]
    # One usage event and one "turn" ledger entry, with the routing call in it.
    usage = [e["data"] for e in events if e["type"] == "usage"]
    assert len(usage) == 1
    assert usage[0]["total_tokens"] == 220  # routing call + the avatar that wrote the reply
    turn_rows = [row for row in harness["ledger"] if row["source"] == "turn"]
    assert len(turn_rows) == 1 and turn_rows[0]["prompt_tokens"] == 100
    assert [row["source"] for row in harness["ledger"]].count("avatar:Matsya") == 1


@pytest.mark.parametrize("hand_off", [False, "false"])  # models sometimes send JSON booleans as strings
def test_hand_off_false_keeps_the_rewrite_and_costs_three_calls(
    monkeypatch: pytest.MonkeyPatch, harness: dict[str, list], hand_off: Any
) -> None:
    runner = _runner(
        harness["log"], _supervisor([("invoke_matsya", {"task": "Rain in Pune today", "hand_off": hand_off})])
    )
    events = _turn(monkeypatch, runner, "How much did it rain in Pune today?")

    assert _calls(harness["log"]) == {"scripted-narad": 2, "scripted-matsya": 1}
    assert _serial_depth(harness["log"]) == 3
    matsya = [e for e in events if e["type"] == "text_delta" and e["data"]["source"] == "Matsya"]
    assert matsya and all(e["data"]["handoff"] is False for e in matsya)
    assert [e["data"]["text"] for e in events if e["type"] == "narad_synthesis"] == [SYNTHESIS]
    assert _streamed(events, "narad") == CHATTER + SYNTHESIS
    assert len([e for e in events if e["type"] == "usage"]) == 1
    turn_rows = [row for row in harness["ledger"] if row["source"] == "turn"]
    assert len(turn_rows) == 1 and turn_rows[0]["prompt_tokens"] == 200  # both supervisor calls


def test_parallel_avatars_never_end_the_turn_early(
    monkeypatch: pytest.MonkeyPatch, harness: dict[str, list]
) -> None:
    # Both calls leave hand_off at its default (true): the guard must still
    # make the supervisor combine them.
    runner = _runner(harness["log"], _supervisor(
        [("invoke_matsya", {"task": "Rain in Pune today"}), ("invoke_rama", {"task": "Plan my morning"})],
        chatter="",
    ))
    events = _turn(monkeypatch, runner, "Will it rain in Pune, and plan my morning around it")

    counts = _calls(harness["log"])
    assert counts == {"scripted-narad": 2, "scripted-matsya": 1, "scripted-rama": 1}
    assert sum(counts.values()) == 4
    assert _serial_depth(harness["log"]) == 3  # the two avatars ran side by side
    assert [e["data"]["text"] for e in events if e["type"] == "narad_synthesis"] == [SYNTHESIS]
    avatar_deltas = [e for e in events if e["type"] == "text_delta" and e["data"]["source"] != "narad"]
    assert {e["data"]["source"] for e in avatar_deltas} == {"Matsya", "Rama"}
    assert all(e["data"]["handoff"] is False for e in avatar_deltas)
    assert not [e for e in events if e["type"] == "text_reset"]  # no chatter was streamed
    shape = _shape(events)
    assert shape[:2] == ["avatar_start:Matsya", "avatar_start:Rama"]
    assert shape[-4:] == ["text_delta:narad", "narad_synthesis", "usage", "done"]


def test_bare_url_is_pre_routed_to_matsya_in_one_call(
    monkeypatch: pytest.MonkeyPatch, harness: dict[str, list]
) -> None:
    runner = _runner(harness["log"], _supervisor([("invoke_rama", {"task": "wrong"})]))
    url = "https://example.com/rainfall/pune"
    bundle = dict(_EMPTY_BUNDLE, urls=[url], context=f"[USER-PROVIDED INPUTS]\n- {url}\n[END USER-PROVIDED INPUTS]")
    events = _turn(monkeypatch, runner, url, bundle=bundle)

    assert _calls(harness["log"]) == {"scripted-matsya": 1}
    assert _shape(events) == [
        "route:Matsya",
        "avatar_start:Matsya",
        "text_delta:Matsya",
        "avatar_done:Matsya",
        "narad_synthesis",
        "usage",
        "done",
    ]
    route = next(e["data"] for e in events if e["type"] == "route")
    assert route == {"avatar": "Matsya", "reason": "url_only", "via": "prerouter"}
    assert [e["data"]["text"] for e in events if e["type"] == "narad_synthesis"] == [ANSWERS["Matsya"]]
    start = next(e["data"] for e in events if e["type"] == "avatar_start")
    assert url in start["task"]
    # No supervisor call, so no "turn" ledger row; the avatar's row is the cost.
    assert [row["source"] for row in harness["ledger"]] == ["avatar:Matsya"]
    usage = [e["data"] for e in events if e["type"] == "usage"]
    assert len(usage) == 1 and usage[0]["total_tokens"] == 110
    assert usage[0]["model"] == "scripted-matsya"


def test_pre_routed_turn_is_in_the_supervisor_history(
    monkeypatch: pytest.MonkeyPatch, harness: dict[str, list]
) -> None:
    runner = _runner(harness["log"], _supervisor([("invoke_matsya", {"task": "wrong"})]))
    url = "https://example.com/rainfall/pune"
    _turn(monkeypatch, runner, url, bundle=dict(_EMPTY_BUNDLE, urls=[url]))

    async def _history() -> list:
        sessions = await runner.session_service.list_sessions(app_name="avatara", user_id="asha")
        session = await runner.session_service.get_session(
            app_name="avatara", user_id="asha", session_id=sessions.sessions[0].id
        )
        return session.events

    events = asyncio.run(_history())
    assert [event.author for event in events] == ["user", "Narad", "Narad"]
    assert events[1].get_function_calls()[0].name == "invoke_matsya"
    assert events[2].actions.skip_summarization is True
    assert events[2].get_function_responses()[0].response["result"] == ANSWERS["Matsya"]


def test_prerouter_off_sends_the_same_turn_to_the_supervisor(
    monkeypatch: pytest.MonkeyPatch, harness: dict[str, list]
) -> None:
    monkeypatch.setenv("NARAD_PREROUTER", "off")
    runner = _runner(harness["log"], _supervisor([("invoke_matsya", {"task": "Read the page"})]))
    url = "https://example.com/rainfall/pune"
    events = _turn(monkeypatch, runner, url, bundle=dict(_EMPTY_BUNDLE, urls=[url]))

    assert _calls(harness["log"]) == {"scripted-narad": 1, "scripted-matsya": 1}
    assert not [e for e in events if e["type"] == "route"]


def test_workflow_stage_owner_gets_the_turn_and_the_stage_advances(
    monkeypatch: pytest.MonkeyPatch, harness: dict[str, list]
) -> None:
    run = workflow_engine.start_workflow_run(
        "teach",
        user_id="asha",
        inputs={
            "topic": "Photosynthesis",
            "outcome": "Explain it to a ten-year-old",
            "current_level": "New",
            "spaced_reviews": False,
            "timezone": "Asia/Kolkata",
        },
    )
    assert workflow_engine.current_stage_owner(run.run_id, user_id="asha") == "Krishna"
    runner = _runner(harness["log"], _supervisor([("invoke_matsya", {"task": "wrong"})]))
    events = _turn(monkeypatch, runner, "I know plants need sunlight", workflow_run_id=run.run_id, session_id="thread-teach")

    assert _calls(harness["log"]) == {"scripted-krishna": 1}
    assert next(e["data"] for e in events if e["type"] == "route")["reason"] == "workflow_stage_owner"
    start = next(e["data"] for e in events if e["type"] == "avatar_start")
    assert "[NARAD WORKFLOW CONTEXT" in start["task"] and "I know plants need sunlight" in start["task"]
    assert any(e["type"] == "workflow_updated" for e in events)
    stage_ids = [stage["id"] for stage in workflow_engine.get_pack("teach")["stages"]]
    advanced = workflow_engine.get_workflow_run(run.run_id)
    assert stage_ids.index(advanced.current_stage_id) > stage_ids.index(run.current_stage_id)


def _chunk(*parts: Any) -> SimpleNamespace:
    return SimpleNamespace(partial=True, content=SimpleNamespace(parts=list(parts)))


def _text(text: str, thought: bool = False) -> SimpleNamespace:
    return SimpleNamespace(text=text, thought=thought, function_call=None, function_response=None)


def test_deltas_never_carry_thought_parts_or_split_think_blocks() -> None:
    narad, matsya = DeltaStream("narad"), DeltaStream("Matsya", handoff=True)
    assert server._event_to_sse(_chunk(_text("private reasoning", thought=True)), stream=narad) == []
    payloads: list[str] = []
    for chunk in ["Hello <thi", "nk>hidden</th", "ink> world"]:
        payloads += server._event_to_sse(_chunk(_text(chunk)), stream=narad)
        # Another source mid-stream keeps its own filter state.
        delta = matsya.feed("Rain: 12 mm. ")
        assert delta and json.loads(delta)["data"] == {"source": "Matsya", "text": "Rain: 12 mm. ", "handoff": True}
    assert "".join(json.loads(p)["data"]["text"] for p in payloads) == "Hello  world"
    # Without a stream (non-streaming callers), chunks are dropped as before.
    assert server._event_to_sse(_chunk(_text("ignored"))) == []


def test_delta_stream_resets_only_after_text_and_flushes_a_held_tail() -> None:
    stream = DeltaStream("narad")
    assert stream.reset() is None  # nothing reached the client: nothing to drop
    assert json.loads(stream.feed("Let me <th"))["data"]["text"] == "Let me "
    assert json.loads(stream.flush())["data"]["text"] == "<th"  # not a think tag after all
    assert json.loads(stream.reset()) == {"type": "text_reset", "data": {"source": "narad"}}
    assert stream.reset() is None


def test_handoff_text_turns_the_phase_marker_into_the_chip() -> None:
    assert server._handoff_text({"result": "Step one done.\nCURRENT_PHASE: explain"}) == (
        "Step one done.\n\n[Continuing: explain]"
    )
    assert server._handoff_text({"result": "All mastered.\nCURRENT_PHASE: DONE"}) == "All mastered."
    assert server._handoff_text({"result": "Here is the plan.\nDONE"}) == "Here is the plan."
    assert server._handoff_text({"result": "short", "full_result": "the full text"}) == "the full text"
    assert server._handoff_text("not a dict") == ""
