# Copyright 2026 Lightibulb
"""Configurable constraint engine for operational limits.

Evaluates observed values against physical capacity and reserved safety
margins, with per-parameter warning thresholds and an explicit minimum
headroom floor. This module is the CANONICAL constraint evaluator: the live decision path
(``decision.state_machine`` via ``decision.transparent``) uses it exclusively.
``decision.constraints`` / ``decision.engine`` are legacy evaluators kept only
for their existing tests and are not used by any runtime code path.

Every observed value is checked against three distinct boundaries:

    OK                 observed <= permitted, with comfortable headroom
    WARNING            observed <= permitted, headroom below warning threshold
                       or below min_headroom
    RESERVE_VIOLATION  observed > permitted = capacity * (1 - reserve_pct)
    PHYSICAL_VIOLATION observed > capacity

Future extensibility: any constraint that follows the "physical capacity
minus reserved fraction" pattern (temperature, airflow, redundancy,
power-distribution limits) subclasses ``ReserveConfig`` and is evaluated by
the same ``evaluate_load`` function.
"""

import math
from enum import StrEnum

import pydantic


class ConstraintSeverity(StrEnum):
    """Severity of a single constraint evaluation.

    Ordered from best to worst; ``_SEVERITY_ORDER`` encodes the ranking.
    """

    OK = "OK"
    WARNING = "WARNING"
    RESERVE_VIOLATION = "RESERVE_VIOLATION"
    PHYSICAL_VIOLATION = "PHYSICAL_VIOLATION"


_SEVERITY_ORDER: dict[ConstraintSeverity, int] = {
    ConstraintSeverity.OK: 0,
    ConstraintSeverity.WARNING: 1,
    ConstraintSeverity.RESERVE_VIOLATION: 2,
    ConstraintSeverity.PHYSICAL_VIOLATION: 3,
}


class ReserveConfig(pydantic.BaseModel):
    """Base configuration for any reserve-based physical limit.

    Attributes:
        capacity: Physical maximum (kW, RPM, degC, etc.). Must be > 0.
        reserve_pct: Fraction (0 <= x < 1) of capacity that must remain unused.
        warning_threshold_pct: Warn when remaining headroom falls below this
            fraction of the permitted value. Default 0.10.
        min_headroom: Absolute warning floor on remaining headroom. Default 0.
    """

    model_config = pydantic.ConfigDict(frozen=True)

    capacity: float = pydantic.Field(gt=0, allow_inf_nan=False)
    reserve_pct: float = pydantic.Field(ge=0.0, lt=1.0, allow_inf_nan=False)
    warning_threshold_pct: float = pydantic.Field(
        ge=0.0, lt=1.0, default=0.10, allow_inf_nan=False
    )
    min_headroom: float = pydantic.Field(
        ge=0.0, default=0.0, allow_inf_nan=False
    )


class ElectricalLimits(ReserveConfig):
    """Electrical capacity limits (kW)."""


class CoolingLimits(ReserveConfig):
    """Cooling capacity limits (kW)."""


class ConstraintConfig(pydantic.BaseModel):
    """Bundle of all active constraint configurations."""

    model_config = pydantic.ConfigDict(frozen=True)

    electrical: ElectricalLimits
    cooling: CoolingLimits


class LimitEvaluation(pydantic.BaseModel):
    """Outcome of evaluating one observed value against one limit."""

    name: str
    observed: float
    physical_capacity: float
    permitted: float
    headroom_to_permitted: float
    headroom_to_physical: float
    margin_used_pct: float
    severity: ConstraintSeverity
    is_limiting: bool = False
    reason: str


class ConstraintSummary(pydantic.BaseModel):
    """Aggregated outcome of evaluating all constraints at once."""

    evaluations: list[LimitEvaluation]
    limiting_factor: str | None
    violations: list[str]
    warnings: list[str]
    overall_severity: ConstraintSeverity


