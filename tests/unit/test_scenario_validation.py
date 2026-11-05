# Copyright 2026 Lightibulb
"""Scenario input validation: NaN, infinity, boundaries."""

import math

import pydantic
import pytest

from ecofac.decision.limits import (
    ConstraintConfig,
    CoolingLimits,
    ElectricalLimits,
)
from ecofac.decision.scenarios import validate_scenario_numerics


def _config_dump(
    *,
    elec_cap: float = 1000.0,
    cool_cap: float = 800.0,
    elec_res: float = 0.10,
    cool_res: float = 0.10,
) -> dict:
    return {
        "electrical": {
            "capacity": elec_cap,
            "reserve_pct": elec_res,
            "warning_threshold_pct": 0.10,
            "min_headroom": 0.0,
        },
        "cooling": {
            "capacity": cool_cap,
            "reserve_pct": cool_res,
            "warning_threshold_pct": 0.10,
            "min_headroom": 0.0,
        },
    }


def _default_kwargs(**overrides):
    base = {
        "it_load_kw": 400.0,
        "cooling_load_kw": 300.0,
        "pue": 1.4,
        "proposed_it_delta_kw": 0.0,
        "proposed_cooling_delta_kw": 0.0,
        "config_dump": _config_dump(),
    }
    base.update(overrides)
    return base


def test_valid_inputs_pass():
    validate_scenario_numerics(**_default_kwargs())


def test_nan_it_load_rejected():
    with pytest.raises(ValueError, match="it_load_kw must be finite"):
        validate_scenario_numerics(
            **_default_kwargs(it_load_kw=float("nan"))
        )


def test_inf_it_load_rejected():
    with pytest.raises(ValueError, match="it_load_kw must be finite"):
        validate_scenario_numerics(
            **_default_kwargs(it_load_kw=float("inf"))
        )


def test_nan_pue_rejected():
    with pytest.raises(ValueError, match="pue must be finite"):
        validate_scenario_numerics(**_default_kwargs(pue=float("nan")))


def test_inf_pue_rejected():
    with pytest.raises(ValueError, match="pue must be finite"):
        validate_scenario_numerics(**_default_kwargs(pue=float("inf")))


def test_nan_proposed_delta_rejected():
    with pytest.raises(
        ValueError, match="proposed_it_delta_kw must be finite"
    ):
        validate_scenario_numerics(
            **_default_kwargs(proposed_it_delta_kw=float("nan"))
        )


def test_inf_capacity_rejected():
    """Infinity in a config capacity is rejected at the Pydantic boundary."""
    with pytest.raises(pydantic.ValidationError):
        ConstraintConfig(
            electrical=ElectricalLimits(capacity=float("inf"), reserve_pct=0.10),
            cooling=CoolingLimits(capacity=800.0, reserve_pct=0.10),
        )


def test_nan_min_headroom_rejected():
    """min_headroom has only ge=0, so Pydantic accepts NaN; the scenario
    validator must catch it before the engine uses it."""
    dump = _config_dump()
    dump["cooling"]["min_headroom"] = float("nan")
    with pytest.raises(
        ValueError, match=r"config\.cooling\.min_headroom must be finite"
    ):
        validate_scenario_numerics(**_default_kwargs(config_dump=dump))


def test_zero_it_load_passes():
    validate_scenario_numerics(**_default_kwargs(it_load_kw=0.0))


def test_zero_proposed_delta_passes():
    validate_scenario_numerics(
        **_default_kwargs(proposed_it_delta_kw=0.0)
    )


def test_negative_proposed_delta_passes_validation():
    validate_scenario_numerics(
        **_default_kwargs(proposed_it_delta_kw=-100.0)
    )


def test_finite_boundary_values_pass():
    validate_scenario_numerics(
        **_default_kwargs(
            it_load_kw=0.0,
            cooling_load_kw=0.0,
            pue=1.0,
            proposed_it_delta_kw=0.0,
            proposed_cooling_delta_kw=0.0,
        )
    )


def test_math_isfinite_baseline():
    assert not math.isfinite(float("nan"))
    assert not math.isfinite(float("inf"))
    assert not math.isfinite(float("-inf"))
    assert math.isfinite(0.0)
    assert math.isfinite(1e9)
