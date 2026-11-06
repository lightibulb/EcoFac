# Copyright 2026 Lightibulb

"""Integration tests for the deprecated decision engine.

These tests target ``decision.engine`` and ``decision.constraints``, both of
which are deprecated (see the modules' docstrings). The tests are skipped —
`decision.transparent` is the canonical path, covered by
``test_transparent_decision``, ``test_operational_decision_safety`` and
``test_trusted_mode``.
"""

import pytest

from ecofac.decision.engine import decide
from ecofac.decision.schemas import (
    ConstraintStatus,
    DataQualitySnapshot,
    DecisionStatus,
    FacilityMeasurements,
    OperationalConstraints,
    ProposedChange,
)

pytestmark = pytest.mark.skip(
    reason="decision.engine is deprecated; use decision.transparent"
)


def _constraints() -> OperationalConstraints:
    return OperationalConstraints(
        electrical_capacity_kw=1000.0,
        cooling_capacity_kw=800.0,
        max_pue=1.5,
        safety_margin_pct=0.10,
    )


def _eligible() -> DataQualitySnapshot:
    return DataQualitySnapshot(eligible=True, stale=False)


def test_decide_approves_when_all_constraints_pass():
    decision = decide(
        _constraints(),
        FacilityMeasurements(it_load_kw=500.0, cooling_load_kw=200.0, pue=1.4),
        ProposedChange(it_load_delta_kw=50.0, cooling_load_delta_kw=20.0),
        _eligible(),
    )
    assert decision.status == DecisionStatus.APPROVE
    assert decision.predicted is not None
    assert decision.predicted.it_load_kw == 550.0
    assert decision.predicted.cooling_load_kw == 220.0
    assert decision.predicted.total_facility_kw == 770.0
    assert decision.predicted.pue == 1.4
    assert all(ev.status == ConstraintStatus.PASS for ev in decision.constraints)


def test_decide_rejects_when_electrical_exceeded():
    decision = decide(
        _constraints(),
        FacilityMeasurements(it_load_kw=850.0, cooling_load_kw=100.0),
        ProposedChange(it_load_delta_kw=100.0),
        _eligible(),
    )
    assert decision.status == DecisionStatus.REJECT
    assert decision.predicted is not None
    assert decision.predicted.it_load_kw == 950.0
    failed = [ev for ev in decision.constraints if ev.status == ConstraintStatus.FAIL]
    assert any(ev.name == "electrical_capacity" for ev in failed)


def test_decide_rejects_when_cooling_exceeded():
    decision = decide(
        _constraints(),
        FacilityMeasurements(it_load_kw=500.0, cooling_load_kw=700.0),
        ProposedChange(cooling_load_delta_kw=50.0),
        _eligible(),
    )
    assert decision.status == DecisionStatus.REJECT
    failed = [ev for ev in decision.constraints if ev.status == ConstraintStatus.FAIL]
    assert any(ev.name == "cooling_capacity" for ev in failed)


def test_decide_rejects_when_pue_exceeded():
    decision = decide(
        _constraints(),
        FacilityMeasurements(it_load_kw=500.0, cooling_load_kw=300.0),
        ProposedChange(it_load_delta_kw=50.0, cooling_load_delta_kw=50.0),
        _eligible(),
    )
    assert decision.status == DecisionStatus.REJECT
    failed = [ev for ev in decision.constraints if ev.status == ConstraintStatus.FAIL]
    assert any(ev.name == "pue_ceiling" for ev in failed)


def test_decide_insufficient_when_quality_ineligible():
    decision = decide(
        _constraints(),
        FacilityMeasurements(it_load_kw=500.0, cooling_load_kw=200.0),
        ProposedChange(),
        DataQualitySnapshot(eligible=False, flags=["missing_data", "duplicate_data"]),
    )
    assert decision.status == DecisionStatus.INSUFFICIENT_DATA
    assert decision.predicted is None
    assert decision.constraints == []
    assert "missing_data" in decision.explanation


def test_decide_insufficient_when_quality_stale():
    decision = decide(
        _constraints(),
        FacilityMeasurements(it_load_kw=500.0, cooling_load_kw=200.0),
        ProposedChange(),
        DataQualitySnapshot(eligible=True, stale=True),
    )
    assert decision.status == DecisionStatus.INSUFFICIENT_DATA
    assert "stale" in decision.explanation.lower()


