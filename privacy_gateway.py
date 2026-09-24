"""privacy_gateway — the one place family text is checked before it leaves the Mac.

Owner policy (2026-09-23): DeepSeek is acceptable only when a local model has
pseudonymised personal details first; xAI is never called; other vendors must
publish clear data-handling terms. Every outbound model and embedding call goes
through this module, which:

  1. classifies the destination into a trust tier
       local    — runs on this machine (Ollama, bundled llama-server)
       trusted  — published no-training / bounded-retention API terms
       redact   — allowed only after local pseudonymisation (DeepSeek, unknown hosts)
       blocked  — never called (xAI)
  2. for `redact`, replaces names, phone numbers, emails and Indian identifiers
     with stable per-profile placeholders such as <PERSON_1>, using fast rules,
     the family name list, and the local OpenMed PII model;
  3. re-checks the outgoing payload with the rules and fails closed on a leak;
  4. restores placeholders in the reply before it reaches local tools or the user,
     except in arguments of tools that send text to the internet (search, HTTP);
  5. appends one line per cloud call to the profile's egress ledger.

Tier overrides: NARAD_PROVIDER_TIERS="deepseek=redact,nebius=trusted" or
NARAD_HOME/config/provider_tiers.json ({"nebius": "trusted"}).
Detector: NARAD_PII_DETECTOR=openmed (default) | rules. With `openmed` and the
model unavailable, `redact` destinations fail closed.
Extra names/addresses: NARAD_HOME/config/privacy_terms.json
({"PERSON": ["..."], "ADDRESS": ["..."]}) — gitignored runtime data.
"""
from __future__ import annotations

import copy
import hashlib
import json
import logging
import os
import re
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from narad_config import NARAD_HOME

log = logging.getLogger("narad.privacy")

LOCAL, TRUSTED, REDACT, BLOCKED = "local", "trusted", "redact", "blocked"
_TIERS = {LOCAL, TRUSTED, REDACT, BLOCKED}

_DEFAULT_TIERS: dict[str, str] = {
    "ollama": LOCAL,
    "ollama_chat": LOCAL,
    "narad-local": LOCAL,
    "anthropic": TRUSTED,
    "narad-claude-sdk": TRUSTED,
    "openai": TRUSTED,
    "gemini": TRUSTED,
    "vertex_ai": TRUSTED,
    "azure": TRUSTED,
    "bedrock": TRUSTED,
    "deepseek": REDACT,
    "nebius": REDACT,
    "fireworks_ai": REDACT,
    "together_ai": REDACT,
    "openrouter": REDACT,
    "deepinfra": REDACT,
    "groq": REDACT,
    "cerebras": REDACT,
    "mimo": REDACT,
    "typesafe": REDACT,  # Jev decision API; retention terms unverified
    "smallest": REDACT,  # Smallest.ai TTS; terms unconfirmed
    "sarvam": REDACT,  # trains on inputs unless the account opts out
    "custom": REDACT,
    "unknown": REDACT,
    "xai": BLOCKED,
}

# Tools whose arguments leave the Mac. Placeholders stay in their arguments so
# a restored name never reaches a search engine or an arbitrary URL.
OUTBOUND_TOOLS = frozenset({
    "web_search", "exa_search", "exa_contents", "search_last30days", "search_arxiv",
    "search_papers", "search_hf_papers", "search_hf_models", "query_deepwiki",
    "http_request", "browse_url", "tavily_search",
})

# Identity-bearing OpenMed labels. Dates and ages stay: health answers need them.
_ML_LABELS = {
    "NAME": "PERSON", "PERSON": "PERSON", "FIRSTNAME": "PERSON", "LASTNAME": "PERSON",
    "PATIENT": "PERSON", "DOCTOR": "PERSON", "USERNAME": "PERSON",
    "PHONE": "PHONE", "PHONENUMBER": "PHONE", "EMAIL": "EMAIL",
    "STREET": "ADDRESS", "ADDRESS": "ADDRESS", "ZIPCODE": "ADDRESS",
    "SSN": "ID", "ID_NUM": "ID", "MRN": "ID", "ACCOUNT_NUMBER": "ACCOUNT",
    "IBAN": "ACCOUNT", "CREDIT_CARD": "CARD", "AADHAAR": "AADHAAR", "PAN": "PAN",
}

