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

# Tiered semantic-memory indexes and manifests
SMRITI_ROOT: Path = NARAD_HOME / "smriti"
SMRITI_VECTOR_DIR: Path = SMRITI_ROOT / "indexes"
SMRITI_MANIFEST_DIR: Path = SMRITI_ROOT / "manifests"
SMRITI_RAW_CACHE_DIR: Path = SMRITI_ROOT / "raw-cache"

# Canonical raw episode store (Smriti evidence; append-only JSONL per user)
EPISODE_DIR: Path = NARAD_HOME / "episodes"

# Durable thread memory and lightweight working/session state
THREAD_DIR: Path = NARAD_HOME / "threads"
WORKING_MEMORY_DIR: Path = NARAD_HOME / "working-memory"
SESSION_CATALOG_DIR: Path = NARAD_HOME / "session-catalog"
LEARNING_DIR: Path = NARAD_HOME / "learning"

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
SANKALPA_COMMITMENTS_PATH: Path = CONFIG_DIR / "sankalpa_commitments.jsonl"

# Expanded cultural-core control files
DHARMA_POLICY_PATH:        Path = CONFIG_DIR / "dharma_policy.json"
KARMA_MUTATIONS_PATH:      Path = CONFIG_DIR / "karma_mutations.jsonl"
SWAPNA_INBOX_DIR:          Path = NARAD_HOME / "swapna" / "inbox"
BENCHMARK_DIR:             Path = NARAD_HOME / "benchmarks"
BASELINE_DIR:              Path = BENCHMARK_DIR / "baseline"

# Finance database (already used by phase-8/finance_skill.py)
FINANCE_DB: Path = NARAD_HOME / "finance.db"

# ── Create all directories on import ──────────────────────────────────────────

for _d in [
    TRACE_DIR,
    Path(SMRITI_DB),
    WIKI_DIR,
    SMRITI_ROOT,
    SMRITI_VECTOR_DIR,
    SMRITI_MANIFEST_DIR,
    SMRITI_RAW_CACHE_DIR,
    EPISODE_DIR,
    THREAD_DIR,
    WORKING_MEMORY_DIR,
    SESSION_CATALOG_DIR,
    LEARNING_DIR,
    ARTIFACTS_DIR,
    CONFIG_DIR,
    SWAPNA_INBOX_DIR,
    BENCHMARK_DIR,
    BASELINE_DIR,
]:
    _d.mkdir(parents=True, exist_ok=True)
