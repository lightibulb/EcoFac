# Copyright 2026 Lightibulb
"""Regression tests for the data-integrity audit fixes."""

import datetime as dt
import json
import sqlite3

import pytest

from ecofac.domain.models import Anomaly
from ecofac.incidents.lifecycle import (
    IncidentNotFoundError,
    create_incident,
    transition_incident,
)
from ecofac.ingestion.pipeline import ingest_csv
from ecofac.persistence.database import get_connection, init_db
from evaluation.runner import seed_metadata

HEADER = "timestamp,building_id,meter_id,energy_value,unit\n"


@pytest.fixture
def db_conn():
    conn = get_connection(":memory:")
    init_db(conn)
    seed_metadata(conn)
    yield conn
    conn.close()


def test_non_finite_values_are_rejected_not_fatal(db_conn):
    csv_data = HEADER + (
        "2026-01-01T00:00:00Z,b1,m1-1,nan,kWh\n"
        "2026-01-01T01:00:00Z,b1,m1-1,inf,kWh\n"
        "2026-01-01T02:00:00Z,b1,m1-1,1e999,kWh\n"
        "2026-01-01T03:00:00Z,b1,m1-1,5,kWh\n"
    )
    summary = ingest_csv(db_conn, csv_data, "s1", "ib1")
    assert summary.accepted_rows == 1
    assert summary.rejected_rows == 3
    kinds = {r["issue_type"] for r in db_conn.execute("SELECT issue_type FROM data_quality_issues")}
    assert kinds == {"malformed_value"}


def test_rejected_row_does_not_shadow_a_later_valid_row(db_conn):
    csv_data = HEADER + (
        "2026-01-01T00:00:00Z,b1,m1-1,10,BTU\n2026-01-01T00:00:00Z,b1,m1-1,10,kWh\n"
    )
    summary = ingest_csv(db_conn, csv_data, "s1", "ib1")
    assert summary.accepted_rows == 1
    assert summary.invalid_units == 1
    assert summary.duplicate_rows == 0


def test_reimporting_negative_reading_does_not_crash(db_conn):
    csv_data = HEADER + "2026-01-01T00:00:00Z,b1,m1-1,-5,kWh\n"
    first = ingest_csv(db_conn, csv_data, "s1", "ib1")
    assert first.negative_consumption == 1
    second = ingest_csv(db_conn, csv_data, "s1", "ib1")
    assert second.accepted_rows == 0
    assert second.duplicate_rows == 1
    assert second.negative_consumption == 0
    issues = db_conn.execute(
        "SELECT count(*) AS c FROM data_quality_issues WHERE issue_type = 'negative_consumption'"
    ).fetchone()["c"]
    assert issues == 1


def test_headers_are_case_and_space_insensitive(db_conn):
    csv_data = (
        " Timestamp , Building_ID ,METER_ID,Energy_Value,Unit\n2026-01-01T00:00:00Z,b1,m1-1,5,kWh\n"
    )
    assert ingest_csv(db_conn, csv_data, "s1", "ib1").accepted_rows == 1


def test_sub_second_timestamps_are_truncated_so_ordering_stays_consistent(db_conn):
    csv_data = HEADER + (
        "2026-01-01T00:00:00.500Z,b1,m1-1,5,kWh\n2026-01-01T00:00:00Z,b1,m1-1,6,kWh\n"
    )
    summary = ingest_csv(db_conn, csv_data, "s1", "ib1")
    assert summary.accepted_rows == 1
    assert summary.duplicate_rows == 1
    row = db_conn.execute("SELECT timestamp_utc FROM energy_readings").fetchone()
    assert row["timestamp_utc"] == "2026-01-01T00:00:00Z"