def evaluate_load(
    config: ReserveConfig,
    observed: float,
    name: str,
) -> LimitEvaluation:
    """Evaluate one observed value against one reserve-based limit.

    ``observed`` must be finite and >= 0. Boundary behaviour:
      observed == capacity, reserve_pct > 0  -> RESERVE_VIOLATION
      observed == capacity, reserve_pct == 0 -> WARNING (zero headroom)
      observed == permitted                  -> WARNING (zero headroom)
      observed <  permitted, tight headroom  -> WARNING
    """
    if not math.isfinite(observed):
        msg = f"observed must be finite (got {observed})"
        raise ValueError(msg)
    if observed < 0:
        msg = f"observed must be >= 0 (got {observed})"
        raise ValueError(msg)

    permitted = config.capacity * (1.0 - config.reserve_pct)
    headroom_permitted = permitted - observed
    headroom_physical = config.capacity - observed
    margin_used_pct = (observed / config.capacity) * 100.0

    if observed > config.capacity:
        severity = ConstraintSeverity.PHYSICAL_VIOLATION
        reason = (
            f"{name}: observed {observed:.4f} exceeds physical capacity "
            f"{config.capacity:.4f}"
        )
    elif observed > permitted:
        severity = ConstraintSeverity.RESERVE_VIOLATION
        reason = (
            f"{name}: observed {observed:.4f} exceeds permitted "
            f"{permitted:.4f} (reserve {config.reserve_pct * 100:.1f}%)"
        )
    elif headroom_permitted < config.warning_threshold_pct * permitted:
        severity = ConstraintSeverity.WARNING
        reason = (
            f"{name}: headroom {headroom_permitted:.4f} below warning "
            f"threshold {config.warning_threshold_pct * 100:.1f}% of "
            f"permitted {permitted:.4f}"
        )
    elif headroom_permitted < config.min_headroom:
        severity = ConstraintSeverity.WARNING
        reason = (
            f"{name}: headroom {headroom_permitted:.4f} below "
            f"min_headroom {config.min_headroom:.4f}"
        )
    else:
        severity = ConstraintSeverity.OK
        reason = (
            f"{name}: observed {observed:.4f} within permitted "
            f"{permitted:.4f}"
        )

    return LimitEvaluation(
        name=name,
        observed=round(observed, 4),
        physical_capacity=round(config.capacity, 4),
        permitted=round(permitted, 4),
        headroom_to_permitted=round(headroom_permitted, 4),
        headroom_to_physical=round(headroom_physical, 4),
        margin_used_pct=round(margin_used_pct, 4),
        severity=severity,
        reason=reason,
    )


def evaluate_all(
    config: ConstraintConfig,
    *,
    observed_electrical: float,
    observed_cooling: float,
) -> ConstraintSummary:
    """Evaluate every active constraint independently.

    ``limiting_factor`` is the constraint with the worst severity. Among
    equal severities, the one with the smallest headroom-to-permitted wins.
    When all constraints are OK, the one with the smallest headroom is still
    reported as the limiting factor.
    """
    evals = [
        evaluate_load(config.electrical, observed_electrical, "electrical"),
        evaluate_load(config.cooling, observed_cooling, "cooling"),
    ]

    worst = max(
        evals,
        key=lambda e: (
            _SEVERITY_ORDER[e.severity],
            -e.headroom_to_permitted,
        ),
    )
    if worst.severity != ConstraintSeverity.OK:
        limiting = worst.name
    else:
        limiting = min(evals, key=lambda e: e.headroom_to_permitted).name

    evals_final = [
        e.model_copy(update={"is_limiting": e.name == limiting}) for e in evals
    ]
    violations = [
        e.name
        for e in evals_final
        if e.severity
        in (
            ConstraintSeverity.PHYSICAL_VIOLATION,
            ConstraintSeverity.RESERVE_VIOLATION,
        )
    ]
    warnings = [
        e.name
        for e in evals_final
        if e.severity == ConstraintSeverity.WARNING
    ]

    return ConstraintSummary(
        evaluations=evals_final,
        limiting_factor=limiting,
        violations=violations,
        warnings=warnings,
        overall_severity=worst.severity,
    )


def config_snapshot(config: ConstraintConfig) -> dict:
    """Return a plain-dict snapshot for logging or persistence."""
    return config.model_dump()


def config_diff(old: ConstraintConfig, new: ConstraintConfig) -> list[str]:
    """Return human-readable strings for every configuration field that changed."""
    changes: list[str] = []
    old_d = old.model_dump()
    new_d = new.model_dump()
    for section in old_d:
        for field, old_val in old_d[section].items():
            new_val = new_d[section][field]
            if old_val != new_val:
                changes.append(f"{section}.{field}: {old_val} -> {new_val}")
    return changes
