from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

_ROOT = next(path for path in Path(__file__).resolve().parents if (path / "narad_paths.py").exists())
sys.path[:0] = [str(_ROOT)]
import narad_litellm  # noqa: E402
from google.adk.models.lite_llm import LiteLlm  # noqa: E402
from google.adk.models.llm_request import LlmRequest  # noqa: E402
from google.adk.models.llm_response import LlmResponse  # noqa: E402
from narad_litellm import NaradLiteLlm, completion_options  # noqa: E402

import narad_paths  # noqa: E402, F401


def test_grok_uses_priority_passthrough_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GROK_SERVICE_TIER", raising=False)
    monkeypatch.delenv("NARAD_XAI_TIMEOUT_S", raising=False)
    options = completion_options("xai/grok-4.6")
    assert options["service_tier"] == "priority"
    assert "service_tier" in options["allowed_openai_params"]
    assert options["timeout"] == 90.0
    assert options["num_retries"] == 0


def test_reasoning_effort_is_opt_in(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GROK_REASONING_EFFORT", raising=False)
    assert "reasoning_effort" not in completion_options("xai/grok-4.6")
    monkeypatch.setenv("GROK_REASONING_EFFORT", "low")
    assert completion_options("xai/grok-4.6")["reasoning_effort"] == "low"


def test_non_xai_models_receive_no_xai_options() -> None:
    assert completion_options("deepseek/deepseek-flash") == {}


def test_adapter_carries_priority_options() -> None:
    model = NaradLiteLlm(model="xai/grok-4.6")
    assert model._additional_args["service_tier"] == "priority"
    assert "service_tier" in model._additional_args["allowed_openai_params"]


def test_adapter_carries_local_gemma_runtime_options(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NARAD_LOCAL_CONTEXT_TOKENS", "32768")
    model = NaradLiteLlm(model="ollama/gemma4:e2b-it-q4_K_M")
    assert model._additional_args["api_base"] == "http://127.0.0.1:11434"
    assert model._additional_args["num_ctx"] == 32768
    assert model._additional_args["top_k"] == 64


def test_grok_timeout_fails_over_before_first_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    async def fake_generate(self, llm_request, stream=False):
        calls.append(self.model)
        if self.model == "xai/grok-4.6":
            raise TimeoutError("Connection timed out")
        yield LlmResponse(model_version=self.model)

    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
    monkeypatch.delenv("NARAD_DISABLED_PROVIDERS", raising=False)
    monkeypatch.setattr(narad_litellm, "ensure_model_credentials", lambda _model: None)
    monkeypatch.setattr(LiteLlm, "generate_content_async", fake_generate)
    narad_litellm._close_xai_circuit()

    model = NaradLiteLlm(model="xai/grok-4.6")
    async def collect():
        return [item async for item in model.generate_content_async(LlmRequest())]

    responses = asyncio.run(collect())

    assert calls == ["xai/grok-4.6", "deepseek/deepseek-flash"]
    assert responses[0].model_version == "deepseek/deepseek-flash"
    assert narad_litellm.xai_resilience_status()["circuit_open"] is True
    narad_litellm._close_xai_circuit()


def test_grok_timeout_does_not_replay_after_partial_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    async def fake_generate(self, llm_request, stream=False):
        calls.append(self.model)
        yield LlmResponse(model_version=self.model, partial=True)
        raise TimeoutError("late stream timeout")

    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
    monkeypatch.delenv("NARAD_DISABLED_PROVIDERS", raising=False)
    monkeypatch.setattr(narad_litellm, "ensure_model_credentials", lambda _model: None)
    monkeypatch.setattr(LiteLlm, "generate_content_async", fake_generate)
    narad_litellm._close_xai_circuit()

    model = NaradLiteLlm(model="xai/grok-4.6")
    async def collect():
        return [item async for item in model.generate_content_async(LlmRequest())]

    with pytest.raises(TimeoutError):
        asyncio.run(collect())

    assert calls == ["xai/grok-4.6"]
    narad_litellm._close_xai_circuit()


def test_open_xai_circuit_routes_directly_to_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    async def fake_generate(self, llm_request, stream=False):
        calls.append(self.model)
        yield LlmResponse(model_version=self.model)

    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
    monkeypatch.delenv("NARAD_DISABLED_PROVIDERS", raising=False)
    monkeypatch.setattr(narad_litellm, "ensure_model_credentials", lambda _model: None)
    monkeypatch.setattr(LiteLlm, "generate_content_async", fake_generate)
    narad_litellm._open_xai_circuit(TimeoutError("timeout"))

    model = NaradLiteLlm(model="xai/grok-4.6")
    async def collect():
        return [item async for item in model.generate_content_async(LlmRequest())]

    asyncio.run(collect())

    assert calls == ["deepseek/deepseek-flash"]
    narad_litellm._close_xai_circuit()


def test_cloud_auth_failure_can_fall_back_to_installed_local_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    async def fake_generate(self, llm_request, stream=False):
        calls.append(self.model)
        if self.model == "deepseek/deepseek-flash":
            raise RuntimeError("AuthenticationError: Invalid API Key")
        yield LlmResponse(model_version=self.model)

    monkeypatch.setattr(
        narad_litellm,
        "_offline_fallback_model",
        lambda _model: "ollama/gemma4:e2b-it-q4_K_M",
    )
    monkeypatch.setattr(LiteLlm, "generate_content_async", fake_generate)
    model = NaradLiteLlm(model="deepseek/deepseek-flash")

    async def collect():
        return [item async for item in model.generate_content_async(LlmRequest())]

    responses = asyncio.run(collect())
    assert calls == ["deepseek/deepseek-flash", "ollama/gemma4:e2b-it-q4_K_M"]
    assert responses[0].model_version == "ollama/gemma4:e2b-it-q4_K_M"
