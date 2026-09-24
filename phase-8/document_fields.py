"""
extract_fields: Matsya's tool for reading values out of family documents.

photo or PDF → local OCR lines with ids (ocr_skill) → the worker model, through
the privacy gateway, answers in strict JSON for the document type → validation,
repair and a hallucination guard → a pending document review
(document_review) that a person confirms crop by crop before Rama's databases
see anything.

Only OCR text goes to the model here, so a `redact`-tier worker such as DeepSeek
sees placeholders instead of names and identifiers. The hallucination guard
keeps a value only if its numbers (and dates) appear in the OCR lines it cites,
or in another printed row, which then becomes its source. Page images leave the
Mac only through escalate(): a per-document choice by the person, to a `local`
or `trusted` vision model, logged in the egress ledger.
"""
from __future__ import annotations

import base64
import json
import logging
import os
import re
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

log = logging.getLogger("narad.documents")

DOC_TYPES = ("lab_report", "bank_statement", "prescription", "school_circular", "bill", "generic")
HEALTH_TYPES = frozenset({"lab_report", "prescription"})
_MAX_ITEMS = 200
_MAX_TOKENS = int(os.environ.get("NARAD_DOC_FIELDS_MAX_TOKENS", "8000"))
_TIMEOUT_S = float(os.environ.get("NARAD_LLM_TIMEOUT_S", "120"))
# Rows sent per model call; longer statements are read a few pages at a time.
_ROWS_PER_CALL = 150
_CHECK_CONFIDENCE = 0.8
_LOW_OCR = 0.75
_ESCALATION_MAX_PAGES = 4
# Issues that leave an item unticked until the person looks at its crop.
_NEEDS_A_LOOK = frozenset({
    "label_differs", "unclear", "handwritten", "low_confidence", "read_from_image",
    "differs_from_ocr", "type_unsure", "no_crop", "date_unclear",
})

_DEVANAGARI_DIGITS = str.maketrans("०१२३४५६७८९", "0123456789")
_NUMBER_RE = re.compile(r"(?<![\d.])\d{1,3}(?:,\d{2,3})+(?:\.\d+)?|\d+(?:\.\d+)?")
_WORD_RE = re.compile(r"[0-9a-zऀ-ॿ]+")  # Latin and Devanagari words
_ID_RE = re.compile(r"^p(\d+)-l(\d+)$")
_MONTHS = (
    ("january", "jan", "जनवरी"), ("february", "feb", "फरवरी", "फ़रवरी"), ("march", "mar", "मार्च"),
    ("april", "apr", "अप्रैल"), ("may", "may", "मई"), ("june", "jun", "जून"),
    ("july", "jul", "जुलाई"), ("august", "aug", "अगस्त"), ("september", "sep", "sept", "सितंबर", "सितम्बर"),
    ("october", "oct", "अक्टूबर", "अक्तूबर"), ("november", "nov", "नवंबर", "नवम्बर"),
    ("december", "dec", "दिसंबर", "दिसम्बर"),
)
_BANKS = ("HDFC", "ICICI", "Axis", "SBI", "Kotak", "Yes Bank", "IDFC", "IndusInd", "PNB", "Bank of Baroda",
          "Canara", "Union Bank", "Federal", "AU")


# ── Shapes and prompt ─────────────────────────────────────────────────────────

_DATE = "YYYY-MM-DD"
_COMMON = {"source_lines": ["p1-l7"], "confidence": 0.9, "unclear": False}
_SHAPES: dict[str, dict[str, Any]] = {
    "lab_report": {
        "document": {"lab_name": "", "patient_name": "", "test_date": _DATE},
        "fields": [{"name": "HbA1c", "value": "8.2", "unit": "%", "reference_range": "4.0 - 5.6", **_COMMON}],
    },
    "bank_statement": {
        "document": {"bank": "", "account_number": "", "period_from": _DATE, "period_to": _DATE},
        "transactions": [{"date": _DATE, "description": "", "amount": "1,250.00", "type": "debit or credit",
                          "balance": "", **_COMMON}],
    },
    "prescription": {
        "document": {"prescriber": "", "patient_name": "", "date": _DATE},
        "medicines": [{"name": "", "strength": "500 mg", "dose": "1 tablet", "frequency": "1-0-1",
                       "duration": "5 days", "instructions": "after food", "handwritten": False, **_COMMON}],
    },
    "school_circular": {
        "document": {"issuer": "", "title": "", "date": _DATE},
        "events": [{"title": "", "date": _DATE, "time": "", "location": "", "action": "", **_COMMON}],
        "fields": [{"name": "", "value": "", "unit": "", **_COMMON}],
    },
    "bill": {
        "document": {"biller": "", "bill_date": _DATE, "due_date": _DATE, "amount_due": "", "consumer_number": ""},
        "events": [{"title": "Pay the bill", "date": _DATE, "time": "", "location": "", "action": "", **_COMMON}],
        "fields": [{"name": "", "value": "", "unit": "", **_COMMON}],
    },
    "generic": {
        "document": {"title": "", "date": _DATE},
        "fields": [{"name": "", "value": "", "unit": "", **_COMMON}],
    },
}
# Which list in each shape holds which kind of item.
_ITEM_LISTS: dict[str, tuple[tuple[str, str], ...]] = {
    "lab_report": (("lab", "fields"),),
    "bank_statement": (("transaction", "transactions"),),
    "prescription": (("medicine", "medicines"),),
    "school_circular": (("event", "events"), ("field", "fields")),
    "bill": (("event", "events"), ("field", "fields")),
    "generic": (("field", "fields"),),
}
_DOC_FIELDS: dict[str, dict[str, str]] = {
    "lab_report": {"lab_name": "text", "patient_name": "text", "test_date": "date"},
    "bank_statement": {"bank": "text", "account_number": "account", "period_from": "date", "period_to": "date"},
    "prescription": {"prescriber": "text", "patient_name": "text", "date": "date"},
    "school_circular": {"issuer": "text", "title": "text", "date": "date"},
    "bill": {"biller": "text", "bill_date": "date", "due_date": "date", "amount_due": "number",
             "consumer_number": "text"},
    "generic": {"title": "text", "date": "date"},
}
_TYPE_NOTES = {
    "lab_report": "One item per test result row. Copy the reference range printed on that row.",
    "bank_statement": "One item per transaction row. type is debit (money out) or credit (money in).",
    "prescription": "One item per medicine. Handwritten entries: set handwritten true, and unclear true unless every letter is legible.",
    "school_circular": "events: dated things to attend, pay or submit. fields: other facts worth keeping.",
    "bill": "events: the payment due date. fields: amounts, units, account or consumer numbers.",
    "generic": "fields: the facts a family would want to keep from this document.",
}

