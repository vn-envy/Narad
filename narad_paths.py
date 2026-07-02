"""
narad_paths — single-source sys.path bootstrap for the Narad harness.

The repo predates its packaging: modules live in hyphenated phase dirs
(phase-1/ … phase-9/) that cannot be imported as packages. Until the
physical rename to a proper `narad/` package (post-launch backlog), this
module is the ONLY place allowed to touch sys.path.

Usage (entry points only — server, scripts, standalone tests):

    import narad_paths  # noqa: F401

Library modules must NOT bootstrap; by the time they are imported, an
entry point has already registered every module dir below.

Idempotent: importing twice is a no-op.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent

# Every directory that contributes importable modules, in resolution order.
# Root first (narad_config, smriti_core, harness_contract, …), then phases.
_MODULE_DIRS = [
    ROOT,
    ROOT / "phase-1",
    ROOT / "phase-2",
    ROOT / "phase-3",
    ROOT / "phase-5",
    ROOT / "phase-6",
    ROOT / "phase-7",
    ROOT / "phase-7" / "skills",
    ROOT / "phase-8",
    ROOT / "phase-9",
]


def _register() -> None:
    for d in _MODULE_DIRS:
        s = str(d)
        if d.is_dir() and s not in sys.path:
            sys.path.append(s)  # append, not insert: never shadow stdlib/site-packages


_register()
