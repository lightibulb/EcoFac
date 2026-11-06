# Copyright 2026 Lightibulb

"""Integration tests for incident lifecycle and evidence management."""

import json

import pytest

from ecofac.domain.models import Anomaly, EnergyReading
from ecofac.incidents.lifecycle import (
    InvalidStateTransitionError,
    create_incident,
    transition_incident,
)
from ecofac.persistence.database import get_connection, init_db
from ecofac.persistence.repository import insert_reading


@pytest.fixture
def db_conn():
    """Yield an in-memory database."""
    conn = get_connection(":memory:")
    init_db(conn)
    now = "2026-01-01T00:00:00Z"

    conn.execute("INSERT INTO organizations (id, name, created_at) VALUES ('o1', 'Org', ?)", (now,))
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
        "INSERT INTO data_sources (id, name, source_type, created_at) VALUES ('s1', 'Src', 'csv', ?)",
        (now,),
    )
    conn.execute(
        "INSERT INTO import_batches (id, source_id, status, imported_at) VALUES ('ib1', 's1', 'started', ?)",
        (now,),
    )

    # Insert a target reading
    r = EnergyReading(
        id="r1",
        meter_id="m1",
        timestamp_utc="2026-01-01T12:00:00Z",
        value=100.0,
        unit="kWh",
        source_id="s1",
        batch_id="ib1",
        is_valid=True,
        created_at=now,
    )
    insert_reading(conn, r)

    yield conn
    conn.close()


def _make_and_insert_anomaly(conn, supporting_ids=None):
    if supporting_ids is None:
        supporting_ids = ["r1"]

    anomaly = Anomaly(
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
        detection_parameters='{"window_size": 24}',
    )

    # Insert anomaly into DB to satisfy the FK constraint on incidents
    conn.execute(
        "INSERT INTO anomalies (id, building_id, meter_id, timestamp_utc, "
        "observed_consumption, expected_consumption, absolute_deviation, "
        "percentage_deviation, detector_name, severity, "
        "is_data_quality_eligible, detection_timestamp) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
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
        ),
    )
    return anomaly


def test_create_incident_with_evidence(db_conn):
    """Verify incident creation generates evidence and audit log."""
    anomaly = _make_and_insert_anomaly(db_conn)
    incident_id = create_incident(db_conn, anomaly)

    # Check incident
    cursor = db_conn.execute("SELECT status FROM incidents WHERE id = ?", (incident_id,))
    assert cursor.fetchone()["status"] == "DETECTED"

    # Check evidence
    cursor = db_conn.execute(
        "SELECT computation_log FROM evidence_records WHERE incident_id = ?", (incident_id,)
    )
    evidence = json.loads(cursor.fetchone()["computation_log"])
    assert evidence["baseline_calculation"]["absolute_deviation"] == 90.0
    assert evidence["target_measurement"]["value"] == 100.0

    # Check audit
    cursor = db_conn.execute("SELECT action FROM audit_events WHERE entity_id = ?", (incident_id,))
    assert cursor.fetchone()["action"] == "CREATED"


def test_evidence_rejects_nonexistent_readings(db_conn):
    """Verify incident creation fails if evidence references missing data."""
    anomaly = _make_and_insert_anomaly(db_conn, supporting_ids=["r1", "missing-reading"])
    with pytest.raises(ValueError, match="nonexistent readings"):
        create_incident(db_conn, anomaly)


def test_valid_state_transitions(db_conn):
    """Verify the full happy path of the state machine."""
    anomaly = _make_and_insert_anomaly(db_conn)
    incident_id = create_incident(db_conn, anomaly)

    transition_incident(db_conn, incident_id, "UNDER_REVIEW")
    transition_incident(db_conn, incident_id, "ACKNOWLEDGED")
    transition_incident(db_conn, incident_id, "RESOLVED", reason="HVAC fixed")

    cursor = db_conn.execute(
        "SELECT status, resolution_notes FROM incidents WHERE id = ?", (incident_id,)
    )
    row = cursor.fetchone()
    assert row["status"] == "RESOLVED"
    assert row["resolution_notes"] == "HVAC fixed"


def test_invalid_state_transitions(db_conn):
    """Verify skipping steps raises an error."""
    anomaly = _make_and_insert_anomaly(db_conn)
    incident_id = create_incident(db_conn, anomaly)

    # Cannot go directly from DETECTED to ACKNOWLEDGED
    with pytest.raises(InvalidStateTransitionError):
        transition_incident(db_conn, incident_id, "ACKNOWLEDGED")


def test_dismissal_requires_reason(db_conn):
    """Verify dismissing without a reason fails."""
    anomaly = _make_and_insert_anomaly(db_conn)
    incident_id = create_incident(db_conn, anomaly)
    transition_incident(db_conn, incident_id, "UNDER_REVIEW")

    with pytest.raises(ValueError, match="dismissal reason is required"):
        transition_incident(db_conn, incident_id, "DISMISSED")