_RULES = """Rules:
- Use only the OCR lines below. Copy every value exactly as printed: never compute, convert units, correct spelling or fill a gap from memory.
- Put the ids of the lines each item comes from in "source_lines" (for example ["p1-l7", "p1-l9"]). Fragments joined by " | " sit on the same printed row.
- If a value is handwritten, smudged or cut off and you are not sure of it, set "unclear": true and copy only what is legible. Do not guess.
- Dates as YYYY-MM-DD when the printed date is unambiguous (Indian documents write the day first); otherwise copy the date as printed.
- "confidence" is your confidence, 0 to 1, that the value is read and attributed correctly.
- Use "" for anything the document does not show. Never add items that are not in the document.
- Names, numbers and ids may appear as placeholders such as <PERSON_1>; copy them unchanged.
- Objective extraction only: no diagnosis, advice or interpretation."""


def build_prompt(doc_type: str, ocr_text: str, *, from_image: bool = False) -> str:
    if doc_type in _SHAPES:
        shape = json.dumps({"doc_type": doc_type, **_SHAPES[doc_type]}, ensure_ascii=False, indent=1)
        head = f"Document type: {doc_type}. {_TYPE_NOTES[doc_type]}\nReturn one JSON object only, in exactly this shape:\n{shape}"
    else:
        shapes = "\n".join(
            f"{name} ({_TYPE_NOTES[name]}):\n{json.dumps({'doc_type': name, **shape}, ensure_ascii=False)}"
            for name, shape in _SHAPES.items()
        )
        head = (
            "First decide doc_type: one of " + ", ".join(DOC_TYPES) + ". Then return one JSON object only, "
            f"in the shape for that type:\n{shapes}"
        )
    source = (
        "The attached image is the page itself. The OCR lines below were read from it on the family's Mac "
        "and may contain mistakes: take values from the image, and cite the ids of the OCR lines at the "
        "place on the page where each value is printed."
        if from_image else "You read OCR lines from one family document."
    )
    return f"{source}\n{_RULES}\n{head}\n\nOCR lines (id, then text):\n{ocr_text}"


# ── Parsing and repair ────────────────────────────────────────────────────────

def parse_json(text: str) -> dict[str, Any]:
    """The first JSON object in a reply, repairing fences, trailing commas and smart quotes."""
    raw = (text or "").strip()
    raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw, flags=re.IGNORECASE)
    start, end = raw.find("{"), raw.rfind("}")
    if start == -1 or end <= start:
        raise ValueError("no JSON object in the reply")
    candidate = raw[start:end + 1]
    for attempt in (
        candidate,
        re.sub(r",\s*([}\]])", r"\1", candidate),
        re.sub(r",\s*([}\]])", r"\1", candidate.replace("“", '"').replace("”", '"')),
    ):
        try:
            parsed = json.loads(attempt)
        except ValueError:
            continue
        if isinstance(parsed, dict):
            return parsed
    raise ValueError("the reply is not valid JSON")


def _text(value: Any, limit: int = 200) -> str:
    if value is None or isinstance(value, (dict, list)):
        return ""
    return re.sub(r"\s+", " ", str(value)).strip()[:limit]


def _confidence(value: Any, default: float = 0.7) -> float:
    try:
        return round(min(max(float(value), 0.0), 1.0), 3)
    except (TypeError, ValueError):
        return default


def _truthy(value: Any) -> bool:
    return value is True or str(value).strip().lower() in {"true", "yes", "1"}


# ── Numbers, dates, words ─────────────────────────────────────────────────────

def _numbers(text: Any) -> set[Decimal]:
    found = set()
    for token in _NUMBER_RE.findall(str(text or "").translate(_DEVANAGARI_DIGITS)):
        try:
            found.add(Decimal(token.replace(",", "")))
        except InvalidOperation:
            continue
    return found


