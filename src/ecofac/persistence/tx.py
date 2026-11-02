# Copyright 2026 Lightibulb

"""Transaction wrappers for SQLite operations."""

import sqlite3
import uuid
from collections.abc import Callable
from typing import TypeVar

T = TypeVar("T")


def persist_atomically(conn: sqlite3.Connection, fn: Callable[[], T]) -> T:
    """Run fn inside a transaction. Uses a savepoint when nested.

    Either fn completes and its writes are committed, or an exception is
    raised and every write made by fn is rolled back.
    """
    if conn.in_transaction:
        sp = f"sp_{uuid.uuid4().hex[:8]}"
        conn.execute(f"SAVEPOINT {sp}")
        try:
            result = fn()
        except Exception:
            conn.execute(f"ROLLBACK TO SAVEPOINT {sp}")
            conn.execute(f"RELEASE SAVEPOINT {sp}")
            raise
        conn.execute(f"RELEASE SAVEPOINT {sp}")
        return result
    conn.execute("BEGIN IMMEDIATE")
    try:
        result = fn()
    except Exception:
        conn.execute("ROLLBACK")
        raise
    conn.execute("COMMIT")
    return result
