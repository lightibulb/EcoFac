# Copyright 2026 Lightibulb
"""Unit tests for uncertainty assessment."""

from ecofac.decision.measurements import (
    MeasurementSource,
    RichMeasurement,
    UncertaintyMetadata,
)
from ecofac.decision.uncertainty import (
    MODEL_LIMITATIONS,
    DataQualityLevel,
    ModelUncertaintyLevel,
    assess_uncertainty,
)


def _measured(name: str, value: float, tol: float | None = None) -> RichMeasurement:
    return RichMeasurement(
        name=name,
        value=value,
        unit="kW",
        source=MeasurementSource.MEASURED,
        uncertainty=UncertaintyMetadata(absolute_tolerance=tol) if tol else None,
    )


def _assumed(name: str, value: float) -> RichMeasurement:
    return RichMeasurement(
        name=name,
        value=value,
        unit="dimensionless",
        source=MeasurementSource.ASSUMED,
    )


def test_all_measured_no_tolerance_is_unknown():
    """Measured provenance does not imply accuracy; without tolerance -> UNKNOWN."""
    report = assess_uncertainty([_measured("a", 1.0), _measured("b", 2.0)])
    assert report.data_quality_level == DataQualityLevel.OK
    assert report.model_uncertainty_level == ModelUncertaintyLevel.UNKNOWN


def test_all_measured_with_tolerance_is_moderate():
    report = assess_uncertainty([_measured("a", 1.0, tol=0.1), _measured("b", 2.0, tol=0.2)])
    assert report.data_quality_level == DataQualityLevel.OK
    assert report.model_uncertainty_level == ModelUncertaintyLevel.MODERATE


def test_partial_tolerance_mixed_with_assumed_is_moderate():
    report = assess_uncertainty([_measured("a", 1.0, tol=0.1), _assumed("pue", 1.4)])
    assert report.model_uncertainty_level == ModelUncertaintyLevel.MODERATE
    assert "pue" in report.estimated_parameters


def test_quality_flags_degrade():
    report = assess_uncertainty([_measured("a", 1.0)], quality_flags=["missing_data"])
    assert report.data_quality_level == DataQualityLevel.DEGRADED


def test_stale_is_unusable():
    report = assess_uncertainty([_measured("a", 1.0)], quality_stale=True)
    assert report.data_quality_level == DataQualityLevel.UNUSABLE


def test_missing_measurement_is_unusable():
    missing = RichMeasurement(name="it", value=None, unit="kW", source=MeasurementSource.MISSING)
    report = assess_uncertainty([missing])
    assert report.data_quality_level == DataQualityLevel.UNUSABLE
    assert "it" in report.missing_measurements


def test_assumed_parameter_raises_model_level():
    report = assess_uncertainty([_assumed("pue", 1.4)])
    assert report.model_uncertainty_level == ModelUncertaintyLevel.UNKNOWN


def test_model_limitations_always_listed():
    report = assess_uncertainty([_measured("a", 1.0)])
    for limit in MODEL_LIMITATIONS:
        assert limit in report.model_limitations


def test_tolerance_used_lists_named_measurements():
    report = assess_uncertainty([_measured("it", 100.0, tol=5.0), _measured("cool", 50.0)])
    assert report.tolerance_used == ["it"]


def test_quality_flags_surfaced():
    report = assess_uncertainty(
        [_measured("a", 1.0, tol=0.1)],
        quality_flags=["flag_a", "flag_b"],
    )
    assert report.quality_flags == ["flag_a", "flag_b"]
