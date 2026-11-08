# Copyright 2026 Lightibulb
"""Regression tests for the Part 2 related fixes (LMF-P2-01..04)."""

import json

POLICIES = "/api/v1/policies"
WORLD_TD = "/api/v1/world/test-drive"


# --- P2-01: presets -----------------------------------------------------------
def test_test_drive_exposes_all_five_presets(client):
    resp = client.get(f"{WORLD_TD}/presets")
    assert resp.status_code == 200, resp.text
    ids = {p["id"] for p in resp.json()["presets"]}
    assert {"baseline", "plus_10pct", "plus_20pct", "plus_40pct", "plus_60pct"} <= ids


def test_baseline_and_plus60_match_expectations(client):
    for preset in ("baseline", "plus_60pct"):
        resp = client.get(f"{WORLD_TD}/{preset}")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body.get("matches_expected") is True, preset


def test_plus60_is_hard_limit_reject(client):
    resp = client.get(f"{WORLD_TD}/plus_60pct")
    assert resp.status_code == 200, resp.text
    blob = json.dumps(resp.json())
    assert "REJECT" in blob
    assert "hard_limit_violation" in blob


# --- P2-03: policy manager ----------------------------------------------------
def test_active_policy_auto_default(client):
    resp = client.get(f"{POLICIES}/active")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["version"] == "default-v1"
    assert body["is_active"] is True
    assert body["electrical_capacity_kw"] > 0
    assert body["fingerprint"]
    assert "electrical" in body["constraints"]


def test_policy_crud_activation_and_conflict(client):
    payload = {
        "version": "lmf-p2-test-v1",
        "electrical_capacity_kw": 1000.0,
        "electrical_reserve_pct": 0.10,
        "electrical_warning_threshold_pct": 0.10,
        "electrical_min_headroom_kw": 0.0,
        "cooling_capacity_kw": 800.0,
        "cooling_reserve_pct": 0.10,
        "cooling_warning_threshold_pct": 0.10,
        "cooling_min_headroom_kw": 0.0,
        "notes": "part-2 regression policy",
    }
    resp = client.post(POLICIES, json=payload)
    assert resp.status_code == 201, resp.text
    policy_id = resp.json()["id"]
    assert resp.json()["fingerprint"]

    assert client.post(
        f"{POLICIES}/{policy_id}/activate").status_code == 200
    assert client.get(
        f"{POLICIES}/{policy_id}").json()["is_active"] is True
    assert client.get(
        f"{POLICIES}/active").json()["version"] == "lmf-p2-test-v1"

    assert client.post(POLICIES, json=payload).status_code == 409

    # Test DB is isolated per test; no need to restore default policy.


def test_unknown_policy_404(client):
    assert client.get(f"{POLICIES}/nope").status_code == 404
    assert client.post(f"{POLICIES}/nope/activate").status_code == 404


# --- P2-04: page + navigation ---------------------------------------------------
def test_policies_page_is_served(client):
    resp = client.get("/policies")
    assert resp.status_code == 200, resp.text
    assert "Facility Operating Policies" in resp.text


def test_nav_links_point_at_policies(client):
    for page in ("/", "/decisions", "/scenarios", "/world"):
        html = client.get(page).text
        assert 'href="/policies"' in html, page


def test_world_page_has_canonical_testdrive(client):
    html = client.get("/world").text
    assert "lmf-td-canonical" in html
    assert "btn-eval-plus10" in html
    assert "btn-eval-plus40" in html


def test_scenario_workbench_policy_selector(client):
    html = client.get("/scenarios").text
    assert 'id="policy-mode"' in html
    assert "Use Active Policy" in html
    assert "Override for This Scenario" in html
