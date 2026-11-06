# Copyright 2026 Lightibulb
"""Corrective action mode separates current overload from proposed violations."""

from ecofac.decision.limits import (
    ConstraintConfig,
    CoolingLimits,
    ElectricalLimits,
)
from ecofac.decision.state_machine import (
    DecisionContext,
    FinalDecisionStatus,
    evaluate_decision,
)


def _config() -> ConstraintConfig:
    return ConstraintConfig(
        electrical=ElectricalLimits(capacity=1000.0, reserve_pct=0.10),
        cooling=CoolingLimits(capacity=800.0, reserve_pct=0.10),
    )


def _ctx(
    current_e: float,
    predicted_e: float,
    *,
    corrective: bool = False,
    current_c: float = 300.0,
    predicted_c: float = 300.0,
) -> DecisionContext:
    return DecisionContext(
        config=_config(),
        config_version="cfg-1",
        current_electrical_kw=current_e,
        current_cooling_kw=current_c,
        predicted_electrical_kw=predicted_e,
        predicted_cooling_kw=predicted_c,
        quality_eligible=True,
        quality_stale=False,
        corrective_action_mode=corrective,
    )


def test_corrective_action_bypasses_current_violation():
    """Current load at 1100 (physical overload) -> proposed reduction to 800."""
    decision = evaluate_decision(_ctx(current_e=1100.0, predicted_e=800.0, corrective=True))
    assert decision.status == FinalDecisionStatus.APPROVE


def test_default_mode_rejects_current_violation():
    decision = evaluate_decision(_ctx(current_e=1100.0, predicted_e=800.0, corrective=False))
    assert decision.status == FinalDecisionStatus.REJECT


def test_corrective_action_still_rejects_predicted_violation():
    """Reduction to 1200 still exceeds capacity."""
    decision = evaluate_decision(_ctx(current_e=1100.0, predicted_e=1200.0, corrective=True))
    assert decision.status == FinalDecisionStatus.REJECT


def test_corrective_action_preserves_clean_approve():
    decision = evaluate_decision(_ctx(current_e=400.0, predicted_e=400.0, corrective=True))
    assert decision.status == FinalDecisionStatus.APPROVE
