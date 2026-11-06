# Copyright 2026 Lightibulb

"""Tests for incident → anomaly → reading traceability and evidence determinism."""

import json
import sqlite3

import pytest

from ecofac.domain.models import Anomaly, EnergyReading
from ecofac.evidence.builder import build_computation_log
from ecofac.incidents.lifecycle import create_incident
from ecofac.persistence.database import get_connection, init_db
from ecofac.persistence.repository import insert_reading


@pytest.fixture
def db_conn():
    """In-memory DB seeded with one building + one meter."""
    conn = get_connection(":memory:")
    init_db(conn)
    now = "2026-01-01T00:00:00Z"
    conn.execute(
        "INSERT INTO organizations (id, name, created_at) VALUES ('o1', 'Org', ?)",
        (now,),
    )
    conn.execute(
        "INSERT INTO buildings (id, org_id, name, created_at) VALUES ('b1', 'o1', 'Bldg', ?)",
        (now,),
    )
    conn.execute(
        "INSERT INTO meters (id, building_id, fuel_type, unit, created_at) "
        "VALUES ('m1', 'b1', 'electricity', 'kWh', ?)",
        (now,),
    )
    conn.execute(
        "INSERT INTO data_sources (id, name, source_type, created_at) "
        "VALUES ('s1', 'Src', 'csv', ?)",
        (now,),
    )
    conn.execute(
        "INSERT INTO import_batches (id, source_id, status, imported_at) "
        "VALUES ('ib1', 's1', 'started', ?)",
        (now,),
    )
    yield conn
    conn.close()


def _insert_reading(conn, reading_id, ts, value):
    insert_reading(
        conn,
        EnergyReading(
            id=reading_id,
            meter_id="m1",
            timestamp_utc=ts,
            value=value,
            unit="kWh",
            source_id="s1",
            batch_id="ib1",
            is_valid=True,
            created_at="2026-01-01T00:00:00Z",
        ),
    )


def _make_anomaly(supporting_ids, config=None):
    """Build an anomaly with a configurable detection_parameters payload."""
    config_json = json.dumps(config) if config is not None else '{"window_size": 24}'
    return Anomaly(
        id="a1",
        building_id="b1",
        meter_id="m1",
        timestamp_utc="2026-01-01T12:00:00Z",
        observed_consumption=100.0,
        expected_consumption=10.0,
        absolute_deviation=90.0,
        percentage_deviation=900.0,
        detector_name="rolling_baseline",
        severity="high",
        is_data_quality_eligible=True,
        detection_timestamp="2026-01-01T12:05:00Z",
        supporting_observation_ids=supporting_ids,
        detection_parameters=config_json,
    )


def _insert_anomaly_row(conn, anomaly):
    conn.execute(
        "INSERT INTO anomalies (id, building_id, meter_id, timestamp_utc, "
        "observed_consumption, expected_consumption, absolute_deviation, "
        "percentage_deviation, detector_name, severity, "
        "is_data_quality_eligible, detection_timestamp, detection_parameters) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            anomaly.id,
            anomaly.building_id,
            anomaly.meter_id,
            anomaly.timestamp_utc,
            anomaly.observed_consumption,
            anomaly.expected_consumption,
            anomaly.absolute_deviation,
            anomaly.percentage_deviation,
            anomaly.detector_name,
            anomaly.severity,
            int(anomaly.is_data_quality_eligible),
            anomaly.detection_timestamp,
            anomaly.detection_parameters,
        ),
    )


def test_evidence_has_detector_version(db_conn):
    """detection_parameters.detector_version propagates to evidence."""
    _insert_reading(db_conn, "r0", "2026-01-01T11:00:00Z", 10.0)
    _insert_reading(db_conn, "r1", "2026-01-01T12:00:00Z", 100.0)
    anomaly = _make_anomaly(
        supporting_ids=["r0"],
        config={"window_size": 24, "z_score": 9.0, "z_threshold": 3.0, "detector_version": "1.0"},
    )
    evidence = json.loads(build_computation_log(db_conn, anomaly))
    assert evidence["detector_version"] == "1.0"
    assert evidence["detector_configuration"]["detector_version"] == "1.0"


def test_evidence_has_reading_ids_at_top_level(db_conn):
    """reading_ids explicitly lists target and historical reading IDs."""
    _insert_reading(db_conn, "r0", "2026-01-01T10:00:00Z", 10.0)
    _insert_reading(db_conn, "r1", "2026-01-01T11:00:00Z", 10.0)
    _insert_reading(db_conn, "t1", "2026-01-01T12:00:00Z", 100.0)
    anomaly = _make_anomaly(supporting_ids=["r0", "r1"])
    evidence = json.loads(build_computation_log(db_conn, anomaly))
    ids = evidence["reading_ids"]
    assert "t1" in ids
    assert "r0" in ids
    assert "r1" in ids
    # Target must be first
    assert ids[0] == "t1"


def test_evidence_has_human_readable_explanation(db_conn):
    """The payload includes a non-empty explanation string."""
    _insert_reading(db_conn, "r0", "2026-01-01T11:00:00Z", 10.0)
    _insert_reading(db_conn, "t1", "2026-01-01T12:00:00Z", 100.0)
    anomaly = _make_anomaly(
        supporting_ids=["r0"],
        config={
            "window_size": 24,
            "z_score": 45.6,
            "z_threshold": 3.0,
            "detector_version": "1.0",
            "sample_count": 1,
        },
    )
    evidence = json.loads(build_computation_log(db_conn, anomaly))
    expl = evidence["explanation"]
    assert isinstance(expl, str)
    assert len(expl) > 20
    assert "observed" in expl
    assert "expected" in expl
    assert "rolling baseline" in expl


