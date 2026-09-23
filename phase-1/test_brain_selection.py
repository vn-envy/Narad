"""Brain resolution — DeepSeek first, xAI/Grok never (owner policy, 2026-09-23).

resolve_brain() decides the fleet's provider when NARAD_BRAIN is unset:
DeepSeek only when its key exists and the API accepts it, then another
connected provider with a published API data policy (Gemini, OpenAI,
Anthropic, a custom OpenAI-compatible endpoint), then local Gemma. An
XAI_API_KEY, a stored Grok sign-in, NARAD_BRAIN=grok or an `xai/*` override
never routes to xAI. A rejected DeepSeek key also disables the provider so
context-escalation fallbacks and restored sessions never route back into it.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import subprocess
import sys
from pathlib import Path

_r = next(p for p in Path(__file__).resolve().parents if (p / "narad_paths.py").exists())
sys.path[:0] = [str(_r)]  # narad root hop
import model_config  # noqa: E402
import model_registry  # noqa: E402
import pytest  # noqa: E402

import narad_paths  # noqa: F401  — registers all phase dirs; must precede phase imports

_REAL_HYDRATE = model_config._hydrate_stored_credentials


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for var in (
        "NARAD_BRAIN",
        "NARAD_MODEL",
        "MATSYA_MODEL",
        "RAMA_MODEL",
        "KRISHNA_MODEL",
        "PARASHURAMA_MODEL",
        "KRISHNA_VISUAL_MODEL",
        "GROK_MODEL",
        "DEEPSEEK_API_KEY",
        "XAI_API_KEY",
        "GEMINI_API_KEY",
        "GOOGLE_API_KEY",
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
        "MIMO_API_KEY",
        "VISION_MODEL",
        "VISION_BASE_URL",
        "VISION_API_KEY",
        "KRISHNA_VISION_MODEL",
        "MATSYA_VISION_MODEL",
        "NARAD_ENDPOINT_URL",
        "NARAD_ENDPOINT_MODEL",
        "NARAD_ENDPOINT_API_KEY",
        "NARAD_ENDPOINT_MULTIMODAL",
        "NARAD_CONTEXT_FALLBACKS",
        "NARAD_DISABLED_PROVIDERS",
    ):
        monkeypatch.delenv(var, raising=False)
    # Never touch real stored credentials or the network from tests.
    monkeypatch.setattr(model_config, "_hydrate_stored_credentials", lambda: None)
    monkeypatch.setenv("NARAD_BRAIN_PROBE", "off")
    monkeypatch.setattr(model_config, "_local_gemma_ready", lambda: True)
    monkeypatch.setattr(model_config, "_XAI_POLICY_WARNED", set())


def _forbid_xai_oauth(monkeypatch):
    """Fail loudly if routing calls into the Grok OAuth module at all."""
    import xai_oauth

    def _boom(*_args, **_kwargs):
        raise AssertionError("routing must not call into xai_oauth")

    for name in ("apply_to_env", "ensure_runtime_token", "get_access_token", "signed_in"):
        monkeypatch.setattr(xai_oauth, name, _boom)


def test_explicit_brain_wins_but_grok_is_ignored(monkeypatch, caplog):
    monkeypatch.setenv("NARAD_BRAIN", "deepseek")
    monkeypatch.setenv("XAI_API_KEY", "xai-token")
    assert model_config.resolve_brain()[0] == "deepseek"

    monkeypatch.setenv("NARAD_BRAIN", "grok")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-good")
    monkeypatch.setattr(model_config, "_deepseek_key_rejected", lambda key: False)
    with caplog.at_level(logging.WARNING, logger="narad.models"):
        assert model_config.resolve_brain()[0] == "deepseek"
        assert model_config.resolve_brain()[0] == "deepseek"
    warnings = [r.getMessage() for r in caplog.records if "NARAD_BRAIN=grok" in r.getMessage()]
    assert len(warnings) == 1
    assert "disabled by owner policy" in warnings[0]

    monkeypatch.setenv("NARAD_BRAIN", "xai")
    monkeypatch.delenv("DEEPSEEK_API_KEY")
    assert model_config.resolve_brain()[0] == "local"


def test_xai_key_without_deepseek_falls_to_next_connected_provider(monkeypatch):
    monkeypatch.setenv("XAI_API_KEY", "xai-token")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-openai")
    brain, reason = model_config.resolve_brain()
    assert brain == "openai"
    assert "Grok" not in reason
    model, _ = model_config.resolve_orchestrator_model()
    assert model_registry.detect_provider(model) == "openai"
    assert model_registry.detect_provider(model_config.get_avatar_model("matsya")) == "openai"


def test_xai_key_alone_falls_to_local_gemma(monkeypatch):
    monkeypatch.setenv("XAI_API_KEY", "xai-token")
    assert model_config.resolve_brain()[0] == "local"
    model, reason = model_config.resolve_orchestrator_model()
    assert model.startswith("ollama/gemma4:")
    assert "zero-key" in reason
    assert model_config.get_avatar_model("rama").startswith("ollama/gemma4:")


def test_stored_grok_sign_in_is_never_consulted_for_routing(monkeypatch):
    import kunji

    _forbid_xai_oauth(monkeypatch)
    monkeypatch.setattr(kunji, "apply_keys_to_env", lambda: [])
    monkeypatch.setattr(model_config, "_hydrate_stored_credentials", _REAL_HYDRATE)
    monkeypatch.setenv("XAI_API_KEY", "xai-oauth-token-exported-at-startup")

    assert model_config.resolve_brain()[0] == "local"
    assert model_config.resolve_orchestrator_model()[0].startswith("ollama/gemma4:")
    endpoint = model_config.get_vision_endpoint("matsya")
    assert endpoint is not None and endpoint.provider == "local"
    assert model_registry.provider_available_for_model("xai/grok-4.6") is False


def test_defaults_to_local_gemma_without_any_credentials(monkeypatch):
    assert model_config.resolve_brain()[0] == "local"
    model, reason = model_config.resolve_orchestrator_model()
    assert model.startswith("ollama/gemma4:e2b")
    assert "zero-key" in reason


def test_default_chain_is_deepseek_first_even_with_grok_connected(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-good")
    monkeypatch.setenv("XAI_API_KEY", "xai-token")
    monkeypatch.setenv("GEMINI_API_KEY", "AIza-test")
    monkeypatch.setattr(model_config, "_deepseek_key_rejected", lambda key: False)
    assert model_config.resolve_brain()[0] == "deepseek"
    assert model_config.get_avatar_model("narad") == model_config.DS_FLASH
    assert model_config.get_avatar_model("matsya") == model_config.DS_FLASH
    assert model_config.get_avatar_model("rama") == model_config.DS_PRO


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


def test_vision_never_picks_xai(monkeypatch):
    monkeypatch.setenv("XAI_API_KEY", "xai-token")
    monkeypatch.setenv("VISION_MODEL", "xai/grok-4.6")
    monkeypatch.setenv("GEMINI_API_KEY", "AIza-test")
    endpoint = model_config.get_vision_endpoint("matsya")
    assert endpoint is not None
    assert endpoint.provider == "google"

    monkeypatch.setenv("VISION_MODEL", "grok-vision")
    monkeypatch.setenv("VISION_BASE_URL", "https://api.x.ai/v1")
    monkeypatch.delenv("GEMINI_API_KEY")
    endpoint = model_config.get_vision_endpoint("matsya")
    assert endpoint is not None
    assert endpoint.provider == "local"


def test_multimodal_uses_the_connected_deepseek_endpoint(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-good")
    monkeypatch.setattr(model_config, "_deepseek_key_rejected", lambda key: False)
    endpoint = model_config.get_vision_endpoint("matsya")
    assert endpoint is not None
    assert endpoint.model == model_config.DS_FLASH
    assert endpoint.provider == "deepseek"
    assert endpoint.source == "active avatar endpoint"


def test_rejected_deepseek_falls_to_next_connected_provider_not_grok(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-rejected")
    monkeypatch.setenv("XAI_API_KEY", "xai-token")
    monkeypatch.setenv("GEMINI_API_KEY", "AIza-test")
    monkeypatch.setattr(model_config, "_deepseek_key_rejected", lambda key: True)
    model, reason = model_config.resolve_orchestrator_model()
    assert model == "gemini/gemini-2.5-flash"
    assert "DeepSeek unavailable" in reason
    assert "Grok" not in reason
    assert model_config.resolve_brain()[0] == "google"
    assert model_registry.provider_available_for_model("deepseek/deepseek-v4-pro") is False


def test_rejected_deepseek_with_only_grok_falls_to_local(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-rejected")
    monkeypatch.setenv("XAI_API_KEY", "xai-token")
    monkeypatch.setattr(model_config, "_deepseek_key_rejected", lambda key: True)
    assert model_config.resolve_orchestrator_model()[0].startswith("ollama/gemma4:")
    assert model_config.resolve_brain()[0] == "local"


def test_custom_endpoint_is_the_last_connected_provider(monkeypatch):
    monkeypatch.setenv("NARAD_ENDPOINT_URL", "http://127.0.0.1:9000/v1")
    monkeypatch.setenv("NARAD_ENDPOINT_MODEL", "my-model")
    assert model_config.resolve_brain() == ("custom", "connected custom endpoint")
    assert model_config.get_avatar_model("narad") == "openai/my-model"
    assert model_config.get_avatar_model("krishna") == "openai/my-model"
    assert model_registry.provider_available_for_model("openai/my-model") is True

    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    assert model_config.resolve_brain()[0] == "anthropic"


def test_custom_endpoint_on_xai_is_ignored(monkeypatch):
    monkeypatch.setenv("NARAD_ENDPOINT_URL", "https://api.x.ai/v1")
    monkeypatch.setenv("NARAD_ENDPOINT_MODEL", "grok-4.6")
    assert model_registry.custom_endpoint_model() == ""
    assert model_config.resolve_brain()[0] == "local"


def test_xai_model_overrides_are_ignored(monkeypatch, caplog):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-good")
    monkeypatch.setattr(model_config, "_deepseek_key_rejected", lambda key: False)
    monkeypatch.setenv("NARAD_MODEL", "xai/grok-4.6")
    monkeypatch.setenv("MATSYA_MODEL", "xai/grok-4.6")
    monkeypatch.setenv("KRISHNA_VISUAL_MODEL", "grok-4.6")
    with caplog.at_level(logging.WARNING, logger="narad.models"):
        assert model_config.get_avatar_model("narad") == model_config.DS_FLASH
        assert model_config.get_avatar_model("matsya") == model_config.DS_FLASH
        assert model_config.get_avatar_model("matsya") == model_config.DS_FLASH
        assert model_config.get_visual_output_model("krishna")[0] == model_config.DS_FLASH
    ignored = [r.getMessage() for r in caplog.records if "MATSYA_MODEL=" in r.getMessage()]
    assert len(ignored) == 1


def test_escalation_never_routes_to_xai(monkeypatch):
    monkeypatch.setenv("XAI_API_KEY", "xai-token")
    monkeypatch.setenv("NARAD_CONTEXT_FALLBACKS", "xai/grok-4.6,gemini/gemini-2.5-pro")
    model_registry.get_model_profile.cache_clear()
    try:
        profile = model_registry.get_model_profile("deepseek/deepseek-flash")
        assert profile.larger_window_fallbacks == ["gemini/gemini-2.5-pro"]
        assert model_registry.select_escalation("openai/gpt-4o-mini", required_input_tokens=200_000) is None
    finally:
        model_registry.get_model_profile.cache_clear()


def test_grok_settings_at_import_warn_once_and_never_route(tmp_path):
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.endswith(("_API_KEY", "_MODEL")) and not key.startswith(("NARAD_", "GROK_"))
    }
    env.update({
        "NARAD_HOME": str(tmp_path),
        "KUNJI_BACKEND": "file",
        "NARAD_BRAIN_PROBE": "off",
        "NARAD_BRAIN": "grok",
        "GROK_MODEL": "xai/grok-4.6",
        "XAI_API_KEY": "xai-token",
        "DEEPSEEK_API_KEY": "sk-good",
        "PYTHONPATH": os.pathsep.join([str(_r), str(_r / "phase-1")]),
    })
    script = (
        "import logging, sys\n"
        "logging.basicConfig(level=logging.WARNING, stream=sys.stdout, format='%(message)s')\n"
        "import narad_paths\n"
        "import model_config\n"
        "print('MODELS', sorted(set(model_config.AVATAR_MODELS.values())))\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=str(_r),
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, result.stderr
    lines = result.stdout.splitlines()
    assert sum("GROK_MODEL ignored" in line for line in lines) == 1
    assert sum("NARAD_BRAIN=grok ignored" in line for line in lines) == 1
    models_line = next(line for line in lines if line.startswith("MODELS"))
    assert "grok" not in models_line and "xai/" not in models_line
    assert "deepseek" in models_line


def test_capability_report_shows_xai_disabled_by_policy(monkeypatch):
    from runtime_contract import provider_status

    monkeypatch.setenv("XAI_API_KEY", "xai-token")
    xai = provider_status()["xai"]
    assert xai["available"] is False
    assert xai["disabled_by_policy"] is True
    assert xai["credential_present"] is True
    assert "owner policy" in xai["reason"]


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
