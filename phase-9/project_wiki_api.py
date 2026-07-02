"""
Project Wiki API — FastAPI routers for projects, sessions, and wiki pages.

Routers (all registered in server.py):
  wiki_router     — prefix "/wiki"
  projects_router — prefix "/projects"
  sessions_router — prefix "/sessions"

Wiki endpoints:
  GET  /wiki/{user_id}                          → list all projects for user
  GET  /wiki/{user_id}/{project_id}             → list wiki pages for project
  GET  /wiki/{user_id}/{project_id}/{entity}    → get page content
  PUT  /wiki/{user_id}/{project_id}/{entity}    → update page
  DELETE /wiki/{user_id}/{project_id}/{entity}  → delete page

Projects endpoints:
  GET   /projects/{user_id}                     → list projects with metadata
  PATCH /projects/{user_id}/{project_id}        → rename project

Sessions endpoints:
  GET   /sessions/{user_id}/{project_id}        → sessions list with metadata
"""

from __future__ import annotations

import json

from fastapi import APIRouter, HTTPException
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel

from smriti_v2 import get_wiki_pages, get_wiki_page, put_wiki_page, add_episode, WIKI_DIR
from project_manager import load_projects, rename_project
from project_session_info import session_info as _fast_session_info

wiki_router     = APIRouter(prefix="/wiki",     tags=["wiki"])
projects_router = APIRouter(prefix="/projects", tags=["projects"])
sessions_router = APIRouter(prefix="/sessions", tags=["sessions"])


class WikiPageUpdate(BaseModel):
    content: str


class ProjectRename(BaseModel):
    name: str


# ── Wiki routes ────────────────────────────────────────────────────────────────

@wiki_router.get("/{user_id}")
async def list_projects_for_user(user_id: str) -> dict:
    """List all projects for a user (and their wiki page counts)."""
    projects = load_projects(user_id)
    result = []
    for p in projects:
        pages = get_wiki_pages(user_id, p["id"])
        result.append({
            **p,
            "page_count": len(pages),
        })
    return {"user_id": user_id, "projects": result}


@wiki_router.get("/{user_id}/{project_id}")
async def list_wiki_pages(user_id: str, project_id: str) -> dict:
    """List all wiki pages for a specific project."""
    pages = get_wiki_pages(user_id, project_id)
    return {
        "user_id":    user_id,
        "project_id": project_id,
        "pages":      pages,
        "wiki_dir":   str(WIKI_DIR / user_id / project_id),
    }


@wiki_router.get("/{user_id}/{project_id}/{entity}", response_class=PlainTextResponse)
async def get_page(user_id: str, project_id: str, entity: str) -> str:
    """Get the full Markdown content of a wiki page."""
    content = get_wiki_page(user_id, entity, project_id)
    if content is None:
        raise HTTPException(
            status_code=404,
            detail=f"Wiki page '{entity}' not found in project '{project_id}'",
        )
    return content


@wiki_router.put("/{user_id}/{project_id}/{entity}")
async def update_page(user_id: str, project_id: str, entity: str, body: WikiPageUpdate) -> dict:
    """Update a wiki page with user-edited content."""
    put_wiki_page(user_id, entity, body.content, project_id)
    try:
        await add_episode(
            user_id=user_id,
            session_id="user-edit",
            avatar="User",
            task=f"Manual edit to {entity} wiki page",
            result=body.content[:1000],
            project_id=project_id,
        )
    except Exception:
        pass
    return {"ok": True, "user_id": user_id, "project_id": project_id, "entity": entity}


@wiki_router.delete("/{user_id}/{project_id}/{entity}")
async def delete_page(user_id: str, project_id: str, entity: str) -> dict:
    """Delete a wiki page."""
    path = WIKI_DIR / user_id / project_id / (entity + ".md")
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"Wiki page '{entity}' not found")
    path.unlink()
    return {"ok": True, "deleted": entity}


# ── Projects routes ────────────────────────────────────────────────────────────

@projects_router.get("/{user_id}")
async def list_projects(user_id: str) -> dict:
    """List all projects with metadata."""
    projects = load_projects(user_id)
    result = []
    for p in projects:
        session_ids = p.get("session_ids", [])
        active_session_id = p.get("active_session_id") or (session_ids[-1] if session_ids else None)
        last_activity_at = p.get("last_activity_at") or p.get("created_at")
        if active_session_id and not last_activity_at:
            latest = _session_info(active_session_id)
            last_activity_at = latest.get("ts") or p.get("created_at")
        result.append({
            "id":            p["id"],
            "name":          p["name"],
            "workspace_root": p.get("workspace_root"),
            "workspace_label": p.get("workspace_label"),
            "status": p.get("project_status", "active"),
            "project_status": p.get("project_status", "active"),
            "created_at":    p.get("created_at"),
            "session_count": len(session_ids),
            "active_session_id": active_session_id,
            "last_activity_at": last_activity_at,
        })
    # Sort by most recent activity first, falling back to created_at.
    result.sort(key=lambda x: x.get("last_activity_at") or x.get("created_at") or "", reverse=True)
    return {"user_id": user_id, "projects": result}


@projects_router.patch("/{user_id}/{project_id}")
async def rename_project_route(user_id: str, project_id: str, body: ProjectRename) -> dict:
    """Rename a project."""
    ok = rename_project(user_id, project_id, body.name)
    if not ok:
        raise HTTPException(status_code=404, detail=f"Project '{project_id}' not found")
    return {"ok": True, "project_id": project_id, "name": body.name}


# ── Sessions routes ────────────────────────────────────────────────────────────

def _session_info(session_id: str) -> dict:
    """Load session metadata from Yantra trace."""
    return _fast_session_info(session_id)


@sessions_router.get("/{user_id}/{project_id}")
async def list_sessions(user_id: str, project_id: str) -> dict:
    """List all sessions for a project with metadata."""
    from project_manager import get_project
    proj = get_project(user_id, project_id)
    if proj is None:
        raise HTTPException(status_code=404, detail=f"Project '{project_id}' not found")

    session_ids = proj.get("session_ids", [])
    sessions = [_session_info(sid) for sid in session_ids]
    # Sort newest first
    sessions.sort(key=lambda s: s.get("ts") or "", reverse=True)
    return {"user_id": user_id, "project_id": project_id, "sessions": sessions}
