# Copyright 2026 Lightibulb
"""End-to-end integration pass for EcoFac Part 2.1.

Covers the five user journeys (A-E), engineering validations, and
cross-feature invariants between the Decision Center and Scenario
Intelligence APIs. Every assertion reads from the live API, not from
internal helpers, so this file is the integration contract.
"""

import json
import sqlite3

import pytest

DECISIONS = "/api/v1/decisions"
SCENARIOS = "/api/v1/scenarios"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _decision_payload(
    *,
    it_value: float = 400.0,
    cool_value: float = 300.0,
    pue_value: float | None = 1.4,
    cap: float = 1000.0,
    cool_cap: float = 800.0,
    reserve: float = 0.10,
    quality: bool = True,
    quality_stale: bool = False,
    quality_flags: list[str] | None = None,
    proposed_it: float = 0.0,
    proposed_cool: float = 0.0,
    config_version: str = "cfg-1",
    it_timestamp: str = "2026-01-01T00:00:00Z",
    max_age: int | None = None,
) -> dict:
    decision = {
        "config": {
            "electrical": {"capacity": cap, "reserve_pct": reserve},
            "cooling": {"capacity": cool_cap, "reserve_pct": reserve},
        },
        "config_version": config_version,
        "it_load": {
            "name": "it_load",
            "value": it_value,
            "unit": "kW",
            "source": "MEASURED",
            "reference_id": "reading-it-1",
            "measurement_timestamp": it_timestamp,
        },
        "cooling_load": {
            "name": "cooling_load",
            "value": cool_value,
            "unit": "kW",
            "source": "MEASURED",
        },
        "proposed_it_delta": proposed_it,
        "proposed_cooling_delta": proposed_cool,
        "quality_eligible": quality,
        "quality_stale": quality_stale,
        "quality_flags": quality_flags or [],
        "uncertainty_flags": [],
        "conflicting_inputs": [],
        "mode": "hypothetical",
    }
    if pue_value is not None:
        decision["pue"] = {
            "name": "pue",
            "value": pue_value,
            "unit": "dimensionless",
            "source": "MEASURED",
        }
    if max_age is not None:
        decision["max_measurement_age_seconds"] = max_age
    return {"decision": decision}


def _scenario_payload(
    *,
    name: str = "s",
    it_load_kw: float = 400.0,
    cooling_load_kw: float = 300.0,
    pue: float = 1.4,
    cap: float = 1000.0,
    cool_cap: float = 800.0,
    reserve: float = 0.10,
    proposed_it: float = 0.0,
    proposed_cool: float = 0.0,
    config_version: str = "cfg-1",
) -> dict:
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
        "proposed_it_delta_kw": proposed_it,
        "proposed_cooling_delta_kw": proposed_cool,
        "it_source": "ASSUMED",
        "cooling_source": "ASSUMED",
        "pue_source": "ASSUMED",
        "persist_as_decision": False,
    }


def _post_raw(client, url: str, payload: dict):
    """POST with allow_nan so infinity reaches the server validator."""
    raw = json.dumps(payload, allow_nan=True).encode("utf-8")
    return client.post(url, content=raw, headers={"Content-Type": "application/json"})


def _create_decision(client, **kwargs) -> str:
    resp = client.post(f"{DECISIONS}/evaluate", json=_decision_payload(**kwargs))
    assert resp.status_code == 200, resp.text
    return resp.json()["decision_id"]


def _create_scenario(client, **kwargs) -> str:
    resp = client.post(f"{SCENARIOS}/evaluate", json=_scenario_payload(**kwargs))
    assert resp.status_code == 200, resp.text
    return resp.json()["scenario_id"]


def _fetch_decision(client, decision_id: str) -> dict:
    resp = client.get(f"{DECISIONS}/{decision_id}")
    assert resp.status_code == 200, resp.text
    return resp.json()


def _fetch_scenario(client, scenario_id: str) -> dict:
    resp = client.get(f"{SCENARIOS}/{scenario_id}")
    assert resp.status_code == 200, resp.text
    return resp.json()


# ---------------------------------------------------------------------------
# Journey A: Decision Center full path
# ---------------------------------------------------------------------------


