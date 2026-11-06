# Copyright 2026 Lightibulb

"""Regression tests for specific API bugs."""


def test_new_source_id_is_auto_created(client):
    """Uploading a file with an unknown source ID should auto-create it and succeed."""
    csv_content = (
        "timestamp,building_id,meter_id,energy_value,unit\n2026-01-01T12:00:00Z,b1,m1,10.5,kWh\n"
    )
    files = {"file": ("test.csv", csv_content, "text/csv")}
    data = {"source_id": "new-source", "batch_id": "ib1"}

    response = client.post("/api/v1/imports", files=files, data=data)
    assert response.status_code == 200
    assert response.json()["accepted_rows"] == 1


def test_quality_rejects_naive_timestamps(client):
    """Naive start/end params must produce a 400, not a 500."""
    response = client.get(
        "/api/v1/quality/m1",
        params={"start": "2026-01-01T00:00:00", "end": "2026-01-02T00:00:00"},
    )
    assert response.status_code == 400


def test_csv_naive_timestamp_rejected_via_http(client):
    """CSV rows with naive timestamps are rejected with a counter."""
    csv_content = (
        "timestamp,building_id,meter_id,energy_value,unit\n2026-01-01T12:00:00,b1,m1,10.5,kWh\n"
    )
    files = {"file": ("n.csv", csv_content, "text/csv")}
    data = {"source_id": "s1", "batch_id": "ib-naive"}
    resp = client.post("/api/v1/imports", files=files, data=data)
    assert resp.status_code == 200
    body = resp.json()
    assert body["accepted_rows"] == 0


def test_full_incident_lifecycle_via_http(client):
    """DETECTED -> UNDER_REVIEW -> ACKNOWLEDGED -> RESOLVED over HTTP."""
    flat = "timestamp,building_id,meter_id,energy_value,unit\n" + "".join(
        f"2026-01-01T{h:02d}:00:00Z,b1,m1,10.0,kWh\n" for h in range(24)
    )
    resp = client.post(
        "/api/v1/imports",
        files={"file": ("flat.csv", flat, "text/csv")},
        data={"source_id": "s1", "batch_id": "ib-flat"},
    )
    assert resp.status_code == 200

    spike = (
        "timestamp,building_id,meter_id,energy_value,unit\n2026-01-02T00:00:00Z,b1,m1,100.0,kWh\n"
    )
    resp = client.post(
        "/api/v1/imports",
        files={"file": ("spike.csv", spike, "text/csv")},
        data={"source_id": "s1", "batch_id": "ib-spike"},
    )
    assert resp.status_code == 200

    resp = client.post("/api/v1/analyze/m1", params={"timestamp": "2026-01-02T00:00:00Z"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "anomalies_detected"
    finding = body["findings"][0]
    assert finding["gated_by_quality"] is False

    incident_id = finding["incident_id"]

    # 1. Transition DETECTED -> UNDER_REVIEW
    resp = client.post(f"/api/v1/incidents/{incident_id}/review", json={"reason": "Investigating"})
    assert resp.status_code == 200
    assert resp.json()["status"] == "UNDER_REVIEW"

    # 2. Transition UNDER_REVIEW -> ACKNOWLEDGED
    resp = client.post(f"/api/v1/incidents/{incident_id}/acknowledge", json={"reason": "Confirmed"})
    assert resp.status_code == 200
    assert resp.json()["status"] == "ACKNOWLEDGED"

    # 3. Transition ACKNOWLEDGED -> RESOLVED
    resp = client.post(f"/api/v1/incidents/{incident_id}/resolve", json={"reason": "Fixed"})
    assert resp.status_code == 200
    assert resp.json()["status"] == "RESOLVED"
