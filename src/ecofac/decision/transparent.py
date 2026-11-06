# Copyright 2026 Lightibulb
"""Transparent, evidence-backed decision orchestrator.

Wraps the deterministic state machine with:
  1. PUE-based facility-power calculation (canonical formulas live in
     ``decision.calculations``)
  2. Uncertainty assessment (data-quality vs physical-model), including PUE
  3. Sensitivity analysis over IT load, cooling load **and PUE**
  4. Full evidence bundle assembly
  5. Structured explanation generation
  6. Reproducibility fingerprint

Invariants:
  - Facility power = IT load * PUE (``calculations.facility_power_kw``). The
    electrical constraint compares facility power (not raw IT load) to capacity.
  - PUE is finite and >= 1.0. A missing PUE is an explicit, visible assumption
    (``pue_trust_basis`` = ``assumed_default``), never a silent 1.0.
  - Negative predicted loads are rejected (``invalid_proposal``), not clamped.
  - Missing measurements force ``quality_eligible=False`` (REVIEW).
  - Units are validated (kW for loads, dimensionless for PUE).
  - Cooling is a directly measured/stated load plus a proposed delta; it is
    never silently replaced by a modelled demand (see ``calculations``).
  - Freshness is evaluated against an explicit ``evaluation_time`` that is
    recorded and stored with the request, so replays are deterministic and
    never depend on the wall clock.

Operational (``DecisionMode.TRUSTED``) behaviour: the engine derives its own
uncertainty flags and never lets an UNKNOWN / assumed / unverified critical
input silently produce APPROVE. Exploratory (``HYPOTHETICAL``) behaviour is
unchanged: the same facts are recorded as advisories in the evidence but do not
by themselves change the verdict.
"""

import dataclasses
import datetime as dt
from enum import StrEnum
from typing import Any

import pydantic

from ecofac.decision.calculations import (
    COOLING_BASIS_MEASURED,
    FORMULAS,
    PUESource,
    facility_power_kw,
    predicted_load_kw,
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
from ecofac.decision.numeric import MIN_PUE, validate_pue_value
from ecofac.decision.reproducibility import (
    ReproductionFingerprint,
    build_fingerprint,
)
from ecofac.decision.state_machine import (
    DecisionContext,
    FinalDecision,
    evaluate_decision,
)
from ecofac.decision.trust import (
    FUTURE_TOLERANCE_SECONDS,
    TrustError,
    parse_aware_utc,
)
from ecofac.decision.uncertainty import (
    MODEL_LIMITATIONS,
    ModelUncertaintyLevel,
    SensitivityResult,
    UncertaintyReport,
    assess_uncertainty,
)
from ecofac.timeutil import now_z, to_z


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
    proposed_it_delta: float = pydantic.Field(default=0.0, allow_inf_nan=False)
    proposed_cooling_delta: float = pydantic.Field(default=0.0, allow_inf_nan=False)
    quality_eligible: bool
    quality_stale: bool = False
    quality_flags: list[str] = pydantic.Field(default_factory=list)
    uncertainty_flags: list[str] = pydantic.Field(default_factory=list)
    conflicting_inputs: list[str] = pydantic.Field(default_factory=list)
    max_measurement_age_seconds: int | None = pydantic.Field(default=None, ge=0)
    mode: DecisionMode = DecisionMode.HYPOTHETICAL
    # The instant freshness is judged against. Stamped by the server for
    # operational decisions and stored with the request so a replay judges
    # freshness at the ORIGINAL time, never at "now".
    evaluation_time: str | None = None
    # True only when the server itself resolved every operational input
    # (set by the decisions route after trusted resolution). TRUSTED mode
    # without it is treated as unverified and cannot APPROVE.
    inputs_resolved_by_server: bool = False
    # Server-detected caveats about trusted inputs (e.g. unclassified meter role).
    trust_caveats: list[str] = pydantic.Field(default_factory=list)

    @pydantic.field_validator("evaluation_time")
    @classmethod
    def _evaluation_time_is_aware(cls, value: str | None) -> str | None:
        if value is not None:
            parse_aware_utc(value, "evaluation_time")
        return value


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
    # The instant used to judge freshness (always recorded; ``None`` only for
    # results stored before this field existed).
    evaluation_time: str | None = None


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
        raw_value=m.raw_value,
        raw_unit=m.raw_unit,
        interval_minutes=m.interval_minutes,
    )


