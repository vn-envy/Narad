"""Brain resolution — a Grok sign-in must be enough to run Narad.

resolve_brain() decides the fleet's provider when NARAD_BRAIN is unset:
DeepSeek only when its key exists and the API accepts it, otherwise the
Grok sign-in (xAI OAuth / XAI_API_KEY). A rejected DeepSeek key also
disables the provider so context-escalation fallbacks and restored
sessions never route back into it.
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

_r = next(p for p in Path(__file__).resolve().parents if (p / "narad_paths.py").exists())
sys.path[:0] = [str(_r)]  # narad root hop
import model_config  # noqa: E402
import model_registry  # noqa: E402
import pytest  # noqa: E402

import narad_paths  # noqa: F401  — registers all phase dirs; must precede phase imports


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for var in (
        "NARAD_BRAIN",
        "NARAD_MODEL",
        "MATSYA_MODEL",
        "DEEPSEEK_API_KEY",
        "XAI_API_KEY",
        "GEMINI_API_KEY",
        "GOOGLE_API_KEY",
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
        "VISION_MODEL",
        "VISION_BASE_URL",
        "VISION_API_KEY",
        "KRISHNA_VISION_MODEL",
        "MATSYA_VISION_MODEL",
        "NARAD_DISABLED_PROVIDERS",
    ):
        monkeypatch.delenv(var, raising=False)
    # Never touch real stored credentials or the network from tests.
    monkeypatch.setattr(model_config, "_hydrate_stored_credentials", lambda: None)
    monkeypatch.setattr(
        model_config,
        "_grok_available",
        lambda: bool(model_config.os.environ.get("XAI_API_KEY", "").strip()),
    )
    monkeypatch.setenv("NARAD_BRAIN_PROBE", "off")
    monkeypatch.setattr(model_config, "_local_gemma_ready", lambda: True)


def test_explicit_brain_always_wins(monkeypatch):
    monkeypatch.setenv("NARAD_BRAIN", "grok")
    assert model_config.resolve_brain()[0] == "grok"
    monkeypatch.setenv("NARAD_BRAIN", "deepseek")
    monkeypatch.setenv("XAI_API_KEY", "xai-token")
    assert model_config.resolve_brain()[0] == "deepseek"


def test_grok_sign_in_is_enough_without_deepseek_key(monkeypatch):
    monkeypatch.setenv("XAI_API_KEY", "xai-token")
    brain, reason = model_config.resolve_brain()
    assert brain == "grok"
    assert "Grok" in reason


def test_defaults_to_local_gemma_without_any_credentials(monkeypatch):
    monkeypatch.setattr(model_config, "_grok_available", lambda: False)
    assert model_config.resolve_brain()[0] == "local"
    model, reason = model_config.resolve_orchestrator_model()
    assert model.startswith("ollama/gemma4:e2b")
    assert "zero-key" in reason


def test_connected_deepseek_still_beats_local_fallback(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-good")
    monkeypatch.setattr(model_config, "_deepseek_key_rejected", lambda key: False)
    assert model_config.resolve_brain()[0] == "deepseek"
    assert model_config.resolve_orchestrator_model()[0] == model_config.DS_FLASH


def test_local_gemma_is_a_multimodal_endpoint(monkeypatch):
    monkeypatch.setenv("MATSYA_MODEL", "ollama/gemma4:e2b-it-q4_K_M")
    monkeypatch.setattr(model_config, "_local_gemma_ready", lambda: True)
    endpoint = model_config.get_vision_endpoint("matsya")
    assert endpoint is not None
    assert endpoint.model == "ollama/gemma4:e2b-it-q4_K_M"
    assert endpoint.provider == "local"


def test_vision_override_keeps_its_own_endpoint_credentials(monkeypatch):
    monkeypatch.setenv("VISION_MODEL", "my-vision-model")
    monkeypatch.setenv("VISION_BASE_URL", "http://127.0.0.1:9000/v1")
    monkeypatch.setenv("VISION_API_KEY", "custom-token")
    endpoint = model_config.get_vision_endpoint("krishna")
    assert endpoint is not None
    assert endpoint.source == "vision override"
    assert endpoint.api_key == "custom-token"
    assert endpoint.litellm_kwargs()["model"] == "openai/my-vision-model"


def test_multimodal_uses_the_connected_deepseek_endpoint(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-good")
    monkeypatch.setattr(model_config, "_grok_available", lambda: False)
    monkeypatch.setattr(model_config, "_deepseek_key_rejected", lambda key: False)
    endpoint = model_config.get_vision_endpoint("matsya")
    assert endpoint is not None
    assert endpoint.model == model_config.DS_FLASH
    assert endpoint.provider == "deepseek"
    assert endpoint.source == "active avatar endpoint"


def test_rejected_deepseek_orchestrator_falls_back_to_grok(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-rejected")
    monkeypatch.setenv("XAI_API_KEY", "xai-token")
    monkeypatch.setattr(model_config, "_deepseek_key_rejected", lambda key: True)
    model, reason = model_config.resolve_orchestrator_model()
    assert model == model_config.GROK
    assert "fallback" in reason
    assert model_registry.provider_available_for_model("deepseek/deepseek-v4-pro") is False


def test_default_split_keeps_deepseek_orchestrator_and_grok_workers(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-good")
    monkeypatch.setenv("XAI_API_KEY", "xai-token")
    monkeypatch.setattr(model_config, "_deepseek_key_rejected", lambda key: False)
    assert model_config.resolve_brain()[0] == "grok"
    assert model_config.get_avatar_model("narad") == model_config.DS_FLASH
    assert model_config.get_avatar_model("matsya") == model_config.GROK


def test_worker_brain_can_still_be_forced_to_deepseek(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-good")
    monkeypatch.setenv("XAI_API_KEY", "xai-token")
    monkeypatch.setenv("NARAD_BRAIN", "deepseek")
    assert model_config.get_avatar_model("matsya") == model_config.DS_FLASH
    assert model_config.get_avatar_model("rama") == model_config.DS_PRO


def test_probe_cache_remembers_a_rejected_key(tmp_path, monkeypatch):
    monkeypatch.delenv("NARAD_BRAIN_PROBE", raising=False)
    key = "sk-dead"
    fp = hashlib.sha256(key.encode()).hexdigest()[:16]
    cache = tmp_path / "deepseek_key_probe.json"
    cache.write_text(json.dumps({fp: {"valid": False, "ts": 0}}), encoding="utf-8")
    monkeypatch.setattr(model_config, "_probe_cache_path", lambda: cache)
    # Cached 401 verdict → rejected, no network call needed.
    assert model_config._deepseek_key_rejected(key) is True


def test_disabled_provider_gate(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-x")
    assert model_registry.provider_available_for_model("deepseek/deepseek-v4-pro") is True
    monkeypatch.setenv("NARAD_DISABLED_PROVIDERS", "deepseek")
    assert model_registry.provider_available_for_model("deepseek/deepseek-v4-pro") is False
