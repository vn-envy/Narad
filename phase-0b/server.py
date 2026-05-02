"""
Phase 0b FastAPI SSE server.

Endpoints:
  POST /chat          Start a conversation turn; streams SSE events
  GET  /health        Liveness check

SSE event schema (each event is JSON):
  { "type": "narad_routing",  "data": { "avatars": [...], "mode": "...", "rationale": "..." } }
  { "type": "avatar_start",   "data": { "avatar": "Matsya", "task": "..." } }
  { "type": "avatar_chunk",   "data": { "avatar": "Matsya", "text": "..." } }
  { "type": "avatar_done",    "data": { "avatar": "Matsya", "result": {...} } }
  { "type": "narad_synthesis","data": { "text": "..." } }  (streamed chunks)
  { "type": "done",           "data": {} }
  { "type": "error",          "data": { "message": "..." } }

This event taxonomy is what Phase 3 (Yantra live call graph) subscribes to.
Locking it here means the frontend can start being built in parallel.
"""

from __future__ import annotations

import asyncio
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
import google.adk.models as _models  # noqa: F401 — triggers model registration

from narad_agent import build_narad_agent

app = FastAPI(title="Avatara — Narad API", version="0.0.1-phase0b")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Build agent + runner once at startup (shared across requests)
_session_service = InMemorySessionService()
_narad = build_narad_agent(
    model=os.environ.get("NARAD_MODEL", "openai/gpt-4o")
)
_runner = Runner(
    agent=_narad,
    app_name="avatara",
    session_service=_session_service,
)


class ChatRequest(BaseModel):
    query: str
    session_id: str | None = None


@app.get("/health")
async def health():
    return {"status": "ok", "agent": "Narad", "model": _narad.model.model}


@app.post("/chat")
async def chat(req: ChatRequest):
    if not req.query.strip():
        raise HTTPException(status_code=400, detail="query cannot be empty")

    session_id = req.session_id or str(uuid.uuid4())

    async def event_stream():
        try:
            # Ensure session exists
            existing = await _session_service.get_session(
                app_name="avatara",
                user_id="user",
                session_id=session_id,
            )
            if existing is None:
                await _session_service.create_session(
                    app_name="avatara",
                    user_id="user",
                    session_id=session_id,
                )

            from google.genai import types as genai_types

            user_message = genai_types.Content(
                role="user",
                parts=[genai_types.Part(text=req.query)],
            )

            async for event in _runner.run_async(
                user_id="user",
                session_id=session_id,
                new_message=user_message,
            ):
                yield _event_to_sse(event)

            yield json.dumps({"type": "done", "data": {}})

        except Exception as exc:
            yield json.dumps({"type": "error", "data": {"message": str(exc)}})

    return EventSourceResponse(event_stream())


def _event_to_sse(event: Event) -> str:
    """Map an ADK Event to our SSE event schema."""
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
                avatar = _tool_to_avatar(fc.name)
                payload = {
                    "type": "avatar_start",
                    "data": {
                        "avatar": avatar,
                        "task": (fc.args or {}).get("task", ""),
                    },
                }
            elif part.function_response:
                fr = part.function_response
                avatar = _tool_to_avatar(fr.name)
                payload = {
                    "type": "avatar_done",
                    "data": {
                        "avatar": avatar,
                        "result": fr.response,
                    },
                }
            elif part.text:
                payload = {
                    "type": "narad_synthesis",
                    "data": {"text": part.text},
                }

    return json.dumps(payload)


_TOOL_AVATAR_MAP = {
    "invoke_matsya": "Matsya",
    "invoke_varaha": "Varaha",
    "invoke_narasimha": "Narasimha",
    "invoke_rama": "Rama",
    "invoke_krishna": "Krishna",
    "invoke_buddha": "Buddha",
    "invoke_parashurama": "Parashurama",
}


def _tool_to_avatar(tool_name: str) -> str:
    return _TOOL_AVATAR_MAP.get(tool_name, tool_name)
