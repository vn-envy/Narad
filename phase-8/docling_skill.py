"""
Matsya document extraction skill.

Uses lightweight, format-specific readers and never loads model runtimes.
"""
from __future__ import annotations

from pathlib import Path


def extract_document(file_path: str) -> dict:
    """Extract text, tables, and structure from a document file.

    Supports PDF, DOCX, PPTX, XLSX, CSV, HTML, Markdown, and plain text.
    Tables are preserved as Markdown tables. Multi-column layouts are
    linearised in reading order.

    Args:
        file_path: Absolute or home-relative path to the document.
                   e.g. "/Users/me/Downloads/report.pdf" or "~/Desktop/plan.docx"

    Returns a dict with:
        status:   "ok" | "error"
        content:  Full extracted content as Markdown string
        tables:   Number of tables found
        pages:    Number of pages (PDFs only, 0 for other formats)
        message:  Summary or error description
    """
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
    }
    if p.suffix.lower() not in supported:
        return {
            "status":  "error",
            "message": (
                f"Unsupported file type: {p.suffix}. "
                f"Supported: {', '.join(sorted(supported))}"
            ),
            "content": "",
        }

    # Default: PDF extraction via PyMuPDF
    if p.suffix.lower() == ".pdf":
        try:
            import fitz  # PyMuPDF

            doc = fitz.open(str(p))
            pages_text = []
            for i, page in enumerate(doc):
                pages_text.append(f"## Page {i + 1}\n\n{page.get_text()}")
            content = "\n\n".join(pages_text)
            doc.close()
            return {
                "status":  "ok",
                "path":    str(p),
                "content": content,
                "tables":  0,
                "pages":   len(pages_text),
                "engine":  "pymupdf",
                "message": f"Extracted {len(content):,} characters from {p.name} via PyMuPDF.",
            }
        except ImportError:
            pass

        # PyMuPDF is optional; pypdf is a lighter fallback commonly already
        # present in the local document stack.
        try:
            from pypdf import PdfReader

            reader = PdfReader(str(p))
            pages_text = [
                f"## Page {index + 1}\n\n{page.extract_text() or ''}"
                for index, page in enumerate(reader.pages)
            ]
            content = "\n\n".join(pages_text)
            return {
                "status": "ok",
                "path": str(p),
                "content": content,
                "tables": 0,
                "pages": len(pages_text),
                "engine": "pypdf",
                "message": f"Extracted {len(content):,} characters from {p.name} via pypdf.",
            }
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
