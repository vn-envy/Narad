from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import sys

_r = next(p for p in Path(__file__).resolve().parents if (p / "narad_paths.py").exists())
sys.path[:0] = [str(_r)]  # narad root hop
import narad_paths  # noqa: F401

import learning_workspace


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


if __name__ == "__main__":
    unittest.main()
