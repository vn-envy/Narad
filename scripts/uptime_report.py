#!/usr/bin/env python3
"""Uptime in waking hours over the last 7 days: the pilot's Stage gate (99% or more).

    .venv/bin/python scripts/uptime_report.py
    .venv/bin/python scripts/uptime_report.py --days 7 --waking 07:00-23:00 --json

Reads NARAD_HOME/ops/uptime.jsonl (scripts/uptime_ping.py, every 5 minutes).
Each check stands for the time until the next one, at most 10 minutes; waking
time that no check covers (the Mac asleep or off) counts as down. Waking hours
default to NARAD_WAKING_HOURS or 07:00-23:00, in the Mac's local time. The
gate passes only on a full 7-day record at 99% or more.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

_r = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(_r)]  # narad root hop
import narad_paths  # noqa: E402, F401  — registers all phase dirs
from pilot_metrics import read_jsonl  # noqa: E402
from pilot_scorecard import OPS_DIR, parse_waking, uptime_summary  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Narad uptime in waking hours.")
    parser.add_argument("--days", type=int, default=7)
    parser.add_argument("--waking", default=None, help="HH:MM-HH:MM local (default NARAD_WAKING_HOURS or 07:00-23:00)")
    parser.add_argument("--json", action="store_true", help="print the summary as JSON")
    args = parser.parse_args(argv)
    now = time.time()
    records = read_jsonl(OPS_DIR / "uptime.jsonl", since=now - (args.days + 1) * 86400)
    summary = uptime_summary(records, now=now, days=args.days, waking=parse_waking(args.waking))
    if args.json:
        print(json.dumps(summary, indent=2))
        return 0 if summary["gate_99"] else 1
    seconds = summary["seconds"]
    print(f"Narad uptime, waking hours {summary['waking_hours']}, "
          f"{summary['window']['since'][:16]} to {summary['window']['until'][:16]}")
    if summary["uptime_pct"] is None:
        print("  No checks in waking hours yet: is com.narad.uptime installed? "
              "(scripts/install_launchd.sh status)")
        return 1
    print(f"  Uptime: {summary['uptime_pct']:.2f}%  (gate: 99% over a full {args.days} days)")
    print(f"  Down: app {seconds['app_down'] // 60} min, tunnel {seconds['tunnel_down'] // 60} min, "
          f"no check recorded {seconds['missing'] // 60} min")
    print(f"  Checks: {', '.join(f'{k} {v}' for k, v in sorted(summary['checks'].items())) or 'none'}")
    if summary["edge_only_checks"]:
        print(f"  {summary['edge_only_checks']} 'up' checks stopped at Cloudflare Access, which does not prove "
              "the tunnel; add an Access Bypass on /health to check it end to end.")
    if not summary["window"]["full_window"]:
        print(f"  Records start {summary['window']['since'][:16]}: not yet a full {args.days}-day window.")
    print(f"  Stage gate: {'PASS' if summary['gate_99'] else 'not met'}")
    return 0 if summary["gate_99"] else 1


if __name__ == "__main__":
    sys.exit(main())
