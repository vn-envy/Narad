"""
Matsya document extraction skill.

Uses lightweight, format-specific readers. Photos and PDF pages without a text
layer fall back to local OCR (ocr_skill), which loads its model only on demand.
"""
from __future__ import annotations

from pathlib import Path

_IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp", ".heic", ".heif"}
# A PDF page with fewer visible characters than this has no usable text layer.
_SCANNED_PAGE_CHARS = 25


def extract_document(file_path: str) -> dict:
    """Extract text, tables, and structure from a document file.

    Supports PDF, DOCX, PPTX, XLSX, CSV, HTML, Markdown, and plain text, and
    photos of documents (JPG, PNG, WebP, HEIC). Photos and scanned PDF pages are
    read with local OCR on this Mac; their lines carry ids such as [p1-l3].
    Tables are preserved as Markdown tables. Multi-column layouts are
    linearised in reading order. To save values from a lab report, statement,
    prescription, bill or circular, use extract_fields instead.

    Args:
        file_path: Absolute or home-relative path to the document.
                   e.g. "/Users/me/Downloads/report.pdf" or "~/Desktop/plan.docx"

    Returns a dict with:
        status:   "ok" | "error"
        content:  Full extracted content as Markdown string
        tables:   Number of tables found
        pages:    Number of pages (PDFs and photos, 0 for other formats)
        message:  Summary or error description
    """
    return _extract(file_path, ocr=True)


def extract_document_text(file_path: str) -> dict:
    """extract_document without OCR, for chat previews that must stay fast.

    Scanned pages are named in the message instead of being read.
    """
    return _extract(file_path, ocr=False)


def _extract(file_path: str, *, ocr: bool) -> dict:
    p = Path(file_path).expanduser().resolve()

    if not p.exists():
        return {
            "status":  "error",
            "message": f"File not found: {p}",
            "content": "",
        }

    if not p.is_file():
        return {
            "status":  "error",
            "message": f"Not a file: {p}",
            "content": "",
        }

    supported = {
        ".pdf", ".docx", ".doc", ".pptx", ".ppt",
        ".xlsx", ".xlsm", ".csv", ".json", ".jsonl", ".yaml", ".yml",
        ".xml", ".html", ".htm", ".md", ".txt", ".rtf", ".odt",
    } | _IMAGE_SUFFIXES
    if p.suffix.lower() not in supported:
        return {
            "status":  "error",
            "message": (
                f"Unsupported file type: {p.suffix}. "
                f"Supported: {', '.join(sorted(supported))}"
            ),
            "content": "",
        }

    if p.suffix.lower() in _IMAGE_SUFFIXES:
        return _extract_photo(p, ocr=ocr)

    # Default: PDF extraction via PyMuPDF
    if p.suffix.lower() == ".pdf":
        try:
            import fitz  # PyMuPDF

            doc = fitz.open(str(p))
            page_texts = [page.get_text() for page in doc]
            doc.close()
            return _pdf_result(p, page_texts, engine="pymupdf", label="PyMuPDF", ocr=ocr)
        except ImportError:
            pass

        # PyMuPDF is optional; pypdf is a lighter fallback commonly already
        # present in the local document stack.
        try:
            from pypdf import PdfReader

            reader = PdfReader(str(p))
            page_texts = [page.extract_text() or "" for page in reader.pages]
            return _pdf_result(p, page_texts, engine="pypdf", label="pypdf", ocr=ocr)
        except ImportError:
            pass
        except Exception as exc:
            return {
                "status": "error",
                "message": f"PDF extraction failed: {exc}",
                "content": "",
            }

    # Word documents via python-docx (lightweight)
    if p.suffix.lower() in {".docx", ".doc"}:
        try:
            import docx  # python-docx

            document = docx.Document(str(p))
            parts: list[str] = [para.text for para in document.paragraphs]
            table_count = len(document.tables)
            for table in document.tables:
                for row in table.rows:
                    parts.append(" | ".join(cell.text.strip() for cell in row.cells))
            content = "\n\n".join(part for part in parts if part.strip())
            return {
                "status":  "ok",
                "path":    str(p),
                "content": content,
                "tables":  table_count,
                "pages":   0,
                "engine":  "python-docx",
                "message": (
                    f"Extracted {len(content):,} characters from {p.name} via python-docx. "
                    f"{table_count} table(s) found."
                ),
            }
        except ImportError:
            pass
        except Exception as exc:
            return {
                "status":  "error",
                "message": f"python-docx extraction failed: {exc}",
                "content": "",
            }

    # Presentations via python-pptx.
    if p.suffix.lower() == ".pptx":
        try:
            from pptx import Presentation

            presentation = Presentation(str(p))
            parts: list[str] = []
            table_count = 0
            for slide_index, slide in enumerate(presentation.slides):
                parts.append(f"## Slide {slide_index + 1}")
                for shape in slide.shapes:
                    if getattr(shape, "has_text_frame", False):
                        text = str(getattr(shape, "text", "")).strip()
                        if text:
                            parts.append(text)
                    if getattr(shape, "has_table", False):
                        table_count += 1
                        for row in shape.table.rows:
                            parts.append(" | ".join(cell.text.strip() for cell in row.cells))
            content = "\n\n".join(parts)
            return {
                "status": "ok",
                "path": str(p),
                "content": content,
                "tables": table_count,
                "pages": len(presentation.slides),
                "engine": "python-pptx",
                "message": (
                    f"Extracted {len(content):,} characters from {p.name} via python-pptx. "
                    f"{table_count} table(s) found."
                ),
            }
        except ImportError:
            pass
        except Exception as exc:
            return {
                "status": "error",
                "message": f"python-pptx extraction failed: {exc}",
                "content": "",
            }

    # Spreadsheets via openpyxl, retaining sheet names and a compact row view.
    if p.suffix.lower() in {".xlsx", ".xlsm"}:
        try:
            import openpyxl

            workbook = openpyxl.load_workbook(str(p), read_only=True, data_only=True)
            parts: list[str] = []
            for sheet in workbook.worksheets:
                parts.append(f"## Sheet: {sheet.title}")
                for row in sheet.iter_rows(values_only=True):
                    values = ["" if value is None else str(value) for value in row]
                    if any(values):
                        parts.append(" | ".join(values))
            workbook.close()
            content = "\n".join(parts)
            return {
                "status": "ok",
                "path": str(p),
                "content": content,
                "tables": len(parts),
                "pages": 0,
                "engine": "openpyxl",
                "message": f"Extracted {len(content):,} characters from {p.name} via openpyxl.",
            }
        except ImportError:
            pass
        except Exception as exc:
            return {
                "status": "error",
                "message": f"Spreadsheet extraction failed: {exc}",
                "content": "",
            }

    # Last resort: plain text read
    if p.suffix.lower() in {
        ".txt", ".md", ".html", ".htm", ".rtf", ".csv", ".json",
        ".jsonl", ".yaml", ".yml", ".xml",
    }:
        try:
            content = p.read_text(encoding="utf-8", errors="replace")
            return {
                "status":  "ok",
                "path":    str(p),
                "content": content,
                "tables":  0,
                "pages":   0,
                "engine":  "plaintext_fallback",
                "message": f"Read {len(content):,} characters from {p.name} as plain text.",
            }
        except Exception as exc:
            return {
                "status":  "error",
                "message": f"Plain text read failed: {exc}",
                "content": "",
            }

    return {
        "status":  "error",
        "message": (
            f"No lightweight extraction engine is installed for {p.suffix}."
        ),
        "content": "",
    }


