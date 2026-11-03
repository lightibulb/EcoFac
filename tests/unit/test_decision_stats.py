# Copyright 2026 Lightibulb
"""Aggregate decision metrics."""

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
from ecofac.decision.stats import aggregate_decisions
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


def _config() -> ConstraintConfig:
    return ConstraintConfig(
        electrical=ElectricalLimits(capacity=1000.0, reserve_pct=0.10),
        cooling=CoolingLimits(capacity=800.0, reserve_pct=0.10),
    )


def _req(it=400.0, cv="cfg-1") -> TransparentDecisionRequest:
    return TransparentDecisionRequest(
        config=_config(),
        config_version=cv,
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


def _persist(conn, it=400.0, cv="cfg-1"):
    req = _req(it=it, cv=cv)
    result = decide_transparently(req)
    return persist_decision(conn, req, result)


def test_empty_table(db_conn):
    stats = aggregate_decisions(db_conn)
    assert stats["total"] == 0
    assert stats["approved"] == 0
    assert stats["rejected"] == 0
    assert stats["review"] == 0
    assert stats["latest_row"] is None
    assert stats["recent_rows"] == []


def test_counts_by_status(db_conn):
    _persist(db_conn, it=400.0)       # APPROVE
    _persist(db_conn, it=400.0)       # APPROVE
    _persist(db_conn, it=1100.0)      # REJECT
    stats = aggregate_decisions(db_conn)
    assert stats["total"] == 3
    assert stats["approved"] == 2
    assert stats["rejected"] == 1
    assert stats["review"] == 0


def test_review_counted(db_conn):
    req = TransparentDecisionRequest(
        config=_config(), config_version="cfg-1",
        it_load=RichMeasurement(
            name="it_load", value=400.0, unit="kW",
            source=MeasurementSource.MEASURED,
        ),
        cooling_load=RichMeasurement(
            name="cooling_load", value=300.0, unit="kW",
            source=MeasurementSource.MEASURED,
        ),
        quality_eligible=False,
        quality_flags=["missing_data"],
    )
    result = decide_transparently(req)
    persist_decision(db_conn, req, result)
    stats = aggregate_decisions(db_conn)
    assert stats["review"] == 1
    assert stats["total"] == 1


def test_latest_row_present(db_conn):
    _persist(db_conn)
    stats = aggregate_decisions(db_conn)
    assert stats["latest_row"] is not None


def test_recent_limit(db_conn):
    for _ in range(15):
        _persist(db_conn)
    stats = aggregate_decisions(db_conn, recent_limit=10)
    assert len(stats["recent_rows"]) == 10


def test_recent_ordering_newest_first(db_conn):
    ids = [_persist(db_conn) for _ in range(3)]
    stats = aggregate_decisions(db_conn)
    # Most recent is the last persisted (created_at has µs resolution)
    assert stats["recent_rows"][0]["id"] == ids[-1]


def test_recent_limit_zero_returns_empty(db_conn):
    _persist(db_conn)
    stats = aggregate_decisions(db_conn, recent_limit=0)
    assert stats["recent_rows"] == []
    assert stats["total"] == 1
