"""Rama calendar skill backed by the scoped Google Workspace connector."""
from __future__ import annotations

import os
import urllib.parse
from datetime import datetime, timedelta


def _parse_dt(value: str) -> datetime:
    for fmt in (
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%dT%H:%M",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
        "%Y-%m-%d",
    ):
        try:
            return datetime.strptime(value.strip(), fmt)
        except ValueError:
            continue
    raise ValueError(f"Cannot parse datetime: {value!r}. Use YYYY-MM-DD or YYYY-MM-DDTHH:MM")


def _calendar_access(write: bool = False) -> bool:
    try:
        from google_workspace import status

        access = status().get("services", {}).get("calendar", {})
        return bool(access.get("write" if write else "read"))
    except Exception:
        return False


def get_upcoming_events(days_ahead: int = 7, max_events: int = 50) -> dict:
    """Read upcoming events from the connected Google Calendar."""
    days_ahead = min(max(1, int(days_ahead)), 90)
    max_events = min(max(1, int(max_events)), 100)
    if not _calendar_access():
        return {
            "status": "unconfigured",
            "message": "Connect Google Calendar read access in System -> Connections.",
            "events": [],
        }

    try:
        from google_workspace import api_request

        now = datetime.now().astimezone()
        params = urllib.parse.urlencode({
            "timeMin": now.isoformat(),
            "timeMax": (now + timedelta(days=days_ahead)).isoformat(),
            "singleEvents": "true",
            "orderBy": "startTime",
            "maxResults": max_events,
        })
        payload = api_request(
            f"https://www.googleapis.com/calendar/v3/calendars/primary/events?{params}"
        )
        events = [{
            "title": item.get("summary") or "(no title)",
            "start": item.get("start", {}).get("dateTime") or item.get("start", {}).get("date"),
            "end": item.get("end", {}).get("dateTime") or item.get("end", {}).get("date"),
            "location": item.get("location", ""),
            "description": str(item.get("description") or "")[:200],
            "calendar": "Google Calendar",
            "html_link": item.get("htmlLink"),
        } for item in payload.get("items", [])]
        return {
            "status": "ok",
            "provider": "google",
            "days_ahead": days_ahead,
            "event_count": len(events),
            "events": events,
            "message": f"Found {len(events)} Google Calendar event(s) in the next {days_ahead} day(s).",
        }
    except Exception as exc:
        return {"status": "error", "message": f"Google Calendar error: {exc}", "events": []}


def create_event(
    title: str,
    start: str,
    end: str,
    description: str = "",
    location: str = "",
    dry_run: bool = True,
) -> dict:
    """Preview or create a Google Calendar event after explicit confirmation."""
    if not title.strip():
        return {"status": "error", "message": "title cannot be empty.", "preview": {}}
    try:
        start_dt, end_dt = _parse_dt(start), _parse_dt(end)
    except ValueError as exc:
        return {"status": "error", "message": str(exc), "preview": {}}
    if end_dt <= start_dt:
        return {"status": "error", "message": "end must be after start.", "preview": {}}

    preview = {
        "title": title,
        "start": start_dt.strftime("%Y-%m-%d %H:%M"),
        "end": end_dt.strftime("%Y-%m-%d %H:%M"),
        "duration": f"{int((end_dt - start_dt).total_seconds() / 60)} minutes",
        "location": location,
        "description": description,
        "dry_run": dry_run,
    }
    if dry_run:
        return {
            "status": "preview",
            "message": "Event not created. Confirm before calling with dry_run=False.",
            "preview": preview,
        }
    if not _calendar_access(write=True):
        return {
            "status": "unconfigured",
            "message": "Connect Google Calendar write access in System -> Connections.",
            "preview": preview,
        }

    try:
        from dharma import gate_action

        verdict = gate_action(
            "calendar_create",
            avatar="Rama",
            detail=f"{title[:100]} at {start_dt.isoformat()}",
            metadata={"duration_minutes": int((end_dt - start_dt).total_seconds() / 60)},
        )
        if not verdict.allowed:
            return {"status": "blocked", "message": "; ".join(verdict.reasons), "preview": preview}
    except Exception as exc:
        return {"status": "blocked", "message": f"Dharma gate unavailable ({exc})", "preview": preview}

    try:
        from google_workspace import api_request

        timezone = os.environ.get("NARAD_TIMEZONE", "Asia/Kolkata")
        event = api_request(
            "https://www.googleapis.com/calendar/v3/calendars/primary/events",
            method="POST",
            payload={
                "summary": title,
                "description": description,
                "location": location,
                "start": {"dateTime": start_dt.isoformat(), "timeZone": timezone},
                "end": {"dateTime": end_dt.isoformat(), "timeZone": timezone},
            },
        )
        return {
            "status": "ok",
            "provider": "google",
            "message": f"Event '{title}' created in Google Calendar.",
            "html_link": event.get("htmlLink"),
            "preview": preview,
        }
    except Exception as exc:
        return {"status": "error", "message": f"Google Calendar error: {exc}", "preview": preview}
