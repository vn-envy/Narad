"""Narad's provider-aware LiteLLM adapter.

Keeps short-lived subscription OAuth credentials fresh at request time and
applies provider options that the installed ADK/LiteLLM versions do not yet
infer from model metadata.
"""
from __future__ import annotations

import copy
import logging
import os
import threading
import time
from collections.abc import AsyncGenerator
from typing import Any

from google.adk.models.lite_llm import LiteLlm
from google.adk.models.llm_request import LlmRequest
from google.adk.models.llm_response import LlmResponse

_XAI_REASONING_EFFORTS = {"none", "low", "medium", "high", "xhigh"}
_XAI_TRANSIENT_MARKERS = (
    "timeout",
    "readtimeout",
    "sockettimeouterror",
    "apiconnectionerror",
    "internalservererror",
    "serviceunavailableerror",
    "ratelimiterror",
    "connection timed out",
    "temporarily unavailable",
    "too many requests",
)
_SAFE_FAILOVER_MARKERS = _XAI_TRANSIENT_MARKERS + (
    "authenticationerror",
    "invalid api key",
    "incorrect api key",
    "unauthorized",
    "permissiondenied",
    "insufficient_quota",
    "quota exceeded",
)
_CIRCUIT_LOCK = threading.Lock()
_XAI_CIRCUIT_OPEN_UNTIL = 0.0
_XAI_CIRCUIT_REASON = ""

log = logging.getLogger("narad.models")


def _is_xai_model(model: str) -> bool:
    lower = (model or "").lower()
    return "xai/" in lower or "grok" in lower


def _float_env(name: str, default: float, *, minimum: float, maximum: float) -> float:
    try:
        value = float(os.environ.get(name, str(default)))
    except (TypeError, ValueError):
        value = default
    return max(minimum, min(value, maximum))


def _copy_request(llm_request: LlmRequest) -> LlmRequest:
    try:
        return llm_request.model_copy(deep=True)
    except AttributeError:
        return copy.deepcopy(llm_request)


def _exception_chain_text(exc: BaseException) -> str:
    parts: list[str] = []
    current: BaseException | None = exc
    seen: set[int] = set()
    while current is not None and id(current) not in seen and len(parts) < 6:
        seen.add(id(current))
        parts.append(f"{type(current).__name__}: {current}")
        current = current.__cause__ or current.__context__
    return " | ".join(parts).lower()


def is_transient_provider_error(exc: BaseException) -> bool:
    """Recognize retryable provider failures even when LiteLLM wraps them."""
    error_text = _exception_chain_text(exc)
    return any(marker in error_text for marker in _XAI_TRANSIENT_MARKERS)


def is_safe_provider_failover_error(exc: BaseException) -> bool:
    """Only retry failures that cannot represent a malformed user request."""
    error_text = _exception_chain_text(exc)
    return any(marker in error_text for marker in _SAFE_FAILOVER_MARKERS)


def _offline_fallback_model(model: str) -> str:
    lower = (model or "").lower()
    if lower.startswith(("ollama/", "ollama_chat/")):
        return ""
    try:
        from local_model_runtime import local_model_id
        from model_registry import provider_available_for_model

        fallback = local_model_id()
        return fallback if provider_available_for_model(fallback) else ""
    except Exception:
        return ""


def _fallback_model(model: str) -> str:
    if not _is_xai_model(model):
        return ""
    configured = os.environ.get(
        "NARAD_XAI_FALLBACK_MODEL", "deepseek/deepseek-flash"
    ).strip()
    candidates = [configured]
    try:
        from local_model_runtime import local_model_id

        candidates.append(local_model_id())
    except Exception:
        pass
    try:
        from model_registry import provider_available_for_model

        for fallback in candidates:
            if (
                fallback
                and fallback.lower() != model.lower()
                and not _is_xai_model(fallback)
                and provider_available_for_model(fallback)
            ):
                return fallback
    except Exception:
        if configured and "deepseek" not in configured.lower():
            return configured
    return ""


def _open_xai_circuit(exc: BaseException) -> None:
    global _XAI_CIRCUIT_OPEN_UNTIL, _XAI_CIRCUIT_REASON
    cooldown = _float_env(
        "NARAD_XAI_CIRCUIT_BREAKER_S", 90.0, minimum=5.0, maximum=900.0
    )
    with _CIRCUIT_LOCK:
        _XAI_CIRCUIT_OPEN_UNTIL = time.monotonic() + cooldown
        _XAI_CIRCUIT_REASON = type(exc).__name__


