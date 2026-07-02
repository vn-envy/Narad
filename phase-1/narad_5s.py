"""
Shuddhi — 5S file-system health for Narad's ~/.narad/ data directory.

Phases:
  Sort       — identify candidate files by age, size, orphan status
  Set-in-Order — build/update ~/.narad/manifest.json index
  Shine      — delete files exceeding retention thresholds (dry_run=True by default)
  Standardize — write ~/.narad/5s_policy.json with retention rules
  Sustain    — full cycle; emits a Yantra event

Retention defaults (env-overridable):
  NARAD_SESSION_TTL_DAYS  = 180   session JSONLs in ~/.narad/sessions/
  NARAD_ARTIFACT_TTL_DAYS = 30    generated artifacts in ~/.narad/artifacts/
  NARAD_WEAK_SESSION_TTL  = 90    entries in weak_sessions.jsonl
"""
from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

from narad_config import (
    NARAD_HOME,
    TRACE_DIR,
    ARTIFACTS_DIR,
    CONFIG_DIR,
    WEAK_SESSIONS_PATH,
)

SESSION_TTL_DAYS  = int(os.environ.get("NARAD_SESSION_TTL_DAYS",  "180"))
ARTIFACT_TTL_DAYS = int(os.environ.get("NARAD_ARTIFACT_TTL_DAYS", "30"))
WEAK_SESSION_TTL  = int(os.environ.get("NARAD_WEAK_SESSION_TTL",  "90"))

_POLICY_PATH   = NARAD_HOME / "5s_policy.json"
_MANIFEST_PATH = NARAD_HOME / "manifest.json"
_SHINE_LOG     = CONFIG_DIR / "5s_shine_log.jsonl"


def _file_age_days(path: Path) -> float:
    try:
        return (time.time() - path.stat().st_mtime) / 86400
    except OSError:
        return 0.0


def _dir_size_mb(path: Path) -> float:
    total = 0
    try:
        for f in path.rglob("*"):
            try:
                total += f.stat().st_size
            except OSError:
                pass
    except OSError:
        pass
    return total / (1024 * 1024)


def _file_size_mb(path: Path) -> float:
    try:
        return path.stat().st_size / (1024 * 1024)
    except OSError:
        return 0.0


