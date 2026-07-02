#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

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