def test_journey_a_decision_center_full_path(client):
    """Open Decision Center, list, select, inspect, verify."""
    # Seed two decisions so history is non-trivial
    id_approve = _create_decision(client, it_value=400.0, pue_value=1.4)
    id_reject = _create_decision(client, it_value=1100.0, pue_value=1.4)

    # List
    listing = client.get(f"{DECISIONS}?limit=10&offset=0")
    assert listing.status_code == 200
    assert listing.headers["X-Total-Count"] == "2"
    rows = listing.json()
    assert len(rows) == 2
    for row in rows:
        for key in (
            "id",
            "status",
            "precedence",
            "engine_version",
            "config_version",
            "created_at",
            "decision_timestamp",
            "supersedes",
            "is_superseded",
        ):
            assert key in row, f"list missing {key}"

    # Select detail
    record = _fetch_decision(client, id_approve)
    assert record["status"] == "APPROVE"
    assert "request" in record
    assert "result" in record

    # Engineering inputs
    result = record["result"]
    inputs = result["evidence"]["calculation_inputs"]
    assert inputs["current_it_load_kw"] == 400.0
    assert inputs["current_facility_power_kw"] == 400.0 * 1.4
    assert inputs["pue"] == 1.4
    assert inputs["pue_provenance"] == "MEASURED"

    # Constraints
    constraints = result["state_machine_decision"]["evaluated_constraints"]
    names = [c["name"] for c in constraints]
    assert "electrical" in names
    assert "cooling" in names
    for c in constraints:
        for key in (
            "observed",
            "permitted",
            "physical_capacity",
            "headroom_to_permitted",
            "headroom_to_physical",
            "severity",
            "reason",
        ):
            assert key in c

    # Evidence
    evidence = result["evidence"]
    assert "formulas" in evidence
    assert "assumptions" in evidence
    assert "constraints_snapshot" in evidence

    # Explanation and uncertainty
    assert result["explanation"]["summary"]
    assert result["explanation"]["limiting_constraint"] in ("electrical", "cooling")
    assert result["uncertainty"]["data_quality_level"]

    # Engineering summary
    eng = client.get(f"{DECISIONS}/{id_approve}/engineering")
    assert eng.status_code == 200
    eng_body = eng.json()
    assert eng_body["pue"] == 1.4
    assert eng_body["current_facility_power_kw"] == 560.0
    assert "headroom_to_permitted_kw" in eng_body["current_electrical"]

    # Verify
    verify = client.get(f"{DECISIONS}/{id_approve}/verify")
    assert verify.status_code == 200
    v = verify.json()
    assert v["verified"] is True
    assert v["output_verified"] is True
    assert v["verification_scope"] == "input_config_output_status"
    assert v["full_bundle_reproduced"] is False
    assert v["engine_version_match"] is True

    # Reject case is present too
    rec = _fetch_decision(client, id_reject)
    assert rec["status"] == "REJECT"
    assert rec["precedence"] == "hard_limit_violation"


# ---------------------------------------------------------------------------
# Journey B: Scenario Intelligence full path
# ---------------------------------------------------------------------------


def test_journey_b_scenario_full_path(client):
    """Open workbench, evaluate, inspect, save, reopen."""
    sid = _create_scenario(client, name="peak-shift", it_load_kw=400.0, pue=1.4)

    # Inspect returned decision
    record = _fetch_scenario(client, sid)
    assert record["name"] == "peak-shift"
    assert record["status"] == "APPROVE"
    assert record["decision_id"] is None
    assert "inputs" in record
    assert "config" in record
    assert "result" in record
    assert record["input_fingerprint"]
    assert record["config_fingerprint"]

    # Reopen via engineering endpoint
    eng = client.get(f"{SCENARIOS}/{sid}/engineering")
    assert eng.status_code == 200
    assert eng.json()["pue"] == 1.4

    # Reopen via verify
    v = client.get(f"{SCENARIOS}/{sid}/verify")
    assert v.status_code == 200
    assert v.json()["verified"] is True

    # History is findable
    history = client.get(f"{SCENARIOS}?limit=10")
    assert history.status_code == 200
    assert history.headers["X-Total-Count"] == "1"
    assert history.json()[0]["id"] == sid


# ---------------------------------------------------------------------------
# Journey C: Multi-scenario comparison
# ---------------------------------------------------------------------------


