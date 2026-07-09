import asyncio
import importlib
import json
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

_r = next(p for p in Path(__file__).resolve().parents if (p / "narad_paths.py").exists())
sys.path[:0] = [str(_r)]  # narad root hop
import narad_paths  # noqa: F401  — registers all phase dirs; must precede phase imports

# isort: split


_RELOADED_MODULES = [
    "narad_config",
    "conversation_memory",
    "dharma",
    "smriti_core",
    "swapna",
]


class CulturalCoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self._prev_home = os.environ.get("NARAD_HOME")
        os.environ["NARAD_HOME"] = self.tmp.name
        for name in _RELOADED_MODULES:
            sys.modules.pop(name, None)
        import conversation_memory  # noqa: WPS433
        import dharma  # noqa: WPS433
        import narad_config  # noqa: WPS433
        import smriti_core  # noqa: WPS433
        import swapna  # noqa: WPS433

        self.narad_config = importlib.reload(narad_config)
        self.conversation_memory = importlib.reload(conversation_memory)
        self.dharma = importlib.reload(dharma)
        self.smriti_core = importlib.reload(smriti_core)
        self.swapna = importlib.reload(swapna)

    def tearDown(self) -> None:
        self.tmp.cleanup()
        if self._prev_home is None:
            os.environ.pop("NARAD_HOME", None)
        else:
            os.environ["NARAD_HOME"] = self._prev_home
        # Un-pollute sys.modules: the reloads above bound these modules to the
        # (now deleted) temp home; leaving them cached poisons later suites in
        # the same pytest process (e.g. the phase-7 executor's lazy dharma
        # import would read policy from a nonexistent dir → fail-closed block).
        for name in _RELOADED_MODULES:
            sys.modules.pop(name, None)

    def test_capture_episode_writes_canonical_episode_and_commitments(self) -> None:
        result = self.smriti_core.capture_episode(
            session_id="sess-1",
            task="Create a goal and workflow constraint for the Narad redesign milestone.",
            avatar="Rama",
            result="Goal accepted. Must preserve the 4-agent build and concise prose format.",
            user_id="tester",
            trace_session_id="trace-1",
        )
        self.assertEqual(result["status"], "ok")

        episode_path = self.narad_config.EPISODE_DIR / "tester.jsonl"
        self.assertTrue(episode_path.exists())
        rows = [json.loads(line) for line in episode_path.read_text().splitlines()]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["avatar"], "Rama")

        commitments = self.smriti_core.load_commitments("tester")
        self.assertGreaterEqual(len(commitments), 1)
        kinds = {c["kind"] for c in commitments}
        self.assertTrue({"goal", "constraint"} & kinds)

    def test_recall_context_aggregates_memory_layers(self) -> None:
        self.smriti_core.capture_episode(
            session_id="sess-2",
            task="User prefers concise bullet summaries for code reviews.",
            avatar="Parashurama",
            result="Preference recorded.",
            user_id="tester",
            trace_session_id="trace-2",
        )

        async def _fake_project_context(*args, **kwargs):
            return "[PROJECT MEMORY]\nrecent architectural notes"

        fake_smriti_v2 = types.SimpleNamespace(get_project_context=_fake_project_context)
        fake_sutra = types.SimpleNamespace(
            get_active_sutras=lambda *args, **kwargs: [{"avatar": "Parashurama", "query": "q", "result": "Use tests first."}],
            format_for_injection=lambda sutras: "[SUTRAS]\n- Use tests first.",
        )
        fake_sankalpa = types.SimpleNamespace(
            get_active_sankalpas=lambda *args, **kwargs: [{"content": "User prefers concise responses."}],
            format_for_injection=lambda rows: "[SANKALPA]\n- User prefers concise responses.",
        )

        with patch.dict(
            sys.modules,
            {
                "smriti_v2": fake_smriti_v2,
                "sutra_engine": fake_sutra,
                "sankalpa": fake_sankalpa,
            },
        ):
            packet = asyncio.run(
                self.smriti_core.recall_context(
                    "concise bullet summaries for code reviews",
                    user_id="tester",
                    avatar="Parashurama",
                )
            )

        # Unified plane: one vector/lexical packet replaces the legacy
        # smriti.recall / recall_exact blocks (M2.2 3-plane merge).
        self.assertIn("[SMRITI VECTOR MEMORY", packet["context"])
        self.assertIn("recalled from", packet["context"])
        self.assertIn("code reviews", packet["context"])
        self.assertIn("[PROJECT MEMORY]", packet["context"])
        self.assertIn("[SUTRAS]", packet["context"])
        self.assertIn("[SANKALPA]", packet["context"])
        self.assertIn("[SANKALPA COMMITMENTS]", packet["context"])
        self.assertGreaterEqual(len(packet["provenance"]), 4)

    def test_dharma_rejects_vague_sutra(self) -> None:
        verdict = self.dharma.validate_sutra_candidate("Krishna", "todo", evidence_count=0)
        self.assertFalse(verdict.allowed)
        self.assertTrue(verdict.reasons)

    def test_swapna_dry_run_preserves_source_and_apply_writes_inbox(self) -> None:
        for idx in range(3):
            self.smriti_core.capture_episode(
                session_id=f"sess-{idx}",
                task=f"Plan milestone {idx} and note decision.",
                avatar="Rama",
                result=f"Decision {idx}: keep provenance and reduce duplicate memory paths.",
                user_id="tester",
                trace_session_id=f"trace-{idx}",
            )

        dry_run = self.swapna.dream(user_id="tester", apply=False, max_episodes=3)
        self.assertEqual(dry_run["status"], "ok")
        self.assertFalse(list(self.narad_config.SWAPNA_INBOX_DIR.glob("*.json")))

        applied = self.swapna.dream(user_id="tester", apply=True, max_episodes=3)
        self.assertEqual(applied["status"], "ok")
        inbox_files = list(self.narad_config.SWAPNA_INBOX_DIR.glob("*.json"))
        self.assertEqual(len(inbox_files), 1)
        record = json.loads(inbox_files[0].read_text())
        self.assertEqual(len(record["source_episode_ids"]), 3)

    def test_architecture_scorecard_reflects_core_import(self) -> None:
        scorecard = self.smriti_core.architecture_scorecard()
        self.assertGreaterEqual(scorecard["smriti_core_imports"], 1)
        self.assertLess(scorecard["legacy_direct_memory_imports"], 6)
        self.assertTrue(scorecard["swapna_enabled"])

    def test_conversation_memory_restores_recent_thread_and_working_state(self) -> None:
        for idx in range(10):
            self.conversation_memory.append_turn(
                user_id="tester",
                session_id="thread-1",
                role="user" if idx % 2 == 0 else "assistant",
                text=f"Prior turn {idx} about Narad continuity and active work items.",
            )
        self.conversation_memory.append_turn(
            user_id="tester",
            session_id="thread-1",
            role="user",
            text="Please keep the Narad dashboard compact and chat-first.",
        )
        self.conversation_memory.append_turn(
            user_id="tester",
            session_id="thread-1",
            role="assistant",
            text="I will preserve the chat-first shell and keep Smriti separate from Karma.",
        )
        self.conversation_memory.save_working_state(
            user_id="tester",
            session_id="thread-1",
            state={
                "avatars": ["Rama", "Parashurama"],
                "last_trace_session_id": "trace-xyz",
                "last_assistant_preview": "I will preserve the chat-first shell.",
                "thread_summary": "User: Prior turn 0 about Narad continuity and active work items.",
                "karya": {
                    "total": 4,
                    "done_count": 1,
                    "blocked_count": 0,
                    "active_titles": ["Tighten durable thread restore", "Review Karya fallback"],
                },
            },
        )

        rebuilt = self.conversation_memory.build_rehydration_query(
            user_id="tester",
            session_id="thread-1",
            current_query="What should we fix next?",
        )

        self.assertIn("THREAD MEMORY", rebuilt)
        self.assertIn("User: Please keep the Narad dashboard compact", rebuilt)
        self.assertIn("Narad: I will preserve the chat-first shell", rebuilt)
        self.assertIn("trace-xyz", rebuilt)
        self.assertIn("Karya state: 4 tasks", rebuilt)
        self.assertIn("Tighten durable thread restore", rebuilt)
        self.assertIn("Earlier summary:", rebuilt)
        self.assertIn("What should we fix next?", rebuilt)

    def test_recent_thread_context_can_bridge_short_sibling_sessions(self) -> None:
        self.conversation_memory.append_turn(
            user_id="tester",
            session_id="thread-a",
            role="user",
            text="Please review the dashboard plan and keep the same thread going.",
        )
        self.conversation_memory.append_turn(
            user_id="tester",
            session_id="thread-a",
            role="assistant",
            text="I mapped the first step and prepared the next actions.",
        )
        self.conversation_memory.append_turn(
            user_id="tester",
            session_id="thread-b",
            role="user",
            text="Go ahead with step 1 and continue from the earlier plan.",
        )
        self.conversation_memory.append_turn(
            user_id="tester",
            session_id="thread-b",
            role="assistant",
            text="I can continue that prior flow once I recover the earlier session context.",
        )

        rebuilt, source_ids = self.conversation_memory.build_recent_thread_context(
            user_id="tester",
            current_query="Carry it on from the previous conversation.",
            exclude_session_id="thread-c",
        )

        self.assertIn("RECENT RELATED THREADS", rebuilt)
        self.assertIn("thread-a", rebuilt)
        self.assertIn("thread-b", rebuilt)
        self.assertIn("Carry it on from the previous conversation.", rebuilt)
        self.assertEqual(source_ids, ["thread-a", "thread-b"])


if __name__ == "__main__":
    unittest.main()
