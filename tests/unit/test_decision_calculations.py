# Copyright 2026 Lightibulb

"""Unit tests for the engineering calculation layer."""

import pydantic
import pytest

from ecofac.decision.calculations import (
    CalculationReport,
    CoolingCapacity,
    ElectricalCapacity,
    FacilityState,
    LoadChange,
    PUEResult,
    PUESource,
    build_report,
    calculate_cooling,
    calculate_electrical,
    compute_current_state,
    compute_predicted_state,
)


def _electrical_capacity() -> ElectricalCapacity:
    return ElectricalCapacity(capacity_kw=1000.0, safety_margin_pct=0.10)


def _cooling_capacity() -> CoolingCapacity:
    return CoolingCapacity(capacity_kw=800.0, safety_margin_pct=0.10)


def _measured_pue(value: float = 1.4) -> PUEResult:
    return PUEResult(value=value, source=PUESource.MEASURED)


# ---------------------------------------------------------------------------
# Schema invariants
# ---------------------------------------------------------------------------


def test_pue_source_enum_values():
    assert PUESource.MEASURED.value == "MEASURED"
    assert PUESource.CONFIGURED.value == "CONFIGURED"
    assert PUESource.ASSUMED.value == "ASSUMED"


def test_pue_result_requires_positive_value():
    with pytest.raises(pydantic.ValidationError):
        PUEResult(value=0.0, source=PUESource.MEASURED)
    with pytest.raises(pydantic.ValidationError):
        PUEResult(value=-1.0, source=PUESource.MEASURED)


def test_electrical_capacity_rejects_zero():
    with pytest.raises(pydantic.ValidationError):
        ElectricalCapacity(capacity_kw=0.0, safety_margin_pct=0.10)


def test_electrical_capacity_rejects_negative():
    with pytest.raises(pydantic.ValidationError):
        ElectricalCapacity(capacity_kw=-100.0, safety_margin_pct=0.10)


def test_electrical_capacity_rejects_margin_at_one():
    with pytest.raises(pydantic.ValidationError):
        ElectricalCapacity(capacity_kw=1000.0, safety_margin_pct=1.0)


def test_cooling_capacity_rejects_non_positive_heat_factor():
    with pytest.raises(pydantic.ValidationError):
        CoolingCapacity(
            capacity_kw=800.0,
            safety_margin_pct=0.10,
            heat_conversion_factor=0.0,
        )


def test_cooling_capacity_default_heat_factor():
    c = CoolingCapacity(capacity_kw=800.0, safety_margin_pct=0.10)
    assert c.heat_conversion_factor == 1.0


# ---------------------------------------------------------------------------
# calculate_electrical
# ---------------------------------------------------------------------------


def test_electrical_basic():
    state = calculate_electrical(500.0, _measured_pue(1.4), _electrical_capacity())
    assert state.facility_power_kw == 700.0
    assert state.physical_headroom_kw == 300.0  # 1000 - 700
    assert state.safety_adjusted_headroom_kw == 200.0  # 900 - 700


def test_electrical_pue_one():
    state = calculate_electrical(500.0, _measured_pue(1.0), _electrical_capacity())
    assert state.facility_power_kw == 500.0
    assert state.physical_headroom_kw == 500.0
    assert state.safety_adjusted_headroom_kw == 400.0


def test_electrical_overload_negative_headroom():
    """Headroom is negative when facility power exceeds capacity."""
    state = calculate_electrical(1000.0, _measured_pue(1.5), _electrical_capacity())
    assert state.facility_power_kw == 1500.0
    assert state.physical_headroom_kw == -500.0


def test_electrical_pue_source_preserved():
    pue = PUEResult(value=1.4, source=PUESource.ASSUMED, notes="industry average")
    state = calculate_electrical(500.0, pue, _electrical_capacity())
    assert state.pue.source == PUESource.ASSUMED
    assert state.pue.notes == "industry average"


# ---------------------------------------------------------------------------
# calculate_cooling
# ---------------------------------------------------------------------------


def test_cooling_basic():
    state = calculate_cooling(500.0, _cooling_capacity())
    assert state.heat_load_kw == 500.0  # factor 1.0
    assert state.physical_headroom_kw == 300.0  # 800 - 500
    assert state.safety_adjusted_headroom_kw == 220.0  # 720 - 500


def test_cooling_heat_conversion_factor():
    capacity = CoolingCapacity(
        capacity_kw=800.0,
        safety_margin_pct=0.10,
        heat_conversion_factor=0.95,
    )
    state = calculate_cooling(500.0, capacity)
    assert state.heat_load_kw == 475.0
    assert state.heat_conversion_factor == 0.95


def test_cooling_overload_negative_headroom():
    state = calculate_cooling(900.0, _cooling_capacity())
    assert state.heat_load_kw == 900.0
    assert state.physical_headroom_kw == -100.0


# ---------------------------------------------------------------------------
# compute_current_state
# ---------------------------------------------------------------------------


def test_current_state_rejects_negative_it_load():
    with pytest.raises(ValueError, match="it_load_kw must be >= 0"):
        compute_current_state(
            -1.0,
            _measured_pue(),
            _electrical_capacity(),
            _cooling_capacity(),
        )


