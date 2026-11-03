# Copyright 2026 Lightibulb
"""Inspector-state fixtures: every decision outcome the UI must render."""

import json

import pytest
from fastapi.testclient import TestClient

from ecofac.api.dependencies import get_db
from ecofac.decision.engineering import build_engineering_summary
from ecofac.decision.limits import (
    ConstraintConfig,
    CoolingLimits,
    ElectricalLimits,
)
from ecofac.decision.measurements import (
    MeasurementSource,
    RichMeasurement,
)
from ecofac.decision.store import persist_decision, verify_decision
from ecofac.decision.transparent import (
    TransparentDecisionRequest,
    decide_transparently,
)
from ecofac.main import app
from ecofac.persistence.database import get_connection, init_db


@pytest.fixture
def inspector_ctx():
    conn = get_connection(":memory:")
    init_db(conn)

    def _override():
        yield conn

    app.dependency_overrides[get_db] = _override
    with TestClient(app) as client:
        yield conn, client
    app.dependency_overrides.clear()
    conn.close()


def _config(
    *, cap=1000.0, cool_cap=800.0, reserve=0.10
) -> ConstraintConfig:
    return ConstraintConfig(
        electrical=ElectricalLimits(capacity=cap, reserve_pct=reserve),
        cooling=CoolingLimits(capacity=cool_cap, reserve_pct=reserve),
    )


def _req(
    *,
    it=400.0,
    cool=300.0,
    pue=1.4,
    cap=1000.0,
    cool_cap=800.0,
    quality_eligible=True,
    quality_flags=None,
    proposed_it_delta=0.0,
    proposed_cooling_delta=0.0,
    it_timestamp="2026-01-01T00:00:00Z",
    max_age=None,
) -> TransparentDecisionRequest:
    return TransparentDecisionRequest(
        config=_config(cap=cap, cool_cap=cool_cap),
        config_version="cfg-1",
        it_load=RichMeasurement(
            name="it_load",
            value=it,
            unit="kW",
            source=MeasurementSource.MEASURED,
            reference_id="reading-it-1",
            measurement_timestamp=it_timestamp,
        ),
        cooling_load=RichMeasurement(
            name="cooling_load",
            value=cool,
            unit="kW",
            source=MeasurementSource.MEASURED,
        ),
        pue=RichMeasurement(
            name="pue",
            value=pue,
            unit="dimensionless",
            source=MeasurementSource.MEASURED,
        ),
        proposed_it_delta=proposed_it_delta,
        proposed_cooling_delta=proposed_cooling_delta,
        quality_eligible=quality_eligible,
        quality_flags=quality_flags or [],
        max_measurement_age_seconds=max_age,
    )


def _persist(conn, req):
    result = decide_transparently(req)
    return persist_decision(conn, req, result)


def _assert_inspector_contract(record: dict) -> None:
    """Every field the inspector needs must be present in the API response."""
    req = record["request"]
    result = record["result"]

    # Identity
    for f in ("id", "status", "precedence", "engine_version",
              "config_version", "decision_timestamp", "created_at"):
        assert f in record, f"missing identity field {f}"

    # Fingerprints used for evidence and reproducibility.
    assert "input_fingerprint" in record
    assert "config_fingerprint" in record

    # Input inspection (from the stored request)
    for f in ("it_load", "cooling_load"):
        assert f in req
        m = req[f]
        for sub in ("name", "value", "unit", "source"):
            assert sub in m, f"{f} missing {sub}"

    # Proposed change
    assert "proposed_it_delta" in req
    assert "proposed_cooling_delta" in req

    # Engineering evidence
    assert "evidence" in result
    evidence = result["evidence"]
    assert "calculation_inputs" in evidence
    ci = evidence["calculation_inputs"]
    for f in (
        "current_it_load_kw", "current_facility_power_kw",
        "current_cooling_load_kw", "predicted_it_load_kw",
        "predicted_facility_power_kw", "predicted_cooling_load_kw",
        "pue", "pue_provenance",
    ):
        assert f in ci, f"missing calculation_input {f}"

    # Constraints (full detail — the inspector reads these directly)
    sm = result["state_machine_decision"]
    assert "evaluated_constraints" in sm
    for ev in sm["evaluated_constraints"]:
        for f in ("name", "severity", "observed", "permitted",
                  "physical_capacity", "headroom_to_permitted",
                  "headroom_to_physical", "reason"):
            assert f in ev, f"constraint missing {f}"

    # Evidence structure
    assert "formulas" in evidence
    assert "assumptions" in evidence
    assert "constraints_snapshot" in evidence

    # Uncertainty
    assert "uncertainty" in result
    u = result["uncertainty"]
    for f in ("data_quality_level", "model_uncertainty_level",
              "missing_measurements", "stale_measurements", "quality_flags",
              "rationale", "assumptions", "model_limitations"):
        assert f in u, f"uncertainty missing {f}"

    # Reproducibility
    assert "fingerprint" in result
    fp = result["fingerprint"]
    assert "input_fingerprint" in fp
    assert "config_fingerprint" in fp

    # Explanation
    assert "explanation" in result
    expl = result["explanation"]
    for f in ("summary", "limiting_constraint", "headroom_summary",
              "what_would_change_decision"):
        assert f in expl, f"explanation missing {f}"