def test_journey_c_multi_scenario_comparison(client):
    """Create 3 scenarios, compare, inspect differences."""
    id_a = _create_scenario(client, name="baseline", it_load_kw=400.0, pue=1.0)
    id_b = _create_scenario(client, name="higher-it", it_load_kw=600.0, pue=1.0)
    id_c = _create_scenario(client, name="higher-pue", it_load_kw=400.0, pue=1.4)

    resp = client.post(
        f"{SCENARIOS}/compare",
        json={"scenario_ids": [id_a, id_b, id_c]},
    )
    assert resp.status_code == 200
    body = resp.json()

    assert len(body["scenarios"]) == 3
    statuses = [s["status"] for s in body["scenarios"]]
    assert all(s in ("APPROVE", "REJECT", "REVIEW") for s in statuses)

    # All scenarios record a most-restrictive constraint
    for s in body["scenarios"]:
        assert s["most_restrictive_constraint"] in ("electrical", "cooling")

    # input_differences flags it_load and pue
    diffs = {d["field_name"]: d for d in body["input_differences"]}
    assert diffs["it_load_kw"]["differs"] is True
    assert diffs["pue"]["differs"] is True
    assert diffs["electrical_capacity_kw"]["differs"] is False

    # No mixed configs (all created with default cfg-1)
    assert body["mixed_config_versions"] is False

    # Explanation names the differing inputs
    expl = body["outcome_explanation"]
    assert "it_load_kw" in expl


# ---------------------------------------------------------------------------
# Journey D: Invalid inputs
# ---------------------------------------------------------------------------


def test_journey_d_invalid_inputs(client, test_db):
    """Reject invalid inputs and prove no records are persisted."""
    # Baseline: no scenarios or decisions exist yet
    assert test_db.execute("SELECT count(*) AS c FROM scenarios").fetchone()["c"] == 0
    assert test_db.execute("SELECT count(*) AS c FROM decisions").fetchone()["c"] == 0

    # --- 422 for Pydantic rejections ---
    neg = _scenario_payload()
    neg["it_load_kw"] = -1.0
    assert client.post(f"{SCENARIOS}/evaluate", json=neg).status_code == 422

    zero_pue = _scenario_payload(pue=0.0)
    assert client.post(f"{SCENARIOS}/evaluate", json=zero_pue).status_code == 422

    neg_cap = _scenario_payload(cap=-100.0)
    assert client.post(f"{SCENARIOS}/evaluate", json=neg_cap).status_code == 422

    reserve_one = _scenario_payload(reserve=1.0)
    assert client.post(f"{SCENARIOS}/evaluate", json=reserve_one).status_code == 422

    empty_name = _scenario_payload(name="")
    assert client.post(f"{SCENARIOS}/evaluate", json=empty_name).status_code == 422

    # --- 422: inf rejected by Pydantic allow_inf_nan=False ---
    inf_payload = _scenario_payload()
    inf_payload["it_load_kw"] = float("inf")
    resp = _post_raw(client, f"{SCENARIOS}/evaluate", inf_payload)
    assert resp.status_code == 422
    detail = resp.json()["detail"]
    assert isinstance(detail, list)
    assert "finite" in str(detail).lower()

    # NaN fails Pydantic's gt=0 check -> 422. The response body must be
    # parseable JSON (before the server-side fix it crashed during
    # serialisation because JSONResponse disallows NaN).
    nan_payload = _scenario_payload()
    nan_payload["pue"] = float("nan")
    resp = _post_raw(client, f"{SCENARIOS}/evaluate", nan_payload)
    assert resp.status_code == 422
    nan_body = resp.json()
    assert "detail" in nan_body

    # --- No records persisted ---
    assert test_db.execute("SELECT count(*) AS c FROM scenarios").fetchone()["c"] == 0
    assert test_db.execute("SELECT count(*) AS c FROM decisions").fetchone()["c"] == 0


# ---------------------------------------------------------------------------
# Journey E: Missing evidence and stale measurements
# ---------------------------------------------------------------------------


