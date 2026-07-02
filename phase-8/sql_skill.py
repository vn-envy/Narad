"""
Parashurama SQL skill — read-only database queries via SQLAlchemy.

Supports any SQLAlchemy-compatible database:
  SQLite:     sqlite:///path/to/db.sqlite  or  sqlite:////absolute/path.db
  PostgreSQL: postgresql://user:password@host:5432/dbname
  MySQL:      mysql+pymysql://user:password@host/dbname

Only SELECT statements are allowed. All DDL and DML are rejected before
the query reaches the database engine.
"""
from __future__ import annotations

import re
from pathlib import Path

_BLOCKED_KEYWORDS = {
    "update", "delete", "drop", "insert", "alter", "create",
    "truncate", "exec", "execute", "call", "merge", "replace",
    "grant", "revoke", "commit", "rollback",
}

_FIRST_KEYWORD_RE = re.compile(r"^\s*(\w+)", re.IGNORECASE)


def _check_sql_safe(sql: str) -> str | None:
    """Return an error message if sql is not a safe SELECT, else None."""
    stripped = sql.strip()
    match = _FIRST_KEYWORD_RE.match(stripped)
    if not match:
        return "Could not parse SQL statement."
    first = match.group(1).lower()
    if first != "select" and first != "with":
        return (
            f"Only SELECT (or WITH ... SELECT) statements are allowed. "
            f"Got: {first.upper()}"
        )
    # Secondary check: block forbidden keywords anywhere in the statement
    tokens = re.findall(r"\b\w+\b", stripped.lower())
    for tok in tokens:
        if tok in _BLOCKED_KEYWORDS:
            return (
                f"Blocked keyword '{tok.upper()}' found in SQL. "
                "Only read-only SELECT queries are permitted."
            )
    return None


def _expand_sqlite_path(connection_string: str) -> str:
    """Expand ~ in sqlite:/// paths."""
    if connection_string.startswith("sqlite:///"):
        raw_path = connection_string[len("sqlite:///"):]
        if raw_path.startswith("~"):
            expanded = str(Path(raw_path).expanduser())
            return "sqlite:///" + expanded
    return connection_string


def query_database(connection_string: str, sql: str, limit: int = 200) -> dict:
    """Execute a read-only SQL SELECT query against a database.

    Args:
        connection_string: SQLAlchemy connection URL.
            SQLite:     sqlite:///~/path/to/db.sqlite
            PostgreSQL: postgresql://user:pass@host:5432/dbname
            MySQL:      mysql+pymysql://user:pass@host/dbname
        sql:   A SELECT statement. UPDATE/DELETE/DROP/INSERT etc. are rejected.
        limit: Maximum rows to return (default 200, hard cap 1000).

    Returns a dict with:
        status:   "ok" | "error"
        rows:     List of dicts (column → value)
        columns:  List of column names
        row_count: Number of rows returned
        truncated: True if more rows exist beyond the limit
        message:  Summary or error description

    Workflow tip for Parashurama:
        1. Inspect schema first:
           SQLite:  SELECT name FROM sqlite_master WHERE type='table'
           Postgres/MySQL: SELECT table_name FROM information_schema.tables WHERE table_schema='public'
        2. Sample a table: SELECT * FROM table_name LIMIT 5
        3. Then run targeted analytics queries
    """
    if not connection_string or not connection_string.strip():
        return {"status": "error", "message": "connection_string is required."}
    if not sql or not sql.strip():
        return {"status": "error", "message": "sql query is required."}

    safety_error = _check_sql_safe(sql)
    if safety_error:
        return {"status": "error", "message": safety_error}

    limit = min(max(1, limit), 1000)
    connection_string = _expand_sqlite_path(connection_string)

    try:
        from sqlalchemy import create_engine, text
    except ImportError:
        return {
            "status":  "error",
            "message": "SQLAlchemy not installed. Run: pip install sqlalchemy",
        }

    try:
        engine = create_engine(
            connection_string,
            connect_args={"check_same_thread": False} if "sqlite" in connection_string else {},
        )

        is_sqlite = "sqlite" in connection_string.lower()

        with engine.connect() as conn:
            # Enforce read-only at the database level — cannot be bypassed by clever SQL
            # (unlike keyword scanning, which is only a fast early-rejection pre-check).
            if is_sqlite:
                conn.execute(text("PRAGMA query_only = 1"))
            else:  # PostgreSQL / MySQL
                conn.execute(text("SET TRANSACTION READ ONLY"))

            result = conn.execute(text(sql))
            columns = list(result.keys())

            rows = []
            truncated = False
            for i, row in enumerate(result):
                if i >= limit:
                    truncated = True
                    break
                rows.append(dict(zip(columns, row)))

        return {
            "status":    "ok",
            "columns":   columns,
            "rows":      rows,
            "row_count": len(rows),
            "truncated": truncated,
            "message":   (
                f"Returned {len(rows)} row(s)."
                + (" (truncated — more rows exist)" if truncated else "")
            ),
        }

    except Exception as exc:
        err = str(exc)
        # Sanitise connection string from error (may contain passwords)
        safe_cs = re.sub(r":[^@/]+@", ":***@", connection_string)
        return {
            "status":  "error",
            "message": f"Query failed on {safe_cs}: {err}",
        }
