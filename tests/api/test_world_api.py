# Copyright 2026 Lightibulb
"""Tests for the /api/v1/world/state snapshot endpoint."""

BASE = "/api/v1/world/state"


def _insert_reading(conn, reading_id, meter_id, ts, value, is_valid=1):
    """Insert a reading. Requires an import_batches row."""
    conn.execute(
        "INSERT OR IGNORE INTO import_batches "
        "(id, source_id, status, imported_at) "
        "VALUES ('ib-world', 's1', 'started', '2026-01-01T00:00:00Z')"
    )
    conn.execute(
        "INSERT INTO energy_readings "
        "(id, meter_id, timestamp_utc, value, unit, source_id, batch_id, "
        "is_valid, created_at) "
        "VALUES (?, ?, ?, ?, 'kWh', 's1', 'ib-world', ?, "
        "'2026-01-01T00:00:00Z')",
        (reading_id, meter_id, ts, value, is_valid),
    )


def _insert_decision(conn, decision_id, status, precedence, created_at):
    conn.execute(
        "INSERT INTO decisions "
        "(id, status, precedence, engine_version, config_version, "
        "input_fingerprint, config_fingerprint, request_json, result_json, "
        "decision_timestamp, created_at, supersedes, supersession_reason) "
        "VALUES (?, ?, ?, '1.0', 'cfg-1', 'fp1', 'fp2', '{}', '{}', ?, ?, "
        "NULL, NULL)",
        (decision_id, status, precedence, created_at, created_at),
    )


def _insert_incident(conn, incident_id, anomaly_id, status, created_at, anomaly_ts=None):
    """Insert one incident plus its parent anomaly.

    The anomalies table has UNIQUE(meter_id, timestamp_utc, detector_name),
    so tests inserting more than one incident must pass distinct anomaly_ts
    values.
    """
    ts = anomaly_ts or created_at
    conn.execute(
        "INSERT INTO anomalies "
        "(id, building_id, meter_id, timestamp_utc, observed_consumption, "
        "expected_consumption, absolute_deviation, percentage_deviation, "
        "detector_name, severity, is_data_quality_eligible, "
        "detection_timestamp) "
        "VALUES (?, 'b1', 'm1', ?, 100, 10, 90, 900, "
        "'rolling_baseline', 'high', 1, ?)",
        (anomaly_id, ts, ts),
    )
    conn.execute(
        "INSERT INTO incidents (id, anomaly_id, status, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (incident_id, anomaly_id, status, created_at, created_at),
    )


def test_world_state_empty(client, test_db):
    test_db.execute("DELETE FROM meters")
    test_db.execute("DELETE FROM buildings")
    test_db.execute("DELETE FROM organizations")
    resp = client.get(BASE)
    assert resp.status_code == 200
    body = resp.json()
    assert body["buildings"] == []
    assert body["totals"]["buildings"] == 0
    assert body["totals"]["meters"] == 0
    assert body["totals"]["decisions"] == {}
    assert body["totals"]["scenarios"] == 0
    assert body["recent_events"] == []


def test_world_state_returns_seeded_buildings(client):
    resp = client.get(BASE)
    body = resp.json()
    assert resp.status_code == 200
    assert len(body["buildings"]) == 1
    assert body["buildings"][0]["id"] == "b1"
    assert len(body["buildings"][0]["meters"]) == 1
    assert body["buildings"][0]["meters"][0]["id"] == "m1"


def test_world_state_latest_reading(client, test_db):
    _insert_reading(test_db, "r1", "m1", "2026-01-01T00:00:00Z", 10.0)
    _insert_reading(test_db, "r2", "m1", "2026-01-01T01:00:00Z", 20.0)
    _insert_reading(test_db, "r3", "m1", "2026-01-01T02:00:00Z", 30.0)
    body = client.get(BASE).json()
    latest = body["buildings"][0]["meters"][0]["latest_reading"]
    assert latest["value"] == 30.0
    assert latest["timestamp_utc"] == "2026-01-01T02:00:00Z"
    assert latest["is_valid"] == 1


def test_world_state_open_incident_count(client, test_db):
    _insert_incident(test_db, "inc1", "an1", "DETECTED", "2026-01-01T00:00:00Z")
    _insert_incident(
        test_db,
        "inc2",
        "an2",
        "RESOLVED",
        "2026-01-02T00:00:00Z",
        anomaly_ts="2026-01-02T00:00:00Z",
    )
    body = client.get(BASE).json()
    meter = body["buildings"][0]["meters"][0]
    assert meter["open_incidents"] == 1
    assert meter["total_incidents"] == 2


def test_world_state_decision_status_counts(client, test_db):
    _insert_decision(test_db, "d1", "APPROVE", "clean", "2026-01-01T00:00:00Z")
    _insert_decision(test_db, "d2", "APPROVE", "clean", "2026-01-01T01:00:00Z")
    _insert_decision(
        test_db,
        "d3",
        "REJECT",
        "hard_limit_violation",
        "2026-01-01T02:00:00Z",
    )
    body = client.get(BASE).json()
    assert body["totals"]["decisions"] == {"APPROVE": 2, "REJECT": 1}


def test_world_state_recent_events_sorted_newest_first(client, test_db):
    _insert_decision(test_db, "d1", "APPROVE", "clean", "2026-01-01T00:00:00Z")
    _insert_incident(test_db, "inc1", "an1", "DETECTED", "2026-01-01T05:00:00Z")
    body = client.get(BASE).json()
    events = body["recent_events"]
    assert len(events) == 2
    assert events[0]["timestamp"] > events[1]["timestamp"]
    assert events[0]["type"] == "incident"
    assert events[1]["type"] == "decision"


def test_world_state_recent_events_capped(client, test_db):
    for i in range(20):
        _insert_decision(
            test_db,
            f"d{i:02d}",
            "APPROVE",
            "clean",
            f"2026-01-01T{i:02d}:00:00Z",
        )
    body = client.get(BASE).json()
    assert len(body["recent_events"]) <= 15


def test_world_state_response_has_all_keys(client):
    body = client.get(BASE).json()
    assert "generated_at" in body
    assert "buildings" in body
    assert "totals" in body
    assert "recent_events" in body
    totals = body["totals"]
    for key in (
        "buildings",
        "meters",
        "open_incidents",
        "total_incidents",
        "decisions",
        "scenarios",
    ):
        assert key in totals, f"missing totals.{key}"


def test_world_state_no_auth_required(client):
    resp = client.get(BASE)
    assert resp.status_code == 200


def test_world_state_no_pollution_from_invalid_method(client):
    resp = client.post(BASE)
    assert resp.status_code == 405


def test_world_state_max_severity_logic(client, test_db):
    _insert_incident(test_db, "inc1", "an1", "DETECTED", "2026-01-01T00:00:00Z")
    test_db.execute("UPDATE anomalies SET severity = 'low' WHERE id = 'an1'")

    body = client.get("/api/v1/world/state").json()
    meter = body["buildings"][0]["meters"][0]
    assert meter["max_severity"] == 1
