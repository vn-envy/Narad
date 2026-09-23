"""Guided-mode engine tests (G7) — the /teach state machine, keyless paths."""
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
import guided_mode

import guru_engine
import learning_workspace

_ATOMS = [
    {
        "id": "a1", "name": "Atom one", "prerequisites": [],
        "eli5": "Like sorting socks into pairs.",
        "plain": "The first idea, in plain words, with enough length to grade.",
        "precise": "Precise wording.", "formal": "Formal statement.",
        "misconception": "People think it is magic; it is bookkeeping.",
        "check": {"q": "What is atom one?", "good_answer": "plain words first idea bookkeeping sorting"},
    },
    {
        "id": "a2", "name": "Atom two", "prerequisites": ["a1"],
        "eli5": "Like stacking lunchboxes.",
        "plain": "The second idea builds directly on the first one.",
        "precise": "Precise wording two.", "formal": "Formal statement two.",
        "misconception": "People think it replaces atom one; it extends it.",
        "check": {"q": "What is atom two?", "good_answer": "second idea builds directly first extends"},
    },
]


def _seed(temp_root: Path, user_id: str, topic: str) -> str:
    workspace = learning_workspace.ensure_workspace(user_id=user_id, topic=topic)
    workspace_id = workspace["workspace_id"]
    path = temp_root / user_id / workspace_id / "syllabus.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "workspace_id": workspace_id, "topic": topic, "generator": "test", "atoms": _ATOMS,
    }), encoding="utf-8")
    return workspace_id


