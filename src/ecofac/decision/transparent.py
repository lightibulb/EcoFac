# Copyright 2026 Lightibulb
"""Transparent, evidence-backed decision orchestrator.

Wraps the deterministic state machine with:
  1. Uncertainty assessment (data-quality vs physical-model)
  2. Sensitivity analysis (does any tolerance band flip the verdict?)
  3. Full evidence bundle assembly
  4. Structured explanation generation
  5. Reproducibility fingerprint

Missing measurements do not crash the call: they force ``quality_eligible``
to False and are listed in the uncertainty report, producing a REVIEW.
"""

from typing import Any

import pydantic

from ecofac.decision.evidence import (
    DecisionEvidenceBundle,
    InputReference,
    build_evidence_bundle,
)
from ecofac.decision.explain import build_explanation
from ecofac.decision.limits import ConstraintConfig, ConstraintSeverity
from ecofac.decision.measurements import MeasurementSource, RichMeasurement
from ecofac.decision.reproducibility import (
    ReproductionFingerprint,
    build_fingerprint,
)
from ecofac.decision.state_machine import (
    DecisionContext,
    FinalDecision,
    evaluate_decision,
)
from ecofac.decision.uncertainty import (
    MODEL_LIMITATIONS,
    SensitivityResult,
    UncertaintyReport,
    assess_uncertainty,
)


class TransparentDecisionRequest(pydantic.BaseModel):
    """Complete request for a transparent decision."""

    model_config = pydantic.ConfigDict(frozen=True)

    config: ConstraintConfig
    config_version: str
    it_load: RichMeasurement
    cooling_load: RichMeasurement
    proposed_it_delta: float = 0.0
    proposed_cooling_delta: float = 0.0
    quality_eligible: bool
    quality_stale: bool = False
    quality_flags: list[str] = pydantic.Field(default_factory=list)
    uncertainty_flags: list[str] = pydantic.Field(default_factory=list)
    conflicting_inputs: list[str] = pydantic.Field(default_factory=list)


class TransparentDecisionResult(pydantic.BaseModel):
    """Complete result with evidence, explanation, and reproducibility."""

    status: str
    precedence: str
    engine_version: str
    config_version: str
    decision_timestamp: str
    state_machine_decision: FinalDecision
    uncertainty: UncertaintyReport
    sensitivity: list[SensitivityResult]
    evidence: DecisionEvidenceBundle
    explanation: dict
    fingerprint: ReproductionFingerprint


def _extract(measurement: RichMeasurement) -> tuple[float, bool]:
    """Return (value_or_zero, present)."""
    if measurement.value is None or measurement.source == MeasurementSource.MISSING:
        return 0.0, False
    return float(measurement.value), True


def _to_input_reference(m: RichMeasurement) -> InputReference:
    return InputReference(
        name=m.name,
        value=m.value,
        unit=m.unit,
        source=m.source.value,
        reference_id=m.reference_id,
        timestamp=m.measurement_timestamp,
    )


def _constraint_snapshot(config: ConstraintConfig) -> dict:
    return config.model_dump(mode="json")


def _formulas() -> dict[str, str]:
    return {
        "predicted_electrical_kw": "current_electrical_kw + proposed_it_delta",
        "predicted_cooling_kw": "current_cooling_kw + proposed_cooling_delta",
        "permitted_kw": "capacity * (1 - reserve_pct)",
        "headroom_to_permitted": "permitted_kw - observed",
        "headroom_to_physical": "capacity - observed",
    }


def _sensitivity_reason(
    name: str,
    low: float,
    high: float,
    status_low: str,
    status_high: str,
    baseline_status: str,
    *,
    changed: bool,
) -> str:
    if changed:
        return (
            f"'{name}' tolerance band [{low:.4f}, {high:.4f}] contains "
            f"values that produce status other than {baseline_status} "
            f"(saw {status_low} at lower, {status_high} at upper)"
        )
    return (
        f"'{name}' tolerance band [{low:.4f}, {high:.4f}] preserves "
        f"baseline status {baseline_status}"
    )


