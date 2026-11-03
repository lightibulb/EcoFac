# Copyright 2026 Lightibulb
"""Negative and invalid proposed loads must be rejected, not clamped."""

import pydantic
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
    it=400.0, cool=300.0, it_delta=0.0, cool_delta=0.0
) -> TransparentDecisionRequest:
    return TransparentDecisionRequest(
        config=_config(),
        config_version="cfg-1",
        it_load=RichMeasurement(
            name="it_load", value=it, unit="kW",
            source=MeasurementSource.MEASURED,
        ),
        cooling_load=RichMeasurement(
            name="cooling_load", value=cool, unit="kW",
            source=MeasurementSource.MEASURED,
        ),
        proposed_it_delta=it_delta,
        proposed_cooling_delta=cool_delta,
        quality_eligible=True,
    )


def test_negative_measurement_value_rejected_at_schema():
    with pytest.raises(pydantic.ValidationError):
        RichMeasurement(
            name="it_load", value=-1.0, unit="kW",
            source=MeasurementSource.MEASURED,
        )


def test_negative_predicted_it_rejected():
    result = decide_transparently(_req(it=400.0, it_delta=-500.0))
    assert result.status == "REJECT"
    assert result.precedence == "invalid_proposal"


def test_negative_predicted_cooling_rejected():
    result = decide_transparently(_req(cool=300.0, cool_delta=-500.0))
    assert result.status == "REJECT"
    assert result.precedence == "invalid_proposal"


def test_exactly_zero_predicted_it_is_valid():
    result = decide_transparently(_req(it=400.0, it_delta=-400.0, cool=100.0))
    assert result.precedence != "invalid_proposal"


def test_invalid_proposal_recorded_in_evidence():
    result = decide_transparently(_req(it=400.0, it_delta=-500.0))
    inputs = result.evidence.calculation_inputs
    assert inputs["invalid_proposal_reasons"]
    assert "negative" in inputs["invalid_proposal_reasons"][0].lower()
