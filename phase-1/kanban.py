"""
Karyakrama — Kanban board for Narad plan steps.

SQLite-backed board at ~/.narad/kanban.db.
KanbanBoard tracks the lifecycle of every PlanStep emitted by Rama:
  backlog → in_progress → review → done | blocked

Steps are populated when Rama creates a plan (PLAN_JSON: block).
Transitions are fired from _make_avatar_tool as avatars start and finish.
Live kanban_update SSE events are emitted on every transition.
"""
from __future__ import annotations

import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))
from narad_config import NARAD_HOME
from plan_models import PlanStep, StepStatus

KANBAN_DB: Path = NARAD_HOME / "kanban.db"

_DDL = """
CREATE TABLE IF NOT EXISTS kanban_steps (
    session_id   TEXT NOT NULL,
    step_id      INTEGER NOT NULL,
    title        TEXT NOT NULL,
    owner        TEXT NOT NULL,
    status       TEXT NOT NULL DEFAULT 'backlog',
    started_at   TEXT,
    completed_at TEXT,
    result_digest TEXT,
    PRIMARY KEY (session_id, step_id)
);
CREATE INDEX IF NOT EXISTS kanban_status_idx ON kanban_steps (status);
CREATE INDEX IF NOT EXISTS kanban_session_idx ON kanban_steps (session_id);
"""


def _conn() -> sqlite3.Connection:
    con = sqlite3.connect(str(KANBAN_DB), check_same_thread=False)
    con.row_factory = sqlite3.Row
    con.executescript(_DDL)
    con.commit()
    return con


class KanbanBoard:
    """Kanban board backed by ~/.narad/kanban.db."""

    def upsert_step(self, session_id: str, step: PlanStep) -> None:
        """Insert or replace a step (called when Rama creates a plan)."""
        with _conn() as con:
            con.execute(
                """
                INSERT OR REPLACE INTO kanban_steps
                    (session_id, step_id, title, owner, status,
                     started_at, completed_at, result_digest)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    session_id,
                    step.step_id,
                    step.description[:200],
                    step.owner,
                    step.status.value,
                    step.started_at,
                    step.completed_at,
                    step.result_digest,
                ),
            )

    def transition(
        self,
        session_id: str,
        step_id: int,
        new_status: StepStatus,
        result_digest: str = "",
    ) -> None:
        """Move a step to a new status and record timing."""
        now = datetime.now(timezone.utc).isoformat()
        started_at = now if new_status == StepStatus.in_progress else None
        completed_at = now if new_status in (StepStatus.done, StepStatus.blocked) else None

        with _conn() as con:
            if started_at:
                con.execute(
                    "UPDATE kanban_steps SET status=?, started_at=? "
                    "WHERE session_id=? AND step_id=?",
                    (new_status.value, started_at, session_id, step_id),
                )
            elif completed_at:
                con.execute(
                    "UPDATE kanban_steps SET status=?, completed_at=?, result_digest=? "
                    "WHERE session_id=? AND step_id=?",
                    (new_status.value, completed_at, result_digest[:120] or None,
                     session_id, step_id),
                )
            else:
                con.execute(
                    "UPDATE kanban_steps SET status=? WHERE session_id=? AND step_id=?",
                    (new_status.value, session_id, step_id),
                )

        # Notion sync hook (fire-and-forget)
        try:
            import os as _os
            if _os.environ.get("NOTION_API_TOKEN"):
                import asyncio as _ao
                from notion_sync import NotionSync as _NS  # type: ignore
                _ns = _NS()
                _ao.get_event_loop().call_soon(lambda _sid=session_id, _stid=step_id, _ns2=_ns:
                    _ao.ensure_future(_ns2.push_kanban_step(
                        _sid, _stid, "", "", new_status.value,
                        started_at, completed_at, result_digest or ""
                    )))
        except Exception:
            pass

    def get_board(self, session_id: str) -> dict[str, Any]:
        """Return board state as {column: [step dicts]} for SSE payload."""
        with _conn() as con:
            rows = con.execute(
                "SELECT * FROM kanban_steps WHERE session_id=? ORDER BY step_id",
                (session_id,),
            ).fetchall()

        columns: dict[str, list[dict]] = {s.value: [] for s in StepStatus}
        for row in rows:
            d = dict(row)
            columns[d["status"]].append(d)

        return {
            "session_id": session_id,
            "columns": columns,
            "total": len(rows),
            "done_count": len(columns[StepStatus.done.value]),
            "blocked_count": len(columns[StepStatus.blocked.value]),
        }

    def get_all_active(self) -> list[dict[str, Any]]:
        """Return all sessions that have at least one in_progress step."""
        with _conn() as con:
            rows = con.execute(
                """
                SELECT DISTINCT session_id FROM kanban_steps
                WHERE status IN ('backlog','in_progress','review')
                ORDER BY rowid DESC LIMIT 20
                """,
            ).fetchall()
        result = []
        for row in rows:
            result.append(self.get_board(row["session_id"]))
        return result

    def find_step_for_avatar(self, session_id: str, avatar_name: str) -> int | None:
        """Return step_id of the first backlog/in_progress step owned by avatar."""
        with _conn() as con:
            row = con.execute(
                """
                SELECT step_id FROM kanban_steps
                WHERE session_id=? AND owner=? AND status IN ('backlog','in_progress')
                ORDER BY step_id LIMIT 1
                """,
                (session_id, avatar_name),
            ).fetchone()
        return row["step_id"] if row else None