def _fetch_record(client: TestClient, decision_id: str) -> dict:
    resp = client.get(f"/api/v1/decisions/{decision_id}")
    assert resp.status_code == 200, resp.text
    return resp.json()


def test_approve_state_full_contract(inspector_ctx):
    conn, client = inspector_ctx
    did = _persist(conn, _req(it=400.0, pue=1.4))
    record = _fetch_record(client, did)
    assert record["status"] == "APPROVE"
    _assert_inspector_contract(record)
    # APPROVE has no failed constraints
    assert record["result"]["evidence"]["failed_constraints"] == []
    # APPROVE has no "what would change" hints
    assert record["result"]["explanation"]["what_would_change_decision"] == []


def test_reject_physical_violation(inspector_ctx):
    conn, client = inspector_ctx
    did = _persist(conn, _req(it=1100.0, pue=1.4))
    record = _fetch_record(client, did)
    assert record["status"] == "REJECT"
    assert record["precedence"] == "hard_limit_violation"
    failed = record["result"]["evidence"]["failed_constraints"]
    assert len(failed) > 0
    # The failure reason must be a string the inspector can render
    assert all(isinstance(f["reason"], str) and f["reason"] for f in failed)


def test_reject_reserve_violation(inspector_ctx):
    # PUE=1.0 keeps facility power = IT load so the reserve band
    # (900 < observed <= 1000) is reachable at IT=950.
    conn, client = inspector_ctx
    did = _persist(conn, _req(it=950.0, pue=1.0))
    record = _fetch_record(client, did)
    assert record["status"] == "REJECT"
    assert record["precedence"] == "reserve_violation"


def test_review_near_threshold(inspector_ctx):
    # PUE=1.0 keeps facility power = IT load so IT=850 falls inside
    # the warning band (< 90 kW headroom below 900 permitted).
    conn, client = inspector_ctx
    did = _persist(conn, _req(it=850.0, pue=1.0))
    record = _fetch_record(client, did)
    assert record["status"] == "REVIEW"
    assert record["precedence"] == "near_threshold"
    expl = record["result"]["explanation"]
    assert expl["limiting_constraint"] in ("electrical", "cooling")
    # REVIEW must give the user actionable hints
    assert len(expl["what_would_change_decision"]) > 0


def test_review_data_quality(inspector_ctx):
    conn, client = inspector_ctx
    did = _persist(
        conn,
        _req(quality_eligible=False, quality_flags=["missing_data"]),
    )
    record = _fetch_record(client, did)
    assert record["status"] == "REVIEW"
    assert record["precedence"] == "data_quality_ineligible"
    uncertainty = record["result"]["uncertainty"]
    # Quality flags must be preserved so the inspector can display them
    assert "missing_data" in uncertainty["quality_flags"]
    # And actionable hints must be present
    hints = record["result"]["explanation"]["what_would_change_decision"]
    assert len(hints) > 0


def test_review_stale_measurement(inspector_ctx):
    conn, client = inspector_ctx
    did = _persist(
        conn,
        _req(
            it_timestamp="2020-01-01T00:00:00Z",
            max_age=1,
        ),
    )
    record = _fetch_record(client, did)
    assert record["status"] == "REVIEW"
    assert record["precedence"] == "data_quality_ineligible"
    uncertainty = record["result"]["uncertainty"]
    assert "it_load" in uncertainty["stale_measurements"]


def test_invalid_proposal_rejected(inspector_ctx):
    conn, client = inspector_ctx
    did = _persist(conn, _req(proposed_it_delta=-500.0))
    record = _fetch_record(client, did)
    assert record["status"] == "REJECT"
    assert record["precedence"] == "invalid_proposal"
    reasons = (
        record["result"]["evidence"]["calculation_inputs"]
        ["invalid_proposal_reasons"]
    )
    assert len(reasons) > 0
    assert "negative" in reasons[0].lower()


