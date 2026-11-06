# Copyright 2026 Lightibulb

"""HTTP-level regression tests for the audit fixes."""

HEADER = "timestamp,building_id,meter_id,energy_value,unit\n"
FLAT = HEADER + "".join(f"2026-01-01T{h:02d}:00:00Z,b1,m1,10.0,kWh\n" for h in range(24))
SPIKE = HEADER + "2026-01-02T00:00:00Z,b1,m1,100.0,kWh\n"


def _post_csv(client, text, batch, source="s1", **params):
    return client.post(
        "/api/v1/imports",
        files={"file": ("data.csv", text, "text/csv")},
        data={"source_id": source, "batch_id": batch},
        params=params,
    )


def test_import_runs_analysis_and_opens_one_incident(client):
    assert _post_csv(client, FLAT, "ib-flat").json()["anomalies_detected"] == 0
    body = _post_csv(client, SPIKE, "ib-spike").json()
    assert body["anomalies_detected"] == 2
    assert body["incidents_created"] == 1
    assert len(client.get("/api/v1/incidents").json()) == 1


def test_import_analysis_can_be_disabled(client):
    _post_csv(client, FLAT, "ib-flat")
    body = _post_csv(client, SPIKE, "ib-spike", analyze="false").json()
    assert body["anomalies_detected"] == 0
    assert client.get("/api/v1/incidents").json() == []


def test_analyze_accepts_offset_timestamps_and_reports_recorded_findings(client):
    _post_csv(client, FLAT, "ib-flat")
    _post_csv(client, SPIKE, "ib-spike", analyze="false")
    resp = client.post("/api/v1/analyze/m1", params={"timestamp": "2026-01-02T00:00:00+00:00"})
    findings = resp.json()["findings"]
    assert len(findings) == 2
    assert len({f["incident_id"] for f in findings}) == 1
    again = client.post("/api/v1/analyze/m1", params={"timestamp": "2026-01-02T00:00:00Z"})
    assert all(f["already_recorded"] for f in again.json()["findings"])
    incident_id = findings[0]["incident_id"]
    assert len(client.get(f"/api/v1/incidents/{incident_id}/evidence").json()) == 2


def test_gated_anomaly_never_becomes_an_incident(client):
    # 23 readings (hour 23 missing) -> the quality window before the spike has a gap.
    gappy = (
        HEADER
        + "".join(f"2026-01-01T{h:02d}:00:00Z,b1,m1,10.0,kWh\n" for h in range(23))
        + "2025-12-31T23:00:00Z,b1,m1,10.0,kWh\n"
    )
    _post_csv(client, gappy, "ib-gappy")
    _post_csv(client, SPIKE, "ib-spike", analyze="false")
    body = client.post("/api/v1/analyze/m1", params={"timestamp": "2026-01-02T00:00:00Z"}).json()
    assert body["status"] == "anomalies_detected"
    assert all(f["gated_by_quality"] and f["incident_id"] is None for f in body["findings"])
    assert client.get("/api/v1/incidents").json() == []


def test_unknown_meter_and_bad_ranges_are_rejected(client):
    assert (
        client.post(
            "/api/v1/analyze/nope", params={"timestamp": "2026-01-01T00:00:00Z"}
        ).status_code
        == 404
    )
    resp = client.get(
        "/api/v1/quality/nope",
        params={"start": "2026-01-01T00:00:00Z", "end": "2026-01-02T00:00:00Z"},
    )
    assert resp.status_code == 404
    resp = client.get(
        "/api/v1/quality/m1",
        params={"start": "2026-01-02T00:00:00Z", "end": "2026-01-01T00:00:00Z"},
    )
    assert resp.status_code == 400


def test_batch_reuse_and_bad_ids_are_rejected(client):
    assert _post_csv(client, FLAT, "ib-1").status_code == 200
    assert _post_csv(client, FLAT, "ib-1").status_code == 409
    assert _post_csv(client, FLAT, "ib-1", source="other").status_code == 409
    assert _post_csv(client, FLAT, "bad id!").status_code == 422


def test_excel_bom_csv_is_accepted(client):
    bom_csv = "\ufeff" + HEADER + "2026-01-01T00:00:00Z,b1,m1,5,kWh\n"
    resp = client.post(
        "/api/v1/imports",
        files={"file": ("excel.csv", bom_csv.encode("utf-8"), "text/csv")},
        data={"source_id": "s1", "batch_id": "ib-bom"},
    )
    assert resp.status_code == 200
    assert resp.json()["accepted_rows"] == 1


def test_missing_incident_returns_404_and_body_is_optional(client):
    assert client.get("/api/v1/incidents/missing").status_code == 404
    assert client.get("/api/v1/incidents/missing/evidence").status_code == 404
    assert client.post("/api/v1/incidents/missing/review").status_code == 404

    _post_csv(client, FLAT, "ib-flat")
    _post_csv(client, SPIKE, "ib-spike")
    incident_id = client.get("/api/v1/incidents").json()[0]["id"]
    resp = client.post(f"/api/v1/incidents/{incident_id}/review")  # no JSON body at all
    assert resp.status_code == 200
    assert resp.json()["status"] == "UNDER_REVIEW"
    resp = client.post(f"/api/v1/incidents/{incident_id}/acknowledge")
    resp = client.post(f"/api/v1/incidents/{incident_id}/resolve", json={"reason": "   "})
    assert resp.status_code == 400


def test_chart_data_skips_invalid_readings(client):
    _post_csv(
        client,
        HEADER + "2026-01-01T00:00:00Z,b1,m1,-5,kWh\n2026-01-01T01:00:00Z,b1,m1,7,kWh\n",
        "ib-neg",
    )
    data = client.get("/api/v1/dashboard/chart-data").json()
    assert data["data"] == [7.0]


def test_generate_demo_is_idempotent_and_creates_incidents(client):
    first = client.post("/api/v1/dashboard/generate-demo")
    assert first.status_code == 200
    assert first.json()["accepted_rows"] > 20000
    assert first.json()["incidents_created"] > 0
    stats_before = client.get("/api/v1/dashboard/stats").json()
    second = client.post("/api/v1/dashboard/generate-demo")
    assert second.status_code == 200
    assert second.json()["accepted_rows"] == 0
    assert client.get("/api/v1/dashboard/stats").json() == stats_before
