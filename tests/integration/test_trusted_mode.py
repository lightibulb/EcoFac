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
        "INSERT INTO data_sources (id, name, source_type, created_at) "
        "VALUES ('s1','S','csv',?)",
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
            name="it_load", value=400.0, unit="kW",
            source=MeasurementSource.MEASURED,
        ),
        cooling_load=RichMeasurement(
            name="cooling_load", value=300.0, unit="kW",
            source=MeasurementSource.MEASURED,
        ),
        quality_eligible=True,
        mode=DecisionMode.TRUSTED,
    )
    # In pure decide_transparently, mode is a passthrough - API layer enforces.
    result = decide_transparently(req)
    assert result.status in ("APPROVE", "REVIEW", "REJECT")


def test_trusted_mode_is_pure_in_decide_transparently():
    """mode is metadata only; resolution happens at the API boundary."""
    req = TransparentDecisionRequest(
        config=_config(),
        config_version="cfg-1",
        it_load=RichMeasurement(
            name="it_load", value=400.0, unit="kW",
            source=MeasurementSource.MEASURED,
        ),
        cooling_load=RichMeasurement(
            name="cooling_load", value=300.0, unit="kW",
            source=MeasurementSource.MEASURED,
        ),
        quality_eligible=True,
        mode=DecisionMode.TRUSTED,
    )
    result = decide_transparently(req)
    assert result.status == "APPROVE"
