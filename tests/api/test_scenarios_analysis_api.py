# Copyright 2026 Lightibulb
"""Scenario Intelligence analysis endpoints."""

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
        "it_source": "ASSUMED",
        "cooling_source": "ASSUMED",
        "pue_source": "ASSUMED",
        "persist_as_decision": False,
    }


def _create(client, **kwargs):
    resp = client.post(f"{BASE}/evaluate", json=_body(**kwargs))
    assert resp.status_code == 200, resp.text
    return resp.json()["scenario_id"]


def test_engineering_endpoint_returns_summary(client):
    sid = _create(client, it_load_kw=400.0, pue=1.4)
    resp = client.get(f"{BASE}/{sid}/engineering")
    assert resp.status_code == 200
    body = resp.json()
    assert body["pue"] == 1.4
    assert body["current_facility_power_kw"] == 400.0 * 1.4
    assert "current_electrical" in body
    assert "headroom_to_permitted_kw" in body["current_electrical"]


def test_engineering_endpoint_404(client):
    resp = client.get(f"{BASE}/missing/engineering")
    assert resp.status_code == 404


def test_bottlenecks_approve_clean(client):
    # Default scenario: cooling has smaller headroom (420 vs 500 kW),
    # so it is the most restrictive constraint.
    sid = _create(client, it_load_kw=400.0, cap=1000.0)
    resp = client.get(f"{BASE}/{sid}/bottlenecks")
    assert resp.status_code == 200
    body = resp.json()
    assert body["has_enough_information"] is True
    assert body["most_restrictive_constraint"] == "cooling"


def test_bottlenecks_reject_physical(client):
    sid = _create(client, it_load_kw=1100.0, pue=1.4, cap=1000.0)
    resp = client.get(f"{BASE}/{sid}/bottlenecks")
    body = resp.json()
    categories = [f["category"] for f in body["findings"]]
    assert "electrical_over_physical" in categories


def test_bottlenecks_invalid_proposal(client):
    sid = _create(client, proposed_it_delta_kw=-500.0)
    resp = client.get(f"{BASE}/{sid}/bottlenecks")
    body = resp.json()
    assert body["has_enough_information"] is False
    assert body["findings"] == []


def test_bottlenecks_404(client):
    resp = client.get(f"{BASE}/missing/bottlenecks")
    assert resp.status_code == 404


def test_sensitivity_endpoint_returns_all_parameters(client):
    sid = _create(client)
    resp = client.get(f"{BASE}/{sid}/sensitivity")
    assert resp.status_code == 200
    body = resp.json()
    names = {p["parameter"] for p in body["parameters"]}
    assert "it_load_kw" in names
    assert "pue" in names
    assert body["baseline_status"] == "APPROVE"


def test_sensitivity_endpoint_detects_flip(client):
    sid = _create(client, it_load_kw=850.0, pue=1.0, cap=1000.0)
    resp = client.get(f"{BASE}/{sid}/sensitivity")
    body = resp.json()
    pue_param = next(p for p in body["parameters"] if p["parameter"] == "pue")
    assert pue_param["verdict_changed"] is True


def test_sensitivity_endpoint_404(client):
    resp = client.get(f"{BASE}/missing/sensitivity")
    assert resp.status_code == 404


def test_compare_two_scenarios(client):
    id_a = _create(client, it_load_kw=400.0)
    id_b = _create(client, it_load_kw=500.0)
    resp = client.post(f"{BASE}/compare", json={"scenario_ids": [id_a, id_b]})
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["scenarios"]) == 2
    by_field = {d["field_name"]: d for d in body["input_differences"]}
    assert by_field["it_load_kw"]["differs"] is True
    assert body["outcome_explanation"]


def test_compare_flags_mixed_config_versions(client):
    id_a = _create(client, config_version="cfg-A")
    id_b = _create(client, config_version="cfg-B")
    resp = client.post(f"{BASE}/compare", json={"scenario_ids": [id_a, id_b]})
    body = resp.json()
    assert body["mixed_config_versions"] is True
    assert set(body["config_versions"]) == {"cfg-A", "cfg-B"}


def test_compare_rejects_single_scenario(client):
    id_a = _create(client)
    resp = client.post(f"{BASE}/compare", json={"scenario_ids": [id_a]})
    assert resp.status_code == 422


def test_compare_rejects_more_than_ten(client):
    ids = [_create(client) for _ in range(2)]
    payload = {"scenario_ids": [ids[0]] * 11}
    resp = client.post(f"{BASE}/compare", json=payload)
    assert resp.status_code == 422


def test_compare_missing_scenario_404(client):
    id_a = _create(client)
    resp = client.post(
        f"{BASE}/compare",
        json={"scenario_ids": [id_a, "missing-id"]},
    )
    assert resp.status_code == 404
    assert "missing-id" in resp.json()["detail"]


def test_compare_identical_inputs_no_differences(client):
    id_a = _create(client)
    id_b = _create(client)
    resp = client.post(f"{BASE}/compare", json={"scenario_ids": [id_a, id_b]})
    body = resp.json()
    for diff in body["input_differences"]:
        assert diff["differs"] is False


def test_all_new_endpoints_in_openapi(client):
    schema = client.get("/openapi.json").json()
    paths = schema["paths"]
    assert "/api/v1/scenarios/{scenario_id}/engineering" in paths
    assert "/api/v1/scenarios/{scenario_id}/bottlenecks" in paths
    assert "/api/v1/scenarios/{scenario_id}/sensitivity" in paths
    assert "/api/v1/scenarios/compare" in paths
