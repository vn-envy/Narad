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
from narad_litellm import NaradLiteLlm, completion_options, ensure_model_credentials  # noqa: E402

import narad_paths  # noqa: E402, F401


@pytest.fixture(autouse=True)
def _no_custom_endpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in ("NARAD_ENDPOINT_URL", "NARAD_ENDPOINT_MODEL", "NARAD_ENDPOINT_API_KEY"):
        monkeypatch.delenv(var, raising=False)


def _forbid_xai_oauth(monkeypatch: pytest.MonkeyPatch) -> None:
    import xai_oauth

    def _boom(*_args, **_kwargs):
        raise AssertionError("routing must not call into xai_oauth")

    for name in ("apply_to_env", "ensure_runtime_token", "get_access_token"):
        monkeypatch.setattr(xai_oauth, name, _boom)


def _collect(model: NaradLiteLlm) -> list[LlmResponse]:
    async def collect():
        return [item async for item in model.generate_content_async(LlmRequest())]

    return asyncio.run(collect())


def test_hosted_models_receive_no_extra_options() -> None:
    assert completion_options("deepseek/deepseek-flash") == {}
    assert completion_options("xai/grok-4.6") == {}


def test_custom_endpoint_gets_its_own_base_and_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NARAD_ENDPOINT_URL", "http://127.0.0.1:9000/v1")
    monkeypatch.setenv("NARAD_ENDPOINT_MODEL", "my-model")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-openai-must-not-leak")
    options = completion_options("openai/my-model")
    assert options == {"api_base": "http://127.0.0.1:9000/v1", "api_key": "not-needed"}
    monkeypatch.setenv("NARAD_ENDPOINT_API_KEY", "endpoint-token")
    model = NaradLiteLlm(model="openai/my-model")
    assert model._additional_args["api_base"] == "http://127.0.0.1:9000/v1"
    assert model._additional_args["api_key"] == "endpoint-token"


def test_xai_credentials_are_refused_by_policy(monkeypatch: pytest.MonkeyPatch) -> None:
    _forbid_xai_oauth(monkeypatch)
    monkeypatch.setenv("XAI_API_KEY", "xai-token")
    with pytest.raises(RuntimeError, match="owner policy"):
        ensure_model_credentials("xai/grok-4.6")
    ensure_model_credentials("deepseek/deepseek-flash")


def test_adapter_carries_local_gemma_runtime_options(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NARAD_LOCAL_CONTEXT_TOKENS", "32768")
    model = NaradLiteLlm(model="ollama/gemma4:e2b-it-q4_K_M")
    assert model._additional_args["api_base"] == "http://127.0.0.1:11434"
    assert model._additional_args["num_ctx"] == 32768
    assert model._additional_args["top_k"] == 64


def test_xai_model_is_served_by_the_default_chain_without_calling_xai(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    async def fake_generate(self, llm_request, stream=False):
        calls.append(self.model)
        yield LlmResponse(model_version=self.model)

    _forbid_xai_oauth(monkeypatch)
    monkeypatch.setenv("XAI_API_KEY", "xai-token")
    monkeypatch.setattr(narad_litellm, "_policy_replacement_model", lambda: "deepseek/deepseek-flash")
    monkeypatch.setattr(narad_litellm, "_offline_fallback_model", lambda _model: "")
    monkeypatch.setattr(LiteLlm, "generate_content_async", fake_generate)

    responses = _collect(NaradLiteLlm(model="xai/grok-4.6"))

    assert calls == ["deepseek/deepseek-flash"]
    assert responses[0].model_version == "deepseek/deepseek-flash"


def test_xai_model_without_a_replacement_fails_with_policy_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    async def fake_generate(self, llm_request, stream=False):
        calls.append(self.model)
        yield LlmResponse(model_version=self.model)

    monkeypatch.setattr(narad_litellm, "_policy_replacement_model", lambda: "")
    monkeypatch.setattr(LiteLlm, "generate_content_async", fake_generate)

    with pytest.raises(RuntimeError, match="owner policy"):
        _collect(NaradLiteLlm(model="xai/grok-4.6"))
    assert calls == []


def test_policy_replacement_is_deepseek_even_with_xai_key(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in ("NARAD_MODEL", "NARAD_DISABLED_PROVIDERS"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("NARAD_BRAIN_PROBE", "off")
    import model_config

    monkeypatch.setattr(model_config, "_hydrate_stored_credentials", lambda: None)
    monkeypatch.setenv("XAI_API_KEY", "xai-token")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-good")
    assert narad_litellm._policy_replacement_model() == model_config.DS_FLASH
    monkeypatch.setenv("NARAD_MODEL", "xai/grok-4.6")
    assert narad_litellm._policy_replacement_model() == model_config.DS_FLASH


def test_timeout_fails_over_to_local_before_first_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    async def fake_generate(self, llm_request, stream=False):
        calls.append(self.model)
        if self.model == "deepseek/deepseek-flash":
            raise TimeoutError("Connection timed out")
        yield LlmResponse(model_version=self.model)

    monkeypatch.setattr(
        narad_litellm,
        "_offline_fallback_model",
        lambda model: "" if model.startswith("ollama/") else "ollama/gemma4:e2b-it-q4_K_M",
    )
    monkeypatch.setattr(LiteLlm, "generate_content_async", fake_generate)

    responses = _collect(NaradLiteLlm(model="deepseek/deepseek-flash"))

    assert calls == ["deepseek/deepseek-flash", "ollama/gemma4:e2b-it-q4_K_M"]
    assert responses[0].model_version == "ollama/gemma4:e2b-it-q4_K_M"


def test_timeout_does_not_replay_after_partial_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    async def fake_generate(self, llm_request, stream=False):
        calls.append(self.model)
        yield LlmResponse(model_version=self.model, partial=True)
        raise TimeoutError("late stream timeout")

    monkeypatch.setattr(
        narad_litellm,
        "_offline_fallback_model",
        lambda _model: "ollama/gemma4:e2b-it-q4_K_M",
    )
    monkeypatch.setattr(LiteLlm, "generate_content_async", fake_generate)

    with pytest.raises(TimeoutError):
        _collect(NaradLiteLlm(model="deepseek/deepseek-flash"))

    assert calls == ["deepseek/deepseek-flash"]


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
