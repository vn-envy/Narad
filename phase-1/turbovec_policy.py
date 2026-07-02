from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any


def _parse_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except Exception:
        return None


@dataclass(frozen=True)
class VectorTierRule:
    namespace: str
    default_tier: str
    archive_tier: str | None
    archive_after_days: int | None
    exact_required: bool = False
    description: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


_RULES: dict[str, VectorTierRule] = {
    "episodic_summary": VectorTierRule(
        namespace="episodic_summary",
        default_tier="turbovec_4bit",
        archive_tier="turbovec_2bit_archive",
        archive_after_days=180,
        description="Compressed recall for durable episodic summaries with archive tiering.",
    ),
    "project_wiki": VectorTierRule(
        namespace="project_wiki",
        default_tier="turbovec_4bit",
        archive_tier="turbovec_2bit_archive",
        archive_after_days=240,
        description="Compressed project/wiki retrieval, exact reread before prompt injection.",
    ),
    "archived_conversation": VectorTierRule(
        namespace="archived_conversation",
        default_tier="turbovec_2bit_archive",
        archive_tier="turbovec_2bit_archive",
        archive_after_days=0,
        description="Long-horizon archive retrieval where density matters more than perfect first-pass recall.",
    ),
    "thread_memory": VectorTierRule(
        namespace="thread_memory",
        default_tier="exact",
        archive_tier=None,
        archive_after_days=None,
        exact_required=True,
        description="Exact recent-turn continuity; never compressed.",
    ),
    "working_memory": VectorTierRule(
        namespace="working_memory",
        default_tier="exact",
        archive_tier=None,
        archive_after_days=None,
        exact_required=True,
        description="Exact active-state memory; never compressed.",
    ),
}


def get_vector_tier_rule(namespace: str) -> VectorTierRule:
    return _RULES.get(
        namespace,
        VectorTierRule(
            namespace=namespace,
            default_tier="turbovec_4bit",
            archive_tier="turbovec_2bit_archive",
            archive_after_days=365,
            description="Default compressed semantic tier.",
        ),
    )


def select_memory_tier(namespace: str, *, created_at: str | None = None) -> str:
    rule = get_vector_tier_rule(namespace)
    if rule.exact_required:
        return "exact"
    if rule.archive_after_days is None or rule.archive_tier is None:
        return rule.default_tier
    created_dt = _parse_ts(created_at)
    if created_dt is None:
        return rule.default_tier
    age_days = (datetime.now(timezone.utc) - created_dt.astimezone(timezone.utc)).days
    if age_days >= rule.archive_after_days:
        return rule.archive_tier
    return rule.default_tier


def memory_tier_policy_payload() -> dict[str, Any]:
    return {
        "local_first": True,
        "default_strategy": "compressed_candidates_exact_reread",
        "exact_namespaces": [
            name for name, rule in _RULES.items() if rule.exact_required
        ],
        "rules": {name: rule.to_dict() for name, rule in _RULES.items()},
    }
