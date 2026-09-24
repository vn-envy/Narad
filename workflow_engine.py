"""Durable state machine for Narad's predefined Workflow Paths.

The engine owns transitions, schedules, approvals, and task mirroring. Agents
receive one compact stage packet and never decide whether a side-effect gate can
be bypassed.

A stage advances only when its declared ``done_when`` holds against evidence
the engine verifies itself (workflow_evidence.py): tool receipts the server
recorded in a turn bound to the run, confirmed document reviews, finished Kriya
tasks, executed approvals, files under the owner's own folder, the person's own
tap, or the Gurukul loop. The stage owner reports with ``report_stage_result``
(done | needs_input | blocked | in_progress); a reply's text never completes a
stage. Evidence that lands later (a review saved on the phone, a task that
finishes) settles the stage on the next read, Kala tick or save hook.
"""

from __future__ import annotations

import importlib.util
import json
import os
import re
import sqlite3
import threading
from calendar import monthrange
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import workflow_evidence
from narad_config import WORKFLOW_DB
from workflow_models import WorkflowEvent, WorkflowRun, WorkflowSchedule
from workflow_packs import get_pack, list_packs

_RUN_DDL = """
CREATE TABLE IF NOT EXISTS workflow_runs (
    run_id TEXT PRIMARY KEY,
    workflow_id TEXT NOT NULL,
    workflow_version INTEGER NOT NULL,
    user_id TEXT NOT NULL,
    title TEXT NOT NULL,
    status TEXT NOT NULL,
    current_stage_id TEXT,
    project_id TEXT,
    session_id TEXT,
    inputs_json TEXT NOT NULL,
    state_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    completed_at TEXT
)
"""

_EVENT_DDL = """
CREATE TABLE IF NOT EXISTS workflow_events (
    event_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    stage_id TEXT,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL
)
"""

_SCHEDULE_DDL = """
CREATE TABLE IF NOT EXISTS workflow_schedules (
    schedule_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    title TEXT NOT NULL,
    cadence TEXT NOT NULL,
    timezone TEXT NOT NULL,
    time_of_day TEXT,
    weekdays_json TEXT NOT NULL,
    day_of_month INTEGER,
    interval_minutes INTEGER,
    next_run_at TEXT,
    last_run_at TEXT,
    enabled INTEGER NOT NULL DEFAULT 1,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
)
"""

# Path records: the career application tracker and what watches found. Kept
# beside the run (not in its state blob) because they are updated one by one.
_RECORD_DDL = """
CREATE TABLE IF NOT EXISTS workflow_records (
    record_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    record_key TEXT NOT NULL,
    data_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
)
"""

_INDEXES = (
    "CREATE INDEX IF NOT EXISTS workflow_runs_user_idx ON workflow_runs (user_id, updated_at DESC)",
    "CREATE INDEX IF NOT EXISTS workflow_runs_status_idx ON workflow_runs (status, updated_at DESC)",
    "CREATE INDEX IF NOT EXISTS workflow_events_run_idx ON workflow_events (run_id, created_at DESC)",
    "CREATE INDEX IF NOT EXISTS workflow_schedules_due_idx ON workflow_schedules (enabled, next_run_at)",
    "CREATE UNIQUE INDEX IF NOT EXISTS workflow_records_key ON workflow_records (run_id, kind, record_key)",
)

_TERMINAL_STATUSES = {"completed", "cancelled"}
_ACTIVE_STATUSES = {"active", "waiting_for_user", "waiting_confirmation", "paused"}
# Statuses in which a chat thread's turns belong to the run bound to it.
_THREAD_BOUND_STATUSES = ("active", "waiting_for_user", "waiting_confirmation")
_TIME_RE = re.compile(r"^(?:[01]\d|2[0-3]):[0-5]\d$")
APPLICATION_STATUSES = ("shortlisted", "applied", "interview", "offer", "rejected", "withdrawn", "no_response")
_APPLIED_OR_LATER = frozenset({"applied", "interview", "offer", "rejected", "no_response"})
# One process owns the database; this keeps a chat turn, a Kala tick and a
# review save from interleaving their read-modify-write of one run's state.
_RUN_LOCK = threading.RLock()


class StageNotDone(ValueError):
    """The current stage's done_when does not hold on verifiable evidence."""

    def __init__(self, stage_title: str, missing: list[str]) -> None:
        self.missing = missing
        super().__init__(f"{stage_title} is not done yet. Still needed: {'; '.join(missing) or 'evidence'}.")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime | None = None) -> str:
    return (value or _now()).astimezone(timezone.utc).isoformat()


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _loads(value: str | None, default: Any) -> Any:
    if not value:
        return default
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return default


def _conn() -> sqlite3.Connection:
    Path(WORKFLOW_DB).parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(WORKFLOW_DB), timeout=10, check_same_thread=False)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA busy_timeout=5000")
    con.execute(_RUN_DDL)
    con.execute(_EVENT_DDL)
    con.execute(_SCHEDULE_DDL)
    con.execute(_RECORD_DDL)
    for ddl in _INDEXES:
        con.execute(ddl)
    con.commit()
    return con


def _row_to_run(row: sqlite3.Row) -> WorkflowRun:
    return WorkflowRun(
        run_id=row["run_id"],
        workflow_id=row["workflow_id"],
        workflow_version=int(row["workflow_version"]),
        user_id=row["user_id"],
        title=row["title"],
        status=row["status"],
        current_stage_id=row["current_stage_id"],
        project_id=row["project_id"],
        session_id=row["session_id"],
        inputs=_loads(row["inputs_json"], {}),
        state=_loads(row["state_json"], {}),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        completed_at=row["completed_at"],
    )


def _row_to_event(row: sqlite3.Row) -> WorkflowEvent:
    return WorkflowEvent(
        event_id=row["event_id"],
        run_id=row["run_id"],
        user_id=row["user_id"],
        event_type=row["event_type"],
        stage_id=row["stage_id"],
        payload=_loads(row["payload_json"], {}),
        created_at=row["created_at"],
    )


