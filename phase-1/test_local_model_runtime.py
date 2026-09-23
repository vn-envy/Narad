from __future__ import annotations

import sys
from pathlib import Path

import pytest

_ROOT = next(path for path in Path(__file__).resolve().parents if (path / "narad_paths.py").exists())
sys.path[:0] = [str(_ROOT)]
import local_model_runtime  # noqa: E402
from local_model_runtime import LocalModelRuntime, local_completion_options  # noqa: E402

import narad_paths  # noqa: E402, F401


def test_under_16gb_selects_e2b(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("NARAD_LOCAL_MODEL", raising=False)
    monkeypatch.setattr(local_model_runtime, "_ram_gb", lambda: 8.0)
    assert local_model_runtime.default_model_tag() == "gemma4:e2b-it-q4_K_M"


def test_16gb_and_above_selects_e4b(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("NARAD_LOCAL_MODEL", raising=False)
    monkeypatch.setattr(local_model_runtime, "_ram_gb", lambda: 16.0)
    assert local_model_runtime.default_model_tag() == "gemma4:e4b-it-q4_K_M"


def test_binary_alone_is_not_reported_as_model_ready(monkeypatch: pytest.MonkeyPatch) -> None:
    runtime = LocalModelRuntime()
    monkeypatch.setattr(local_model_runtime.shutil, "which", lambda _name: "/opt/bin/ollama")

    def unavailable(_path: str, **_kwargs):
        raise OSError("offline")

    monkeypatch.setattr(runtime, "_request_json", unavailable)
    status = runtime.probe(force=True)
    assert status["runtime_installed"] is True
    assert status["reachable"] is False
    assert status["ready"] is False


def test_reachable_runtime_requires_the_exact_model(monkeypatch: pytest.MonkeyPatch) -> None:
    runtime = LocalModelRuntime()
    monkeypatch.setattr(local_model_runtime, "default_model_tag", lambda: "gemma4:e2b-it-q4_K_M")
    monkeypatch.setattr(local_model_runtime.shutil, "which", lambda _name: "/opt/bin/ollama")

    def request(path: str, **_kwargs):
        if path == "/api/version":
            return {"version": "0.30.8"}
        return {"models": [{"name": "gemma4:e2b-it-q4_K_M"}]}

    monkeypatch.setattr(runtime, "_request_json", request)
    status = runtime.probe(force=True)
    assert status["ready"] is True
    assert status["model_size"] == "E2B"
    assert status["optimized_variant"] == "q4-k-m"
    assert status["supports"]["native_tools"] is True
    assert status["supports"]["computer_use"] is True


def test_gemma_completion_options_apply_context_and_sampling(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NARAD_LOCAL_CONTEXT_TOKENS", "32768")
    options = local_completion_options("ollama/gemma4:e2b-it-q4_K_M")
    assert options["api_base"] == "http://127.0.0.1:11434"
    assert options["num_ctx"] == 32768
    assert options["temperature"] == 1.0
    assert options["top_p"] == 0.95
    assert options["top_k"] == 64


def test_non_gemma_ollama_models_do_not_receive_gemma_sampling() -> None:
    assert local_completion_options("ollama/qwen3:8b") == {}
