from __future__ import annotations

import asyncio
from typing import Optional

import guided_mode
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from guru_engine import (
    due_reviews,
    generate_syllabus,
    grade_check_answer,
    load_learner_state,
)
from learning_workspace import (
    WORKSPACE_ID_PATTERN,
    append_learning_record,
    create_learning_artifact,
    ensure_workspace,
    list_artifacts,
    list_records,
    list_workspaces,
    load_artifact,
    load_artifact_version,
    load_workspace,
    merge_resources,
    update_glossary_terms,
    update_learning_artifact,
)

learning_router = APIRouter(prefix="/learning", tags=["learning"])


class LearningRecordCreate(BaseModel):
    title: str
    summary: str = ""
    body: str
    record_type: str = "lesson"
    session_id: Optional[str] = None
    tags: list[str] = Field(default_factory=list)
    source: str = "api"


class LearningWorkspaceCreate(BaseModel):
    topic: str
    mission: str = ""
    session_id: Optional[str] = None


class LearningResourcesUpdate(BaseModel):
    resources: list[dict]


class LearningGlossaryUpdate(BaseModel):
    entries: dict[str, str]


class LearningArtifactCreate(BaseModel):
    workspace_id: str = Field(pattern=WORKSPACE_ID_PATTERN)
    topic: str
    artifact_type: str
    teaching_context: str = ""
    record_ids: list[str] = Field(default_factory=list)


class LearningArtifactUpdate(BaseModel):
    instruction: str
    workspace_id: Optional[str] = Field(default=None, pattern=WORKSPACE_ID_PATTERN)
    record_ids: list[str] = Field(default_factory=list)


class SyllabusGenerate(BaseModel):
    topic: str = ""
    force: bool = False


class CheckAnswer(BaseModel):
    atom_id: str
    answer: str


class GuidedStart(BaseModel):
    topic: str
    mode: str = "teach"
    workflow_run_id: Optional[str] = None


class GuidedAnswer(BaseModel):
    workspace_id: str = Field(pattern=WORKSPACE_ID_PATTERN)
    answer: str = ""
    choice_index: Optional[int] = None
    workflow_run_id: Optional[str] = None


class GuidedWorkspaceRef(BaseModel):
    workspace_id: str = Field(pattern=WORKSPACE_ID_PATTERN)
    workflow_run_id: Optional[str] = None


def _resolve_teach_workflow(*, user_id: str, topic: str, run_id: Optional[str] = None):
    from workflow_engine import get_workflow_run, list_workflow_runs, start_workflow_run

    if run_id:
        run = get_workflow_run(run_id)
        if not run:
            raise HTTPException(status_code=404, detail="Teach workflow run not found")
        if run.user_id != user_id:
            raise HTTPException(status_code=403, detail="Teach workflow belongs to another user")
        if run.workflow_id != "teach":
            raise HTTPException(status_code=409, detail="The selected workflow is not a Teach Anything path")
        if run.status in {"completed", "cancelled"}:
            raise HTTPException(status_code=409, detail="Start a new Teach Anything path for this lesson")
        return run

    topic_key = topic.strip().casefold()
    for candidate in list_workflow_runs(user_id=user_id, workflow_id="teach", limit=50):
        if candidate.status in {"completed", "cancelled"}:
            continue
        if str(candidate.inputs.get("topic") or "").strip().casefold() == topic_key:
            return candidate
    return start_workflow_run(
        "teach",
        user_id=user_id,
        inputs={
            "topic": topic,
            "outcome": f"Build durable, usable understanding of {topic}",
            "current_level": "New",
            "mode": "Hybrid",
            "spaced_reviews": True,
        },
    )


def _sync_guided_checkpoint(run_id: Optional[str], result: dict, *, user_id: str, action: str):
    if not run_id:
        return None
    from workflow_engine import (
        get_workflow_run,
        record_guided_progress,
        record_workflow_checkpoint,
    )

    run = get_workflow_run(run_id)
    if not run:
        return None
    if run.user_id != user_id:
        raise HTTPException(status_code=403, detail="Teach workflow belongs to another user")
    if run.workflow_id != "teach" or run.status in {"completed", "cancelled"}:
        return run
    grade = result.get("grade") if isinstance(result.get("grade"), dict) else {}
    progress = (result.get("step") or {}).get("progress", {}) if isinstance(result.get("step"), dict) else {}
    details = {
        "action": action,
        "correct": grade.get("correct"),
        "feedback": grade.get("feedback"),
        "progress": progress,
    }
    # The graded answer (or skip) is the evidence: the Teach stages' done_when
    # accepts the guided loop's events, so the engine decides what finishes.
    run = record_workflow_checkpoint(
        run_id,
        summary=f"Guided learning checkpoint: {action}.",
        details=details,
        event_type="guided_learning_checkpoint",
    )
    if action in {"answer", "skip"}:
        run = record_guided_progress(run_id, user_id=user_id, event=action, details=details)

    step = result.get("step") if isinstance(result.get("step"), dict) else {}
    if step.get("kind") == "complete":
        for _ in range(8):
            if not run or run.status == "completed" or not run.current_stage_id:
                break
            before = run.current_stage_id
            run = record_guided_progress(run_id, user_id=user_id, event="complete", details={"progress": progress})
            if run.current_stage_id == before:
                break
    return run


