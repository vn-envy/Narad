import json
import unittest
from types import SimpleNamespace
from typing import Optional
from unittest.mock import patch

import learning_workspace_api
import project_execution_api
import project_wiki_api
import server
from fastapi.testclient import TestClient
from project_tasks import ProjectTask


class _FakeEvent:
    def __init__(self, *, final: bool = False, parts: Optional[list] = None):
        self._final = final
        self.content = SimpleNamespace(parts=parts or [])

    def is_final_response(self) -> bool:
        return self._final


class ServerContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = TestClient(server.app)

    def test_health_and_capabilities_are_canonical(self) -> None:
        health = self.client.get("/health")
        capabilities = self.client.get("/capabilities")

        self.assertEqual(health.status_code, 200)
        self.assertEqual(capabilities.status_code, 200)

        health_payload = health.json()
        capabilities_payload = capabilities.json()

        self.assertEqual(health_payload["architecture"]["canonical_agent_count"], 4)
        self.assertEqual(
            [agent["name"] for agent in capabilities_payload["agents"]],
            ["Matsya", "Rama", "Krishna", "Parashurama"],
        )
        self.assertIn("context_policy", capabilities_payload)
        self.assertIn("fallback_graph", capabilities_payload["context_policy"])
        self.assertIn("memory_tiers", capabilities_payload)

    def test_chat_returns_coherent_degraded_stream_without_adk(self) -> None:
        with self.client.stream("POST", "/chat", json={"query": "hello from test"}) as response:
            self.assertEqual(response.status_code, 200)
            body = "".join(chunk for chunk in response.iter_text() if chunk.strip())

        self.assertIn("Narad is running in degraded mode", body)
        self.assertIn('"type": "done"', body)

    def test_event_to_sse_includes_discipline_metadata(self) -> None:
        start_event = _FakeEvent(parts=[
            SimpleNamespace(
                function_call=SimpleNamespace(name="invoke_matsya", args={"request": "Read this"}),
                function_response=None,
                text=None,
            )
        ])
        done_event = _FakeEvent(parts=[
            SimpleNamespace(
                function_call=None,
                function_response=SimpleNamespace(name="invoke_parashurama", response={"ok": True}),
                text=None,
            )
        ])

        start_payload = json.loads(server._event_to_sse(start_event)[0])
        done_payload = json.loads(server._event_to_sse(done_event)[0])

        self.assertEqual(start_payload["type"], "avatar_start")
        self.assertEqual(start_payload["data"]["avatar"], "Matsya")
        self.assertEqual(start_payload["data"]["discipline"], "retrieval")
        self.assertIn("documents", start_payload["data"]["disciplines"])

        self.assertEqual(done_payload["type"], "avatar_done")
        self.assertEqual(done_payload["data"]["avatar"], "Parashurama")
        self.assertEqual(done_payload["data"]["discipline"], "engineering")
        self.assertIn("shell", done_payload["data"]["disciplines"])

    def test_new_cultural_endpoints_exist(self) -> None:
        scorecard = self.client.get("/architecture/scorecard")
        self.assertEqual(scorecard.status_code, 200)
        self.assertIn("swapna_enabled", scorecard.json())

        evolution = self.client.get("/evolution/history")
        self.assertEqual(evolution.status_code, 200)
        evolution_payload = evolution.json()
        self.assertIn("agents", evolution_payload)
        self.assertIn("timeline", evolution_payload)

        swapna = self.client.post("/swapna/run", params={"apply": False})
        self.assertEqual(swapna.status_code, 200)
        self.assertEqual(swapna.json()["status"], "ok")

        inbox = self.client.get("/swapna/inbox")
        self.assertEqual(inbox.status_code, 200)
        self.assertIn("items", inbox.json())

        memory_tiers = self.client.get("/memory/tiers")
        self.assertEqual(memory_tiers.status_code, 200)
        self.assertIn("policy", memory_tiers.json())

    def test_sutras_endpoint_includes_ui_settings(self) -> None:
        sutras = self.client.get("/sutras")
        self.assertEqual(sutras.status_code, 200)
        payload = sutras.json()
        self.assertIn("settings", payload)
        self.assertIn("promote_threshold", payload["settings"])
        self.assertIn("cooldown_hours", payload["settings"])

    def test_thread_endpoint_exposes_turns_and_can_clear(self) -> None:
        sample_turns = [{"role": "user", "text": "hello"}]
        sample_state = {"last_trace_session_id": "trace-1", "thread_summary": "Earlier summary"}
        with patch.object(server, "_load_thread", return_value=sample_turns), patch.object(
            server, "_load_working_state", return_value=sample_state
        ), patch.object(server, "_clear_thread", return_value={"status": "ok", "removed": True, "session_id": "sess-1"}):
            thread = self.client.get("/thread/sess-1")
            cleared = self.client.delete("/thread/sess-1")

        self.assertEqual(thread.status_code, 200)
        self.assertEqual(thread.json()["turns"], sample_turns)
        self.assertEqual(thread.json()["turn_count"], 1)
        self.assertEqual(thread.json()["working_state"], sample_state)
        self.assertEqual(thread.json()["thread_summary"], "Earlier summary")
        self.assertTrue(thread.json()["restorable"])
        self.assertEqual(cleared.status_code, 200)
        self.assertTrue(cleared.json()["removed"])

    def test_project_workspace_execution_and_listing_endpoints_expose_workspace_first_fields(self) -> None:
        project_record = {
            "id": "proj_narad",
            "name": "Narad Platform",
            "workspace_root": "/workspace/narad",
            "workspace_label": "narad",
            "project_status": "active",
            "created_at": "2026-06-06T00:00:00+00:00",
            "session_ids": ["sess-77"],
        }
        session_info = {
            "session_id": "sess-77",
            "ts": "2026-06-06T01:00:00+00:00",
            "query": "Continue Karma",
            "avatars": ["Rama"],
            "total_ms": 1200,
        }
        task = ProjectTask(
            task_id="task_1",
            project_id="proj_narad",
            workspace_root="/workspace/narad",
            source_session_id="sess-77",
            title="Finish execution panel",
            description="",
            status="todo",
            priority="medium",
            owner="Parashurama",
            kind="implementation",
            blocked_by=[],
            artifact_refs=[],
            sort_order=0,
            created_at="2026-06-06T00:00:00+00:00",
            updated_at="2026-06-06T01:00:00+00:00",
            completed_at=None,
        )
        task_summary = {
            "total": 1,
            "by_status": {"todo": 1},
            "now": [],
            "next": [task.to_dict()],
            "blocked": [],
            "recent_done": [],
        }
        with patch.object(project_wiki_api, "load_projects", return_value=[project_record]), patch.object(
            project_wiki_api, "_session_info", return_value=session_info
        ), patch.object(project_execution_api, "get_project", return_value=project_record), patch.object(
            project_execution_api, "_session_info", return_value=session_info
        ), patch.object(project_execution_api, "get_wiki_pages", return_value=[]), patch.object(
            project_execution_api, "list_tasks", return_value=[task]
        ), patch.object(project_execution_api, "task_summary", return_value=task_summary):
            projects = self.client.get("/projects/default")
            workspace = self.client.get("/projects/default/proj_narad/workspace")
            execution = self.client.get("/projects/default/proj_narad/execution")
            tasks = self.client.get("/projects/default/proj_narad/tasks")

        self.assertEqual(projects.status_code, 200)
        self.assertEqual(projects.json()["projects"][0]["workspace_root"], "/workspace/narad")
        self.assertEqual(projects.json()["projects"][0]["active_session_id"], "sess-77")
        self.assertEqual(workspace.status_code, 200)
        self.assertEqual(workspace.json()["project"]["workspace_label"], "narad")
        self.assertEqual(execution.status_code, 200)
        self.assertEqual(execution.json()["workspace_root"], "/workspace/narad")
        self.assertEqual(tasks.status_code, 200)
        self.assertEqual(tasks.json()["tasks"][0]["workspace_root"], "/workspace/narad")

    def test_latest_threads_endpoint_exposes_recent_thread(self) -> None:
        recent = [{
            "session_id": "sess-latest",
            "turn_count": 4,
            "updated_at": "2026-06-02T00:00:00+00:00",
            "last_user_query": "Continue the Narad architecture work",
            "last_assistant_preview": "I will continue from the prior context.",
            "thread_summary": "Earlier summary",
        }]
        with patch.object(server, "_recent_threads", return_value=recent):
            latest = self.client.get("/threads/latest")
            listing = self.client.get("/threads", params={"limit": 5})

        self.assertEqual(latest.status_code, 200)
        self.assertTrue(latest.json()["has_thread"])
        self.assertEqual(latest.json()["thread"]["session_id"], "sess-latest")
        self.assertEqual(listing.status_code, 200)
        self.assertEqual(listing.json()["threads"][0]["turn_count"], 4)

    def test_harness_endpoints_expose_session_plane_and_context(self) -> None:
        overview_payload = {
            "summary": {"session_count": 2, "restorable_count": 2},
            "sessions": [{"session_id": "sess-1"}],
            "context": {"context_order": [{"key": "thread"}]},
        }
        session_record = {
            "session_id": "sess-1",
            "title": "Resume Narad architecture work",
            "turn_count": 6,
            "restorable": True,
        }
        context_payload = {
            "thread_plane": {"turn_count": 6},
            "working_plane": {"avatars": ["Matsya"]},
            "smriti_plane": {"episode_count": 2},
            "governance_plane": {"mutation_count": 3},
        }
        fork_record = {
            "session_id": "sess-fork",
            "parent_session_id": "sess-1",
            "source": "fork",
        }
        with patch.object(server, "_harness_overview", return_value=overview_payload), patch.object(
            server, "_list_harness_sessions", return_value=[session_record]
        ), patch.object(server, "_get_harness_session_record", return_value=session_record), patch.object(
            server, "_build_harness_context_bundle", return_value=context_payload
        ), patch.object(server, "_compact_harness_session", return_value=session_record), patch.object(
            server, "_archive_harness_session", return_value=dict(session_record, archived=True)
        ), patch.object(server, "_recover_harness_session", return_value=dict(session_record, archived=False)), patch.object(
            server, "_fork_harness_session", return_value=fork_record
        ):
            overview = self.client.get("/harness/overview")
            listing = self.client.get("/harness/sessions")
            detail = self.client.get("/harness/sessions/sess-1")
            context = self.client.get("/harness/context/sess-1")
            compact = self.client.post("/harness/sessions/sess-1/compact")
            archive = self.client.post("/harness/sessions/sess-1/archive")
            recover = self.client.post("/harness/sessions/sess-1/recover")
            fork = self.client.post("/harness/sessions/sess-1/fork", params={"title": "Forked path"})

        self.assertEqual(overview.status_code, 200)
        self.assertEqual(overview.json()["summary"]["session_count"], 2)
        self.assertEqual(listing.status_code, 200)
        self.assertEqual(listing.json()["sessions"][0]["session_id"], "sess-1")
        self.assertEqual(detail.status_code, 200)
        self.assertEqual(detail.json()["context"]["smriti_plane"]["episode_count"], 2)
        self.assertEqual(context.status_code, 200)
        self.assertEqual(context.json()["working_plane"]["avatars"], ["Matsya"])
        self.assertEqual(compact.status_code, 200)
        self.assertEqual(compact.json()["status"], "ok")
        self.assertEqual(archive.status_code, 200)
        self.assertTrue(archive.json()["session"]["archived"])
        self.assertEqual(recover.status_code, 200)
        self.assertFalse(recover.json()["session"]["archived"])
        self.assertEqual(fork.status_code, 200)
        self.assertEqual(fork.json()["session"]["source"], "fork")

    def test_learning_workspace_endpoints_exist(self) -> None:
        workspace_payload = {
            "workspace_id": "learn_123",
            "topic": "transformer attention",
            "mission": "# Mission",
            "glossary": "# Glossary",
            "resources": "# Resources",
            "records": [],
        }
        record_payload = {
            "record_id": "0001",
            "title": "Checkpoint",
            "summary": "Summary",
            "body": "# Checkpoint",
            "created_at": "2026-06-09T00:00:00+00:00",
            "type": "lesson",
            "session_id": "sess-1",
            "tags": ["teach"],
            "path": "/tmp/checkpoint.md",
        }
        with patch.object(learning_workspace_api, "list_workspaces", return_value=[workspace_payload]), patch.object(
            learning_workspace_api, "load_workspace", return_value=workspace_payload
        ), patch.object(
            learning_workspace_api, "list_records", return_value=[record_payload]
        ), patch.object(
            learning_workspace_api, "append_learning_record", return_value=record_payload
        ):
            listing = self.client.get("/learning/workspaces")
            detail = self.client.get("/learning/workspaces/learn_123")
            records = self.client.get("/learning/workspaces/learn_123/records")
            create = self.client.post(
                "/learning/workspaces/learn_123/records",
                json={"title": "Checkpoint", "summary": "Summary", "body": "Body"},
            )

        self.assertEqual(listing.status_code, 200)
        self.assertEqual(listing.json()["workspaces"][0]["workspace_id"], "learn_123")
        self.assertEqual(detail.status_code, 200)
        self.assertEqual(detail.json()["topic"], "transformer attention")
        self.assertEqual(records.status_code, 200)
        self.assertEqual(records.json()["records"][0]["record_id"], "0001")
        self.assertEqual(create.status_code, 200)
        self.assertEqual(create.json()["record"]["title"], "Checkpoint")


if __name__ == "__main__":
    unittest.main()
