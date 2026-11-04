# Copyright 2026 Lightibulb
"""Bottleneck, comparison, and sensitivity analysis tests."""

import pytest

from ecofac.decision.limits import (
    ConstraintConfig,
    CoolingLimits,
    ElectricalLimits,
)
from ecofac.decision.measurements import (
    MeasurementSource,
    RichMeasurement,
)
from ecofac.decision.scenario_analysis import (
    analyze_sensitivity,
    compare_scenarios,
    detect_bottlenecks,
)
from ecofac.decision.transparent import (
    DecisionMode,
    TransparentDecisionRequest,
    build_config_payload,
    build_input_payload,
    decide_transparently,
)


def _make_record(
    *,
    it: float = 400.0,
    cool: float = 300.0,
    pue: float = 1.0,
    cap: float = 1000.0,
    cool_cap: float = 800.0,
    reserve: float = 0.10,
    cool_reserve: float = 0.10,
    proposed_it: float = 0.0,
    proposed_cool: float = 0.0,
    config_version: str = "cfg-1",
    record_id: str | None = None,
) -> dict:
    req = TransparentDecisionRequest(
        config=ConstraintConfig(
            electrical=ElectricalLimits(
                capacity=cap, reserve_pct=reserve,
            ),
            cooling=CoolingLimits(
                capacity=cool_cap, reserve_pct=cool_reserve,
            ),
        ),
        config_version=config_version,
        it_load=RichMeasurement(
            name="it_load", value=it, unit="kW",
            source=MeasurementSource.ASSUMED,
        ),
        cooling_load=RichMeasurement(
            name="cooling_load", value=cool, unit="kW",
            source=MeasurementSource.ASSUMED,
        ),
        pue=RichMeasurement(
            name="pue", value=pue, unit="dimensionless",
            source=MeasurementSource.ASSUMED,
        ),
        proposed_it_delta=proposed_it,
        proposed_cooling_delta=proposed_cool,
        quality_eligible=True,
        mode=DecisionMode.HYPOTHETICAL,
    )
    result = decide_transparently(req)
    rid = record_id or f"s-{it}-{pue}-{cap}-{reserve}"
    return {
        "id": rid,
        "name": f"scenario-{rid}",
        "created_at": "2026-01-01T00:00:00Z",
        "status": result.status,
        "precedence": result.precedence,
        "engine_version": result.engine_version,
        "config_version": config_version,
        "decision_id": None,
        "input_fingerprint": result.fingerprint.input_fingerprint,
        "config_fingerprint": result.fingerprint.config_fingerprint,
        "inputs": build_input_payload(req),
        "config": build_config_payload(req),
        "result": result.model_dump(mode="json"),
    }


def test_bottleneck_invalid_proposal_no_data():
    record = _make_record(it=400.0, proposed_it=-500.0)
    report = detect_bottlenecks(record)
    assert report.has_enough_information is False
    assert report.most_restrictive_constraint is None
    assert report.findings == []
    assert "invalid" in (report.reason_if_insufficient or "").lower()


def test_bottleneck_approve_clean_no_violations():
    # Default scenario: electrical headroom 500 kW (900 - 400),
    # cooling headroom 420 kW (720 - 300). Cooling is tighter.
    record = _make_record(it=400.0, cap=1000.0, reserve=0.10)
    report = detect_bottlenecks(record)
    assert report.has_enough_information is True
    assert report.most_restrictive_constraint == "cooling"
    categories = [f.category for f in report.findings]
    assert not any("over_" in c for c in categories)


def test_bottleneck_reject_physical():
    record = _make_record(it=1100.0, pue=1.4, cap=1000.0)
    assert record["precedence"] == "hard_limit_violation"
    report = detect_bottlenecks(record)
    categories = [f.category for f in report.findings]
    assert "electrical_over_physical" in categories


def test_bottleneck_reject_reserve():
    record = _make_record(it=950.0, pue=1.0, cap=1000.0)
    assert record["precedence"] == "reserve_violation"
    report = detect_bottlenecks(record)
    categories = [f.category for f in report.findings]
    assert "electrical_over_permitted" in categories


def test_bottleneck_most_restrictive_electrical():
    record = _make_record(
        it=850.0, pue=1.0, cap=1000.0, reserve=0.10,
        cool=100.0, cool_cap=800.0, cool_reserve=0.10,
    )
    report = detect_bottlenecks(record)
    assert report.most_restrictive_constraint == "electrical"


def test_bottleneck_approaching_permitted():
    record = _make_record(it=850.0, pue=1.0, cap=1000.0, reserve=0.10)
    report = detect_bottlenecks(record)
    categories = [f.category for f in report.findings]
    assert "electrical_approaching_permitted" in categories