def to_number(text: Any) -> float | None:
    """The first number in a printed value ("<0.5" → 0.5, "1,50,000" → 150000)."""
    match = _NUMBER_RE.search(str(text or "").translate(_DEVANAGARI_DIGITS))
    if not match:
        return None
    try:
        return float(Decimal(match.group(0).replace(",", "")))
    except InvalidOperation:
        return None


def range_flag(value: float | None, reference_range: str) -> str:
    """"high" / "low" / "normal" against the range printed on the report, else ""."""
    if value is None or not reference_range:
        return ""
    text = re.sub(r"(?<=\d),(?=\d)", "", reference_range.translate(_DEVANAGARI_DIGITS).lower())
    text = text.replace("–", "-").replace("—", "-")
    pair = re.search(r"(\d+(?:\.\d+)?)\s*(?:-|to)\s*(\d+(?:\.\d+)?)", text)
    if pair:
        low, high = float(pair.group(1)), float(pair.group(2))
        if low <= high:
            return "low" if value < low else "high" if value > high else "normal"
    upper = re.search(r"(?:<=?|≤|less than|up ?to|below)\s*(\d+(?:\.\d+)?)", text)
    if upper:
        return "high" if value > float(upper.group(1)) else "normal"
    lower = re.search(r"(?:>=?|≥|more than|greater than|above)\s*(\d+(?:\.\d+)?)", text)
    if lower:
        return "low" if value < float(lower.group(1)) else "normal"
    return ""


def iso_date(text: Any) -> str:
    """YYYY-MM-DD from a printed day-first date, or ""."""
    value = _text(text, 40).translate(_DEVANAGARI_DIGITS).replace(",", " ")
    value = re.sub(r"(\d)(st|nd|rd|th)\b", r"\1", value, flags=re.IGNORECASE)
    value = re.sub(r"\s+", " ", value).strip()
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%d.%m.%Y", "%d/%m/%y", "%d-%m-%y", "%d.%m.%y",
                "%d-%b-%Y", "%d-%b-%y", "%d %b %Y", "%d %B %Y", "%d %b %y", "%d-%B-%Y", "%b %d %Y", "%B %d %Y"):
        try:
            return datetime.strptime(value, fmt).date().isoformat()
        except ValueError:
            continue
    return ""


def _date_grounded(value: str, hay: str, document_text: str) -> bool:
    """Whether the date (ISO or as printed) is printed in ``hay``; a row may omit
    the year when the document prints it elsewhere (statements do)."""
    iso = iso_date(value)
    text = hay.lower().translate(_DEVANAGARI_DIGITS)
    if not iso:
        return bool(_compact(value)) and _compact(value) in _compact(hay)
    year, month, day = (int(part) for part in iso.split("-"))
    names = "|".join(re.escape(name) for name in _MONTHS[month - 1])
    dd, mm = rf"0?{day}", rf"0?{month}"
    yy = rf"(?:{year}|{year % 100:02d})"
    sep = r"\s*[/.\-]\s*"
    with_year = (
        rf"(?<!\d){dd}{sep}{mm}{sep}{yy}(?!\d)",
        rf"(?<!\d){year}{sep}{mm}{sep}{dd}(?!\d)",
        rf"(?<!\d){dd}(?:st|nd|rd|th)?[\s,.\-/]*(?:{names})[a-z]*\.?[\s,.\-/']*{yy}(?!\d)",
        rf"(?:{names})[a-z]*\.?[\s.\-]*{dd}(?:st|nd|rd|th)?[\s,]*{yy}(?!\d)",
    )
    if any(re.search(pattern, text) for pattern in with_year):
        return True
    without_year = (
        rf"(?<![\d/.\-]){dd}{sep}{mm}(?![\d])(?!{sep}\d)",
        rf"(?<!\d){dd}(?:st|nd|rd|th)?[\s.\-/]*(?:{names})\b",
    )
    return (
        any(re.search(pattern, text) for pattern in without_year)
        and str(year) in document_text.translate(_DEVANAGARI_DIGITS)
    )


def _words(text: Any) -> set[str]:
    return {word for word in _WORD_RE.findall(str(text or "").lower()) if len(word) >= 2}


def _compact(text: Any) -> str:
    return re.sub(r"[\W_]+", "", str(text or "").lower())


def _overlap(value: str, hay: str) -> float:
    """Share of the value's words printed in ``hay`` (1.0 for a compact substring)."""
    if not _compact(value):
        return 1.0
    if _compact(value) in _compact(hay):
        return 1.0
    words = _words(value)
    if not words:
        return 0.0
    return len(words & _words(hay)) / len(words)


# ── The OCR text, as rows of line ids ─────────────────────────────────────────

@dataclass
class _Row:
    page: int
    ids: list[str]
    text: str
    numbers: set[Decimal]