# Relation words are how families talk about each other, not identifiers.
_NOT_NAMES = {
    "mom", "mum", "mother", "dad", "father", "papa", "mummy", "maa", "ma", "pa",
    "bhai", "didi", "dadi", "dada", "nani", "nana", "beta", "beti", "chachu",
    "chachi", "mama", "mami", "bua", "tau", "tai", "owner", "default", "family",
}

_PLACEHOLDER_RE = re.compile(r"<([A-Z]+)_(\d+)>")
_DEVANAGARI_DIGITS = str.maketrans("\u0966\u0967\u0968\u0969\u096a\u096b\u096c\u096d\u096e\u096f", "0123456789")
_WORD = r"\w\u0900-\u097F"


class PrivacyGatewayError(RuntimeError):
    """A call was refused because policy could not be satisfied."""


class PolicyBlocked(PrivacyGatewayError):
    """The destination is never called (owner policy)."""


class RedactorUnavailable(PrivacyGatewayError):
    """The configured local PII model is not installed or failed to load."""


class LeakBlocked(PrivacyGatewayError):
    """Personal details were still present after pseudonymisation."""


@dataclass(frozen=True)
class Span:
    start: int
    end: int
    label: str
    source: str
    canonical: str = ""  # one person known by several names shares one placeholder


# ── Tiers ─────────────────────────────────────────────────────────────────────

def provider_for_model(model: str) -> str:
    """Provider that actually receives the request (the host, not the model family)."""
    lower = (model or "").strip().lower()
    if not lower:
        return "unknown"
    try:
        from model_registry import custom_endpoint_model

        if model == custom_endpoint_model():
            return "custom"
    except Exception:
        pass
    prefix = lower.split("/", 1)[0] if "/" in lower else ""
    if prefix in _DEFAULT_TIERS or prefix in _tier_overrides():
        return prefix
    if prefix in {"google", "gemini"}:
        return "gemini"
    if "narad-local" in lower:
        return "narad-local"
    if "narad-claude-sdk" in lower:
        return "narad-claude-sdk"
    if "grok" in lower or "xai" in lower:
        return "xai"
    if "deepseek" in lower:
        return "deepseek"
    if "claude" in lower or "anthropic" in lower:
        return "anthropic"
    if "gemini" in lower:
        return "gemini"
    if lower.startswith(("gpt", "o1", "o3", "o4", "text-embedding")):
        return "openai"
    if "mimo" in lower:
        return "mimo"
    return "unknown"


_overrides_cache: tuple[float, dict[str, str]] = (0.0, {})


def _tier_overrides() -> dict[str, str]:
    global _overrides_cache
    overrides: dict[str, str] = {}
    path = NARAD_HOME / "config" / "provider_tiers.json"
    try:
        mtime = path.stat().st_mtime
    except OSError:
        mtime = 0.0
    if mtime and mtime == _overrides_cache[0]:
        overrides.update(_overrides_cache[1])
    elif mtime:
        try:
            loaded = json.loads(path.read_text())
            file_overrides = {
                str(k).lower(): str(v).lower()
                for k, v in loaded.items()
                if str(v).lower() in _TIERS
            }
        except Exception:
            file_overrides = {}
        _overrides_cache = (mtime, file_overrides)
        overrides.update(file_overrides)
    for item in os.environ.get("NARAD_PROVIDER_TIERS", "").split(","):
        name, _, tier = item.partition("=")
        if name.strip() and tier.strip().lower() in _TIERS:
            overrides[name.strip().lower()] = tier.strip().lower()
    # Owner policy is not overridable: xAI stays blocked.
    overrides.pop("xai", None)
    return overrides


def provider_tier(model_or_provider: str) -> str:
    provider = (
        model_or_provider.lower()
        if model_or_provider.lower() in _DEFAULT_TIERS
        else provider_for_model(model_or_provider)
    )
    return _tier_overrides().get(provider) or _DEFAULT_TIERS.get(provider, REDACT)


# ── Detectors ─────────────────────────────────────────────────────────────────

