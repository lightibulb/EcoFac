# Copyright 2026 Lightibulb

"""Unit tests for pure constraint functions."""

from ecofac.decision.constraints import (
    compute_predicted,
    evaluate_cooling,
    evaluate_electrical,
    evaluate_pue,
)
from ecofac.decision.schemas import (
    ConstraintStatus,
    FacilityMeasurements,
    OperationalConstraints,
    ProposedChange,
)


def _constraints() -> OperationalConstraints:
    return OperationalConstraints(
        electrical_capacity_kw=1000.0,
        cooling_capacity_kw=800.0,
        max_pue=1.5,
        safety_margin_pct=0.10,
    )


def test_compute_predicted_happy_path():
    predicted = compute_predicted(
        FacilityMeasurements(it_load_kw=500.0, cooling_load_kw=200.0),
        ProposedChange(it_load_delta_kw=50.0, cooling_load_delta_kw=20.0),
    )
    assert predicted is not None
    assert predicted.it_load_kw == 550.0
    assert predicted.cooling_load_kw == 220.0
    assert predicted.total_facility_kw == 770.0
    assert predicted.pue == 1.4


def test_compute_predicted_missing_it():
    assert (
        compute_predicted(
            FacilityMeasurements(cooling_load_kw=200.0),
            ProposedChange(),
        )
        is None
    )


def test_compute_predicted_missing_cooling():
    assert (
        compute_predicted(
            FacilityMeasurements(it_load_kw=500.0),
            ProposedChange(),
        )
        is None
    )


def test_compute_predicted_zero_it_returns_none():
    """A projected zero or negative IT load is meaningless for PUE."""
    assert (
        compute_predicted(
            FacilityMeasurements(it_load_kw=10.0, cooling_load_kw=5.0),
            ProposedChange(it_load_delta_kw=-20.0),
        )
        is None
    )


def test_evaluate_electrical_pass():
    predicted = compute_predicted(
        FacilityMeasurements(it_load_kw=500.0, cooling_load_kw=100.0),
        ProposedChange(),
    )
    assert predicted is not None
    ev = evaluate_electrical(predicted, _constraints())
    assert ev.status == ConstraintStatus.PASS
    assert ev.observed == 500.0
    assert ev.limit == 900.0
    assert ev.margin == 400.0


def test_evaluate_electrical_fail():
    predicted = compute_predicted(
        FacilityMeasurements(it_load_kw=850.0, cooling_load_kw=100.0),
        ProposedChange(it_load_delta_kw=100.0),
    )
    assert predicted is not None
    ev = evaluate_electrical(predicted, _constraints())
    assert ev.status == ConstraintStatus.FAIL
    assert ev.observed == 950.0
    assert ev.limit == 900.0
    assert ev.margin == -50.0


def test_evaluate_cooling_pass():
    predicted = compute_predicted(
        FacilityMeasurements(it_load_kw=500.0, cooling_load_kw=200.0),
        ProposedChange(),
    )
    assert predicted is not None
    ev = evaluate_cooling(predicted, _constraints())
    assert ev.status == ConstraintStatus.PASS
    assert ev.limit == 720.0


def test_evaluate_cooling_fail():
    predicted = compute_predicted(
        FacilityMeasurements(it_load_kw=500.0, cooling_load_kw=700.0),
        ProposedChange(cooling_load_delta_kw=50.0),
    )
    assert predicted is not None
    ev = evaluate_cooling(predicted, _constraints())
    assert ev.status == ConstraintStatus.FAIL


def test_evaluate_pue_pass():
    predicted = compute_predicted(
        FacilityMeasurements(it_load_kw=500.0, cooling_load_kw=200.0),
        ProposedChange(),
    )
    assert predicted is not None
    ev = evaluate_pue(predicted, _constraints())
    assert ev.status == ConstraintStatus.PASS
    assert ev.limit == 1.5
    assert ev.observed == 1.4


def test_evaluate_pue_fail():
    """Projected PUE above ceiling must fail."""
    predicted = compute_predicted(
        FacilityMeasurements(it_load_kw=500.0, cooling_load_kw=300.0),
        ProposedChange(it_load_delta_kw=50.0, cooling_load_delta_kw=50.0),
    )
    assert predicted is not None
    assert predicted.pue > 1.5
    ev = evaluate_pue(predicted, _constraints())
    assert ev.status == ConstraintStatus.FAIL
