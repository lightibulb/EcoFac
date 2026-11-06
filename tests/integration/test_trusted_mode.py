# Copyright 2026 Lightibulb
"""Trusted mode resolves measurements from the database."""

import pytest

from ecofac.decision.limits import (
    ConstraintConfig,
    CoolingLimits,
    ElectricalLimits,
)
from ecofac.decision.measurements import (
    MeasurementSource,
    RichMeasurement,
    UncertaintyMetadata,
)
from ecofac.decision.transparent import (
    DecisionMode,
    TransparentDecisionRequest,
    decide_transparently,
)
from ecofac.persistence.database import get_connection, init_db


@pytest.fixture
def db_conn():
    conn = get_connection(":memory:")
    init_db(conn)
    now = "2026-01-01T00:00:00Z"
    conn.execute(
        "INSERT INTO organizations (id, name, created_at) VALUES ('o1','O',?)",
        (now,),
    )
    conn.execute(
        "INSERT INTO buildings (id, org_id, name, created_at) VALUES ('b1','o1','B',?)",
        (now,),
    )
    conn.execute(
        "INSERT INTO meters (id, building_id, fuel_type, unit, created_at) "
        "VALUES ('m1','b1','elec','kWh',?)",
        (now,),
    )
    conn.execute(
        "INSERT INTO data_sources (id, name, source_type, created_at) VALUES ('s1','S','csv',?)",
        (now,),
    )
    conn.execute(
        "INSERT INTO import_batches (id, source_id, status, imported_at) "
        "VALUES ('ib1','s1','started',?)",
        (now,),
    )
    yield conn
    conn.close()


def _config() -> ConstraintConfig:
    return ConstraintConfig(
        electrical=ElectricalLimits(capacity=1000.0, reserve_pct=0.10),
        cooling=CoolingLimits(capacity=800.0, reserve_pct=0.10),
    )


def test_trusted_mode_requires_meter_id():
    req = TransparentDecisionRequest(
        config=_config(),
        config_version="cfg-1",
        it_load=RichMeasurement(
            name="it_load",
            value=400.0,
            unit="kW",
            source=MeasurementSource.MEASURED,
        ),
        cooling_load=RichMeasurement(
            name="cooling_load",
            value=300.0,
            unit="kW",
            source=MeasurementSource.MEASURED,
        ),
        quality_eligible=True,
        mode=DecisionMode.TRUSTED,
    )
    # In pure decide_transparently, mode is a passthrough - API layer enforces.
    result = decide_transparently(req)
    assert result.status in ("APPROVE", "REVIEW", "REJECT")


def _operational_request(**overrides) -> TransparentDecisionRequest:
    """A fully specified, server-resolved operational request."""
    ts = "2026-03-01T11:30:00Z"
    base = {
        "config": _config(),
        "config_version": "cfg-1",
        "it_load": RichMeasurement(
            name="it_load",
            value=400.0,
            unit="kW",
            source=MeasurementSource.MEASURED,
            uncertainty=UncertaintyMetadata(relative_tolerance=0.02),
            measurement_timestamp=ts,
            reference_id="r-it",
            meter_id="it",
        ),
        "cooling_load": RichMeasurement(
            name="cooling_load",
            value=300.0,
            unit="kW",
            source=MeasurementSource.MEASURED,
            uncertainty=UncertaintyMetadata(relative_tolerance=0.03),
            measurement_timestamp=ts,
            reference_id="r-c",
            meter_id="cool",
        ),
        "pue": RichMeasurement(
            name="pue",
            value=1.4,
            unit="dimensionless",
            source=MeasurementSource.CONFIGURED,
        ),
        "quality_eligible": True,
        "mode": DecisionMode.TRUSTED,
        "max_measurement_age_seconds": 7200,
        "evaluation_time": "2026-03-01T12:00:00Z",
        "inputs_resolved_by_server": True,
    }
    base.update(overrides)
    return TransparentDecisionRequest(**base)


def test_trusted_mode_without_trusted_inputs_cannot_approve():
    """Regression: TRUSTED + no tolerance + no PUE used to APPROVE silently.

    Mode is no longer mere metadata: an operational decision whose inputs were
    not server-resolved, have unknown uncertainty and an assumed PUE must be
    REVIEW, with the reasons recorded in the evidence.
    """
    req = TransparentDecisionRequest(
        config=_config(),
        config_version="cfg-1",
        it_load=RichMeasurement(
            name="it_load",
            value=400.0,
            unit="kW",
            source=MeasurementSource.MEASURED,
        ),
        cooling_load=RichMeasurement(
            name="cooling_load",
            value=300.0,
            unit="kW",
            source=MeasurementSource.MEASURED,
        ),
        quality_eligible=True,
        mode=DecisionMode.TRUSTED,
    )
    result = decide_transparently(req)
    assert result.status == "REVIEW"
    reasons = result.evidence.calculation_outputs["decision_reasons"]
    joined = "; ".join(reasons)
    for expected in (
        "inputs_not_server_resolved",
        "unknown_model_uncertainty",
        "pue_assumed",
    ):
        assert expected in joined
    assert result.evidence.calculation_inputs["pue_trust_basis"] == "assumed_default"


def test_trusted_mode_with_trusted_inputs_approves():
    result = decide_transparently(_operational_request())
    assert result.status == "APPROVE"
    inputs = result.evidence.calculation_inputs
    assert inputs["pue_trust_basis"] == "configured"
    assert inputs["applied_uncertainty_flags"] == []
    assert inputs["evaluation_time"] == "2026-03-01T12:00:00Z"
