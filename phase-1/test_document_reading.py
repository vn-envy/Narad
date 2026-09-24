"""Reading family documents: local OCR, extract_fields, crop review, save-only-confirmed.

All synthetic and offline. A stub OCR engine stands in for PaddleOCR/Surya (no
model downloads) and a fake LiteLLM stands in for the worker model; the
privacy gateway itself is real, so the tests see exactly what would leave the
Mac. NARAD_OCR_REAL_TEST=1 adds one run through the installed OCR engine.
"""
from __future__ import annotations

import importlib.machinery
import io
import json
import os
import sqlite3
import sys
import types as pytypes
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import pytest

_ROOT = next(path for path in Path(__file__).resolve().parents if (path / "narad_paths.py").exists())
sys.path[:0] = [str(_ROOT)]
import narad_paths  # noqa: E402, F401

# isort: split
import docling_skill  # noqa: E402
import document_fields  # noqa: E402
import document_review  # noqa: E402
import finance_skill  # noqa: E402
import health_skill  # noqa: E402
import host_access  # noqa: E402
import ocr_skill  # noqa: E402
from PIL import Image  # noqa: E402

import privacy_gateway as gw  # noqa: E402
import profile_context  # noqa: E402
from profile_context import profile_scope  # noqa: E402

FAMILY = {term: ("PERSON", "Asha Sharma") for term in ("asha sharma", "asha", "sharma")}
_REAL_ESCALATION_MODEL = document_fields.escalation_model  # the fixture stubs it per test

# (text, confidence, box as page fractions x0, y0, x1, y1); deliberately not in reading order.
LAB_LINES = [
    ("HbA1c", 0.98, (0.04, 0.30, 0.12, 0.335)),
    ("Sunrise Diagnostics", 0.99, (0.04, 0.04, 0.30, 0.08)),
    ("Patient: Asha Sharma", 0.97, (0.04, 0.11, 0.33, 0.145)),
    ("Age: 52 Y", 0.98, (0.37, 0.11, 0.50, 0.145)),
    ("Date: 12/03/2026", 0.96, (0.54, 0.11, 0.76, 0.145)),
    ("Test", 0.99, (0.04, 0.22, 0.10, 0.255)),
    ("Result", 0.99, (0.40, 0.22, 0.49, 0.255)),
    ("Unit", 0.99, (0.56, 0.22, 0.61, 0.255)),
    ("Reference Range", 0.99, (0.71, 0.22, 0.93, 0.255)),
    ("8.2", 0.97, (0.40, 0.30, 0.44, 0.335)),
    ("%", 0.95, (0.56, 0.30, 0.57, 0.335)),
    ("4.0 - 5.6", 0.96, (0.71, 0.30, 0.81, 0.335)),
    ("Fasting Blood Sugar", 0.97, (0.04, 0.38, 0.26, 0.415)),
    ("142", 0.97, (0.40, 0.38, 0.44, 0.415)),
    ("mg/dL", 0.96, (0.56, 0.38, 0.62, 0.415)),
    ("70 - 100", 0.96, (0.71, 0.38, 0.80, 0.415)),
    ("TSH", 0.95, (0.04, 0.46, 0.09, 0.495)),
    ("2.1", 0.62, (0.40, 0.46, 0.43, 0.495)),
    ("uIU/mL", 0.93, (0.56, 0.46, 0.63, 0.495)),
    ("0.4 - 4.2", 0.95, (0.71, 0.46, 0.80, 0.495)),
    ("Haemoglobin", 0.97, (0.04, 0.54, 0.18, 0.575)),
    ("11.8", 0.97, (0.40, 0.54, 0.44, 0.575)),
    ("g/dL", 0.96, (0.56, 0.54, 0.60, 0.575)),
    ("12.0 - 15.5", 0.96, (0.71, 0.54, 0.83, 0.575)),
]
# After reading order: p1-l1 Sunrise … p1-l9 HbA1c, l10 8.2, l11 %, l12 range, l13 Fasting …
LAB_REPLY = """```json
{"doc_type": "lab_report",
 "document": {"lab_name": "Sunrise Diagnostics", "patient_name": "<PERSON_1>", "test_date": "2026-03-12"},
 "fields": [
  {"name": "HbA1c", "value": "8.2", "unit": "%", "reference_range": "4.0 - 5.6",
   "source_lines": ["p1-l9", "p1-l10", "p1-l11", "p1-l12"], "confidence": 0.95},
  {"name": "Fasting Blood Sugar", "value": "142", "unit": "mg/dL", "reference_range": "70 - 100",
   "source_lines": ["p1-l9"], "confidence": 0.9},
  {"name": "TSH", "value": "2.1", "unit": "uIU/mL", "reference_range": "0.4 - 4.2",
   "source_lines": ["p1-l17", "p1-l18"], "confidence": 0.9},
  {"name": "Vitamin D", "value": "18.5", "unit": "ng/mL", "reference_range": "30 - 100",
   "source_lines": ["p1-l21"], "confidence": 0.8},
  {"name": "Haemoglobin", "value": "11.8", "unit": "g/dL", "reference_range": "12.0 - 15.5",
   "source_lines": ["p1-l21", "p1-l22", "p1-l23", "p1-l24"], "confidence": 0.93},
 ]}
```"""

