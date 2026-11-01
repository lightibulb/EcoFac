# Copyright 2026 Lightibulb
"""FastAPI dependencies."""
import os
import sqlite3
from collections.abc import Generator

from ecofac.persistence.database import init_db

# On Render, set ECOFAC_DB_PATH to a file on the mounted persistent disk
# (e.g. /opt/render/data/ecofac_api.db) so data survives redeploys.
DB_PATH = os.environ.get("ECOFAC_DB_PATH", "ecofac_api.db")

_initialized: set[str] = set()


def get_db() -> Generator[sqlite3.Connection, None, None]:
    """Yield a database connection for the request."""
    conn = sqlite3.connect(
        DB_PATH, isolation_level=None, check_same_thread=False, timeout=30
    )
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    if DB_PATH not in _initialized:
        init_db(conn)
        _initialized.add(DB_PATH)
    try:
        yield conn
    finally:
        conn.close()
