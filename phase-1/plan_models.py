"""
Plan / PlanStep typed model for Rama's structured project plans.

Adapted from IBM/AssetOpsBench src/agent/plan_execute/planner.py (Apache 2.0).
Rama emits PLAN_JSON: blocks that get parsed into this model.
Plan.levels() topologically sorts steps so parallel-safe batches can be
dispatched to multiple avatars simultaneously.

Schema:
    PlanStep.dependencies = [step_id, ...]   # zero-indexed
    Plan.levels()          = [[step, ...], [step, ...], ...]
                             # level 0 = no deps, level 1 = depends on level 0, etc.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any


class StepStatus(str, Enum):
    backlog     = "backlog"
    in_progress = "in_progress"
    review      = "review"
    done        = "done"
    blocked     = "blocked"


@dataclass
class PlanStep:
    step_id: int
    description: str
    owner: str              # "Matsya" | "Rama" | "Krishna" | "Parashurama" | etc.
    expected_output: str
    dependencies: list[int] = field(default_factory=list)
    due_date: str | None = None
    calendar_event: bool = False
    # Kanban tracking fields — all optional with defaults so parse_plan() is unchanged
    status: StepStatus = StepStatus.backlog
    started_at: str | None = None
    completed_at: str | None = None
    result_digest: str | None = None   # first 120 chars of avatar output

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Plan:
    title: str
    steps: list[PlanStep]
    horizon_days: int = 0
    session_id: str = ""
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def levels(self) -> list[list[PlanStep]]:
        """Return steps grouped into parallel-safe levels via topological sort.

        Level 0 = steps with no dependencies (safe to dispatch in parallel).
        Level N = steps whose dependencies are all in levels 0..N-1.
        Cycle guard: if a deadlock is detected, remaining steps form a final level.
        """
        resolved: set[int] = set()
        remaining = list(self.steps)
        result: list[list[PlanStep]] = []
        while remaining:
            level = [s for s in remaining if all(d in resolved for d in s.dependencies)]
            if not level:
                result.append(remaining)
                break
            result.append(level)
            resolved.update(s.step_id for s in level)
            remaining = [s for s in remaining if s not in level]
        return result

    def to_dict(self) -> dict[str, Any]:
        return {
            "title":        self.title,
            "horizon_days": self.horizon_days,
            "session_id":   self.session_id,
            "created_at":   self.created_at,
            "steps":        [s.to_dict() for s in self.steps],
            "levels":       [[s.to_dict() for s in lvl] for lvl in self.levels()],
        }


def parse_plan(data: dict[str, Any], session_id: str = "") -> Plan:
    """Deserialise a PLAN_JSON dict emitted by Rama into a Plan instance."""
    raw_steps = data.get("steps", [])
    steps = [
        PlanStep(
            step_id=s.get("step_id", i),
            description=s.get("description", ""),
            owner=s.get("owner", "Rama"),
            expected_output=s.get("expected_output", ""),
            dependencies=s.get("dependencies", []),
            due_date=s.get("due_date"),
            calendar_event=bool(s.get("calendar_event", False)),
            status=StepStatus(s.get("status", "backlog")),
            started_at=s.get("started_at"),
            completed_at=s.get("completed_at"),
            result_digest=s.get("result_digest"),
        )
        for i, s in enumerate(raw_steps)
    ]
    return Plan(
        title=data.get("title", "Plan"),
        steps=steps,
        horizon_days=int(data.get("horizon_days", 0)),
        session_id=session_id,
        created_at=data.get("created_at", datetime.now(timezone.utc).isoformat()),
    )
