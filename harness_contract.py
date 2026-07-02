"""
Harness contract layer for Narad.

Promotes Narad's session plane, working-state plane, durable memory plane,
and governance plane into one additive API surface for the frontend.
"""

from __future__ import annotations

import json
import uuid
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from conversation_memory import (
    load_thread,
    load_working_state,
    recent_threads,
    save_working_state,
    summarize_thread,
)
from narad_config import (
    EPISODE_DIR,
    KARMA_MUTATIONS_PATH,
    SANKALPA_COMMITMENTS_PATH,
    SESSION_CATALOG_DIR,
    SWAPNA_INBOX_DIR,
)
from runtime_contract import collect_runtime_contract
from smriti_core import architecture_scorecard


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
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
    return rows


def _catalog_path(user_id: str) -> Path:
    SESSION_CATALOG_DIR.mkdir(parents=True, exist_ok=True)
    return SESSION_CATALOG_DIR / f"{user_id}.json"


def _load_catalog(user_id: str) -> list[dict[str, Any]]:
    path = _catalog_path(user_id)
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []
    if not isinstance(data, list):
        return []
    return [item for item in data if isinstance(item, dict)]


def _save_catalog(user_id: str, rows: list[dict[str, Any]]) -> None:
    path = _catalog_path(user_id)
    path.write_text(json.dumps(rows, indent=2, ensure_ascii=False), encoding="utf-8")


def _parse_ts(value: Any) -> datetime:
    if not value:
        return datetime.fromtimestamp(0, tz=timezone.utc)
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except Exception:
        return datetime.fromtimestamp(0, tz=timezone.utc)


def _session_title(
    *,
    session_id: str,
    turns: list[dict[str, Any]],
    working_state: dict[str, Any] | None,
    existing: dict[str, Any] | None,
    explicit_title: str | None = None,
) -> str:
    for candidate in (
        explicit_title,
        (existing or {}).get("title"),
        (working_state or {}).get("last_user_query"),
    ):
        if candidate:
            text = " ".join(str(candidate).split()).strip()
            if text:
                return text[:72]

    for turn in turns:
        if turn.get("role") == "user" and turn.get("text"):
            text = " ".join(str(turn["text"]).split()).strip()
            if text:
                return text[:72]
    return f"Session {session_id[:8]}"


def _last_turn_text(turns: list[dict[str, Any]], role: str) -> str:
    for turn in reversed(turns):
        if turn.get("role") == role and turn.get("text"):
            return str(turn["text"])[:220]
    return ""


def _build_session_record(
    *,
    user_id: str,
    session_id: str,
    turns: list[dict[str, Any]] | None = None,
    working_state: dict[str, Any] | None = None,
    existing: dict[str, Any] | None = None,
    parent_session_id: str | None = None,
    source: str | None = None,
    title: str | None = None,
) -> dict[str, Any]:
    existing = existing or {}
    turns = turns if turns is not None else load_thread(user_id, session_id)
    working_state = working_state if working_state is not None else load_working_state(user_id, session_id)

    first_turn_ts = next((turn.get("ts") for turn in turns if turn.get("ts")), None)
    last_turn_ts = next((turn.get("ts") for turn in reversed(turns) if turn.get("ts")), None)
    updated_at = (
        (working_state or {}).get("updated_at")
        or last_turn_ts
        or existing.get("updated_at")
        or _now_iso()
    )
    created_at = existing.get("created_at") or first_turn_ts or updated_at
    title_value = _session_title(
        session_id=session_id,
        turns=turns,
        working_state=working_state,
        existing=existing,
        explicit_title=title,
    )
    lineage_root_id = (
        existing.get("lineage_root_id")
        or (working_state or {}).get("lineage_root_id")
        or existing.get("parent_session_id")
        or parent_session_id
        or session_id
    )
    if lineage_root_id == parent_session_id and existing.get("lineage_root_id"):
        lineage_root_id = existing["lineage_root_id"]

    thread_summary = (
        (working_state or {}).get("thread_summary")
        or existing.get("thread_summary")
        or summarize_thread(user_id=user_id, session_id=session_id)
    )

    restored_after_reset = bool(
        (working_state or {}).get("restored_after_reset")
        or existing.get("restored_after_reset")
    )
    restorable = bool(turns or thread_summary or (working_state or {}).get("continued_from_sessions"))

    record: dict[str, Any] = {
        "session_id": session_id,
        "user_id": user_id,
        "title": title_value,
        "created_at": created_at,
        "updated_at": updated_at,
        "turn_count": len(turns),
        "restorable": restorable,
        "archived": bool(existing.get("archived", False)),
        "archived_at": existing.get("archived_at"),
        "parent_session_id": parent_session_id or existing.get("parent_session_id"),
        "lineage_root_id": lineage_root_id,
        "source": source or existing.get("source") or "live",
        "last_user_query": (
            (working_state or {}).get("last_user_query")
            or existing.get("last_user_query")
            or _last_turn_text(turns, "user")
        ),
        "last_assistant_preview": (
            (working_state or {}).get("last_assistant_preview")
            or existing.get("last_assistant_preview")
            or _last_turn_text(turns, "assistant")
        ),
        "thread_summary": thread_summary,
        "restored_after_reset": restored_after_reset,
        "last_trace_session_id": (
            (working_state or {}).get("last_trace_session_id")
            or existing.get("last_trace_session_id")
        ),
        "avatars": list((working_state or {}).get("avatars") or existing.get("avatars") or []),
        "karya": (working_state or {}).get("karya") or existing.get("karya"),
        "continued_from_sessions": list(
            (working_state or {}).get("continued_from_sessions")
            or existing.get("continued_from_sessions")
            or []
        ),
        "compacted_at": (working_state or {}).get("compacted_at") or existing.get("compacted_at"),
    }
    return record


