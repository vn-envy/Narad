"""
pilot_scorecard — host uptime in waking hours, backup state, and the weekly
pilot scorecard that the Stage gates read.

Inputs, all local:
  NARAD_HOME/ops/uptime.jsonl        scripts/uptime_ping.py, every 5 minutes
  NARAD_HOME/ops/backup.jsonl        scripts/narad_backup.py backup, nightly
  NARAD_HOME/ops/backup_drill.jsonl  scripts/narad_backup.py drill, weekly
  NARAD_HOME/ops/watchdog.jsonl      scripts/narad_watchdog.sh, on each restart
  per-profile metrics                pilot_metrics.py

Used by GET /pilot/metrics?scope=all (owner), scripts/uptime_report.py and
scripts/weekly_scorecard.py. Definitions: docs/PILOT_CONSENT_AND_METRICS.md.
"""
from __future__ import annotations

import os
import time
from datetime import datetime, timedelta, tzinfo
from datetime import time as dtime
from pathlib import Path
from typing import Any, Iterable

from narad_config import NARAD_HOME
from pilot_metrics import (
    consent_status,
    iso_local,
    known_profiles,
    owner_view,
    percentile,
    profile_summary,
    ratio,
    read_jsonl,
)

OPS_DIR: Path = NARAD_HOME / "ops"


# ── Uptime (waking hours) ─────────────────────────────────────────────────────

def parse_waking(value: str | None) -> tuple[dtime, dtime]:
    """"07:00-23:00" -> (07:00, 23:00). An end before the start wraps past midnight."""
    raw = (value or os.environ.get("NARAD_WAKING_HOURS") or "07:00-23:00").strip()

    def clock(text: str) -> dtime:
        hour, _, minute = text.strip().partition(":")
        return dtime(int(hour), int(minute or 0))

    start, _, end = raw.partition("-")
    try:
        return clock(start), clock(end)
    except ValueError as exc:
        raise ValueError(f"waking hours must look like 07:00-23:00, got {raw!r}") from exc


def _waking_windows(start: float, end: float, waking: tuple[dtime, dtime],
                    tz: tzinfo | None) -> list[tuple[float, float]]:
    first = datetime.fromtimestamp(start, tz).date() - timedelta(days=1)
    last = datetime.fromtimestamp(end, tz).date()
    windows = []
    day = first
    while day <= last:
        opens = datetime.combine(day, waking[0])
        closes = datetime.combine(day + timedelta(days=1 if waking[1] <= waking[0] else 0), waking[1])
        if tz is not None:
            opens, closes = opens.replace(tzinfo=tz), closes.replace(tzinfo=tz)
        lo, hi = max(start, opens.timestamp()), min(end, closes.timestamp())
        if hi > lo:
            windows.append((lo, hi))
        day += timedelta(days=1)
    return windows


