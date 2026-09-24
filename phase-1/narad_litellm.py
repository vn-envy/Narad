"""Narad's provider-aware LiteLLM adapter.

Applies provider options that the installed ADK/LiteLLM versions do not yet
infer from model metadata, and fails a cloud call over to the installed local
model before any output has escaped.

Every request passes through privacy_gateway first: `redact`-tier providers
(DeepSeek and unknown hosts) only ever see pseudonymised text, and their replies
are restored on the Mac.

Owner policy (2026-09-23): xAI/Grok is out. An `xai/*` model is never called:
a stale session or explicit override naming one is served by the default
chain (DeepSeek, other connected providers, local Gemma) instead.
"""
from __future__ import annotations

import copy
import logging
import os
from collections.abc import AsyncGenerator
from typing import Any

from google.adk.models.lite_llm import LiteLlm
from google.adk.models.llm_request import LlmRequest
from google.adk.models.llm_response import LlmResponse

import privacy_gateway

_TRANSIENT_MARKERS = (
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
_SAFE_FAILOVER_MARKERS = _TRANSIENT_MARKERS + (
    "authenticationerror",
    "invalid api key",
    "incorrect api key",
    "unauthorized",
    "permissiondenied",
    "insufficient_quota",
    "quota exceeded",
)
XAI_DISABLED_DETAIL = (
    "xAI/Grok is disabled by owner policy. Connect DeepSeek, Gemini, OpenAI, "
    "Claude or a custom endpoint, or install the local Gemma model."
)
_XAI_REROUTE_WARNED: set[str] = set()

log = logging.getLogger("narad.models")


def _is_xai_model(model: str) -> bool:
    lower = (model or "").lower()
    return "xai/" in lower or "grok" in lower


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
    return any(marker in error_text for marker in _TRANSIENT_MARKERS)


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


def _policy_replacement_model() -> str:
    """The default chain's live model for a request that named xAI — never xAI."""
    try:
        from model_config import resolve_orchestrator_model
        from model_registry import provider_available_for_model

        replacement = resolve_orchestrator_model()[0]
        if not _is_xai_model(replacement) and provider_available_for_model(replacement):
            return replacement
    except Exception:
        pass
    return ""


def completion_options(model: str) -> dict[str, Any]:
    """Return per-model completion arguments.

    Only the connected custom OpenAI-compatible endpoint needs any: its base
    URL and its own key (or a placeholder), so LiteLLM never forwards another
    provider's key such as OPENAI_API_KEY to a third-party base URL.
    """
    try:
        from model_registry import custom_endpoint_model

        custom = custom_endpoint_model()
    except Exception:
        return {}
    if not model or model != custom:
        return {}
    return {
        "api_base": os.environ.get("NARAD_ENDPOINT_URL", "").strip(),
        "api_key": os.environ.get("NARAD_ENDPOINT_API_KEY", "").strip() or "not-needed",
    }


def ensure_model_credentials(model: str) -> None:
    """Refuse a model owner policy keeps out of routing, before any request."""
    if _is_xai_model(model):
        raise RuntimeError(XAI_DISABLED_DETAIL)


class NaradLiteLlm(LiteLlm):
    """LiteLlm with provider options, the xAI policy gate and no-byte failover."""

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
        if _is_xai_model(self.model):
            replacement = _policy_replacement_model()
            if not replacement:
                raise RuntimeError(XAI_DISABLED_DETAIL)
            if self.model not in _XAI_REROUTE_WARNED:
                _XAI_REROUTE_WARNED.add(self.model)
                log.warning(
                    "xAI/Grok is disabled by owner policy; routing %s to %s",
                    self.model,
                    replacement,
                )
            replacement_request = _copy_request(llm_request)
            replacement_request.model = replacement
            replacement_llm = NaradLiteLlm(model=replacement)
            async for response in replacement_llm.generate_content_async(replacement_request, stream):
                yield response
            return

        fallback = _offline_fallback_model(self.model)
        primary_request = _copy_request(llm_request)
        try:
            # Pseudonymise the copy for `redact`-tier providers; the session
            # history keeps real values and the reply is restored below.
            tier = privacy_gateway.prepare_llm_request(primary_request, self.model)
        except privacy_gateway.PrivacyGatewayError as exc:
            if not fallback or isinstance(exc, privacy_gateway.PolicyBlocked):
                raise
            log.warning("%s; serving this turn on %s instead of %s", exc, fallback, self.model)
            fallback_request = _copy_request(llm_request)
            fallback_request.model = fallback
            async for response in NaradLiteLlm(model=fallback).generate_content_async(fallback_request, stream):
                yield response
            return
        emitted = False
        try:
            async for response in super().generate_content_async(primary_request, stream):
                emitted = True
                if tier == privacy_gateway.REDACT:
                    response = privacy_gateway.restore_llm_response(response)
                yield response
        except Exception as exc:
            # Once any content or tool-call response has escaped, replaying the
            # request on another provider could duplicate user-visible effects.
            if emitted or not fallback or not is_safe_provider_failover_error(exc):
                raise
            log.warning(
                "Model call failed before first response (%s); failing over %s -> %s",
                type(exc).__name__,
                self.model,
                fallback,
            )
            fallback_request = _copy_request(llm_request)
            fallback_request.model = fallback
            fallback_llm = NaradLiteLlm(model=fallback)
            async for response in fallback_llm.generate_content_async(fallback_request, stream):
                yield response