def _upsert_catalog_record(user_id: str, record: dict[str, Any]) -> dict[str, Any]:
    rows = _load_catalog(user_id)
    found = False
    next_rows: list[dict[str, Any]] = []
    for row in rows:
        if row.get("session_id") == record["session_id"]:
            merged = dict(row)
            merged.update(record)
            next_rows.append(merged)
            record = merged
            found = True
        else:
            next_rows.append(row)
    if not found:
        next_rows.append(record)
    next_rows.sort(key=lambda item: _parse_ts(item.get("updated_at")), reverse=True)
    _save_catalog(user_id, next_rows)
    return record


def record_session_state(
    *,
    user_id: str,
    session_id: str,
    working_state: dict[str, Any] | None = None,
    parent_session_id: str | None = None,
    source: str | None = None,
    title: str | None = None,
) -> dict[str, Any]:
    rows = _load_catalog(user_id)
    existing = next((row for row in rows if row.get("session_id") == session_id), None)
    record = _build_session_record(
        user_id=user_id,
        session_id=session_id,
        working_state=working_state,
        existing=existing,
        parent_session_id=parent_session_id,
        source=source,
        title=title,
    )
    return _upsert_catalog_record(user_id, record)


def _sync_recent_threads(user_id: str, *, limit: int = 80) -> None:
    rows = _load_catalog(user_id)
    existing_map = {row.get("session_id"): row for row in rows if row.get("session_id")}
    changed = False
    for discovered in recent_threads(user_id, limit=limit):
        session_id = str(discovered.get("session_id", "")).strip()
        if not session_id:
            continue
        existing = existing_map.get(session_id)
        record = _build_session_record(
            user_id=user_id,
            session_id=session_id,
            existing=existing,
        )
        if existing != record:
            existing_map[session_id] = record
            changed = True
    if changed:
        next_rows = list(existing_map.values())
        next_rows.sort(key=lambda item: _parse_ts(item.get("updated_at")), reverse=True)
        _save_catalog(user_id, next_rows)


def list_session_records(
    user_id: str,
    *,
    limit: int = 20,
    include_archived: bool = True,
) -> list[dict[str, Any]]:
    _sync_recent_threads(user_id, limit=max(limit, 40))
    rows = _load_catalog(user_id)
    if not include_archived:
        rows = [row for row in rows if not row.get("archived")]
    rows.sort(key=lambda item: _parse_ts(item.get("updated_at")), reverse=True)
    return rows[: max(1, min(limit, 200))]


def get_session_record(user_id: str, session_id: str) -> dict[str, Any] | None:
    _sync_recent_threads(user_id, limit=80)
    rows = _load_catalog(user_id)
    existing = next((row for row in rows if row.get("session_id") == session_id), None)
    if existing:
        return existing
    turns = load_thread(user_id, session_id)
    working = load_working_state(user_id, session_id)
    if not turns and not working:
        return None
    return record_session_state(user_id=user_id, session_id=session_id, working_state=working)


def delete_session_record(user_id: str, session_id: str) -> None:
    rows = [row for row in _load_catalog(user_id) if row.get("session_id") != session_id]
    _save_catalog(user_id, rows)


def archive_session(user_id: str, session_id: str) -> dict[str, Any] | None:
    record = get_session_record(user_id, session_id)
    if not record:
        return None
    record["archived"] = True
    record["archived_at"] = _now_iso()
    return _upsert_catalog_record(user_id, record)


