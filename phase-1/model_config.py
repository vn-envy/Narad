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
  GROK_MODEL      — Grok via xAI OAuth / XAI_API_KEY (default xai/grok-4.3)

Whole-brain switch: NARAD_BRAIN=grok flips all tier defaults onto Grok
(sign in with SuperGrok / X Premium+ in Settings → Connections). Per-avatar
vars still win, so mixed DeepSeek+Grok fleets stay one-line changes.

When NARAD_BRAIN is unset the brain resolves itself: DeepSeek if its key
exists AND the API accepts it, otherwise Grok when signed in. A rejected
DeepSeek key (401/403, verdict cached on disk) also disables the provider
for context-escalation fallbacks — signing into Grok is enough; no .env
edit required.

Switching any avatar to a local model, OpenAI, or Claude is a one-line .env change.
Example: KRISHNA_MODEL=ollama/llama3  or  KRISHNA_MODEL=claude-opus-4-7

Eval result (phase-0a, 2026-05-02):
  DeepSeek routing accuracy: 93.0% weighted (GPT-4o: 84.0%)
  DeepSeek beats GPT-4o by 9pp with 0 parse errors → single-API consolidation confirmed.
"""
from __future__ import annotations

import logging
import os

log = logging.getLogger("narad.models")

DS_PRO   = os.environ.get("DS_PRO_MODEL",   "deepseek/deepseek-v4-pro")
DS_FLASH = os.environ.get("DS_FLASH_MODEL", "deepseek/deepseek-v4-flash")
GROK     = os.environ.get("GROK_MODEL",     "xai/grok-4.3")


# ── Brain resolution ──────────────────────────────────────────────────────────
# NARAD_BRAIN=grok flips every tier default onto Grok (via xAI OAuth or
# XAI_API_KEY). Per-avatar env vars still override individually — so mixed
# fleets (e.g. Grok brain + DeepSeek Flash for retrieval) stay one-liners.
#
# When NARAD_BRAIN is unset, the brain picks itself so a Grok sign-in "just
# works": DeepSeek only when its key exists and the API accepts it, else Grok.

def _hydrate_stored_credentials() -> None:
    """Stored Kunji keys + Grok OAuth token → env (idempotent; .env wins).

    The server startup event does this too, but brain resolution happens at
    import — before the event fires — so hydrate here as well.
    """
    try:
        from kunji import apply_keys_to_env
        apply_keys_to_env()
    except Exception:
        pass
    try:
        import xai_oauth
        xai_oauth.apply_to_env()
    except Exception:
        pass


def _grok_available() -> bool:
    if os.environ.get("XAI_API_KEY", "").strip():
        return True
    try:
        from xai_oauth import signed_in
        return signed_in()
    except Exception:
        return False


def _probe_cache_path():
    from pathlib import Path
    try:
        from narad_config import CONFIG_DIR
        return CONFIG_DIR / "deepseek_key_probe.json"
    except Exception:
        return Path.home() / ".narad" / "config" / "deepseek_key_probe.json"


def _deepseek_key_rejected(key: str) -> bool:
    """True only when the API has positively rejected this key (401/403).

    One cheap GET against /user/balance, verdict cached on disk by key
    fingerprint: a rejected key stays rejected until it changes; a valid
    verdict is trusted for 24h. Network trouble never flips the brain.
    Kill switch: NARAD_BRAIN_PROBE=off skips the network entirely.
    """
    if os.environ.get("NARAD_BRAIN_PROBE", "").strip().lower() in {"0", "off", "false"}:
        return False
    import hashlib
    import json
    import time

    fp = hashlib.sha256(key.encode()).hexdigest()[:16]
    path = _probe_cache_path()
    try:
        cache = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(cache, dict):
            cache = {}
    except Exception:
        cache = {}
    entry = cache.get(fp) or {}
    if entry.get("valid") is False:
        return True
    if entry.get("valid") is True and time.time() - float(entry.get("ts", 0)) < 86_400:
        return False
    try:
        import httpx
        base = os.environ.get("DEEPSEEK_API_BASE", "https://api.deepseek.com").rstrip("/")
        resp = httpx.get(
            f"{base}/user/balance",
            headers={"Authorization": f"Bearer {key}"},
            timeout=4.0,
        )
    except Exception:
        return False  # can't tell — keep the configured brain
    rejected = resp.status_code in (401, 403)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        cache[fp] = {"valid": not rejected, "ts": time.time()}
        path.write_text(json.dumps(cache), encoding="utf-8")
    except Exception:
        pass
    return rejected


def _disable_provider(provider: str) -> None:
    """Mark a provider unusable process-wide (model_registry honours this)."""
    current = {
        p.strip().lower()
        for p in os.environ.get("NARAD_DISABLED_PROVIDERS", "").split(",")
        if p.strip()
    }
    current.add(provider)
    os.environ["NARAD_DISABLED_PROVIDERS"] = ",".join(sorted(current))


def resolve_brain() -> tuple[str, str]:
    """→ (brain, reason) with brain ∈ {"deepseek", "grok"}."""
    explicit = os.environ.get("NARAD_BRAIN", "").strip().lower()
    if explicit in {"grok", "xai"}:
        return "grok", "NARAD_BRAIN=grok"
    if explicit:
        return "deepseek", f"NARAD_BRAIN={explicit}"
    _hydrate_stored_credentials()
    ds_key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
    if not ds_key:
        if _grok_available():
            return "grok", "no DeepSeek key — using the Grok sign-in"
        return "deepseek", "default"
    if _grok_available() and _deepseek_key_rejected(ds_key):
        _disable_provider("deepseek")
        return "grok", "DeepSeek rejected its key (401) — using the Grok sign-in instead"
    return "deepseek", "DeepSeek key present"


_BRAIN, _BRAIN_REASON = resolve_brain()
if _BRAIN == "grok":
    _TIER_PRO, _TIER_FLASH = GROK, GROK
    if os.environ.get("NARAD_BRAIN", "").strip():
        log.info("Brain: %s (%s)", GROK, _BRAIN_REASON)
    else:
        log.warning("Brain: %s (%s)", GROK, _BRAIN_REASON)
else:
    _TIER_PRO, _TIER_FLASH = DS_PRO, DS_FLASH
    log.info("Brain: %s (%s)", DS_PRO, _BRAIN_REASON)

# Public tier aliases — follow the resolved brain. (DS_PRO / DS_FLASH always
# name the DeepSeek models; use these when "same provider as the brain" is
# what you actually mean.)
TIER_PRO, TIER_FLASH = _TIER_PRO, _TIER_FLASH

AVATAR_MODELS = {
    "narad":       os.environ.get("NARAD_MODEL",       _TIER_FLASH),  # fast routing dispatch, not multi-turn reasoning
    "matsya":      os.environ.get("MATSYA_MODEL",      _TIER_FLASH),  # retrieval, analysis, synthesis, local access
    "rama":        os.environ.get("RAMA_MODEL",        _TIER_PRO),    # planning, calendar, personal data lifecycle
    "krishna":     os.environ.get("KRISHNA_MODEL",     _TIER_FLASH),  # communication, creation, wellness
    "parashurama": os.environ.get("PARASHURAMA_MODEL", _TIER_PRO),    # code, systems, quantitative modeling
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
    if "grok" in m or "xai" in m:
        return "xai"
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
    "xai":       256_000,
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
# Used to bump Krishna onto the brain's pro tier for visual artifact generation
# while keeping the same provider instead of swapping into Mimo.

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

    Visual output tasks stay on the brain's own pro tier to avoid
    cross-provider auth failures mid-turn. A per-avatar override remains
    possible via {AVATAR_NAME}_VISUAL_MODEL.
    """
    override = os.environ.get(f"{avatar_name.upper()}_VISUAL_MODEL", "")
    return (override or TIER_PRO, None)


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
