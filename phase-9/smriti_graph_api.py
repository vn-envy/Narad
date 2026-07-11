"""
Smriti Knowledge Graph — deterministic, fully traceable, zero LLM calls.

Builds a graph straight from what already exists on disk:

  project ──has_page──▶ wiki page ──contains──▶ section ──by──▶ avatar
  session ──in──▶ project        session ──used──▶ avatar

Every section node carries its exact source (file path + anchor + verbatim
text), so each fact in the graph can be traced back to the moment it was
written. Nothing here is inferred — no embeddings, no extraction model.

Router (registered in server.py):
  GET /smriti/graph/{user_id}?project_id=&max_sections=&max_sessions=
"""

from __future__ import annotations

import json
import re
from typing import Any

from fastapi import APIRouter
from project_manager import load_projects

from narad_config import EPISODE_DIR, WIKI_DIR
from smriti_indexer import _split_wiki_sections

graph_router = APIRouter(prefix="/smriti/graph", tags=["smriti-graph"])

_AVATAR_RE = re.compile(r"\*\*Avatar:\*\*\s*([\w-]+)")


def _section_avatar(chunk: str) -> str | None:
    match = _AVATAR_RE.search(chunk)
    return match.group(1) if match else None


def _read_episodes(user_id: str) -> list[dict[str, Any]]:
    path = EPISODE_DIR / f"{user_id}.jsonl"
    if not path.exists():
        return []
    episodes: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            episodes.append(json.loads(line))
        except Exception:
            continue
    return episodes


def _project_ids_on_disk(user_id: str) -> list[str]:
    root = WIKI_DIR / user_id
    if not root.exists():
        return []
    return sorted(d.name for d in root.iterdir() if d.is_dir())


def build_knowledge_graph(
    user_id: str,
    project_id: str | None = None,
    max_sections: int = 10,
    max_sessions: int = 8,
) -> dict[str, Any]:
    """Assemble nodes + edges for one project (or every project of the user)."""
    named = {p["id"]: p.get("name") or p["id"] for p in load_projects(user_id)}
    episodes = _read_episodes(user_id)

    project_ids = set(_project_ids_on_disk(user_id))
    project_ids.update(ep.get("project_id") or "general" for ep in episodes)
    project_ids.update(named)
    if project_id:
        project_ids = {project_id} if project_id in project_ids else set()

    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []
    avatar_weight: dict[str, int] = {}
    seen_edges: set[tuple[str, str, str]] = set()

    def add_edge(source: str, target: str, kind: str) -> None:
        key = (source, target, kind)
        if key in seen_edges:
            return
        seen_edges.add(key)
        edges.append({"source": source, "target": target, "kind": kind})

    total_sections = 0
    for pid in sorted(project_ids):
        project_episodes = [ep for ep in episodes if (ep.get("project_id") or "general") == pid]
        project_dir = WIKI_DIR / user_id / pid
        pages = sorted(project_dir.glob("*.md")) if project_dir.exists() else []

        session_groups: dict[str, list[dict[str, Any]]] = {}
        for ep in project_episodes:
            sid = str(ep.get("session_id") or "unknown")
            session_groups.setdefault(sid, []).append(ep)

        project_node_id = f"project:{pid}"
        nodes.append({
            "id": project_node_id,
            "kind": "project",
            "label": named.get(pid, pid),
            "weight": max(len(pages), 1),
            "meta": {
                "project_id": pid,
                "page_count": len(pages),
                "session_count": len(session_groups),
                "episode_count": len(project_episodes),
            },
        })

        for page in pages:
            # Keep timestamped `## ` entries (and single-chunk documents);
            # drop the page-title preamble block.
            sections = [
                (anchor, chunk)
                for anchor, chunk in _split_wiki_sections(page.read_text(encoding="utf-8"))
                if chunk.startswith("## ") or anchor == "document"
            ]
            total_sections += len(sections)
            page_node_id = f"page:{pid}:{page.stem}"
            nodes.append({
                "id": page_node_id,
                "kind": "page",
                "label": page.stem,
                "weight": len(sections),
                "meta": {"project_id": pid, "section_count": len(sections)},
                "trace": {"path": str(page)},
            })
            add_edge(project_node_id, page_node_id, "has_page")

            # Pages are append-only, so the last N sections are the newest.
            for idx, (anchor, chunk) in list(enumerate(sections))[-max_sections:]:
                section_node_id = f"sec:{pid}:{page.stem}:{idx}"
                avatar = _section_avatar(chunk)
                body_lines = [ln for ln in chunk.splitlines()[1:] if ln.strip()]
                nodes.append({
                    "id": section_node_id,
                    "kind": "section",
                    "label": anchor,
                    "weight": 1,
                    "meta": {"project_id": pid, "entity": page.stem, "avatar": avatar},
                    "trace": {
                        "path": str(page),
                        "anchor": anchor,
                        "preview": (body_lines[0] if body_lines else anchor)[:180],
                        "content": chunk[:700],
                    },
                })
                add_edge(page_node_id, section_node_id, "contains")
                if avatar:
                    avatar_weight[avatar] = avatar_weight.get(avatar, 0) + 1
                    add_edge(section_node_id, f"avatar:{avatar}", "by")

        newest_sessions = sorted(
            session_groups.items(),
            key=lambda item: max(str(ep.get("ts") or "") for ep in item[1]),
            reverse=True,
        )[:max_sessions]
        for sid, group in newest_sessions:
            session_node_id = f"session:{sid}"
            last_ts = max(str(ep.get("ts") or "") for ep in group)
            nodes.append({
                "id": session_node_id,
                "kind": "session",
                "label": sid[:8],
                "weight": len(group),
                "meta": {
                    "project_id": pid,
                    "episode_count": len(group),
                    "last_ts": last_ts,
                },
                "trace": {
                    "episode_ids": [str(ep.get("id") or "") for ep in group[:5]],
                    "preview": str(group[-1].get("task") or "")[:180],
                },
            })
            add_edge(session_node_id, project_node_id, "in")
            for avatar in sorted({str(ep.get("avatar") or "") for ep in group if ep.get("avatar")}):
                avatar_weight.setdefault(avatar, 0)
                add_edge(session_node_id, f"avatar:{avatar}", "used")

    for avatar, weight in sorted(avatar_weight.items()):
        nodes.append({
            "id": f"avatar:{avatar}",
            "kind": "avatar",
            "label": avatar,
            "weight": max(weight, 1),
            "meta": {"section_count": weight},
        })

    return {
        "user_id": user_id,
        "project_id": project_id,
        "nodes": nodes,
        "edges": edges,
        "stats": {
            "projects": len(project_ids),
            "nodes": len(nodes),
            "edges": len(edges),
            "wiki_sections_total": total_sections,
        },
    }


@graph_router.get("/{user_id}")
async def get_knowledge_graph(
    user_id: str,
    project_id: str | None = None,
    max_sections: int = 10,
    max_sessions: int = 8,
) -> dict[str, Any]:
    """Deterministic knowledge graph over projects, wiki pages, sections,
    sessions, and avatars — every node traceable to its source text."""
    return build_knowledge_graph(
        user_id,
        project_id=project_id,
        max_sections=max(1, min(max_sections, 40)),
        max_sessions=max(1, min(max_sessions, 25)),
    )
