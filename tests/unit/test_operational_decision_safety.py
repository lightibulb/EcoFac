# Copyright 2026 Lightibulb
"""Operational (TRUSTED) decisions must never turn unknowns into APPROVE.

Hypothetical/exploratory behaviour is deliberately unchanged: the same facts
are recorded as advisories in the evidence but do not alter the verdict.
"""

import pytest

from ecofac.decision.limits import ConstraintConfig, CoolingLimits, ElectricalLimits
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

TS = "2026-03-01T11:30:00Z"
EVAL = "2026-03-01T12:00:00Z"


def config(cap=1000.0):
    return ConstraintConfig(
        electrical=ElectricalLimits(capacity=cap, reserve_pct=0.10),
        cooling=CoolingLimits(capacity=800.0, reserve_pct=0.10),
    )


def load(name, value, *, tol=0.02, source=MeasurementSource.MEASURED, ts=TS):
    return RichMeasurement(
        name=name,
        value=value,
        unit="kW",
        source=source,
        uncertainty=UncertaintyMetadata(relative_tolerance=tol) if tol else None,
        measurement_timestamp=ts,
        reference_id=f"ref-{name}",
    )


def configured_pue(value=1.4, **kw):
    return RichMeasurement(
        name="pue",
        value=value,
        unit="dimensionless",
        source=MeasurementSource.CONFIGURED,
        **kw,
    )


def request(**overrides):
    base = {
        "config": config(),
        "config_version": "cfg-1",
        "it_load": load("it_load", 400.0),
        "cooling_load": load("cooling_load", 300.0, tol=0.03),
        "pue": configured_pue(),
        "quality_eligible": True,
        "mode": DecisionMode.TRUSTED,
        "max_measurement_age_seconds": 7200,
        "evaluation_time": EVAL,
        "inputs_resolved_by_server": True,
    }
    base.update(overrides)
    return TransparentDecisionRequest(**base)


def reasons(result):
    return "; ".join(result.evidence.calculation_outputs["decision_reasons"])


# ---- positive control ------------------------------------------------------


def test_fully_trusted_inputs_approve():
    result = decide_transparently(request())
    assert result.status == "APPROVE"
    assert result.evidence.calculation_inputs["applied_uncertainty_flags"] == []


# ---- PUE: assumed / configured / trusted -----------------------------------


def test_missing_pue_is_review_not_unconditional_approve():
    result = decide_transparently(request(pue=None))
    assert result.status == "REVIEW"
    assert "pue_assumed" in reasons(result)
    inputs = result.evidence.calculation_inputs
    assert inputs["pue"] == 1.0
    assert inputs["pue_trust_basis"] == "assumed_default"
    assert inputs["pue_assumed"] is True
    assert any("default 1.0" in a for a in result.evidence.assumptions)


def test_assumed_pue_source_is_review():
    pue = RichMeasurement(
        name="pue", value=1.4, unit="dimensionless", source=MeasurementSource.ASSUMED
    )
    result = decide_transparently(request(pue=pue))
    assert result.status == "REVIEW"
    assert result.evidence.calculation_inputs["pue_trust_basis"] == "assumed"


def test_configured_pue_is_distinguished_and_visible():
    result = decide_transparently(request())
    inputs = result.evidence.calculation_inputs
    assert inputs["pue_trust_basis"] == "configured"
    assert inputs["pue_provenance"] == "CONFIGURED"
    assert any("explicitly configured" in a for a in result.evidence.assumptions)


def test_measured_pue_without_trusted_reference_is_flagged():
    pue = RichMeasurement(
        name="pue", value=1.4, unit="dimensionless", source=MeasurementSource.MEASURED
    )
    result = decide_transparently(request(pue=pue))
    assert result.status == "REVIEW"
    assert "pue_unverified" in reasons(result)


def test_measured_pue_with_trusted_reference_is_trusted():
    pue = RichMeasurement(
        name="pue",
        value=1.4,
        unit="dimensionless",
        source=MeasurementSource.MEASURED,
        reference_id="pue-ref-1",
    )
    result = decide_transparently(request(pue=pue))
    assert result.status == "APPROVE"
    assert result.evidence.calculation_inputs["pue_trust_basis"] == "trusted"


@pytest.mark.parametrize("bad", [0.5, 0.99, 10.5])
def test_non_physical_pue_is_rejected_by_the_engine(bad):
    # Bypass model-level checks so the engine's own validation is exercised.
    pue = RichMeasurement.model_construct(
        name="pue",
        value=bad,
        unit="dimensionless",
        source=MeasurementSource.CONFIGURED,
        uncertainty=None,
        measurement_timestamp=None,
        reference_id=None,
        meter_id=None,
        raw_value=None,
        raw_unit=None,
        interval_minutes=None,
    )
    with pytest.raises(ValueError, match="pue"):
        decide_transparently(request(pue=pue))


# ---- unknown uncertainty ---------------------------------------------------


