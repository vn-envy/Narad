"""
Krishna email skill — Gmail OAuth with phone approval and HTML templates.

Safety model — the person approves on their phone (Anumati):
  send_email(... dry_run=True)  → previews what would be sent, nothing goes out
  send_email(... dry_run=False) → an approval card for this exact email; Narad
                                  sends it only when the person taps Approve.
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


def _email_args(to: str | list, subject: str, body: str, cc: str | list, html_body: str) -> dict | str:
    """Canonical send arguments, or the reason they are invalid."""
    to_list = _parse_recipients(to)
    cc_list = _parse_recipients(cc) if cc else []
    if not to_list:
        return "Add at least one recipient."
    err = _validate_recipients(to_list + cc_list)
    if err:
        return err
    if not str(subject or "").strip():
        return "subject cannot be empty."
    if not str(body or "").strip():
        return "body cannot be empty."
    return {"to": to_list, "cc": cc_list, "subject": str(subject), "body": str(body), "html_body": str(html_body or "")}


def _gmail_write_ready() -> bool:
    try:
        from google_workspace import status as google_status
        return bool(google_status()["services"]["gmail"]["write"])
    except Exception:
        return False


def _email_preview(args: dict, *, dry_run: bool = False) -> dict:
    html_body = args.get("html_body") or ""
    sender = os.environ.get("EMAIL_ADDRESS", "")
    return {
        "kind":      "email",
        "from":      sender or ("connected Google account" if _gmail_write_ready() else "(EMAIL_ADDRESS not set)"),
        "to":        list(args["to"]),
        "cc":        list(args["cc"]),
        "subject":   args["subject"],
        "body":      args["body"],
        "html_body": html_body[:200] + "…" if len(html_body) > 200 else html_body,
        "dry_run":   dry_run,
    }


def _email_summary(args: dict) -> str:
    recipients = ", ".join(args["to"])
    copied = f" (cc {', '.join(args['cc'])})" if args["cc"] else ""
    return f'Send an email to {recipients}{copied}: "{args["subject"][:120]}"'


def _email_target(args: dict) -> str:
    return "; ".join(filter(None, [
        f"to: {', '.join(args['to'])}", f"cc: {', '.join(args['cc'])}" if args["cc"] else "",
    ]))


def _deliver_email(args: dict) -> dict:
    """Send exactly ``args``. Reached only through an approved Anumati proposal."""
    to_list, cc_list = list(args["to"]), list(args["cc"])
    subject, body, html_body = args["subject"], args["body"], args.get("html_body") or ""
    preview = _email_preview(args)

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
        return {"status": "blocked", "summary": _gate_err, "message": _gate_err, "preview": preview}

    # Build MIME message
    msg = MIMEMultipart("alternative")
    msg["From"]    = os.environ.get("EMAIL_ADDRESS", "") or "me"
    msg["To"]      = ", ".join(to_list)
    msg["Subject"] = subject
    if cc_list:
        msg["Cc"] = ", ".join(cc_list)

    msg.attach(MIMEText(body, "plain"))
    if html_body.strip():
        msg.attach(MIMEText(html_body, "html"))

    if _gmail_write_ready():
        try:
            from google_workspace import api_request
            raw = base64.urlsafe_b64encode(msg.as_bytes()).decode().rstrip("=")
            result = api_request(
                "https://gmail.googleapis.com/gmail/v1/users/me/messages/send",
                method="POST", payload={"raw": raw},
            )
            message = f"Email sent to {', '.join(to_list)} through Gmail."
            return {
                "status": "ok", "provider": "google", "summary": message, "message": message,
                "message_id": result.get("id"), "preview": preview,
            }
        except Exception as exc:
            message = f"Gmail API error: {exc}"
            return {"status": "error", "summary": message, "message": message, "preview": preview}

    message = "Connect Gmail write access in System -> Connections."
    return {"status": "unconfigured", "summary": message, "message": message, "preview": preview}


def send_email(
    to: str,
    subject: str,
    body: str,
    cc: str = "",
    dry_run: bool = True,
    html_body: str = "",
) -> dict:
    """Preview an email, or ask the person to approve sending it.

    SAFETY CONTRACT — the person approves on their phone, never in chat:
      dry_run=True (default): returns a full preview. Nothing is sent.
      dry_run=False: puts an approval card for this exact email (To, CC,
        Subject, Body) on the person's phone and returns status
        "needs_approval". Narad sends it only when they tap Approve (they can
        also edit or reject it there). You do not need a dry run first.

    Tell the person it is waiting for their OK. Never ask them to type "yes",
    and never call this again for the same email to "confirm" it: a repeated
    call with identical arguments cannot send twice.

    Args:
        to:        Recipient(s). Single address or comma/semicolon-separated list.
        subject:   Email subject line.
        body:      Plain-text body (required — serves as fallback for non-HTML clients).
        cc:        CC recipients. Optional, same format as to.
        dry_run:   True = preview only (default). False = request approval to send.
        html_body: Optional rendered HTML from compose_rich_email(). When provided,
                   the email is sent as multipart/alternative with HTML + plain-text.

    Returns:
        status:   "preview" | "needs_approval" | "already_done" | "ok" | "error"
        preview:  Full email summary
        message:  Status description
    """
    args = _email_args(to, subject, body, cc, html_body)
    if isinstance(args, str):
        return {"status": "error", "message": args, "preview": {}}
    preview = _email_preview(args, dry_run=dry_run)

    if dry_run:
        return {
            "status":  "preview",
            "message": (
                "Preview only — nothing was sent. Call send_email(..., dry_run=False) to put this "
                "exact email on the person's phone for approval."
            ),
            "preview": preview,
        }

    import anumati

    gate = anumati.require(
        surface="email",
        action="send",
        target=_email_target(args),
        args=args,
        summary=_email_summary(args),
        risk_class="send",
        preview=preview,
    )
    if gate.status == "needs_approval":
        return anumati.needs_approval_result(gate.proposal, message=gate.proposal.summary, preview=preview)
    if gate.status == "already_executed":
        return anumati.already_executed_result(gate.proposal, preview=preview)
    result = _deliver_email(args)
    anumati.record_result(gate.proposal.proposal_id, result, profile_id=gate.proposal.profile_id)
    return result


def _execute_email_proposal(proposal) -> dict:
    return _deliver_email(proposal.args)


def _edit_email_proposal(proposal, changes: dict) -> dict:
    """The person's edits (recipients, subject, body) as a new proposal spec."""
    current = proposal.args
    body_changed = "body" in changes and changes["body"] != current["body"]
    args = _email_args(
        changes.get("to", current["to"]),
        changes.get("subject", current["subject"]),
        changes.get("body", current["body"]),
        changes.get("cc", current["cc"]),
        # A rendered HTML version would no longer match an edited body.
        "" if body_changed else current.get("html_body", ""),
    )
    if isinstance(args, str):
        raise ValueError(args)
    return {
        "action": "send",
        "target": _email_target(args),
        "args": args,
        "summary": _email_summary(args),
        "preview": _email_preview(args),
    }


def _register_approvals() -> None:
    import anumati

    anumati.register_executor("email", _execute_email_proposal)
    anumati.register_editor("email", _edit_email_proposal)


_register_approvals()


def compose_email(to: str, subject: str, body: str, cc: str = "") -> dict:
    """Preview a formatted email without sending it. Always safe — no network call.

    Use this to show the user a draft. To send it, call send_email(..., dry_run=False):
    the person approves the exact email on their phone.

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
        "message": (
            "Email composed. To send it, call send_email(..., dry_run=False); the person approves it "
            "on their phone."
        ),
        "preview": {
            "from":    sender,
            "to":      to_list,
            "cc":      cc_list,
            "subject": subject,
            "body":    body,
        },
    }
