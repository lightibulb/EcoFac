# Copyright 2026 Lightibulb
"""PUE integration: facility power = IT load x PUE."""

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
    TransparentDecisionRequest,
    decide_transparently,
)


def _config(cap=500.0) -> ConstraintConfig:
    return ConstraintConfig(
        electrical=ElectricalLimits(capacity=cap, reserve_pct=0.10),
        cooling=CoolingLimits(capacity=800.0, reserve_pct=0.10),
    )


def _pue(value, source=MeasurementSource.MEASURED):
    return RichMeasurement(
        name="pue",
        value=value,
        unit="dimensionless",
        source=source,
    )


def _req(it=400.0, cool=100.0, pue=None, cap=500.0) -> TransparentDecisionRequest:
    return TransparentDecisionRequest(
        config=_config(cap=cap),
        config_version="cfg-1",
        it_load=RichMeasurement(
            name="it_load", value=it, unit="kW",
            source=MeasurementSource.MEASURED,
        ),
        cooling_load=RichMeasurement(
            name="cooling_load", value=cool, unit="kW",
            source=MeasurementSource.MEASURED,
        ),
        pue=pue,
        quality_eligible=True,
    )


def test_high_pue_flips_verdict_to_reject():
    """IT load 400 within permitted 450, but 400 x 1.4 = 560 > 500 physical."""
    result = decide_transparently(
        _req(it=400.0, pue=_pue(1.4, MeasurementSource.MEASURED), cap=500.0)
    )
    assert result.status == "REJECT"
    assert result.precedence == "hard_limit_violation"


def test_low_pue_keeps_approve():
    result = decide_transparently(
        _req(it=200.0, pue=_pue(1.4, MeasurementSource.MEASURED), cap=500.0)
    )
    assert result.status == "APPROVE"


def test_no_pue_defaults_to_unity_and_high_uncertainty():
    result = decide_transparently(_req(it=400.0, cap=1000.0))
    assert result.status == "APPROVE"
    inputs = result.evidence.calculation_inputs
    assert inputs["pue"] == 1.0
    assert inputs["pue_provenance"] == "ASSUMED"


def test_pue_recorded_in_evidence():
    result = decide_transparently(
        _req(it=400.0, pue=_pue(1.3, MeasurementSource.CONFIGURED), cap=1000.0)
    )
    inputs = result.evidence.calculation_inputs
    assert inputs["pue"] == 1.3
    assert inputs["pue_provenance"] == "CONFIGURED"
    assert inputs["current_facility_power_kw"] == 400.0 * 1.3


def test_facility_power_used_for_constraint_not_it_load():
    """Same IT load, different PUE -> different outcome."""
    ok = decide_transparently(
        _req(it=400.0, pue=_pue(1.0, MeasurementSource.MEASURED), cap=500.0)
    )
    bad = decide_transparently(
        _req(it=400.0, pue=_pue(1.4, MeasurementSource.MEASURED), cap=500.0)
    )
    assert ok.status == "APPROVE"
    assert bad.status == "REJECT"


def test_pue_wrong_unit_rejected():
    bad_pue = RichMeasurement(
        name="pue", value=1.4, unit="kW", source=MeasurementSource.MEASURED
    )
    request = TransparentDecisionRequest(
        config=_config(cap=1000.0),
        config_version="cfg-1",
        it_load=RichMeasurement(
            name="it_load", value=400.0, unit="kW",
            source=MeasurementSource.MEASURED,
        ),
        cooling_load=RichMeasurement(
            name="cooling_load", value=100.0, unit="kW",
            source=MeasurementSource.MEASURED,
        ),
        pue=bad_pue,
        quality_eligible=True,
    )
    with pytest.raises(ValueError, match="dimensionless"):
        decide_transparently(request)


def test_pue_in_input_fingerprint():
    r1 = decide_transparently(
        _req(it=400.0, pue=_pue(1.0, MeasurementSource.MEASURED), cap=1000.0)
    )
    r2 = decide_transparently(
        _req(it=400.0, pue=_pue(1.4, MeasurementSource.MEASURED), cap=1000.0)
    )
    assert (
        r1.fingerprint.input_fingerprint
        != r2.fingerprint.input_fingerprint
    )


def test_pue_with_uncertainty_metadata_surfaces_in_sensitivity():
    pue = RichMeasurement(
        name="pue", value=1.4, unit="dimensionless",
        source=MeasurementSource.MEASURED,
        uncertainty=UncertaintyMetadata(absolute_tolerance=0.1),
    )
    result = decide_transparently(_req(it=400.0, pue=pue, cap=1000.0))
    inputs = result.evidence.calculation_inputs
    assert inputs["pue"] == 1.4