def _close_xai_circuit() -> None:
    global _XAI_CIRCUIT_OPEN_UNTIL, _XAI_CIRCUIT_REASON
    with _CIRCUIT_LOCK:
        _XAI_CIRCUIT_OPEN_UNTIL = 0.0
        _XAI_CIRCUIT_REASON = ""


def xai_resilience_status() -> dict[str, Any]:
    """Return non-secret failover state for runtime capability reporting."""
    with _CIRCUIT_LOCK:
        remaining = max(0.0, _XAI_CIRCUIT_OPEN_UNTIL - time.monotonic())
        reason = _XAI_CIRCUIT_REASON
    return {
        "request_timeout_s": _float_env(
            "NARAD_XAI_TIMEOUT_S", 90.0, minimum=15.0, maximum=3_600.0
        ),
        "fallback_model": os.environ.get(
            "NARAD_XAI_FALLBACK_MODEL", "deepseek/deepseek-flash"
        ).strip(),
        "circuit_open": remaining > 0,
        "circuit_remaining_s": round(remaining, 1),
        "last_transient_error": reason or None,
    }


def completion_options(model: str) -> dict[str, Any]:
    """Return completion arguments for a model without embedding credentials."""
    if not _is_xai_model(model):
        return {}

    options: dict[str, Any] = {
        # Keep the interactive surface responsive. A no-byte xAI stall fails
        # over to the configured larger harness lane instead of freezing Narad.
        "timeout": _float_env(
            "NARAD_XAI_TIMEOUT_S", 90.0, minimum=15.0, maximum=3_600.0
        ),
        "num_retries": 0,
    }
    service_tier = os.environ.get("GROK_SERVICE_TIER", "priority").strip().lower()
    if service_tier in {"default", "priority"}:
        options["service_tier"] = service_tier
        # LiteLLM 1.83 predates xAI's service-tier metadata but supports an
        # explicit OpenAI-compatible passthrough for newly released params.
        options["allowed_openai_params"] = ["service_tier"]

    reasoning_effort = os.environ.get("GROK_REASONING_EFFORT", "").strip().lower()
    if reasoning_effort in _XAI_REASONING_EFFORTS:
        options["reasoning_effort"] = reasoning_effort
    return options


def ensure_model_credentials(model: str) -> None:
    """Refresh an OAuth-backed xAI credential immediately before inference."""
    if not _is_xai_model(model):
        return
    from xai_oauth import ensure_runtime_token

    if not ensure_runtime_token():
        raise RuntimeError(
            "Grok is selected but xAI is not connected. "
            "Use Settings > Connections > Sign in with Grok."
        )


class NaradLiteLlm(LiteLlm):
    """LiteLlm with xAI OAuth refresh and no-byte transient failover."""

    def __init__(self, model: str, **kwargs: Any) -> None:
        try:
            from local_model_runtime import local_completion_options

            provider_options = local_completion_options(model)
        except Exception:
            provider_options = {}
        provider_options.update(completion_options(model))
        provider_options.update(kwargs)
        super().__init__(model=model, **provider_options)

    async def generate_content_async(
        self,
        llm_request: LlmRequest,
        stream: bool = False,
    ) -> AsyncGenerator[LlmResponse, None]:
        ensure_model_credentials(self.model)
        fallback = _fallback_model(self.model) or _offline_fallback_model(self.model)
        circuit = xai_resilience_status()
        if _is_xai_model(self.model) and fallback and circuit["circuit_open"]:
            log.warning(
                "xAI circuit open for %.1fs; routing %s to %s",
                circuit["circuit_remaining_s"],
                self.model,
                fallback,
            )
            fallback_request = _copy_request(llm_request)
            fallback_request.model = fallback
            fallback_llm = NaradLiteLlm(model=fallback)
            async for response in fallback_llm.generate_content_async(fallback_request, stream):
                yield response
            return

        primary_request = _copy_request(llm_request)
        emitted = False
        try:
            async for response in super().generate_content_async(primary_request, stream):
                emitted = True
                if _is_xai_model(self.model):
                    _close_xai_circuit()
                yield response
        except Exception as exc:
            if _is_xai_model(self.model) and is_transient_provider_error(exc):
                _open_xai_circuit(exc)
            # Once any content or tool-call response has escaped, replaying the
            # request on another provider could duplicate user-visible effects.
            if emitted or not fallback or not is_safe_provider_failover_error(exc):
                raise
            log.warning(
                "Grok call failed before first response (%s); failing over %s -> %s",
                type(exc).__name__,
                self.model,
                fallback,
            )
            fallback_request = _copy_request(llm_request)
            fallback_request.model = fallback
            fallback_llm = NaradLiteLlm(model=fallback)
            async for response in fallback_llm.generate_content_async(fallback_request, stream):
                yield response
