"""
Rama calendar skill — CalDAV read + event creation with dry-run confirmation.

Works with any CalDAV server:
  iCloud:  https://caldav.icloud.com  (use an Apple ID app-specific password)
  Google:  https://www.google.com/calendar/dav/{CALENDAR_ID}/events
  Fastmail, Nextcloud, DAViCal, etc.

Environment variables:
  CALDAV_URL       CalDAV server URL
  CALDAV_USERNAME  Username (usually your email address)
  CALDAV_PASSWORD  Password or app-specific password

Falls back gracefully with instructions if caldav is not installed or
credentials are not configured.

Safety model:
  create_event(... dry_run=True)  → previews the event, nothing is created
  create_event(... dry_run=False) → actually creates the event in the calendar
  Always preview first, execute only after explicit user confirmation.
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, date
from typing import Optional


def _get_creds() -> tuple[str, str, str]:
    return (
        os.environ.get("CALDAV_URL", ""),
        os.environ.get("CALDAV_USERNAME", ""),
        os.environ.get("CALDAV_PASSWORD", ""),
    )


def _parse_dt(value: str) -> datetime:
    """Parse ISO-ish date/datetime strings into a datetime object."""
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
    raise ValueError(
        f"Cannot parse datetime: {value!r}. "
        "Use ISO format: YYYY-MM-DD or YYYY-MM-DDTHH:MM"
    )


def get_upcoming_events(days_ahead: int = 7, max_events: int = 50) -> dict:
    """Fetch upcoming calendar events from CalDAV.

    Reads from the CalDAV server configured via CALDAV_URL / CALDAV_USERNAME /
    CALDAV_PASSWORD environment variables.

    Args:
        days_ahead:  How many days ahead to look (default 7, max 90).
        max_events:  Maximum events to return (default 50).

    Returns a list of events with title, start, end, location, description.
    Read-only — always safe to call without confirmation.
    """
    days_ahead = min(max(1, days_ahead), 90)
    url, username, password = _get_creds()

    if not url or not username or not password:
        return {
            "status": "unconfigured",
            "message": (
                "Calendar not configured. Set environment variables:\n"
                "  CALDAV_URL      — e.g. https://caldav.icloud.com\n"
                "  CALDAV_USERNAME — your email address\n"
                "  CALDAV_PASSWORD — app-specific password\n"
                "For iCloud: Apple ID → Sign-In & Security → App-Specific Passwords\n"
                "For Google: use a Google App Password"
            ),
            "events": [],
        }

    try:
        import caldav
        from caldav.elements import dav
    except ImportError:
        return {
            "status":  "error",
            "message": "caldav not installed. Run: pip install caldav",
            "events":  [],
        }

    try:
        client = caldav.DAVClient(url=url, username=username, password=password)
        principal = client.principal()
        calendars = principal.calendars()

        now = datetime.now()
        end = now + timedelta(days=days_ahead)
        all_events: list[dict] = []

        for cal in calendars:
            try:
                vevent_list = cal.date_search(start=now, end=end, expand=True)
                for vevent in vevent_list:
                    comp = vevent.vobject_instance.vevent
                    try:
                        dtstart = comp.dtstart.value
                        dtend   = comp.dtend.value if hasattr(comp, "dtend") else dtstart
                        summary = str(comp.summary.value) if hasattr(comp, "summary") else "(no title)"
                        location = str(comp.location.value) if hasattr(comp, "location") else ""
                        description = str(comp.description.value) if hasattr(comp, "description") else ""

                        # Normalise date vs datetime
                        if isinstance(dtstart, date) and not isinstance(dtstart, datetime):
                            dtstart = datetime(dtstart.year, dtstart.month, dtstart.day)
                        if isinstance(dtend, date) and not isinstance(dtend, datetime):
                            dtend = datetime(dtend.year, dtend.month, dtend.day)

                        all_events.append({
                            "title":       summary,
                            "start":       dtstart.strftime("%Y-%m-%d %H:%M"),
                            "end":         dtend.strftime("%Y-%m-%d %H:%M"),
                            "location":    location,
                            "description": description[:200],
                            "calendar":    str(cal.name) if hasattr(cal, "name") else "",
                        })
                    except Exception:
                        continue
            except Exception:
                continue

        all_events.sort(key=lambda e: e["start"])
        all_events = all_events[:max_events]

        return {
            "status":      "ok",
            "days_ahead":  days_ahead,
            "event_count": len(all_events),
            "events":      all_events,
            "message":     f"Found {len(all_events)} event(s) in the next {days_ahead} day(s).",
        }

    except Exception as exc:
        return {
            "status":  "error",
            "message": f"CalDAV error: {exc}",
            "events":  [],
        }


def create_event(
    title: str,
    start: str,
    end: str,
    description: str = "",
    location: str = "",
    dry_run: bool = True,
) -> dict:
    """Create a calendar event via CalDAV.

    SAFETY CONTRACT:
      dry_run=True (default): returns a full preview of the event. Nothing is created.
      dry_run=False: actually creates the event. ONLY call after user confirms.

    Always call with dry_run=True first, show the user the preview
    (Title, Start, End, Location, Description), then wait for explicit confirmation
    before calling with dry_run=False.

    Args:
        title:       Event title.
        start:       Start datetime. ISO format: "2026-05-10T14:00" or "2026-05-10 14:00"
        end:         End datetime. Same format. Must be after start.
        description: Optional event description or agenda.
        location:    Optional location or meeting link.
        dry_run:     True = preview only (default). False = create event.

    Required environment variables:
        CALDAV_URL, CALDAV_USERNAME, CALDAV_PASSWORD
    """
    if not title.strip():
        return {"status": "error", "message": "title cannot be empty.", "preview": {}}

    try:
        start_dt = _parse_dt(start)
        end_dt   = _parse_dt(end)
    except ValueError as exc:
        return {"status": "error", "message": str(exc), "preview": {}}

    if end_dt <= start_dt:
        return {
            "status":  "error",
            "message": f"end ({end}) must be after start ({start}).",
            "preview": {},
        }

    duration_min = int((end_dt - start_dt).total_seconds() / 60)
    preview = {
        "title":       title,
        "start":       start_dt.strftime("%Y-%m-%d %H:%M"),
        "end":         end_dt.strftime("%Y-%m-%d %H:%M"),
        "duration":    f"{duration_min} minutes",
        "location":    location,
        "description": description,
        "dry_run":     dry_run,
    }

    if dry_run:
        return {
            "status":  "preview",
            "message": (
                "DRY RUN — event NOT created. Review the preview below, then call "
                "create_event(..., dry_run=False) after the user confirms."
            ),
            "preview": preview,
        }

    url, username, password = _get_creds()
    if not url or not username or not password:
        return {
            "status":  "error",
            "message": "Calendar not configured. Set CALDAV_URL, CALDAV_USERNAME, CALDAV_PASSWORD.",
            "preview": preview,
        }

    try:
        import caldav
    except ImportError:
        return {
            "status":  "error",
            "message": "caldav not installed. Run: pip install caldav",
            "preview": preview,
        }

    # Build iCalendar event string
    uid = f"{start_dt.strftime('%Y%m%dT%H%M%S')}-avatara@narad"
    now_utc = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
    start_str = start_dt.strftime("%Y%m%dT%H%M%S")
    end_str   = end_dt.strftime("%Y%m%dT%H%M%S")

    ical = (
        "BEGIN:VCALENDAR\r\n"
        "VERSION:2.0\r\n"
        "PRODID:-//Avatara//Narad//EN\r\n"
        "BEGIN:VEVENT\r\n"
        f"UID:{uid}\r\n"
        f"DTSTAMP:{now_utc}\r\n"
        f"DTSTART:{start_str}\r\n"
        f"DTEND:{end_str}\r\n"
        f"SUMMARY:{title}\r\n"
    )
    if location:
        ical += f"LOCATION:{location}\r\n"
    if description:
        description_escaped = description.replace("\n", "\\n")
        ical += f"DESCRIPTION:{description_escaped}\r\n"
    ical += "END:VEVENT\r\nEND:VCALENDAR\r\n"

    try:
        client = caldav.DAVClient(url=url, username=username, password=password)
        principal = client.principal()
        calendars = principal.calendars()

        if not calendars:
            return {
                "status":  "error",
                "message": "No calendars found on the CalDAV server.",
                "preview": preview,
            }

        # Use the first calendar (usually the default/personal calendar)
        cal = calendars[0]
        cal.save_event(ical)

        return {
            "status":  "ok",
            "message": f"Event '{title}' created on {start_dt.strftime('%Y-%m-%d at %H:%M')}.",
            "preview": preview,
        }

    except Exception as exc:
        return {
            "status":  "error",
            "message": f"CalDAV error: {exc}",
            "preview": preview,
        }
