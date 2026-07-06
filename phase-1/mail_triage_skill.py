"""
Mail triage for Krishna (M3.4).

Fetches unseen IMAP mail, classifies each message with fast local rules
(no LLM call), and returns a grouped summary Krishna can narrate — or
delivers it straight to the Vahana inbox/ntfy as a `triage` event.

Env (either set works; nothing set → status "unconfigured"):
  IMAP_USER / IMAP_PASSWORD / IMAP_HOST / IMAP_PORT   — generic servers
  EMAIL_ADDRESS / EMAIL_APP_PASSWORD /
  EMAIL_IMAP_HOST / EMAIL_IMAP_PORT                   — Gmail convention
                                                        (same creds as
                                                        email_skill +
                                                        finance sync_gmail)

Read-only: messages are fetched with BODY.PEEK — the \\Seen flag is never
set, so triage never marks anything as read.
"""
from __future__ import annotations

import email as _email_lib
import imaplib
import os
import re
from datetime import datetime
from email.header import decode_header, make_header

_CATEGORY_ORDER = ["urgent", "action", "finance", "calendar", "newsletter", "social", "other"]

_URGENT_RE = re.compile(
    r"\b(urgent|asap|immediately|action required|final notice|overdue|"
    r"past due|deadline|expires? (today|tomorrow)|last chance|suspended)\b",
    re.IGNORECASE,
)
_ACTION_RE = re.compile(
    r"\b(please (review|confirm|approve|sign|respond|reply)|awaiting your|"
    r"reminder:|follow[- ]?up|rsvp|verification|confirm your)\b",
    re.IGNORECASE,
)
_FINANCE_SENDER_RE = re.compile(
    r"(hdfcbank|icicibank|axisbank|sbi|kotak|cred\.club|indmoney|groww|"
    r"zerodha|paypal|stripe|razorpay|billing|invoice|payments?)",
    re.IGNORECASE,
)
_FINANCE_SUBJ_RE = re.compile(
    r"\b(transaction|debited|credited|payment|invoice|receipt|statement|bill\b)",
    re.IGNORECASE,
)
_CALENDAR_RE = re.compile(
    r"\b(invitation|invite|meeting|calendar|\.ics|rescheduled|event)\b", re.IGNORECASE
)
_SOCIAL_SENDER_RE = re.compile(
    r"(linkedin|facebook|instagram|twitter|x\.com|reddit|discord|quora)", re.IGNORECASE
)


def _get_imap_creds() -> tuple[str, str, str, int]:
    """Generic IMAP_* env first, then the Gmail EMAIL_* convention."""
    user = os.environ.get("IMAP_USER") or os.environ.get("EMAIL_ADDRESS", "")
    pwd = os.environ.get("IMAP_PASSWORD") or os.environ.get("EMAIL_APP_PASSWORD", "")
    host = os.environ.get("IMAP_HOST") or os.environ.get("EMAIL_IMAP_HOST", "imap.gmail.com")
    try:
        port = int(os.environ.get("IMAP_PORT") or os.environ.get("EMAIL_IMAP_PORT", "993"))
    except ValueError:
        port = 993
    return user, pwd, host, port


def imap_configured() -> bool:
    user, pwd, _, _ = _get_imap_creds()
    return bool(user and pwd)


def _decode(value: str) -> str:
    try:
        return str(make_header(decode_header(value or "")))
    except Exception:
        return value or ""


def classify_message(sender: str, subject: str, headers: dict[str, str]) -> str:
    """Rule-based category for one message. Order encodes precedence."""
    text = f"{subject} {sender}"
    if _URGENT_RE.search(subject or ""):
        return "urgent"
    if headers.get("list-unsubscribe"):
        return "newsletter"
    if _CALENDAR_RE.search(subject or "") or "text/calendar" in headers.get("content-type", ""):
        return "calendar"
    if _FINANCE_SENDER_RE.search(sender or "") or _FINANCE_SUBJ_RE.search(subject or ""):
        return "finance"
    if _SOCIAL_SENDER_RE.search(sender or ""):
        return "social"
    if _ACTION_RE.search(text):
        return "action"
    return "other"