def test_unknown_uncertainty_is_review_with_reasons_in_evidence():
    result = decide_transparently(
        request(
            it_load=load("it_load", 400.0, tol=None),
            cooling_load=load("cooling_load", 300.0, tol=None),
            pue=configured_pue(),
        )
    )
    assert result.status == "REVIEW"
    assert result.uncertainty.model_uncertainty_level.value == "UNKNOWN"
    text = reasons(result)
    assert "unknown_model_uncertainty" in text
    assert "missing_tolerance: 'it_load'" in text
    assert text in result.evidence.final_reason


def test_missing_tolerance_on_one_measurement_is_review():
    """Mixed-confidence inputs: one bounded, one unbounded -> REVIEW."""
    result = decide_transparently(request(cooling_load=load("cooling_load", 300.0, tol=None)))
    assert result.status == "REVIEW"
    text = reasons(result)
    assert "missing_tolerance: 'cooling_load'" in text
    assert "missing_tolerance: 'it_load'" not in text


def test_estimated_load_in_operational_mode_is_review():
    result = decide_transparently(
        request(
            cooling_load=load("cooling_load", 300.0, tol=0.03, source=MeasurementSource.ESTIMATED)
        )
    )
    assert result.status == "REVIEW"
    assert "unverified_provenance" in reasons(result)


def test_invalid_quality_is_review():
    result = decide_transparently(request(quality_eligible=False, quality_flags=["missing_data"]))
    assert result.status == "REVIEW"


def test_caller_flags_are_not_needed_to_reach_review():
    """Derived flags work even when the caller supplies none (or an empty list)."""
    result = decide_transparently(request(pue=None, uncertainty_flags=[]))
    assert result.status == "REVIEW"


def test_caller_cannot_launder_unresolved_inputs_into_trusted():
    result = decide_transparently(request(inputs_resolved_by_server=False))
    assert result.status == "REVIEW"
    assert "inputs_not_server_resolved" in reasons(result)


def test_unclassified_meter_role_caveat_forces_review():
    result = decide_transparently(request(trust_caveats=["meter_role_unclassified"]))
    assert result.status == "REVIEW"
    assert "meter_role_unclassified" in reasons(result)


def test_missing_timestamp_or_freshness_policy_is_review():
    no_ts = decide_transparently(request(it_load=load("it_load", 400.0, ts=None)))
    assert no_ts.status == "REVIEW"
    assert "missing_timestamp: 'it_load'" in reasons(no_ts)
    no_policy = decide_transparently(request(max_measurement_age_seconds=None))
    assert no_policy.status == "REVIEW"
    assert "freshness_policy_missing" in reasons(no_policy)


# ---- hypothetical behaviour is preserved -----------------------------------


def test_hypothetical_missing_pue_still_approves_but_assumption_is_visible():
    result = decide_transparently(
        request(
            pue=None,
            mode=DecisionMode.HYPOTHETICAL,
            inputs_resolved_by_server=False,
        )
    )
    assert result.status == "APPROVE"
    inputs = result.evidence.calculation_inputs
    assert inputs["pue_trust_basis"] == "assumed_default"
    assert inputs["applied_uncertainty_flags"] == []
    assert inputs["derived_uncertainty_flags"]  # computed, recorded, not applied
    assert any("advisory (not applied)" in a for a in result.evidence.assumptions)


def test_hypothetical_unknown_uncertainty_does_not_block():
    result = decide_transparently(
        request(
            it_load=load("it_load", 400.0, tol=None),
            cooling_load=load("cooling_load", 300.0, tol=None),
            mode=DecisionMode.HYPOTHETICAL,
            inputs_resolved_by_server=False,
        )
    )
    assert result.status == "APPROVE"


# ---- sensitivity feeds the operational verdict ------------------------------


def wide_pue_request(mode):
    """Baseline APPROVE, but PUE 1.3 +/- 0.3 reaches 1.6 -> 640 kW > permitted 630."""
    return request(
        config=config(cap=700.0),
        pue=configured_pue(1.3, uncertainty=UncertaintyMetadata(absolute_tolerance=0.3)),
        mode=mode,
        inputs_resolved_by_server=mode == DecisionMode.TRUSTED,
    )


def test_operational_sensitivity_flip_downgrades_approve_to_review():
    result = decide_transparently(wide_pue_request(DecisionMode.TRUSTED))
    pue_sens = next(s for s in result.sensitivity if s.measurement_name == "pue")
    assert pue_sens.baseline_status == "APPROVE"
    assert pue_sens.could_change_verdict is True
    assert result.status == "REVIEW"
    assert "sensitivity_changes_verdict" in reasons(result)
    assert result.evidence.calculation_outputs["physical_status_before_uncertainty"] == "APPROVE"


def test_hypothetical_sensitivity_flip_is_recorded_but_not_applied():
    result = decide_transparently(wide_pue_request(DecisionMode.HYPOTHETICAL))
    assert result.status == "APPROVE"
    assert any(s.could_change_verdict for s in result.sensitivity)
    assert any("sensitivity_changes_verdict" in a for a in result.evidence.assumptions)


# ---- cooling basis is explicit ----------------------------------------------


def test_cooling_basis_is_measured_not_modelled():
    inputs = decide_transparently(request()).evidence.calculation_inputs
    assert inputs["cooling_load_basis"] == "measured_cooling_load"
    assert inputs["cooling_demand_modelled"] is False
