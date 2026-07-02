"""
Health data tools for Rama and Matsya.

Storage: SQLite at ~/.narad/health.db (same pattern as finance.db)
Drug info: RxNorm free REST API (no auth required)
"""

from __future__ import annotations

import json
import sqlite3
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path

_DB_PATH = Path.home() / ".narad" / "health.db"


def _get_conn() -> sqlite3.Connection:
    _DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(_DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("""
        CREATE TABLE IF NOT EXISTS symptom_log (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT    NOT NULL,
            symptom   TEXT    NOT NULL,
            severity  INTEGER NOT NULL,
            notes     TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS medication_reminders (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            created   TEXT NOT NULL,
            med_name  TEXT NOT NULL,
            dose      TEXT NOT NULL,
            schedule  TEXT NOT NULL,
            active    INTEGER DEFAULT 1
        )
    """)
    conn.commit()
    return conn


def log_symptom(symptom: str, severity: int, notes: str = "") -> dict:
    """Log a health symptom to the local health database.

    Args:
        symptom:  Name of the symptom (e.g. 'headache', 'back pain')
        severity: Severity on a 1-10 scale
        notes:    Optional context (location, character, triggers)
    Returns:
        Confirmation dict with id and timestamp
    """
    if not 1 <= severity <= 10:
        return {"status": "error", "message": "severity must be between 1 and 10"}
    ts = datetime.now().isoformat(timespec="seconds")
    conn = _get_conn()
    cur = conn.execute(
        "INSERT INTO symptom_log (timestamp, symptom, severity, notes) VALUES (?, ?, ?, ?)",
        (ts, symptom.strip(), int(severity), notes.strip()),
    )
    conn.commit()
    row_id = cur.lastrowid
    conn.close()
    return {
        "status": "logged",
        "id": row_id,
        "timestamp": ts,
        "symptom": symptom,
        "severity": severity,
        "notes": notes,
    }


def set_medication_reminder(med_name: str, dose: str, schedule: str) -> dict:
    """Create a medication reminder entry in the health database.

    Args:
        med_name: Medication name (e.g. 'Aspirin')
        dose:     Dose amount and unit (e.g. '100mg')
        schedule: Frequency and timing (e.g. 'once daily, 8am')
    Returns:
        Confirmation dict with id and details
    """
    ts = datetime.now().isoformat(timespec="seconds")
    conn = _get_conn()
    cur = conn.execute(
        "INSERT INTO medication_reminders (created, med_name, dose, schedule) VALUES (?, ?, ?, ?)",
        (ts, med_name.strip(), dose.strip(), schedule.strip()),
    )
    conn.commit()
    row_id = cur.lastrowid
    conn.close()
    return {
        "status": "set",
        "id": row_id,
        "med_name": med_name,
        "dose": dose,
        "schedule": schedule,
        "created": ts,
    }


def get_health_log(days: int = 7, anomaly_detection: bool = False, symptom_filter: str = "") -> dict:
    """Retrieve symptom log entries from the past N days.

    Args:
        days:              Number of days to look back (default 7)
        anomaly_detection: If True, run statistical anomaly detection on each symptom type
                           present in the results and append anomaly insights.
        symptom_filter:    If set, only return entries for this symptom (partial match).
    Returns:
        Dict with entries list, summary statistics, and optional anomaly analysis.
    """
    from datetime import timedelta
    conn = _get_conn()
    cutoff_dt = (datetime.now() - timedelta(days=days)).isoformat(timespec="seconds")

    query = "SELECT id, timestamp, symptom, severity, notes FROM symptom_log WHERE timestamp >= ?"
    params: list = [cutoff_dt]
    if symptom_filter:
        query += " AND symptom LIKE ?"
        params.append(f"%{symptom_filter.lower()}%")
    query += " ORDER BY timestamp DESC"

    rows = conn.execute(query, params).fetchall()
    meds = conn.execute(
        "SELECT med_name, dose, schedule FROM medication_reminders WHERE active = 1"
    ).fetchall()
    conn.close()

    entries = [
        {
            "id": r["id"],
            "timestamp": r["timestamp"],
            "symptom": r["symptom"],
            "severity": r["severity"],
            "notes": r["notes"] or "",
        }
        for r in rows
    ]

    avg_severity = round(sum(e["severity"] for e in entries) / len(entries), 1) if entries else None

    result = {
        "status": "ok",
        "period_days": days,
        "entries": entries,
        "count": len(entries),
        "average_severity": avg_severity,
        "active_medications": [
            {"med_name": m["med_name"], "dose": m["dose"], "schedule": m["schedule"]}
            for m in meds
        ],
    }

    if anomaly_detection and entries:
        # Run anomaly detection per distinct symptom type in the result set
        try:
            from health_anomaly import detect_health_anomalies
            seen: set[str] = set()
            anomaly_results: dict[str, dict] = {}
            for entry in entries:
                sym = entry["symptom"].lower()
                if sym not in seen:
                    seen.add(sym)
                    anomaly_results[sym] = detect_health_anomalies(symptom=sym, days=days)
            result["anomaly_analysis"] = anomaly_results
        except Exception:
            result["anomaly_analysis"] = {"status": "unavailable"}

    return result


def query_rxnorm(drug_name: str) -> dict:
    """Look up drug information via the free RxNorm REST API (no auth needed).

    Args:
        drug_name: Medication name to look up (e.g. 'aspirin', 'metformin')
    Returns:
        Dict with drug class, uses, and interaction flags if available
    """
    base = "https://rxnav.nlm.nih.gov/REST"
    name_enc = urllib.parse.quote(drug_name.strip())

    try:
        # Step 1: Get RxCUI
        url_cui = f"{base}/rxcui.json?name={name_enc}&search=1"
        with urllib.request.urlopen(url_cui, timeout=8) as resp:
            data = json.loads(resp.read())
        cui_list = (
            data.get("idGroup", {}).get("rxnormId") or []
        )
        if not cui_list:
            return {
                "status": "not_found",
                "drug_name": drug_name,
                "message": f"No RxNorm entry found for '{drug_name}'. Check spelling or try the generic name.",
            }
        rxcui = cui_list[0]

        # Step 2: Get drug properties
        url_props = f"{base}/rxcui/{rxcui}/allProperties.json?prop=all"
        with urllib.request.urlopen(url_props, timeout=8) as resp:
            props_data = json.loads(resp.read())

        props = props_data.get("propConceptGroup", {}).get("propConcept", [])
        prop_map: dict[str, str] = {}
        for p in props:
            prop_map[p.get("propName", "")] = p.get("propValue", "")

        # Step 3: Drug classes
        url_class = f"{base}/rxcui/{rxcui}/classes.json"
        classes: list[str] = []
        try:
            with urllib.request.urlopen(url_class, timeout=8) as resp:
                class_data = json.loads(resp.read())
            for grp in class_data.get("rxclassDrugInfoList", {}).get("rxclassDrugInfo", []):
                cls = grp.get("rxclassMinConceptItem", {}).get("className", "")
                if cls and cls not in classes:
                    classes.append(cls)
        except Exception:
            pass

        return {
            "status": "ok",
            "drug_name": drug_name,
            "rxcui": rxcui,
            "full_name": prop_map.get("RxNorm Name", drug_name),
            "drug_classes": classes[:5],
            "synonym": prop_map.get("Synonym", ""),
            "disclaimer": "For dosage and medical guidance, consult your prescribing physician.",
        }

    except Exception as exc:
        return {
            "status": "error",
            "drug_name": drug_name,
            "message": f"RxNorm lookup failed: {exc}. Drug info unavailable.",
        }
