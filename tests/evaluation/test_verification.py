# Copyright 2026 Lightibulb

"""Tests for the verification engine."""

import datetime as dt
import sqlite3

import pytest

from evaluation.verification import verify_intervention

from ecofac.persistence.database import init_db


@pytest.fixture
def db_conn():
    """Yield an in-memory database."""
    conn = sqlite3.connect(":memory:", isolation_level=None)
    conn.row_factory = sqlite3.Row
    init_db(conn)
    now = "2026-01-01T00:00:00Z"
    conn.execute("INSERT INTO organizations (id, name, created_at) VALUES ('o1', 'O', ?)", (now,))
    conn.execute("INSERT INTO buildings (id, org_id, name, created_at) VALUES ('b1', 'o1', 'B', ?)", (now,))
    conn.execute("INSERT INTO meters (id, building_id, fuel_type, unit, created_at) VALUES ('m1', 'b1', 'e', 'kWh', ?)", (now,))
    conn.execute("INSERT INTO data_sources (id, name, source_type, created_at) VALUES ('s1', 'S', 'csv', ?)", (now,))
    conn.execute("INSERT INTO import_batches (id, source_id, status, imported_at) VALUES ('ib1', 's1', 'started', ?)", (now,))
    yield conn
    conn.close()


def _insert_data(conn, start: dt.datetime, days: int, value: float):
    for d in range(days):
        for h in range(24):
            ts = (start + dt.timedelta(days=d, hours=h)).isoformat().replace("+00:00", "Z")
            conn.execute(
                "INSERT INTO energy_readings (id, meter_id, timestamp_utc, value, unit, source_id, batch_id, is_valid, created_at) "
                "VALUES (?, 'm1', ?, ?, 'kWh', 's1', 'ib1', 1, ?)",
                (f"r-{ts}", ts, value, ts)
            )


def test_successful_intervention(db_conn):
    """Verify a significant drop is flagged as SUCCESSFUL."""
    base_date = dt.datetime(2026, 1, 1, tzinfo=dt.UTC)
    _insert_data(db_conn, base_date, 30, 100.0) # Pre
    _insert_data(db_conn, base_date + dt.timedelta(days=31), 14, 80.0) # Post (20% drop)

    result = verify_intervention(db_conn, "m1", base_date + dt.timedelta(days=30))
    assert result.status == "SUCCESSFUL"
    assert result.percent_change == -20.0


def test_failed_intervention(db_conn):
    """Verify an increase is flagged as FAILED."""
    base_date = dt.datetime(2026, 1, 1, tzinfo=dt.UTC)
    _insert_data(db_conn, base_date, 30, 100.0)
    _insert_data(db_conn, base_date + dt.timedelta(days=31), 14, 110.0)

    result = verify_intervention(db_conn, "m1", base_date + dt.timedelta(days=30))
    assert result.status == "FAILED"


def test_inconclusive_intervention(db_conn):
    """Verify insufficient data returns INCONCLUSIVE."""
    base_date = dt.datetime(2026, 1, 1, tzinfo=dt.UTC)
    _insert_data(db_conn, base_date, 30, 100.0) # No post data

    result = verify_intervention(db_conn, "m1", base_date + dt.timedelta(days=30))
    assert result.status == "INCONCLUSIVE"
    assert result.data_completeness_pct < 80.0
