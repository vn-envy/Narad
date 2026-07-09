"""
Smriti Core — Narad's canonical memory and learning control plane.

This module unifies the old peer memory paths behind one compatibility facade.
It keeps the current stores intact while making the runtime think in terms of:
  - raw episodes
  - contextual recall
  - sutra promotion
  - sankalpa updates
  - swapna consolidation
  - provenance lookup
"""

from __future__ import annotations

import json
import logging
import os
import uuid
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_log = logging.getLogger("narad.smriti")

_ROOT = Path(__file__).parent

from runtime_contract import primary_discipline

from dharma import (
    validate_memory_write,
    validate_sutra_candidate,
    validate_swapna_plan,
)
from narad_config import (
    BASELINE_DIR,
    BENCHMARK_DIR,
    EPISODE_DIR,
    KARMA_MUTATIONS_PATH,
    SANKALPA_COMMITMENTS_PATH,
    SWAPNA_INBOX_DIR,
    TRACE_DIR,
)
from smriti_indexer import index_episode_record
from smriti_recall_ranker import build_semantic_memory_context
from smriti_vector_store import memory_tier_diagnostics as _vector_memory_tier_diagnostics

try:
    from context_governor import compact_text_block, count_text_tokens
except Exception:  # pragma: no cover - additive dependency
    compact_text_block = None  # type: ignore[assignment]
    count_text_tokens = None  # type: ignore[assignment]