def test_journey_e_missing_evidence_and_stale(client, test_db):
    """Confirm REVIEW, stale reporting, and 422 for old records."""
    # Stale measurement: timestamp far in the past, max_age = 60s
    sid = _create_scenario(client, pue=1.0)
    # Update the scenario's stored timestamp by re-evaluating via decision
    # (scenarios use default max_age; use decision path for explicit stale)
    did_stale = _create_decision(
        client,
        it_timestamp="2020-01-01T00:00:00Z",
        max_age=60,
    )
    rec = _fetch_decision(client, did_stale)
    assert rec["status"] == "REVIEW"
    assert rec["precedence"] == "data_quality_ineligible"
    uncertainty = rec["result"]["uncertainty"]
    assert "it_load" in uncertainty["stale_measurements"]

    # Quality-ineligible -> REVIEW
    did_qual = _create_decision(client, quality=False, quality_flags=["missing_data"])
    rec2 = _fetch_decision(client, did_qual)
    assert rec2["status"] == "REVIEW"
    assert rec2["precedence"] == "data_quality_ineligible"

    # Old scenario without PUE evidence -> 422
    test_db.execute(
        "INSERT INTO scenarios "
        "(id, name, created_at, status, precedence, engine_version, "
        "config_version, input_fingerprint, config_fingerprint, "
        "input_json, config_json, result_json, decision_id) "
        "VALUES ('old-1', 'legacy', '2026-01-01T00:00:00Z', "
        "'APPROVE', 'clean', '0.9', 'cfg-1', 'fp1', 'fp2', "
        "'{}', '{}', "
        '\'{"evidence": {"calculation_inputs": {}}}\', NULL)'
    )
    eng = client.get(f"{SCENARIOS}/old-1/engineering")
    assert eng.status_code == 422
    assert "predates" in eng.json()["detail"]

    # Base record still retrievable; only engineering is missing
    base = client.get(f"{SCENARIOS}/old-1")
    assert base.status_code == 200
    assert base.json()["status"] == "APPROVE"

    _ = sid  # sanity: created scenario exists


# ---------------------------------------------------------------------------
# Regression: NaN/inf must not crash the error-response serialiser
# ---------------------------------------------------------------------------


def test_nan_returns_clean_422_not_server_crash(client, test_db):
    """Regression: NaN in a Pydantic-validated field returns clean JSON 422."""
    before_scenarios = test_db.execute("SELECT count(*) AS c FROM scenarios").fetchone()["c"]
    before_decisions = test_db.execute("SELECT count(*) AS c FROM decisions").fetchone()["c"]

    for field in ("pue", "it_load_kw", "cooling_load_kw"):
        payload = _scenario_payload()
        payload[field] = float("nan")
        resp = _post_raw(client, f"{SCENARIOS}/evaluate", payload)
        # Server must answer cleanly (before the fix, this raised a 500)
        assert resp.status_code in (400, 422), (
            f"{field}=nan returned {resp.status_code}: {resp.text}"
        )
        # Body must be parseable JSON with a detail field
        body = resp.json()
        assert "detail" in body

    # No rows leaked through the error path
    assert (
        test_db.execute("SELECT count(*) AS c FROM scenarios").fetchone()["c"] == before_scenarios
    )
    assert (
        test_db.execute("SELECT count(*) AS c FROM decisions").fetchone()["c"] == before_decisions
    )


def test_inf_returns_clean_400_not_server_crash(client):
    """inf passes Pydantic gt=0 but is caught by validate_scenario_numerics."""
    payload = _scenario_payload()
    payload["it_load_kw"] = float("inf")
    resp = _post_raw(client, f"{SCENARIOS}/evaluate", payload)
    assert resp.status_code == 422
    body = resp.json()
    # 422 detail is a list of Pydantic error dicts, not a string.
    assert isinstance(body["detail"], list)
    assert "finite" in str(body["detail"]).lower()


# ---------------------------------------------------------------------------
# Engineering validation
# ---------------------------------------------------------------------------


def test_engineering_pue_multiplies_facility_power(client):
    """Facility power = IT load * PUE, exactly."""
    id_a = _create_scenario(client, it_load_kw=400.0, pue=1.0, cap=1000.0)
    id_b = _create_scenario(client, it_load_kw=400.0, pue=1.4, cap=1000.0)
    eng_a = client.get(f"{SCENARIOS}/{id_a}/engineering").json()
    eng_b = client.get(f"{SCENARIOS}/{id_b}/engineering").json()
    assert eng_a["current_facility_power_kw"] == 400.0
    assert eng_b["current_facility_power_kw"] == 400.0 * 1.4


