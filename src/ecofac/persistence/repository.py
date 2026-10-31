# Copyright 2026 Lightibulb

"""CRUD operations for domain entities."""

import sqlite3

from ecofac.domain.models import EnergyReading


class DuplicateReadingError(Exception):
    """Raised when a reading for a meter at a specific timestamp already exists."""


def insert_reading(conn: sqlite3.Connection, reading: EnergyReading) -> None:
    """Insert a reading safely, rolling back on duplicate."""
    try:
        conn.execute("BEGIN")
        conn.execute(
            """
            INSERT INTO energy_readings
            (id, meter_id, timestamp_utc, value, unit, source_id, batch_id, is_valid, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                reading.id,
                reading.meter_id,
                reading.timestamp_utc,
                reading.value,
                reading.unit,
                reading.source_id,
                reading.batch_id,
                int(reading.is_valid),
                reading.created_at,
            ),
        )
        conn.execute("COMMIT")
    except sqlite3.IntegrityError as e:
        conn.execute("ROLLBACK")
        msg = f"Reading for meter {reading.meter_id} at {reading.timestamp_utc} already exists."
        raise DuplicateReadingError(msg) from e
