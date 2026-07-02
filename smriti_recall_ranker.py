from __future__ import annotations

import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).parent
_PHASE1 = _ROOT / "phase-1"
for _path in [str(_ROOT), str(_PHASE1)]:
    if _path not in sys.path:
        sys.path.insert(0, _path)

from context_governor import compact_text_block, count_text_tokens
from smriti_indexer import ensure_project_wiki_indexed, ensure_user_episode_index
from smriti_vector_store import VectorMemoryRecord, memory_tier_diagnostics, search_records


def _parse_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except Exception:
        return None


def _lexical_score(query: str, text: str) -> float:
    query_words = set(re.findall(r"\w+", query.lower()))
    text_words = set(re.findall(r"\w+", text.lower()))
    if not query_words or not text_words:
        return 0.0
    return len(query_words & text_words) / max(len(query_words), 1)


def _recency_bonus(ts: str | None) -> float:
    parsed = _parse_ts(ts)
    if parsed is None:
        return 0.0
    age_days = max((datetime.now(timezone.utc) - parsed.astimezone(timezone.utc)).days, 0)
    return max(0.0, 1.0 - min(age_days / 365.0, 1.0))


def _load_episode_text(record: VectorMemoryRecord) -> str:
    path = Path(record.source_path)
    if not path.exists():
        return record.text
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except Exception:
            continue
        if str(row.get("id")) != record.source_ref:
            continue
        task = row.get("task", "")
        result = row.get("result", "")
        return f"[EPISODE]\nTask: {task}\nResult: {result}".strip()
    return record.text


def _load_wiki_text(record: VectorMemoryRecord) -> str:
    path = Path(record.source_path)
    if not path.exists():
        return record.text
    text = path.read_text(encoding="utf-8")
    if record.source_ref and record.source_ref != "document":
        parts = re.split(r"\n(?=##\s)", text)
        for part in parts:
            chunk = part.strip()
            if not chunk:
                continue
            first_line = chunk.splitlines()[0].strip()
            anchor = first_line[3:].strip() if first_line.startswith("## ") else "document"
            if anchor == record.source_ref:
                return f"[{path.stem.upper()}]\n{chunk}".strip()
    return f"[{path.stem.upper()}]\n{text}".strip()


def _exact_reread(record: VectorMemoryRecord) -> str:
    if record.source_kind == "episode":
        return _load_episode_text(record)
    if record.source_kind == "wiki_section":
        return _load_wiki_text(record)
    return record.text


def _rank_hits(query: str, hits: list[dict[str, Any]]) -> list[dict[str, Any]]:
    for hit in hits:
        exact_text = hit["exact_text"]
        hit["final_score"] = (
            (0.6 * float(hit["score"]))
            + (0.3 * _lexical_score(query, exact_text))
            + (0.1 * _recency_bonus(hit["record"].updated_at))
        )
        if hit["record"].project_id and hit["record"].project_id != "general":
            hit["final_score"] += 0.05
    hits.sort(key=lambda item: item["final_score"], reverse=True)
    return hits


def _fit_blocks(
    *,
    query: str,
    model: str,
    token_budget: int | None,
    label: str,
    ranked_hits: list[dict[str, Any]],
    max_items: int,
) -> dict[str, Any]:
    remaining = token_budget
    blocks: list[str] = []
    provenance: list[dict[str, Any]] = []
    compaction_applied: list[str] = []
    for hit in ranked_hits[:max_items]:
        exact_text = hit["exact_text"]
        if not exact_text:
            continue
        candidate = f"[{label}]\n{exact_text}"
        if remaining is not None and count_text_tokens(model, candidate) > remaining:
            compacted = compact_text_block(
                candidate,
                model=model,
                token_budget=max(96, remaining),
                query=query,
                preserve_artifacts=True,
            )
            if not compacted.text:
                continue
            candidate = compacted.text
            compaction_applied.extend(f"{label.lower()}:{step}" for step in compacted.applied)
        if remaining is not None:
            needed = count_text_tokens(model, candidate)
            if needed > remaining:
                continue
            remaining -= needed
        blocks.append(candidate)
        provenance.append({
            "kind": label.lower(),
            "record_id": hit["record"].record_id,
            "namespace": hit["record"].namespace,
            "tier": hit["record"].tier,
            "backend": hit["backend"],
            "compressed_candidate": True,
            "exact_reread": True,
            "source_path": hit["record"].source_path,
            "preview": hit["record"].preview[:180],
        })
    return {
        "text": "\n\n".join(blocks),
        "provenance": provenance,
        "compaction_applied": compaction_applied,
        "remaining_budget": remaining,
    }


def build_semantic_memory_context(
    *,
    query: str,
    user_id: str = "default",
    project_id: str = "general",
    token_budget: int | None = None,
    model: str = "deepseek/deepseek-v4-flash",
    limit: int = 4,
) -> dict[str, Any]:
    ensure_user_episode_index(user_id)
    raw_hits = search_records(
        user_id=user_id,
        namespace="episodic_summary",
        query=query,
        limit=max(limit * 2, 6),
        project_id=project_id,
    )
    hits = _rank_hits(
        query,
        [
            {
                "record": hit.record,
                "score": hit.score,
                "backend": hit.backend,
                "exact_text": _exact_reread(hit.record),
            }
            for hit in raw_hits
        ],
    )
    fitted = _fit_blocks(
        query=query,
        model=model,
        token_budget=token_budget,
        label="SMRITI VECTOR MEMORY",
        ranked_hits=hits,
        max_items=limit,
    )
    fitted["diagnostics"] = memory_tier_diagnostics(user_id)
    return fitted


def build_project_memory_context(
    *,
    query: str,
    user_id: str = "default",
    project_id: str = "general",
    token_budget: int | None = None,
    model: str = "deepseek/deepseek-v4-flash",
    limit: int = 5,
) -> dict[str, Any]:
    ensure_project_wiki_indexed(user_id, project_id)
    raw_hits = search_records(
        user_id=user_id,
        namespace="project_wiki",
        query=query,
        limit=max(limit * 2, 6),
        project_id=project_id,
    )
    hits = _rank_hits(
        query,
        [
            {
                "record": hit.record,
                "score": hit.score,
                "backend": hit.backend,
                "exact_text": _exact_reread(hit.record),
            }
            for hit in raw_hits
        ],
    )
    fitted = _fit_blocks(
        query=query,
        model=model,
        token_budget=token_budget,
        label="PROJECT MEMORY",
        ranked_hits=hits,
        max_items=limit,
    )
    fitted["diagnostics"] = memory_tier_diagnostics(user_id)
    return fitted
