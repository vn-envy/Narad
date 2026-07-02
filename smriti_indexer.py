from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).parent
_PHASE1 = _ROOT / "phase-1"
_PHASE9 = _ROOT / "phase-9"
for _path in [str(_ROOT), str(_PHASE1), str(_PHASE9)]:
    if _path not in sys.path:
        sys.path.insert(0, _path)

from narad_config import EPISODE_DIR, WIKI_DIR
from smriti_vector_store import VectorMemoryRecord, embed_text, sync_records, upsert_record
from turbovec_policy import select_memory_tier


def _file_mtime_iso(path: Path) -> str:
    return datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).isoformat()


def _stable_id(prefix: str, *parts: str) -> str:
    import hashlib

    raw = "::".join([prefix, *parts])
    return f"{prefix}:{hashlib.sha1(raw.encode('utf-8')).hexdigest()[:16]}"


def index_episode_record(episode: dict[str, Any]) -> None:
    text = (
        f"Task: {episode.get('task', '')}\n"
        f"Result: {episode.get('result', '')}\n"
        f"Avatar: {episode.get('avatar', '')}\n"
        f"Discipline: {episode.get('discipline', '')}"
    ).strip()
    if not text:
        return
    embedding, embedding_model = embed_text(text)
    record = VectorMemoryRecord(
        record_id=f"episode:{episode.get('id', '')}",
        namespace="episodic_summary",
        tier=select_memory_tier("episodic_summary", created_at=episode.get("ts")),
        user_id=episode.get("user_id", "default"),
        project_id=episode.get("project_id", "general"),
        source_kind="episode",
        source_path=str(EPISODE_DIR / f"{episode.get('user_id', 'default')}.jsonl"),
        source_ref=str(episode.get("id", "")),
        created_at=episode.get("ts", datetime.now(timezone.utc).isoformat()),
        updated_at=episode.get("ts", datetime.now(timezone.utc).isoformat()),
        preview=(episode.get("task", "") or episode.get("result", ""))[:180],
        text=text[:1400],
        content_hash=_stable_id("hash", text),
        embedding_model=embedding_model,
        dim=len(embedding),
        metadata={
            "avatar": episode.get("avatar", ""),
            "discipline": episode.get("discipline", ""),
            "session_id": episode.get("session_id", ""),
            "trace_session_id": episode.get("trace_session_id", ""),
            "workspace_root": episode.get("workspace_root"),
        },
        embedding=embedding,
    )
    upsert_record(record)


def ensure_user_episode_index(user_id: str = "default") -> None:
    path = EPISODE_DIR / f"{user_id}.jsonl"
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            episode = json.loads(line)
        except Exception:
            continue
        if episode.get("user_id") != user_id:
            continue
        index_episode_record(episode)


def _split_wiki_sections(text: str) -> list[tuple[str, str]]:
    if "\n## " not in text:
        return [("document", text.strip())] if text.strip() else []
    import re

    parts = re.split(r"\n(?=##\s)", text)
    sections: list[tuple[str, str]] = []
    for idx, part in enumerate(parts):
        chunk = part.strip()
        if not chunk:
            continue
        first_line = chunk.splitlines()[0].strip()
        anchor = first_line[3:].strip() if first_line.startswith("## ") else f"section-{idx}"
        sections.append((anchor, chunk))
    return sections


def ensure_project_wiki_indexed(user_id: str = "default", project_id: str = "general") -> None:
    project_dir = WIKI_DIR / user_id / project_id
    project_dir.mkdir(parents=True, exist_ok=True)
    records: list[VectorMemoryRecord] = []
    for page in sorted(project_dir.glob("*.md")):
        text = page.read_text(encoding="utf-8")
        for anchor, chunk in _split_wiki_sections(text):
            chunk_text = f"[{page.stem.upper()}]\n{chunk}".strip()
            embedding, embedding_model = embed_text(chunk_text)
            record_id = _stable_id("wiki", project_id, page.name, anchor)
            records.append(
                VectorMemoryRecord(
                    record_id=record_id,
                    namespace="project_wiki",
                    tier=select_memory_tier("project_wiki", created_at=_file_mtime_iso(page)),
                    user_id=user_id,
                    project_id=project_id,
                    source_kind="wiki_section",
                    source_path=str(page),
                    source_ref=anchor,
                    created_at=_file_mtime_iso(page),
                    updated_at=_file_mtime_iso(page),
                    preview=chunk.splitlines()[0][:180],
                    text=chunk_text[:1800],
                    content_hash=_stable_id("hash", chunk_text),
                    embedding_model=embedding_model,
                    dim=len(embedding),
                    metadata={
                        "entity": page.stem,
                        "workspace_root": None,
                        "section_anchor": anchor,
                    },
                    embedding=embedding,
                )
            )
    if records:
        sync_records(
            user_id=user_id,
            namespace="project_wiki",
            records=records,
            prune_project_id=project_id,
            prune_source_kind="wiki_section",
        )
