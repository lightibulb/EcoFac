# Copyright 2026 Lightibulb
"""Full verification and supersession-chain integrity tests."""

import json
import uuid

import pytest

from ecofac.decision.limits import (
    ConstraintConfig,
    CoolingLimits,
    ElectricalLimits,
)
from ecofac.decision.measurements import (
    MeasurementSource,
    RichMeasurement,
)
from ecofac.decision.store import (
    persist_decision,
    verify_decision,
    walk_supersession_chain_with_branches,
)
from ecofac.decision.transparent import (
    TransparentDecisionRequest,
    decide_transparently,
)
from ecofac.persistence.database import get_connection, init_db


@pytest.fixture
def db_conn():
    conn = get_connection(":memory:")
    init_db(conn)
    yield conn
    conn.close()


def _config() -> ConstraintConfig:
    return ConstraintConfig(
        electrical=ElectricalLimits(capacity=1000.0, reserve_pct=0.10),
        cooling=CoolingLimits(capacity=800.0, reserve_pct=0.10),
    )


def _req(it=400.0) -> TransparentDecisionRequest:
    return TransparentDecisionRequest(
        config=_config(),
        config_version="cfg-1",
        it_load=RichMeasurement(
            name="it_load", value=it, unit="kW",
            source=MeasurementSource.MEASURED,
        ),
        cooling_load=RichMeasurement(
            name="cooling_load", value=300.0, unit="kW",
            source=MeasurementSource.MEASURED,
        ),
        quality_eligible=True,
    )


def _persist(conn, req):
    result = decide_transparently(req)
    return persist_decision(conn, req, result)


def test_verify_reports_output_verified(db_conn):
    did = _persist(db_conn, _req())
    v = verify_decision(db_conn, did)
    assert v is not None
    assert v["verified"] is True
    assert v["output_verified"] is True
    assert v["stored_status"] == v["recomputed_status"]


def test_verify_detects_fingerprint_tamper(db_conn):
    did = _persist(db_conn, _req())
    db_conn.execute("DROP TRIGGER trg_decisions_no_update")
    db_conn.execute(
        "UPDATE decisions SET input_fingerprint = 'tampered' WHERE id = ?",
        (did,),
    )
    db_conn.execute(
        "CREATE TRIGGER trg_decisions_no_update "
        "BEFORE UPDATE ON decisions "
        "BEGIN SELECT RAISE(ABORT, 'decisions is append-only'); END"
    )
    v = verify_decision(db_conn, did)
    assert v is not None
    assert v["verified"] is False
    assert v["stored_input_fingerprint"] == "tampered"


def test_supersession_rejects_second_child(db_conn):
    id_a = _persist(db_conn, _req())
    req_b = _req(it=500.0)
    result_b = decide_transparently(req_b)
    persist_decision(
        db_conn, req_b, result_b,
        supersedes=id_a, supersession_reason="first",
    )
    req_c = _req(it=600.0)
    result_c = decide_transparently(req_c)
    with pytest.raises(ValueError, match="already superseded"):
        persist_decision(
            db_conn, req_c, result_c,
            supersedes=id_a, supersession_reason="second",
        )


def test_supersession_chain_with_legacy_branches(db_conn):
    """Legacy data with multiple children surfaces as branches."""
    id_a = _persist(db_conn, _req())
    req_b = _req(it=500.0)
    result_b = decide_transparently(req_b)
    id_b = persist_decision(
        db_conn, req_b, result_b,
        supersedes=id_a, supersession_reason="first",
    )
    id_c = str(uuid.uuid4())
    req_c = _req(it=600.0)
    result_c = decide_transparently(req_c)
    db_conn.execute(
        "INSERT INTO decisions "
        "(id, status, precedence, engine_version, config_version, "
        "input_fingerprint, config_fingerprint, request_json, result_json, "
        "decision_timestamp, created_at, supersedes, supersession_reason) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            id_c, result_c.status, result_c.precedence,
            result_c.engine_version, result_c.config_version,
            result_c.fingerprint.input_fingerprint,
            result_c.fingerprint.config_fingerprint,
            json.dumps(req_c.model_dump(mode="json"), sort_keys=True),
            json.dumps(result_c.model_dump(mode="json"), sort_keys=True),
            result_c.decision_timestamp,
            "9999-01-01T00:00:00Z",
            id_a, "second",
        ),
    )
    chain, branches = walk_supersession_chain_with_branches(db_conn, id_b)
    assert [row["id"] for row in chain] == [id_a, id_b]
    assert [row["id"] for row in branches] == [id_c]


def test_verify_engine_version_mismatch(db_conn, monkeypatch):
    did = _persist(db_conn, _req())
    monkeypatch.setattr(
        "ecofac.decision.store.CURRENT_ENGINE_VERSION", "0.9"
    )
    v = verify_decision(db_conn, did)
    assert v is not None
    assert v["engine_version_match"] is False
    assert v["stored_engine_version"] == "1.0"
    assert v["current_engine_version"] == "0.9"


def test_verify_reports_scope_explicitly(db_conn):
    did = _persist(db_conn, _req())
    v = verify_decision(db_conn, did)
    assert v is not None
    assert v["verification_scope"] == "input_config_output_status"
    assert v["full_bundle_reproduced"] is False
    assert v["engine_version_match"] is True