def uptime_summary(
    records: list[dict[str, Any]],
    *,
    now: float | None = None,
    days: int = 7,
    waking: tuple[dtime, dtime] | None = None,
    max_gap_s: float = 600,
    tz: tzinfo | None = None,
) -> dict[str, Any]:
    """Share of waking-hour time in the window during which Narad was up.

    Each check stands for the time until the next check, but at most
    `max_gap_s`; time that no check covers (the Mac asleep or off, the job
    not running) counts as down. The window starts at the first check ever
    recorded when that is later than now - days, and `full_window` says so.
    """
    now = time.time() if now is None else now
    waking = waking or parse_waking(None)
    checks = sorted(
        (float(r["t"]), str(r.get("status") or "unknown"), r)
        for r in records if r.get("t") is not None
    )
    window_start = now - days * 86400
    full_window = bool(checks) and checks[0][0] <= window_start
    start = max(window_start, checks[0][0]) if checks else window_start
    windows = _waking_windows(start, now, waking, tz)
    waking_s = sum(hi - lo for lo, hi in windows)
    seconds: dict[str, float] = {"up": 0.0, "app_down": 0.0, "tunnel_down": 0.0}
    counts: dict[str, int] = {}
    edge_only = 0
    for index, (stamp, status, row) in enumerate(checks):
        if stamp >= start:
            counts[status] = counts.get(status, 0) + 1
            if status == "up" and (row.get("public") or {}).get("reached") == "access":
                edge_only += 1
        following = checks[index + 1][0] if index + 1 < len(checks) else now
        cover_lo, cover_hi = stamp, min(following, stamp + max_gap_s, now)
        for lo, hi in windows:
            overlap = min(hi, cover_hi) - max(lo, cover_lo)
            if overlap > 0:
                bucket = status if status in seconds else "app_down"
                seconds[bucket] += overlap
    seconds["missing"] = max(0.0, waking_s - sum(seconds.values()))
    uptime = round(100 * seconds["up"] / waking_s, 3) if waking_s and checks else None
    return {
        "window": {"days": days, "since": iso_local(start), "until": iso_local(now), "full_window": full_window},
        "waking_hours": f"{waking[0].strftime('%H:%M')}-{waking[1].strftime('%H:%M')}",
        "waking_seconds": round(waking_s),
        "uptime_pct": uptime,
        "seconds": {k: round(v) for k, v in seconds.items()},
        "checks": counts,
        "edge_only_checks": edge_only,
        "gate_99": bool(uptime is not None and uptime >= 99.0 and full_window),
    }


# ── Weekly scorecard ──────────────────────────────────────────────────────────

def _latest(path: Path) -> dict[str, Any] | None:
    rows = read_jsonl(path)
    return rows[-1] if rows else None


def ops_status(*, now: float | None = None, days: int = 7) -> dict[str, Any]:
    now = time.time() if now is None else now
    since = now - days * 86400
    backup = _latest(OPS_DIR / "backup.jsonl")
    drill = _latest(OPS_DIR / "backup_drill.jsonl")
    restarts = read_jsonl(OPS_DIR / "watchdog.jsonl", since=since)
    backup_age_h = round((now - float(backup["t"])) / 3600, 1) if backup and backup.get("t") else None
    drill_age_d = round((now - float(drill["t"])) / 86400, 1) if drill and drill.get("t") else None
    return {
        "backup": backup,
        "backup_age_hours": backup_age_h,
        "drill": drill,
        "drill_age_days": drill_age_d,
        "watchdog_restarts": len(restarts),
    }


def _totals(summaries: Iterable[dict[str, Any]]) -> dict[str, Any]:
    rows = list(summaries)
    turns = sum(r["turns"] for r in rows)
    successes = sum(r["task_success"]["successes"] for r in rows)
    sessions = sum(r["abandonment"]["sessions"] for r in rows)
    abandoned = sum(r["abandonment"]["abandoned"] for r in rows)
    approvals = {k: sum(r["approvals"][k] for r in rows) for k in ("requested", "needed", "unclassified", "unneeded")}
    by_tier: dict[str, int] = {}
    blocked = 0
    for r in rows:
        for tier, n in r["egress"]["by_tier"].items():
            by_tier[tier] = by_tier.get(tier, 0) + n
        blocked += sum(r["egress"]["blocked"].values())
    return {
        "turns": turns,
        "task_success_rate": ratio(successes, turns),
        # Household latency: the median of each person's median, so one heavy user doesn't set it.
        "time_to_done_p50_ms": percentile((r["time_to_done_ms"]["p50"] for r in rows if r["turns"]), 50),
        "first_text_p50_no_tools_ms": percentile(
            (r["first_text_ms"]["p50_no_tools"] for r in rows if r["turns"]), 50
        ),
        "abandonment_rate": ratio(abandoned, sessions),
        "approvals": approvals,
        "feedback": {
            "up": sum(r["feedback"]["up"] for r in rows),
            "down": sum(r["feedback"]["down"] for r in rows),
            "coverage": ratio(sum(r["feedback"]["rated_turns"] for r in rows), turns),
        },
        "voice": {"stt": sum(r["voice"]["stt"] for r in rows), "tts": sum(r["voice"]["tts"] for r in rows)},
        "egress": {"by_tier": by_tier, "blocked": blocked,
                   "redact_rules_only": sum(r["egress"]["redact_rules_only"] for r in rows),
                   "blocked_tier_sent": sum(r["egress"]["blocked_tier_sent"] for r in rows)},
    }


