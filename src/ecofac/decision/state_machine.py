# Copyright 2026 Lightibulb
"""Deterministic decision state machine.

Consumes evaluated constraints from ``decision.limits`` and produces one of
three final states: APPROVE, REJECT, or REVIEW. Precedence is fixed and
explicit; two calls with identical inputs produce identical output except
for the recorded ``decision_timestamp``.

**Structural guarantees** (enforced by input schema):

- ``DecisionContext`` accepts no anomaly or AI-detection field. The engine
  cannot approve a proposal "because no anomaly was detected."
- ``quality_eligible`` must be True and ``quality_stale`` False for an
  APPROVE. Missing or stale data can never silently become a safe result.
- Statistical anomaly detection is a separate subsystem. Nothing in this
  module consumes its outputs.

Precedence order (first match wins):

1. ``PHYSICAL_VIOLATION``   -> REJECT  (``hard_limit_violation``)
2. ``RESERVE_VIOLATION``    -> REJECT  (``reserve_violation``)
3. Quality ineligible/stale -> REVIEW  (``data_quality_ineligible``)
4. ``WARNING``              -> REVIEW  (``near_threshold``)
5. Uncertainty flags        -> REVIEW  (``uncertainty``)
6. Conflicting inputs       -> REVIEW  (``conflicting_inputs``)
7. Otherwise                -> APPROVE (``clean``)
"""

from enum import StrEnum

import pydantic

from ecofac.decision.limits import (
    ConstraintConfig,
    ConstraintSeverity,
    LimitEvaluation,
    evaluate_all,
)
from ecofac.timeutil import now_z

ENGINE_VERSION = "1.0"


class FinalDecisionStatus(StrEnum):
    """Final decision state."""

    APPROVE = "APPROVE"
    REJECT = "REJECT"
    REVIEW = "REVIEW"


class DecisionPrecedence(StrEnum):
    """Which rule produced the final state. Recorded for auditability."""

    HARD_LIMIT_VIOLATION = "hard_limit_violation"
    RESERVE_VIOLATION = "reserve_violation"
    DATA_QUALITY_INELIGIBLE = "data_quality_ineligible"
    NEAR_THRESHOLD = "near_threshold"
    UNCERTAINTY = "uncertainty"
    CONFLICTING_INPUTS = "conflicting_inputs"
    CLEAN = "clean"


class DecisionContext(pydantic.BaseModel):
    """All inputs the state machine evaluates.

    Numeric load fields must be non-negative. ``quality_eligible`` and
    ``quality_stale`` are required. List fields default to empty. There is
    no anomaly field, by design.
    """

    model_config = pydantic.ConfigDict(frozen=True)

    config: ConstraintConfig
    config_version: str
    current_electrical_kw: float = pydantic.Field(ge=0)
    current_cooling_kw: float = pydantic.Field(ge=0)
    predicted_electrical_kw: float = pydantic.Field(ge=0)
    predicted_cooling_kw: float = pydantic.Field(ge=0)
    quality_eligible: bool
    quality_stale: bool
    quality_flags: list[str] = pydantic.Field(default_factory=list)
    uncertainty_flags: list[str] = pydantic.Field(default_factory=list)
    conflicting_inputs: list[str] = pydantic.Field(default_factory=list)


class FinalDecision(pydantic.BaseModel):
    """Complete, reproducible decision record."""

    model_config = pydantic.ConfigDict(frozen=True)

    status: FinalDecisionStatus
    precedence: DecisionPrecedence
    reasons: list[str]
    warnings: list[str]
    unresolved_conditions: list[str]
    evaluated_constraints: list[LimitEvaluation]
    decision_timestamp: str
    engine_version: str
    config_version: str


def _evaluate_context_constraints(
    context: DecisionContext,
) -> list[LimitEvaluation]:
    """Evaluate constraints on both current and predicted state.

    Predicted state is authoritative for status. Current state is included
    so an already-overloaded input is visible to reviewers.
    """
    current_summary = evaluate_all(
        context.config,
        observed_electrical=context.current_electrical_kw,
        observed_cooling=context.current_cooling_kw,
    )
    predicted_summary = evaluate_all(
        context.config,
        observed_electrical=context.predicted_electrical_kw,
        observed_cooling=context.predicted_cooling_kw,
    )
    return current_summary.evaluations + predicted_summary.evaluations


