"""
Local OCR for photographed and scanned family documents.

Turns a photo, scan or PDF into pages of lines, each with its text, the
engine's confidence and a bounding box normalised to the page (0-1), plus the
page image it was read from. Line ids ("p1-l7") let a value the worker model
extracts be traced back to the exact crop a person confirms. Nothing here sends
an image anywhere: both engines run on this Mac.

Engines (NARAD_OCR_ENGINE, default "auto" = the first one installed):
  paddle  PaddleOCR PP-OCRv5 on CPU: mobile detection plus the Devanagari
          recogniser, which also reads English. Code and weights Apache-2.0.
  surya   Surya 2 (a 650M document VLM served by llama.cpp on Apple Silicon).
          Code Apache-2.0; weights under a modified OpenRAIL-M licence (free for
          personal use and organisations under $5M revenue or funding).
Install with `pip install -e ".[ocr]"` (PaddleOCR and pillow-heif). A model
loads on first use and unloads after NARAD_OCR_IDLE_UNLOAD_S (default 120 s)
idle; only one engine is resident at a time.

Inputs: jpg, png, webp, heic/heif (with pillow-heif), phone photos turned
upright from their EXIF orientation, and PDFs. A PDF page with a text layer is
read from it; only pages without one are OCR'd (PyMuPDF renders them). Results
are cached per profile by file hash, so reading a document twice costs nothing.
"""
from __future__ import annotations

import gc
import hashlib
import html
import importlib.util
import json
import logging
import os
import re
import shutil
import statistics
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Protocol

log = logging.getLogger("narad.ocr")

IMAGE_SUFFIXES = frozenset({".jpg", ".jpeg", ".png", ".webp", ".heic", ".heif"})
HEIC_SUFFIXES = frozenset({".heic", ".heif"})
INSTALL_HINT = 'Local OCR is not installed on this Mac. Install it with: pip install -e ".[ocr]"'

MAX_OCR_SIDE = int(os.environ.get("NARAD_OCR_MAX_SIDE", "2400"))
MAX_PAGES = int(os.environ.get("NARAD_OCR_MAX_PAGES", "12"))
PDF_RENDER_DPI = int(os.environ.get("NARAD_OCR_PDF_DPI", "200"))
IDLE_UNLOAD_S = float(os.environ.get("NARAD_OCR_IDLE_UNLOAD_S", "120"))
# A PDF page with fewer visible characters than this is treated as a scan.
TEXT_LAYER_MIN_CHARS = 25
# Below this a line is "hard to read"; a page is poor when its mean is below
# POOR_PAGE_MEAN or more than POOR_PAGE_SHARE of its lines are hard to read.
LOW_LINE_CONFIDENCE = 0.75
POOR_PAGE_MEAN = 0.82
POOR_PAGE_SHARE = 0.25
_CACHE_KEEP = 40


class OcrUnavailable(RuntimeError):
    """No local OCR engine (or HEIC/PDF reader) is installed for this input."""


# ── Engines ───────────────────────────────────────────────────────────────────

@dataclass
class EnginePage:
    """What an engine read: (text, confidence 0-1, pixel box x0,y0,x1,y1) per line.

    ``image`` is set when the engine turned the page upright itself; the boxes
    are then in that image's coordinates.
    """

    lines: list[tuple[str, float, tuple[float, float, float, float]]] = field(default_factory=list)
    image: Any = None


class OcrEngine(Protocol):
    name: str

    def read(self, image: Any) -> EnginePage: ...


