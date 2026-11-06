# Copyright 2026 Lightibulb
"""Scenario Intelligence API end-to-end tests."""

import json
import sqlite3

import pytest

BASE = "/api/v1/scenarios"


def _body(
    *,
    name="baseline",
    it_load_kw=400.0,
    cooling_load_kw=300.0,
    pue=1.0,
    proposed_it_delta_kw=0.0,
    proposed_cooling_delta_kw=0.0,
    cap=1000.0,
    cool_cap=800.0,
    reserve=0.10,
    config_version="cfg-1",
    persist_as_decision=False,
    it_source="ASSUMED",
    cooling_source="ASSUMED",
    pue_source="ASSUMED",
):
    return {
        "name": name,
        "config": {
            "electrical": {"capacity": cap, "reserve_pct": reserve},
            "cooling": {"capacity": cool_cap, "reserve_pct": reserve},
        },
        "config_version": config_version,
        "it_load_kw": it_load_kw,
        "cooling_load_kw": cooling_load_kw,
        "pue": pue,
        "proposed_it_delta_kw": proposed_it_delta_kw,
        "proposed_cooling_delta_kw": proposed_cooling_delta_kw,
        "it_source": it_source,
        "cooling_source": cooling_source,
        "pue_source": pue_source,
        "persist_as_decision": persist_as_decision,
    }


def _post(client, **kwargs):
    return client.post(f"{BASE}/evaluate", json=_body(**kwargs))


def _post_raw(client, payload):
    """Send raw JSON so allow_nan=True can serialize Infinity/NaN."""
    raw = json.dumps(payload, allow_nan=True).encode("utf-8")
    return client.post(
        f"{BASE}/evaluate",
        content=raw,
        headers={"Content-Type": "application/json"},
    )


def test_evaluate_approve(client):
    resp = _post(client)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["result"]["status"] == "APPROVE"
    assert body["scenario_id"]
    assert body["decision_id"] is None


def test_evaluate_reject_physical(client):
    resp = _post(client, it_load_kw=1100.0, pue=1.4)
    body = resp.json()
    assert body["result"]["status"] == "REJECT"
    assert body["result"]["precedence"] == "hard_limit_violation"


def test_evaluate_reject_reserve(client):
    resp = _post(client, it_load_kw=950.0, pue=1.0)
    body = resp.json()
    assert body["result"]["precedence"] == "reserve_violation"


def test_evaluate_review_near_threshold(client):
    resp = _post(client, it_load_kw=850.0, pue=1.0)
    body = resp.json()
    assert body["result"]["status"] == "REVIEW"
    assert body["result"]["precedence"] == "near_threshold"


def test_evaluate_invalid_proposal(client):
    resp = _post(client, proposed_it_delta_kw=-500.0)
    assert resp.status_code == 200
    body = resp.json()
    assert body["result"]["status"] == "REJECT"
    assert body["result"]["precedence"] == "invalid_proposal"


def test_evaluate_pue_increases_power(client):
    resp = _post(client, it_load_kw=400.0, pue=1.4)
    body = resp.json()
    inputs = body["result"]["evidence"]["calculation_inputs"]
    assert inputs["current_facility_power_kw"] == 400.0 * 1.4
    assert inputs["pue"] == 1.4
    assert inputs["pue_provenance"] == "ASSUMED"


def test_reject_inf_it_load(client):
    payload = _body()
    payload["it_load_kw"] = float("inf")
    resp = _post_raw(client, payload)
    assert resp.status_code == 422
    detail = resp.json()["detail"]
    assert isinstance(detail, list)
    assert "finite" in str(detail).lower()


def test_reject_inf_pue(client):
    payload = _body()
    payload["pue"] = float("inf")
    resp = _post_raw(client, payload)
    assert resp.status_code == 422
    detail = resp.json()["detail"]
    assert isinstance(detail, list)
    assert "finite" in str(detail).lower()


def test_reject_zero_pue(client):
    resp = _post(client, pue=0.0)
    assert resp.status_code == 422


def test_reject_negative_it_load(client):
    resp = _post(client, it_load_kw=-1.0)
    assert resp.status_code == 422


def test_reject_negative_capacity(client):
    resp = _post(client, cap=-100.0)
    assert resp.status_code == 422


