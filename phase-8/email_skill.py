"""
Krishna email skill — SMTP sending with dry-run-first confirmation.

Uses Python's built-in smtplib — no OAuth required.
Configure with environment variables:

  EMAIL_ADDRESS       sender address          e.g. you@gmail.com
  EMAIL_APP_PASSWORD  app-specific password   (NOT your account password)
  EMAIL_SMTP_HOST     SMTP server             default: smtp.gmail.com
  EMAIL_SMTP_PORT     SMTP port               default: 587

For Gmail: generate an App Password at
  Google Account → Security → 2-Step Verification → App passwords

Safety model — same as Vamana:
  send_email(... dry_run=True)  → previews what would be sent, nothing goes out
  send_email(... dry_run=False) → actually sends. ONLY call after user confirms.
"""
from __future__ import annotations

import os
import re
import smtplib
import ssl
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _parse_recipients(raw: str | list) -> list[str]:
    if isinstance(raw, list):
        return [r.strip() for r in raw if r.strip()]
    return [r.strip() for r in re.split(r"[,;]", raw) if r.strip()]


def _validate_recipients(recipients: list[str]) -> str | None:
    for addr in recipients:
        if not _EMAIL_RE.match(addr):
            return f"Invalid email address: {addr!r}"
    return None


def send_email(
    to: str,
    subject: str,
    body: str,
    cc: str = "",
    dry_run: bool = True,
) -> dict:
    """Send an email via SMTP. Requires EMAIL_ADDRESS and EMAIL_APP_PASSWORD env vars.

    SAFETY CONTRACT — same pattern as Vamana's move_to_trash:
      dry_run=True (default): returns a full preview of the email. Nothing is sent.
      dry_run=False: actually sends. ONLY call after the user explicitly confirms.

    Always call with dry_run=True first, show the user the full preview
    (To, CC, Subject, Body), then wait for "yes", "send it", "go ahead" before
    calling with dry_run=False.

    Args:
        to:      Recipient(s). Single address or comma/semicolon-separated list.
        subject: Email subject line.
        body:    Email body (plain text). Write the complete final draft here.
        cc:      CC recipients. Optional, same format as to.
        dry_run: True = preview only (default). False = send now.

    Required environment variables:
        EMAIL_ADDRESS       — sender address (e.g. you@gmail.com)
        EMAIL_APP_PASSWORD  — app-specific password (not your login password)
        EMAIL_SMTP_HOST     — SMTP server (default: smtp.gmail.com)
        EMAIL_SMTP_PORT     — SMTP port (default: 587)

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
    preview = {
        "from":    sender or "(EMAIL_ADDRESS not set)",
        "to":      to_list,
        "cc":      cc_list,
        "subject": subject,
        "body":    body,
        "dry_run": dry_run,
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

    # Validate configuration
    app_password = os.environ.get("EMAIL_APP_PASSWORD", "")
    smtp_host    = os.environ.get("EMAIL_SMTP_HOST", "smtp.gmail.com")
    smtp_port    = int(os.environ.get("EMAIL_SMTP_PORT", "587"))

    if not sender:
        return {
            "status":  "error",
            "message": "EMAIL_ADDRESS environment variable is not set.",
            "preview": preview,
        }
    if not app_password:
        return {
            "status":  "error",
            "message": (
                "EMAIL_APP_PASSWORD environment variable is not set. "
                "For Gmail: Google Account → Security → App Passwords."
            ),
            "preview": preview,
        }

    # Build MIME message
    msg = MIMEMultipart("alternative")
    msg["From"]    = sender
    msg["To"]      = ", ".join(to_list)
    msg["Subject"] = subject
    if cc_list:
        msg["Cc"] = ", ".join(cc_list)
    msg.attach(MIMEText(body, "plain"))

    all_recipients = to_list + cc_list

    try:
        context = ssl.create_default_context()
        with smtplib.SMTP(smtp_host, smtp_port) as server:
            server.ehlo()
            server.starttls(context=context)
            server.login(sender, app_password)
            server.sendmail(sender, all_recipients, msg.as_string())

        return {
            "status":  "ok",
            "message": f"Email sent to {', '.join(to_list)}.",
            "preview": preview,
        }

    except smtplib.SMTPAuthenticationError:
        return {
            "status":  "error",
            "message": (
                "SMTP authentication failed. Check EMAIL_APP_PASSWORD. "
                "For Gmail use an App Password, not your account password."
            ),
            "preview": preview,
        }
    except Exception as exc:
        return {
            "status":  "error",
            "message": f"SMTP error: {exc}",
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
