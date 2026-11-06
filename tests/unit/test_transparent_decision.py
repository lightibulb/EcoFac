# Copyright 2026 Lightibulb
"""Unit tests for the transparent decision orchestrator."""

import pydantic
import pytest

from ecofac.decision.limits import (
    ConstraintConfig,
    CoolingLimits,
    ElectricalLimits,
)
from ecofac.decision.measurements import (
    MeasurementSource,
    RichMeasurement,
    UncertaintyMetadata,
)
from ecofac.decision.transparent import (
    TransparentDecisionRequest,
    decide_transparently,
)


def _config() -> ConstraintConfig:
    return ConstraintConfig(
        electrical=ElectricalLimits(capacity=1000.0, reserve_pct=0.10),
        cooling=CoolingLimits(capacity=800.0, reserve_pct=0.10),
    )


def _it_load(value: float = 400.0, tol: float | None = None) -> RichMeasurement:
    return RichMeasurement(
        name="it_load",
        value=value,
        unit="kW",
        source=MeasurementSource.MEASURED,
        uncertainty=UncertaintyMetadata(absolute_tolerance=tol) if tol else None,
        reference_id="reading-it-1",
        measurement_timestamp="2026-01-01T00:00:00Z",
    )


def _cooling(value: float = 300.0, tol: float | None = None) -> RichMeasurement:
    return RichMeasurement(
        name="cooling_load",
        value=value,
        unit="kW",
        source=MeasurementSource.MEASURED,
        uncertainty=UncertaintyMetadata(absolute_tolerance=tol) if tol else None,
    )


def test_approve_clean():
    req = TransparentDecisionRequest(
        config=_config(),
        config_version="cfg-1",
        it_load=_it_load(400.0),
        cooling_load=_cooling(300.0),
        quality_eligible=True,
        quality_stale=False,
    )
    result = decide_transparently(req)
    assert result.status == "APPROVE"
    assert result.precedence == "clean"
    assert result.evidence.final_status == "APPROVE"
    assert result.explanation["what_would_change_decision"] == []


def test_reject_physical_violation():
    req = TransparentDecisionRequest(
        config=_config(),
        config_version="cfg-1",
        it_load=_it_load(1100.0),
        cooling_load=_cooling(300.0),
        quality_eligible=True,
    )
    result = decide_transparently(req)
    assert result.status == "REJECT"
    assert result.precedence == "hard_limit_violation"
    assert len(result.evidence.failed_constraints) >= 1


def test_review_data_quality():
    req = TransparentDecisionRequest(
        config=_config(),
        config_version="cfg-1",
        it_load=_it_load(400.0),
        cooling_load=_cooling(300.0),
        quality_eligible=False,
        quality_flags=["missing_data"],
    )
    result = decide_transparently(req)
    assert result.status == "REVIEW"
    assert result.precedence == "data_quality_ineligible"
    hints = result.explanation["what_would_change_decision"]
    assert hints
    assert any("Resolve quality" in h or "Provide" in h for h in hints)


def test_review_near_threshold():
    req = TransparentDecisionRequest(
        config=_config(),
        config_version="cfg-1",
        it_load=_it_load(850.0),
        cooling_load=_cooling(300.0),
        quality_eligible=True,
    )
    result = decide_transparently(req)
    assert result.status == "REVIEW"
    assert result.precedence == "near_threshold"
    hints = result.explanation["what_would_change_decision"]
    assert any("headroom" in h.lower() or "capacity" in h.lower() for h in hints)


def test_missing_measurement_forces_review():
    missing_it = RichMeasurement(
        name="it_load", value=None, unit="kW", source=MeasurementSource.MISSING
    )
    req = TransparentDecisionRequest(
        config=_config(),
        config_version="cfg-1",
        it_load=missing_it,
        cooling_load=_cooling(300.0),
        quality_eligible=True,
    )
    result = decide_transparently(req)
    assert result.status == "REVIEW"
    assert "it_load" in result.uncertainty.missing_measurements
    hints = result.explanation["what_would_change_decision"]
    assert any("it_load" in h for h in hints)


def test_sensitivity_can_change_verdict():
    # Baseline 890 -> headroom 10 vs threshold 90 -> WARNING -> REVIEW.
    # Tolerance 20 -> band [870, 910]; 910 > permitted 900 -> RESERVE_VIOLATION.
    req = TransparentDecisionRequest(
        config=_config(),
        config_version="cfg-1",
        it_load=_it_load(890.0, tol=20.0),
        cooling_load=_cooling(300.0),
        quality_eligible=True,
    )
    result = decide_transparently(req)
    assert result.status == "REVIEW"
    sensitivity_it = [s for s in result.sensitivity if s.measurement_name == "it_load"]
    assert len(sensitivity_it) == 1
    assert sensitivity_it[0].could_change_verdict is True


def test_no_sensitivity_without_tolerance():
    req = TransparentDecisionRequest(
        config=_config(),
        config_version="cfg-1",
        it_load=_it_load(400.0),
        cooling_load=_cooling(300.0),
        quality_eligible=True,
    )
    result = decide_transparently(req)
    assert result.sensitivity == []


def test_evidence_preserves_input_references():
    req = TransparentDecisionRequest(
        config=_config(),
        config_version="cfg-1",
        it_load=_it_load(400.0),
        cooling_load=_cooling(300.0),
        quality_eligible=True,
    )
    result = decide_transparently(req)
    refs = {r.name: r for r in result.evidence.input_references}
    assert refs["it_load"].reference_id == "reading-it-1"
    assert refs["it_load"].value == 400.0


def test_reproducibility_fingerprint_stable():
    req = TransparentDecisionRequest(
        config=_config(),
        config_version="cfg-1",
        it_load=_it_load(400.0),
        cooling_load=_cooling(300.0),
        quality_eligible=True,
    )
    r1 = decide_transparently(req)
    r2 = decide_transparently(req)
    assert r1.fingerprint.input_fingerprint == r2.fingerprint.input_fingerprint
    assert r1.fingerprint.config_fingerprint == r2.fingerprint.config_fingerprint


def test_engine_and_config_versions_preserved():
    req = TransparentDecisionRequest(
        config=_config(),
        config_version="cfg-42",
        it_load=_it_load(400.0),
        cooling_load=_cooling(300.0),
        quality_eligible=True,
    )
    result = decide_transparently(req)
    assert result.config_version == "cfg-42"
    assert result.engine_version == "1.0"
    assert result.fingerprint.engine_version == "1.0"
    assert result.fingerprint.config_version == "cfg-42"


def test_frozen_request():
    req = TransparentDecisionRequest(
        config=_config(),
        config_version="cfg-1",
        it_load=_it_load(400.0),
        cooling_load=_cooling(300.0),
        quality_eligible=True,
    )
    with pytest.raises(pydantic.ValidationError):
        req.quality_eligible = False
