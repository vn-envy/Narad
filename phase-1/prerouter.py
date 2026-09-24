"""Deterministic pre-router: skip the supervisor's routing call when the owner is obvious.

A turn whose owning avatar is clear from structure alone (a bound workflow
stage, /teach, a bank-statement CSV, a document with a "summarise this" ask, a
bare URL) goes straight to that avatar through the same avatar tool, so memory,
sutras, tracing, Andon and the privacy gateway all still apply. Everything else
goes to the supervisor as before.

The rules are deliberately conservative: a miss costs one routing call, a
wrong route costs a wrong answer. Pure functions only — the caller gathers the
facts. NARAD_PREROUTER=off disables it.

``suggest_path`` reads the same facts to recognise one of the six guided paths
(path_intent.py); the server only offers it on a card, never starts it.
"""
from __future__ import annotations

import os
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

from path_intent import PathMatch, match_path

AVATARS = ("Matsya", "Rama", "Krishna", "Parashurama")

# Parashurama is never pre-routed: stage context names tools and formats, and
# pre-specifying those for Parashurama bypasses its phase-gated skills.
_STAGE_OWNERS = frozenset({"Matsya", "Rama", "Krishna"})

_TEACH_COMMAND_RE = re.compile(r"^\s*(?:/teach\b|teach me\b)", re.IGNORECASE)
_URL_ONLY_RE = re.compile(r"^\s*<?https?://[^\s<>]+>?\s*$", re.IGNORECASE)
_DOCUMENT_ASK_RE = re.compile(
    r"\b(?:summari[sz]e|summary|tl;?dr|key points|what do(?:es)? (?:this|it|these|they|the|my)\b[^?]{0,40}\bsay"
    r"|what(?:'s| is) in (?:this|it|the \w+)|read (?:this|it)(?: out)?|go through (?:this|it)"
    r"|extract|review the attached inputs)\b",
    re.IGNORECASE,
)
# Asks that add a second deliverable or a different owner: leave to the supervisor.
_OTHER_DELIVERABLE_RE = re.compile(
    r"\b(?:e-?mail|mail|send|draft|write|reply|post|tweet|slides?|deck|presentation|video"
    r"|plan|schedule|remind|calendar|code|script|fix|debug|import|budget|translate|compare"
    r"|should i|teach|quiz|flashcards?)\b",
    re.IGNORECASE,
)
_BANK_TERMS_RE = re.compile(
    r"\b(?:bank|statement|passbook|transactions?|spend(?:ing)?|expenses?|upi"
    r"|hdfc|icici|sbi|axis|kotak)\b",
    re.IGNORECASE,
)
# CSV asks that belong elsewhere: models and portfolios (Matsya/Parashurama),
# pipelines and analytics code (Parashurama).
_NOT_PERSONAL_FINANCE_RE = re.compile(
    r"\b(?:pipeline|etl|schema|sql|database|script|code|dashboard|dcf|irr|portfolio"
    r"|backtest|forecast model|earnings)\b",
    re.IGNORECASE,
)
_CSV_SUFFIXES = (".csv", ".tsv")
_DOCUMENT_KINDS = frozenset({"document", "image", "text"})


@dataclass(frozen=True)
class TurnFacts:
    """What the server knows about a turn before any model call."""

    query: str
    attachments: list[Mapping[str, Any]] = field(default_factory=list)
    stage_owner: str = ""  # owner avatar of the bound workflow run's current stage
    check_answer: bool = False  # the learner is answering a pending Gurukul check


@dataclass(frozen=True)
class PreRoute:
    avatar: str
    reason: str


def enabled(env: Mapping[str, str] | None = None) -> bool:
    value = (env if env is not None else os.environ).get("NARAD_PREROUTER", "on")
    return value.strip().lower() not in {"off", "0", "false", "no"}


def _name(item: Mapping[str, Any]) -> str:
    return str(item.get("name") or item.get("relative_path") or "").strip().lower()


def _is_csv(item: Mapping[str, Any]) -> bool:
    mime = str(item.get("mime_type") or "").lower()
    return _name(item).endswith(_CSV_SUFFIXES) or mime in {"text/csv", "text/tab-separated-values"}


def _stage_owner(facts: TurnFacts) -> bool:
    return facts.stage_owner in _STAGE_OWNERS


def _teach_command(facts: TurnFacts) -> bool:
    return bool(_TEACH_COMMAND_RE.match(facts.query))


def _bank_statement_csv(facts: TurnFacts) -> bool:
    items = facts.attachments
    if not items or not all(_is_csv(item) for item in items):
        return False
    names = " ".join(re.sub(r"[_\-.]+", " ", _name(item)) for item in items)  # "hdfc_statement" → words
    return bool(_BANK_TERMS_RE.search(f"{facts.query} {names}")) and not _NOT_PERSONAL_FINANCE_RE.search(facts.query)


def _document_read(facts: TurnFacts) -> bool:
    items = facts.attachments
    if not items or not all(str(item.get("kind") or "") in _DOCUMENT_KINDS for item in items):
        return False
    if any(_is_csv(item) for item in items):
        return False
    return bool(_DOCUMENT_ASK_RE.search(facts.query)) and not _OTHER_DELIVERABLE_RE.search(facts.query)


def _url_only(facts: TurnFacts) -> bool:
    return not facts.attachments and bool(_URL_ONLY_RE.match(facts.query))


# First match wins. (reason, predicate, avatar); avatar None = the stage owner.
RULES: tuple[tuple[str, Callable[[TurnFacts], bool], str | None], ...] = (
    ("workflow_stage_owner", _stage_owner, None),
    ("teach_command", _teach_command, "Krishna"),
    ("learning_check_answer", lambda facts: facts.check_answer, "Krishna"),
    ("bank_statement_csv", _bank_statement_csv, "Rama"),
    ("document_read", _document_read, "Matsya"),
    ("url_only", _url_only, "Matsya"),
)


def route_turn(facts: TurnFacts) -> PreRoute | None:
    """The avatar that owns this turn, or None to let the supervisor decide."""
    for reason, applies, avatar in RULES:
        if applies(facts):
            return PreRoute(avatar=avatar or facts.stage_owner, reason=reason)
    return None


def suggest_path(facts: TurnFacts) -> PathMatch | None:
    """The guided path to *offer* for this turn (the server never starts one).

    Nothing while a path stage is bound to the thread or a lesson check is
    pending; a bank-statement CSV offers Personal Finance; otherwise the
    table-driven phrase match in path_intent decides.
    """
    if facts.stage_owner or facts.check_answer:
        return None
    if _bank_statement_csv(facts):
        return PathMatch(workflow_id="finance", phrase="bank statement", reason="bank_statement_csv")
    return match_path(facts.query)
