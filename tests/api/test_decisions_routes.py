# Copyright 2026 Lightibulb
"""API tests for the decision engine endpoints."""

BASE = "/api/v1/decisions"


def _req(
    *,
    it_value=400.0,
    cool_value=300.0,
    it_tol=None,
    quality=True,
    quality_stale=False,
    quality_flags=None,
    it_source="MEASURED",
    config_version="cfg-1",
    supersedes=None,
    supersession_reason=None,
    pue_value=None,
    pue_source="MEASURED",
    it_unit="kW",
    cool_unit="kW",
    it_meter_id=None,
    it_timestamp=None,
    mode="hypothetical",
    cap=1000.0,
):
    it_uncertainty = (
        {"absolute_tolerance": it_tol} if it_tol is not None else None
    )
    it_load: dict = {
        "name": "it_load",
        "value": it_value,
        "unit": it_unit,
        "source": it_source,
        "uncertainty": it_uncertainty,
        "reference_id": "reading-it-1",
    }
    if it_timestamp is not None:
        it_load["measurement_timestamp"] = it_timestamp
    if it_meter_id is not None:
        it_load["meter_id"] = it_meter_id
    payload_decision: dict = {
        "config": {
            "electrical": {"capacity": cap, "reserve_pct": 0.10},
            "cooling": {"capacity": 800.0, "reserve_pct": 0.10},
        },
        "config_version": config_version,
        "it_load": it_load,
        "cooling_load": {
            "name": "cooling_load",
            "value": cool_value,
            "unit": cool_unit,
            "source": "MEASURED",
        },
        "proposed_it_delta": 0.0,
        "proposed_cooling_delta": 0.0,
        "quality_eligible": quality,
        "quality_stale": quality_stale,
        "quality_flags": quality_flags or [],
        "uncertainty_flags": [],
        "conflicting_inputs": [],
        "mode": mode,
    }
    if pue_value is not None:
        payload_decision["pue"] = {
            "name": "pue",
            "value": pue_value,
            "unit": "dimensionless",
            "source": pue_source,
        }
    payload: dict = {"decision": payload_decision}
    if supersedes is not None:
        payload["supersedes"] = supersedes
    if supersession_reason is not None:
        payload["supersession_reason"] = supersession_reason
    return payload


def _evaluate(client, **kwargs):
    return client.post(f"{BASE}/evaluate", json=_req(**kwargs))


def test_evaluate_approve_clean(client):
    resp = _evaluate(client)
    assert resp.status_code == 200
    body = resp.json()
    assert body["result"]["status"] == "APPROVE"
    assert body["decision_id"]


def test_evaluate_reject_electrical_reserve(client):
    resp = _evaluate(client, it_value=950.0)
    assert resp.json()["result"]["precedence"] == "reserve_violation"


def test_evaluate_reject_electrical_physical(client):
    resp = _evaluate(client, it_value=1100.0)
    assert resp.json()["result"]["precedence"] == "hard_limit_violation"


def test_evaluate_reject_cooling_physical(client):
    resp = _evaluate(client, cool_value=850.0)
    assert resp.json()["result"]["precedence"] == "hard_limit_violation"


def test_evaluate_boundary_at_permitted(client):
    resp = _evaluate(client, it_value=900.0)
    assert resp.json()["result"]["precedence"] == "near_threshold"


def test_evaluate_boundary_just_above_permitted(client):
    resp = _evaluate(client, it_value=901.0)
    assert resp.json()["result"]["precedence"] == "reserve_violation"


def test_evaluate_boundary_at_capacity(client):
    resp = _evaluate(client, it_value=1000.0)
    assert resp.json()["result"]["precedence"] == "reserve_violation"


def test_evaluate_boundary_just_above_capacity(client):
    resp = _evaluate(client, it_value=1001.0)
    assert resp.json()["result"]["precedence"] == "hard_limit_violation"


def test_evaluate_simultaneous_physical(client):
    resp = _evaluate(client, it_value=1100.0, cool_value=850.0)
    assert resp.json()["result"]["precedence"] == "hard_limit_violation"


def test_evaluate_simultaneous_reserve(client):
    resp = _evaluate(client, it_value=950.0, cool_value=750.0)
    assert resp.json()["result"]["precedence"] == "reserve_violation"


def test_evaluate_physical_beats_reserve(client):
    resp = _evaluate(client, it_value=1100.0, cool_value=750.0)
    assert resp.json()["result"]["precedence"] == "hard_limit_violation"


def test_evaluate_review_quality(client):
    resp = _evaluate(client, quality=False, quality_flags=["missing_data"])
    assert resp.json()["result"]["status"] == "REVIEW"
    assert resp.json()["result"]["precedence"] == "data_quality_ineligible"


