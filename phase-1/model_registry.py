from __future__ import annotations

import math
import os
import shutil
from dataclasses import asdict, dataclass
from functools import lru_cache
from typing import Any


@dataclass(frozen=True)
class ModelProfile:
    model: str
    provider: str
    max_context_tokens: int
    reserved_output_tokens: int
    hard_input_budget_tokens: int
    safe_input_budget_tokens: int
    soft_target_tokens: int
    supports_prompt_cache: bool
    supports_native_compaction: bool
    supports_provider_token_count: bool
    larger_window_fallbacks: list[str]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


_PROVIDER_DEFAULTS: dict[str, int] = {
    "anthropic": 200_000,
    "openai": 128_000,
    "deepseek": 128_000,
    "google": 1_000_000,
    "local": 32_000,
    "unknown": 32_000,
}

# LiteLLM's context map can lag provider reality. Narad keeps explicit overrides
# for models we have seen mismatch in live traffic.
_MODEL_OVERRIDES: dict[str, int] = {
    "deepseek/deepseek-v4-flash": 1_048_565,
    "deepseek/deepseek-v4-pro": 1_048_565,
    "deepseek-v4-flash": 1_048_565,
    "deepseek-v4-pro": 1_048_565,
    "gemini/gemini-2.5-pro": 1_048_576,
    "gemini/gemini-2.5-flash": 1_048_576,
    "gemini/gemini-3-flash-preview": 1_048_576,
}

_PROMPT_CACHE_PROVIDERS = {"anthropic", "openai", "google"}
_NATIVE_COMPACTION_PROVIDERS = {"openai"}
_PROVIDER_TOKEN_COUNT_PROVIDERS = {"google"}


def detect_provider(model: str) -> str:
    lower = (model or "").lower()
    if "deepseek" in lower:
        return "deepseek"
    if "gemini" in lower or "google" in lower:
        return "google"
    if "gpt" in lower or "openai" in lower or "o1" in lower or "o3" in lower:
        return "openai"
    if "claude" in lower or "anthropic" in lower:
        return "anthropic"
    if "ollama" in lower or "localhost" in lower or "127.0.0.1" in lower:
        return "local"
    return "unknown"


def provider_available_for_model(model: str) -> bool:
    provider = detect_provider(model)
    if provider == "deepseek":
        return bool(os.environ.get("DEEPSEEK_API_KEY", "").strip())
    if provider == "google":
        return bool(os.environ.get("GEMINI_API_KEY", "").strip() or os.environ.get("GOOGLE_API_KEY", "").strip())
    if provider == "openai":
        return bool(os.environ.get("OPENAI_API_KEY", "").strip())
    if provider == "anthropic":
        return bool(os.environ.get("ANTHROPIC_API_KEY", "").strip())
    if provider == "local":
        return bool(os.environ.get("OLLAMA_HOST", "").strip() or shutil.which("ollama"))
    return False


def _litellm_max_tokens(model: str) -> int | None:
    try:
        from litellm import get_max_tokens

        value = get_max_tokens(model)
        if isinstance(value, int) and value > 0:
            return value
    except Exception:
        return None
    return None


