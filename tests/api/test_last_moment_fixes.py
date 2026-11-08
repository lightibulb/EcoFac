# Copyright 2026 Lightibulb
"""Regression tests for the last-moment fix bundle (LMF-01 .. LMF-08)."""

INC = "/api/v1/incidents"
WORLD_TD = "/api/v1/world/test-drive"
DASH = "/api/v1/dashboard"


def _mk_incident(test_db, idx, status="DETECTED"):
    ts = f"2026-02-01T{idx:02d}:00:00Z"
    aid = f"lmf-an-{idx}"
    iid = f"lmf-inc-{idx}"
    test_db.execute(
        "INSERT INTO anomalies (id, building_id, meter_id, timestamp_utc, "
        "observed_consumption, expected_consumption, absolute_deviation, "
        "percentage_deviation, detector_name, severity, "
        "is_data_quality_eligible, detection_timestamp) "
        "VALUES (?, 'b1', 'm1', ?, 100, 10, 90, 900, "
        "'robust_mad', 'high', 1, ?)",
        (aid, ts, ts),
    )
    test_db.execute(
        "INSERT INTO incidents (id, anomaly_id, status, created_at, "
        "updated_at) VALUES (?, ?, ?, ?, ?)",
        (iid, aid, status, ts, ts),
    )
    return iid


def _first_preset_id(client):
    """Discover a real test-drive preset id from the presets endpoint."""
    resp = client.get(f"{WORLD_TD}/presets")
    assert resp.status_code == 200, resp.text
    data = resp.json()
    if isinstance(data, dict):
        data = data.get("presets", data)
    if isinstance(data, list) and data:
        first = data[0]
        if isinstance(first, dict):
            for key in ("id", "preset_id", "name"):
                if key in first:
                    return first[key]
        else:
            return str(first)
    return "plus10"


# --- LMF-01: pagination + status filter -------------------------------------
def test_incident_list_paginates_and_reports_total(client, test_db):
    for i in range(1, 8):
        _mk_incident(test_db, i)
    test_db.commit()
    resp = client.get(INC, params={"limit": 3, "offset": 0})
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 3
    assert int(resp.headers["X-Total-Count"]) >= 7
    page2 = client.get(INC, params={"limit": 3, "offset": 3}).json()
    assert [r["id"] for r in page2] != [r["id"] for r in body]


def test_incident_list_status_filter(client, test_db):
    _mk_incident(test_db, 11, status="RESOLVED")
    test_db.commit()
    body = client.get(INC, params={"status": "RESOLVED", "limit": 100}).json()
    assert body
    assert "lmf-inc-11" in [r["id"] for r in body]
    assert all(r["status"] == "RESOLVED" for r in body)


# --- LMF-02: resolve requires a reason ---------------------------------------
def _to_acknowledged(client, test_db, idx):
    iid = _mk_incident(test_db, idx)
    test_db.commit()
    assert client.post(f"{INC}/{iid}/review", json={}).status_code == 200
    assert client.post(
        f"{INC}/{iid}/acknowledge", json={}).status_code == 200
    return iid


def test_resolve_requires_reason(client, test_db):
    iid = _to_acknowledged(client, test_db, 21)
    assert client.post(f"{INC}/{iid}/resolve", json={}).status_code == 422
    assert client.post(f"{INC}/{iid}/resolve").status_code == 422
    assert client.post(
        f"{INC}/{iid}/resolve", json={"reason": "   "}).status_code == 422
    row = test_db.execute(
        "SELECT status FROM incidents WHERE id = ?", (iid,)
    ).fetchone()
    assert row["status"] == "ACKNOWLEDGED"


def test_resolve_with_reason_persists_notes(client, test_db):
    iid = _to_acknowledged(client, test_db, 22)
    resp = client.post(
        f"{INC}/{iid}/resolve", json={"reason": "Repaired faulty sensor"}
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "RESOLVED"
    row = test_db.execute(
        "SELECT resolution_notes FROM incidents WHERE id = ?", (iid,)
    ).fetchone()
    assert row["resolution_notes"] == "Repaired faulty sensor"


def test_resolve_unknown_incident_404(client):
    resp = client.post(f"{INC}/does-not-exist/resolve", json={"reason": "x"})
    assert resp.status_code == 404


# --- LMF-04: test-drive GET endpoint -----------------------------------------
def test_test_drive_get_preset_available(client):
    preset_id = _first_preset_id(client)
    resp = client.get(f"{WORLD_TD}/{preset_id}")
    assert resp.status_code == 200, resp.text
    assert isinstance(resp.json(), dict)


def test_test_drive_unknown_preset_is_client_error(client):
    resp = client.get(f"{WORLD_TD}/definitely-not-a-preset")
    assert resp.status_code in (400, 404, 422)


def test_test_drive_does_not_record_decision(client, test_db):
    preset_id = _first_preset_id(client)
    before = test_db.execute(
        "SELECT COUNT(*) n FROM decisions").fetchone()["n"]
    assert client.get(f"{WORLD_TD}/{preset_id}").status_code == 200
    after = test_db.execute(
        "SELECT COUNT(*) n FROM decisions").fetchone()["n"]
    assert before == after


# --- LMF-08: re-analyse + run manifest ---------------------------------------
def test_run_manifest_shape(client):
    resp = client.get(f"{DASH}/run-manifest")
    assert resp.status_code == 200
    body = resp.json()
    for key in ("buildings", "meters", "readings", "anomalies", "incidents"):
        assert key in body["counts"]
    assert "generated_at" in body


def test_reanalyze_endpoint(client):
    resp = client.post(f"{DASH}/reanalyze")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "reanalyzed"
    for key in ("anomalies_detected", "anomalies_gated", "skipped"):
        assert key in body


# --- frontend contracts -------------------------------------------------------
def test_world_page_has_record_checkbox(client):
    html = client.get("/world").text
    assert 'id="lmf-record-decision"' in html
    assert "Also record in Decision Center" in html
    assert "/api/v1/world/test-drive/" in html


def test_world_page_test_drive_buttons_intact(client):
    html = client.get("/world").text
    # Test-drive buttons render as .preset elements keyed by
    # data-preset="<id>"; the old fixed IDs btn-eval-plus10/40 are gone.
    assert "data-preset" in html, (
        "world.html should render test-drive buttons with data-preset"
    )


def test_dashboard_has_lmf_panels(client):
    html = client.get("/").text
    for element_id in (
        "lmf-incident-history",
        "lmf-current-readings",
        "lmf-demo-controls",
    ):
        assert f'id="{element_id}"' in html
    assert "X-Total-Count" in html
    assert "/api/v1/dashboard/run-manifest" in html


def test_dashboard_severity_uses_max_severity(client):
    html = client.get("/").text
    assert "/api/v1/world/state" in html
    assert "max_severity" in html


def test_label_overlay_on_decision_pages(client):
    for page in ("/decisions", "/scenarios"):
        html = client.get(page).text
        assert 'id="lmf-label-overlay"' in html, page
