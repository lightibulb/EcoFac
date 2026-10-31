#!/usr/bin/env bash
set -euo pipefail

echo "==> 1. Updating pyproject.toml Ruff ignores..."
# Add PLR0913 (too many args for dataclasses) to global ignore
sed -i '/"ANN401",/a\    "PLR0913", # Too many arguments (dataclasses)' pyproject.toml

echo "==> 2. Creating Domain Models..."
cat > src/ecofac/domain/models.py <<'PY'
# Copyright 2026 Lightibulb

"""Domain models representing facilities and energy observations."""

import dataclasses
import datetime


@dataclasses.dataclass
class Organization:
    """Represents an entity that owns buildings."""

    id: str
    name: str
    created_at: datetime.datetime


@dataclasses.dataclass
class Building:
    """A physical building associated with an organization."""

    id: str
    org_id: str
    name: str
    created_at: datetime.datetime


@dataclasses.dataclass
class Meter:
    """A measurement device associated with a building."""

    id: str
    building_id: str
    fuel_type: str
    unit: str
    created_at: datetime.datetime


@dataclasses.dataclass
class DataSource:
    """Origin of imported data (e.g., manual upload, API)."""

    id: str
    name: str
    source_type: str
    created_at: datetime.datetime


@dataclasses.dataclass
class ImportBatch:
    """A specific import event from a data source."""

    id: str
    source_id: str
    status: str
    imported_at: datetime.datetime


@dataclasses.dataclass
class EnergyReading:
    """A normalized energy measurement at a specific UTC timestamp."""

    id: str
    meter_id: str
    timestamp_utc: str
    value: float
    unit: str
    source_id: str
    batch_id: str
    is_valid: bool
    created_at: str


@dataclasses.dataclass
class DataQualityIssue:
    """An issue discovered during data validation."""

    id: str
    reading_id: str | None
    batch_id: str
    issue_type: str
    description: str
    created_at: str


@dataclasses.dataclass
class Incident:
    """Tracks the lifecycle of an anomaly or quality issue."""

    id: str
    meter_id: str
    status: str
    created_at: str


@dataclasses.dataclass
class EvidenceRecord:
    """Immutable proof supporting a finding or incident."""

    id: str
    incident_id: str
    computation_log: str
    created_at: str


@dataclasses.dataclass
class VerificationRun:
    """Before/after verification of an incident resolution."""

    id: str
    incident_id: str
    result: str
    created_at: str


@dataclasses.dataclass
class AuditEvent:
    """System-level audit log for state changes."""

    id: str
    entity_type: str
    entity_id: str
    action: str
    details: str
    created_at: str
PY

