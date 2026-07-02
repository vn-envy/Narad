"""
Markov spend-pattern analysis for Rama.

Builds a category → next-category transition probability matrix from
transaction history, then predicts the most likely next spending category.

Adapted from IBM/AssetOpsBench src/servers/wo/tools.py (Apache 2.0):
  get_transition_matrix + predict_next_wo  →  spend transition + predict_next_category

Usage:
    from finance_patterns import build_spend_transition_matrix, predict_next_category
    txns = [{"category": "Dining", "amount": 800, "date": "2026-05-01"}, ...]
    matrix = build_spend_transition_matrix(txns)
    predictions = predict_next_category("Dining", matrix)
    # [("Shopping", 0.68), ("Transport", 0.22), ("Other", 0.10)]
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any


def build_spend_transition_matrix(
    transactions: list[dict[str, Any]],
) -> dict[str, dict[str, float]]:
    """Build a category → next-category probability matrix from transaction history.

    Args:
        transactions: List of dicts with at least {"category": str, "date": str}.
                      Must be sorted by date ascending (oldest first).

    Returns:
        Nested dict: {from_category: {to_category: probability, ...}, ...}
        Probabilities per row sum to 1.0.
    """
    counts: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for i in range(len(transactions) - 1):
        a = transactions[i].get("category", "Other")
        b = transactions[i + 1].get("category", "Other")
        counts[a][b] += 1

    matrix: dict[str, dict[str, float]] = {}
    for cat, nexts in counts.items():
        total = sum(nexts.values())
        if total > 0:
            matrix[cat] = {k: round(v / total, 4) for k, v in sorted(
                nexts.items(), key=lambda x: x[1], reverse=True
            )}
    return matrix


def predict_next_category(
    last_category: str,
    matrix: dict[str, dict[str, float]],
    top_n: int = 3,
) -> list[tuple[str, float]]:
    """Return the top N predicted next spending categories given the last category.

    Args:
        last_category: The most recent spending category.
        matrix:        Transition matrix from build_spend_transition_matrix().
        top_n:         Number of predictions to return (default 3).

    Returns:
        List of (category, probability) tuples sorted by probability descending.
        Empty list if last_category not in matrix.
    """
    row = matrix.get(last_category, {})
    sorted_preds = sorted(row.items(), key=lambda x: x[1], reverse=True)
    return sorted_preds[:top_n]


def get_pattern_insights(
    transactions: list[dict[str, Any]],
    last_category: str | None = None,
) -> dict[str, Any]:
    """High-level spending pattern analysis with Markov predictions.

    Args:
        transactions:  Full transaction history (sorted oldest-first).
        last_category: Optional override for the 'most recent' category.
                       If None, uses the last transaction's category.

    Returns:
        {
          "matrix_size": int,              # number of categories in the matrix
          "last_category": str,
          "predictions": [{"category": str, "probability": float, "pct": str}, ...],
          "top_sequences": [{"from": str, "to": str, "probability": float}, ...],
          "status": "ok" | "insufficient_data",
        }
    """
    if len(transactions) < 5:
        return {
            "status": "insufficient_data",
            "message": "Need at least 5 transactions to build spending patterns.",
        }

    sorted_txns = sorted(transactions, key=lambda x: x.get("date", ""))
    matrix = build_spend_transition_matrix(sorted_txns)

    if last_category is None:
        last_category = sorted_txns[-1].get("category", "Other")

    predictions = predict_next_category(last_category, matrix, top_n=3)
    pred_dicts = [
        {
            "category":    cat,
            "probability": prob,
            "pct":         f"{prob * 100:.0f}%",
        }
        for cat, prob in predictions
    ]

    # Top 3 most common transitions overall
    all_transitions: list[tuple[str, str, float]] = []
    for from_cat, nexts in matrix.items():
        for to_cat, prob in nexts.items():
            all_transitions.append((from_cat, to_cat, prob))
    top_seqs = sorted(all_transitions, key=lambda x: x[2], reverse=True)[:3]

    return {
        "status":          "ok",
        "matrix_size":     len(matrix),
        "last_category":   last_category,
        "predictions":     pred_dicts,
        "top_sequences":   [
            {"from": a, "to": b, "probability": round(p, 3)}
            for a, b, p in top_seqs
        ],
    }
