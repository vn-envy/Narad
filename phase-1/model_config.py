"""
Central model assignments — Mahati Veena (4-string architecture).

Per-avatar overrides via environment variables (each falls back to tier default):
  NARAD_MODEL        — Narad router (Sa — orchestration)
  MATSYA_MODEL       — Matsya: retrieval, analysis, synthesis, local access
  RAMA_MODEL         — Rama: planning, calendar, personal data (finance + health)
  KRISHNA_MODEL      — Krishna: communication, creation, wellness
  PARASHURAMA_MODEL  — Parashurama: code, systems, quantitative modeling

Default chain (orchestrator and the four avatar workers):
  DeepSeek (`DS_FLASH` / `DS_PRO`) → another connected provider with a
  published API data policy (Gemini API, OpenAI API, Anthropic API, or a
  custom OpenAI-compatible endpoint) → managed local Gemma 4.

Tier aliases (used as fallbacks when per-avatar vars are unset):
  DS_FLASH_MODEL  — DeepSeek V4.1 Flash API alias
  DS_PRO_MODEL    — DeepSeek fallback for legacy pro-tier callers

Owner policy (2026-09-23): xAI/Grok is out. NARAD_BRAIN=grok/xai, GROK_MODEL
and per-avatar `xai/*` overrides are ignored with one warning each, and an
XAI_API_KEY or stored Grok sign-in never routes a request to xAI.

`NARAD_BRAIN` controls the worker fleet only. The orchestrator stays on
DeepSeek unless `NARAD_MODEL` is explicitly set or DeepSeek is unavailable.

Switching any avatar to a local model, OpenAI, or Claude is a one-line .env change.
Example: KRISHNA_MODEL=ollama/llama3  or  KRISHNA_MODEL=claude-opus-4-7

Eval result (phase-0a, 2026-05-02):
  DeepSeek routing accuracy: 93.0% weighted (GPT-4o: 84.0%)
  DeepSeek beats GPT-4o by 9pp with 0 parse errors → single-API consolidation confirmed.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass

from model_registry import custom_endpoint_model, disabled_by_policy

log = logging.getLogger("narad.models")

DS_FLASH = os.environ.get("DS_FLASH_MODEL", "deepseek/deepseek-flash")
# V4.1 Flash supersedes the V4 Pro generation and is the safer compatibility
# default while keeping DS_PRO_MODEL as an explicit escape hatch.
DS_PRO = os.environ.get("DS_PRO_MODEL", DS_FLASH)

_XAI_POLICY_WARNED: set[str] = set()


def _warn_xai_ignored(setting: str) -> None:
    """Log once per setting that owner policy keeps xAI/Grok out of routing."""
    if setting in _XAI_POLICY_WARNED:
        return
    _XAI_POLICY_WARNED.add(setting)
    log.warning(
        "%s ignored: xAI/Grok is disabled by owner policy; Narad routes to "
        "DeepSeek, then other connected providers, then local Gemma",
        setting,
    )


def _model_override(env_name: str) -> str:
    """An explicit model env var, unless owner policy rules its provider out."""
    value = os.environ.get(env_name, "").strip()
    if value and disabled_by_policy(value):
        _warn_xai_ignored(f"{env_name}={value}")
        return ""
    return value


def _local_gemma_model() -> str:
    try:
        from local_model_runtime import local_model_id

        return local_model_id()
    except Exception:
        return "ollama/gemma4:e2b-it-q4_K_M"


def _local_gemma_ready() -> bool:
    try:
        from local_model_runtime import local_model_ready

        return local_model_ready()
    except Exception:
        return False


# ── Brain resolution ──────────────────────────────────────────────────────────
# NARAD_BRAIN selects the four worker defaults. Narad itself has an independent
# orchestration default so one provider failure cannot silently move the whole
# fleet mid-turn.

def _hydrate_stored_credentials() -> None:
    """Stored Kunji keys → env (idempotent; .env wins).

    The server startup event does this too, but brain resolution happens at
    import — before the event fires — so hydrate here as well. A stored Grok
    sign-in is deliberately not consulted: xAI is out of routing by policy.
    """
    try:
        from kunji import apply_keys_to_env
        apply_keys_to_env()
    except Exception:
        pass


def _deepseek_available() -> bool:
    key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
    if not key:
        return False
    if _deepseek_key_rejected(key):
        _disable_provider("deepseek")
        return False
    return True


def _connected_provider_fallback() -> tuple[str, str, str] | None:
    """Return a connected non-DeepSeek provider's model before falling back local.

    Only providers with published API data-handling terms are candidates;
    xAI never is.
    """
    candidates = (
        (
            "google",
            ("GEMINI_API_KEY", "GOOGLE_API_KEY"),
            os.environ.get("GEMINI_MODEL", "gemini/gemini-2.5-flash"),
        ),
        (
            "openai",
            ("OPENAI_API_KEY",),
            os.environ.get("OPENAI_MODEL", "openai/gpt-4o-mini"),
        ),
        (
            "anthropic",
            ("ANTHROPIC_API_KEY",),
            os.environ.get("ANTHROPIC_MODEL", "anthropic/claude-sonnet-4-6"),
        ),
    )
    for provider, key_names, model in candidates:
        if any(os.environ.get(name, "").strip() for name in key_names):
            return provider, model, f"connected {provider} credential"
    custom = custom_endpoint_model()
    if custom:
        return "custom", custom, "connected custom endpoint"
    return None


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
    """Resolve workers: DeepSeek, other connected providers, then local Gemma."""
    explicit = os.environ.get("NARAD_BRAIN", "").strip().lower()
    if explicit in {"grok", "xai"}:
        _warn_xai_ignored(f"NARAD_BRAIN={explicit}")
        explicit = ""
    if explicit in {"deepseek", "ds"}:
        return "deepseek", f"NARAD_BRAIN={explicit}"
    if explicit in {"local", "offline", "gemma", "gemma4"}:
        return "local", f"NARAD_BRAIN={explicit}"
    if explicit in {"google", "gemini", "openai", "anthropic", "claude"}:
        normalized = "google" if explicit == "gemini" else "anthropic" if explicit == "claude" else explicit
        return normalized, f"NARAD_BRAIN={explicit}"
    _hydrate_stored_credentials()
    if _deepseek_available():
        return "deepseek", "connected DeepSeek credential"
    configured = _connected_provider_fallback()
    if configured:
        return configured[0], configured[2]
    readiness = "ready" if _local_gemma_ready() else "download required"
    return "local", f"zero-key Gemma 4 edge fallback ({readiness})"


def resolve_orchestrator_model() -> tuple[str, str]:
    """Resolve Narad independently, ending on a zero-key local model."""
    explicit = _model_override("NARAD_MODEL")
    if explicit:
        return explicit, "NARAD_MODEL override"
    _hydrate_stored_credentials()
    if _deepseek_available():
        return DS_FLASH, "DeepSeek V4.1 Flash orchestrator"
    configured = _connected_provider_fallback()
    if configured:
        return configured[1], f"DeepSeek unavailable — {configured[2]}"
    readiness = "ready" if _local_gemma_ready() else "download required"
    return _local_gemma_model(), f"zero-key Gemma 4 edge orchestrator ({readiness})"


def _worker_models_for_brain(brain: str) -> tuple[str, str]:
    if brain == "deepseek":
        return DS_PRO, DS_FLASH
    if brain == "local":
        local = _local_gemma_model()
        return local, local
    configured = _connected_provider_fallback()
    if configured and configured[0] == brain:
        return configured[1], configured[1]
    local = _local_gemma_model()
    return local, local


if os.environ.get("GROK_MODEL", "").strip():
    _warn_xai_ignored("GROK_MODEL")
_BRAIN, _BRAIN_REASON = resolve_brain()
_ORCHESTRATOR_MODEL, _ORCHESTRATOR_REASON = resolve_orchestrator_model()
_TIER_PRO, _TIER_FLASH = _worker_models_for_brain(_BRAIN)
log.info("Worker fleet: %s (%s)", _TIER_PRO, _BRAIN_REASON)
log.info("Orchestrator: %s (%s)", _ORCHESTRATOR_MODEL, _ORCHESTRATOR_REASON)

# Public tier aliases — follow the resolved brain. (DS_PRO / DS_FLASH always
# name the DeepSeek models; use these when "same provider as the brain" is
# what you actually mean.)
TIER_PRO, TIER_FLASH = _TIER_PRO, _TIER_FLASH

AVATAR_MODELS = {
    "narad":       _ORCHESTRATOR_MODEL,                                # routing and synthesis
    "matsya":      _model_override("MATSYA_MODEL")      or _TIER_FLASH,  # retrieval, analysis, synthesis, local access
    "rama":        _model_override("RAMA_MODEL")        or _TIER_PRO,    # planning, calendar, personal data lifecycle
    "krishna":     _model_override("KRISHNA_MODEL")     or _TIER_FLASH,  # communication, creation, wellness
    "parashurama": _model_override("PARASHURAMA_MODEL") or _TIER_PRO,    # code, systems, quantitative modeling
}

_AVATAR_MODEL_ENV = {
    "narad": "NARAD_MODEL",
    "matsya": "MATSYA_MODEL",
    "rama": "RAMA_MODEL",
    "krishna": "KRISHNA_MODEL",
    "parashurama": "PARASHURAMA_MODEL",
}


def get_avatar_model(avatar_name: str) -> str:
    """Return the live model assignment, including post-login OAuth changes."""
    name = avatar_name.strip().lower()
    env_name = _AVATAR_MODEL_ENV.get(name)
    override = _model_override(env_name) if env_name else ""
    if override:
        return override
    if name == "narad":
        return resolve_orchestrator_model()[0]
    brain, _ = resolve_brain()
    pro, flash = _worker_models_for_brain(brain)
    return pro if name in {"rama", "parashurama"} else flash


def refresh_avatar_models() -> dict[str, str]:
    """Refresh the public snapshot used by capabilities and new sessions."""
    for name in AVATAR_MODELS:
        AVATAR_MODELS[name] = get_avatar_model(name)
    return dict(AVATAR_MODELS)


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


# Native reasoning support. Do not ask these providers to print chain-of-thought
# into normal message content; their APIs carry reasoning separately.
SUPPORTS_THINKING: dict[str, bool] = {
    name: _provider(model) in {"anthropic", "deepseek", "xai"} or "gemma4" in model.lower()
    for name, model in AVATAR_MODELS.items()
}

# Context window in tokens — used by skills to decide how much context to inject.
_CTX: dict[str, int] = {
    "anthropic": 200_000,
    "openai":    128_000,
    "deepseek": 1_048_565,
    "google":  1_000_000,
    "xai":       500_000,
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


@dataclass(frozen=True)
class ModelEndpoint:
    model: str
    provider: str
    source: str
    api_base: str | None = None
    api_key: str | None = None

    def litellm_kwargs(self) -> dict[str, str]:
        model = self.model
        if self.api_base and "/" not in model:
            model = f"openai/{model}"
        result = {"model": model}
        if self.api_base:
            result["api_base"] = self.api_base
        if self.api_key:
            result["api_key"] = self.api_key
        return result


_VISION_MODEL_HINTS = (
    "gemma4", "grok", "gemini", "gpt-4", "gpt-5", "claude", "deepseek", "mimo",
    "vision", "vl", "pixtral", "llava", "qwen2.5-vl", "qwen3-vl",
)


def _model_supports_images(model: str) -> bool:
    lower = (model or "").lower()
    return any(hint in lower for hint in _VISION_MODEL_HINTS)


def _endpoint_for_model(model: str, *, source: str) -> ModelEndpoint | None:
    if model and model == custom_endpoint_model():
        return ModelEndpoint(
            model,
            "custom",
            source,
            os.environ.get("NARAD_ENDPOINT_URL", "").strip(),
            os.environ.get("NARAD_ENDPOINT_API_KEY", "").strip() or None,
        )
    provider = _provider(model)
    if provider == "local":
        if not _local_gemma_ready():
            return None
        try:
            from local_model_runtime import local_runtime_status

            return ModelEndpoint(
                model=model,
                provider="local",
                source=source,
                api_base=str(local_runtime_status().get("url") or "http://127.0.0.1:11434"),
            )
        except Exception:
            return ModelEndpoint(model=model, provider="local", source=source)
    if provider == "deepseek" and _deepseek_available():
        return ModelEndpoint(model=model, provider=provider, source=source)
    if provider == "google" and (os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")):
        return ModelEndpoint(model=model, provider=provider, source=source)
    if provider == "openai" and os.environ.get("OPENAI_API_KEY"):
        return ModelEndpoint(model=model, provider=provider, source=source)
    if provider == "anthropic" and os.environ.get("ANTHROPIC_API_KEY"):
        return ModelEndpoint(model=model, provider=provider, source=source)
    return None


def get_vision_endpoint(avatar_name: str) -> ModelEndpoint | None:
    """Choose a healthy multimodal endpoint rather than a hardcoded provider."""
    _hydrate_stored_credentials()
    upper = avatar_name.upper()
    override = (
        os.environ.get(f"{upper}_VISION_MODEL", "").strip()
        or os.environ.get("VISION_MODEL", "").strip()
    )
    base = (
        os.environ.get(f"{upper}_VISION_BASE_URL", "").strip()
        or os.environ.get("VISION_BASE_URL", "").strip()
        or None
    )
    if override and disabled_by_policy(override, base or ""):
        _warn_xai_ignored(f"vision override {override}")
        override = ""
    if override:
        key = (
            os.environ.get(f"{upper}_VISION_API_KEY", "").strip()
            or os.environ.get("VISION_API_KEY", "").strip()
            or None
        )
        if base:
            return ModelEndpoint(override, "custom", "vision override", base, key)
        endpoint = _endpoint_for_model(override, source="vision override")
        if endpoint:
            return endpoint

    custom_model = os.environ.get("NARAD_ENDPOINT_MODEL", "").strip()
    custom_base = os.environ.get("NARAD_ENDPOINT_URL", "").strip()
    custom_multimodal = os.environ.get("NARAD_ENDPOINT_MULTIMODAL", "").strip().lower()
    if custom_endpoint_model() and custom_multimodal in {"1", "true", "yes", "on"}:
        return ModelEndpoint(
            custom_model,
            "custom",
            "connected custom endpoint",
            custom_base,
            os.environ.get("NARAD_ENDPOINT_API_KEY", "").strip() or None,
        )

    assigned = get_avatar_model(avatar_name)
    if _model_supports_images(assigned):
        endpoint = _endpoint_for_model(assigned, source="active avatar endpoint")
        if endpoint:
            return endpoint

    candidates = (
        (os.environ.get("GEMINI_VISION_MODEL", "gemini/gemini-2.5-flash"), "connected Google endpoint"),
        (os.environ.get("ANTHROPIC_VISION_MODEL", "anthropic/claude-sonnet-4-6"), "connected Anthropic endpoint"),
        (os.environ.get("OPENAI_VISION_MODEL", "openai/gpt-4o"), "connected OpenAI endpoint"),
    )
    for model, source in candidates:
        endpoint = _endpoint_for_model(model, source=source)
        if endpoint:
            return endpoint

    if os.environ.get("MIMO_API_KEY", "").strip():
        return ModelEndpoint(
            model=os.environ.get("MIMO_MODEL", "mimo-v2.5"),
            provider="mimo",
            source="connected MiMo endpoint",
            api_base=os.environ.get("MIMO_BASE_URL") or None,
            api_key=os.environ.get("MIMO_API_KEY"),
        )

    local = _local_gemma_model()
    return _endpoint_for_model(local, source="offline Gemma fallback")


def get_vision_model(avatar_name: str) -> tuple[str, str | None]:
    """Backward-compatible vision tuple; new callers should use the endpoint."""
    endpoint = get_vision_endpoint(avatar_name)
    return (endpoint.model, endpoint.api_base) if endpoint else ("", None)


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

    Visual output tasks stay on the avatar's live worker model to avoid
    cross-provider auth failures mid-turn. A per-avatar override remains
    possible via {AVATAR_NAME}_VISUAL_MODEL.
    """
    override = _model_override(f"{avatar_name.upper()}_VISUAL_MODEL")
    return (override or get_avatar_model(avatar_name), None)


def get_thinking_instructions(avatar_name: str) -> str:
    """Return a model-agnostic prompt fragment for structured chain-of-thought.

    Models with native reasoning: returns '' — thinking is activated via API
    behavior or a provider parameter, not printed into response text.
    All other models: injects <thinking>...</thinking> instruction so the model
    produces equivalent structured pre-response reasoning.
    """
    model = get_avatar_model(avatar_name)
    if _provider(model) in {"anthropic", "deepseek", "xai"} or "gemma4" in model.lower():
        return ""
    return (
        "\nBefore responding, write your full reasoning process in "
        "<thinking>...</thinking> tags. Think through all possibilities "
        "before committing to an answer. Only what follows </thinking> "
        "is shown to the user.\n"
    )
