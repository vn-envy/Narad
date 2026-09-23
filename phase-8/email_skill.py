"""
Krishna email skill — Gmail OAuth with dry-run-first confirmation and HTML templates.

Safety model — preview-first:
  send_email(... dry_run=True)  → previews what would be sent, nothing goes out
  send_email(... dry_run=False) → actually sends. ONLY call after user confirms.
"""
from __future__ import annotations

import base64
import os
import re
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_TEMPLATES_DIR = Path(__file__).parent / "email-templates"


def _parse_recipients(raw: str | list) -> list[str]:
    if isinstance(raw, list):
        return [r.strip() for r in raw if r.strip()]
    return [r.strip() for r in re.split(r"[,;]", raw) if r.strip()]


def _validate_recipients(recipients: list[str]) -> str | None:
    for addr in recipients:
        if not _EMAIL_RE.match(addr):
            return f"Invalid email address: {addr!r}"
    return None


def compose_rich_email(template_name: str, context: dict) -> str:
    """Render an HTML email from a pre-built template by filling {{SLOT}} placeholders.

    Templates live in phase-8/email-templates/. Pass context as a dict mapping
    slot name (without braces) → replacement value.

    Available templates: announcement, invitation, follow_up, digest, alert.

    Args:
        template_name: One of "announcement", "invitation", "follow_up", "digest", "alert".
        context:       Dict of slot-name → value pairs to substitute.
                       Un-filled slots keep their default value from the template.

    Returns:
        Rendered HTML string, ready to pass as html_body to send_email().

    Example:
        html = compose_rich_email("announcement", {
            "HEADLINE": "Narad 2.0 is here",
            "HERO_BODY": "Your personal AI just got a lot smarter.",
            "CTA_LABEL": "See What's New",
            "SENDER_NAME": "The Narad Team",
        })
        send_email(to="user@example.com", subject="Narad 2.0",
                   body="See HTML version.", html_body=html, dry_run=True)
    """
    name = template_name.strip().lower().replace("-", "_")
    html_path = _TEMPLATES_DIR / f"{name}.html"
    if not html_path.exists():
        available = [p.stem for p in _TEMPLATES_DIR.glob("*.html")]
        return f"Template {name!r} not found. Available: {available}"

    html = html_path.read_text(encoding="utf-8")

    def replacer(m: re.Match) -> str:
        slot, default = m.group(1), m.group(2) or ""
        return str(context.get(slot, default))

    html = re.sub(r'\{\{([A-Z0-9_]+)\|?([^}]*)\}\}', replacer, html)
    return html


def send_email(
    to: str,
    subject: str,
    body: str,
    cc: str = "",
    dry_run: bool = True,
    html_body: str = "",
) -> dict:
    """Preview or send an email through the connected Gmail account.

    SAFETY CONTRACT — same preview-first pattern as other mutating Narad tools:
      dry_run=True (default): returns a full preview of the email. Nothing is sent.
      dry_run=False: actually sends. ONLY call after the user explicitly confirms.

    Always call with dry_run=True first, show the user the full preview
    (To, CC, Subject, Body), then wait for "yes", "send it", "go ahead" before
    calling with dry_run=False.

    Args:
        to:        Recipient(s). Single address or comma/semicolon-separated list.
        subject:   Email subject line.
        body:      Plain-text body (required — serves as fallback for non-HTML clients).
        cc:        CC recipients. Optional, same format as to.
        dry_run:   True = preview only (default). False = send now.
        html_body: Optional rendered HTML from compose_rich_email(). When provided,
                   the email is sent as multipart/alternative with HTML + plain-text.

    Returns:
        status:   "ok" | "error" | "preview"
        preview:  Full email summary (always returned, even when sent)
        message:  Status description
    """
    to_list  = _parse_recipients(to)
    cc_list  = _parse_recipients(cc) if cc else []

    err = _validate_recipients(to_list + cc_list)
    if err:
        return {"status": "error", "message": err, "preview": {}}

    if not subject.strip():
        return {"status": "error", "message": "subject cannot be empty.", "preview": {}}
    if not body.strip():
        return {"status": "error", "message": "body cannot be empty.", "preview": {}}

    sender = os.environ.get("EMAIL_ADDRESS", "")
    google_write = False
    try:
        from google_workspace import status as google_status
        google_write = bool(google_status()["services"]["gmail"]["write"])
    except Exception:
        pass
    preview = {
        "from":      sender or ("connected Google account" if google_write else "(EMAIL_ADDRESS not set)"),
        "to":        to_list,
        "cc":        cc_list,
        "subject":   subject,
        "body":      body,
        "html_body": html_body[:200] + "…" if len(html_body) > 200 else html_body,
        "dry_run":   dry_run,
    }

    if dry_run:
        return {
            "status":  "preview",
            "message": (
                "DRY RUN — email NOT sent. Review the preview below, then call "
                "send_email(..., dry_run=False) after the user confirms."
            ),
            "preview": preview,
        }

    # Mandatory Dharma gate on the real send — verdict lands in the Karma
    # ledger. Fail closed: no policy layer, no outbound email.
    try:
        from dharma import gate_action

        _verdict = gate_action(
            "email_send",
            avatar="Krishna",
            detail=f"to={', '.join(to_list)[:120]} subject={subject[:80]}",
            metadata={"recipients": len(to_list) + len(cc_list)},
        )
        _gate_err = None if _verdict.allowed else "; ".join(_verdict.reasons)
    except Exception as _exc:
        _gate_err = f"Dharma gate unavailable ({_exc}) — refusing to send."
    if _gate_err:
        return {"status": "blocked", "message": _gate_err, "preview": preview}

    # Build MIME message
    msg = MIMEMultipart("alternative")
    msg["From"]    = sender or "me"
    msg["To"]      = ", ".join(to_list)
    msg["Subject"] = subject
    if cc_list:
        msg["Cc"] = ", ".join(cc_list)

    msg.attach(MIMEText(body, "plain"))
    if html_body.strip():
        msg.attach(MIMEText(html_body, "html"))

    if google_write:
        try:
            from google_workspace import api_request
            raw = base64.urlsafe_b64encode(msg.as_bytes()).decode().rstrip("=")
            result = api_request(
                "https://gmail.googleapis.com/gmail/v1/users/me/messages/send",
                method="POST", payload={"raw": raw},
            )
            return {
                "status": "ok", "provider": "google",
                "message": f"Email sent to {', '.join(to_list)} through Gmail.",
                "message_id": result.get("id"), "preview": preview,
            }
        except Exception as exc:
            return {"status": "error", "message": f"Gmail API error: {exc}", "preview": preview}

    return {
        "status": "unconfigured",
        "message": "Connect Gmail write access in System -> Connections.",
        "preview": preview,
    }


def compose_email(to: str, subject: str, body: str, cc: str = "") -> dict:
    """Preview a formatted email without sending it. Always safe — no network call.

    Use this to show the user exactly what the email will look like before
    asking them to confirm a send_email call.

    Returns a structured preview: From, To, CC, Subject, Body.
    """
    to_list = _parse_recipients(to)
    cc_list = _parse_recipients(cc) if cc else []
    err = _validate_recipients(to_list + cc_list)
    if err:
        return {"status": "error", "message": err, "preview": {}}

    sender = os.environ.get("EMAIL_ADDRESS", "(EMAIL_ADDRESS not configured)")
    return {
        "status":  "ok",
        "message": "Email composed. Share this preview with the user and ask for confirmation before sending.",
        "preview": {
            "from":    sender,
            "to":      to_list,
            "cc":      cc_list,
            "subject": subject,
            "body":    body,
        },
    }
