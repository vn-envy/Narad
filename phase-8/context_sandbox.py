"""
Context Sandbox — tool output compression for Narad's synthesis context.

Any avatar result > 2000 words is compressed: full text stored in SQLite keyed
by a UUID; an extractive summary (≤500 words) returned to Narad's synthesis
context. Narad (or the avatar) can retrieve the full output via expand_context().

Motivation: a single Playwright screenshot or document extract can be 40–60K tokens.
When three avatars run tools in parallel their raw outputs can saturate Narad's
entire synthesis context budget before synthesis begins.

Usage:
    summary, uuid = compress_if_large(raw_output)
    # uuid is None when no compression occurred
    full = expand_context(uuid)   # retrieves original
"""
from __future__ import annotations

import sqlite3
import uuid as _uuid
from datetime import datetime, timezone
from pathlib import Path

_DB_PATH = Path.home() / ".narad" / "context_sandbox.db"
_WORD_THRESHOLD = 2000   # compress if result exceeds this many whitespace-split tokens
_SUMMARY_WORDS  = 500    # target word count for extractive summary


# ── SQLite bootstrap ──────────────────────────────────────────────────────────

def _get_conn() -> sqlite3.Connection:
    _DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(_DB_PATH))
    conn.execute(
        "CREATE TABLE IF NOT EXISTS sandbox ("
        "  id TEXT PRIMARY KEY,"
        "  content TEXT NOT NULL,"
        "  created_at TEXT NOT NULL,"
        "  word_count INTEGER NOT NULL"
        ")"
    )
    conn.commit()
    return conn


# ── Extractive summarisation ──────────────────────────────────────────────────

def _extractive_summary(text: str, target_words: int = _SUMMARY_WORDS) -> str:
    """Return a summary of `text` under `target_words` words.

    Strategy: head + tail extraction.
    - Take the first ⌊target × 0.65⌋ words (context-setting content)
    - Take the last  ⌊target × 0.35⌋ words (conclusion/result)
    - Connect with an omission marker
    """
    words = text.split()
    total = len(words)

    head_n = int(target_words * 0.65)
    tail_n = int(target_words * 0.35)

    if total <= head_n + tail_n:
        # Already short enough — return as-is (caller should not reach here normally)
        return text

    head = " ".join(words[:head_n])
    tail = " ".join(words[total - tail_n:])
    omitted = total - head_n - tail_n
    return (
        f"{head}\n\n"
        f"… [{omitted:,} words omitted] …\n\n"
        f"{tail}"
    )


# ── Public API ────────────────────────────────────────────────────────────────

def compress_if_large(
    output: str,
    threshold: int = _WORD_THRESHOLD,
) -> tuple[str, str | None]:
    """Compress `output` if it exceeds `threshold` words.

    Returns:
        (summary_with_hint, uuid_str)  when compressed
        (output, None)                 when no compression needed
    """
    if not output:
        return output, None

    word_count = len(output.split())
    if word_count <= threshold:
        return output, None

    doc_id = str(_uuid.uuid4())
    summary = _extractive_summary(output, target_words=_SUMMARY_WORDS)
    hint = (
        f"\n\n[Full output ({word_count:,} words) stored. "
        f"Retrieve with: expand_context('{doc_id}')]"
    )

    try:
        conn = _get_conn()
        conn.execute(
            "INSERT INTO sandbox (id, content, created_at, word_count) VALUES (?, ?, ?, ?)",
            (doc_id, output, datetime.now(timezone.utc).isoformat(), word_count),
        )
        conn.commit()
        conn.close()
    except Exception:
        # Storage failure → return full output rather than a broken summary
        return output, None

    return summary + hint, doc_id


def expand_context(doc_id: str) -> str:
    """Retrieve full output previously stored by compress_if_large.

    Returns the original text, or an error string if not found.
    """
    if not doc_id:
        return "expand_context: doc_id is required."
    try:
        conn = _get_conn()
        row = conn.execute(
            "SELECT content FROM sandbox WHERE id = ?", (doc_id,)
        ).fetchone()
        conn.close()
        if row:
            return row[0]
        return f"expand_context: no entry found for id={doc_id!r}."
    except Exception as exc:
        return f"expand_context: error retrieving content — {exc}"


def purge_old_entries(days: int = 7) -> int:
    """Delete sandbox entries older than `days` days. Returns count deleted."""
    try:
        from datetime import timedelta
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        conn = _get_conn()
        cur = conn.execute("DELETE FROM sandbox WHERE created_at < ?", (cutoff,))
        conn.commit()
        deleted = cur.rowcount
        conn.close()
        return deleted
    except Exception:
        return 0
