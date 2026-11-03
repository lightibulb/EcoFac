# Copyright 2026 Lightibulb
"""Structured, human-readable decision explanations.

The explanation identifies:
  - The limiting constraint (smallest remaining headroom)
  - The amount of remaining headroom relative to the permitted limit
  - Which constraints passed, warned, or failed
  - Documented uncertainty notes
  - Concrete information that could change a REVIEW decision

No wording here claims scientific calibration.
"""

import pydantic

from ecofac.decision.limits import ConstraintSeverity, LimitEvaluation
from ecofac.decision.uncertainty import SensitivityResult, UncertaintyReport


class DecisionExplanation(pydantic.BaseModel):
    """Structured human-readable explanation."""

    summary: str
    limiting_constraint: str | None
    headroom_summary: str | None
    passed: list[str]
    failed: list[str]
    warnings: list[str]
    uncertainty_notes: list[str]
    what_would_change_decision: list[str]


def _limiting_constraint(
    evaluations: list[LimitEvaluation],
) -> LimitEvaluation | None:
    """Return the evaluation with the smallest headroom, or None."""
    present = [e for e in evaluations if e.headroom_to_permitted is not None]
    if not present:
        return None
    return min(present, key=lambda e: e.headroom_to_permitted)


def _review_hints(
    precedence: str,
    uncertainty: UncertaintyReport,
    sensitivity: list[SensitivityResult],
) -> list[str]:
    """Return concrete information that could change a REVIEW decision."""
    hints: list[str] = []
    if precedence == "data_quality_ineligible":
        if uncertainty.missing_measurements:
            hints.append(
                "Provide values for: "
                + ", ".join(uncertainty.missing_measurements)
            )
        if uncertainty.stale_measurements:
            hints.append(
                "Provide fresh readings for: "
                + ", ".join(uncertainty.stale_measurements)
            )
        if uncertainty.low_quality_measurements:
            hints.append(
                "Resolve quality flags for: "
                + ", ".join(uncertainty.low_quality_measurements)
            )
    elif precedence == "near_threshold":
        hints.append(
            "Free capacity headroom by reducing load, or supply measured "
            "tolerances that prove the true value is below the warning band."
        )
    elif precedence == "uncertainty":
        hints.append("Replace assumed/estimated parameters with MEASURED values.")
    elif precedence == "conflicting_inputs":
        hints.append("Reconcile conflicting inputs.")
    hints.extend(
        f"Measurement '{s.measurement_name}' tolerance could flip the "
        f"verdict: {s.reason}"
        for s in sensitivity
        if s.could_change_verdict
    )
    return hints


def build_explanation(
    *,
    status: str,
    precedence: str,
    evaluations: list[LimitEvaluation],
    uncertainty: UncertaintyReport,
    sensitivity: list[SensitivityResult],
) -> DecisionExplanation:
    """Assemble a structured, human-readable explanation."""
    limiting = _limiting_constraint(evaluations)
    if limiting is not None:
        limiting_name = limiting.name
        headroom = (
            f"{limiting.headroom_to_permitted:.4f} "
            f"(permitted {limiting.permitted:.4f})"
        )
    else:
        limiting_name = None
        headroom = None

    passed = [e.reason for e in evaluations if e.severity == ConstraintSeverity.OK]
    warnings = [
        e.reason for e in evaluations if e.severity == ConstraintSeverity.WARNING
    ]
    failed = [
        e.reason
        for e in evaluations
        if e.severity
        in (
            ConstraintSeverity.RESERVE_VIOLATION,
            ConstraintSeverity.PHYSICAL_VIOLATION,
        )
    ]

    uncertainty_notes: list[str] = []
    if uncertainty.data_quality_level != "OK":
        uncertainty_notes.append(
            f"data quality level: {uncertainty.data_quality_level}"
        )
    if uncertainty.model_uncertainty_level not in ("LOW", "UNKNOWN"):
        uncertainty_notes.append(
            f"model uncertainty level: {uncertainty.model_uncertainty_level}"
        )
    uncertainty_notes.extend(
        f"sensitivity: {s.measurement_name} tolerance could flip verdict"
        for s in sensitivity
        if s.could_change_verdict
    )

    summary = (
        f"Decision: {status} (precedence: {precedence}). "
        f"{len(passed)} passed, {len(warnings)} warning, {len(failed)} failed. "
        f"Data quality: {uncertainty.data_quality_level}. "
        f"Model uncertainty: {uncertainty.model_uncertainty_level}."
    )

    if status == "REVIEW":
        what_would_change = _review_hints(precedence, uncertainty, sensitivity)
    else:
        what_would_change = []

    return DecisionExplanation(
        summary=summary,
        limiting_constraint=limiting_name,
        headroom_summary=headroom,
        passed=passed,
        failed=failed,
        warnings=warnings,
        uncertainty_notes=uncertainty_notes,
        what_would_change_decision=what_would_change,
    )
