# Copyright 2026 Lightibulb

"""Boundary and precedence tests for the deterministic decision state machine."""

import pydantic
import pytest

from ecofac.decision.limits import (
    ConstraintConfig,
    ConstraintSeverity,
    CoolingLimits,
    ElectricalLimits,
)
from ecofac.decision.state_machine import (
    ENGINE_VERSION,
    DecisionContext,
    DecisionPrecedence,
    FinalDecisionStatus,
    evaluate_decision,
)


def _config() -> ConstraintConfig:
    return ConstraintConfig(
        electrical=ElectricalLimits(capacity=1000.0, reserve_pct=0.10),
        cooling=CoolingLimits(capacity=800.0, reserve_pct=0.10),
    )


def _clean_context(
    predicted_e: float = 400.0, predicted_c: float = 300.0
) -> DecisionContext:
    """Comfortable margins on both constraints."""
    return DecisionContext(
        config=_config(),
        config_version="cfg-1",
        current_electrical_kw=predicted_e,
        current_cooling_kw=predicted_c,
        predicted_electrical_kw=predicted_e,
        predicted_cooling_kw=predicted_c,
        quality_eligible=True,
        quality_stale=False,
    )


# ---------------------------------------------------------------------------
# Enum and version invariants
# ---------------------------------------------------------------------------

def test_status_enum_values():
    assert FinalDecisionStatus.APPROVE.value == "APPROVE"
    assert FinalDecisionStatus.REJECT.value == "REJECT"
    assert FinalDecisionStatus.REVIEW.value == "REVIEW"


def test_precedence_enum_values():
    assert DecisionPrecedence.HARD_LIMIT_VIOLATION.value == "hard_limit_violation"
    assert DecisionPrecedence.RESERVE_VIOLATION.value == "reserve_violation"
    assert (
        DecisionPrecedence.DATA_QUALITY_INELIGIBLE.value
        == "data_quality_ineligible"
    )
    assert DecisionPrecedence.NEAR_THRESHOLD.value == "near_threshold"
    assert DecisionPrecedence.UNCERTAINTY.value == "uncertainty"
    assert DecisionPrecedence.CONFLICTING_INPUTS.value == "conflicting_inputs"
    assert DecisionPrecedence.CLEAN.value == "clean"


def test_engine_version_is_string():
    assert isinstance(ENGINE_VERSION, str)
    assert len(ENGINE_VERSION) > 0


# ---------------------------------------------------------------------------
# Context schema validation
# ---------------------------------------------------------------------------

def test_context_rejects_negative_current_electrical():
    with pytest.raises(pydantic.ValidationError):
        DecisionContext(
            config=_config(),
            config_version="cfg-1",
            current_electrical_kw=-1.0,
            current_cooling_kw=100.0,
            predicted_electrical_kw=100.0,
            predicted_cooling_kw=100.0,
            quality_eligible=True,
            quality_stale=False,
        )


def test_context_rejects_negative_predicted_cooling():
    with pytest.raises(pydantic.ValidationError):
        DecisionContext(
            config=_config(),
            config_version="cfg-1",
            current_electrical_kw=100.0,
            current_cooling_kw=100.0,
            predicted_electrical_kw=100.0,
            predicted_cooling_kw=-5.0,
            quality_eligible=True,
            quality_stale=False,
        )


def test_context_defaults_lists_to_empty():
    ctx = _clean_context()
    assert ctx.quality_flags == []
    assert ctx.uncertainty_flags == []
    assert ctx.conflicting_inputs == []


def test_context_frozen():
    ctx = _clean_context()
    with pytest.raises(pydantic.ValidationError):
        ctx.quality_eligible = False  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Structural guarantee: no anomaly input
# ---------------------------------------------------------------------------

def test_anomaly_not_an_input():
    """DecisionContext must not accept anomaly fields."""
    fields = set(DecisionContext.model_fields)
    for forbidden in (
        "anomaly",
        "anomalies",
        "anomaly_flags",
        "detection_result",
    ):
        assert forbidden not in fields


# ---------------------------------------------------------------------------
# APPROVE
# ---------------------------------------------------------------------------

def test_approve_clean_inputs():
    decision = evaluate_decision(_clean_context())
    assert decision.status == FinalDecisionStatus.APPROVE
    assert decision.precedence == DecisionPrecedence.CLEAN
    assert decision.warnings == []
    assert decision.unresolved_conditions == []
    assert decision.reasons


