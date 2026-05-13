"""
Vamana local filesystem skill — safe, dry-run-first file operations.

All mutating tools (move_to_trash, organize_by_type) default to dry_run=True.
Vamana MUST present the preview to the user and wait for explicit confirmation
("yes", "do it", "go ahead") before calling with dry_run=False.

Files are NEVER permanently deleted — always sent to macOS/OS Trash via send2trash.
"""
from __future__ import annotations

import os
import shutil
import time
from datetime import datetime
from pathlib import Path
from typing import Any

_BLOCKED_PATHS = {
    "/System", "/Library", "/usr", "/bin", "/sbin",
    "/etc", "/var", "/private", "/Applications", "/Network",
}

_TYPE_MAP: dict[str, str] = {
    # Images
    ".jpg": "Images", ".jpeg": "Images", ".png": "Images", ".gif": "Images",
    ".bmp": "Images", ".tiff": "Images", ".tif": "Images", ".webp": "Images",
    ".heic": "Images", ".heif": "Images", ".svg": "Images", ".raw": "Images",
    # Videos
    ".mp4": "Videos", ".mov": "Videos", ".avi": "Videos", ".mkv": "Videos",
    ".wmv": "Videos", ".flv": "Videos", ".webm": "Videos", ".m4v": "Videos",
    # Audio
    ".mp3": "Audio", ".wav": "Audio", ".aac": "Audio", ".flac": "Audio",
    ".ogg": "Audio", ".m4a": "Audio", ".wma": "Audio",
    # Documents
    ".pdf": "Documents", ".doc": "Documents", ".docx": "Documents",
    ".xls": "Documents", ".xlsx": "Documents", ".ppt": "Documents",
    ".pptx": "Documents", ".pages": "Documents", ".numbers": "Documents",
    ".key": "Documents", ".txt": "Documents", ".rtf": "Documents",
    ".odt": "Documents", ".ods": "Documents", ".odp": "Documents",
    # Code
    ".py": "Code", ".js": "Code", ".ts": "Code", ".jsx": "Code", ".tsx": "Code",
    ".html": "Code", ".css": "Code", ".json": "Code", ".xml": "Code",
    ".yaml": "Code", ".yml": "Code", ".sh": "Code", ".rb": "Code",
    ".go": "Code", ".rs": "Code", ".cpp": "Code", ".c": "Code", ".h": "Code",
    # Archives
    ".zip": "Archives", ".tar": "Archives", ".gz": "Archives", ".bz2": "Archives",
    ".7z": "Archives", ".rar": "Archives", ".dmg": "Archives", ".pkg": "Archives",
}


def _resolve(path: str) -> Path:
    return Path(path).expanduser().resolve()


def _check_blocked(p: Path) -> str | None:
    ps = str(p)
    for blocked in _BLOCKED_PATHS:
        if ps == blocked or ps.startswith(blocked + "/"):
            return f"Blocked: '{ps}' is a protected system path. Vamana cannot operate on system directories."
    return None


