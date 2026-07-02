"""
Conversation memory plane for Narad.

Separates:
  - Thread memory: exact recent turns, durable across refresh/restart
  - Working memory: lightweight session-state snapshot for restore hints
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from narad_config import THREAD_DIR, WORKING_MEMORY_DIR

try:
    from context_governor import compact_text_block, count_text_tokens, extract_artifact_references
except Exception:  # pragma: no cover - governor is additive
    compact_text_block = None  # type: ignore[assignment]
    count_text_tokens = None  # type: ignore[assignment]
    extract_artifact_references = None  # type: ignore[assignment]


def _thread_path(user_id: str, session_id: str) -> Path:
    path = THREAD_DIR / user_id
    path.mkdir(parents=True, exist_ok=True)
    return path / f"{session_id}.jsonl"


def _working_path(user_id: str, session_id: str) -> Path:
    path = WORKING_MEMORY_DIR / user_id
    path.mkdir(parents=True, exist_ok=True)
    return path / f"{session_id}.json"


def append_turn(
    *,
    user_id: str,
    session_id: str,
    role: str,
    text: str,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    record = {
        "id": str(uuid.uuid4()),
        "ts": datetime.now(timezone.utc).isoformat(),
        "role": role,
        "text": text[:16000],
        "metadata": metadata or {},
    }
    with _thread_path(user_id, session_id).open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")
    return record


def load_thread(user_id: str, session_id: str, *, limit: int | None = None) -> list[dict[str, Any]]:
    path = _thread_path(user_id, session_id)
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            rows.append(json.loads(line))
        except Exception:
            continue
    if limit is not None:
        return rows[-limit:]
    return rows


def summarize_thread(
    *,
    user_id: str,
    session_id: str,
    keep_recent_turns: int = 8,
    max_lines: int = 8,
) -> str:
    turns = load_thread(user_id, session_id)
    older_turns = turns[:-keep_recent_turns] if len(turns) > keep_recent_turns else []
    if not older_turns:
        return ""

    highlights: list[str] = []
    seen: set[str] = set()
    for turn in older_turns:
        text = " ".join(str(turn.get("text", "")).strip().split())
        if not text:
            continue
        label = "User" if turn.get("role") == "user" else "Narad"
        snippet = text[:180] + ("…" if len(text) > 180 else "")
        line = f"{label}: {snippet}"
        if line in seen:
            continue
        highlights.append(line)
        seen.add(line)
        if len(highlights) >= max_lines:
            break
    return "\n".join(highlights)


def save_working_state(*, user_id: str, session_id: str, state: dict[str, Any]) -> Path:
    path = _working_path(user_id, session_id)
    payload = dict(state)
    payload["session_id"] = session_id
    payload["user_id"] = user_id
    payload["updated_at"] = datetime.now(timezone.utc).isoformat()
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return path


def load_working_state(user_id: str, session_id: str) -> dict[str, Any] | None:
    path = _working_path(user_id, session_id)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def build_rehydration_query(
    *,
    user_id: str,
    session_id: str,
    current_query: str,
    max_turns: int = 10,
    char_budget: int = 6000,
    model: str | None = None,
    token_budget: int | None = None,
    return_metadata: bool = False,
) -> str | tuple[str, dict[str, Any]]:
    working = load_working_state(user_id, session_id) or {}
    exact_turn_limit = min(max_turns, 2) if working.get("thread_summary") else max_turns
    turns = load_thread(user_id, session_id, limit=exact_turn_limit)
    if not turns and not working:
        empty_meta = {
            "token_budget": token_budget,
            "predicted_tokens": 0,
            "compaction_applied": [],
            "compacted_from_tokens": 0,
            "artifact_references": [],
        }
        return (current_query, empty_meta) if return_metadata else current_query

    lines = ["[THREAD MEMORY — exact recent conversation, restored after runtime reset]"]
    if working:
        summary_parts = []
        if working.get("last_trace_session_id"):
            summary_parts.append(f"last trace {working['last_trace_session_id']}")
        if working.get("avatars"):
            summary_parts.append(f"avatars {' → '.join(working['avatars'])}")
        if working.get("last_assistant_preview"):
            summary_parts.append(f"last result: {working['last_assistant_preview']}")
        if summary_parts:
            lines.append("Session state: " + " · ".join(summary_parts))
        karya = working.get("karya")
        if isinstance(karya, dict) and karya.get("total"):
            karya_parts = [f"{karya.get('total', 0)} tasks"]
            done_count = karya.get("done_count", 0)
            blocked_count = karya.get("blocked_count", 0)
            if done_count:
                karya_parts.append(f"{done_count} done")
            if blocked_count:
                karya_parts.append(f"{blocked_count} blocked")
            lines.append("Karya state: " + " · ".join(karya_parts))
            active_titles = karya.get("active_titles") or []
            if active_titles:
                lines.append("Active task cards:")
                lines.extend(f"- {title}" for title in active_titles[:5])
        if working.get("thread_summary"):
            lines.append("Earlier summary:")
            lines.append(str(working["thread_summary"]))

    used = 0
    selected: list[str] = []
    for turn in reversed(turns):
        role = "User" if turn.get("role") == "user" else "Narad"
        text = str(turn.get("text", "")).strip()
        if not text:
            continue
        block = f"{role}: {text}"
        if used + len(block) > char_budget:
            break
        selected.append(block)
        used += len(block)
    selected.reverse()
    if selected:
        lines.append("Recent exact turns:")
        lines.extend(selected)
    lines.append("[END THREAD MEMORY]")

    memory_block = "\n".join(lines)
    compaction_applied: list[str] = []
    compacted_from_tokens = 0
    artifact_refs: list[dict[str, Any]] = []
    if extract_artifact_references is not None:
        try:
            artifact_refs = extract_artifact_references(memory_block)
        except Exception:
            artifact_refs = []

    if token_budget and model and compact_text_block is not None and count_text_tokens is not None:
        current_tokens = count_text_tokens(model, current_query)
        prefix_budget = max(256, token_budget - current_tokens - 96)
        compacted = compact_text_block(
            memory_block,
            model=model,
            token_budget=prefix_budget,
            query=current_query,
            preserve_artifacts=True,
        )
        memory_block = compacted.text or "[THREAD MEMORY]\nContext compacted.\n[END THREAD MEMORY]"
        compaction_applied = compacted.applied
        compacted_from_tokens = compacted.original_tokens
        artifact_refs = compacted.artifact_references or artifact_refs
    else:
        # Backward-compatible character ceiling for environments without token counting.
        if len(memory_block) > char_budget:
            memory_block = memory_block[:char_budget].rstrip() + "…"
            compaction_applied = ["char_trim"]

    final_text = "\n".join([
        memory_block,
        "",
        "[CURRENT USER TURN]",
        current_query,
    ])
    predicted_tokens = (
        count_text_tokens(model, final_text)
        if token_budget and model and count_text_tokens is not None else 0
    )
    metadata = {
        "token_budget": token_budget,
        "predicted_tokens": predicted_tokens,
        "compaction_applied": compaction_applied,
        "compacted_from_tokens": compacted_from_tokens,
        "artifact_references": artifact_refs,
    }
    return (final_text, metadata) if return_metadata else final_text


def working_snapshot(*, user_id: str, session_id: str) -> dict[str, Any]:
    return {
        "session_id": session_id,
        "turns": load_thread(user_id, session_id),
        "working_state": load_working_state(user_id, session_id),
    }


def recent_threads(user_id: str, *, limit: int = 10) -> list[dict[str, Any]]:
    user_dir = THREAD_DIR / user_id
    if not user_dir.exists():
        return []

    rows: list[dict[str, Any]] = []
    for path in sorted(user_dir.glob("*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)[:limit]:
        turns = load_thread(user_id, path.stem)
        working = load_working_state(user_id, path.stem)
        rows.append({
            "session_id": path.stem,
            "turn_count": len(turns),
            "updated_at": datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).isoformat(),
            "last_user_query": (working or {}).get("last_user_query"),
            "last_assistant_preview": (working or {}).get("last_assistant_preview"),
            "thread_summary": (working or {}).get("thread_summary", ""),
        })
    return rows


def build_recent_thread_context(
    *,
    user_id: str,
    current_query: str,
    exclude_session_id: str | None = None,
    limit_threads: int = 3,
    turn_budget: int = 10,
    max_age_hours: int = 12,
) -> tuple[str, list[str]]:
    user_dir = THREAD_DIR / user_id
    if not user_dir.exists():
        return "", []

    cutoff = datetime.now(timezone.utc) - timedelta(hours=max_age_hours)
    candidate_paths: list[Path] = []
    for path in sorted(user_dir.glob("*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True):
        if exclude_session_id and path.stem == exclude_session_id:
            continue
        updated = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
        if updated < cutoff:
            continue
        candidate_paths.append(path)
        if len(candidate_paths) >= limit_threads:
            break

    if not candidate_paths:
        return "", []

    candidate_paths.reverse()
    lines = ["[RECENT RELATED THREADS — continuity fallback across recent sessions]"]
    used_turns = 0
    source_ids: list[str] = []
    for path in candidate_paths:
        turns = load_thread(user_id, path.stem)
        if not turns:
            continue
        source_ids.append(path.stem)
        lines.append(f"Session {path.stem}:")
        selected = turns[-max(1, turn_budget // max(1, len(candidate_paths))):]
        for turn in selected:
            if used_turns >= turn_budget:
                break
            role = "User" if turn.get("role") == "user" else "Narad"
            text = " ".join(str(turn.get("text", "")).strip().split())
            if not text:
                continue
            snippet = text[:220] + ("…" if len(text) > 220 else "")
            lines.append(f"{role}: {snippet}")
            used_turns += 1
        if used_turns >= turn_budget:
            break

    if len(lines) == 1:
        return "", []

    lines.append("[END RECENT RELATED THREADS]")
    lines.append("")
    lines.append("[CURRENT USER TURN]")
    lines.append(current_query)
    return "\n".join(lines), source_ids


def clear_thread(user_id: str, session_id: str) -> dict[str, Any]:
    removed = False
    for path in (_thread_path(user_id, session_id), _working_path(user_id, session_id)):
        if path.exists():
            path.unlink()
            removed = True
    return {"status": "ok", "removed": removed, "session_id": session_id}
