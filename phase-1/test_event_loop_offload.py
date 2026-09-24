"""Phase 0 event-loop floor: one person's slow task must not freeze everyone.

Narad serves every family member from one uvicorn worker (one asyncio loop).
These tests pin the offloads: sync avatar tools, Tapas/Sankalpa judges,
Smriti embeddings, and the runtime status probe all run on worker threads
while a concurrent coroutine keeps ticking. All offline: no network, no LLM.
"""
from __future__ import annotations

import asyncio
import concurrent.futures
import importlib
import inspect
import os
import sys
import tempfile
import threading
import time
import types
import unittest
from enum import Enum
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

_r = next(p for p in Path(__file__).resolve().parents if (p / "narad_paths.py").exists())
sys.path[:0] = [str(_r)]  # narad root hop
import narad_paths  # noqa: F401  — registers all phase dirs; must precede phase imports

# isort: split
import avatar_agents
import browser_skill
import runtime_contract
from google.adk.agents import LlmAgent
from google.adk.models.base_llm import BaseLlm
from google.adk.models.llm_response import LlmResponse
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.adk.tools import FunctionTool, ToolContext
from google.genai import types as genai_types

from profile_context import current_profile_id, profile_scope

_TICK_S = 0.05
_MAX_GAP_S = 0.5  # a blocked loop shows a gap as long as the blocking call


async def _run_while_ticking(awaitable) -> tuple[object, float, int]:
    """Await `awaitable` while a ticker sleeps _TICK_S in a loop.

    Returns (result, longest gap between ticks, tick count). A call that
    blocks the loop shows up as one gap as long as the call itself.
    """
    gaps: list[float] = []
    started = asyncio.Event()
    done = asyncio.Event()

    async def _ticker() -> None:
        last = time.monotonic()
        started.set()
        while not done.is_set():
            await asyncio.sleep(_TICK_S)
            now = time.monotonic()
            gaps.append(now - last)
            last = now

    ticker = asyncio.ensure_future(_ticker())
    await started.wait()
    try:
        result = await awaitable
    finally:
        done.set()
        await ticker
    return result, max(gaps, default=0.0), len(gaps)


def _slow_lookup(query: str, limit: int = 3) -> dict:
    """Look something up slowly.

    Args:
        query: What to look up.
        limit: Maximum number of results.
    """
    time.sleep(1.5)
    return {"query": query, "limit": limit, "thread": threading.current_thread().name}


class Priority(str, Enum):
    low = "low"
    high = "high"


def _triage_with_context(item: str, tool_context: ToolContext, priority: Priority = Priority.low) -> dict:
    """Triage one item.

    Args:
        item: The item to triage.
        priority: How urgent it is.
    """
    return {"item": item, "priority": str(priority), "context": tool_context}


