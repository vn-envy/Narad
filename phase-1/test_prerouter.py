"""The deterministic pre-router: which turns skip the supervisor's routing call.

A miss costs one routing call; a wrong route costs a wrong answer. So every
rule below has cases that must fire and near-misses that must fall through to
the supervisor (None).
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_r = next(p for p in Path(__file__).resolve().parents if (p / "narad_paths.py").exists())
sys.path[:0] = [str(_r)]  # narad root hop
import narad_paths  # noqa: F401  — registers all phase dirs; must precede phase imports

# isort: split
from prerouter import RULES, PreRoute, TurnFacts, enabled, route_turn

PDF = {"name": "blood-report.pdf", "kind": "document", "mime_type": "application/pdf"}
PHOTO = {"name": "prescription.jpg", "kind": "image", "mime_type": "image/jpeg"}
NOTES = {"name": "notes.txt", "kind": "text", "mime_type": "text/plain"}
BANK_CSV = {"name": "HDFC_statement_aug.csv", "kind": "data", "mime_type": "text/csv"}
PLAIN_CSV = {"name": "export.csv", "kind": "data", "mime_type": "text/csv"}
CODE = {"name": "app.py", "kind": "code", "mime_type": "text/x-python"}
ZIP = {"name": "photos.zip", "kind": "archive", "mime_type": "application/zip"}


def _route(query: str, attachments: list | None = None, **facts) -> PreRoute | None:
    return route_turn(TurnFacts(query=query, attachments=attachments or [], **facts))


@pytest.mark.parametrize(
    ("query", "attachments", "facts", "expected"),
    [
        # A bound workflow stage goes to its declared owner, whatever is asked.
        ("rank them", [], {"stage_owner": "Rama"}, ("Rama", "workflow_stage_owner")),
        ("find current roles", [], {"stage_owner": "Matsya"}, ("Matsya", "workflow_stage_owner")),
        ("I know plants need sunlight", [], {"stage_owner": "Krishna"}, ("Krishna", "workflow_stage_owner")),
        ("summarise this", [PDF], {"stage_owner": "Rama"}, ("Rama", "workflow_stage_owner")),
        # Teaching.
        ("/teach me photosynthesis", [], {}, ("Krishna", "teach_command")),
        ("Teach me how compound interest works", [], {}, ("Krishna", "teach_command")),
        ("light makes sugar in the leaf", [], {"check_answer": True}, ("Krishna", "learning_check_answer")),
        # A personal bank statement CSV.
        ("import this", [BANK_CSV], {}, ("Rama", "bank_statement_csv")),
        ("how much did I spend on food?", [PLAIN_CSV], {}, ("Rama", "bank_statement_csv")),
        ("add my bank transactions", [PLAIN_CSV], {}, ("Rama", "bank_statement_csv")),
        # A document, photo or text file with a reading ask.
        ("What does my blood report say?", [PDF], {}, ("Matsya", "document_read")),
        ("summarise this", [PDF, NOTES], {}, ("Matsya", "document_read")),
        ("what's in this?", [PHOTO], {}, ("Matsya", "document_read")),
        ("tl;dr please", [NOTES], {}, ("Matsya", "document_read")),
        ("Review the attached inputs and summarize what matters.", [PDF], {}, ("Matsya", "document_read")),
        # A bare URL.
        ("https://example.com/menu", [], {}, ("Matsya", "url_only")),
        ("  <https://example.com/a/b?c=d>  ", [], {}, ("Matsya", "url_only")),
    ],
)
def test_unambiguous_turns_are_pre_routed(query, attachments, facts, expected) -> None:
    route = _route(query, attachments, **facts)
    assert route is not None
    assert (route.avatar, route.reason) == expected


@pytest.mark.parametrize(
    ("query", "attachments", "facts"),
    [
        # Ordinary questions are the supervisor's call.
        ("hi", [], {}),
        ("What's the weather in Pune?", [], {}),
        ("what is photosynthesis", [], {}),
        ("Can you teach Asha to swim?", [], {}),
        # Parashurama is never pre-routed (never pre-solve its tasks); no owner, no rule.
        ("analyse the sources", [], {"stage_owner": "Parashurama"}),
        ("go on", [], {"stage_owner": ""}),
        ("go on", [], {"stage_owner": "Varaha"}),
        # A second deliverable or another owner's verb.
        ("summarise this and email it to Asha", [PDF], {}),
        ("summarise this into slides", [PDF], {}),
        ("extract the dates and add them to my calendar", [PDF], {}),
        ("read this and fix the bug", [NOTES], {}),
        # Documents without a reading ask.
        ("is this a good offer?", [PDF], {}),
        ("", [PDF], {}),
        # Attachments the rules do not cover.
        ("summarise this", [CODE], {}),
        ("summarise this", [ZIP], {}),
        ("summarise this", [PDF, CODE], {}),
        ("summarise this", [PLAIN_CSV], {}),
        # CSVs without a personal-finance signal, or with an engineering one.
        ("clean this up", [PLAIN_CSV], {}),
        ("build an ETL pipeline for my bank transactions", [BANK_CSV], {}),
        ("run a DCF on this statement", [BANK_CSV], {}),
        ("import these", [BANK_CSV, PDF], {}),
        # URLs with a question, or with attachments.
        ("compare https://example.com/a with https://example.com/b", [], {}),
        ("what does https://example.com say about fees?", [], {}),
        ("https://example.com/menu", [PDF], {}),
        ("example.com/menu", [], {}),
    ],
)
def test_ambiguous_turns_go_to_the_supervisor(query, attachments, facts) -> None:
    assert _route(query, attachments, **facts) is None


def test_rules_are_ordered_most_specific_first() -> None:
    assert [reason for reason, _applies, _avatar in RULES] == [
        "workflow_stage_owner",
        "teach_command",
        "learning_check_answer",
        "bank_statement_csv",
        "document_read",
        "url_only",
    ]
    for _reason, _applies, avatar in RULES:
        assert avatar in (None, "Matsya", "Rama", "Krishna")


@pytest.mark.parametrize(
    ("value", "expected"),
    [(None, True), ("on", True), ("1", True), ("off", False), ("OFF", False), ("0", False), ("false", False)],
)
def test_narad_prerouter_env_gate(value, expected) -> None:
    env = {} if value is None else {"NARAD_PREROUTER": value}
    assert enabled(env) is expected