def weekly_scorecard(*, now: float | None = None, days: int = 7, waking: str | None = None,
                     tz: tzinfo | None = None) -> dict[str, Any]:
    """This week against last week, plus uptime, backups and the Stage gates."""
    now = time.time() if now is None else now
    profiles = known_profiles()
    this_week = {p: profile_summary(p, days=days, now=now) for p in profiles}
    last_week = {p: profile_summary(p, days=days, now=now, until=now - days * 86400) for p in profiles}
    uptime = uptime_summary(
        read_jsonl(OPS_DIR / "uptime.jsonl", since=now - (days + 1) * 86400),
        now=now, days=days, waking=parse_waking(waking), tz=tz,
    )
    ops = ops_status(now=now, days=days)
    consent = {p: consent_status(p) for p in profiles}
    totals, previous = _totals(this_week.values()), _totals(last_week.values())
    active = [p for p, s in this_week.items() if s["turns"]]
    drill = ops["drill"] or {}
    gates = {
        "uptime_99_waking": uptime["gate_99"],
        "restore_drill_passed": bool(drill.get("passed")) and ops["drill_age_days"] is not None
        and ops["drill_age_days"] <= 8,
        "backup_fresh": ops["backup_age_hours"] is not None and ops["backup_age_hours"] <= 26,
        "consent_current_for_active": all(not consent[p]["needs_consent"] for p in active),
        "no_blocked_tier_egress": totals["egress"]["blocked_tier_sent"] == 0,
        "success_not_down": (
            previous["task_success_rate"] is None or totals["task_success_rate"] is None
            or totals["task_success_rate"] >= previous["task_success_rate"]
        ),
    }
    return {
        "generated_at": iso_local(now),
        "window_days": days,
        "uptime": uptime,
        "ops": ops,
        "totals": totals,
        "previous": previous,
        "profiles": {p: owner_view(s) for p, s in this_week.items()},
        "consent": {p: {k: c[k] for k in ("version", "accepted", "needs_consent")} for p, c in consent.items()},
        "gates": gates,
    }


def _fmt(value: Any, suffix: str = "") -> str:
    if value is None:
        return "n/a"
    if isinstance(value, float) and suffix == "%":
        return f"{value * 100:.1f}%"
    return f"{value}{suffix}"


