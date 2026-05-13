"""
Personal finance data layer — cross-domain skill for Vamana / Rama / Buddha.

Data sources:
  CSV import  — HDFC / ICICI / Axis / SBI bank statement exports (credit + debit)
  Gmail IMAP  — bank transaction alert emails + CRED / INDMoney / Groww digests

Storage: SQLite at ~/.narad/finance.db (auto-created, auto-migrated)

Env vars (reuses existing email credentials — zero new setup required):
  EMAIL_ADDRESS        — Gmail address for IMAP login
  EMAIL_APP_PASSWORD   — Gmail app password (same as Krishna's SMTP)
  EMAIL_IMAP_HOST      — default: imap.gmail.com
  EMAIL_IMAP_PORT      — default: 993
"""
from __future__ import annotations

import csv
import email as _email_lib
import hashlib
import imaplib
import os
import re
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

# ── DB path ──────────────────────────────────────────────────────────────────

_DB_PATH = Path.home() / ".narad" / "finance.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS transactions (
    id        TEXT PRIMARY KEY,
    date      TEXT NOT NULL,
    amount    REAL NOT NULL,
    merchant  TEXT NOT NULL,
    category  TEXT NOT NULL DEFAULT 'Other',
    account   TEXT NOT NULL DEFAULT '',
    bank      TEXT NOT NULL DEFAULT '',
    card_type TEXT NOT NULL DEFAULT '',
    source    TEXT NOT NULL DEFAULT '',
    raw       TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS accounts (
    name       TEXT PRIMARY KEY,
    type       TEXT NOT NULL DEFAULT 'savings',
    bank       TEXT NOT NULL DEFAULT '',
    balance    REAL NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS budgets (
    category      TEXT PRIMARY KEY,
    monthly_limit REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS goals (
    name        TEXT PRIMARY KEY,
    target      REAL NOT NULL,
    target_date TEXT NOT NULL,
    current     REAL NOT NULL DEFAULT 0,
    notes       TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS sync_state (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


def _db() -> sqlite3.Connection:
    _DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(_DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.executescript(_SCHEMA)
    conn.commit()
    return conn


def _txn_id(date: str, amount: float, merchant: str, account: str) -> str:
    raw = f"{date}|{amount:.2f}|{merchant.lower().strip()}|{account.lower().strip()}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


# ── Auto-categorisation ───────────────────────────────────────────────────────

_CATEGORY_KEYWORDS: dict[str, list[str]] = {
    "Food":          ["swiggy", "zomato", "blinkit", "bigbasket", "grofers", "dunzo",
                      "cafe", "restaurant", "mcdonald", "domino", "subway", "kfc",
                      "burger king", "pizza", "instamart", "zepto"],
    "Transport":     ["uber", "ola", "rapido", "metro", "petrol", "hpcl", "iocl",
                      "shell", "fuel", "auto", "cab", "bmtc", "best bus", "rickshaw"],
    "Shopping":      ["amazon", "flipkart", "myntra", "ajio", "nykaa", "h&m", "zara",
                      "meesho", "tata cliq", "snapdeal", "reliance digital", "croma"],
    "Entertainment": ["netflix", "spotify", "disney", "hotstar", "prime video",
                      "pvr", "bookmyshow", "youtube premium", "apple music", "jiosaavn"],
    "Bills":         ["airtel", "jio", "bsnl", "tata sky", "electricity", "bescom",
                      "msedcl", "water bill", "lpg", "gas", "broadband", "d2h",
                      "tatapower", "adani electricity"],
    "Health":        ["apollo", "medplus", "medlife", "pharmeasy", "1mg", "practo",
                      "hospital", "clinic", "pharmacy", "diagnostic", "healthians"],
    "Travel":        ["makemytrip", "goibibo", "irctc", "indigo", "air india",
                      "spicejet", "vistara", "oyo", "hotel", "cleartrip", "ixigo"],
    "Investment":    ["groww", "zerodha", "upstox", "paytm money", "indmoney",
                      "mutual fund", "sip credit", "nps", "ppf", "kuvera"],
    "Transfer":      ["neft", "imps", "rtgs", "upi-transfer", "self transfer",
                      "fund transfer"],
    "Income":        ["salary", "credit salary", "payroll"],
}


def _auto_category(merchant: str) -> str:
    m = merchant.lower()
    for category, keywords in _CATEGORY_KEYWORDS.items():
        if any(kw in m for kw in keywords):
            return category
    return "Other"


# ── CSV parsing ───────────────────────────────────────────────────────────────

def _detect_bank_format(headers: list[str]) -> tuple[str, str]:
    """Return (bank, card_type) from normalised CSV column headers."""
    h = [c.strip().lower() for c in headers]
    joined = " | ".join(h)
    if "narration" in joined and "debit amount" in joined and "closing balance" in joined:
        return "HDFC", "debit"
    if "narration" in joined and "debit amount" in joined:
        return "HDFC", "credit"
    if "transaction remarks" in joined and "amount in inr" in joined:
        return "ICICI", "credit"
    if "transaction remarks" in joined and "withdrawal amount" in joined:
        return "ICICI", "debit"
    if "particulars" in joined and "amt(inr)" in joined and "available balance" in joined:
        return "Axis", "debit"
    if "particulars" in joined and "amt(inr)" in joined:
        return "Axis", "credit"
    if "description" in joined and "ref no" in joined and "debit" in joined:
        return "SBI", "debit"
    return "Unknown", "unknown"


def _parse_amount(raw: str) -> float | None:
    cleaned = re.sub(r"[₹,\s]", "", raw.strip())
    try:
        return float(cleaned) if cleaned else None
    except ValueError:
        return None


def _parse_date(raw: str) -> str | None:
    raw = raw.strip()
    for fmt in ("%d/%m/%Y", "%d-%m-%Y", "%d-%m-%y", "%d-%b-%Y", "%d-%b-%y",
                "%d/%m/%y", "%Y-%m-%d", "%m/%d/%Y"):
        try:
            return datetime.strptime(raw, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return None


def _parse_hdfc_row(row: dict, card_type: str) -> tuple[str, float, str] | None:
    date = _parse_date(row.get("Date", ""))
    merchant = row.get("Narration", "").strip()
    if card_type == "debit":
        amt = _parse_amount(row.get("Debit Amount", ""))
    else:
        amt = _parse_amount(row.get("Debit Amount", ""))
    if not date or not merchant or not amt:
        return None
    return date, amt, merchant


def _parse_icici_row(row: dict, card_type: str) -> tuple[str, float, str] | None:
    if card_type == "credit":
        date = _parse_date(row.get("Date", ""))
        merchant = row.get("Transaction Remarks", "").strip()
        raw_amt = row.get("Amount in INR", "").strip()
        # ICICI credit: "1,234.56 Dr" or "1,234.56 Cr"
        is_credit = raw_amt.endswith("Cr") or raw_amt.endswith("CR")
        amt = _parse_amount(raw_amt.replace("Dr", "").replace("DR", "").replace("Cr", "").replace("CR", ""))
        if is_credit:
            return None  # skip credits
    else:
        date = _parse_date(row.get("Value Date", "") or row.get("Transaction Date", ""))
        merchant = row.get("Transaction Remarks", "").strip()
        amt = _parse_amount(row.get("Withdrawal Amount (INR)", ""))
    if not date or not merchant or not amt:
        return None
    return date, amt, merchant


def _parse_axis_row(row: dict, card_type: str) -> tuple[str, float, str] | None:
    date = _parse_date(row.get("Tran Date", ""))
    merchant = row.get("PARTICULARS", "").strip()
    dr_cr = row.get("DR/CR", "").strip().upper()
    if dr_cr == "CR":
        return None
    amt = _parse_amount(row.get("AMT(INR)", ""))
    if not date or not merchant or not amt:
        return None
    return date, amt, merchant


def _parse_sbi_row(row: dict) -> tuple[str, float, str] | None:
    date = _parse_date(row.get("Txn Date", ""))
    merchant = row.get("Description", "").strip()
    amt = _parse_amount(row.get("Debit", ""))
    if not date or not merchant or not amt:
        return None
    return date, amt, merchant


# ── Gmail IMAP parsing ────────────────────────────────────────────────────────

# Sender domain → (bank, card_type, [compiled regexes returning (amount, merchant)])
_GMAIL_PARSERS: dict[str, tuple[str, str, list[re.Pattern]]] = {
    "hdfcbank.com": ("HDFC", "credit", [
        re.compile(r"Rs\.?\s*([\d,]+\.?\d*)\s+(?:has been )?debited.*?at\s+(.+?)\s+on", re.I),
    ]),
    "hdfcbanksmtp.com": ("HDFC", "debit", [
        re.compile(r"Rs\.?\s*([\d,]+\.?\d*)\s+debited from a/c.*?(?:at|for)\s+(.+?)[\.\s\n]", re.I),
    ]),
    "icicibank.com": ("ICICI", "credit", [
        re.compile(r"INR\s*([\d,]+\.?\d*).*?Credit Card.*?at\s+(.+?)\s+on", re.I),
        re.compile(r"INR\s*([\d,]+\.?\d*).*?(?:UPI[/-])(.+?)(?:\s+on|\.|$)", re.I),
    ]),
    "axisbank.com": ("Axis", "credit", [
        re.compile(r"Rs\.?\s*([\d,]+\.?\d*)\s+spent.*?at\s+(.+?)\s+on", re.I),
        re.compile(r"INR\s*([\d,]+\.?\d*)\s+debited.*?for\s+(.+?)[\.\s\n]", re.I),
    ]),
    "sbi.co.in": ("SBI", "debit", [
        re.compile(r"INR\s*([\d,]+\.?\d*)\s+on\s+[\d/\-]+\s+for\s+(.+?)[\.\s\n]", re.I),
    ]),
    "cred.club": ("CRED", "credit", [
        re.compile(r"bill of Rs\.?\s*([\d,]+)", re.I),
    ]),
    "indmoney.com": ("INDMoney", "investment", [
        re.compile(r"Invested\s+Rs\.?\s*([\d,]+).*?in\s+(.+?)[\.\n]", re.I),
    ]),
    "groww.in": ("Groww", "investment", [
        re.compile(r"(?:Invested|SIP of)\s+Rs\.?\s*([\d,]+).*?in\s+(.+?)[\.\n]", re.I),
    ]),
}

_AGGREGATOR_DOMAINS = {"cred.club", "indmoney.com", "groww.in"}


def _get_imap_creds() -> tuple[str, str, str, int]:
    addr = os.environ.get("EMAIL_ADDRESS", "")
    pwd = os.environ.get("EMAIL_APP_PASSWORD", "")
    host = os.environ.get("EMAIL_IMAP_HOST", "imap.gmail.com")
    port = int(os.environ.get("EMAIL_IMAP_PORT", "993"))
    return addr, pwd, host, port


def _email_body(msg) -> str:
    body = ""
    if msg.is_multipart():
        for part in msg.walk():
            ct = part.get_content_type()
            if ct in ("text/plain", "text/html"):
                try:
                    body += part.get_payload(decode=True).decode("utf-8", errors="replace")
                except Exception:
                    pass
    else:
        try:
            body = msg.get_payload(decode=True).decode("utf-8", errors="replace")
        except Exception:
            pass
    return body


# ── Public: Ingestion ─────────────────────────────────────────────────────────

def import_csv(file_path: str, bank: str = "auto") -> dict:
    """Import a bank CSV statement and store transactions locally.

    Auto-detects bank and card type from column headers when bank="auto".
    Supported: HDFC (credit+debit), ICICI (credit+debit), Axis (credit+debit), SBI (debit).
    Deduplicates by content hash — safe to re-import the same file.

    Args:
        file_path: Path to the CSV file (e.g. "~/Downloads/hdfc_statement.csv")
        bank:      "auto" to detect, or force with "HDFC"/"ICICI"/"Axis"/"SBI"

    Returns:
        status:     "ok" | "error" | "unconfigured"
        imported:   number of new transactions stored
        duplicates: number skipped as already present
        errors:     list of rows that couldn't be parsed
        bank:       detected bank name
        card_type:  detected card type
    """
    try:
        path = Path(file_path).expanduser().resolve()
        if not path.exists():
            return {"status": "error", "message": f"File not found: {path}",
                    "imported": 0, "duplicates": 0, "errors": []}

        imported = 0
        duplicates = 0
        errors: list[str] = []
        detected_bank = bank
        detected_type = "unknown"

        with open(path, newline="", encoding="utf-8-sig", errors="replace") as f:
            reader = csv.DictReader(f)
            headers = reader.fieldnames or []
            if bank == "auto":
                detected_bank, detected_type = _detect_bank_format(headers)

            conn = _db()
            for i, row in enumerate(reader):
                try:
                    parsed: tuple[str, float, str] | None = None
                    b = detected_bank.upper()
                    if b == "HDFC":
                        parsed = _parse_hdfc_row(row, detected_type)
                    elif b == "ICICI":
                        parsed = _parse_icici_row(row, detected_type)
                    elif b == "AXIS":
                        parsed = _parse_axis_row(row, detected_type)
                    elif b == "SBI":
                        parsed = _parse_sbi_row(row)

                    if not parsed:
                        continue

                    date, amount, merchant = parsed
                    txn_id = _txn_id(date, amount, merchant, detected_bank)
                    category = _auto_category(merchant)

                    try:
                        conn.execute(
                            "INSERT INTO transactions VALUES (?,?,?,?,?,?,?,?,?,?)",
                            (txn_id, date, amount, merchant, category,
                             detected_bank, detected_bank, detected_type,
                             "csv", str(row)),
                        )
                        imported += 1
                    except sqlite3.IntegrityError:
                        duplicates += 1
                except Exception as exc:
                    errors.append(f"Row {i}: {exc}")

            conn.commit()
            conn.close()

        return {
            "status":     "ok",
            "imported":   imported,
            "duplicates": duplicates,
            "errors":     errors[:10],
            "bank":       detected_bank,
            "card_type":  detected_type,
            "file_path":  str(path),
            "message":    f"Imported {imported} new transactions from {detected_bank} {detected_type} CSV. {duplicates} duplicates skipped.",
        }
    except Exception as exc:
        return {"status": "error", "message": str(exc), "imported": 0, "duplicates": 0, "errors": []}


def sync_gmail(days_back: int = 30) -> dict:
    """Pull Gmail for bank transaction alert emails and aggregator digests.

    Searches for emails from HDFC, ICICI, Axis, SBI, CRED, INDMoney, Groww.
    Auto-detects which aggregators the user has by checking if emails exist.
    Stores last_synced_at in DB to enable incremental syncs.

    Uses EMAIL_ADDRESS + EMAIL_APP_PASSWORD (same creds as email_skill.py).

    Args:
        days_back: How many days of email history to scan (default 30)

    Returns:
        status:          "ok" | "error" | "unconfigured"
        imported:        new transactions stored
        duplicates:      skipped as already present
        sources_found:   list of senders that had matching emails
        last_synced_at:  ISO timestamp of this sync
    """
    addr, pwd, host, port = _get_imap_creds()
    if not addr or not pwd:
        return {
            "status":    "unconfigured",
            "message":   "EMAIL_ADDRESS and EMAIL_APP_PASSWORD must be set in .env",
            "imported":  0,
            "duplicates": 0,
            "sources_found": [],
        }

    try:
        mail = imaplib.IMAP4_SSL(host, port)
        mail.login(addr, pwd)
        mail.select("INBOX")

        since_date = (datetime.now() - timedelta(days=days_back)).strftime("%d-%b-%Y")
        imported = 0
        duplicates = 0
        sources_found: list[str] = []
        conn = _db()

        for domain, (bank, card_type, patterns) in _GMAIL_PARSERS.items():
            search_criteria = f'(FROM "@{domain}" SINCE "{since_date}")'
            _, data = mail.search(None, search_criteria)
            msg_nums = data[0].split()
            if not msg_nums:
                continue

            sources_found.append(domain)

            for num in msg_nums:
                _, msg_data = mail.fetch(num, "(RFC822)")
                raw = msg_data[0][1]
                msg = _email_lib.message_from_bytes(raw)
                body = _email_body(msg)
                date_str = msg.get("Date", "")
                parsed_date = None
                try:
                    from email.utils import parsedate_to_datetime
                    parsed_date = parsedate_to_datetime(date_str).strftime("%Y-%m-%d")
                except Exception:
                    parsed_date = datetime.now().strftime("%Y-%m-%d")

                for pattern in patterns:
                    for m in pattern.finditer(body):
                        groups = m.groups()
                        if len(groups) >= 1:
                            raw_amt = groups[0]
                            merchant = groups[1].strip() if len(groups) >= 2 else domain
                        else:
                            continue
                        amount = _parse_amount(raw_amt)
                        if not amount:
                            continue
                        merchant = re.sub(r"\s+", " ", merchant)[:100]
                        category = _auto_category(merchant)
                        if domain in _AGGREGATOR_DOMAINS and not category or category == "Other":
                            category = "Investment" if domain in ("indmoney.com", "groww.in") else "Bills"
                        txn_id = _txn_id(parsed_date, amount, merchant, bank)
                        try:
                            conn.execute(
                                "INSERT INTO transactions VALUES (?,?,?,?,?,?,?,?,?,?)",
                                (txn_id, parsed_date, amount, merchant, category,
                                 bank, bank, card_type, "gmail", body[:200]),
                            )
                            imported += 1
                        except sqlite3.IntegrityError:
                            duplicates += 1

        # Record aggregators found in sync_state for future reference
        detected_aggregators = [d for d in sources_found if d in _AGGREGATOR_DOMAINS]
        if detected_aggregators:
            for agg in detected_aggregators:
                conn.execute(
                    "INSERT OR REPLACE INTO sync_state VALUES (?,?)",
                    (f"aggregator_{agg}", "active"),
                )

        now = datetime.now().isoformat()
        conn.execute("INSERT OR REPLACE INTO sync_state VALUES (?,?)", ("last_synced_at", now))
        conn.commit()
        conn.close()
        mail.logout()

        return {
            "status":          "ok",
            "imported":        imported,
            "duplicates":      duplicates,
            "sources_found":   sources_found,
            "last_synced_at":  now,
            "message":         (
                f"Synced {imported} new transactions from {len(sources_found)} source(s): "
                f"{', '.join(sources_found) or 'none found'}. {duplicates} duplicates skipped."
            ),
        }
    except imaplib.IMAP4.error as exc:
        return {"status": "error", "message": f"IMAP error: {exc}", "imported": 0,
                "duplicates": 0, "sources_found": []}
    except Exception as exc:
        return {"status": "error", "message": str(exc), "imported": 0,
                "duplicates": 0, "sources_found": []}


# ── Public: Queries (read-only, shared across Vamana + Rama + Buddha) ─────────

def _period_to_dates(period: str) -> tuple[str, str, str]:
    """Return (start_date, end_date, label) for a period string."""
    now = datetime.now()
    if period == "this_month":
        start = now.replace(day=1).strftime("%Y-%m-%d")
        end = now.strftime("%Y-%m-%d")
        label = now.strftime("%B %Y")
    elif period == "last_month":
        first_this = now.replace(day=1)
        last_month_end = first_this - timedelta(days=1)
        start = last_month_end.replace(day=1).strftime("%Y-%m-%d")
        end = last_month_end.strftime("%Y-%m-%d")
        label = last_month_end.strftime("%B %Y")
    elif period == "last_30_days":
        start = (now - timedelta(days=30)).strftime("%Y-%m-%d")
        end = now.strftime("%Y-%m-%d")
        label = "last 30 days"
    elif period == "this_year":
        start = now.replace(month=1, day=1).strftime("%Y-%m-%d")
        end = now.strftime("%Y-%m-%d")
        label = str(now.year)
    elif re.match(r"^\d{4}-\d{2}$", period):
        year, month = int(period[:4]), int(period[5:])
        import calendar
        last_day = calendar.monthrange(year, month)[1]
        start = f"{period}-01"
        end = f"{period}-{last_day:02d}"
        label = datetime(year, month, 1).strftime("%B %Y")
    else:
        start = (now - timedelta(days=30)).strftime("%Y-%m-%d")
        end = now.strftime("%Y-%m-%d")
        label = "last 30 days"
    return start, end, label


def get_spending(
    period: str = "this_month",
    category: str | None = None,
    account: str | None = None,
) -> dict:
    """Aggregate spending for a period, optionally filtered by category or account.

    Args:
        period:   "this_month" | "last_month" | "last_30_days" | "this_year" | "YYYY-MM"
        category: Filter to a single category (e.g. "Food", "Transport")
        account:  Filter to a specific bank/account name

    Returns:
        status:            "ok" | "error"
        total:             total spend (INR)
        by_category:       {category: total_amount}
        transaction_count: number of transactions
        period_label:      human-readable period
    """
    try:
        start, end, label = _period_to_dates(period)
        conn = _db()
        params: list[Any] = [start, end]
        where = "WHERE date >= ? AND date <= ?"
        if category:
            where += " AND category = ?"
            params.append(category)
        if account:
            where += " AND bank = ?"
            params.append(account)

        rows = conn.execute(
            f"SELECT category, SUM(amount) as total, COUNT(*) as cnt "
            f"FROM transactions {where} GROUP BY category ORDER BY total DESC",
            params,
        ).fetchall()

        total = sum(r["total"] for r in rows)
        by_cat = {r["category"]: round(r["total"], 2) for r in rows}
        count = sum(r["cnt"] for r in rows)
        conn.close()

        return {
            "status":            "ok",
            "total":             round(total, 2),
            "by_category":       by_cat,
            "transaction_count": count,
            "period_label":      label,
            "message":           f"₹{total:,.0f} spent in {label} across {count} transactions.",
        }
    except Exception as exc:
        return {"status": "error", "message": str(exc), "total": 0, "by_category": {}}


def get_budget_status() -> dict:
    """Compare actual spending against monthly budgets for the current month.

    Returns:
        status:          "ok" | "error"
        on_track:        list of {category, limit, spent, remaining}
        over_budget:     list of {category, limit, spent, over_by}
        under_budget:    list of {category, limit, spent, remaining}
        total_budget:    sum of all limits
        total_spent:     sum of all spending in budget categories
        no_budget_set:   True if no budgets configured yet
    """
    try:
        conn = _db()
        budgets = {r["category"]: r["monthly_limit"]
                   for r in conn.execute("SELECT * FROM budgets").fetchall()}
        if not budgets:
            conn.close()
            return {
                "status":       "ok",
                "no_budget_set": True,
                "on_track":     [],
                "over_budget":  [],
                "under_budget": [],
                "total_budget": 0,
                "total_spent":  0,
                "message":      "No budgets set yet. Use set_budget(category, amount) to create limits.",
            }

        start, end, label = _period_to_dates("this_month")
        spending_rows = conn.execute(
            "SELECT category, SUM(amount) as total FROM transactions "
            "WHERE date >= ? AND date <= ? GROUP BY category",
            [start, end],
        ).fetchall()
        conn.close()

        actual = {r["category"]: r["total"] for r in spending_rows}
        on_track, over_budget, under_budget = [], [], []

        for cat, limit in sorted(budgets.items()):
            spent = actual.get(cat, 0.0)
            diff = limit - spent
            entry = {"category": cat, "limit": round(limit, 2), "spent": round(spent, 2)}
            if spent > limit:
                over_budget.append({**entry, "over_by": round(spent - limit, 2)})
            elif diff < limit * 0.2:
                on_track.append({**entry, "remaining": round(diff, 2)})
            else:
                under_budget.append({**entry, "remaining": round(diff, 2)})

        total_budget = sum(budgets.values())
        total_spent = sum(actual.get(c, 0) for c in budgets)
        return {
            "status":       "ok",
            "on_track":     on_track,
            "over_budget":  over_budget,
            "under_budget": under_budget,
            "total_budget": round(total_budget, 2),
            "total_spent":  round(total_spent, 2),
            "period_label": label,
            "message":      (
                f"{label}: spent ₹{total_spent:,.0f} of ₹{total_budget:,.0f} budget. "
                f"{len(over_budget)} categories over budget."
            ),
        }
    except Exception as exc:
        return {"status": "error", "message": str(exc)}


def get_financial_context() -> dict:
    """Single-call financial summary — use before any money-adjacent task.

    Provides the minimal financial context needed for planning, tradeoff analysis,
    or any cross-domain task involving money. Safe to call anytime.

    Returns:
        status:                  "ok" | "error"
        monthly_spend_estimate:  average monthly spend (last 3 months)
        top_categories:          [{category, avg_monthly}] top 5 by spend
        savings_rate_pct:        estimated savings % (requires income transactions)
        active_goals:            [{name, target, current, progress_pct, target_date}]
        over_budget_categories:  categories currently over limit
        accounts_summary:        [{name, type, balance}]
        data_freshness_days:     days since last sync
        has_data:                False if DB is empty (no transactions yet)
    """
    try:
        conn = _db()
        total_txns = conn.execute("SELECT COUNT(*) as n FROM transactions").fetchone()["n"]
        if total_txns == 0:
            conn.close()
            return {
                "status":   "ok",
                "has_data": False,
                "message":  "No financial data yet. Use import_csv() or sync_gmail() to load transactions.",
            }

        # Monthly spend average (last 3 months)
        three_months_ago = (datetime.now() - timedelta(days=90)).strftime("%Y-%m-%d")
        today = datetime.now().strftime("%Y-%m-%d")
        rows = conn.execute(
            "SELECT category, SUM(amount) as total FROM transactions "
            "WHERE date >= ? AND date <= ? AND category != 'Income' AND category != 'Transfer' "
            "GROUP BY category ORDER BY total DESC LIMIT 5",
            [three_months_ago, today],
        ).fetchall()
        total_90d = sum(r["total"] for r in rows)
        monthly_estimate = round(total_90d / 3, 2)
        top_cats = [{"category": r["category"], "avg_monthly": round(r["total"] / 3, 2)} for r in rows]

        # Income estimate
        income_rows = conn.execute(
            "SELECT SUM(amount) as total FROM transactions "
            "WHERE date >= ? AND category = 'Income'",
            [three_months_ago],
        ).fetchone()
        income_90d = income_rows["total"] or 0
        savings_rate = round(((income_90d / 3 - monthly_estimate) / max(income_90d / 3, 1)) * 100, 1) if income_90d > 0 else None

        # Goals
        goals_rows = conn.execute("SELECT * FROM goals").fetchall()
        active_goals = []
        for g in goals_rows:
            pct = round((g["current"] / g["target"]) * 100, 1) if g["target"] > 0 else 0
            active_goals.append({
                "name": g["name"], "target": g["target"], "current": g["current"],
                "progress_pct": pct, "target_date": g["target_date"],
            })

        # Over-budget categories
        budget_status = get_budget_status()
        over_budget = [e["category"] for e in budget_status.get("over_budget", [])]

        # Accounts
        accounts = [{"name": r["name"], "type": r["type"], "balance": r["balance"]}
                    for r in conn.execute("SELECT * FROM accounts").fetchall()]

        # Data freshness
        sync_row = conn.execute(
            "SELECT value FROM sync_state WHERE key = 'last_synced_at'"
        ).fetchone()
        freshness = 999
        if sync_row:
            try:
                last_sync = datetime.fromisoformat(sync_row["value"])
                freshness = (datetime.now() - last_sync).days
            except Exception:
                pass

        conn.close()
        return {
            "status":                  "ok",
            "has_data":                True,
            "monthly_spend_estimate":  monthly_estimate,
            "top_categories":          top_cats,
            "savings_rate_pct":        savings_rate,
            "active_goals":            active_goals,
            "over_budget_categories":  over_budget,
            "accounts_summary":        accounts,
            "data_freshness_days":     freshness,
            "message":                 (
                f"Avg monthly spend: ₹{monthly_estimate:,.0f}. "
                f"Top category: {top_cats[0]['category'] if top_cats else 'N/A'}. "
                + (f"Savings rate: {savings_rate}%." if savings_rate is not None else "No income data.")
            ),
        }
    except Exception as exc:
        return {"status": "error", "message": str(exc), "has_data": False}


def get_recurring_expenses() -> dict:
    """Auto-detect recurring subscriptions and EMIs from transaction history.

    Identifies merchants with 2+ transactions at similar amounts across months.

    Returns:
        status:                  "ok" | "error"
        recurring:               [{merchant, avg_amount, frequency, last_seen}]
        total_monthly_estimate:  estimated monthly recurring total
    """
    try:
        conn = _db()
        rows = conn.execute(
            "SELECT merchant, COUNT(*) as cnt, AVG(amount) as avg_amt, MAX(date) as last_seen "
            "FROM transactions "
            "WHERE date >= ? "
            "GROUP BY merchant HAVING cnt >= 2 AND avg_amt < 5000 "
            "ORDER BY avg_amt DESC",
            [(datetime.now() - timedelta(days=180)).strftime("%Y-%m-%d")],
        ).fetchall()
        conn.close()

        recurring = [
            {
                "merchant":   r["merchant"],
                "avg_amount": round(r["avg_amt"], 2),
                "occurrences": r["cnt"],
                "last_seen":  r["last_seen"],
            }
            for r in rows
        ]
        total = sum(r["avg_amount"] for r in recurring)
        return {
            "status":                 "ok",
            "recurring":              recurring,
            "total_monthly_estimate": round(total, 2),
            "message":                f"Found {len(recurring)} recurring expenses. Est. ₹{total:,.0f}/month.",
        }
    except Exception as exc:
        return {"status": "error", "message": str(exc), "recurring": []}


def get_net_worth() -> dict:
    """Return net worth from stored account balance snapshots.

    Returns:
        status:    "ok" | "error"
        total:     sum of all account balances
        by_type:   {savings: X, credit: Y, investment: Z}
        accounts:  [{name, type, bank, balance, updated_at}]
    """
    try:
        conn = _db()
        rows = conn.execute("SELECT * FROM accounts ORDER BY balance DESC").fetchall()
        conn.close()
        if not rows:
            return {
                "status":  "ok",
                "total":   0,
                "by_type": {},
                "accounts": [],
                "message": "No account balances recorded yet. Use add_balance_snapshot() to track net worth.",
            }
        by_type: dict[str, float] = {}
        accounts = []
        for r in rows:
            by_type[r["type"]] = by_type.get(r["type"], 0) + r["balance"]
            accounts.append({"name": r["name"], "type": r["type"],
                             "bank": r["bank"], "balance": r["balance"],
                             "updated_at": r["updated_at"]})
        total = sum(r["balance"] for r in rows)
        return {
            "status":   "ok",
            "total":    round(total, 2),
            "by_type":  {k: round(v, 2) for k, v in by_type.items()},
            "accounts": accounts,
            "message":  f"Net worth: ₹{total:,.0f} across {len(rows)} account(s).",
        }
    except Exception as exc:
        return {"status": "error", "message": str(exc), "total": 0}


def get_goals() -> dict:
    """List all financial goals with progress.

    Returns:
        status: "ok" | "error"
        goals:  [{name, target, current, progress_pct, target_date, on_track}]
    """
    try:
        conn = _db()
        rows = conn.execute("SELECT * FROM goals ORDER BY target_date").fetchall()
        conn.close()
        goals = []
        for r in rows:
            pct = round((r["current"] / r["target"]) * 100, 1) if r["target"] > 0 else 0
            try:
                td = datetime.strptime(r["target_date"], "%Y-%m-%d")
                months_left = max(1, (td - datetime.now()).days // 30)
                needed_pm = (r["target"] - r["current"]) / months_left
                on_track = r["current"] >= r["target"] * (1 - months_left / 12)
            except Exception:
                needed_pm = 0
                on_track = False
            goals.append({
                "name": r["name"], "target": r["target"], "current": r["current"],
                "progress_pct": pct, "target_date": r["target_date"],
                "notes": r["notes"], "on_track": on_track,
                "needed_per_month": round(needed_pm, 2),
            })
        return {
            "status":  "ok",
            "goals":   goals,
            "message": f"{len(goals)} active goal(s)." if goals else "No goals set yet.",
        }
    except Exception as exc:
        return {"status": "error", "message": str(exc), "goals": []}


# ── Public: Write tools (Vamana-only) ─────────────────────────────────────────

def set_budget(category: str, amount: float) -> dict:
    """Set a monthly spending limit for a category.

    Args:
        category: Spending category (e.g. "Food", "Transport", "Entertainment")
        amount:   Monthly limit in INR

    Returns:
        status: "ok" | "error"
    """
    try:
        conn = _db()
        conn.execute(
            "INSERT OR REPLACE INTO budgets VALUES (?,?)",
            (category.strip(), float(amount)),
        )
        conn.commit()
        conn.close()
        return {"status": "ok", "category": category, "monthly_limit": amount,
                "message": f"Budget set: ₹{amount:,.0f}/month for {category}."}
    except Exception as exc:
        return {"status": "error", "message": str(exc)}


def add_goal(name: str, target: float, target_date: str, notes: str = "") -> dict:
    """Create a financial savings goal.

    Args:
        name:        Goal name (e.g. "Europe Trip", "Emergency Fund")
        target:      Target amount in INR
        target_date: Target date in YYYY-MM-DD format
        notes:       Optional context

    Returns:
        status: "ok" | "error"
    """
    try:
        conn = _db()
        conn.execute(
            "INSERT OR REPLACE INTO goals VALUES (?,?,?,?,?)",
            (name.strip(), float(target), target_date, 0.0, notes),
        )
        conn.commit()
        conn.close()
        return {"status": "ok", "name": name, "target": target, "target_date": target_date,
                "message": f"Goal created: '{name}' — ₹{target:,.0f} by {target_date}."}
    except Exception as exc:
        return {"status": "error", "message": str(exc)}


def update_goal_progress(name: str, current_amount: float) -> dict:
    """Update the current saved amount toward a goal.

    Args:
        name:           Goal name
        current_amount: Amount saved so far in INR

    Returns:
        status:       "ok" | "error"
        progress_pct: updated progress percentage
    """
    try:
        conn = _db()
        row = conn.execute("SELECT target FROM goals WHERE name = ?", [name]).fetchone()
        if not row:
            conn.close()
            return {"status": "error", "message": f"Goal '{name}' not found."}
        conn.execute("UPDATE goals SET current = ? WHERE name = ?", [float(current_amount), name])
        conn.commit()
        conn.close()
        pct = round((current_amount / row["target"]) * 100, 1)
        return {"status": "ok", "name": name, "current": current_amount,
                "progress_pct": pct, "message": f"Goal '{name}': {pct}% complete."}
    except Exception as exc:
        return {"status": "error", "message": str(exc)}


def add_balance_snapshot(
    account: str,
    balance: float,
    account_type: str = "savings",
    bank: str = "",
) -> dict:
    """Record a point-in-time account balance for net worth tracking.

    Args:
        account:      Account name/label (e.g. "HDFC Savings", "ICICI Credit Card")
        balance:      Current balance in INR (use negative for credit card outstanding)
        account_type: "savings" | "credit" | "investment" | "loan"
        bank:         Bank name for grouping

    Returns:
        status: "ok" | "error"
    """
    try:
        conn = _db()
        now = datetime.now().isoformat()
        conn.execute(
            "INSERT OR REPLACE INTO accounts VALUES (?,?,?,?,?)",
            (account.strip(), account_type, bank, float(balance), now),
        )
        conn.commit()
        conn.close()
        return {"status": "ok", "account": account, "balance": balance,
                "message": f"Recorded ₹{balance:,.0f} for '{account}' ({account_type})."}
    except Exception as exc:
        return {"status": "error", "message": str(exc)}


def categorize_transaction(txn_id: str, category: str) -> dict:
    """Override the auto-assigned category for a transaction.

    Args:
        txn_id:   Transaction ID (from get_spending results)
        category: New category name

    Returns:
        status: "ok" | "error"
    """
    try:
        conn = _db()
        result = conn.execute(
            "UPDATE transactions SET category = ? WHERE id = ?",
            [category.strip(), txn_id],
        )
        conn.commit()
        conn.close()
        if result.rowcount == 0:
            return {"status": "error", "message": f"Transaction '{txn_id}' not found."}
        return {"status": "ok", "txn_id": txn_id, "category": category,
                "message": f"Transaction categorised as '{category}'."}
    except Exception as exc:
        return {"status": "error", "message": str(exc)}


def get_spend_patterns(months: int = 3) -> dict:
    """Analyse spending sequences and predict likely next spend category (Markov model).

    Adapted from IBM/AssetOpsBench Markov work-order transition pattern (Apache 2.0).
    Builds a category → next-category probability matrix from transaction history,
    then returns predictions for the most recent spending category.

    Args:
        months: Look-back window in months (default 3)

    Returns:
        {
          "status":          "ok" | "insufficient_data" | "error",
          "last_category":   str,
          "predictions":     [{"category": str, "probability": float, "pct": str}, ...],
          "top_sequences":   [{"from": str, "to": str, "probability": float}, ...],
          "matrix_size":     int,
          "insight":         str,   # natural-language summary
        }
    """
    try:
        cutoff = (datetime.now() - timedelta(days=months * 30)).strftime("%Y-%m-%d")
        conn = _db()
        rows = conn.execute(
            "SELECT date, category FROM transactions WHERE date >= ? ORDER BY date ASC",
            [cutoff],
        ).fetchall()
        conn.close()

        transactions = [{"date": r["date"], "category": r["category"]} for r in rows]

        if len(transactions) < 5:
            return {
                "status":  "insufficient_data",
                "message": f"Only {len(transactions)} transactions in the last {months} months. Need 5+ to build patterns.",
            }

        import sys as _sys_fp
        import pathlib as _pl_fp
        _sys_fp.path.insert(0, str(_pl_fp.Path(__file__).parent.parent / "phase-1"))
        from finance_patterns import get_pattern_insights
        result = get_pattern_insights(transactions)

        if result.get("status") == "ok":
            preds = result.get("predictions", [])
            if preds:
                top = preds[0]
                insight = (
                    f"After '{result['last_category']}' spending, you most often spend on "
                    f"'{top['category']}' ({top['pct']} of the time)."
                )
            else:
                insight = f"No strong spending sequence pattern found for '{result.get('last_category', 'Unknown')}'."
            result["insight"] = insight

        return result
    except Exception as exc:
        return {"status": "error", "message": str(exc)}
