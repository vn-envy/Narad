"""
Phase 5 — Karma Log

Append-only log of every mutation to the sutra bank.
Events: promoted | accepted | reverted | expired | blocked_critique | blocked_injection
        | blocked_hallucination

Schema (one JSON per line):
  {
    "id":                uuid,
    "ts":                ISO timestamp,
    "action":            "promoted" | "accepted" | "reverted" | "expired"
                         | "blocked_critique" | "blocked_injection"
                         | "blocked_hallucination",
    "sutra_id":          str,
    "avatar":            str,
    "detail":            str          (query summary or reason),
    "triggered_by":      str | null   (session_id that produced this event),
    "tapas_score":       float | null (Tapas score; None for user-initiated actions),
    "content_hash":      str | null   (sha256[:12] of the sutra/detail text),
    "critique_passed":   bool | null  (True/False = CAI reviewed; None = not reviewed),
    "hallucination_free": bool | null (False = hallucination detected and blocked),
    "entity_type":       str | null   (defaults to "sutra" for legacy events),
    "policy":            str | null   (e.g. dharma.swapna),
    "provenance_ids":    list[str] | null,
    "metadata":          dict | null,
  }
"""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from narad_config import KARMA_PATH as _KARMA_PATH
from narad_config import KARMA_MUTATIONS_PATH as _KARMA_MUTATIONS_PATH


def log_karma(
    action: str,
    sutra_id: str,
    avatar: str,
    detail: str = "",
    *,
    triggered_by: str | None = None,
    tapas_score: float | None = None,
    critique_passed: bool | None = None,
    hallucination_free: bool | None = None,
    entity_type: str = "sutra",
    policy: str | None = None,
    provenance_ids: list[str] | None = None,
    metadata: dict[str, Any] | None = None,
) -> None:
    """Append a karma event. Best-effort — never raises.

    Args:
        action:             Event type (promoted, accepted, reverted, etc.)
        sutra_id:           Sutra or entity ID involved.
        avatar:             Avatar name.
        detail:             Human-readable summary (truncated to 200 chars).
        triggered_by:       Session ID that caused this event (for automated actions).
        tapas_score:        Quality score from Tapas (None for user-initiated events).
        critique_passed:    Result of CAI self-critique (None if not reviewed).
        hallucination_free: False if Tapas detected hallucination in the response.
    """
    try:
        content_hash = hashlib.sha256(detail.encode()).hexdigest()[:12] if detail else None
        record: dict[str, Any] = {
            "id":       str(uuid.uuid4()),
            "ts":       datetime.now(timezone.utc).isoformat(),
            "action":   action,
            "sutra_id": sutra_id,
            "avatar":   avatar,
            "detail":   detail[:200],
            "entity_type": entity_type,
        }
        if triggered_by is not None:
            record["triggered_by"] = triggered_by
        if tapas_score is not None:
            record["tapas_score"] = round(tapas_score, 4)
        if content_hash is not None:
            record["content_hash"] = content_hash
        if critique_passed is not None:
            record["critique_passed"] = critique_passed
        if hallucination_free is not None:
            record["hallucination_free"] = hallucination_free
        if policy is not None:
            record["policy"] = policy
        if provenance_ids:
            record["provenance_ids"] = provenance_ids
        if metadata:
            record["metadata"] = metadata

        _KARMA_PATH.parent.mkdir(parents=True, exist_ok=True)
        with _KARMA_PATH.open("a") as f:
            f.write(json.dumps(record) + "\n")
        with _KARMA_MUTATIONS_PATH.open("a") as f2:
            f2.write(json.dumps(record) + "\n")
    except Exception:
        pass


def load_karma(limit: int = 100) -> list[dict]:
    """Load recent karma events, newest first."""
    if not _KARMA_PATH.exists():
        return []
    events = []
    for line in _KARMA_PATH.read_text().splitlines():
        if not line.strip():
            continue
        try:
            events.append(json.loads(line))
        except Exception:
            continue
    events.sort(key=lambda x: x.get("ts", ""), reverse=True)
    return events[:limit]


def karma_summary() -> dict:
    """Quick stats for the /karma endpoint."""
    events = load_karma(limit=1000)
    counts: dict[str, int] = {}
    for e in events:
        counts[e["action"]] = counts.get(e["action"], 0) + 1
    return {
        "total_events": len(events),
        "by_action": counts,
        "recent": events[:20],
    }


def load_mutations(limit: int = 100) -> list[dict]:
    """Load expanded mutation log, newest first."""
    path = _KARMA_MUTATIONS_PATH if _KARMA_MUTATIONS_PATH.exists() else _KARMA_PATH
    if not path.exists():
        return []
    rows = []
    for line in path.read_text().splitlines():
        if not line.strip():
            continue
        try:
            rows.append(json.loads(line))
        except Exception:
            continue
    rows.sort(key=lambda x: x.get("ts", ""), reverse=True)
    return rows[:limit]