def test_bottleneck_insufficient_margin_approve():
    record = _make_record(it=800.0, pue=1.0, cap=1000.0, reserve=0.10)
    assert record["status"] == "APPROVE"
    report = detect_bottlenecks(record)
    categories = [f.category for f in report.findings]
    assert "electrical_insufficient_margin" in categories


def test_comparison_requires_two_scenarios():
    record = _make_record()
    with pytest.raises(ValueError, match="At least 2"):
        compare_scenarios([record])


def test_comparison_identical_inputs():
    a = _make_record(record_id="a")
    b = _make_record(record_id="b")
    result = compare_scenarios([a, b])
    assert len(result.scenarios) == 2
    assert result.mixed_config_versions is False
    for diff in result.input_differences:
        assert diff.differs is False


def test_comparison_identifies_it_load_difference():
    a = _make_record(it=400.0, record_id="a")
    b = _make_record(it=500.0, record_id="b")
    result = compare_scenarios([a, b])
    by_field = {d.field_name: d for d in result.input_differences}
    assert by_field["it_load_kw"].differs is True
    assert by_field["it_load_kw"].values == [400.0, 500.0]


def test_comparison_identifies_config_difference():
    a = _make_record(cap=1000.0, record_id="a")
    b = _make_record(cap=800.0, record_id="b")
    result = compare_scenarios([a, b])
    by_field = {d.field_name: d for d in result.input_differences}
    assert by_field["electrical_capacity_kw"].differs is True


def test_comparison_flags_mixed_config_versions():
    a = _make_record(config_version="cfg-A", record_id="a")
    b = _make_record(config_version="cfg-B", record_id="b")
    result = compare_scenarios([a, b])
    assert result.mixed_config_versions is True
    assert set(result.config_versions) == {"cfg-A", "cfg-B"}


def test_comparison_explanation_non_empty():
    a = _make_record(it=400.0, record_id="a")
    b = _make_record(it=500.0, record_id="b")
    result = compare_scenarios([a, b])
    assert result.outcome_explanation
    assert "it_load_kw" in result.outcome_explanation


def test_comparison_snapshots_have_engineering():
    a = _make_record(it=400.0, record_id="a")
    b = _make_record(it=500.0, record_id="b")
    result = compare_scenarios([a, b])
    for snapshot in result.scenarios:
        assert snapshot.pue is not None
        assert snapshot.current_it_load_kw is not None
        assert snapshot.predicted_facility_power_kw is not None


def test_sensitivity_deterministic():
    record = _make_record(it=850.0, pue=1.0, cap=1000.0)
    a = analyze_sensitivity(record)
    b = analyze_sensitivity(record)
    assert a == b


def test_sensitivity_has_all_parameters():
    record = _make_record()
    result = analyze_sensitivity(record)
    names = {p.parameter for p in result.parameters}
    assert "it_load_kw" in names
    assert "pue" in names
    assert "electrical_capacity_kw" in names
    assert "cooling_capacity_kw" in names
    assert "electrical_reserve_pct" in names
    assert "cooling_reserve_pct" in names


def test_sensitivity_it_load_change_detected():
    record = _make_record(it=850.0, pue=1.0, cap=1000.0)
    result = analyze_sensitivity(record)
    it_param = next(
        p for p in result.parameters if p.parameter == "it_load_kw"
    )
    assert it_param.verdict_changed is True
    assert any(s.status == "REJECT" for s in it_param.steps)


def test_sensitivity_pue_change_detected():
    record = _make_record(it=850.0, pue=1.0, cap=1000.0)
    result = analyze_sensitivity(record)
    pue_param = next(p for p in result.parameters if p.parameter == "pue")
    assert pue_param.verdict_changed is True
    assert any(s.status == "REJECT" for s in pue_param.steps)


def test_sensitivity_reserve_sweep_additive():
    record = _make_record(
        it=850.0, pue=1.0, cap=1000.0, reserve=0.10
    )
    result = analyze_sensitivity(record)
    reserve_param = next(
        p for p in result.parameters
        if p.parameter == "electrical_reserve_pct"
    )
    assert reserve_param.baseline_value == 0.10
    step_values = [s.value for s in reserve_param.steps]
    assert 0.05 in step_values
    assert 0.15 in step_values


def test_sensitivity_pue_default_one_when_missing():
    record = _make_record(pue=1.0)
    result = analyze_sensitivity(record)
    pue_param = next(p for p in result.parameters if p.parameter == "pue")
    assert pue_param.baseline_value == 1.0


def test_sensitivity_each_sweep_has_expected_step_count():
    record = _make_record(it=400.0, pue=1.0, cap=1000.0)
    result = analyze_sensitivity(record)
    for param in result.parameters:
        assert len(param.steps) <= 5
        assert len(param.steps) >= 1
