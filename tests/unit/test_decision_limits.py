# Copyright 2026 Lightibulb

"""Unit tests for the configurable constraint engine."""

import pydantic
import pytest

from ecofac.decision.limits import (
    ConstraintConfig,
    ConstraintSeverity,
    CoolingLimits,
    ElectricalLimits,
    ReserveConfig,
    config_diff,
    config_snapshot,
    evaluate_all,
    evaluate_load,
)


def _config(
    e_cap: float = 1000.0,
    c_cap: float = 800.0,
    reserve: float = 0.10,
) -> ConstraintConfig:
    return ConstraintConfig(
        electrical=ElectricalLimits(capacity=e_cap, reserve_pct=reserve),
        cooling=CoolingLimits(capacity=c_cap, reserve_pct=reserve),
    )


# ---------------------------------------------------------------------------
# Schema validation
# ---------------------------------------------------------------------------

def test_severity_enum_values():
    assert ConstraintSeverity.OK.value == "OK"
    assert ConstraintSeverity.WARNING.value == "WARNING"
    assert ConstraintSeverity.RESERVE_VIOLATION.value == "RESERVE_VIOLATION"
    assert ConstraintSeverity.PHYSICAL_VIOLATION.value == "PHYSICAL_VIOLATION"


def test_electrical_limits_accepts_valid():
    e = ElectricalLimits(capacity=1000.0, reserve_pct=0.10)
    assert e.capacity == 1000.0
    assert e.reserve_pct == 0.10
    assert e.warning_threshold_pct == 0.10
    assert e.min_headroom == 0.0


def test_electrical_limits_rejects_negative_capacity():
    with pytest.raises(pydantic.ValidationError):
        ElectricalLimits(capacity=-100.0, reserve_pct=0.10)


def test_electrical_limits_rejects_zero_capacity():
    with pytest.raises(pydantic.ValidationError):
        ElectricalLimits(capacity=0.0, reserve_pct=0.10)


def test_electrical_limits_rejects_reserve_at_one():
    with pytest.raises(pydantic.ValidationError):
        ElectricalLimits(capacity=1000.0, reserve_pct=1.0)


def test_electrical_limits_rejects_negative_reserve():
    with pytest.raises(pydantic.ValidationError):
        ElectricalLimits(capacity=1000.0, reserve_pct=-0.1)


def test_electrical_limits_accepts_zero_reserve():
    e = ElectricalLimits(capacity=1000.0, reserve_pct=0.0)
    assert e.reserve_pct == 0.0


def test_electrical_limits_rejects_negative_min_headroom():
    with pytest.raises(pydantic.ValidationError):
        ElectricalLimits(
            capacity=1000.0, reserve_pct=0.10, min_headroom=-10.0
        )


def test_electrical_limits_rejects_warning_threshold_at_one():
    with pytest.raises(pydantic.ValidationError):
        ElectricalLimits(
            capacity=1000.0, reserve_pct=0.10, warning_threshold_pct=1.0
        )


def test_electrical_limits_frozen():
    e = ElectricalLimits(capacity=1000.0, reserve_pct=0.10)
    with pytest.raises(pydantic.ValidationError):
        e.capacity = 2000.0  # type: ignore[misc]


# ---------------------------------------------------------------------------
# evaluate_load severity branches
# ---------------------------------------------------------------------------

def test_evaluate_load_ok():
    config = ElectricalLimits(capacity=1000.0, reserve_pct=0.10)
    ev = evaluate_load(config, observed=500.0, name="elec")
    assert ev.severity == ConstraintSeverity.OK
    assert ev.permitted == 900.0
    assert ev.headroom_to_permitted == 400.0
    assert ev.headroom_to_physical == 500.0
    assert ev.margin_used_pct == 50.0
    assert ev.is_limiting is False


def test_evaluate_load_warning_margin():
    config = ElectricalLimits(
        capacity=1000.0, reserve_pct=0.10, warning_threshold_pct=0.10
    )
    ev = evaluate_load(config, observed=850.0, name="elec")
    assert ev.severity == ConstraintSeverity.WARNING
    assert ev.headroom_to_permitted == 50.0


def test_evaluate_load_warning_min_headroom():
    config = ElectricalLimits(
        capacity=1000.0, reserve_pct=0.10, min_headroom=200.0
    )
    ok = evaluate_load(config, observed=500.0, name="elec")
    assert ok.severity == ConstraintSeverity.OK
    warn = evaluate_load(config, observed=750.0, name="elec")
    assert warn.severity == ConstraintSeverity.WARNING


def test_evaluate_load_reserve_violation():
    config = ElectricalLimits(capacity=1000.0, reserve_pct=0.10)
    ev = evaluate_load(config, observed=950.0, name="elec")
    assert ev.severity == ConstraintSeverity.RESERVE_VIOLATION
    assert ev.headroom_to_permitted == -50.0
    assert ev.headroom_to_physical == 50.0


def test_evaluate_load_physical_violation():
    config = ElectricalLimits(capacity=1000.0, reserve_pct=0.10)
    ev = evaluate_load(config, observed=1050.0, name="elec")
    assert ev.severity == ConstraintSeverity.PHYSICAL_VIOLATION
    assert ev.headroom_to_physical == -50.0


def test_evaluate_load_boundary_at_permitted():
    config = ElectricalLimits(capacity=1000.0, reserve_pct=0.10)
    ev = evaluate_load(config, observed=900.0, name="elec")
    assert ev.severity == ConstraintSeverity.WARNING
    assert ev.headroom_to_permitted == 0.0


def test_evaluate_load_boundary_at_capacity_with_reserve():
    config = ElectricalLimits(capacity=1000.0, reserve_pct=0.10)
    ev = evaluate_load(config, observed=1000.0, name="elec")
    assert ev.severity == ConstraintSeverity.RESERVE_VIOLATION