class NaradShuddhi:
    """5S health and cleanup for ~/.narad/ data directories."""

    # ── S1: Sort ──────────────────────────────────────────────────────────────

    def sort(self) -> dict:
        """Identify stale sessions, orphaned artifacts, and oversized config files."""
        now = time.time()

        # Session files
        session_files = list(TRACE_DIR.glob("*.jsonl")) if TRACE_DIR.exists() else []
        stale_sessions = [
            f for f in session_files
            if _file_age_days(f) > SESSION_TTL_DAYS
        ]
        session_size_mb = sum(_file_size_mb(f) for f in session_files)
        stale_size_mb   = sum(_file_size_mb(f) for f in stale_sessions)

        # Artifact directories
        art_dirs = [d for d in ARTIFACTS_DIR.iterdir() if d.is_dir()] \
            if ARTIFACTS_DIR.exists() else []
        stale_art = [d for d in art_dirs if _file_age_days(d) > ARTIFACT_TTL_DAYS]
        art_size_mb   = sum(_dir_size_mb(d) for d in art_dirs)
        stale_art_mb  = sum(_dir_size_mb(d) for d in stale_art)

        # Weak sessions — filter old entries only (don't delete the file itself)
        weak_entries = 0
        stale_weak   = 0
        if WEAK_SESSIONS_PATH.exists():
            cutoff = (datetime.now(timezone.utc) - timedelta(days=WEAK_SESSION_TTL)).isoformat()
            for line in WEAK_SESSIONS_PATH.read_text().splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    e = json.loads(line)
                    weak_entries += 1
                    if e.get("ts", "") < cutoff:
                        stale_weak += 1
                except json.JSONDecodeError:
                    pass

        return {
            "sessions": {
                "total":         len(session_files),
                "stale":         len(stale_sessions),
                "total_mb":      round(session_size_mb, 2),
                "reclaimable_mb": round(stale_size_mb, 2),
                "oldest_days":   round(max((_file_age_days(f) for f in session_files), default=0), 1),
                "ttl_days":      SESSION_TTL_DAYS,
                "candidates":    [str(f) for f in stale_sessions[:20]],
            },
            "artifacts": {
                "total":         len(art_dirs),
                "stale":         len(stale_art),
                "total_mb":      round(art_size_mb, 2),
                "reclaimable_mb": round(stale_art_mb, 2),
                "oldest_days":   round(max((_file_age_days(d) for d in art_dirs), default=0), 1),
                "ttl_days":      ARTIFACT_TTL_DAYS,
                "candidates":    [str(d) for d in stale_art[:20]],
            },
            "weak_sessions": {
                "total":  weak_entries,
                "stale":  stale_weak,
                "ttl_days": WEAK_SESSION_TTL,
            },
        }

    # ── S2: Set-in-Order ──────────────────────────────────────────────────────

    def set_in_order(self) -> dict:
        """Build/update ~/.narad/manifest.json listing all known data paths."""
        dirs = {
            "sessions":  str(TRACE_DIR),
            "artifacts": str(ARTIFACTS_DIR),
            "config":    str(CONFIG_DIR),
            "home":      str(NARAD_HOME),
        }
        counts = {}
        for name, path in dirs.items():
            p = Path(path)
            counts[name] = len(list(p.iterdir())) if p.exists() else 0

        manifest = {
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "directories": dirs,
            "item_counts": counts,
            "policy":      str(_POLICY_PATH),
        }
        _MANIFEST_PATH.write_text(json.dumps(manifest, indent=2))
        return {"status": "ok", "manifest": str(_MANIFEST_PATH), "counts": counts}

    # ── S3: Shine ─────────────────────────────────────────────────────────────

    def shine(self, dry_run: bool = True) -> dict:
        """Delete stale files/dirs. dry_run=True lists candidates without deleting."""
        candidates = self.sort()
        deleted_sessions = 0
        deleted_artifacts = 0
        freed_mb = 0.0
        log_entries = []

        if not dry_run:
            import shutil

            # Delete stale session files
            for path_str in candidates["sessions"]["candidates"]:
                path = Path(path_str)
                if path.exists() and path.is_file():
                    mb = _file_size_mb(path)
                    path.unlink()
                    deleted_sessions += 1
                    freed_mb += mb
                    log_entries.append({"type": "session", "path": path_str, "mb": round(mb, 3)})

            # Delete stale artifact directories
            for path_str in candidates["artifacts"]["candidates"]:
                path = Path(path_str)
                if path.exists() and path.is_dir():
                    mb = _dir_size_mb(path)
                    shutil.rmtree(path)
                    deleted_artifacts += 1
                    freed_mb += mb
                    log_entries.append({"type": "artifact", "path": path_str, "mb": round(mb, 3)})

            # Write shine log
            CONFIG_DIR.mkdir(parents=True, exist_ok=True)
            with open(_SHINE_LOG, "a") as f:
                f.write(json.dumps({
                    "ts": datetime.now(timezone.utc).isoformat(),
                    "deleted_sessions": deleted_sessions,
                    "deleted_artifacts": deleted_artifacts,
                    "freed_mb": round(freed_mb, 2),
                }) + "\n")

        return {
            "dry_run":           dry_run,
            "deleted_sessions":  deleted_sessions,
            "deleted_artifacts": deleted_artifacts,
            "freed_mb":          round(freed_mb, 2),
            "reclaimable_mb":    round(
                candidates["sessions"]["reclaimable_mb"]
                + candidates["artifacts"]["reclaimable_mb"], 2
            ),
            "session_candidates":  candidates["sessions"]["stale"],
            "artifact_candidates": candidates["artifacts"]["stale"],
        }

    # ── S4: Standardize ───────────────────────────────────────────────────────

    def standardize(self) -> dict:
        """Write retention policy to ~/.narad/5s_policy.json."""
        policy = {
            "created_at":       datetime.now(timezone.utc).isoformat(),
            "session_ttl_days": SESSION_TTL_DAYS,
            "artifact_ttl_days": ARTIFACT_TTL_DAYS,
            "weak_session_ttl_days": WEAK_SESSION_TTL,
            "shine_schedule":   "daily",
            "dry_run_default":  True,
        }
        _POLICY_PATH.write_text(json.dumps(policy, indent=2))
        return {"status": "ok", "policy_path": str(_POLICY_PATH), "policy": policy}

    # ── S5: Sustain ───────────────────────────────────────────────────────────

    def sustain(self) -> dict:
        """Full 5S cycle — sort → set_in_order → shine (dry_run) → standardize."""
        sort_result  = self.sort()
        order_result = self.set_in_order()
        shine_result = self.shine(dry_run=False)
        std_result   = self.standardize()

        try:
            from phase_2_yantra import tracer  # TODO: dead path — module name never existed; wire to yantra tracer properly
            tracer.log_event("shuddhi_run", freed_mb=shine_result["freed_mb"])
        except Exception:
            pass

        return {
            "sort":        sort_result,
            "set_in_order": order_result,
            "shine":       shine_result,
            "standardize": std_result,
        }

    # ── Report ────────────────────────────────────────────────────────────────

    def report(self) -> dict:
        """Return a health snapshot with a 5S score (0–1.0)."""
        sort_data = self.sort()

        total_files = (
            sort_data["sessions"]["total"]
            + sort_data["artifacts"]["total"]
            + sort_data["weak_sessions"]["total"]
        )
        stale_files = (
            sort_data["sessions"]["stale"]
            + sort_data["artifacts"]["stale"]
            + sort_data["weak_sessions"]["stale"]
        )
        score = round(1.0 - (stale_files / max(total_files, 1)), 3)
        score = max(0.0, min(1.0, score))

        # Last shine timestamp
        last_shine = None
        if _SHINE_LOG.exists():
            lines = [l for l in _SHINE_LOG.read_text().splitlines() if l.strip()]
            if lines:
                try:
                    last_shine = json.loads(lines[-1]).get("ts")
                except Exception:
                    pass

        return {
            "session_files": {
                "count":          sort_data["sessions"]["total"],
                "oldest_days":    sort_data["sessions"]["oldest_days"],
                "reclaimable_mb": sort_data["sessions"]["reclaimable_mb"],
                "stale":          sort_data["sessions"]["stale"],
            },
            "artifacts": {
                "count":          sort_data["artifacts"]["total"],
                "orphaned":       sort_data["artifacts"]["stale"],
                "reclaimable_mb": sort_data["artifacts"]["reclaimable_mb"],
            },
            "weak_sessions": {
                "count": sort_data["weak_sessions"]["total"],
                "stale": sort_data["weak_sessions"]["stale"],
            },
            "5s_score":  score,
            "last_shine": last_shine,
            "total_reclaimable_mb": round(
                sort_data["sessions"]["reclaimable_mb"]
                + sort_data["artifacts"]["reclaimable_mb"], 2
            ),
        }