@learning_router.get("/workspaces")
async def get_learning_workspaces(user_id: str = "default"):
    return {"workspaces": list_workspaces(user_id)}


@learning_router.post("/workspaces")
async def post_learning_workspace(payload: LearningWorkspaceCreate, user_id: str = "default"):
    workspace = ensure_workspace(
        user_id=user_id,
        topic=payload.topic,
        mission=payload.mission,
        session_id=payload.session_id,
    )
    return {"status": "ok", "workspace": workspace}


@learning_router.get("/workspaces/{workspace_id}")
async def get_learning_workspace(workspace_id: str, user_id: str = "default"):
    workspace = load_workspace(user_id=user_id, workspace_id=workspace_id)
    if not workspace:
        raise HTTPException(status_code=404, detail="learning workspace not found")
    return workspace


@learning_router.get("/workspaces/{workspace_id}/records")
async def get_learning_records(workspace_id: str, user_id: str = "default", limit: int = 50):
    workspace = load_workspace(user_id=user_id, workspace_id=workspace_id)
    if not workspace:
        raise HTTPException(status_code=404, detail="learning workspace not found")
    return {"workspace_id": workspace_id, "records": list_records(user_id=user_id, workspace_id=workspace_id, limit=limit)}


@learning_router.post("/workspaces/{workspace_id}/records")
async def post_learning_record(workspace_id: str, payload: LearningRecordCreate, user_id: str = "default"):
    try:
        record = append_learning_record(
            user_id=user_id,
            workspace_id=workspace_id,
            title=payload.title,
            summary=payload.summary,
            body=payload.body,
            record_type=payload.record_type,
            session_id=payload.session_id,
            tags=payload.tags,
            source=payload.source,
        )
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="learning workspace not found")
    return {"status": "ok", "record": record}


@learning_router.post("/workspaces/{workspace_id}/resources")
async def post_learning_resources(workspace_id: str, payload: LearningResourcesUpdate, user_id: str = "default"):
    try:
        workspace = merge_resources(user_id=user_id, workspace_id=workspace_id, resources=payload.resources)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="learning workspace not found")
    return {"status": "ok", "workspace": workspace}


@learning_router.post("/workspaces/{workspace_id}/glossary")
async def post_learning_glossary(workspace_id: str, payload: LearningGlossaryUpdate, user_id: str = "default"):
    try:
        workspace = update_glossary_terms(user_id=user_id, workspace_id=workspace_id, entries=payload.entries)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="learning workspace not found")
    return {"status": "ok", "workspace": workspace}


@learning_router.get("/workspaces/{workspace_id}/artifacts")
async def get_learning_artifacts(workspace_id: str, user_id: str = "default", limit: int = 20):
    workspace = load_workspace(user_id=user_id, workspace_id=workspace_id)
    if not workspace:
        raise HTTPException(status_code=404, detail="learning workspace not found")
    return {"workspace_id": workspace_id, "artifacts": list_artifacts(user_id=user_id, workspace_id=workspace_id, limit=limit)}


@learning_router.get("/artifacts/{artifact_id}")
async def get_learning_artifact(
    artifact_id: str,
    user_id: str = "default",
    workspace_id: Optional[str] = Query(default=None, pattern=WORKSPACE_ID_PATTERN),
):
    artifact = load_artifact(user_id=user_id, artifact_id=artifact_id, workspace_id=workspace_id)
    if not artifact:
        raise HTTPException(status_code=404, detail="learning artifact not found")
    return artifact


@learning_router.post("/artifacts")
async def post_learning_artifact(payload: LearningArtifactCreate, user_id: str = "default"):
    try:
        artifact = await asyncio.to_thread(  # LLM-backed: never on the event loop
            create_learning_artifact,
            user_id=user_id,
            workspace_id=payload.workspace_id,
            topic=payload.topic,
            artifact_type=payload.artifact_type,
            teaching_context=payload.teaching_context,
            record_ids=payload.record_ids,
        )
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="learning workspace not found")
    return {"status": "ok", "artifact": artifact}


# ── Gurukul (G1/G3): syllabus + mastery ───────────────────────────────────────

@learning_router.post("/workspaces/{workspace_id}/syllabus")
async def post_learning_syllabus(workspace_id: str, payload: SyllabusGenerate, user_id: str = "default"):
    workspace = load_workspace(user_id=user_id, workspace_id=workspace_id)
    if not workspace:
        raise HTTPException(status_code=404, detail="learning workspace not found")
    syllabus = await asyncio.to_thread(
        generate_syllabus,
        user_id=user_id,
        workspace_id=workspace_id,
        topic=payload.topic.strip() or str(workspace.get("topic", "")),
        force=payload.force,
    )
    return {"status": "ok", "syllabus": syllabus}


