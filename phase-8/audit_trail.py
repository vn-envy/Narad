"""
Audit Trail — append-only invocation log for avatar dispatch.

Every time _make_avatar_tool fires an avatar, a record is appended to
~/.narad/audit.jsonl so there is a durable, human-readable log of who
asked what, when, and with what scope.

Usage:
    from audit_trail import log_invocation, log_scope_warning
    log_invocation("matsya", "research quantum computing", "default")
    log_scope_warning("matsya", "finance query routed to Matsya", "default")
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

_AUDIT_PATH = Path.home() / ".narad" / "audit.jsonl"

# Per-avatar allowed data categories (soft enforcement — warns, never blocks)
AVATAR_SCOPES: dict[str, set[str]] = {
    "matsya":      {"web", "documents", "filesystem", "research", "academic", "ml"},
    "rama":        {"finance", "calendar", "health", "planning", "scheduling"},
    "krishna":     {"communication", "education", "wellness", "media", "creative"},
    "parashurama": {"code", "systems", "engineering", "database", "shell"},
}

# Keywords that suggest a cross-scope task (soft signal — not a hard block)
_SCOPE_SIGNALS: dict[str, list[str]] = {
    "matsya":      ["bank", "statement", "prescription", "medication", "salary", "investment"],
    "rama":        ["code", "debug", "function", "class", "repository", "dockerfile"],
    "krishna":     ["deploy", "server", "database", "kernel", "binary", "filesystem"],
    "parashurama": ["email", "send", "calendar", "schedule event", "health", "symptom"],
}


def _ensure_path() -> None:
    _AUDIT_PATH.parent.mkdir(parents=True, exist_ok=True)


def log_invocation(
    avatar: str,
    task_preview: str,
    user_id: str,
    ts: str | None = None,
) -> None:
    """Append an invocation record to audit.jsonl."""
    record = {
        "event":        "invocation",
        "avatar":       avatar,
        "task_preview": task_preview[:200],
        "user_id":      user_id,
        "ts":           ts or datetime.now(timezone.utc).isoformat(),
    }
    _write(record)


def log_scope_warning(
    avatar: str,
    task_preview: str,
    user_id: str,
    matched_signals: list[str] | None = None,
) -> None:
    """Append a scope-warning record (soft signal, does not block execution)."""
    record = {
        "event":           "scope_warning",
        "avatar":          avatar,
        "task_preview":    task_preview[:200],
        "user_id":         user_id,
        "matched_signals": matched_signals or [],
        "ts":              datetime.now(timezone.utc).isoformat(),
    }
    _write(record)


def check_scope(avatar: str, task: str) -> list[str]:
    """Return a list of cross-scope signal words found in task, or empty list."""
    avatar_key = avatar.lower()
    signals = _SCOPE_SIGNALS.get(avatar_key, [])
    task_lower = task.lower()
    return [sig for sig in signals if sig in task_lower]


def _write(record: dict) -> None:
    try:
        _ensure_path()
        with open(_AUDIT_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception:
        pass  # audit failure never blocks execution
