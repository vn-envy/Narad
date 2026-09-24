"""Stage A carryovers: generated runs and learning/audit logs are per profile.

A family member reaches only their own runs and log records; the owner sees
their own by default and every profile's with ?scope=all. Records written
before they named a profile are the owner's. All offline.
"""

from __future__ import annotations

import importlib
import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

_r = next(p for p in Path(__file__).resolve().parents if (p / "narad_paths.py").exists())
sys.path[:0] = [str(_r)]
import narad_paths  # noqa: F401

# isort: split
import andon
import audit_trail
import host_access
import imagen_skill
import karma_log
import sankalpa
import server
import sutra_engine
import tapas
import veo_skill
import webpage_skill
from fastapi.testclient import TestClient

import cost_ledger
import family_profiles
import guru_engine
import learning_workspace
import onboarding
import profile_context
import tool_result
from profile_context import profile_scope


def _media_static_app():
    return next(route.app for route in server.app.routes if getattr(route, "name", "") == "media")


def _now(days_ago: int = 0) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat()


class _FamilyServer(unittest.TestCase):
    """Strict auth, an owner (default), Alice and Bob, and every log in a temp dir."""

    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.media_root = self.root / "artifacts"
        self.media_root.mkdir()
        self.logs = self.root / "logs"
        # Other suites reload Smriti against their own homes: patch whichever is live.
        self.smriti = importlib.import_module("smriti_core")
        ledger = self.logs / "karma_mutations.jsonl"
        self.patches = [
            patch.object(server, "_AUTH_MODE", "strict"),
            patch.object(server, "_login_failures", {}),
            patch.object(family_profiles, "FAMILY_PROFILES_PATH", self.root / "family_profiles.json"),
            patch.object(family_profiles, "PROFILE_SESSION_SECRET_PATH", self.root / "profile_secret"),
            patch.object(profile_context, "PROFILES_DIR", self.root / "profiles"),
            patch.object(onboarding, "ONBOARDING_PATH", self.root / "onboarding.json"),
            patch.object(_media_static_app(), "all_directories", [self.media_root]),
            # The owner's legacy (pre-family) global files.
            patch.object(sutra_engine, "_SUTRAS_PATH", self.logs / "sutras.jsonl"),
            patch.object(sutra_engine, "_OVERRIDES_PATH", self.logs / "sutra_overrides.jsonl"),
            patch.object(sutra_engine, "_DEMOTIONS_PATH", self.logs / "sutra_demotions.jsonl"),
            patch.object(tapas, "_SUTRAS_PATH", self.logs / "sutras.jsonl"),
            patch.object(tapas, "_WEAK_PATH", self.logs / "weak_sessions.jsonl"),
            patch.object(tapas, "_DEMOTIONS_PATH", self.logs / "sutra_demotions.jsonl"),
            patch.object(karma_log, "_KARMA_PATH", self.logs / "karma.jsonl"),
            patch.object(karma_log, "_KARMA_MUTATIONS_PATH", ledger),
            patch.object(self.smriti, "KARMA_MUTATIONS_PATH", ledger),
            patch.object(self.smriti, "SANKALPA_COMMITMENTS_PATH", self.logs / "commitments.jsonl"),
            patch.object(self.smriti, "EPISODE_DIR", self.logs / "episodes"),
            patch.object(self.smriti, "SWAPNA_INBOX_DIR", self.logs / "swapna"),
            patch.object(andon, "ANDON_LOG_PATH", self.logs / "andon_log.jsonl"),
            patch.object(cost_ledger, "COST_LEDGER_PATH", self.logs / "cost_ledger.jsonl"),
            patch.object(audit_trail, "_AUDIT_PATH", self.logs / "audit.jsonl"),
            patch.object(sankalpa, "_SANKALPAS_PATH", self.logs / "sankalpas.jsonl"),
            patch.object(sankalpa, "_OVERRIDES_PATH", self.logs / "sankalpa_overrides.jsonl"),
        ]
        for active_patch in self.patches:
            active_patch.start()
        self.client = TestClient(server.app)
        family_profiles.update_profile("default", pin="8642")
        family_profiles.create_profile("Alice", "2468")
        family_profiles.create_profile("Bob", "1357")
        self.pins = {"default": "8642", "alice": "2468", "bob": "1357"}
        self.tokens: dict[str, str] = {}

    def tearDown(self) -> None:
        for active_patch in reversed(self.patches):
            active_patch.stop()
        self.tempdir.cleanup()

    def _token(self, user_id: str) -> str:
        if user_id not in self.tokens:
            response = self.client.post("/profiles/login", json={"user_id": user_id, "pin": self.pins[user_id]})
            self.assertEqual(response.status_code, 200, response.text)
            self.tokens[user_id] = response.json()["token"]
        return self.tokens[user_id]

    def _headers(self, user_id: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._token(user_id)}"}

    def _get(self, user_id: str, path: str) -> object:
        response = self.client.get(path, headers=self._headers(user_id))
        self.assertEqual(response.status_code, 200, f"{user_id} {path}: {response.text}")
        return response.json()

    def _append(self, path: Path, row: dict) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row) + "\n")


