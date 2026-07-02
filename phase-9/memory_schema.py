"""
Memory schema for Smriti v3 RRF fusion layer.

Two hierarchical levels:
  L1 Fact   — atomic extracted fact from a session trace (3–7 per session)
  L2 Scenario — cluster of related L1 facts grouped by project/topic

These are used alongside the existing Markdown wiki (smriti_v2.py) and LanceDB
vector store to enable Reciprocal Rank Fusion (RRF) recall.

RRF formula:
  score(d, q) = Σ_r  1 / (rank_r(d) + k)
where r ∈ {bm25, cosine, recency} and k=60 (standard RRF constant).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

# ── L1: Atomic fact ────────────────────────────────────────────────────────────

@dataclass
class Fact:
    """A single atomic fact extracted from a session trace.

    Attributes:
        text:        The fact in plain English (≤ 200 chars).
        entity_tags: List of entity labels, e.g. ["finance", "goal", "learner_profile"].
        session_id:  Source session UUID.
        avatar:      Which avatar produced this fact.
        user_id:     Owner of this fact.
        ts:          ISO-8601 timestamp (UTC).
    """
    text:        str
    entity_tags: list[str]
    session_id:  str
    avatar:      str
    user_id:     str
    ts:          str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> dict:
        return {
            "text":        self.text,
            "entity_tags": self.entity_tags,
            "session_id":  self.session_id,
            "avatar":      self.avatar,
            "user_id":     self.user_id,
            "ts":          self.ts,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Fact":
        return cls(
            text=d["text"],
            entity_tags=d.get("entity_tags", []),
            session_id=d.get("session_id", ""),
            avatar=d.get("avatar", ""),
            user_id=d.get("user_id", ""),
            ts=d.get("ts", ""),
        )


# ── L2: Scenario (cluster of L1 facts) ────────────────────────────────────────

@dataclass
class Scenario:
    """A topic-level cluster of related L1 facts.

    Attributes:
        topic:      Short topic label, e.g. "budget planning 2025".
        facts:      List of Fact objects belonging to this cluster.
        embedding:  Dense vector (list[float]) for cosine similarity — None until computed.
        user_id:    Owner.
        ts:         ISO-8601 timestamp of last update (UTC).
        scenario_id: UUID (auto-generated or supplied).
    """
    topic:       str
    facts:       list[Fact]
    user_id:     str
    embedding:   Optional[list[float]] = None
    ts:          str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    scenario_id: str = field(default_factory=lambda: __import__("uuid").uuid4().hex)

    @property
    def text(self) -> str:
        """Concatenated text of all facts — used as the embedding input."""
        return " ".join(f.text for f in self.facts)

    def to_dict(self) -> dict:
        return {
            "scenario_id": self.scenario_id,
            "topic":       self.topic,
            "facts":       [f.to_dict() for f in self.facts],
            "user_id":     self.user_id,
            "embedding":   self.embedding,
            "ts":          self.ts,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Scenario":
        return cls(
            topic=d["topic"],
            facts=[Fact.from_dict(fd) for fd in d.get("facts", [])],
            user_id=d.get("user_id", ""),
            embedding=d.get("embedding"),
            ts=d.get("ts", ""),
            scenario_id=d.get("scenario_id", __import__("uuid").uuid4().hex),
        )


# ── RRF helpers ────────────────────────────────────────────────────────────────

def rrf_score(ranks: list[tuple[str, int]], k: int = 60) -> dict[str, float]:
    """Compute RRF scores from a list of (doc_id, rank) pairs across multiple retrievers.

    Args:
        ranks: list of (doc_id, rank) — rank is 1-indexed (rank=1 is the top result).
        k:     RRF smoothing constant (default 60, as per the original paper).

    Returns:
        dict mapping doc_id → accumulated RRF score (higher = more relevant).
    """
    scores: dict[str, float] = {}
    for doc_id, rank in ranks:
        scores[doc_id] = scores.get(doc_id, 0.0) + 1.0 / (rank + k)
    return scores


def merge_ranked(
    bm25_hits:     list[str],
    cosine_hits:   list[str],
    recency_hits:  list[str],
    k: int = 60,
    top_n: int = 5,
) -> list[str]:
    """Merge three ranked lists with RRF and return top-N doc IDs.

    Args:
        bm25_hits:    Ordered list of doc IDs from BM25 / FTS5 retrieval.
        cosine_hits:  Ordered list of doc IDs from cosine similarity retrieval.
        recency_hits: Ordered list of doc IDs from recency-decay retrieval.
        k:            RRF constant.
        top_n:        Number of results to return.

    Returns:
        Ordered list of doc IDs, highest RRF score first.
    """
    all_ranks: list[tuple[str, int]] = []
    for ranked_list in (bm25_hits, cosine_hits, recency_hits):
        for rank, doc_id in enumerate(ranked_list, start=1):
            all_ranks.append((doc_id, rank))

    scores = rrf_score(all_ranks, k=k)
    return sorted(scores, key=lambda d: scores[d], reverse=True)[:top_n]