class _PaddleEngine:
    """PaddleOCR 3.x, PP-OCRv5 mobile detection + Devanagari recognition, CPU."""

    name = "paddle"

    def __init__(self) -> None:
        # Skip PaddleX's start-up probe of model hosts; weights download once on first use.
        os.environ.setdefault("PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK", "True")
        from paddleocr import PaddleOCR

        self._ocr = PaddleOCR(
            text_detection_model_name=os.environ.get("NARAD_OCR_PADDLE_DET", "PP-OCRv5_mobile_det"),
            text_recognition_model_name=os.environ.get(
                "NARAD_OCR_PADDLE_REC", "devanagari_PP-OCRv5_mobile_rec"
            ),
            use_doc_orientation_classify=True,  # sideways photos without EXIF
            use_doc_unwarping=False,
            use_textline_orientation=False,
        )

    def read(self, image: Any) -> EnginePage:
        import numpy as np
        from PIL import Image

        # PaddleX treats arrays as BGR, as if it had read the file itself.
        results = list(self._ocr.predict(np.asarray(image)[:, :, ::-1]))
        if not results:
            return EnginePage()
        res = results[0]
        # Scores and boxes are numpy arrays: never test them for truth.
        texts = _as_list(res.get("rec_texts"))
        scores = _as_list(res.get("rec_scores"))
        boxes = res.get("rec_boxes")
        if boxes is None or len(boxes) != len(texts):
            boxes = [_poly_box(poly) for poly in _as_list(res.get("rec_polys"))]
        lines = [
            (str(text), float(score), tuple(float(v) for v in box[:4]))
            for text, score, box in zip(texts, scores, boxes)
        ]
        page = EnginePage(lines=lines)
        pre = res.get("doc_preprocessor_res") or {}
        if int(pre.get("angle", -1) or -1) not in (-1, 0) and pre.get("output_img") is not None:
            page.image = Image.fromarray(np.ascontiguousarray(pre["output_img"][:, :, ::-1]))
        return page


class _SuryaEngine:
    """Surya 2 full-page OCR. It returns layout blocks, not lines, so each block's
    text is split into lines and the block's box is shared out between them."""

    name = "surya"

    def __init__(self) -> None:
        from surya.inference import SuryaInferenceManager
        from surya.recognition import RecognitionPredictor

        self._manager = SuryaInferenceManager()
        self._predictor = RecognitionPredictor(self._manager)

    def read(self, image: Any) -> EnginePage:
        predictions = self._predictor([image])
        page = EnginePage()
        for block in getattr(predictions[0], "blocks", []) if predictions else []:
            if getattr(block, "skipped", False) or getattr(block, "error", False):
                continue
            texts = _html_lines(str(getattr(block, "html", "") or ""))
            if not texts:
                continue
            x0, y0, x1, y1 = (float(v) for v in block.bbox)
            step = (y1 - y0) / len(texts)
            confidence = float(getattr(block, "confidence", 0.0) or 0.0)
            for index, text in enumerate(texts):
                page.lines.append((text, confidence, (x0, y0 + index * step, x1, y0 + (index + 1) * step)))
        return page

    def close(self) -> None:
        for name in ("shutdown", "close", "stop"):
            stop = getattr(self._manager, name, None)
            if callable(stop):
                stop()
                return


def _as_list(value: Any) -> list[Any]:
    return [] if value is None else list(value)


def _poly_box(poly: Any) -> tuple[float, float, float, float]:
    xs = [float(point[0]) for point in poly]
    ys = [float(point[1]) for point in poly]
    return (min(xs), min(ys), max(xs), max(ys))


_BLOCK_BREAK_RE = re.compile(r"<br\s*/?>|</(?:p|tr|li|h[1-6]|div|caption)>", re.IGNORECASE)
_CELL_BREAK_RE = re.compile(r"</t[dh]>", re.IGNORECASE)
_TAG_RE = re.compile(r"<[^>]+>")


def _html_lines(markup: str) -> list[str]:
    text = _CELL_BREAK_RE.sub("  ", _BLOCK_BREAK_RE.sub("\n", markup))
    lines = [re.sub(r"\s+", " ", html.unescape(_TAG_RE.sub(" ", line))).strip() for line in text.split("\n")]
    return [line for line in lines if line]


_FACTORIES: dict[str, Callable[[], OcrEngine]] = {"paddle": _PaddleEngine, "surya": _SuryaEngine}
_MODULES = {"paddle": "paddleocr", "surya": "surya"}


