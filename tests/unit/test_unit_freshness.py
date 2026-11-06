# Copyright 2026 Lightibulb
"""Unit validation and freshness checks."""

import datetime as dt

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
    TransparentDecisionRequest,
    decide_transparently,
)


def _config() -> ConstraintConfig:
    return ConstraintConfig(
        electrical=ElectricalLimits(capacity=1000.0, reserve_pct=0.10),
        cooling=CoolingLimits(capacity=800.0, reserve_pct=0.10),
    )


def _req(
    it_unit: str = "kW",
    cool_unit: str = "kW",
    max_age: int | None = None,
    it_timestamp: str | None = None,
    cool_timestamp: str | None = None,
) -> TransparentDecisionRequest:
    return TransparentDecisionRequest(
        config=_config(),
        config_version="cfg-1",
        it_load=RichMeasurement(
            name="it_load",
            value=400.0,
            unit=it_unit,
            source=MeasurementSource.MEASURED,
            measurement_timestamp=it_timestamp,
        ),
        cooling_load=RichMeasurement(
            name="cooling_load",
            value=300.0,
            unit=cool_unit,
            source=MeasurementSource.MEASURED,
            measurement_timestamp=cool_timestamp,
        ),
        quality_eligible=True,
        max_measurement_age_seconds=max_age,
    )


def test_wrong_it_unit_rejected():
    with pytest.raises(ValueError, match="expected 'kW'"):
        decide_transparently(_req(it_unit="W"))


def test_wrong_cooling_unit_rejected():
    with pytest.raises(ValueError, match="expected 'kW'"):
        decide_transparently(_req(cool_unit="W"))


def test_correct_units_accepted():
    result = decide_transparently(_req())
    assert result.status == "APPROVE"


def test_stale_measurement_forces_review():
    old_ts = (dt.datetime.now(dt.UTC) - dt.timedelta(hours=10)).isoformat()
    result = decide_transparently(_req(max_age=3600, it_timestamp=old_ts))
    assert result.status == "REVIEW"
    assert result.uncertainty.stale_measurements


def test_fresh_measurement_ok():
    fresh_ts = dt.datetime.now(dt.UTC).isoformat()
    result = decide_transparently(_req(max_age=3600, it_timestamp=fresh_ts))
    assert result.status == "APPROVE"


def test_no_max_age_means_no_check():
    old_ts = (dt.datetime.now(dt.UTC) - dt.timedelta(days=30)).isoformat()
    result = decide_transparently(_req(max_age=None, it_timestamp=old_ts))
    assert result.status == "APPROVE"


def test_unparseable_timestamp_treated_as_stale():
    result = decide_transparently(_req(max_age=3600, it_timestamp="not-a-timestamp"))
    assert result.status == "REVIEW"