def _row_to_schedule(row: sqlite3.Row) -> WorkflowSchedule:
    return WorkflowSchedule(
        schedule_id=row["schedule_id"],
        run_id=row["run_id"],
        user_id=row["user_id"],
        title=row["title"],
        cadence=row["cadence"],
        timezone=row["timezone"],
        time_of_day=row["time_of_day"],
        weekdays=[int(item) for item in _loads(row["weekdays_json"], [])],
        day_of_month=row["day_of_month"],
        interval_minutes=row["interval_minutes"],
        next_run_at=row["next_run_at"],
        last_run_at=row["last_run_at"],
        enabled=bool(row["enabled"]),
        payload=_loads(row["payload_json"], {}),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _capability_flags() -> dict[str, bool]:
    try:
        from computer_use_skill import browser_runtime_status

        computer = bool(browser_runtime_status().get("available"))
    except Exception:
        computer = False
    try:
        from google_workspace import status as google_workspace_status

        google_services = google_workspace_status().get("services", {})
    except Exception:
        google_services = {}
    return {
        "planning": True,
        "learning": importlib.util.find_spec("learning_workspace") is not None,
        "health": importlib.util.find_spec("health_skill") is not None,
        "finance": importlib.util.find_spec("finance_skill") is not None,
        "documents": importlib.util.find_spec("document_skill") is not None,
        "presentation": importlib.util.find_spec("sandbox_skill") is not None,
        "filesystem": importlib.util.find_spec("local_skill") is not None,
        "sql": importlib.util.find_spec("sql_skill") is not None,
        "tts": importlib.util.find_spec("voice_engine") is not None,
        "search": bool(
            os.environ.get("EXA_API_KEY")
            or os.environ.get("FIRECRAWL_API_KEY")
        ),
        "computer": computer,
        "calendar": bool(google_services.get("calendar", {}).get("read")),
        "email": bool(google_services.get("gmail", {}).get("read")),
    }


def _pack_readiness(pack: dict[str, Any]) -> dict[str, Any]:
    flags = _capability_flags()
    missing_required = [name for name in pack.get("required_capabilities", []) if not flags.get(name, False)]
    missing_optional = [name for name in pack.get("optional_capabilities", []) if not flags.get(name, False)]
    return {
        "status": "unavailable" if missing_required else ("limited" if missing_optional else "ready"),
        "missing_required": missing_required,
        "missing_optional": missing_optional,
        "capabilities": {name: flags.get(name, False) for name in sorted(set(pack.get("required_capabilities", []) + pack.get("optional_capabilities", [])))},
    }


def list_workflow_definitions() -> list[dict[str, Any]]:
    result = []
    for pack in list_packs():
        pack["readiness"] = _pack_readiness(pack)
        result.append(pack)
    return result


def _normalise_inputs(pack: dict[str, Any], supplied: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    fields = {field["key"]: field for field in pack.get("intake", [])}
    values: dict[str, Any] = {}
    missing: list[str] = []
    for key, field in fields.items():
        value = supplied.get(key, field.get("default"))
        if field.get("kind") == "boolean":
            value = bool(value)
        elif field.get("kind") == "number" and value not in (None, ""):
            try:
                value = float(value)
                if value.is_integer():
                    value = int(value)
            except (TypeError, ValueError):
                value = field.get("default")
        elif value is not None:
            value = str(value).strip()
        if field.get("required") and (value is None or value == ""):
            missing.append(key)
        values[key] = value
    return values, missing


def intake_questions(pack: dict[str, Any], inputs: dict[str, Any], *, limit: int = 2) -> list[dict[str, Any]]:
    """The next one or two missing required details, as short questions."""
    _values, missing = _normalise_inputs(pack, inputs)
    fields = {field["key"]: field for field in pack.get("intake", [])}
    return [
        {
            "key": key,
            "label": fields[key]["label"],
            "question": fields[key].get("ask") or f"{fields[key]['label']}?",
            "kind": fields[key].get("kind", "text"),
            "options": fields[key].get("options", []),
            "placeholder": fields[key].get("placeholder", ""),
        }
        for key in missing[:max(1, limit)]
    ]


def intake_prefill(workflow_id: str, *, user_id: str) -> dict[str, Any]:
    """Values to start a path with: the phone's time zone and what this person
    gave their previous run of the same path (fields marked ``carry``)."""
    pack = get_pack(workflow_id)
    if not pack:
        raise ValueError(f"Unknown workflow: {workflow_id}")
    values: dict[str, Any] = {}
    sources: dict[str, str] = {}
    try:
        from vahana import load_preferences

        zone = load_preferences(user_id).get("timezone")
    except Exception:
        zone = None
    if zone and any(field["key"] == "timezone" for field in pack.get("intake", [])):
        values["timezone"], sources["timezone"] = zone, "profile"
    previous = next(
        (run for run in list_workflow_runs(user_id=user_id, workflow_id=workflow_id, limit=10) if run.status != "cancelled"),
        None,
    )
    if previous:
        for field in pack.get("intake", []):
            value = previous.inputs.get(field["key"])
            if field.get("carry") and value not in (None, "", False):
                values[field["key"]], sources[field["key"]] = value, "previous_run"
    return {"values": values, "sources": sources}


def _stage(pack: dict[str, Any], stage_id: str | None) -> dict[str, Any] | None:
    if not stage_id:
        return None
    return next((item for item in pack.get("stages", []) if item["id"] == stage_id), None)


def _next_stage_id(pack: dict[str, Any], stage_id: str) -> str | None:
    stages = pack.get("stages", [])
    for index, item in enumerate(stages):
        if item["id"] == stage_id:
            return stages[index + 1]["id"] if index + 1 < len(stages) else None
    return None


def _append_event(
    run: WorkflowRun,
    event_type: str,
    *,
    stage_id: str | None = None,
    payload: dict[str, Any] | None = None,
    event_id: str | None = None,
) -> WorkflowEvent | None:
    event = WorkflowEvent(
        event_id=event_id or f"wfe_{uuid4().hex[:12]}",
        run_id=run.run_id,
        user_id=run.user_id,
        event_type=event_type,
        stage_id=stage_id,
        payload=payload or {},
        created_at=_iso(),
    )
    with _conn() as con:
        cursor = con.execute(
            "INSERT OR IGNORE INTO workflow_events VALUES (?,?,?,?,?,?,?)",
            (
                event.event_id,
                event.run_id,
                event.user_id,
                event.event_type,
                event.stage_id,
                _json(event.payload),
                event.created_at,
            ),
        )
    return event if cursor.rowcount else None


def _save_run(run: WorkflowRun) -> WorkflowRun:
    run.updated_at = _iso()
    with _conn() as con:
        con.execute(
            """
            UPDATE workflow_runs
            SET title=?, status=?, current_stage_id=?, project_id=?, session_id=?,
                inputs_json=?, state_json=?, updated_at=?, completed_at=?
            WHERE run_id=?
            """,
            (
                run.title,
                run.status,
                run.current_stage_id,
                run.project_id,
                run.session_id,
                _json(run.inputs),
                _json(run.state),
                run.updated_at,
                run.completed_at,
                run.run_id,
            ),
        )
    return run


def get_workflow_run(run_id: str) -> WorkflowRun | None:
    with _conn() as con:
        row = con.execute("SELECT * FROM workflow_runs WHERE run_id=?", (run_id,)).fetchone()
    return _row_to_run(row) if row else None


def list_workflow_events(run_id: str, *, limit: int = 100) -> list[WorkflowEvent]:
    with _conn() as con:
        rows = con.execute(
            "SELECT * FROM workflow_events WHERE run_id=? ORDER BY created_at DESC LIMIT ?",
            (run_id, max(1, min(limit, 500))),
        ).fetchall()
    return [_row_to_event(row) for row in rows]


def list_workflow_schedules(run_id: str) -> list[WorkflowSchedule]:
    with _conn() as con:
        rows = con.execute(
            "SELECT * FROM workflow_schedules WHERE run_id=? ORDER BY created_at ASC",
            (run_id,),
        ).fetchall()
    return [_row_to_schedule(row) for row in rows]


def get_workflow_schedule(schedule_id: str) -> WorkflowSchedule | None:
    with _conn() as con:
        row = con.execute(
            "SELECT * FROM workflow_schedules WHERE schedule_id=?",
            (schedule_id,),
        ).fetchone()
    return _row_to_schedule(row) if row else None


def _safe_timezone(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name or "UTC")
    except ZoneInfoNotFoundError:
        return ZoneInfo("UTC")


def _local_time(value: str | None, fallback: str = "09:00") -> tuple[int, int, str]:
    candidate = value if value and _TIME_RE.match(value) else fallback
    hour, minute = (int(part) for part in candidate.split(":"))
    return hour, minute, candidate


def _next_for_parts(
    *,
    cadence: str,
    timezone_name: str,
    time_of_day: str | None,
    weekdays: list[int],
    day_of_month: int | None,
    interval_minutes: int | None,
    after: datetime,
) -> datetime | None:
    zone = _safe_timezone(timezone_name)
    local = after.astimezone(zone)
    hour, minute, _ = _local_time(time_of_day)
    if cadence == "interval":
        return after + timedelta(minutes=max(5, int(interval_minutes or 60)))
    if cadence == "daily":
        candidate = local.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if candidate <= local:
            candidate += timedelta(days=1)
        return candidate.astimezone(timezone.utc)
    if cadence == "weekly":
        allowed = sorted({int(day) % 7 for day in (weekdays or [0])})
        for offset in range(0, 8):
            day = local + timedelta(days=offset)
            candidate = day.replace(hour=hour, minute=minute, second=0, microsecond=0)
            if day.weekday() in allowed and candidate > local:
                return candidate.astimezone(timezone.utc)
        return None
    if cadence == "monthly":
        wanted = max(1, min(int(day_of_month or 1), 31))
        year, month = local.year, local.month
        for _ in range(14):
            day = min(wanted, monthrange(year, month)[1])
            candidate = local.replace(year=year, month=month, day=day, hour=hour, minute=minute, second=0, microsecond=0)
            if candidate > local:
                return candidate.astimezone(timezone.utc)
            month += 1
            if month > 12:
                month = 1
                year += 1
        return None
    return None


def _create_schedules(run: WorkflowRun, pack: dict[str, Any], *, replace: bool = True) -> None:
    """Create the run's schedules; ``replace=False`` keeps any that already exist
    (a path whose intake finished in chat must not re-enable a muted schedule)."""
    timezone_name = str(run.inputs.get("timezone") or "UTC")
    now = _now()
    for template in pack.get("schedule_templates", []):
        enabled_by = template.get("enabled_by")
        if enabled_by and not bool(run.inputs.get(enabled_by)):
            continue
        time_of_day = str(
            run.inputs.get(template.get("time_field"))
            or template.get("time")
            or "09:00"
        )
        cadence = str(template.get("cadence") or "weekly")
        weekdays = [int(day) for day in template.get("weekdays", [])]
        day_of_month = int(template.get("day", 1)) if cadence == "monthly" else None
        interval_minutes = int(template.get("interval_minutes", 60)) if cadence == "interval" else None
        next_run = _next_for_parts(
            cadence=cadence,
            timezone_name=timezone_name,
            time_of_day=time_of_day,
            weekdays=weekdays,
            day_of_month=day_of_month,
            interval_minutes=interval_minutes,
            after=now,
        )
        created = _iso(now)
        schedule_id = f"wfs_{run.run_id[4:12]}_{template['id']}"
        with _conn() as con:
            con.execute(
                f"""
                INSERT OR {'REPLACE' if replace else 'IGNORE'} INTO workflow_schedules (
                    schedule_id, run_id, user_id, title, cadence, timezone, time_of_day,
                    weekdays_json, day_of_month, interval_minutes, next_run_at, last_run_at,
                    enabled, payload_json, created_at, updated_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    schedule_id,
                    run.run_id,
                    run.user_id,
                    template["title"],
                    cadence,
                    timezone_name,
                    time_of_day,
                    _json(weekdays),
                    day_of_month,
                    interval_minutes,
                    _iso(next_run) if next_run else None,
                    None,
                    1,
                    _json({"template_id": template["id"], "target_stage": template.get("target_stage")}),
                    created,
                    created,
                ),
            )


def start_workflow_run(
    workflow_id: str,
    *,
    user_id: str = "default",
    inputs: dict[str, Any] | None = None,
    title: str | None = None,
    session_id: str | None = None,
    partial: bool = False,
) -> WorkflowRun:
    """Start a run. With ``partial`` (a start from a chat card) missing details
    are allowed: the run opens on its intake stage, prefilled from the profile
    and the last run of this path, and the owner asks one or two at a time.
    ``session_id`` binds the run to that chat thread at once."""
    pack = get_pack(workflow_id)
    if not pack:
        raise ValueError(f"Unknown workflow: {workflow_id}")
    readiness = _pack_readiness(pack)
    if readiness["missing_required"]:
        raise RuntimeError(f"Workflow unavailable: missing {', '.join(readiness['missing_required'])}")
    supplied = {key: value for key, value in (inputs or {}).items() if value not in (None, "")}
    if partial:
        supplied = {**intake_prefill(workflow_id, user_id=user_id)["values"], **supplied}
    values, missing = _normalise_inputs(pack, supplied)
    if missing and not partial:
        raise ValueError(f"Missing required intake fields: {', '.join(missing)}")
    now = _iso()
    stages = pack["stages"]
    intake_stage = stages[0]
    intake_done = not missing
    next_stage = (stages[1]["id"] if len(stages) > 1 else None) if intake_done else intake_stage["id"]
    state: dict[str, Any] = {
        "cycle": 1,
        "completed_stage_ids": [intake_stage["id"]] if intake_done else [],
        "stage_outputs": {
            intake_stage["id"]: {
                "status": "done",
                "summary": f"{pack['title']} intake captured.",
                "done_when": [{"text": workflow_evidence.describe(item), "met": True, "detail": ""}
                              for item in intake_stage.get("done_when", [])],
                "recorded_at": now,
            }
        } if intake_done else {},
        "stage_evidence": {},
        "artifacts": [],
        "citations": [],
        "feedback": [],
        "versions": [],
        "confirmation": None,
        "last_stage_result": None,
        "readiness_at_start": readiness,
    }
    if next_stage:
        state["stage_evidence"][next_stage] = _fresh_evidence(1)
    if workflow_id == "teach":
        try:
            from learning_workspace import ensure_workspace

            workspace = ensure_workspace(
                user_id=user_id,
                topic=str(values.get("topic") or "Learning path"),
                mission=str(values.get("outcome") or values.get("topic") or "Learn the topic"),
            )
            state["learning_workspace_id"] = workspace.get("workspace_id")
        except Exception:
            state["learning_workspace_id"] = None
    focus = next(
        (
            str(values.get(key))
            for key in ("target_role", "goal", "destination", "topic", "objective")
            if values.get(key)
        ),
        pack["title"],
    )
    run = WorkflowRun(
        run_id=f"wfr_{uuid4().hex[:12]}",
        workflow_id=workflow_id,
        workflow_version=int(pack["version"]),
        user_id=user_id,
        title=(title or (f"{pack['title']}: {focus}" if focus != pack["title"] else pack["title"]))[:180],
        status=("active" if intake_done else "waiting_for_user") if next_stage else "completed",
        current_stage_id=next_stage,
        project_id=None,
        session_id=session_id or None,
        inputs=values,
        state=state,
        created_at=now,
        updated_at=now,
        completed_at=now if not next_stage else None,
    )
    with _conn() as con:
        con.execute(
            "INSERT INTO workflow_runs VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                run.run_id,
                run.workflow_id,
                run.workflow_version,
                run.user_id,
                run.title,
                run.status,
                run.current_stage_id,
                run.project_id,
                run.session_id,
                _json(run.inputs),
                _json(run.state),
                run.created_at,
                run.updated_at,
                run.completed_at,
            ),
        )
    _append_event(run, "workflow_started", stage_id=intake_stage["id"], payload={
        "title": run.title, "workflow_id": workflow_id, "partial": bool(missing), "bound": bool(session_id),
    })
    if intake_done:
        _append_event(run, "stage_completed", stage_id=intake_stage["id"], payload=state["stage_outputs"][intake_stage["id"]])
        if next_stage:
            _append_event(run, "stage_started", stage_id=next_stage, payload={"cycle": 1})
        _create_schedules(run, pack)
    return run


def list_workflow_runs(
    *,
    user_id: str = "default",
    workflow_id: str | None = None,
    status: str | None = None,
    limit: int = 50,
) -> list[WorkflowRun]:
    clauses = ["user_id=?"]
    params: list[Any] = [user_id]
    if workflow_id:
        clauses.append("workflow_id=?")
        params.append(workflow_id)
    if status:
        clauses.append("status=?")
        params.append(status)
    params.append(max(1, min(limit, 200)))
    query = f"SELECT * FROM workflow_runs WHERE {' AND '.join(clauses)} ORDER BY updated_at DESC LIMIT ?"
    with _conn() as con:
        rows = con.execute(query, params).fetchall()
    return [_row_to_run(row) for row in rows]


def _plain_conditions(stage: dict[str, Any]) -> list[dict[str, Any]]:
    return [{"kind": item.get("kind"), "text": workflow_evidence.describe(item)} for item in stage.get("done_when", [])]


def workflow_run_payload(run: WorkflowRun, *, include_history: bool = True) -> dict[str, Any]:
    pack = get_pack(run.workflow_id) or {"stages": [], "title": run.workflow_id}
    completed = set(run.state.get("completed_stage_ids", []))
    skipped = set(run.state.get("skipped_stage_ids", []))
    current = _stage(pack, run.current_stage_id)
    check = _check(run, pack, current) if current and run.status not in _TERMINAL_STATUSES else None
    stage_items = []
    for stage in pack.get("stages", []):
        if stage["id"] == run.current_stage_id and run.status not in _TERMINAL_STATUSES:
            stage_status = run.status
        elif stage["id"] in skipped and stage["id"] in completed:
            stage_status = "skipped"
        elif stage["id"] in completed:
            stage_status = "done"
        else:
            stage_status = "todo"
        item = {
            **stage,
            "status": stage_status,
            "output": run.state.get("stage_outputs", {}).get(stage["id"]),
            "done_when_text": _plain_conditions(stage),
        }
        if check is not None and stage["id"] == run.current_stage_id:
            item["check"] = {key: check[key] for key in ("met", "conditions", "missing")}
        stage_items.append(item)
    total = len(stage_items)
    progress = round((len(completed) / total) * 100) if total else 100
    evidence = _evidence(run, current["id"]) if current else {}
    report = dict(evidence.get("report") or {})
    payload = {
        **run.to_dict(),
        "definition": {
            "id": pack.get("id"),
            "title": pack.get("title"),
            "description": pack.get("description"),
            "accent": pack.get("accent"),
            "owner": pack.get("owner"),
        },
        "current_stage": current,
        "stages": stage_items,
        "progress_percent": progress,
        "next_action": _next_action(run, current, check=check, pack=pack),
        # What proves (or will prove) the current stage: verified ids with links.
        "evidence": (check or {}).get("refs", []),
        "stage_result": {
            key: report.get(key) for key in ("status", "summary", "questions", "reason", "at")
        } if report else None,
        "intake_questions": intake_questions(pack, run.inputs) if current and current["id"] == "intake" else [],
        "applications": [record["data"] for record in list_records(run.run_id, "application")]
        if run.workflow_id == "career" else [],
        "findings": [record["data"] for record in list_records(run.run_id, "finding", limit=20)],
        "versions": list(run.state.get("versions", [])),
        "schedules": [item.to_dict() for item in list_workflow_schedules(run.run_id)],
        # Stages are the canonical execution records. Keep the additive tasks
        # field for existing clients without maintaining a second task database.
        "tasks": [
            {
                "task_id": f"wf:{run.run_id}:{stage['id']}",
                "workflow_run_id": run.run_id,
                "workflow_stage_id": stage["id"],
                "title": stage["title"],
                "description": stage["purpose"],
                "owner": stage["owner"],
                "status": stage["status"],
                "kind": "workflow_stage",
            }
            for stage in stage_items
        ],
        "readiness": _pack_readiness(pack),
    }
    if include_history:
        payload["events"] = [event.to_dict() for event in list_workflow_events(run.run_id)]
    return payload


def _accepts(conditions: list[dict[str, Any]], kind: str) -> bool:
    return any(
        item.get("kind") == kind or (item.get("kind") == "any" and _accepts(item.get("of", []), kind))
        for item in conditions or []
    )


def _exports(stage: dict[str, Any]) -> bool:
    return any(
        item.get("kind") == "artifact_exists" and item.get("artifact") == "export" for item in stage.get("done_when", [])
    )


def _next_action(
    run: WorkflowRun,
    stage: dict[str, Any] | None,
    *,
    check: dict[str, Any] | None = None,
    pack: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """What happens next, in plain words, and which control the Paths screen shows."""
    if run.status == "completed":
        return {"kind": "complete", "label": "Path complete", "prompt": "Review outcomes or add feedback to begin another cycle."}
    if run.status == "cancelled":
        return {"kind": "none", "label": "Path cancelled", "prompt": ""}
    if run.status == "paused":
        return {"kind": "resume", "label": "Resume path", "prompt": "Resume this workflow from its saved stage."}
    confirmation = dict(run.state.get("confirmation") or {})
    approval_url = f"/?approval={confirmation['proposal_id']}" if confirmation.get("proposal_id") else ""
    if run.status == "waiting_confirmation":
        return {"kind": "approve", "label": "Review approval", "url": approval_url,
                "prompt": "Review the exact proposed external action before continuing."}
    if not stage:
        return {"kind": "none", "label": "No active stage", "prompt": ""}
    if stage.get("requires_confirmation") and confirmation.get("status") == "pending":
        return {
            "kind": "approve",
            "label": f"Approve: {stage['title']}",
            "url": approval_url,
            "prompt": "Review the prepared preview and approve only if every external action is correct.",
        }
    conditions = stage.get("done_when", [])
    common = {
        "can_confirm": _accepts(conditions, "user_confirmed"),
        "can_skip": bool(stage.get("optional")),
        "missing": list((check or {}).get("missing", [])),
    }
    continue_prompt = f"Continue my {run.title} path: {stage['title']}."
    if stage["id"] == "intake" and pack is not None:
        questions = intake_questions(pack, run.inputs)
        if questions:
            return {**common, "kind": "answer", "label": "A couple of quick questions",
                    "questions": [item["question"] for item in questions], "prompt": continue_prompt}
    report = dict(_evidence(run, stage["id"]).get("report") or {})
    if report.get("status") == "needs_input" and report.get("questions"):
        return {**common, "kind": "answer", "label": "Narad needs your answer",
                "questions": list(report["questions"]), "prompt": continue_prompt}
    if report.get("status") == "blocked":
        return {**common, "kind": "blocked", "label": f"Blocked: {stage['title']}",
                "detail": str(report.get("reason") or report.get("summary") or ""), "prompt": continue_prompt}
    refs = (check or {}).get("refs", [])
    pending_review = next((ref for ref in refs if ref["kind"] == "review" and ref["status"] == "pending"), None)
    if pending_review:
        return {**common, "kind": "review", "label": "Confirm the values from your document",
                "url": pending_review["url"], "detail": "Nothing is saved until you confirm each value.",
                "prompt": continue_prompt}
    running = next((ref for ref in refs if ref["kind"] == "task" and ref["status"] not in {"done", "failed", "cancelled"}), None)
    if running:
        return {**common, "kind": "wait", "label": "A web task is running", "url": running["url"],
                "detail": running["label"], "prompt": continue_prompt}
    if _exports(stage):
        return {**common, "kind": "export", "label": "Export the final version",
                "detail": "Packs the latest version with its sources and provenance.", "prompt": continue_prompt}
    approved = stage.get("requires_confirmation") and confirmation.get("status") == "approved"
    return {
        **common,
        "kind": "chat",
        "label": (
            f"Execute: {stage['title']}"
            if approved
            else f"{'Prepare' if stage.get('requires_confirmation') else 'Continue'}: {stage['title']}"
        ),
        "detail": next((item["detail"] for item in (check or {}).get("conditions", []) if not item["met"] and item["detail"]), ""),
        "prompt": (
            f"Continue my {run.title} workflow. Execute the approved current stage: {stage['title']}."
            if approved
            else f"Continue my {run.title} workflow. {'Prepare an exact preview for' if stage.get('requires_confirmation') else 'Complete'} the current stage: {stage['title']}."
        ),
    }


def _update_run_fields(run: WorkflowRun, *, status: str | None = None, current_stage_id: str | None | object = ...) -> WorkflowRun:
    if status is not None:
        run.status = status
    if current_stage_id is not ...:
        run.current_stage_id = current_stage_id  # type: ignore[assignment]
    run.completed_at = _iso() if run.status == "completed" else None
    return _save_run(run)


def request_stage_confirmation(run_id: str, *, summary: str = "", details: dict[str, Any] | None = None) -> WorkflowRun:
    run = get_workflow_run(run_id)
    if not run:
        raise KeyError(f"Unknown workflow run: {run_id}")
    pack = get_pack(run.workflow_id) or {}
    stage = _stage(pack, run.current_stage_id)
    if not stage or not stage.get("requires_confirmation"):
        raise ValueError("Current stage does not require confirmation")
    previous = dict(run.state.get("confirmation") or {})
    confirmation = {
        "stage_id": stage["id"],
        "action": stage.get("confirmation_action"),
        "summary": summary or stage["purpose"],
        "details": details or {},
        "status": "pending",
        "requested_at": _iso(),
    }
    # The approval is an Anumati proposal bound to this exact stage preview:
    # a new preview is a new proposal, and the old card stops being valid.
    confirmation["proposal_id"] = _propose_stage_approval(run, stage, confirmation)
    if previous.get("proposal_id") and previous["proposal_id"] != confirmation["proposal_id"]:
        import anumati

        anumati.supersede(previous["proposal_id"], profile_id=run.user_id, reason="Replaced by a newer stage preview")
    run.state["confirmation"] = confirmation
    _update_run_fields(run, status="waiting_confirmation")
    _append_event(run, "confirmation_requested", stage_id=stage["id"], payload=confirmation)
    return run


def _propose_stage_approval(run: WorkflowRun, stage: dict[str, Any], confirmation: dict[str, Any]) -> str:
    import hashlib

    import anumati

    summary = str(confirmation.get("summary") or stage["purpose"])
    details_digest = hashlib.sha256(anumati.canonical_json(confirmation.get("details") or {}).encode()).hexdigest()
    proposal, _created = anumati.propose(
        surface="workflow",
        action=str(stage.get("confirmation_action") or "stage"),
        target=f"{run.run_id}:{stage['id']}",
        args={
            "run_id": run.run_id,
            "stage_id": stage["id"],
            "cycle": run.state.get("cycle", 1),
            "summary": summary,
            "details_sha256": details_digest,
        },
        summary=f"{run.title}, {stage['title']}: {summary[:300]}",
        risk_class="workflow_stage",
        preview={
            "kind": "workflow",
            "run_id": run.run_id,
            "workflow_id": run.workflow_id,
            "run_title": run.title,
            "stage_title": stage["title"],
            "purpose": stage["purpose"],
            "text": summary[:2000],
        },
        profile_id=run.user_id,
        session_id=run.session_id or "",
    )
    return proposal.proposal_id


def approve_stage(run_id: str, *, approved_by: str = "user", proposal_id: str | None = None) -> WorkflowRun:
    """Mark the pending stage approved. Reached through its Anumati proposal:
    a confirmation bound to a proposal is approved only by that proposal."""
    run = get_workflow_run(run_id)
    if not run:
        raise KeyError(f"Unknown workflow run: {run_id}")
    confirmation = dict(run.state.get("confirmation") or {})
    if run.status != "waiting_confirmation" or confirmation.get("status") != "pending":
        raise ValueError("No pending confirmation for this workflow")
    if confirmation.get("proposal_id") and confirmation["proposal_id"] != proposal_id:
        raise PermissionError("Approve this step from its approval card")
    from dharma import gate_action

    action = str(confirmation.get("action") or "")
    verdict = gate_action(
        action,
        avatar="workflow",
        detail=str(confirmation.get("summary") or action),
        metadata={"run_id": run.run_id, "stage_id": run.current_stage_id, "approved_by": approved_by},
    )
    if not verdict.allowed:
        raise PermissionError("; ".join(verdict.reasons))
    confirmation.update({"status": "approved", "approved_at": _iso(), "approved_by": approved_by})
    run.state["confirmation"] = confirmation
    _update_run_fields(run, status="active")
    _append_event(run, "workflow_approved", stage_id=run.current_stage_id, payload=confirmation)
    return run


def approve_pending_stage(run_id: str, *, approved_by: str, device: str = "") -> WorkflowRun:
    """The Paths screen's Approve: decide the stage's proposal and carry it out."""
    import anumati

    run = get_workflow_run(run_id)
    if not run:
        raise KeyError(f"Unknown workflow run: {run_id}")
    confirmation = dict(run.state.get("confirmation") or {})
    if run.status != "waiting_confirmation" or confirmation.get("status") != "pending":
        raise ValueError("No pending confirmation for this workflow")
    proposal_id = confirmation.get("proposal_id")
    if not proposal_id:  # requested before stage approvals were proposals
        stage = _stage(get_pack(run.workflow_id) or {}, run.current_stage_id)
        if not stage:
            raise ValueError("No pending confirmation for this workflow")
        proposal_id = confirmation["proposal_id"] = _propose_stage_approval(run, stage, confirmation)
        run.state["confirmation"] = confirmation
        _save_run(run)
    try:
        anumati.approve(proposal_id, profile_id=run.user_id, decided_by=approved_by, device=device)
    except anumati.ProposalClosed as exc:
        raise PermissionError(str(exc)) from exc
    proposal = anumati.execute_approved(proposal_id, profile_id=run.user_id)
    if proposal.status != "executed":
        raise PermissionError(str((proposal.result or {}).get("summary") or "The approval did not go through"))
    return get_workflow_run(run_id) or run


def _execute_stage_proposal(proposal: Any) -> dict[str, Any]:
    """Anumati executor for path steps: approve exactly the stage it was proposed for."""
    try:
        run = approve_stage(
            str(proposal.args["run_id"]), approved_by=proposal.decided_by or "user", proposal_id=proposal.proposal_id
        )
    except (KeyError, ValueError, PermissionError) as exc:
        return {"status": "error", "summary": str(exc).strip("'")}
    return {"status": "ok", "summary": f"{run.title} can continue with this step.", "run_status": run.status}


# ── Stage evidence and settling ───────────────────────────────────────────────

_MAX_CLAIMED = 20
_MAX_FIELD_CHARS = 4000


def _fresh_evidence(cycle: int) -> dict[str, Any]:
    return {
        "cycle": cycle,
        "opened_at": _iso(),
        "receipts": [],
        "claimed": [],
        "citations": [],
        "report": None,
        "user_confirmed": None,
        "guided": [],
    }


def _evidence(run: WorkflowRun, stage_id: str) -> dict[str, Any]:
    """The current cycle's evidence for a stage (created fresh when missing or stale)."""
    store = run.state.setdefault("stage_evidence", {})
    cycle = int(run.state.get("cycle", 1))
    evidence = store.get(stage_id)
    if not isinstance(evidence, dict) or evidence.get("cycle") != cycle:
        evidence = store[stage_id] = _fresh_evidence(cycle)
    return evidence


def _open_stage(run: WorkflowRun, stage_id: str) -> None:
    """A stage (re)opens with no evidence: what proved it last time proves nothing now."""
    run.state.setdefault("stage_evidence", {})[stage_id] = _fresh_evidence(int(run.state.get("cycle", 1)))


def _merge_receipts(evidence: dict[str, Any], receipts: list[dict[str, Any]] | None) -> int:
    known = {item.get("receipt_id") for item in evidence["receipts"]}
    added = [item for item in receipts or [] if isinstance(item, dict) and item.get("receipt_id") not in known]
    evidence["receipts"] = [*evidence["receipts"], *added][-80:]
    return len(added)


def _approved_for(run: WorkflowRun, stage: dict[str, Any]) -> bool:
    confirmation = dict(run.state.get("confirmation") or {})
    return confirmation.get("stage_id") == stage["id"] and confirmation.get("status") == "approved"


def _missing_inputs(run: WorkflowRun, pack: dict[str, Any]) -> list[str]:
    labels = {field["key"]: field["label"] for field in pack.get("intake", [])}
    return [labels.get(key, key) for key in _normalise_inputs(pack, run.inputs)[1]]


def _check(run: WorkflowRun, pack: dict[str, Any], stage: dict[str, Any]) -> dict[str, Any]:
    """The stage's done_when checked against its verified evidence."""
    confirmation = dict(run.state.get("confirmation") or {})
    evidence = _evidence(run, stage["id"])
    opened_at = str(evidence.get("opened_at") or run.created_at)
    missing_inputs = _missing_inputs(run, pack) if stage["id"] == "intake" else []
    if stage["id"] == "intake" and int(run.state.get("cycle", 1)) > 1 and not evidence.get("inputs_touched"):
        # Reopened by feedback ("goal changed"): the old answers need a fresh look.
        missing_inputs = [*missing_inputs, "your updated details for this cycle"]
    context = workflow_evidence.StageContext(
        profile_id=run.user_id,
        run_id=run.run_id,
        stage_id=stage["id"],
        cycle=int(run.state.get("cycle", 1)),
        run_created_at=run.created_at,
        missing_inputs=missing_inputs,
        stage_proposal_id=str(confirmation.get("proposal_id") or "") if confirmation.get("stage_id") == stage["id"] else "",
        # Tracker rows count only when written or changed while this stage was open.
        record_count=lambda kind, status: _record_count(run.run_id, kind, status, since=opened_at),
    )
    return workflow_evidence.check_done_when(stage.get("done_when") or [], evidence, context)


def _stage_complete(run: WorkflowRun, stage: dict[str, Any], check: dict[str, Any]) -> bool:
    report = _evidence(run, stage["id"]).get("report") or {}
    if not check["met"] or (check["needs_report"] and report.get("status") != "done"):
        return False
    return not stage.get("requires_confirmation") or _approved_for(run, stage)


def _add_versions(run: WorkflowRun, stage: dict[str, Any], artifacts: list[dict[str, Any]]) -> None:
    """Every file a stage produced is a numbered version of the path's output."""
    versions = list(run.state.get("versions", []))
    known = {item.get("id") for item in versions}
    for ref in artifacts:
        if ref.get("id") in known or ref.get("type") == "export":
            continue
        versions.append({
            "version": len(versions) + 1,
            "id": ref["id"],
            "stage_id": stage["id"],
            "stage_title": stage["title"],
            "label": ref.get("label"),
            "type": ref.get("type"),
            "url": ref.get("url"),
            "created_at": ref.get("created_at") or _iso(),
        })
    run.state["versions"] = versions[-50:]


def _move_past(run: WorkflowRun, pack: dict[str, Any], stage: dict[str, Any], result: dict[str, Any]) -> None:
    """Record a finished (or skipped) stage, open the next one and save."""
    run.state["completed_stage_ids"] = list(dict.fromkeys([*run.state.get("completed_stage_ids", []), stage["id"]]))
    run.state.setdefault("stage_outputs", {})[stage["id"]] = result
    run.state["last_stage_result"] = result
    run.state["confirmation"] = None
    next_stage = _next_stage_id(pack, stage["id"])
    run.current_stage_id = next_stage
    run.status = "active" if next_stage else "completed"
    run.completed_at = None if next_stage else _iso()
    if next_stage:
        _open_stage(run, next_stage)
    _save_run(run)
    _append_event(run, "stage_completed" if result["status"] == "done" else "stage_skipped", stage_id=stage["id"], payload={
        key: result.get(key) for key in ("status", "summary", "done_when", "evidence")
    })
    if stage["id"] == "intake":
        # A path started from chat gets its rhythm once its details are known.
        _create_schedules(run, pack, replace=False)
    if next_stage:
        _append_event(run, "stage_started", stage_id=next_stage, payload={"cycle": run.state.get("cycle", 1)})
    else:
        _append_event(run, "workflow_completed", stage_id=stage["id"], payload={"cycle": run.state.get("cycle", 1)})
        _notify_completed(run)


def _advance(run: WorkflowRun, pack: dict[str, Any], stage: dict[str, Any], check: dict[str, Any], *, summary: str = "") -> None:
    evidence = _evidence(run, stage["id"])
    report = evidence.get("report") or {}
    artifacts = [ref for ref in check["refs"] if ref["kind"] == "artifact"]
    result = {
        "status": "done",
        "summary": (
            summary or str(report.get("summary") or "")
            or "; ".join(item["text"] for item in check["conditions"] if item["met"])
        ).strip()[:2000],
        "fields": report.get("fields") or {},
        "done_when": [
            {"text": item["text"], "met": item["met"], "detail": item["detail"], "evidence": item["evidence"][:6]}
            for item in check["conditions"]
        ],
        "evidence": check["refs"][:20],
        "artifacts": artifacts,
        "citations": list(evidence.get("citations", []))[:20],
        "cycle": int(run.state.get("cycle", 1)),
        "recorded_at": _iso(),
        "session_id": run.session_id,
    }
    _add_versions(run, stage, artifacts)
    run.state["artifacts"] = [*run.state.get("artifacts", []), *artifacts][-100:]
    _move_past(run, pack, stage, result)


def _settle(run: WorkflowRun, pack: dict[str, Any]) -> bool:
    """Advance through every stage whose done_when now holds; True if any did."""
    advanced = False
    for _ in range(len(pack.get("stages", [])) + 1):
        if run.status in _TERMINAL_STATUSES or run.status == "paused":
            break
        stage = _stage(pack, run.current_stage_id)
        if not stage:
            break
        check = _check(run, pack, stage)
        if not _stage_complete(run, stage, check):
            break
        _advance(run, pack, stage, check)
        advanced = True
    return advanced


def _owned(run_id: str, user_id: str | None) -> tuple[WorkflowRun, dict[str, Any]]:
    run = get_workflow_run(run_id)
    if not run:
        raise KeyError(f"Unknown workflow run: {run_id}")
    if user_id is not None and run.user_id != user_id:
        raise PermissionError("Workflow belongs to another user")
    pack = get_pack(run.workflow_id)
    if not pack:
        raise ValueError(f"Unknown workflow: {run.workflow_id}")
    return run, pack


def _clean_fields(fields: Any) -> dict[str, Any]:
    """A report's fields, bounded: JSON values only, long text cut."""
    if not isinstance(fields, dict):
        return {}
    clean: dict[str, Any] = {}
    for key, value in list(fields.items())[:20]:
        name = re.sub(r"[^a-z0-9_]", "_", str(key).strip().lower())[:60]
        if not name:
            continue
        try:
            text = json.dumps(value, ensure_ascii=False)
        except (TypeError, ValueError):
            value, text = str(value), json.dumps(str(value))
        if len(text) > _MAX_FIELD_CHARS:
            value = text[:_MAX_FIELD_CHARS]
        clean[name] = value
    return clean


def settle_run(run_id: str) -> WorkflowRun | None:
    """Re-check the current stage (and watch findings) against evidence that may
    have landed since: a review saved on the phone, a task that finished."""
    with _RUN_LOCK:
        run = get_workflow_run(run_id)
        pack = get_pack(run.workflow_id) if run else None
        if not run or not pack:
            return run
        _settle_findings(run)
        if run.status in {"active", "waiting_for_user", "waiting_confirmation"}:
            _settle(run, pack)
        return get_workflow_run(run_id)


def settle_active_runs(*, user_id: str | None = None) -> int:
    """Settle every open run (of one person, or everyone's); returns how many moved."""
    clauses, params = ["status IN (?,?,?)"], list(_THREAD_BOUND_STATUSES)
    if user_id:
        clauses.append("user_id=?")
        params.append(user_id)
    with _conn() as con:
        rows = con.execute(f"SELECT run_id, current_stage_id FROM workflow_runs WHERE {' AND '.join(clauses)}", params).fetchall()
    moved = 0
    for row in rows:
        try:
            settled = settle_run(row["run_id"])
        except Exception:
            continue
        moved += int(bool(settled and settled.current_stage_id != row["current_stage_id"]))
    return moved


def complete_current_stage(
    run_id: str,
    *,
    summary: str = "",
    evidence: dict[str, Any] | None = None,
    citations: list[dict[str, Any]] | None = None,
    session_id: str | None = None,
) -> WorkflowRun:
    """Complete the current stage only if its done_when holds.

    ``evidence`` adds to the stage's evidence first (``receipts``, ``claimed``
    ids, a ``report``, ``guided`` events); it is checked like any other, so an
    id from another profile, a failed tool or an unsaved review proves nothing.
    Raises StageNotDone with what is still missing.
    """
    with _RUN_LOCK:
        run, pack = _owned(run_id, None)
        if run.status in _TERMINAL_STATUSES and run.status != "completed":
            raise ValueError(f"Cannot advance a {run.status} workflow")
        stage = _stage(pack, run.current_stage_id)
        if not stage:
            return run
        if stage.get("requires_confirmation") and not _approved_for(run, stage):
            raise PermissionError("This stage requires explicit confirmation before completion")
        current = _evidence(run, stage["id"])
        extra = dict(evidence or {})
        _merge_receipts(current, extra.get("receipts"))
        current["claimed"] = list(dict.fromkeys([*current["claimed"], *map(str, extra.get("claimed") or [])]))[-_MAX_CLAIMED:]
        if isinstance(extra.get("report"), dict):
            report = dict(extra["report"])
            report["fields"] = {**((current.get("report") or {}).get("fields") or {}), **_clean_fields(report.get("fields"))}
            current["report"] = {**report, "at": _iso()}
        current["guided"] = [*current["guided"], *(extra.get("guided") or [])][-20:]
        current["citations"] = [*current["citations"], *(citations or [])][-40:]
        if session_id and not run.session_id:
            run.session_id = session_id
        check = _check(run, pack, stage)
        if not _stage_complete(run, stage, check):
            _save_run(run)
            raise StageNotDone(stage["title"], check["missing"] or ["the stage owner's done report"])
        _advance(run, pack, stage, check, summary=summary)
        _settle(run, pack)
        return run


def _notify_completed(run: WorkflowRun) -> None:
    """A finished path is a "task done" in the person's Activity, and reaches
    any carer they share finished tasks with. Best-effort: never blocks completion."""
    try:
        from vahana import deliver

        deliver(
            kind="task_done",
            title=f"Path complete: {run.title}",
            body=f"{run.title} has reached the end of its path. Open Work > Paths to review what it produced.",
            user_id=run.user_id,
            source="workflow_engine.completed",
            data={"workflow_id": run.workflow_id, "workflow_run_id": run.run_id},
            summary=f"{run.title} is complete.",
        )
    except Exception:
        pass


def _start_new_cycle(run: WorkflowRun, pack: dict[str, Any], target: str) -> None:
    """Rewind the run to `target` as a fresh cycle (the caller saves it)."""
    run.state["cycle"] = int(run.state.get("cycle", 1)) + 1
    completed = list(run.state.get("completed_stage_ids", []))
    target_index = next(index for index, item in enumerate(pack["stages"]) if item["id"] == target)
    reopen_ids = {item["id"] for item in pack["stages"][target_index:]}
    run.state["completed_stage_ids"] = [item for item in completed if item not in reopen_ids]
    run.state["skipped_stage_ids"] = [item for item in run.state.get("skipped_stage_ids", []) if item not in reopen_ids]
    run.current_stage_id = target
    run.status = "active"
    run.completed_at = None
    run.state["confirmation"] = None
    _open_stage(run, target)


def record_workflow_feedback(run_id: str, event: str, *, details: dict[str, Any] | None = None) -> WorkflowRun:
    run = get_workflow_run(run_id)
    if not run:
        raise KeyError(f"Unknown workflow run: {run_id}")
    pack = get_pack(run.workflow_id)
    if not pack:
        raise ValueError(f"Unknown workflow: {run.workflow_id}")
    target = pack.get("feedback_routes", {}).get(event)
    feedback = {"event": event, "details": details or {}, "recorded_at": _iso(), "routed_to": target}
    run.state.setdefault("feedback", []).append(feedback)
    run.state["feedback"] = run.state["feedback"][-100:]
    if target and _stage(pack, target):
        _start_new_cycle(run, pack, target)
    _save_run(run)
    _append_event(run, "workflow_feedback", stage_id=run.current_stage_id, payload=feedback)
    if target:
        _append_event(run, "stage_reopened", stage_id=target, payload={"feedback_event": event, "cycle": run.state.get("cycle")})
    return run


def record_workflow_checkpoint(
    run_id: str,
    *,
    summary: str,
    details: dict[str, Any] | None = None,
    event_type: str = "workflow_checkpoint",
) -> WorkflowRun:
    """Record durable progress inside a stage without advancing the stage."""
    run = get_workflow_run(run_id)
    if not run:
        raise KeyError(f"Unknown workflow run: {run_id}")
    checkpoint = {
        "summary": summary.strip()[:2000],
        "details": details or {},
        "recorded_at": _iso(),
    }
    run.state.setdefault("checkpoints", []).append(checkpoint)
    run.state["checkpoints"] = run.state["checkpoints"][-100:]
    run.state["last_checkpoint"] = checkpoint
    _save_run(run)
    _append_event(run, event_type, stage_id=run.current_stage_id, payload=checkpoint)
    return run


def _apply_inputs(run: WorkflowRun, pack: dict[str, Any], updates: dict[str, Any]) -> list[str]:
    """Merge intake answers; returns the keys that were accepted."""
    allowed = {field["key"] for field in pack.get("intake", [])}
    accepted = {key: value for key, value in (updates or {}).items() if key in allowed and value not in (None, "")}
    run.inputs = _normalise_inputs(pack, {**run.inputs, **accepted})[0]
    if accepted and run.current_stage_id == "intake":
        _evidence(run, "intake")["inputs_touched"] = _iso()
    return sorted(accepted)


def update_workflow_inputs(run_id: str, updates: dict[str, Any]) -> WorkflowRun:
    """Update intake details. While the run is still on its intake stage the
    answers may come one or two at a time; later, every required detail stays."""
    with _RUN_LOCK:
        run, pack = _owned(run_id, None)
        at_intake = run.current_stage_id == "intake"
        allowed = {field["key"] for field in pack.get("intake", [])}
        merged = {**run.inputs, **{key: value for key, value in updates.items() if key in allowed}}
        _values, missing = _normalise_inputs(pack, merged)
        if missing and not at_intake:
            raise ValueError(f"Missing required intake fields: {', '.join(missing)}")
        keys = _apply_inputs(run, pack, updates)
        _save_run(run)
        _append_event(run, "workflow_inputs_updated", stage_id=run.current_stage_id, payload={"keys": keys})
        _settle(run, pack)
        return get_workflow_run(run_id) or run


def set_workflow_status(run_id: str, status: str) -> WorkflowRun:
    if status not in {"active", "paused", "cancelled"}:
        raise ValueError(f"Unsupported workflow status: {status}")
    run = get_workflow_run(run_id)
    if not run:
        raise KeyError(f"Unknown workflow run: {run_id}")
    if status == "active" and run.status == "completed":
        raise ValueError("Use feedback to begin a new workflow cycle")
    run.status = status
    if status == "cancelled":
        run.completed_at = _iso()
        with _conn() as con:
            con.execute("UPDATE workflow_schedules SET enabled=0, updated_at=? WHERE run_id=?", (_iso(), run_id))
    _save_run(run)
    _append_event(run, f"workflow_{status}", stage_id=run.current_stage_id)
    return run


# ── Stage results, the person's own taps, and the guided loop ────────────────


def _verdict(run: WorkflowRun, pack: dict[str, Any], stage: dict[str, Any], status: str, summary: str, **extra: Any) -> dict[str, Any]:
    nxt = _stage(pack, run.current_stage_id)
    return {
        "status": status,
        "summary": summary,
        "stage": {"id": stage["id"], "title": stage["title"]},
        "next_stage": {"id": nxt["id"], "title": nxt["title"], "owner": nxt["owner"], "purpose": nxt["purpose"]}
        if nxt and nxt["id"] != stage["id"] else None,
        "run_status": run.status,
        **extra,
    }


def submit_stage_result(
    run_id: str,
    *,
    user_id: str,
    session_id: str | None,
    status: str,
    summary: str = "",
    fields: dict[str, Any] | None = None,
    questions: list[str] | None = None,
    reason: str = "",
    evidence_ids: list[str] | None = None,
    receipts: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Record the stage owner's structured result and settle the stage.

    Returns a verdict for the avatar: ``completed`` only when done_when holds on
    verified evidence; ``not_done`` with what is missing otherwise;
    ``approval_requested`` when a gated stage's preview went to an approval
    card; ``recorded`` for needs_input, blocked and in_progress.
    """
    status = str(status or "").strip().lower()
    if status not in workflow_evidence.STAGE_RESULT_STATUSES:
        raise ValueError(f"status must be one of: {', '.join(workflow_evidence.STAGE_RESULT_STATUSES)}")
    with _RUN_LOCK:
        run, pack = _owned(run_id, user_id)
        _assert_bound_session(run, session_id)
        stage = _stage(pack, run.current_stage_id)
        if not stage or run.status in _TERMINAL_STATUSES or run.status == "paused":
            return {"status": "closed", "summary": f"This path is {run.status}; nothing was recorded.",
                    "stage": None, "next_stage": None, "run_status": run.status}
        if session_id and not run.session_id:
            run.session_id = session_id
        evidence = _evidence(run, stage["id"])
        _merge_receipts(evidence, receipts)
        claimed = [str(item).strip() for item in evidence_ids or [] if workflow_evidence.classify_id(str(item))]
        evidence["claimed"] = list(dict.fromkeys([*evidence["claimed"], *claimed]))[-_MAX_CLAIMED:]
        clean = _clean_fields(fields)
        asked = [" ".join(str(item).split())[:300] for item in questions or [] if str(item).strip()][:2]
        report = {
            "status": status,
            "summary": " ".join(str(summary or "").split())[:2000],
            "fields": {**((evidence.get("report") or {}).get("fields") or {}), **clean},
            "questions": asked,
            "reason": " ".join(str(reason or "").split())[:500],
            "at": _iso(),
            "session_id": session_id,
        }
        evidence["report"] = report
        if stage["id"] == "intake" and clean:
            _apply_inputs(run, pack, clean)
        if status in {"needs_input", "blocked"} and run.status == "active":
            run.status = "waiting_for_user"
        elif status in {"done", "in_progress"} and run.status == "waiting_for_user":
            run.status = "active"
        _save_run(run)
        _append_event(run, "stage_result_reported", stage_id=stage["id"], payload={
            key: report[key] for key in ("status", "summary", "questions", "reason")
        } | {"fields": sorted(report["fields"]), "claimed": claimed})

        confirmation = dict(run.state.get("confirmation") or {})
        if (
            stage.get("requires_confirmation")
            and status in {"done", "in_progress"}
            and report["summary"]
            and not _live_confirmation(run, stage, confirmation)
        ):
            # A gated stage's result before approval is its preview: it goes to
            # the person as an approval card, and nothing runs until they approve.
            run = request_stage_confirmation(
                run.run_id, summary=report["summary"], details={"fields": clean, "session_id": session_id},
            )
            return _verdict(
                run, pack, stage, "approval_requested",
                "The preview is on an approval card on the person's phone. Nothing runs until they approve; "
                "tell them in one sentence and do not ask them to type yes.",
            )

        check = _check(run, pack, stage)
        if _stage_complete(run, stage, check):
            _advance(run, pack, stage, check)
            _settle(run, pack)
            nxt = _stage(pack, run.current_stage_id)
            words = (f"Next: {nxt['title']} ({nxt['owner']}): {nxt['purpose']}" if nxt else "The path is complete.")
            return _verdict(run, pack, stage, "completed", f"Done: {stage['title']}. {words}")
        if status == "needs_input":
            return _verdict(run, pack, stage, "recorded",
                            "Saved. Ask the person only these questions: " + " ".join(asked) if asked
                            else "Saved. Ask the person for what is missing, one or two questions at a time.",
                            missing=check["missing"])
        if status == "blocked":
            return _verdict(run, pack, stage, "recorded",
                            f"Recorded as blocked: {report['reason'] or report['summary']}. Tell the person why "
                            "and what would unblock it.", missing=check["missing"])
        details = [
            f"{item['text']}" + (f" ({item['detail']})" if item["detail"] else "")
            for item in check["conditions"] if not item["met"]
        ]
        if not details and stage.get("requires_confirmation") and not _approved_for(run, stage):
            details = ["the person's approval of this step"]
        return _verdict(
            run, pack, stage, "not_done",
            f"Not done yet: {stage['title']}. Still needed: {'; '.join(details) or 'a done report'}. "
            "Tell the person what is left; do not say this step is complete.",
            missing=details,
        )


def _live_confirmation(run: WorkflowRun, stage: dict[str, Any], confirmation: dict[str, Any]) -> bool:
    """Whether this stage already has an approval waiting or given (not one the
    person rejected or that expired)."""
    if confirmation.get("stage_id") != stage["id"] or confirmation.get("status") not in {"pending", "approved"}:
        return False
    if confirmation.get("status") == "approved" or not confirmation.get("proposal_id"):
        return True
    ref = workflow_evidence.verify_proposal(str(confirmation["proposal_id"]), run.user_id)
    return bool(ref and ref["status"] in {"pending", "approved", "executing", "executed"})


def confirm_stage(run_id: str, *, user_id: str, note: str = "") -> WorkflowRun:
    """The person's own "Done" for the current stage. It finishes only stages
    whose done_when accepts the person's word; anything else raises StageNotDone."""
    with _RUN_LOCK:
        run, pack = _owned(run_id, user_id)
        stage = _stage(pack, run.current_stage_id)
        if not stage or run.status in _TERMINAL_STATUSES:
            raise ValueError("This path has no open step")
        if stage.get("requires_confirmation") and not _approved_for(run, stage):
            raise PermissionError("Approve the pending action before completing this stage")
        evidence = _evidence(run, stage["id"])
        evidence["user_confirmed"] = {"by": user_id, "at": _iso(), "note": " ".join(str(note).split())[:300]}
        if stage["id"] == "intake":
            evidence["inputs_touched"] = _iso()
        _save_run(run)
        _append_event(run, "stage_confirmed_by_person", stage_id=stage["id"], payload={"note": evidence["user_confirmed"]["note"]})
        check = _check(run, pack, stage)
        if not _stage_complete(run, stage, check):
            raise StageNotDone(stage["title"], check["missing"] or ["the stage owner's done report"])
        _advance(run, pack, stage, check, summary="You confirmed this step.")
        _settle(run, pack)
        return run


def skip_stage(run_id: str, *, user_id: str) -> WorkflowRun:
    """Skip an optional stage (a lab report the person does not have). It shows as
    skipped, never as done."""
    with _RUN_LOCK:
        run, pack = _owned(run_id, user_id)
        stage = _stage(pack, run.current_stage_id)
        if not stage or run.status in _TERMINAL_STATUSES:
            raise ValueError("This path has no open step")
        if not stage.get("optional"):
            raise ValueError(f"{stage['title']} cannot be skipped")
        run.state["skipped_stage_ids"] = list(dict.fromkeys([*run.state.get("skipped_stage_ids", []), stage["id"]]))
        _move_past(run, pack, stage, {"status": "skipped", "summary": "Skipped by you.", "recorded_at": _iso()})
        _settle(run, pack)
        return run


def record_guided_progress(run_id: str, *, user_id: str, event: str, details: dict[str, Any] | None = None) -> WorkflowRun:
    """Evidence from the Gurukul loop (a graded answer, a skip, a finished
    syllabus) for the current Teach stage; settles it when that is enough."""
    with _RUN_LOCK:
        run, pack = _owned(run_id, user_id)
        stage = _stage(pack, run.current_stage_id)
        if not stage or run.status in _TERMINAL_STATUSES:
            return run
        evidence = _evidence(run, stage["id"])
        evidence["guided"] = [*evidence["guided"], {"event": event, "at": _iso(), **{
            key: (details or {}).get(key) for key in ("correct", "workspace_id") if key in (details or {})
        }}][-20:]
        _save_run(run)
        _settle(run, pack)
        return run


def record_chat_stage_result(
    run_id: str,
    *,
    user_id: str,
    session_id: str,
    response_text: str = "",
    artifacts: list[dict[str, Any]] | None = None,
    citations: list[dict[str, Any]] | None = None,
    receipts: list[dict[str, Any]] | None = None,
    reported: bool = False,
) -> WorkflowRun:
    """Close a chat turn bound to the run: bind the thread, keep the turn's tool
    receipts as evidence, and settle. The reply's text never completes a stage;
    only ``report_stage_result`` and verified evidence do."""
    with _RUN_LOCK:
        run, pack = _owned(run_id, user_id)
        # A turn from any other thread must never touch its stage. Unbound runs bind here.
        _assert_bound_session(run, session_id)
        if not run.session_id:
            run.session_id = session_id
        stage = _stage(pack, run.current_stage_id)
        added = 0
        if stage and run.status not in _TERMINAL_STATUSES:
            evidence = _evidence(run, stage["id"])
            added = _merge_receipts(evidence, receipts)
            evidence["citations"] = [*evidence["citations"], *(citations or [])][-40:]
            run.state["citations"] = [*run.state.get("citations", []), *(citations or [])][-100:]
        _save_run(run)
        if stage and not reported:
            _append_event(run, "chat_turn", stage_id=stage["id"], payload={
                "receipts": added, "note": "No stage result was reported, so the stage stays open.",
            })
        _settle(run, pack)
        return get_workflow_run(run_id) or run


def bound_run_for_session(user_id: str, session_id: str) -> str | None:
    """The open run a chat thread is bound to (the most recently updated), if any."""
    if not session_id or not Path(WORKFLOW_DB).exists():  # no paths yet: nothing to create
        return None
    with _conn() as con:
        row = con.execute(
            "SELECT run_id FROM workflow_runs WHERE user_id=? AND session_id=? AND status IN (?,?,?) "
            "ORDER BY updated_at DESC LIMIT 1",
            (user_id, session_id, *_THREAD_BOUND_STATUSES),
        ).fetchone()
    return str(row["run_id"]) if row else None


def bind_run_to_session(run_id: str, *, user_id: str, session_id: str) -> WorkflowRun:
    """The person chose to continue this path in this chat thread."""
    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,128}", str(session_id or "")):
        raise ValueError("A chat thread id is required")
    with _RUN_LOCK:
        run, _pack = _owned(run_id, user_id)
        if run.status in _TERMINAL_STATUSES:
            raise ValueError(f"This path is {run.status}")
        previous = run.session_id
        run.session_id = session_id
        if run.status == "paused":
            run.status = "active"
        _save_run(run)
        _append_event(run, "workflow_bound", stage_id=run.current_stage_id,
                      payload={"moved": bool(previous and previous != session_id)})
        return run


# ── Path records: the application tracker and watch findings ─────────────────


def _record_count(run_id: str, kind: str, status: str = "", *, since: str = "") -> int:
    count = 0
    for record in list_records(run_id, kind, limit=500):
        data = record["data"]
        if since and record["updated_at"] < since:
            continue
        if status == "applied" and data.get("status") not in _APPLIED_OR_LATER:
            continue
        if status and status != "applied" and data.get("status") != status:
            continue
        count += 1
    return count


def list_records(run_id: str, kind: str, *, limit: int = 100) -> list[dict[str, Any]]:
    with _conn() as con:
        rows = con.execute(
            "SELECT * FROM workflow_records WHERE run_id=? AND kind=? ORDER BY updated_at DESC LIMIT ?",
            (run_id, kind, max(1, min(limit, 500))),
        ).fetchall()
    return [
        {"record_id": row["record_id"], "record_key": row["record_key"], "updated_at": row["updated_at"],
         "created_at": row["created_at"],
         "data": {**_loads(row["data_json"], {}), "record_id": row["record_id"], "updated_at": row["updated_at"]}}
        for row in rows
    ]


def _put_record(
    run: WorkflowRun, kind: str, key: str, data: dict[str, Any], *, merge: bool = True, touch: bool = True,
) -> dict[str, Any]:
    """Insert or update one record. ``touch=False`` annotates without counting as
    a new change (a watch noting a reply is not the person updating the tracker)."""
    now = _iso()
    with _conn() as con:
        row = con.execute(
            "SELECT * FROM workflow_records WHERE run_id=? AND kind=? AND record_key=?", (run.run_id, kind, key)
        ).fetchone()
        if row:
            merged = {**_loads(row["data_json"], {}), **data} if merge else data
            updated = now if touch else row["updated_at"]
            con.execute("UPDATE workflow_records SET data_json=?, updated_at=? WHERE record_id=?",
                        (_json(merged), updated, row["record_id"]))
            return {**merged, "record_id": row["record_id"], "updated_at": updated}
        record_id = f"wrec_{uuid4().hex[:12]}"
        con.execute("INSERT INTO workflow_records VALUES (?,?,?,?,?,?,?,?)",
                    (record_id, run.run_id, run.user_id, kind, key, _json(data), now, now))
        return {**data, "record_id": record_id, "updated_at": now}


def upsert_application(
    run_id: str,
    *,
    user_id: str,
    company: str,
    role: str,
    status: str = "shortlisted",
    link: str = "",
    next_step: str = "",
    date: str = "",
    notes: str = "",
) -> dict[str, Any]:
    """Add or update one role in a Career run's application tracker."""
    company, role = " ".join(str(company).split())[:120], " ".join(str(role).split())[:120]
    status = str(status or "shortlisted").strip().lower().replace(" ", "_")
    if not company or not role:
        raise ValueError("company and role are required")
    if status not in APPLICATION_STATUSES:
        raise ValueError(f"status must be one of: {', '.join(APPLICATION_STATUSES)}")
    link = str(link or "").strip()
    if link and not re.match(r"^https?://", link):
        link = ""
    with _RUN_LOCK:
        run, _pack = _owned(run_id, user_id)
        if run.workflow_id != "career":
            raise ValueError("The application tracker belongs to the Career path")
        data = {"company": company, "role": role, "status": status}
        for key, value in (("link", link[:500]), ("next_step", next_step), ("date", date), ("notes", notes)):
            if str(value or "").strip():
                data[key] = " ".join(str(value).split())[:300] if key != "link" else value
        record = _put_record(run, "application", f"{company.casefold()}|{role.casefold()}", data)
        _append_event(run, "application_tracked", stage_id=run.current_stage_id,
                      payload={"company": company, "role": role, "status": status})
        return record


# ── Watches: schedules that only append findings ─────────────────────────────


def _price_goal(run: WorkflowRun) -> str:
    inputs = run.inputs
    return (
        f"Check current prices for {inputs.get('travelers') or 'the travellers'} going from {inputs.get('origin')} "
        f"to {inputs.get('destination')} on {inputs.get('dates')}. Report the three cheapest reasonable options "
        "with airline or operator, times and total price. Search only: do not sign in, book or pay."
    )


def _start_price_check(run: WorkflowRun) -> dict[str, Any]:
    if not _capability_flags().get("computer"):
        return {"kind": "price_check", "status": "unavailable", "detail": "The browser on the Mac is not available."}
    try:
        from kriya.runtime import runtime

        task = runtime().submit(profile_id=run.user_id, goal=_price_goal(run), surface="browser")
    except Exception as exc:  # the queue is full, the runtime is missing, a bad goal
        return {"kind": "price_check", "status": "unavailable", "detail": str(exc)[:200]}
    return {"kind": "price_check", "status": "started", "task_id": task.task_id, "goal": task.goal[:300]}


def _check_gmail_replies(run: WorkflowRun) -> dict[str, Any]:
    """Read-only: look for replies from the companies in the tracker."""
    tracked = [
        record["data"] for record in list_records(run.run_id, "application")
        if record["data"].get("status") in {"applied", "interview"}
    ]
    if not tracked:
        return {"kind": "reply_check", "status": "nothing_to_watch"}
    try:
        from google_workspace_skill import search_google_mail

        from profile_context import profile_scope
    except ImportError:
        return {"kind": "reply_check", "status": "unavailable", "detail": "Gmail is not set up on this Mac."}
    companies = sorted({item["company"] for item in tracked})[:10]
    query = "newer_than:8d -from:me (" + " OR ".join(f'"{name}"' for name in companies) + ")"
    try:
        with profile_scope(run.user_id):
            result = search_google_mail(query, max_results=20)
    except Exception as exc:
        return {"kind": "reply_check", "status": "unavailable", "detail": str(exc)[:200]}
    if result.get("status") != "ok":
        return {"kind": "reply_check", "status": "unavailable", "detail": str(result.get("message") or "")[:200]}
    seen = {
        message_id
        for record in list_records(run.run_id, "finding", limit=200)
        for message_id in record["data"].get("message_ids", [])
    }
    messages = list(result.get("messages") or [])
    replies = []
    for message in messages:
        if message.get("id") in seen:
            continue
        text = f"{message.get('from', '')} {message.get('subject', '')}".casefold()
        company = next((name for name in companies if name.casefold() in text), "")
        if not company:
            continue
        replies.append({"company": company, "from": str(message.get("from", ""))[:120],
                        "subject": str(message.get("subject", ""))[:200], "date": str(message.get("date", ""))[:60]})
        for item in tracked:
            if item["company"] == company:
                _put_record(run, "application", f"{item['company'].casefold()}|{item['role'].casefold()}",
                            {"reply_seen": {"subject": replies[-1]["subject"], "date": replies[-1]["date"]}},
                            touch=False)
    return {
        "kind": "reply_check",
        "status": "found" if replies else "nothing_new",
        "message_ids": [str(message.get("id")) for message in messages if message.get("id")][:50],
        "replies": replies[:10],
    }


def _run_watch(run: WorkflowRun, template: dict[str, Any], event_id: str) -> dict[str, Any]:
    watch = template.get("watch")
    finding = (
        _start_price_check(run) if watch == "prices"
        else _check_gmail_replies(run) if watch == "gmail_replies"
        else {"kind": str(watch), "status": "unsupported"}
    )
    finding = {**finding, "at": _iso(), "schedule_template": template.get("id")}
    return _put_record(run, "finding", event_id, finding, merge=False)


def _settle_findings(run: WorkflowRun) -> None:
    """A started price check records the task's answer once it has finished."""
    for record in list_records(run.run_id, "finding", limit=20):
        data = record["data"]
        if data.get("kind") != "price_check" or data.get("status") != "started" or not data.get("task_id"):
            continue
        task = workflow_evidence.verify_task(str(data["task_id"]), run.user_id)
        if task and task["status"] in {"done", "failed", "cancelled"}:
            _put_record(run, "finding", record["record_key"], {
                "status": task["status"], "answer": task["answer"], "finished_at": _iso(),
            })


# ── Documents: versions and export ────────────────────────────────────────────


def export_run(run_id: str, *, user_id: str) -> WorkflowRun:
    """Pack the latest version with a provenance note into a zip in the owner's
    own folder; the zip is the export stage's evidence."""
    import zipfile

    from tool_result import profile_run_path

    with _RUN_LOCK:
        run, pack = _owned(run_id, user_id)
        stage = _stage(pack, run.current_stage_id)
        if not stage or not _exports(stage) or run.status in _TERMINAL_STATUSES:
            raise ValueError("Export is the last step; finish the earlier steps first")
        latest = None
        for version in reversed(run.state.get("versions", [])):
            latest = workflow_evidence.verify_artifact({"url": version.get("url"), "label": version.get("label")}, run.user_id)
            if latest:
                latest = {**latest, "version": version.get("version")}
                break
        if not latest:
            raise ValueError("There is no saved version to export yet")
        source = Path(workflow_evidence.ARTIFACTS_DIR) / latest["id"]
        out_dir = Path(workflow_evidence.ARTIFACTS_DIR) / profile_run_path(f"export_{uuid4().hex[:8]}", profile_id=run.user_id)
        out_dir.mkdir(parents=True, exist_ok=True)
        slug = re.sub(r"[^a-z0-9]+", "-", run.title.lower()).strip("-")[:48] or "export"
        target = out_dir / f"{slug}-v{latest['version']}.zip"
        lines = [f"# {run.title}", "", f"Version {latest['version']}: {latest['label']}", ""]
        for key, label in (("objective", "Objective"), ("audience", "Audience")):
            if run.inputs.get(key):
                lines.append(f"{label}: {run.inputs[key]}")
        lines += ["", "## Stages"]
        for item in pack["stages"]:
            output = run.state.get("stage_outputs", {}).get(item["id"]) or {}
            if output.get("summary"):
                lines.append(f"- {item['title']}: {output['summary'][:400]}")
        citations = [item for item in run.state.get("citations", []) if isinstance(item, dict) and item.get("url")]
        if citations:
            lines += ["", "## Sources"] + [f"- {item.get('title') or item['url']}: {item['url']}" for item in citations[:50]]
        lines += ["", "## Versions"] + [
            f"- v{item.get('version')}: {item.get('label')} ({item.get('stage_title')}, {item.get('created_at')})"
            for item in run.state.get("versions", [])
        ]
        with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
            bundle.write(source, arcname=source.name)
            bundle.writestr("provenance.md", "\n".join(lines) + "\n")
        receipt = {
            "receipt_id": f"rct_{uuid4().hex[:12]}", "tool": "path_export", "status": "ok", "ok": True,
            "refs": {"artifacts": [{"type": "export", "label": target.name, "path": str(target), "url": ""}]},
            "summary": f"Exported version {latest['version']}.", "at": _iso(),
        }
        evidence = _evidence(run, stage["id"])
        _merge_receipts(evidence, [receipt])
        _save_run(run)
        _append_event(run, "workflow_exported", stage_id=stage["id"], payload={"version": latest["version"], "file": target.name})
        _settle(run, pack)
        return get_workflow_run(run_id) or run


class WorkflowSessionMismatch(PermissionError):
    """A chat turn arrived from a thread other than the one the run is bound to."""


def _assert_bound_session(run: WorkflowRun, session_id: str | None) -> None:
    # A run is bound to the chat thread it was continued in; unbound runs
    # accept any thread (and bind on their first recorded result).
    if session_id is not None and run.session_id and run.session_id != session_id:
        raise WorkflowSessionMismatch(
            f"Workflow run {run.run_id} is bound to another chat session; ignoring this turn"
        )


def current_stage_owner(run_id: str, *, user_id: str | None = None, session_id: str | None = None) -> str:
    """The avatar declared as owner of the run's current stage ("" if none).

    The chat pre-router sends a stage-bound turn straight to this avatar.
    """
    run = get_workflow_run(run_id)
    if not run or (user_id is not None and run.user_id != user_id):
        return ""
    try:
        _assert_bound_session(run, session_id)
    except WorkflowSessionMismatch:
        return ""
    stage = _stage(get_pack(run.workflow_id) or {}, run.current_stage_id)
    return str((stage or {}).get("owner") or "")


def build_workflow_context(
    run_id: str,
    *,
    user_id: str | None = None,
    session_id: str | None = None,
    max_chars: int = 7000,
) -> str:
    run = get_workflow_run(run_id)
    if not run:
        raise KeyError(f"Unknown workflow run: {run_id}")
    if user_id is not None and run.user_id != user_id:
        raise PermissionError("Workflow belongs to another user")
    _assert_bound_session(run, session_id)
    pack = get_pack(run.workflow_id)
    if not pack:
        raise ValueError(f"Unknown workflow: {run.workflow_id}")
    stage = _stage(pack, run.current_stage_id)
    if not stage:
        return f"[WORKFLOW CONTEXT]\nPath: {run.title}\nStatus: {run.status}\nNo active stage."
    fields = {field["key"]: field["label"] for field in pack.get("intake", [])}
    input_lines = [f"- {fields.get(key, key)}: {value}" for key, value in run.inputs.items() if value not in (None, "", False)]
    recent_outputs = []
    for stage_id, result in list(run.state.get("stage_outputs", {}).items())[-3:]:
        summary = str((result or {}).get("summary", "")).strip()
        if summary:
            recent_outputs.append(f"- {stage_id}: {summary[:500]}")
        # Earlier structured results prefill this stage: do not ask for them again.
        for key, value in list(((result or {}).get("fields") or {}).items())[:4]:
            recent_outputs.append(f"  {key}: {json.dumps(value, ensure_ascii=False)[:300]}")
    check = _check(run, pack, stage)
    finish_lines = [
        f"- [{'met' if item['met'] else 'not yet'}] {item['text']}" + (f" ({item['detail']})" if item["detail"] and not item["met"] else "")
        for item in check["conditions"]
    ]
    wanted = workflow_evidence.reported_fields(stage.get("done_when", []))
    report_line = (
        "Report with report_stage_result: status done | needs_input | blocked | in_progress, a one-line summary"
        + (f", and fields {{{', '.join(wanted)}}} with real content" if wanted else "")
        + ". Name ids that prove the work (rev_..., tsk_..., apr_..., file URLs) in evidence_ids. "
        "Your reply's text never finishes the stage; the Mac checks the finish line itself."
    )
    intake_lines: list[str] = []
    if stage["id"] == "intake":
        asks = intake_questions(pack, run.inputs)
        intake_lines = [
            "Missing details (ask at most two at a time, conversationally; use what you already know from "
            "memory and the conversation, and report answers as fields keyed like this):",
            *[f"- {item['key']}: {item['question']}" for item in asks],
        ]
    waiting = [
        f"- {ref['label']} ({ref['status']}): {ref['url']}"
        for ref in check["refs"] if ref["kind"] in {"review", "task", "approval"} and ref["status"] != "executed"
    ]
    confirmation = dict(run.state.get("confirmation") or {})
    confirmation_line = ""
    if stage.get("requires_confirmation"):
        confirmation_line = (
            f"External-action approval: {confirmation.get('status', 'not requested')}. "
            "Never execute the action unless this says approved."
        )
    safety = {
        "health": "Do not diagnose, prescribe, or override clinician guidance. Escalate red flags. Never send personal health details in an external search query; search only abstracted public questions.",
        "finance": "Provide scenarios and sourced assumptions, not fiduciary certainty or autonomous transactions. Never send account, transaction, or personally identifying details to external search providers.",
        "travel": "Recheck live price and availability immediately before any commitment.",
        "career": "Never submit an application or send a message without the approved action gate.",
    }.get(run.workflow_id, "Keep claims grounded and preserve artifact provenance.")
    packet = "\n".join([
        "[NARAD WORKFLOW CONTEXT - DURABLE RUN]",
        f"Run ID: {run.run_id}",
        f"Path: {pack['title']} / cycle {run.state.get('cycle', 1)}",
        f"Run title: {run.title}",
        f"Current stage: {stage['title']} ({stage['id']})",
        f"Stage owner: {stage['owner']}",
        f"Purpose: {stage['purpose']}",
        f"Preferred tools: {', '.join(stage.get('tools', [])) or 'none required'}",
        confirmation_line,
        f"Safety boundary: {safety}",
        "Finish line (done_when, checked by the Mac):",
        *finish_lines,
        *(["Waiting on:", *waiting] if waiting else []),
        report_line,
        "Ask the person one or two questions at a time. Files come from their phone as attachments in this "
        "chat: ask them to attach one; never ask for a file path on the Mac.",
        *intake_lines,
        "Intake:",
        *input_lines,
        "Recent completed-stage summaries:",
        *(recent_outputs or ["- Intake only; no prior stage output yet."]),
        "Execution rule: complete only this stage. Return a concrete result, artifact, or next decision; do not replay the whole workflow.",
        "[END NARAD WORKFLOW CONTEXT]",
    ])
    return packet[:max_chars]


def _record_scheduled_check_in(
    run_id: str,
    target_stage: str | None,
    schedule: WorkflowSchedule,
    *,
    occurrence: str,
    event_id: str,
) -> WorkflowRun | None:
    """Apply one fired schedule without corrupting the run.

    A pending or approved confirmation is never dropped: the prompt is queued
    behind it. Only a recurring loop stage the run has already reached is
    reopened, and a template marked ``new_cycle_when_complete`` (a weekly role
    scan, a document refresh) starts a fresh cycle once the path is complete;
    every other prompt is only recorded as a check-in, so a schedule never
    skips ahead and never drags a booked trip or a submitted application back
    to research. A ``watch`` template (a price watch, a Gmail reply check)
    never moves the run at all: it only appends a finding. The deterministic
    event id keeps replays idempotent.
    """
    with _RUN_LOCK:
        run = get_workflow_run(run_id)
        if not run:
            return None
        pack = get_pack(run.workflow_id) or {}
        template = next(
            (
                item for item in pack.get("schedule_templates", [])
                if item.get("id") == schedule.payload.get("template_id")
            ),
            {},
        )
        stage_ids = [item["id"] for item in pack.get("stages", [])]
        if not target_stage or target_stage not in stage_ids:
            target_stage = stage_ids[-1] if stage_ids else None
        if not target_stage:
            return run
        current_index = stage_ids.index(run.current_stage_id) if run.current_stage_id in stage_ids else len(stage_ids)
        confirmation = dict(run.state.get("confirmation") or {})
        finding: dict[str, Any] | None = None
        if template.get("mode") == "watch":
            mode = "closed" if run.status == "completed" else "watch"
            if mode == "watch":
                finding = _run_watch(run, template, event_id)
        elif run.status == "waiting_confirmation" or confirmation.get("status") in {"pending", "approved"}:
            mode = "queued"
        elif (_stage(pack, target_stage) or {}).get("recurring") and stage_ids.index(target_stage) <= current_index:
            mode = "reopened"
            run.current_stage_id = target_stage
            run.status = "waiting_for_user"
            run.completed_at = None
            _open_stage(run, target_stage)
        elif run.status == "completed" and template.get("new_cycle_when_complete"):
            mode = "new_cycle"
            _start_new_cycle(run, pack, target_stage)
        elif run.status == "completed":
            mode = "closed"  # the path is done; recorded, never pushed
        else:
            mode = "check_in"
        prompt = {
            "schedule_id": schedule.schedule_id,
            "title": schedule.title,
            "stage_id": target_stage,
            "mode": mode,
            "scheduled_at": occurrence,
            "triggered_at": _iso(),
        }
        if finding is not None:
            prompt["finding"] = {key: finding.get(key) for key in ("kind", "status", "task_id", "detail", "replies")}
        run.state["scheduled_prompt"] = prompt
        _save_run(run)
        _append_event(run, "scheduled_check_in", stage_id=run.current_stage_id, payload=prompt, event_id=f"{event_id}_checkin")
        if mode == "new_cycle":
            _append_event(
                run, "stage_started", stage_id=target_stage,
                payload={"cycle": run.state.get("cycle"), "schedule_id": schedule.schedule_id},
                event_id=f"{event_id}_cycle",
            )
        return run


def _scheduled_push_body(run: WorkflowRun, schedule: WorkflowSchedule) -> str | None:
    """Word the push for what the schedule actually did; None means stay quiet."""
    prompt = run.state.get("scheduled_prompt") or {}
    mode = str(prompt.get("mode") or "")
    if mode in {"reopened", "new_cycle"}:
        return f"{run.title} is ready for its next checkpoint. Open Work > Paths to continue."
    if mode == "queued":
        return f"{run.title} is waiting for your approval before {schedule.title.lower()} can run. Open Work > Paths to review it."
    if mode == "check_in":
        return f"Reminder for {run.title}: {schedule.title.lower()}. Open Work > Paths when you are ready."
    if mode == "watch":
        finding = prompt.get("finding") or {}
        if finding.get("status") == "found":
            replies = finding.get("replies") or []
            companies = ", ".join(sorted({str(item.get("company")) for item in replies}))
            return (f"{len(replies)} new email{'s' if len(replies) != 1 else ''} may be about your applications "
                    f"({companies}). Open Work > Paths to see them.")
        if finding.get("kind") == "price_check" and finding.get("status") == "unavailable":
            return f"Reminder for {run.title}: the {schedule.title.lower()} could not run by itself. Ask in the path's chat to check."
        # A started price check notifies when its task finishes; nothing new stays quiet.
    return None


def fire_due_workflow_schedules(now: datetime | None = None) -> dict[str, Any]:
    """Claim and deliver due workflow schedules exactly once per occurrence."""
    from vahana import deliver

    current = (now or _now()).astimezone(timezone.utc)
    with _conn() as con:
        rows = con.execute(
            "SELECT * FROM workflow_schedules WHERE enabled=1 AND next_run_at IS NOT NULL AND next_run_at<=? ORDER BY next_run_at ASC",
            (_iso(current),),
        ).fetchall()
    fired: list[dict[str, Any]] = []
    for row in rows:
        schedule = _row_to_schedule(row)
        run = get_workflow_run(schedule.run_id)
        if not run or run.status in {"cancelled", "paused"}:
            continue
        occurrence = str(schedule.next_run_at)
        event_id = f"wfe_schedule_{schedule.schedule_id}_{re.sub(r'[^0-9]', '', occurrence)[:14]}"
        event = _append_event(
            run,
            "schedule_triggered",
            stage_id=schedule.payload.get("target_stage"),
            payload={"schedule_id": schedule.schedule_id, "scheduled_at": occurrence, "title": schedule.title},
            event_id=event_id,
        )
        if event is None:
            continue
        next_run = _next_for_parts(
            cadence=schedule.cadence,
            timezone_name=schedule.timezone,
            time_of_day=schedule.time_of_day,
            weekdays=schedule.weekdays,
            day_of_month=schedule.day_of_month,
            interval_minutes=schedule.interval_minutes,
            after=current,
        )
        with _conn() as con:
            con.execute(
                "UPDATE workflow_schedules SET last_run_at=?, next_run_at=?, updated_at=? WHERE schedule_id=?",
                (_iso(current), _iso(next_run) if next_run else None, _iso(current), schedule.schedule_id),
            )
        run = _record_scheduled_check_in(
            run.run_id,
            schedule.payload.get("target_stage"),
            schedule,
            occurrence=occurrence,
            event_id=event_id,
        ) or run
        body = _scheduled_push_body(run, schedule)
        delivered = deliver(
            kind="reminder",
            title=schedule.title,
            body=body,
            user_id=run.user_id,
            source="kala_scheduler.workflow",
            priority="default",
            data={"workflow_id": run.workflow_id, "workflow_run_id": run.run_id, "schedule_id": schedule.schedule_id},
        ) if body else {"status": "skipped", "reason": "path_complete" if run.status == "completed" else "quiet"}
        fired.append({"run_id": run.run_id, "schedule_id": schedule.schedule_id, "delivery": delivered})
    # Evidence that landed since the last tick (a finished task, a review saved
    # on the phone) settles its stage even if nobody opens Paths.
    try:
        settled = settle_active_runs()
    except Exception:
        settled = 0
    return {"fired": len(fired), "items": fired, "settled": settled, "ts": _iso(current)}


def set_schedule_enabled(schedule_id: str, enabled: bool) -> WorkflowSchedule:
    with _conn() as con:
        row = con.execute("SELECT * FROM workflow_schedules WHERE schedule_id=?", (schedule_id,)).fetchone()
        if not row:
            raise KeyError(f"Unknown workflow schedule: {schedule_id}")
        con.execute(
            "UPDATE workflow_schedules SET enabled=?, updated_at=? WHERE schedule_id=?",
            (1 if enabled else 0, _iso(), schedule_id),
        )
        updated = con.execute("SELECT * FROM workflow_schedules WHERE schedule_id=?", (schedule_id,)).fetchone()
    return _row_to_schedule(updated)


def _register_approvals() -> None:
    import anumati

    anumati.register_executor("workflow", _execute_stage_proposal)


_register_approvals()