_VERHOEFF_D = [
    [0, 1, 2, 3, 4, 5, 6, 7, 8, 9], [1, 2, 3, 4, 0, 6, 7, 8, 9, 5],
    [2, 3, 4, 0, 1, 7, 8, 9, 5, 6], [3, 4, 0, 1, 2, 8, 9, 5, 6, 7],
    [4, 0, 1, 2, 3, 9, 5, 6, 7, 8], [5, 9, 8, 7, 6, 0, 4, 3, 2, 1],
    [6, 5, 9, 8, 7, 1, 0, 4, 3, 2], [7, 6, 5, 9, 8, 2, 1, 0, 4, 3],
    [8, 7, 6, 5, 9, 3, 2, 1, 0, 4], [9, 8, 7, 6, 5, 4, 3, 2, 1, 0],
]
_VERHOEFF_P = [
    [0, 1, 2, 3, 4, 5, 6, 7, 8, 9], [1, 5, 7, 6, 2, 8, 3, 0, 9, 4],
    [5, 8, 0, 3, 7, 9, 6, 1, 4, 2], [8, 9, 1, 6, 0, 4, 3, 5, 2, 7],
    [9, 4, 5, 3, 1, 2, 6, 8, 7, 0], [4, 2, 8, 6, 5, 7, 3, 9, 0, 1],
    [2, 7, 9, 3, 8, 0, 6, 4, 1, 5], [7, 0, 4, 6, 9, 1, 3, 2, 5, 8],
]


def verhoeff_valid(digits: str) -> bool:
    check = 0
    for i, char in enumerate(reversed(digits)):
        check = _VERHOEFF_D[check][_VERHOEFF_P[i % 8][int(char)]]
    return check == 0


def luhn_valid(digits: str) -> bool:
    total = 0
    for i, char in enumerate(reversed(digits)):
        value = int(char)
        if i % 2:
            value *= 2
            if value > 9:
                value -= 9
        total += value
    return total % 10 == 0


_RULES: list[tuple[str, re.Pattern[str]]] = [
    ("EMAIL", re.compile(r"(?<![\w.+-])[\w.+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+")),
    # UPI VPA: handle has no dot (that is what separates it from an email).
    ("UPI", re.compile(r"(?<![\w.@-])[\w.-]{2,}@[A-Za-z][A-Za-z0-9]{1,}(?![\w.@-])")),
    ("PAN", re.compile(r"(?<![A-Za-z0-9])[A-Z]{3}[PCHFATBLJG][A-Z]\d{4}[A-Z](?![A-Za-z0-9])")),
    ("IFSC", re.compile(r"(?<![A-Za-z0-9])[A-Z]{4}0[A-Z0-9]{6}(?![A-Za-z0-9])")),
    ("PASSPORT", re.compile(
        r"(?i:passport)[^\n]{0,24}?(?<![A-Za-z0-9])(?P<value>[A-PR-WY][1-9]\d\s?\d{4}[1-9])(?![A-Za-z0-9])"
    )),
    ("AADHAAR", re.compile(r"(?<!\d)[2-9]\d{3}[\s-]?\d{4}[\s-]?\d{4}(?!\d)")),
    ("CARD", re.compile(r"(?<!\d)(?:\d[\s-]?){12,18}\d(?!\d)")),
    ("PHONE", re.compile(r"(?<![\w+])(?:(?:\+|00)91[\s-]?|0)?[6-9]\d{4}[\s-]?\d{5}(?!\d)")),
]


def _rule_spans(text: str) -> list[Span]:
    folded = text.translate(_DEVANAGARI_DIGITS)
    spans: list[Span] = []
    for label, pattern in _RULES:
        for match in pattern.finditer(folded):
            group = "value" if "value" in pattern.groupindex else 0
            start, end = match.span(group)
            digits = re.sub(r"\D", "", folded[start:end])
            if label == "AADHAAR" and (len(digits) != 12 or not verhoeff_valid(digits)):
                continue
            if label == "CARD" and not (13 <= len(digits) <= 19 and luhn_valid(digits)):
                continue
            spans.append(Span(start, end, label, "rules"))
    return spans


_TERMS_TTL_S = 30.0
_terms_cache: dict[str, Any] = {"key": None, "pattern": None, "labels": {}, "checked": 0.0}
_terms_lock = threading.Lock()