class SyncToolOffloadTests(unittest.TestCase):
    def test_wrapped_sync_tool_does_not_block_the_loop(self) -> None:
        wrapped = avatar_agents._run_off_loop(_slow_lookup)

        async def _scenario():
            return await _run_while_ticking(wrapped(query="tides", limit=2))

        result, max_gap, ticks = asyncio.run(_scenario())
        self.assertEqual(result["query"], "tides")
        self.assertEqual(result["limit"], 2)
        self.assertNotEqual(result["thread"], threading.main_thread().name)
        self.assertLess(max_gap, _MAX_GAP_S)
        self.assertGreaterEqual(ticks, 15)  # ~1.5 s of 50 ms ticks kept flowing

    def test_unwrapped_sync_call_does_block_the_loop(self) -> None:
        # Control: proves the ticker harness detects a blocked loop.
        async def _blocking():
            time.sleep(0.6)

        _, max_gap, _ = asyncio.run(_run_while_ticking(_blocking()))
        self.assertGreaterEqual(max_gap, 0.55)

    def test_context_vars_are_visible_inside_the_offloaded_tool(self) -> None:
        def _whoami() -> dict:
            """Report the request context seen by the tool."""
            return {
                "profile": current_profile_id(),
                "session": avatar_agents._http_session_id_ctx.get(""),
                "main_thread": threading.current_thread() is threading.main_thread(),
            }

        wrapped = avatar_agents._run_off_loop(_whoami)

        async def _scenario():
            avatar_agents._http_session_id_ctx.set("sess-42")
            with profile_scope("asha"):
                return await wrapped()

        seen = asyncio.run(_scenario())
        self.assertEqual(seen, {"profile": "asha", "session": "sess-42", "main_thread": False})

    def test_every_avatar_sync_tool_is_offloaded_with_identical_declaration(self) -> None:
        checked = 0
        for agent in (
            avatar_agents.matsya,
            avatar_agents.rama,
            avatar_agents.krishna,
            avatar_agents.parashurama,
        ):
            for tool in agent.tools:
                with self.subTest(avatar=agent.name, tool=tool.name):
                    self.assertTrue(inspect.iscoroutinefunction(tool.func))
                    raw = FunctionTool(tool.func.__wrapped__)
                    self.assertEqual(tool.name, raw.name)
                    self.assertEqual(tool.description, raw.description)
                    self.assertEqual(tool._get_mandatory_args(), raw._get_mandatory_args())
                    self.assertEqual(
                        tool._get_declaration().model_dump(),
                        raw._get_declaration().model_dump(),
                    )
                    checked += 1
        names = {tool.name for tool in avatar_agents.matsya.tools}
        self.assertTrue({"computer_use", "phone_use", "browse_url", "web_search"} <= names)
        self.assertGreater(checked, 60)

    def test_tool_context_tool_keeps_declaration_and_receives_context(self) -> None:
        raw = FunctionTool(_triage_with_context)
        wrapped = avatar_agents._offload_sync_tools([raw])[0]
        self.assertIsNot(wrapped, raw)
        declaration = wrapped._get_declaration().model_dump()
        self.assertEqual(declaration, raw._get_declaration().model_dump())
        self.assertNotIn("tool_context", declaration["parameters"]["properties"])

        sentinel = object()
        result = asyncio.run(wrapped.run_async(args={"item": "rent"}, tool_context=sentinel))
        self.assertEqual(result["item"], "rent")
        self.assertIs(result["context"], sentinel)

    def test_async_tools_are_left_on_the_loop(self) -> None:
        async def _already_async(query: str) -> dict:
            """Async tool."""
            return {"query": query}

        tool = FunctionTool(_already_async)
        self.assertIs(avatar_agents._offload_sync_tools([tool])[0], tool)


class _ScriptedLlm(BaseLlm):
    """Calls slow_lookup once, then answers with the tool's result."""

    async def generate_content_async(self, llm_request, stream: bool = False):
        last = llm_request.contents[-1]
        responses = [part.function_response for part in last.parts or [] if part.function_response]
        if responses:
            text = f"done on {responses[0].response.get('thread')}"
            yield LlmResponse(content=genai_types.Content(role="model", parts=[genai_types.Part(text=text)]))
            return
        call = genai_types.FunctionCall(name="slow_lookup", args={"query": "tides"})
        yield LlmResponse(content=genai_types.Content(role="model", parts=[genai_types.Part(function_call=call)]))


class AdkRunnerOffloadTests(unittest.TestCase):
    def test_slow_sync_tool_inside_adk_run_keeps_the_loop_responsive(self) -> None:
        def slow_lookup(query: str) -> dict:
            """Look something up slowly.

            Args:
                query: What to look up.
            """
            time.sleep(1.0)
            return {"query": query, "thread": threading.current_thread().name}

        agent = LlmAgent(
            name="Probe",
            model=_ScriptedLlm(model="scripted"),
            instruction="Use slow_lookup.",
            tools=avatar_agents._offload_sync_tools([FunctionTool(slow_lookup)]),
        )

        async def _scenario() -> str:
            svc = InMemorySessionService()
            runner = Runner(agent=agent, app_name="offload_probe", session_service=svc)
            await svc.create_session(app_name="offload_probe", user_id="u", session_id="s")
            message = genai_types.Content(role="user", parts=[genai_types.Part(text="go")])
            final = ""
            async for event in runner.run_async(user_id="u", session_id="s", new_message=message):
                if event.is_final_response() and event.content and event.content.parts:
                    final = "".join(part.text or "" for part in event.content.parts)
            return final

        final, max_gap, ticks = asyncio.run(_run_while_ticking(_scenario()))
        self.assertTrue(final.startswith("done on "))
        self.assertNotIn(threading.main_thread().name, final)
        self.assertLess(max_gap, _MAX_GAP_S)
        self.assertGreaterEqual(ticks, 10)


