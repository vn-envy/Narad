import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Optional
from unittest.mock import patch

import chat_attachments
import interaction_targets
import learning_workspace_api
import local_model_runtime
import server
from fastapi.testclient import TestClient

import onboarding


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

    def test_interaction_target_api_is_durable_and_revocable(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir, patch.object(
            interaction_targets,
            "_path",
            side_effect=lambda profile_id: Path(tempdir) / profile_id / "targets.json",
        ):
            created = self.client.post(
                "/interaction-targets",
                json={
                    "kind": "browser_skill",
                    "external_id": "browser-test",
                    "label": "Test browser",
                },
            )
            self.assertEqual(created.status_code, 200, created.text)
            target = created.json()["target"]
            listed = self.client.get("/interaction-targets?kind=browser_skill")
            self.assertEqual(listed.status_code, 200)
            self.assertEqual(listed.json()["targets"][0]["external_id"], "browser-test")
            removed = self.client.delete(f"/interaction-targets/{target['target_id']}")
            self.assertEqual(removed.status_code, 200)
            self.assertEqual(self.client.get("/interaction-targets").json()["targets"], [])

    def test_vite_dev_origins_can_reach_the_api(self) -> None:
        for host in ("localhost", "127.0.0.1"):
            for port in (5173, 5174):
                origin = f"http://{host}:{port}"
                response = self.client.get("/capabilities", headers={"Origin": origin})
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.headers.get("access-control-allow-origin"), origin)

    def test_google_oauth_redirect_prefers_secure_public_origin(self) -> None:
        request = SimpleNamespace(url=SimpleNamespace(port=8000))
        with patch.dict("os.environ", {"NARAD_PUBLIC_URL": "https://narad.example.ts.net"}):
            self.assertEqual(
                server._google_oauth_redirect_uri(request),
                "https://narad.example.ts.net/google/callback",
            )
        with patch.dict("os.environ", {"NARAD_PUBLIC_URL": "http://192.168.1.2"}):
            with self.assertRaises(ValueError):
                server._google_oauth_redirect_uri(request)

    def test_xai_callback_answers_private_network_preflight(self) -> None:
        """auth.x.ai delivers the OAuth code via a browser fetch to /callback;
        the preflight must be answered with CORS + PNA headers or xAI falls
        back to the manual copy-this-code page."""
        preflight = self.client.options("/callback", headers={
            "Origin": "https://auth.x.ai",
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Private-Network": "true",
        })
        self.assertEqual(preflight.status_code, 204)
        self.assertEqual(preflight.headers.get("access-control-allow-origin"), "https://auth.x.ai")
        self.assertEqual(preflight.headers.get("access-control-allow-private-network"), "true")

        # The actual GET carries the headers too (400 body: bogus state).
        got = self.client.get("/callback?code=x&state=y", headers={"Origin": "https://auth.x.ai"})
        self.assertEqual(got.headers.get("access-control-allow-origin"), "https://auth.x.ai")

        # Foreign origins get nothing.
        evil = self.client.options("/callback", headers={
            "Origin": "https://evil.example",
            "Access-Control-Request-Method": "GET",
        })
        self.assertIsNone(evil.headers.get("access-control-allow-origin"))

    def test_chat_returns_coherent_degraded_stream_without_adk(self) -> None:
        with patch.object(server, "_agent_runtime_unavailable_reason", return_value="test runtime disabled"):
            with self.client.stream("POST", "/chat", json={"query": "hello from test"}) as response:
                self.assertEqual(response.status_code, 200)
                body = "".join(chunk for chunk in response.iter_text() if chunk.strip())

        self.assertIn("Narad is running in degraded mode", body)
        self.assertIn('"type": "done"', body)

    def test_chat_attachment_api_uploads_previews_and_removes_private_batch(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            original_root = chat_attachments.ATTACHMENTS_DIR
            original_index = chat_attachments._INDEX_DIR
            original_batches = chat_attachments._BATCH_INDEX_DIR
            root = Path(tempdir)
            chat_attachments.ATTACHMENTS_DIR = root
            chat_attachments._INDEX_DIR = root / "index"
            chat_attachments._BATCH_INDEX_DIR = root / "batches"
            chat_attachments._INDEX_DIR.mkdir(parents=True)
            chat_attachments._BATCH_INDEX_DIR.mkdir(parents=True)
            try:
                uploaded = self.client.post(
                    "/chat/attachments",
                    data={
                        "user_id": "default",
                        "source": "folder",
                        "relative_paths": json.dumps(["demo/notes.txt"]),
                    },
                    files=[("files", ("notes.txt", b"hello attachment", "text/plain"))],
                )
                self.assertEqual(uploaded.status_code, 200, uploaded.text)
                payload = uploaded.json()
                self.assertEqual(payload["label"], "demo")
                self.assertEqual(payload["file_count"], 1)
                attachment = payload["attachments"][0]

                content = self.client.get(attachment["content_url"])
                self.assertEqual(content.status_code, 200)
                self.assertEqual(content.content, b"hello attachment")
                self.assertIn("inline", content.headers.get("content-disposition", ""))

                removed = self.client.delete(
                    f"/chat/attachment-batches/{payload['batch_id']}?user_id=default"
                )
                self.assertEqual(removed.status_code, 200)
                self.assertEqual(self.client.get(attachment["content_url"]).status_code, 404)
            finally:
                chat_attachments.ATTACHMENTS_DIR = original_root
                chat_attachments._INDEX_DIR = original_index
                chat_attachments._BATCH_INDEX_DIR = original_batches

    def test_onboarding_is_durable_and_preserves_existing_config(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            path = Path(tempdir) / "onboarding.json"
            path.write_text(json.dumps({"tier_choice": {"tier": "T2"}}), encoding="utf-8")
            readiness = {
                "model_ready": True,
                "research_ready": False,
                "connected_model_providers": ["deepseek"],
                "connected_search_providers": [],
                "connected_subscriptions": [],
                "local_model_ready": False,
            }
            with patch.object(onboarding, "ONBOARDING_PATH", path), patch.object(
                onboarding, "_connection_readiness", return_value=readiness
            ):
                initial = self.client.get("/onboarding?user_id=default")
                self.assertEqual(initial.status_code, 200)
                self.assertTrue(initial.json()["needs_onboarding"])
                self.assertTrue(initial.json()["readiness"]["model_ready"])

                saved = self.client.patch("/onboarding", json={
                    "user_id": "default",
                    "display_name": "  Nikhil   Vatsa  ",
                    "completed": True,
                    "skipped": False,
                })
                self.assertEqual(saved.status_code, 200, saved.text)
                self.assertTrue(saved.json()["completed"])
                self.assertEqual(saved.json()["display_name"], "Nikhil Vatsa")

                persisted = json.loads(path.read_text(encoding="utf-8"))
                self.assertEqual(persisted["tier_choice"], {"tier": "T2"})
                self.assertNotIn("connections", persisted)

                invalid = self.client.patch("/onboarding", json={"completed": "yes"})
                self.assertEqual(invalid.status_code, 400)

    def test_local_model_status_is_exposed_without_secrets(self) -> None:
        status = {
            "available": True,
            "ready": True,
            "runtime_installed": True,
            "reachable": True,
            "managed": True,
            "local_host": True,
            "url": "http://127.0.0.1:11434",
            "model": "ollama/gemma4:e2b-it-q4_K_M",
            "model_tag": "gemma4:e2b-it-q4_K_M",
            "model_installed": True,
            "model_size": "E2B",
            "optimized_variant": "q4-k-m",
            "download_gb": 7.2,
            "upgrade_threshold_gb": 16,
            "ram_gb": 16,
            "memory_constrained": False,
            "residency": "warm",
            "keep_alive": "10m",
            "configured_context_tokens": 32768,
            "max_context_tokens": 131072,
            "no_api_key": True,
            "supports": {"images": True, "native_tools": True, "computer_use": True},
            "install": {"state": "complete", "progress": 1.0, "status": "ready", "error": None},
            "reason": None,
        }
        with patch.object(local_model_runtime, "local_runtime_status", return_value=status), patch.object(
            server, "refresh_avatar_models", return_value=dict(server.AVATAR_MODELS)
        ):
            response = self.client.get("/local-model/status")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["model_tag"], "gemma4:e2b-it-q4_K_M")
        self.assertTrue(response.json()["supports"]["computer_use"])
        self.assertNotIn("api_key", response.json())

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

    def test_partial_streamed_function_json_is_not_structurally_repaired(self) -> None:
        partial = '{"query":"senior product manager'
        with self.assertRaises(json.JSONDecodeError):
            server._json_loads_tolerant(partial)
        with self.assertRaises(json.JSONDecodeError):
            server._json_loads_tolerant("")

    def test_matsya_retrieval_guard_blocks_duplicates_and_enforces_budget(self) -> None:
        import avatar_agents

        context = SimpleNamespace(state={})
        tool = SimpleNamespace(name="exa_search")
        avatar_agents._reset_matsya_retrieval_budget(context)
        with patch.dict("os.environ", {"NARAD_MATSYA_RETRIEVAL_BUDGET": "2"}):
            first_args = {
                "query": "one",
                "max_results": 25,
                "include_text": True,
                "text_max_characters": 50000,
            }
            self.assertIsNone(avatar_agents._guard_matsya_retrieval(tool, first_args, context))
            self.assertEqual(first_args["max_results"], 6)
            self.assertEqual(first_args["text_max_characters"], 6000)
            duplicate = avatar_agents._guard_matsya_retrieval(tool, first_args, context)
            self.assertEqual(duplicate["status"], "skipped")
            self.assertIsNone(avatar_agents._guard_matsya_retrieval(tool, {"query": "two"}, context))
            exhausted = avatar_agents._guard_matsya_retrieval(tool, {"query": "three"}, context)
            self.assertEqual(exhausted["status"], "budget_exhausted")

    def test_memory_diagnostics_endpoints_exist(self) -> None:
        scorecard = self.client.get("/architecture/scorecard")
        self.assertEqual(scorecard.status_code, 200)
        self.assertTrue(scorecard.json()["episode_store_enabled"])

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
        ), patch.object(server, "_clear_thread", return_value={"status": "ok", "removed": True, "session_id": "sess-1"}), patch.object(
            server, "_delete_harness_session_record"
        ):
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
