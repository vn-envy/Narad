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
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from narad_config import DHARMA_POLICY_PATH

_ROOT = Path(__file__).parent
_PHASE1 = _ROOT / "phase-1"
if str(_PHASE1) not in sys.path:
    sys.path.insert(0, str(_PHASE1))
from runtime_contract import agent_contract_map


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
    return _ensure_policy_file()


def allowed_tool_families(avatar: str) -> set[str]:
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
