"""
Risk policy v2: one table decides which actions wait for a person's approval.

Only commit-class actions need an Anumati approval: send, pay, book, buy,
apply, submit a form (it may carry personal data), upload, delete, change an
account setting, post publicly, type a secret, and anything Narad cannot see
well enough to classify (a click at bare coordinates, a submit button with no
label). Reading, scrolling, navigating, typing into an ordinary field,
searching, filtering, sorting, paging, cookie and consent banners, and opening
a sign-in page are benign and run without asking.

Rules are checked in order and the first match wins, so narrow benign phrases
("apply filters", "continue to payment", "accept cookies") sit above the broad
commit verbs they contain. Hindi and Hinglish labels are covered where the
word is unambiguous ("भेजें", "भुगतान", "bhejo", "pay karo").
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

READ = "read"
INPUT = "input"
BENIGN = "benign"
COMMIT = "commit"

# Word edges that also hold for Devanagari, whose vowel signs are not \w.
_EDGE_L = r"(?<![\wऀ-ॿ])"
_EDGE_R = r"(?![\wऀ-ॿ])"


def _phrases(*items: str) -> re.Pattern[str]:
    return re.compile(f"{_EDGE_L}(?:{'|'.join(items)}){_EDGE_R}", re.IGNORECASE)


def _whole_label(*items: str) -> re.Pattern[str]:
    """Match only when the label is exactly one of ``items`` (an arrow may follow)."""
    return re.compile(rf"^\s*(?:{'|'.join(items)})\s*[›»>→]?\s*$", re.IGNORECASE)


@dataclass(frozen=True)
class Rule:
    category: str
    risk: str
    pattern: re.Pattern[str]


@dataclass(frozen=True)
class Verdict:
    risk: str  # read | input | benign | commit
    category: str  # what kind of read/input/benign/commit, e.g. "pay", "search"
    reason: str

    @property
    def needs_approval(self) -> bool:
        return self.risk == COMMIT


# Plain-language names for commit categories, used on approval cards.
COMMIT_LABELS: dict[str, str] = {
    "pay": "Payment",
    "buy": "Purchase",
    "book": "Booking",
    "apply": "Application",
    "send": "Send",
    "post": "Public post",
    "delete": "Delete",
    "account": "Account change",
    "sign": "Signature",
    "submit": "Form submission",
    "upload": "Upload",
    "sensitive_input": "Secret or ID entry",
    "unclassified": "Unrecognised action",
    "flagged": "Flagged step",
    "injection": "Suspicious page",
    "desktop_input": "Desktop control",
    "phone_task": "Phone task",
    "sensitive": "Sensitive task",
    "workflow_stage": "Path step",
}

_MONEY = r"(?:₹|rs\.?|inr|\$|usd)\s?[\d,.]+"

# Control labels: buttons, links, and the element an Enter key lands on.
LABEL_RULES: tuple[Rule, ...] = (
    # Consent banners: accepting or refusing cookies commits nothing.
    Rule("consent", BENIGN, _phrases(
        r"cookies?", "consent", r"privacy (?:choices|settings|preferences)",
        r"confirm (?:my |your )?(?:choices|selection|preferences)", r"accept all", r"reject all",
        r"allow all", r"deny all", r"(?:only |strictly )?necessary(?: only)?", r"essential only",
        r"manage (?:preferences|options)", r"save (?:my )?preferences",
    )),
    # The last button of a checkout or booking is the commit itself.
    Rule("pay", COMMIT, _phrases(
        r"(?:complete|confirm|finish|place|submit) (?:your |my |the )?(?:checkout|purchase|order|payment)",
        r"checkout now", r"buy now", r"order now",
    )),
    Rule("book", COMMIT, _phrases(
        r"(?:complete|confirm|finish) (?:your |my |the )?(?:booking|reservation|appointment)",
    )),
    # Moving between steps: the commit is the final button, not the way to it.
    # A bare "Next" or "Continue" counts only as the whole label, so "Continue
    # to pay ₹6,000" still reaches the pay rule below.
    Rule("step", BENIGN, _phrases(
        r"(?:continue|proceed|go|move on) to (?:the )?(?:checkout|payment page|cart|basket|review|summary|"
        r"next (?:step|page)|shipping|delivery|address|details|seat selection)",
        r"(?:view|go to|open) (?:your |my |the )?(?:cart|basket|bag)", "checkout",
    )),
    Rule("step", BENIGN, _whole_label(
        "next", "continue", "previous", "prev", "back", "go back", "आगे", "पीछे", "aage", "jaari rakhein",
    )),
    Rule("filter", BENIGN, _phrases(
        r"apply filters?", r"filters?", r"sort(?: by)?", r"clear (?:all|filters?)", r"reset filters?",
        "show results", "refine", "फ़िल्टर", "फिल्टर",
    )),
    Rule("auth", BENIGN, _phrases(
        r"sign ?in", r"log ?in", r"sign ?out", r"log ?out", r"forgot (?:your )?password",
        r"(?:sign|log) in with [\w ]{1,20}", "लॉग इन", "साइन इन", "लॉगिन",
    )),
    Rule("pagination", BENIGN, _phrases(
        r"page \d+", r"load more", r"show more", r"see more", r"view more", r"more results",
        r"next page", r"previous page", r"older (?:posts|messages|results)", r"newer (?:posts|messages|results)",
    )),
    Rule("pagination", BENIGN, _whole_label(r"\d{1,4}", "first", "last", "older", "newer", "‹", "›", "«", "»")),
    Rule("pay", COMMIT, _phrases(
        r"pay(?: now| later| securely| with [\w ]{1,20})?", rf"pay {_MONEY}", r"make (?:a )?payment",
        r"payment karo", r"pay karo", r"transfer(?: money| funds)?", r"send money", r"add money",
        "recharge", "donate", "purchase", "place (?:your |my |an )?order", "भुगतान(?: करें)?", "bhugtan",
        r"upi pay", r"pay via upi",
    )),
    # A cart is reversible; paying for it is the commit.
    Rule("cart", BENIGN, _phrases(r"add to (?:cart|basket|bag)", r"remove from (?:cart|basket|bag)")),
    Rule("buy", COMMIT, _phrases(
        "buy", "खरीदें", "खरीदो", "kharido", "khareedo", r"buy karo", r"order karo", "ऑर्डर करें",
    )),
    Rule("book", COMMIT, _phrases(
        "book", r"book (?:now|it|this|ticket|tickets|flight|hotel|room|slot|appointment|a demo)",
        "reserve", r"reserve (?:now|a table|seats?)", "बुक करें", r"book karo",
    )),
    Rule("apply", COMMIT, _phrases(
        "apply", r"apply now", r"easy apply", r"submit (?:my |your |the )?application",
        r"send (?:my |your |the )?application", "आवेदन(?: करें)?", r"apply karo",
    )),
    Rule("send", COMMIT, _phrases(
        "send", r"send (?:now|it|message|email|mail|reply|invite|invitation|request|otp)",
        "भेजें", "भेजो", "भेज दो", "bhejo", r"bhej do", "bhejein", r"send karo",
    )),
    Rule("post", COMMIT, _phrases(
        "post", r"post (?:now|it|comment|reply|review)", "publish", "tweet", r"share (?:publicly|now|post)",
        "comment", r"go live", "पोस्ट करें", r"post karo", "प्रकाशित करें",
    )),
    Rule("delete", COMMIT, _phrases(
        "delete", "remove", "erase", "discard", r"clear (?:history|data|everything)",
        r"cancel (?:my |your |the )?(?:subscription|order|booking|reservation|account|membership|plan|"
        r"appointment|ticket)",
        "deactivate", r"close (?:my |your )?account", "हटाएं", "हटाओ", "मिटाएं", r"delete karo", "hatao",
    )),
    Rule("account", COMMIT, _phrases(
        r"save (?:changes|settings|profile|address)",
        r"update (?:my |your )?(?:profile|password|email|phone(?: number)?|address|settings|details|pin)",
        r"change (?:my |your )?(?:password|email|phone(?: number)?|pin|address|settings)",
        r"reset (?:my |your )?password", r"authori[sz]e", r"grant (?:access|permission)",
        r"allow (?:access|permission)", "allow", r"link (?:my |your )?account", r"connect (?:my |your )?account",
        r"(?:enable|disable|turn (?:on|off)) (?:two[- ]factor|2fa|2-step)", r"create (?:an |my )?account",
        r"sign ?up", "register", "subscribe", "unsubscribe", "verify", r"verify (?:otp|code|payment)",
    )),
    # Sign in, out and up were matched above; a bare "sign" signs something.
    Rule("sign", COMMIT, _phrases(
        r"(?:e-?)?sign", r"sign (?:and|&) (?:submit|send|continue|accept)",
        r"sign (?:document|contract|agreement|form|here|now)", r"adopt and sign", "हस्ताक्षर",
    )),
    Rule("search", BENIGN, _phrases(
        "search", "find", "go", "look up", "खोजें", "खोज", "खोजो", "khojo", r"search karo", "dhoondho",
    )),
    Rule("submit", COMMIT, _phrases(
        "submit", r"submit (?:form|now|details|request)", "confirm", "जमा करें", r"submit karo",
        "पुष्टि करें",
    )),
    Rule("dismiss", BENIGN, _phrases(
        "accept", "reject", "decline", "dismiss", "close", "ok", "okay", r"got it", r"no,? thanks",
        r"not now", r"maybe later", "skip", "cancel", r"i agree", "agree", "understood", "done",
    )),
)

# Fields whose value is a secret or an identity document number: the page's
# scripts see it as it is typed, and an OTP usually authorises a payment.
_SENSITIVE_FIELD = _phrases(
    "password", "passcode", "passphrase", r"one[- ]?time(?: password| code)?", "otp", "cvv", "cvc",
    r"(?:credit|debit) card(?: number)?", r"card number", r"bank account(?: number)?", r"account number",
    "routing number", r"upi pin", "mpin", r"pin(?! ?code)", "aadhaar", "aadhar", r"pan(?: number| card)?",
    r"passport(?: number)?", "ssn", "social security", r"private key", r"seed phrase", "पासवर्ड",
    "ओटीपी", "आधार",
)

_READ_ACTIONS = frozenset({
    "navigate", "back", "forward", "reload", "scroll", "wait", "hover", "screenshot",
    "download", "request_help", "close",
})
_INPUT_ACTIONS = frozenset({"fill", "set_field", "type", "select", "check", "uncheck"})
_ENTER_KEYS = frozenset({"enter", "return", "numpadenter"})
_DESKTOP_PASSIVE = frozenset({"screenshot", "wait", "move"})


def classify_label(text: str, *, context: str = "") -> Verdict | None:
    """Classify a control by its visible label. None: no rule matched.

    ``context`` is the text around the control (its dialog, banner, or form);
    a consent banner makes its buttons benign unless the label itself pays,
    sends, books, buys, posts or deletes.
    """
    label = " ".join(str(text or "").split())
    matched: Rule | None = None
    for rule in LABEL_RULES:
        if label and rule.pattern.search(label):
            matched = rule
            break
    if context and _consent_context(context) and (
        matched is None or matched.risk != COMMIT or matched.category in {"submit", "account"}
    ):
        return Verdict(BENIGN, "consent", "Consent banner button")
    if matched is None:
        return None
    return Verdict(matched.risk, matched.category, f"Matched the {matched.category} rule: {label[:80]!r}")


_CONSENT_CONTEXT = _phrases(
    r"cookies?", "consent", "gdpr", r"tracking technolog(?:y|ies)", r"privacy (?:choices|preferences|settings)",
)


def _consent_context(context: str) -> bool:
    return bool(_CONSENT_CONTEXT.search(" ".join(str(context).split())[:600]))


_ELEMENT_KEYS = ("text", "aria", "label", "name", "placeholder", "title")


def element_label(element: dict[str, Any] | None) -> str:
    """The one label a person sees on an element: its text, else its accessible name."""
    for key in _ELEMENT_KEYS:
        value = " ".join(str((element or {}).get(key) or "").split())
        if value:
            return value[:160]
    return ""


def _element_text(element: dict[str, Any] | None) -> str:
    return " ".join(str((element or {}).get(key) or "") for key in _ELEMENT_KEYS).strip()


def action_target_text(action: dict[str, Any]) -> str:
    """The words a model used to name an action's target."""
    target = action.get("target")
    values: list[str] = []
    if isinstance(target, dict):
        values.extend(str(value) for value in target.values() if value is not None)
    elif target:
        values.append(str(target))
    for key in ("selector", "ref", "role", "name", "label", "text", "placeholder", "intent"):
        if action.get(key) is not None:
            values.append(str(action[key]))
    return " ".join(values)


