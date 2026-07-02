from __future__ import annotations

import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

_PHASE9 = Path(__file__).parent
_ROOT = _PHASE9.parent
_PHASE1 = _ROOT / "phase-1"
for _path in [str(_PHASE9), str(_ROOT), str(_PHASE1)]:
    if _path not in sys.path:
        sys.path.insert(0, _path)

import project_tasks as pt


LEGACY_DDL = """
CREATE TABLE tasks (
    task_id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    source_session_id TEXT,
    title TEXT NOT NULL,
    description TEXT,
    status TEXT NOT NULL,
    priority TEXT NOT NULL DEFAULT 'medium',
    owner TEXT,
    kind TEXT NOT NULL DEFAULT 'plan_step',
    blocked_by TEXT,
    artifact_refs TEXT,
    sort_order INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    completed_at TEXT
);
CREATE INDEX tasks_project_idx ON tasks (project_id, updated_at DESC);
CREATE INDEX tasks_session_idx ON tasks (source_session_id);
CREATE INDEX tasks_status_idx ON tasks (status);
"""


class ProjectTasksSchemaTest(unittest.TestCase):
    def test_legacy_db_migrates_before_workspace_index(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            legacy_db = Path(tmpdir) / "tasks.db"
            with sqlite3.connect(str(legacy_db)) as con:
                con.executescript(LEGACY_DDL)
                con.execute(
                    """
                    INSERT INTO tasks (
                        task_id, project_id, source_session_id, title, description, status,
                        priority, owner, kind, blocked_by, artifact_refs, sort_order,
                        created_at, updated_at, completed_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        "task_legacy",
                        "proj_legacy",
                        "session_legacy",
                        "Legacy task",
                        "",
                        "todo",
                        "medium",
                        None,
                        "follow_up",
                        "[]",
                        "[]",
                        0,
                        "2026-06-07T00:00:00+00:00",
                        "2026-06-07T00:00:00+00:00",
                        None,
                    ),
                )
                con.commit()

            original_db = pt.TASK_DB
            try:
                pt.TASK_DB = legacy_db
                tasks = pt.list_tasks("proj_legacy")
                self.assertEqual(len(tasks), 1)
                self.assertEqual(tasks[0].task_id, "task_legacy")

                with sqlite3.connect(str(legacy_db)) as con:
                    cols = {row[1] for row in con.execute("PRAGMA table_info(tasks)").fetchall()}
                    indexes = {row[1] for row in con.execute("PRAGMA index_list(tasks)").fetchall()}
                self.assertIn("workspace_root", cols)
                self.assertIn("tasks_workspace_idx", indexes)
            finally:
                pt.TASK_DB = original_db


if __name__ == "__main__":
    unittest.main()
