"""
Health data tools for Rama and Matsya.

Storage: profile-isolated SQLite (the legacy default remains ~/.narad/health.db)
Drug info: RxNorm free REST API (no auth required)
"""

from __future__ import annotations

import json
import sqlite3
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path

try:
    from narad_config import HEALTH_DB as _DB_PATH
except ImportError:  # standalone use without narad_paths bootstrap
    _DB_PATH = Path.home() / ".narad" / "health.db"


def _get_conn() -> sqlite3.Connection:
    from profile_context import profile_data_path

    db_path = profile_data_path("health.db", legacy_default=_DB_PATH)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
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
    # Values a person confirmed from a lab report (document_review); the
    # review and item ids lead back to the page crop they were read from.
    conn.execute("""
        CREATE TABLE IF NOT EXISTS lab_results (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            created          TEXT NOT NULL,
            test_date        TEXT NOT NULL,
            test_name        TEXT NOT NULL,
            test_key         TEXT NOT NULL,
            value            TEXT NOT NULL,
            value_num        REAL,
            unit             TEXT NOT NULL DEFAULT '',
            ref_range        TEXT NOT NULL DEFAULT '',
            flag             TEXT NOT NULL DEFAULT '',
            lab_name         TEXT NOT NULL DEFAULT '',
            source_review_id TEXT NOT NULL DEFAULT '',
            source_item_id   TEXT NOT NULL DEFAULT ''
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS lab_results_key_date ON lab_results (test_key, test_date)")
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


# Report labels for the same test differ between labs; a canonical key lets
# "how has my HbA1c changed?" find every report. Checked in order, so HbA1c
# ("glycated haemoglobin") wins over plain haemoglobin.
_LAB_ALIASES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("hba1c", ("hba1c", "hb a1c", "a1c", "glycated haemoglobin", "glycated hemoglobin",
               "glycosylated haemoglobin", "glycosylated hemoglobin")),
    ("fasting_glucose", ("fasting blood sugar", "fbs", "fasting plasma glucose", "glucose fasting",
                         "blood sugar fasting", "fasting glucose")),
    ("pp_glucose", ("post prandial", "postprandial", "ppbs", "pp blood sugar")),
    ("tsh", ("tsh", "thyroid stimulating hormone")),
    ("vitamin_d", ("vitamin d", "25 oh vitamin d", "25 hydroxy vitamin d", "vit d")),
    ("vitamin_b12", ("vitamin b12", "vit b12", "cyanocobalamin")),
    ("non_hdl", ("non hdl", "non hdl cholesterol")),
    ("ldl", ("ldl", "ldl cholesterol")),
    ("hdl", ("hdl", "hdl cholesterol")),
    ("triglycerides", ("triglycerides", "triglyceride")),
    ("total_cholesterol", ("total cholesterol", "cholesterol total", "serum cholesterol", "cholesterol")),
    ("creatinine", ("creatinine", "serum creatinine")),
    ("mchc", ("mchc", "mean corpuscular haemoglobin concentration", "mean corpuscular hemoglobin concentration")),
    ("mch", ("mch", "mean corpuscular haemoglobin", "mean corpuscular hemoglobin")),
    ("haemoglobin", ("haemoglobin", "hemoglobin", "hb", "hgb")),
)


def lab_test_key(test_name: str) -> str:
    """Canonical key for a lab test label (e.g. "Glycated Haemoglobin" → "hba1c")."""
    import re

    normalised = " ".join(re.sub(r"[^0-9a-z]+", " ", (test_name or "").lower()).split())
    for key, aliases in _LAB_ALIASES:
        for alias in aliases:
            # Two-letter aliases ("hb") only as the whole label; longer ones as words.
            if normalised == alias or (len(alias) >= 3 and f" {alias} " in f" {normalised} "):
                return key
    return normalised.replace(" ", "_")[:60] or "unknown"


def record_lab_result(
    test_name: str,
    value: str,
    test_date: str,
    *,
    value_num: float | None = None,
    unit: str = "",
    ref_range: str = "",
    flag: str = "",
    lab_name: str = "",
    source_review_id: str = "",
    source_item_id: str = "",
) -> int:
    """Store one confirmed lab value. Only document_review's save calls this."""
    conn = _get_conn()
    cur = conn.execute(
        "INSERT INTO lab_results (created, test_date, test_name, test_key, value, value_num, unit, "
        "ref_range, flag, lab_name, source_review_id, source_item_id) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            datetime.now().isoformat(timespec="seconds"), test_date, test_name.strip(),
            lab_test_key(test_name), str(value).strip(), value_num, unit.strip(), ref_range.strip(),
            flag, lab_name.strip(), source_review_id, source_item_id,
        ),
    )
    conn.commit()
    row_id = int(cur.lastrowid)
    conn.close()
    return row_id


