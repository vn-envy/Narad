"""Provider-neutral typed decision layer for Narad.

Jev is deliberately kept behind this narrow boundary. Agents cannot call an
arbitrary evaluator; Narad code owns the question schemas, confidence policy,
redaction, fallback, and all resulting side effects.
"""

from __future__ import annotations

import json
import os
import re
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Literal

import httpx

DecisionKind = Literal["noul", "choice", "score"]

_SECRET_KEY = re.compile(
    r"(?:password|passcode|secret|token|cookie|authorization|api[_-]?key|otp|pin|seed|private[_-]?key)",
    re.IGNORECASE,
)
_BEARER = re.compile(r"\bBearer\s+[A-Za-z0-9._~+/=-]{8,}", re.IGNORECASE)
_EMAIL = re.compile(r"(?<![\w.+-])[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}(?![\w.-])")
_PHONE = re.compile(r"(?<!\d)(?:\+?\d[\d ()-]{7,}\d)(?!\d)")
_SENSITIVE_DOMAIN = re.compile(
    r"\b(?:medical|medical diagnosis|diagnosed with|prescription|bank|brokerage|wallet|credit card|debit card|"
    r"account number|tax return|health record|seed phrase|private key|one[- ]?time password)\b",
    re.IGNORECASE,
)


def _stored_typesafe_key() -> str:
    """Resolve the TypeSafe Jev key from the project secret store (Kunji).

    Kunji keeps the key in the OS keychain or its 0600 file fallback under
    ~/.narad/config. Guarded so the decision engine never crashes on import when
    Kunji is unavailable; a real env var still wins (callers check env first).
    """
    try:
        from kunji import get_key
        return (get_key("typesafe") or "").strip()
    except Exception:
        return ""


@dataclass(frozen=True)
class DecisionQuestion:
    kind: DecisionKind
    instructions: str
    criteria: Any = None

    def to_wire(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "type": self.kind,
            "instructions": self.instructions,
        }
        if self.criteria is not None:
            payload["criteria"] = self.criteria
        return payload


@dataclass(frozen=True)
class DecisionAnswer:
    kind: DecisionKind
    value: bool | str | float
    confidence: float
    probabilities: dict[str, float] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class DecisionResult:
    decision_id: str
    status: str
    provider: str
    model: str
    answers: dict[str, DecisionAnswer] = field(default_factory=dict)
    latency_ms: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    estimated_cost_usd: float = 0.0
    redactions: int = 0
    error: str | None = None

    @property
    def available(self) -> bool:
        return self.status == "ok"

    @property
    def minimum_confidence(self) -> float:
        if not self.answers:
            return 0.0
        return min(answer.confidence for answer in self.answers.values())

    def to_dict(self, *, include_probabilities: bool = True) -> dict[str, Any]:
        answers: dict[str, Any] = {}
        for name, answer in self.answers.items():
            item = {
                "kind": answer.kind,
                "value": answer.value,
                "confidence": round(answer.confidence, 4),
            }
            if include_probabilities:
                item["probabilities"] = answer.probabilities
            answers[name] = item
        return {
            "decision_id": self.decision_id,
            "status": self.status,
            "provider": self.provider,
            "model": self.model,
            "answers": answers,
            "minimum_confidence": round(self.minimum_confidence, 4),
            "latency_ms": self.latency_ms,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "estimated_cost_usd": self.estimated_cost_usd,
            "redactions": self.redactions,
            "error": self.error,
        }


