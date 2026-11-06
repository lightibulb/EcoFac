# Copyright 2026 Lightibulb

"""Identifier validation and entity auto-creation for CSV imports.

Entities (organization, building, meter) are created *lazily*, one meter at a
time, and only when a row that refers to it has passed every other row-level
check. A CSV whose rows are all rejected therefore creates nothing, and the
import route runs the whole import in one transaction so a failure rolls the
entities back too.
"""

import re
import sqlite3

MAX_AUTO_ENTITIES = 500
MAX_ID_LENGTH = 100

# One definition of "a valid identifier", used by the HTTP form fields
# (``ID_PATTERN``, a pydantic/Rust regex) and by CSV rows (``_ID_RE``,
# applied with ``fullmatch`` so a trailing newline can never slip through).
ID_CHARS = r"[A-Za-z0-9._:-]"
ID_PATTERN = rf"^{ID_CHARS}+$"
_ID_RE = re.compile(rf"{ID_CHARS}+")

AUTO_ORG_ID = "o-auto"
DEFAULT_INTERVAL_MINUTES = 60  # matches meters.interval_minutes DEFAULT


def invalid_identifier_reason(building_id: str, meter_id: str) -> str | None:
    """Return why a (building, meter) pair is unusable, or ``None`` if it is fine."""
    for label, value in (("building_id", building_id), ("meter_id", meter_id)):
        if not value:
            return f"{label} is empty"
        if len(value) > MAX_ID_LENGTH:
            return f"{label} is longer than {MAX_ID_LENGTH} characters"
        if _ID_RE.fullmatch(value) is None:
            return f"{label} contains characters outside A-Z a-z 0-9 . _ : -"
    return None


def ensure_building_and_meter(
    conn: sqlite3.Connection, building_id: str, meter_id: str, now: str
) -> tuple[bool, bool]:
    """Create the building (and auto organization) and meter if missing.

    Returns ``(building_created, meter_created)``. Must run inside the import
    transaction; it never commits on its own.
    """
    building_exists = (
        conn.execute("SELECT 1 FROM buildings WHERE id = ?", (building_id,)).fetchone() is not None
    )
    if not building_exists:
        conn.execute(
            "INSERT OR IGNORE INTO organizations (id, name, created_at) VALUES (?, 'Auto Org', ?)",
            (AUTO_ORG_ID, now),
        )
        conn.execute(
            "INSERT INTO buildings (id, org_id, name, created_at) VALUES (?, ?, ?, ?)",
            (building_id, AUTO_ORG_ID, building_id, now),
        )
    cursor = conn.execute(
        "INSERT OR IGNORE INTO meters (id, building_id, fuel_type, unit, created_at) "
        "VALUES (?, ?, 'elec', 'kWh', ?)",
        (meter_id, building_id, now),
    )
    return (not building_exists, cursor.rowcount == 1)