STATEMENT_LINES = [
    ("HDFC Bank Statement of Account", 0.99, (0.05, 0.05, 0.60, 0.08)),
    ("Date", 0.99, (0.05, 0.15, 0.12, 0.17)), ("Narration", 0.99, (0.20, 0.15, 0.35, 0.17)),
    ("Withdrawal", 0.99, (0.52, 0.15, 0.64, 0.17)), ("Deposit", 0.99, (0.66, 0.15, 0.76, 0.17)),
    ("Balance", 0.99, (0.80, 0.15, 0.90, 0.17)),
    ("12/03/2026", 0.98, (0.05, 0.20, 0.18, 0.22)), ("UPI-SWIGGY BANGALORE", 0.96, (0.20, 0.20, 0.50, 0.22)),
    ("450.00", 0.97, (0.55, 0.20, 0.62, 0.22)), ("10,550.00", 0.97, (0.80, 0.20, 0.90, 0.22)),
    ("13/03/2026", 0.98, (0.05, 0.25, 0.18, 0.27)), ("SALARY CREDIT ACME", 0.96, (0.20, 0.25, 0.45, 0.27)),
    ("85,000.00", 0.97, (0.66, 0.25, 0.76, 0.27)), ("95,550.00", 0.97, (0.80, 0.25, 0.90, 0.27)),
    ("14/03/2026", 0.98, (0.05, 0.30, 0.18, 0.32)), ("AMAZON PAY INDIA", 0.96, (0.20, 0.30, 0.42, 0.32)),
    ("1,299.00", 0.97, (0.55, 0.30, 0.63, 0.32)), ("94,251.00", 0.97, (0.80, 0.30, 0.90, 0.32)),
]
STATEMENT_REPLY = {
    "doc_type": "bank_statement",
    "document": {"bank": "HDFC Bank", "account_number": "", "period_from": "", "period_to": ""},
    "transactions": [
        {"date": "2026-03-12", "description": "UPI-SWIGGY BANGALORE", "amount": "450.00", "type": "debit",
         "balance": "10,550.00", "source_lines": ["p1-l7", "p1-l8", "p1-l9", "p1-l10"], "confidence": 0.95},
        {"date": "2026-03-13", "description": "SALARY CREDIT ACME", "amount": "85,000.00", "type": "credit",
         "balance": "95,550.00", "source_lines": ["p1-l11", "p1-l12", "p1-l13", "p1-l14"], "confidence": 0.95},
        {"date": "2026-03-14", "description": "AMAZON PAY INDIA", "amount": "1,299.00", "type": "debit",
         "balance": "94,251.00", "source_lines": ["p1-l7"], "confidence": 0.9},
        {"date": "2026-03-15", "description": "ZOMATO", "amount": "350.00", "type": "debit",
         "balance": "", "source_lines": ["p1-l15"], "confidence": 0.9},
    ],
}

PRESCRIPTION_LINES = [
    ("Dr. R. Mehta", 0.97, (0.05, 0.05, 0.30, 0.08)),
    ("Rx", 0.95, (0.05, 0.15, 0.10, 0.18)),
    ("Tab. Metformin 500 mg", 0.93, (0.05, 0.25, 0.40, 0.28)), ("1-0-1", 0.92, (0.45, 0.25, 0.55, 0.28)),
    ("30 days", 0.93, (0.60, 0.25, 0.72, 0.28)),
    ("Tab. Pcm 650", 0.58, (0.05, 0.33, 0.30, 0.36)), ("SOS", 0.55, (0.45, 0.33, 0.52, 0.36)),
]
PRESCRIPTION_REPLY = {
    "doc_type": "prescription",
    "document": {"prescriber": "Dr. R. Mehta", "patient_name": "", "date": ""},
    "medicines": [
        {"name": "Metformin", "strength": "500 mg", "dose": "1 tablet", "frequency": "1-0-1", "duration": "30 days",
         "instructions": "", "source_lines": ["p1-l3", "p1-l4", "p1-l5"], "confidence": 0.9},
        {"name": "Paracetamol", "strength": "650 mg", "dose": "", "frequency": "SOS", "duration": "",
         "instructions": "", "source_lines": ["p1-l6"], "confidence": 0.6, "handwritten": True},
        {"name": "Pcm", "strength": "650", "dose": "", "frequency": "SOS", "duration": "",
         "instructions": "", "source_lines": ["p1-l6", "p1-l7"], "confidence": 0.6, "unclear": True,
         "handwritten": True},
    ],
}

BILL_LINES = [
    ("Electricity Bill", 0.99, (0.05, 0.05, 0.40, 0.08)),
    ("Consumer No 1234567890", 0.97, (0.05, 0.12, 0.45, 0.15)),
    ("Bill Date 01/04/2026", 0.97, (0.05, 0.18, 0.40, 0.21)),
    ("Due Date 15/04/2026", 0.97, (0.05, 0.24, 0.40, 0.27)),
    ("Amount Payable Rs 2,340.00", 0.97, (0.05, 0.30, 0.50, 0.33)),
]
BILL_REPLY = {
    "doc_type": "bill",
    "document": {"biller": "Electricity", "bill_date": "2026-04-01", "due_date": "2026-04-15",
                 "amount_due": "2,340.00", "consumer_number": "1234567890"},
    "events": [{"title": "Pay the electricity bill", "date": "2026-04-15", "time": "", "location": "",
                "action": "Pay Rs 2,340.00", "source_lines": ["p1-l4"], "confidence": 0.95}],
    "fields": [{"name": "Amount Payable", "value": "Rs 2,340.00", "unit": "", "source_lines": ["p1-l5"],
                "confidence": 0.95}],
}