def test_approve_requires_no_uncertainty():
    ctx = _clean_context().model_copy(
        update={"uncertainty_flags": ["assumed_pue"]}
    )
    decision = evaluate_decision(ctx)
    assert decision.status != FinalDecisionStatus.APPROVE


# ---------------------------------------------------------------------------
# REJECT precedence
# ---------------------------------------------------------------------------

def test_reject_on_predicted_physical_violation():
    ctx = _clean_context(predicted_e=1100.0)
    decision = evaluate_decision(ctx)
    assert decision.status == FinalDecisionStatus.REJECT
    assert decision.precedence == DecisionPrecedence.HARD_LIMIT_VIOLATION
    assert any("capacity" in r for r in decision.reasons)


def test_reject_on_current_physical_violation():
    ctx = _clean_context()
    ctx = ctx.model_copy(
        update={
            "current_electrical_kw": 1100.0,
            "predicted_electrical_kw": 400.0,
        }
    )
    decision = evaluate_decision(ctx)
    assert decision.status == FinalDecisionStatus.REJECT
    assert decision.precedence == DecisionPrecedence.HARD_LIMIT_VIOLATION


def test_reject_on_reserve_violation():
    ctx = _clean_context(predicted_e=950.0)
    decision = evaluate_decision(ctx)
    assert decision.status == FinalDecisionStatus.REJECT
    assert decision.precedence == DecisionPrecedence.RESERVE_VIOLATION


def test_reject_precedence_over_quality():
    """Hard limit wins over ineligible data."""
    ctx = _clean_context(predicted_e=1100.0).model_copy(
        update={
            "quality_eligible": False,
            "quality_flags": ["missing_data"],
        }
    )
    decision = evaluate_decision(ctx)
    assert decision.status == FinalDecisionStatus.REJECT
    assert decision.precedence == DecisionPrecedence.HARD_LIMIT_VIOLATION


def test_reject_precedence_reserve_over_quality():
    """Reserve violation wins over ineligible data."""
    ctx = _clean_context(predicted_e=950.0).model_copy(
        update={"quality_eligible": False}
    )
    decision = evaluate_decision(ctx)
    assert decision.status == FinalDecisionStatus.REJECT
    assert decision.precedence == DecisionPrecedence.RESERVE_VIOLATION


def test_reject_records_violated_constraints():
    ctx = _clean_context(predicted_e=1100.0)
    decision = evaluate_decision(ctx)
    severities = {ev.severity for ev in decision.evaluated_constraints}
    assert ConstraintSeverity.PHYSICAL_VIOLATION in severities


# ---------------------------------------------------------------------------
# REVIEW cases
# ---------------------------------------------------------------------------

def test_review_on_quality_ineligible():
    ctx = _clean_context().model_copy(
        update={
            "quality_eligible": False,
            "quality_flags": ["missing_data", "duplicate_data"],
        }
    )
    decision = evaluate_decision(ctx)
    assert decision.status == FinalDecisionStatus.REVIEW
    assert decision.precedence == DecisionPrecedence.DATA_QUALITY_INELIGIBLE
    assert any("missing_data" in r for r in decision.reasons)
    assert any("quality" in u for u in decision.unresolved_conditions)


def test_review_on_quality_stale():
    ctx = _clean_context().model_copy(update={"quality_stale": True})
    decision = evaluate_decision(ctx)
    assert decision.status == FinalDecisionStatus.REVIEW
    assert decision.precedence == DecisionPrecedence.DATA_QUALITY_INELIGIBLE
    assert "stale" in decision.reasons[0].lower()


def test_review_on_warning_margin():
    """Predicted 850 against permitted 900 -> headroom 50 < 90 (warning)."""
    ctx = _clean_context(predicted_e=850.0)
    decision = evaluate_decision(ctx)
    assert decision.status == FinalDecisionStatus.REVIEW
    assert decision.precedence == DecisionPrecedence.NEAR_THRESHOLD


def test_review_on_uncertainty():
    ctx = _clean_context().model_copy(
        update={"uncertainty_flags": ["assumed_pue"]}
    )
    decision = evaluate_decision(ctx)
    assert decision.status == FinalDecisionStatus.REVIEW
    assert decision.precedence == DecisionPrecedence.UNCERTAINTY


def test_review_on_conflicting_inputs():
    ctx = _clean_context().model_copy(
        update={"conflicting_inputs": ["pue_mismatch"]}
    )
    decision = evaluate_decision(ctx)
    assert decision.status == FinalDecisionStatus.REVIEW
    assert decision.precedence == DecisionPrecedence.CONFLICTING_INPUTS


