"""
Smriti Embed — the single embedding client for all memory planes.

One provider, one model, chosen once per process:
  - SMRITI_EMBEDDING_MODEL = gemini | mimo | openai | local (explicit pin)
  - unset → first configured API key wins (gemini → mimo → openai)
  - no keys at all → 'local' (deterministic hash embeddings) with a loud warning

There is deliberately NO silent fallback between providers: records written
under a stand-in model are invisible to queries under the real one
(split-brain). If the configured provider fails, embedding raises
EmbeddingUnavailableError and the caller decides — visibly — what to do.

This module must stay dependency-light (no lancedb/pyarrow): every memory
plane imports it, including environments where the legacy store is absent.
"""

from __future__ import annotations

import hashlib
import logging
import math
import os
import re
import threading
import time
from datetime import datetime, timezone
from typing import Any

import privacy_gateway

log = logging.getLogger("narad.smriti")

_LOCAL_DIM = 256


class EmbeddingUnavailableError(RuntimeError):
    """The configured embedding provider cannot embed right now.

    Never swallow this into a hash fallback — pin SMRITI_EMBEDDING_MODEL=local
    explicitly if offline embeddings are wanted.
    """


def _select_embed_provider() -> str:
    configured = os.environ.get("SMRITI_EMBEDDING_MODEL", "").strip().lower()
    if configured in {"gemini", "mimo", "openai", "local"}:
        return configured
    if configured and configured != "auto":
        return configured
    if os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY"):
        return "gemini"
    if os.environ.get("MIMO_API_KEY"):
        return "mimo"
    if os.environ.get("OPENAI_API_KEY"):
        return "openai"
    log.warning(
        "Smriti: no embedding provider configured — pinning the deterministic "
        "local model (SMRITI_EMBEDDING_MODEL=local). Semantic recall quality "
        "will be reduced until an API provider is configured."
    )
    return "local"


def _provider_dim(provider: str) -> int:
    if provider == "gemini":
        return 768
    if provider == "local":
        return _LOCAL_DIM
    return 1536


_SMRITI_EMBED_PROVIDER = _select_embed_provider()
_EMBED_DIM = _provider_dim(_SMRITI_EMBED_PROVIDER)
_EMBED_FAILURE_COOLDOWN_S = int(os.environ.get("SMRITI_EMBED_FAILURE_COOLDOWN_S", "300"))
_embed_unavailable_until = 0.0


def refresh_provider() -> None:
    """Re-read provider selection from the environment (tests, reconfigure)."""
    global _SMRITI_EMBED_PROVIDER, _EMBED_DIM, _EMBED_FAILURE_COOLDOWN_S
    _SMRITI_EMBED_PROVIDER = _select_embed_provider()
    _EMBED_DIM = _provider_dim(_SMRITI_EMBED_PROVIDER)
    _EMBED_FAILURE_COOLDOWN_S = int(os.environ.get("SMRITI_EMBED_FAILURE_COOLDOWN_S", "300"))


def _sha(value: str) -> str:
    return hashlib.sha1(value.encode("utf-8")).hexdigest()


def _l2_normalize(values: list[float]) -> list[float]:
    norm = math.sqrt(sum(v * v for v in values)) or 1.0
    return [v / norm for v in values]


def _local_embed(text: str, dim: int = _LOCAL_DIM) -> list[float]:
    """Deterministic hash-bucket embedding — the pinned 'local' provider."""
    buckets = [0.0] * dim
    words = re.findall(r"\w+", text.lower())
    if not words:
        return buckets
    for token in words:
        base = int(_sha(token)[:8], 16)
        buckets[base % dim] += 1.0
        pair = int(_sha(token[::-1])[:8], 16)
        buckets[pair % dim] += 0.35
    return _l2_normalize(buckets)


_client_lock = threading.Lock()
_clients: dict[str, tuple[tuple[str, str | None], Any]] = {}


def _embed_client(provider: str, api_key: str, base_url: str | None = None) -> Any:
    """One lazily-built SDK client per provider, shared across calls and threads.

    Building a client per call redid connection setup on every embedding
    (up to three per turn). Both SDK clients are safe to share across
    threads; a changed key or base URL builds a fresh one.
    """
    fingerprint = (_sha(api_key), base_url)
    with _client_lock:
        cached = _clients.get(provider)
        if cached is not None and cached[0] == fingerprint:
            return cached[1]
        if provider == "gemini":
            from google import genai as _genai
            client = _genai.Client(api_key=api_key)
        else:
            import openai as _openai
            client = _openai.OpenAI(api_key=api_key, base_url=base_url)
        _clients[provider] = (fingerprint, client)
        return client