def _family_terms() -> dict[str, tuple[str, str]]:
    """term -> (label, canonical) for family display names plus extra terms.

    A name part that belongs to exactly one person (a first name) maps to that
    person's full name, so "Asha" and "Asha Sharma" share one placeholder. A
    part shared by several people (a family surname) stands on its own.
    """
    terms: dict[str, tuple[str, str]] = {}
    names: list[str] = []
    try:
        from family_profiles import list_profiles

        names = [
            " ".join(str(profile.get("display_name") or "").split())
            for profile in list_profiles()
        ]
    except Exception:
        pass
    part_owners: dict[str, set[str]] = {}
    for name in filter(None, names):
        for part in name.split():
            part_owners.setdefault(part.lower(), set()).add(name)
    for name in filter(None, names):
        terms[name.lower()] = ("PERSON", name)
        for part in name.split():
            owners = part_owners[part.lower()]
            terms.setdefault(part.lower(), ("PERSON", name if len(owners) == 1 else part))
    extra = NARAD_HOME / "config" / "privacy_terms.json"
    try:
        loaded = json.loads(extra.read_text())
        for label, values in loaded.items():
            for value in values if isinstance(values, list) else []:
                # {"PERSON": ["Asha Sharma", {"name": "आशा", "same_as": "Asha Sharma"}]}
                if isinstance(value, dict):
                    clean = " ".join(str(value.get("name") or "").split())
                    canonical = " ".join(str(value.get("same_as") or clean).split())
                else:
                    clean = canonical = " ".join(str(value).split())
                if clean:
                    terms[clean.lower()] = (str(label).upper(), canonical)
    except Exception:
        pass
    return {
        term: entry
        for term, entry in terms.items()
        if len(term) >= 3 and term not in _NOT_NAMES
    }


def _terms_pattern() -> tuple[re.Pattern[str] | None, dict[str, tuple[str, str]]]:
    with _terms_lock:
        if time.monotonic() - _terms_cache["checked"] < _TERMS_TTL_S:
            return _terms_cache["pattern"], _terms_cache["labels"]
    terms = _family_terms()
    key = hashlib.sha256(json.dumps(sorted(terms.items())).encode()).hexdigest()
    with _terms_lock:
        _terms_cache["checked"] = time.monotonic()
        if _terms_cache["key"] != key:
            ordered = sorted(terms, key=len, reverse=True)
            pattern = (
                re.compile(
                    rf"(?<![{_WORD}])(?:{'|'.join(re.escape(t) for t in ordered)})(?![{_WORD}])",
                    re.IGNORECASE,
                )
                if ordered
                else None
            )
            _terms_cache.update(key=key, pattern=pattern, labels=terms)
        return _terms_cache["pattern"], _terms_cache["labels"]


def _term_spans(text: str) -> list[Span]:
    pattern, labels = _terms_pattern()
    if pattern is None:
        return []
    spans = []
    for match in pattern.finditer(text):
        label, canonical = labels.get(match.group(0).lower(), ("PERSON", match.group(0)))
        spans.append(Span(match.start(), match.end(), label, "names", canonical))
    return spans


