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
):
    it_uncertainty = (
        {"absolute_tolerance": it_tol} if it_tol is not None else None
    )
    payload = {
        "decision": {
            "config": {
                "electrical": {"capacity": 1000.0, "reserve_pct": 0.10},
                "cooling": {"capacity": 800.0, "reserve_pct": 0.10},
            },
            "config_version": config_version,
            "it_load": {
                "name": "it_load",
                "value": it_value,
                "unit": "kW",
                "source": it_source,
                "uncertainty": it_uncertainty,
                "reference_id": "reading-it-1",
                "measurement_timestamp": "2026-01-01T00:00:00Z",
            },
            "cooling_load": {
                "name": "cooling_load",
                "value": cool_value,
                "unit": "kW",
                "source": "MEASURED",
            },
            "proposed_it_delta": 0.0,
            "proposed_cooling_delta": 0.0,
            "quality_eligible": quality,
            "quality_stale": quality_stale,
            "quality_flags": quality_flags or [],
            "uncertainty_flags": [],
            "conflicting_inputs": [],
        },
    }
    if supersedes is not None:
        payload["supersedes"] = supersedes
    if supersession_reason is not None:
        payload["supersession_reason"] = supersession_reason
    return payload


def _evaluate(client, **kwargs):
    return client.post(f"{BASE}/evaluate", json=_req(**kwargs))


# ---- happy path ----
def test_evaluate_approve_clean(client):
    resp = _evaluate(client)
    assert resp.status_code == 200
    body = resp.json()
    assert body["result"]["status"] == "APPROVE"
    assert body["result"]["precedence"] == "clean"
    assert body["decision_id"]


# ---- violations ----
def test_evaluate_reject_electrical_reserve(client):
    resp = _evaluate(client, it_value=950.0)
    assert resp.status_code == 200
    assert resp.json()["result"]["precedence"] == "reserve_violation"


def test_evaluate_reject_electrical_physical(client):
    resp = _evaluate(client, it_value=1100.0)
    assert resp.status_code == 200
    assert resp.json()["result"]["precedence"] == "hard_limit_violation"


def test_evaluate_reject_cooling_physical(client):
    resp = _evaluate(client, cool_value=850.0)
    assert resp.status_code == 200
    assert resp.json()["result"]["precedence"] == "hard_limit_violation"


# ---- exact thresholds ----
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


# ---- simultaneous violations ----
def test_evaluate_simultaneous_physical(client):
    resp = _evaluate(client, it_value=1100.0, cool_value=850.0)
    assert resp.json()["result"]["precedence"] == "hard_limit_violation"


def test_evaluate_simultaneous_reserve(client):
    resp = _evaluate(client, it_value=950.0, cool_value=750.0)
    assert resp.json()["result"]["precedence"] == "reserve_violation"


def test_evaluate_physical_beats_reserve(client):
    resp = _evaluate(client, it_value=1100.0, cool_value=750.0)
    assert resp.json()["result"]["precedence"] == "hard_limit_violation"


# ---- quality ----
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
    assert (
        resp.json()["result"]["precedence"]
        == "data_quality_ineligible"
    )


# ---- invalid configs ----
def test_evaluate_rejects_empty_body(client):
    resp = client.post(f"{BASE}/evaluate", json={})
    assert resp.status_code == 422


def test_evaluate_rejects_negative_capacity(client):
    payload = _req()
    payload["decision"]["config"]["electrical"]["capacity"] = -1.0
    resp = client.post(f"{BASE}/evaluate", json=payload)
    assert resp.status_code == 422


def test_evaluate_rejects_reserve_at_one(client):
    payload = _req()
    payload["decision"]["config"]["cooling"]["reserve_pct"] = 1.0
    resp = client.post(f"{BASE}/evaluate", json=payload)
    assert resp.status_code == 422


# ---- uncertainty ----
def test_evaluate_uncertainty_band_flips_verdict(client):
    resp = _evaluate(client, it_value=890.0, it_tol=20.0)
    assert resp.json()["result"]["status"] == "REVIEW"
    sensitivity = resp.json()["result"]["sensitivity"]
    it_sens = [s for s in sensitivity if s["measurement_name"] == "it_load"]
    assert len(it_sens) == 1
    assert it_sens[0]["could_change_verdict"] is True