def scorecard_markdown(card: dict[str, Any]) -> str:
    uptime, ops, totals, previous = card["uptime"], card["ops"], card["totals"], card["previous"]
    drill = ops.get("drill") or {}
    backup = ops.get("backup") or {}
    lines = [
        f"# Narad pilot scorecard, week ending {card['generated_at'][:10]}",
        "",
        "Counts and outcomes only; no prompts or replies are stored or shown.",
        "",
        "## Stage gates",
        "",
        "| Gate | Status |",
        "|---|---|",
    ]
    labels = {
        "uptime_99_waking": "Uptime 99% or more in waking hours, full 7 days",
        "restore_drill_passed": "Restore drill passed in the last 8 days",
        "backup_fresh": "Last backup under 26 hours old",
        "consent_current_for_active": "Everyone active accepted the current consent",
        "no_blocked_tier_egress": "Nothing sent to a blocked provider",
        "success_not_down": "Task success not below last week",
    }
    for key, label in labels.items():
        lines.append(f"| {label} | {'pass' if card['gates'].get(key) else 'FAIL'} |")
    lines += [
        "",
        "## Host",
        "",
        "| Measure | Value |",
        "|---|---|",
        f"| Uptime, waking hours ({uptime['waking_hours']}) | {_fmt(uptime['uptime_pct'], ' %')} |",
        f"| Time down: app / tunnel / no check (min) | {uptime['seconds']['app_down'] // 60} / "
        f"{uptime['seconds']['tunnel_down'] // 60} / {uptime['seconds']['missing'] // 60} |",
        f"| Full 7-day window recorded | {'yes' if uptime['window']['full_window'] else 'no'} |",
        f"| Checks that stopped at Cloudflare Access (tunnel not proven) | {uptime['edge_only_checks']} |",
        f"| Watchdog restarts | {ops['watchdog_restarts']} |",
        f"| Last backup | {backup.get('ts', 'none')} ({_fmt(ops['backup_age_hours'], ' h ago')}) |",
        f"| Last restore drill | {drill.get('ts', 'none')}: {'pass' if drill.get('passed') else 'fail or missing'} |",
        "",
        "## Family (this week vs last week)",
        "",
        "| Measure | This week | Last week |",
        "|---|---|---|",
        f"| Turns | {totals['turns']} | {previous['turns']} |",
        f"| Task success | {_fmt(totals['task_success_rate'], '%')} | {_fmt(previous['task_success_rate'], '%')} |",
        f"| First words on screen, p50, turns without tools | {_fmt(totals['first_text_p50_no_tools_ms'], ' ms')} | "
        f"{_fmt(previous['first_text_p50_no_tools_ms'], ' ms')} |",
        f"| Time to done, p50 | {_fmt(totals['time_to_done_p50_ms'], ' ms')} | {_fmt(previous['time_to_done_p50_ms'], ' ms')} |",
        f"| Abandoned sessions | {_fmt(totals['abandonment_rate'], '%')} | {_fmt(previous['abandonment_rate'], '%')} |",
        f"| Approvals requested / needed / unclassified | {totals['approvals']['requested']} / "
        f"{totals['approvals']['needed']} / {totals['approvals']['unclassified']} | "
        f"{previous['approvals']['requested']} / {previous['approvals']['needed']} / "
        f"{previous['approvals']['unclassified']} |",
        f"| Thumbs up / down (coverage) | {totals['feedback']['up']} / {totals['feedback']['down']} "
        f"({_fmt(totals['feedback']['coverage'], '%')}) | {previous['feedback']['up']} / "
        f"{previous['feedback']['down']} ({_fmt(previous['feedback']['coverage'], '%')}) |",
        f"| Voice in / voice out | {totals['voice']['stt']} / {totals['voice']['tts']} | "
        f"{previous['voice']['stt']} / {previous['voice']['tts']} |",
        f"| Cloud calls by tier | {_tiers(totals['egress'])} | {_tiers(previous['egress'])} |",
        f"| Calls refused by the privacy gateway | {totals['egress']['blocked']} | {previous['egress']['blocked']} |",
        "",
        "## Per person (this week)",
        "",
        "| Profile | Consent | Turns | Success | p50 done | Abandoned | Up / down | Voice in |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for profile_id, summary in card["profiles"].items():
        consent = card["consent"].get(profile_id, {})
        lines.append(
            f"| {profile_id} | {'current' if not consent.get('needs_consent') else 'needed'} | "
            f"{summary['turns']} | {_fmt(summary['task_success']['rate'], '%')} | "
            f"{_fmt(summary['time_to_done_ms']['p50'], ' ms')} | {summary['abandonment']['abandoned']} | "
            f"{summary['feedback']['up']} / {summary['feedback']['down']} | {summary['voice']['stt']} |"
        )
    return "\n".join(lines) + "\n"


def _tiers(egress: dict[str, Any]) -> str:
    tiers = egress.get("by_tier") or {}
    return ", ".join(f"{k} {tiers[k]}" for k in sorted(tiers)) or "none"