class _StubEngine:
    """Stands in for PaddleOCR: returns STUB["lines"] scaled to the page it is given."""

    name = "stub"
    loads = 0
    reads = 0

    def __init__(self) -> None:
        type(self).loads += 1

    def read(self, image):
        type(self).reads += 1
        width, height = image.size
        return ocr_skill.EnginePage(lines=[
            (text, confidence, (x0 * width, y0 * height, x1 * width, y1 * height))
            for text, confidence, (x0, y0, x1, y1) in STUB["lines"]
        ])


STUB: dict[str, list] = {"lines": []}


@pytest.fixture(autouse=True)
def _isolated(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    for var in ("NARAD_ENDPOINT_URL", "NARAD_ENDPOINT_MODEL", "NARAD_PROVIDER_TIERS",
                "NARAD_DOC_ESCALATION_MODEL", "NARAD_DOC_FIELDS_MODEL"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("NARAD_PII_DETECTOR", "rules")
    monkeypatch.setenv("NARAD_OCR_ENGINE", "stub")
    monkeypatch.setattr(profile_context, "PROFILES_DIR", tmp_path / "profiles")
    monkeypatch.setattr(health_skill, "_DB_PATH", tmp_path / "owner-health.db")
    monkeypatch.setattr(finance_skill, "_DB_PATH", tmp_path / "owner-finance.db")
    monkeypatch.setattr(gw, "_privacy_dir", lambda profile_id=None: tmp_path / "privacy" / (profile_id or "x"))
    monkeypatch.setattr(gw, "_family_terms", lambda: dict(FAMILY))
    monkeypatch.setattr(gw, "_stores", {})
    gw._terms_cache.update(key=None, pattern=None, labels={}, checked=0.0)
    monkeypatch.setattr(host_access, "path_access_error", lambda path: None)
    monkeypatch.setattr(document_fields, "_worker_models", lambda: ["deepseek/deepseek-flash"])
    monkeypatch.setattr(document_fields, "escalation_model", lambda: "")
    STUB["lines"] = list(LAB_LINES)
    _StubEngine.loads = _StubEngine.reads = 0
    ocr_skill.register_engine("stub", _StubEngine)
    with profile_scope("asha"):
        yield
    ocr_skill.register_engine("stub", None)


def _photo(tmp_path: Path, name: str = "report.png", size: tuple[int, int] = (1400, 1000)) -> Path:
    path = tmp_path / name
    image = Image.new("RGB", size, "white")
    image.putpixel((0, 0), tuple(name.encode()[:3].ljust(3, b"x")))  # distinct files, distinct OCR cache keys
    image.save(path)
    return path


def _ledger(tmp_path: Path) -> list[dict]:
    path = tmp_path / "privacy" / "x" / "egress.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


def _fake_litellm(monkeypatch: pytest.MonkeyPatch, replies: list[str]) -> list[dict]:
    captured: list[dict] = []
    module = pytypes.ModuleType("litellm")
    module.__spec__ = importlib.machinery.ModuleSpec("litellm", None)

    def completion(**kwargs):
        captured.append(kwargs)
        reply = replies[min(len(captured), len(replies)) - 1]
        message = pytypes.SimpleNamespace(content=reply)
        return pytypes.SimpleNamespace(choices=[pytypes.SimpleNamespace(message=message)])

    module.completion = completion
    monkeypatch.setitem(sys.modules, "litellm", module)
    return captured


def _read_review(monkeypatch, tmp_path, lines, reply, doc_type="", name="doc.png") -> dict:
    STUB["lines"] = list(lines)
    _fake_litellm(monkeypatch, [reply if isinstance(reply, str) else json.dumps(reply)])
    result = document_fields.extract_fields(str(_photo(tmp_path, name)), doc_type)
    assert result["status"] == "ok", result
    return document_review.load_review(result["review_id"])


def _health_rows(table: str) -> list[sqlite3.Row]:
    conn = health_skill._get_conn()
    rows = conn.execute(f"SELECT * FROM {table} ORDER BY id").fetchall()
    conn.close()
    return rows


def _transactions() -> list[sqlite3.Row]:
    conn = finance_skill._db()
    rows = conn.execute("SELECT * FROM transactions ORDER BY date").fetchall()
    conn.close()
    return rows


# ── Local OCR ─────────────────────────────────────────────────────────────────

def test_ocr_lines_are_normalised_ordered_and_grouped_into_rows(tmp_path: Path) -> None:
    result = ocr_skill.read_document(_photo(tmp_path))
    assert result["status"] == "ok" and result["engine"] == "stub"
    page = result["pages"][0]
    assert (page["width"], page["height"], page["source"]) == (1400, 1000, "ocr")
    assert Path(page["image"]).is_file()
    texts = [line["text"] for line in page["lines"]]
    assert texts[:4] == ["Sunrise Diagnostics", "Patient: Asha Sharma", "Age: 52 Y", "Date: 12/03/2026"]
    hba1c = next(line for line in page["lines"] if line["text"] == "HbA1c")
    assert hba1c["id"] == "p1-l9" and hba1c["bbox"] == [0.04, 0.3, 0.12, 0.335]
    assert all(0.0 <= value <= 1.0 for line in page["lines"] for value in line["bbox"])
    assert page["mean_confidence"] > 0.9 and page["poor"] is False
    text = ocr_skill.format_lines(result)
    assert "[p1-l9] HbA1c | [p1-l10] 8.2 | [p1-l11] % | [p1-l12] 4.0 - 5.6" in text


def test_phone_rotation_and_heic_without_pillow_heif(tmp_path: Path) -> None:
    sideways = tmp_path / "sideways.jpg"
    exif = Image.Exif()
    exif[0x0112] = 6  # stored rotated; shown after turning 90 degrees clockwise
    Image.new("RGB", (400, 200), "white").save(sideways, exif=exif.tobytes())
    assert ocr_skill.load_image(sideways).size == (200, 400)

    if ocr_skill._importable("pillow_heif"):
        pytest.skip("pillow-heif is installed")
    heic = tmp_path / "photo.heic"
    heic.write_bytes(b"\x00\x00\x00\x18ftypheic")
    result = ocr_skill.read_document(heic)
    assert result["status"] == "error" and result["unavailable"]
    assert "pillow-heif" in result["message"] and "JPEG" in result["message"]


def test_missing_engine_gives_the_install_step(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("NARAD_OCR_ENGINE", "surya")
    monkeypatch.setattr(ocr_skill, "_importable", lambda module: False)
    extracted = docling_skill.extract_document(str(_photo(tmp_path)))
    assert extracted["status"] == "error"
    assert 'pip install -e ".[ocr]"' in extracted["message"]


def test_ocr_is_cached_per_profile_and_unloads_when_idle(tmp_path: Path) -> None:
    photo = _photo(tmp_path)
    ocr_skill.read_document(photo)
    ocr_skill.read_document(photo)
    assert (_StubEngine.loads, _StubEngine.reads) == (1, 1)
    with profile_scope("ravi"):
        ocr_skill.read_document(photo)
    assert _StubEngine.reads == 2  # another profile never reuses Asha's cache

    assert ocr_skill._slot.loaded() == "stub"
    assert ocr_skill._slot.unload_idle(now=10**9) is True
    assert ocr_skill._slot.loaded() == ""
    ocr_skill.read_document(photo, use_cache=False)
    assert _StubEngine.loads == 2


def test_paddle_adapter_reads_paddlex_results(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    np = pytest.importorskip("numpy")
    seen: dict = {}

    class FakePaddleOCR:
        def __init__(self, **kwargs):
            seen["kwargs"] = kwargs

        def predict(self, array):
            seen["pixel"] = array[0, 0].tolist()
            rotated = np.zeros((array.shape[1], array.shape[0], 3), dtype=np.uint8)
            return [{
                "rec_texts": ["HbA1c", "8.2"],
                "rec_scores": np.array([0.98, 0.91]),
                "rec_boxes": np.array([[10, 20, 110, 40], [150, 20, 190, 40]], dtype=np.int16),
                "doc_preprocessor_res": {"angle": 90, "output_img": rotated},
            }]

    module = pytypes.ModuleType("paddleocr")
    module.__spec__ = importlib.machinery.ModuleSpec("paddleocr", None)
    module.PaddleOCR = FakePaddleOCR
    monkeypatch.setitem(sys.modules, "paddleocr", module)
    monkeypatch.setenv("NARAD_OCR_ENGINE", "paddle")
    photo = tmp_path / "red.png"
    Image.new("RGB", (300, 200), (255, 0, 0)).save(photo)

    result = ocr_skill.read_document(photo, use_cache=False)
    ocr_skill.unload()
    assert result["engine"] == "paddle"
    assert seen["pixel"] == [0, 0, 255]  # PaddleX expects BGR arrays
    assert seen["kwargs"]["text_recognition_model_name"] == "devanagari_PP-OCRv5_mobile_rec"
    assert seen["kwargs"]["use_doc_orientation_classify"] is True
    page = result["pages"][0]
    assert (page["width"], page["height"]) == (200, 300)  # boxes are on the upright page
    assert page["lines"][0]["bbox"] == [0.05, 0.0667, 0.55, 0.1333]
    assert [line["confidence"] for line in page["lines"]] == [0.98, 0.91]


# ── extract_document ──────────────────────────────────────────────────────────

def _scanned_pdf(tmp_path: Path) -> Path:
    fitz = pytest.importorskip("fitz")
    pdf = fitz.open()
    page = pdf.new_page()
    page.insert_text((72, 72), "Sunrise Diagnostics summary page with a real text layer for testing.")
    scan = pdf.new_page()
    scan.insert_image(scan.rect, filename=str(_photo(tmp_path, "scan.png", (600, 800))))
    path = tmp_path / "report.pdf"
    pdf.save(str(path))
    pdf.close()
    return path


def test_extract_document_reads_photos_and_only_the_scanned_pdf_pages(tmp_path: Path) -> None:
    photo = docling_skill.extract_document(str(_photo(tmp_path)))
    assert photo["status"] == "ok" and photo["engine"] == "ocr:stub" and photo["pages"] == 1
    assert "[p1-l9] HbA1c  [p1-l10] 8.2" in photo["content"]
    assert set(photo) >= {"status", "path", "content", "tables", "pages", "engine", "message"}

    pdf = _scanned_pdf(tmp_path)
    reads = _StubEngine.reads
    text_only = docling_skill.extract_document_text(str(pdf))
    assert _StubEngine.reads == reads  # chat previews never run OCR
    assert "Page(s) 2 are scanned" in text_only["message"]

    extracted = docling_skill.extract_document(str(pdf))
    assert extracted["status"] == "ok" and extracted["pages"] == 2
    assert extracted["engine"] == "pymupdf+ocr:stub"
    assert "## Page 1\n\nSunrise Diagnostics summary page" in extracted["content"]
    assert "## Page 2 (OCR)" in extracted["content"] and "[p2-l9] HbA1c" in extracted["content"]
    assert _StubEngine.reads == reads + 1  # the text page was not OCR'd


# ── extract_fields: schema, repair, hallucination guard ──────────────────────

def test_parse_json_repairs_fences_trailing_commas_and_quotes() -> None:
    assert document_fields.parse_json('Sure:\n```json\n{"a": [1, 2,],}\n```') == {"a": [1, 2]}
    assert document_fields.parse_json('{“a”: “b”}') == {"a": "b"}
    with pytest.raises(ValueError):
        document_fields.parse_json("no json here")


def test_numbers_ranges_and_dates() -> None:
    assert document_fields.range_flag(8.2, "4.0 - 5.6") == "high"
    assert document_fields.range_flag(65, "70-100 mg/dL") == "low"
    assert document_fields.range_flag(180, "< 200") == "normal"
    assert document_fields.range_flag(120000, "1,50,000 – 4,10,000") == "low"
    assert document_fields.range_flag(45, "> 40") == "normal"
    assert document_fields.to_number("<0.5") == 0.5
    assert document_fields.to_number("१२.५ %") == 12.5
    assert document_fields.to_number("Negative") is None
    assert document_fields.iso_date("12-Mar-2026") == "2026-03-12"
    assert document_fields.iso_date("03/12/2026") == "2026-12-03"  # day first
    assert document_fields._date_grounded("2026-03-12", "Date: 12/03/26", "")
    assert document_fields._date_grounded("2026-03-12", "12 मार्च 2026", "")
    assert document_fields._date_grounded("2026-03-12", "12/03 UPI", "Statement 2026")
    assert not document_fields._date_grounded("2026-03-13", "Date: 12/03/2026", "")


def test_extract_fields_lab_report_is_pseudonymised_guarded_and_pending(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    captured = _fake_litellm(monkeypatch, [LAB_REPLY])
    result = document_fields.extract_fields(str(_photo(tmp_path)))

    assert result["status"] == "ok" and result["requires_confirmation"] is True
    assert result["doc_type"] == "lab_report" and result["review_url"] == f"/?review={result['review_id']}"
    assert result["item_count"] == 4 and result["left_out"] == 1
    assert "do not diagnose" in result["summary"] and "Check and save" in result["summary"]

    # The worker model saw placeholders and line ids, never the name or an image.
    prompt = captured[0]["messages"][0]["content"]
    assert isinstance(prompt, str)
    assert "Asha" not in prompt and "Sharma" not in prompt and "<PERSON_1>" in prompt
    assert "[p1-l9] HbA1c | [p1-l10] 8.2" in prompt
    assert _ledger(tmp_path)[-1]["source"] == "document_fields"
    assert _ledger(tmp_path)[-1]["tier"] == "redact"

    review = document_review.load_review(result["review_id"])
    assert review["status"] == "pending"
    assert review["document"] == {"lab_name": "Sunrise Diagnostics", "patient_name": "Asha Sharma",
                                  "test_date": "2026-03-12"}
    items = {item["label"]: item for item in review["items"]}
    assert set(items) == {"HbA1c", "Fasting Blood Sugar", "TSH", "Haemoglobin"}
    assert review["rejected"] == [{"kind": "lab", "label": "Vitamin D", "value": "18.5",
                                   "reason": "value not found in the document text"}]
    hba1c = items["HbA1c"]
    assert (hba1c["flag"], hba1c["normalised_value"], hba1c["default_checked"]) == ("high", 8.2, True)
    assert hba1c["bbox"] == [0.04, 0.3, 0.81, 0.335]
    fbs = items["Fasting Blood Sugar"]
    assert fbs["source_lines"] == ["p1-l13", "p1-l14", "p1-l15", "p1-l16"]
    assert "source_repaired" in fbs["issues"] and fbs["flag"] == "high"
    tsh = items["TSH"]
    assert "low_confidence" in tsh["issues"] and tsh["default_checked"] is False
    assert items["Haemoglobin"]["flag"] == "low"
    assert (tmp_path / "profiles" / "asha" / "documents" / "reviews" / review["review_id"] / "page-1.jpg").is_file()

    # Pending means nothing reached health.db.
    assert _health_rows("lab_results") == []


def test_a_bad_reply_gets_one_repair_round_then_fails_cleanly(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    captured = _fake_litellm(monkeypatch, ["Here are the values: HbA1c 8.2", "still not json"])
    result = document_fields.extract_fields(str(_photo(tmp_path)), "lab_report")
    assert result["status"] == "error" and result["error"] == "model_unavailable"
    assert len(captured) == 2
    assert not (tmp_path / "profiles" / "asha" / "documents" / "reviews").exists()


def test_extract_fields_refuses_another_profiles_file(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(host_access, "path_access_error", lambda path: "That file belongs to another family profile")
    result = document_fields.extract_fields(str(_photo(tmp_path)))
    assert result["status"] == "blocked" and _StubEngine.reads == 0


def test_statement_validation_keeps_debits_marks_credits_and_drops_inventions(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    review = _read_review(monkeypatch, tmp_path, STATEMENT_LINES, STATEMENT_REPLY)
    assert review["doc_type"] == "bank_statement" and review["document"]["account"] == "HDFC"
    items = {item["label"]: item for item in review["items"]}
    assert set(items) == {"UPI-SWIGGY BANGALORE", "SALARY CREDIT ACME", "AMAZON PAY INDIA"}
    assert items["SALARY CREDIT ACME"]["saveable"] is False
    assert items["AMAZON PAY INDIA"]["details"] == {"date": "2026-03-14", "type": "debit", "balance": "94,251.00"}
    assert "source_repaired" in items["AMAZON PAY INDIA"]["issues"]
    assert [r["label"] for r in review["rejected"]] == ["ZOMATO"]


# ── Crop review screen (server) ───────────────────────────────────────────────

def test_crop_and_review_are_only_served_to_their_own_profile(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import server
    from fastapi.testclient import TestClient

    import family_profiles

    monkeypatch.setattr(server, "_AUTH_MODE", "strict")
    monkeypatch.setattr(server, "_login_failures", {})
    monkeypatch.setattr(family_profiles, "FAMILY_PROFILES_PATH", tmp_path / "family_profiles.json")
    monkeypatch.setattr(family_profiles, "PROFILE_SESSION_SECRET_PATH", tmp_path / "profile_secret")
    family_profiles.update_profile("default", pin="8642")
    family_profiles.create_profile("Alice", "2468")
    family_profiles.create_profile("Bob", "1357")
    client = TestClient(server.app)

    def headers(user_id: str, pin: str) -> dict[str, str]:
        response = client.post("/profiles/login", json={"user_id": user_id, "pin": pin})
        return {"Authorization": f"Bearer {response.json()['token']}"}

    alice, bob = headers("alice", "2468"), headers("bob", "1357")
    with profile_scope("alice"):
        review = _read_review(monkeypatch, tmp_path, LAB_LINES, LAB_REPLY)
    rid = review["review_id"]
    item = review["items"][0]["id"]

    own = client.get(f"/documents/reviews/{rid}/items/{item}/crop", headers=alice)
    assert own.status_code == 200 and own.headers["content-type"] == "image/jpeg"
    assert "no-store" in own.headers["cache-control"]
    assert Image.open(io.BytesIO(own.content)).size[0] > 0
    body = client.get(f"/documents/reviews/{rid}", headers=alice).json()
    assert body["items"][0]["crop_url"] == f"/documents/reviews/{rid}/items/{item}/crop"
    assert "path" not in json.dumps(body["pages"]) and "source_lines" not in body["items"][0]

    for path in (f"/documents/reviews/{rid}", f"/documents/reviews/{rid}/items/{item}/crop",
                 f"/documents/reviews/{rid}/pages/1"):
        assert client.get(path, headers=bob).status_code == 404
    assert client.post(f"/documents/reviews/{rid}/save", headers=bob,
                       json={"items": [{"id": item, "action": "confirm"}]}).status_code == 404
    assert client.post(f"/documents/reviews/{rid}/discard", headers=bob).status_code == 404
    assert client.get(f"/documents/reviews/{rid}/items/{item}/crop").status_code == 401
    assert client.get("/documents/reviews", headers=bob).json() == {"reviews": []}

    saved = client.post(f"/documents/reviews/{rid}/save", headers=alice, json={
        "items": [{"id": item, "action": "confirm"}], "document": {"test_date": "2026-03-12"},
    })
    assert saved.status_code == 200 and saved.json()["stored"] == 1
    with profile_scope("bob"):
        assert _health_rows("lab_results") == []
    with profile_scope("alice"):
        assert len(_health_rows("lab_results")) == 1


# ── Save only what was confirmed ──────────────────────────────────────────────

def test_only_confirmed_lab_values_reach_health_db(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    review = _read_review(monkeypatch, tmp_path, LAB_LINES, LAB_REPLY)
    ids = {item["label"]: item["id"] for item in review["items"]}
    rid = review["review_id"]

    needs_date = document_review.save_review(rid, [{"id": ids["HbA1c"], "action": "confirm"}],
                                             document={"test_date": ""})
    assert needs_date["status"] == "needs_input" and _health_rows("lab_results") == []

    result = document_review.save_review(rid, [
        {"id": ids["HbA1c"], "action": "confirm"},
        {"id": ids["TSH"], "action": "drop"},
        {"id": ids["Haemoglobin"], "action": "confirm", "value": "11.9"},
    ])
    assert result["status"] == "ok" and result["stored"] == 2
    rows = _health_rows("lab_results")
    assert [(r["test_name"], r["value"], r["flag"], r["test_date"]) for r in rows] == [
        ("HbA1c", "8.2", "high", "2026-03-12"),
        ("Haemoglobin", "11.9", "low", "2026-03-12"),
    ]
    assert {r["source_review_id"] for r in rows} == {rid}
    assert rows[0]["source_item_id"] == ids["HbA1c"]

    saved = document_review.load_review(rid)
    assert saved["status"] == "saved" and saved["saved"]["edited"] == [ids["Haemoglobin"]]
    haemoglobin = next(item for item in saved["items"] if item["id"] == ids["Haemoglobin"])
    assert (haemoglobin["value"], haemoglobin["confirmed_as"]["value"]) == ("11.8", "11.9")
    assert {row["item_id"] for row in saved["saved"]["rows"]} == {ids["HbA1c"], ids["Haemoglobin"]}
    decisions = {item["label"]: item["decision"] for item in saved["items"]}
    assert decisions == {"HbA1c": "confirmed", "Fasting Blood Sugar": "dropped", "TSH": "dropped",
                         "Haemoglobin": "confirmed"}
    again = document_review.save_review(rid, [{"id": ids["TSH"], "action": "confirm"}])
    assert again["status"] == "error" and len(_health_rows("lab_results")) == 2
    assert document_review.discard_review(rid) is False  # saved rows keep their crops


def test_statement_saves_confirmed_debits_once_and_dedupes_with_csv(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    first = _read_review(monkeypatch, tmp_path, STATEMENT_LINES, STATEMENT_REPLY, name="march.png")
    ids = {item["label"]: item["id"] for item in first["items"]}
    result = document_review.save_review(first["review_id"], [
        {"id": ids["UPI-SWIGGY BANGALORE"], "action": "confirm"},
        {"id": ids["SALARY CREDIT ACME"], "action": "confirm"},  # money in: never saved
        {"id": ids["AMAZON PAY INDIA"], "action": "drop"},
    ])
    assert result["stored"] == 1
    assert [(r["date"], r["amount"], r["merchant"], r["bank"]) for r in _transactions()] == [
        ("2026-03-12", 450.0, "UPI-SWIGGY BANGALORE", "HDFC"),
    ]
    assert _transactions()[0]["raw"] == f"document:{first['review_id']}:{ids['UPI-SWIGGY BANGALORE']}"

    again = _read_review(monkeypatch, tmp_path, STATEMENT_LINES, STATEMENT_REPLY, name="march-again.png")
    ids = {item["label"]: item["id"] for item in again["items"]}
    result = document_review.save_review(again["review_id"], [
        {"id": ids["UPI-SWIGGY BANGALORE"], "action": "confirm"},
        {"id": ids["AMAZON PAY INDIA"], "action": "confirm"},
    ])
    assert [row["status"] for row in result["rows"]] == ["duplicate", "stored"]
    assert len(_transactions()) == 2

    csv_path = tmp_path / "hdfc.csv"
    csv_path.write_text("Date,Narration,Debit Amount,Credit Amount,Closing Balance\n"
                        "14/03/2026,AMAZON PAY INDIA,1299.00,,94251.00\n"
                        "16/03/2026,BIGBASKET,800.00,,93451.00\n", encoding="utf-8")
    imported = finance_skill.import_csv(str(csv_path))
    assert (imported["imported"], imported["duplicates"]) == (1, 1)


def test_medicine_reminders_and_calendar_only_when_ticked(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    review = _read_review(monkeypatch, tmp_path, PRESCRIPTION_LINES, PRESCRIPTION_REPLY, "prescription")
    items = {item["label"]: item for item in review["items"]}
    assert set(items) == {"Metformin", "Pcm"}  # "Paracetamol" is not what the page says
    assert {"unclear", "handwritten"} <= set(items["Pcm"]["issues"]) and not items["Pcm"]["default_checked"]
    assert review["escalation"]["pages"] == [1] and review["escalation"]["suggested"] is False  # no model
    document_review.save_review(review["review_id"], [{"id": items["Metformin"]["id"], "action": "confirm"}])
    assert _health_rows("medication_reminders") == []

    review = _read_review(monkeypatch, tmp_path, PRESCRIPTION_LINES, PRESCRIPTION_REPLY, "prescription", "rx2.png")
    metformin = next(item for item in review["items"] if item["label"] == "Metformin")
    document_review.save_review(review["review_id"], [{"id": metformin["id"], "action": "confirm"}],
                                options={"medication_reminders": True})
    rows = _health_rows("medication_reminders")
    assert [(r["med_name"], r["dose"], r["schedule"]) for r in rows] == [
        ("Metformin", "500 mg 1 tablet", "8am and 9pm, for 30 days"),
    ]

    bill = _read_review(monkeypatch, tmp_path, BILL_LINES, BILL_REPLY, "bill", "bill.png")
    event = next(item for item in bill["items"] if item["kind"] == "event")
    assert event["value"] == "2026-04-15" and event["default_checked"]
    with patch("calendar_skill.create_event", return_value={"status": "ok", "html_link": "x"}) as create:
        document_review.save_review(bill["review_id"], [{"id": event["id"], "action": "confirm"}])
        assert create.call_count == 0
    bill = _read_review(monkeypatch, tmp_path, BILL_LINES, BILL_REPLY, "bill", "bill2.png")
    event = next(item for item in bill["items"] if item["kind"] == "event")
    with patch("calendar_skill.create_event", return_value={"status": "ok", "html_link": "x"}) as create:
        document_review.save_review(bill["review_id"], [{"id": event["id"], "action": "confirm"}],
                                    options={"calendar_events": True, "reminders": True})
    assert create.call_args.kwargs["dry_run"] is False
    assert create.call_args.args[1:] == ("2026-04-15T09:00", "2026-04-15T10:00")

    delivered: list[dict] = []
    with patch("vahana.deliver", side_effect=lambda **kw: delivered.append(kw)):
        assert document_review.fire_due_reminders(datetime(2026, 4, 15, 8, 0), ["asha"]) == 0
        assert document_review.fire_due_reminders(datetime(2026, 4, 15, 9, 30), ["asha"]) == 1
        assert document_review.fire_due_reminders(datetime(2026, 4, 15, 10, 0), ["asha"]) == 0
    assert delivered[0]["user_id"] == "asha" and delivered[0]["title"] == "Pay the electricity bill"


# ── Lab history for Rama ──────────────────────────────────────────────────────

def test_lab_history_tool_follows_one_test_across_reports() -> None:
    health_skill.record_lab_result("Glycated Haemoglobin (HbA1c)", "8.2", "2025-12-01", value_num=8.2,
                                   unit="%", ref_range="4.0 - 5.6", flag="high",
                                   source_review_id="rev_0123456789abcdef", source_item_id="f1")
    health_skill.record_lab_result("HbA1c", "7.1", "2026-03-12", value_num=7.1, unit="%",
                                   ref_range="4.0 - 5.6", flag="high")
    health_skill.record_lab_result("Haemoglobin", "11.8", "2026-03-12", value_num=11.8, unit="g/dL")
    assert health_skill.lab_test_key("Mean Corpuscular Haemoglobin (MCH)") == "mch"
    assert health_skill.lab_test_key("Cholesterol, LDL") == "ldl"

    result = health_skill.get_lab_results("hba1c", days=3650)
    assert [entry["value"] for entry in result["entries"]] == ["8.2", "7.1"]
    trend = result["trends"]["hba1c"]
    assert trend["change"] == -1.1 and trend["from"]["date"] == "2025-12-01"
    assert result["entries"][0]["source_crop"] == "/documents/reviews/rev_0123456789abcdef/items/f1/crop"
    assert "Not a diagnosis" in result["note"]
    with profile_scope("ravi"):
        assert health_skill.get_lab_results("HbA1c", days=3650)["count"] == 0


# ── Escalation: consent and tiers ─────────────────────────────────────────────

def test_escalation_needs_consent_and_only_reaches_trusted_vision(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    review = _read_review(monkeypatch, tmp_path, PRESCRIPTION_LINES, PRESCRIPTION_REPLY, "prescription")
    rid = review["review_id"]
    image_reply = json.dumps({
        "doc_type": "prescription",
        "document": {},
        "medicines": [{"name": "Pcm", "strength": "650 mg", "dose": "1 tablet", "frequency": "SOS",
                       "duration": "3 days", "instructions": "", "source_lines": ["p1-l6", "p1-l7"],
                       "confidence": 0.85}],
    })
    captured = _fake_litellm(monkeypatch, [image_reply])

    refused = document_fields.escalate(rid, [1], consent=False)
    assert refused["status"] == "needs_consent" and captured == []

    # A redact-tier model can never be chosen, and the gateway refuses images for it anyway.
    monkeypatch.setattr(document_fields, "escalation_model", lambda: "")
    assert document_fields.escalate(rid, [1], consent=True)["status"] == "unavailable"
    image_message = [{"role": "user", "content": [{"type": "text", "text": "read"},
                                                  {"type": "image_url", "image_url": {"url": "data:,"}}]}]
    with pytest.raises(gw.PrivacyGatewayError):
        gw.completion(model="deepseek/deepseek-flash", messages=image_message, narad_source="document_escalation")
    assert _ledger(tmp_path)[-1]["blocked"] == "raw_content" and captured == []
    monkeypatch.setenv("NARAD_DOC_ESCALATION_MODEL", "deepseek/deepseek-flash")
    assert _REAL_ESCALATION_MODEL() != "deepseek/deepseek-flash"
    monkeypatch.setenv("NARAD_DOC_ESCALATION_MODEL", "ollama/qwen3-vl:8b")
    with patch("model_config._endpoint_for_model", return_value=object()):
        assert _REAL_ESCALATION_MODEL() == "ollama/qwen3-vl:8b"  # a local vision model needs no cloud

    monkeypatch.setattr(document_fields, "escalation_model", lambda: "gemini/gemini-2.5-flash")
    done = document_fields.escalate(rid, [1], consent=True)
    assert done["status"] == "ok" and done["pages"] == [1]
    parts = captured[-1]["messages"][0]["content"]
    assert parts[1]["type"] == "image_url" and parts[1]["image_url"]["url"].startswith("data:image/jpeg;base64,")
    entry = _ledger(tmp_path)[-1]
    assert (entry["source"], entry["tier"], entry["provider"], entry["blocked"]) == (
        "document_escalation", "trusted", "gemini", "")

    updated = document_review.load_review(rid)
    assert [item["label"] for item in updated["items"]] == ["Pcm"]
    read = updated["items"][0]
    assert read["id"] == "v1-1" and "read_from_image" in read["issues"] and read["default_checked"] is False
    assert read["details"]["duration"] == "3 days" and read["bbox"] is not None
    assert updated["escalation"]["done"]["label"] == "Gemini"


# ── Opt-in: the installed OCR engine on a synthetic report ───────────────────

@pytest.mark.skipif(not os.environ.get("NARAD_OCR_REAL_TEST"), reason="set NARAD_OCR_REAL_TEST=1 to run the real OCR engine")
def test_real_engine_reads_a_synthetic_lab_report(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from PIL import ImageDraw, ImageFont

    monkeypatch.setenv("NARAD_OCR_ENGINE", os.environ.get("NARAD_OCR_REAL_ENGINE", "auto"))
    ocr_skill.register_engine("stub", None)
    if not ocr_skill.engine_name():
        pytest.skip('no OCR engine installed: pip install -e ".[ocr]"')
    image = Image.new("RGB", (1400, 700), "white")
    draw = ImageDraw.Draw(image)
    try:
        font = ImageFont.truetype("DejaVuSans.ttf", 34)
    except OSError:
        font = ImageFont.load_default(size=34)
    for y, row in enumerate((("Patient: Asha Sharma", ""), ("HbA1c", "8.2 %"), ("Haemoglobin", "11.8 g/dL"))):
        draw.text((60, 80 + y * 120), row[0], fill="black", font=font)
        draw.text((700, 80 + y * 120), row[1], fill="black", font=font)
    path = tmp_path / "synthetic.png"
    image.save(path)
    result = ocr_skill.read_document(path, use_cache=False)
    ocr_skill.unload()
    text = ocr_skill.format_lines(result)
    assert result["status"] == "ok", result
    assert "8.2" in text and "11.8" in text and "hba1c" in text.lower()
    assert result["timings_ms"]["pages"][0] < 30_000
