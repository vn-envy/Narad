"""
Central model assignments for Phase 1.

Per-avatar overrides via environment variables (each falls back to tier default):
  NARAD_MODEL        — Narad router
  MATSYA_MODEL       — Matsya research/retrieval
  VARAHA_MODEL       — Varaha document analysis + finance quantitative
  NARASIMHA_MODEL    — Narasimha debugging
  RAMA_MODEL         — Rama planning + calendar
  KRISHNA_MODEL      — Krishna communication + education (guru mode) + finance advisory
  BUDDHA_MODEL       — Buddha analysis + safety gate
  PARASHURAMA_MODEL  — Parashurama code + media + UI creation
  VAMANA_MODEL       — Vamana filesystem + personal finance

Tier aliases (used as fallbacks when per-avatar var is unset):
  DS_CHAT_MODEL   — DeepSeek Chat / V3+ (heavy reasoning, code, analysis)
  DS_FLASH_MODEL  — DeepSeek V4 Flash (prose, synthesis, lighter tasks)

Switching any avatar to a local model, OpenAI, or Claude is a one-line .env change.
Example: KRISHNA_MODEL=ollama/llama3  or  KRISHNA_MODEL=claude-opus-4-7

Eval result (phase-0a, 2026-05-02):
  DeepSeek routing accuracy: 93.0% weighted (GPT-4o: 84.0%)
  DeepSeek beats GPT-4o by 9pp with 0 parse errors → single-API consolidation confirmed.
"""
from __future__ import annotations
import os

DS_CHAT  = os.environ.get("DS_CHAT_MODEL",  "deepseek/deepseek-chat")
DS_FLASH = os.environ.get("DS_FLASH_MODEL", "deepseek/deepseek-v4-flash")

AVATAR_MODELS = {
    "narad":       os.environ.get("NARAD_MODEL",       DS_CHAT),
    "matsya":      os.environ.get("MATSYA_MODEL",      DS_FLASH),
    "varaha":      os.environ.get("VARAHA_MODEL",      DS_CHAT),
    "narasimha":   os.environ.get("NARASIMHA_MODEL",   DS_CHAT),
    "rama":        os.environ.get("RAMA_MODEL",        DS_FLASH),
    "krishna":     os.environ.get("KRISHNA_MODEL",     DS_CHAT),   # upgraded: multi-phase skills need reasoning
    "buddha":      os.environ.get("BUDDHA_MODEL",      DS_CHAT),
    "parashurama": os.environ.get("PARASHURAMA_MODEL", DS_CHAT),
    "vamana":      os.environ.get("VAMANA_MODEL",      DS_FLASH),  # downgraded: simple data ops
}


# ── Capability detection (auto-derived from model name, never hardcoded) ──────

def _provider(model: str) -> str:
    m = model.lower()
    if "claude" in m or "anthropic" in m:
        return "anthropic"
    if "gpt" in m or "o1" in m or "o3" in m or "openai" in m:
        return "openai"
    if "gemini" in m or "google" in m:
        return "google"
    if "deepseek" in m:
        return "deepseek"
    if "ollama" in m or "localhost" in m or "127.0.0.1" in m:
        return "local"
    return "unknown"


# Extended/native thinking support: Anthropic claude-3-7+ only.
# All other providers: use <thinking>...</thinking> prompt-based chain-of-thought.
SUPPORTS_THINKING: dict[str, bool] = {
    name: _provider(model) == "anthropic"
    for name, model in AVATAR_MODELS.items()
}

# Context window in tokens — used by skills to decide how much context to inject.
_CTX: dict[str, int] = {
    "anthropic": 200_000,
    "openai":    128_000,
    "deepseek":  128_000,
    "google":  1_000_000,
    "local":      32_000,
    "unknown":    32_000,
}
CONTEXT_WINDOW: dict[str, int] = {
    name: _CTX[_provider(model)]
    for name, model in AVATAR_MODELS.items()
}

# Prompt caching: Anthropic tokens cached at ~50% cost reduction.
# DeepSeek handles prefix caching automatically — no annotation required.
# Flag lets skill code inject cache_control when switched to Anthropic.
SUPPORTS_PROMPT_CACHE: dict[str, bool] = {
    name: _provider(model) == "anthropic"
    for name, model in AVATAR_MODELS.items()
}


def _detect_vision_model() -> tuple[str, str | None]:
    """Return (model_string, api_base_or_None) for best available vision provider."""
    if os.environ.get("MIMO_API_KEY"):
        model = os.environ.get("MIMO_MODEL", "openai/mimo-2.5-pro")
        return model, os.environ.get("MIMO_BASE_URL")
    if os.environ.get("OPENAI_API_KEY"):
        return "gpt-4o", None
    if os.environ.get("ANTHROPIC_API_KEY"):
        return "claude-opus-4-7", None
    if os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY"):
        return "gemini/gemini-3.1-flash", None
    return "", None


_GLOBAL_VISION_MODEL = os.environ.get("VISION_MODEL", "")
_AUTO_VISION_MODEL, _AUTO_VISION_BASE = _detect_vision_model()


def get_vision_model(avatar_name: str) -> tuple[str, str | None]:
    """Return (model, api_base_or_None) for vision tasks, with per-avatar override support.

    Model name resolution: per-avatar env > VISION_MODEL global > auto-detected default.
    Base URL: always uses MIMO_BASE_URL when MIMO_API_KEY is present — regardless of where
    the model name came from. This lets VISION_MODEL override the model string while still
    routing through Mimo's endpoint.
    """
    per_avatar = os.environ.get(f"{avatar_name.upper()}_VISION_MODEL", "")
    model = per_avatar or _GLOBAL_VISION_MODEL or _AUTO_VISION_MODEL
    # _AUTO_VISION_BASE is non-None only when MIMO_API_KEY is configured; apply it for any model
    base = _AUTO_VISION_BASE
    return model, base


def get_thinking_instructions(avatar_name: str) -> str:
    """Return a model-agnostic prompt fragment for structured chain-of-thought.

    Anthropic models with native thinking: returns '' — thinking activated via
    API parameter, not prompt text.
    All other models: injects <thinking>...</thinking> instruction so the model
    produces equivalent structured pre-response reasoning.
    """
    if SUPPORTS_THINKING.get(avatar_name, False):
        return ""
    return (
        "\nBefore responding, write your full reasoning process in "
        "<thinking>...</thinking> tags. Think through all possibilities "
        "before committing to an answer. Only what follows </thinking> "
        "is shown to the user.\n"
    )
