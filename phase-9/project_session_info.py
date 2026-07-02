from __future__ import annotations

import json
from functools import lru_cache
from typing import Any

from narad_config import TRACE_DIR


def _empty_session_info(session_id: str) -> dict[str, Any]:
    return {
        "session_id": session_id,
        "ts": None,
        "query": None,
        "avatars": [],
        "total_ms": None,
    }


@lru_cache(maxsize=2048)
def _session_info_cached(session_id: str, mtime_ns: int) -> dict[str, Any]:
    path = TRACE_DIR / f"{session_id}.jsonl"
    start_evt: dict[str, Any] | None = None
    first_avatar: dict[str, Any] | None = None
    done_evt: dict[str, Any] | None = None
    avatars_seen: set[str] = set()
    avatars: list[str] = []
    summed_latency_ms = 0

    try:
        with path.open() as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    event = json.loads(line)
                except Exception:
                    continue

                name = event.get("event")
                if name == "session_start" and start_evt is None:
                    start_evt = event
                elif name == "avatar_start" and first_avatar is None:
                    first_avatar = event
                elif name == "avatar_done":
                    avatar = event.get("avatar")
                    if avatar and avatar not in avatars_seen:
                        avatars_seen.add(avatar)
                        avatars.append(avatar)
                    summed_latency_ms += int(event.get("latency_ms") or 0)
                elif name == "session_done":
                    done_evt = event
        ts = (start_evt or first_avatar or {}).get("ts")
        query = (start_evt or first_avatar or {}).get("task")
        total_ms = done_evt.get("total_ms") if done_evt else None
        if total_ms is None and summed_latency_ms:
            total_ms = summed_latency_ms
        return {
            "session_id": session_id,
            "ts": ts,
            "query": query,
            "avatars": avatars,
            "total_ms": total_ms,
        }
    except Exception:
        return _empty_session_info(session_id)


def session_info(session_id: str) -> dict[str, Any]:
    path = TRACE_DIR / f"{session_id}.jsonl"
    if not path.exists():
        return _empty_session_info(session_id)
    try:
        mtime_ns = path.stat().st_mtime_ns
    except Exception:
        mtime_ns = 0
    return dict(_session_info_cached(session_id, mtime_ns))