def _fetch_unseen(mail: imaplib.IMAP4_SSL, limit: int) -> list[dict]:
    """Header-only PEEK fetch of the newest `limit` unseen messages."""
    _, data = mail.search(None, "UNSEEN")
    nums = data[0].split()
    messages: list[dict] = []
    for num in reversed(nums[-limit:]):  # newest first
        try:
            _, msg_data = mail.fetch(
                num, "(BODY.PEEK[HEADER.FIELDS (FROM SUBJECT DATE LIST-UNSUBSCRIBE CONTENT-TYPE)])"
            )
            raw = next((p[1] for p in msg_data if isinstance(p, tuple)), b"")
            msg = _email_lib.message_from_bytes(raw)
            headers = {k.lower(): str(v) for k, v in msg.items()}
            sender = _decode(headers.get("from", ""))
            subject = _decode(headers.get("subject", "(no subject)"))
            messages.append({
                "from": sender[:120],
                "subject": subject[:180],
                "date": headers.get("date", "")[:40],
                "category": classify_message(sender, subject, headers),
            })
        except Exception:
            continue
    return messages


def triage_inbox(limit: int = 25, deliver: bool = False, user_id: str = "default") -> dict:
    """Triage unseen email into urgent/action/finance/calendar/newsletter/social/other.

    Read-only (PEEK) — never marks mail as seen. Env-gated: returns
    status "unconfigured" with setup instructions when no IMAP creds.

    Args:
        limit:   Max unseen messages to examine (newest first, default 25).
        deliver: If True, also push the summary to the Narad inbox
                 (and phone via ntfy when configured) as a triage event.
        user_id: Vahana inbox owner when deliver=True.
    Returns:
        status, total, counts per category, messages grouped by category,
        and a one-paragraph summary suitable for chat.
    """
    user, pwd, host, port = _get_imap_creds()
    if not user or not pwd:
        return {
            "status": "unconfigured",
            "message": (
                "Set IMAP_USER + IMAP_PASSWORD (or EMAIL_ADDRESS + "
                "EMAIL_APP_PASSWORD for Gmail) in .env to enable mail triage."
            ),
            "total": 0,
        }
    try:
        mail = imaplib.IMAP4_SSL(host, port)
        mail.login(user, pwd)
        mail.select("INBOX", readonly=True)
        messages = _fetch_unseen(mail, max(1, min(limit, 100)))
        mail.logout()
    except imaplib.IMAP4.error as exc:
        return {"status": "error", "message": f"IMAP error: {exc}", "total": 0}
    except OSError as exc:
        return {"status": "error", "message": f"Connection failed: {exc}", "total": 0}

    grouped: dict[str, list[dict]] = {c: [] for c in _CATEGORY_ORDER}
    for m in messages:
        grouped[m["category"]].append(m)
    counts = {c: len(v) for c, v in grouped.items() if v}

    parts = [f"{n} {c}" for c, n in ((c, counts.get(c, 0)) for c in _CATEGORY_ORDER) if n]
    summary = (
        f"{len(messages)} unread email(s): " + ", ".join(parts) + "."
        if messages else "Inbox clear — no unread email."
    )
    top = grouped["urgent"][:3] + grouped["action"][:3]
    if top:
        summary += " Needs attention: " + "; ".join(
            f"“{m['subject']}” from {m['from'].split('<')[0].strip()}" for m in top
        )

    result = {
        "status": "ok",
        "checked_at": datetime.now().isoformat(timespec="seconds"),
        "total": len(messages),
        "counts": counts,
        "messages": {c: v for c, v in grouped.items() if v},
        "summary": summary,
    }

    if deliver and messages:
        try:
            from vahana import deliver as _deliver
            urgent = bool(grouped["urgent"])
            _deliver(
                kind="triage",
                title=f"Mail triage: {len(messages)} unread"
                      + (f", {len(grouped['urgent'])} urgent" if urgent else ""),
                body=summary,
                user_id=user_id,
                source="mail_triage_skill",
                priority="high" if urgent else "default",
                data={"counts": counts},
            )
        except Exception:
            pass
    return result