def test_evaluate_load_boundary_at_capacity_no_reserve():
    config = ElectricalLimits(capacity=1000.0, reserve_pct=0.0)
    ev = evaluate_load(config, observed=1000.0, name="elec")
    assert ev.severity == ConstraintSeverity.WARNING
    assert ev.permitted == 1000.0


def test_evaluate_load_rejects_negative_observed():
    config = ElectricalLimits(capacity=1000.0, reserve_pct=0.10)
    with pytest.raises(ValueError, match="observed must be >= 0"):
        evaluate_load(config, observed=-1.0, name="elec")


def test_evaluate_load_rejects_nan():
    config = ElectricalLimits(capacity=1000.0, reserve_pct=0.10)
    with pytest.raises(ValueError, match="observed must be finite"):
        evaluate_load(config, observed=float("nan"), name="elec")


def test_evaluate_load_rejects_inf():
    config = ElectricalLimits(capacity=1000.0, reserve_pct=0.10)
    with pytest.raises(ValueError, match="observed must be finite"):
        evaluate_load(config, observed=float("inf"), name="elec")


# ---------------------------------------------------------------------------
# evaluate_all aggregation
# ---------------------------------------------------------------------------

def test_evaluate_all_ok():
    cfg = _config()
    summary = evaluate_all(
        cfg, observed_electrical=500.0, observed_cooling=400.0
    )
    assert summary.overall_severity == ConstraintSeverity.OK
    assert summary.violations == []
    assert summary.warnings == []
    # Electrical headroom 400, cooling headroom 320 -> cooling is limiting
    assert summary.limiting_factor == "cooling"


def test_evaluate_all_exactly_one_limiting():
    cfg = _config()
    summary = evaluate_all(
        cfg, observed_electrical=500.0, observed_cooling=400.0
    )
    limitings = [e for e in summary.evaluations if e.is_limiting]
    assert len(limitings) == 1


def test_evaluate_all_electrical_violation():
    cfg = _config()
    summary = evaluate_all(
        cfg, observed_electrical=950.0, observed_cooling=400.0
    )
    assert summary.overall_severity == ConstraintSeverity.RESERVE_VIOLATION
    assert "electrical" in summary.violations
    assert summary.limiting_factor == "electrical"


def test_evaluate_all_cooling_physical_violation():
    cfg = _config()
    summary = evaluate_all(
        cfg, observed_electrical=500.0, observed_cooling=850.0
    )
    assert summary.overall_severity == ConstraintSeverity.PHYSICAL_VIOLATION
    assert "cooling" in summary.violations
    assert summary.limiting_factor == "cooling"


def test_evaluate_all_multiple_violations():
    cfg = _config()
    summary = evaluate_all(
        cfg, observed_electrical=1050.0, observed_cooling=900.0
    )
    assert "electrical" in summary.violations
    assert "cooling" in summary.violations
    # Electrical headroom -50, cooling headroom -100 -> cooling is worse
    assert summary.limiting_factor == "cooling"


def test_evaluate_all_warnings_both():
    cfg = _config()
    summary = evaluate_all(
        cfg, observed_electrical=850.0, observed_cooling=700.0
    )
    assert summary.overall_severity == ConstraintSeverity.WARNING
    assert "electrical" in summary.warnings
    assert "cooling" in summary.warnings
    assert summary.violations == []
    # Electrical headroom 50, cooling headroom 20 -> cooling tighter
    assert summary.limiting_factor == "cooling"


def test_evaluate_all_worst_wins():
    """When one passes and the other violates, the violation is the severity."""
    cfg = _config()
    summary = evaluate_all(
        cfg, observed_electrical=200.0, observed_cooling=950.0
    )
    assert summary.overall_severity == ConstraintSeverity.PHYSICAL_VIOLATION
    assert summary.violations == ["cooling"]
    assert summary.warnings == []


# ---------------------------------------------------------------------------
# Configuration helpers
# ---------------------------------------------------------------------------

def test_config_snapshot():
    cfg = _config()
    snap = config_snapshot(cfg)
    assert snap["electrical"]["capacity"] == 1000.0
    assert snap["cooling"]["capacity"] == 800.0
    assert snap["electrical"]["reserve_pct"] == 0.10


def test_config_diff_identical():
    cfg = _config()
    assert config_diff(cfg, cfg) == []


def test_config_diff_capacity_change():
    old = _config(e_cap=1000.0)
    new = _config(e_cap=1200.0)
    diffs = config_diff(old, new)
    assert any("electrical.capacity" in d for d in diffs)
    assert any("1000" in d and "1200" in d for d in diffs)


def test_config_diff_reserve_change():
    old = _config(reserve=0.10)
    new = _config(reserve=0.15)
    diffs = config_diff(old, new)
    assert any("electrical.reserve_pct" in d for d in diffs)
    assert any("cooling.reserve_pct" in d for d in diffs)


def test_config_diff_no_duplicate_entries():
    old = _config()
    new = _config(e_cap=1200.0, c_cap=1000.0)
    diffs = config_diff(old, new)
    assert len(diffs) == len(set(diffs))


# ---------------------------------------------------------------------------
# Extensibility
# ---------------------------------------------------------------------------

def test_future_constraint_subclass():
    """Any ReserveConfig subclass works with evaluate_load."""

    class TemperatureLimits(ReserveConfig):
        """Future temperature constraint."""

    temp = TemperatureLimits(capacity=30.0, reserve_pct=0.20)
    ev = evaluate_load(temp, observed=20.0, name="intake_temp")
    assert ev.severity == ConstraintSeverity.OK
    assert ev.permitted == 24.0
    assert ev.headroom_to_permitted == 4.0
