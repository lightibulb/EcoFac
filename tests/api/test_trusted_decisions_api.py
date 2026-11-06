# Copyright 2026 Lightibulb
"""Trusted decision path over HTTP: no mixing of trusted and caller inputs."""

import datetime as dt
import json

import pytest

BASE = "/api/v1/decisions"
INTERVAL = 30


def z(ts: dt.datetime) -> str:
    return ts.isoformat().replace("+00:00", "Z")


def slot(*, offset_minutes: int = 0) -> dt.datetime:
    """The most recent completed 30-minute slot, shifted by ``offset_minutes``."""
    now = dt.datetime.now(dt.UTC)
    floored = now.replace(minute=(now.minute // INTERVAL) * INTERVAL, second=0, microsecond=0)
    return floored - dt.timedelta(minutes=INTERVAL) + dt.timedelta(minutes=offset_minutes)


@pytest.fixture(autouse=True)
def _default_policy(monkeypatch):
    monkeypatch.delenv("ECOFAC_TRUSTED_MAX_AGE_SECONDS", raising=False)


def seed(
    db,
    *,
    ts=None,
    it_role="IT_LOAD",
    cool_role="COOLING",
    it_tol=0.02,
    cool_tol=0.03,
    interval=INTERVAL,
    with_prev=True,
):
    ts = ts or slot()
    db.execute(
        "INSERT OR IGNORE INTO import_batches (id, source_id, status, imported_at) "
        "VALUES ('ib1','s1','started','2026-01-01T00:00:00Z')"
    )
    for meter, role, tol in (("it-m", it_role, it_tol), ("cool-m", cool_role, cool_tol)):
        db.execute(
            "INSERT INTO meters (id, building_id, fuel_type, unit, interval_minutes, "
            "role, relative_tolerance, created_at) "
            "VALUES (?, 'b1', 'electricity', 'kWh', ?, ?, ?, '2026-01-01T00:00:00Z')",
            (meter, interval, role, tol),
        )
    stamps = [ts - dt.timedelta(minutes=interval), ts] if with_prev else [ts]
    for meter, kwh in (("it-m", 200.0), ("cool-m", 150.0)):  # 400 kW / 300 kW
        for t in stamps:
            db.execute(
                "INSERT INTO energy_readings (id, meter_id, timestamp_utc, value, unit, "
                "source_id, batch_id, is_valid, created_at) "
                "VALUES (?, ?, ?, ?, 'kWh', 's1', 'ib1', 1, ?)",
                (f"{meter}@{z(t)}", meter, z(t), kwh, z(t)),
            )
    return ts


def decision(ts, *, it_ref=True, cool_ref=True, pue="configured", **overrides):
    it = {
        "name": "it_load",
        "value": 1.0,
        "unit": "kW",
        "source": "ESTIMATED",
        "uncertainty": {"absolute_tolerance": 0.0001},  # caller lies about accuracy
    }
    cool = {"name": "cooling_load", "value": 1.0, "unit": "kW", "source": "ESTIMATED"}
    if it_ref:
        it.update(meter_id="it-m", measurement_timestamp=z(ts))
    if cool_ref:
        cool.update(meter_id="cool-m", measurement_timestamp=z(ts))
    body = {
        "config": {
            "electrical": {"capacity": 1000.0, "reserve_pct": 0.10},
            "cooling": {"capacity": 800.0, "reserve_pct": 0.10},
        },
        "config_version": "cfg-1",
        "it_load": it,
        "cooling_load": cool,
        "proposed_it_delta": 0.0,
        "proposed_cooling_delta": 0.0,
        "quality_eligible": True,
        "quality_stale": False,
        "uncertainty_flags": [],
        "conflicting_inputs": [],
        "mode": "trusted",
    }
    if pue == "configured":
        body["pue"] = {"name": "pue", "value": 1.4, "unit": "dimensionless", "source": "CONFIGURED"}
    elif pue == "measured":
        body["pue"] = {"name": "pue", "value": 1.4, "unit": "dimensionless", "source": "MEASURED"}
    body.update(overrides)
    return {"decision": body}


def post_json(client, payload):
    return client.post(f"{BASE}/evaluate", json=payload)


# backward-compat alias: existing callers use `module.post(...)`.
# The AST graph sees only `def post_json`, so the false edge
# from `@router.post` decorators to this test helper disappears.
post = post_json


def applied_flags(resp):
    inputs = resp.json()["result"]["evidence"]["calculation_inputs"]
    return "; ".join(inputs["applied_uncertainty_flags"])


# ---- the happy path and energy/power semantics ------------------------------


def test_trusted_path_converts_energy_to_power_and_preserves_the_raw_reading(client, test_db):
    ts = seed(test_db)
    resp = post_json(client, decision(ts))
    assert resp.status_code == 200, resp.text
    result = resp.json()["result"]
    assert result["status"] == "APPROVE"
    inputs = result["evidence"]["calculation_inputs"]
    assert inputs["current_it_load_kw"] == pytest.approx(400.0)
    assert inputs["current_cooling_load_kw"] == pytest.approx(300.0)
    it_ref = next(r for r in result["evidence"]["input_references"] if r["name"] == "it_load")
    assert it_ref["value"] == pytest.approx(400.0)
    assert it_ref["unit"] == "kW"
    assert it_ref["raw_value"] == 200.0
    assert it_ref["raw_unit"] == "kWh"
    assert it_ref["interval_minutes"] == INTERVAL
    assert it_ref["source"] == "MEASURED"


def test_fifteen_minute_meters_are_not_read_as_hourly(client, test_db):
    ts = seed(test_db, interval=15)
    resp = post_json(client, decision(ts))
    assert resp.status_code == 200, resp.text
    # 200 kWh in 15 minutes is 800 kW average, not 200 or 400.
    inputs = resp.json()["result"]["evidence"]["calculation_inputs"]
    assert inputs["current_it_load_kw"] == pytest.approx(800.0)


# ---- partial trust / missing references / alignment -------------------------


def test_partial_trust_is_rejected(client, test_db):
    ts = seed(test_db)
    resp = post_json(client, decision(ts, cool_ref=False))
    assert resp.status_code == 400
    assert "cooling_load" in resp.json()["detail"]


def test_no_references_is_rejected(client, test_db):
    ts = seed(test_db)
    assert post_json(client, decision(ts, it_ref=False, cool_ref=False)).status_code == 400


def test_mismatched_timestamps_are_rejected(client, test_db):
    ts = seed(test_db)
    payload = decision(ts)
    payload["decision"]["cooling_load"]["measurement_timestamp"] = z(
        ts - dt.timedelta(minutes=INTERVAL)
    )
    resp = post_json(client, payload)
    assert resp.status_code == 400
    assert "not aligned" in resp.json()["detail"]


def test_future_timestamp_is_rejected(client, test_db):
    ts = seed(test_db, ts=slot(offset_minutes=180))
    resp = post_json(client, decision(ts))
    assert resp.status_code == 400
    assert "future" in resp.json()["detail"]


# ---- semantics: roles, units, intervals -------------------------------------


def test_wrong_meter_role_is_rejected(client, test_db):
    ts = seed(test_db, it_role="COOLING")
    resp = post_json(client, decision(ts))
    assert resp.status_code == 400
    assert "role" in resp.json()["detail"]


def test_one_meter_cannot_serve_both_roles(client, test_db):
    ts = seed(test_db)
    payload = decision(ts)
    payload["decision"]["cooling_load"]["meter_id"] = "it-m"
    resp = post_json(client, payload)
    assert resp.status_code == 400
    assert "same meter" in resp.json()["detail"]


def test_power_unit_meter_is_incompatible(client, test_db):
    ts = seed(test_db)
    test_db.execute("UPDATE meters SET unit = 'kW' WHERE id = 'it-m'")
    resp = post_json(client, decision(ts))
    assert resp.status_code == 400
    assert "energy" in resp.json()["detail"]


def test_meter_without_a_valid_interval_is_rejected(client, test_db):
    ts = seed(test_db)
    test_db.execute("UPDATE meters SET interval_minutes = 0 WHERE id = 'it-m'")
    resp = post_json(client, decision(ts))
    assert resp.status_code == 400
    assert "interval" in resp.json()["detail"]


def test_invalid_reading_is_rejected(client, test_db):
    ts = seed(test_db)
    test_db.execute(
        "UPDATE energy_readings SET is_valid = 0 WHERE meter_id='cool-m' AND timestamp_utc=?",
        (z(ts),),
    )
    assert post_json(client, decision(ts)).status_code == 400


# ---- caller-supplied metadata cannot override server derivation -------------


def test_caller_quality_claims_cannot_override_real_quality(client, test_db):
    ts = seed(test_db)
    test_db.execute(
        "DELETE FROM energy_readings WHERE meter_id='cool-m' AND timestamp_utc=?",
        (z(ts - dt.timedelta(minutes=INTERVAL)),),
    )
    # The caller asserts perfect quality; the server assesses the COOLING meter too.
    resp = post_json(client, decision(ts, quality_eligible=True, quality_flags=[]))
    assert resp.status_code == 200
    assert resp.json()["result"]["status"] == "REVIEW"
    assert "missing_data" in resp.json()["result"]["uncertainty"]["quality_flags"]


def test_caller_freshness_policy_and_clock_are_ignored(client, test_db):
    ts = seed(test_db, ts=slot(offset_minutes=-3 * 24 * 60))
    resp = post_json(
        client,
        decision(
            ts,
            max_measurement_age_seconds=10**9,
            evaluation_time="2000-01-01T00:00:00Z",
            quality_stale=False,
        ),
    )
    assert resp.status_code == 200, resp.text
    result = resp.json()["result"]
    inputs = result["evidence"]["calculation_inputs"]
    assert result["status"] == "REVIEW"
    assert set(result["uncertainty"]["stale_measurements"]) == {"it_load", "cooling_load"}
    assert inputs["max_measurement_age_seconds"] == 7200
    assert inputs["evaluation_time"] != "2000-01-01T00:00:00Z"


def test_caller_tolerance_is_replaced_by_meter_metadata(client, test_db):
    ts = seed(test_db)
    result = post_json(client, decision(ts)).json()["result"]
    it_sens = next(s for s in result["sensitivity"] if s["measurement_name"] == "it_load")
    # meter accuracy 2 % of 400 kW, not the caller's 0.0001 kW
    assert it_sens["lower_bound"] == pytest.approx(392.0)
    assert it_sens["upper_bound"] == pytest.approx(408.0)


def test_caller_cannot_claim_server_resolution_or_uncertainty_flags(client, test_db):
    ts = seed(test_db, it_tol=None, cool_tol=None)
    resp = post_json(
        client,
        decision(ts, inputs_resolved_by_server=True, trust_caveats=[], uncertainty_flags=[]),
    )
    assert resp.status_code == 200
    assert resp.json()["result"]["status"] == "REVIEW"
    assert "unknown_model_uncertainty" in applied_flags(resp)


# ---- PUE and uncertainty policy ---------------------------------------------


def test_missing_pue_in_trusted_mode_is_review(client, test_db):
    ts = seed(test_db)
    resp = post_json(client, decision(ts, pue=None))
    assert resp.status_code == 200
    assert resp.json()["result"]["status"] == "REVIEW"
    assert "pue_assumed" in applied_flags(resp)


def test_caller_cannot_claim_a_measured_pue(client, test_db):
    ts = seed(test_db)
    resp = post_json(client, decision(ts, pue="measured"))
    assert resp.status_code == 400
    assert "PUE" in resp.json()["detail"]


def test_pue_below_one_is_rejected(client, test_db):
    ts = seed(test_db)
    payload = decision(ts)
    payload["decision"]["pue"]["value"] = 0.9
    resp = post_json(client, payload)
    assert resp.status_code in (400, 422)


def test_meter_without_tolerance_cannot_approve(client, test_db):
    ts = seed(test_db, cool_tol=None)
    resp = post_json(client, decision(ts))
    assert resp.json()["result"]["status"] == "REVIEW"
    assert "missing_tolerance: 'cooling_load'" in applied_flags(resp)


def test_unclassified_meter_role_cannot_approve(client, test_db):
    ts = seed(test_db, it_role=None)
    resp = post_json(client, decision(ts))
    assert resp.json()["result"]["status"] == "REVIEW"
    assert "meter_role_unclassified" in applied_flags(resp)


# ---- numeric safety at the boundary ------------------------------------------


@pytest.mark.parametrize("token", ["NaN", "Infinity", "-Infinity"])
def test_non_finite_numbers_are_rejected_over_http(client, test_db, token):
    ts = seed(test_db)
    raw = json.dumps(decision(ts, proposed_it_delta=0.5)).replace(
        '"proposed_it_delta": 0.5', f'"proposed_it_delta": {token}'
    )
    resp = client.post(
        f"{BASE}/evaluate", content=raw, headers={"Content-Type": "application/json"}
    )
    assert resp.status_code == 422


# ---- deterministic verification ---------------------------------------------


def test_trusted_decision_verifies_after_wall_clock_advances(client, test_db, monkeypatch):
    ts = seed(test_db)
    decision_id = post_json(client, decision(ts)).json()["decision_id"]
    first = client.get(f"{BASE}/{decision_id}/verify").json()
    assert first["verified"] is True

    monkeypatch.setattr("ecofac.decision.transparent.now_z", lambda: "2031-01-01T00:00:00Z")
    later = client.get(f"{BASE}/{decision_id}/verify").json()
    assert later["verified"] is True
    assert later["recomputed_status"] == first["recomputed_status"] == "APPROVE"