def test_evaluate_review_stale(client):
    resp = _evaluate(client, quality_stale=True)
    assert resp.json()["result"]["status"] == "REVIEW"


def test_evaluate_missing_it_load(client):
    resp = _evaluate(client, it_value=None, it_source="MISSING")
    assert resp.json()["result"]["status"] == "REVIEW"


def test_evaluate_rejects_empty_body(client):
    assert client.post(f"{BASE}/evaluate", json={}).status_code == 422


def test_evaluate_rejects_negative_capacity(client):
    payload = _req()
    payload["decision"]["config"]["electrical"]["capacity"] = -1.0
    assert client.post(f"{BASE}/evaluate", json=payload).status_code == 422


def test_evaluate_rejects_reserve_at_one(client):
    payload = _req()
    payload["decision"]["config"]["cooling"]["reserve_pct"] = 1.0
    assert client.post(f"{BASE}/evaluate", json=payload).status_code == 422


def test_evaluate_uncertainty_band_flips_verdict(client):
    resp = _evaluate(client, it_value=890.0, it_tol=20.0)
    sensitivity = resp.json()["result"]["sensitivity"]
    it_sens = [s for s in sensitivity if s["measurement_name"] == "it_load"]
    assert len(it_sens) == 1
    assert it_sens[0]["could_change_verdict"] is True


def test_evaluate_no_tolerance_no_sensitivity(client):
    resp = _evaluate(client)
    assert resp.json()["result"]["sensitivity"] == []


def test_evaluate_deterministic_fingerprints(client):
    fp1 = _evaluate(client).json()["result"]["fingerprint"]
    fp2 = _evaluate(client).json()["result"]["fingerprint"]
    assert fp1["input_fingerprint"] == fp2["input_fingerprint"]


def test_get_decision_by_id(client):
    did = _evaluate(client).json()["decision_id"]
    body = client.get(f"{BASE}/{did}").json()
    assert body["id"] == did
    assert "request" in body


def test_get_decision_404(client):
    assert client.get(f"{BASE}/does-not-exist").status_code == 404


def test_list_decisions_pagination(client):
    for _ in range(3):
        _evaluate(client)
    page = client.get(f"{BASE}?limit=2&offset=0").json()
    assert len(page) == 2


def test_list_decisions_filtered_by_status(client):
    _evaluate(client)
    _evaluate(client, it_value=1100.0)
    rows = client.get(f"{BASE}?status=REJECT").json()
    assert len(rows) == 1


def test_list_decisions_filtered_by_config(client):
    _evaluate(client, config_version="cfg-1")
    _evaluate(client, config_version="cfg-2")
    rows = client.get(f"{BASE}?config_version=cfg-2").json()
    assert len(rows) == 1


def test_verify_endpoint_ok(client):
    did = _evaluate(client).json()["decision_id"]
    body = client.get(f"{BASE}/{did}/verify").json()
    assert body["verified"] is True
    assert body["output_verified"] is True


def test_verify_endpoint_404(client):
    assert client.get(f"{BASE}/missing/verify").status_code == 404


def test_supersede_existing_decision(client):
    id_a = _evaluate(client).json()["decision_id"]
    resp = _evaluate(
        client, supersedes=id_a, supersession_reason="rebalance"
    )
    assert resp.status_code == 200
    id_b = resp.json()["decision_id"]
    chain = client.get(f"{BASE}/{id_b}/supersession-chain").json()
    ids = [item["id"] for item in chain["chain"]]
    assert ids == [id_a, id_b]


def test_supersede_missing_decision_400(client):
    resp = _evaluate(
        client, supersedes="missing", supersession_reason="oops"
    )
    assert resp.status_code == 400


def test_supersede_requires_reason(client):
    id_a = _evaluate(client).json()["decision_id"]
    resp = _evaluate(client, supersedes=id_a, supersession_reason="   ")
    assert resp.status_code == 400


def test_supersession_chain_404(client):
    assert (
        client.get(f"{BASE}/missing/supersession-chain").status_code == 404
    )


def test_decision_audit_events_present(client):
    did = _evaluate(client).json()["decision_id"]
    events = client.get("/api/v1/audit?limit=100").json()
    actions = [e["action"] for e in events if e["entity_id"] == did]
    assert "DECISION_CREATED" in actions


def test_config_registered_once_via_api(client):
    _evaluate(client)
    _evaluate(client)
    events = client.get("/api/v1/audit?limit=100").json()
    registered = [e for e in events if e["action"] == "CONFIG_REGISTERED"]
    assert len(registered) == 1


def test_existing_endpoints_unaffected(client):
    assert client.get("/health").status_code == 200
    assert client.get("/api/v1/incidents").status_code == 200
    assert client.get("/api/v1/dashboard/stats").status_code == 200
    assert client.get("/api/v1/audit").status_code == 200