def _constraint_snapshot(config: ConstraintConfig) -> dict:
    return config.model_dump(mode="json")


def _formulas() -> dict[str, str]:
    """Formulas recorded in evidence: the canonical set from ``calculations``."""
    return dict(FORMULAS)


# How the PUE used by a decision was obtained. Recorded in evidence.
PUE_TRUSTED = "trusted"  # measured, with a server-resolved reference
PUE_UNVERIFIED_MEASURED = "unverified_measured"  # claimed measured, no trusted reference
PUE_CONFIGURED = "configured"  # explicit configured value
PUE_ASSUMED = "assumed"  # supplied but estimated/assumed
PUE_ASSUMED_DEFAULT = "assumed_default"  # absent; library default 1.0 applied

_PUE_ASSUMED_BASES = (PUE_ASSUMED, PUE_ASSUMED_DEFAULT)


@dataclasses.dataclass(frozen=True)
class _PueInfo:
    value: float
    provenance: str
    trust_basis: str
    measurement: RichMeasurement
    is_default: bool

    @property
    def assumed(self) -> bool:
        return self.trust_basis in _PUE_ASSUMED_BASES


def _resolve_pue(pue: RichMeasurement | None) -> _PueInfo:
    """Resolve and validate PUE; distinguish trusted / configured / assumed.

    A missing PUE falls back to ``DEFAULT_PUE_VALUE`` but the fallback is an
    explicit, recorded assumption (``assumed_default``), never silent. A
    supplied PUE must be finite and ``>= 1.0`` (standard definition).
    """
    if pue is None or pue.value is None or pue.source == MeasurementSource.MISSING:
        synthetic = RichMeasurement(
            name="pue",
            value=DEFAULT_PUE_VALUE,
            unit=DEFAULT_PUE_UNIT,
            source=MeasurementSource.ASSUMED,
        )
        return _PueInfo(
            DEFAULT_PUE_VALUE,
            PUESource.ASSUMED.value,
            PUE_ASSUMED_DEFAULT,
            synthetic,
            is_default=True,
        )
    pue.validate_unit(DEFAULT_PUE_UNIT)
    value = validate_pue_value(pue.value)
    if pue.source == MeasurementSource.MEASURED:
        basis = PUE_TRUSTED if pue.reference_id else PUE_UNVERIFIED_MEASURED
        return _PueInfo(value, PUESource.MEASURED.value, basis, pue, is_default=False)
    if pue.source == MeasurementSource.CONFIGURED:
        return _PueInfo(value, PUESource.CONFIGURED.value, PUE_CONFIGURED, pue, is_default=False)
    return _PueInfo(value, PUESource.ASSUMED.value, PUE_ASSUMED, pue, is_default=False)


def _validate_units(request: TransparentDecisionRequest) -> None:
    """Enforce canonical units: kW for loads, dimensionless for PUE."""
    if request.it_load.value is not None:
        request.it_load.validate_unit(LOAD_UNIT_KW)
    if request.cooling_load.value is not None:
        request.cooling_load.validate_unit(LOAD_UNIT_KW)


def _timestamp_state(
    measurement: RichMeasurement,
    max_age_seconds: int | None,
    evaluation_time: dt.datetime,
) -> str | None:
    """Classify a measurement timestamp against ``evaluation_time``.

    Returns ``None`` (fine / no timestamp), ``"stale"``, ``"future"`` or
    ``"invalid"`` (unparseable or naive). Never reads the wall clock.
    """
    if measurement.measurement_timestamp is None:
        return None
    try:
        ts = parse_aware_utc(measurement.measurement_timestamp)
    except TrustError:
        return "invalid"
    age = (evaluation_time - ts).total_seconds()
    if age < -FUTURE_TOLERANCE_SECONDS:
        return "future"
    if max_age_seconds is not None and age > max_age_seconds:
        return "stale"
    return None


def _is_stale(
    measurement: RichMeasurement,
    max_age_seconds: int | None,
    evaluation_time: dt.datetime,
) -> bool:
    """True if older than ``max_age_seconds`` at ``evaluation_time``.

    ``evaluation_time`` is required: freshness is always judged against an
    explicit instant so historical decisions replay deterministically.
    """
    state = _timestamp_state(measurement, max_age_seconds, evaluation_time)
    if state == "stale":
        return True
    return state == "invalid" and max_age_seconds is not None


