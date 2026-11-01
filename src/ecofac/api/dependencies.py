# Copyright 2026 Lightibulb

"""FastAPI dependencies."""

import sqlite3
from collections.abc import Generator

from ecofac.persistence.database import get_connection, init_db

DB_PATH = "ecofac_api.db"


def get_db() -> Generator[sqlite3.Connection, None, None]:
    """Yield a database connection for the request."""
    # timeout=30 allows SQLite to wait for locked writes instead of failing instantly
    conn = sqlite3.connect(DB_PATH, isolation_level=None, check_same_thread=False, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    init_db(conn)
    try:
        yield conn
    finally:
        conn.close()