class GuidedModeLoopTests(unittest.TestCase):
    def _patched(self, temp_root: Path):
        return (
            patch.object(learning_workspace, "LEARNING_DIR", temp_root),
            patch.object(guru_engine, "LEARNING_DIR", temp_root),
            patch.object(guided_mode, "llm_json", side_effect=RuntimeError("offline test")),
            patch.object(guru_engine, "llm_json", side_effect=RuntimeError("offline test")),
        )

    def test_full_loop_answer_skip_exit_resume(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            temp_root = Path(tmpdir)
            p1, p2, p3, p4 = self._patched(temp_root)
            with p1, p2, p3, p4:
                ws = _seed(temp_root, "u", "loop topic")

                started = guided_mode.start_session(user_id="u", mode="teach", topic="loop topic")
                self.assertFalse(started["resumed"])
                step = started["step"]
                self.assertEqual(step["kind"], "atom")
                self.assertEqual(step["atom_id"], "a1")
                self.assertEqual(step["avatar"], "Krishna")
                self.assertTrue(step["narration"])          # template fallback fills it
                self.assertIn("<style>", step["artifact_html"])
                self.assertNotIn("<script", step["artifact_html"].lower())
                # free-quiz fallback uses the atom's own check question
                self.assertEqual(step["quiz"]["type"], "free")
                self.assertEqual(step["quiz"]["question"], "What is atom one?")
                self.assertNotIn("correct_index", step["quiz"])  # answers never leak

                # Wrong answer: stays on the atom
                wrong = guided_mode.submit_answer(user_id="u", workspace_id=ws, answer="nope")
                self.assertFalse(wrong["grade"]["correct"])
                self.assertFalse(wrong["advanced"])

                # Right answer (heuristic grader): advances to a2
                right = guided_mode.submit_answer(
                    user_id="u", workspace_id=ws,
                    answer="the plain words first idea is bookkeeping like sorting things",
                )
                self.assertTrue(right["grade"]["correct"])
                self.assertTrue(right["advanced"])
                self.assertEqual(right["step"]["atom_id"], "a2")
                self.assertEqual(right["step"]["progress"]["mastered"], 1)

                # Skip a2 → syllabus exhausted → complete
                skipped = guided_mode.skip_atom(user_id="u", workspace_id=ws)
                self.assertEqual(skipped["step"]["kind"], "complete")

                # Resume retries the skipped atom
                resumed = guided_mode.start_session(user_id="u", mode="teach", topic="loop topic")
                self.assertTrue(resumed["resumed"])
                self.assertEqual(resumed["step"]["atom_id"], "a2")

                # Exit reports progress and persists state on disk
                exited = guided_mode.exit_session(user_id="u", workspace_id=ws)
                self.assertEqual(exited["session"]["status"], "exited")
                self.assertIn("1/2", exited["message"])
                session_file = temp_root / "u" / ws / "guided_session.json"
                self.assertTrue(session_file.exists())

    def test_mcq_grading_is_local_and_records_mastery(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            temp_root = Path(tmpdir)
            p1, p2, p3, p4 = self._patched(temp_root)
            with p1, p2, p3, p4:
                ws = _seed(temp_root, "u", "mcq topic")
                guided_mode.start_session(user_id="u", mode="teach", topic="mcq topic")

                # Force the cached presentation to an MCQ quiz
                session = guided_mode._load_session("u", ws)
                session["presented"]["a1"]["quiz"] = {
                    "type": "mcq", "question": "Pick one",
                    "options": ["right", "wrong", "worse"], "correct_index": 0,
                    "why": "because it is right",
                }
                guided_mode._save_session(session)

                miss = guided_mode.submit_answer(user_id="u", workspace_id=ws, choice_index=2)
                self.assertFalse(miss["grade"]["correct"])
                self.assertEqual(miss["grade"]["grader"], "local")
                self.assertIn("right", miss["grade"]["feedback"])

                hit = guided_mode.submit_answer(user_id="u", workspace_id=ws, choice_index=0)
                self.assertTrue(hit["grade"]["correct"])
                self.assertTrue(hit["advanced"])

                state = guru_engine.load_learner_state(user_id="u", workspace_id=ws)
                self.assertEqual(state["a1"]["status"], "mastered")
                self.assertEqual(state["a1"]["attempts"], 2)

                # MCQ without choice_index is a usage error
                with self.assertRaises(ValueError):
                    session = guided_mode._load_session("u", ws)
                    session["presented"]["a2"] = dict(session["presented"]["a1"])
                    session["current_atom_id"] = "a2"
                    guided_mode._save_session(session)
                    guided_mode.submit_answer(user_id="u", workspace_id=ws, answer="typed instead")

    def test_guardrails(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            temp_root = Path(tmpdir)
            p1, p2, p3, p4 = self._patched(temp_root)
            with p1, p2, p3, p4:
                with self.assertRaises(ValueError):
                    guided_mode.start_session(user_id="u", mode="no-such-mode", topic="x")
                with self.assertRaises(ValueError):
                    guided_mode.start_session(user_id="u", mode="teach", topic="   ")
                with self.assertRaises(ValueError):
                    guided_mode.submit_answer(user_id="u", workspace_id="missing", answer="x")
                with self.assertRaises(ValueError):
                    guided_mode.exit_session(user_id="u", workspace_id="missing")
                self.assertIsNone(guided_mode.get_session(user_id="u", workspace_id="missing"))

    def test_presentation_is_cached_per_atom(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            temp_root = Path(tmpdir)
            p1, p2, p3, p4 = self._patched(temp_root)
            with p1, p2, p3, p4:
                _seed(temp_root, "u", "cache topic")
                first = guided_mode.start_session(user_id="u", mode="teach", topic="cache topic")
                with patch.object(guided_mode, "_present_atom") as present:
                    again = guided_mode.start_session(user_id="u", mode="teach", topic="cache topic")
                    present.assert_not_called()  # a1 came from the session cache
                self.assertEqual(first["step"]["narration"], again["step"]["narration"])


class SanitizerTests(unittest.TestCase):
    def test_strips_scripts_handlers_and_external_urls(self) -> None:
        dirty = (
            '<div onclick="steal()"><script>alert(1)</script>'
            '<img src="https://evil.example/x.png">'
            '<a href="javascript:bad()">x</a>'
            '<iframe src="https://evil.example"></iframe>'
            '<svg><circle r="4"/></svg></div>'
        )
        clean = guided_mode.sanitize_artifact_html(dirty)
        lowered = clean.lower()
        self.assertNotIn("<script", lowered)
        self.assertNotIn("<iframe", lowered)
        self.assertNotIn("onclick", lowered)
        self.assertNotIn("https://evil.example", clean)
        self.assertNotIn("javascript:", lowered)
        self.assertIn("<svg>", clean)  # legitimate SVG content survives

    def test_mode_registry_shape(self) -> None:
        self.assertIn("teach", guided_mode.MODES)
        for cfg in guided_mode.MODES.values():
            for key in ("avatar", "label", "persona", "narration_goal", "completion"):
                self.assertIn(key, cfg)


if __name__ == "__main__":
    unittest.main()