def recover_session(user_id: str, session_id: str) -> dict[str, Any] | None:
    record = get_session_record(user_id, session_id)
    if not record:
        return None
    record["archived"] = False
    record["archived_at"] = None
    record["updated_at"] = _now_iso()
    return _upsert_catalog_record(user_id, record)


def compact_session(user_id: str, session_id: str) -> dict[str, Any] | None:
    working = load_working_state(user_id, session_id) or {}
    turns = load_thread(user_id, session_id)
    if not turns and not working:
        return None
    summary = summarize_thread(user_id=user_id, session_id=session_id, keep_recent_turns=6, max_lines=10)
    working["thread_summary"] = summary
    working["compacted_at"] = _now_iso()
    save_working_state(user_id=user_id, session_id=session_id, state=working)
    return record_session_state(user_id=user_id, session_id=session_id, working_state=working)


def fork_session(
    user_id: str,
    session_id: str,
    *,
    title: str | None = None,
) -> dict[str, Any] | None:
    parent = get_session_record(user_id, session_id)
    if not parent:
        return None

    child_session_id = str(uuid.uuid4())
    working = load_working_state(user_id, session_id) or {}
    child_state = {
        "forked_from_session": session_id,
        "parent_session_id": session_id,
        "lineage_root_id": parent.get("lineage_root_id") or session_id,
        "thread_summary": working.get("thread_summary") or parent.get("thread_summary", ""),
        "last_user_query": parent.get("last_user_query", ""),
        "last_assistant_preview": parent.get("last_assistant_preview", ""),
        "avatars": list(working.get("avatars") or parent.get("avatars") or []),
        "karya": working.get("karya") or parent.get("karya"),
        "continued_from_sessions": [session_id],
    }
    save_working_state(user_id=user_id, session_id=child_session_id, state=child_state)
    child = record_session_state(
        user_id=user_id,
        session_id=child_session_id,
        working_state=child_state,
        parent_session_id=session_id,
        source="fork",
        title=title or f"Fork · {parent.get('title', session_id[:8])}",
    )
    return child


def _episode_count(user_id: str, session_id: str) -> int:
    path = EPISODE_DIR / f"{user_id}.jsonl"
    return sum(1 for row in _load_jsonl(path) if row.get("session_id") == session_id)


def _user_episode_count(user_id: str) -> int:
    path = EPISODE_DIR / f"{user_id}.jsonl"
    return len(_load_jsonl(path))


def _commitment_rows(user_id: str, session_id: str | None = None) -> list[dict[str, Any]]:
    rows = [
        row
        for row in _load_jsonl(SANKALPA_COMMITMENTS_PATH)
        if row.get("user_id") == user_id
    ]
    if session_id is not None:
        rows = [row for row in rows if row.get("session_id") == session_id]
    return rows


def _mutation_rows(session_id: str | None = None) -> list[dict[str, Any]]:
    rows = _load_jsonl(KARMA_MUTATIONS_PATH)
    if session_id is None:
        return rows
    return [
        row
        for row in rows
        if session_id in (row.get("provenance_ids") or [])
        or row.get("entity_id") == session_id
        or (row.get("metadata") or {}).get("session_id") == session_id
    ]