def _fmt_size(size_bytes: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if size_bytes < 1024:
            return f"{size_bytes:.1f} {unit}"
        size_bytes //= 1024
    return f"{size_bytes:.1f} TB"


def _file_info(p: Path) -> dict[str, Any]:
    stat = p.stat()
    age_days = (time.time() - stat.st_mtime) / 86400
    return {
        "name":       p.name,
        "path":       str(p),
        "size_bytes": stat.st_size,
        "size":       _fmt_size(stat.st_size),
        "type":       "directory" if p.is_dir() else p.suffix.lstrip(".") or "file",
        "modified":   datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d"),
        "age_days":   round(age_days, 1),
    }


def scan_directory(path: str, include_hidden: bool = False) -> dict:
    """List files and folders in a directory with size, type, and age.

    This is a read-only operation — always safe to call without confirmation.

    Returns a summary with total count, total size, and per-file details so
    Vamana can decide what to propose cleaning up or organising.
    """
    p = _resolve(path)
    blocked = _check_blocked(p)
    if blocked:
        return {"status": "error", "message": blocked}
    if not p.exists():
        return {"status": "error", "message": f"Path does not exist: {p}"}
    if not p.is_dir():
        return {"status": "error", "message": f"Not a directory: {p}"}

    items = []
    total_size = 0
    try:
        children = sorted(p.iterdir())
    except PermissionError:
        return {
            "status":  "error",
            "message": (
                f"Permission denied: '{p}'. "
                "macOS restricts access to protected folders (Documents, Desktop, Downloads) "
                "unless the terminal has Full Disk Access in System Settings → Privacy & Security."
            ),
        }
    for child in children:
        if not include_hidden and child.name.startswith("."):
            continue
        try:
            info = _file_info(child)
            items.append(info)
            if child.is_file():
                total_size += info["size_bytes"]
        except PermissionError:
            items.append({"name": child.name, "path": str(child), "error": "permission denied"})

    return {
        "status":     "ok",
        "path":       str(p),
        "item_count": len(items),
        "total_size": _fmt_size(total_size),
        "items":      items,
    }


def move_to_trash(paths: list, dry_run: bool = True) -> dict:
    """Move files or folders to the system Trash (recoverable).

    SAFETY CONTRACT:
    - dry_run=True (default): describes what WOULD be moved. Nothing changes.
    - dry_run=False: actually moves files. ONLY call after user explicitly confirms.

    Uses send2trash — files remain in Trash and can be recovered via Finder.
    Will REFUSE any path under protected system directories.
    """
    try:
        import send2trash
    except ImportError:
        return {
            "status":  "error",
            "message": "send2trash not installed. Run: pip install send2trash",
        }

    resolved: list[dict] = []
    errors: list[str] = []
    total_size = 0

    for raw in paths:
        p = _resolve(raw)
        blocked = _check_blocked(p)
        if blocked:
            errors.append(blocked)
            continue
        if not p.exists():
            errors.append(f"Does not exist: {p}")
            continue
        try:
            if p.is_dir():
                size = sum(f.stat().st_size for f in p.rglob("*") if f.is_file())
            else:
                size = p.stat().st_size
            total_size += size
            resolved.append({"path": str(p), "name": p.name, "size": _fmt_size(size)})
        except PermissionError:
            errors.append(f"Permission denied: {p}")

    preview = {
        "action":      "move_to_trash",
        "dry_run":     dry_run,
        "file_count":  len(resolved),
        "total_size":  _fmt_size(total_size),
        "files":       resolved,
        "errors":      errors,
        "recoverable": True,
        "note":        "Files will be moved to Trash — fully recoverable via Finder.",
    }

    if dry_run:
        preview["message"] = (
            f"DRY RUN — nothing moved yet. "
            f"Would move {len(resolved)} item(s) ({_fmt_size(total_size)}) to Trash. "
            "Call again with dry_run=False after user confirms."
        )
        return {"status": "ok", **preview}

    moved = []
    failed = []
    for item in resolved:
        try:
            send2trash.send2trash(item["path"])
            moved.append(item)
        except Exception as exc:
            failed.append({"path": item["path"], "error": str(exc)})

    return {
        "status":     "ok" if not failed else "partial",
        "action":     "move_to_trash",
        "dry_run":    False,
        "moved":      moved,
        "failed":     failed,
        "total_size": _fmt_size(total_size),
        "recoverable": True,
        "message":    f"Moved {len(moved)} item(s) to Trash. All recoverable from Finder.",
    }


def organize_by_type(source_dir: str, dry_run: bool = True) -> dict:
    """Sort loose files in a directory into type-based subdirectories.

    Creates: Images/, Videos/, Audio/, Documents/, Code/, Archives/, Other/
    Subdirectories and hidden files are left untouched.

    SAFETY CONTRACT:
    - dry_run=True (default): returns proposed move list. Nothing changes.
    - dry_run=False: actually moves files. ONLY call after user explicitly confirms.
    """
    p = _resolve(source_dir)
    blocked = _check_blocked(p)
    if blocked:
        return {"status": "error", "message": blocked}
    if not p.exists() or not p.is_dir():
        return {"status": "error", "message": f"Not a valid directory: {p}"}

    plan: list[dict] = []
    for child in sorted(p.iterdir()):
        if child.name.startswith(".") or child.is_dir():
            continue
        folder = _TYPE_MAP.get(child.suffix.lower(), "Other")
        dest = p / folder / child.name
        plan.append({
            "file":        child.name,
            "from":        str(child),
            "to":          str(dest),
            "destination": folder,
            "size":        _fmt_size(child.stat().st_size),
        })

    by_folder: dict[str, int] = {}
    for item in plan:
        by_folder[item["destination"]] = by_folder.get(item["destination"], 0) + 1

    if dry_run:
        return {
            "status":   "ok",
            "dry_run":  True,
            "source":   str(p),
            "plan":     plan,
            "summary":  by_folder,
            "message":  (
                f"DRY RUN — nothing moved yet. Would sort {len(plan)} file(s) into "
                f"{len(by_folder)} folder(s): {by_folder}. "
                "Call again with dry_run=False after user confirms."
            ),
        }

    moved = []
    failed = []
    for item in plan:
        try:
            dest_dir = Path(item["to"]).parent
            dest_dir.mkdir(parents=True, exist_ok=True)
            shutil.move(item["from"], item["to"])
            moved.append(item)
        except Exception as exc:
            failed.append({"file": item["file"], "error": str(exc)})

    return {
        "status":  "ok" if not failed else "partial",
        "dry_run": False,
        "moved":   moved,
        "failed":  failed,
        "summary": by_folder,
        "message": f"Organised {len(moved)} file(s) into type folders under {p}.",
    }


def find_large_files(path: str, min_size_mb: float = 100) -> dict:
    """Find files larger than min_size_mb in a directory tree. Read-only.

    Useful for identifying what's consuming disk space before cleaning up.
    Safe to call without confirmation — no filesystem changes.
    """
    p = _resolve(path)
    blocked = _check_blocked(p)
    if blocked:
        return {"status": "error", "message": blocked}
    if not p.exists():
        return {"status": "error", "message": f"Path does not exist: {p}"}

    min_bytes = int(min_size_mb * 1024 * 1024)
    large: list[dict] = []

    for f in p.rglob("*"):
        if not f.is_file():
            continue
        try:
            size = f.stat().st_size
            if size >= min_bytes:
                large.append({
                    "path":       str(f),
                    "name":       f.name,
                    "size_bytes": size,
                    "size":       _fmt_size(size),
                    "modified":   datetime.fromtimestamp(f.stat().st_mtime).strftime("%Y-%m-%d"),
                })
        except PermissionError:
            continue

    large.sort(key=lambda x: x["size_bytes"], reverse=True)
    total = sum(x["size_bytes"] for x in large)

    return {
        "status":     "ok",
        "path":       str(p),
        "threshold":  f"{min_size_mb} MB",
        "file_count": len(large),
        "total_size": _fmt_size(total),
        "files":      large,
    }


def get_disk_info(path: str = "~") -> dict:
    """Return disk usage statistics for the filesystem containing path.

    Shows total capacity, used space, free space, and usage percentage.
    Read-only — no confirmation needed.
    """
    p = _resolve(path)

    try:
        usage = shutil.disk_usage(p)
    except Exception as exc:
        return {"status": "error", "message": str(exc)}

    pct_used = (usage.used / usage.total) * 100

    return {
        "status":   "ok",
        "path":     str(p),
        "total":    _fmt_size(usage.total),
        "used":     _fmt_size(usage.used),
        "free":     _fmt_size(usage.free),
        "pct_used": round(pct_used, 1),
        "warning":  pct_used > 85,
        "message":  (
            f"Disk {pct_used:.0f}% full. "
            f"{_fmt_size(usage.free)} free of {_fmt_size(usage.total)}."
        ),
    }
