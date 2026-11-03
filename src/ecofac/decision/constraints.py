# Copyright 2026 Lightibulb
"""DEPRECATED: superseded by decision.state_machine + decision.limits.

Retained for backward compatibility. New code should use the constraint
evaluators in ``decision.limits`` and the orchestrator in
``decision.transparent``.
"""


from ecofac.decision.schemas import (
    ConstraintEvaluation,
    ConstraintStatus,
    FacilityMeasurements,
    OperationalConstraints,
    PredictedConditions,
    ProposedChange,
)


def compute_predicted(
    measurements: FacilityMeasurements,
    change: ProposedChange,
) -> PredictedConditions | None:
    """Project post-change operational state.

    Returns None if IT or cooling load is missing, or if the projected IT load
    is not strictly positive (which would make PUE undefined).
    """
    if measurements.it_load_kw is None or measurements.cooling_load_kw is None:
        return None
    new_it = measurements.it_load_kw + change.it_load_delta_kw
    new_cooling = measurements.cooling_load_kw + change.cooling_load_delta_kw
    if new_it <= 0:
        return None
    total = new_it + new_cooling
    return PredictedConditions(
        it_load_kw=round(new_it, 4),
        cooling_load_kw=round(new_cooling, 4),
        total_facility_kw=round(total, 4),
        pue=round(total / new_it, 4),
    )


def evaluate_electrical(
    predicted: PredictedConditions,
    constraints: OperationalConstraints,
) -> ConstraintEvaluation:
    """Check projected IT load against electrical capacity minus safety margin."""
    limit = constraints.electrical_capacity_kw * (1 - constraints.safety_margin_pct)
    observed = predicted.it_load_kw
    margin = limit - observed
    is_pass = observed <= limit
    status = ConstraintStatus.PASS if is_pass else ConstraintStatus.FAIL
    cmp = "<=" if is_pass else ">"
    reason = (
        f"IT load {observed:.2f} kW {cmp} {limit:.2f} kW "
        f"(capacity {constraints.electrical_capacity_kw:.2f} kW "
        f"minus {constraints.safety_margin_pct * 100:.1f}% safety margin)"
    )
    return ConstraintEvaluation(
        name="electrical_capacity",
        status=status,
        observed=observed,
        limit=round(limit, 4),
        margin=round(margin, 4),
        reason=reason,
    )


def evaluate_cooling(
    predicted: PredictedConditions,
    constraints: OperationalConstraints,
) -> ConstraintEvaluation:
    """Check projected cooling load against cooling capacity minus safety margin."""
    limit = constraints.cooling_capacity_kw * (1 - constraints.safety_margin_pct)
    observed = predicted.cooling_load_kw
    margin = limit - observed
    is_pass = observed <= limit
    status = ConstraintStatus.PASS if is_pass else ConstraintStatus.FAIL
    cmp = "<=" if is_pass else ">"
    reason = (
        f"Cooling load {observed:.2f} kW {cmp} {limit:.2f} kW "
        f"(capacity {constraints.cooling_capacity_kw:.2f} kW "
        f"minus {constraints.safety_margin_pct * 100:.1f}% safety margin)"
    )
    return ConstraintEvaluation(
        name="cooling_capacity",
        status=status,
        observed=observed,
        limit=round(limit, 4),
        margin=round(margin, 4),
        reason=reason,
    )


def evaluate_pue(
    predicted: PredictedConditions,
    constraints: OperationalConstraints,
) -> ConstraintEvaluation:
    """Check projected PUE against the caller-supplied ceiling."""
    observed = predicted.pue
    limit = constraints.max_pue
    margin = limit - observed
    is_pass = observed <= limit
    status = ConstraintStatus.PASS if is_pass else ConstraintStatus.FAIL
    cmp = "<=" if is_pass else ">"
    reason = f"PUE {observed:.3f} {cmp} {limit:.3f}"
    return ConstraintEvaluation(
        name="pue_ceiling",
        status=status,
        observed=observed,
        limit=round(limit, 4),
        margin=round(margin, 4),
        reason=reason,
    )