class OcrText:
    """Lines (id → text, confidence, bbox, page) grouped into printed rows."""

    def __init__(self, lines: dict[str, dict[str, Any]]) -> None:
        from ocr_skill import rows

        self.lines = lines
        self.rows: list[_Row] = []
        self.row_of: dict[str, int] = {}
        by_page: dict[int, list[dict[str, Any]]] = {}
        for line_id in sorted(lines, key=_id_key):
            by_page.setdefault(int(lines[line_id]["page"]), []).append({"id": line_id, **lines[line_id]})
        for page, page_lines in sorted(by_page.items()):
            for row in rows(page_lines):
                text = " | ".join(line["text"] for line in row)
                for line in row:
                    self.row_of[line["id"]] = len(self.rows)
                self.rows.append(_Row(page, [line["id"] for line in row], text, _numbers(text)))
        self.text = "\n".join(row.text for row in self.rows)

    def row_indexes(self, ids: list[str]) -> list[int]:
        return sorted({self.row_of[i] for i in ids if i in self.row_of})

    def hay(self, ids: list[str]) -> tuple[str, set[Decimal]]:
        indexes = self.row_indexes(ids)
        text = "\n".join(self.rows[i].text for i in indexes)
        numbers: set[Decimal] = set()
        for index in indexes:
            numbers |= self.rows[index].numbers
        return text, numbers

    def format(self, pages: set[int] | None = None) -> str:
        out, page = [], None
        for row in self.rows:
            if pages is not None and row.page not in pages:
                continue
            if row.page != page:
                page = row.page
                out.append(f"## Page {page}")
            out.append(" | ".join(f"[{i}] {self.lines[i]['text']}" for i in row.ids))
        return "\n".join(out)

    def page_chunks(self) -> list[set[int]]:
        """Pages grouped so each model call sees at most about _ROWS_PER_CALL rows."""
        counts: dict[int, int] = {}
        for row in self.rows:
            counts[row.page] = counts.get(row.page, 0) + 1
        chunks: list[set[int]] = []
        current: set[int] = set()
        size = 0
        for page, count in sorted(counts.items()):
            if current and size + count > _ROWS_PER_CALL:
                chunks.append(current)
                current, size = set(), 0
            current.add(page)
            size += count
        if current:
            chunks.append(current)
        return chunks or [set()]


def _id_key(line_id: str) -> tuple[int, int]:
    match = _ID_RE.match(line_id)
    return (int(match.group(1)), int(match.group(2))) if match else (10**6, 0)


def _union(boxes: list[list[float]]) -> list[float] | None:
    if not boxes:
        return None
    return [round(min(b[0] for b in boxes), 4), round(min(b[1] for b in boxes), 4),
            round(max(b[2] for b in boxes), 4), round(max(b[3] for b in boxes), 4)]


# ── Validation and the hallucination guard ────────────────────────────────────

@dataclass(frozen=True)
class _Spec:
    label: str
    value: str
    value_mode: str  # number | number_or_text | date | optional
    optional: tuple[str, ...] = ()
    dates: tuple[str, ...] = ()
    details: tuple[str, ...] = ()
    printed_label: bool = True  # False: the label may be a summary ("Pay the bill")


_SPECS = {
    "lab": _Spec("name", "value", "number_or_text", optional=("reference_range",)),
    "transaction": _Spec("description", "amount", "number", optional=("balance",), dates=("date",),
                         details=("date", "type", "balance")),
    "medicine": _Spec("name", "strength", "optional", optional=("strength", "dose", "frequency", "duration"),
                      details=("dose", "frequency", "duration", "instructions")),
    "event": _Spec("title", "date", "date", optional=("time",), details=("time", "location", "action"),
                   printed_label=False),
    "field": _Spec("name", "value", "number_or_text"),
}


def _value_found(spec: _Spec, fields: dict[str, str], hay: str, numbers: set[Decimal], doc: OcrText) -> str:
    """Why the item's required value is not printed in ``hay``; "" when it is."""
    value = fields[spec.value]
    if spec.value_mode == "number":
        wanted = _numbers(value)
        if not wanted:
            return "no amount"
        if not wanted <= numbers:
            return "value not found in the document text"
    elif spec.value_mode == "number_or_text":
        wanted = _numbers(value)
        if not value:
            return "no value"
        if wanted and not wanted <= numbers:
            return "value not found in the document text"
        if not wanted and _overlap(value, hay) < 0.5:
            return "value not found in the document text"
    elif spec.value_mode == "date" and not _date_grounded(value, hay, doc.text):
        return "date not found in the document text"
    for key in spec.dates:
        if not _date_grounded(fields[key], hay, doc.text):
            return "date not found in the document text"
    if spec.printed_label and _overlap(fields[spec.label], hay) < 0.3:
        return "label not found in the document text"
    return ""


