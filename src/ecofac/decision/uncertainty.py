# Copyright 2026 Lightibulb
"""Transparent uncertainty assessment for facility decisions.

Distinguishes data-quality uncertainty (missing / stale / low-quality inputs)
from physical-model uncertainty (assumed or estimated parameters, documented
model limitations).

Emits categorical levels only. No calibrated percentage is ever claimed.
"""

from enum import StrEnum

import pydantic

from ecofac.decision.measurements import RichMeasurement


class DataQualityLevel(StrEnum):
    """Categorical indicator of input data reliability."""

    OK = "OK"
    DEGRADED = "DEGRADED"
    UNUSABLE = "UNUSABLE"


class ModelUncertaintyLevel(StrEnum):
    """Categorical indicator of physical-model uncertainty."""

    LOW = "LOW"
    MODERATE = "MODERATE"
    HIGH = "HIGH"
    UNKNOWN = "UNKNOWN"


class SensitivityResult(pydantic.BaseModel):
    """Whether a measurement's tolerance band could flip the verdict."""

    measurement_name: str
    baseline_status: str
    lower_bound: float | None
    upper_bound: float | None
    status_at_lower: str | None
    status_at_upper: str | None
    could_change_verdict: bool
    reason: str


class UncertaintyReport(pydantic.BaseModel):
    """Complete uncertainty picture for one decision."""

    missing_measurements: list[str]
    stale_measurements: list[str]
    low_quality_measurements: list[str]
    estimated_parameters: list[str]
    assumptions: list[str]
    model_limitations: list[str]
    data_quality_level: DataQualityLevel
    model_uncertainty_level: ModelUncertaintyLevel
    rationale: list[str]
    tolerance_used: list[str]
    quality_flags: list[str] = pydantic.Field(default_factory=list)


# Documented model limitations, referenced by every decision.
MODEL_LIMITATIONS: list[str] = [
    "IT electrical load is treated as approximate heat load (factor 1.0).",
    "PUE provenance may be MEASURED, CONFIGURED, or ASSUMED; results depend on it.",
    "Comparable-period checks use UTC only; local work calendars are not modelled.",
    "No weather or occupancy normalisation is applied.",
    "Statistical confidence is categorical only; no calibrated probability is claimed.",
]


def _assess_data_quality(
    measurements: list[RichMeasurement],
    quality_flags: list[str],
    *,
    quality_stale: bool,
) -> tuple[DataQualityLevel, list[str]]:
    """Determine data-quality level and rationale."""
    rationale: list[str] = []
    if quality_stale:
        rationale.append("quality_stale flag is set")
    if quality_flags:
        rationale.append(f"quality flags present: {', '.join(quality_flags)}")
    missing = [m.name for m in measurements if not m.is_present()]
    if missing:
        rationale.append(f"missing measurements: {', '.join(missing)}")

    if quality_stale or missing:
        return DataQualityLevel.UNUSABLE, rationale
    if quality_flags:
        return DataQualityLevel.DEGRADED, rationale
    rationale.append("all required measurements present, no quality flags")
    return DataQualityLevel.OK, rationale


def _assess_model_uncertainty(
    measurements: list[RichMeasurement],
) -> tuple[ModelUncertaintyLevel, list[str]]:
    """Determine model uncertainty level and rationale."""
    rationale: list[str] = []
    measured_count = sum(1 for m in measurements if m.is_measured())
    estimated_count = sum(1 for m in measurements if m.is_estimated())
    unknown_count = sum(
        1
        for m in measurements
        if m.is_present() and not m.is_measured() and not m.is_estimated()
    )
    has_any_tolerance = any(m.band() is not None for m in measurements)

    rationale.append(
        f"{measured_count} measured, {estimated_count} estimated, "
        f"{unknown_count} unknown provenance"
    )
    if has_any_tolerance:
        rationale.append("explicit tolerances provided for at least one measurement")
    else:
        rationale.append("no measurement tolerances provided")

    # Measurement provenance is not the same thing as measurement accuracy.
    # A MEASURED value without a tolerance is not evidence of low uncertainty.
    if not has_any_tolerance:
        rationale.append(
            "measurement uncertainty is UNKNOWN because no tolerance metadata "
            "was supplied"
        )
        return ModelUncertaintyLevel.UNKNOWN, rationale

    if estimated_count == 0 and unknown_count == 0:
        return ModelUncertaintyLevel.MODERATE, rationale
    if estimated_count <= measured_count:
        return ModelUncertaintyLevel.MODERATE, rationale
    return ModelUncertaintyLevel.HIGH, rationale


def assess_uncertainty(
    measurements: list[RichMeasurement],
    *,
    quality_flags: list[str] | None = None,
    quality_stale: bool = False,
    stale_measurement_names: list[str] | None = None,
    low_quality_names: list[str] | None = None,
    assumptions: list[str] | None = None,
) -> UncertaintyReport:
    """Build a transparent uncertainty report for the given measurements."""
    quality_flags = quality_flags or []
    stale_measurement_names = stale_measurement_names or []
    low_quality_names = low_quality_names or []
    assumptions = assumptions or []

    data_level, data_rationale = _assess_data_quality(
        measurements, quality_flags, quality_stale=quality_stale
    )
    model_level, model_rationale = _assess_model_uncertainty(measurements)

    return UncertaintyReport(
        missing_measurements=[m.name for m in measurements if not m.is_present()],
        stale_measurements=list(stale_measurement_names),
        low_quality_measurements=list(low_quality_names),
        estimated_parameters=[m.name for m in measurements if m.is_estimated()],
        assumptions=[*assumptions, *MODEL_LIMITATIONS],
        model_limitations=list(MODEL_LIMITATIONS),
        data_quality_level=data_level,
        model_uncertainty_level=model_level,
        rationale=[*data_rationale, *model_rationale],
        tolerance_used=[m.name for m in measurements if m.band() is not None],
        quality_flags=list(quality_flags),
    )