def _append_jsonl(path: Path, record: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            rows.append(json.loads(line))
        except Exception:
            continue
    return rows


def _truncate(text: str, limit: int) -> str:
    return text[:limit] + ("…" if len(text) > limit else "")


@dataclass
class Episode:
    id: str
    ts: str
    session_id: str
    user_id: str
    avatar: str
    discipline: str
    task: str
    result: str
    project_id: str
    trace_session_id: str
    provenance: list[str]


def _episode_path(user_id: str) -> Path:
    return EPISODE_DIR / f"{user_id}.jsonl"


def _extract_commitments(task: str, result: str) -> list[dict[str, Any]]:
    text = f"{task}\n{result}".lower()
    out: list[dict[str, Any]] = []
    if any(kw in text for kw in ("goal", "target", "deadline", "milestone", "objective")):
        out.append({"kind": "goal", "content": _truncate(task or result, 220)})
    if any(kw in text for kw in ("prefer", "always", "never", "format", "tone", "workflow")):
        out.append({"kind": "preference", "content": _truncate(task or result, 220)})
    if any(kw in text for kw in ("must", "constraint", "required", "policy", "avoid")):
        out.append({"kind": "constraint", "content": _truncate(task or result, 220)})
    return out


def _record_commitments(
    *,
    user_id: str,
    avatar: str,
    session_id: str,
    task: str,
    result: str,
    provenance_ids: list[str],
) -> list[dict[str, Any]]:
    commitments: list[dict[str, Any]] = []
    for item in _extract_commitments(task, result):
        record = {
            "id": str(uuid.uuid4()),
            "ts": datetime.now(timezone.utc).isoformat(),
            "user_id": user_id,
            "avatar": avatar,
            "session_id": session_id,
            "kind": item["kind"],
            "content": item["content"],
            "provenance_ids": provenance_ids,
        }
        _append_jsonl(SANKALPA_COMMITMENTS_PATH, record)
        commitments.append(record)
    return commitments


def log_mutation(
    action: str,
    *,
    entity_type: str,
    entity_id: str,
    actor: str,
    detail: str = "",
    policy: str | None = None,
    provenance_ids: list[str] | None = None,
    metadata: dict[str, Any] | None = None,
) -> None:
    record: dict[str, Any] = {
        "id": str(uuid.uuid4()),
        "ts": datetime.now(timezone.utc).isoformat(),
        "action": action,
        "entity_type": entity_type,
        "entity_id": entity_id,
        "actor": actor,
        "detail": _truncate(detail, 220),
    }
    if policy:
        record["policy"] = policy
    if provenance_ids:
        record["provenance_ids"] = provenance_ids
    if metadata:
        record["metadata"] = metadata
    _append_jsonl(KARMA_MUTATIONS_PATH, record)


def capture_episode(
    *,
    session_id: str,
    task: str,
    avatar: str,
    result: str,
    user_id: str = "default",
    project_id: str = "general",
    trace_session_id: str | None = None,
) -> dict[str, Any]:
    verdict = validate_memory_write(
        "episode",
        {"session_id": session_id, "user_id": user_id, "avatar": avatar},
    )
    if not verdict.allowed:
        return {"status": "blocked", "verdict": verdict.to_dict()}

    episode = Episode(
        id=str(uuid.uuid4()),
        ts=datetime.now(timezone.utc).isoformat(),
        session_id=session_id,
        user_id=user_id,
        avatar=avatar,
        discipline=primary_discipline(avatar),
        task=_truncate(task, 1200),
        result=_truncate(result, 3000),
        project_id=project_id,
        trace_session_id=trace_session_id or session_id,
        provenance=[trace_session_id or session_id],
    )
    _append_jsonl(_episode_path(user_id), asdict(episode))
    index_deferred = False
    try:
        index_episode_record(asdict(episode))
    except Exception as exc:
        # Episode is safely in the source of truth; the index catches up on the
        # next ensure_user_episode_index() pass. Visible, never silent.
        index_deferred = True
        _log.warning("Smriti: episode %s written but not indexed yet: %s", episode.id, exc)
    log_mutation(
        "episode_captured",
        entity_type="episode",
        entity_id=episode.id,
        actor=avatar,
        detail=task,
        provenance_ids=episode.provenance,
    )

    commitments = _record_commitments(
        user_id=user_id,
        avatar=avatar,
        session_id=session_id,
        task=task,
        result=result,
        provenance_ids=episode.provenance,
    )
    for commitment in commitments:
        log_mutation(
            "sankalpa_commitment_recorded",
            entity_type="sankalpa_commitment",
            entity_id=commitment["id"],
            actor=avatar,
            detail=commitment["content"],
            provenance_ids=commitment["provenance_ids"],
        )

    return {
        "status": "ok",
        "episode_id": episode.id,
        "index_deferred": index_deferred,
        "commitment_count": len(commitments),
        "provenance": episode.provenance,
    }


async def recall_context(
    query: str,
    *,
    user_id: str = "default",
    avatar: str = "",
    project_id: str = "general",
    token_budget: int | None = None,
    model: str | None = None,
) -> dict[str, Any]:
    provenance: list[dict[str, Any]] = []
    blocks: list[str] = []
    compaction_applied: list[str] = []
    budget_model = model or "deepseek/deepseek-v4-flash"
    remaining_budget = token_budget

    def _fit_block(kind: str, text: str, preview_limit: int = 200) -> str:
        nonlocal remaining_budget, compaction_applied
        if not text:
            return ""
        if remaining_budget is None or count_text_tokens is None:
            return text

        needed = count_text_tokens(budget_model, text)
        if needed <= remaining_budget:
            remaining_budget -= needed
            return text

        if compact_text_block is None or remaining_budget <= 96:
            return ""

        compacted = compact_text_block(
            text,
            model=budget_model,
            token_budget=max(96, remaining_budget),
            query=query,
            preserve_artifacts=True,
        )
        if compacted.text:
            compaction_applied.extend(f"{kind}:{step}" for step in compacted.applied)
            final_tokens = count_text_tokens(budget_model, compacted.text)
            if final_tokens <= remaining_budget:
                remaining_budget -= final_tokens
                return compacted.text
        return ""

    try:
        vector_packet = build_semantic_memory_context(
            query=query,
            user_id=user_id,
            project_id=project_id,
            token_budget=remaining_budget,
            model=budget_model,
            limit=4,
        )
        vector_context = vector_packet.get("text", "")
        if vector_context:
            block = _fit_block("smriti_vector_semantic", vector_context)
            if block:
                blocks.append(block)
                provenance.extend(vector_packet.get("provenance", []))
                compaction_applied.extend(vector_packet.get("compaction_applied", []))
    except Exception as exc:
        _log.warning("Smriti: vector/semantic recall unavailable this turn: %s", exc)

    try:
        from smriti_v2 import get_project_context

        project_budget = remaining_budget if remaining_budget is not None else None
        project_context = await get_project_context(
            user_id,
            query,
            project_id=project_id,
            token_budget=project_budget,
            model=budget_model if project_budget is not None else None,
        )
        if project_context:
            block = _fit_block("project_context", project_context)
            if block:
                blocks.append(block)
                provenance.append({"kind": "project_context", "preview": _truncate(block, 200)})
    except Exception:
        project_context = ""

    try:
        from sutra_engine import format_for_injection as format_sutras
        from sutra_engine import get_active_sutras

        active_sutras = get_active_sutras(avatar, task=query) if avatar else []
        if active_sutras:
            sutra_block = format_sutras(active_sutras)
            verdict = validate_sutra_candidate(
                avatar or active_sutras[0].get("avatar", "Narad"),
                sutra_block,
                evidence_count=len(active_sutras),
            )
            if verdict.allowed:
                block = _fit_block("sutras", sutra_block)
                if block:
                    blocks.append(block)
                    provenance.append({"kind": "sutras", "count": len(active_sutras)})
    except Exception:
        pass

    try:
        from sankalpa import format_for_injection as format_sankalpa
        from sankalpa import get_active_sankalpas

        active_sankalpas = get_active_sankalpas(user_id, avatar) if avatar else []
        if active_sankalpas:
            sankalpa_block = format_sankalpa(active_sankalpas)
            block = _fit_block("sankalpas", sankalpa_block)
            if block:
                blocks.append(block)
                provenance.append({"kind": "sankalpas", "count": len(active_sankalpas)})
    except Exception:
        pass

    commitments = [
        row for row in _load_jsonl(SANKALPA_COMMITMENTS_PATH)
        if row.get("user_id") == user_id and (
            row.get("avatar") == avatar or row.get("avatar") == "__global__" or not avatar
        )
    ]
    if commitments:
        lines = [f"- [{c['kind']}] {c['content']}" for c in commitments[-5:]]
        block = _fit_block("sankalpa_commitments", "[SANKALPA COMMITMENTS]\n" + "\n".join(lines))
        if block:
            blocks.append(block)
            provenance.append({"kind": "sankalpa_commitments", "count": len(lines)})

    context = "\n\n".join(blocks)
    if context:
        context += (
            "\n\nUse the above only when directly relevant. "
            "Do not repeat it back unless the user asks."
        )
    return {
        "context": context,
        "provenance": provenance,
        "memory_path_count": len([p for p in provenance if p["kind"] != "project_context"]) + (1 if project_context else 0),
        "compaction_applied": compaction_applied,
        "token_budget": token_budget,
        "remaining_budget": remaining_budget,
        "memory_tiers": _vector_memory_tier_diagnostics(user_id),
    }


def memory_tier_diagnostics(user_id: str = "default") -> dict[str, Any]:
    return _vector_memory_tier_diagnostics(user_id)


def reflect_failure(
    *,
    session_id: str,
    query: str,
    avatar: str,
    result: str,
    error: str = "",
) -> dict[str, Any]:
    try:
        from tapas import score_session
        score, reason, hallucination_free, sequence_correct = score_session(query, avatar, result or error)
        reflection = {
            "score": score,
            "reason": reason,
            "hallucination_free": hallucination_free,
            "sequence_correct": sequence_correct,
            "reflection_needed": (score < 0.6) or bool(error),
        }
        log_mutation(
            "failure_reflected",
            entity_type="reflection",
            entity_id=session_id,
            actor=avatar,
            detail=reason,
            provenance_ids=[session_id],
            metadata={"score": score, "error": error[:120]},
        )
        return reflection
    except Exception as exc:
        return {"score": 0.0, "reason": f"reflection unavailable: {exc}", "reflection_needed": True}


def promote_sutra(
    *,
    session_id: str,
    query: str,
    avatar: str,
    result: str,
) -> dict[str, Any]:
    verdict = validate_sutra_candidate(avatar, result, evidence_count=1)
    if not verdict.allowed:
        log_mutation(
            "sutra_blocked_by_dharma",
            entity_type="sutra",
            entity_id=session_id,
            actor=avatar,
            detail="; ".join(verdict.reasons),
            provenance_ids=[session_id],
        )
        return {"status": "blocked", "verdict": verdict.to_dict()}

    from tapas import process_session

    outcome = process_session(session_id=session_id, query=query, avatar=avatar, result=result)
    log_mutation(
        "tapas_processed",
        entity_type="sutra_candidate",
        entity_id=session_id,
        actor=avatar,
        detail=outcome.get("action", "unknown"),
        provenance_ids=[session_id],
        metadata=outcome,
    )
    return {"status": "ok", "outcome": outcome}


def update_sankalpa(
    *,
    user_id: str,
    avatar: str,
    task: str,
    result: str,
    session_id: str = "",
) -> dict[str, Any]:
    from sankalpa import observe_session

    observe_session(user_id=user_id, avatar=avatar, task=task, result=result)
    commitments = _record_commitments(
        user_id=user_id,
        avatar=avatar,
        session_id=session_id or str(uuid.uuid4()),
        task=task,
        result=result,
        provenance_ids=[session_id] if session_id else [],
    )
    return {"status": "ok", "commitment_count": len(commitments)}


def load_commitments(user_id: str = "default") -> list[dict[str, Any]]:
    return [row for row in _load_jsonl(SANKALPA_COMMITMENTS_PATH) if row.get("user_id") == user_id]


def _select_swapna_region(user_id: str, max_episodes: int) -> list[dict[str, Any]]:
    episodes = [row for row in _load_jsonl(_episode_path(user_id)) if row.get("user_id") == user_id]
    episodes.sort(key=lambda row: row.get("ts", ""))
    return episodes[-max_episodes:]


def _build_swapna_suggestions(episodes: list[dict[str, Any]]) -> dict[str, Any]:
    texts = []
    for ep in episodes:
        texts.append(f"Task: {ep.get('task','')}\nResult: {ep.get('result','')}")

    facts: list[dict[str, Any]] = []
    scenarios: list[dict[str, Any]] = []
    try:
        from smriti_v2 import cluster_l2_scenarios, extract_l1_facts

        for ep in episodes:
            facts.extend(
                fact.to_dict()
                for fact in extract_l1_facts(
                    f"Task: {ep.get('task','')}\nResult: {ep.get('result','')}",
                    session_id=ep.get("session_id", ""),
                    avatar=ep.get("avatar", ""),
                    user_id=ep.get("user_id", "default"),
                )
            )
        scenarios = [s.to_dict() for s in cluster_l2_scenarios([
            type("FactProxy", (), fact)() for fact in facts
        ], user_id=episodes[-1].get("user_id", "default"))] if facts else []
    except Exception:
        pass

    common_words = Counter()
    for ep in episodes:
        for word in (ep.get("task", "") + " " + ep.get("result", "")).lower().split():
            if len(word) > 4:
                common_words[word] += 1

    high_signal = [word for word, count in common_words.most_common(10) if count > 1]
    return {
        "facts": facts[:20],
        "scenarios": scenarios[:10],
        "candidate_keywords": high_signal[:8],
    }


def run_swapna_cycle(
    *,
    user_id: str = "default",
    project_id: str = "general",
    max_episodes: int = 20,
    apply: bool = False,
) -> dict[str, Any]:
    episodes = _select_swapna_region(user_id, max_episodes)
    source_ids = [ep.get("id", "") for ep in episodes if ep.get("id")]
    if not source_ids:
        return {
            "status": "ok",
            "apply": apply,
            "source_episode_count": 0,
            "inbox_id": None,
            "suggestions": {"facts": [], "scenarios": [], "candidate_keywords": []},
        }
    verdict = validate_swapna_plan(
        source_ids,
        mutates_source=False,
        has_provenance=bool(source_ids),
    )
    if not verdict.allowed:
        return {"status": "blocked", "verdict": verdict.to_dict()}

    suggestions = _build_swapna_suggestions(episodes)
    inbox_record = {
        "id": str(uuid.uuid4()),
        "ts": datetime.now(timezone.utc).isoformat(),
        "user_id": user_id,
        "project_id": project_id,
        "apply": apply,
        "source_episode_ids": source_ids,
        "suggestions": suggestions,
    }
    if apply:
        inbox_path = SWAPNA_INBOX_DIR / f"{inbox_record['id']}.json"
        inbox_path.write_text(json.dumps(inbox_record, indent=2, ensure_ascii=False))
        log_mutation(
            "swapna_consolidated",
            entity_type="swapna_cycle",
            entity_id=inbox_record["id"],
            actor="Swapna",
            detail=f"{len(source_ids)} episode(s) consolidated",
            policy="dharma.swapna",
            provenance_ids=source_ids,
            metadata={"fact_count": len(suggestions["facts"]), "scenario_count": len(suggestions["scenarios"])},
        )
    else:
        log_mutation(
            "swapna_dry_run",
            entity_type="swapna_cycle",
            entity_id=inbox_record["id"],
            actor="Swapna",
            detail=f"{len(source_ids)} episode(s) analyzed",
            policy="dharma.swapna",
            provenance_ids=source_ids,
            metadata={"fact_count": len(suggestions["facts"]), "scenario_count": len(suggestions["scenarios"])},
        )
    return {
        "status": "ok",
        "apply": apply,
        "source_episode_count": len(source_ids),
        "inbox_id": inbox_record["id"],
        "suggestions": suggestions,
    }


def _rewrite_jsonl_without(path: Path, entity_id: str) -> int:
    """Rewrite a jsonl file dropping rows whose id matches. Returns count removed."""
    rows = _load_jsonl(path)
    keep = [row for row in rows if str(row.get("id")) != entity_id]
    removed = len(rows) - len(keep)
    if removed:
        tmp = path.with_suffix(path.suffix + ".tmp")
        with tmp.open("w", encoding="utf-8") as fh:
            for row in keep:
                fh.write(json.dumps(row, ensure_ascii=False) + "\n")
        tmp.replace(path)
    return removed


def forget(
    entity_id: str,
    *,
    entity_type: str = "generic",
    actor: str = "Narad",
    user_id: str = "default",
) -> dict[str, Any]:
    """Forget an entity across all planes it lives in — not just a tombstone.

    Episodes cascade: source of truth (episodes.jsonl) rewrite, vector
    manifests, lexical FTS row, and the index checkpoint (so the shrunken file
    triggers a clean rescan). Derived wiki pages are regenerated by Swapna
    (M3) rather than surgically edited here — recorded honestly in the result.
    """
    cascade: dict[str, Any] = {}
    if entity_type == "episode":
        try:
            cascade["episode_rows_removed"] = _rewrite_jsonl_without(
                _episode_path(user_id), entity_id
            )
            from smriti_indexer import _episode_ckpt_path, fts_delete_episode
            from smriti_vector_store import remove_records

            cascade["vector_records_removed"] = remove_records(
                user_id=user_id, record_ids={f"episode:{entity_id}"}
            )
            fts_delete_episode(user_id, entity_id)
            cascade["fts_deleted"] = True
            _episode_ckpt_path(user_id).unlink(missing_ok=True)
            cascade["wiki_cascade"] = "deferred — wiki is regenerated from episodes by Swapna (M3)"
        except Exception as exc:
            cascade["error"] = str(exc)
            _log.warning("Smriti: forget(%s) cascade incomplete: %s", entity_id, exc)
    elif entity_type == "sankalpa_commitment":
        cascade["commitment_rows_removed"] = _rewrite_jsonl_without(
            SANKALPA_COMMITMENTS_PATH, entity_id
        )

    tombstone = {
        "id": str(uuid.uuid4()),
        "ts": datetime.now(timezone.utc).isoformat(),
        "action": "forgotten",
        "entity_type": entity_type,
        "entity_id": entity_id,
        "actor": actor,
    }
    if cascade:
        tombstone["metadata"] = cascade
    _append_jsonl(KARMA_MUTATIONS_PATH, tombstone)
    return {"status": "ok", "tombstone_id": tombstone["id"], "cascade": cascade}


def get_provenance(entity_id: str, *, user_id: str = "default") -> dict[str, Any]:
    episodes = [row for row in _load_jsonl(_episode_path(user_id)) if row.get("id") == entity_id]
    if episodes:
        return {"kind": "episode", "record": episodes[0]}

    mutations = [row for row in _load_jsonl(KARMA_MUTATIONS_PATH) if row.get("entity_id") == entity_id]
    if mutations:
        return {"kind": "mutation", "records": mutations}

    commitments = [row for row in _load_jsonl(SANKALPA_COMMITMENTS_PATH) if row.get("id") == entity_id]
    if commitments:
        return {"kind": "sankalpa_commitment", "record": commitments[0]}

    swapna_file = SWAPNA_INBOX_DIR / f"{entity_id}.json"
    if swapna_file.exists():
        return {"kind": "swapna", "record": json.loads(swapna_file.read_text())}
    return {"kind": "unknown", "record": None}


def architecture_scorecard() -> dict[str, Any]:
    avatar_agents = (_ROOT / "phase-1" / "avatar_agents.py").read_text(encoding="utf-8")
    direct_legacy_imports = sum(
        avatar_agents.count(snippet)
        for snippet in (
            "from smriti import",
            "from sutra_engine import",
            "from sankalpa import",
            "from tapas import",
            "from smriti_v2 import",
        )
    )
    new_imports = avatar_agents.count("from smriti_core import")

    baseline_tests = {
        "server_contract": "phase-1/test_server_contract.py",
        "runtime_contract": "phase-1/test_runtime_contract.py",
        "smriti": "phase-1/test_smriti_vector_tiers.py",
        "tools": "phase-8/test_tool_smoke.py",
    }
    return {
        "legacy_direct_memory_imports": direct_legacy_imports,
        "smriti_core_imports": new_imports,
        "episode_store_enabled": True,
        "swapna_enabled": True,
        "karma_mutation_log_enabled": True,
        "baseline_test_files": baseline_tests,
    }


def benchmark_snapshot(*, name: str, metrics: dict[str, Any]) -> Path:
    path = (BASELINE_DIR if name.startswith("baseline") else BENCHMARK_DIR) / f"{name}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(metrics, indent=2, ensure_ascii=False))
    return path


def load_swapna_inbox() -> list[dict[str, Any]]:
    rows = []
    for file in sorted(SWAPNA_INBOX_DIR.glob("*.json")):
        try:
            rows.append(json.loads(file.read_text()))
        except Exception:
            continue
    return rows


def _load_all_episodes() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for file in sorted(EPISODE_DIR.glob("*.jsonl")):
        rows.extend(_load_jsonl(file))
    return rows


def _load_trace_events() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for file in sorted(TRACE_DIR.glob("*.jsonl")):
        rows.extend(_load_jsonl(file))
    rows.sort(key=lambda row: row.get("ts", ""))
    return rows


def _empty_evolution_agent(name: str) -> dict[str, Any]:
    return {
        "name": name,
        "discipline": primary_discipline(name),
        "totals": {
            "tools": 0,
            "skills": 0,
            "behavior": 0,
            "memory": 0,
            "runtime": 0,
        },
        "tool_usage": {},
        "models": {},
        "memory": {
            "episodes": 0,
            "commitments": 0,
            "reflections": 0,
            "swapna_touchpoints": 0,
        },
        "learning": {
            "sutras_promoted": 0,
            "sutras_active": 0,
            "sutras_reverted": 0,
            "sankalpa_updates": 0,
        },
        "behavior": {
            "sessions": 0,
            "avg_latency_ms": 0,
            "total_tokens": 0,
            "degraded_events": 0,
        },
        "runtime": {
            "models_seen": [],
        },
    }


def _mutation_category(row: dict[str, Any]) -> str:
    entity_type = row.get("entity_type", "")
    action = row.get("action", "")
    if entity_type in {"sutra", "sutra_candidate"} or action in {"promoted", "accepted", "reverted", "expired"}:
        return "skills"
    if entity_type in {"episode", "smriti_memory", "sankalpa_commitment", "swapna_cycle"}:
        return "memory"
    if entity_type in {"reflection", "sankalpa"} or "tapas" in action:
        return "behavior"
    if entity_type in {"runtime_config", "provider_config"} or "config" in action or "hyperparameter" in action:
        return "runtime"
    return "behavior"


def evolution_history(*, days: int = 30) -> dict[str, Any]:
    from runtime_contract import agent_contract_map

    contracts = agent_contract_map()
    agent_names = list(contracts.keys())
    agents = {name: _empty_evolution_agent(name) for name in agent_names}
    today = datetime.now(timezone.utc)
    cutoff = today.timestamp() - max(days, 1) * 86400

    timeline_by_day: dict[str, dict[str, dict[str, int]]] = {}

    def add_point(day: str, agent: str, category: str, amount: int = 1) -> None:
        if agent not in agents:
            return
        day_row = timeline_by_day.setdefault(day, {})
        agent_row = day_row.setdefault(
            agent,
            {"tools": 0, "skills": 0, "behavior": 0, "memory": 0, "runtime": 0},
        )
        agent_row[category] = agent_row.get(category, 0) + amount
        agents[agent]["totals"][category] += amount

    trace_events = _load_trace_events()
    latency_totals: dict[str, list[int]] = {name: [] for name in agent_names}
    recent_changes: list[dict[str, Any]] = []

    for event in trace_events:
        ts = event.get("ts", "")
        try:
            event_dt = datetime.fromisoformat(ts)
        except Exception:
            continue
        if event_dt.tzinfo is None:
            event_dt = event_dt.replace(tzinfo=timezone.utc)
        if event_dt.timestamp() < cutoff:
            continue

        avatar = event.get("avatar")
        if avatar not in agents:
            continue
        day = event_dt.date().isoformat()

        if event.get("event") == "avatar_done":
            agents[avatar]["behavior"]["sessions"] += 1
            latency = int(event.get("latency_ms") or 0)
            if latency > 0:
                latency_totals[avatar].append(latency)
                recent_changes.append({
                    "ts": ts,
                    "agent": avatar,
                    "category": "behavior",
                    "title": "completed run",
                    "detail": f"{primary_discipline(avatar)} completed in {latency / 1000:.1f}s",
                })

            usage = event.get("usage") or {}
            agents[avatar]["behavior"]["total_tokens"] += int(usage.get("total_tokens") or 0)
            add_point(day, avatar, "behavior")

            trajectory = event.get("trajectory") or {}
            model = trajectory.get("model")
            if model:
                agents[avatar]["models"][model] = agents[avatar]["models"].get(model, 0) + 1
                add_point(day, avatar, "runtime")

            turns = trajectory.get("turns") or []
            for turn in turns:
                for tool_call in turn.get("tool_calls") or []:
                    tool_name = tool_call.get("tool") or "unknown"
                    agents[avatar]["tool_usage"][tool_name] = agents[avatar]["tool_usage"].get(tool_name, 0) + 1
                    add_point(day, avatar, "tools")

        degraded = event.get("degraded_capabilities") or []
        if degraded:
            agents[avatar]["behavior"]["degraded_events"] += 1

    for name, values in latency_totals.items():
        if values:
            agents[name]["behavior"]["avg_latency_ms"] = int(sum(values) / len(values))
        agents[name]["runtime"]["models_seen"] = sorted(
            agents[name]["models"],
            key=lambda model_name: agents[name]["models"][model_name],
            reverse=True,
        )

    mutations = _load_jsonl(KARMA_MUTATIONS_PATH)
    mutations.sort(key=lambda row: row.get("ts", ""))
    for row in mutations:
        ts = row.get("ts", "")
        try:
            mutation_dt = datetime.fromisoformat(ts)
        except Exception:
            continue
        if mutation_dt.tzinfo is None:
            mutation_dt = mutation_dt.replace(tzinfo=timezone.utc)
        if mutation_dt.timestamp() < cutoff:
            continue

        actor = row.get("actor")
        if actor not in agents:
            continue
        category = _mutation_category(row)
        day = mutation_dt.date().isoformat()
        add_point(day, actor, category)

        entity_type = row.get("entity_type", "")
        action = row.get("action", "")
        if entity_type in {"sutra", "sutra_candidate"} or action == "promoted":
            agents[actor]["learning"]["sutras_promoted"] += 1
        if action == "accepted":
            agents[actor]["learning"]["sutras_active"] += 1
        if action == "reverted":
            agents[actor]["learning"]["sutras_reverted"] += 1
        if entity_type == "sankalpa_commitment":
            agents[actor]["learning"]["sankalpa_updates"] += 1
            agents[actor]["memory"]["commitments"] += 1
        if entity_type == "reflection":
            agents[actor]["memory"]["reflections"] += 1
        if entity_type in {"episode", "smriti_memory"}:
            agents[actor]["memory"]["episodes"] += 1
        if entity_type == "swapna_cycle":
            agents[actor]["memory"]["swapna_touchpoints"] += 1

        detail = row.get("detail") or row.get("entity_type") or row.get("action")
        recent_changes.append({
            "ts": ts,
            "agent": actor,
            "category": category,
            "title": str(action).replace("_", " "),
            "detail": str(detail),
        })

    for episode in _load_all_episodes():
        avatar = episode.get("avatar")
        if avatar not in agents:
            continue
        ts = episode.get("ts", "")
        try:
            episode_dt = datetime.fromisoformat(ts)
        except Exception:
            continue
        if episode_dt.tzinfo is None:
            episode_dt = episode_dt.replace(tzinfo=timezone.utc)
        if episode_dt.timestamp() < cutoff:
            continue
        agents[avatar]["memory"]["episodes"] += 1

    timeline = []
    cumulative = {
        name: {"tools": 0, "skills": 0, "behavior": 0, "memory": 0, "runtime": 0}
        for name in agent_names
    }
    for day in sorted(timeline_by_day):
        agent_points = []
        for name in agent_names:
            daily = timeline_by_day.get(day, {}).get(name, {"tools": 0, "skills": 0, "behavior": 0, "memory": 0, "runtime": 0})
            for key, value in daily.items():
                cumulative[name][key] += value
            agent_points.append({
                "agent": name,
                "daily": daily,
                "cumulative": dict(cumulative[name]),
            })
        timeline.append({"date": day, "agents": agent_points})

    for name in agent_names:
        top_tools = sorted(
            agents[name]["tool_usage"].items(),
            key=lambda item: item[1],
            reverse=True,
        )
        agents[name]["tool_usage"] = [
            {"tool": tool, "count": count}
            for tool, count in top_tools[:8]
        ]
        agents[name]["models"] = [
            {"model": model, "count": count}
            for model, count in sorted(
                agents[name]["models"].items(),
                key=lambda item: item[1],
                reverse=True,
            )
        ]

    recent_changes.sort(key=lambda row: row.get("ts", ""), reverse=True)
    recent_changes = recent_changes[:40]

    return {
        "generated_at": today.isoformat(),
        "window_days": days,
        "categories": ["tools", "skills", "behavior", "memory", "runtime"],
        "config": {
            "tapas_promote_threshold": float(os.environ.get("TAPAS_PROMOTE_THRESHOLD", "0.80")),
            "sutra_cooldown_hours": int(os.environ.get("SUTRA_COOLDOWN_HOURS", "24")),
            "tapas_judge_model": os.environ.get("TAPAS_JUDGE_MODEL", "deepseek/deepseek-v4-pro"),
        },
        "agents": [agents[name] for name in agent_names],
        "timeline": timeline,
        "recent_changes": recent_changes,
    }
