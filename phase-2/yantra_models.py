"""
Typed trace models for Yantra — Trajectory, TurnRecord, ToolCall.

Adapted from IBM/AssetOpsBench (Apache 2.0, github.com/IBM/AssetOpsBench).
Every avatar run produces one Trajectory stored inside the avatar_done Yantra event.
This makes every tool call fully auditable without reading the full result text.

Usage:
    traj = Trajectory(avatar="Krishna", model="deepseek/deepseek-chat", task_preview=task[:80])
    tc = ToolCall(tool="create_webpage", params_preview="...", result_preview="...", latency_ms=420)
    traj.turns.append(TurnRecord(turn=1, tool_calls=[tc], prompt_tokens=800, completion_tokens=200))
    traj.total_ms = 3200
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field


@dataclass
class ToolCall:
    tool: str
    params_preview: str    # first 200 chars of serialised args
    result_preview: str    # first 200 chars of result
    latency_ms: int
    error: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class TurnRecord:
    turn: int
    tool_calls: list[ToolCall] = field(default_factory=list)
    text_preview: str = ""     # first 200 chars of any non-tool text in this turn
    prompt_tokens: int = 0
    completion_tokens: int = 0

    def to_dict(self) -> dict:
        return {
            "turn":              self.turn,
            "tool_calls":        [tc.to_dict() for tc in self.tool_calls],
            "text_preview":      self.text_preview,
            "prompt_tokens":     self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
        }


@dataclass
class Trajectory:
    avatar: str
    model: str
    task_preview: str          # first 80 chars of the task
    turns: list[TurnRecord] = field(default_factory=list)
    total_ms: int = 0
    error: str | None = None

    def to_dict(self) -> dict:
        return {
            "avatar":       self.avatar,
            "model":        self.model,
            "task_preview": self.task_preview,
            "turns":        [t.to_dict() for t in self.turns],
            "total_ms":     self.total_ms,
            "error":        self.error,
        }
