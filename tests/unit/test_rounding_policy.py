"""Tests for the rounding policy in decision.calculations.

The engineering calculations round output to 4 decimal places for stable
JSON. The canonical safety path (decision.limits.evaluate_all, driven by
decision.transparent) consumes *unrounded* floats via facility_power_kw
and predicted_load_kw. These tests lock in the separation so a future
change cannot silently route display-rounded values into the safety path
and hide sub-1e-4 kW overloads.
"""

import math

from ecofac.decision.calculations import (
    ElectricalCapacity,
    PUEResult,
    PUESource,
    calculate_electrical,
    facility_power_kw,
)
from ecofac.decision.limits import (
    ConstraintConfig,
    ConstraintSeverity,
    CoolingLimits,
    ElectricalLimits,
    evaluate_all,
)


def _measured_pue(value: float = 1.4) -> PUEResult:
    return PUEResult(value=value, source=PUESource.MEASURED)


def _electrical_capacity() -> ElectricalCapacity:
    return ElectricalCapacity(capacity_kw=1000.0, safety_margin_pct=0.10)


def test_facility_power_kw_returns_raw_float() -> None:
    """facility_power_kw must NOT round; it feeds the safety path."""
    assert facility_power_kw(1e-7, 1.0) == 1e-7
    assert math.isclose(facility_power_kw(0.5, 1.4), 0.5 * 1.4)


def test_calculate_electrical_state_is_rounded_for_display() -> None:
    """ElectricalState fields are rounded to 4 dp — display only."""
    state = calculate_electrical(1e-5, _measured_pue(1.0), _electrical_capacity())
    # 1e-5 rounds to 0.0 at 4 dp
    assert state.facility_power_kw == 0.0
    assert state.it_load_kw == 0.0


def test_safety_path_uses_raw_not_rounded_state() -> None:
    """evaluate_all compares raw observed, not rounded display values.

    If a future change wires calculate_electrical().facility_power_kw into
    the constraint evaluator, a 1e-6 kW overload would round to the
    permitted value and be classified as WARNING instead of
    PHYSICAL_VIOLATION. This test locks in the correct behavior.
    """
    config = ConstraintConfig(
        electrical=ElectricalLimits(capacity=1.0, reserve_pct=0.0),
        cooling=CoolingLimits(capacity=1.0, reserve_pct=0.0),
    )

    # Raw: 1.0 + 1e-6 > capacity 1.0 -> PHYSICAL_VIOLATION
    raw_observed = 1.0 + 1e-6
    raw_summary = evaluate_all(
        config, observed_electrical=raw_observed, observed_cooling=0.5
    )
    raw_sev = next(
        e.severity for e in raw_summary.evaluations if e.name == "electrical"
    )
    assert raw_sev == ConstraintSeverity.PHYSICAL_VIOLATION, (
        f"raw observed {raw_observed} > capacity 1.0 must be "
        f"PHYSICAL_VIOLATION, got {raw_sev}"
    )

    # Rounded (4 dp): 1.000001 -> 1.0 -> WARNING (zero headroom at capacity)
    rounded_observed = round(raw_observed, 4)
    rounded_summary = evaluate_all(
        config, observed_electrical=rounded_observed, observed_cooling=0.5
    )
    rounded_sev = next(
        e.severity for e in rounded_summary.evaluations if e.name == "electrical"
    )
    assert rounded_sev == ConstraintSeverity.WARNING, (
        f"rounded {rounded_observed} should be WARNING (zero headroom), "
        f"got {rounded_sev}. If this fails, the safety path may be "
        f"consuming rounded values — a P0 regression."
    )