class _OpenMedDetector:
    """Lazy wrapper over openmed.extract_pii with a per-chunk result cache."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._extract: Any = None
        self._error: str = ""
        self._cache: OrderedDict[str, list[tuple[int, int, str]]] = OrderedDict()

    def _load(self) -> Any:
        if self._extract is not None or self._error:
            return self._extract
        try:
            from openmed import extract_pii

            self._extract = extract_pii
        except Exception as exc:  # pragma: no cover - depends on optional install
            self._error = f"{type(exc).__name__}: {exc}"
        return self._extract

    def available(self) -> bool:
        with self._lock:
            return self._load() is not None

    def require(self) -> Any:
        with self._lock:
            extract = self._load()
        if extract is None:
            raise RedactorUnavailable(
                "The local OpenMed PII model is not available "
                f"({self._error or 'not installed'}). Install it with "
                "`pip install -e .[privacy]`, switch this provider to a trusted "
                "tier, or set NARAD_PII_DETECTOR=rules to accept rules-only redaction."
            )
        return extract

    def spans(self, text: str) -> list[Span]:
        spans: list[Span] = []
        offset = 0
        # Chunk by line so static prompt lines are cached across turns and
        # only new text reaches the model.
        for line in text.splitlines(keepends=True):
            if len(line.strip()) >= 3 and re.search(r"[A-Za-z\u0900-\u097F]", line):
                for start, end, label in self._chunk(line):
                    spans.append(Span(offset + start, offset + end, label, "openmed"))
            offset += len(line)
        return spans

    def _chunk(self, chunk: str) -> list[tuple[int, int, str]]:
        key = hashlib.sha256(chunk.encode("utf-8")).hexdigest()
        with self._lock:
            if key in self._cache:
                self._cache.move_to_end(key)
                return self._cache[key]
        extract = self.require()
        devanagari = bool(re.search(r"[\u0900-\u097F]", chunk))
        lang = "hi" if devanagari else "en"
        try:
            # Hinglish route first; it adds Roman-Hindi context patterns.
            result = extract(chunk, lang=lang, code_mixed=not devanagari, preserve_whitespace=True)
        except Exception:
            try:
                result = extract(chunk, lang=lang, preserve_whitespace=True)
            except Exception as exc:
                raise RedactorUnavailable(
                    f"OpenMed could not scan text: {type(exc).__name__}: {exc}"
                ) from exc
        found = [
            (int(e.start), int(e.end), _ML_LABELS[e.label.upper()])
            for e in getattr(result, "entities", [])
            if e.start is not None and e.end is not None and e.label.upper() in _ML_LABELS
        ]
        with self._lock:
            self._cache[key] = found
            while len(self._cache) > 4096:
                self._cache.popitem(last=False)
        return found


_openmed = _OpenMedDetector()


def warm_up() -> None:
    """Load the OpenMed model once, off the request path (startup thread)."""
    if detector_mode() != "openmed" or not _openmed.available():
        return
    try:
        started = time.perf_counter()
        _openmed.spans("Warm-up: Dr. Rao will call Asha tomorrow.")
        log.info("OpenMed PII model ready in %.1fs", time.perf_counter() - started)
    except Exception as exc:
        log.warning("OpenMed PII warm-up failed: %s", exc)


def detector_mode() -> str:
    mode = os.environ.get("NARAD_PII_DETECTOR", "openmed").strip().lower()
    return mode if mode in {"openmed", "rules"} else "openmed"


def redactor_ready() -> bool:
    return detector_mode() == "rules" or _openmed.available()


def find_spans(text: str, *, use_ml: bool) -> list[Span]:
    """Non-overlapping spans, longest first; existing placeholders are skipped."""
    if not text:
        return []
    candidates = _rule_spans(text) + _term_spans(text)
    if use_ml and detector_mode() == "openmed":
        candidates += _openmed.spans(text)
    protected = [(m.start(), m.end()) for m in _PLACEHOLDER_RE.finditer(text)]
    chosen: list[Span] = []
    priority = {"rules": 0, "names": 1, "openmed": 2}
    for span in sorted(candidates, key=lambda s: (-(s.end - s.start), priority[s.source], s.start)):
        if span.end <= span.start or not text[span.start:span.end].strip():
            continue
        if any(span.start < end and start < span.end for start, end in protected):
            continue
        if any(span.start < c.end and c.start < span.end for c in chosen):
            continue
        chosen.append(span)
    return sorted(chosen, key=lambda s: s.start)


# ── Pseudonyms ────────────────────────────────────────────────────────────────

class _PseudonymStore:
    """Stable placeholder <-> value map for one profile, persisted 0600 on the Mac."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.lock = threading.Lock()
        self.by_value: dict[str, str] = {}
        self.by_placeholder: dict[str, str] = {}
        self.counters: dict[str, int] = {}
        try:
            data = json.loads(path.read_text())
            self.by_placeholder = dict(data.get("placeholders", {}))
            self.counters = {k: int(v) for k, v in data.get("counters", {}).items()}
            self.by_value = {
                self._key(_PLACEHOLDER_RE.fullmatch(p).group(1), v): p
                for p, v in self.by_placeholder.items()
                if _PLACEHOLDER_RE.fullmatch(p)
            }
        except Exception:
            pass

    @staticmethod
    def _key(label: str, value: str) -> str:
        return f"{label}\x00{' '.join(value.split()).casefold()}"

    def placeholder(self, label: str, value: str) -> str:
        key = self._key(label, value)
        with self.lock:
            existing = self.by_value.get(key)
            if existing:
                return existing
            self.counters[label] = self.counters.get(label, 0) + 1
            placeholder = f"<{label}_{self.counters[label]}>"
            self.by_value[key] = placeholder
            self.by_placeholder[placeholder] = value
            self._save()
            return placeholder

    def original(self, placeholder: str) -> str | None:
        return self.by_placeholder.get(placeholder)

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(f".{os.getpid()}.{threading.get_ident()}.tmp")
        tmp.write_text(json.dumps({"placeholders": self.by_placeholder, "counters": self.counters}))
        os.chmod(tmp, 0o600)
        tmp.replace(self.path)


