# Copyright 2026 Lightibulb

"""Cross-endpoint smoke test — proves the full request chain still works."""

HEADER = "timestamp,building_id,meter_id,energy_value,unit\n"


def _spike_csv() -> str:
    rows = [f"2026-01-01T{h:02d}:00:00Z,b1,m1,10.0,kWh\n" for h in range(24)]
    rows.append("2026-01-02T00:00:00Z,b1,m1,100.0,kWh\n")
    return HEADER + "".join(rows)


def test_api_smoke_sequence(client):
    """Exercise every user-facing endpoint in a realistic sequence."""
    # 1. Health
    assert client.get("/health").json() == {"status": "ok"}

    # 2. Empty dashboard (conftest seeds one building and one meter)
    stats = client.get("/api/v1/dashboard/stats").json()
    assert stats["buildings"] == 1
    assert stats["meters"] == 1
    assert stats["total_incidents"] == 0

    # 3. Entity endpoints
    buildings = client.get("/api/v1/buildings").json()
    assert len(buildings) == 1
    assert buildings[0]["id"] == "b1"
    meters = client.get("/api/v1/meters").json()
    assert len(meters) == 1
    assert meters[0]["id"] == "m1"

    # 4. Upload a CSV that produces an anomaly
    resp = client.post(
        "/api/v1/imports",
        files={"file": ("spike.csv", _spike_csv(), "text/csv")},
        data={"source_id": "s1", "batch_id": "ib-smoke"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["accepted_rows"] == 25
    assert body["rejected_rows"] == 0
    # Analysis runs automatically after import; both detectors fire
    assert body["anomalies_detected"] >= 1
    assert body["incidents_created"] >= 1

    # 5. Dashboard now shows activity
    stats2 = client.get("/api/v1/dashboard/stats").json()
    assert stats2["total_incidents"] >= 1
    assert stats2["open_incidents"] >= 1

    # 6. Chart endpoint returns valid readings
    chart = client.get("/api/v1/dashboard/chart-data").json()
    assert chart["meter_id"] == "m1"
    assert len(chart["data"]) > 0

    # 7. Incidents list includes the newly created incident
    incidents = client.get("/api/v1/incidents").json()
    assert len(incidents) >= 1
    incident_id = incidents[0]["id"]
    assert incidents[0]["status"] == "DETECTED"

    # 8. Evidence is retrievable
    evidence = client.get(f"/api/v1/incidents/{incident_id}/evidence").json()
    assert len(evidence) >= 1
    assert "computation_log" in evidence[0]

    # 9. Manual re-analysis on the same timestamp is idempotent
    reanalysis = client.post(
        "/api/v1/analyze/m1", params={"timestamp": "2026-01-02T00:00:00Z"}
    ).json()
    assert reanalysis["status"] == "anomalies_detected"
    assert all(f["already_recorded"] for f in reanalysis["findings"])

    # 10. Lifecycle transition works over HTTP
    resp = client.post(
        f"/api/v1/incidents/{incident_id}/review",
        json={"reason": "Investigating via smoke test"},
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "UNDER_REVIEW"

    # 11. Audit endpoint returns the transition
    audit = client.get("/api/v1/audit").json()
    assert any(
        a["entity_id"] == incident_id and "TRANSITION" in a["action"]
        for a in audit
    )

    # 12. Dashboard root HTML renders
    html = client.get("/").text
    assert "EcoFac" in html
    assert 'id="incidents"' in html
