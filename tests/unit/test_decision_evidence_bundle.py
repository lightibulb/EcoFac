# Copyright 2026 Lightibulb
"""Tests for the decision evidence bundle."""

from ecofac.decision.evidence import (
    EvidenceInputs,
    build_evidence_bundle,
)
from ecofac.decision.limits import ConstraintSeverity, LimitEvaluation
from ecofac.decision.uncertainty import (
    DataQualityLevel,
    ModelUncertaintyLevel,
    UncertaintyReport,
)


def _eval(
    name: str,
    severity: ConstraintSeverity,
    observed: float = 100.0,
    permitted: float = 200.0,
) -> LimitEvaluation:
    return LimitEvaluation(
        name=name,
        observed=observed,
        physical_capacity=250.0,
        permitted=permitted,
        headroom_to_permitted=permitted - observed,
        headroom_to_physical=250.0 - observed,
        margin_used_pct=40.0,
        severity=severity,
        is_limiting=False,
        reason=f"{name} reason",
    )


def _report() -> UncertaintyReport:
    return UncertaintyReport(
        missing_measurements=[],
        stale_measurements=[],
        low_quality_measurements=[],
        estimated_parameters=[],
        assumptions=[],
        model_limitations=[],
        data_quality_level=DataQualityLevel.OK,
        model_uncertainty_level=ModelUncertaintyLevel.LOW,
        rationale=[],
        tolerance_used=[],
    )


def test_pass_warn_fail_split():
    bundle = build_evidence_bundle(
        EvidenceInputs(
            input_references=[],
            constraints_snapshot={},
            calculation_inputs={},
            calculation_outputs={},
            formulas={},
            assumptions=[],
            evaluated_constraints=[
                _eval("a", ConstraintSeverity.OK),
                _eval("b", ConstraintSeverity.WARNING),
                _eval("c", ConstraintSeverity.RESERVE_VIOLATION),
                _eval("d", ConstraintSeverity.PHYSICAL_VIOLATION),
            ],
            uncertainty_report=_report(),
            sensitivity=[],
            final_status="REJECT",
            final_reason="c and d failed",
            engine_version="1.0",
            config_version="cfg-1",
            decision_timestamp="2026-01-01T00:00:00Z",
        )
    )
    assert len(bundle.passed_constraints) == 1
    assert len(bundle.warning_constraints) == 1
    assert len(bundle.failed_constraints) == 2
    assert bundle.final_status == "REJECT"
    assert bundle.engine_version == "1.0"
    assert bundle.config_version == "cfg-1"


def test_constraint_record_maps_margin():
    bundle = build_evidence_bundle(
        EvidenceInputs(
            input_references=[],
            constraints_snapshot={},
            calculation_inputs={},
            calculation_outputs={},
            formulas={},
            assumptions=[],
            evaluated_constraints=[_eval("elec", ConstraintSeverity.OK, 100.0, 200.0)],
            uncertainty_report=_report(),
            sensitivity=[],
            final_status="APPROVE",
            final_reason="ok",
            engine_version="1.0",
            config_version="cfg-1",
            decision_timestamp="2026-01-01T00:00:00Z",
        )
    )
    record = bundle.passed_constraints[0]
    assert record.observed == 100.0
    assert record.limit == 200.0
    assert record.margin == 100.0
    assert record.status == "OK"
