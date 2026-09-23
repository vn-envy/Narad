from __future__ import annotations

import json
import logging
import re
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from time import monotonic
from typing import Any

from turbovec_policy import select_memory_tier

from narad_config import EPISODE_DIR, SMRITI_MANIFEST_DIR, WIKI_DIR
from smriti_vector_store import (
    VectorMemoryRecord,
    _safe_slug,
    current_embedding_model,
    embed_text,
    list_records,
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


# ── Wiki FTS5 (lexical plane over project wiki pages) ───────────────────────
# The wiki is NEVER embedded. It used to be — and one embedding-provider switch
# re-embedded 516 sections serially, freezing chat routing for 6+ minutes.
# Lexical FTS5 is indexed synchronously at write time (pure SQLite, no network),
# so recall over the wiki is instant AND always fresh.


def _wiki_fts_db_path(user_id: str) -> Path:
    return SMRITI_MANIFEST_DIR / _safe_slug(user_id) / "wiki_fts.db"


def _wiki_fts_conn(user_id: str) -> sqlite3.Connection:
    path = _wiki_fts_db_path(user_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.execute(
        """
        CREATE VIRTUAL TABLE IF NOT EXISTS wiki_fts USING fts5(
            section_id UNINDEXED,
            project_id UNINDEXED,
            entity UNINDEXED,
            anchor UNINDEXED,
            source_path UNINDEXED,
            updated_at UNINDEXED,
            content,
            tokenize='porter unicode61'
        )
        """
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS wiki_pages (path TEXT PRIMARY KEY, mtime REAL)"
    )
    return conn


def fts_reindex_wiki_page(user_id: str, project_id: str, path: Path) -> int:
    """Replace one page's sections in the lexical index. Called at write time."""
    if not path.exists():
        return 0
    sections = _split_wiki_sections(path.read_text(encoding="utf-8"))
    updated_at = _file_mtime_iso(path)
    conn = _wiki_fts_conn(user_id)
    try:
        conn.execute("DELETE FROM wiki_fts WHERE source_path = ?", (str(path),))
        for idx, (anchor, chunk) in enumerate(sections):
            conn.execute(
                "INSERT INTO wiki_fts(section_id, project_id, entity, anchor, "
                "source_path, updated_at, content) VALUES (?,?,?,?,?,?,?)",
                (
                    _stable_id("wikifts", project_id, path.name, anchor, str(idx)),
                    project_id,
                    path.stem,
                    anchor,
                    str(path),
                    updated_at,
                    f"[{path.stem.upper()}]\n{chunk}".strip(),
                ),
            )
        conn.execute(
            "INSERT OR REPLACE INTO wiki_pages(path, mtime) VALUES (?, ?)",
            (str(path), path.stat().st_mtime),
        )
        conn.commit()
    finally:
        conn.close()
    return len(sections)


def ensure_wiki_fts(user_id: str = "default", project_id: str = "general") -> None:
    """Backfill/repair the wiki lexical index — mtime-gated, no network.

    Write paths reindex their own page synchronously; this sweep catches pages
    written before the FTS plane existed or edited outside the API. Cost when
    nothing changed: one stat() per page.
    """
    project_dir = WIKI_DIR / user_id / project_id
    if not project_dir.exists():
        return
    conn = _wiki_fts_conn(user_id)
    try:
        known = dict(conn.execute("SELECT path, mtime FROM wiki_pages").fetchall())
        # Pages deleted on disk lose their index rows.
        for stale in [p for p in known if p.startswith(str(project_dir)) and not Path(p).exists()]:
            conn.execute("DELETE FROM wiki_fts WHERE source_path = ?", (stale,))
            conn.execute("DELETE FROM wiki_pages WHERE path = ?", (stale,))
        conn.commit()
    finally:
        conn.close()
    for page in sorted(project_dir.glob("*.md")):
        if known.get(str(page)) != page.stat().st_mtime:
            fts_reindex_wiki_page(user_id, project_id, page)


def fts_search_wiki_sections(
    query: str,
    *,
    user_id: str = "default",
    project_id: str = "general",
    limit: int = 8,
) -> list[dict[str, Any]]:
    """BM25 lexical search over wiki sections. Returns row dicts, best first.

    Same token sanitisation as episode search — user text never reaches the
    FTS5 syntax parser.
    """
    tokens = re.findall(r"\w+", query)[:12]
    if not tokens or not _wiki_fts_db_path(user_id).exists():
        return []
    match = " OR ".join(f'"{token}"' for token in tokens)
    conn = _wiki_fts_conn(user_id)
    try:
        rows = conn.execute(
            "SELECT section_id, project_id, entity, anchor, source_path, updated_at, content "
            "FROM wiki_fts WHERE wiki_fts MATCH ? AND project_id = ? "
            "ORDER BY rank LIMIT ?",
            (match, project_id, limit),
        ).fetchall()
    except Exception as exc:
        log.warning("Smriti: wiki FTS search failed: %s", exc)
        return []
    finally:
        conn.close()
    return [
        {
            "section_id": row[0],
            "project_id": row[1],
            "entity": row[2],
            "anchor": row[3],
            "source_path": row[4],
            "updated_at": row[5],
            "content": row[6],
        }
        for row in rows
    ]


# ── Background refresh (single-flight) ───────────────────────────────────────
# Episode embedding must NEVER run inline in a chat turn: a provider/model
# switch can re-embed everything, which once blocked routing for 6+ minutes.
# Recall paths call schedule_index_refresh() and proceed against the existing
# index; the refresh lands for the next turn. Brand-new episodes and wiki
# writes are findable immediately via their FTS5 lexical planes, written
# synchronously at append time.

_REFRESH_LOCK = threading.Lock()
_REFRESH_IDLE = threading.Condition(_REFRESH_LOCK)
_REFRESH_IN_FLIGHT: set[str] = set()


def schedule_index_refresh(user_id: str = "default", project_id: str = "general") -> bool:
    """Kick episode embedding + wiki FTS backfill on a daemon thread.
    Single-flight per USER (not per project) so two refreshes never write the
    same episode manifests concurrently; a skipped project is picked up on the
    next call. Returns True if a new refresh was started."""
    key = user_id
    with _REFRESH_IDLE:
        if key in _REFRESH_IN_FLIGHT:
            return False
        _REFRESH_IN_FLIGHT.add(key)

    def _run() -> None:
        try:
            ensure_user_episode_index(user_id)
            ensure_wiki_fts(user_id, project_id)
        except Exception as exc:  # visible, never fatal — recall stays lexical
            log.warning("Smriti background index refresh failed: %s", exc)
        finally:
            with _REFRESH_IDLE:
                _REFRESH_IN_FLIGHT.discard(key)
                _REFRESH_IDLE.notify_all()

    threading.Thread(target=_run, name=f"smriti-index-{key}", daemon=True).start()
    return True


def wait_for_index_refresh(user_id: str | None = None, *, timeout_s: float = 5.0) -> bool:
    """Wait for active background refreshes without making recall synchronous."""
    deadline = monotonic() + max(0.0, timeout_s)
    with _REFRESH_IDLE:
        while _REFRESH_IN_FLIGHT if user_id is None else user_id in _REFRESH_IN_FLIGHT:
            remaining = deadline - monotonic()
            if remaining <= 0:
                return False
            _REFRESH_IDLE.wait(timeout=remaining)
    return True
