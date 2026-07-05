from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from turbovec_policy import memory_tier_policy_payload

from narad_config import SMRITI_MANIFEST_DIR, SMRITI_VECTOR_DIR
from smriti_embed import (  # noqa: F401  — re-exported for callers
    EmbeddingUnavailableError,
    current_embedding_model,
    embed_text,
)

try:
    import numpy as np
except Exception:  # pragma: no cover - numpy is expected but keep additive
    np = None  # type: ignore[assignment]

try:
    from turbovec import IdMapIndex
except Exception:  # pragma: no cover - optional acceleration
    IdMapIndex = None  # type: ignore[assignment]


def _safe_slug(value: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9._-]+", "_", value.strip())
    return slug[:80] or "default"


def _sha(value: str) -> str:
    return hashlib.sha1(value.encode("utf-8")).hexdigest()


def _numeric_id(record_id: str) -> int:
    return int(_sha(record_id)[:16], 16)


@dataclass
class VectorMemoryRecord:
    record_id: str
    namespace: str
    tier: str
    user_id: str
    project_id: str
    source_kind: str
    source_path: str
    source_ref: str
    created_at: str
    updated_at: str
    preview: str
    text: str
    content_hash: str
    embedding_model: str
    dim: int
    metadata: dict[str, Any] = field(default_factory=dict)
    embedding: list[float] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class VectorSearchHit:
    record: VectorMemoryRecord
    score: float
    backend: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "record": self.record.to_dict(),
            "score": self.score,
            "backend": self.backend,
        }


def _manifest_path(user_id: str, namespace: str, tier: str, embedding_model: str) -> Path:
    return (
        SMRITI_MANIFEST_DIR
        / _safe_slug(user_id)
        / _safe_slug(embedding_model)
        / _safe_slug(namespace)
        / _safe_slug(tier)
        / "records.jsonl"
    )


def _index_dir(user_id: str, namespace: str, tier: str, embedding_model: str) -> Path:
    return (
        SMRITI_VECTOR_DIR
        / _safe_slug(user_id)
        / _safe_slug(embedding_model)
        / _safe_slug(namespace)
        / _safe_slug(tier)
    )


def _load_records(path: Path) -> dict[str, VectorMemoryRecord]:
    if not path.exists():
        return {}
    out: dict[str, VectorMemoryRecord] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
            record = VectorMemoryRecord(**payload)
            out[record.record_id] = record
        except Exception:
            continue
    return out


