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
    for var in ("NARAD_BRAIN", "DEEPSEEK_API_KEY", "XAI_API_KEY", "NARAD_DISABLED_PROVIDERS"):
        monkeypatch.delenv(var, raising=False)
    # Never touch real stored credentials or the network from tests.
    monkeypatch.setattr(model_config, "_hydrate_stored_credentials", lambda: None)
    monkeypatch.setenv("NARAD_BRAIN_PROBE", "off")


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


def test_defaults_to_deepseek_without_any_credentials(monkeypatch):
    monkeypatch.setattr(model_config, "_grok_available", lambda: False)
    assert model_config.resolve_brain()[0] == "deepseek"


def test_rejected_deepseek_key_flips_to_grok_and_disables_provider(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-rejected")
    monkeypatch.setenv("XAI_API_KEY", "xai-token")
    monkeypatch.setattr(model_config, "_deepseek_key_rejected", lambda key: True)
    brain, reason = model_config.resolve_brain()
    assert brain == "grok"
    assert "rejected" in reason
    assert model_registry.provider_available_for_model("deepseek/deepseek-v4-pro") is False


def test_working_deepseek_key_keeps_deepseek(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-good")
    monkeypatch.setenv("XAI_API_KEY", "xai-token")
    monkeypatch.setattr(model_config, "_deepseek_key_rejected", lambda key: False)
    assert model_config.resolve_brain()[0] == "deepseek"


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
