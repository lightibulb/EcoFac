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


def test_pue_uncertainty_changes_the_verdict_across_its_band():
    """PUE 1.3 +/- 0.4: the band spans APPROVE (floor 1.0) to REJECT (1.7).

    400 kW IT at PUE 1.0 -> 400 kW facility (inside permitted 540); at PUE
    1.7 -> 680 kW, above the 600 kW physical capacity. The lower bound is
    clamped to the physical PUE floor of 1.0 (1.3 - 0.4 = 0.9 is impossible).
    """
    pue = RichMeasurement(
        name="pue", value=1.3, unit="dimensionless",
        source=MeasurementSource.MEASURED,
        uncertainty=UncertaintyMetadata(absolute_tolerance=0.4),
    )
    result = decide_transparently(_req(it=400.0, pue=pue, cap=600.0))
    pue_sens = next(s for s in result.sensitivity if s.measurement_name == "pue")
    assert pue_sens.could_change_verdict is True
    assert pue_sens.lower_bound == 1.0
    assert pue_sens.upper_bound == 1.7
    assert pue_sens.status_at_lower == "APPROVE"
    assert pue_sens.status_at_upper == "REJECT"
    assert "clamped" in pue_sens.reason
    assert "pue" in result.uncertainty.tolerance_used
    assert result.evidence.calculation_inputs["pue"] == 1.3


def test_pue_uncertainty_with_headroom_preserves_the_verdict():
    pue = RichMeasurement(
        name="pue", value=1.4, unit="dimensionless",
        source=MeasurementSource.MEASURED,
        uncertainty=UncertaintyMetadata(absolute_tolerance=0.05),
    )
    result = decide_transparently(_req(it=100.0, pue=pue, cap=1000.0))
    pue_sens = next(s for s in result.sensitivity if s.measurement_name == "pue")
    assert pue_sens.could_change_verdict is False
    assert pue_sens.status_at_lower == pue_sens.status_at_upper == result.status


def test_pue_without_tolerance_has_no_sensitivity_entry():
    result = decide_transparently(_req(it=400.0, pue=_pue(1.3), cap=600.0))
    assert all(s.measurement_name != "pue" for s in result.sensitivity)