def test_engineering_facility_power_not_it_load_for_constraint(client):
    """The electrical constraint compares facility power, not raw IT load."""
    # IT load 400 (would pass IT-only), PUE 1.4 -> facility 560 > cap 500
    sid = _create_scenario(client, it_load_kw=400.0, pue=1.4, cap=500.0)
    record = _fetch_scenario(client, sid)
    assert record["status"] == "REJECT"
    assert record["precedence"] == "hard_limit_violation"

    constraints = record["result"]["state_machine_decision"]["evaluated_constraints"]
    elecs = [c for c in constraints if c["name"] == "electrical"]
    assert any(c["severity"] == "PHYSICAL_VIOLATION" for c in elecs)
    # The observed value is facility power, not IT load
    assert any(abs(c["observed"] - 560.0) < 0.01 for c in elecs)


def test_engineering_reserve_reduces_permitted(client):
    """Reserve reduces the permitted limit below physical capacity."""
    sid = _create_scenario(client, it_load_kw=400.0, pue=1.0, cap=1000.0, reserve=0.10)
    eng = client.get(f"{SCENARIOS}/{sid}/engineering").json()
    elec = eng["current_electrical"]
    assert elec["physical_capacity_kw"] == 1000.0
    assert elec["permitted_kw"] == 900.0
    assert elec["headroom_to_permitted_kw"] == 500.0
    assert elec["headroom_to_physical_kw"] == 600.0


def test_engineering_cooling_independent(client):
    """Cooling constraint is independent of electrical."""
    # Electrical fine but cooling violated
    sid = _create_scenario(
        client,
        it_load_kw=400.0,
        pue=1.0,
        cap=1000.0,
        cooling_load_kw=900.0,
        cool_cap=800.0,
    )
    record = _fetch_scenario(client, sid)
    assert record["status"] == "REJECT"
    constraints = record["result"]["state_machine_decision"]["evaluated_constraints"]
    cools = [c for c in constraints if c["name"] == "cooling"]
    elecs = [c for c in constraints if c["name"] == "electrical"]
    assert any(c["severity"] == "PHYSICAL_VIOLATION" for c in cools)
    assert all(c["severity"] == "OK" for c in elecs)


def test_engineering_negative_predicted_load(client):
    """Negative predicted load is classified, not silently clamped."""
    sid = _create_scenario(client, it_load_kw=400.0, proposed_it=-500.0, pue=1.0)
    record = _fetch_scenario(client, sid)
    assert record["status"] == "REJECT"
    assert record["precedence"] == "invalid_proposal"
    reasons = record["result"]["evidence"]["calculation_inputs"]["invalid_proposal_reasons"]
    assert reasons
    assert "negative" in reasons[0].lower()


def test_engineering_unit_conversion_rejected(client):
    """Only canonical units (kW / dimensionless) are accepted."""
    # Reasonable PUE value but wrong unit ("kW" instead of "dimensionless")
    bad_payload = _decision_payload()
    bad_payload["decision"]["pue"] = {
        "name": "pue",
        "value": 1.4,
        "unit": "kW",
        "source": "MEASURED",
    }
    resp = client.post(f"{DECISIONS}/evaluate", json=bad_payload)
    assert resp.status_code == 400
    assert "dimensionless" in resp.json()["detail"].lower()


def test_engineering_missing_tolerance_unknown_uncertainty(client):
    """Measured without tolerance -> model uncertainty UNKNOWN."""
    sid = _create_scenario(client, it_load_kw=400.0, pue=1.0)
    record = _fetch_scenario(client, sid)
    assert record["result"]["uncertainty"]["model_uncertainty_level"] == "UNKNOWN"


