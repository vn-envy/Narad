"""Typed public records for Narad's durable workflow runtime."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(frozen=True)
class WorkflowDefinition:
    workflow_id: str
    version: int
    title: str
    description: str
    owner: str
    intake: list[dict[str, Any]]
    stages: list[dict[str, Any]]
    schedule_templates: list[dict[str, Any]] = field(default_factory=list)
    feedback_routes: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class WorkflowRun:
    run_id: str
    workflow_id: str
    workflow_version: int
    user_id: str
    title: str
    status: str
    current_stage_id: str | None
    project_id: str | None
    session_id: str | None
    inputs: dict[str, Any]
    state: dict[str, Any]
    created_at: str
    updated_at: str
    completed_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class WorkflowEvent:
    event_id: str
    run_id: str
    user_id: str
    event_type: str
    stage_id: str | None
    payload: dict[str, Any]
    created_at: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class WorkflowSchedule:
    schedule_id: str
    run_id: str
    user_id: str
    title: str
    cadence: str
    timezone: str
    time_of_day: str | None
    weekdays: list[int]
    day_of_month: int | None
    interval_minutes: int | None
    next_run_at: str | None
    last_run_at: str | None
    enabled: bool
    payload: dict[str, Any]
    created_at: str
    updated_at: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