def _validate_item(
    kind: str,
    raw: dict[str, Any],
    doc: OcrText,
    *,
    from_image: bool,
    pages: set[int] | None = None,
) -> tuple[dict | None, str]:
    spec = _SPECS[kind]
    keys = {spec.label, spec.value, *spec.optional, *spec.dates, *spec.details}
    fields = {key: _text(raw.get(key)) for key in keys}
    if not fields[spec.label] and not fields[spec.value]:
        return None, "empty"
    cited = [
        i for i in (raw.get("source_lines") or [])
        if isinstance(i, str) and i in doc.lines and (pages is None or int(doc.lines[i]["page"]) in pages)
    ][:12]
    issues: list[str] = []

    hay, numbers = doc.hay(cited)
    problem = _value_found(spec, fields, hay, numbers, doc) if cited else "no source lines"
    if problem:
        # Repair the citation: the row that prints the value, best label match first.
        candidates = sorted(
            (index for index, row in enumerate(doc.rows) if pages is None or row.page in pages),
            key=lambda index: -_overlap(fields[spec.label], doc.rows[index].text),
        )
        for index in candidates:
            row = doc.rows[index]
            if not _value_found(spec, fields, row.text, row.numbers, doc):
                cited, problem = list(row.ids), ""
                issues.append("source_repaired")
                hay, numbers = row.text, row.numbers
                break
    if problem and not from_image:
        return None, problem
    if problem:
        issues.append("differs_from_ocr")
    if from_image:
        issues.append("read_from_image")

    for key in spec.optional:
        wanted = _numbers(fields[key])
        if fields[key] and wanted and not wanted <= numbers:
            if from_image:
                issues.append("differs_from_ocr")
            else:
                fields[key] = ""
                issues.append(f"dropped_{key}")
    if spec.printed_label and cited and _overlap(fields[spec.label], hay) < 0.6:
        issues.append("label_differs")
    if _truthy(raw.get("unclear")):
        issues.append("unclear")
    if _truthy(raw.get("handwritten")):
        issues.append("handwritten")

    lines = [doc.lines[i] for i in cited]
    page = int(lines[0]["page"]) if lines else 0
    on_page = [line for line in lines if int(line["page"]) == page]
    row_ids = [i for index in doc.row_indexes(cited) for i in doc.rows[index].ids if doc.rows[index].page == page]
    ocr_confidence = round(min((float(line["confidence"]) for line in on_page), default=0.0), 3)
    model_confidence = _confidence(raw.get("confidence"))
    if not cited:
        issues.append("no_crop")
    elif ocr_confidence < _LOW_OCR:
        issues.append("low_confidence")

    item: dict[str, Any] = {
        "kind": kind,
        "label": fields[spec.label],
        "value": fields[spec.value],
        "unit": _text(raw.get("unit"), 40) if kind in {"lab", "field"} else "",
        "reference_range": fields.get("reference_range", ""),
        "normalised_value": None,
        "flag": "",
        "details": {key: fields[key] for key in spec.details},
        "source_lines": cited,
        "page": page,
        "bbox": _union([line["bbox"] for line in on_page]),
        "row_bbox": _union([doc.lines[i]["bbox"] for i in row_ids]),
        "ocr_confidence": ocr_confidence,
        "model_confidence": model_confidence,
        "confidence": min(ocr_confidence, model_confidence) if cited else model_confidence,
        "saveable": True,
    }
    if kind in {"lab", "field", "transaction"}:
        item["normalised_value"] = to_number(item["value"])
    if kind == "lab":
        item["flag"] = range_flag(item["normalised_value"], item["reference_range"])
    elif kind == "transaction":
        _settle_transaction(item, hay, issues)
    elif kind == "event":
        item["value"] = iso_date(item["value"]) or item["value"]
        if not iso_date(item["value"]):
            issues.append("date_unclear")
            item["saveable"] = False
    item["issues"] = sorted(set(issues))
    item["default_checked"] = bool(
        item["saveable"] and item["confidence"] >= _CHECK_CONFIDENCE and not _NEEDS_A_LOOK & set(issues)
    )
    return item, ""


def _settle_transaction(item: dict[str, Any], hay: str, issues: list[str]) -> None:
    details = item["details"]
    details["date"] = iso_date(details.get("date")) or details.get("date", "")
    kind = details.get("type", "").strip().lower()
    words = _words(hay)
    if kind not in {"debit", "credit"}:
        kind = "credit" if "cr" in words and "dr" not in words else "debit" if "dr" in words else ""
    if not kind:
        kind = "debit"
        issues.append("type_unsure")
    details["type"] = kind
    if kind == "credit":
        # The ledger tracks spending (as import_csv does); money in is shown, not saved.
        item["saveable"] = False


def _validate_document(doc_type: str, raw: Any, doc: OcrText) -> dict[str, str]:
    raw = raw if isinstance(raw, dict) else {}
    out: dict[str, str] = {}
    for key, mode in _DOC_FIELDS.get(doc_type, {}).items():
        value = _text(raw.get(key), 120)
        if not value:
            out[key] = ""
        elif mode == "date":
            out[key] = iso_date(value) if iso_date(value) and _date_grounded(value, doc.text, doc.text) else ""
        elif mode == "number":
            out[key] = value if _numbers(value) and _numbers(value) <= _numbers(doc.text) else ""
        elif mode == "account":
            digits = re.sub(r"\D", "", value)
            out[key] = f"XX{digits[-4:]}" if len(digits) >= 4 and digits[-4:] in re.sub(r"\D", "", doc.text) else ""
        else:
            out[key] = value if _overlap(value, doc.text) >= 0.6 else ""
    if doc_type == "bank_statement":
        bank = out.get("bank", "")
        out["account"] = next((name for name in _BANKS if name.lower() in bank.lower()), bank[:40])
    return out


