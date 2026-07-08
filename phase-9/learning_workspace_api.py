from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from guru_engine import (
    due_reviews,
    generate_syllabus,
    grade_check_answer,
    load_learner_state,
)
from learning_workspace import (
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
    workspace_id: str
    topic: str
    artifact_type: str
    teaching_context: str = ""
    record_ids: list[str] = Field(default_factory=list)


class LearningArtifactUpdate(BaseModel):
    instruction: str
    workspace_id: Optional[str] = None
    record_ids: list[str] = Field(default_factory=list)


class SyllabusGenerate(BaseModel):
    topic: str = ""
    force: bool = False


class CheckAnswer(BaseModel):
    atom_id: str
    answer: str


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
async def get_learning_artifact(artifact_id: str, user_id: str = "default", workspace_id: Optional[str] = None):
    artifact = load_artifact(user_id=user_id, artifact_id=artifact_id, workspace_id=workspace_id)
    if not artifact:
        raise HTTPException(status_code=404, detail="learning artifact not found")
    return artifact


@learning_router.post("/artifacts")
async def post_learning_artifact(payload: LearningArtifactCreate, user_id: str = "default"):
    try:
        artifact = create_learning_artifact(
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
    syllabus = generate_syllabus(
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
    grade = grade_check_answer(
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
    workspace_id: str,
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
        artifact = update_learning_artifact(
            user_id=user_id,
            artifact_id=artifact_id,
            instruction=payload.instruction,
            workspace_id=payload.workspace_id,
            record_ids=payload.record_ids,
        )
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="learning artifact not found")
    return {"status": "ok", "artifact": artifact}