def _truthy(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _redact(value: Any, *, key: str = "") -> tuple[Any, int]:
    if key and _SECRET_KEY.search(key):
        return "[REDACTED]", 1
    if isinstance(value, dict):
        output: dict[str, Any] = {}
        count = 0
        for child_key, child_value in value.items():
            cleaned, child_count = _redact(child_value, key=str(child_key))
            output[str(child_key)] = cleaned
            count += child_count
        return output, count
    if isinstance(value, list):
        output_list = []
        count = 0
        for item in value:
            cleaned, child_count = _redact(item)
            output_list.append(cleaned)
            count += child_count
        return output_list, count
    if isinstance(value, tuple):
        return _redact(list(value))
    if isinstance(value, str):
        cleaned, count = _BEARER.subn("Bearer [REDACTED]", value)
        cleaned, email_count = _EMAIL.subn("[EMAIL]", cleaned)
        cleaned, phone_count = _PHONE.subn("[PHONE]", cleaned)
        return cleaned, count + email_count + phone_count
    if value is None or isinstance(value, (bool, int, float)):
        return value, 0
    return str(value), 0


def _bounded_state(value: Any, max_bytes: int) -> Any:
    encoded = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    if len(encoded.encode("utf-8")) <= max_bytes:
        return value
    # Preserve valid structured state while making truncation explicit.
    excerpt = encoded.encode("utf-8")[: max(512, max_bytes - 128)].decode("utf-8", errors="ignore")
    return {
        "compacted": True,
        "original_bytes": len(encoded.encode("utf-8")),
        "state_excerpt": excerpt,
    }


def jev_status() -> dict[str, Any]:
    key_present = bool(os.environ.get("TYPESAFE_API_KEY", "").strip() or _stored_typesafe_key())
    enabled = _truthy("NARAD_JEV_ENABLED", default=True)
    return {
        "available": bool(key_present and enabled),
        "configured": key_present,
        "enabled": enabled,
        "provider": "typesafe",
        "model": os.environ.get("TYPESAFE_DEFAULT_MODEL", "jev-latest"),
        "base_url": os.environ.get("TYPESAFE_BASE_URL", "https://api.typesafe.ai").rstrip("/"),
        "mode": os.environ.get("NARAD_JEV_MODE", "active").strip().lower(),
        "reason": (
            None
            if key_present and enabled
            else "Jev is disabled" if not enabled else "TYPESAFE_API_KEY not set"
        ),
        "local_fallback_required": True,
        "sends_redacted_state_to_cloud": True,
    }


class JevDecisionProvider:
    """Minimal implementation of TypeSafe's official System One protocol."""

    def __init__(
        self,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str | None = None,
        timeout_s: float | None = None,
    ) -> None:
        key = (api_key if api_key is not None else os.environ.get("TYPESAFE_API_KEY", "")).strip()
        if not key:
            key = _stored_typesafe_key()
            if key:
                os.environ.setdefault("TYPESAFE_API_KEY", key)
        self.api_key = key
        self.base_url = (
            base_url if base_url is not None else os.environ.get("TYPESAFE_BASE_URL", "https://api.typesafe.ai")
        ).rstrip("/")
        self.model = model or os.environ.get("TYPESAFE_DEFAULT_MODEL", "jev-latest")
        self.timeout_s = timeout_s or float(os.environ.get("NARAD_JEV_TIMEOUT_S", "10"))

    def evaluate(
        self,
        *,
        decision_id: str,
        state: Any,
        questions: dict[str, DecisionQuestion],
        allow_sensitive: bool = False,
        record_cost: bool = True,
    ) -> DecisionResult:
        started = time.perf_counter()
        if not self.api_key or not _truthy("NARAD_JEV_ENABLED", default=True):
            return self._error(decision_id, started, "unavailable", "TYPESAFE_API_KEY not set or Jev disabled")
        if not questions:
            return self._error(decision_id, started, "invalid", "At least one decision question is required")

        cleaned_state, redactions = _redact(state)
        state_text = json.dumps(cleaned_state, ensure_ascii=False)
        sensitive_allowed = allow_sensitive or _truthy("NARAD_JEV_ALLOW_SENSITIVE", default=False)
        if _SENSITIVE_DOMAIN.search(state_text) and not sensitive_allowed:
            return DecisionResult(
                decision_id=decision_id,
                status="privacy_blocked",
                provider="typesafe",
                model=self.model,
                latency_ms=int((time.perf_counter() - started) * 1000),
                redactions=redactions,
                error="Sensitive state is not enabled for the cloud decision provider",
            )

        try:
            max_bytes = max(2_048, int(os.environ.get("NARAD_JEV_MAX_STATE_BYTES", "32768")))
        except ValueError:
            max_bytes = 32_768
        cleaned_state = _bounded_state(cleaned_state, max_bytes)
        payload = {
            "state": cleaned_state,
            "model": self.model,
            "questions": {name: question.to_wire() for name, question in questions.items()},
        }
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": "Narad/DecisionEngine",
        }

        try:
            response = self._post(payload, headers)
            response.raise_for_status()
            body = response.json()
            answers = self._parse_answers(body.get("answers"), questions)
            usage = body.get("usage") if isinstance(body.get("usage"), dict) else {}
            input_tokens = int(usage.get("input_tokens") or 0)
            output_tokens = int(usage.get("output_tokens") or 0)
            cost = round(input_tokens * 0.042 / 1_000_000, 8)
            result = DecisionResult(
                decision_id=decision_id,
                status="ok",
                provider="typesafe",
                model=str(body.get("model") or self.model),
                answers=answers,
                latency_ms=int((time.perf_counter() - started) * 1000),
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                estimated_cost_usd=cost,
                redactions=redactions,
            )
            if record_cost:
                self._record_cost(result)
            return result
        except httpx.HTTPStatusError as exc:
            detail = self._response_error(exc.response)
            return self._error(
                decision_id,
                started,
                "error",
                f"TypeSafe HTTP {exc.response.status_code}: {detail}",
                redactions,
            )
        except (httpx.HTTPError, ValueError, TypeError, KeyError) as exc:
            return self._error(decision_id, started, "error", f"{type(exc).__name__}: {exc}", redactions)

    def _post(self, payload: dict[str, Any], headers: dict[str, str]) -> httpx.Response:
        with httpx.Client(timeout=self.timeout_s) as client:
            return client.post(f"{self.base_url}/v1/systemone", json=payload, headers=headers)

    def _parse_answers(
        self,
        raw_answers: Any,
        questions: dict[str, DecisionQuestion],
    ) -> dict[str, DecisionAnswer]:
        if not isinstance(raw_answers, dict):
            raise ValueError("TypeSafe response did not contain an answers object")
        parsed: dict[str, DecisionAnswer] = {}
        for name, question in questions.items():
            raw = raw_answers.get(name)
            if not isinstance(raw, dict) or raw.get("type") != question.kind:
                raise ValueError(f"Invalid or missing answer for {name}")
            if question.kind == "noul":
                probability = max(0.0, min(1.0, float(raw["noul"])))
                parsed[name] = DecisionAnswer(
                    kind="noul",
                    value=probability >= 0.5,
                    confidence=abs(probability - 0.5) * 2.0,
                    probabilities={"true": probability, "false": 1.0 - probability},
                )
            elif question.kind == "choice":
                probabilities = {
                    str(key): float(value)
                    for key, value in (raw.get("probabilities") or {}).items()
                }
                parsed[name] = DecisionAnswer(
                    kind="choice",
                    value=str(raw["choice"]),
                    confidence=max(0.0, min(1.0, float(raw.get("confidence") or 0.0))),
                    probabilities=probabilities,
                )
            else:
                probabilities = {
                    str(key): float(value)
                    for key, value in (raw.get("probabilities") or {}).items()
                }
                parsed[name] = DecisionAnswer(
                    kind="score",
                    value=float(raw["score"]),
                    confidence=max(0.0, min(1.0, float(raw.get("confidence") or 0.0))),
                    probabilities=probabilities,
                )
        return parsed

    def _error(
        self,
        decision_id: str,
        started: float,
        status: str,
        error: str,
        redactions: int = 0,
    ) -> DecisionResult:
        return DecisionResult(
            decision_id=decision_id,
            status=status,
            provider="typesafe",
            model=self.model,
            latency_ms=int((time.perf_counter() - started) * 1000),
            redactions=redactions,
            error=error[:600],
        )

    @staticmethod
    def _response_error(response: httpx.Response) -> str:
        try:
            body = response.json()
            if isinstance(body, dict):
                value = body.get("detail") or body.get("error") or body.get("message")
                if value:
                    return str(value)[:500]
        except ValueError:
            pass
        return response.text[:500] or "request failed"

    @staticmethod
    def _record_cost(result: DecisionResult) -> None:
        try:
            from cost_ledger import record

            record(
                source=f"jev:{result.decision_id}",
                model=result.model,
                prompt_tokens=result.input_tokens,
                completion_tokens=result.output_tokens,
            )
        except Exception:
            pass


def evaluate_decision(
    decision_id: str,
    state: Any,
    questions: dict[str, DecisionQuestion],
    *,
    allow_sensitive: bool = False,
    record_cost: bool = True,
) -> DecisionResult:
    return JevDecisionProvider().evaluate(
        decision_id=decision_id,
        state=state,
        questions=questions,
        allow_sensitive=allow_sensitive,
        record_cost=record_cost,
    )
