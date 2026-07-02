from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

import sys

_ROOT = Path(__file__).parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from narad_config import NARAD_HOME
from plan_models import Plan, PlanStep

TASK_DB = NARAD_HOME / "tasks.db"

_TABLE_DDL = """
CREATE TABLE IF NOT EXISTS tasks (
    task_id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    workspace_root TEXT,
    source_session_id TEXT,
    title TEXT NOT NULL,
    description TEXT,
    status TEXT NOT NULL,
    priority TEXT NOT NULL DEFAULT 'medium',
    owner TEXT,
    kind TEXT NOT NULL DEFAULT 'plan_step',
    blocked_by TEXT,
    artifact_refs TEXT,
    sort_order INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    completed_at TEXT
);
"""

_INDEX_DDL = (
    "CREATE INDEX IF NOT EXISTS tasks_project_idx ON tasks (project_id, updated_at DESC)",
    "CREATE INDEX IF NOT EXISTS tasks_workspace_idx ON tasks (workspace_root, updated_at DESC)",
    "CREATE INDEX IF NOT EXISTS tasks_session_idx ON tasks (source_session_id)",
    "CREATE INDEX IF NOT EXISTS tasks_status_idx ON tasks (status)",
)


def _ensure_schema(con: sqlite3.Connection) -> None:
    con.execute(_TABLE_DDL)
    cols = {row["name"] for row in con.execute("PRAGMA table_info(tasks)").fetchall()}
    if "workspace_root" not in cols:
        con.execute("ALTER TABLE tasks ADD COLUMN workspace_root TEXT")
    for ddl in _INDEX_DDL:
        con.execute(ddl)
    con.commit()