class RuntimeStatusCacheTests(unittest.TestCase):
    def setUp(self) -> None:
        runtime_contract._runtime_status_cache.clear()
        self.addCleanup(runtime_contract._runtime_status_cache.clear)
        self.probes = 0

    def _fake_status(self, delay_s: float = 0.0):
        def _probe() -> list[dict]:
            self.probes += 1
            time.sleep(delay_s)
            return [{"name": "Matsya", "degraded_tool_families": ["phone"], "probe": self.probes}]
        return _probe

    def test_cached_value_is_reused_within_ttl_without_reprobing(self) -> None:
        clock = [1000.0]
        with patch.object(runtime_contract, "agent_runtime_status", self._fake_status()), \
             patch.object(runtime_contract, "time", SimpleNamespace(monotonic=lambda: clock[0])):
            first = runtime_contract.cached_agent_runtime_status()
            first[0]["degraded_tool_families"].append("mutated-by-caller")
            clock[0] += 29.0
            second = runtime_contract.cached_agent_runtime_status()
            self.assertEqual(self.probes, 1)
            self.assertEqual(second[0]["degraded_tool_families"], ["phone"])
            clock[0] += 2.0  # past the 30 s TTL
            third = runtime_contract.cached_agent_runtime_status()
        self.assertEqual(self.probes, 2)
        self.assertEqual(third[0]["probe"], 2)

    def test_concurrent_misses_share_one_probe(self) -> None:
        results: list[list[dict]] = []
        with patch.object(runtime_contract, "agent_runtime_status", self._fake_status(0.2)):
            threads = [
                threading.Thread(target=lambda: results.append(runtime_contract.cached_agent_runtime_status()))
                for _ in range(6)
            ]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()
        self.assertEqual(self.probes, 1)
        self.assertEqual(len(results), 6)

    def test_avatar_status_lookup_runs_off_the_loop(self) -> None:
        with patch.object(runtime_contract, "agent_runtime_status", self._fake_status(0.8)):
            row, max_gap, _ = asyncio.run(
                _run_while_ticking(avatar_agents._avatar_runtime_status("Matsya"))
            )
            missing = asyncio.run(avatar_agents._avatar_runtime_status("Nobody"))
        self.assertEqual(row["degraded_tool_families"], ["phone"])
        self.assertIsNone(missing)
        self.assertLess(max_gap, _MAX_GAP_S)
        self.assertEqual(self.probes, 1)

    def test_slow_probe_never_holds_the_avatar_and_fills_the_cache(self) -> None:
        async def _timed_lookup() -> tuple[dict | None, float]:
            started = time.monotonic()
            row = await avatar_agents._avatar_runtime_status("Matsya", wait_s=0.1)
            return row, time.monotonic() - started

        with patch.object(runtime_contract, "agent_runtime_status", self._fake_status(0.5)):
            # asyncio.run's shutdown joins the still-probing worker thread, so
            # time the lookup inside the loop, as a long-lived server sees it.
            row, waited = asyncio.run(_timed_lookup())
            self.assertIsNone(row)  # this run goes without the metadata...
            self.assertLess(waited, 0.4)
            row = asyncio.run(avatar_agents._avatar_runtime_status("Matsya", wait_s=0.1))
        self.assertEqual(row["degraded_tool_families"], ["phone"])
        self.assertEqual(self.probes, 1)

    def test_capabilities_probe_seeds_the_cache_without_waiting(self) -> None:
        fresh = [{"name": "Rama", "degraded_tool_families": []}]
        with patch.object(runtime_contract, "agent_runtime_status", self._fake_status()):
            with runtime_contract._runtime_status_lock:  # a probe is in flight
                runtime_contract._store_runtime_status(fresh)  # returns, no deadlock
            self.assertEqual(runtime_contract._runtime_status_cache, {})
            runtime_contract._store_runtime_status(fresh)
            self.assertEqual(runtime_contract.cached_agent_runtime_status(), fresh)
        self.assertEqual(self.probes, 0)

    def test_avatar_status_probe_failure_is_metadata_only(self) -> None:
        def _broken() -> list[dict]:
            raise RuntimeError("cua-driver hung")

        with patch.object(runtime_contract, "agent_runtime_status", _broken), \
             self.assertLogs("narad.avatar", level="WARNING"):
            self.assertIsNone(asyncio.run(avatar_agents._avatar_runtime_status("Matsya")))

    def test_capabilities_report_vendor_versions_from_the_probe(self) -> None:
        import computer_use_skill

        probe = {
            "available": True,
            "contexts": {"signed_in": {"available": True, "version": "0.9.4"}},
            "desktop": {"adapters": {"cua": {"version": "cua-driver 1.2.3"}}},
        }
        with patch.object(computer_use_skill, "browser_runtime_status", lambda: probe):
            computer = runtime_contract.tool_family_status()["computer"]
        self.assertEqual(computer["versions"], {"cua_driver": "cua-driver 1.2.3", "bsk": "0.9.4"})