_LABEL_KEYS = ("name", "label", "text", "placeholder", "query", "intent")


def _target_label(action: dict[str, Any]) -> str:
    """The human words for a target (its name or label), not its role or selector."""
    target = action.get("target") if isinstance(action.get("target"), dict) else {}
    # On a typing step a top-level "text" is the value being typed, not a label.
    typed = str(action.get("action") or "").lower() in _INPUT_ACTIONS
    words = [
        str(source[key])
        for source in (target, action)
        for key in _LABEL_KEYS
        if source.get(key) and not (typed and source is action and key == "text")
    ]
    if not words and isinstance(action.get("target"), str):
        words.append(str(action["target"]))
    return " ".join(words) or action_target_text(action)


def _has_label_words(action: dict[str, Any]) -> bool:
    target = action.get("target") if isinstance(action.get("target"), dict) else {}
    return any(target.get(key) or action.get(key) for key in _LABEL_KEYS)


def _is_button(element: dict[str, Any]) -> bool:
    return (
        str(element.get("tag") or "").lower() == "button"
        or str(element.get("role") or "").lower() == "button"
        or str(element.get("type") or "").lower() in {"button", "submit", "image"}
    )


# Enter in a message or comment box sends it.
_COMPOSE_FIELD = _phrases(
    "message", r"write (?:a )?(?:message|reply|comment)", "reply", "comment", "chat", r"type a message",
    "संदेश", "मैसेज",
)