def validate(
    doc_type: str,
    payload: dict[str, Any],
    doc: OcrText,
    *,
    from_image: bool = False,
    pages: set[int] | None = None,
    id_prefix: str = "f",
    start: int = 1,
) -> tuple[dict[str, str], list[dict[str, Any]], list[dict[str, Any]]]:
    """(document fields, items, rejected) from the model's JSON."""
    document = _validate_document(doc_type, payload.get("document"), doc)
    items: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    for kind, key in _ITEM_LISTS.get(doc_type, ()):
        raw_items = payload.get(key)
        for raw in raw_items[:_MAX_ITEMS] if isinstance(raw_items, list) else []:
            if not isinstance(raw, dict):
                continue
            item, reason = _validate_item(kind, raw, doc, from_image=from_image, pages=pages)
            if item is None:
                if reason != "empty":
                    spec = _SPECS[kind]
                    rejected.append({"kind": kind, "label": _text(raw.get(spec.label)),
                                     "value": _text(raw.get(spec.value)), "reason": reason})
                continue
            item["id"] = f"{id_prefix}{start + len(items)}"
            items.append(item)
    return document, items, rejected


def detect_doc_type(text: str) -> str:
    """A confident guess from keywords, or "" to let the model decide."""
    lowered = text.lower()
    hints = {
        "lab_report": ("reference range", "ref. range", "biological reference", "pathology", "laboratory",
                       "haemoglobin", "hemoglobin", "hba1c", "serum", "specimen", "sample", "mg/dl", "g/dl",
                       "cholesterol", "thyroid", "जाँच"),
        "bank_statement": ("statement of account", "account statement", "opening balance", "closing balance",
                           "withdrawal", "deposit", "narration", "ifsc", "value date", "txn date", "cheque",
                           "खाता"),
        "prescription": ("rx", "tab", "cap", "syp", "syrup", "after food", "before food", "bd", "tds",
                         "sos", "diagnosis", "follow up", "1-0-1", "दवा"),
        "school_circular": ("circular", "dear parents", "parents", "students", "school", "principal", "ptm",
                            "अभिभावक", "विद्यालय", "परिपत्र"),
        "bill": ("invoice", "amount payable", "due date", "consumer no", "consumer number", "units consumed",
                 "tariff", "pay by", "bill date", "बिल"),
    }
    scores = {
        doc_type: sum(1 for hint in words if re.search(rf"(?<![a-z]){re.escape(hint)}(?![a-z])", lowered))
        for doc_type, words in hints.items()
    }
    ranked = sorted(scores.items(), key=lambda pair: -pair[1])
    best, runner_up = ranked[0], ranked[1]
    return best[0] if best[1] >= 2 and best[1] > runner_up[1] else ""


# ── The worker model, through the privacy gateway ─────────────────────────────

def _worker_models() -> list[str]:
    models: list[str] = []
    override = os.environ.get("NARAD_DOC_FIELDS_MODEL", "").strip()
    if override:
        models.append(override)
    try:
        from model_config import get_avatar_model

        models.append(get_avatar_model("Matsya"))
    except Exception:
        pass
    try:
        from narad_litellm import _offline_fallback_model

        fallback = _offline_fallback_model(models[-1] if models else "")
        if fallback:
            models.append(fallback)
    except Exception:
        pass
    return list(dict.fromkeys(model for model in models if model))


def _completion_kwargs(model: str) -> dict[str, Any]:
    options: dict[str, Any] = {}
    try:
        from local_model_runtime import local_completion_options

        options.update(local_completion_options(model))
    except Exception:
        pass
    from narad_litellm import completion_options

    options.update(completion_options(model))
    options.update({"temperature": 0, "max_tokens": _MAX_TOKENS, "timeout": _TIMEOUT_S})
    return options


def _record_cost(response: Any, source: str, model: str) -> None:
    try:
        usage = getattr(response, "usage", None)
        if usage:
            from cost_ledger import record

            record(source=source, model=model,
                   prompt_tokens=int(getattr(usage, "prompt_tokens", 0) or 0),
                   completion_tokens=int(getattr(usage, "completion_tokens", 0) or 0))
    except Exception:
        pass


def _ask(model: str, content: Any, *, source: str) -> str:
    from narad_litellm import ensure_model_credentials

    import privacy_gateway

    ensure_model_credentials(model)
    response = privacy_gateway.completion(
        model=model,
        messages=[{"role": "user", "content": content}],
        narad_source=source,
        **_completion_kwargs(model),
    )
    _record_cost(response, source, model)
    return str(response.choices[0].message.content or "")


def _ask_json(model: str, content: Any, *, source: str) -> dict[str, Any]:
    reply = _ask(model, content, source=source)
    try:
        return parse_json(reply)
    except ValueError:
        # One repair round: models occasionally truncate or wrap the object.
        retry = _ask(model, "Return the same answer as one valid JSON object and nothing else:\n" + reply[-12000:],
                     source=source)
        return parse_json(retry)