def _fake_litellm(content: str, calls: list[str], delay_s: float = 0.8) -> types.ModuleType:
    """A litellm stand-in whose completion blocks like a real HTTP call."""
    fake = types.ModuleType("litellm")

    def completion(**_kwargs):
        calls.append(threading.current_thread().name)
        time.sleep(delay_s)
        message = SimpleNamespace(content=content, reasoning_content=None)
        return SimpleNamespace(choices=[SimpleNamespace(message=message)], usage=None)

    fake.completion = completion
    return fake


def _rules_only_privacy(test: unittest.TestCase) -> None:
    """These tests exercise offloading, not redaction: allow the redact tier
    with the rules detector and keep the egress ledger out of the real home."""
    import tempfile

    import privacy_gateway

    home = tempfile.mkdtemp(prefix="narad-privacy-")
    for patcher in (
        patch.dict(os.environ, {"NARAD_PII_DETECTOR": "rules"}),
        patch.object(privacy_gateway, "_privacy_dir", lambda profile_id=None: Path(home) / (profile_id or "p")),
    ):
        patcher.start()
        test.addCleanup(patcher.stop)


class LearningLoopOffloadTests(unittest.TestCase):
    def setUp(self) -> None:
        _rules_only_privacy(self)

    def test_tapas_judge_runs_off_the_loop(self) -> None:
        smriti_core = importlib.import_module("smriti_core")
        tapas = importlib.import_module("tapas")
        calls: list[str] = []
        verdict = '{"score": 0.6, "reason": "fine", "hallucination_free": true, "sequence_correct": true}'
        with patch.dict(sys.modules, {"litellm": _fake_litellm(verdict, calls)}), \
             patch.dict(os.environ, {"TAPAS_JUDGE_PROVIDER": "llm", "NARAD_LEARNING_FREEZE": ""}), \
             patch.object(tapas, "_load_run_evidence", lambda *_: {"available": False}), \
             patch.object(smriti_core, "log_mutation", lambda *_a, **_k: None):
            _, max_gap, _ = asyncio.run(_run_while_ticking(avatar_agents._run_tapas(
                session_id="s1",
                task="Summarise the household budget for March",
                avatar="Rama",
                result="March spending was within budget across groceries, rent, and utilities.",
            )))
        self.assertEqual(len(calls), 1)
        self.assertNotEqual(calls[0], threading.main_thread().name)
        self.assertLess(max_gap, _MAX_GAP_S)

    def test_sankalpa_extraction_runs_off_the_loop(self) -> None:
        smriti_core = importlib.import_module("smriti_core")
        sankalpa = importlib.import_module("sankalpa")
        calls: list[str] = []
        sessions = [{"task": f"task {i}", "result": f"result {i}"} for i in range(4)]
        with patch.dict(sys.modules, {"litellm": _fake_litellm("[]", calls)}), \
             patch.dict(os.environ, {"NARAD_LEARNING_FREEZE": ""}), \
             patch.object(sankalpa, "_log_session", lambda *_: None), \
             patch.object(sankalpa, "_session_count", lambda *_: sankalpa.EXTRACT_EVERY), \
             patch.object(sankalpa, "_load_recent_sessions", lambda *_a, **_k: sessions), \
             patch.object(sankalpa, "load_sankalpas", lambda **_: []), \
             patch.object(smriti_core, "_record_commitments", lambda **_: []):
            _, max_gap, _ = asyncio.run(_run_while_ticking(avatar_agents._run_sankalpa_observe(
                user_id="default", avatar="Rama", task="Plan groceries", result="Here is the plan.",
            )))
        self.assertEqual(len(calls), 1)
        self.assertNotEqual(calls[0], threading.main_thread().name)
        self.assertLess(max_gap, _MAX_GAP_S)

    def test_learning_failures_are_logged_not_raised(self) -> None:
        smriti_core = importlib.import_module("smriti_core")

        def _boom(**_kwargs):
            raise RuntimeError("judge offline")

        with patch.dict(os.environ, {"NARAD_LEARNING_FREEZE": ""}), \
             patch.object(smriti_core, "promote_sutra", _boom), \
             self.assertLogs("narad.tapas", level="WARNING"):
            asyncio.run(avatar_agents._run_tapas("s1", "task", "Rama", "result"))