def _reasons_for(
    evaluations: list[LimitEvaluation], severity: ConstraintSeverity
) -> list[str]:
    """Collect human-readable reasons for every evaluation at a given severity."""
    return [ev.reason for ev in evaluations if ev.severity == severity]


def _any_severity(
    evaluations: list[LimitEvaluation], severity: ConstraintSeverity
) -> bool:
    """True if any evaluation has the given severity."""
    return any(ev.severity == severity for ev in evaluations)


def _build_decision(
    context: DecisionContext,
    status: FinalDecisionStatus,
    precedence: DecisionPrecedence,
    reasons: list[str],
    evaluations: list[LimitEvaluation],
) -> FinalDecision:
    """Assemble a FinalDecision with metadata and recorded unresolved issues."""
    warnings: list[str] = []
    unresolved: list[str] = []
    if status != FinalDecisionStatus.APPROVE:
        if not context.quality_eligible:
            warnings.append("data quality is ineligible")
        if context.quality_stale:
            warnings.append("measurements are stale")
        unresolved.extend(
            f"quality: {flag}" for flag in context.quality_flags
        )
        unresolved.extend(
            f"uncertainty: {flag}" for flag in context.uncertainty_flags
        )
        unresolved.extend(
            f"conflict: {flag}" for flag in context.conflicting_inputs
        )
    return FinalDecision(
        status=status,
        precedence=precedence,
        reasons=reasons,
        warnings=warnings,
        unresolved_conditions=unresolved,
        evaluated_constraints=evaluations,
        decision_timestamp=now_z(),
        engine_version=ENGINE_VERSION,
        config_version=context.config_version,
    )


def evaluate_decision(context: DecisionContext) -> FinalDecision:
    """Apply the precedence rules and return the final decision."""
    evaluations = _evaluate_context_constraints(context)

    if _any_severity(evaluations, ConstraintSeverity.PHYSICAL_VIOLATION):
        return _build_decision(
            context,
            FinalDecisionStatus.REJECT,
            DecisionPrecedence.HARD_LIMIT_VIOLATION,
            _reasons_for(evaluations, ConstraintSeverity.PHYSICAL_VIOLATION),
            evaluations,
        )

    if _any_severity(evaluations, ConstraintSeverity.RESERVE_VIOLATION):
        return _build_decision(
            context,
            FinalDecisionStatus.REJECT,
            DecisionPrecedence.RESERVE_VIOLATION,
            _reasons_for(evaluations, ConstraintSeverity.RESERVE_VIOLATION),
            evaluations,
        )

    if not context.quality_eligible or context.quality_stale:
        quality_reasons: list[str] = []
        if not context.quality_eligible:
            flags = (
                ", ".join(context.quality_flags)
                if context.quality_flags
                else "no flags"
            )
            quality_reasons.append(f"data quality ineligible: {flags}")
        if context.quality_stale:
            quality_reasons.append("measurements are stale")
        return _build_decision(
            context,
            FinalDecisionStatus.REVIEW,
            DecisionPrecedence.DATA_QUALITY_INELIGIBLE,
            quality_reasons,
            evaluations,
        )

    if _any_severity(evaluations, ConstraintSeverity.WARNING):
        return _build_decision(
            context,
            FinalDecisionStatus.REVIEW,
            DecisionPrecedence.NEAR_THRESHOLD,
            _reasons_for(evaluations, ConstraintSeverity.WARNING),
            evaluations,
        )

    if context.uncertainty_flags:
        return _build_decision(
            context,
            FinalDecisionStatus.REVIEW,
            DecisionPrecedence.UNCERTAINTY,
            [f"uncertainty: {flag}" for flag in context.uncertainty_flags],
            evaluations,
        )

    if context.conflicting_inputs:
        return _build_decision(
            context,
            FinalDecisionStatus.REVIEW,
            DecisionPrecedence.CONFLICTING_INPUTS,
            [f"conflict: {flag}" for flag in context.conflicting_inputs],
            evaluations,
        )

    return _build_decision(
        context,
        FinalDecisionStatus.APPROVE,
        DecisionPrecedence.CLEAN,
        ["all constraints within permitted limits; no unresolved issues"],
        evaluations,
    )
