"""Intent → path: which of the six paths a message looks like (table-driven,
English, Hinglish and Hindi), and when the pre-router offers one at all."""

from __future__ import annotations

import pytest
from path_intent import PATH_INTENTS, match_path
from prerouter import TurnFacts, suggest_path

POSITIVES = [
    # Career
    ("I am looking for a new job in Bengaluru", "career"),
    ("help me find a job as a product manager", "career"),
    ("I want to switch jobs this year", "career"),
    ("naukri chahiye mujhe jaldi", "career"),
    ("mujhe nayi job dhoondhni hai", "career"),
    ("मुझे नई नौकरी चाहिए", "career"),
    # Health
    ("my blood report came today, what does it say", "health"),
    ("I want to lose weight by December", "health"),
    ("set up medicine reminders for my BP tablets", "health"),
    ("dawai yaad dilana roz subah", "health"),
    ("meri sugar report aayi hai", "health"),
    ("मेरी ब्लड रिपोर्ट देखो", "health"),
    # Travel
    ("plan a trip to Goa in December", "travel"),
    ("book flights to Delhi for next week", "travel"),
    ("hum Kerala ghoomne jaana chahte hain", "travel"),
    ("मुझे जापान की यात्रा करनी है", "travel"),
    # Teach
    ("I want to learn guitar properly", "teach"),
    ("mujhe python sikhna hai", "teach"),
    ("मुझे गिटार सीखना है", "teach"),
    # Finance
    ("here is my bank statement for August", "finance"),
    ("help me track my monthly expenses", "finance"),
    ("ghar ka kharcha kam karna hai", "finance"),
    ("मुझे हर महीने बचत करनी है", "finance"),
    # Documents
    ("make a presentation from this data", "documents"),
    ("turn these notes into a board deck", "documents"),
    ("ppt banana hai kal ke liye", "documents"),
]

NEGATIVES = [
    "hi",
    "what is hba1c?",
    "what is a savings goal",
    "Steve Jobs biography please",
    "thanks for the job done",
    "latest news on the job market",
    "that was a power trip",
    "is time travel possible",
    "find a budget airline to Goa",
    "union budget highlights",
    "my mother's blood report is here",
    "mummy ki sugar report aayi",
    "papa ka bp track karna hai",
    "meri maa की ब्लड रिपोर्ट",
    "I learned my lesson",
    "How much did it rain in Pune today?",
    "Email Ravi about the school trip",
    # Two paths at once: ambiguous, so no offer.
    "plan a trip with a monthly budget",
]


@pytest.mark.parametrize(("text", "workflow_id"), POSITIVES)
def test_a_clear_ask_matches_its_path(text: str, workflow_id: str) -> None:
    match = match_path(text)
    assert match is not None, text
    assert match.workflow_id == workflow_id
    assert match.phrase


@pytest.mark.parametrize("text", NEGATIVES)
def test_look_alikes_definitions_and_other_peoples_records_do_not_match(text: str) -> None:
    assert match_path(text) is None


def test_every_path_has_english_hinglish_and_hindi_phrases() -> None:
    covered = {workflow_id for _text, workflow_id in POSITIVES}
    assert covered == {intent.workflow_id for intent in PATH_INTENTS}
    for intent in PATH_INTENTS:
        if intent.workflow_id != "documents":
            assert any(any("ऀ" <= ch <= "ॿ" for ch in pattern) for pattern in intent.positives), intent


def test_the_pre_router_never_offers_inside_a_bound_stage_or_a_lesson_check() -> None:
    assert suggest_path(TurnFacts(query="plan a trip to Goa in December")).workflow_id == "travel"
    assert suggest_path(TurnFacts(query="plan a trip to Goa in December", stage_owner="Rama")) is None
    assert suggest_path(TurnFacts(query="I want to learn guitar properly", check_answer=True)) is None


def test_a_bank_statement_csv_offers_personal_finance() -> None:
    offer = suggest_path(TurnFacts(
        query="please import this",
        attachments=[{"name": "hdfc_statement_aug.csv", "kind": "text", "mime_type": "text/csv"}],
    ))
    assert offer is not None
    assert (offer.workflow_id, offer.reason) == ("finance", "bank_statement_csv")
