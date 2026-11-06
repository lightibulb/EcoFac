"""P0-1 spec: sensitivity statuses come from the state machine.

The per-decision sensitivity artifact records a status for each
tolerance-band edge. Those statuses must be produced by
``state_machine.evaluate_decision`` -- the same evaluator that produces
the primary verdict -- never by an inline re-implementation of
constraint thresholds. Otherwise the artifact can silently drift from
the verdict it claims to describe.

Earlier architecture analysis flagged this as a divergence risk because
``ARCHITECTURE.json`` showed no edge from ``_compute_sensitivity`` to
``evaluate_decision``. The edge was present in code; the graph does not
trace calls made from inside nested closures (``_status_it`` etc.). The
refactor in ``transparent.py`` extracts ``_sensitivity_status`` so the
delegation is one module-level function and the graph can see it.

IMPORTANT: import models from ``ecofac.decision.*`` -- never from
``src.ecofac.decision.*``. ``transparent.py`` declares its pydantic
fields against the ``ecofac.*`` class objects. Importing from ``src.*``
loads a second copy of each class, and pydantic compares by identity,
so it rejects an instance of the "wrong copy" with a confusing
``Input should be a valid dictionary or instance of X`` error even
though the input *is* an X. Every existing test in the repo uses the
``ecofac.*`` prefix; this file must too.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import ValidationError

from ecofac.decision import state_machine, transparent
from ecofac.decision.limits import (
    ConstraintConfig,
    CoolingLimits,
    ElectricalLimits,
)
from ecofac.decision.measurements import MeasurementSource, RichMeasurement
from ecofac.decision.transparent import (
    DecisionMode,
    TransparentDecisionRequest,
    decide_transparently,
)

if TYPE_CHECKING:
    from ecofac.decision.state_machine import (
        DecisionContext,
        FinalDecision,
    )

BAND_EDGES_PER_MEASUREMENT = 2  # lower + upper


def _measurement_with_tolerance(name: str, value: float, unit: str, tol: float) -> RichMeasurement:
    """Construct a RichMeasurement with a tolerance.

    Tries several plausible field names and requires that ``band()``
    returns a non-None tuple as proof the tolerance was accepted rather
    than silently ignored.
    """
    base = {
        "name": name,
        "value": value,
        "unit": unit,
        "source": MeasurementSource.MEASURED,
    }
    candidates = (
        {"tolerance_abs": tol},
        {"tolerance": tol},
        {"absolute_tolerance": tol},
        {"tolerance_absolute": tol},
        {"uncertainty": {"absolute": tol}},
        {"uncertainty": {"absolute_tolerance": tol}},
        {"uncertainty": {"tolerance_abs": tol}},
    )
    tried: list[list[str]] = []
    for kw in candidates:
        tried.append(list(kw.keys()))
        try:
            m = RichMeasurement(**base, **kw)
        except (TypeError, ValidationError):
            continue
        if m.band() is not None:
            return m
    msg = (
        "Could not construct RichMeasurement with a tolerance that yields "
        "a non-None band(). Please share "
        "src/ecofac/decision/measurements.py so the correct field name can "
        f"be used. Tried: {tried}"
    )
    raise AssertionError(msg)


def _base_request() -> TransparentDecisionRequest:
    """A request with all three swept measurements: IT, cooling, PUE."""
    return TransparentDecisionRequest(
        config=ConstraintConfig(
            electrical=ElectricalLimits(capacity=200.0, reserve_pct=0.1),
            cooling=CoolingLimits(capacity=250.0, reserve_pct=0.1),
        ),
        config_version="test-p0-1",
        it_load=_measurement_with_tolerance("it_load", 80.0, "kW", 2.0),
        cooling_load=_measurement_with_tolerance("cooling_load", 90.0, "kW", 2.0),
        pue=_measurement_with_tolerance("pue", 1.4, "dimensionless", 0.05),
        quality_eligible=True,
        mode=DecisionMode.HYPOTHETICAL,
    )


def _swept_bands(request: TransparentDecisionRequest) -> int:
    measurements = (request.it_load, request.cooling_load, request.pue)
    return sum(1 for m in measurements if m is not None and m.band() is not None)


def test_sensitivity_delegates_to_state_machine(monkeypatch) -> None:
    """Every band edge must invoke evaluate_decision."""
    request = _base_request()
    n_bands = _swept_bands(request)
    assert n_bands > 0, "test request must have at least one tolerance band"

    real = state_machine.evaluate_decision
    calls: list[DecisionContext] = []

    def counting_spy(ctx: DecisionContext) -> FinalDecision:
        calls.append(ctx)
        return real(ctx)

    monkeypatch.setattr(transparent, "evaluate_decision", counting_spy)
    decide_transparently(request)

    expected_min = 1 + BAND_EDGES_PER_MEASUREMENT * n_bands
    assert len(calls) >= expected_min, (
        f"expected >= {expected_min} evaluate_decision calls "
        f"(1 base + {BAND_EDGES_PER_MEASUREMENT} per band edge for "
        f"{n_bands} bands), got {len(calls)}. "
        "Sensitivity must delegate to the state machine."
    )


def test_sensitivity_statuses_are_produced_by_state_machine(
    monkeypatch,
) -> None:
    """Every recorded status must have been produced by evaluate_decision."""
    request = _base_request()

    real = state_machine.evaluate_decision
    produced: set[str] = set()

    def recording_spy(ctx: DecisionContext) -> FinalDecision:
        result = real(ctx)
        produced.add(result.status.value)
        return result

    monkeypatch.setattr(transparent, "evaluate_decision", recording_spy)
    result = decide_transparently(request)

    for sens in result.sensitivity:
        for recorded in (sens.status_at_lower, sens.status_at_upper):
            if recorded is None:
                continue
            assert recorded in produced, (
                f"{sens.measurement_name}: recorded status {recorded!r} "
                "was not produced by evaluate_decision"
            )


def test_sensitivity_baseline_is_physical_verdict() -> None:
    """In HYPOTHETICAL mode, sensitivity baseline == decision status.

    Operational mode may downgrade the verdict after sensitivity is
    computed, so this invariant is specific to HYPOTHETICAL.
    """
    request = _base_request()
    result = decide_transparently(request)

    for sens in result.sensitivity:
        assert sens.baseline_status == result.status, (
            f"{sens.measurement_name}: sensitivity baseline "
            f"{sens.baseline_status!r} != decision status {result.status!r}"
        )


def test_sensitivity_reproducible() -> None:
    """Repeating the same request produces an identical sensitivity artifact."""
    request = _base_request()
    a = decide_transparently(request)
    b = decide_transparently(request)
    assert a.sensitivity == b.sensitivity