def _embed_many(texts: list[str]) -> list[list[float]]:
    """Embed a batch via the selected provider — ONE API call per batch.

    Raises EmbeddingUnavailableError on failure. Batching matters: indexing
    516 wiki sections serially took 6+ minutes; batched it is a few calls.
    """
    global _embed_unavailable_until
    provider = _SMRITI_EMBED_PROVIDER
    if provider == "local":
        return [_local_embed(text) for text in texts]

    now = time.time()
    if _embed_unavailable_until and now < _embed_unavailable_until:
        raise EmbeddingUnavailableError(
            f"embedding provider {provider} cooling down until "
            f"{datetime.fromtimestamp(_embed_unavailable_until, tz=timezone.utc).isoformat()}"
        )

    try:
        clipped = privacy_gateway.guard_texts(provider, [text[:4000] for text in texts], source="memory")
    except privacy_gateway.PrivacyGatewayError as exc:
        raise EmbeddingUnavailableError(str(exc)) from exc

    if provider == "gemini":
        try:
            from google.genai import types as _gtypes
            api_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY", "")
            if not api_key:
                raise RuntimeError("SMRITI_EMBEDDING_MODEL=gemini but GEMINI_API_KEY is not set")
            client = _embed_client("gemini", api_key)
            resp = client.models.embed_content(
                model="gemini-embedding-001",
                contents=clipped,
                config=_gtypes.EmbedContentConfig(output_dimensionality=768),
            )
            return [e.values for e in resp.embeddings]
        except Exception as exc:
            message = str(exc).lower()
            if any(token in message for token in ("quota", "resource_exhausted", "429", "rate limit")):
                _embed_unavailable_until = now + _EMBED_FAILURE_COOLDOWN_S
            else:
                # Gemini failures tend to repeat for the same request burst, so cool
                # the provider down briefly instead of re-paying long error latencies.
                _embed_unavailable_until = max(
                    _embed_unavailable_until,
                    now + min(_EMBED_FAILURE_COOLDOWN_S, 60),
                )
            log.warning("Smriti: gemini embedding failed (visible, no fallback): %s", exc)
            raise EmbeddingUnavailableError(str(exc)) from exc

    # OpenAI-compatible path: Mimo when configured, otherwise plain OpenAI.
    try:
        if provider == "mimo":
            api_key = os.environ.get("MIMO_API_KEY", "")
            base_url = os.environ.get("MIMO_BASE_URL")
        else:
            api_key = os.environ.get("OPENAI_API_KEY", "")
            base_url = None
        if not api_key:
            raise RuntimeError(f"SMRITI_EMBEDDING_MODEL={provider} but no API key is set")
        client = _embed_client(provider, api_key, base_url)
        resp = client.embeddings.create(model="text-embedding-3-small", input=clipped)
        # The API may return out of order — index field is authoritative.
        ordered = sorted(resp.data, key=lambda item: item.index)
        return [item.embedding for item in ordered]
    except Exception as exc:
        _embed_unavailable_until = max(
            _embed_unavailable_until,
            now + min(_EMBED_FAILURE_COOLDOWN_S, 60),
        )
        log.warning("Smriti: %s embedding failed (visible, no fallback): %s", provider, exc)
        raise EmbeddingUnavailableError(str(exc)) from exc


def _embed(text: str) -> list[float]:
    """Embed via the selected provider. Raises EmbeddingUnavailableError on failure."""
    return _embed_many([text])[0]


def _safe_slug(value: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9._-]+", "_", value.strip())
    return slug[:80] or "default"


def embed_text(text: str) -> tuple[list[float], str]:
    """Embed text and return (normalized vector, model id).

    The model id keys the on-disk manifests, so it must be stable per provider.
    Raises EmbeddingUnavailableError — callers surface it, never mask it.
    """
    provider = _SMRITI_EMBED_PROVIDER
    if provider == "local":
        vector = _local_embed(text)
        return vector, f"local-hash-v1-{len(vector)}"
    vector = _l2_normalize(list(map(float, _embed(text[:4000]))))
    return vector, f"smriti-{_safe_slug(provider)}-{len(vector)}"


_EMBED_BATCH_SIZE = int(os.environ.get("SMRITI_EMBED_BATCH_SIZE", "64"))


def embed_texts(texts: list[str]) -> tuple[list[list[float]], str]:
    """Batch variant of embed_text — same vectors and model id, far fewer calls.

    Chunks the input so provider batch limits are respected. Raises
    EmbeddingUnavailableError on failure — callers surface it, never mask it.
    """
    if not texts:
        return [], current_embedding_model()
    provider = _SMRITI_EMBED_PROVIDER
    if provider == "local":
        vectors = [_local_embed(text) for text in texts]
        return vectors, f"local-hash-v1-{len(vectors[0])}"
    vectors = []
    for start in range(0, len(texts), max(_EMBED_BATCH_SIZE, 1)):
        chunk = texts[start : start + max(_EMBED_BATCH_SIZE, 1)]
        for raw in _embed_many(chunk):
            vectors.append(_l2_normalize(list(map(float, raw))))
    return vectors, f"smriti-{_safe_slug(provider)}-{len(vectors[0])}"


_MODEL_CACHE: dict[str, str] = {}


def current_embedding_model() -> str:
    """Model id new records and queries will use. Probes once per provider.

    Lets indexers compare stored content hashes for the *right* model before
    embedding anything — the fix for re-embedding every episode on recall.
    """
    provider = _SMRITI_EMBED_PROVIDER
    cached = _MODEL_CACHE.get(provider)
    if cached:
        return cached
    _, model = embed_text("smriti embedding model probe")
    _MODEL_CACHE[provider] = model
    return model