echo "==> 3. Creating SQLite Schema..."
cat > src/ecofac/persistence/schema.sql <<'SQL'
CREATE TABLE IF NOT EXISTS organizations (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS buildings (
    id TEXT PRIMARY KEY,
    org_id TEXT NOT NULL,
    name TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY (org_id) REFERENCES organizations(id)
);

CREATE TABLE IF NOT EXISTS meters (
    id TEXT PRIMARY KEY,
    building_id TEXT NOT NULL,
    fuel_type TEXT NOT NULL,
    unit TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY (building_id) REFERENCES buildings(id)
);

CREATE TABLE IF NOT EXISTS data_sources (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    source_type TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS import_batches (
    id TEXT PRIMARY KEY,
    source_id TEXT NOT NULL,
    status TEXT NOT NULL,
    imported_at TEXT NOT NULL,
    FOREIGN KEY (source_id) REFERENCES data_sources(id)
);

CREATE TABLE IF NOT EXISTS energy_readings (
    id TEXT PRIMARY KEY,
    meter_id TEXT NOT NULL,
    timestamp_utc TEXT NOT NULL,
    value REAL NOT NULL,
    unit TEXT NOT NULL,
    source_id TEXT NOT NULL,
    batch_id TEXT NOT NULL,
    is_valid INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    FOREIGN KEY (meter_id) REFERENCES meters(id),
    FOREIGN KEY (source_id) REFERENCES data_sources(id),
    FOREIGN KEY (batch_id) REFERENCES import_batches(id),
    UNIQUE (meter_id, timestamp_utc)
);

CREATE TABLE IF NOT EXISTS data_quality_issues (
    id TEXT PRIMARY KEY,
    reading_id TEXT,
    batch_id TEXT NOT NULL,
    issue_type TEXT NOT NULL,
    description TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY (reading_id) REFERENCES energy_readings(id),
    FOREIGN KEY (batch_id) REFERENCES import_batches(id)
);

CREATE TABLE IF NOT EXISTS incidents (
    id TEXT PRIMARY KEY,
    meter_id TEXT NOT NULL,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY (meter_id) REFERENCES meters(id)
);

CREATE TABLE IF NOT EXISTS evidence_records (
    id TEXT PRIMARY KEY,
    incident_id TEXT NOT NULL,
    computation_log TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY (incident_id) REFERENCES incidents(id)
);

CREATE TABLE IF NOT EXISTS verification_runs (
    id TEXT PRIMARY KEY,
    incident_id TEXT NOT NULL,
    result TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY (incident_id) REFERENCES incidents(id)
);

CREATE TABLE IF NOT EXISTS audit_events (
    id TEXT PRIMARY KEY,
    entity_type TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    action TEXT NOT NULL,
    details TEXT NOT NULL,
    created_at TEXT NOT NULL
);
SQL

echo "==> 4. Creating Database and Repository utilities..."
cat > src/ecofac/persistence/database.py <<'PY'
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
PY

cat > src/ecofac/persistence/repository.py <<'PY'
# Copyright 2026 Lightibulb

"""CRUD operations for domain entities."""

import sqlite3
from ecofac.domain.models import EnergyReading

class DuplicateReadingError(Exception):
    """Raised when a reading for a meter at a specific timestamp already exists."""

def insert_reading(conn: sqlite3.Connection, reading: EnergyReading) -> None:
    """Insert a reading safely, rolling back on duplicate."""
    try:
        conn.execute("BEGIN")
        conn.execute(
            """
            INSERT INTO energy_readings
            (id, meter_id, timestamp_utc, value, unit, source_id, batch_id, is_valid, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                reading.id,
                reading.meter_id,
                reading.timestamp_utc,
                reading.value,
                reading.unit,
                reading.source_id,
                reading.batch_id,
                int(reading.is_valid),
                reading.created_at,
            ),
        )
        conn.execute("COMMIT")
    except sqlite3.IntegrityError as e:
        conn.execute("ROLLBACK")
        msg = f"Reading for meter {reading.meter_id} at {reading.timestamp_utc} already exists."
        raise DuplicateReadingError(msg) from e
PY

echo "==> 5. Creating Integration Tests..."
cat > tests/integration/test_persistence.py <<'PY'
# Copyright 2026 Lightibulb

"""Integration tests for the SQLite persistence layer."""

import datetime
import sqlite3

import pytest

from ecofac.domain.models import EnergyReading, Organization
from ecofac.persistence.database import get_connection, init_db
from ecofac.persistence.repository import DuplicateReadingError, insert_reading


@pytest.fixture
def db_conn():
    """Yield an in-memory database connection initialized with the schema."""
    conn = get_connection(":memory:")
    init_db(conn)
    yield conn
    conn.close()


def test_schema_initialization(db_conn):
    """Verify tables are created."""
    cursor = db_conn.execute("SELECT name FROM sqlite_master WHERE type='table';")
    tables = {row["name"] for row in cursor.fetchall()}
    assert "organizations" in tables
    assert "energy_readings" in tables
    assert "audit_events" in tables


def test_identifier_uniqueness(db_conn):
    """Verify primary keys prevent duplicate organizations."""
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    db_conn.execute("INSERT INTO organizations (id, name, created_at) VALUES (?, ?, ?)", ("org-1", "Test", now))
    
    with pytest.raises(sqlite3.IntegrityError):
        db_conn.execute("INSERT INTO organizations (id, name, created_at) VALUES (?, ?, ?)", ("org-1", "Duplicate", now))


def test_relationships(db_conn):
    """Verify foreign keys restrict invalid associations."""
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    # Attempting to add a building to a non-existent org should fail
    with pytest.raises(sqlite3.IntegrityError):
        db_conn.execute("INSERT INTO buildings (id, org_id, name, created_at) VALUES (?, ?, ?, ?)", ("b-1", "org-999", "Test Bldg", now))


def test_duplicate_reading_handling(db_conn):
    """Verify duplicate readings raise an error and do not silently overwrite."""
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    db_conn.execute("INSERT INTO organizations (id, name, created_at) VALUES ('o1', 'O1', ?)", (now,))
    db_conn.execute("INSERT INTO buildings (id, org_id, name, created_at) VALUES ('b1', 'o1', 'B1', ?)", (now,))
    db_conn.execute("INSERT INTO meters (id, building_id, fuel_type, unit, created_at) VALUES ('m1', 'b1', 'electricity', 'kWh', ?)", (now,))
    db_conn.execute("INSERT INTO data_sources (id, name, source_type, created_at) VALUES ('s1', 'S1', 'csv', ?)", (now,))
    db_conn.execute("INSERT INTO import_batches (id, source_id, status, imported_at) VALUES ('ib1', 's1', 'complete', ?)", (now,))

    r1 = EnergyReading(
        id="r1", meter_id="m1", timestamp_utc="2026-01-01T00:00:00Z",
        value=10.5, unit="kWh", source_id="s1", batch_id="ib1",
        is_valid=True, created_at=now
    )
    insert_reading(db_conn, r1)

    # Attempt duplicate
    r2 = EnergyReading(
        id="r2", meter_id="m1", timestamp_utc="2026-01-01T00:00:00Z",
        value=20.0, unit="kWh", source_id="s1", batch_id="ib1",
        is_valid=True, created_at=now
    )
    with pytest.raises(DuplicateReadingError):
        insert_reading(db_conn, r2)

    # Verify value was not overwritten
    cursor = db_conn.execute("SELECT value FROM energy_readings WHERE meter_id='m1'")
    assert cursor.fetchone()["value"] == 10.5


def test_transaction_rollback(db_conn):
    """Verify that a failed insert rolls back completely."""
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    db_conn.execute("INSERT INTO organizations (id, name, created_at) VALUES ('o2', 'O2', ?)", (now,))
    
    try:
        db_conn.execute("BEGIN")
        db_conn.execute("INSERT INTO buildings (id, org_id, name, created_at) VALUES ('b2', 'o2', 'B2', ?)", (now,))
        # Force an error by inserting a duplicate primary key
        db_conn.execute("INSERT INTO buildings (id, org_id, name, created_at) VALUES ('b2', 'o2', 'B2-dupe', ?)", (now,))
        db_conn.execute("COMMIT")
    except sqlite3.IntegrityError:
        db_conn.execute("ROLLBACK")

    # Verify the first insert was rolled back
    cursor = db_conn.execute("SELECT count(*) as c FROM buildings WHERE id='b2'")
    assert cursor.fetchone()["c"] == 0


def test_persistence_across_restarts(tmp_path):
    """Verify data persists when closing and reopening the connection."""
    db_file = tmp_path / "test.db"
    
    # 1. Write data
    conn1 = get_connection(str(db_file))
    init_db(conn1)
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    conn1.execute("INSERT INTO organizations (id, name, created_at) VALUES ('o3', 'Persist', ?)", (now,))
    conn1.close()

    # 2. Read data after restart
    conn2 = get_connection(str(db_file))
    cursor = conn2.execute("SELECT name FROM organizations WHERE id='o3'")
    assert cursor.fetchone()["name"] == "Persist"
    conn2.close()
PY

echo "==> 6. Adding headers to any missing files and formatting..."
find src tests -name "*.py" | while read -r file; do
    if ! grep -qF "# Copyright 2026 Lightibulb" "$file"; then
        echo "# Copyright 2026 Lightibulb" > "${file}.tmp"
        cat "$file" >> "${file}.tmp"
        mv "${file}.tmp" "$file"
    fi
done

echo "==> Done. Part 3 implemented."