def _write_records(path: Path, records: list[VectorMemoryRecord]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for record in sorted(records, key=lambda item: (item.updated_at, item.record_id)):
            fh.write(json.dumps(record.to_dict(), ensure_ascii=False) + "\n")


def _mark_dirty(index_dir: Path) -> None:
    index_dir.mkdir(parents=True, exist_ok=True)
    (index_dir / ".dirty").write_text("dirty", encoding="utf-8")


def _clear_dirty(index_dir: Path) -> None:
    (index_dir / ".dirty").unlink(missing_ok=True)


def _index_dirty(index_dir: Path) -> bool:
    return (index_dir / ".dirty").exists()


def sync_records(
    *,
    user_id: str,
    namespace: str,
    records: list[VectorMemoryRecord],
    prune_project_id: str | None = None,
    prune_source_kind: str | None = None,
) -> None:
    if not records:
        return
    grouped: dict[tuple[str, str], list[VectorMemoryRecord]] = {}
    for record in records:
        grouped.setdefault((record.tier, record.embedding_model), []).append(record)

    for (tier, embedding_model), bucket in grouped.items():
        manifest = _manifest_path(user_id, namespace, tier, embedding_model)
        existing = _load_records(manifest)
        if prune_project_id is not None:
            existing = {
                rid: rec
                for rid, rec in existing.items()
                if not (
                    rec.project_id == prune_project_id
                    and (prune_source_kind is None or rec.source_kind == prune_source_kind)
                )
            }
        for record in bucket:
            existing[record.record_id] = record
        _write_records(manifest, list(existing.values()))
        _mark_dirty(_index_dir(user_id, namespace, tier, embedding_model))


def upsert_record(record: VectorMemoryRecord) -> None:
    sync_records(user_id=record.user_id, namespace=record.namespace, records=[record])


def _build_turbovec_index(
    *,
    records: list[VectorMemoryRecord],
    index_dir: Path,
    bit_width: int,
) -> Any | None:
    if IdMapIndex is None or np is None or not records:
        return None
    dim = records[0].dim
    index = IdMapIndex(dim=dim, bit_width=bit_width)
    vectors = np.asarray([record.embedding for record in records], dtype=np.float32)
    ids = np.asarray([_numeric_id(record.record_id) for record in records], dtype=np.uint64)
    index.add_with_ids(vectors, ids)
    index_dir.mkdir(parents=True, exist_ok=True)
    index_path = index_dir / "index.tvim"
    index.write(str(index_path))
    meta_path = index_dir / "id_map.json"
    meta_path.write_text(
        json.dumps(
            {
                str(_numeric_id(record.record_id)): record.record_id
                for record in records
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    _clear_dirty(index_dir)
    return index


def _load_turbovec_index(index_dir: Path) -> tuple[Any | None, dict[int, str]]:
    if IdMapIndex is None:
        return None, {}
    index_path = index_dir / "index.tvim"
    meta_path = index_dir / "id_map.json"
    if not index_path.exists() or not meta_path.exists():
        return None, {}
    try:
        index = IdMapIndex.load(str(index_path))
        raw = json.loads(meta_path.read_text(encoding="utf-8"))
        mapping = {int(key): value for key, value in raw.items()}
        return index, mapping
    except Exception:
        return None, {}


def _exact_cosine_search(
    query_vector: list[float],
    records: list[VectorMemoryRecord],
    *,
    limit: int,
) -> list[VectorSearchHit]:
    if not records:
        return []
    if np is not None:
        matrix = np.asarray([record.embedding for record in records], dtype=np.float32)
        query = np.asarray(query_vector, dtype=np.float32)
        scores = matrix @ query
        ranked = sorted(
            enumerate(scores.tolist()),
            key=lambda item: item[1],
            reverse=True,
        )[:limit]
    else:
        ranked = sorted(
            enumerate(sum(q * v for q, v in zip(query_vector, record.embedding)) for record in records),
            key=lambda item: item[1],
            reverse=True,
        )[:limit]
    return [
        VectorSearchHit(record=records[idx], score=float(score), backend="exact")
        for idx, score in ranked
    ]


def _turbovec_search(
    query_vector: list[float],
    records: list[VectorMemoryRecord],
    *,
    user_id: str,
    namespace: str,
    tier: str,
    embedding_model: str,
    limit: int,
) -> list[VectorSearchHit]:
    if IdMapIndex is None or np is None or not records:
        return []
    index_dir = _index_dir(user_id, namespace, tier, embedding_model)
    index, mapping = _load_turbovec_index(index_dir)
    if index is None or _index_dirty(index_dir):
        index = _build_turbovec_index(
            records=records,
            index_dir=index_dir,
            bit_width=4 if "4bit" in tier else 2,
        )
        mapping = { _numeric_id(record.record_id): record.record_id for record in records }
    if index is None:
        return []
    by_record_id = {record.record_id: record for record in records}
    allowlist = np.asarray([_numeric_id(record.record_id) for record in records], dtype=np.uint64)
    try:
        scores, ids = index.search(
            np.asarray([query_vector], dtype=np.float32),
            k=min(limit, len(records)),
            allowlist=allowlist,
        )
    except Exception:
        return []
    result_ids = ids[0].tolist() if hasattr(ids, "tolist") else list(ids[0])
    result_scores = scores[0].tolist() if hasattr(scores, "tolist") else list(scores[0])
    hits: list[VectorSearchHit] = []
    for score, numeric in zip(result_scores, result_ids):
        record_id = mapping.get(int(numeric))
        if not record_id:
            continue
        record = by_record_id.get(record_id)
        if not record:
            continue
        hits.append(VectorSearchHit(record=record, score=float(score), backend="turbovec"))
    return hits


def list_records(
    *,
    user_id: str,
    namespace: str,
    tier: str | None = None,
    embedding_model: str | None = None,
) -> list[VectorMemoryRecord]:
    records: list[VectorMemoryRecord] = []
    base = SMRITI_MANIFEST_DIR / _safe_slug(user_id)
    if not base.exists():
        return []
    model_roots = [base / _safe_slug(embedding_model)] if embedding_model else list(base.iterdir())
    for model_root in model_roots:
        namespace_root = model_root / _safe_slug(namespace)
        if not namespace_root.exists():
            continue
        tier_roots = [namespace_root / _safe_slug(tier)] if tier else list(namespace_root.iterdir())
        for tier_root in tier_roots:
            manifest = tier_root / "records.jsonl"
            records.extend(_load_records(manifest).values())
    return records


def search_records(
    *,
    user_id: str,
    namespace: str,
    query: str,
    limit: int = 6,
    project_id: str | None = None,
    workspace_root: str | None = None,
    tiers: list[str] | None = None,
) -> list[VectorSearchHit]:
    query_vector, embedding_model = embed_text(query)
    grouped_records: dict[str, list[VectorMemoryRecord]] = {}
    search_tiers = tiers or []
    if not search_tiers:
        policy = memory_tier_policy_payload()["rules"].get(namespace, {})
        default_tier = policy.get("default_tier")
        archive_tier = policy.get("archive_tier")
        search_tiers = [tier for tier in [default_tier, archive_tier] if tier]
    if not search_tiers:
        search_tiers = ["turbovec_4bit"]

    for tier in search_tiers:
        bucket = list_records(
            user_id=user_id,
            namespace=namespace,
            tier=tier,
            embedding_model=embedding_model,
        )
        filtered = [
            record for record in bucket
            if (project_id is None or record.project_id in {project_id, "general", ""})
            and (
                workspace_root is None
                or record.metadata.get("workspace_root") == workspace_root
                or record.metadata.get("workspace_root") is None
            )
        ]
        if filtered:
            grouped_records[tier] = filtered

    if not grouped_records:
        return []
    combined: list[VectorSearchHit] = []
    for tier, filtered in grouped_records.items():
        turbo_hits = _turbovec_search(
            query_vector,
            filtered,
            user_id=user_id,
            namespace=namespace,
            tier=tier,
            embedding_model=embedding_model,
            limit=limit,
        )
        combined.extend(turbo_hits or _exact_cosine_search(query_vector, filtered, limit=limit))
    combined.sort(key=lambda item: item.score, reverse=True)
    return combined[:limit]


def memory_tier_diagnostics(user_id: str = "default") -> dict[str, Any]:
    stats: dict[str, dict[str, Any]] = {}
    base = SMRITI_MANIFEST_DIR / _safe_slug(user_id)
    if base.exists():
        for model_root in base.iterdir():
            if not model_root.is_dir():
                continue
            for namespace_root in model_root.iterdir():
                if not namespace_root.is_dir():
                    continue
                namespace = namespace_root.name
                for tier_root in namespace_root.iterdir():
                    if not tier_root.is_dir():
                        continue
                    tier = tier_root.name
                    records = list(_load_records(tier_root / "records.jsonl").values())
                    bucket = stats.setdefault(namespace, {})
                    bucket[tier] = {
                        "record_count": len(records),
                        "embedding_model": model_root.name,
                    }
    return {
        "user_id": user_id,
        "backend": {
            "turbovec_available": IdMapIndex is not None,
            "numpy_available": np is not None,
            "default_mode": "turbovec_if_available_else_exact",
        },
        "policy": memory_tier_policy_payload(),
        "namespaces": stats,
    }
