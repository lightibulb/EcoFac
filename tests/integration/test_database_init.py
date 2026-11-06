# Copyright 2026 Lightibulb
"""Database initialisation: concurrency, failure cleanup, schema readiness."""

import sqlite3
import threading
import time

import pytest

from ecofac.persistence import database
from ecofac.persistence.database import (
    check_schema,
    ensure_initialized,
    get_connection,
    init_db,
    reset_initialization_cache,
)


@pytest.fixture(autouse=True)
def _clean_cache():
    reset_initialization_cache()
    yield
    reset_initialization_cache()


def test_concurrent_cold_start_initialises_exactly_once(tmp_path, monkeypatch):
    calls = []
    real_init = database.init_db

    def slow_init(conn):
        calls.append(threading.get_ident())
        time.sleep(0.15)  # widen the race window
        real_init(conn)

    monkeypatch.setattr(database, "init_db", slow_init)
    db = str(tmp_path / "cold.db")
    errors = []

    def worker():
        try:
            ensure_initialized(db)
        except Exception as exc:  # surfaced in `errors` below
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(12)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert errors == []
    assert len(calls) == 1, "init_db must run once, not once per racing request"
    conn = get_connection(db)
    assert check_schema(conn) == []
    conn.close()


def test_failed_initialisation_closes_connection_and_can_retry(tmp_path, monkeypatch):
    opened = []
    real_get = database.get_connection

    def tracking_get(path):
        conn = real_get(path)
        opened.append(conn)
        return conn

    def boom(_conn):
        msg = "disk on fire"
        raise sqlite3.OperationalError(msg)

    monkeypatch.setattr(database, "get_connection", tracking_get)
    monkeypatch.setattr(database, "init_db", boom)
    db = str(tmp_path / "fail.db")

    with pytest.raises(sqlite3.OperationalError, match="disk on fire"):
        ensure_initialized(db)

    assert len(opened) == 1
    with pytest.raises(sqlite3.ProgrammingError):  # closed connection
        opened[0].execute("SELECT 1")
    assert db not in database._INITIALIZED  # noqa: SLF001 - failure is not cached

    monkeypatch.setattr(database, "init_db", init_db)
    ensure_initialized(db)  # the next request retries and succeeds
    assert db in database._INITIALIZED  # noqa: SLF001


def test_incompatible_schema_is_reported_not_swallowed(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "init_db", lambda _conn: None)  # creates nothing
    with pytest.raises(RuntimeError, match="incompatible"):
        ensure_initialized(str(tmp_path / "empty.db"))


def test_check_schema_detects_missing_tables_and_columns():
    conn = get_connection(":memory:")
    assert any("missing table: meters" in p for p in check_schema(conn))
    conn.execute(
        "CREATE TABLE meters (id TEXT PRIMARY KEY, building_id TEXT, "
        "fuel_type TEXT, unit TEXT, created_at TEXT)"
    )
    problems = check_schema(conn)
    assert "missing column: meters.interval_minutes" in problems
    assert "missing column: meters.role" in problems
    conn.close()


def test_init_db_upgrades_an_older_meters_table():
    conn = get_connection(":memory:")
    conn.execute(
        "CREATE TABLE meters (id TEXT PRIMARY KEY, building_id TEXT NOT NULL, "
        "fuel_type TEXT NOT NULL, unit TEXT NOT NULL, created_at TEXT NOT NULL)"
    )
    conn.execute("INSERT INTO meters VALUES ('m1','b1','elec','kWh','2026-01-01T00:00:00Z')")
    init_db(conn)
    cols = {r[1] for r in conn.execute("PRAGMA table_info(meters)")}
    assert {"interval_minutes", "role", "relative_tolerance"} <= cols
    assert conn.execute("SELECT interval_minutes FROM meters").fetchone()[0] == 60
    assert check_schema(conn) == []
    init_db(conn)  # idempotent
    conn.close()