def test_reject_reserve_at_one(client):
    resp = _post(client, reserve=1.0)
    assert resp.status_code == 422


def test_reject_empty_name(client):
    resp = _post(client, name="")
    assert resp.status_code == 422


def test_does_not_pollute_decisions_by_default(client):
    resp = _post(client)
    assert resp.status_code == 200
    body = resp.json()
    assert body["decision_id"] is None

    decisions = client.get("/api/v1/decisions").json()
    assert decisions == []

    scenarios = client.get(BASE).json()
    assert len(scenarios) == 1


def test_persist_as_decision_creates_both_rows(client):
    resp = _post(client, persist_as_decision=True)
    assert resp.status_code == 200
    body = resp.json()
    decision_id = body["decision_id"]
    assert decision_id is not None

    decisions = client.get("/api/v1/decisions").json()
    assert len(decisions) == 1
    assert decisions[0]["id"] == decision_id

    scenarios = client.get(BASE).json()
    assert len(scenarios) == 1
    assert scenarios[0]["decision_id"] == decision_id


def test_list_pagination(client):
    for _ in range(3):
        _post(client, name="s")
    resp = client.get(f"{BASE}?limit=2&offset=0")
    assert len(resp.json()) == 2
    assert resp.headers["X-Total-Count"] == "3"
    resp2 = client.get(f"{BASE}?limit=2&offset=2")
    assert len(resp2.json()) == 1


def test_list_filter_by_status(client):
    _post(client, name="approve", it_load_kw=400.0)
    _post(client, name="reject", it_load_kw=1100.0, pue=1.4)
    rows = client.get(f"{BASE}?status=REJECT").json()
    assert len(rows) == 1
    assert rows[0]["status"] == "REJECT"


def test_list_filter_by_config_version(client):
    _post(client, config_version="cfg-A")
    _post(client, config_version="cfg-B")
    rows = client.get(f"{BASE}?config_version=cfg-B").json()
    assert len(rows) == 1
    assert rows[0]["config_version"] == "cfg-B"


def test_list_x_total_count_header(client):
    for _ in range(3):
        _post(client)
    resp = client.get(f"{BASE}?limit=1&offset=0")
    assert resp.headers["X-Total-Count"] == "3"


def test_get_by_id(client):
    scenario_id = _post(client).json()["scenario_id"]
    resp = client.get(f"{BASE}/{scenario_id}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == scenario_id
    assert "inputs" in body
    assert "config" in body
    assert "result" in body
    assert body["input_fingerprint"]
    assert body["config_fingerprint"]


def test_get_404(client):
    resp = client.get(f"{BASE}/missing")
    assert resp.status_code == 404


def test_verify_ok(client):
    scenario_id = _post(client).json()["scenario_id"]
    resp = client.get(f"{BASE}/{scenario_id}/verify")
    assert resp.status_code == 200
    body = resp.json()
    assert body["verified"] is True
    assert body["output_verified"] is True
    assert body["verification_scope"] == "input_config_output_status"
    assert body["full_bundle_reproduced"] is False


def test_verify_404(client):
    resp = client.get(f"{BASE}/missing/verify")
    assert resp.status_code == 404


def test_requires_auth_when_token_set(client, monkeypatch):
    monkeypatch.setenv("ECOFAC_WRITE_TOKEN", "secret")
    resp = client.post(f"{BASE}/evaluate", json=_body())
    assert resp.status_code == 401


def test_with_auth_token(client, monkeypatch):
    monkeypatch.setenv("ECOFAC_WRITE_TOKEN", "secret")
    resp = client.post(
        f"{BASE}/evaluate",
        json=_body(),
        headers={"X-EcoFac-Token": "secret"},
    )
    assert resp.status_code == 200


def test_auto_generated_config_version(client):
    resp = _post(client, config_version="")
    assert resp.status_code == 200
    scenarios = client.get(BASE).json()
    assert len(scenarios) == 1
    assert scenarios[0]["config_version"].startswith("cfg-")


def test_append_only_scenarios(client, test_db):
    scenario_id = _post(client).json()["scenario_id"]
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        test_db.execute(
            "UPDATE scenarios SET name = 'tampered' WHERE id = ?",
            (scenario_id,),
        )
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        test_db.execute("DELETE FROM scenarios WHERE id = ?", (scenario_id,))
