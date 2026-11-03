# Copyright 2026 Lightibulb
"""Full verification and single-child supersession enforcement."""

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
