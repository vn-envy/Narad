"""
Central model assignments — Mahati Veena (4-string architecture).

Per-avatar overrides via environment variables (each falls back to tier default):
  NARAD_MODEL        — Narad router (Sa — orchestration)
  MATSYA_MODEL       — Matsya: retrieval, analysis, synthesis, local access
  RAMA_MODEL         — Rama: planning, calendar, personal data (finance + health)
  KRISHNA_MODEL      — Krishna: communication, creation, wellness
  PARASHURAMA_MODEL  — Parashurama: code, systems, quantitative modeling

Tier aliases (used as fallbacks when per-avatar var is unset):
  DS_PRO_MODEL    — DeepSeek V4 Pro (reasoning, planning, code, analysis)
  DS_FLASH_MODEL  — DeepSeek V4 Flash (fast retrieval, prose, lighter tasks)

Switching any avatar to a local model, OpenAI, or Claude is a one-line .env change.
Example: KRISHNA_MODEL=ollama/llama3  or  KRISHNA_MODEL=claude-opus-4-7

Eval result (phase-0a, 2026-05-02):
  DeepSeek routing accuracy: 93.0% weighted (GPT-4o: 84.0%)
  DeepSeek beats GPT-4o by 9pp with 0 parse errors → single-API consolidation confirmed.
"""
from __future__ import annotations
import os

DS_PRO   = os.environ.get("DS_PRO_MODEL",   "deepseek/deepseek-v4-pro")
DS_FLASH = os.environ.get("DS_FLASH_MODEL", "deepseek/deepseek-v4-flash")

AVATAR_MODELS = {
    "narad":       os.environ.get("NARAD_MODEL",       DS_FLASH),  # fast routing dispatch, not multi-turn reasoning
    "matsya":      os.environ.get("MATSYA_MODEL",      DS_FLASH),  # retrieval, analysis, synthesis, local access
    "rama":        os.environ.get("RAMA_MODEL",        DS_PRO),    # planning, calendar, personal data lifecycle
    "krishna":     os.environ.get("KRISHNA_MODEL",     DS_FLASH),  # communication, creation, wellness
    "parashurama": os.environ.get("PARASHURAMA_MODEL", DS_PRO),    # code, systems, quantitative modeling
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
    """Return (model_string, api_base_or_None) for best available vision provider.

    Priority: MiMo (MIMO_API_KEY) > OpenAI > Anthropic.
    Gemini removed — use only DeepSeek + Mimo stack.
    Used only when the user attaches images — visual output tasks stay on DeepSeek.
    """
    if os.environ.get("MIMO_API_KEY"):
        model = os.environ.get("MIMO_MODEL", "openai/mimo-v2.5")
        return model, os.environ.get("MIMO_BASE_URL")
    if os.environ.get("OPENAI_API_KEY"):
        return "gpt-4o", None
    if os.environ.get("ANTHROPIC_API_KEY"):
        return "claude-opus-4-7", None
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


# ── Visual output task detection (UI / PPT / HTML deck generation) ────────────
# Used to bump Krishna onto DeepSeek V4 Pro for visual artifact generation while
# keeping the normal provider path on DeepSeek instead of swapping into Mimo.

_VISUAL_OUTPUT_KEYWORDS: frozenset[str] = frozenset({
    "slide deck", "slides", "presentation", "pitch deck", "ppt",
    "mockup", "wireframe", "ui design", "ux design", "html deck",
    "landing page", "web page", "website", "dashboard design",
})

# Video tasks use Veo / moviepy — NEVER route to Gemini Flash LLM.
# This guard overrides any incidental "presentation" / "slides" wording
# that Narad's routing LLM might include when framing a video task.
_VIDEO_OVERRIDE_KEYWORDS: frozenset[str] = frozenset({
    "video", "clip", "mp4", "cinematic", "film", "footage",
    "animation", "animate", "explainer", "veo", "moviepy",
})

def is_visual_output_task(task: str) -> bool:
    t = task.lower()
    if any(kw in t for kw in _VIDEO_OVERRIDE_KEYWORDS):
        return False  # video tasks stay on DeepSeek and call Veo / create_video() tools
    return any(kw in t for kw in _VISUAL_OUTPUT_KEYWORDS)


def get_visual_output_model(avatar_name: str) -> tuple[str, str | None]:
    """Return (model, api_base_or_None) for visual output generation tasks.

    Visual output tasks stay on DeepSeek by default to avoid cross-provider auth
    failures mid-turn. A per-avatar override remains possible via
    {AVATAR_NAME}_VISUAL_MODEL if we ever want a different same-provider path.
    """
    override = os.environ.get(f"{avatar_name.upper()}_VISUAL_MODEL", "")
    return (override or DS_PRO, None)


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
