"""
Smriti — Avatara's persistent memory layer (v1.5, legacy store).

Direct LanceDB implementation (mem0 2.x dropped native LanceDB support).
Embedding lives in smriti_embed (the single embedding client) — this module
only owns the legacy LanceDB table + memory_fts sidecar.

Three operations:
  recall(task, user_id, limit, max_age_days)  → relevant memories as context prefix string
  remember(task, result, avatar, user_id)     → stores avatar output for future recall
  recall_exact(query_str, user_id, avatar)    → FTS5 exact-phrase search (Hermes pattern)

Memory is scoped per user_id. Operations are best-effort — exceptions never
block the main avatar flow.

Vismriti (healthy forgetting):
  recall() filters out memories older than max_age_days (default 90).
  remember() skips near-duplicate entries (L2 distance < 0.10 = already known).
  Probabilistic size guard: on ~5% of inserts, purges entries older than 90 days.
  _get_table() detects vector dim mismatch (provider switch) and drops/recreates the table.
"""

from __future__ import annotations

import random
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any

import lancedb
import pyarrow as pa

import smriti_embed as _se
from narad_config import SMRITI_DB as _DB_PATH

# Re-read provider selection whenever this module is (re)imported, so test
# reloads and env changes are honoured even though smriti_embed stays cached.
_se.refresh_provider()

_TABLE = "memories"
_DISTANCE_THRESHOLD = 1.3   # L2 distance — above this is semantically unrelated noise
_DEDUP_THRESHOLD    = 0.10  # L2 distance — below this is a near-duplicate, skip insert
_FTS_DB_PATH = Path(str(_DB_PATH)).parent / "memory_fts.db"

_db: lancedb.DBConnection | None = None
_table: Any = None


def __getattr__(name: str) -> Any:
    """Forward embedding state to smriti_embed (single source of truth)."""
    if name in {"_SMRITI_EMBED_PROVIDER", "_EMBED_DIM", "_embed_unavailable_until"}:
        return getattr(_se, name)
    raise AttributeError(f"module 'smriti' has no attribute {name!r}")


def _get_table() -> Any:
    global _db, _table
    if _table is not None:
        return _table

    _db = lancedb.connect(_DB_PATH)
    existing = _db.list_tables().tables

    if _TABLE in existing:
        tbl = _db.open_table(_TABLE)
        # Drop and recreate if embedding provider switched (dim mismatch)
        try:
            existing_dim = tbl.schema.field("vector").type.list_size
            if existing_dim != _se._EMBED_DIM:
                import logging as _lg
                _lg.getLogger("narad.smriti").warning(
                    "Smriti: vector dim mismatch (existing=%d, expected=%d) — "
                    "wiping memory table. Re-populate via remember().",
                    existing_dim, _se._EMBED_DIM,
                )
                _db.drop_table(_TABLE)
                # fall through to create fresh schema below
            else:
                _table = tbl
                return _table
        except Exception:
            _table = tbl
            return _table

    schema = pa.schema([
        pa.field("id",         pa.utf8()),
        pa.field("user_id",    pa.utf8()),
        pa.field("avatar",     pa.utf8()),
        pa.field("memory",     pa.utf8()),
        pa.field("created_at", pa.utf8()),
        pa.field("vector",     pa.list_(pa.float32(), _se._EMBED_DIM)),
    ])
    _table = _db.create_table(_TABLE, schema=schema)
    return _table


def _get_fts_conn() -> sqlite3.Connection:
    """Get SQLite FTS5 connection, creating the virtual table if needed."""
    _FTS_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(_FTS_DB_PATH))
    conn.execute("""
        CREATE VIRTUAL TABLE IF NOT EXISTS memory_fts USING fts5(
            id,
            user_id UNINDEXED,
            avatar UNINDEXED,
            memory,
            created_at UNINDEXED,
            tokenize='porter unicode61'
        )
    """)
    conn.commit()
    return conn


@lru_cache(maxsize=128)
def _embed(text: str) -> list[float]:
    """Single embedding client — delegates to smriti_embed (no fallback)."""
    return _se._embed(text)


def _extract_substance(result: str) -> str:
    """Extract meaningful content from avatar output, skipping preamble.

    Strips internal reasoning openers and truncates to 600 chars.
    """
    preamble_starts = (
        "now i ", "let me ", "we need to", "we are ", "i need to",
        "okay", "alright", "sure", "the user", "this is a",
    )
    lines = result.split("\n")
    first_substance = 0
    for i, line in enumerate(lines):
        stripped = line.strip().lower()
        if not stripped:
            continue
        if any(stripped.startswith(p) for p in preamble_starts):
            first_substance = i + 1
        else:
            break

    substance = "\n".join(lines[first_substance:]).strip()
    if not substance:
        substance = result.strip()
    return substance[:600]


