# Copyright 2026 Lightibulb

"""SQLite connection and initialization utilities."""

import datetime as dt
import pathlib
import re
import sqlite3
import threading

BUSY_TIMEOUT_SECONDS = 30

# Columns that were added after the first public schema. ``schema.sql`` only
# uses ``CREATE TABLE IF NOT EXISTS`` so an older on-disk database would
# silently keep its old table shape; these additive ALTERs bring it forward.
# Each entry: (table, column, column DDL used in ``ALTER TABLE ... ADD``).
ADDITIVE_COLUMNS: tuple[tuple[str, str, str], ...] = (
    ("meters", "interval_minutes", "INTEGER NOT NULL DEFAULT 60"),
    ("meters", "role", "TEXT"),
    ("meters", "relative_tolerance", "REAL"),
)

# What a ready database must contain. Checked by ``check_schema``.
REQUIRED_SCHEMA: dict[str, tuple[str, ...]] = {
    "organizations": ("id", "name", "created_at"),
    "buildings": ("id", "org_id", "name", "created_at"),
    "meters": (
        "id",
        "building_id",
        "fuel_type",
        "unit",
        "interval_minutes",
        "role",
        "relative_tolerance",
    ),
    "energy_readings": (
        "id",
        "meter_id",
        "timestamp_utc",
        "value",
        "unit",
        "is_valid",
    ),
    "anomalies": ("id", "meter_id", "timestamp_utc", "detector_name"),
    "incidents": ("id", "anomaly_id", "status"),
    "decisions": ("id", "status", "request_json", "result_json"),
}

# One lock per process serialises first-time initialisation. SQLite is
# single-process by design (see docs/limitations.md), so a process-level lock
# is the right granularity.
_INIT_LOCK = threading.Lock()
_INITIALIZED: set[str] = set()


def get_connection(db_path: str) -> sqlite3.Connection:
    """Create a connection with explicit transaction control."""
    # isolation_level=None disables implicit transactions, allowing explicit BEGIN/COMMIT
    conn = sqlite3.connect(
        db_path,
        isolation_level=None,
        check_same_thread=False,
        timeout=BUSY_TIMEOUT_SECONDS,
    )
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn


def enable_wal(conn: sqlite3.Connection) -> None:
    """Switch a file-backed database to WAL so readers never block the writer."""
    conn.execute("PRAGMA journal_mode = WAL;")
    conn.execute("PRAGMA synchronous = NORMAL;")


def _table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}


def _apply_additive_columns(conn: sqlite3.Connection) -> None:
    """Add columns missing from databases created by an older release."""
    for table, column, ddl in ADDITIVE_COLUMNS:
        existing = _table_columns(conn, table)
        if existing and column not in existing:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")


def init_db(conn: sqlite3.Connection) -> None:
    """Initialize the database with the schema (idempotent)."""
    schema_path = pathlib.Path(__file__).parent / "schema.sql"
    schema_sql = schema_path.read_text(encoding="utf-8")
    conn.executescript(schema_sql)
    _apply_additive_columns(conn)
    _apply_migrations(conn)


def _apply_migrations(conn: sqlite3.Connection) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS _schema_version (
            version INTEGER PRIMARY KEY,
            applied_at TEXT NOT NULL
        )
    """)
    row = conn.execute("SELECT MAX(version) FROM _schema_version").fetchone()
    current_version = row[0] if row[0] is not None else 0

    migrations_dir = pathlib.Path(__file__).resolve().parents[3] / "migrations"
    if not migrations_dir.exists():
        return

    sql_files = sorted(migrations_dir.glob("*.sql"))

    meter_cols = _table_columns(conn, "meters")
    if (
        current_version == 0
        and "relative_tolerance" in meter_cols
        and "interval_minutes" in meter_cols
    ):
        for f in sql_files:
            match = re.match(r"(\d+)_", f.name)
            if match:
                ver = int(match.group(1))
                conn.execute(
                    "INSERT OR IGNORE INTO _schema_version (version, applied_at) VALUES (?, ?)",
                    (ver, dt.datetime.now(dt.UTC).isoformat()),
                )
        return

    for f in sql_files:
        match = re.match(r"(\d+)_", f.name)
        if match:
            ver = int(match.group(1))
            if ver > current_version:
                sql = f.read_text(encoding="utf-8")
                lines = []
                for line in sql.splitlines():
                    alter_match = re.match(
                        r"^\s*ALTER\s+TABLE\s+(\w+)\s+ADD\s+(?:COLUMN\s+)?(\w+)",
                        line,
                        re.IGNORECASE,
                    )
                    if alter_match:
                        table, col = alter_match.group(1), alter_match.group(2)
                        existing_cols = _table_columns(conn, table)
                        if col in existing_cols:
                            continue
                    lines.append(line)
                filtered_sql = "\n".join(lines).strip()
                if filtered_sql:
                    conn.executescript(filtered_sql)
                conn.execute(
                    "INSERT INTO _schema_version (version, applied_at) VALUES (?, ?)",
                    (ver, dt.datetime.now(dt.UTC).isoformat()),
                )


def check_schema(conn: sqlite3.Connection) -> list[str]:
    """Return human-readable schema problems; an empty list means compatible."""
    problems: list[str] = []
    for table, columns in REQUIRED_SCHEMA.items():
        existing = _table_columns(conn, table)
        if not existing:
            problems.append(f"missing table: {table}")
            continue
        problems.extend(f"missing column: {table}.{col}" for col in columns if col not in existing)
    return problems


def ensure_initialized(db_path: str) -> None:
    """Initialise ``db_path`` exactly once per process, safely under concurrency.

    Concurrent cold-start requests block on the lock; the first one runs
    ``init_db`` and the rest see the path as initialised. The connection used
    for initialisation is always closed, including when initialisation fails,
    and a failed attempt is *not* recorded as done so the next request retries.
    """
    if db_path in _INITIALIZED:
        return
    with _INIT_LOCK:
        if db_path in _INITIALIZED:
            return
        if db_path != ":memory:":
            pathlib.Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        conn = get_connection(db_path)
        try:
            enable_wal(conn)
            init_db(conn)
            problems = check_schema(conn)
            if problems:
                msg = "Database schema incompatible: " + "; ".join(problems)
                raise RuntimeError(msg)
        finally:
            conn.close()
        _INITIALIZED.add(db_path)


def reset_initialization_cache() -> None:
    """Forget which databases were initialised (used by tests)."""
    with _INIT_LOCK:
        _INITIALIZED.clear()
