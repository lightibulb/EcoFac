# Copyright 2026 Lightibulb
"""Engineering summary extraction."""

import pytest

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
from ecofac.decision.store import persist_decision
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


def _persisted_record(conn, *, it=400.0, pue=1.4, elec_cap=1000.0,
                      cool_cap=800.0, elec_res=0.10, cool_res=0.10):
    req = TransparentDecisionRequest(
        config=ConstraintConfig(
            electrical=ElectricalLimits(capacity=elec_cap, reserve_pct=elec_res),
            cooling=CoolingLimits(capacity=cool_cap, reserve_pct=cool_res),
        ),
        config_version="cfg-1",
        it_load=RichMeasurement(
            name="it_load", value=it, unit="kW",
            source=MeasurementSource.MEASURED,
        ),
        cooling_load=RichMeasurement(
            name="cooling_load", value=300.0, unit="kW",
            source=MeasurementSource.MEASURED,
        ),
        pue=RichMeasurement(
            name="pue", value=pue, unit="dimensionless",
            source=MeasurementSource.MEASURED,
        ),
        quality_eligible=True,
    )
    result = decide_transparently(req)
    persist_decision(conn, req, result)
    row = conn.execute("SELECT * FROM decisions").fetchone()
    return {
        "id": row["id"],
        "result": __import__("json").loads(row["result_json"]),
    }


def test_basic_extraction(db_conn):
    record = _persisted_record(db_conn, it=400.0, pue=1.4)
    summary = build_engineering_summary(record)
    assert summary["pue"] == 1.4
    assert summary["pue_provenance"] == "MEASURED"
    assert summary["current_it_load_kw"] == 400.0
    assert summary["current_facility_power_kw"] == 560.0
    assert summary["predicted_facility_power_kw"] == 560.0


def test_headroom_present_for_both_states(db_conn):
    record = _persisted_record(db_conn, it=400.0, elec_cap=1000.0)
    summary = build_engineering_summary(record)
    current = summary["current_electrical"]
    assert current["physical_capacity_kw"] == 1000.0
    assert current["permitted_kw"] == 900.0
    assert current["observed_kw"] == 560.0
    assert current["headroom_to_permitted_kw"] == 340.0
    assert current["headroom_to_physical_kw"] == 440.0
    assert current["reserve_pct"] == 0.10


def test_cooling_headroom(db_conn):
    record = _persisted_record(db_conn, cool_cap=800.0, cool_res=0.10)
    summary = build_engineering_summary(record)
    current = summary["current_cooling"]
    assert current["permitted_kw"] == 720.0
    assert current["observed_kw"] == 300.0
    assert current["headroom_to_permitted_kw"] == 420.0


def test_missing_evidence_raises():
    """A record without the PUE-integrated evidence cannot be summarised."""
    record = {
        "id": "old-1",
        "result": {"evidence": {"calculation_inputs": {}}},
    }
    with pytest.raises(ValueError, match="predates the PUE integration"):
        build_engineering_summary(record)


def test_missing_result_raises():
    with pytest.raises(ValueError, match="predates"):
        build_engineering_summary({"id": "old-1", "result": {}})


def test_headroom_roundtrip_matches_engine(db_conn):
    """Extracted headroom equals the engine's own (permit-observed) math."""
    record = _persisted_record(db_conn, it=400.0, elec_cap=1000.0)
    summary = build_engineering_summary(record)
    # Engine computed 900 - 560 = 340; extraction must match exactly.
    assert summary["current_electrical"]["headroom_to_permitted_kw"] == 340.0