class SmritiEmbeddingOffloadTests(unittest.TestCase):
    def setUp(self) -> None:
        self.embed = importlib.import_module("smriti_embed")
        self.embed._clients.clear()
        self.addCleanup(self.embed._clients.clear)
        for name, value in (("_SMRITI_EMBED_PROVIDER", "gemini"), ("_embed_unavailable_until", 0.0)):
            patcher = patch.object(self.embed, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        _rules_only_privacy(self)

    def test_gemini_client_is_built_once_and_shared_across_threads(self) -> None:
        built: list[str] = []

        class _FakeClient:
            def __init__(self, api_key: str) -> None:
                built.append(api_key)
                self.models = self

            def embed_content(self, *, model, contents, config):
                return SimpleNamespace(embeddings=[SimpleNamespace(values=[1.0, 0.0]) for _ in contents])

        with patch("google.genai.Client", _FakeClient), \
             patch.dict(os.environ, {"GEMINI_API_KEY": "key-one"}):
            self.embed.embed_texts(["a", "b"])
            self.embed.embed_text("c")
            threads = [threading.Thread(target=self.embed.embed_text, args=("d",)) for _ in range(4)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()
            self.assertEqual(built, ["key-one"])
            with patch.dict(os.environ, {"GEMINI_API_KEY": "key-two"}):
                self.embed.embed_text("e")
        self.assertEqual(built, ["key-one", "key-two"])

    def test_openai_compatible_client_is_shared_and_failures_stay_visible(self) -> None:
        built: list[tuple[str, str | None]] = []

        class _FakeOpenAI:
            def __init__(self, api_key: str, base_url: str | None) -> None:
                built.append((api_key, base_url))
                self.embeddings = self

            def create(self, *, model, input):
                raise RuntimeError("quota exceeded")

        with patch.object(self.embed, "_SMRITI_EMBED_PROVIDER", "mimo"), \
             patch("openai.OpenAI", _FakeOpenAI), \
             patch.dict(os.environ, {"MIMO_API_KEY": "mimo-key", "MIMO_BASE_URL": "https://mimo.test"}):
            for _ in range(2):
                self.embed._embed_unavailable_until = 0.0
                with self.assertRaises(self.embed.EmbeddingUnavailableError):
                    self.embed.embed_text("no silent fallback")
        self.assertEqual(built, [("mimo-key", "https://mimo.test")])

    def test_recall_runs_the_embedding_plane_off_the_loop(self) -> None:
        smriti_core = importlib.import_module("smriti_core")
        smriti_v2 = importlib.import_module("smriti_v2")
        threads: list[str] = []

        def _slow_semantic(**_kwargs):
            threads.append(threading.current_thread().name)
            time.sleep(0.8)  # the query embedding HTTP call
            return {"text": "", "provenance": [], "compaction_applied": []}

        with patch.object(smriti_core, "build_semantic_memory_context", _slow_semantic), \
             patch.object(smriti_core, "_vector_memory_tier_diagnostics", lambda *_: {}), \
             patch.object(smriti_core, "_load_jsonl", lambda *_: []), \
             patch.object(smriti_v2, "get_project_context", AsyncMock(return_value="")):
            packet, max_gap, _ = asyncio.run(_run_while_ticking(
                smriti_core.recall_context("what did we plan for March?", user_id="default")
            ))
        self.assertEqual(packet["context"], "")
        self.assertEqual(len(threads), 1)
        self.assertNotEqual(threads[0], threading.main_thread().name)
        self.assertLess(max_gap, _MAX_GAP_S)


class BrowseUrlThreadTests(unittest.TestCase):
    def test_browse_url_sync_works_from_an_offloaded_worker_thread(self) -> None:
        seen: list[bool] = []

        async def _fake_browse(url: str, extract: str = "text") -> dict:
            seen.append(threading.current_thread() is threading.main_thread())
            return {"status": "ok", "url": url, "extract": extract}

        async def _scenario():
            return await asyncio.to_thread(browser_skill.browse_url_sync, "https://example.com", "links")

        with patch.object(browser_skill, "browse_url", _fake_browse):
            result = asyncio.run(_scenario())
        self.assertEqual(result, {"status": "ok", "url": "https://example.com", "extract": "links"})
        self.assertEqual(seen, [False])


def _sleepy(delay_s: float, calls: list[str]):
    def _call(**kwargs):
        calls.append(threading.current_thread().name)
        time.sleep(delay_s)
        return {"id": "made", **{key: value for key, value in kwargs.items() if key == "user_id"}}
    return _call


class LearningEndpointOffloadTests(unittest.TestCase):
    """Guru's LLM calls (up to 3 × 120 s) must never run on the one event loop."""

    def test_learning_api_llm_calls_run_off_the_loop(self) -> None:
        api = importlib.import_module("learning_workspace_api")
        calls: list[str] = []
        workspace = {"topic": "photosynthesis"}
        scenarios = [
            ("create_learning_artifact", lambda: api.post_learning_artifact(
                api.LearningArtifactCreate(workspace_id="w1", topic="photosynthesis", artifact_type="flashcards"))),
            ("update_learning_artifact", lambda: api.post_learning_artifact_update(
                "a1", api.LearningArtifactUpdate(instruction="add a card about chlorophyll", workspace_id="w1"))),
            ("generate_syllabus", lambda: api.post_learning_syllabus("w1", api.SyllabusGenerate(topic="light"))),
            ("grade_check_answer", lambda: api.post_learning_check("w1", api.CheckAnswer(atom_id="a", answer="x"))),
        ]
        for name, call in scenarios:
            with self.subTest(name), patch.object(api, name, _sleepy(0.8, calls)), \
                 patch.object(api, "load_workspace", lambda **_: workspace):
                _, max_gap, _ = asyncio.run(_run_while_ticking(call()))
                self.assertLess(max_gap, _MAX_GAP_S)
        with patch.object(api.guided_mode, "submit_answer", _sleepy(0.8, calls)), \
             patch.object(api, "_sync_guided_checkpoint", lambda *a, **k: None):
            _, max_gap, _ = asyncio.run(_run_while_ticking(
                api.post_guided_answer(api.GuidedAnswer(workspace_id="w1", answer="x"))))
        self.assertLess(max_gap, _MAX_GAP_S)
        self.assertEqual(len(calls), 5)
        self.assertNotIn(threading.main_thread().name, calls)


class HealthProbeTests(unittest.TestCase):
    """/health is unauthenticated: probes run off the loop and are shared."""

    def setUp(self) -> None:
        import server

        self.server = server
        self.probes = 0
        server.app.state.runtime_contract = None
        for name, value in (("_contract_probe", None), ("_contract_probed_at", 0.0)):
            patcher = patch.object(server, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.addCleanup(setattr, server.app.state, "runtime_contract", None)

    def _slow_probe(self) -> dict:
        self.probes += 1
        time.sleep(0.8)  # cua-driver + bsk subprocesses and HTTP checks
        return self._contract

    def test_concurrent_health_checks_share_one_off_loop_probe(self) -> None:
        import httpx

        self._contract = {
            "status": "healthy",
            "build": {"phase": "p", "label": "l", "runtime_mode": "local"},
            "architecture": {"model": "m", "canonical_agent_count": 4, "agent_names": []},
            "local_ready": {"local_model_runtime": False},
            "issue_count": 0,
        }

        async def _burst():
            transport = httpx.ASGITransport(app=self.server.app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                responses = await asyncio.gather(*(client.get("/health") for _ in range(6)))
                cached = await client.get("/capabilities")
            return responses, cached

        with patch.object(self.server, "collect_runtime_contract", self._slow_probe):
            (responses, cached), max_gap, _ = asyncio.run(_run_while_ticking(_burst()))
            self.assertEqual([response.status_code for response in responses], [200] * 6)
            self.assertEqual(responses[0].json()["architecture"]["canonical_agent_count"], 4)
            self.assertEqual(cached.json()["status"], "healthy")
            self.assertEqual(self.probes, 1)  # one probe for seven requests
            self.assertLess(max_gap, _MAX_GAP_S)

            self.server.app.state.runtime_contract = None  # a key or grant changed
            asyncio.run(self.server._runtime_contract_snapshot())
            self.assertEqual(self.probes, 2)


class ToolPoolIsolationTests(unittest.TestCase):
    def test_long_tools_never_starve_the_default_executor(self) -> None:
        wrapped = avatar_agents._run_off_loop(_slow_lookup)

        async def _scenario():
            loop = asyncio.get_running_loop()
            small = concurrent.futures.ThreadPoolExecutor(max_workers=2)
            loop.set_default_executor(small)
            tools = [asyncio.ensure_future(wrapped(query=f"q{index}")) for index in range(4)]
            await asyncio.sleep(0.1)  # every tool is now on a worker
            started = time.monotonic()
            await asyncio.to_thread(lambda: None)  # a hot-path offload (recall, capture…)
            waited = time.monotonic() - started
            results = await asyncio.gather(*tools)
            return waited, results

        waited, results = asyncio.run(_scenario())
        self.assertLess(waited, 0.5)
        self.assertTrue(all(result["thread"].startswith("narad-tool") for result in results))


class BrowserLoopStartupRaceTests(unittest.TestCase):
    def test_concurrent_first_calls_start_exactly_one_browser_loop(self) -> None:
        import computer_use_skill

        real_new_loop = asyncio.new_event_loop

        def _slow_new_loop():
            time.sleep(0.02)  # a slower machine: the window between start and ready
            return real_new_loop()

        manager = computer_use_skill.BrowserSessionManager()
        barrier = threading.Barrier(6)
        loops: list[object] = []

        def _first_call():
            barrier.wait()
            manager._ensure_thread()
            loops.append(manager._loop)

        with patch.object(computer_use_skill.asyncio, "new_event_loop", _slow_new_loop):
            workers = [threading.Thread(target=_first_call) for _ in range(6)]
            for worker in workers:
                worker.start()
            for worker in workers:
                worker.join()
        runtime_threads = [thread for thread in threading.enumerate() if thread.name == "narad-browser-runtime"]
        try:
            self.assertEqual(len({id(loop) for loop in loops}), 1)
            self.assertIs(loops[0], manager._loop)
        finally:
            for loop in {id(item): item for item in [*loops, manager._loop] if item is not None}.values():
                loop.call_soon_threadsafe(loop.stop)
            for thread in runtime_threads:
                thread.join(timeout=5)


class VectorManifestRaceTests(unittest.TestCase):
    def test_parallel_upserts_into_one_manifest_all_survive(self) -> None:
        store = importlib.import_module("smriti_vector_store")
        with tempfile.TemporaryDirectory() as tmp, \
             patch.object(store, "SMRITI_MANIFEST_DIR", Path(tmp) / "manifests"), \
             patch.object(store, "SMRITI_VECTOR_DIR", Path(tmp) / "vectors"):

            def _record(index: int) -> object:
                return store.VectorMemoryRecord(
                    record_id=f"r{index:04d}", namespace="episodic_summary", tier="hot", user_id="asha",
                    project_id="", source_kind="episode", source_path="", source_ref="",
                    created_at=f"2026-09-{1 + index // 100:02d}T00:00:{index % 60:02d}",
                    updated_at=f"2026-09-{1 + index // 100:02d}T00:00:{index % 60:02d}",
                    preview="p" * 40, text="t" * 400, content_hash=str(index), embedding_model="m", dim=4,
                    embedding=[0.1, 0.2, 0.3, 0.4],
                )

            store.sync_records(user_id="asha", namespace="episodic_summary", records=[_record(i) for i in range(300)])
            barrier = threading.Barrier(4)

            def _capture(index: int) -> None:
                barrier.wait()
                store.upsert_record(_record(900 + index))

            workers = [threading.Thread(target=_capture, args=(index,)) for index in range(4)]
            for worker in workers:
                worker.start()
            for worker in workers:
                worker.join()
            manifest = store._manifest_path("asha", "episodic_summary", "hot", "m")
            self.assertEqual(len(store._load_records(manifest)), 304)
            self.assertEqual(list(manifest.parent.glob("*.tmp")), [])


if __name__ == "__main__":
    unittest.main()
