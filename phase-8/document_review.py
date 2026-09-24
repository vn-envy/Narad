"""
Document reviews: values read from a family document wait here until a person
confirms them, crop by crop, on the review screen (/?review=<id>).

A review lives under the profile's own folder,
profiles/<id>/documents/reviews/<review_id>/, with review.json and the page
images the values were read from. Nothing in a review reaches health.db or
finance.db until save_review() is called with the person's decisions, and then
only the values they confirmed. Saved rows keep the review and item ids, so any
stored value can be traced back to its crop.

Saving by kind:
  lab          → health.db lab_results (always, once confirmed)
  transaction  → finance.db transactions (debits; deduplicated like import_csv)
  medicine     → medication reminders, only when the person ticks that option
  event        → a calendar event and/or a reminder, only when ticked
  field        → kept in the review record only
"""
from __future__ import annotations

import io
import json
import logging
import os
import re
import shutil
import threading
import uuid
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

log = logging.getLogger("narad.documents")

REVIEW_ID_RE = re.compile(r"^rev_[a-f0-9]{16}$")
ITEM_KINDS = ("lab", "transaction", "medicine", "event", "field")
_EDITABLE = {"label", "value", "unit", "reference_range"}
_EDITABLE_DETAILS = {
    "transaction": {"date", "type"},
    "medicine": {"strength", "dose", "frequency", "duration", "instructions"},
    "event": {"time", "location", "action"},
}
_DOCUMENT_EDITABLE = {"test_date", "date", "account", "lab_name"}
_ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_lock = threading.Lock()


# ── Storage ───────────────────────────────────────────────────────────────────

def _reviews_root(profile_id: str | None = None) -> Path:
    from profile_context import profile_root

    return profile_root(profile_id) / "documents" / "reviews"


def review_dir(review_id: str, profile_id: str | None = None) -> Path | None:
    """The review's folder in the caller's own profile, or None for a bad id."""
    if not REVIEW_ID_RE.fullmatch(str(review_id or "")):
        return None
    return _reviews_root(profile_id) / review_id


