# Copyright 2026 Lightibulb
"""Transparent, evidence-backed decision orchestrator.

Wraps the deterministic state machine with:
  1. PUE-based facility-power calculation (uses decision.calculations)
  2. Uncertainty assessment (data-quality vs physical-model)
  3. Sensitivity analysis (does any tolerance band flip the verdict?)
  4. Full evidence bundle assembly
  5. Structured explanation generation
  6. Reproducibility fingerprint

Invariants:
  - Facility power = IT load * PUE. The electrical constraint compares
    facility power (not raw IT load) to capacity.
  - Negative predicted loads are rejected (``invalid_proposal``), not clamped.
  - Missing measurements force ``quality_eligible=False`` (REVIEW).
  - Units are validated (kW for loads, dimensionless for PUE).
  - Freshness is computed from ``measurement_timestamp`` when a max-age is set.
"""

import datetime as dt
from enum import StrEnum
from typing import Any

import pydantic

from ecofac.decision.calculations import (
    ElectricalCapacity,
    PUEResult,
    PUESource,
    calculate_electrical,
)
from ecofac.decision.evidence import (
    DecisionEvidenceBundle,
    InputReference,
    build_evidence_bundle,
)
from ecofac.decision.explain import build_explanation
from ecofac.decision.limits import ConstraintConfig
from ecofac.decision.measurements import (
    MeasurementSource,
    RichMeasurement,
)
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
from ecofac.timeutil import parse_utc


class DecisionMode(StrEnum):
    """Whether the request is a hypothetical scenario or a trusted evaluation."""

    HYPOTHETICAL = "hypothetical"
    TRUSTED = "trusted"


DEFAULT_PUE_VALUE = 1.0
DEFAULT_PUE_UNIT = "dimensionless"
LOAD_UNIT_KW = "kW"


class TransparentDecisionRequest(pydantic.BaseModel):
    """Complete request for a transparent decision."""

    model_config = pydantic.ConfigDict(frozen=True)

    config: ConstraintConfig
    config_version: str
    it_load: RichMeasurement
    cooling_load: RichMeasurement
    pue: RichMeasurement | None = None
    proposed_it_delta: float = 0.0
    proposed_cooling_delta: float = 0.0
    quality_eligible: bool
    quality_stale: bool = False
    quality_flags: list[str] = pydantic.Field(default_factory=list)
    uncertainty_flags: list[str] = pydantic.Field(default_factory=list)
    conflicting_inputs: list[str] = pydantic.Field(default_factory=list)
    max_measurement_age_seconds: int | None = None
    mode: DecisionMode = DecisionMode.HYPOTHETICAL


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
        "facility_power_kw": "it_load_kw * pue_value",
        "predicted_it_load_kw": "current_it_load_kw + proposed_it_delta",
        "predicted_facility_power_kw": "predicted_it_load_kw * pue_value",
        "predicted_cooling_kw": "current_cooling_kw + proposed_cooling_delta",
        "permitted_kw": "capacity * (1 - reserve_pct)",
        "headroom_to_permitted": "permitted_kw - observed",
        "headroom_to_physical": "capacity - observed",
    }


def _resolve_pue(
    pue: RichMeasurement | None,
) -> tuple[float, str, MeasurementSource]:
    """Return (value, provenance_str, source_enum)."""
    if pue is None or pue.value is None:
        return DEFAULT_PUE_VALUE, PUESource.ASSUMED.value, MeasurementSource.ASSUMED
    pue.validate_unit(DEFAULT_PUE_UNIT)
    if pue.source == MeasurementSource.MEASURED:
        return pue.value, PUESource.MEASURED.value, pue.source
    if pue.source == MeasurementSource.CONFIGURED:
        return pue.value, PUESource.CONFIGURED.value, pue.source
    return pue.value, PUESource.ASSUMED.value, pue.source


def _compute_facility_power(
    it_kw: float,
    pue_value: float,
    capacity_kw: float,
    reserve_pct: float,
) -> float:
    """Compute facility power via calculate_electrical (facility = IT * PUE)."""
    if it_kw == 0.0:
        return 0.0
    pue_result = PUEResult(value=pue_value, source=PUESource.MEASURED)
    state = calculate_electrical(
        it_load_kw=it_kw,
        pue=pue_result,
        capacity=ElectricalCapacity(
            capacity_kw=max(capacity_kw, it_kw * pue_value + 1.0),
            safety_margin_pct=min(max(reserve_pct, 0.0), 0.99),
        ),
    )
    return state.facility_power_kw


def _validate_units(request: TransparentDecisionRequest) -> None:
    """Enforce canonical units: kW for loads, dimensionless for PUE."""
    if request.it_load.value is not None:
        request.it_load.validate_unit(LOAD_UNIT_KW)
    if request.cooling_load.value is not None:
        request.cooling_load.validate_unit(LOAD_UNIT_KW)


