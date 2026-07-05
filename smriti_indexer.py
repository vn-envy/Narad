from __future__ import annotations

import json
import logging
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from turbovec_policy import select_memory_tier

from narad_config import EPISODE_DIR, SMRITI_MANIFEST_DIR, WIKI_DIR
from smriti_vector_store import (
    VectorMemoryRecord,
    _safe_slug,
    current_embedding_model,
    embed_text,
    list_records,
    sync_records,
    upsert_record,
)

log = logging.getLogger("narad.smriti")


def _file_mtime_iso(path: Path) -> str:
    return datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).isoformat()


def _stable_id(prefix: str, *parts: str) -> str:
    import hashlib

    raw = "::".join([prefix, *parts])
    return f"{prefix}:{hashlib.sha1(raw.encode('utf-8')).hexdigest()[:16]}"


# ── Episode FTS5 (lexical plane over episodes.jsonl) ────────────────────────
# One sidecar DB per user, next to the vector manifests. Written BEFORE any
# embedding call, so exact-match recall survives embedding-provider outages.


def _fts_db_path(user_id: str) -> Path:
    return SMRITI_MANIFEST_DIR / _safe_slug(user_id) / "episode_fts.db"


def _fts_conn(user_id: str) -> sqlite3.Connection:
    path = _fts_db_path(user_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.execute(
        """
        CREATE VIRTUAL TABLE IF NOT EXISTS episodes_fts USING fts5(
            episode_id UNINDEXED,
            avatar UNINDEXED,
            project_id UNINDEXED,
            ts UNINDEXED,
            task,
            result,
            tokenize='porter unicode61'
        )
        """
    )
    return conn


def fts_upsert_episode(episode: dict[str, Any], *, user_id: str | None = None) -> None:
    """Idempotent write of one episode into the lexical index (delete+insert)."""
    uid = user_id or episode.get("user_id", "default")
    episode_id = str(episode.get("id", ""))
    if not episode_id:
        return
    conn = _fts_conn(uid)
    try:
        conn.execute("DELETE FROM episodes_fts WHERE episode_id = ?", (episode_id,))
        conn.execute(
            "INSERT INTO episodes_fts(episode_id, avatar, project_id, ts, task, result) "
            "VALUES (?,?,?,?,?,?)",
            (
                episode_id,
                episode.get("avatar", ""),
                episode.get("project_id", "general"),
                episode.get("ts", ""),
                episode.get("task", ""),
                episode.get("result", ""),
            ),
        )
        conn.commit()
    finally:
        conn.close()


def fts_delete_episode(user_id: str, episode_id: str) -> None:
    if not _fts_db_path(user_id).exists():
        return
    conn = _fts_conn(user_id)
    try:
        conn.execute("DELETE FROM episodes_fts WHERE episode_id = ?", (episode_id,))
        conn.commit()
    finally:
        conn.close()


def fts_search_episodes(
    query: str,
    *,
    user_id: str = "default",
    limit: int = 5,
    avatar: str | None = None,
) -> list[dict[str, Any]]:
    """BM25 lexical search over episodes. Returns row dicts, best first.

    Query is tokenized to bare words joined with OR — user text never reaches
    the FTS5 syntax parser, so stack traces and shell errors are safe input.
    """
    tokens = re.findall(r"\w+", query)[:12]
    if not tokens or not _fts_db_path(user_id).exists():
        return []
    match = " OR ".join(f'"{token}"' for token in tokens)
    conn = _fts_conn(user_id)
    try:
        if avatar:
            rows = conn.execute(
                "SELECT episode_id, avatar, project_id, ts, task, result "
                "FROM episodes_fts WHERE episodes_fts MATCH ? AND avatar = ? "
                "ORDER BY rank LIMIT ?",
                (match, avatar, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT episode_id, avatar, project_id, ts, task, result "
                "FROM episodes_fts WHERE episodes_fts MATCH ? ORDER BY rank LIMIT ?",
                (match, limit),
            ).fetchall()
    except Exception as exc:
        log.warning("Smriti: episode FTS search failed: %s", exc)
        return []
    finally:
        conn.close()
    return [
        {
            "episode_id": row[0],
            "avatar": row[1],
            "project_id": row[2],
            "ts": row[3],
            "task": row[4],
            "result": row[5],
        }
        for row in rows
    ]


def _episode_index_text(episode: dict[str, Any]) -> str:
    return (
        f"Task: {episode.get('task', '')}\n"
        f"Result: {episode.get('result', '')}\n"
        f"Avatar: {episode.get('avatar', '')}\n"
        f"Discipline: {episode.get('discipline', '')}"
    ).strip()


def index_episode_record(
    episode: dict[str, Any],
    *,
    known_hashes: dict[str, str] | None = None,
) -> bool:
    """Index one episode into the vector plane.

    known_hashes maps record_id → content_hash for records already stored
    under the current embedding model; unchanged episodes are skipped WITHOUT
    an embedding call (the fix for re-embedding everything on every recall).
    Returns True if a record was (re)embedded and written.
    """
    text = _episode_index_text(episode)
    if not text:
        return False
    # Lexical plane first: FTS must not be lost when embedding fails below.
    try:
        fts_upsert_episode(episode)
    except Exception as exc:
        log.warning("Smriti: episode FTS upsert failed: %s", exc)
    record_id = f"episode:{episode.get('id', '')}"
    content_hash = _stable_id("hash", text)
    if known_hashes is not None and known_hashes.get(record_id) == content_hash:
        return False
    embedding, embedding_model = embed_text(text)
    record = VectorMemoryRecord(
        record_id=record_id,
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
        content_hash=content_hash,
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
    return True


def _episode_ckpt_path(user_id: str) -> Path:
    return SMRITI_MANIFEST_DIR / _safe_slug(user_id) / "episode_index_checkpoint.json"


def _load_episode_ckpt(user_id: str) -> dict[str, Any]:
    path = _episode_ckpt_path(user_id)
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else {}
    except Exception:
        return {}


def _write_episode_ckpt(user_id: str, *, model: str, offset: int) -> None:
    path = _episode_ckpt_path(user_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"model": model, "offset": offset}), encoding="utf-8")


def ensure_user_episode_index(user_id: str = "default") -> None:
    """Bring the vector index up to date with episodes.jsonl — incrementally.

    episodes.jsonl is append-only, so a byte-offset checkpoint makes the
    common case O(1): nothing new appended → return without reading the file.
    New tail lines are parsed and embedded once. A model switch or a rewritten
    (shrunk) file triggers a full rescan, still skipping any episode whose
    content_hash is already stored under the current model.

    Raises EmbeddingUnavailableError if the provider is down — visibly, so
    callers decide; nothing is ever written under a stand-in model.
    """
    path = EPISODE_DIR / f"{user_id}.jsonl"
    if not path.exists():
        return
    model = current_embedding_model()
    size = path.stat().st_size
    ckpt = _load_episode_ckpt(user_id)
    start = 0
    if not _fts_db_path(user_id).exists():
        pass  # lexical index missing (pre-FTS data) → full rescan backfills it
    elif ckpt.get("model") == model and isinstance(ckpt.get("offset"), int):
        if ckpt["offset"] == size:
            return  # fast path: no new episodes since last index
        if 0 < ckpt["offset"] < size:
            start = ckpt["offset"]  # append-only: only the tail is new
        # offset > size → file was rewritten (e.g. forget()) → full rescan

    known_hashes: dict[str, str] | None = None
    if start == 0:
        known_hashes = {
            record.record_id: record.content_hash
            for record in list_records(
                user_id=user_id, namespace="episodic_summary", embedding_model=model
            )
        }

    with path.open("rb") as fh:
        fh.seek(start)
        data = fh.read()
    # Only advance past newline-terminated lines — a torn trailing write is
    # left for the next pass instead of being skipped forever.
    last_nl = data.rfind(b"\n")
    if last_nl < 0:
        return
    data = data[: last_nl + 1]
    end_offset = start + last_nl + 1

    embedded = 0
    for line in data.decode("utf-8", errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            episode = json.loads(line)
        except Exception:
            continue
        if episode.get("user_id") != user_id:
            continue
        if index_episode_record(episode, known_hashes=known_hashes):
            embedded += 1
    _write_episode_ckpt(user_id, model=model, offset=end_offset)
    if embedded:
        log.info("Smriti index: embedded %d new episode(s) for %s", embedded, user_id)


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
    """Bring the wiki index up to date — re-embedding only changed sections.

    Unchanged sections (same content_hash under the current model) reuse their
    stored embedding, and if nothing was added, changed, or deleted the sync
    (and the turbovec index rebuild it triggers) is skipped entirely.
    """
    project_dir = WIKI_DIR / user_id / project_id
    project_dir.mkdir(parents=True, exist_ok=True)
    model = current_embedding_model()
    existing = {
        record.record_id: record
        for record in list_records(
            user_id=user_id, namespace="project_wiki", embedding_model=model
        )
        if record.project_id == project_id and record.source_kind == "wiki_section"
    }

    records: list[VectorMemoryRecord] = []
    embedded = 0
    for page in sorted(project_dir.glob("*.md")):
        text = page.read_text(encoding="utf-8")
        for anchor, chunk in _split_wiki_sections(text):
            chunk_text = f"[{page.stem.upper()}]\n{chunk}".strip()
            record_id = _stable_id("wiki", project_id, page.name, anchor)
            content_hash = _stable_id("hash", chunk_text)
            previous = existing.get(record_id)
            if (
                previous is not None
                and previous.content_hash == content_hash
                and previous.embedding
            ):
                records.append(previous)  # unchanged — no embedding call
                continue
            embedding, embedding_model = embed_text(chunk_text)
            embedded += 1
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
                    content_hash=content_hash,
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

    unchanged = embedded == 0 and {r.record_id for r in records} == set(existing)
    if records and not unchanged:
        sync_records(
            user_id=user_id,
            namespace="project_wiki",
            records=records,
            prune_project_id=project_id,
            prune_source_kind="wiki_section",
        )
        if embedded:
            log.info(
                "Smriti index: embedded %d wiki section(s) for %s/%s",
                embedded, user_id, project_id,
            )
