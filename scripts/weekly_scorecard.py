#!/usr/bin/env python3
"""The weekly pilot scorecard as Markdown: uptime, backups, the restore drill,
consent, and each person's counts and outcomes (never prompts or replies).

    .venv/bin/python scripts/weekly_scorecard.py                 # print Markdown
    .venv/bin/python scripts/weekly_scorecard.py --out ~/Desktop/narad-week.md
    .venv/bin/python scripts/weekly_scorecard.py --json

The same scorecard is served to the owner at GET /pilot/metrics?scope=all
(add format=markdown for text). The weekly review ritual that uses it is in
docs/PILOT_CONSENT_AND_METRICS.md.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_r = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(_r)]  # narad root hop
import narad_paths  # noqa: E402, F401  — registers all phase dirs
from pilot_scorecard import scorecard_markdown, weekly_scorecard  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Narad weekly pilot scorecard.")
    parser.add_argument("--days", type=int, default=7)
    parser.add_argument("--waking", default=None, help="HH:MM-HH:MM local (default NARAD_WAKING_HOURS or 07:00-23:00)")
    parser.add_argument("--json", action="store_true", help="print JSON instead of Markdown")
    parser.add_argument("--out", help="also write the output to this file")
    args = parser.parse_args(argv)
    card = weekly_scorecard(days=args.days, waking=args.waking)
    text = json.dumps(card, indent=2) + "\n" if args.json else scorecard_markdown(card)
    if args.out:
        out = Path(args.out).expanduser()
        out.write_text(text, encoding="utf-8")
        out.chmod(0o600)
    sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