def _read(doc_type: str, doc: OcrText) -> tuple[str, dict[str, Any], list[dict[str, Any]], list[dict[str, Any]], str]:
    """Run the worker model over the document, a few pages per call."""
    import privacy_gateway

    errors: list[str] = []
    for model in _worker_models():
        try:
            document: dict[str, Any] = {}
            items: list[dict[str, Any]] = []
            rejected: list[dict[str, Any]] = []
            chosen = doc_type
            for pages in doc.page_chunks():
                payload = _ask_json(model, build_prompt(chosen, doc.format(pages)), source="document_fields")
                if chosen not in _SHAPES:
                    chosen = str(payload.get("doc_type") or "generic")
                    chosen = chosen if chosen in _SHAPES else "generic"
                part_doc, part_items, part_rejected = validate(chosen, payload, doc, start=len(items) + 1)
                for key, value in part_doc.items():
                    document[key] = document.get(key) or value
                items += part_items
                rejected += part_rejected
            return chosen, document, items, rejected, model
        except privacy_gateway.PrivacyGatewayError as exc:
            errors.append(f"{model}: {exc}")
        except Exception as exc:
            log.warning("extract_fields: %s failed: %s", model, exc)
            errors.append(f"{model}: {type(exc).__name__}: {exc}")
    raise RuntimeError("; ".join(errors) or "No worker model is configured.")


# ── The tool ──────────────────────────────────────────────────────────────────

def _escalation_offer(ocr: dict[str, Any], items: list[dict[str, Any]], doc_type: str) -> dict[str, Any]:
    unclear_pages = {item["page"] for item in items if {"unclear", "handwritten", "low_confidence"} & set(item["issues"])}
    pages = sorted({page["page"] for page in ocr.get("pages", []) if page.get("poor") and page["source"] == "ocr"}
                   | {page for page in unclear_pages if page})
    model = escalation_model()
    offer: dict[str, Any] = {"suggested": bool(pages) and bool(model), "pages": pages[:_ESCALATION_MAX_PAGES]}
    if model:
        import privacy_gateway

        offer.update(model=model, tier=privacy_gateway.provider_tier(model), label=_model_label(model))
    if pages:
        offer["reason"] = (
            "Parts of this prescription look handwritten." if doc_type == "prescription" and unclear_pages
            else "Some of this page was hard to read on this Mac."
        )
    return offer


def extract_fields(path: str, doc_type: str = "") -> dict:
    """Read the values in a family document (photo, scan or PDF) and prepare them
    for the person to confirm before anything is saved.

    Use for lab reports, bank statements, prescriptions, school circulars and
    bills (and "generic" for other forms) when the person wants values kept,
    tracked or acted on. The document is read on this Mac with local OCR; only
    its text reaches the model. Nothing is saved here: the person checks each
    value next to its image crop on the review screen, and only confirmed values
    reach their health or finance records.

    Args:
        path:     Local path of the photo or PDF (from the chat upload block).
        doc_type: lab_report | bank_statement | prescription | school_circular |
                  bill | generic. Leave empty to detect it.
    Returns:
        status, summary, review_id and review_url for the review screen, the
        detected doc_type, and a short preview of the values found.
    """
    import document_review
    from host_access import path_access_error
    from ocr_skill import line_index, read_document

    from tool_result import envelope

    p = Path(str(path or "")).expanduser()
    denied = path_access_error(p)
    if denied:
        return envelope(status="blocked", summary=denied, error=denied)
    wanted = (doc_type or "").strip().lower()
    if wanted and wanted not in DOC_TYPES:
        return envelope(status="error", summary=f"doc_type must be one of {', '.join(DOC_TYPES)}.",
                        error="bad_doc_type")

    ocr = read_document(p)
    if ocr.get("status") not in {"ok", "partial"} or not ocr.get("pages"):
        message = ocr.get("message", "Could not read the document.")
        return envelope(status="error", summary=message, error="ocr_unavailable" if ocr.get("unavailable") else "ocr")
    doc = OcrText(line_index(ocr))
    if not doc.rows:
        return envelope(status="error", summary="No text was found in this document.", error="no_text")

    chosen = wanted or detect_doc_type(doc.text) or "auto"
    try:
        chosen, document, items, rejected, model = _read(chosen, doc)
    except RuntimeError as exc:
        return envelope(status="error", error="model_unavailable",
                        summary=f"The document was read on this Mac, but no allowed model could extract its values ({exc}).")
    if not items:
        return envelope(status="ok", summary=(
            f"Read {ocr.get('name')} but found no values to save"
            + (f"; {len(rejected)} were left out because they were not in the document text." if rejected else ".")
        ), doc_type=chosen, review_id=None)

    import privacy_gateway

    review = document_review.create_review(
        doc_type=chosen,
        ocr=ocr,
        document=document,
        items=items,
        rejected=rejected,
        model=model,
        tier=privacy_gateway.provider_tier(model),
        escalation=_escalation_offer(ocr, items, chosen),
    )
    return envelope(
        status="ok",
        summary=_summary(review, ocr),
        requires_confirmation=True,
        review_id=review["review_id"],
        review_url=f"/?review={review['review_id']}",
        doc_type=chosen,
        item_count=len(items),
        needs_a_look=sum(1 for item in items if not item["default_checked"]),
        left_out=len(rejected),
        preview=[
            {"label": item["label"], "value": item["value"], "unit": item["unit"],
             "outside_printed_range": item["flag"] in {"high", "low"}, "needs_a_look": not item["default_checked"]}
            for item in items[:25]
        ],
    )


_TYPE_NAMES = {
    "lab_report": "lab report", "bank_statement": "bank statement", "prescription": "prescription",
    "school_circular": "school circular", "bill": "bill", "generic": "document",
}