_stores: dict[str, _PseudonymStore] = {}
_stores_lock = threading.Lock()


def _profile_id() -> str:
    try:
        from profile_context import current_profile_id

        return current_profile_id()
    except Exception:
        return "default"


def _privacy_dir(profile_id: str | None = None) -> Path:
    from profile_context import profile_root

    return profile_root(profile_id) / "privacy"


def _store(profile_id: str | None = None) -> _PseudonymStore:
    pid = profile_id or _profile_id()
    with _stores_lock:
        store = _stores.get(pid)
        if store is None:
            store = _PseudonymStore(_privacy_dir(pid) / "pseudonyms.json")
            _stores[pid] = store
        return store


def redact_text(text: str, *, use_ml: bool = True, counts: dict[str, int] | None = None) -> str:
    spans = find_spans(text, use_ml=use_ml)
    if not spans:
        return text
    store = _store()
    pieces: list[str] = []
    cursor = 0
    for span in spans:
        pieces.append(text[cursor:span.start])
        pieces.append(store.placeholder(span.label, span.canonical or text[span.start:span.end]))
        cursor = span.end
        if counts is not None:
            counts[span.label] = counts.get(span.label, 0) + 1
    pieces.append(text[cursor:])
    return "".join(pieces)


def restore_text(text: str) -> str:
    if not text or "<" not in text:
        return text
    store = _store()
    return _PLACEHOLDER_RE.sub(lambda m: store.original(m.group(0)) or m.group(0), text)


def leak_labels(text: str) -> list[str]:
    """Rules and family names still present in outgoing text (no ML; fast)."""
    return sorted({span.label for span in find_spans(text, use_ml=False)})


def _walk(value: Any, fn: Any) -> Any:
    if isinstance(value, str):
        return fn(value)
    if isinstance(value, dict):
        return {k: _walk(v, fn) for k, v in value.items()}
    if isinstance(value, list):
        return [_walk(v, fn) for v in value]
    if isinstance(value, tuple):
        return tuple(_walk(v, fn) for v in value)
    return value


# ── Egress ledger ─────────────────────────────────────────────────────────────

_ledger_lock = threading.Lock()


def record_egress(
    *,
    model: str,
    source: str,
    tier: str,
    entities: dict[str, int] | None = None,
    chars: int = 0,
    blocked: str = "",
) -> None:
    entry = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "profile": _profile_id(),
        "source": source,
        "model": model,
        "provider": provider_for_model(model),
        "tier": tier,
        "detector": detector_mode() if tier == REDACT else "",
        "entities": entities or {},
        "chars": chars,
        "blocked": blocked,
    }
    try:
        path = _privacy_dir() / "egress.jsonl"
        with _ledger_lock:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
            os.chmod(path, 0o600)
    except Exception as exc:
        log.warning("Egress ledger write failed: %s", exc)


def recent_egress(limit: int = 50, profile_id: str | None = None) -> list[dict[str, Any]]:
    path = _privacy_dir(profile_id) / "egress.jsonl"
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    rows: list[dict[str, Any]] = []
    for line in lines[-max(1, min(limit, 500)):]:
        try:
            rows.append(json.loads(line))
        except ValueError:
            continue
    return list(reversed(rows))


# ── ADK request/response ──────────────────────────────────────────────────────

def _check_outgoing(serialized: str, model: str, source: str, counts: dict[str, int]) -> None:
    leaks = leak_labels(serialized)
    if leaks:
        record_egress(model=model, source=source, tier=REDACT, entities=counts,
                      chars=len(serialized), blocked="leak:" + ",".join(leaks))
        raise LeakBlocked(f"Refused to send {', '.join(leaks)} to {model} after pseudonymisation.")