def _compute_sensitivity(
    *,
    build_context: Any,
    baseline_status: str,
    it_load: RichMeasurement,
    cooling_load: RichMeasurement,
    current_it: float,
    current_cooling: float,
    predicted_it: float,
    predicted_cooling: float,
) -> list[SensitivityResult]:
    """Run sensitivity for every measurement that has tolerance metadata."""
    results: list[SensitivityResult] = []

    it_band = it_load.band()
    if it_band is not None:
        low, high = it_band

        def _status_it(measured_value: float) -> str:
            delta = measured_value - current_it
            ctx = build_context(
                predicted_electrical=max(0.0, predicted_it + delta),
                predicted_cooling=predicted_cooling,
                current_electrical=max(0.0, measured_value),
                current_cooling=current_cooling,
            )
            return evaluate_decision(ctx).status.value

        status_low = _status_it(low)
        status_high = _status_it(high)
        changed = status_low != baseline_status or status_high != baseline_status
        results.append(
            SensitivityResult(
                measurement_name=it_load.name,
                baseline_status=baseline_status,
                lower_bound=round(low, 4),
                upper_bound=round(high, 4),
                status_at_lower=status_low,
                status_at_upper=status_high,
                could_change_verdict=changed,
                reason=_sensitivity_reason(
                    it_load.name,
                    low,
                    high,
                    status_low,
                    status_high,
                    baseline_status,
                    changed=changed,
                ),
            )
        )

    cooling_band = cooling_load.band()
    if cooling_band is not None:
        low, high = cooling_band

        def _status_cooling(measured_value: float) -> str:
            delta = measured_value - current_cooling
            ctx = build_context(
                predicted_electrical=predicted_it,
                predicted_cooling=max(0.0, predicted_cooling + delta),
                current_electrical=current_it,
                current_cooling=max(0.0, measured_value),
            )
            return evaluate_decision(ctx).status.value

        status_low = _status_cooling(low)
        status_high = _status_cooling(high)
        changed = status_low != baseline_status or status_high != baseline_status
        results.append(
            SensitivityResult(
                measurement_name=cooling_load.name,
                baseline_status=baseline_status,
                lower_bound=round(low, 4),
                upper_bound=round(high, 4),
                status_at_lower=status_low,
                status_at_upper=status_high,
                could_change_verdict=changed,
                reason=_sensitivity_reason(
                    cooling_load.name,
                    low,
                    high,
                    status_low,
                    status_high,
                    baseline_status,
                    changed=changed,
                ),
            )
        )

    return results