def _summary(review: dict[str, Any], ocr: dict[str, Any]) -> str:
    items = review["items"]
    name = _TYPE_NAMES.get(review["doc_type"], "document")
    look = sum(1 for item in items if not item["default_checked"])
    parts = [f"Read {len(items)} value(s) from the {name} ({ocr.get('name')})."]
    if look:
        parts.append(f"{look} need a closer look.")
    if review.get("rejected"):
        parts.append(f"{len(review['rejected'])} were left out because they were not in the document text.")
    if review["doc_type"] in HEALTH_TYPES:
        outside = sum(1 for item in items if item["flag"] in {"high", "low"})
        if outside:
            parts.append(f"{outside} are outside the range printed on the report.")
        parts.append("Report these values objectively; do not diagnose or interpret them.")
    if review.get("escalation", {}).get("suggested"):
        parts.append("Some of it was hard to read; the review screen offers a clearer read.")
    parts.append(
        "Nothing is saved yet: the person checks each value against its crop and saves on the review "
        f"screen. Include this link in your reply: [Check and save](/?review={review['review_id']})"
    )
    return " ".join(parts)


# ── Escalation: a clearer read of hard pages, only with consent ───────────────

def _model_label(model: str) -> str:
    import privacy_gateway

    provider = privacy_gateway.provider_for_model(model)
    return {"anthropic": "Claude", "gemini": "Gemini", "openai": "OpenAI", "vertex_ai": "Gemini"}.get(
        provider, "the local vision model" if privacy_gateway.provider_tier(model) == "local" else provider
    )


def escalation_model() -> str:
    """The vision model a hard page may go to: local or trusted tier, with credentials.

    NARAD_DOC_ESCALATION_MODEL first (for example a local vision model in
    Ollama), then the connected Google, Anthropic and OpenAI vision endpoints.
    """
    from model_config import _endpoint_for_model, hosted_vision_endpoints

    import privacy_gateway

    override = os.environ.get("NARAD_DOC_ESCALATION_MODEL", "").strip()
    if override and privacy_gateway.raw_allowed(override) and _endpoint_for_model(override, source="document escalation"):
        return override
    return next(
        (endpoint.model for endpoint in hosted_vision_endpoints() if privacy_gateway.raw_allowed(endpoint.model)),
        "",
    )


def _page_data_uri(review_id: str, page: int) -> str | None:
    import document_review

    data = document_review.render_page(review_id, page)
    if data is None:
        return None
    return "data:image/jpeg;base64," + base64.b64encode(data).decode("ascii")


def escalate(review_id: str, pages: list[int], *, consent: bool) -> dict[str, Any]:
    """Ask a local or trusted vision model to read hard pages of one review.

    Only on the person's explicit, per-document consent; the page images go to
    that one model, and the call is logged in the profile's egress ledger.
    Items from those pages are replaced by the clearer read, unticked, so the
    person still confirms each against its crop.
    """
    import document_review

    import privacy_gateway

    if consent is not True:
        return {"status": "needs_consent", "message": "The person has not agreed to send this page."}
    review = document_review.load_review(review_id)
    if not review:
        return {"status": "not_found", "message": "No such document review."}
    if review.get("status") != "pending":
        return {"status": "error", "message": "This review is already closed."}
    known = {int(page["page"]) for page in review.get("pages", [])}
    wanted = sorted({int(page) for page in pages or [] if int(page) in known})[:_ESCALATION_MAX_PAGES]
    if not wanted:
        return {"status": "error", "message": "Choose a page of this document."}
    model = escalation_model()
    if not model:
        return {"status": "unavailable",
                "message": "No trusted or local vision model is connected, so the page stays on this Mac."}

    doc = OcrText(review.get("lines", {}))
    new_items: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    for page in wanted:
        uri = _page_data_uri(review_id, page)
        if uri is None:
            continue
        content = [
            {"type": "text", "text": build_prompt(review["doc_type"], doc.format({page}), from_image=True)},
            {"type": "image_url", "image_url": {"url": uri}},
        ]
        try:
            payload = _ask_json(model, content, source="document_escalation")
        except privacy_gateway.PrivacyGatewayError as exc:
            return {"status": "blocked", "message": str(exc)}
        except Exception as exc:
            return {"status": "error", "message": f"{_model_label(model)} could not read the page: {exc}"}
        _, items, dropped = validate(review["doc_type"], payload, doc, from_image=True, pages={page},
                                     id_prefix=f"v{page}-", start=1)
        new_items += items
        rejected += dropped

    with document_review._lock:
        review = document_review.load_review(review_id) or review
        kept = [item for item in review.get("items", []) if item.get("page") not in wanted]
        review["items"] = kept + new_items
        review["rejected"] = review.get("rejected", []) + rejected
        review["escalation"] = {
            **(review.get("escalation") or {}),
            "suggested": False,
            "done": {"at": datetime.now().isoformat(timespec="seconds"), "pages": wanted,
                     "model": model, "label": _model_label(model),
                     "tier": privacy_gateway.provider_tier(model)},
        }
        document_review._write_review(review)
    return {
        "status": "ok",
        "pages": wanted,
        "items": len(new_items),
        "message": f"{_model_label(model)} read page(s) {', '.join(map(str, wanted))}. Check each value against its crop.",
    }
