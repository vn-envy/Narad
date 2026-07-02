#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
from pathlib import Path

_r = next(p for p in Path(__file__).resolve().parents if (p / "narad_paths.py").exists())
sys.path[:0] = [str(_r)]  # narad root hop
import narad_paths  # noqa: F401  — registers all phase dirs; must precede phase imports

# isort: split
from smriti_core import benchmark_snapshot


def main() -> int:
    baseline = {
        "name": "baseline_narad_legacy",
        "legacy_direct_memory_imports": 6,
        "legacy_writer_count": 5,
        "baseline_unit_tests": 12,
        "notes": "Captured from Narad before the cultural-core refactor on 2026-05-31.",
    }
    path = benchmark_snapshot(name="baseline_narad_legacy", metrics=baseline)
    print(json.dumps({"status": "ok", "path": str(path), "metrics": baseline}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
