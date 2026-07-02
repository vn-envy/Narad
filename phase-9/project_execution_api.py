from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from project_manager import get_project
from project_session_info import session_info as _fast_session_info
from project_tasks import create_task, get_task, get_task_workspace_root, list_tasks, task_summary, update_task
from smriti_v2 import get_wiki_pages

project_execution_router = APIRouter(prefix="/projects", tags=["project-execution"])
tasks_router = APIRouter(prefix="/tasks", tags=["tasks"])


class TaskCreate(BaseModel):
    title: str
    description: str = ""
    status: str = "todo"
    priority: str = "medium"
    owner: Optional[str] = None
    kind: str = "follow_up"
    source_session_id: Optional[str] = None
    blocked_by: List[str] = []
    artifact_refs: List[Dict[str, Any]] = []
    sort_order: int = 0


class TaskPatch(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    status: Optional[str] = None
    priority: Optional[str] = None
    owner: Optional[str] = None
    kind: Optional[str] = None
    blocked_by: Optional[List[str]] = None
    artifact_refs: Optional[List[Dict[str, Any]]] = None
    sort_order: Optional[int] = None


def _session_info(session_id: str) -> dict[str, Any]:
    return _fast_session_info(session_id)


def _workspace_payload(user_id: str, project_id: str) -> dict[str, Any]:
    project = get_project(user_id, project_id)
    if not project:
        raise HTTPException(status_code=404, detail=f"Project '{project_id}' not found")

    session_ids = list(project.get("session_ids", []))
    active_session_id = project.get("active_session_id")
    candidate_session_ids = session_ids[-8:]
    if active_session_id and active_session_id not in candidate_session_ids:
        candidate_session_ids.append(active_session_id)
    candidate_session_ids = list(dict.fromkeys(candidate_session_ids))

    sessions = [_session_info(sid) for sid in candidate_session_ids]
    sessions.sort(key=lambda item: item.get("ts") or "", reverse=True)

    pages = get_wiki_pages(user_id, project_id)
    tasks = list_tasks(project_id)
    summary = task_summary(project_id)
    anchors = [
        {
            "entity": page["entity"],
            "preview": page["preview"],
            "size_chars": page["size_chars"],
        }
        for page in pages[:5]
    ]

    current_goal = None
    for task in tasks:
        if task.kind == "goal":
            current_goal = task.title
            break
    if not current_goal:
        current_goal = sessions[0]["query"] if sessions else None
    if not current_goal:
        current_goal = anchors[0]["preview"] if anchors else None

    derived_active_session_id = None
    if summary["now"]:
        derived_active_session_id = summary["now"][0].get("source_session_id")
    if not derived_active_session_id:
        derived_active_session_id = active_session_id
    if not derived_active_session_id and sessions:
        derived_active_session_id = sessions[0]["session_id"]

    active_session = next((session for session in sessions if session["session_id"] == derived_active_session_id), None)
    if active_session is None and derived_active_session_id:
        active_session = _session_info(derived_active_session_id)
        if active_session.get("session_id"):
            sessions = [active_session, *sessions]
            sessions = list({session["session_id"]: session for session in sessions}.values())
            sessions.sort(key=lambda item: item.get("ts") or "", reverse=True)

    return {
        "project": {
            "id": project["id"],
            "name": project["name"],
            "workspace_root": project.get("workspace_root"),
            "workspace_label": project.get("workspace_label"),
            "created_at": project.get("created_at"),
            "updated_at": project.get("last_activity_at") or (sessions[0].get("ts") if sessions else project.get("created_at")),
            "status": project.get("project_status", "active"),
            "project_status": project.get("project_status", "active"),
            "current_goal": current_goal,
            "active_session_id": derived_active_session_id,
            "session_count": len(session_ids),
            "last_activity_at": project.get("last_activity_at") or (sessions[0].get("ts") if sessions else project.get("created_at")),
        },
        "active_session": active_session,
        "recent_sessions": sessions[:8],
        "task_summary": summary,
        "memory_anchors": anchors,
        "avatars": sorted({avatar for session in sessions[:8] for avatar in session.get("avatars", [])}),
    }


@project_execution_router.get("/{user_id}/{project_id}/workspace")
async def get_project_workspace(user_id: str, project_id: str) -> dict[str, Any]:
    return _workspace_payload(user_id, project_id)


@project_execution_router.get("/{user_id}/{project_id}/tasks")
async def get_project_tasks(user_id: str, project_id: str, include_done: bool = True) -> dict[str, Any]:
    if not get_project(user_id, project_id):
        raise HTTPException(status_code=404, detail=f"Project '{project_id}' not found")
    tasks = [task.to_dict() for task in list_tasks(project_id, include_done=include_done)]
    return {"user_id": user_id, "project_id": project_id, "tasks": tasks}


@project_execution_router.post("/{user_id}/{project_id}/tasks")
async def create_project_task(user_id: str, project_id: str, body: TaskCreate) -> dict[str, Any]:
    if not get_project(user_id, project_id):
        raise HTTPException(status_code=404, detail=f"Project '{project_id}' not found")
    task = create_task(
        project_id=project_id,
        workspace_root=(get_project(user_id, project_id) or {}).get("workspace_root") or get_task_workspace_root(project_id),
        title=body.title,
        description=body.description,
        status=body.status,
        priority=body.priority,
        owner=body.owner,
        kind=body.kind,
        source_session_id=body.source_session_id,
        blocked_by=body.blocked_by,
        artifact_refs=body.artifact_refs,
        sort_order=body.sort_order,
    )
    return {"ok": True, "task": task.to_dict()}


@project_execution_router.get("/{user_id}/{project_id}/execution")
async def get_project_execution(user_id: str, project_id: str) -> dict[str, Any]:
    payload = _workspace_payload(user_id, project_id)
    summary = payload["task_summary"]
    active_session = payload["active_session"]
    recent_events: list[dict[str, Any]] = []
    if active_session:
        try:
            from yantra import Tracer
            events = Tracer.load(active_session["session_id"])
            recent_events = [
                {
                    "ts": event.get("ts"),
                    "event": event.get("event"),
                    "avatar": event.get("avatar"),
                    "task": event.get("task") or event.get("result") or event.get("trigger"),
                }
                for event in events[-12:]
            ]
        except Exception:
            recent_events = []
    return {
        "project_id": project_id,
        "project_name": payload["project"]["name"],
        "workspace_root": payload["project"].get("workspace_root"),
        "workspace_label": payload["project"].get("workspace_label"),
        "current_goal": payload["project"]["current_goal"],
        "active_session": active_session,
        "now": summary["now"],
        "next": summary["next"],
        "blocked": summary["blocked"],
        "recent_done": summary["recent_done"],
        "artifacts": [
            artifact
            for task in (summary["now"] + summary["recent_done"])[:6]
            for artifact in task.get("artifact_refs", [])
        ],
        "active_agents": payload["avatars"],
        "recent_events": recent_events,
    }


@tasks_router.patch("/{task_id}")
async def patch_task(task_id: str, body: TaskPatch) -> dict[str, Any]:
    updated = update_task(
        task_id,
        title=body.title,
        description=body.description,
        status=body.status,
        priority=body.priority,
        owner=body.owner,
        kind=body.kind,
        blocked_by=body.blocked_by,
        artifact_refs=body.artifact_refs,
        sort_order=body.sort_order,
    )
    if not updated:
        raise HTTPException(status_code=404, detail="Task not found")
    return {"ok": True, "task": updated.to_dict()}


@tasks_router.post("/{task_id}/complete")
async def complete_task(task_id: str) -> dict[str, Any]:
    updated = update_task(task_id, status="done")
    if not updated:
        raise HTTPException(status_code=404, detail="Task not found")
    return {"ok": True, "task": updated.to_dict()}


@tasks_router.post("/{task_id}/block")
async def block_task(task_id: str) -> dict[str, Any]:
    updated = update_task(task_id, status="blocked")
    if not updated:
        raise HTTPException(status_code=404, detail="Task not found")
    return {"ok": True, "task": updated.to_dict()}


@tasks_router.post("/{task_id}/resume")
async def resume_task(task_id: str) -> dict[str, Any]:
    existing = get_task(task_id)
    if not existing:
        raise HTTPException(status_code=404, detail="Task not found")
    next_status = "in_progress" if existing.owner else "todo"
    updated = update_task(task_id, status=next_status)
    return {"ok": True, "task": updated.to_dict() if updated else None}