def _is_search_field(element: dict[str, Any] | None, text: str) -> bool:
    element = element or {}
    if str(element.get("type") or "").lower() == "search":
        return True
    if str(element.get("role") or "").lower() in {"searchbox", "search"}:
        return True
    if element.get("in_search_form"):
        return True
    rule = classify_label(text)
    return rule is not None and rule.category == "search"


def classify_browser_action(
    action: dict[str, Any],
    element: dict[str, Any] | None = None,
    *,
    environment: str = "browser",
    injection: bool = False,
) -> Verdict:
    """Classify one browser or desktop action.

    ``element`` is what the target actually is on the page (tag, type, role,
    label text, surrounding context), when the caller could resolve it; the
    model's own words for the target are used either way. ``injection`` means
    the page carries instruction-like text: then every step that is not a
    pure read waits for approval.
    """
    kind = str(action.get("action") or "").lower()
    if action.get("requires_confirmation"):
        return Verdict(COMMIT, "flagged", "The step was flagged for approval")
    if environment == "desktop":
        if kind in _DESKTOP_PASSIVE:
            return Verdict(READ, "read", "Desktop observation")
        return Verdict(COMMIT, "desktop_input", "Desktop input always waits for approval")
    if kind in _READ_ACTIONS:
        return Verdict(READ, "read", f"{kind} only reads or moves around")
    if injection:
        return Verdict(COMMIT, "injection", "The page contains instruction-like text")
    target_text = _target_label(action)
    element_text = _element_text(element)
    text = f"{element_text} {target_text}".strip()
    context = str((element or {}).get("context") or "")
    if kind == "upload":
        return Verdict(COMMIT, "upload", "Uploading sends a file to the site")
    if kind in _INPUT_ACTIONS:
        if _SENSITIVE_FIELD.search(text) or str((element or {}).get("type") or "").lower() == "password":
            return Verdict(COMMIT, "sensitive_input", "The field takes a secret or an ID number")
        return Verdict(INPUT, "input", "Typing into a field is reversible")
    if kind == "click" and (action.get("x") is not None or action.get("y") is not None):
        return Verdict(COMMIT, "unclassified", "A click at coordinates cannot be classified")
    if kind == "press":
        key = str(action.get("key") or "").strip().lower()
        if key not in _ENTER_KEYS:
            return Verdict(INPUT, "key", f"Pressing {key or 'a key'} is reversible")
        if _is_search_field(element, text):
            return Verdict(BENIGN, "search", "Enter in a search box")
        if str((element or {}).get("type") or "").lower() == "password":
            return Verdict(BENIGN, "auth", "Enter on a sign-in form")
        if _COMPOSE_FIELD.search(text) or str((element or {}).get("tag") or "").lower() == "textarea":
            return Verdict(COMMIT, "send", "Enter in a message box sends it")
        verdict = classify_label(text, context=context)
        if verdict is not None and verdict.category not in {"step", "pagination", "dismiss", "filter"}:
            return verdict
        return Verdict(COMMIT, "submit", "Enter may submit a form")
    if kind in {"click", "submit"}:
        verdict = None
        for candidate in dict.fromkeys(filter(None, (element_label(element), element_text, target_text))):
            verdict = classify_label(candidate, context=context)
            if verdict is not None:
                return verdict
        if element and _is_button(element) and not _element_text(element) and not _has_label_words(action):
            # An icon-only button (a paper plane, a tick) could be "Send" or "Pay".
            return Verdict(COMMIT, "unclassified", "A button with no label cannot be classified")
        if kind == "submit" or str((element or {}).get("type") or "").lower() == "submit":
            if (element or {}).get("in_search_form"):
                return Verdict(BENIGN, "search", "Submits a search form")
            return Verdict(COMMIT, "submit", "Submits a form")
        return Verdict(BENIGN, "click", "An ordinary click")
    return Verdict(INPUT, kind or "unknown", "No side effect")