def _is_stale(
    measurement: RichMeasurement, max_age_seconds: int | None
) -> bool:
    """True if the measurement timestamp is older than max_age_seconds."""
    if max_age_seconds is None or measurement.measurement_timestamp is None:
        return False
    try:
        ts = parse_utc(measurement.measurement_timestamp)
    except (ValueError, TypeError):
        return True
    age = (dt.datetime.now(dt.UTC) - ts).total_seconds()
    return age > max_age_seconds


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
    pue_value: float,
    electrical_capacity: float,
    cooling_capacity: float,
) -> list[SensitivityResult]:
    """Run sensitivity for every measurement that has tolerance metadata."""
    results: list[SensitivityResult] = []

    it_band = it_load.band()
    if it_band is not None:
        low, high = it_band

        def _status_it(measured_value: float) -> str:
            delta = measured_value - current_it
            new_predicted_it = max(0.0, predicted_it + delta)
            new_facility = new_predicted_it * pue_value
            ctx = build_context(
                predicted_electrical=new_facility,
                predicted_cooling=predicted_cooling,
                current_electrical=measured_value * pue_value,
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
                    it_load.name, low, high, status_low, status_high,
                    baseline_status, changed=changed,
                ),
            )
        )

    cooling_band = cooling_load.band()
    if cooling_band is not None:
        low, high = cooling_band

        def _status_cooling(measured_value: float) -> str:
            delta = measured_value - current_cooling
            new_predicted_cooling = max(0.0, predicted_cooling + delta)
            ctx = build_context(
                predicted_electrical=predicted_it * pue_value,
                predicted_cooling=new_predicted_cooling,
                current_electrical=current_it * pue_value,
                current_cooling=measured_value,
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
                    cooling_load.name, low, high, status_low, status_high,
                    baseline_status, changed=changed,
                ),
            )
        )

    _ = electrical_capacity
    _ = cooling_capacity
    return results


def build_input_payload(request: TransparentDecisionRequest) -> dict:
    """Extract the fingerprintable input payload from a request."""
    return {
        "it_load": request.it_load.model_dump(mode="json"),
        "cooling_load": request.cooling_load.model_dump(mode="json"),
        "pue": request.pue.model_dump(mode="json") if request.pue else None,
        "proposed_it_delta": request.proposed_it_delta,
        "proposed_cooling_delta": request.proposed_cooling_delta,
        "quality_eligible": request.quality_eligible,
        "quality_stale": request.quality_stale,
        "quality_flags": request.quality_flags,
        "uncertainty_flags": request.uncertainty_flags,
        "conflicting_inputs": request.conflicting_inputs,
        "mode": request.mode.value,
        "max_measurement_age_seconds": request.max_measurement_age_seconds,
    }


def build_config_payload(request: TransparentDecisionRequest) -> dict:
    """Extract the fingerprintable config payload from a request."""
    return request.config.model_dump(mode="json")


def decide_transparently(
    request: TransparentDecisionRequest,
) -> TransparentDecisionResult:
    """Run a transparent decision."""
    _validate_units(request)

    current_it, it_present = _extract(request.it_load)
    current_cooling, cooling_present = _extract(request.cooling_load)
    pue_value, pue_provenance, _ = _resolve_pue(request.pue)

    effective_it_delta = request.proposed_it_delta if it_present else 0.0
    effective_cooling_delta = (
        request.proposed_cooling_delta if cooling_present else 0.0
    )

    predicted_it = current_it + effective_it_delta
    predicted_cooling = current_cooling + effective_cooling_delta

    current_facility = current_it * pue_value
    predicted_facility = max(0.0, predicted_it) * pue_value

    invalid_reasons: list[str] = []
    if it_present and predicted_it < 0:
        invalid_reasons.append(
            f"Proposed IT delta {effective_it_delta} kW would drive "
            f"predicted IT load to {predicted_it} kW (negative)"
        )
    if cooling_present and predicted_cooling < 0:
        invalid_reasons.append(
            f"Proposed cooling delta {effective_cooling_delta} kW would "
            f"drive predicted cooling load to {predicted_cooling} kW (negative)"
        )

    effective_quality_flags = list(request.quality_flags)
    if not it_present:
        effective_quality_flags.append("missing_it_load")
    if not cooling_present:
        effective_quality_flags.append("missing_cooling_load")

    stale_by_age: list[str] = []
    if _is_stale(request.it_load, request.max_measurement_age_seconds):
        stale_by_age.append(request.it_load.name)
    if _is_stale(request.cooling_load, request.max_measurement_age_seconds):
        stale_by_age.append(request.cooling_load.name)
    effective_stale = request.quality_stale or bool(stale_by_age)

    effective_quality_eligible = (
        request.quality_eligible and it_present and cooling_present
    )

    measurements = [request.it_load, request.cooling_load]
    uncertainty = assess_uncertainty(
        measurements,
        quality_flags=effective_quality_flags,
        quality_stale=effective_stale,
        stale_measurement_names=stale_by_age,
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
            quality_stale=effective_stale,
            quality_flags=effective_quality_flags,
            uncertainty_flags=list(request.uncertainty_flags),
            conflicting_inputs=list(request.conflicting_inputs),
            invalid_proposal_reasons=invalid_reasons,
            corrective_action_mode=False,
        )

    base_decision = evaluate_decision(
        _build_context(
            predicted_electrical=predicted_facility,
            predicted_cooling=predicted_cooling,
            current_electrical=current_facility,
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
        pue_value=pue_value,
        electrical_capacity=request.config.electrical.capacity,
        cooling_capacity=request.config.cooling.capacity,
    )

    evidence = build_evidence_bundle(
        input_references=[_to_input_reference(m) for m in measurements],
        constraints_snapshot=_constraint_snapshot(request.config),
        calculation_inputs={
            "current_it_load_kw": current_it,
            "current_facility_power_kw": current_facility,
            "current_cooling_load_kw": current_cooling,
            "pue": pue_value,
            "pue_provenance": pue_provenance,
            "proposed_it_delta_kw": effective_it_delta,
            "proposed_cooling_delta_kw": effective_cooling_delta,
            "predicted_it_load_kw": predicted_it,
            "predicted_facility_power_kw": predicted_facility,
            "predicted_cooling_load_kw": predicted_cooling,
            "invalid_proposal_reasons": list(invalid_reasons),
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
        final_reason="; ".join(base_decision.reasons) or "no reasons recorded",
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

    input_payload = build_input_payload(request)
    config_payload = build_config_payload(request)
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