def prepare_llm_request(llm_request: Any, model: str, *, source: str = "agent") -> str:
    """Pseudonymise an ADK LlmRequest in place for `model`; returns the tier.

    Callers pass a copy: the session history keeps the real values.
    """
    tier = provider_tier(model)
    if tier == BLOCKED:
        record_egress(model=model, source=source, tier=tier, blocked="policy")
        raise PolicyBlocked(f"{model} is blocked by owner policy.")
    if tier != REDACT:
        if tier != LOCAL:
            record_egress(model=model, source=source, tier=tier,
                          chars=len(_serialize_request(llm_request)))
        return tier
    if not redactor_ready():
        record_egress(model=model, source=source, tier=tier, blocked="redactor_unavailable")
        _openmed.require()
    counts: dict[str, int] = {}

    def scrub(text: str) -> str:
        return redact_text(text, counts=counts)

    for content in getattr(llm_request, "contents", None) or []:
        for part in getattr(content, "parts", None) or []:
            if getattr(part, "inline_data", None) is not None or getattr(part, "file_data", None) is not None:
                record_egress(model=model, source=source, tier=tier, blocked="media")
                raise PrivacyGatewayError(
                    f"Images and files cannot be pseudonymised, so they are never sent to {model}."
                )
            if getattr(part, "text", None):
                part.text = scrub(part.text)
            call = getattr(part, "function_call", None)
            if call is not None and call.args:
                call.args = _walk(dict(call.args), scrub)
            response = getattr(part, "function_response", None)
            if response is not None and response.response:
                response.response = _walk(dict(response.response), scrub)
    config = getattr(llm_request, "config", None)
    instruction = getattr(config, "system_instruction", None) if config is not None else None
    if isinstance(instruction, str):
        config.system_instruction = scrub(instruction)
    elif instruction is not None and getattr(instruction, "parts", None):
        for part in instruction.parts:
            if getattr(part, "text", None):
                part.text = scrub(part.text)
    serialized = _serialize_request(llm_request)
    _check_outgoing(serialized, model, source, counts)
    record_egress(model=model, source=source, tier=tier, entities=counts, chars=len(serialized))
    return tier


def _serialize_request(llm_request: Any) -> str:
    parts: list[str] = []
    config = getattr(llm_request, "config", None)
    instruction = getattr(config, "system_instruction", None) if config is not None else None
    if isinstance(instruction, str):
        parts.append(instruction)
    elif instruction is not None:
        parts.extend(p.text for p in getattr(instruction, "parts", None) or [] if getattr(p, "text", None))
    for content in getattr(llm_request, "contents", None) or []:
        for part in getattr(content, "parts", None) or []:
            if getattr(part, "text", None):
                parts.append(part.text)
            call = getattr(part, "function_call", None)
            if call is not None and call.args:
                parts.append(json.dumps(dict(call.args), ensure_ascii=False, default=str))
            response = getattr(part, "function_response", None)
            if response is not None and response.response:
                parts.append(json.dumps(dict(response.response), ensure_ascii=False, default=str))
    return "\n".join(parts)


def restore_llm_response(llm_response: Any) -> Any:
    """Put real values back into a reply before local code or the user sees it."""
    content = getattr(llm_response, "content", None)
    for part in getattr(content, "parts", None) or []:
        if getattr(part, "text", None):
            part.text = restore_text(part.text)
        call = getattr(part, "function_call", None)
        if call is not None and call.args and call.name not in OUTBOUND_TOOLS:
            call.args = _walk(dict(call.args), restore_text)
    return llm_response


# ── Plain LiteLLM calls (background learners, judges) ─────────────────────────

def completion(**kwargs: Any) -> Any:
    """litellm.completion with the same policy as agent calls."""
    import litellm

    kwargs = dict(kwargs)
    model = str(kwargs.get("model") or "")
    source = str(kwargs.pop("narad_source", "background"))
    tier = provider_tier(model)
    if tier == BLOCKED:
        record_egress(model=model, source=source, tier=tier, blocked="policy")
        raise PolicyBlocked(f"{model} is blocked by owner policy.")
    messages = kwargs.get("messages") or []
    if tier == REDACT:
        if not redactor_ready():
            record_egress(model=model, source=source, tier=tier, blocked="redactor_unavailable")
            _openmed.require()
        counts: dict[str, int] = {}
        kwargs["messages"] = _walk(copy.deepcopy(messages), lambda t: redact_text(t, counts=counts))
        serialized = json.dumps(kwargs["messages"], ensure_ascii=False, default=str)
        _check_outgoing(serialized, model, source, counts)
        record_egress(model=model, source=source, tier=tier, entities=counts, chars=len(serialized))
        response = litellm.completion(**kwargs)
        for choice in getattr(response, "choices", None) or []:
            message = getattr(choice, "message", None)
            if message is not None and isinstance(getattr(message, "content", None), str):
                message.content = restore_text(message.content)
        return response
    if tier != LOCAL:
        record_egress(model=model, source=source, tier=tier,
                      chars=len(json.dumps(messages, ensure_ascii=False, default=str)))
    return litellm.completion(**kwargs)