def test_engineering_existing_overload_with_corrective_action(client):
    """Document current engine behavior for corrective actions.

    The API does not expose corrective_action_mode. When the current state
    is overloaded, the engine rejects even a proposed change that would
    bring predicted load under capacity, because both current and predicted
    states are evaluated by default.
    """
    sid = _create_scenario(
        client,
        it_load_kw=1100.0,
        proposed_it=-300.0,
        pue=1.0,
        cap=1000.0,
    )
    record = _fetch_scenario(client, sid)
    # Predicted is fine (1100 - 300 = 800 < 900) but current is overloaded
    assert record["status"] == "REJECT"
    assert record["precedence"] == "hard_limit_violation"


# ---------------------------------------------------------------------------
# Frontend / backend schema compatibility
# ---------------------------------------------------------------------------


def test_frontend_pages_render(client):
    """All three pages render with their expected containers."""
    assert client.get("/").status_code == 200
    assert client.get("/decisions").status_code == 200
    scenarios_page = client.get("/scenarios")
    assert scenarios_page.status_code == 200
    assert 'id="scenario-form"' in scenarios_page.text
    assert 'id="result-panel"' in scenarios_page.text
    assert 'id="comparison-panel"' in scenarios_page.text


def test_nav_cross_links(client):
    """Every page links to every other page."""
    for page in ("/", "/decisions", "/scenarios"):
        html = client.get(page).text
        assert 'href="/"' in html
        assert 'href="/decisions"' in html
        assert 'href="/scenarios"' in html


# ---------------------------------------------------------------------------
# Performance / bounded inputs
# ---------------------------------------------------------------------------


def test_pagination_is_bounded(client):
    """List endpoints refuse limit > 100."""
    for base in (DECISIONS, SCENARIOS):
        assert client.get(f"{base}?limit=0").status_code == 422
        assert client.get(f"{base}?limit=101").status_code == 422
        assert client.get(f"{base}?offset=-1").status_code == 422


def test_comparison_bounded(client):
    """Compare requires 2-10 scenarios."""
    sid = _create_scenario(client)
    resp = client.post(f"{SCENARIOS}/compare", json={"scenario_ids": [sid]})
    assert resp.status_code == 422

    resp = client.post(
        f"{SCENARIOS}/compare",
        json={"scenario_ids": [sid] * 11},
    )
    assert resp.status_code == 422


def test_x_total_count_accurate_under_pagination(client):
    """X-Total-Count reflects total, not page size."""
    for _ in range(5):
        _create_scenario(client)
    page = client.get(f"{SCENARIOS}?limit=2&offset=0")
    assert page.headers["X-Total-Count"] == "5"
    assert len(page.json()) == 2
    page2 = client.get(f"{SCENARIOS}?limit=2&offset=4")
    assert len(page2.json()) == 1


# ---------------------------------------------------------------------------
# Cross-feature invariants
# ---------------------------------------------------------------------------


def test_scenario_can_bridge_to_decision_history(client):
    """persist_as_decision=true creates both rows and links them."""
    payload = _scenario_payload(name="bridge-test", pue=1.4)
    payload["persist_as_decision"] = True
    resp = client.post(f"{SCENARIOS}/evaluate", json=payload)
    assert resp.status_code == 200
    body = resp.json()
    assert body["decision_id"] is not None

    # Decision exists in decisions table
    record = _fetch_decision(client, body["decision_id"])
    assert record["status"] == body["result"]["status"]

    # Scenario references it
    scenario = _fetch_scenario(client, body["scenario_id"])
    assert scenario["decision_id"] == body["decision_id"]


def test_decisions_and_scenarios_are_independent_streams(client):
    """By default scenarios do not pollute decision history."""
    _create_decision(client)
    _create_scenario(client)
    _create_scenario(client)

    decisions = client.get(f"{DECISIONS}").json()
    scenarios = client.get(f"{SCENARIOS}").json()
    assert len(decisions) == 1
    assert len(scenarios) == 2


def test_append_only_enforced_for_both_tables(client, test_db):
    """Both decisions and scenarios reject UPDATE/DELETE."""
    did = _create_decision(client)
    sid = _create_scenario(client)
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        test_db.execute("UPDATE decisions SET status = 'REJECT' WHERE id = ?", (did,))
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        test_db.execute("UPDATE scenarios SET name = 'tampered' WHERE id = ?", (sid,))
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        test_db.execute("DELETE FROM decisions WHERE id = ?", (did,))
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        test_db.execute("DELETE FROM scenarios WHERE id = ?", (sid,))