def test_evaluate_no_tolerance_no_sensitivity(client):
    resp = _evaluate(client)
    assert resp.json()["result"]["sensitivity"] == []


# ---- determinism ----
def test_evaluate_deterministic_fingerprints(client):
    r1 = _evaluate(client)
    r2 = _evaluate(client)
    fp1 = r1.json()["result"]["fingerprint"]
    fp2 = r2.json()["result"]["fingerprint"]
    assert fp1["input_fingerprint"] == fp2["input_fingerprint"]
    assert fp1["config_fingerprint"] == fp2["config_fingerprint"]


# ---- retrieval ----
def test_get_decision_by_id(client):
    did = _evaluate(client).json()["decision_id"]
    resp = client.get(f"{BASE}/{did}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == did
    assert "request" in body
    assert "result" in body


def test_get_decision_404(client):
    assert client.get(f"{BASE}/does-not-exist").status_code == 404


def test_list_decisions_pagination(client):
    for _ in range(3):
        _evaluate(client)
    page = client.get(f"{BASE}?limit=2&offset=0").json()
    assert len(page) == 2
    page2 = client.get(f"{BASE}?limit=2&offset=2").json()
    assert len(page2) == 1


def test_list_decisions_filtered_by_status(client):
    _evaluate(client)                       # APPROVE
    _evaluate(client, it_value=1100.0)      # REJECT
    rows = client.get(f"{BASE}?status=REJECT").json()
    assert len(rows) == 1
    assert rows[0]["status"] == "REJECT"


def test_list_decisions_filtered_by_config(client):
    _evaluate(client, config_version="cfg-1")
    _evaluate(client, config_version="cfg-2")
    rows = client.get(f"{BASE}?config_version=cfg-2").json()
    assert len(rows) == 1
    assert rows[0]["config_version"] == "cfg-2"


# ---- verify ----
def test_verify_endpoint_ok(client):
    did = _evaluate(client).json()["decision_id"]
    resp = client.get(f"{BASE}/{did}/verify")
    assert resp.status_code == 200
    body = resp.json()
    assert body["verified"] is True
    assert (
        body["stored_input_fingerprint"]
        == body["computed_input_fingerprint"]
    )


def test_verify_endpoint_404(client):
    assert client.get(f"{BASE}/missing/verify").status_code == 404


# ---- supersession ----
def test_supersede_existing_decision(client):
    id_a = _evaluate(client).json()["decision_id"]
    resp = _evaluate(
        client,
        supersedes=id_a,
        supersession_reason="rebalance",
    )
    assert resp.status_code == 200
    id_b = resp.json()["decision_id"]
    chain = client.get(f"{BASE}/{id_b}/supersession-chain").json()
    ids = [item["id"] for item in chain["chain"]]
    assert ids == [id_a, id_b]


def test_supersede_missing_decision_400(client):
    resp = _evaluate(
        client,
        supersedes="missing",
        supersession_reason="oops",
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


# ---- audit and immutability ----
def test_decision_audit_events_present(client):
    did = _evaluate(client).json()["decision_id"]
    events = client.get("/api/v1/audit?limit=100").json()
    actions = [
        e["action"] for e in events if e["entity_id"] == did
    ]
    assert "DECISION_CREATED" in actions


def test_config_registered_once_via_api(client):
    _evaluate(client)
    _evaluate(client)
    events = client.get("/api/v1/audit?limit=100").json()
    registered = [
        e for e in events if e["action"] == "CONFIG_REGISTERED"
    ]
    assert len(registered) == 1


# ---- regression ----
def test_existing_endpoints_unaffected(client):
    assert client.get("/health").status_code == 200
    assert client.get("/api/v1/incidents").status_code == 200
    assert client.get("/api/v1/dashboard/stats").status_code == 200
    assert client.get("/api/v1/audit").status_code == 200
    assert client.get("/api/v1/buildings").status_code == 200
