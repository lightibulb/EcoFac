# Copyright 2026 Lightibulb

"""Schema validation for the decision engine."""

import pydantic
import pytest

from ecofac.decision.schemas import (
    ConstraintStatus,
    DataQualitySnapshot,
    DecisionStatus,
    FacilityMeasurements,
    OperationalConstraints,
    ProposedChange,
)


def test_constraints_accept_valid_inputs():
    c = OperationalConstraints(
        electrical_capacity_kw=1000.0,
        cooling_capacity_kw=800.0,
        max_pue=1.5,
        safety_margin_pct=0.10,
    )
    assert c.electrical_capacity_kw == 1000.0
    assert c.safety_margin_pct == 0.10


def test_constraints_reject_zero_capacity():
    with pytest.raises(pydantic.ValidationError):
        OperationalConstraints(
            electrical_capacity_kw=0.0,
            cooling_capacity_kw=800.0,
            max_pue=1.5,
            safety_margin_pct=0.10,
        )


def test_constraints_reject_negative_capacity():
    with pytest.raises(pydantic.ValidationError):
        OperationalConstraints(
            electrical_capacity_kw=-1.0,
            cooling_capacity_kw=800.0,
            max_pue=1.5,
            safety_margin_pct=0.10,
        )


def test_constraints_reject_zero_max_pue():
    with pytest.raises(pydantic.ValidationError):
        OperationalConstraints(
            electrical_capacity_kw=1000.0,
            cooling_capacity_kw=800.0,
            max_pue=0.0,
            safety_margin_pct=0.10,
        )


def test_constraints_reject_safety_margin_at_one():
    """safety_margin_pct == 1 would leave zero usable capacity."""
    with pytest.raises(pydantic.ValidationError):
        OperationalConstraints(
            electrical_capacity_kw=1000.0,
            cooling_capacity_kw=800.0,
            max_pue=1.5,
            safety_margin_pct=1.0,
        )


def test_constraints_reject_negative_safety_margin():
    with pytest.raises(pydantic.ValidationError):
        OperationalConstraints(
            electrical_capacity_kw=1000.0,
            cooling_capacity_kw=800.0,
            max_pue=1.5,
            safety_margin_pct=-0.1,
        )


def test_measurements_allow_missing_values():
    m = FacilityMeasurements()
    assert m.it_load_kw is None
    assert m.cooling_load_kw is None
    assert m.pue is None


def test_measurements_allow_partial_values():
    m = FacilityMeasurements(it_load_kw=500.0)
    assert m.it_load_kw == 500.0
    assert m.cooling_load_kw is None


def test_quality_requires_eligible_field():
    with pytest.raises(pydantic.ValidationError):
        DataQualitySnapshot()  # type: ignore[call-arg]


def test_quality_defaults_flags_and_stale():
    q = DataQualitySnapshot(eligible=True)
    assert q.flags == []
    assert q.stale is False


def test_proposed_change_defaults_to_zero_delta():
    c = ProposedChange()
    assert c.it_load_delta_kw == 0.0
    assert c.cooling_load_delta_kw == 0.0
    assert c.description is None


def test_decision_status_enum_values():
    assert DecisionStatus.APPROVE.value == "APPROVE"
    assert DecisionStatus.REJECT.value == "REJECT"
    assert DecisionStatus.INSUFFICIENT_DATA.value == "INSUFFICIENT_DATA"


def test_constraint_status_enum_values():
    assert ConstraintStatus.PASS.value == "PASS"
    assert ConstraintStatus.FAIL.value == "FAIL"
    assert ConstraintStatus.UNKNOWN.value == "UNKNOWN"
