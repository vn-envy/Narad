"""
Project Manager — classifies sessions into named projects using LiteLLM.

Storage: project-memory/{user_id}/projects.json

Each project is normalized to include workspace-first metadata so the UI can
group and summarize workspaces without leaning on wiki pages as the primary
project model.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from narad_config import WIKI_DIR as _WIKI_DIR
_MODEL = os.environ.get("NARAD_CLASSIFY_MODEL", "deepseek/deepseek-chat")
_DEFAULT_WORKSPACE_ROOT = str((Path(__file__).parent.parent).resolve())


# ── Storage helpers ────────────────────────────────────────────────────────────

def _proj_file(user_id: str) -> Path:
    d = _WIKI_DIR / user_id
    d.mkdir(parents=True, exist_ok=True)
    return d / "projects.json"


def _default_workspace_root() -> str:
    return os.environ.get("NARAD_WORKSPACE_ROOT", _DEFAULT_WORKSPACE_ROOT)


def _workspace_label(workspace_root: str | None) -> str:
    if not workspace_root:
        return "workspace"
    try:
        name = Path(workspace_root).name.strip()
        return name or workspace_root
    except Exception:
        return workspace_root


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _normalize_project(project: dict) -> dict:
    workspace_root = str(project.get("workspace_root") or _default_workspace_root())
    normalized = dict(project)
    session_ids = list(dict.fromkeys(project.get("session_ids", [])))
    normalized["workspace_root"] = workspace_root
    normalized["workspace_label"] = str(
        project.get("workspace_label") or _workspace_label(workspace_root)
    )
    normalized["project_status"] = str(project.get("project_status") or "active")
    normalized["session_ids"] = session_ids
    normalized["active_session_id"] = (
        str(project.get("active_session_id"))
        if project.get("active_session_id")
        else (session_ids[-1] if session_ids else None)
    )
    normalized["last_activity_at"] = str(
        project.get("last_activity_at") or project.get("created_at") or _iso_now()
    )
    return normalized


def load_projects(user_id: str) -> list[dict]:
    f = _proj_file(user_id)
    if not f.exists():
        return []
    try:
        raw_projects = json.loads(f.read_text()).get("projects", [])
        normalized = [_normalize_project(project) for project in raw_projects]
        if normalized != raw_projects:
            save_projects(user_id, normalized)
        return normalized
    except Exception:
        return []


def save_projects(user_id: str, projects: list[dict]) -> None:
    normalized = [_normalize_project(project) for project in projects]
    _proj_file(user_id).write_text(json.dumps({"projects": normalized}, indent=2))


# ── CRUD ───────────────────────────────────────────────────────────────────────

def get_project(user_id: str, project_id: str) -> dict | None:
    return next((p for p in load_projects(user_id) if p["id"] == project_id), None)


def create_project(
    user_id: str,
    name: str,
    session_id: str | None = None,
    *,
    workspace_root: str | None = None,
) -> dict:
    projects = load_projects(user_id)
    proj: dict = {
        "id": f"proj_{uuid4().hex[:8]}",
        "name": name,
        "created_at": _iso_now(),
        "session_ids": [session_id] if session_id else [],
        "workspace_root": workspace_root or _default_workspace_root(),
        "workspace_label": _workspace_label(workspace_root or _default_workspace_root()),
        "project_status": "active",
        "active_session_id": session_id,
        "last_activity_at": _iso_now(),
    }
    projects.append(proj)
    save_projects(user_id, projects)
    return _normalize_project(proj)


def assign_session(
    user_id: str,
    project_id: str,
    session_id: str,
    *,
    workspace_root: str | None = None,
) -> None:
    touched = False
    projects = load_projects(user_id)
    for p in projects:
        if p["id"] == project_id:
            session_ids = list(p.get("session_ids", []))
            if session_id in session_ids:
                session_ids = [sid for sid in session_ids if sid != session_id]
            session_ids.append(session_id)
            p["session_ids"] = session_ids
            p["active_session_id"] = session_id
            p["last_activity_at"] = _iso_now()
            if workspace_root and not p.get("workspace_root"):
                p["workspace_root"] = workspace_root
                p["workspace_label"] = _workspace_label(workspace_root)
            touched = True
            break
    if touched:
        save_projects(user_id, projects)


def touch_project_activity(
    user_id: str,
    project_id: str,
    *,
    session_id: str | None = None,
    ts: str | None = None,
) -> bool:
    projects = load_projects(user_id)
    changed = False
    for p in projects:
        if p["id"] != project_id:
            continue
        if session_id:
            session_ids = [sid for sid in p.get("session_ids", []) if sid != session_id]
            session_ids.append(session_id)
            p["session_ids"] = session_ids
            p["active_session_id"] = session_id
        p["last_activity_at"] = ts or _iso_now()
        changed = True
        break
    if changed:
        save_projects(user_id, projects)
    return changed


def rename_project(user_id: str, project_id: str, new_name: str) -> bool:
    projects = load_projects(user_id)
    for p in projects:
        if p["id"] == project_id:
            p["name"] = new_name
            save_projects(user_id, projects)
            return True
    return False


def get_or_create_general(user_id: str, session_id: str | None = None) -> str:
    """Return project_id for the General fallback project."""
    for p in load_projects(user_id):
        if p["name"].lower() == "general":
            if session_id:
                assign_session(user_id, p["id"], session_id)
            return p["id"]
    return create_project(user_id, "General", session_id)["id"]


# ── Auto-detection ─────────────────────────────────────────────────────────────

def get_session_project(user_id: str, session_id: str) -> str | None:
    """Return the project_id this session is already assigned to, or None."""
    for p in load_projects(user_id):
        if session_id in p.get("session_ids", []):
            return p["id"]
    return None


async def detect_project(user_id: str, session_id: str, tasks: list[str]) -> str:
    """
    Classify a session into a project by name.
    Returns project_id — creating a new project if needed.
    Falls back to General project on any error.
    Skips LiteLLM call if session is already assigned.
    """
    existing = get_session_project(user_id, session_id)
    if existing:
        touch_project_activity(user_id, existing, session_id=session_id)
        return existing

    if not tasks:
        return get_or_create_general(user_id, session_id)

    projects = load_projects(user_id)
    existing_names = [p["name"] for p in projects]

    task_text = "\n".join(f"- {t[:150]}" for t in tasks[:10])
    existing_text = (
        "\n".join(f"- {n}" for n in existing_names)
        if existing_names else "(none yet)"
    )

    prompt = (
        f"These are the tasks from one conversation session:\n{task_text}\n\n"
        f"Existing project names:\n{existing_text}\n\n"
        "Your job: assign this session to the BEST MATCHING existing project. "
        "Be aggressive about merging — if the session is even loosely related to an existing project, use that project. "
        "Only invent a new project name if the topic is clearly unrelated to every single existing project "
        "(completely different domain, different user goal, different product area).\n\n"
        "Rules:\n"
        "- Prefer broad project names that can absorb related sessions (e.g. 'Narad Platform Development' covers all Narad/avatar/backend work)\n"
        "- Variations of the same product/tool/workflow belong in ONE project, not separate ones\n"
        "- When in doubt, merge into an existing project\n"
        "- New project names must be 2-5 words in Title Case\n\n"
        "Reply with ONLY the project name (exact existing name or new name). Nothing else."
    )

    try:
        from litellm import acompletion
        resp = await acompletion(
            model=_MODEL,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=20,
            temperature=0.2,
        )
        name = resp.choices[0].message.content.strip().strip('"').strip("'")
    except Exception:
        return get_or_create_general(user_id, session_id)

    # Match existing (case-insensitive)
    for p in projects:
        if p["name"].lower() == name.lower():
            assign_session(user_id, p["id"], session_id)
            return p["id"]

    # New project
    return create_project(user_id, name, session_id)["id"]
