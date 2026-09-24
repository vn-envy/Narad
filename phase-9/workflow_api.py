"""HTTP contracts for Narad's predefined Workflow Paths."""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from workflow_engine import (
    StageNotDone,
    approve_pending_stage,
    bind_run_to_session,
    build_workflow_context,
    confirm_stage,
    export_run,
    get_pack,
    get_workflow_run,
    get_workflow_schedule,
    intake_prefill,
    intake_questions,
    list_workflow_definitions,
    list_workflow_runs,
    record_workflow_feedback,
    request_stage_confirmation,
    set_schedule_enabled,
    set_workflow_status,
    settle_active_runs,
    settle_run,
    skip_stage,
    start_workflow_run,
    update_workflow_inputs,
    workflow_run_payload,
)

workflow_router = APIRouter(tags=["workflows"])


class WorkflowStart(BaseModel):
    inputs: dict[str, Any] = Field(default_factory=dict)
    title: str | None = None
    # From a chat card: bind the run to that thread and let intake continue
    # there one or two questions at a time.
    session_id: str | None = Field(default=None, pattern=r"^[A-Za-z0-9_.-]{1,128}$")
    partial: bool = False


class WorkflowAction(BaseModel):
    action: str
    summary: str = ""
    payload: dict[str, Any] = Field(default_factory=dict)
    artifacts: list[dict[str, Any]] = Field(default_factory=list)
    citations: list[dict[str, Any]] = Field(default_factory=list)


class SchedulePatch(BaseModel):
    enabled: bool