def decide_transparently(
    request: TransparentDecisionRequest,
) -> TransparentDecisionResult:
    """Run a transparent decision: uncertainty, state machine, evidence, explain, fingerprint."""
    current_it, it_present = _extract(request.it_load)
    current_cooling, cooling_present = _extract(request.cooling_load)

    effective_it_delta = request.proposed_it_delta if it_present else 0.0
    effective_cooling_delta = request.proposed_cooling_delta if cooling_present else 0.0

    predicted_it = current_it + effective_it_delta
    predicted_cooling = current_cooling + effective_cooling_delta

    effective_quality_flags = list(request.quality_flags)
    if not it_present:
        effective_quality_flags.append("missing_it_load")
    if not cooling_present:
        effective_quality_flags.append("missing_cooling_load")
    effective_quality_eligible = (
        request.quality_eligible and it_present and cooling_present
    )

    measurements = [request.it_load, request.cooling_load]
    uncertainty = assess_uncertainty(
        measurements,
        quality_flags=effective_quality_flags,
        quality_stale=request.quality_stale,
        stale_measurement_names=(
            [m.name for m in measurements] if request.quality_stale else []
        ),
    )

    def _build_context(
        *,
        predicted_electrical: float,
        predicted_cooling: float,
        current_electrical: float,
        current_cooling: float,
    ) -> DecisionContext:
        return DecisionContext(
            config=request.config,
            config_version=request.config_version,
            current_electrical_kw=max(0.0, current_electrical),
            current_cooling_kw=max(0.0, current_cooling),
            predicted_electrical_kw=max(0.0, predicted_electrical),
            predicted_cooling_kw=max(0.0, predicted_cooling),
            quality_eligible=effective_quality_eligible,
            quality_stale=request.quality_stale,
            quality_flags=effective_quality_flags,
            uncertainty_flags=list(request.uncertainty_flags),
            conflicting_inputs=list(request.conflicting_inputs),
        )

    base_decision = evaluate_decision(
        _build_context(
            predicted_electrical=predicted_it,
            predicted_cooling=predicted_cooling,
            current_electrical=current_it,
            current_cooling=current_cooling,
        )
    )

    sensitivity = _compute_sensitivity(
        build_context=_build_context,
        baseline_status=base_decision.status.value,
        it_load=request.it_load,
        cooling_load=request.cooling_load,
        current_it=current_it,
        current_cooling=current_cooling,
        predicted_it=predicted_it,
        predicted_cooling=predicted_cooling,
    )

    passed_records = [
        ev for ev in base_decision.evaluated_constraints
        if ev.severity == ConstraintSeverity.OK
    ]
    _ = passed_records  # kept for symmetry with evidence bundle

    evidence = build_evidence_bundle(
        input_references=[_to_input_reference(m) for m in measurements],
        constraints_snapshot=_constraint_snapshot(request.config),
        calculation_inputs={
            "current_it_load_kw": current_it,
            "current_cooling_load_kw": current_cooling,
            "proposed_it_delta_kw": effective_it_delta,
            "proposed_cooling_delta_kw": effective_cooling_delta,
            "predicted_it_load_kw": predicted_it,
            "predicted_cooling_load_kw": predicted_cooling,
        },
        calculation_outputs={
            "status": base_decision.status.value,
            "precedence": base_decision.precedence.value,
        },
        formulas=_formulas(),
        assumptions=[*base_decision.reasons, *MODEL_LIMITATIONS],
        evaluated_constraints=base_decision.evaluated_constraints,
        uncertainty_report=uncertainty,
        sensitivity=sensitivity,
        final_status=base_decision.status.value,
        final_reason="; ".join(base_decision.reasons),
        engine_version=base_decision.engine_version,
        config_version=base_decision.config_version,
        decision_timestamp=base_decision.decision_timestamp,
    )

    explanation = build_explanation(
        status=base_decision.status.value,
        precedence=base_decision.precedence.value,
        evaluations=base_decision.evaluated_constraints,
        uncertainty=uncertainty,
        sensitivity=sensitivity,
    )

    input_payload = {
        "it_load": request.it_load.model_dump(mode="json"),
        "cooling_load": request.cooling_load.model_dump(mode="json"),
        "proposed_it_delta": request.proposed_it_delta,
        "proposed_cooling_delta": request.proposed_cooling_delta,
        "quality_eligible": request.quality_eligible,
        "quality_stale": request.quality_stale,
        "quality_flags": request.quality_flags,
        "uncertainty_flags": request.uncertainty_flags,
        "conflicting_inputs": request.conflicting_inputs,
    }
    config_payload = request.config.model_dump(mode="json")

    fingerprint = build_fingerprint(
        engine_version=base_decision.engine_version,
        config_version=base_decision.config_version,
        input_payload=input_payload,
        config_payload=config_payload,
        decision_timestamp=base_decision.decision_timestamp,
        full_bundle=evidence.model_dump(mode="json"),
    )

    return TransparentDecisionResult(
        status=base_decision.status.value,
        precedence=base_decision.precedence.value,
        engine_version=base_decision.engine_version,
        config_version=base_decision.config_version,
        decision_timestamp=base_decision.decision_timestamp,
        state_machine_decision=base_decision,
        uncertainty=uncertainty,
        sensitivity=sensitivity,
        evidence=evidence,
        explanation=explanation.model_dump(mode="json"),
        fingerprint=fingerprint,
    )