def test_evidence_deterministic_across_builds(db_conn):
    """Two builds on identical inputs produce byte-identical output."""
    _insert_reading(db_conn, "r0", "2026-01-01T10:00:00Z", 10.0)
    _insert_reading(db_conn, "r1", "2026-01-01T11:00:00Z", 11.0)
    _insert_reading(db_conn, "t1", "2026-01-01T12:00:00Z", 100.0)
    anomaly = _make_anomaly(
        supporting_ids=["r0", "r1"],
        config={"window_size": 24, "z_score": 12.3, "z_threshold": 3.0, "detector_version": "1.0"},
    )
    first = build_computation_log(db_conn, anomaly)
    second = build_computation_log(db_conn, anomaly)
    assert first == second


def test_incident_traceable_to_readings(db_conn):
    """From incident_id, walk: incident → anomaly → reading IDs → readings."""
    _insert_reading(db_conn, "r0", "2026-01-01T11:00:00Z", 10.0)
    _insert_reading(db_conn, "t1", "2026-01-01T12:00:00Z", 100.0)
    anomaly = _make_anomaly(supporting_ids=["r0"])
    _insert_anomaly_row(db_conn, anomaly)
    incident_id = create_incident(db_conn, anomaly)

    # incident → anomaly
    row = db_conn.execute(
        "SELECT anomaly_id FROM incidents WHERE id = ?", (incident_id,)
    ).fetchone()
    assert row["anomaly_id"] == "a1"

    # anomaly → evidence
    evidence_row = db_conn.execute(
        "SELECT computation_log FROM evidence_records WHERE incident_id = ?",
        (incident_id,),
    ).fetchone()
    evidence = json.loads(evidence_row["computation_log"])

    # evidence → readings (every referenced ID must exist)
    for rid in evidence["reading_ids"]:
        found = db_conn.execute("SELECT 1 FROM energy_readings WHERE id = ?", (rid,)).fetchone()
        assert found is not None, f"Evidence references missing reading {rid}"


def test_evidence_works_with_minimal_config(db_conn):
    """Old anomalies with '{}' detection_parameters still produce valid evidence."""
    _insert_reading(db_conn, "r0", "2026-01-01T11:00:00Z", 10.0)
    _insert_reading(db_conn, "t1", "2026-01-01T12:00:00Z", 100.0)
    anomaly = _make_anomaly(supporting_ids=["r0"], config={})
    evidence = json.loads(build_computation_log(db_conn, anomaly))
    assert evidence["detector_version"] == "unknown"
    assert evidence["detector_configuration"] == {}
    assert "explanation" in evidence
    assert len(evidence["explanation"]) > 10


def test_incident_survives_across_connections(tmp_path):
    """A created incident is visible from a separate connection."""
    db_file = tmp_path / "test.db"
    conn1 = get_connection(str(db_file))
    init_db(conn1)
    now = "2026-01-01T00:00:00Z"
    conn1.execute(
        "INSERT INTO organizations (id, name, created_at) VALUES ('o1', 'Org', ?)",
        (now,),
    )
    conn1.execute(
        "INSERT INTO buildings (id, org_id, name, created_at) VALUES ('b1', 'o1', 'B', ?)",
        (now,),
    )
    conn1.execute(
        "INSERT INTO meters (id, building_id, fuel_type, unit, created_at) "
        "VALUES ('m1', 'b1', 'elec', 'kWh', ?)",
        (now,),
    )
    conn1.execute(
        "INSERT INTO data_sources (id, name, source_type, created_at) "
        "VALUES ('s1', 'Src', 'csv', ?)",
        (now,),
    )
    conn1.execute(
        "INSERT INTO import_batches (id, source_id, status, imported_at) "
        "VALUES ('ib1', 's1', 'started', ?)",
        (now,),
    )
    _insert_reading(conn1, "r0", "2026-01-01T11:00:00Z", 10.0)
    _insert_reading(conn1, "t1", "2026-01-01T12:00:00Z", 100.0)
    anomaly = _make_anomaly(supporting_ids=["r0"])
    _insert_anomaly_row(conn1, anomaly)
    incident_id = create_incident(conn1, anomaly)
    conn1.close()

    conn2 = get_connection(str(db_file))
    row = conn2.execute("SELECT id FROM incidents WHERE anomaly_id = ?", ("a1",)).fetchone()
    assert row is not None
    assert row["id"] == incident_id
    evidence_count = conn2.execute(
        "SELECT count(*) AS c FROM evidence_records WHERE incident_id = ?",
        (incident_id,),
    ).fetchone()["c"]
    assert evidence_count == 1
    conn2.close()


def test_append_only_still_enforced(db_conn):
    """Evidence and audit remain append-only after this change."""
    _insert_reading(db_conn, "r0", "2026-01-01T11:00:00Z", 10.0)
    _insert_reading(db_conn, "t1", "2026-01-01T12:00:00Z", 100.0)
    anomaly = _make_anomaly(supporting_ids=["r0"])
    _insert_anomaly_row(db_conn, anomaly)
    create_incident(db_conn, anomaly)
    for table in ("audit_events", "evidence_records"):
        with pytest.raises(sqlite3.DatabaseError, match="append-only"):
            db_conn.execute(f"UPDATE {table} SET id = id")
