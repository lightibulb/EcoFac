# Copyright 2026 Lightibulb
"""FastAPI dependencies."""
import pathlib
import sqlite3
from collections.abc import Generator

from ecofac.persistence.database import get_connection, init_db

DB_PATH = str(pathlib.Path("ecofac_api.db").resolve())

_initialized: set[str] = set()


def get_db() -> Generator[sqlite3.Connection, None, None]:
    """Yield a database connection for the request (schema init cached)."""
    conn = get_connection(DB_PATH)
    if DB_PATH not in _initialized:
        init_db(conn)
        _initialized.add(DB_PATH)
    try:
        yield conn
    finally:
        conn.close()