def _owned_run(run_id: str, user_id: str):
    run = get_workflow_run(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Workflow run not found")
    if run.user_id != user_id:
        raise HTTPException(status_code=403, detail="Workflow belongs to another user")
    return run


def _as_http_error(exc: Exception) -> HTTPException:
    if isinstance(exc, KeyError):
        return HTTPException(status_code=404, detail=str(exc).strip("'"))
    if isinstance(exc, (PermissionError, StageNotDone)):
        return HTTPException(status_code=409, detail=str(exc))
    if isinstance(exc, RuntimeError):
        return HTTPException(status_code=503, detail=str(exc))
    return HTTPException(status_code=422, detail=str(exc))


@workflow_router.get("/workflows")
async def get_workflows() -> dict[str, Any]:
    return {"workflows": list_workflow_definitions()}


@workflow_router.get("/workflows/{workflow_id}/intake")
async def get_workflow_intake(workflow_id: str, user_id: str = "default") -> dict[str, Any]:
    """Prefilled starting values and the first one or two questions."""
    pack = get_pack(workflow_id)
    if not pack:
        raise HTTPException(status_code=404, detail="Unknown workflow")
    prefill = intake_prefill(workflow_id, user_id=user_id)
    return {**prefill, "questions": intake_questions(pack, prefill["values"])}


@workflow_router.post("/workflows/{workflow_id}/runs")
async def create_workflow_run(
    workflow_id: str,
    body: WorkflowStart,
    user_id: str = "default",
) -> dict[str, Any]:
    try:
        run = start_workflow_run(
            workflow_id, user_id=user_id, inputs=body.inputs, title=body.title,
            session_id=body.session_id, partial=body.partial,
        )
    except Exception as exc:
        raise _as_http_error(exc) from exc
    return {"ok": True, "event": "workflow_started", "run": workflow_run_payload(run)}


@workflow_router.get("/workflow-runs")
async def get_workflow_runs(
    user_id: str = "default",
    workflow_id: str | None = None,
    status: str | None = None,
    limit: int = 50,
) -> dict[str, Any]:
    # Evidence that landed since the last look (a review saved, a task done) first.
    await asyncio.to_thread(settle_active_runs, user_id=user_id)
    runs = list_workflow_runs(user_id=user_id, workflow_id=workflow_id, status=status, limit=limit)
    return {"user_id": user_id, "runs": [workflow_run_payload(run, include_history=False) for run in runs]}


@workflow_router.get("/workflow-runs/{run_id}")
async def get_workflow_run_route(run_id: str, user_id: str = "default") -> dict[str, Any]:
    run = _owned_run(run_id, user_id)
    run = await asyncio.to_thread(settle_run, run_id) or run
    return workflow_run_payload(run)


@workflow_router.get("/workflow-runs/{run_id}/context")
async def get_workflow_context_route(run_id: str, user_id: str = "default") -> dict[str, Any]:
    _owned_run(run_id, user_id)
    try:
        return {"run_id": run_id, "context": build_workflow_context(run_id, user_id=user_id)}
    except Exception as exc:
        raise _as_http_error(exc) from exc


@workflow_router.post("/workflow-runs/{run_id}/actions")
async def act_on_workflow_run(
    run_id: str,
    body: WorkflowAction,
    user_id: str = "default",
) -> dict[str, Any]:
    run = _owned_run(run_id, user_id)
    action = body.action.strip().lower()
    try:
        if action in {"pause", "resume", "cancel"}:
            status = {"pause": "paused", "resume": "active", "cancel": "cancelled"}[action]
            run = set_workflow_status(run_id, status)
        elif action == "update_inputs":
            run = update_workflow_inputs(run_id, body.payload)
        elif action == "feedback":
            event = str(body.payload.get("event") or "").strip()
            if not event:
                raise ValueError("feedback requires payload.event")
            details = body.payload.get("details")
            run = record_workflow_feedback(run_id, event, details=details if isinstance(details, dict) else {})
        elif action == "request_confirmation":
            # Off the loop: the approval request notifies (Vahana file I/O).
            run = await asyncio.to_thread(
                request_stage_confirmation, run_id, summary=body.summary, details=body.payload
            )
        elif action == "approve":
            # The stage's Anumati proposal is approved and carried out, so the
            # Paths screen and the approval card are one decision.
            run = await asyncio.to_thread(approve_pending_stage, run_id, approved_by=user_id)
        elif action in {"confirm", "complete", "advance"}:
            # The person's own "Done". It finishes only a stage whose done_when
            # accepts their word; anything else answers 409 with what is missing.
            run = await asyncio.to_thread(
                confirm_stage, run_id, user_id=user_id, note=str(body.payload.get("note") or body.summary or "")
            )
        elif action == "skip":
            run = await asyncio.to_thread(skip_stage, run_id, user_id=user_id)
        elif action == "bind":
            run = bind_run_to_session(run_id, user_id=user_id, session_id=str(body.payload.get("session_id") or ""))
        elif action == "export":
            run = await asyncio.to_thread(export_run, run_id, user_id=user_id)
        else:
            raise ValueError(f"Unknown workflow action: {body.action}")
    except Exception as exc:
        raise _as_http_error(exc) from exc
    event = {
        "pause": "workflow_paused",
        "resume": "workflow_resumed",
        "cancel": "workflow_cancelled",
        "feedback": "workflow_feedback",
        "request_confirmation": "workflow_waiting_for_user",
        "approve": "workflow_updated",
    }.get(action, "workflow_updated")
    return {"ok": True, "event": event, "run": workflow_run_payload(run)}


@workflow_router.patch("/workflow-schedules/{schedule_id}")
async def patch_workflow_schedule(
    schedule_id: str,
    body: SchedulePatch,
    user_id: str = "default",
) -> dict[str, Any]:
    existing = get_workflow_schedule(schedule_id)
    if not existing:
        raise HTTPException(status_code=404, detail="Workflow schedule not found")
    if existing.user_id != user_id:
        raise HTTPException(status_code=403, detail="Schedule belongs to another user")
    try:
        schedule = set_schedule_enabled(schedule_id, body.enabled)
    except Exception as exc:
        raise _as_http_error(exc) from exc
    return {"ok": True, "event": "workflow_updated", "schedule": schedule.to_dict()}
