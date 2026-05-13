"""
Narad workspace configuration — canonical paths for all data storage.

All data lives under NARAD_HOME (default: ~/.narad/).
Override with the NARAD_HOME environment variable.

Import from any module:
    import sys
    from pathlib import Path
    _ROOT = Path(__file__).parent.parent  # adjust depth as needed
    if str(_ROOT) not in sys.path:
        sys.path.insert(0, str(_ROOT))
    from narad_config import TRACE_DIR, WIKI_DIR  # etc.
"""

from __future__ import annotations

import os
from pathlib import Path

# ── Home directory ─────────────────────────────────────────────────────────────

NARAD_HOME: Path = Path(os.environ.get("NARAD_HOME", Path.home() / ".narad"))

# ── Sub-directories ────────────────────────────────────────────────────────────

# Yantra session traces
TRACE_DIR: Path = NARAD_HOME / "sessions"

# LanceDB vector memory (stored as string because lancedb.connect() takes str)
SMRITI_DB: str = str(NARAD_HOME / "memory")

# Project wiki (smriti_v2 — WIKI_DIR/user_id/project_id/entity.md)
WIKI_DIR: Path = NARAD_HOME / "wiki"

# Generated artifacts (executor output — ARTIFACTS_DIR/run_id/file)
ARTIFACTS_DIR: Path = NARAD_HOME / "artifacts"

# Config files (sutras, karma, sankalpa)
CONFIG_DIR: Path = NARAD_HOME / "config"

SUTRAS_PATH:               Path = CONFIG_DIR / "sutras.jsonl"
WEAK_SESSIONS_PATH:        Path = CONFIG_DIR / "weak_sessions.jsonl"
KARMA_PATH:                Path = CONFIG_DIR / "karma.jsonl"
SUTRA_OVERRIDES_PATH:      Path = CONFIG_DIR / "sutra_overrides.jsonl"
SANKALPAS_PATH:            Path = CONFIG_DIR / "sankalpas.jsonl"
SANKALPA_OVERRIDES_PATH:   Path = CONFIG_DIR / "sankalpa_overrides.jsonl"
SANKALPA_SESSION_LOG_PATH: Path = CONFIG_DIR / "session_log.jsonl"

# Finance database (already used by phase-8/finance_skill.py)
FINANCE_DB: Path = NARAD_HOME / "finance.db"

# ── Create all directories on import ──────────────────────────────────────────

for _d in [
    TRACE_DIR,
    Path(SMRITI_DB),
    WIKI_DIR,
    ARTIFACTS_DIR,
    CONFIG_DIR,
]:
    _d.mkdir(parents=True, exist_ok=True)
