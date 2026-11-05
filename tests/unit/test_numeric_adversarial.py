# Copyright 2026 Lightibulb
"""Adversarial numeric inputs must never reach calculations or JSON output."""

import json
import math
import random

import pydantic
import pytest

from ecofac.decision.calculations import PUEResult, PUESource
from ecofac.decision.limits import ConstraintConfig, CoolingLimits, ElectricalLimits
from ecofac.decision.measurements import (
    MeasurementSource,
    RichMeasurement,
    UncertaintyMetadata,
)
from ecofac.decision.transparent import (
    TransparentDecisionRequest,
    decide_transparently,
)

BAD = [float("nan"), float("inf"), float("-inf")]


def measurement(value, **kw):
    return RichMeasurement(
        name="m", value=value, unit="kW", source=MeasurementSource.MEASURED, **kw
    )


def cfg():
    return ConstraintConfig(
        electrical=ElectricalLimits(capacity=1000.0, reserve_pct=0.1),
        cooling=CoolingLimits(capacity=800.0, reserve_pct=0.1),
    )


def request(**kw):
    base = {
        "config": cfg(),
        "config_version": "c",
        "it_load": measurement(400.0),
        "cooling_load": measurement(300.0),
        "quality_eligible": True,
    }
    base.update(kw)
    return TransparentDecisionRequest(**base)


@pytest.mark.parametrize("bad", BAD)
def test_measurement_value_rejects_non_finite(bad):
    with pytest.raises(pydantic.ValidationError):
        measurement(bad)


@pytest.mark.parametrize("bad", BAD)
def test_tolerances_reject_non_finite(bad):
    with pytest.raises(pydantic.ValidationError):
        UncertaintyMetadata(absolute_tolerance=bad)
    with pytest.raises(pydantic.ValidationError):
        UncertaintyMetadata(relative_tolerance=bad)


@pytest.mark.parametrize("bad", BAD)
def test_scenario_deltas_reject_non_finite(bad):
    with pytest.raises(pydantic.ValidationError):
        request(proposed_it_delta=bad)
    with pytest.raises(pydantic.ValidationError):
        request(proposed_cooling_delta=bad)


@pytest.mark.parametrize("bad", BAD)
def test_capacities_and_reserves_reject_non_finite(bad):
    with pytest.raises(pydantic.ValidationError):
        ElectricalLimits(capacity=bad, reserve_pct=0.1)
    with pytest.raises(pydantic.ValidationError):
        CoolingLimits(capacity=800.0, reserve_pct=bad)
    with pytest.raises(pydantic.ValidationError):
        ElectricalLimits(capacity=800.0, reserve_pct=0.1, warning_threshold_pct=bad)
    with pytest.raises(pydantic.ValidationError):
        ElectricalLimits(capacity=800.0, reserve_pct=0.1, min_headroom=bad)


@pytest.mark.parametrize("bad", [*BAD, 0.0, 0.5, 0.999, 10.01, -1.0])
def test_pue_result_rejects_non_physical_values(bad):
    with pytest.raises(pydantic.ValidationError):
        PUEResult(value=bad, source=PUESource.MEASURED)


@pytest.mark.parametrize("bad", BAD)
def test_pue_measurement_non_finite_rejected(bad):
    with pytest.raises(pydantic.ValidationError):
        RichMeasurement(
            name="pue", value=bad, unit="dimensionless",
            source=MeasurementSource.CONFIGURED,
        )


def test_negative_measurement_is_rejected():
    with pytest.raises(pydantic.ValidationError):
        measurement(-1.0)


def test_wide_tolerance_never_implies_a_negative_load():
    m = measurement(100.0, uncertainty=UncertaintyMetadata(absolute_tolerance=500.0))
    assert m.band() == (0.0, 600.0)
    assert m.band_clamped() is True
    narrow = measurement(100.0, uncertainty=UncertaintyMetadata(absolute_tolerance=5.0))
    assert narrow.band() == (95.0, 105.0)
    assert narrow.band_clamped() is False


def test_wide_tolerance_decision_has_non_negative_bounds_and_clamp_note():
    result = decide_transparently(
        request(
            it_load=measurement(
                100.0, uncertainty=UncertaintyMetadata(absolute_tolerance=500.0)
            )
        )
    )
    sens = next(s for s in result.sensitivity if s.measurement_name == "m")
    assert sens.lower_bound == 0.0
    assert "clamped" in sens.reason


def test_results_are_strict_json_serialisable():
    rng = random.Random(1234)
    for _ in range(40):
        it = rng.uniform(0, 1500)
        tol = rng.uniform(0, 2000)
        result = decide_transparently(
            request(
                it_load=measurement(
                    it, uncertainty=UncertaintyMetadata(absolute_tolerance=tol)
                ),
                proposed_it_delta=rng.uniform(-3000, 3000),
            )
        )
        text = json.dumps(result.model_dump(mode="json"), allow_nan=False)
        assert "NaN" not in text
        assert math.isfinite(result.evidence.calculation_inputs["predicted_facility_power_kw"])


def test_huge_but_finite_values_do_not_overflow_into_inf():
    result = decide_transparently(request(it_load=measurement(1e300)))
    assert result.status == "REJECT"
    json.dumps(result.model_dump(mode="json"), allow_nan=False)