def test_missing_evidence_old_record(inspector_ctx):
    """An old record without PUE fields triggers the 422 path cleanly."""
    conn, client = inspector_ctx
    conn.execute(
        "INSERT INTO decisions "
        "(id, status, precedence, engine_version, config_version, "
        "input_fingerprint, config_fingerprint, request_json, result_json, "
        "decision_timestamp, created_at, supersedes, supersession_reason) "
        "VALUES ('old-1', 'APPROVE', 'clean', '0.9', 'cfg-1', "
        "'fp1', 'fp2', '{}', "
        "'{\"evidence\": {\"calculation_inputs\": {}}}', "
        "'2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z', NULL, NULL)"
    )
    # Engineering endpoint must return 422 — the UI relies on this
    resp = client.get("/api/v1/decisions/old-1/engineering")
    assert resp.status_code == 422
    assert "predates" in resp.json()["detail"]

    # The base record is still retrievable; only engineering is missing
    base = client.get("/api/v1/decisions/old-1")
    assert base.status_code == 200
    body = base.json()
    assert body["status"] == "APPROVE"

    # Direct call to the extractor also raises
    record = {
        "id": "old-1",
        "result": json.loads(
            conn.execute(
                "SELECT result_json FROM decisions WHERE id = 'old-1'"
            ).fetchone()["result_json"]
        ),
    }
    with pytest.raises(ValueError, match="predates"):
        build_engineering_summary(record)


def test_failed_verification_detected(inspector_ctx):
    conn, client = inspector_ctx
    did = _persist(conn, _req(it=400.0))

    # First verify passes
    ok = client.get(f"/api/v1/decisions/{did}/verify").json()
    assert ok["verified"] is True

    # Tamper the stored fingerprint (bypass the append-only trigger briefly)
    conn.execute("DROP TRIGGER trg_decisions_no_update")
    conn.execute(
        "UPDATE decisions SET input_fingerprint = 'tampered' WHERE id = ?",
        (did,),
    )
    conn.execute(
        "CREATE TRIGGER trg_decisions_no_update "
        "BEFORE UPDATE ON decisions "
        "BEGIN SELECT RAISE(ABORT, 'decisions is append-only'); END"
    )

    v = client.get(f"/api/v1/decisions/{did}/verify").json()
    assert v["verified"] is False
    assert v["stored_input_fingerprint"] == "tampered"
    assert v["computed_input_fingerprint"] != "tampered"


def test_verify_endpoint_reports_scope(inspector_ctx):
    """Inspector relies on verification_scope and full_bundle_reproduced."""
    conn, client = inspector_ctx
    did = _persist(conn, _req())
    v = client.get(f"/api/v1/decisions/{did}/verify").json()
    assert v["verification_scope"] == "input_config_output_status"
    assert v["full_bundle_reproduced"] is False
    assert v["engine_version_match"] is True


def test_all_states_render_through_api(inspector_ctx):
    """Every inspector state can be fetched via the API."""
    conn, client = inspector_ctx
    cases = [
        _req(it=400.0),                                    # APPROVE
        _req(it=1100.0),                                   # REJECT physical
        _req(it=950.0),                                    # REJECT reserve
        _req(it=850.0),                                    # REVIEW near threshold
        _req(quality_eligible=False, quality_flags=["missing_data"]),
        _req(proposed_it_delta=-500.0),                    # REJECT invalid
    ]
    ids = [_persist(conn, c) for c in cases]
    for decision_id in ids:
        record = _fetch_record(client, decision_id)
        _assert_inspector_contract(record)
        # Engineering endpoint must succeed for all of these
        eng = client.get(f"/api/v1/decisions/{decision_id}/engineering")
        assert eng.status_code == 200
        assert eng.json()["pue"] > 0


def test_verify_decision_store_helper_reports_full_scope(inspector_ctx):
    conn, _ = inspector_ctx
    did = _persist(conn, _req())
    v = verify_decision(conn, did)
    assert v is not None
    for key in (
        "verified", "output_verified", "engine_version_match",
        "verification_scope", "full_bundle_reproduced",
        "stored_engine_version", "current_engine_version",
    ):
        assert key in v, f"verify missing {key}"
    assert v["verification_scope"] == "input_config_output_status"
    assert v["full_bundle_reproduced"] is False
