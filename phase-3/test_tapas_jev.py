from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

_r = next(p for p in Path(__file__).resolve().parents if (p / "narad_paths.py").exists())
sys.path[:0] = [str(_r)]
import narad_paths  # noqa: F401

# isort: split
import tapas

from decision_engine import DecisionAnswer, DecisionResult


def _answer(kind: str, value, confidence: float = 0.9) -> DecisionAnswer:
    probabilities = {}
    if kind == "noul":
        probability = 0.9 if value else 0.1
        probabilities = {"true": probability, "false": 1.0 - probability}
    return DecisionAnswer(
        kind=kind,
        value=value,
        confidence=confidence,
        probabilities=probabilities,
    )


class TapasJevTests(unittest.TestCase):
    def test_jev_dimensions_are_aggregated_in_code(self) -> None:
        decision = DecisionResult(
            decision_id="tapas_score_v1",
            status="ok",
            provider="typesafe",
            model="jev-latest",
            answers={
                "correctness": _answer("score", 7.0),
                "specificity": _answer("score", 6.0),
                "actionability": _answer("score", 8.0),
                "conciseness": _answer("score", 7.0),
                "unsupported_claims": _answer("noul", False),
                "sequence_violation": _answer("noul", False),
            },
            latency_ms=140,
            input_tokens=500,
            estimated_cost_usd=0.000021,
        )
        with patch("decision_engine.evaluate_decision", return_value=decision):
            scored = tapas.score_session_detailed("Build it", "Parashurama", "Implemented and tested", provider="jev")

        self.assertAlmostEqual(scored.score, 0.795)
        self.assertTrue(scored.hallucination_free)
        self.assertTrue(scored.sequence_correct)
        self.assertEqual(scored.provider, "jev")
        self.assertEqual(scored.confidence, 0.9)

    def test_auto_mode_falls_back_when_jev_is_uncertain(self) -> None:
        uncertain = tapas.TapasScore(
            score=0.8,
            reason="uncertain",
            hallucination_free=True,
            sequence_correct=True,
            provider="jev",
            model="jev-latest",
            confidence=0.2,
            latency_ms=100,
        )
        fallback = tapas.TapasScore(
            score=0.7,
            reason="llm result",
            hallucination_free=True,
            sequence_correct=True,
            provider="llm",
            model="deepseek/deepseek-flash",
            confidence=0.5,
            latency_ms=900,
        )
        with patch.object(tapas, "_score_session_jev", return_value=uncertain), patch.object(
            tapas, "_score_session_llm", return_value=fallback
        ):
            scored = tapas.score_session_detailed("q", "Matsya", "r", provider="auto")

        self.assertEqual(scored.provider, "llm")
        self.assertIn("below", scored.fallback_reason or "")

    def test_jev_sequence_violation_applies_deterministic_penalty(self) -> None:
        decision = DecisionResult(
            decision_id="tapas_score_v1",
            status="ok",
            provider="typesafe",
            model="jev-latest",
            answers={
                "correctness": _answer("score", 10.0),
                "specificity": _answer("score", 10.0),
                "actionability": _answer("score", 10.0),
                "conciseness": _answer("score", 10.0),
                "unsupported_claims": _answer("noul", False),
                "sequence_violation": _answer("noul", True),
            },
        )
        with patch("decision_engine.evaluate_decision", return_value=decision):
            scored = tapas.score_session_detailed("Teach me", "Krishna", "Everything at once", provider="jev")
        self.assertEqual(scored.score, 0.8)
        self.assertFalse(scored.sequence_correct)


if __name__ == "__main__":
    unittest.main()
