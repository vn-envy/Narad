"""Knowledge graph — deterministic build from wiki pages + episodes."""

from __future__ import annotations

import importlib
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

_r = next(p for p in Path(__file__).resolve().parents if (p / "narad_paths.py").exists())
sys.path[:0] = [str(_r)]  # narad root hop
import narad_paths  # noqa: F401  — registers all phase dirs; must precede phase imports

_RELOADED = ["narad_config", "smriti_indexer", "smriti_graph_api"]


class KnowledgeGraphTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self._old_home = os.environ.get("NARAD_HOME")
        os.environ["NARAD_HOME"] = self._tmp.name
        for name in _RELOADED:
            sys.modules.pop(name, None)
        self.graph_api = importlib.import_module("smriti_graph_api")
        self.config = importlib.import_module("narad_config")

    def tearDown(self) -> None:
        if self._old_home is None:
            os.environ.pop("NARAD_HOME", None)
        else:
            os.environ["NARAD_HOME"] = self._old_home
        for name in _RELOADED:
            sys.modules.pop(name, None)
        self._tmp.cleanup()

    def _write_fixture(self) -> None:
        wiki = self.config.WIKI_DIR / "tester" / "proj_graph"
        wiki.mkdir(parents=True, exist_ok=True)
        (wiki / "decisions.md").write_text(
            "# DECISIONS\n\n_tester project memory_\n\n"
            "## 2026-07-01 10:00 UTC\n**Avatar:** Rama  \n**Task:** Keep port 8010  \n"
            "**Summary:** Backend stays on 8010.\n\n"
            "## 2026-07-02 11:00 UTC\n**Avatar:** Vishwakarma  \n**Task:** Use FTS5 for wiki recall  \n"
            "**Summary:** No embeddings for wiki.\n"
        )
        episodes = self.config.EPISODE_DIR
        episodes.mkdir(parents=True, exist_ok=True)
        rows = [
            {
                "id": "ep-1", "ts": "2026-07-01T10:00:00+00:00", "session_id": "sess-alpha",
                "user_id": "tester", "avatar": "Rama", "task": "Keep port 8010",
                "result": "done", "project_id": "proj_graph",
            },
            {
                "id": "ep-2", "ts": "2026-07-02T11:00:00+00:00", "session_id": "sess-alpha",
                "user_id": "tester", "avatar": "Vishwakarma", "task": "Wiki recall via FTS5",
                "result": "done", "project_id": "proj_graph",
            },
        ]
        (episodes / "tester.jsonl").write_text(
            "\n".join(json.dumps(row) for row in rows) + "\n"
        )

    def test_graph_nodes_edges_and_traceability(self) -> None:
        self._write_fixture()
        graph = self.graph_api.build_knowledge_graph("tester", project_id="proj_graph")

        kinds = {node["kind"] for node in graph["nodes"]}
        self.assertEqual(kinds, {"project", "page", "section", "session", "avatar"})

        by_id = {node["id"]: node for node in graph["nodes"]}
        self.assertIn("project:proj_graph", by_id)
        self.assertIn("page:proj_graph:decisions", by_id)
        self.assertIn("avatar:Rama", by_id)
        self.assertIn("session:sess-alpha", by_id)

        # Every section node is traceable to its exact source text.
        sections = [node for node in graph["nodes"] if node["kind"] == "section"]
        self.assertEqual(len(sections), 2)
        for section in sections:
            self.assertTrue(Path(section["trace"]["path"]).exists())
            self.assertTrue(section["trace"]["content"])
            self.assertTrue(section["trace"]["anchor"])
        port_section = next(s for s in sections if "8010" in s["trace"]["content"])
        self.assertEqual(port_section["meta"]["avatar"], "Rama")

        edge_kinds = {edge["kind"] for edge in graph["edges"]}
        self.assertEqual(edge_kinds, {"has_page", "contains", "by", "in", "used"})
        self.assertIn(
            {"source": "session:sess-alpha", "target": "project:proj_graph", "kind": "in"},
            graph["edges"],
        )

    def test_section_cap_keeps_newest(self) -> None:
        self._write_fixture()
        graph = self.graph_api.build_knowledge_graph(
            "tester", project_id="proj_graph", max_sections=1
        )
        sections = [node for node in graph["nodes"] if node["kind"] == "section"]
        self.assertEqual(len(sections), 1)
        self.assertIn("FTS5", sections[0]["trace"]["content"])  # newest entry survives
        # Page weight still reports the full count for traceability.
        page = next(node for node in graph["nodes"] if node["kind"] == "page")
        self.assertEqual(page["meta"]["section_count"], 2)

    def test_empty_user_returns_empty_graph(self) -> None:
        graph = self.graph_api.build_knowledge_graph("nobody")
        self.assertEqual(graph["nodes"], [])
        self.assertEqual(graph["edges"], [])


if __name__ == "__main__":
    unittest.main()