def test_decide_insufficient_when_missing_it_load():
    decision = decide(
        _constraints(),
        FacilityMeasurements(cooling_load_kw=200.0),
        ProposedChange(),
        _eligible(),
    )
    assert decision.status == DecisionStatus.INSUFFICIENT_DATA
    assert "it_load_kw" in decision.explanation


def test_decide_insufficient_when_missing_cooling_load():
    decision = decide(
        _constraints(),
        FacilityMeasurements(it_load_kw=500.0),
        ProposedChange(),
        _eligible(),
    )
    assert decision.status == DecisionStatus.INSUFFICIENT_DATA
    assert "cooling_load_kw" in decision.explanation


def test_decide_does_not_silently_assume_zero():
    """A missing measurement must never be treated as 0."""
    decision = decide(
        _constraints(),
        FacilityMeasurements(),  # both None
        ProposedChange(),
        _eligible(),
    )
    assert decision.status == DecisionStatus.INSUFFICIENT_DATA
    assert decision.predicted is None


def test_decide_warns_when_margin_tight():
    """PUE 1.45 vs limit 1.5 -> margin 0.05, ratio 0.033 < 0.10 -> warning."""
    decision = decide(
        _constraints(),
        FacilityMeasurements(it_load_kw=500.0, cooling_load_kw=225.0),
        ProposedChange(),
        _eligible(),
    )
    assert decision.status == DecisionStatus.APPROVE
    assert any("pue_ceiling" in w for w in decision.warnings)


def test_decide_no_warning_when_margin_comfortable():
    decision = decide(
        _constraints(),
        FacilityMeasurements(it_load_kw=500.0, cooling_load_kw=200.0),
        ProposedChange(),
        _eligible(),
    )
    assert decision.status == DecisionStatus.APPROVE
    # PUE = 1.4, limit = 1.5, margin = 0.1, ratio = 0.067 < 0.10 -> this DOES warn
    # So use a wider margin instead: below we add a relaxed max_pue
    relaxed = OperationalConstraints(
        electrical_capacity_kw=1000.0,
        cooling_capacity_kw=800.0,
        max_pue=2.0,
        safety_margin_pct=0.10,
    )
    decision2 = decide(
        relaxed,
        FacilityMeasurements(it_load_kw=500.0, cooling_load_kw=200.0),
        ProposedChange(),
        _eligible(),
    )
    assert decision2.warnings == []


def test_decide_deterministic_on_repeat():
    constraints = _constraints()
    measurements = FacilityMeasurements(it_load_kw=500.0, cooling_load_kw=200.0)
    change = ProposedChange(it_load_delta_kw=50.0, cooling_load_delta_kw=20.0)
    quality = _eligible()
    first = decide(constraints, measurements, change, quality)
    second = decide(constraints, measurements, change, quality)
    assert first.model_dump() == second.model_dump()


def test_decide_evidence_includes_all_inputs():
    decision = decide(
        _constraints(),
        FacilityMeasurements(it_load_kw=500.0, cooling_load_kw=200.0, pue=1.4),
        ProposedChange(it_load_delta_kw=50.0, description="Add 2 servers"),
        _eligible(),
    )
    evidence = decision.evidence
    assert evidence["inputs"]["it_load_kw"] == 500.0
    assert evidence["inputs"]["cooling_load_kw"] == 200.0
    assert evidence["inputs"]["pue"] == 1.4
    assert evidence["change"]["it_load_delta_kw"] == 50.0
    assert evidence["change"]["description"] == "Add 2 servers"
    assert evidence["constraints"]["electrical_capacity_kw"] == 1000.0
    assert evidence["quality"]["eligible"] is True


def test_decide_explanation_mentions_reject_reason():
    decision = decide(
        _constraints(),
        FacilityMeasurements(it_load_kw=850.0, cooling_load_kw=100.0),
        ProposedChange(it_load_delta_kw=100.0),
        _eligible(),
    )
    assert decision.status == DecisionStatus.REJECT
    assert "electrical_capacity" in decision.explanation
    assert "950" in decision.explanation
