"""
Smriti — Avatara's persistent memory layer (v1.5).

Direct LanceDB implementation (mem0 2.x dropped native LanceDB support).
Embedding provider: Gemini gemini-embedding-001 (768 dims) by default when configured.
Set SMRITI_EMBEDDING_MODEL=mimo or openai to force the OpenAI-compatible path.

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

import os
import random
import sqlite3
import time
import uuid
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any

import lancedb
import pyarrow as pa

import sys as _sys_nc
_sys_nc.path.insert(0, str(Path(__file__).parent.parent))
from narad_config import SMRITI_DB as _DB_PATH
_TABLE = "memories"
def _select_embed_provider() -> str:
    configured = os.environ.get("SMRITI_EMBEDDING_MODEL", "").strip().lower()
    if configured in {"gemini", "mimo", "openai"}:
        return configured
    if configured and configured != "auto":
        return configured
    if os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY"):
        return "gemini"
    if os.environ.get("MIMO_API_KEY"):
        return "mimo"
    if os.environ.get("OPENAI_API_KEY"):
        return "openai"
    return "gemini"


_SMRITI_EMBED_PROVIDER = _select_embed_provider()
_EMBED_DIM = 768 if _SMRITI_EMBED_PROVIDER == "gemini" else 1536
_DISTANCE_THRESHOLD = 1.3   # L2 distance — above this is semantically unrelated noise
_DEDUP_THRESHOLD    = 0.10  # L2 distance — below this is a near-duplicate, skip insert
_FTS_DB_PATH = Path(str(_DB_PATH)).parent / "memory_fts.db"
_EMBED_FAILURE_COOLDOWN_S = int(os.environ.get("SMRITI_EMBED_FAILURE_COOLDOWN_S", "300"))
_embed_unavailable_until = 0.0

_db: lancedb.DBConnection | None = None
_table: Any = None


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
            if existing_dim != _EMBED_DIM:
                import logging as _lg
                _lg.getLogger("narad.smriti").warning(
                    "Smriti: vector dim mismatch (existing=%d, expected=%d) — "
                    "wiping memory table. Re-populate via remember().",
                    existing_dim, _EMBED_DIM,
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
        pa.field("vector",     pa.list_(pa.float32(), _EMBED_DIM)),
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
    global _embed_unavailable_until
    now = time.time()
    if _embed_unavailable_until and now < _embed_unavailable_until:
        raise RuntimeError(
            f"embedding provider {_SMRITI_EMBED_PROVIDER} cooling down until "
            f"{datetime.fromtimestamp(_embed_unavailable_until, tz=timezone.utc).isoformat()}"
        )
    if _SMRITI_EMBED_PROVIDER == "gemini":
        # Legacy path — only used if SMRITI_EMBEDDING_MODEL=gemini explicitly set
        try:
            from google import genai as _genai
            from google.genai import types as _gtypes
            api_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY", "")
            if not api_key:
                raise RuntimeError("SMRITI_EMBEDDING_MODEL=gemini but GEMINI_API_KEY is not set")
            client = _genai.Client(api_key=api_key)
            resp = client.models.embed_content(
                model="gemini-embedding-001",
                contents=text[:4000],
                config=_gtypes.EmbedContentConfig(output_dimensionality=768),
            )
            return resp.embeddings[0].values
        except Exception as exc:
            message = str(exc).lower()
            if any(token in message for token in ("quota", "resource_exhausted", "429", "rate limit")):
                _embed_unavailable_until = now + _EMBED_FAILURE_COOLDOWN_S
            else:
                # Gemini failures tend to repeat for the same request burst, so cool
                # the provider down briefly instead of re-paying long error latencies.
                _embed_unavailable_until = max(
                    _embed_unavailable_until,
                    now + min(_EMBED_FAILURE_COOLDOWN_S, 60),
                )
            raise
    # OpenAI-compatible path: Mimo when configured, otherwise plain OpenAI.
    import openai as _openai
    if _SMRITI_EMBED_PROVIDER == "mimo":
        api_key = os.environ.get("MIMO_API_KEY", "")
        base_url = os.environ.get("MIMO_BASE_URL")
    else:
        api_key = os.environ.get("OPENAI_API_KEY", "")
        base_url = None
    if not api_key:
        raise RuntimeError(f"SMRITI_EMBEDDING_MODEL={_SMRITI_EMBED_PROVIDER} but no API key is set")
    client = _openai.OpenAI(api_key=api_key, base_url=base_url)
    resp = client.embeddings.create(model="text-embedding-3-small", input=text[:4000])
    return resp.data[0].embedding


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

        # Notion sync hook (fire-and-forget, non-blocking)
        try:
            if os.environ.get("NOTION_API_TOKEN"):
                import asyncio as _ao
                import sys as _sys2
                _sys2.path.insert(0, str(Path(__file__).parent.parent / "phase-1"))
                from notion_sync import NotionSync as _NSynth  # type: ignore
                _ns_inst = _NSynth()
                _ao.get_event_loop().call_soon(lambda _r=row_id, _u=user_id, _a=avatar, _m=memory_text, _c=created_at:
                    _ao.ensure_future(_ns_inst.push_memory(_r, _u, _a, _m, _c)))
        except Exception:
            pass

    except Exception:
        pass
