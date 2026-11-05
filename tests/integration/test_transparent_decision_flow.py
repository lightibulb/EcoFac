# Copyright 2026 Lightibulb
"""End-to-end integration tests for transparent decisions."""

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
from ecofac.decision.reproducibility import verify_reproduction
from ecofac.decision.transparent import (
    TransparentDecisionRequest,
    build_config_payload,
    build_input_payload,
    decide_transparently,
)


def _config() -> ConstraintConfig:
    return ConstraintConfig(
        electrical=ElectricalLimits(capacity=1000.0, reserve_pct=0.10),
        cooling=CoolingLimits(capacity=800.0, reserve_pct=0.10),
    )


def _req(
    it_value: float,
    cool_value: float,
    *,
    it_tol: float | None = None,
    cool_tol: float | None = None,
    quality: bool = True,
) -> TransparentDecisionRequest:
    return TransparentDecisionRequest(
        config=_config(),
        config_version="cfg-1",
        it_load=RichMeasurement(
            name="it_load", value=it_value, unit="kW",
            source=MeasurementSource.MEASURED,
            uncertainty=(
                UncertaintyMetadata(absolute_tolerance=it_tol) if it_tol else None
            ),
            reference_id="reading-it-1",
            measurement_timestamp="2026-01-01T00:00:00Z",
        ),
        cooling_load=RichMeasurement(
            name="cooling_load", value=cool_value, unit="kW",
            source=MeasurementSource.MEASURED,
            uncertainty=(
                UncertaintyMetadata(absolute_tolerance=cool_tol)
                if cool_tol else None
            ),
        ),
        quality_eligible=quality,
    )


def test_full_flow_approve():
    result = decide_transparently(_req(400.0, 300.0))
    assert result.status == "APPROVE"
    assert result.evidence.passed_constraints
    assert result.explanation["limiting_constraint"] in ("electrical", "cooling")
    assert result.explanation["headroom_summary"] is not None


def test_full_flow_reject_and_reproduce():
    req = _req(1100.0, 300.0)
    result = decide_transparently(req)
    assert result.status == "REJECT"
    from ecofac.decision.transparent import pinned_request  # noqa: PLC0415
    pinned = pinned_request(req, result)
    verified = verify_reproduction(
        result.fingerprint,
        input_payload=build_input_payload(pinned),
        config_payload=build_config_payload(pinned),
    )
    assert verified is True


def test_full_flow_review_with_hints():
    result = decide_transparently(_req(850.0, 300.0))
    assert result.status == "REVIEW"
    hints = result.explanation["what_would_change_decision"]
    assert hints


def test_reproduction_fails_on_mismatched_inputs():
    req = _req(1100.0, 300.0)
    result = decide_transparently(req)
    tampered = req.it_load.model_dump(mode="json")
    tampered["value"] = 400.0
    payload = build_input_payload(req)
    payload["it_load"] = tampered
    verified = verify_reproduction(
        result.fingerprint,
        input_payload=payload,
        config_payload=build_config_payload(req),
    )
    assert verified is False
