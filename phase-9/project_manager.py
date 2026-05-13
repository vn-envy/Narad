"""
Project Manager — classifies sessions into named projects using LiteLLM.

Storage: project-memory/{user_id}/projects.json

Each project:  {"id": "proj_{hex8}", "name": str, "created_at": ISO, "session_ids": [str]}
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import sys as _sys_nc
_sys_nc.path.insert(0, str(Path(__file__).parent.parent))
from narad_config import WIKI_DIR as _WIKI_DIR
_MODEL = os.environ.get("NARAD_CLASSIFY_MODEL", "deepseek/deepseek-chat")


# ── Storage helpers ────────────────────────────────────────────────────────────

def _proj_file(user_id: str) -> Path:
    d = _WIKI_DIR / user_id
    d.mkdir(parents=True, exist_ok=True)
    return d / "projects.json"


def load_projects(user_id: str) -> list[dict]:
    f = _proj_file(user_id)
    if not f.exists():
        return []
    try:
        return json.loads(f.read_text()).get("projects", [])
    except Exception:
        return []


def save_projects(user_id: str, projects: list[dict]) -> None:
    _proj_file(user_id).write_text(json.dumps({"projects": projects}, indent=2))


# ── CRUD ───────────────────────────────────────────────────────────────────────

def get_project(user_id: str, project_id: str) -> dict | None:
    return next((p for p in load_projects(user_id) if p["id"] == project_id), None)


def create_project(user_id: str, name: str, session_id: str | None = None) -> dict:
    projects = load_projects(user_id)
    proj: dict = {
        "id": f"proj_{uuid4().hex[:8]}",
        "name": name,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "session_ids": [session_id] if session_id else [],
    }
    projects.append(proj)
    save_projects(user_id, projects)
    return proj


def assign_session(user_id: str, project_id: str, session_id: str) -> None:
    projects = load_projects(user_id)
    for p in projects:
        if p["id"] == project_id:
            if session_id not in p.get("session_ids", []):
                p.setdefault("session_ids", []).append(session_id)
    save_projects(user_id, projects)


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
