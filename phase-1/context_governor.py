from __future__ import annotations

import hashlib
import math
import re
from dataclasses import asdict, dataclass, field
from typing import Any

from model_registry import ModelProfile, get_model_profile, select_escalation


@dataclass
class ContextPlane:
    key: str
    content: str
    priority: int
    hard: bool
    compaction_strategy: str
    metadata: dict[str, Any] = field(default_factory=dict)
    token_estimate: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class CompactionResult:
    text: str
    original_tokens: int
    final_tokens: int
    applied: list[str] = field(default_factory=list)
    artifact_references: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class RuntimeEpoch:
    epoch_id: str
    model: str
    turn_count: int = 0
    last_prompt_tokens: int = 0
    peak_prompt_tokens: int = 0
    compaction_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ContextPlan:
    model_profile: ModelProfile
    planes: list[ContextPlane]
    predicted_input_tokens: int
    hard_input_budget_tokens: int
    soft_target_tokens: int
    fits_hard_budget: bool
    exceeds_soft_target: bool
    compaction_applied: list[str] = field(default_factory=list)
    compacted_from_tokens: int = 0
    selected_model: str = ""
    model_escalated_from: str | None = None
    model_escalated_to: str | None = None
    cache_hit_tokens: int = 0

    def to_event_dict(self) -> dict[str, Any]:
        return {
            "model": self.model_profile.model,
            "predicted_input_tokens": self.predicted_input_tokens,
            "hard_input_budget_tokens": self.hard_input_budget_tokens,
            "soft_target_tokens": self.soft_target_tokens,
            "fits_hard_budget": self.fits_hard_budget,
            "exceeds_soft_target": self.exceeds_soft_target,
            "planes": [
                {
                    "key": plane.key,
                    "tokens": plane.token_estimate,
                    "hard": plane.hard,
                    "strategy": plane.compaction_strategy,
                }
                for plane in self.planes
            ],
            "compaction_applied": self.compaction_applied,
            "compacted_from_tokens": self.compacted_from_tokens,
            "selected_model": self.selected_model or self.model_profile.model,
            "model_escalated_from": self.model_escalated_from,
            "model_escalated_to": self.model_escalated_to,
            "cache_hit_tokens": self.cache_hit_tokens,
        }


_FILE_PATH_RE = re.compile(r"(?<!\w)(/(?:Users|home|tmp|var|opt|etc)[^\s<>\]\)\"']+)")
_URL_RE = re.compile(r"(https?://[^\s<>\]\)\"']+)")


def _fallback_token_count(text: str) -> int:
    if not text:
        return 0
    return max(1, math.ceil(len(text) / 4))


def count_text_tokens(model: str, text: str) -> int:
    if not text:
        return 0
    try:
        from litellm import token_counter

        return int(token_counter(model=model, messages=[{"role": "user", "content": text}]))
    except Exception:
        return _fallback_token_count(text)


def count_messages_tokens(model: str, messages: list[dict[str, Any]]) -> int:
    try:
        from litellm import token_counter

        return int(token_counter(model=model, messages=messages))
    except Exception:
        return sum(_fallback_token_count(str(item.get("content", ""))) for item in messages)


def trim_to_token_budget(text: str, *, model: str, token_budget: int) -> str:
    if not text:
        return ""
    if token_budget <= 0:
        return ""
    if count_text_tokens(model, text) <= token_budget:
        return text

    low = 0
    high = len(text)
    best = ""
    while low <= high:
        mid = (low + high) // 2
        candidate = text[:mid].rstrip()
        candidate = candidate + ("…" if mid < len(text) else "")
        tokens = count_text_tokens(model, candidate)
        if tokens <= token_budget:
            best = candidate
            low = mid + 1
        else:
            high = mid - 1
    return best


def extract_artifact_references(text: str, *, max_refs: int = 6) -> list[dict[str, Any]]:
    if not text:
        return []

    refs: list[dict[str, Any]] = []
    seen: set[str] = set()

    def _push(kind: str, value: str) -> None:
        if value in seen or len(refs) >= max_refs:
            return
        seen.add(value)
        refs.append({
            "kind": kind,
            "ref": value,
            "hash": hashlib.sha1(value.encode("utf-8")).hexdigest()[:12],
        })

    for match in _FILE_PATH_RE.findall(text):
        _push("path", match)
    for match in _URL_RE.findall(text):
        _push("url", match)
    return refs