def _ocr_blocks(result: dict, pages: set[int] | None = None) -> dict[int, str]:
    """Page number → OCR text, one visual row per line, each fragment with its line id."""
    from ocr_skill import rows

    blocks: dict[int, str] = {}
    for page in result.get("pages", []):
        if page.get("source") != "ocr" or (pages is not None and page["page"] not in pages):
            continue
        blocks[page["page"]] = "\n".join(
            "  ".join(f"[{line['id']}] {line['text']}" for line in row)
            for row in rows(page.get("lines", []))
        )
    return blocks


def _ocr_summary(result: dict) -> dict:
    return {
        "engine": result.get("engine"),
        "pages": [
            {"page": page["page"], "mean_confidence": page.get("mean_confidence"), "hard_to_read": page.get("poor")}
            for page in result.get("pages", [])
            if page.get("source") == "ocr"
        ],
    }


def _extract_photo(p: Path, *, ocr: bool) -> dict:
    if not ocr:
        return {
            "status": "ok",
            "path": str(p),
            "content": "",
            "tables": 0,
            "pages": 1,
            "engine": "none",
            "message": f"{p.name} is a photo; extract_document reads it with local OCR.",
        }
    from ocr_skill import read_document

    result = read_document(p)
    if result.get("status") != "ok":
        return {"status": "error", "message": result.get("message", "Local OCR failed."), "content": ""}
    blocks = _ocr_blocks(result)
    content = "\n\n".join(f"## Page {number} (OCR)\n\n{text}" for number, text in blocks.items())
    return {
        "status": "ok",
        "path": str(p),
        "content": content,
        "tables": 0,
        "pages": len(result.get("pages", [])),
        "engine": f"ocr:{result.get('engine')}",
        "ocr": _ocr_summary(result),
        "message": f"Extracted {len(content):,} characters from {p.name} with local OCR. {result.get('message', '')}".strip(),
    }


def _pdf_result(p: Path, page_texts: list[str], *, engine: str, label: str, ocr: bool) -> dict:
    """PDF text per page; pages without a text layer are OCR'd when ``ocr``."""
    scanned = [
        index for index, text in enumerate(page_texts, start=1)
        if len("".join(text.split())) < _SCANNED_PAGE_CHARS
    ]
    blocks: dict[int, str] = {}
    note = ""
    ocr_summary = None
    if scanned and ocr:
        from ocr_skill import read_document

        result = read_document(p)
        blocks = _ocr_blocks(result, set(scanned))
        if blocks:
            engine, label = f"{engine}+ocr:{result.get('engine')}", f"{label} and local OCR"
            ocr_summary = _ocr_summary(result)
        unread = [number for number in scanned if number not in blocks]
        if unread:
            note = (
                f" Page(s) {', '.join(map(str, unread))} have no text layer and could not be read: "
                f"{result.get('message', '')}"
            )
    elif scanned:
        note = f" Page(s) {', '.join(map(str, scanned))} are scanned images; extract_document reads them with local OCR."
    content = "\n\n".join(
        f"## Page {index} (OCR)\n\n{blocks[index]}" if index in blocks else f"## Page {index}\n\n{text}"
        for index, text in enumerate(page_texts, start=1)
    )
    payload = {
        "status": "ok",
        "path": str(p),
        "content": content,
        "tables": 0,
        "pages": len(page_texts),
        "engine": engine,
        "message": f"Extracted {len(content):,} characters from {p.name} via {label}.{note}",
    }
    if ocr_summary:
        payload["ocr"] = ocr_summary
    return payload