def test_current_state_zero_it_load():
    state = compute_current_state(
        0.0,
        _measured_pue(1.4),
        _electrical_capacity(),
        _cooling_capacity(),
    )
    assert state.electrical.facility_power_kw == 0.0
    assert state.cooling.heat_load_kw == 0.0


# ---------------------------------------------------------------------------
# compute_predicted_state
# ---------------------------------------------------------------------------


def _current_state() -> FacilityState:
    return compute_current_state(
        500.0,
        _measured_pue(1.4),
        _electrical_capacity(),
        _cooling_capacity(),
    )


def test_predicted_positive_delta():
    current = _current_state()
    predicted = compute_predicted_state(
        current,
        [LoadChange(it_load_delta_kw=50.0)],
        _measured_pue(1.4),
        _electrical_capacity(),
        _cooling_capacity(),
    )
    assert predicted.electrical.it_load_kw == 550.0
    assert predicted.electrical.facility_power_kw == 770.0
    assert predicted.cooling.heat_load_kw == 550.0


def test_predicted_negative_delta():
    current = _current_state()
    predicted = compute_predicted_state(
        current,
        [LoadChange(it_load_delta_kw=-50.0)],
        _measured_pue(1.4),
        _electrical_capacity(),
        _cooling_capacity(),
    )
    assert predicted.electrical.it_load_kw == 450.0
    assert predicted.cooling.heat_load_kw == 450.0


def test_predicted_multiple_changes_aggregate():
    current = _current_state()
    predicted = compute_predicted_state(
        current,
        [
            LoadChange(it_load_delta_kw=50.0),
            LoadChange(it_load_delta_kw=-20.0),
            LoadChange(it_load_delta_kw=30.0),
        ],
        _measured_pue(1.4),
        _electrical_capacity(),
        _cooling_capacity(),
    )
    assert predicted.electrical.it_load_kw == 560.0


def test_predicted_rejects_negative_it_load():
    current = _current_state()
    with pytest.raises(ValueError, match="drive IT load negative"):
        compute_predicted_state(
            current,
            [LoadChange(it_load_delta_kw=-600.0)],
            _measured_pue(1.4),
            _electrical_capacity(),
            _cooling_capacity(),
        )


# ---------------------------------------------------------------------------
# build_report
# ---------------------------------------------------------------------------


def test_build_report_shape():
    report = build_report(
        500.0,
        _measured_pue(1.4),
        [LoadChange(it_load_delta_kw=50.0, description="Add 2 servers")],
        _electrical_capacity(),
        _cooling_capacity(),
    )
    assert isinstance(report, CalculationReport)
    assert report.current.electrical.it_load_kw == 500.0
    assert report.predicted.electrical.it_load_kw == 550.0
    assert len(report.changes) == 1
    assert report.changes[0].description == "Add 2 servers"


def test_build_report_formula_log_complete():
    report = build_report(
        500.0,
        _measured_pue(),
        [],
        _electrical_capacity(),
        _cooling_capacity(),
    )
    for key in (
        "facility_power_kw",
        "physical_headroom_kw",
        "safety_adjusted_headroom_kw",
        "heat_load_kw",
        "predicted_it_load_kw",
    ):
        assert key in report.formula_log


def test_build_report_assumptions_mention_heat_approximation():
    report = build_report(
        500.0,
        _measured_pue(),
        [],
        _electrical_capacity(),
        _cooling_capacity(),
    )
    joined = " ".join(report.assumptions).lower()
    assert "heat" in joined
    assert "approximation" in joined or "simplification" in joined


def test_build_report_assumptions_mention_pue_source():
    report = build_report(
        500.0,
        PUEResult(value=1.4, source=PUESource.ASSUMED),
        [],
        _electrical_capacity(),
        _cooling_capacity(),
    )
    assert any("ASSUMED" in a for a in report.assumptions)


def test_build_report_deterministic():
    args = (
        500.0,
        _measured_pue(1.4),
        [LoadChange(it_load_delta_kw=50.0)],
        _electrical_capacity(),
        _cooling_capacity(),
    )
    first = build_report(*args)
    second = build_report(*args)
    assert first.model_dump() == second.model_dump()


def test_build_report_precision():
    """10.5 * 1.4 = 14.7 must survive rounding."""
    report = build_report(
        10.5,
        _measured_pue(1.4),
        [],
        ElectricalCapacity(capacity_kw=1000.0, safety_margin_pct=0.10),
        _cooling_capacity(),
    )
    assert report.current.electrical.facility_power_kw == 14.7


def test_build_report_pue_measured_source_preserved():
    report = build_report(
        500.0,
        _measured_pue(1.4),
        [],
        _electrical_capacity(),
        _cooling_capacity(),
    )
    assert report.current.electrical.pue.source == PUESource.MEASURED
    assert report.predicted.electrical.pue.source == PUESource.MEASURED


def test_build_report_configured_pue():
    report = build_report(
        500.0,
        PUEResult(value=1.5, source=PUESource.CONFIGURED, notes="design spec"),
        [],
        _electrical_capacity(),
        _cooling_capacity(),
    )
    assert report.current.electrical.pue.value == 1.5
    assert report.current.electrical.pue.source == PUESource.CONFIGURED
    assert report.current.electrical.facility_power_kw == 750.0
