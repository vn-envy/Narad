"""
Smriti — Avatara's persistent memory layer.

Direct LanceDB implementation (mem0 2.x dropped native LanceDB support).
Uses OpenAI text-embedding-3-small for vectors — cheap and fast.

Two operations:
  recall(task, user_id)  → relevant memories as context prefix string
  remember(task, result, avatar, user_id) → stores avatar output for future recall

Memory is scoped per user_id. Operations are best-effort — exceptions never
block the main avatar flow.
"""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import lancedb
import pyarrow as pa

_DB_PATH = str(Path(__file__).parent / "smriti_db")
_TABLE = "memories"
_EMBED_MODEL = "text-embedding-3-small"
_EMBED_DIM = 1536

_db: lancedb.DBConnection | None = None
_table: Any = None


def _get_table() -> Any:
    global _db, _table
    if _table is not None:
        return _table

    _db = lancedb.connect(_DB_PATH)
    existing = _db.table_names()

    if _TABLE in existing:
        _table = _db.open_table(_TABLE)
    else:
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


def _embed(text: str) -> list[float]:
    import openai
    client = openai.OpenAI(api_key=os.environ.get("OPENAI_API_KEY", ""))
    resp = client.embeddings.create(model=_EMBED_MODEL, input=text[:4000])
    return resp.data[0].embedding


def recall(task: str, user_id: str, limit: int = 3) -> str:
    """Return relevant past memories as a formatted context string, or '' if none."""
    try:
        table = _get_table()
        vector = _embed(task)
        results = (
            table.search(vector)
            .where(f"user_id = '{user_id}'", prefilter=True)
            .limit(limit)
            .select(["memory", "avatar", "created_at"])
            .to_list()
        )
        if not results:
            return ""
        lines = [f"- [{r['avatar']}] {r['memory']}" for r in results]
        return "\n".join(lines)
    except Exception:
        return ""


def remember(task: str, result: str, avatar: str, user_id: str) -> None:
    """Store an avatar's completed work for future recall."""
    try:
        memory_text = f"Task: {task[:300]} | Result: {result[:400]}"
        vector = _embed(memory_text)
        table = _get_table()
        table.add([{
            "id":         str(uuid.uuid4()),
            "user_id":    user_id,
            "avatar":     avatar,
            "memory":     memory_text,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "vector":     vector,
        }])
    except Exception:
        pass