def register_engine(name: str, factory: Callable[[], OcrEngine] | None) -> None:
    """Add (or with None remove) an engine; tests use this for a stub engine."""
    _slot.unload()
    if factory is None:
        _FACTORIES.pop(name, None)
    else:
        _FACTORIES[name] = factory


def _importable(module: str) -> bool:
    try:
        return importlib.util.find_spec(module) is not None
    except (ImportError, ValueError):
        return False


def installed_engines() -> list[str]:
    return [name for name in _FACTORIES if name not in _MODULES or _importable(_MODULES[name])]


def engine_name() -> str:
    """The engine NARAD_OCR_ENGINE selects, or "" when none is installed."""
    wanted = os.environ.get("NARAD_OCR_ENGINE", "auto").strip().lower() or "auto"
    installed = installed_engines()
    if wanted != "auto":
        return wanted if wanted in installed else ""
    for name in ("paddle", "surya"):
        if name in installed:
            return name
    return next((name for name in installed if name not in _MODULES), "")


class _EngineSlot:
    """One OCR engine in memory at a time, loaded on demand, unloaded when idle."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._engine: OcrEngine | None = None
        self._name = ""
        self._used = 0.0
        self._timer: threading.Timer | None = None

    def read(self, name: str, image: Any) -> EnginePage:
        """Run one page through ``name``, loading it first if needed. One page at
        a time: the engines are not thread-safe and the Mac is fanless."""
        with self._lock:
            if self._engine is None or self._name != name:
                self.unload()
                started = time.perf_counter()
                self._engine = _FACTORIES[name]()
                self._name = name
                log.info("OCR engine %s loaded in %.1f s", name, time.perf_counter() - started)
            try:
                return self._engine.read(image)
            finally:
                self._used = time.monotonic()
                if self._timer is None and IDLE_UNLOAD_S > 0:
                    self._schedule(IDLE_UNLOAD_S)

    def loaded(self) -> str:
        return self._name if self._engine is not None else ""

    def _schedule(self, delay: float) -> None:
        timer = threading.Timer(delay, self.unload_idle)
        timer.daemon = True
        self._timer = timer
        timer.start()

    def unload_idle(self, now: float | None = None) -> bool:
        now = time.monotonic() if now is None else now
        with self._lock:
            self._timer = None
            if self._engine is None:
                return False
            idle = now - self._used
            if idle < IDLE_UNLOAD_S:
                self._schedule(max(1.0, IDLE_UNLOAD_S - idle))
                return False
            self.unload()
            return True

    def unload(self) -> None:
        with self._lock:
            engine, self._engine, self._name = self._engine, None, ""
            if self._timer is not None:
                self._timer.cancel()
                self._timer = None
        if engine is None:
            return
        close = getattr(engine, "close", None)
        if callable(close):
            try:
                close()
            except Exception as exc:
                log.warning("OCR engine close failed: %s", exc)
        del engine
        gc.collect()
        log.info("OCR engine unloaded")


_slot = _EngineSlot()


def unload() -> None:
    _slot.unload()


def status() -> dict[str, Any]:
    return {
        "engine": engine_name(),
        "requested": os.environ.get("NARAD_OCR_ENGINE", "auto"),
        "installed": installed_engines(),
        "loaded": _slot.loaded(),
        "heic": _importable("pillow_heif"),
        "pdf_render": _importable("fitz"),
        "idle_unload_s": IDLE_UNLOAD_S,
    }


# ── Images and PDF pages ──────────────────────────────────────────────────────

def load_image(path: str | Path) -> Any:
    """An upright RGB PIL image: HEIC decoded, EXIF orientation applied, bounded size."""
    from PIL import Image, ImageOps

    p = Path(path)
    if p.suffix.lower() in HEIC_SUFFIXES:
        try:
            from pillow_heif import register_heif_opener
        except ImportError:
            raise OcrUnavailable(
                "iPhone HEIC photos need pillow-heif on this Mac "
                '(pip install -e ".[ocr]"). Sharing the photo as JPEG also works.'
            ) from None
        register_heif_opener()
    with Image.open(p) as opened:
        image = ImageOps.exif_transpose(opened)
        image = image.convert("RGB")
    return _bounded(image)


def _bounded(image: Any) -> Any:
    if max(image.size) > MAX_OCR_SIDE:
        image = image.copy()
        image.thumbnail((MAX_OCR_SIDE, MAX_OCR_SIDE))
    return image


@dataclass
class _PageSource:
    number: int
    image: Any
    lines: list[dict[str, Any]] | None = None  # text-layer lines; None means OCR the image


def _pdf_pages(path: Path, *, force_ocr: bool, limit: int) -> tuple[list[_PageSource], int]:
    try:
        import fitz  # PyMuPDF
    except ImportError:
        raise OcrUnavailable(
            'Reading scanned PDFs needs PyMuPDF on this Mac (pip install -e ".[documents]").'
        ) from None
    from PIL import Image

    pages: list[_PageSource] = []
    with fitz.open(str(path)) as doc:
        total = doc.page_count
        for index in range(min(total, limit)):
            page = doc[index]
            pix = page.get_pixmap(dpi=PDF_RENDER_DPI)
            image = _bounded(Image.frombytes("RGB", (pix.width, pix.height), pix.samples))
            lines = None if force_ocr else _text_layer_lines(page)
            pages.append(_PageSource(index + 1, image, lines))
    return pages, total


def _text_layer_lines(page: Any) -> list[dict[str, Any]] | None:
    rect = page.rect
    width, height = float(rect.width) or 1.0, float(rect.height) or 1.0
    rotate = page.rotation_matrix if page.rotation else None
    lines: list[dict[str, Any]] = []
    for block in page.get_text("dict").get("blocks", []):
        if block.get("type") != 0:
            continue
        for line in block.get("lines", []):
            text = "".join(span.get("text", "") for span in line.get("spans", [])).strip()
            if not text:
                continue
            x0, y0, x1, y1 = line["bbox"]
            if rotate is not None:
                import fitz

                box = fitz.Rect(x0, y0, x1, y1) * rotate
                x0, y0, x1, y1 = box.x0, box.y0, box.x1, box.y1
            lines.append({
                "text": text,
                "confidence": 1.0,
                "bbox": _normalise((x0, y0, x1, y1), width, height),
            })
    if sum(len(line["text"].replace(" ", "")) for line in lines) < TEXT_LAYER_MIN_CHARS:
        return None
    return lines


def _normalise(box: tuple[float, float, float, float], width: float, height: float) -> list[float]:
    x0, y0, x1, y1 = box
    return [
        round(min(max(x0 / width, 0.0), 1.0), 4),
        round(min(max(y0 / height, 0.0), 1.0), 4),
        round(min(max(x1 / width, 0.0), 1.0), 4),
        round(min(max(y1 / height, 0.0), 1.0), 4),
    ]


def _reading_order(lines: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Top-to-bottom rows, left-to-right within a row (engines return detection order)."""
    if not lines:
        return lines
    heights = [max(line["bbox"][3] - line["bbox"][1], 1e-4) for line in lines]
    band = max(statistics.median(heights) * 0.6, 1e-4)
    return sorted(lines, key=lambda line: (
        round(((line["bbox"][1] + line["bbox"][3]) / 2) / band), line["bbox"][0],
    ))