def _insert_anomaly(conn, anomaly_id, detector, reading_ids):
    anomaly = Anomaly(
        id=anomaly_id,
        building_id="b1",
        meter_id="m1-1",
        timestamp_utc="2026-01-01T12:00:00Z",
        observed_consumption=100.0,
        expected_consumption=10.0,
        absolute_deviation=90.0,
        percentage_deviation=900.0,
        detector_name=detector,
        severity="high",
        is_data_quality_eligible=True,
        detection_timestamp="2026-01-01T12:05:00Z",
        supporting_observation_ids=reading_ids,
        detection_parameters='{"window_size": 24}',
    )
    conn.execute(
        "INSERT INTO anomalies (id, building_id, meter_id, timestamp_utc, observed_consumption, "
        "expected_consumption, absolute_deviation, percentage_deviation, detector_name, "
        "severity, is_data_quality_eligible, detection_timestamp, detection_parameters) "
        "VALUES (?, 'b1', 'm1-1', ?, 100, 10, 90, 900, ?, 'high', 1, ?, '{}')",
        (anomaly_id, anomaly.timestamp_utc, detector, anomaly.detection_timestamp),
    )
    return anomaly


@pytest.fixture
def anomaly_db(db_conn):
    ingest_csv(db_conn, HEADER + "2026-01-01T12:00:00Z,b1,m1-1,100,kWh\n", "s1", "ib1")
    return db_conn


def _reading_id(conn):
    return conn.execute("SELECT id FROM energy_readings").fetchone()["id"]


def test_create_incident_is_idempotent(anomaly_db):
    anomaly = _insert_anomaly(anomaly_db, "a1", "rolling_baseline", [_reading_id(anomaly_db)])
    first = create_incident(anomaly_db, anomaly)
    assert create_incident(anomaly_db, anomaly) == first
    count = anomaly_db.execute("SELECT count(*) AS c FROM incidents").fetchone()["c"]
    assert count == 1


def test_two_detectors_share_one_incident_with_two_evidence_records(anomaly_db):
    rid = _reading_id(anomaly_db)
    first = create_incident(
        anomaly_db, _insert_anomaly(anomaly_db, "a1", "rolling_baseline", [rid])
    )
    second = create_incident(anomaly_db, _insert_anomaly(anomaly_db, "a2", "robust_mad", [rid]))
    assert first == second
    evidence = anomaly_db.execute(
        "SELECT computation_log FROM evidence_records WHERE incident_id = ?", (first,)
    ).fetchall()
    detectors = {json.loads(e["computation_log"])["detector_name"] for e in evidence}
    assert detectors == {"rolling_baseline", "robust_mad"}


def test_unknown_incident_raises_not_found(db_conn):
    with pytest.raises(IncidentNotFoundError):
        transition_incident(db_conn, "missing", "UNDER_REVIEW")


def test_whitespace_reason_counts_as_missing(anomaly_db):
    anomaly = _insert_anomaly(anomaly_db, "a1", "rolling_baseline", [_reading_id(anomaly_db)])
    incident_id = create_incident(anomaly_db, anomaly)
    transition_incident(anomaly_db, incident_id, "UNDER_REVIEW")
    with pytest.raises(ValueError, match="dismissal reason is required"):
        transition_incident(anomaly_db, incident_id, "DISMISSED", reason="   ")


def test_audit_and_evidence_are_append_only(anomaly_db):
    anomaly = _insert_anomaly(anomaly_db, "a1", "rolling_baseline", [_reading_id(anomaly_db)])
    create_incident(anomaly_db, anomaly)
    for table in ("audit_events", "evidence_records"):
        for sql in (f"UPDATE {table} SET id = id", f"DELETE FROM {table}"):
            with pytest.raises(sqlite3.DatabaseError, match="append-only"):
                anomaly_db.execute(sql)


def test_timestamp_strings_in_audit_use_z_suffix(anomaly_db):
    anomaly = _insert_anomaly(anomaly_db, "a1", "rolling_baseline", [_reading_id(anomaly_db)])
    create_incident(anomaly_db, anomaly)
    stamp = anomaly_db.execute("SELECT created_at FROM audit_events").fetchone()["created_at"]
    assert stamp.endswith("Z")
    assert dt.datetime.fromisoformat(stamp).tzinfo is not None