def load_review(review_id: str, profile_id: str | None = None) -> dict[str, Any] | None:
    folder = review_dir(review_id, profile_id)
    if folder is None:
        return None
    try:
        review = json.loads((folder / "review.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return review if isinstance(review, dict) else None


def _write_review(review: dict[str, Any]) -> None:
    folder = review_dir(review["review_id"])
    assert folder is not None
    folder.mkdir(parents=True, exist_ok=True)
    review["updated_at"] = _now()
    tmp = folder / f".review.{os.getpid()}.{threading.get_ident()}.tmp"
    tmp.write_text(json.dumps(review, ensure_ascii=False, indent=1), encoding="utf-8")
    os.chmod(tmp, 0o600)
    tmp.replace(folder / "review.json")


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def create_review(
    *,
    doc_type: str,
    ocr: dict[str, Any],
    document: dict[str, Any],
    items: list[dict[str, Any]],
    rejected: list[dict[str, Any]],
    model: str,
    tier: str,
    escalation: dict[str, Any],
) -> dict[str, Any]:
    """Persist a pending review with copies of its page images."""
    from profile_context import current_profile_id

    review_id = f"rev_{uuid.uuid4().hex[:16]}"
    folder = review_dir(review_id)
    assert folder is not None
    folder.mkdir(parents=True, exist_ok=True)
    pages = []
    for page in ocr.get("pages", []):
        name = f"page-{int(page['page'])}.jpg"
        shutil.copyfile(page["image"], folder / name)
        pages.append({
            "page": page["page"],
            "width": page["width"],
            "height": page["height"],
            "source": page["source"],
            "image": name,
            "mean_confidence": page.get("mean_confidence"),
            "poor": bool(page.get("poor")),
        })
    lines = {
        line["id"]: {"page": page["page"], "text": line["text"], "confidence": line["confidence"],
                     "bbox": line["bbox"]}
        for page in ocr.get("pages", [])
        for line in page.get("lines", [])
    }
    review = {
        "review_id": review_id,
        "profile_id": current_profile_id(),
        "status": "pending",
        "created_at": _now(),
        "doc_type": doc_type,
        "source": {"name": ocr.get("name", ""), "path": ocr.get("path", ""), "sha256": ocr.get("sha256", "")},
        "engine": ocr.get("engine", ""),
        "model": model,
        "model_tier": tier,
        "pages": pages,
        "lines": lines,
        "document": document,
        "items": items,
        "rejected": rejected,
        "escalation": escalation,
    }
    with _lock:
        _write_review(review)
    return review


def public_review(review: dict[str, Any]) -> dict[str, Any]:
    """What the review screen needs: no file paths, no raw OCR lines."""
    rid = review["review_id"]
    items = []
    for item in review.get("items", []):
        public = {key: value for key, value in item.items() if key not in {"source_lines"}}
        public["crop_url"] = f"/documents/reviews/{rid}/items/{item['id']}/crop"
        items.append(public)
    return {
        "review_id": rid,
        "status": review.get("status"),
        "created_at": review.get("created_at"),
        "doc_type": review.get("doc_type"),
        "source_name": review.get("source", {}).get("name", ""),
        "engine": review.get("engine"),
        "pages": [
            {**{k: v for k, v in page.items() if k != "image"},
             "image_url": f"/documents/reviews/{rid}/pages/{page['page']}"}
            for page in review.get("pages", [])
        ],
        "document": review.get("document", {}),
        "items": items,
        "rejected_count": len(review.get("rejected", [])),
        "escalation": {k: v for k, v in (review.get("escalation") or {}).items() if k != "model"},
        "saved": review.get("saved"),
    }


def list_reviews(status: str = "", limit: int = 20) -> list[dict[str, Any]]:
    root = _reviews_root()
    if not root.is_dir():
        return []
    found = []
    for folder in sorted(root.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True):
        review = load_review(folder.name)
        if not review or (status and review.get("status") != status):
            continue
        found.append({
            "review_id": review["review_id"],
            "status": review.get("status"),
            "doc_type": review.get("doc_type"),
            "source_name": review.get("source", {}).get("name", ""),
            "created_at": review.get("created_at"),
            "item_count": len(review.get("items", [])),
        })
        if len(found) >= max(1, min(int(limit), 100)):
            break
    return found


def discard_review(review_id: str) -> bool:
    """Delete a pending review and its page images. A saved review stays: its
    rows point back to it for their crops."""
    with _lock:
        review = load_review(review_id)
        folder = review_dir(review_id)
        if not review or folder is None or review.get("status") != "pending":
            return False
        shutil.rmtree(folder, ignore_errors=True)
    return True


# ── Crops ─────────────────────────────────────────────────────────────────────

def _page_image(review: dict[str, Any], page_number: int) -> Any | None:
    from PIL import Image

    page = next((p for p in review.get("pages", []) if int(p["page"]) == int(page_number)), None)
    folder = review_dir(review["review_id"])
    if page is None or folder is None:
        return None
    try:
        with Image.open(folder / page["image"]) as opened:
            return opened.convert("RGB")
    except OSError:
        return None


def _jpeg(image: Any, max_side: int) -> bytes:
    if max(image.size) > max_side:
        image = image.copy()
        image.thumbnail((max_side, max_side))
    buffer = io.BytesIO()
    image.save(buffer, "JPEG", quality=85)
    return buffer.getvalue()


def render_crop(review_id: str, item_id: str) -> bytes | None:
    """JPEG of the page region an item was read from: its whole row, with a margin."""
    review = load_review(review_id)
    if not review:
        return None
    item = next((i for i in review.get("items", []) if i.get("id") == item_id), None)
    if not item or not item.get("bbox"):
        return None
    image = _page_image(review, int(item.get("page") or 1))
    if image is None:
        return None
    width, height = image.size
    x0, y0, x1, y1 = item.get("row_bbox") or item["bbox"]
    line_h = max(y1 - y0, 0.01)
    x0, x1 = max(0.0, x0 - 0.03), min(1.0, x1 + 0.03)
    y0, y1 = max(0.0, y0 - 0.6 * line_h), min(1.0, y1 + 0.6 * line_h)
    if x1 - x0 < 0.35:  # a lone number means little without its neighbours
        centre = (x0 + x1) / 2
        x0, x1 = max(0.0, centre - 0.175), min(1.0, centre + 0.175)
    box = (int(x0 * width), int(y0 * height), max(int(x1 * width), int(x0 * width) + 1),
           max(int(y1 * height), int(y0 * height) + 1))
    return _jpeg(image.crop(box), 1400)


def render_page(review_id: str, page_number: int) -> bytes | None:
    review = load_review(review_id)
    image = _page_image(review, page_number) if review else None
    return _jpeg(image, 1600) if image is not None else None


# ── Save ──────────────────────────────────────────────────────────────────────

_FREQUENCY_TIMES = {
    "od": "once daily, 8am", "qd": "once daily, 8am", "once daily": "once daily, 8am",
    "bd": "8am and 8pm", "bid": "8am and 8pm", "twice daily": "8am and 8pm",
    "tds": "8am, 2pm and 8pm", "tid": "8am, 2pm and 8pm", "thrice daily": "8am, 2pm and 8pm",
    "qid": "8am, 12pm, 4pm and 8pm", "hs": "bedtime", "at night": "night", "sos": "",
}
_SLOT_TIMES = ("8am", "2pm", "9pm")  # morning-afternoon-night pattern such as 1-0-1


def schedule_for(frequency: str) -> str:
    """A reminder schedule Kala understands from a prescription frequency."""
    text = (frequency or "").strip().lower()
    pattern = re.fullmatch(r"\s*([01])\s*-\s*([01])\s*-\s*([01])\s*", text)
    if pattern:
        times = [slot for slot, flag in zip(_SLOT_TIMES, pattern.groups()) if flag == "1"]
        return " and ".join(times) or text
    for key, schedule in _FREQUENCY_TIMES.items():
        if re.search(rf"\b{re.escape(key)}\b", text):
            return f"{schedule} ({frequency})" if schedule else frequency
    return frequency or "once daily, 8am"


def _clean_edit(value: Any, limit: int = 200) -> str:
    return re.sub(r"\s+", " ", str(value if value is not None else "")).strip()[:limit]


def _apply_edits(item: dict[str, Any], decision: dict[str, Any]) -> dict[str, Any]:
    merged = json.loads(json.dumps(item))
    edited = False
    for key in _EDITABLE:
        if key in decision and _clean_edit(decision[key]) != _clean_edit(item.get(key)):
            merged[key] = _clean_edit(decision[key])
            edited = True
    details = decision.get("details") if isinstance(decision.get("details"), dict) else {}
    for key in _EDITABLE_DETAILS.get(item.get("kind", ""), set()):
        if key in details and _clean_edit(details[key]) != _clean_edit(item.get("details", {}).get(key)):
            merged.setdefault("details", {})[key] = _clean_edit(details[key])
            edited = True
    merged["edited"] = edited
    if edited and merged.get("kind") in {"lab", "transaction", "field"}:
        from document_fields import range_flag, to_number

        merged["normalised_value"] = to_number(merged.get("value", ""))
        if merged["kind"] == "lab":
            merged["flag"] = range_flag(merged["normalised_value"], merged.get("reference_range", ""))
    return merged


def _valid_date(value: Any) -> str:
    text = _clean_edit(value, 20)
    if not _ISO_DATE_RE.fullmatch(text):
        return ""
    try:
        date.fromisoformat(text)
    except ValueError:
        return ""
    return text


def save_review(
    review_id: str,
    decisions: list[dict[str, Any]],
    *,
    document: dict[str, Any] | None = None,
    options: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Write only the values the person confirmed; everything else stays out.

    decisions: [{"id": item id, "action": "confirm" | "drop", edits...}]. An
    item missing from decisions is dropped. options: medication_reminders,
    calendar_events, reminders (all default off).
    """
    with _lock:
        review = load_review(review_id)
        if not review:
            return {"status": "not_found", "message": "No such document review."}
        if review.get("status") != "pending":
            return {"status": "error", "message": f"This review was already {review.get('status')}."}
        options = {key: bool((options or {}).get(key)) for key in ("medication_reminders", "calendar_events", "reminders")}
        doc = dict(review.get("document") or {})
        for key, value in (document or {}).items():
            if key in _DOCUMENT_EDITABLE:
                doc[key] = _clean_edit(value)
        by_id = {item["id"]: item for item in review.get("items", [])}
        confirmed = []
        for decision in decisions or []:
            item = by_id.get(str(decision.get("id", "")))
            if item and decision.get("action") == "confirm" and item.get("saveable", True):
                confirmed.append(_apply_edits(item, decision))
        if any(item["kind"] == "lab" for item in confirmed):
            test_date = _valid_date(doc.get("test_date"))
            if not test_date:
                return {"status": "needs_input", "field": "test_date",
                        "message": "Add the date of the test before saving lab values."}
            doc["test_date"] = test_date

        rows: list[dict[str, Any]] = []
        notes: list[str] = []
        _save_labs(review, doc, [i for i in confirmed if i["kind"] == "lab"], rows)
        _save_transactions(review, doc, [i for i in confirmed if i["kind"] == "transaction"], rows, notes)
        medicines = [i for i in confirmed if i["kind"] == "medicine"]
        if options["medication_reminders"]:
            _save_medicines(medicines, rows)
        events = [i for i in confirmed if i["kind"] == "event"]
        if options["calendar_events"]:
            _save_calendar(review, events, rows, notes)
        if options["reminders"]:
            _save_reminders(review, events, rows)

        review["status"] = "saved"
        review["document"] = doc
        review["saved"] = {
            "at": _now(),
            "confirmed": [item["id"] for item in confirmed],
            "edited": [item["id"] for item in confirmed if item.get("edited")],
            "options": options,
            "rows": rows,
            "notes": notes,
        }
        edits = {item["id"]: item for item in confirmed if item.get("edited")}
        for item in review.get("items", []):
            item["decision"] = "confirmed" if item["id"] in review["saved"]["confirmed"] else "dropped"
            if item["id"] in edits:
                # What was read stays; what the person confirmed is kept beside it.
                item["confirmed_as"] = {key: edits[item["id"]].get(key) for key in (*_EDITABLE, "details")}
        _write_review(review)
    # One value can create two rows (a calendar event and a reminder): count values.
    stored = sorted({row["item_id"] for row in rows if row.get("status") in {"stored", "created"}})
    return {
        "status": "ok",
        "review_id": review_id,
        "confirmed": len(confirmed),
        "stored": len(stored),
        "rows": rows,
        "notes": notes,
        "message": _save_message(confirmed, stored, rows, notes),
    }


def _plural(count: int, word: str) -> str:
    return f"{count} {word}{'' if count == 1 else 's'}"


def _save_message(confirmed: list, stored: list, rows: list, notes: list) -> str:
    if not confirmed:
        return "Nothing was saved."
    duplicates = sum(1 for row in rows if row.get("status") == "duplicate")
    parts = [f"Saved {_plural(len(stored), 'value')}."]
    if duplicates:
        parts.append(f"{_plural(duplicates, 'transaction')} {'was' if duplicates == 1 else 'were'} already recorded.")
    kept = len(confirmed) - len({row['item_id'] for row in rows if row.get('item_id')})
    if kept > 0:
        parts.append(f"{_plural(kept, 'confirmed value')} {'stays' if kept == 1 else 'stay'} with this document only.")
    return " ".join(parts + notes)


def _save_labs(review: dict, doc: dict, items: list[dict], rows: list[dict]) -> None:
    if not items:
        return
    from health_skill import record_lab_result

    for item in items:
        row_id = record_lab_result(
            item.get("label", ""),
            item.get("value", ""),
            doc["test_date"],
            value_num=item.get("normalised_value"),
            unit=item.get("unit", ""),
            ref_range=item.get("reference_range", ""),
            flag=item.get("flag", ""),
            lab_name=str(doc.get("lab_name") or ""),
            source_review_id=review["review_id"],
            source_item_id=item["id"],
        )
        rows.append({"item_id": item["id"], "db": "health.db", "table": "lab_results", "row_id": row_id,
                     "status": "stored"})


def _save_transactions(review: dict, doc: dict, items: list[dict], rows: list[dict], notes: list[str]) -> None:
    if not items:
        return
    from finance_skill import import_transactions

    account = _clean_edit(doc.get("account") or doc.get("bank") or "Statement", 60)
    result = import_transactions(
        [
            {
                "date": item.get("details", {}).get("date", ""),
                "description": item.get("label", ""),
                "amount": item.get("normalised_value") or 0,
                "type": item.get("details", {}).get("type", "debit"),
                "ref": f"document:{review['review_id']}:{item['id']}",
            }
            for item in items
        ],
        account=account,
        source=f"document:{review['review_id']}",
    )
    for item, outcome in zip(items, result.get("results", [])):
        status = {"imported": "stored"}.get(outcome.get("status"), outcome.get("status"))
        rows.append({"item_id": item["id"], "db": "finance.db", "table": "transactions",
                     "row_id": outcome.get("txn_id", ""), "status": status})
    invalid = sum(1 for outcome in result.get("results", []) if outcome.get("status") == "invalid")
    if invalid:
        notes.append(f"{invalid} transaction(s) had no readable date or amount and were not saved.")


def _save_medicines(items: list[dict], rows: list[dict]) -> None:
    from health_skill import set_medication_reminder

    for item in items:
        details = item.get("details", {})
        dose = " ".join(part for part in (item.get("value", ""), details.get("dose", "")) if part).strip()
        schedule = schedule_for(details.get("frequency", ""))
        if details.get("duration"):
            schedule = f"{schedule}, for {details['duration']}"
        result = set_medication_reminder(item.get("label", ""), dose or "as prescribed", schedule)
        rows.append({"item_id": item["id"], "db": "health.db", "table": "medication_reminders",
                     "row_id": result.get("id"), "status": "created"})


def _event_times(item: dict) -> tuple[str, str] | None:
    day = _valid_date(item.get("value"))
    if not day:
        return None
    match = re.search(r"(\d{1,2})(?::(\d{2}))?\s*(am|pm)?", str(item.get("details", {}).get("time", "")), re.I)
    hour, minute = 9, 0
    if match:
        hour, minute = int(match.group(1)) % 24, int(match.group(2) or 0)
        if (match.group(3) or "").lower() == "pm" and hour < 12:
            hour += 12
        hour, minute = min(hour, 23), min(minute, 59)
    start = datetime.fromisoformat(day).replace(hour=hour, minute=minute)
    return start.strftime("%Y-%m-%dT%H:%M"), (start + timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M")


def _save_calendar(review: dict, items: list[dict], rows: list[dict], notes: list[str]) -> None:
    from calendar_skill import create_event

    for item in items:
        times = _event_times(item)
        if times is None:
            rows.append({"item_id": item["id"], "table": "calendar", "status": "no_date"})
            continue
        details = item.get("details", {})
        result = create_event(
            item.get("label", "") or "From a document",
            times[0],
            times[1],
            description=f"{details.get('action', '')}\nFrom {review.get('source', {}).get('name', 'a document')}".strip(),
            location=details.get("location", ""),
            dry_run=False,
        )
        status = "created" if result.get("status") in {"ok", "created"} else str(result.get("status"))
        rows.append({"item_id": item["id"], "db": "google_calendar", "table": "events",
                     "row_id": result.get("html_link") or "", "status": status})
        if status != "created" and result.get("message") and result["message"] not in notes:
            notes.append(str(result["message"]))


# ── Reminders (bills, circulars) ──────────────────────────────────────────────

def _reminders_path(profile_id: str | None = None) -> Path:
    from profile_context import profile_root

    return profile_root(profile_id) / "documents" / "reminders.json"


def _load_reminders(profile_id: str | None = None) -> list[dict[str, Any]]:
    try:
        data = json.loads(_reminders_path(profile_id).read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except (OSError, ValueError):
        return []


def _store_reminders(reminders: list[dict[str, Any]], profile_id: str | None = None) -> None:
    path = _reminders_path(profile_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(reminders, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(path)


def _save_reminders(review: dict, items: list[dict], rows: list[dict]) -> None:
    reminders = _load_reminders()
    for item in items:
        times = _event_times(item)
        if times is None:
            rows.append({"item_id": item["id"], "table": "reminders", "status": "no_date"})
            continue
        reminder_id = f"docrem_{uuid.uuid4().hex[:10]}"
        reminders.append({
            "id": reminder_id,
            "at": times[0],
            "title": item.get("label", "") or "Document reminder",
            "body": item.get("details", {}).get("action", ""),
            "review_id": review["review_id"],
            "item_id": item["id"],
            "delivered": False,
        })
        rows.append({"item_id": item["id"], "table": "reminders", "row_id": reminder_id, "status": "created"})
    _store_reminders(reminders)


def fire_due_reminders(now: datetime, profile_ids: list[str]) -> int:
    """Deliver document reminders that are due (Kala calls this every tick)."""
    from vahana import deliver

    fired = 0
    for profile_id in profile_ids:
        reminders = _load_reminders(profile_id)
        changed = False
        for reminder in reminders:
            if reminder.get("delivered"):
                continue
            try:
                due = datetime.fromisoformat(str(reminder.get("at")))
            except ValueError:
                continue
            if due > now:
                continue
            if now - due < timedelta(days=7):
                deliver(
                    kind="reminder",
                    title=str(reminder.get("title") or "Reminder"),
                    body=str(reminder.get("body") or "From a document you saved."),
                    user_id=profile_id,
                    source="document_review.reminder",
                    data={"review_id": reminder.get("review_id"), "profile_id": profile_id},
                )
                fired += 1
            reminder["delivered"] = True
            changed = True
        if changed:
            _store_reminders(reminders, profile_id)
    return fired
