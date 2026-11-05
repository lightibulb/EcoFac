# Copyright 2026 Lightibulb
"""Evidence bundle assembly for transparent decisions.

Every decision is accompanied by a structured bundle containing:
  - References back to the original measurement inputs (with their ids)
  - The constraint configuration that was in force
  - Calculation inputs, outputs, formulas, and assumptions
  - Passed, warning, and failed constraints (kept separate for clarity)
  - The full uncertainty report and sensitivity analysis
  - The final status, exact reason, and engine/config versions
"""

import pydantic

from ecofac.decision.limits import ConstraintSeverity, LimitEvaluation
from ecofac.decision.uncertainty import SensitivityResult, UncertaintyReport


class InputReference(pydantic.BaseModel):
    """A reference to one original measurement value."""

    name: str
    value: float | None
    unit: str
    source: str
    reference_id: str | None
    timestamp: str | None
    # Original stored reading when ``value`` was converted (kWh over an
    # interval -> average kW). ``None`` when no conversion took place.
    raw_value: float | None = None
    raw_unit: str | None = None
    interval_minutes: int | None = None


class ConstraintRecord(pydantic.BaseModel):
    """Recorded outcome of one constraint evaluation."""

    name: str
    status: str
    observed: float | None
    limit: float
    margin: float | None
    reason: str


class DecisionEvidenceBundle(pydantic.BaseModel):
    """Complete, structured evidence supporting one decision."""

    input_references: list[InputReference]
    constraints_snapshot: dict
    calculation_inputs: dict
    calculation_outputs: dict
    formulas: dict[str, str]
    assumptions: list[str]
    passed_constraints: list[ConstraintRecord]
    warning_constraints: list[ConstraintRecord]
    failed_constraints: list[ConstraintRecord]
    uncertainty_report: UncertaintyReport
    sensitivity: list[SensitivityResult]
    final_status: str
    final_reason: str
    engine_version: str
    config_version: str
    decision_timestamp: str


def _to_constraint_record(ev: LimitEvaluation) -> ConstraintRecord:
    return ConstraintRecord(
        name=ev.name,
        status=ev.severity.value,
        observed=ev.observed,
        limit=ev.permitted,
        margin=ev.headroom_to_permitted,
        reason=ev.reason,
    )


def build_evidence_bundle(
    *,
    input_references: list[InputReference],
    constraints_snapshot: dict,
    calculation_inputs: dict,
    calculation_outputs: dict,
    formulas: dict[str, str],
    assumptions: list[str],
    evaluated_constraints: list[LimitEvaluation],
    uncertainty_report: UncertaintyReport,
    sensitivity: list[SensitivityResult],
    final_status: str,
    final_reason: str,
    engine_version: str,
    config_version: str,
    decision_timestamp: str,
) -> DecisionEvidenceBundle:
    """Assemble a complete evidence bundle from already-computed parts."""
    passed: list[ConstraintRecord] = []
    warnings: list[ConstraintRecord] = []
    failed: list[ConstraintRecord] = []
    for ev in evaluated_constraints:
        record = _to_constraint_record(ev)
        if ev.severity == ConstraintSeverity.OK:
            passed.append(record)
        elif ev.severity == ConstraintSeverity.WARNING:
            warnings.append(record)
        else:
            failed.append(record)

    return DecisionEvidenceBundle(
        input_references=input_references,
        constraints_snapshot=constraints_snapshot,
        calculation_inputs=calculation_inputs,
        calculation_outputs=calculation_outputs,
        formulas=formulas,
        assumptions=assumptions,
        passed_constraints=passed,
        warning_constraints=warnings,
        failed_constraints=failed,
        uncertainty_report=uncertainty_report,
        sensitivity=sensitivity,
        final_status=final_status,
        final_reason=final_reason,
        engine_version=engine_version,
        config_version=config_version,
        decision_timestamp=decision_timestamp,
    )