def _conn() -> sqlite3.Connection:
    con = sqlite3.connect(str(TASK_DB), check_same_thread=False)
    con.row_factory = sqlite3.Row
    _ensure_schema(con)
    return con


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class ProjectTask:
    task_id: str
    project_id: str
    workspace_root: str | None
    source_session_id: str | None
    title: str
    description: str
    status: str
    priority: str
    owner: str | None
    kind: str
    blocked_by: list[str]
    artifact_refs: list[dict[str, Any]]
    sort_order: int
    created_at: str
    updated_at: str
    completed_at: str | None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _row_to_task(row: sqlite3.Row) -> ProjectTask:
    return ProjectTask(
        task_id=row["task_id"],
        project_id=row["project_id"],
        workspace_root=row["workspace_root"],
        source_session_id=row["source_session_id"],
        title=row["title"],
        description=row["description"] or "",
        status=row["status"],
        priority=row["priority"],
        owner=row["owner"],
        kind=row["kind"],
        blocked_by=json.loads(row["blocked_by"] or "[]"),
        artifact_refs=json.loads(row["artifact_refs"] or "[]"),
        sort_order=row["sort_order"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        completed_at=row["completed_at"],
    )


def list_tasks(project_id: str, *, include_done: bool = True) -> list[ProjectTask]:
    query = "SELECT * FROM tasks WHERE project_id=?"
    params: list[Any] = [project_id]
    if not include_done:
        query += " AND status != 'done'"
    query += " ORDER BY CASE status WHEN 'in_progress' THEN 0 WHEN 'review' THEN 1 WHEN 'blocked' THEN 2 WHEN 'todo' THEN 3 WHEN 'done' THEN 4 ELSE 5 END, sort_order ASC, updated_at DESC"
    with _conn() as con:
        rows = con.execute(query, params).fetchall()
    return [_row_to_task(row) for row in rows]


def get_task(task_id: str) -> ProjectTask | None:
    with _conn() as con:
        row = con.execute("SELECT * FROM tasks WHERE task_id=?", (task_id,)).fetchone()
    return _row_to_task(row) if row else None


def create_task(
    project_id: str,
    title: str,
    *,
    workspace_root: str | None = None,
    description: str = "",
    status: str = "todo",
    priority: str = "medium",
    owner: str | None = None,
    kind: str = "follow_up",
    source_session_id: str | None = None,
    blocked_by: list[str] | None = None,
    artifact_refs: list[dict[str, Any]] | None = None,
    sort_order: int = 0,
) -> ProjectTask:
    now = _now()
    task = ProjectTask(
        task_id=f"task_{uuid4().hex[:10]}",
        project_id=project_id,
        workspace_root=workspace_root,
        source_session_id=source_session_id,
        title=title.strip(),
        description=description.strip(),
        status=status,
        priority=priority,
        owner=owner,
        kind=kind,
        blocked_by=blocked_by or [],
        artifact_refs=artifact_refs or [],
        sort_order=sort_order,
        created_at=now,
        updated_at=now,
        completed_at=now if status == "done" else None,
    )
    with _conn() as con:
        con.execute(
            """
            INSERT INTO tasks (
                task_id, project_id, source_session_id, title, description, status, priority,
                owner, kind, blocked_by, artifact_refs, sort_order, created_at, updated_at, completed_at, workspace_root
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                task.task_id,
                task.project_id,
                task.source_session_id,
                task.title,
                task.description,
                task.status,
                task.priority,
                task.owner,
                task.kind,
                json.dumps(task.blocked_by),
                json.dumps(task.artifact_refs),
                task.sort_order,
                task.created_at,
                task.updated_at,
                task.completed_at,
                task.workspace_root,
            ),
        )
    return task


def update_task(
    task_id: str,
    *,
    title: str | None = None,
    description: str | None = None,
    status: str | None = None,
    priority: str | None = None,
    owner: str | None = None,
    kind: str | None = None,
    blocked_by: list[str] | None = None,
    artifact_refs: list[dict[str, Any]] | None = None,
    sort_order: int | None = None,
) -> ProjectTask | None:
    existing = get_task(task_id)
    if not existing:
        return None
    now = _now()
    next_status = status or existing.status
    completed_at = existing.completed_at
    if next_status == "done" and not completed_at:
        completed_at = now
    elif next_status != "done":
        completed_at = None
    updated = ProjectTask(
        task_id=existing.task_id,
        project_id=existing.project_id,
        workspace_root=existing.workspace_root,
        source_session_id=existing.source_session_id,
        title=title.strip() if title is not None else existing.title,
        description=description.strip() if description is not None else existing.description,
        status=next_status,
        priority=priority or existing.priority,
        owner=owner if owner is not None else existing.owner,
        kind=kind or existing.kind,
        blocked_by=blocked_by if blocked_by is not None else existing.blocked_by,
        artifact_refs=artifact_refs if artifact_refs is not None else existing.artifact_refs,
        sort_order=sort_order if sort_order is not None else existing.sort_order,
        created_at=existing.created_at,
        updated_at=now,
        completed_at=completed_at,
    )
    with _conn() as con:
        con.execute(
            """
            UPDATE tasks
            SET title=?, description=?, status=?, priority=?, owner=?, kind=?, blocked_by=?,
                artifact_refs=?, sort_order=?, updated_at=?, completed_at=?
            WHERE task_id=?
            """,
            (
                updated.title,
                updated.description,
                updated.status,
                updated.priority,
                updated.owner,
                updated.kind,
                json.dumps(updated.blocked_by),
                json.dumps(updated.artifact_refs),
                updated.sort_order,
                updated.updated_at,
                updated.completed_at,
                task_id,
            ),
        )
    return updated


def upsert_plan_tasks(project_id: str, source_session_id: str, plan: Plan) -> list[ProjectTask]:
    created: list[ProjectTask] = []
    now = _now()
    workspace_root: str | None = None
    try:
        from project_manager import load_projects
        for project in load_projects("default"):
            if project.get("id") == project_id:
                workspace_root = project.get("workspace_root")
                break
    except Exception:
        workspace_root = None
    with _conn() as con:
        for step in plan.steps:
            task_id = f"{project_id}:{source_session_id}:{step.step_id}"
            blocked_by = [f"{project_id}:{source_session_id}:{dep}" for dep in step.dependencies]
            description = step.expected_output.strip() or step.description.strip()
            status = "todo"
            if step.status.value == "in_progress":
                status = "in_progress"
            elif step.status.value == "review":
                status = "review"
            elif step.status.value == "done":
                status = "done"
            elif step.status.value == "blocked":
                status = "blocked"
            artifact_refs: list[dict[str, Any]] = []
            if step.result_digest:
                artifact_refs.append({"type": "digest", "text": step.result_digest})
            con.execute(
                """
                INSERT INTO tasks (
                    task_id, project_id, source_session_id, title, description, status, priority,
                    owner, kind, blocked_by, artifact_refs, sort_order, created_at, updated_at, completed_at, workspace_root
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(task_id) DO UPDATE SET
                    title=excluded.title,
                    description=excluded.description,
                    status=excluded.status,
                    owner=excluded.owner,
                    blocked_by=excluded.blocked_by,
                    artifact_refs=excluded.artifact_refs,
                    workspace_root=excluded.workspace_root,
                    updated_at=excluded.updated_at,
                    completed_at=excluded.completed_at
                """,
                (
                    task_id,
                    project_id,
                    source_session_id,
                    step.description[:220],
                    description[:500],
                    status,
                    "medium",
                    step.owner,
                    "plan_step",
                    json.dumps(blocked_by),
                    json.dumps(artifact_refs),
                    step.step_id,
                    now,
                    now,
                    now if status == "done" else None,
                    workspace_root,
                ),
            )
            row = con.execute("SELECT * FROM tasks WHERE task_id=?", (task_id,)).fetchone()
            if row:
                created.append(_row_to_task(row))
    return created


def get_task_workspace_root(project_id: str) -> str | None:
    with _conn() as con:
        row = con.execute(
            "SELECT workspace_root FROM tasks WHERE project_id=? AND workspace_root IS NOT NULL ORDER BY updated_at DESC LIMIT 1",
            (project_id,),
        ).fetchone()
    if row and row["workspace_root"]:
        return str(row["workspace_root"])
    try:
        from project_manager import load_projects
        for project in load_projects("default"):
            if project.get("id") == project_id:
                return project.get("workspace_root")
    except Exception:
        return None
    return None


def sync_plan_step_status(
    project_id: str,
    source_session_id: str,
    step_id: int,
    status: str,
    *,
    artifact_text: str | None = None,
) -> ProjectTask | None:
    task_id = f"{project_id}:{source_session_id}:{step_id}"
    existing = get_task(task_id)
    if not existing:
        return None
    artifact_refs = list(existing.artifact_refs)
    if artifact_text:
        digest = artifact_text.strip()
        if digest:
            artifact_refs = [artifact for artifact in artifact_refs if artifact.get("type") != "digest"]
            artifact_refs.append({"type": "digest", "text": digest[:220]})
    return update_task(task_id, status=status, artifact_refs=artifact_refs)


def create_signal_task(
    project_id: str,
    source_session_id: str,
    *,
    title: str,
    description: str,
    kind: str,
    owner: str | None = None,
    priority: str = "medium",
) -> ProjectTask:
    return create_task(
        project_id=project_id,
        workspace_root=get_task_workspace_root(project_id),
        title=title,
        description=description,
        status="todo",
        priority=priority,
        owner=owner,
        kind=kind,
        source_session_id=source_session_id,
    )


def task_summary(project_id: str) -> dict[str, Any]:
    tasks = list_tasks(project_id)
    by_status: dict[str, int] = {}
    for task in tasks:
        by_status[task.status] = by_status.get(task.status, 0) + 1
    now_items = [task.to_dict() for task in tasks if task.status in {"in_progress", "review"}][:5]
    next_items = [task.to_dict() for task in tasks if task.status == "todo"][:5]
    blocked_items = [task.to_dict() for task in tasks if task.status == "blocked"][:5]
    recent_done = [task.to_dict() for task in tasks if task.status == "done"][:5]
    return {
        "total": len(tasks),
        "by_status": by_status,
        "now": now_items,
        "next": next_items,
        "blocked": blocked_items,
        "recent_done": recent_done,
    }
