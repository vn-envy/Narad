"""Durable Kriya tasks: one SQLite file (WAL) per profile, with an event log.

A task moves through queued -> running -> (waiting_approval | waiting_help)
-> done | failed | cancelled. Every step, verification, approval and help
request is appended to ``task_events`` in plain words, which is the step list
the phone shows. A profile only ever opens its own file, so another profile's
task id is simply not found. Typed values are never written here for secret
fields, and takeover typing is never written at all.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

import profile_context
from profile_context import profile_root, validate_profile_id

STATUSES = ("queued", "running", "waiting_approval", "waiting_help", "done", "failed", "cancelled")
ACTIVE = frozenset({"queued", "running", "waiting_approval", "waiting_help"})
TERMINAL = frozenset({"done", "failed", "cancelled"})
_DB_NAME = "kriya.db"
_ID_RE = re.compile(r"^tsk_[0-9a-f]{16}$")
_PRUNE_AFTER_S = 90 * 24 * 60 * 60

_SCHEMA = """
CREATE TABLE IF NOT EXISTS tasks (
    task_id          TEXT PRIMARY KEY,
    profile_id       TEXT NOT NULL,
    goal             TEXT NOT NULL,
    start_url        TEXT NOT NULL DEFAULT '',
    done_when        TEXT NOT NULL DEFAULT '',
    surface          TEXT NOT NULL,
    status           TEXT NOT NULL,
    detail           TEXT NOT NULL DEFAULT '',
    step             INTEGER NOT NULL DEFAULT 0,
    max_steps        INTEGER NOT NULL,
    proposal_id      TEXT,
    pending_json     TEXT,
    help_json        TEXT,
    session_id       TEXT,
    last_url         TEXT NOT NULL DEFAULT '',
    result_json      TEXT,
    envelope_json    TEXT NOT NULL DEFAULT '{}',
    usage_json       TEXT NOT NULL DEFAULT '{}',
    cancel_requested INTEGER NOT NULL DEFAULT 0,
    created_ts       REAL NOT NULL,
    updated_ts       REAL NOT NULL,
    started_ts       REAL,
    finished_ts      REAL
);
CREATE INDEX IF NOT EXISTS tasks_status ON tasks(status, created_ts);
CREATE TABLE IF NOT EXISTS task_events (
    event_id   INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id    TEXT NOT NULL,
    ts         REAL NOT NULL,
    kind       TEXT NOT NULL,
    step       INTEGER,
    summary    TEXT NOT NULL,
    data_json  TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS task_events_task ON task_events(task_id, event_id);
"""

_READY: set[str] = set()
_JSON_FIELDS = {"pending": "pending_json", "help": "help_json", "result": "result_json",
                "envelope": "envelope_json", "usage": "usage_json"}
_PLAIN_FIELDS = frozenset({
    "status", "detail", "step", "max_steps", "proposal_id", "last_url", "surface",
    "cancel_requested", "started_ts", "finished_ts",
})


class TaskNotFound(KeyError):
    """No task with this id belongs to this profile."""


def _iso(ts: float | None) -> str | None:
    if ts is None:
        return None
    return datetime.fromtimestamp(ts, timezone.utc).isoformat()


@dataclass
class Task:
    task_id: str
    profile_id: str
    goal: str
    start_url: str
    done_when: str
    surface: str
    status: str
    detail: str
    step: int
    max_steps: int
    created_ts: float
    updated_ts: float
    proposal_id: str | None = None
    pending: dict[str, Any] | None = None
    help: dict[str, Any] | None = None
    session_id: str | None = None
    last_url: str = ""
    result: dict[str, Any] | None = None
    envelope: dict[str, Any] = field(default_factory=dict)
    usage: dict[str, Any] = field(default_factory=dict)
    cancel_requested: bool = False
    started_ts: float | None = None
    finished_ts: float | None = None

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "Task":
        def _load(name: str) -> Any:
            value = row[name]
            return json.loads(value) if value else None

        return cls(
            task_id=row["task_id"],
            profile_id=row["profile_id"],
            goal=row["goal"],
            start_url=row["start_url"],
            done_when=row["done_when"],
            surface=row["surface"],
            status=row["status"],
            detail=row["detail"],
            step=int(row["step"]),
            max_steps=int(row["max_steps"]),
            created_ts=row["created_ts"],
            updated_ts=row["updated_ts"],
            proposal_id=row["proposal_id"],
            pending=_load("pending_json"),
            help=_load("help_json"),
            session_id=row["session_id"],
            last_url=row["last_url"],
            result=_load("result_json"),
            envelope=_load("envelope_json") or {},
            usage=_load("usage_json") or {},
            cancel_requested=bool(row["cancel_requested"]),
            started_ts=row["started_ts"],
            finished_ts=row["finished_ts"],
        )

    @property
    def active(self) -> bool:
        return self.status in ACTIVE

    def to_payload(self, events: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        """What the app, the chat stream and notifications see."""
        payload: dict[str, Any] = {
            "id": self.task_id,
            "profile_id": self.profile_id,
            "goal": self.goal,
            "start_url": self.start_url,
            "done_when": self.done_when,
            "surface": self.surface,
            "status": self.status,
            "detail": self.detail,
            "step": self.step,
            "max_steps": self.max_steps,
            "proposal_id": self.proposal_id,
            "help": self.help,
            "session_id": self.session_id,
            "last_url": self.last_url,
            "result": self.result,
            "usage": self.usage,
            "cancel_requested": self.cancel_requested,
            "created_at": _iso(self.created_ts),
            "updated_at": _iso(self.updated_ts),
            "started_at": _iso(self.started_ts),
            "finished_at": _iso(self.finished_ts),
            "live": self.status in {"running", "waiting_approval", "waiting_help"},
        }
        phone = self.envelope.get("phone") or {}
        if phone:  # which phone, and whether Artemis checks the result
            payload["device"] = phone.get("device_label")
            payload["mode"] = phone.get("mode")
        if events is not None:
            payload["events"] = events
        return payload


# ── Storage ──────────────────────────────────────────────────────────────────


def _db_path(profile_id: str) -> Path:
    return profile_root(profile_id) / _DB_NAME


@contextmanager
def _connect(profile_id: str) -> Iterator[sqlite3.Connection]:
    path = _db_path(validate_profile_id(profile_id))
    path.parent.mkdir(parents=True, exist_ok=True)
    fresh = not path.exists()
    con = sqlite3.connect(path, timeout=10, isolation_level=None)
    con.row_factory = sqlite3.Row
    try:
        con.execute("PRAGMA busy_timeout=10000")
        if str(path) not in _READY or fresh:
            con.execute("PRAGMA journal_mode=WAL")
            con.executescript(_SCHEMA)
            _READY.add(str(path))
            try:
                os.chmod(path, 0o600)  # goals and page addresses are personal
            except OSError:
                pass
        yield con
    finally:
        con.close()


@contextmanager
def _transaction(profile_id: str) -> Iterator[sqlite3.Connection]:
    with _connect(profile_id) as con:
        con.execute("BEGIN IMMEDIATE")
        try:
            yield con
        except BaseException:
            con.execute("ROLLBACK")
            raise
        con.execute("COMMIT")


def _fetch(con: sqlite3.Connection, task_id: str) -> Task | None:
    row = con.execute("SELECT * FROM tasks WHERE task_id = ?", (task_id,)).fetchone()
    return Task.from_row(row) if row else None


def valid_task_id(task_id: str) -> bool:
    return bool(_ID_RE.fullmatch(str(task_id or "")))


# ── Tasks ────────────────────────────────────────────────────────────────────


def create_task(
    *,
    profile_id: str,
    goal: str,
    start_url: str = "",
    done_when: str = "",
    surface: str = "browser",
    session_id: str | None = None,
    max_steps: int = 30,
    envelope: dict[str, Any] | None = None,
) -> Task:
    owner = validate_profile_id(profile_id)
    now = time.time()
    task = Task(
        task_id=f"tsk_{uuid.uuid4().hex[:16]}",
        profile_id=owner,
        goal=" ".join(str(goal or "").split())[:2000],
        start_url=str(start_url or "")[:2000],
        done_when=" ".join(str(done_when or "").split())[:1000],
        surface=surface,
        status="queued",
        detail="Waiting to start",
        step=0,
        max_steps=int(max_steps),
        created_ts=now,
        updated_ts=now,
        session_id=session_id,
        envelope=envelope or {},
    )
    with _transaction(owner) as con:
        con.execute(
            "INSERT INTO tasks (task_id, profile_id, goal, start_url, done_when, surface, status, detail, "
            "step, max_steps, session_id, envelope_json, created_ts, updated_ts) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                task.task_id, owner, task.goal, task.start_url, task.done_when, surface, task.status,
                task.detail, 0, task.max_steps, session_id, json.dumps(task.envelope), now, now,
            ),
        )
        _prune(con, now)
    return task


def _prune(con: sqlite3.Connection, now: float) -> None:
    old = [row["task_id"] for row in con.execute(
        "SELECT task_id FROM tasks WHERE status IN ('done', 'failed', 'cancelled') AND created_ts < ?",
        (now - _PRUNE_AFTER_S,),
    ).fetchall()]
    for task_id in old:
        con.execute("DELETE FROM task_events WHERE task_id = ?", (task_id,))
        con.execute("DELETE FROM tasks WHERE task_id = ?", (task_id,))


def get_task(task_id: str, *, profile_id: str) -> Task:
    if not valid_task_id(task_id):
        raise TaskNotFound(task_id)
    with _connect(profile_id) as con:
        task = _fetch(con, task_id)
    if task is None:
        raise TaskNotFound(task_id)
    return task


def list_tasks(*, profile_id: str, status: str | None = None, limit: int = 50) -> list[Task]:
    wanted = [item.strip() for item in str(status or "").split(",") if item.strip()]
    if any(item not in STATUSES for item in wanted):
        raise ValueError(f"status must be one of: {', '.join(STATUSES)}")
    query = "SELECT * FROM tasks"
    params: list[Any] = []
    if wanted:
        query += f" WHERE status IN ({', '.join('?' for _ in wanted)})"
        params.extend(wanted)
    query += " ORDER BY created_ts DESC LIMIT ?"
    params.append(max(1, min(int(limit), 200)))
    with _connect(profile_id) as con:
        rows = con.execute(query, params).fetchall()
    return [Task.from_row(row) for row in rows]


def update_task(task_id: str, *, profile_id: str, **changes: Any) -> Task:
    """Set plain and JSON fields; ``updated_ts`` follows."""
    sets: list[str] = []
    params: list[Any] = []
    for key, value in changes.items():
        if key in _JSON_FIELDS:
            sets.append(f"{_JSON_FIELDS[key]} = ?")
            params.append(None if value is None else json.dumps(value, ensure_ascii=False, default=str))
        elif key in _PLAIN_FIELDS:
            sets.append(f"{key} = ?")
            params.append(int(value) if key == "cancel_requested" else value)
        else:
            raise ValueError(f"Unknown task field {key!r}")
    sets.append("updated_ts = ?")
    params.append(time.time())
    params.append(task_id)
    with _transaction(profile_id) as con:
        con.execute(f"UPDATE tasks SET {', '.join(sets)} WHERE task_id = ?", params)
        task = _fetch(con, task_id)
    if task is None:
        raise TaskNotFound(task_id)
    return task


def add_event(
    task_id: str,
    *,
    profile_id: str,
    kind: str,
    summary: str,
    step: int | None = None,
    data: dict[str, Any] | None = None,
) -> dict[str, Any]:
    now = time.time()
    text = " ".join(str(summary or "").split())[:400]
    with _transaction(profile_id) as con:
        cursor = con.execute(
            "INSERT INTO task_events (task_id, ts, kind, step, summary, data_json) VALUES (?, ?, ?, ?, ?, ?)",
            (task_id, now, kind[:40], step, text, json.dumps(data or {}, ensure_ascii=False, default=str)),
        )
    return {"id": cursor.lastrowid, "at": _iso(now), "kind": kind, "step": step, "summary": text, "data": data or {}}


def list_events(task_id: str, *, profile_id: str, limit: int = 200) -> list[dict[str, Any]]:
    with _connect(profile_id) as con:
        rows = con.execute(
            "SELECT * FROM (SELECT * FROM task_events WHERE task_id = ? ORDER BY event_id DESC LIMIT ?) "
            "ORDER BY event_id",
            (task_id, max(1, min(int(limit), 1000))),
        ).fetchall()
    return [
        {
            "id": row["event_id"],
            "at": _iso(row["ts"]),
            "kind": row["kind"],
            "step": row["step"],
            "summary": row["summary"],
            "data": json.loads(row["data_json"] or "{}"),
        }
        for row in rows
    ]


def active_tasks_everywhere() -> list[Task]:
    """Every unfinished task across profiles, oldest first (resume after a restart)."""
    tasks: list[Task] = []
    root = profile_context.PROFILES_DIR
    if not root.is_dir():
        return tasks
    for path in sorted(root.glob(f"*/{_DB_NAME}")):
        try:
            profile_id = validate_profile_id(path.parent.name)
            with _connect(profile_id) as con:
                rows = con.execute(
                    "SELECT * FROM tasks WHERE status IN ('queued', 'running', 'waiting_approval', 'waiting_help') "
                    "ORDER BY created_ts"
                ).fetchall()
        except (ValueError, sqlite3.Error):
            continue
        tasks.extend(Task.from_row(row) for row in rows)
    return sorted(tasks, key=lambda task: task.created_ts)