def _swapna_rows(user_id: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for path in sorted(SWAPNA_INBOX_DIR.glob("*.json"), reverse=True):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if payload.get("user_id") == user_id:
            rows.append(payload)
    return rows


def build_context_bundle(user_id: str, session_id: str) -> dict[str, Any] | None:
    session = get_session_record(user_id, session_id)
    if not session:
        return None

    turns = load_thread(user_id, session_id, limit=8)
    working = load_working_state(user_id, session_id) or {}
    commitments = _commitment_rows(user_id, session_id)
    session_mutations = sorted(
        _mutation_rows(session_id),
        key=lambda row: _parse_ts(row.get("ts")),
        reverse=True,
    )[:8]
    swapna_rows = _swapna_rows(user_id)

    thread_plane = {
        "turn_count": session.get("turn_count", 0),
        "restorable": session.get("restorable", False),
        "summary": session.get("thread_summary", ""),
        "recent_turns": [
            {
                "role": turn.get("role"),
                "text": str(turn.get("text", ""))[:400],
                "ts": turn.get("ts"),
            }
            for turn in turns
        ],
    }
    working_plane = {
        "avatars": list(working.get("avatars") or []),
        "karya": working.get("karya"),
        "latencies_ms": working.get("latencies_ms") or {},
        "phase_transitions": working.get("phase_transitions") or [],
        "last_trace_session_id": working.get("last_trace_session_id"),
        "restored_after_reset": bool(working.get("restored_after_reset")),
        "continued_from_sessions": list(working.get("continued_from_sessions") or []),
    }
    smriti_plane = {
        "episode_count": _episode_count(user_id, session_id),
        "commitment_count": len(commitments),
        "durable_layers": [
            "episodes",
            "semantic recall",
            "project memory",
            "sankalpa",
            "sutra",
        ],
        "commitments": commitments[-4:],
    }
    governance_plane = {
        "runtime_status": collect_runtime_contract().get("status", "unknown"),
        "mutation_count": len(session_mutations),
        "recent_mutations": session_mutations,
        "swapna_pending": len(swapna_rows),
        "dharma_guarded": True,
    }
    context_order = [
        {
            "key": "thread",
            "label": "Thread Memory",
            "status": "ready" if thread_plane["restorable"] else "empty",
            "detail": f"{thread_plane['turn_count']} exact turns retained",
        },
        {
            "key": "working",
            "label": "Working Memory",
            "status": "ready" if working else "warming",
            "detail": (
                f"{len(working_plane['avatars'])} avatars · {working_plane['last_trace_session_id'] or 'no trace yet'}"
            ),
        },
        {
            "key": "smriti",
            "label": "Smriti",
            "status": "ready" if smriti_plane["episode_count"] or smriti_plane["commitment_count"] else "warming",
            "detail": (
                f"{smriti_plane['episode_count']} episodes · {smriti_plane['commitment_count']} commitments"
            ),
        },
        {
            "key": "governance",
            "label": "Dharma / Karma / Yantra",
            "status": "ready",
            "detail": f"{governance_plane['mutation_count']} mutations · {governance_plane['swapna_pending']} swapna items",
        },
    ]
    return {
        "session": session,
        "context_order": context_order,
        "thread_plane": thread_plane,
        "working_plane": working_plane,
        "smriti_plane": smriti_plane,
        "governance_plane": governance_plane,
        "rehydration_preview": (
            session.get("thread_summary")
            or working.get("last_assistant_preview")
            or session.get("last_user_query", "")
        ),
    }


def harness_overview(*, user_id: str = "default", selected_session_id: str | None = None) -> dict[str, Any]:
    sessions = list_session_records(user_id, limit=24, include_archived=True)
    runtime = collect_runtime_contract()
    scorecard = architecture_scorecard()
    active_session = selected_session_id or next(
        (row.get("session_id") for row in sessions if not row.get("archived")),
        None,
    )
    context = build_context_bundle(user_id, active_session) if active_session else None
    swapna_rows = _swapna_rows(user_id)
    mutation_rows = _mutation_rows()
    episode_total = _user_episode_count(user_id)
    commitment_total = len(_commitment_rows(user_id))
    source_counts = Counter(str(row.get("source") or "live") for row in sessions)

    return {
        "generated_at": _now_iso(),
        "user_id": user_id,
        "selected_session_id": active_session,
        "runtime": {
            "status": runtime.get("status", "unknown"),
            "issue_count": runtime.get("issue_count", 0),
            "mode": runtime.get("build", {}).get("runtime_mode", "cloud"),
        },
        "summary": {
            "session_count": len([row for row in sessions if not row.get("archived")]),
            "archived_count": len([row for row in sessions if row.get("archived")]),
            "restorable_count": len([row for row in sessions if row.get("restorable")]),
            "forked_count": source_counts.get("fork", 0),
            "swapna_pending": len(swapna_rows),
            "mutation_count": len(mutation_rows),
            "episode_count": episode_total,
            "commitment_count": commitment_total,
        },
        "planes": {
            "session": {
                "label": "Session Plane",
                "detail": "Durable session lineage, forkability, continuity, and resume state.",
                "count": len(sessions),
                "active_session_id": active_session,
                "forked_count": source_counts.get("fork", 0),
            },
            "working": {
                "label": "Working-State Plane",
                "detail": "Current avatars, Karya state, last trace, and compact session summaries.",
                "count": len([row for row in sessions if row.get("karya") or row.get("avatars")]),
                "restored_count": len([row for row in sessions if row.get("restored_after_reset")]),
            },
            "smriti": {
                "label": "Smriti Plane",
                "detail": "Episodes, semantic recall, project memory, Sankalpa, and Sutra.",
                "count": episode_total,
                "commitment_count": commitment_total,
            },
            "governance": {
                "label": "Dharma / Karma / Yantra",
                "detail": "Guardrails, mutation ledger, traces, and dream-cycle readiness.",
                "count": len(mutation_rows),
                "issue_count": runtime.get("issue_count", 0),
            },
        },
        "sessions": sessions,
        "context": context,
        "scorecard": scorecard,
    }
