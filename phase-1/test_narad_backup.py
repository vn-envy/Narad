"""Encrypted backups: round trip, live WAL databases, tamper detection, retention, excludes."""

from __future__ import annotations

import base64
import importlib.util
import json
import os
import sqlite3
import stat
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "narad_backup.py"
_spec = importlib.util.spec_from_file_location("narad_backup_under_test", _SCRIPT)
backup = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(backup)  # type: ignore[union-attr]


class BackupTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.home = self.root / "narad-home"
        self.dest = self.root / "NaradBackups"
        self.key_file = self.root / "keys" / "backup.key"
        self.env = patch.dict(os.environ, {
            "NARAD_HOME": str(self.home),
            "NARAD_BACKUP_DIR": str(self.dest),
            "NARAD_BACKUP_KEY_FILE": str(self.key_file),
        })
        self.env.start()
        self.live_db: sqlite3.Connection | None = None
        self._fake_home()

    def tearDown(self) -> None:
        if self.live_db is not None:
            self.live_db.close()
        self.env.stop()
        self.tempdir.cleanup()

    def _fake_home(self) -> None:
        for folder in (
            "config", "profiles/asha/metrics", "profiles/asha/privacy", "__pycache__",
            "models", "smriti/raw-cache", "logs", "browser/Default/Code Cache", "integrations/artemis/.venv",
        ):
            (self.home / folder).mkdir(parents=True, exist_ok=True)
        # A live WAL database whose newest rows exist only in the -wal file: a raw
        # copy of workflows.db would miss them; the backup API must not.
        self.live_db = sqlite3.connect(self.home / "workflows.db")
        self.live_db.execute("PRAGMA journal_mode=WAL")
        self.live_db.execute("PRAGMA wal_autocheckpoint=0")
        self.live_db.execute("CREATE TABLE runs (id INTEGER PRIMARY KEY, stage TEXT)")
        self.live_db.executemany("INSERT INTO runs (stage) VALUES (?)", [(f"s{i}",) for i in range(500)])
        self.live_db.commit()
        self.assertGreater((self.home / "workflows.db-wal").stat().st_size, 0)
        (self.home / "config" / "family_profiles.json").write_text(json.dumps({"profiles": {"asha": {}}}))
        (self.home / "profiles" / "asha" / "metrics" / "turns.jsonl").write_text('{"t": 1}\n{"t": 2}\n')
        (self.home / "profiles" / "asha" / "privacy" / "egress.jsonl").write_text('{"tier": "trusted"}\n')
        (self.home / "profiles" / "asha" / "notes.bin").write_bytes(os.urandom(70_000))
        (self.home / "__pycache__" / "x.cpython-311.pyc").write_bytes(b"\0")
        (self.home / "models" / "gemma.gguf").write_bytes(b"w" * 1000)
        (self.home / "smriti" / "raw-cache" / "blob").write_bytes(b"c")
        (self.home / "logs" / "server.txt").write_text("log")
        (self.home / "config" / "ollama-runtime.log").write_text("log")
        (self.home / "browser" / "Default" / "Code Cache" / "js").write_bytes(b"c")
        (self.home / "browser" / "Default" / "Cookies.json").write_text("{}")
        (self.home / "integrations" / "artemis" / ".venv" / "lib").write_text("x")
        os.symlink(self.root, self.home / "outside-link")

    def _backup(self, **kwargs) -> Path:
        result = backup.create_backup(**kwargs)
        return self.dest / result["archive"]

    def test_round_trip_restores_wal_rows_json_and_skips_excluded(self) -> None:
        with patch("sys.stdout"):
            archive = self._backup()
        self.assertEqual(stat.S_IMODE(archive.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(self.key_file.stat().st_mode), 0o600)
        self.assertEqual(len(base64.b64decode(self.key_file.read_text().strip())), 32)
        self.assertNotIn(b"family_profiles", archive.read_bytes())  # encrypted, not just gzip'd

        out = self.root / "restored"
        restored = backup.restore(archive, out)
        for excluded in (
            "__pycache__", "models", "smriti/raw-cache", "logs", "config/ollama-runtime.log",
            "browser/Default/Code Cache", "integrations/artemis/.venv", "workflows.db-wal",
            "workflows.db-shm", "outside-link",
        ):
            self.assertFalse((out / excluded).exists(), excluded)
        rows = sqlite3.connect(out / "workflows.db").execute("SELECT count(*) FROM runs").fetchone()[0]
        self.assertEqual(rows, 500)
        self.assertEqual(json.loads((out / "config" / "family_profiles.json").read_text()), {"profiles": {"asha": {}}})
        self.assertEqual(
            (out / "profiles" / "asha" / "notes.bin").read_bytes(),
            (self.home / "profiles" / "asha" / "notes.bin").read_bytes(),
        )
        self.assertTrue((out / "browser" / "Default" / "Cookies.json").exists())
        manifest = restored["manifest"]
        self.assertEqual(restored["files"], manifest["files"])
        self.assertEqual(restored["bytes"], manifest["bytes"])
        self.assertEqual([e[0] for e in manifest["entries"] if e[2] == "sqlite"], ["workflows.db"])
        self.assertEqual(backup.verify_tree(out, manifest)["failures"], [])
        self.assertEqual(json.loads((self.home / "ops" / "backup.jsonl").read_text().splitlines()[-1])["ok"], True)

    def test_small_chunks_stream_across_many_chunks(self) -> None:
        with patch("sys.stdout"):
            archive = self._backup(chunk_size=4096)
        self.assertGreater(archive.stat().st_size, 10 * 4096)  # many chunks
        restored = backup.restore(archive, self.root / "out")
        self.assertEqual(backup.verify_tree(self.root / "out", restored["manifest"])["failures"], [])

    def test_tampered_truncated_and_wrong_key_archives_fail(self) -> None:
        with patch("sys.stdout"):
            archive = self._backup(chunk_size=4096)
        original = archive.read_bytes()

        tampered = bytearray(original)
        tampered[len(tampered) // 2] ^= 0x01
        archive.write_bytes(bytes(tampered))
        with self.assertRaises(backup.IntegrityError):
            backup.restore(archive, self.root / "tampered")

        archive.write_bytes(original[:-100])
        with self.assertRaises(backup.IntegrityError):
            backup.restore(archive, self.root / "truncated")

        archive.write_bytes(original + b"extra")
        with self.assertRaises(backup.IntegrityError):
            backup.restore(archive, self.root / "appended")

        archive.write_bytes(original)
        with self.assertRaisesRegex(backup.BackupError, "different backup key"):
            backup.restore(archive, self.root / "wrong-key", key=os.urandom(32))

    def test_drill_passes_on_good_backup_and_fails_on_tampered_one(self) -> None:
        with patch("sys.stdout"):
            archive = self._backup()
        passed = backup.drill()
        self.assertTrue(passed["passed"], passed["failures"])
        self.assertEqual(passed["sqlite_ok"], passed["sqlite_total"])
        self.assertGreaterEqual(passed["jsonl_total"], 2)

        data = bytearray(archive.read_bytes())
        data[-40] ^= 0xFF
        archive.write_bytes(bytes(data))
        failed = backup.drill()
        self.assertFalse(failed["passed"])
        self.assertIn("IntegrityError", failed["failures"][0])

        lines = (self.home / "ops" / "backup_drill.jsonl").read_text().splitlines()
        self.assertEqual([json.loads(line)["passed"] for line in lines], [True, False])

    def test_verify_tree_flags_corrupt_json_and_sqlite(self) -> None:
        with patch("sys.stdout"):
            archive = self._backup()
        out = self.root / "out"
        manifest = backup.restore(archive, out)["manifest"]
        (out / "config" / "family_profiles.json").write_text("{not json")
        (out / "workflows.db").write_bytes(b"SQLite format 3\x00" + b"\x00" * 4000)
        failures = backup.verify_tree(out, manifest)["failures"]
        self.assertTrue(any(f.startswith("config/family_profiles.json") for f in failures), failures)
        self.assertTrue(any(f.startswith("workflows.db") for f in failures), failures)

    def test_restore_refuses_a_non_empty_target(self) -> None:
        with patch("sys.stdout"):
            archive = self._backup()
        with self.assertRaisesRegex(backup.BackupError, "not empty"):
            backup.restore(archive, self.home)

    def test_key_must_live_outside_narad_home(self) -> None:
        with patch.dict(os.environ, {"NARAD_BACKUP_KEY_FILE": str(self.home / "backup.key")}):
            with self.assertRaisesRegex(backup.BackupError, "outside"):
                backup.load_key(create=True)
        with self.assertRaisesRegex(backup.BackupError, "No backup key"):
            backup.load_key()

    def test_retention_keeps_14_daily_and_8_weekly(self) -> None:
        start = datetime(2026, 6, 1, 22, 0, tzinfo=timezone.utc)
        stamps = [start + timedelta(days=i) for i in range(70)]
        stamps.append(stamps[-1] + timedelta(hours=1))  # a second backup on the newest day
        keep = backup.select_keep(stamps)
        newest = stamps[-1]
        daily = {s for s in keep if (newest - s).days < 14}
        self.assertEqual(len({s.date() for s in daily}), 14)
        self.assertNotIn(stamps[-2], keep)  # superseded on the same day
        weeks = {s.isocalendar()[:2] for s in keep}
        self.assertEqual(len(weeks), 8)
        self.assertLessEqual(len(keep), 14 + 8)
        self.assertLessEqual((newest - min(keep)).days, 8 * 7)

        self.dest.mkdir(parents=True)
        for stamp in stamps:
            (self.dest / f"narad-{stamp.strftime('%Y%m%dT%H%M%SZ')}.nbk").write_bytes(b"x")
        (self.dest / "notes.txt").write_text("not a backup")
        removed = backup.prune(self.dest)
        self.assertEqual(len(removed), len(stamps) - len(keep))
        self.assertTrue((self.dest / "notes.txt").exists())
        self.assertEqual(len(backup.archives(self.dest)), len(keep))

    def test_exclude_rules(self) -> None:
        excluded = [
            "__pycache__/a.pyc", "memory/__pycache__/b.py", "models/gemma/weights.bin", "x/model.gguf",
            "logs/today.txt", "config/andon.log", "config/andon.log.1", "workflows.db-wal", "health.db-shm",
            "finance.db-journal", "browser/Default/GPUCache/data_0", "browser/Default/Code Cache/js/1",
            "smriti/raw-cache/x", "integrations/artemis/.venv/bin/python", ".DS_Store", "a.tmp",
        ]
        kept = [
            "workflows.db", "config/family_profiles.json", "profiles/asha/metrics/turns.jsonl",
            "threads/asha/s1.jsonl", "memory/lance/data.lance", "browser/Default/Cookies",
            "attachments/content/ab/cd/file.pdf", "ops/uptime.jsonl",
        ]
        for path in excluded:
            self.assertTrue(backup.is_excluded(Path(path)), path)
        for path in kept:
            self.assertFalse(backup.is_excluded(Path(path)), path)

    def test_cli_backup_list_and_drill(self) -> None:
        with patch("sys.stdout") as out:
            self.assertEqual(backup.main(["backup"]), 0)
            self.assertEqual(backup.main(["list"]), 0)
            self.assertEqual(backup.main(["drill"]), 0)
        printed = "".join(str(call.args[0]) for call in out.write.call_args_list if call.args)
        self.assertIn("Save a copy in your password manager", printed)
        self.assertNotIn(self.key_file.read_text().strip(), printed)  # the key is never printed
        self.assertIn("Restore drill PASS", printed)


if __name__ == "__main__":
    unittest.main()
