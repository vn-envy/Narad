"""Durable state machine for Narad's predefined Workflow Paths.

The engine owns transitions, schedules, approvals, and task mirroring. Agents
receive one compact stage packet and never decide whether a side-effect gate can
be bypassed.
"""

from __future__ import annotations

import importlib.util
import json
import os
import re
import sqlite3
from calendar import monthrange
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

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

_INDEXES = (
    "CREATE INDEX IF NOT EXISTS workflow_runs_user_idx ON workflow_runs (user_id, updated_at DESC)",
    "CREATE INDEX IF NOT EXISTS workflow_runs_status_idx ON workflow_runs (status, updated_at DESC)",
    "CREATE INDEX IF NOT EXISTS workflow_events_run_idx ON workflow_events (run_id, created_at DESC)",
    "CREATE INDEX IF NOT EXISTS workflow_schedules_due_idx ON workflow_schedules (enabled, next_run_at)",
)

_TERMINAL_STATUSES = {"completed", "cancelled"}
_ACTIVE_STATUSES = {"active", "waiting_for_user", "waiting_confirmation", "paused"}
_TIME_RE = re.compile(r"^(?:[01]\d|2[0-3]):[0-5]\d$")


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


def _create_schedules(run: WorkflowRun, pack: dict[str, Any]) -> None:
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
                """
                INSERT OR REPLACE INTO workflow_schedules (
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
) -> WorkflowRun:
    pack = get_pack(workflow_id)
    if not pack:
        raise ValueError(f"Unknown workflow: {workflow_id}")
    readiness = _pack_readiness(pack)
    if readiness["missing_required"]:
        raise RuntimeError(f"Workflow unavailable: missing {', '.join(readiness['missing_required'])}")
    values, missing = _normalise_inputs(pack, inputs or {})
    if missing:
        raise ValueError(f"Missing required intake fields: {', '.join(missing)}")
    now = _iso()
    stages = pack["stages"]
    intake_stage = stages[0]
    next_stage = stages[1]["id"] if len(stages) > 1 else None
    state: dict[str, Any] = {
        "cycle": 1,
        "completed_stage_ids": [intake_stage["id"]],
        "stage_outputs": {
            intake_stage["id"]: {
                "status": "ok",
                "summary": f"{pack['title']} intake captured.",
                "recorded_at": now,
            }
        },
        "artifacts": [],
        "citations": [],
        "feedback": [],
        "confirmation": None,
        "last_stage_result": None,
        "readiness_at_start": readiness,
    }
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
        title=(title or f"{pack['title']}: {focus}")[:180],
        status="active" if next_stage else "completed",
        current_stage_id=next_stage,
        project_id=None,
        session_id=None,
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
    _append_event(run, "workflow_started", stage_id=intake_stage["id"], payload={"title": run.title, "workflow_id": workflow_id})
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


def workflow_run_payload(run: WorkflowRun, *, include_history: bool = True) -> dict[str, Any]:
    pack = get_pack(run.workflow_id) or {"stages": [], "title": run.workflow_id}
    completed = set(run.state.get("completed_stage_ids", []))
    stage_items = []
    for stage in pack.get("stages", []):
        if stage["id"] in completed:
            stage_status = "done"
        elif stage["id"] == run.current_stage_id:
            stage_status = run.status
        else:
            stage_status = "todo"
        stage_items.append({**stage, "status": stage_status, "output": run.state.get("stage_outputs", {}).get(stage["id"])})
    total = len(stage_items)
    progress = round((len(completed) / total) * 100) if total else 100
    current = _stage(pack, run.current_stage_id)
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
        "next_action": _next_action(run, current),
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


def _next_action(run: WorkflowRun, stage: dict[str, Any] | None) -> dict[str, Any]:
    if run.status == "completed":
        return {"kind": "complete", "label": "Path complete", "prompt": "Review outcomes or add feedback to begin another cycle."}
    if run.status == "cancelled":
        return {"kind": "none", "label": "Path cancelled", "prompt": ""}
    if run.status == "paused":
        return {"kind": "resume", "label": "Resume path", "prompt": "Resume this workflow from its saved stage."}
    if run.status == "waiting_confirmation":
        return {"kind": "approve", "label": "Review approval", "prompt": "Review the exact proposed external action before continuing."}
    if not stage:
        return {"kind": "none", "label": "No active stage", "prompt": ""}
    confirmation = dict(run.state.get("confirmation") or {})
    if stage.get("requires_confirmation") and confirmation.get("status") == "pending":
        return {
            "kind": "approve",
            "label": f"Approve: {stage['title']}",
            "prompt": "Review the prepared preview and approve only if every external action is correct.",
        }
    return {
        "kind": "chat",
        "label": (
            f"Execute: {stage['title']}"
            if stage.get("requires_confirmation") and confirmation.get("status") == "approved"
            else f"{'Prepare' if stage.get('requires_confirmation') else 'Continue'}: {stage['title']}"
        ),
        "prompt": (
            f"Continue my {run.title} workflow. Execute the approved current stage: {stage['title']}."
            if stage.get("requires_confirmation") and confirmation.get("status") == "approved"
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
    confirmation = {
        "stage_id": stage["id"],
        "action": stage.get("confirmation_action"),
        "summary": summary or stage["purpose"],
        "details": details or {},
        "status": "pending",
        "requested_at": _iso(),
    }
    run.state["confirmation"] = confirmation
    _update_run_fields(run, status="waiting_confirmation")
    _append_event(run, "confirmation_requested", stage_id=stage["id"], payload=confirmation)
    return run


def approve_stage(run_id: str, *, approved_by: str = "user") -> WorkflowRun:
    run = get_workflow_run(run_id)
    if not run:
        raise KeyError(f"Unknown workflow run: {run_id}")
    confirmation = dict(run.state.get("confirmation") or {})
    if run.status != "waiting_confirmation" or confirmation.get("status") != "pending":
        raise ValueError("No pending confirmation for this workflow")
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


def complete_current_stage(
    run_id: str,
    *,
    summary: str,
    output: dict[str, Any] | None = None,
    artifacts: list[dict[str, Any]] | None = None,
    citations: list[dict[str, Any]] | None = None,
    session_id: str | None = None,
) -> WorkflowRun:
    run = get_workflow_run(run_id)
    if not run:
        raise KeyError(f"Unknown workflow run: {run_id}")
    if run.status in _TERMINAL_STATUSES and run.status != "completed":
        raise ValueError(f"Cannot advance a {run.status} workflow")
    pack = get_pack(run.workflow_id)
    if not pack:
        raise ValueError(f"Unknown workflow: {run.workflow_id}")
    stage = _stage(pack, run.current_stage_id)
    if not stage:
        return run
    if stage.get("requires_confirmation"):
        confirmation = dict(run.state.get("confirmation") or {})
        if confirmation.get("stage_id") != stage["id"] or confirmation.get("status") != "approved":
            raise PermissionError("This stage requires explicit confirmation before completion")
    result = {
        "status": "ok",
        "summary": summary.strip()[:4000],
        "output": output or {},
        "artifacts": artifacts or [],
        "citations": citations or [],
        "recorded_at": _iso(),
        "session_id": session_id,
    }
    completed = list(dict.fromkeys([*run.state.get("completed_stage_ids", []), stage["id"]]))
    run.state["completed_stage_ids"] = completed
    run.state.setdefault("stage_outputs", {})[stage["id"]] = result
    run.state["artifacts"] = [*run.state.get("artifacts", []), *(artifacts or [])][-100:]
    run.state["citations"] = [*run.state.get("citations", []), *(citations or [])][-100:]
    run.state["last_stage_result"] = result
    run.state["confirmation"] = None
    if session_id:
        run.session_id = session_id
    next_stage = _next_stage_id(pack, stage["id"])
    run.current_stage_id = next_stage
    run.status = "active" if next_stage else "completed"
    run.completed_at = None if next_stage else _iso()
    _save_run(run)
    _append_event(run, "stage_completed", stage_id=stage["id"], payload=result)
    if next_stage:
        _append_event(run, "stage_started", stage_id=next_stage, payload={"cycle": run.state.get("cycle", 1)})
    else:
        _append_event(run, "workflow_completed", stage_id=stage["id"], payload={"cycle": run.state.get("cycle", 1)})
    return run


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
        run.state["cycle"] = int(run.state.get("cycle", 1)) + 1
        completed = list(run.state.get("completed_stage_ids", []))
        target_index = next(index for index, item in enumerate(pack["stages"]) if item["id"] == target)
        reopen_ids = {item["id"] for item in pack["stages"][target_index:]}
        run.state["completed_stage_ids"] = [item for item in completed if item not in reopen_ids]
        run.current_stage_id = target
        run.status = "active"
        run.completed_at = None
        run.state["confirmation"] = None
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


def update_workflow_inputs(run_id: str, updates: dict[str, Any]) -> WorkflowRun:
    run = get_workflow_run(run_id)
    if not run:
        raise KeyError(f"Unknown workflow run: {run_id}")
    pack = get_pack(run.workflow_id) or {}
    allowed = {field["key"] for field in pack.get("intake", [])}
    merged = {**run.inputs, **{key: value for key, value in updates.items() if key in allowed}}
    values, missing = _normalise_inputs(pack, merged)
    if missing:
        raise ValueError(f"Missing required intake fields: {', '.join(missing)}")
    run.inputs = values
    _save_run(run)
    _append_event(run, "workflow_inputs_updated", stage_id=run.current_stage_id, payload={"keys": sorted(updates)})
    return run


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


class WorkflowSessionMismatch(PermissionError):
    """A chat turn arrived from a thread other than the one the run is bound to."""


def _assert_bound_session(run: WorkflowRun, session_id: str | None) -> None:
    # A run is bound to the chat thread it was continued in; unbound runs
    # accept any thread (and bind on their first recorded result).
    if session_id is not None and run.session_id and run.session_id != session_id:
        raise WorkflowSessionMismatch(
            f"Workflow run {run.run_id} is bound to another chat session; ignoring this turn"
        )


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
        "Intake:",
        *input_lines,
        "Recent completed-stage summaries:",
        *(recent_outputs or ["- Intake only; no prior stage output yet."]),
        "Execution rule: complete only this stage. Return a concrete result, artifact, or next decision; do not replay the whole workflow.",
        "[END NARAD WORKFLOW CONTEXT]",
    ])
    return packet[:max_chars]


def record_chat_stage_result(
    run_id: str,
    *,
    user_id: str,
    session_id: str,
    response_text: str,
    artifacts: list[dict[str, Any]] | None = None,
    citations: list[dict[str, Any]] | None = None,
) -> WorkflowRun:
    run = get_workflow_run(run_id)
    if not run:
        raise KeyError(f"Unknown workflow run: {run_id}")
    if run.user_id != user_id:
        raise PermissionError("Workflow belongs to another user")
    # A turn from any other thread must never complete its stage. Unbound runs bind below.
    _assert_bound_session(run, session_id)
    summary = response_text.strip()
    if not summary:
        raise ValueError("Cannot complete a workflow stage from an empty response")
    pack = get_pack(run.workflow_id) or {}
    stage = _stage(pack, run.current_stage_id)
    confirmation = dict(run.state.get("confirmation") or {})
    if stage and stage.get("requires_confirmation") and confirmation.get("status") != "approved":
        run.state.setdefault("stage_outputs", {})[stage["id"]] = {
            "status": "preview",
            "summary": summary[:2000],
            "artifacts": artifacts or [],
            "citations": citations or [],
            "recorded_at": _iso(),
            "session_id": session_id,
        }
        run.state["artifacts"] = [*run.state.get("artifacts", []), *(artifacts or [])][-100:]
        run.state["citations"] = [*run.state.get("citations", []), *(citations or [])][-100:]
        run.session_id = session_id
        _save_run(run)
        return request_stage_confirmation(
            run_id,
            summary=summary[:2000],
            details={"artifacts": artifacts or [], "citations": citations or [], "session_id": session_id},
        )
    return complete_current_stage(
        run_id,
        summary=summary[:2000],
        output={"source": "narad_chat"},
        artifacts=artifacts,
        citations=citations,
        session_id=session_id,
    )


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
    reopened; every other prompt is only recorded as a check-in, so a schedule
    never skips ahead and never drags a booked trip or a submitted application
    back to research. The deterministic event id keeps replays idempotent.
    """
    run = get_workflow_run(run_id)
    if not run:
        return None
    pack = get_pack(run.workflow_id) or {}
    stage_ids = [item["id"] for item in pack.get("stages", [])]
    if not target_stage or target_stage not in stage_ids:
        target_stage = stage_ids[-1] if stage_ids else None
    if not target_stage:
        return run
    current_index = stage_ids.index(run.current_stage_id) if run.current_stage_id in stage_ids else len(stage_ids)
    confirmation = dict(run.state.get("confirmation") or {})
    if run.status == "waiting_confirmation" or confirmation.get("status") in {"pending", "approved"}:
        mode = "queued"
    elif (_stage(pack, target_stage) or {}).get("recurring") and stage_ids.index(target_stage) <= current_index:
        mode = "reopened"
        run.current_stage_id = target_stage
        run.status = "waiting_for_user"
        run.completed_at = None
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
    run.state["scheduled_prompt"] = prompt
    _save_run(run)
    _append_event(run, "scheduled_check_in", stage_id=run.current_stage_id, payload=prompt, event_id=f"{event_id}_checkin")
    return run


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
        delivered = deliver(
            kind="reminder",
            title=schedule.title,
            body=f"{run.title} is ready for its next checkpoint. Open Work > Paths to continue.",
            user_id=run.user_id,
            source="kala_scheduler.workflow",
            priority="default",
            data={"workflow_id": run.workflow_id, "workflow_run_id": run.run_id, "schedule_id": schedule.schedule_id},
        )
        fired.append({"run_id": run.run_id, "schedule_id": schedule.schedule_id, "delivery": delivered})
    return {"fired": len(fired), "items": fired, "ts": _iso(current)}


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
