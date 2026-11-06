# Copyright 2026 Lightibulb
"""FastAPI dependencies."""

import hmac
import os
import sqlite3
from collections.abc import Generator
from typing import Annotated

from fastapi import Header, HTTPException

from ecofac.persistence import database
from ecofac.persistence.database import BUSY_TIMEOUT_SECONDS, ensure_initialized


def get_db_path() -> str:
    """Return the database path, evaluated dynamically per call."""
    return os.environ.get("ECOFAC_DB_PATH", "ecofac_api.db")


# Kept for backwards compatibility with tests that import DB_PATH
DB_PATH = get_db_path()

# Kept for backwards compatibility with tests that reset it; the authoritative
# cache lives in ``persistence.database`` behind a lock.
_initialized: set[str] = database._INITIALIZED  # noqa: SLF001


def get_db() -> Generator[sqlite3.Connection, None, None]:
    """Yield a database connection for the request.

    First-time initialisation is serialised by ``ensure_initialized`` so
    concurrent cold-start requests cannot race through ``init_db``. A failed
    initialisation closes its own connection and surfaces as an error; the
    request connection below is only opened after initialisation succeeded.
    """
    db_path = get_db_path()
    ensure_initialized(db_path)
    conn = sqlite3.connect(
        db_path,
        isolation_level=None,
        check_same_thread=False,
        timeout=BUSY_TIMEOUT_SECONDS,
    )
    try:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON;")
        yield conn
    finally:
        conn.close()


def require_write_access(
    x_ecofac_token: Annotated[str | None, Header()] = None,
) -> None:
    """Env-gated write auth. When ECOFAC_WRITE_TOKEN is unset, no auth is required."""
    token = os.environ.get("ECOFAC_WRITE_TOKEN", "").strip()
    if not token:
        return
    if not x_ecofac_token or not hmac.compare_digest(x_ecofac_token, token):
        raise HTTPException(
            status_code=401,
            detail="Missing or invalid X-EcoFac-Token header",
        )
