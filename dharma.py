"""
Dharma — Narad's normative operating layer.

This module centralizes:
  - canonical tool-family permissions by avatar
  - evidence requirements for promoted learnings
  - swapna consolidation safety checks
  - retrieval-policy mutation guards

The point is not decorative naming: Dharma is the boundary between "can" and
"should" inside Narad's runtime and offline evolution loops.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from narad_config import DHARMA_POLICY_PATH


@dataclass(frozen=True)
class DharmaVerdict:
    allowed: bool
    reasons: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {"allowed": self.allowed, "reasons": self.reasons}


_DEFAULT_POLICY: dict[str, Any] = {
    "sutra": {
        "min_evidence_count": 1,
        "min_content_words": 4,
        "reject_markers": ["todo", "tbd", "placeholder", "lorem ipsum"],
    },
    "swapna": {
        "allow_source_mutation": False,
        "max_source_items": 100,
        "require_provenance": True,
    },
    "retrieval_policy": {
        "mutable_keys": [
            "top_k",
            "fusion_mode",
            "answer_verification",
            "reflection_rounds",
            "entity_swap",
            "query_decomposition",
        ],
    },
    # Side-effect actions. Every gated action is consulted here and the verdict
    # is written to the Karma ledger — allowed AND denied. Disable an action by
    # setting enabled=false in ~/.narad/config/dharma_policy.json.
    "actions": {
        "executor":       {"enabled": True},
        "email_send":     {"enabled": True, "max_recipients": 10},
        "browser_submit": {"enabled": True},
    },
}


def _ensure_policy_file() -> dict[str, Any]:
    if not DHARMA_POLICY_PATH.exists():
        DHARMA_POLICY_PATH.write_text(json.dumps(_DEFAULT_POLICY, indent=2))
        return dict(_DEFAULT_POLICY)
    try:
        return json.loads(DHARMA_POLICY_PATH.read_text())
    except Exception:
        return dict(_DEFAULT_POLICY)


def load_policy() -> dict[str, Any]:
    # Overlay the on-disk policy onto defaults so installs created before a
    # policy section existed (e.g. 'actions') still get sane fallbacks.
    return {**_DEFAULT_POLICY, **_ensure_policy_file()}


def allowed_tool_families(avatar: str) -> set[str]:
    # Lazy import: keeps `import dharma` dependency-light so action gates work
    # from any phase without phase-1 on the path.
    from runtime_contract import agent_contract_map

    agent = agent_contract_map().get(avatar, {})
    return set(agent.get("allowed_tool_families", []))


def validate_tool_family(avatar: str, family: str) -> DharmaVerdict:
    allowed = allowed_tool_families(avatar)
    if not allowed:
        return DharmaVerdict(True, [])
    if family in allowed:
        return DharmaVerdict(True, [])
    return DharmaVerdict(False, [f"{avatar} is not permitted to mutate tool family '{family}'"])


def validate_sutra_candidate(
    avatar: str,
    content: str,
    *,
    evidence_count: int = 1,
) -> DharmaVerdict:
    policy = load_policy()["sutra"]
    reasons: list[str] = []
    words = [w for w in content.split() if w.strip()]
    if evidence_count < int(policy["min_evidence_count"]):
        reasons.append("Sutra candidate lacks enough evidence.")
    if len(words) < int(policy["min_content_words"]):
        reasons.append("Sutra candidate is too short to be reusable.")
    lower = content.lower()
    for marker in policy["reject_markers"]:
        if marker in lower:
            reasons.append(f"Sutra candidate contains rejected marker '{marker}'.")
    return DharmaVerdict(not reasons, reasons)


def validate_swapna_plan(
    source_ids: list[str],
    *,
    mutates_source: bool,
    has_provenance: bool,
) -> DharmaVerdict:
    policy = load_policy()["swapna"]
    reasons: list[str] = []
    if mutates_source and not bool(policy["allow_source_mutation"]):
        reasons.append("Swapna may not mutate source evidence directly.")
    if len(source_ids) > int(policy["max_source_items"]):
        reasons.append("Swapna working set exceeds policy limit.")
    if bool(policy["require_provenance"]) and not has_provenance:
        reasons.append("Swapna output must carry provenance.")
    return DharmaVerdict(not reasons, reasons)


def validate_retrieval_policy_change(changes: dict[str, Any]) -> DharmaVerdict:
    allowed_keys = set(load_policy()["retrieval_policy"]["mutable_keys"])
    illegal = sorted(set(changes) - allowed_keys)
    if illegal:
        return DharmaVerdict(False, [f"Illegal retrieval-policy key(s): {', '.join(illegal)}"])
    return DharmaVerdict(True, [])


def gate_action(
    action: str,
    *,
    avatar: str = "system",
    detail: str = "",
    metadata: dict[str, Any] | None = None,
) -> DharmaVerdict:
    """Mandatory gate for side-effect actions (executor, email_send, browser_submit).

    Consults the 'actions' policy and writes the verdict — allowed or denied —
    to the Karma ledger. Unknown actions are DENIED: every new side-effect
    channel must be registered in policy before it can fire.
    """
    policy = load_policy().get("actions", {})
    rules = policy.get(action)
    reasons: list[str] = []

    if rules is None:
        reasons.append(f"Action '{action}' is not registered in Dharma policy.")
    elif not bool(rules.get("enabled", False)):
        reasons.append(f"Action '{action}' is disabled by Dharma policy.")
    else:
        max_recipients = rules.get("max_recipients")
        recipients = (metadata or {}).get("recipients")
        if max_recipients is not None and recipients is not None and int(recipients) > int(max_recipients):
            reasons.append(
                f"Action '{action}' exceeds max_recipients "
                f"({recipients} > {max_recipients})."
            )

    verdict = DharmaVerdict(not reasons, reasons)

    # Karma ledger — best-effort, never blocks the verdict itself.
    try:
        from karma_log import log_karma

        log_karma(
            action="action_allowed" if verdict.allowed else "action_denied",
            sutra_id=action,
            avatar=avatar,
            detail=detail or action,
            entity_type="action",
            policy="dharma.actions",
            metadata={**(metadata or {}), "reasons": reasons} if (metadata or reasons) else None,
        )
    except Exception:
        pass

    return verdict


def validate_memory_write(kind: str, payload: dict[str, Any]) -> DharmaVerdict:
    """Best-effort normative checks over memory writes."""
    reasons: list[str] = []
    if kind == "episode" and not payload.get("session_id"):
        reasons.append("Episodes require a session_id.")
    if kind == "profile" and not payload.get("user_id"):
        reasons.append("Profiles require a user_id.")
    if kind == "skill" and not payload.get("provenance_ids"):
        reasons.append("Skill assets require provenance.")
    return DharmaVerdict(not reasons, reasons)