class ProfileRunTests(_FamilyServer):
    """Executor, Imagen, Veo and web captures land in the caller's runs folder."""

    def test_generated_output_is_filed_under_the_active_profile(self) -> None:
        image = SimpleNamespace(save=lambda path: Path(path).write_bytes(b"png"))
        client = SimpleNamespace(models=SimpleNamespace(
            generate_images=lambda **_: SimpleNamespace(generated_images=[SimpleNamespace(image=image)]),
            generate_videos=lambda **_: SimpleNamespace(
                done=True,
                response=SimpleNamespace(generated_videos=[
                    SimpleNamespace(video=SimpleNamespace(video_bytes=b"mp4")),
                ]),
            ),
        ))
        executed = {
            "status": "ok", "stdout": "", "stderr": "", "duration_s": 0.1, "run_id": "ab12cd34",
            "media_path": "runs/alice/ab12cd34",
            "output_files": [str(self.media_root / "runs/alice/ab12cd34/index.html")],
        }
        with patch.object(tool_result, "ARTIFACTS_DIR", self.media_root), \
             patch.object(imagen_skill, "ARTIFACTS_DIR", self.media_root), \
             patch.object(veo_skill, "ARTIFACTS_DIR", self.media_root), \
             patch.dict("os.environ", {"GEMINI_API_KEY": "test-key"}), \
             patch("google.genai.Client", return_value=client), \
             patch.object(webpage_skill, "execute_code", return_value=executed), \
             profile_scope("alice"):
            capture = tool_result.ensure_artifact_dir("exa_search")
            picture = imagen_skill.generate_image("a brass lamp")
            clip = veo_skill.generate_video_clip("a brass lamp")
            page = webpage_skill.create_webpage("print('page')")

        alice_runs = self.media_root / "runs" / "alice"
        self.assertEqual(capture.parent, alice_runs)
        for result, name in ((picture, "image.png"), (clip, "clip.mp4")):
            self.assertEqual(result["status"], "ok", result)
            path = Path(result["path"])
            self.assertEqual(path.parent.parent, alice_runs)
            self.assertTrue(result["url"].endswith(f"/media/runs/alice/{path.parent.name}/{name}"), result["url"])
        self.assertTrue(page["url"].endswith("/media/runs/alice/ab12cd34/index.html"), page["url"])

    def test_media_serves_a_run_only_to_its_profile(self) -> None:
        for relative in (
            "runs/alice/r1/page.html",
            "runs/bob/r2/page.html",
            "runs/default/r3/page.html",
            "legacy-run/video.mp4",  # a top-level run folder from before per-profile runs
        ):
            target = self.media_root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(relative.encode())
        alice = TestClient(server.app, cookies={server._MEDIA_COOKIE: self._token("alice")})
        owner = TestClient(server.app, cookies={server._MEDIA_COOKIE: self._token("default")})

        self.assertEqual(alice.get("/media/runs/alice/r1/page.html").content, b"runs/alice/r1/page.html")
        for foreign in (
            "/media/runs/bob/r2/page.html",
            "/media/RUNS/Bob/r2/page.html",
            "/media//runs/bob/r2/page.html",
            "/media/runs//bob/r2/page.html",
            "/media/runs/default/r3/page.html",
            "/media/legacy-run/video.mp4",
        ):
            self.assertEqual(alice.get(foreign).status_code, 404, foreign)
        # The owner keeps existing links and gets no pass into a member's runs.
        self.assertEqual(owner.get("/media/legacy-run/video.mp4").status_code, 200)
        self.assertEqual(owner.get("/media/runs/default/r3/page.html").status_code, 200)
        self.assertEqual(owner.get("/media/runs/alice/r1/page.html").status_code, 404)

    def test_file_tools_keep_each_profile_to_its_own_runs(self) -> None:
        own = self.media_root / "runs" / "alice" / "r1" / "report.docx"
        foreign = self.media_root / "runs" / "bob" / "r2" / "report.docx"
        legacy = self.media_root / "legacy-run" / "report.docx"
        with patch.object(host_access, "ARTIFACTS_DIR", self.media_root):
            with profile_scope("alice"):
                self.assertIsNone(host_access.path_access_error(own))
                self.assertEqual(host_access.path_access_error(foreign), "That file belongs to another family profile")
                self.assertIsNotNone(host_access.path_access_error(legacy))
            with profile_scope("default"):
                self.assertIsNone(host_access.path_access_error(legacy))
                self.assertEqual(host_access.path_access_error(own), "That file belongs to another family profile")