def _sensitivity_reason(
    name: str,
    low: float,
    high: float,
    status_low: str,
    status_high: str,
    baseline_status: str,
    *,
    changed: bool,
    clamped: bool = False,
) -> str:
    suffix = " (lower bound clamped at physical floor)" if clamped else ""
    if changed:
        return (
            f"'{name}' tolerance band [{low:.4f}, {high:.4f}] contains "
            f"values that produce status other than {baseline_status} "
            f"(saw {status_low} at lower, {status_high} at upper){suffix}"
        )
    return (
        f"'{name}' tolerance band [{low:.4f}, {high:.4f}] preserves "
        f"baseline status {baseline_status}{suffix}"
    )


def _band_result(
    measurement: RichMeasurement,
    band: tuple[float, float],
    status_of: Any,
    baseline_status: str,
    *,
    clamped: bool,
) -> SensitivityResult:
    low, high = band
    status_low = status_of(low)
    status_high = status_of(high)
    changed = status_low != baseline_status or status_high != baseline_status
    return SensitivityResult(
        measurement_name=measurement.name,
        baseline_status=baseline_status,
        lower_bound=round(low, 4),
        upper_bound=round(high, 4),
        status_at_lower=status_low,
        status_at_upper=status_high,
        could_change_verdict=changed,
        reason=_sensitivity_reason(
            measurement.name,
            low,
            high,
            status_low,
            status_high,
            baseline_status,
            changed=changed,
            clamped=clamped,
        ),
    )


def _compute_sensitivity(
    *,
    build_context: Any,
    baseline_status: str,
    it_load: RichMeasurement,
    cooling_load: RichMeasurement,
    pue: RichMeasurement | None,
    current_it: float,
    current_cooling: float,
    predicted_it: float,
    predicted_cooling: float,
    pue_value: float,
) -> list[SensitivityResult]:
    """Run sensitivity for every measurement (incl. PUE) with tolerance metadata."""
    results: list[SensitivityResult] = []

    it_band = it_load.band()
    if it_band is not None:

        def _status_it(measured_value: float) -> str:
            delta = measured_value - current_it
            new_predicted_it = max(0.0, predicted_it + delta)
            ctx = build_context(
                predicted_electrical=facility_power_kw(new_predicted_it, pue_value),
                predicted_cooling=predicted_cooling,
                current_electrical=facility_power_kw(measured_value, pue_value),
                current_cooling=current_cooling,
            )
            return evaluate_decision(ctx).status.value

        results.append(
            _band_result(
                it_load,
                it_band,
                _status_it,
                baseline_status,
                clamped=it_load.band_clamped(),
            )
        )

    cooling_band = cooling_load.band()
    if cooling_band is not None:

        def _status_cooling(measured_value: float) -> str:
            delta = measured_value - current_cooling
            new_predicted_cooling = max(0.0, predicted_cooling + delta)
            ctx = build_context(
                predicted_electrical=facility_power_kw(max(0.0, predicted_it), pue_value),
                predicted_cooling=new_predicted_cooling,
                current_electrical=facility_power_kw(current_it, pue_value),
                current_cooling=measured_value,
            )
            return evaluate_decision(ctx).status.value

        results.append(
            _band_result(
                cooling_load,
                cooling_band,
                _status_cooling,
                baseline_status,
                clamped=cooling_load.band_clamped(),
            )
        )

    pue_band = pue.band(floor=MIN_PUE) if pue is not None else None
    if pue is not None and pue_band is not None:

        def _status_pue(candidate_pue: float) -> str:
            ctx = build_context(
                predicted_electrical=facility_power_kw(max(0.0, predicted_it), candidate_pue),
                predicted_cooling=predicted_cooling,
                current_electrical=facility_power_kw(current_it, candidate_pue),
                current_cooling=current_cooling,
            )
            return evaluate_decision(ctx).status.value

        results.append(
            _band_result(
                pue,
                pue_band,
                _status_pue,
                baseline_status,
                clamped=pue.band_clamped(floor=MIN_PUE),
            )
        )

    return results


