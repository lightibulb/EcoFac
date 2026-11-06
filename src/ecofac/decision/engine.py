# LEGACY MODULE - deprecated; scheduled for removal in v2.0.
# See docs/LEGACY.md. Use ecofac.decision.state_machine / limits instead.

# Copyright 2026 Lightibulb
"""DEPRECATED: superseded by decision.transparent.

Retained for backward compatibility with existing callers. New code
should use ``decision.transparent.decide_transparently``, which wraps the
state machine and produces evidence-backed decisions.
"""

import warnings

from ecofac.decision.constraints import (
    compute_predicted,
    evaluate_cooling,
    evaluate_electrical,
    evaluate_pue,
)
from ecofac.decision.schemas import (
    ConstraintEvaluation,
    ConstraintStatus,
    DataQualitySnapshot,
    Decision,
    DecisionStatus,
    FacilityMeasurements,
    OperationalConstraints,
    ProposedChange,
)

warnings.warn(
    "decision.engine is deprecated; use decision.transparent",
    DeprecationWarning,
    stacklevel=2,
)

# Warn when a passing constraint has less than this fraction of its limit
# remaining. This is a policy constant, not an operational threshold; the
# limits themselves are always supplied by the caller.
WARNING_MARGIN_FRACTION = 0.10


def _insufficient_data(reasons: list[str]) -> Decision:
    """Build an INSUFFICIENT_DATA decision with all reasons listed."""
    return Decision(
        status=DecisionStatus.INSUFFICIENT_DATA,
        predicted=None,
        constraints=[],
        warnings=[],
        evidence={"insufficient_reasons": reasons},
        explanation="Insufficient data: " + "; ".join(reasons),
    )


def _collect_warnings(evaluations: list[ConstraintEvaluation]) -> list[str]:
    """Flag passing constraints whose remaining margin is tight."""
    warnings: list[str] = []
    for ev in evaluations:
        if ev.status != ConstraintStatus.PASS:
            continue
        if ev.margin is None or ev.limit <= 0:
            continue
        if ev.margin / ev.limit < WARNING_MARGIN_FRACTION:
            warnings.append(
                f"{ev.name}: margin {ev.margin:.2f} is within "
                f"{WARNING_MARGIN_FRACTION * 100:.0f}% of limit {ev.limit:.2f}"
            )
    return warnings


def decide(
    constraints: OperationalConstraints,
    measurements: FacilityMeasurements,
    change: ProposedChange,
    quality: DataQualitySnapshot,
) -> Decision:
    """Evaluate a proposed operational change against constraints.

    Rules (in order):
    1. Quality ineligible -> INSUFFICIENT_DATA with quality flags.
    2. Quality stale -> INSUFFICIENT_DATA.
    3. Missing IT or cooling load -> INSUFFICIENT_DATA, names the missing field.
    4. Otherwise evaluate electrical, cooling, and PUE. Any FAIL -> REJECT.
    5. All pass -> APPROVE, with warnings for tight margins.
    """
    if not quality.eligible:
        flags = ", ".join(quality.flags) if quality.flags else "no flags"
        return _insufficient_data([f"Data quality ineligible: {flags}"])

    if quality.stale:
        return _insufficient_data(["Measurements are stale"])

    predicted = compute_predicted(measurements, change)
    if predicted is None:
        missing: list[str] = []
        if measurements.it_load_kw is None:
            missing.append("it_load_kw")
        if measurements.cooling_load_kw is None:
            missing.append("cooling_load_kw")
        if not missing:
            missing.append("projected IT load must be > 0")
        return _insufficient_data([f"Missing or invalid: {', '.join(missing)}"])

    evaluations = [
        evaluate_electrical(predicted, constraints),
        evaluate_cooling(predicted, constraints),
        evaluate_pue(predicted, constraints),
    ]

    failed = [ev for ev in evaluations if ev.status == ConstraintStatus.FAIL]
    warnings = _collect_warnings(evaluations)

    if failed:
        status = DecisionStatus.REJECT
        explanation = f"Rejected: {len(failed)} constraint(s) failed - " + "; ".join(
            f"{ev.name}: {ev.reason}" for ev in failed
        )
    else:
        status = DecisionStatus.APPROVE
        explanation = (
            "Approved: all constraints pass with "
            f"{constraints.safety_margin_pct * 100:.1f}% safety margin"
        )

    evidence = {
        "inputs": {
            "it_load_kw": measurements.it_load_kw,
            "cooling_load_kw": measurements.cooling_load_kw,
            "pue": measurements.pue,
        },
        "change": {
            "it_load_delta_kw": change.it_load_delta_kw,
            "cooling_load_delta_kw": change.cooling_load_delta_kw,
            "description": change.description,
        },
        "constraints": {
            "electrical_capacity_kw": constraints.electrical_capacity_kw,
            "cooling_capacity_kw": constraints.cooling_capacity_kw,
            "max_pue": constraints.max_pue,
            "safety_margin_pct": constraints.safety_margin_pct,
        },
        "quality": {
            "eligible": quality.eligible,
            "stale": quality.stale,
            "flags": list(quality.flags),
        },
    }

    return Decision(
        status=status,
        predicted=predicted,
        constraints=evaluations,
        warnings=warnings,
        evidence=evidence,
        explanation=explanation,
    )
