"""Regression tests for local EcoFac safety corrections."""

import math

from ecofac.decision.measurements import (
    MeasurementSource,
    RichMeasurement,
)
from ecofac.decision.uncertainty import (
    ModelUncertaintyLevel,
    _assess_model_uncertainty,
)


def measured(name="IT load", value=400.0):
    return RichMeasurement(
        name=name,
        value=value,
        unit="kW",
        source=MeasurementSource.MEASURED,
    )


def test_measured_without_tolerance_is_not_low_uncertainty():
    level, rationale = _assess_model_uncertainty([measured()])
    assert level == ModelUncertaintyLevel.UNKNOWN
    assert any("UNKNOWN" in item for item in rationale)


def test_measured_with_explicit_tolerance_is_not_claimed_perfect():
    measurement = RichMeasurement(
        name="IT load",
        value=400.0,
        unit="kW",
        source=MeasurementSource.MEASURED,
        uncertainty={"absolute_tolerance": 2.0},
    )
    level, _ = _assess_model_uncertainty([measurement])
    assert level == ModelUncertaintyLevel.MODERATE


def test_measurement_model_accepts_finite_demo_value():
    assert math.isfinite(measured().value)
