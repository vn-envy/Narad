"""
Phase 5 — Karma Log

Append-only log of every mutation to the sutra bank.
Events: promoted | accepted | reverted | expired | blocked_critique | blocked_injection
        | blocked_hallucination | skipped_no_rule | demotion_strike (M4.4)

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
from typing import Any

from narad_config import KARMA_MUTATIONS_PATH as _KARMA_MUTATIONS_PATH
from narad_config import KARMA_PATH as _KARMA_PATH


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

        # Single audit ledger: KARMA_MUTATIONS_PATH. (Events used to be written
        # to both files; load_karma merge-reads so pre-merge history survives.)
        _KARMA_MUTATIONS_PATH.parent.mkdir(parents=True, exist_ok=True)
        with _KARMA_MUTATIONS_PATH.open("a") as f:
            f.write(json.dumps(record) + "\n")
    except Exception:
        pass


def _read_jsonl(path) -> list[dict]:
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
    return rows


def _normalize_event(row: dict) -> dict:
    """Present a stable superset schema regardless of which writer produced the row.

    Two writers share the mutations ledger:
      * karma_log.log_karma      → {action, sutra_id, avatar, detail, ...}
      * smriti_core.log_mutation → {action, entity_type, entity_id, actor, detail, ...}
    Consumers (/karma UI panels) index fields like sutra_id/avatar directly, so a
    log_mutation row without them crashed the frontend. Fill each family from the
    other so every row carries both.
    """
    row.setdefault("action", "unknown")
    row.setdefault("detail", "")
    row.setdefault("entity_type", "sutra")
    if not row.get("sutra_id"):
        row["sutra_id"] = str(row.get("entity_id", "") or "")
    if not row.get("entity_id"):
        row["entity_id"] = str(row.get("sutra_id", "") or "")
    if not row.get("avatar"):
        row["avatar"] = str(row.get("actor", "") or "system")
    if not row.get("actor"):
        row["actor"] = str(row.get("avatar", "") or "system")
    return row


def load_karma(limit: int = 100) -> list[dict]:
    """Load recent karma events, newest first.

    Merge-reads the legacy karma.jsonl and the unified mutations ledger,
    deduplicating by event id (dual-write era wrote the same record to both).
    Every row is normalized to carry both the sutra-event and mutation-ledger
    field families (see _normalize_event).
    """
    seen: set[str] = set()
    events: list[dict] = []
    for row in _read_jsonl(_KARMA_MUTATIONS_PATH) + _read_jsonl(_KARMA_PATH):
        rid = str(row.get("id", ""))
        if rid and rid in seen:
            continue
        if rid:
            seen.add(rid)
        events.append(_normalize_event(row))
    events.sort(key=lambda x: x.get("ts", ""), reverse=True)
    return events[:limit]


def karma_summary() -> dict:
    """Quick stats for the /karma endpoint."""
    events = load_karma(limit=1000)
    counts: dict[str, int] = {}
    for e in events:
        action = e.get("action", "unknown")
        counts[action] = counts.get(action, 0) + 1
    return {
        "total_events": len(events),
        "by_action": counts,
        "recent": events[:20],
    }


def load_mutations(limit: int = 100) -> list[dict]:
    """Load expanded mutation log, newest first (merge-read, dedupe by id)."""
    return load_karma(limit=limit)