def get_lab_results(test_name: str = "", days: int = 730) -> dict:
    """Lab values confirmed from the person's own reports, oldest first, with a trend per test.

    Use for "how has my HbA1c changed?" or "show my last cholesterol results".
    Values are exactly as confirmed from the report; flags only say whether a
    value was outside the range printed on that report. Never diagnose.

    Args:
        test_name: Test to look up (e.g. "HbA1c", "TSH", "fasting sugar"); empty for all tests.
        days:      How far back to look (default 730, about two years).
    Returns:
        Dict with entries (date, test, value, unit, range, flag, source) and,
        per test, the first and latest values and the change between them.
    """
    from datetime import timedelta

    cutoff = (datetime.now() - timedelta(days=max(1, int(days)))).strftime("%Y-%m-%d")
    query = "SELECT * FROM lab_results WHERE test_date >= ?"
    params: list = [cutoff]
    if test_name.strip():
        query += " AND (test_key = ? OR test_name LIKE ?)"
        params += [lab_test_key(test_name), f"%{test_name.strip()}%"]
    query += " ORDER BY test_date ASC, id ASC"
    conn = _get_conn()
    rows = conn.execute(query, params).fetchall()
    conn.close()

    entries = [
        {
            "id": r["id"],
            "date": r["test_date"],
            "test": r["test_name"],
            "test_key": r["test_key"],
            "value": r["value"],
            "value_num": r["value_num"],
            "unit": r["unit"],
            "reference_range": r["ref_range"],
            "flag": r["flag"],
            "lab": r["lab_name"],
            "source_review_id": r["source_review_id"],
            "source_crop": (
                f"/documents/reviews/{r['source_review_id']}/items/{r['source_item_id']}/crop"
                if r["source_review_id"] and r["source_item_id"] else ""
            ),
        }
        for r in rows
    ]
    trends: dict[str, dict] = {}
    for entry in entries:
        trend = trends.setdefault(entry["test_key"], {"test": entry["test"], "count": 0, "values": []})
        trend["count"] += 1
        trend["values"].append({"date": entry["date"], "value": entry["value"], "unit": entry["unit"],
                                "flag": entry["flag"]})
        if entry["value_num"] is not None:
            trend.setdefault("first", entry)
            trend["latest"] = entry
    for trend in trends.values():
        first, latest = trend.pop("first", None), trend.pop("latest", None)
        if first and latest and first is not latest and first["unit"] == latest["unit"]:
            trend["change"] = round(latest["value_num"] - first["value_num"], 3)
            trend["from"] = {"date": first["date"], "value": first["value"]}
            trend["to"] = {"date": latest["date"], "value": latest["value"]}
    message = (
        f"{len(entries)} confirmed lab value(s) since {cutoff}."
        if entries else
        "No confirmed lab values yet. Share a lab report photo or PDF and confirm its values first."
    )
    return {
        "status": "ok",
        "since": cutoff,
        "count": len(entries),
        "entries": entries,
        "trends": trends,
        "message": message,
        "note": "Values as printed on each report; flags compare with that report's own range. Not a diagnosis.",
    }


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
