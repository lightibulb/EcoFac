# P2-2 Phase B: analysis-retry endpoint tests.
#
# Lives in tests/api/ because it uses the client and test_db fixtures from
# tests/api/conftest.py. The test_db fixture is a sqlite3.Connection (open,
# same underlying DB the API uses) -- NOT a path. Do not wrap it in
# get_connection() and do not close it (the fixture owns its lifecycle).

import sqlite3

from ecofac.api.routes import imports as imports_route


def _post_csv(client, content, batch, source, **params):
    return client.post(
        "/api/v1/imports",
        files={"file": ("data.csv", content.encode(), "text/csv")},
        data={"source_id": source, "batch_id": batch},
        params=params,
    )


def _spike_csv():
    lines = ["timestamp,building_id,meter_id,energy_value,unit"]
    lines.extend(f"2026-01-01T{h:02d}:00:00+00:00,b1,m1,100.0,kwh" for h in range(24))
    lines.append("2026-01-02T00:00:00+00:00,b1,m1,5000.0,kwh")
    return "\n".join(lines) + "\n"


def test_analyze_endpoint_returns_summary(client):
    r = _post_csv(client, _spike_csv(), batch="rb1", source="rs1", analyze="false")
    assert r.status_code == 200, r.text

    r = client.post("/api/v1/imports/rb1/analyze")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["batch_id"] == "rb1"
    assert body["status"] == "completed"
    assert body["anomalies_detected"] >= 1
    assert body["incidents_created"] >= 1


def test_analyze_endpoint_is_idempotent(client):
    _post_csv(client, _spike_csv(), batch="rb2", source="rs2", analyze="false")
    first = client.post("/api/v1/imports/rb2/analyze").json()
    second = client.post("/api/v1/imports/rb2/analyze").json()
    assert first["status"] == "completed"
    assert second["status"] == "completed"
    assert second["incidents_created"] == 0


def test_analyze_endpoint_404_on_missing_batch(client):
    r = client.post("/api/v1/imports/does-not-exist/analyze")
    assert r.status_code == 404


def test_analyze_endpoint_409_on_started_batch(client, test_db):
    # test_db is an open sqlite3.Connection (autocommit per get_connection
    # settings). Direct inserts are visible to the API on the next request.
    test_db.execute(
        "INSERT OR IGNORE INTO data_sources "
        "(id, name, source_type, created_at) "
        "VALUES ('s9', 's9', 'csv', '2026-01-01T00:00:00Z')"
    )
    test_db.execute(
        "INSERT INTO import_batches (id, source_id, status, imported_at) "
        "VALUES ('b-started', 's9', 'started', '2026-01-01T00:00:00Z')"
    )
    r = client.post("/api/v1/imports/b-started/analyze")
    assert r.status_code == 409


def test_analysis_failure_marks_batch_and_retry_succeeds(client, test_db, monkeypatch):
    real = imports_route.analyze_all

    def boom(*_args, **_kwargs):
        raise sqlite3.Error("simulated analysis failure")

    monkeypatch.setattr(imports_route, "analyze_all", boom)
    r = _post_csv(client, _spike_csv(), batch="rb3", source="rs3", analyze="true")
    assert r.status_code == 200, r.text

    n = test_db.execute(
        "SELECT COUNT(*) AS c FROM energy_readings WHERE batch_id = 'rb3'"
    ).fetchone()["c"]
    assert n > 0
    status_after_failure = test_db.execute(
        "SELECT status FROM import_batches WHERE id = 'rb3'"
    ).fetchone()["status"]
    assert status_after_failure == "analysis_failed"

    monkeypatch.setattr(imports_route, "analyze_all", real)
    r = client.post("/api/v1/imports/rb3/analyze")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "completed"
    assert body["incidents_created"] >= 1
