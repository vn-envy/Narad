"""Read-only Gmail triage for Krishna using the shared Google connector."""
from __future__ import annotations

import re
from datetime import datetime

_CATEGORY_ORDER = ["urgent", "action", "finance", "calendar", "newsletter", "social", "other"]
_URGENT_RE = re.compile(
    r"\b(urgent|asap|immediately|action required|final notice|overdue|past due|deadline|"
    r"expires? (today|tomorrow)|last chance|suspended)\b",
    re.IGNORECASE,
)
_ACTION_RE = re.compile(
    r"\b(please (review|confirm|approve|sign|respond|reply)|awaiting your|reminder:|"
    r"follow[- ]?up|rsvp|verification|confirm your)\b",
    re.IGNORECASE,
)
_FINANCE_RE = re.compile(
    r"(hdfcbank|icicibank|axisbank|sbi|kotak|cred\.club|indmoney|groww|zerodha|paypal|"
    r"stripe|razorpay|billing|invoice|payments?|transaction|debited|credited|receipt|statement)",
    re.IGNORECASE,
)
_CALENDAR_RE = re.compile(r"\b(invitation|invite|meeting|calendar|rescheduled|event)\b", re.IGNORECASE)
_NEWSLETTER_RE = re.compile(r"\b(newsletter|digest|unsubscribe|weekly update)\b", re.IGNORECASE)
_SOCIAL_RE = re.compile(r"(linkedin|facebook|instagram|twitter|x\.com|reddit|discord|quora)", re.IGNORECASE)


def classify_message(sender: str, subject: str, headers: dict[str, str] | None = None) -> str:
    """Classify one message with deterministic local rules."""
    text = f"{subject} {sender}"
    if _URGENT_RE.search(subject or ""):
        return "urgent"
    if _CALENDAR_RE.search(subject or ""):
        return "calendar"
    if _FINANCE_RE.search(text):
        return "finance"
    if _SOCIAL_RE.search(sender or ""):
        return "social"
    if _NEWSLETTER_RE.search(text):
        return "newsletter"
    if _ACTION_RE.search(text):
        return "action"
    return "other"


def triage_inbox(limit: int = 25, deliver: bool = False) -> dict:
    """Group unread Gmail messages without changing their read state.

    The mail read and the notification both belong to the calling profile:
    there is deliberately no user_id argument for the model to fill in.
    """
    from profile_context import current_profile_id

    user_id = current_profile_id()
    try:
        from google_workspace_skill import search_google_mail

        response = search_google_mail("is:unread", max_results=max(1, min(int(limit), 50)))
    except Exception as exc:
        return {"status": "error", "message": f"Gmail triage failed: {exc}", "total": 0}
    if response.get("status") != "ok":
        return {**response, "total": 0}

    messages = []
    for item in response.get("messages", []):
        sender = str(item.get("from") or "")[:120]
        subject = str(item.get("subject") or "(no subject)")[:180]
        messages.append({
            "id": item.get("id"),
            "from": sender,
            "subject": subject,
            "date": str(item.get("date") or "")[:80],
            "snippet": str(item.get("snippet") or "")[:240],
            "category": classify_message(sender, subject),
        })

    grouped: dict[str, list[dict]] = {category: [] for category in _CATEGORY_ORDER}
    for message in messages:
        grouped[message["category"]].append(message)
    counts = {category: len(items) for category, items in grouped.items() if items}
    parts = [f"{counts[category]} {category}" for category in _CATEGORY_ORDER if counts.get(category)]
    summary = (
        f"{len(messages)} unread email(s): {', '.join(parts)}."
        if messages else "Inbox clear - no unread email."
    )
    attention = grouped["urgent"][:3] + grouped["action"][:3]
    if attention:
        summary += " Needs attention: " + "; ".join(
            f"'{item['subject']}' from {item['from'].split('<')[0].strip()}" for item in attention
        )

    result = {
        "status": "ok",
        "provider": "google",
        "checked_at": datetime.now().isoformat(timespec="seconds"),
        "total": len(messages),
        "counts": counts,
        "messages": {category: items for category, items in grouped.items() if items},
        "summary": summary,
    }
    if deliver and messages:
        try:
            from vahana import deliver as deliver_notification

            deliver_notification(
                kind="triage",
                title=f"Mail triage: {len(messages)} unread",
                body=summary,
                user_id=user_id,
                source="mail_triage_skill",
                priority="high" if grouped["urgent"] else "default",
                data={"counts": counts},
            )
        except Exception:
            pass
    return result
