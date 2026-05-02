"""
Phase 1 FastAPI SSE server (+ Smriti memory + Yantra observability).

SSE event taxonomy (locked):
  avatar_start | avatar_done | narad_synthesis | done | error

New endpoints:
  GET /trace/{session_id}  — structured trace for a completed session
"""

from __future__ import annotations

import json
import os
import sys
import uuid

sys.path.insert(0, str(__import__("pathlib").Path(__file__).parent.parent / "phase-2"))
sys.path.insert(0, str(__import__("pathlib").Path(__file__).parent.parent / "phase-3"))

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.adk.events import Event

from narad_agent import build_narad_agent
from avatar_agents import AGENT_TOOL_NAMES
from yantra import Tracer

app = FastAPI(title="Avatara — Narad API", version="0.1.0-phase1")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

_session_service = InMemorySessionService()
_narad = build_narad_agent()
_runner = Runner(agent=_narad, app_name="avatara", session_service=_session_service)

def _rebuild_runner_for_user(user_id: str) -> Runner:
    """Return a runner whose avatar tools are scoped to the given user_id."""
    from narad_agent import build_narad_agent as _build
    narad = _build(user_id=user_id)
    svc = InMemorySessionService()
    return Runner(agent=narad, app_name="avatara", session_service=svc)


class ChatRequest(BaseModel):
    query: str
    session_id: str | None = None
    user_id: str = "default"


@app.get("/health")
async def health():
    return {
        "status": "ok",
        "agent": "Narad",
        "phase": "1",
        "model": _narad.model.model,
    }


@app.post("/chat")
async def chat(req: ChatRequest):
    if not req.query.strip():
        raise HTTPException(status_code=400, detail="query cannot be empty")

    session_id = req.session_id or str(uuid.uuid4())
    runner = _rebuild_runner_for_user(req.user_id)

    tracer = Tracer(session_id=session_id, user_id=req.user_id)

    async def event_stream():
        try:
            existing = await runner.session_service.get_session(
                app_name="avatara", user_id=req.user_id, session_id=session_id
            )
            if existing is None:
                await runner.session_service.create_session(
                    app_name="avatara", user_id=req.user_id, session_id=session_id
                )

            from google.genai import types as genai_types

            user_message = genai_types.Content(
                role="user", parts=[genai_types.Part(text=req.query)]
            )

            tracer.session_start(req.query)

            async for event in runner.run_async(
                user_id=req.user_id, session_id=session_id, new_message=user_message
            ):
                yield _event_to_sse(event)

            tracer.session_done()
            yield json.dumps({"type": "done", "data": {"session_id": session_id}})

        except Exception as exc:
            yield json.dumps({"type": "error", "data": {"message": str(exc)}})

    return EventSourceResponse(event_stream())


@app.get("/trace/{session_id}")
async def get_trace(session_id: str):
    events = Tracer.load(session_id)
    if not events:
        raise HTTPException(status_code=404, detail="No trace found for session")
    return {"session_id": session_id, "events": events, "summary": Tracer.summary(session_id)}


@app.get("/sutras")
async def get_sutras():
    from tapas import sutra_summary, load_sutras
    sutras = load_sutras()
    return {"summary": sutra_summary(), "recent": sutras[-10:]}


def _event_to_sse(event: Event) -> str:
    payload: dict = {"type": "unknown", "data": {}}

    if event.is_final_response():
        text = ""
        if event.content and event.content.parts:
            text = "".join(p.text or "" for p in event.content.parts)
        payload = {"type": "narad_synthesis", "data": {"text": text}}

    elif event.content and event.content.parts:
        for part in event.content.parts:
            if part.function_call:
                fc = part.function_call
                avatar = _resolve_avatar(fc.name)
                payload = {
                    "type": "avatar_start",
                    "data": {
                        "avatar": avatar,
                        "task": (fc.args or {}).get("request", ""),
                    },
                }
            elif part.function_response:
                fr = part.function_response
                avatar = _resolve_avatar(fr.name)
                payload = {
                    "type": "avatar_done",
                    "data": {"avatar": avatar, "result": fr.response},
                }
            elif part.text:
                payload = {"type": "narad_synthesis", "data": {"text": part.text}}

    return json.dumps(payload)


def _resolve_avatar(tool_name: str) -> str:
    lower = tool_name.lower()
    # phase-1: invoke_matsya → Matsya; phase-0b compat: matsya → Matsya
    return AGENT_TOOL_NAMES.get(lower, AGENT_TOOL_NAMES.get(lower.replace("invoke_", ""), tool_name.capitalize()))
