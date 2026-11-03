# Copyright 2026 Lightibulb
"""Aggregate decision metrics for the Decision Center overview.

All counts come from a single bounded SQL scan. Rows are returned raw; the
API layer enriches them with supersession indicators.
"""

import sqlite3


def aggregate_decisions(
    conn: sqlite3.Connection,
    *,
    recent_limit: int = 10,
) -> dict:
    """Return aggregate counts + latest + recent activity rows.

    Keys: total, approved, rejected, review, latest_row, recent_rows.
    ``latest_row`` is None when the table is empty; ``recent_rows`` is a list.
    """
    row = conn.execute(
        "SELECT "
        "  COUNT(*) AS total, "
        "  COALESCE(SUM(CASE WHEN status = 'APPROVE' THEN 1 ELSE 0 END), 0) "
        "    AS approved, "
        "  COALESCE(SUM(CASE WHEN status = 'REJECT' THEN 1 ELSE 0 END), 0) "
        "    AS rejected, "
        "  COALESCE(SUM(CASE WHEN status = 'REVIEW' THEN 1 ELSE 0 END), 0) "
        "    AS review "
        "FROM decisions"
    ).fetchone()

    latest_row = conn.execute(
        "SELECT * FROM decisions ORDER BY created_at DESC, id LIMIT 1"
    ).fetchone()
    recent_rows = conn.execute(
        "SELECT * FROM decisions ORDER BY created_at DESC, id LIMIT ?",
        (recent_limit,),
    ).fetchall()

    return {
        "total": int(row["total"]),
        "approved": int(row["approved"]),
        "rejected": int(row["rejected"]),
        "review": int(row["review"]),
        "latest_row": latest_row,
        "recent_rows": recent_rows,
    }