def compact_text_block(
    text: str,
    *,
    model: str,
    token_budget: int,
    query: str = "",
    preserve_artifacts: bool = True,
) -> CompactionResult:
    original_tokens = count_text_tokens(model, text)
    if original_tokens <= token_budget:
        return CompactionResult(
            text=text,
            original_tokens=original_tokens,
            final_tokens=original_tokens,
        )

    applied: list[str] = []
    artifact_refs = extract_artifact_references(text) if preserve_artifacts else []
    query_words = set(re.findall(r"\w+", query.lower()))
    raw_lines = [line.strip() for line in text.splitlines() if line.strip()]

    deduped: list[str] = []
    seen_lines: set[str] = set()
    for line in raw_lines:
        if line in seen_lines:
            continue
        seen_lines.add(line)
        deduped.append(line)
    if len(deduped) != len(raw_lines):
        applied.append("dedupe_lines")

    scored: list[tuple[int, int, str]] = []
    for idx, line in enumerate(deduped):
        words = set(re.findall(r"\w+", line.lower()))
        score = len(query_words & words)
        if "```" in line or line.startswith("/") or "http" in line:
            score += 4
        if "error" in line.lower() or "traceback" in line.lower():
            score += 3
        if idx < 8:
            score += 1
        scored.append((score, idx, line))

    scored.sort(key=lambda item: (-item[0], item[1]))
    selected_lines = [line for _, _, line in scored[: min(18, len(scored))]]
    selected_lines.sort(key=lambda line: deduped.index(line))

    rebuilt = "\n".join(selected_lines)
    if artifact_refs:
        artifact_block = "\n".join(
            f"- [{item['kind']}] {item['ref']} (ref {item['hash']})"
            for item in artifact_refs
        )
        rebuilt = rebuilt + "\n\n[ARTIFACT REFERENCES]\n" + artifact_block
        applied.append("artifact_references")
    rebuilt = trim_to_token_budget(rebuilt, model=model, token_budget=token_budget)
    if rebuilt != text:
        applied.append("ranked_excerpt")

    final_tokens = count_text_tokens(model, rebuilt)
    return CompactionResult(
        text=rebuilt,
        original_tokens=original_tokens,
        final_tokens=final_tokens,
        applied=applied,
        artifact_references=artifact_refs,
    )


def build_context_plan(
    *,
    model: str,
    plane_specs: list[dict[str, Any]],
    long_running: bool = False,
) -> ContextPlan:
    profile = get_model_profile(model, long_running=long_running)
    planes: list[ContextPlane] = []
    predicted_input_tokens = 0
    for spec in plane_specs:
        content = str(spec.get("content", "") or "")
        tokens = spec.get("token_estimate")
        if tokens is None:
            tokens = count_text_tokens(model, content)
        plane = ContextPlane(
            key=str(spec["key"]),
            content=content,
            priority=int(spec.get("priority", 100)),
            hard=bool(spec.get("hard", False)),
            compaction_strategy=str(spec.get("compaction_strategy", "none")),
            metadata=dict(spec.get("metadata", {})),
            token_estimate=int(tokens),
        )
        predicted_input_tokens += plane.token_estimate
        planes.append(plane)
    planes.sort(key=lambda plane: (plane.priority, 0 if plane.hard else 1))
    return ContextPlan(
        model_profile=profile,
        planes=planes,
        predicted_input_tokens=predicted_input_tokens,
        hard_input_budget_tokens=profile.hard_input_budget_tokens,
        soft_target_tokens=profile.soft_target_tokens,
        fits_hard_budget=predicted_input_tokens <= profile.hard_input_budget_tokens,
        exceeds_soft_target=predicted_input_tokens > profile.soft_target_tokens,
        selected_model=model,
    )


def should_rollover_epoch(epoch: RuntimeEpoch | None, plan: ContextPlan, *, max_turns: int = 12) -> list[str]:
    reasons: list[str] = []
    if epoch is None:
        return reasons
    if plan.predicted_input_tokens > plan.soft_target_tokens:
        reasons.append("predicted_input_exceeds_soft_target")
    if epoch.last_prompt_tokens > plan.soft_target_tokens:
        reasons.append("epoch_last_prompt_exceeds_soft_target")
    if epoch.peak_prompt_tokens > plan.hard_input_budget_tokens:
        reasons.append("epoch_peak_prompt_exceeds_hard_budget")
    if epoch.turn_count >= max_turns:
        reasons.append("epoch_turn_limit_reached")
    return reasons


def choose_model_and_plan(
    *,
    model: str,
    plane_specs: list[dict[str, Any]],
    long_running: bool = False,
) -> tuple[ContextPlan, ModelProfile]:
    plan = build_context_plan(model=model, plane_specs=plane_specs, long_running=long_running)
    selected = plan.model_profile
    if plan.predicted_input_tokens > plan.hard_input_budget_tokens:
        escalated = select_escalation(model, required_input_tokens=plan.predicted_input_tokens)
        if escalated is not None:
            selected = escalated
            plan.model_escalated_from = model
            plan.model_escalated_to = escalated.model
            plan.selected_model = escalated.model
    return plan, selected


def is_context_window_error(exc: Exception) -> bool:
    text = f"{type(exc).__name__}: {exc}"
    return "ContextWindowExceeded" in text or "maximum context length" in text