class ProfileLogTests(_FamilyServer):
    """Sutras, andon, karma, sankalpa, costs, audit, search and provenance."""

    def _promote_sutra(self, profile_id: str, rule: str) -> None:
        """Tapas' real write path, with the judges stubbed out."""
        with profile_scope(profile_id), \
             patch.object(tapas, "score_session", lambda *_a, **_k: (0.9, "good", True, True)), \
             patch.object(tapas, "_is_duplicate", lambda *_a: False), \
             patch.object(tapas, "_distill_rule", lambda *_a: (rule, "")), \
             patch.object(tapas, "_cai_critique", lambda *_a: (True, "")):
            outcome = tapas.process_session(f"{profile_id}-session", f"{profile_id} task", "Rama", "done")
        self.assertEqual(outcome["action"], "promoted")

    def test_sutras_are_per_profile_and_only_the_owner_changes_them(self) -> None:
        self._promote_sutra("alice", "Alice rule: give recipe weights in grams.")
        self._promote_sutra("bob", "Bob rule: keep exam plans to one page.")
        self._append(sutra_engine._SUTRAS_PATH, {
            "id": "sut-legacy", "ts": _now(2), "avatar": "Rama", "kind": "rule",
            "rule": "Owner rule from before profiles.", "query": "owner task", "result": "", "score": 0.9,
        })
        stored = json.loads((self.root / "profiles/alice/sutras.jsonl").read_text(encoding="utf-8"))
        self.assertEqual(stored["profile_id"], "alice")

        alice_rows = self._get("alice", "/sutras")["sutras"]
        self.assertEqual([row["rule"] for row in alice_rows], ["Alice rule: give recipe weights in grams."])
        self.assertEqual(self._get("alice", "/sutras")["summary"]["total_active_sutras"], 1)
        owner_rows = self._get("default", "/sutras")["sutras"]
        self.assertEqual([(row["id"], row["profile_id"]) for row in owner_rows], [("sut-legacy", "default")])
        everyone = self._get("default", "/sutras?scope=all")["sutras"]
        self.assertEqual({row["profile_id"] for row in everyone}, {"alice", "bob", "default"})
        self.assertEqual(self.client.get("/sutras?scope=all", headers=self._headers("alice")).status_code, 403)

        alice_sutra = alice_rows[0]["id"]
        for user_id in ("alice", "bob"):
            for change in ("accept", "revert"):
                denied = self.client.post(f"/sutras/{alice_sutra}/{change}", headers=self._headers(user_id))
                self.assertEqual(denied.status_code, 403, f"{user_id} {change}")
        accepted = self.client.post(f"/sutras/{alice_sutra}/accept", headers=self._headers("default"))
        self.assertEqual(accepted.status_code, 200, accepted.text)
        self.assertEqual(accepted.json()["profile_id"], "alice")
        self.assertEqual(self._get("alice", "/sutras")["sutras"][0]["status"], "active")
        self.assertEqual(
            self.client.post("/sutras/sut-missing/revert", headers=self._headers("default")).status_code, 404
        )

    def _write_family_logs(self) -> None:
        """One record per profile through the real writers, plus unstamped legacy rows."""
        for profile_id in ("alice", "bob"):
            with profile_scope(profile_id):
                andon.log_andon("Rama", "EMPTY_RESULT", f"{profile_id}-s", f"{profile_id} private task", "")
                karma_log.log_karma("vahana_delivered", f"{profile_id}-note", "Narad", f"{profile_id} reminder")
                self.smriti.log_mutation(
                    "episode_captured", entity_type="episode", entity_id=f"{profile_id}-episode",
                    actor="Rama", detail=f"{profile_id} private task",
                )
                cost_ledger.record(source="turn", model="deepseek-flash", prompt_tokens=1_000)
                audit_trail.log_invocation("Rama", f"{profile_id} private task", profile_id)
        self._append(andon.ANDON_LOG_PATH, {
            "id": "andon-legacy", "ts": _now(), "avatar": "Rama", "trigger": "TIMEOUT",
            "task_preview": "owner legacy task", "result_preview": "",
        })
        self._append(karma_log._KARMA_MUTATIONS_PATH, {
            "id": "karma-legacy", "ts": _now(), "action": "accepted", "sutra_id": "sut-legacy",
            "avatar": "Rama", "detail": "owner legacy task",
        })
        self._append(cost_ledger.COST_LEDGER_PATH, {
            "ts": datetime.now().isoformat(timespec="seconds"), "source": "turn", "model": "deepseek-flash",
            "prompt_tokens": 500, "completion_tokens": 0, "cost_usd": 0.001, "priced": True,
        })
        self._append(audit_trail._AUDIT_PATH, {
            "event": "invocation", "avatar": "Rama", "task_preview": "owner legacy task", "ts": _now(),
        })

    def test_andon_karma_costs_and_audit_show_only_the_callers_records(self) -> None:
        self._write_family_logs()

        def details(rows: list[dict], key: str) -> set[str]:
            return {row[key] for row in rows}

        for user_id, own in (("alice", "alice private task"), ("bob", "bob private task")):
            andon_rows = self._get(user_id, "/andon/log")["events"]
            self.assertEqual(details(andon_rows, "task_preview"), {own})
            self.assertEqual(details(andon_rows, "profile_id"), {user_id})
            self.assertEqual(self._get(user_id, "/andon/stats")["total"], 1)
            karma = self._get(user_id, "/karma")
            # Its own ledger, plus Smriti's rows about it in the shared ledger.
            self.assertEqual(details(karma["recent"], "detail"), {f"{user_id} reminder", own})
            self.assertEqual(details(self._get(user_id, "/karma/mutations")["mutations"], "profile_id"), {user_id})
            self.assertEqual(self._get(user_id, "/costs")["entries"], 1)
            self.assertEqual(details(self._get(user_id, "/audit"), "task_preview"), {own})

        # Unstamped legacy rows are the owner's, and only the owner's.
        self.assertEqual(details(self._get("default", "/andon/log")["events"], "task_preview"), {"owner legacy task"})
        self.assertEqual(details(self._get("default", "/karma")["recent"], "detail"), {"owner legacy task"})
        self.assertEqual(self._get("default", "/costs")["entries"], 1)
        self.assertEqual(details(self._get("default", "/audit"), "task_preview"), {"owner legacy task"})

        # The owner's explicit switch shows everyone; a member cannot use it.
        everyone = {"owner legacy task", "alice private task", "bob private task"}
        self.assertEqual(details(self._get("default", "/andon/log?scope=all")["events"], "task_preview"), everyone)
        self.assertEqual(self._get("default", "/andon/stats?scope=all")["total"], 3)
        self.assertEqual(
            details(self._get("default", "/karma/mutations?scope=all")["mutations"], "profile_id"),
            {"default", "alice", "bob"},
        )
        self.assertEqual(self._get("default", "/costs?scope=all")["entries"], 3)
        self.assertEqual(details(self._get("default", "/audit?scope=all"), "task_preview"), everyone)
        alice = self._headers("alice")
        for path in (
            "/andon/log?scope=all", "/andon/stats?scope=all", "/karma?scope=all",
            "/karma/mutations?scope=all", "/costs?scope=all", "/audit?scope=all", "/sankalpa?scope=all",
            "/search?q=private&scope=all", "/provenance/bob-episode?scope=all",
        ):
            self.assertEqual(self.client.get(path, headers=alice).status_code, 403, path)
        # Naming another profile never reaches its records.
        for path in ("/costs?user_id=bob", "/audit?user_id=bob", "/sankalpa?user_id=default"):
            self.assertEqual(self.client.get(path, headers=alice).status_code, 403, path)

    def test_search_and_provenance_stay_within_the_callers_profile(self) -> None:
        self._write_family_logs()
        with patch.object(importlib.import_module("smriti_indexer"), "fts_search_episodes", return_value=[]):
            alice_hits = self._get("alice", "/search?q=task")
            owner_hits = self._get("default", "/search?q=task")
            everyone = self._get("default", "/search?q=private&scope=all")
        self.assertEqual({hit["type"] for hit in alice_hits}, {"andon", "audit"})
        self.assertEqual({hit["preview"].split(" — ")[-1] for hit in alice_hits}, {"alice private task"})
        self.assertEqual({hit["profile_id"] for hit in owner_hits}, {"default"})
        self.assertEqual({hit["profile_id"] for hit in everyone}, {"alice", "bob"})

        self.assertEqual(self._get("alice", "/provenance/bob-episode")["kind"], "unknown")
        self.assertEqual(self._get("bob", "/provenance/bob-episode")["kind"], "mutation")
        self.assertEqual(self._get("default", "/provenance/bob-episode")["kind"], "unknown")
        self.assertEqual(self._get("default", "/provenance/bob-episode?scope=all")["kind"], "mutation")

    def test_sankalpa_and_commitments_are_the_callers_own(self) -> None:
        for profile_id in ("alice", "bob"):
            self._append(sankalpa._SANKALPAS_PATH, {
                "id": f"{profile_id}-style", "ts": _now(2), "user_id": profile_id, "avatar": "Krishna",
                "pattern_type": "preference", "content": f"{profile_id} likes short answers", "ttl_days": 180,
            })
            self._append(self.smriti.SANKALPA_COMMITMENTS_PATH, {
                "id": f"{profile_id}-goal", "ts": _now(), "user_id": profile_id, "avatar": "Rama",
                "kind": "goal", "content": f"{profile_id} goal",
            })
        alice = self._get("alice", "/sankalpa")
        self.assertEqual([row["id"] for row in alice["sankalpas"]], ["alice-style"])
        self.assertEqual([row["id"] for row in alice["commitments"]], ["alice-goal"])
        self.assertEqual(self._get("default", "/sankalpa")["sankalpas"], [])
        everyone = self._get("default", "/sankalpa?scope=all")
        self.assertEqual({row["id"] for row in everyone["sankalpas"]}, {"alice-style", "bob-style"})
        self.assertEqual({row["id"] for row in everyone["commitments"]}, {"alice-goal", "bob-goal"})
        # Accepting a style pattern stays self-service, and only for your own.
        bob = self._headers("bob")
        self.assertEqual(self.client.post("/sankalpa/alice-style/accept", headers=bob).status_code, 404)
        self.assertEqual(
            self.client.post("/sankalpa/alice-style/accept", headers=self._headers("alice")).status_code, 200
        )

    def test_a_workspace_id_never_reaches_another_profiles_learning(self) -> None:
        with patch.object(learning_workspace, "LEARNING_DIR", self.root / "learning"), \
             patch.object(guru_engine, "LEARNING_DIR", self.root / "learning"):
            bob_space = learning_workspace.ensure_workspace(user_id="bob", topic="Chess openings")
            climb = f"../bob/{bob_space['workspace_id']}"
            alice = self._headers("alice")
            refused = [
                self.client.post(
                    "/learning/artifacts",
                    json={"workspace_id": climb, "topic": "Chess openings", "artifact_type": "flashcards"},
                    headers=alice,
                ),
                self.client.get(f"/learning/artifacts/art_1?workspace_id={climb}", headers=alice),
                self.client.get(f"/learning/artifacts/art_1/versions/1?workspace_id={climb}", headers=alice),
                self.client.post("/learning/artifacts/art_1/update", json={"instruction": "x", "workspace_id": climb}, headers=alice),
                self.client.post("/learning/guided/answer", json={"workspace_id": climb, "answer": "e4"}, headers=alice),
                self.client.post("/learning/guided/skip", json={"workspace_id": climb}, headers=alice),
                self.client.post("/learning/guided/exit", json={"workspace_id": climb}, headers=alice),
            ]
            self.assertEqual([response.status_code for response in refused], [422] * len(refused))
            # The storage layer refuses too, whoever calls it.
            with self.assertRaises(ValueError):
                learning_workspace.load_workspace(user_id="alice", workspace_id=climb)
            with self.assertRaises(ValueError):
                guru_engine.load_syllabus(user_id="alice", workspace_id=climb)
            self.assertEqual(
                learning_workspace.load_workspace(user_id="bob", workspace_id=bob_space["workspace_id"])["topic"],
                "Chess openings",
            )

    def test_writers_name_the_profile_their_record_is_about(self) -> None:
        with profile_scope("alice"):
            cost = cost_ledger.record(source="tapas_judge", model="deepseek-flash", prompt_tokens=10)
            karma_log.log_karma("promoted", "sut-a", "Rama", "rule")
            andon.log_andon("Rama", "TIMEOUT", "s", "task", "")
            self.smriti.log_mutation("tapas_processed", entity_type="sutra_candidate", entity_id="s", actor="Rama")
        self.smriti.log_mutation(
            "episode_captured", entity_type="episode", entity_id="e", actor="Rama", profile_id="bob",
        )
        self.assertEqual(cost["user_id"], "alice")
        alice_home = self.root / "profiles" / "alice"
        for path in (alice_home / "karma_mutations.jsonl", alice_home / "andon_log.jsonl"):
            self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["profile_id"], "alice", path)
        shared = [json.loads(line) for line in karma_log._KARMA_MUTATIONS_PATH.read_text().splitlines()]
        self.assertEqual([row["profile_id"] for row in shared], ["alice", "bob"])


if __name__ == "__main__":
    unittest.main()