@learning_router.post("/workspaces/{workspace_id}/check")
async def post_learning_check(workspace_id: str, payload: CheckAnswer, user_id: str = "default"):
    workspace = load_workspace(user_id=user_id, workspace_id=workspace_id)
    if not workspace:
        raise HTTPException(status_code=404, detail="learning workspace not found")
    grade = await asyncio.to_thread(
        grade_check_answer,
        user_id=user_id,
        workspace_id=workspace_id,
        atom_id=payload.atom_id,
        answer=payload.answer,
    )
    return {"status": "ok", **grade}


@learning_router.get("/workspaces/{workspace_id}/state")
async def get_learning_state(workspace_id: str, user_id: str = "default"):
    return {
        "workspace_id": workspace_id,
        "learner_state": load_learner_state(user_id=user_id, workspace_id=workspace_id),
        "due_reviews": due_reviews(user_id=user_id, workspace_id=workspace_id),
    }


@learning_router.get("/artifacts/{artifact_id}/versions/{version}")
async def get_learning_artifact_version(
    artifact_id: str,
    version: int,
    workspace_id: str = Query(pattern=WORKSPACE_ID_PATTERN),
    user_id: str = "default",
):
    artifact = load_artifact_version(
        user_id=user_id,
        workspace_id=workspace_id,
        artifact_id=artifact_id,
        version=version,
    )
    if not artifact:
        raise HTTPException(status_code=404, detail="artifact version not found")
    return artifact


@learning_router.post("/artifacts/{artifact_id}/update")
async def post_learning_artifact_update(artifact_id: str, payload: LearningArtifactUpdate, user_id: str = "default"):
    try:
        artifact = await asyncio.to_thread(
            update_learning_artifact,
            user_id=user_id,
            artifact_id=artifact_id,
            instruction=payload.instruction,
            workspace_id=payload.workspace_id,
            record_ids=payload.record_ids,
        )
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="learning artifact not found")
    return {"status": "ok", "artifact": artifact}


# ── Guided mode (G7): /teach's step-by-step session loop ─────────────────────

@learning_router.get("/guided/modes")
async def get_guided_modes():
    return {
        "modes": [
            {"mode": name, "label": cfg["label"], "avatar": cfg["avatar"]}
            for name, cfg in guided_mode.MODES.items()
        ]
    }


@learning_router.post("/guided/start")
async def post_guided_start(payload: GuidedStart, user_id: str = "default"):
    workflow_run = _resolve_teach_workflow(
        user_id=user_id,
        topic=payload.topic,
        run_id=payload.workflow_run_id,
    )
    try:
        result = await asyncio.to_thread(  # may generate a syllabus via an LLM
            guided_mode.start_session,
            user_id=user_id, mode=payload.mode, topic=payload.topic,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    from workflow_engine import record_workflow_checkpoint, workflow_run_payload

    workflow_run = record_workflow_checkpoint(
        workflow_run.run_id,
        summary="Guided teaching session opened.",
        details={"workspace_id": result.get("session", {}).get("workspace_id"), "resumed": result.get("resumed", False)},
        event_type="guided_learning_started",
    )
    return {"status": "ok", **result, "workflow_run": workflow_run_payload(workflow_run, include_history=False)}


@learning_router.get("/guided/session/{workspace_id}")
async def get_guided_session(workspace_id: str, user_id: str = "default"):
    result = guided_mode.get_session(user_id=user_id, workspace_id=workspace_id)
    if result is None:
        raise HTTPException(status_code=404, detail="no guided session for this workspace")
    return {"status": "ok", **result}


@learning_router.post("/guided/answer")
async def post_guided_answer(payload: GuidedAnswer, user_id: str = "default"):
    try:
        result = await asyncio.to_thread(  # grades through an LLM
            guided_mode.submit_answer,
            user_id=user_id,
            workspace_id=payload.workspace_id,
            answer=payload.answer,
            choice_index=payload.choice_index,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    workflow_run = _sync_guided_checkpoint(payload.workflow_run_id, result, user_id=user_id, action="answer")
    if workflow_run:
        from workflow_engine import workflow_run_payload

        result["workflow_run"] = workflow_run_payload(workflow_run, include_history=False)
    return {"status": "ok", **result}


@learning_router.post("/guided/skip")
async def post_guided_skip(payload: GuidedWorkspaceRef, user_id: str = "default"):
    try:
        result = guided_mode.skip_atom(user_id=user_id, workspace_id=payload.workspace_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    workflow_run = _sync_guided_checkpoint(payload.workflow_run_id, result, user_id=user_id, action="skip")
    if workflow_run:
        from workflow_engine import workflow_run_payload

        result["workflow_run"] = workflow_run_payload(workflow_run, include_history=False)
    return {"status": "ok", **result}


@learning_router.post("/guided/exit")
async def post_guided_exit(payload: GuidedWorkspaceRef, user_id: str = "default"):
    try:
        result = guided_mode.exit_session(user_id=user_id, workspace_id=payload.workspace_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    workflow_run = _sync_guided_checkpoint(payload.workflow_run_id, result, user_id=user_id, action="exit")
    if workflow_run:
        from workflow_engine import workflow_run_payload

        result["workflow_run"] = workflow_run_payload(workflow_run, include_history=False)
    return {"status": "ok", **result}
