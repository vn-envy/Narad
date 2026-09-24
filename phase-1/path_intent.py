"""Which of the six guided paths a chat message looks like, if any.

A table of English, Hinglish (Roman) and Hindi (Devanagari) phrases per path,
with negatives for the look-alikes ("budget airline", "power trip", "what is
HbA1c?"). It is deliberately conservative: a match only lets the app *offer* a
path on a small card (Start / Not now); nothing starts without the person's
tap. A message that looks like two paths, a definition question, or someone
else's records gets no offer. Pure functions; the server gathers the facts.
"""
from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class PathIntent:
    workflow_id: str
    positives: tuple[str, ...]
    negatives: tuple[str, ...] = ()


@dataclass(frozen=True)
class PathMatch:
    workflow_id: str
    phrase: str
    reason: str = "phrase"


# Devanagari has combining vowel signs that are not word characters to `re`,
# so Hindi terms are matched as plain substrings (no \b around them).
PATH_INTENTS: tuple[PathIntent, ...] = (
    PathIntent(
        "career",
        positives=(
            r"\b(?:looking|searching|hunting)\s+for\s+(?:a\s+)?(?:new\s+|better\s+)?(?:job|role|position)\b",
            r"\b(?:find|get)\s+(?:me\s+)?(?:a\s+)?(?:new\s+|better\s+)?job\b",
            r"\bjob\s+(?:search|hunt|hunting|change|switch)\b",
            r"\b(?:switch|change)\s+(?:my\s+)?(?:job|jobs|career)\b",
            r"\bcareer\s+(?:change|switch|move)\b",
            r"\bapply(?:ing)?\s+(?:for|to)\s+(?:\w+\s+){0,3}(?:jobs|roles|positions|openings)\b",
            r"\b(?:job|naukri|naukari)\s+(?:chahiye|dhoond\w*|dhund\w*|khoj\w*|badal\w*)\b",
            r"\b(?:nayi|new|dusri|doosri)\s+(?:naukri|naukari|job)\b",
            "नई नौकरी", "नौकरी चाहिए", "नौकरी ढूंढ", "नौकरी बदल",
        ),
        negatives=(r"\bsteve\s+jobs\b", r"\bjob\s+(?:done|well\s+done)\b", r"\bjobs?\s+report\b"),
    ),
    PathIntent(
        "health",
        positives=(
            r"\b(?:lab|blood|sugar|thyroid|lipid|cholesterol|urine|health)\s+(?:test\s+)?reports?\b",
            r"\bblood\s+tests?\b",
            r"\b(?:track|log|monitor|check)\w*\s+(?:my\s+)?(?:blood\s+)?(?:sugar|hba1c|bp|blood\s+pressure|cholesterol|weight)\b",
            r"\b(?:lose|losing|reduce|kam\s+karn\w*)\s+(?:my\s+)?weight\b|\bweight\s+(?:loss|kam)\b",
            r"\b(?:diet|fitness|health|exercise|workout)\s+plan\b",
            r"\bmedicines?\s+reminders?\b|\bremind\s+me\s+to\s+take\s+(?:my\s+)?(?:medicine|tablets?|pills?|dose)\b",
            r"\b(?:dawai|dawa|dawaai|goli)\s+(?:yaad|reminder|time)\b",
            "ब्लड रिपोर्ट", "लैब रिपोर्ट", "जांच की रिपोर्ट", "दवाई", "शुगर", "वजन कम",
        ),
        negatives=(
            # Someone else's records would land in this person's own profile.
            r"\b(?:mother|father|mom|dad|mum|maa|papa|wife|husband|son|daughter|beti|beta|friend|brother|sister)(?:'s)?\s+"
            r"(?:\w+\s+){0,2}(?:reports?|tests?|sugar|weight|medicines?)\b",
            r"\b(?:mummy|mumma|mom|maa|papa|dad|bhai|behen|didi|nani|dadi|nana|dada|beti|beta|pati|patni)"
            r"\s+(?:ki|ka|ke|की|का|के)(?:\s|$)",
            "माँ की", "मां की", "मम्मी की", "पापा की", "पिताजी की", "पति की", "पत्नी की", "बेटी की", "बेटे की",
            r"\bhealth\s+insurance\b",
        ),
    ),
    PathIntent(
        "travel",
        positives=(
            r"\b(?:plan|planning|book|booking)\b[^.?!\n]{0,30}\b(?:trip|holiday|vacation|honeymoon|flights?|hotel)\b",
            r"\b(?:trip|holiday|vacation|honeymoon|getaway)\s+(?:to|in|for)\s+\w",
            r"\bitinerary\b",
            r"\b(?:flights?|train\s+tickets?|air\s+tickets?)\s+(?:to|from)\s+\w",
            r"\b(?:ghoomne|ghumne|ghoomna|ghumna|safar|yatra)\b",
            r"\bchutti(?:yon|yan)?\s+(?:me|mein)\s+\w+\s+(?:jaana|jana|jaenge|jayenge)\b",
            "घूमने", "यात्रा",
        ),
        negatives=(
            r"\b(?:power|ego|guilt)\s+trip\b", r"\btime\s+travel\b", r"\bflight\s+of\s+stairs\b",
            r"\btrip(?:ped|ping)\s+(?:over|on)\b", r"\bflight\s+mode\b",
        ),
    ),
    PathIntent(
        "teach",
        positives=(
            r"\b(?:i\s+want\s+to|i'?d\s+like\s+to|help\s+me)\s+learn\b",
            r"\bteach\s+me\b",
            r"\b(?:sikhna|seekhna|sikhni|seekhni)\s+(?:hai|chahta|chahti)\b",
            r"\bsikhao\b|\bsikha\s+(?:do|dijiye)\b",
            "सीखना", "सिखाओ", "सिखा दो",
        ),
        negatives=(r"\blearn(?:ed|t)?\s+(?:my|a|the)\s+lesson\b", r"\blearn\s+from\s+(?:my\s+)?mistakes?\b"),
    ),
    PathIntent(
        "finance",
        positives=(
            r"\bbank\s+statements?\b",
            r"\b(?:track|manage|control|cut|reduce)\w*\s+(?:my\s+)?(?:\w+\s+)?(?:expenses|spending|kharch\w*)\b",
            r"\b(?:monthly|household|family|my)\s+budget\b|\bmake\s+(?:a\s+)?budget\b|\bbudget\s+(?:banana|banao|bana\s+do)\b",
            r"\b(?:emergency\s+fund|savings?\s+goal|save\s+(?:money|up)\s+for)\b",
            r"\b(?:kharcha|kharche|kharchon|bachat|paise\s+bachan\w*)\b",
            "खर्चे", "खर्चों", "बचत", "बजट",
        ),
        negatives=(
            r"\bbudget\s+(?:airline|hotel|flight|stay|phone|laptop|trip|friendly)\b",
            r"\b(?:company|government|union|state)\s+budget\b",
        ),
    ),
    PathIntent(
        "documents",
        positives=(
            r"\b(?:presentation|slide\s*deck|slides|deck|report|ppt)\b[^.?!\n]{0,40}\b(?:from|using|out\s+of|based\s+on)\b"
            r"[^.?!\n]{0,30}\b(?:data|files?|documents?|notes|spreadsheets?|csv|pdfs?|these|this|attached)\b",
            r"\b(?:turn|convert)\b[^.?!\n]{0,40}\binto\s+(?:a\s+|an\s+)?(?:presentation|deck|slides|report|story)\b",
            r"\b(?:board|pitch)\s+deck\b",
            r"\b(?:ppt|presentation)\s+(?:banana|banao|bana\s+do)\b",
        ),
    ),
)

