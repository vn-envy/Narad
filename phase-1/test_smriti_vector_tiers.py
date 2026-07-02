from __future__ import annotations

import asyncio
import importlib
import os
import sys
import tempfile
import unittest
from pathlib import Path

_r = next(p for p in Path(__file__).resolve().parents if (p / "narad_paths.py").exists())
sys.path[:0] = [str(_r)]  # narad root hop
import narad_paths  # noqa: F401  — registers all phase dirs; must precede phase imports

# isort: split


class SmritiVectorTierTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["NARAD_HOME"] = self.tmp.name
        for name in [
            "narad_config",
            "smriti_core",
            "smriti_indexer",
            "smriti_recall_ranker",
            "smriti_vector_store",
            "smriti_v2",
            "turbovec_policy",
        ]:
            sys.modules.pop(name, None)
        import smriti_v2  # noqa: WPS433

        import narad_config  # noqa: WPS433
        import smriti_core  # noqa: WPS433
        import smriti_recall_ranker  # noqa: WPS433

        self.narad_config = importlib.reload(narad_config)
        self.smriti_core = importlib.reload(smriti_core)
        self.smriti_recall_ranker = importlib.reload(smriti_recall_ranker)
        self.smriti_v2 = importlib.reload(smriti_v2)

    def tearDown(self) -> None:
        self.tmp.cleanup()
        os.environ.pop("NARAD_HOME", None)

    def test_semantic_memory_uses_vector_tier_with_exact_reread(self) -> None:
        self.smriti_core.capture_episode(
            session_id="sess-vector",
            task="Investigate browser automation credentials and note the blocker.",
            avatar="Matsya",
            result="Blocked because the browser automation credentials are missing for the staging tenant.",
            user_id="tester",
            project_id="proj_vector",
            trace_session_id="trace-vector",
        )

        packet = self.smriti_recall_ranker.build_semantic_memory_context(
            query="What blocked the browser automation work?",
            user_id="tester",
            project_id="proj_vector",
            token_budget=420,
            model="deepseek/deepseek-v4-flash",
        )

        self.assertIn("browser automation credentials", packet["text"])
        self.assertTrue(packet["provenance"])
        self.assertTrue(packet["provenance"][0]["exact_reread"])
        self.assertEqual(packet["diagnostics"]["policy"]["local_first"], True)

    def test_project_context_uses_tiered_project_wiki_index(self) -> None:
        self.smriti_v2.put_wiki_page(
            "tester",
            "decision",
            "# DECISIONS\n\n## API routing\nKeep the Narad backend on port 8010 and separate from the other local app.\n",
            project_id="proj_docs",
        )

        packet = self.smriti_recall_ranker.build_project_memory_context(
            query="Which port should Narad keep using?",
            user_id="tester",
            project_id="proj_docs",
            token_budget=420,
            model="deepseek/deepseek-v4-flash",
        )

        self.assertIn("8010", packet["text"])
        self.assertTrue(packet["provenance"])
        self.assertEqual(packet["provenance"][0]["namespace"], "project_wiki")

    def test_recall_context_reports_memory_tiers(self) -> None:
        self.smriti_core.capture_episode(
            session_id="sess-tiers",
            task="Remember that the user prefers exact provenance for project memory.",
            avatar="Rama",
            result="Preference stored with provenance.",
            user_id="tester",
            project_id="proj_tiers",
            trace_session_id="trace-tiers",
        )

        packet = asyncio.run(
            self.smriti_core.recall_context(
                "What does the user prefer for project memory?",
                user_id="tester",
                avatar="Rama",
                project_id="proj_tiers",
                token_budget=360,
                model="deepseek/deepseek-v4-flash",
            )
        )

        self.assertIn("memory_tiers", packet)
        self.assertIn("episodic_summary", packet["memory_tiers"]["policy"]["rules"])


if __name__ == "__main__":
    unittest.main()