def recall(task: str, user_id: str, limit: int = 3, max_age_days: int = 90) -> str:
    """Return relevant past memories as a formatted context string, or '' if none.

    Vismriti: memories older than max_age_days are excluded (healthy forgetting).
    """
    try:
        table = _get_table()
        vector = _embed(task)
        results = (
            table.search(vector)
            .where(f"user_id = '{user_id}'", prefilter=True)
            .limit(limit * 3)  # fetch extra, filter by age in Python
            .select(["memory", "avatar", "created_at", "_distance"])
            .to_list()
        )
        if not results:
            return ""

        # Vismriti: exclude entries older than max_age_days
        if max_age_days:
            cutoff = (datetime.now(timezone.utc) - timedelta(days=max_age_days)).isoformat()
            results = [r for r in results if r.get("created_at", "") >= cutoff]

        # Filter out semantically distant memories
        relevant = [r for r in results if r.get("_distance", 1.0) <= _DISTANCE_THRESHOLD]
        relevant = relevant[:limit]
        if not relevant:
            return ""

        lines = [f"- [{r['avatar']}] {r['memory']}" for r in relevant]
        return "\n".join(lines)
    except Exception as _exc:
        import logging as _lg_smriti
        _lg_smriti.getLogger("narad.smriti").warning("recall() failed (embedding/search): %s", _exc)
        return ""


def recall_exact(query_str: str, user_id: str, avatar: str | None = None, limit: int = 3) -> str:
    """FTS5 BM25 exact-phrase search over memory bank (Hermes pattern).

    Best for Parashurama (code snippets, stack traces, exact shell errors)
    where semantic similarity misses exact-match needs.
    Returns formatted context string or '' if none.
    """
    try:
        conn = _get_fts_conn()
        # Escape FTS5 special chars
        safe_query = query_str.replace('"', '""')[:200]
        if avatar:
            rows = conn.execute(
                "SELECT memory, avatar FROM memory_fts "
                "WHERE memory_fts MATCH ? AND user_id = ? AND avatar = ? "
                "ORDER BY rank LIMIT ?",
                (safe_query, user_id, avatar, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT memory, avatar FROM memory_fts "
                "WHERE memory_fts MATCH ? AND user_id = ? "
                "ORDER BY rank LIMIT ?",
                (safe_query, user_id, limit),
            ).fetchall()
        conn.close()
        if not rows:
            return ""
        lines = [f"- [{r[1]}] {r[0]}" for r in rows]
        return "\n".join(lines)
    except Exception:
        return ""


def remember(task: str, result: str, avatar: str, user_id: str) -> None:
    """Store an avatar's completed work for future recall.

    Deduplication: skips insert if a near-identical memory already exists
    (L2 distance < 0.10 = same knowledge, different wording).
    Size guard: probabilistically purges entries older than 90 days (~5% of inserts).
    """
    try:
        substance = _extract_substance(result)
        memory_text = f"{task[:300]}\n\n{avatar} answered: {substance}"
        vector = _embed(task[:2000])
        table = _get_table()

        # Deduplication: skip if near-identical memory already exists for this user
        existing = (
            table.search(vector)
            .where(f"user_id = '{user_id}'", prefilter=True)
            .limit(1)
            .to_list()
        )
        if existing and existing[0].get("_distance", 999) < _DEDUP_THRESHOLD:
            return  # near-duplicate — knowledge already captured

        row_id = str(uuid.uuid4())
        created_at = datetime.now(timezone.utc).isoformat()
        table.add([{
            "id":         row_id,
            "user_id":    user_id,
            "avatar":     avatar,
            "memory":     memory_text,
            "created_at": created_at,
            "vector":     vector,
        }])

        # FTS5 write-through
        try:
            conn = _get_fts_conn()
            conn.execute(
                "INSERT INTO memory_fts(id, user_id, avatar, memory, created_at) VALUES (?,?,?,?,?)",
                (row_id, user_id, avatar, memory_text, created_at),
            )
            conn.commit()
            conn.close()
        except Exception:
            pass

        # Probabilistic size guard (~5% of inserts): purge entries older than 90 days
        if random.random() < 0.05:
            cutoff = (datetime.now(timezone.utc) - timedelta(days=90)).isoformat()
            try:
                table.delete(f"user_id = '{user_id}' AND created_at < '{cutoff}'")
                conn2 = _get_fts_conn()
                conn2.execute(
                    "DELETE FROM memory_fts WHERE user_id = ? AND created_at < ?",
                    (user_id, cutoff),
                )
                conn2.commit()
                conn2.close()
            except Exception:
                pass

    except Exception:
        pass
