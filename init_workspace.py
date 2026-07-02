"""
Narad workspace initialiser — run once to migrate data from phase-X/ dirs to ~/.narad/.

Usage:
    python init_workspace.py

Safe to re-run: skips files/dirs that already exist at the destination.
Does NOT delete old phase-X data — verify the migration, then clean up manually.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

_HERE = Path(__file__).parent
_r = next(p for p in Path(__file__).resolve().parents if (p / "narad_paths.py").exists())
sys.path[:0] = [str(_r)]  # narad root hop
import narad_paths  # noqa: F401

from narad_config import (
    NARAD_HOME, TRACE_DIR, SMRITI_DB, WIKI_DIR, ARTIFACTS_DIR, CONFIG_DIR,
    SUTRAS_PATH, WEAK_SESSIONS_PATH, KARMA_PATH,
    SUTRA_OVERRIDES_PATH, SANKALPAS_PATH,
    SANKALPA_OVERRIDES_PATH, SANKALPA_SESSION_LOG_PATH,
)


def _copy_file(src: Path, dst: Path) -> str:
    if not src.exists():
        return f"  skip  {src.name} (source missing)"
    if dst.exists():
        return f"  skip  {dst.name} (already migrated)"
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)
    return f"  ok    {src.name} → {dst}"


def _copy_dir(src: Path, dst: Path) -> str:
    if not src.exists():
        return f"  skip  {src.name}/ (source missing)"
    if dst.exists():
        return f"  skip  {dst.name}/ (already migrated)"
    shutil.copytree(src, dst)
    return f"  ok    {src.name}/ → {dst}"


def _copy_dir_contents(src: Path, dst: Path) -> list[str]:
    """Copy all files from src/ into dst/, skipping existing."""
    if not src.exists():
        return [f"  skip  {src.name}/ (source missing)"]
    dst.mkdir(parents=True, exist_ok=True)
    results = []
    for item in src.iterdir():
        dest_item = dst / item.name
        if dest_item.exists():
            results.append(f"  skip  {item.name} (exists)")
        elif item.is_file():
            shutil.copy2(item, dest_item)
            results.append(f"  ok    {item.name}")
        elif item.is_dir():
            shutil.copytree(item, dest_item)
            results.append(f"  ok    {item.name}/")
    return results or [f"  (nothing to copy from {src.name}/)"]


def main() -> None:
    print(f"\nNarad Workspace Init")
    print(f"Home: {NARAD_HOME}\n")

    # ── Sessions (Yantra traces) ───────────────────────────────────────────────
    print("Sessions (Yantra traces):")
    trace_src = _HERE / "phase-2" / "yantra_traces"
    for line in _copy_dir_contents(trace_src, TRACE_DIR):
        print(line)

    # ── Memory (LanceDB) ──────────────────────────────────────────────────────
    print("\nMemory (LanceDB):")
    smriti_src = _HERE / "phase-2" / "smriti_db"
    print(_copy_dir(smriti_src, Path(SMRITI_DB)))

    # ── Wiki (project-memory) ─────────────────────────────────────────────────
    print("\nWiki (project-memory):")
    wiki_src = _HERE / "phase-9" / "project-memory"
    for line in _copy_dir_contents(wiki_src, WIKI_DIR):
        print(line)

    # ── Artifacts (executor outputs) ──────────────────────────────────────────
    print("\nArtifacts (executor outputs):")
    artifacts_src = _HERE / "phase-7" / "outputs"
    for line in _copy_dir_contents(artifacts_src, ARTIFACTS_DIR):
        print(line)

    # ── Config files ──────────────────────────────────────────────────────────
    print("\nConfig files:")
    migrations = [
        (_HERE / "phase-3" / "sutras.jsonl",              SUTRAS_PATH),
        (_HERE / "phase-3" / "weak_sessions.jsonl",        WEAK_SESSIONS_PATH),
        (_HERE / "phase-5" / "karma.jsonl",                KARMA_PATH),
        (_HERE / "phase-5" / "sutra_overrides.jsonl",      SUTRA_OVERRIDES_PATH),
        (_HERE / "phase-6" / "sankalpas.jsonl",            SANKALPAS_PATH),
        (_HERE / "phase-6" / "sankalpa_overrides.jsonl",   SANKALPA_OVERRIDES_PATH),
        (_HERE / "phase-6" / "session_log.jsonl",          SANKALPA_SESSION_LOG_PATH),
    ]
    for src, dst in migrations:
        print(_copy_file(src, dst))

    # ── Summary ───────────────────────────────────────────────────────────────
    print(f"\nWorkspace structure:")
    for item in sorted(NARAD_HOME.iterdir()):
        if item.is_dir():
            count = sum(1 for _ in item.rglob("*") if _.is_file())
            print(f"  {item.name}/  ({count} files)")
        else:
            print(f"  {item.name}")

    print(f"\nDone. Old phase-X/ data is untouched — verify then delete manually.\n")


if __name__ == "__main__":
    main()
