"""
Health anomaly detection for Rama's health log analysis.

Primary path: statistical z-score analysis (no extra dependencies).
Enhanced path: IBM Granite TinyTimeMixer (tsfm-public) zero-shot forecasting,
activated when `pip install tsfm-public` is present.

Adapted from IBM/AssetOpsBench src/servers/tsfm/main.py (Apache 2.0).

Usage:
    from health_anomaly import detect_health_anomalies
    result = detect_health_anomalies(user_id="default", symptom="headache", days=30)
    # {"anomalies": [{"date": ..., "severity": 7, "zscore": 2.4}], "trend": "worsening"}
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

_DB_PATH = Path.home() / ".narad" / "health.db"
_ZSCORE_THRESHOLD = 2.0   # flag points more than 2 std-devs above the mean


def _load_symptom_series(symptom: str, days: int) -> list[dict[str, Any]]:
    """Load severity time series for a named symptom from health.db."""
    if not _DB_PATH.exists():
        return []
    cutoff = (datetime.now() - timedelta(days=days)).isoformat(timespec="seconds")
    try:
        conn = sqlite3.connect(str(_DB_PATH))
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT timestamp, severity FROM symptom_log "
            "WHERE symptom LIKE ? AND timestamp >= ? ORDER BY timestamp ASC",
            (f"%{symptom.lower()}%", cutoff),
        ).fetchall()
        conn.close()
        return [{"date": r["timestamp"], "severity": r["severity"]} for r in rows]
    except Exception:
        return []


def _zscore_anomalies(series: list[dict]) -> list[dict[str, Any]]:
    """Detect anomalies via z-score. Returns entries with |z| >= threshold."""
    if len(series) < 3:
        return []
    values = [p["severity"] for p in series]
    mean = sum(values) / len(values)
    variance = sum((v - mean) ** 2 for v in values) / len(values)
    std = variance ** 0.5
    if std == 0:
        return []
    anomalies = []
    for point in series:
        z = (point["severity"] - mean) / std
        if abs(z) >= _ZSCORE_THRESHOLD:
            anomalies.append({
                "date":     point["date"],
                "severity": point["severity"],
                "zscore":   round(z, 2),
            })
    return anomalies


def _compute_trend(series: list[dict]) -> str:
    """Compare first-half vs second-half mean severity to determine trend."""
    if len(series) < 4:
        return "insufficient_data"
    mid = len(series) // 2
    first_mean = sum(p["severity"] for p in series[:mid]) / mid
    second_mean = sum(p["severity"] for p in series[mid:]) / (len(series) - mid)
    delta = second_mean - first_mean
    if delta > 1.0:
        return "worsening"
    elif delta < -1.0:
        return "improving"
    return "stable"


def _tsad_anomalies(series: list[dict]) -> list[dict[str, Any]] | None:
    """Granite TTM zero-shot anomaly detection. Returns None if tsfm not available."""
    try:
        import pandas as pd  # noqa: F401 — presence check
        from tsfm_public import TimeSeriesForecastingPipeline  # type: ignore  # noqa
        from tsfm_public.models.tinytimemixer import TinyTimeMixerForPrediction  # type: ignore  # noqa

        import pandas as _pd
        df = _pd.DataFrame(series)
        df["date"] = _pd.to_datetime(df["date"])
        df = df.sort_values("date").set_index("date")

        if len(df) < 8:
            return None

        model = TinyTimeMixerForPrediction.from_pretrained(
            "ibm/TTM-r2",
            context_length=min(len(df), 32),
            prediction_length=1,
        )
        pipeline = TimeSeriesForecastingPipeline(model=model, target_columns=["severity"])
        predictions = pipeline(df)

        # Compute residuals: actual − predicted → large residual = anomaly
        residuals = (df["severity"].values - predictions["severity"].values).tolist()
        res_std = (sum(r ** 2 for r in residuals) / len(residuals)) ** 0.5 if residuals else 1.0

        anomalies = []
        for i, (point, residual) in enumerate(zip(series, residuals)):
            z = abs(residual) / res_std if res_std > 0 else 0.0
            if z >= _ZSCORE_THRESHOLD:
                anomalies.append({
                    "date":     point["date"],
                    "severity": point["severity"],
                    "zscore":   round(z, 2),
                    "method":   "granite_ttm",
                })
        return anomalies
    except (ImportError, Exception):
        return None


def detect_health_anomalies(
    symptom: str,
    days: int = 30,
    user_id: str = "default",
) -> dict[str, Any]:
    """Detect anomalies in symptom severity over the last N days.

    Args:
        symptom:  Symptom name to analyse (partial match, e.g. 'headache')
        days:     Look-back window in days (default 30)
        user_id:  Unused — single-user local DB; kept for API consistency

    Returns:
        {
          "symptom":   str,
          "period_days": int,
          "data_points": int,
          "trend":     "stable" | "worsening" | "improving" | "insufficient_data",
          "anomalies": [{"date": ..., "severity": int, "zscore": float}, ...],
          "method":    "zscore" | "granite_ttm",
          "status":    "ok" | "unavailable",
        }
    """
    series = _load_symptom_series(symptom, days)
    if not series:
        return {
            "status":      "unavailable",
            "symptom":     symptom,
            "period_days": days,
            "data_points": 0,
            "message":     f"No '{symptom}' entries in the last {days} days.",
        }

    # Try Granite TTM first; fall back to z-score
    tsad_result = _tsad_anomalies(series)
    if tsad_result is not None:
        method = "granite_ttm"
        anomalies = tsad_result
    else:
        method = "zscore"
        anomalies = _zscore_anomalies(series)

    return {
        "status":      "ok",
        "symptom":     symptom,
        "period_days": days,
        "data_points": len(series),
        "trend":       _compute_trend(series),
        "anomalies":   anomalies,
        "method":      method,
    }