def raw_allowed(provider: str) -> bool:
    """Whether content that cannot be pseudonymised may go to `provider` at all."""
    return provider_tier(provider) in (LOCAL, TRUSTED)


def allow_raw(provider: str, *, source: str, chars: int = 0) -> bool:
    """Gate audio, images and text read aloud: only `local` or `trusted` destinations.

    Placeholders cannot be spoken or drawn, so `redact`-tier providers never
    receive this content; the refusal is logged and the caller falls back.
    """
    tier = provider_tier(provider)
    if tier == LOCAL:
        return True
    if tier == TRUSTED:
        record_egress(model=provider, source=source, tier=tier, chars=chars)
        return True
    record_egress(model=provider, source=source, tier=tier,
                  blocked="policy" if tier == BLOCKED else "raw_content")
    return False


def guard_payload(provider: str, payload: Any, *, source: str) -> Any:
    """A JSON-like payload that may be sent to `provider` (pseudonymised if required)."""
    tier = provider_tier(provider)
    if tier == BLOCKED:
        record_egress(model=provider, source=source, tier=tier, blocked="policy")
        raise PolicyBlocked(f"{provider} is blocked by owner policy.")
    if tier != REDACT:
        if tier != LOCAL:
            record_egress(model=provider, source=source, tier=tier,
                          chars=len(json.dumps(payload, ensure_ascii=False, default=str)))
        return payload
    if not redactor_ready():
        record_egress(model=provider, source=source, tier=tier, blocked="redactor_unavailable")
        _openmed.require()
    counts: dict[str, int] = {}
    cleaned = _walk(copy.deepcopy(payload), lambda text: redact_text(text, counts=counts))
    serialized = json.dumps(cleaned, ensure_ascii=False, default=str)
    _check_outgoing(serialized, provider, source, counts)
    record_egress(model=provider, source=source, tier=tier, entities=counts, chars=len(serialized))
    return cleaned


def guard_texts(provider_or_model: str, texts: Iterable[str], *, source: str = "embedding") -> list[str]:
    """Texts that may be sent to an embedding provider (pseudonymised if required)."""
    items = list(texts)
    tier = provider_tier(provider_or_model)
    if tier == BLOCKED:
        record_egress(model=provider_or_model, source=source, tier=tier, blocked="policy")
        raise PolicyBlocked(f"{provider_or_model} is blocked by owner policy.")
    if tier != REDACT:
        if tier != LOCAL:
            record_egress(model=provider_or_model, source=source, tier=tier, chars=sum(map(len, items)))
        return items
    if not redactor_ready():
        record_egress(model=provider_or_model, source=source, tier=tier, blocked="redactor_unavailable")
        _openmed.require()
    counts: dict[str, int] = {}
    cleaned = [redact_text(text, counts=counts) for text in items]
    _check_outgoing("\n".join(cleaned), provider_or_model, source, counts)
    record_egress(model=provider_or_model, source=source, tier=tier, entities=counts,
                  chars=sum(map(len, cleaned)))
    return cleaned


def embedding(**kwargs: Any) -> Any:
    """litellm.embedding with inputs passed through guard_texts."""
    import litellm

    kwargs = dict(kwargs)
    source = str(kwargs.pop("narad_source", "embedding"))
    inputs = kwargs.get("input")
    single = isinstance(inputs, str)
    guarded = guard_texts(str(kwargs.get("model") or ""), [inputs] if single else list(inputs or []), source=source)
    kwargs["input"] = guarded[0] if single else guarded
    return litellm.embedding(**kwargs)
