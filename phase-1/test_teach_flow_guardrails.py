from __future__ import annotations

import sys
import unittest
from pathlib import Path

_r = next(p for p in Path(__file__).resolve().parents if (p / "narad_paths.py").exists())
sys.path[:0] = [str(_r)]  # narad root hop
import narad_paths  # noqa: F401

from learning_workspace import is_learning_query
from server import (
    _clean_optional_text,
    _extract_learning_artifact_request,
    _is_explicit_learning_artifact_request,
    _is_explicit_learning_artifact_edit,
    _learning_artifact_offer_pending,
)


class TeachFlowGuardrailsTest(unittest.TestCase):
    def test_plain_teach_query_is_not_artifact_request(self) -> None:
        query = "Teach me transformer attention for interviews"
        self.assertTrue(is_learning_query(query))
        self.assertFalse(_is_explicit_learning_artifact_request(query))

    def test_explicit_flashcard_request_is_artifact_request(self) -> None:
        query = "Make flashcards for transformer attention"
        self.assertTrue(_is_explicit_learning_artifact_request(query))

    def test_concept_diagram_request_extracts_topic_and_kind(self) -> None:
        query = "Create a concept diagram for how RAG works"
        self.assertEqual(
            _extract_learning_artifact_request(query),
            ("how RAG works", "concept_map"),
        )

    def test_offer_pending_allows_short_visualise_reply(self) -> None:
        self.assertTrue(_is_explicit_learning_artifact_request("yes", offer_pending=True))
        self.assertTrue(_is_explicit_learning_artifact_request("D", offer_pending=True))

    def test_explicit_artifact_edit_detection(self) -> None:
        self.assertTrue(_is_explicit_learning_artifact_edit("Add one more card about scaling", artifact_type="flashcards"))
        self.assertTrue(_is_explicit_learning_artifact_edit("Add a node for retrieval latency", artifact_type="concept_map"))
        self.assertFalse(_is_explicit_learning_artifact_edit("Explain that more simply", artifact_type="flashcards"))

    def test_offer_detection_looks_for_visual_artifact_offer(self) -> None:
        self.assertTrue(
            _learning_artifact_offer_pending(
                "Would you like me to create a visual learning artifact for this — flashcards, "
                "an interactive quiz, or a diagram? I can have that built for you."
            )
        )
        self.assertFalse(_learning_artifact_offer_pending("Let's keep learning step by step."))

    def test_clean_optional_text_normalizes_nullish_workspace_values(self) -> None:
        self.assertEqual(_clean_optional_text(None), "")
        self.assertEqual(_clean_optional_text("None"), "")
        self.assertEqual(_clean_optional_text(" null "), "")
        self.assertEqual(_clean_optional_text("learn_123"), "learn_123")


if __name__ == "__main__":
    unittest.main()