def test_review_precedence_quality_over_warning():
    """Ineligible quality wins over a near-threshold warning."""
    ctx = _clean_context(predicted_e=850.0).model_copy(
        update={"quality_eligible": False}
    )
    decision = evaluate_decision(ctx)
    assert decision.status == FinalDecisionStatus.REVIEW
    assert decision.precedence == DecisionPrecedence.DATA_QUALITY_INELIGIBLE


def test_review_precedence_warning_over_uncertainty():
    """Near-threshold wins over uncertainty when both exist."""
    ctx = _clean_context(predicted_e=850.0).model_copy(
        update={"uncertainty_flags": ["assumed_pue"]}
    )
    decision = evaluate_decision(ctx)
    assert decision.status == FinalDecisionStatus.REVIEW
    assert decision.precedence == DecisionPrecedence.NEAR_THRESHOLD


def test_review_precedence_uncertainty_over_conflict():
    """Uncertainty wins over conflicting inputs when both exist."""
    ctx = _clean_context().model_copy(
        update={
            "uncertainty_flags": ["assumed_pue"],
            "conflicting_inputs": ["pue_mismatch"],
        }
    )
    decision = evaluate_decision(ctx)
    assert decision.status == FinalDecisionStatus.REVIEW
    assert decision.precedence == DecisionPrecedence.UNCERTAINTY


def test_review_records_all_unresolved_conditions():
    """Even when quality drives the REVIEW, other flags are still recorded."""
    ctx = _clean_context().model_copy(
        update={
            "quality_eligible": False,
            "quality_flags": ["missing_data"],
            "uncertainty_flags": ["assumed_pue"],
            "conflicting_inputs": ["pue_mismatch"],
        }
    )
    decision = evaluate_decision(ctx)
    assert decision.status == FinalDecisionStatus.REVIEW
    assert decision.precedence == DecisionPrecedence.DATA_QUALITY_INELIGIBLE
    joined = " ".join(decision.unresolved_conditions)
    assert "missing_data" in joined
    assert "assumed_pue" in joined
    assert "pue_mismatch" in joined


# ---------------------------------------------------------------------------
# Safety-critical invariants
# ---------------------------------------------------------------------------

def test_never_approve_without_eligible_quality():
    """Structural: ineligible quality is never APPROVE, regardless of loads."""
    for e_load in (0.0, 400.0, 850.0):
        for c_load in (0.0, 300.0, 700.0):
            ctx = _clean_context(
                predicted_e=e_load, predicted_c=c_load
            ).model_copy(update={"quality_eligible": False})
            decision = evaluate_decision(ctx)
            assert decision.status != FinalDecisionStatus.APPROVE, (
                f"APPROVE reached with ineligible quality at {e_load}/{c_load}"
            )


def test_missing_data_never_treated_as_safe():
    """Structural: quality ineligible must produce REVIEW or REJECT."""
    ctx = _clean_context().model_copy(
        update={
            "quality_eligible": False,
            "quality_flags": ["missing_data"],
        }
    )
    decision = evaluate_decision(ctx)
    assert decision.status in (
        FinalDecisionStatus.REVIEW,
        FinalDecisionStatus.REJECT,
    )


# ---------------------------------------------------------------------------
# Determinism and metadata
# ---------------------------------------------------------------------------

def test_deterministic_same_inputs():
    ctx = _clean_context()
    first = evaluate_decision(ctx)
    second = evaluate_decision(ctx)
    first_dump = first.model_dump(exclude={"decision_timestamp"})
    second_dump = second.model_dump(exclude={"decision_timestamp"})
    assert first_dump == second_dump


def test_metadata_complete_approve():
    decision = evaluate_decision(_clean_context())
    assert decision.decision_timestamp.endswith("Z")
    assert decision.engine_version == ENGINE_VERSION
    assert decision.config_version == "cfg-1"
    assert isinstance(decision.evaluated_constraints, list)
    assert len(decision.evaluated_constraints) >= 2


def test_metadata_complete_reject():
    decision = evaluate_decision(_clean_context(predicted_e=1100.0))
    assert decision.engine_version == ENGINE_VERSION
    assert decision.config_version == "cfg-1"
    assert decision.decision_timestamp.endswith("Z")


def test_config_version_propagated():
    ctx = _clean_context().model_copy(update={"config_version": "cfg-42"})
    decision = evaluate_decision(ctx)
    assert decision.config_version == "cfg-42"


def test_engine_version_constant_used():
    decision = evaluate_decision(_clean_context())
    assert decision.engine_version == "1.0"