# Natural-language tasks for a phone: the verb decides.
_TASK_COMMIT = _phrases(
    "send", "submit", "publish", "post", "buy", "purchase", "pay", "checkout", "delete", "remove",
    "cancel", "transfer", "book", "reserve", "apply", "sign", "authori[sz]e", "message", "call",
    "install", "uninstall", r"place (?:an |my |the )?order", "reply", "share", "upload", "भेजें", "भेजो",
    "भुगतान", "bhejo",
    r"bhej do", r"pay karo", r"order karo", r"book karo", "kharido", "खरीदें", "हटाएं", r"call karo",
)
_TASK_SENSITIVE = _phrases(
    "bank", "brokerage", "wallet", "payment", r"medical records?", "prescription", "password", "passcode",
    "otp", r"one[- ]?time", r"private key", r"seed phrase", "upi", "paytm", "phonepe", r"google pay", "gpay",
)


def classify_task(text: str) -> Verdict:
    """Classify a free-text task (phone_use). Any commit verb or sensitive app waits."""
    task = " ".join(str(text or "").split())
    match = _TASK_COMMIT.search(task)
    if match:
        return Verdict(COMMIT, "phone_task", f"The task asks to {match.group(0).lower()}")
    match = _TASK_SENSITIVE.search(task)
    if match:
        return Verdict(COMMIT, "sensitive", f"The task touches {match.group(0).lower()}")
    return Verdict(READ, "read", "A read-oriented task")


def commit_label(category: str) -> str:
    return COMMIT_LABELS.get(category, category.replace("_", " ").capitalize())