# Messages that are about a thing, not a plan to act on it.
_GLOBAL_NEGATIVES = (
    r"^\s*(?:what|who)\s*(?:'s|\s+is|\s+are|\s+was|\s+were)\s+(?:a\s+|an\s+|the\s+)?[\w\s-]{1,30}\??\s*$",
    r"^\s*(?:define|meaning\s+of)\b",
    r"\b(?:news|headlines)\b",
)
_MIN_WORDS = 3

_COMPILED = tuple(
    (
        intent.workflow_id,
        tuple(re.compile(pattern, re.IGNORECASE) for pattern in intent.positives),
        tuple(re.compile(pattern, re.IGNORECASE) for pattern in intent.negatives),
    )
    for intent in PATH_INTENTS
)
_GLOBAL = tuple(re.compile(pattern, re.IGNORECASE) for pattern in _GLOBAL_NEGATIVES)


def match_path(text: str) -> PathMatch | None:
    """The one path this message looks like, or None (no match, or ambiguous)."""
    query = " ".join(str(text or "").split())
    if len(query.split()) < _MIN_WORDS or any(pattern.search(query) for pattern in _GLOBAL):
        return None
    found: list[PathMatch] = []
    for workflow_id, positives, negatives in _COMPILED:
        if any(pattern.search(query) for pattern in negatives):
            continue
        hit = next((match for pattern in positives if (match := pattern.search(query))), None)
        if hit:
            found.append(PathMatch(workflow_id=workflow_id, phrase=hit.group(0).strip()[:80]))
    return found[0] if len(found) == 1 else None
