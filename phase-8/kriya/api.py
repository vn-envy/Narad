"""``/tasks``: watch, stop and help Kriya tasks from the phone.

GET  /tasks                      this profile's tasks, newest first (?status=running,…)
GET  /tasks/{id}                 one task with its step list, and its approval while one waits
POST /tasks/{id}/cancel          stop it on the server, within one step
POST /tasks/{id}/resume          continue after a sign-in or captcha (waiting_help only)
POST /tasks/{id}/takeover        tap, type, key, scroll or back on the page (waiting_help only)
GET  /tasks/{id}/frame           the latest viewport as a JPEG (1-2 fps polling; never cached)

Every route resolves the caller with the server's identity helper, and a
task is looked up only in the caller's own store, so another profile's task
id is simply not found (404). Typed takeover text is never logged.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Callable, Literal, Optional

from fastapi import APIRouter, HTTPException, Request, Response
from kriya import store
from pydantic import BaseModel, Field

log = logging.getLogger("narad.kriya")

_NO_STORE = {"Cache-Control": "no-store, max-age=0", "Pragma": "no-cache"}


class TakeoverRequest(BaseModel):
    kind: Literal["click", "type", "key", "scroll", "back"]
    x: float = Field(default=0.0, ge=0.0, le=1.0)  # a fraction of the frame's width
    y: float = Field(default=0.0, ge=0.0, le=1.0)
    text: str = Field(default="", max_length=500)
    key: str = Field(default="", max_length=20)
    direction: Literal["up", "down"] = "down"


def _approval_payload(task: store.Task) -> Optional[dict[str, Any]]:
    if not task.proposal_id or task.status != "waiting_approval":
        return None
    try:
        import anumati

        return anumati.get(task.proposal_id, profile_id=task.profile_id).to_payload()
    except Exception:
        return None


def task_payload(task: store.Task, *, events: bool = True) -> dict[str, Any]:
    payload = task.to_payload(store.list_events(task.task_id, profile_id=task.profile_id) if events else None)
    approval = _approval_payload(task)
    if approval is not None:
        payload["approval"] = approval
    return payload


def build_tasks_router(identity: Callable[[Request, Optional[str]], str]) -> APIRouter:
    """The routes, with ``identity(request, None)`` as the caller's profile."""
    from kriya.runtime import runtime

    router = APIRouter(tags=["tasks"])

    def _owned(request: Request, task_id: str) -> tuple[str, store.Task]:
        profile_id = identity(request, None)
        try:
            return profile_id, store.get_task(task_id, profile_id=profile_id)
        except store.TaskNotFound as exc:
            raise HTTPException(status_code=404, detail="Task not found") from exc

    @router.get("/tasks")
    async def list_tasks(request: Request, status: Optional[str] = None, limit: int = 30) -> dict[str, Any]:
        profile_id = identity(request, None)
        try:
            tasks = await asyncio.to_thread(store.list_tasks, profile_id=profile_id, status=status, limit=limit)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return {"tasks": [task.to_payload() for task in tasks]}

    @router.get("/tasks/{task_id}")
    async def get_task(task_id: str, request: Request) -> dict[str, Any]:
        _profile_id, task = await asyncio.to_thread(_owned, request, task_id)
        return await asyncio.to_thread(task_payload, task)

    @router.post("/tasks/{task_id}/cancel")
    async def cancel_task(task_id: str, request: Request) -> dict[str, Any]:
        profile_id, _task = await asyncio.to_thread(_owned, request, task_id)
        task = await asyncio.to_thread(runtime().cancel, task_id, profile_id=profile_id)
        return await asyncio.to_thread(task_payload, task)

    @router.post("/tasks/{task_id}/resume")
    async def resume_task(task_id: str, request: Request) -> dict[str, Any]:
        profile_id, _task = await asyncio.to_thread(_owned, request, task_id)
        try:
            task = await asyncio.to_thread(runtime().resume, task_id, profile_id=profile_id)
        except PermissionError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return await asyncio.to_thread(task_payload, task)

    @router.post("/tasks/{task_id}/takeover")
    async def takeover(task_id: str, body: TakeoverRequest, request: Request) -> dict[str, Any]:
        profile_id, _task = await asyncio.to_thread(_owned, request, task_id)
        try:
            result = await asyncio.to_thread(
                runtime().takeover, task_id, profile_id=profile_id, kind=body.kind, x=body.x, y=body.y,
                text=body.text, key=body.key, direction=body.direction,
            )
        except PermissionError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except Exception as exc:
            log.warning("Kriya takeover failed: %s", type(exc).__name__)
            raise HTTPException(status_code=502, detail="The page did not respond") from exc
        return {"ok": True, "url": result.get("url"), "refused": result.get("refused")}

    @router.get("/tasks/{task_id}/frame")
    async def frame(task_id: str, request: Request) -> Response:
        profile_id, _task = await asyncio.to_thread(_owned, request, task_id)
        try:
            data = await asyncio.to_thread(runtime().frame, task_id, profile_id=profile_id)
        except Exception:
            data = None
        if not data:
            return Response(status_code=204, headers=_NO_STORE)
        return Response(content=data, media_type="image/jpeg", headers=_NO_STORE)

    return router


def resume_tasks_after_restart() -> int:
    """Requeue unfinished tasks (server startup; runs off the event loop)."""
    from kriya.runtime import runtime

    try:
        count = runtime().resume_all()
    except Exception:
        log.warning("Kriya: resuming tasks failed", exc_info=True)
        return 0
    if count:
        log.info("Kriya: resumed %d unfinished task(s)", count)
    return count
