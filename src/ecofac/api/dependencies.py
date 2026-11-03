# Copyright 2026 Lightibulb
"""FastAPI dependencies."""
import os
import pathlib
import sqlite3
from collections.abc import Generator
from typing import Annotated

from fastapi import Header, HTTPException

from ecofac.persistence.database import BUSY_TIMEOUT_SECONDS, enable_wal, init_db

# On Render, set ECOFAC_DB_PATH to a file on the mounted persistent disk
# (e.g. /opt/render/data/ecofac_api.db) so data survives redeploys.
DB_PATH = os.environ.get("ECOFAC_DB_PATH", "ecofac_api.db")

_initialized: set[str] = set()


def get_db() -> Generator[sqlite3.Connection, None, None]:
    """Yield a database connection for the request."""
    first_use = DB_PATH not in _initialized
    if first_use:
        pathlib.Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(
        DB_PATH,
        isolation_level=None,
        check_same_thread=False,
        timeout=BUSY_TIMEOUT_SECONDS,
    )
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    if first_use:
        enable_wal(conn)
        init_db(conn)
        _initialized.add(DB_PATH)
    try:
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
    if not x_ecofac_token or x_ecofac_token != token:
        raise HTTPException(
            status_code=401,
            detail="Missing or invalid X-EcoFac-Token header",
        )