_PAYLOAD_OPTIONAL_MEASUREMENT_KEYS = ("raw_value", "raw_unit", "interval_minutes")


def _dump_measurement(measurement: RichMeasurement) -> dict:
    """Dump a measurement for fingerprinting.

    The optional raw-reading keys are omitted when unset so fingerprints of
    decisions stored before those fields existed remain verifiable.
    """
    data = measurement.model_dump(mode="json")
    for key in _PAYLOAD_OPTIONAL_MEASUREMENT_KEYS:
        if data.get(key) is None:
            data.pop(key, None)
    return data


def build_input_payload(request: TransparentDecisionRequest) -> dict:
    """Extract the fingerprintable input payload from a request.

    Fields introduced by the remediation (``evaluation_time``,
    ``inputs_resolved_by_server``, ``trust_caveats``) are only included when
    set, so legacy stored decisions keep their original fingerprints.
    """
    payload: dict = {
        "it_load": _dump_measurement(request.it_load),
        "cooling_load": _dump_measurement(request.cooling_load),
        "pue": _dump_measurement(request.pue) if request.pue else None,
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
    if request.evaluation_time is not None:
        payload["evaluation_time"] = request.evaluation_time
    if request.inputs_resolved_by_server:
        payload["inputs_resolved_by_server"] = True
    if request.trust_caveats:
        payload["trust_caveats"] = list(request.trust_caveats)
    return payload


def build_config_payload(request: TransparentDecisionRequest) -> dict:
    """Extract the fingerprintable config payload from a request."""
    return request.config.model_dump(mode="json")


def is_time_dependent(request: TransparentDecisionRequest) -> bool:
    """True only if the verdict can change as wall-clock time advances.

    Freshness (``max_measurement_age_seconds``) and trusted-mode server
    resolution are the only paths that consult ``evaluation_time`` in a way
    that varies with the clock. A merely present historical timestamp does
    not — the future check cannot trigger for it — so pinning
    ``evaluation_time`` for such requests only breaks reproducibility.
    """
    return request.mode == DecisionMode.TRUSTED or request.max_measurement_age_seconds is not None


def pinned_request(
    request: TransparentDecisionRequest, result: TransparentDecisionResult
) -> TransparentDecisionRequest:
    """Return the request to PERSIST: evaluation time pinned when it matters.

    ``decide_transparently`` computes its fingerprint from exactly this
    request, so storing it makes a later replay judge freshness at the
    original instant.
    """
    if request.evaluation_time is None and is_time_dependent(request):
        return request.model_copy(update={"evaluation_time": result.evaluation_time})
    return request


def _derive_uncertainty_flags(
    *,
    request: TransparentDecisionRequest,
    pue_info: _PueInfo,
    uncertainty: UncertaintyReport,
    it_present: bool,
    cooling_present: bool,
    sensitivity: list[SensitivityResult],
) -> list[str]:
    """Derive operational uncertainty flags from actual metadata.

    Nothing here reads caller-supplied ``uncertainty_flags``; every flag is a
    consequence of the measurements, their provenance, tolerances, the PUE
    basis and the sensitivity analysis.
    """
    flags: list[str] = []
    if not request.inputs_resolved_by_server:
        flags.append(
            "inputs_not_server_resolved: operational inputs were not resolved "
            "from trusted references by the server"
        )
    flags.extend(f"unverified_trust_input: {c}" for c in request.trust_caveats)

    loads = (
        (request.it_load, it_present),
        (request.cooling_load, cooling_present),
    )
    for measurement, present in loads:
        if not present:
            continue  # already forces REVIEW via missing_* quality flags
        if measurement.source != MeasurementSource.MEASURED:
            flags.append(
                f"unverified_provenance: '{measurement.name}' source is "
                f"{measurement.source.value}, not MEASURED"
            )
        if measurement.band() is None:
            flags.append(
                f"missing_tolerance: '{measurement.name}' has no tolerance "
                "metadata; its uncertainty cannot be bounded"
            )
        if measurement.measurement_timestamp is None:
            flags.append(
                f"missing_timestamp: '{measurement.name}' has no measurement "
                "timestamp, so freshness cannot be established"
            )
    if request.max_measurement_age_seconds is None:
        flags.append("freshness_policy_missing: no max measurement age is configured")
    if uncertainty.model_uncertainty_level == ModelUncertaintyLevel.UNKNOWN:
        flags.append(
            "unknown_model_uncertainty: no tolerance metadata is available for the measurements"
        )
    if pue_info.trust_basis in _PUE_ASSUMED_BASES:
        supplied_source = request.pue.source.value if request.pue else "unknown"
        what = (
            f"missing; default {DEFAULT_PUE_VALUE} applied"
            if pue_info.is_default
            else f"supplied with {supplied_source} provenance"
        )
        flags.append(
            f"pue_assumed: PUE is {what}; an assumed PUE cannot support an operational APPROVE"
        )
    elif pue_info.trust_basis == PUE_UNVERIFIED_MEASURED:
        flags.append("pue_unverified: PUE is labelled MEASURED but carries no trusted reference")
    flags.extend(
        f"sensitivity_changes_verdict: {s.reason}" for s in sensitivity if s.could_change_verdict
    )
    return flags


def decide_transparently(
    request: TransparentDecisionRequest,
) -> TransparentDecisionResult:
    """Run a transparent decision."""
    _validate_units(request)

    operational = request.mode == DecisionMode.TRUSTED
    evaluation_time = to_z(parse_aware_utc(request.evaluation_time or now_z(), "evaluation_time"))
    eval_dt = parse_aware_utc(evaluation_time)

    current_it, it_present = _extract(request.it_load)
    current_cooling, cooling_present = _extract(request.cooling_load)
    pue_info = _resolve_pue(request.pue)
    pue_value = pue_info.value

    effective_it_delta = request.proposed_it_delta if it_present else 0.0
    effective_cooling_delta = request.proposed_cooling_delta if cooling_present else 0.0

    predicted_it = predicted_load_kw(current_it, effective_it_delta)
    predicted_cooling = predicted_load_kw(current_cooling, effective_cooling_delta)

    current_facility = facility_power_kw(current_it, pue_value)
    predicted_facility = facility_power_kw(max(0.0, predicted_it), pue_value)

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

    # Freshness is judged against the recorded evaluation time, never "now".
    stale_by_age: list[str] = []
    future_names: list[str] = []
    for m in (request.it_load, request.cooling_load):
        state = _timestamp_state(m, request.max_measurement_age_seconds, eval_dt)
        if _is_stale(m, request.max_measurement_age_seconds, eval_dt):
            stale_by_age.append(m.name)
        if state == "future":
            future_names.append(m.name)
            effective_quality_flags.append(f"future_timestamp:{m.name}")
    # A caller-supplied ``quality_stale`` can only ADD staleness, never hide it.
    effective_stale = request.quality_stale or bool(stale_by_age)

    effective_quality_eligible = (
        request.quality_eligible and it_present and cooling_present and not future_names
    )

    measurements = [request.it_load, request.cooling_load, pue_info.measurement]
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
        extra_flags: tuple[str, ...] = (),
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
            uncertainty_flags=[*request.uncertainty_flags, *extra_flags],
            conflicting_inputs=list(request.conflicting_inputs),
            invalid_proposal_reasons=invalid_reasons,
            corrective_action_mode=False,
        )

    # Pass 1: physical / quality verdict with only the caller's own flags.
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
        pue=pue_info.measurement if not pue_info.is_default else None,
        current_it=current_it,
        current_cooling=current_cooling,
        predicted_it=predicted_it,
        predicted_cooling=predicted_cooling,
        pue_value=pue_value,
    )

    # Pass 2 (operational only): derived uncertainty flags. In exploratory
    # mode the same facts are recorded as advisories but do not change the
    # verdict, preserving hypothetical-scenario behaviour.
    derived_flags = _derive_uncertainty_flags(
        request=request,
        pue_info=pue_info,
        uncertainty=uncertainty,
        it_present=it_present,
        cooling_present=cooling_present,
        sensitivity=sensitivity,
    )
    applied_flags = derived_flags if operational else []
    decision = base_decision
    if applied_flags:
        decision = evaluate_decision(
            _build_context(
                predicted_electrical=predicted_facility,
                predicted_cooling=predicted_cooling,
                current_electrical=current_facility,
                current_cooling=current_cooling,
                extra_flags=tuple(applied_flags),
            )
        )

    pue_assumption = {
        PUE_ASSUMED_DEFAULT: (
            f"PUE was not supplied; the default {DEFAULT_PUE_VALUE} (the "
            "physical minimum, i.e. the most optimistic value) was assumed"
        ),
        PUE_ASSUMED: "PUE is an assumed/estimated value, not measured",
        PUE_CONFIGURED: "PUE is an explicitly configured value, not measured",
        PUE_UNVERIFIED_MEASURED: ("PUE is labelled MEASURED but has no trusted reference"),
        PUE_TRUSTED: "PUE comes from a trusted measurement reference",
    }[pue_info.trust_basis]
    advisories = [] if operational else [f"advisory (not applied): {f}" for f in derived_flags]

    cooling_basis = (
        COOLING_BASIS_MEASURED
        if request.cooling_load.source == MeasurementSource.MEASURED
        else f"stated_cooling_load_{request.cooling_load.source.value.lower()}"
    )

    input_references = [_to_input_reference(m) for m in (request.it_load, request.cooling_load)]
    if not pue_info.is_default:
        input_references.append(_to_input_reference(pue_info.measurement))

    evidence = build_evidence_bundle(
        input_references=input_references,
        constraints_snapshot=_constraint_snapshot(request.config),
        calculation_inputs={
            "current_it_load_kw": current_it,
            "current_facility_power_kw": current_facility,
            "current_cooling_load_kw": current_cooling,
            "pue": pue_value,
            "pue_provenance": pue_info.provenance,
            "pue_trust_basis": pue_info.trust_basis,
            "pue_assumed": pue_info.assumed,
            "proposed_it_delta_kw": effective_it_delta,
            "proposed_cooling_delta_kw": effective_cooling_delta,
            "predicted_it_load_kw": predicted_it,
            "predicted_facility_power_kw": predicted_facility,
            "predicted_cooling_load_kw": predicted_cooling,
            "cooling_load_basis": cooling_basis,
            "cooling_demand_modelled": False,
            "invalid_proposal_reasons": list(invalid_reasons),
            "mode": request.mode.value,
            "evaluation_time": evaluation_time,
            "max_measurement_age_seconds": request.max_measurement_age_seconds,
            "stale_measurements": list(stale_by_age),
            "future_timestamp_measurements": list(future_names),
            "derived_uncertainty_flags": list(derived_flags),
            "applied_uncertainty_flags": list(applied_flags),
        },
        calculation_outputs={
            "status": decision.status.value,
            "precedence": decision.precedence.value,
            "physical_status_before_uncertainty": base_decision.status.value,
            "decision_reasons": list(decision.reasons),
        },
        formulas=_formulas(),
        assumptions=[
            *decision.reasons,
            pue_assumption,
            *advisories,
            *MODEL_LIMITATIONS,
        ],
        evaluated_constraints=decision.evaluated_constraints,
        uncertainty_report=uncertainty,
        sensitivity=sensitivity,
        final_status=decision.status.value,
        final_reason="; ".join(decision.reasons) or "no reasons recorded",
        engine_version=decision.engine_version,
        config_version=decision.config_version,
        decision_timestamp=decision.decision_timestamp,
    )

    explanation = build_explanation(
        status=decision.status.value,
        precedence=decision.precedence.value,
        evaluations=decision.evaluated_constraints,
        uncertainty=uncertainty,
        sensitivity=sensitivity,
    )

    fingerprint_request = request
    if request.evaluation_time is None and is_time_dependent(request):
        fingerprint_request = request.model_copy(update={"evaluation_time": evaluation_time})
    fingerprint = build_fingerprint(
        engine_version=decision.engine_version,
        config_version=decision.config_version,
        input_payload=build_input_payload(fingerprint_request),
        config_payload=build_config_payload(request),
        decision_timestamp=decision.decision_timestamp,
        full_bundle=evidence.model_dump(mode="json"),
    )

    return TransparentDecisionResult(
        status=decision.status.value,
        precedence=decision.precedence.value,
        engine_version=decision.engine_version,
        config_version=decision.config_version,
        decision_timestamp=decision.decision_timestamp,
        state_machine_decision=decision,
        uncertainty=uncertainty,
        sensitivity=sensitivity,
        evidence=evidence,
        explanation=explanation.model_dump(mode="json"),
        fingerprint=fingerprint,
        evaluation_time=evaluation_time,
    )
