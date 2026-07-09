from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

_r = next(p for p in Path(__file__).resolve().parents if (p / "narad_paths.py").exists())
sys.path[:0] = [str(_r)]  # narad root hop
import narad_paths  # noqa: F401  — registers all phase dirs; must precede phase imports

# isort: split
import guru_engine
import kala_scheduler
import learning_workspace
import vahana

_SYLLABUS = {
    "workspace_id": "ws",
    "topic": "transformer attention",
    "atoms": [
        {
            "id": "dot-product", "name": "Dot product", "prerequisites": [],
            "eli5": "Matching socks by how alike they are.",
            "plain": "A similarity score between two vectors.",
            "misconception": "It is not a distance — bigger means more similar.",
            "check": {"q": "What does a large dot product mean?", "good_answer": "high similarity"},
        },
        {
            "id": "softmax", "name": "Softmax", "prerequisites": ["dot-product"],
            "eli5": "Sharing one pizza by hunger.",
            "plain": "Turns scores into weights that sum to one.",
            "misconception": "It is not a max — everything gets some weight.",
            "check": {"q": "Why do softmax outputs sum to one?", "good_answer": "normalization"},
        },
    ],
}


class GuruFrontierTests(unittest.TestCase):
    def test_frontier_walks_prerequisite_order(self) -> None:
        self.assertEqual(guru_engine.frontier_atom(_SYLLABUS, {})["id"], "dot-product")
        mastered = {"dot-product": {"status": "mastered"}}
        self.assertEqual(guru_engine.frontier_atom(_SYLLABUS, mastered)["id"], "softmax")
        all_done = {"dot-product": {"status": "mastered"}, "softmax": {"status": "mastered"}}
        self.assertIsNone(guru_engine.frontier_atom(_SYLLABUS, all_done))

    def test_frontier_blocked_by_unmastered_prereq(self) -> None:
        shaky = {"dot-product": {"status": "shaky"}}
        self.assertEqual(guru_engine.frontier_atom(_SYLLABUS, shaky)["id"], "dot-product")

    def test_mastery_summary_counts(self) -> None:
        counts = guru_engine.mastery_summary(_SYLLABUS, {"dot-product": {"status": "shaky"}})
        self.assertEqual(counts, {"total": 2, "mastered": 0, "shaky": 1})


class TeachPatternTests(unittest.TestCase):
    def test_new_teach_intents_detected(self) -> None:
        for query in (
            "can you teach me softmax",
            "i want to learn linear algebra",
            "help me learn calculus",
            "walk me through backprop",
            "I'm studying operating systems",
            "quiz me about attention",
        ):
            self.assertTrue(learning_workspace.is_learning_query(query), query)

    def test_topic_extraction_strips_new_prefixes(self) -> None:
        self.assertEqual(learning_workspace.extract_learning_topic("i want to learn calculus"), "calculus")
        self.assertEqual(learning_workspace.extract_learning_topic("walk me through backprop"), "backprop")


class GuruReviewSchedulerTests(unittest.TestCase):
    def test_review_digest_fires_once_per_day(self) -> None:
        from datetime import datetime

        with tempfile.TemporaryDirectory() as tmpdir:
            temp_root = Path(tmpdir)
            delivered: list[dict] = []
            with patch.object(learning_workspace, "LEARNING_DIR", temp_root), \
                 patch.object(guru_engine, "LEARNING_DIR", temp_root), \
                 patch.object(kala_scheduler, "LEARNING_DIR", temp_root), \
                 patch.object(vahana, "deliver", side_effect=lambda **kw: delivered.append(kw) or {"id": "x"}):
                workspace = learning_workspace.ensure_workspace(
                    user_id="default", topic="softmax", mission="m", session_id="s",
                )
                ws_dir = temp_root / "default" / workspace["workspace_id"]
                (ws_dir / "syllabus.json").write_text(json.dumps(_SYLLABUS), encoding="utf-8")
                (ws_dir / "learner_state.json").write_text(json.dumps({
                    "dot-product": {"status": "shaky", "next_review": "2020-01-01T00:00:00+00:00"},
                }), encoding="utf-8")

                state: dict = {}
                noon = datetime(2026, 7, 9, 12, 0)
                self.assertEqual(kala_scheduler._fire_due_reviews(noon, state), 1)
                self.assertEqual(len(delivered), 1)
                self.assertEqual(delivered[0]["kind"], "reminder")
                self.assertIn("1 atom", delivered[0]["title"])
                # same day → no second digest
                self.assertEqual(kala_scheduler._fire_due_reviews(noon, state), 0)
                # before the review hour → nothing fires
                dawn = datetime(2026, 7, 10, 5, 0)
                self.assertEqual(kala_scheduler._fire_due_reviews(dawn, {}), 0)


class LearningWorkspaceTests(unittest.TestCase):
    def test_workspace_creation_and_record_append(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            temp_root = Path(tmpdir)
            with patch.object(learning_workspace, "LEARNING_DIR", temp_root):
                workspace = learning_workspace.ensure_workspace(
                    user_id="default",
                    topic="transformer attention",
                    mission="Teach me transformer attention for interviews.",
                    session_id="sess-1",
                )
                self.assertTrue((temp_root / "default" / workspace["workspace_id"] / "MISSION.md").exists())

                record = learning_workspace.append_learning_record(
                    user_id="default",
                    workspace_id=workspace["workspace_id"],
                    title="Checkpoint",
                    summary="Covered scaled dot-product attention.",
                    body="We walked through Q, K, and V.",
                    session_id="sess-1",
                    tags=["teach"],
                )
                self.assertEqual(record["title"], "Checkpoint")

                packet = learning_workspace.build_workspace_packet(
                    user_id="default",
                    workspace_id=workspace["workspace_id"],
                )
                self.assertIn("transformer attention", packet.lower())

                refreshed = learning_workspace.load_workspace(
                    user_id="default",
                    workspace_id=workspace["workspace_id"],
                )
                self.assertEqual(refreshed["record_count"], 1)

    def test_packet_carries_frontier_atom_when_syllabus_exists(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            temp_root = Path(tmpdir)
            with patch.object(learning_workspace, "LEARNING_DIR", temp_root), \
                 patch.object(guru_engine, "LEARNING_DIR", temp_root):
                workspace = learning_workspace.ensure_workspace(
                    user_id="default",
                    topic="transformer attention",
                    mission="Teach me transformer attention.",
                    session_id="sess-2",
                )
                syllabus_path = temp_root / "default" / workspace["workspace_id"] / "syllabus.json"
                syllabus_path.write_text(json.dumps(_SYLLABUS), encoding="utf-8")
                packet = learning_workspace.build_workspace_packet(
                    user_id="default",
                    workspace_id=workspace["workspace_id"],
                )
                self.assertIn("SYLLABUS PROGRESS: 0/2 atoms mastered", packet)
                self.assertIn("CURRENT TEACHING ATOM: Dot product [dot-product]", packet)
                self.assertIn("Check question to ask: What does a large dot product mean?", packet)
                # mastering the first atom advances the frontier in the packet
                (temp_root / "default" / workspace["workspace_id"] / "learner_state.json").write_text(
                    json.dumps({"dot-product": {"status": "mastered"}}), encoding="utf-8"
                )
                packet2 = learning_workspace.build_workspace_packet(
                    user_id="default",
                    workspace_id=workspace["workspace_id"],
                )
                self.assertIn("CURRENT TEACHING ATOM: Softmax [softmax]", packet2)


if __name__ == "__main__":
    unittest.main()
