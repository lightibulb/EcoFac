# Copyright 2026 Lightibulb

"""FastAPI dependencies."""

import sqlite3
from collections.abc import Generator

from ecofac.persistence.database import get_connection, init_db

DB_PATH = "ecofac_api.db"


def get_db() -> Generator[sqlite3.Connection, None, None]:
    """Yield a database connection for the request."""
    conn = get_connection(DB_PATH)
    init_db(conn)
    try:
        yield conn
    finally:
        conn.close()