def _ocr_page(engine: str, image: Any) -> tuple[list[dict[str, Any]], Any]:
    read = _slot.read(engine, image)
    page_image = read.image if read.image is not None else image
    width, height = page_image.size
    lines = []
    for text, confidence, box in read.lines:
        clean = re.sub(r"\s+", " ", str(text or "")).strip()
        if not clean:
            continue
        lines.append({
            "text": clean,
            "confidence": round(min(max(float(confidence), 0.0), 1.0), 4),
            "bbox": _normalise(box, width, height),
        })
    return _reading_order(lines), page_image


# ── Cache ─────────────────────────────────────────────────────────────────────

def _cache_root() -> Path:
    from profile_context import profile_root

    return profile_root() / "documents" / "ocr"


def file_sha256(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _prune_cache(root: Path) -> None:
    entries = sorted((p for p in root.iterdir() if p.is_dir()), key=lambda p: p.stat().st_mtime)
    for stale in entries[:-_CACHE_KEEP]:
        shutil.rmtree(stale, ignore_errors=True)


# ── Public ────────────────────────────────────────────────────────────────────

def is_image(path: str | Path) -> bool:
    return Path(path).suffix.lower() in IMAGE_SUFFIXES


def read_document(path: str | Path, *, force_ocr: bool = False, use_cache: bool = True) -> dict[str, Any]:
    """Pages → lines (id, text, confidence, normalised bbox) plus page images.

    Returns status "ok"; "partial" when some scanned PDF pages could not be
    read because no engine is installed; "error" with a message otherwise.
    Page images are JPEGs under the profile's OCR cache (never served directly).
    """
    p = Path(path).expanduser().resolve()
    if not p.is_file():
        return {"status": "error", "message": f"File not found: {p}", "pages": []}
    suffix = p.suffix.lower()
    if suffix not in IMAGE_SUFFIXES and suffix != ".pdf":
        return {"status": "error", "message": f"OCR reads photos and PDFs, not {suffix or 'this file'}.", "pages": []}

    sha = file_sha256(p)
    engine = engine_name()
    key = f"{sha[:24]}-{engine or 'none'}{'-ocr' if force_ocr else ''}"
    root = _cache_root()
    cache_dir = root / key
    cached = cache_dir / "result.json"
    if use_cache and cached.is_file():
        try:
            result = json.loads(cached.read_text(encoding="utf-8"))
            if all(Path(page["image"]).is_file() for page in result.get("pages", [])):
                os.utime(cache_dir)
                result["path"] = str(p)
                result["cached"] = True
                return result
        except (OSError, ValueError, KeyError, TypeError):
            pass

    started = time.perf_counter()
    try:
        if suffix == ".pdf":
            sources, total_pages = _pdf_pages(p, force_ocr=force_ocr, limit=MAX_PAGES)
        else:
            sources, total_pages = [_PageSource(1, load_image(p))], 1
    except OcrUnavailable as exc:
        return {"status": "error", "message": str(exc), "pages": [], "unavailable": True}
    except Exception as exc:
        return {"status": "error", "message": f"Could not open {p.name}: {exc}", "pages": []}

    needs_ocr = [source for source in sources if source.lines is None]
    if needs_ocr and not engine:
        if suffix != ".pdf" or len(needs_ocr) == len(sources):
            return {"status": "error", "message": INSTALL_HINT, "pages": [], "unavailable": True}

    tmp_dir = root / f".{key}.{os.getpid()}.{threading.get_ident()}.tmp"
    shutil.rmtree(tmp_dir, ignore_errors=True)
    tmp_dir.mkdir(parents=True, exist_ok=True)
    pages: list[dict[str, Any]] = []
    missing: list[int] = []
    timings: list[int] = []
    try:
        for source in sources:
            page_started = time.perf_counter()
            image = source.image
            if source.lines is not None:
                lines, origin = source.lines, "text_layer"
            elif engine:
                lines, image = _ocr_page(engine, image)
                origin = "ocr"
            else:
                lines, origin = [], "unread"
                missing.append(source.number)
            timings.append(int((time.perf_counter() - page_started) * 1000))
            for index, line in enumerate(lines, start=1):
                line["id"] = f"p{source.number}-l{index}"
            image_path = tmp_dir / f"page-{source.number}.jpg"
            image.save(image_path, "JPEG", quality=85)
            pages.append({
                "page": source.number,
                "width": image.size[0],
                "height": image.size[1],
                "source": origin,
                "image": image_path.name,
                "lines": lines,
                **page_quality(lines),
            })
    except Exception as exc:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        log.warning("OCR failed for %s: %s", p.name, exc)
        return {"status": "error", "message": f"Local OCR failed on {p.name}: {exc}", "pages": []}

    result: dict[str, Any] = {
        "status": "partial" if missing else "ok",
        "engine": engine if any(page["source"] == "ocr" for page in pages) else "text_layer",
        "name": p.name,
        "sha256": sha,
        "total_pages": total_pages,
        "truncated": total_pages > len(sources),
        "missing_ocr_pages": missing,
        "timings_ms": {"total": int((time.perf_counter() - started) * 1000), "pages": timings},
        "pages": pages,
    }
    result["message"] = _summary(result)
    shutil.rmtree(cache_dir, ignore_errors=True)
    tmp_dir.replace(cache_dir)
    for page in pages:
        page["image"] = str(cache_dir / page["image"])
    if not missing:
        (cache_dir / "result.json").write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
    try:
        _prune_cache(root)
    except OSError:
        pass
    result["path"] = str(p)
    return result


def page_quality(lines: list[dict[str, Any]]) -> dict[str, Any]:
    """Mean line confidence, the share of hard-to-read lines, and whether the page is poor."""
    if not lines:
        return {"mean_confidence": 0.0, "low_share": 0.0, "poor": False}
    scores = [float(line.get("confidence", 0.0)) for line in lines]
    mean = sum(scores) / len(scores)
    low = sum(score < LOW_LINE_CONFIDENCE for score in scores) / len(scores)
    return {
        "mean_confidence": round(mean, 3),
        "low_share": round(low, 3),
        "poor": mean < POOR_PAGE_MEAN or low > POOR_PAGE_SHARE,
    }


def _summary(result: dict[str, Any]) -> str:
    pages = result["pages"]
    ocr_pages = [page["page"] for page in pages if page["source"] == "ocr"]
    parts = [f"Read {len(pages)} page(s) of {result['name']}"]
    if ocr_pages:
        parts.append(f"OCR ({result['engine']}) on page(s) {', '.join(map(str, ocr_pages))}")
    if result["missing_ocr_pages"]:
        pages_text = ", ".join(map(str, result["missing_ocr_pages"]))
        parts.append(f"page(s) {pages_text} are scanned and could not be read: {INSTALL_HINT}")
    if result["truncated"]:
        parts.append(f"only the first {len(pages)} of {result['total_pages']} pages were read")
    poor = [page["page"] for page in pages if page.get("poor")]
    if poor:
        parts.append(f"page(s) {', '.join(map(str, poor))} are hard to read")
    return "; ".join(parts) + "."


def line_index(result: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Line id → the line plus its page number."""
    return {
        line["id"]: {**line, "page": page["page"]}
        for page in result.get("pages", [])
        for line in page.get("lines", [])
    }


def rows(lines: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    """Group a page's lines into visual rows (a table row's cells share one row)."""
    grouped: list[list[dict[str, Any]]] = []
    for line in lines:
        y0, y1 = line["bbox"][1], line["bbox"][3]
        if grouped:
            last = grouped[-1]
            top = min(item["bbox"][1] for item in last)
            bottom = max(item["bbox"][3] for item in last)
            overlap = min(bottom, y1) - max(top, y0)
            if overlap > 0.5 * min(bottom - top, y1 - y0):
                last.append(line)
                continue
        grouped.append([line])
    return [sorted(row, key=lambda item: item["bbox"][0]) for row in grouped]


def format_lines(result: dict[str, Any], *, max_chars: int = 60_000) -> str:
    """Model-facing text: one visual row per line, each fragment prefixed by its id."""
    out: list[str] = []
    for page in result.get("pages", []):
        origin = {"ocr": "OCR", "text_layer": "text layer"}.get(page["source"], "not read")
        out.append(f"## Page {page['page']} ({origin})")
        for row in rows(page.get("lines", [])):
            out.append(" | ".join(f"[{line['id']}] {line['text']}" for line in row))
    text = "\n".join(out)
    return text if len(text) <= max_chars else text[:max_chars] + "\n[remaining lines omitted]"
