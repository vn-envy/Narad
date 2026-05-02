"""
Phase 1 FastAPI SSE server.

Same event taxonomy as Phase 0b (locked contract):
  avatar_start | avatar_done | narad_synthesis | done | error
"""

from __future__ import annotations

import json
import os
import uuid

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.adk.events import Event

from narad_agent import build_narad_agent
from avatar_agents import AGENT_TOOL_NAMES

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


class ChatRequest(BaseModel):
    query: str
    session_id: str | None = None


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

    async def event_stream():
        try:
            existing = await _session_service.get_session(
                app_name="avatara", user_id="user", session_id=session_id
            )
            if existing is None:
                await _session_service.create_session(
                    app_name="avatara", user_id="user", session_id=session_id
                )

            from google.genai import types as genai_types

            user_message = genai_types.Content(
                role="user", parts=[genai_types.Part(text=req.query)]
            )

            async for event in _runner.run_async(
                user_id="user", session_id=session_id, new_message=user_message
            ):
                yield _event_to_sse(event)

            yield json.dumps({"type": "done", "data": {}})

        except Exception as exc:
            yield json.dumps({"type": "error", "data": {"message": str(exc)}})

    return EventSourceResponse(event_stream())


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
    """Map tool name to display name — handles both phase-0b and phase-1 patterns."""
    lower = tool_name.lower().replace("invoke_", "")
    return AGENT_TOOL_NAMES.get(lower, tool_name.capitalize())
