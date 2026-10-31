# Copyright 2026 Lightibulb

"""SQLite connection and initialization utilities."""

import pathlib
import sqlite3

def get_connection(db_path: str) -> sqlite3.Connection:
    """Create a connection with explicit transaction control."""
    # isolation_level=None disables implicit transactions, allowing explicit BEGIN/COMMIT
    conn = sqlite3.connect(db_path, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn

def init_db(conn: sqlite3.Connection) -> None:
    """Initialize the database with the schema."""
    schema_path = pathlib.Path(__file__).parent / "schema.sql"
    schema_sql = schema_path.read_text(encoding="utf-8")
    conn.executescript(schema_sql)