# ---- PUE via API ----
def test_evaluate_pue_flips_verdict(client):
    resp = _evaluate(client, it_value=400.0, pue_value=1.4, cap=500.0)
    assert resp.json()["result"]["precedence"] == "hard_limit_violation"


def test_evaluate_pue_recorded_in_evidence(client):
    body = _evaluate(client, it_value=400.0, pue_value=1.3).json()
    inputs = body["result"]["evidence"]["calculation_inputs"]
    assert inputs["pue"] == 1.3


def test_evaluate_pue_wrong_unit_400(client):
    payload = _req(pue_value=1.4)
    payload["decision"]["pue"]["unit"] = "kW"
    assert client.post(f"{BASE}/evaluate", json=payload).status_code == 400


# ---- Negative proposed delta ----
def test_evaluate_negative_delta_rejected(client):
    payload = _req(it_value=400.0)
    payload["decision"]["proposed_it_delta"] = -500.0
    resp = client.post(f"{BASE}/evaluate", json=payload)
    assert resp.status_code == 200
    assert resp.json()["result"]["precedence"] == "invalid_proposal"


# ---- Wrong unit ----
def test_evaluate_wrong_unit_400(client):
    payload = _req(it_value=400.0, it_unit="W")
    resp = client.post(f"{BASE}/evaluate", json=payload)
    assert resp.status_code == 400


# ---- Auth ----
def test_evaluate_no_token_401_when_env_set(client, monkeypatch):
    monkeypatch.setenv("ECOFAC_WRITE_TOKEN", "secret")
    resp = _evaluate(client)
    assert resp.status_code == 401


def test_evaluate_with_token_200(client, monkeypatch):
    monkeypatch.setenv("ECOFAC_WRITE_TOKEN", "secret")
    resp = client.post(
        f"{BASE}/evaluate",
        json=_req(),
        headers={"X-EcoFac-Token": "secret"},
    )
    assert resp.status_code == 200


def test_evaluate_wrong_token_401(client, monkeypatch):
    monkeypatch.setenv("ECOFAC_WRITE_TOKEN", "secret")
    resp = client.post(
        f"{BASE}/evaluate",
        json=_req(),
        headers={"X-EcoFac-Token": "wrong"},
    )
    assert resp.status_code == 401


# ---- Trusted mode ----
def test_trusted_mode_requires_meter_and_timestamp(client):
    resp = _evaluate(client, mode="trusted")
    assert resp.status_code == 400


def test_trusted_mode_missing_reading_400(client):
    resp = _evaluate(
        client,
        mode="trusted",
        it_meter_id="nonexistent",
        it_timestamp="2026-01-01T00:00:00Z",
    )
    assert resp.status_code == 400


def test_trusted_mode_resolves_valid_reading(client, test_db):
    test_db.execute(
        "INSERT OR IGNORE INTO import_batches "
        "(id, source_id, status, imported_at) "
        "VALUES ('ib1', 's1', 'started', '2026-01-01T00:00:00Z')"
    )
    test_db.execute(
        "INSERT INTO energy_readings "
        "(id, meter_id, timestamp_utc, value, unit, source_id, batch_id, "
        "is_valid, created_at) "
        "VALUES ('r1', 'm1', '2026-01-01T00:00:00Z', 450.0, 'kWh', 's1', "
        "'ib1', 1, '2026-01-01T00:00:00Z')"
    )
    resp = _evaluate(
        client,
        mode="trusted",
        it_value=999.0,  # will be overridden
        it_meter_id="m1",
        it_timestamp="2026-01-01T00:00:00Z",
        cool_value=100.0,
    )
    assert resp.status_code == 200
    inputs = resp.json()["result"]["evidence"]["calculation_inputs"]
    assert inputs["current_it_load_kw"] == 450.0


def test_trusted_mode_rejects_invalid_reading(client, test_db):
    test_db.execute(
        "INSERT OR IGNORE INTO import_batches "
        "(id, source_id, status, imported_at) "
        "VALUES ('ib1', 's1', 'started', '2026-01-01T00:00:00Z')"
    )
    test_db.execute(
        "INSERT INTO energy_readings "
        "(id, meter_id, timestamp_utc, value, unit, source_id, batch_id, "
        "is_valid, created_at) "
        "VALUES ('r1', 'm1', '2026-01-01T00:00:00Z', 450.0, 'kWh', 's1', "
        "'ib1', 0, '2026-01-01T00:00:00Z')"
    )
    resp = _evaluate(
        client,
        mode="trusted",
        it_meter_id="m1",
        it_timestamp="2026-01-01T00:00:00Z",
    )
    assert resp.status_code == 400
