"""
Aider-style repo map generator for Parashurama.

Produces a compressed Markdown table (~200 tokens for a 50-file project)
summarising the codebase structure without dumping raw file contents.

Usage:
    from repo_map import generate
    map_text = generate("/path/to/project")
    # Inject map_text into Parashurama's context at task start
"""

from __future__ import annotations

import ast
import re
from pathlib import Path
from typing import NamedTuple

# Files/dirs to always skip
_SKIP_DIRS = {
    ".git", ".hg", ".svn", "node_modules", "__pycache__", ".venv", "venv",
    ".mypy_cache", ".pytest_cache", ".ruff_cache", "dist", "build", ".next",
    ".nuxt", "coverage", ".coverage", "htmlcov", "target", "out",
}
_SKIP_EXTENSIONS = {
    ".pyc", ".pyo", ".pyd", ".so", ".dylib", ".dll", ".exe",
    ".png", ".jpg", ".jpeg", ".gif", ".svg", ".ico", ".webp",
    ".mp4", ".mp3", ".wav", ".ogg",
    ".zip", ".tar", ".gz", ".bz2", ".7z",
    ".lock",  # e.g. package-lock.json excluded from map (huge)
}
_MAX_FILE_SIZE_KB = 500  # skip files larger than this


class FileEntry(NamedTuple):
    path: str          # relative path from root
    language: str
    size_kb: float
    symbols: list[str]  # classes, functions, exports (top-level only)


def _detect_language(path: Path) -> str:
    ext = path.suffix.lower()
    return {
        ".py": "python", ".ts": "typescript", ".tsx": "typescript",
        ".js": "javascript", ".jsx": "javascript", ".go": "go",
        ".rs": "rust", ".java": "java", ".kt": "kotlin",
        ".rb": "ruby", ".php": "php", ".cs": "csharp",
        ".cpp": "cpp", ".c": "c", ".h": "c",
        ".swift": "swift", ".scala": "scala",
        ".json": "json", ".yaml": "yaml", ".yml": "yaml",
        ".toml": "toml", ".md": "markdown", ".sql": "sql",
        ".sh": "shell", ".bash": "shell", ".zsh": "shell",
        ".html": "html", ".css": "css", ".scss": "scss",
    }.get(ext, "text")


def _extract_python_symbols(source: str) -> list[str]:
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []
    symbols = []
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if not node.name.startswith("_"):
                symbols.append(f"fn:{node.name}")
        elif isinstance(node, ast.ClassDef):
            symbols.append(f"cls:{node.name}")
    return symbols[:12]  # cap at 12 symbols per file


def _extract_js_symbols(source: str) -> list[str]:
    symbols = []
    # export function / export const / export class
    for m in re.finditer(
        r"export\s+(?:default\s+)?(?:async\s+)?(?:function|class|const|let|var)\s+(\w+)",
        source,
    ):
        symbols.append(m.group(1))
    # top-level function declarations (non-export)
    for m in re.finditer(r"^(?:async\s+)?function\s+(\w+)\s*\(", source, re.MULTILINE):
        name = m.group(1)
        if name not in symbols:
            symbols.append(f"fn:{name}")
    return symbols[:12]


def _extract_go_symbols(source: str) -> list[str]:
    symbols = []
    for m in re.finditer(r"^func\s+(?:\(\w+\s+\*?\w+\)\s+)?(\w+)\s*\(", source, re.MULTILINE):
        name = m.group(1)
        if name[0].isupper():  # exported only
            symbols.append(f"fn:{name}")
    for m in re.finditer(r"^type\s+(\w+)\s+struct", source, re.MULTILINE):
        symbols.append(f"cls:{m.group(1)}")
    return symbols[:12]


def _extract_symbols(path: Path, source: str) -> list[str]:
    lang = _detect_language(path)
    if lang == "python":
        return _extract_python_symbols(source)
    if lang in ("typescript", "javascript"):
        return _extract_js_symbols(source)
    if lang == "go":
        return _extract_go_symbols(source)
    return []


def _should_skip(path: Path) -> bool:
    if path.suffix in _SKIP_EXTENSIONS:
        return True
    if any(part in _SKIP_DIRS for part in path.parts):
        return True
    try:
        size_kb = path.stat().st_size / 1024
        return size_kb > _MAX_FILE_SIZE_KB
    except OSError:
        return True


def generate(root_dir: str, max_files: int = 100) -> str:
    """
    Walk root_dir and return a compressed repo map as Markdown.
    max_files: cap at this many files to keep token count bounded.
    """
    root = Path(root_dir).resolve()
    if not root.is_dir():
        return f"[repo_map: {root_dir} is not a directory]"

    entries: list[FileEntry] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(root)
        if _should_skip(rel):
            continue
        try:
            source = path.read_text(errors="ignore")
            size_kb = path.stat().st_size / 1024
            symbols = _extract_symbols(path, source)
            entries.append(FileEntry(
                path=str(rel),
                language=_detect_language(path),
                size_kb=round(size_kb, 1),
                symbols=symbols,
            ))
        except (OSError, UnicodeDecodeError):
            continue
        if len(entries) >= max_files:
            break

    if not entries:
        return f"[repo_map: no source files found in {root_dir}]"

    lines = [
        f"## Repo Map — {root.name}",
        f"_({len(entries)} files)_\n",
        "| File | Lang | KB | Symbols |",
        "|------|------|----|---------|",
    ]
    for e in entries:
        sym_str = ", ".join(e.symbols) if e.symbols else "—"
        lines.append(f"| `{e.path}` | {e.language} | {e.size_kb} | {sym_str} |")

    lines.append(
        "\n_Ask for specific file contents only when you need to read or modify them._"
    )
    return "\n".join(lines)


if __name__ == "__main__":
    import sys
    target = sys.argv[1] if len(sys.argv) > 1 else "."
    print(generate(target))
