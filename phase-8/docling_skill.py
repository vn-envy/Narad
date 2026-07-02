"""
Matsya document extraction skill — IBM Docling.

Replaces raw PyMuPDF/text extraction with Docling, which correctly handles:
  - Multi-column PDF layouts
  - Tables (preserved as Markdown tables)
  - Figures and captions
  - Headers, footers, footnotes
  - Word (.docx), PowerPoint (.pptx), HTML, Markdown, and plain text

Falls back gracefully to basic text extraction if Docling is not installed.
"""
from __future__ import annotations

import os
from pathlib import Path


def extract_document(file_path: str) -> dict:
    """Extract text, tables, and structure from a document file.

    Supports PDF, DOCX, PPTX, HTML, Markdown, and plain text.
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
        ".html", ".htm", ".md", ".txt", ".rtf", ".odt",
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

    # Try Docling first (full fidelity)
    try:
        from docling.document_converter import DocumentConverter

        converter = DocumentConverter()
        doc_result = converter.convert(str(p))
        markdown = doc_result.document.export_to_markdown()

        # Count tables (markdown tables start with a line containing |)
        table_lines = [ln for ln in markdown.splitlines() if ln.strip().startswith("|")]
        table_count = sum(
            1 for i, ln in enumerate(markdown.splitlines())
            if ln.strip().startswith("|") and (
                i == 0 or not markdown.splitlines()[i - 1].strip().startswith("|")
            )
        )

        return {
            "status":  "ok",
            "path":    str(p),
            "content": markdown,
            "tables":  table_count,
            "pages":   0,
            "engine":  "docling",
            "message": (
                f"Extracted {len(markdown):,} characters from {p.name}. "
                f"{table_count} table(s) found."
            ),
        }

    except ImportError:
        pass  # fall through to basic extraction

    except Exception as exc:
        return {
            "status":  "error",
            "message": f"Docling extraction failed: {exc}",
            "content": "",
        }

    # Fallback: basic PDF extraction via PyMuPDF, plain read for text files
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
                "engine":  "pymupdf_fallback",
                "message": (
                    f"Extracted {len(content):,} characters from {p.name} via PyMuPDF. "
                    "Install docling for better table and layout support."
                ),
            }
        except ImportError:
            pass

    # Last resort: plain text read
    if p.suffix.lower() in {".txt", ".md", ".html", ".htm", ".rtf"}:
        try:
            content = p.read_text(encoding="utf-8", errors="replace")
            return {
                "status":  "ok",
                "path":    str(p),
                "content": content,
                "tables":  0,
                "pages":   0,
                "engine":  "plaintext_fallback",
                "message": (
                    f"Read {len(content):,} characters from {p.name} as plain text. "
                    "Install docling for richer extraction."
                ),
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
            "No extraction engine available. "
            "Install docling: pip install docling"
        ),
        "content": "",
    }