def _fallback_candidates(model: str) -> list[str]:
    provider = detect_provider(model)
    configured = [
        item.strip()
        for item in os.environ.get("NARAD_CONTEXT_FALLBACKS", "").split(",")
        if item.strip()
    ]
    if configured:
        return configured

    deepseek_defaults = [
        os.environ.get("GOOGLE_CONTEXT_FALLBACK_MODEL", "gemini/gemini-2.5-pro"),
        os.environ.get("ANTHROPIC_CONTEXT_FALLBACK_MODEL", "claude-sonnet-4-6"),
        os.environ.get("OPENAI_CONTEXT_FALLBACK_MODEL", "gpt-4o"),
    ]
    openai_defaults = [
        os.environ.get("ANTHROPIC_CONTEXT_FALLBACK_MODEL", "claude-sonnet-4-6"),
        os.environ.get("GOOGLE_CONTEXT_FALLBACK_MODEL", "gemini/gemini-2.5-pro"),
    ]
    google_defaults = [
        os.environ.get("GOOGLE_CONTEXT_FALLBACK_MODEL", "gemini/gemini-2.5-pro"),
        os.environ.get("ANTHROPIC_CONTEXT_FALLBACK_MODEL", "claude-sonnet-4-6"),
    ]
    anthropic_defaults = [
        os.environ.get("GOOGLE_CONTEXT_FALLBACK_MODEL", "gemini/gemini-2.5-pro"),
        os.environ.get("OPENAI_CONTEXT_FALLBACK_MODEL", "gpt-4o"),
    ]
    local_defaults = [
        os.environ.get("GOOGLE_CONTEXT_FALLBACK_MODEL", "gemini/gemini-2.5-pro"),
        os.environ.get("DEEPSEEK_CONTEXT_FALLBACK_MODEL", "deepseek/deepseek-v4-pro"),
    ]

    options = {
        "deepseek": deepseek_defaults,
        "openai": openai_defaults,
        "google": google_defaults,
        "anthropic": anthropic_defaults,
        "local": local_defaults,
        "unknown": deepseek_defaults,
    }
    return [candidate for candidate in options.get(provider, []) if candidate and candidate != model]


@lru_cache(maxsize=128)
def get_model_profile(model: str, *, long_running: bool = False) -> ModelProfile:
    provider = detect_provider(model)
    override = _MODEL_OVERRIDES.get((model or "").lower())
    max_context_tokens = override or _litellm_max_tokens(model) or _PROVIDER_DEFAULTS[provider]
    reserved_output_tokens = max(4096, math.ceil(max_context_tokens * 0.10))
    hard_input_budget_tokens = max(1024, max_context_tokens - reserved_output_tokens)
    soft_ratio = 0.70 if long_running else 0.80
    soft_target_tokens = max(1024, int(hard_input_budget_tokens * soft_ratio))
    safe_input_budget_tokens = soft_target_tokens
    larger_window_fallbacks = [
        candidate
        for candidate in _fallback_candidates(model)
        if candidate != model
    ]
    return ModelProfile(
        model=model,
        provider=provider,
        max_context_tokens=max_context_tokens,
        reserved_output_tokens=reserved_output_tokens,
        hard_input_budget_tokens=hard_input_budget_tokens,
        safe_input_budget_tokens=safe_input_budget_tokens,
        soft_target_tokens=soft_target_tokens,
        supports_prompt_cache=provider in _PROMPT_CACHE_PROVIDERS,
        supports_native_compaction=provider in _NATIVE_COMPACTION_PROVIDERS,
        supports_provider_token_count=provider in _PROVIDER_TOKEN_COUNT_PROVIDERS,
        larger_window_fallbacks=larger_window_fallbacks,
    )


def select_escalation(model: str, *, required_input_tokens: int) -> ModelProfile | None:
    current = get_model_profile(model, long_running=True)
    for candidate in current.larger_window_fallbacks:
        if not provider_available_for_model(candidate):
            continue
        profile = get_model_profile(candidate, long_running=True)
        if profile.hard_input_budget_tokens >= required_input_tokens:
            return profile
    return None


def context_policy_payload(models: dict[str, str]) -> dict[str, Any]:
    per_agent: dict[str, Any] = {}
    fallback_graph: dict[str, list[str]] = {}
    for actor, model in models.items():
        profile = get_model_profile(model, long_running=True)
        per_agent[actor] = profile.to_dict()
        fallback_graph[actor] = profile.larger_window_fallbacks
    return {
        "overflow_policy": "compact_then_escalate",
        "fidelity_policy": "lossless_artifacts",
        "profiles": per_agent,
        "fallback_graph": fallback_graph,
    }
