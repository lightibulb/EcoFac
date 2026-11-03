# Copyright 2026 Lightibulb
"""Rich measurement model with optional uncertainty metadata.

Every measurement carries provenance (measured / estimated / configured /
assumed / missing), an optional explicit tolerance, and an optional source
reference so it can be traced back to the original reading.

The system never invents uncertainty values: if neither ``absolute_tolerance``
nor ``relative_tolerance`` is provided, the measurement has *unknown*
uncertainty and downstream code must treat it as such.
"""

from enum import StrEnum

import pydantic


class MeasurementSource(StrEnum):
    """Where a measurement value came from."""

    MEASURED = "MEASURED"
    ESTIMATED = "ESTIMATED"
    CONFIGURED = "CONFIGURED"
    ASSUMED = "ASSUMED"
    MISSING = "MISSING"


class UncertaintyMetadata(pydantic.BaseModel):
    """Explicit uncertainty bounds for a measurement.

    Both tolerances are optional and caller-supplied. If neither is present,
    the measurement has unknown uncertainty and the system must not fabricate
    a value.
    """

    model_config = pydantic.ConfigDict(frozen=True)

    absolute_tolerance: float | None = pydantic.Field(default=None, ge=0.0)
    relative_tolerance: float | None = pydantic.Field(default=None, ge=0.0, lt=1.0)
    notes: str | None = None

    def tolerance_band(self, value: float) -> tuple[float, float] | None:
        """Return (lower, upper) bounds around ``value``, or None if unknown.

        ``absolute_tolerance`` takes precedence when both are supplied.
        """
        if self.absolute_tolerance is not None:
            delta = self.absolute_tolerance
        elif self.relative_tolerance is not None:
            delta = abs(value) * self.relative_tolerance
        else:
            return None
        return (value - delta, value + delta)


class RichMeasurement(pydantic.BaseModel):
    """A measurement value with provenance and optional uncertainty."""

    model_config = pydantic.ConfigDict(frozen=True)

    name: str
    value: float | None
    unit: str
    source: MeasurementSource
    uncertainty: UncertaintyMetadata | None = None
    measurement_timestamp: str | None = None
    reference_id: str | None = None

    def is_present(self) -> bool:
        """True if a value is available and the source is not MISSING."""
        return self.value is not None and self.source != MeasurementSource.MISSING

    def band(self) -> tuple[float, float] | None:
        """Return the tolerance band for this measurement, or None."""
        if self.value is None or self.uncertainty is None:
            return None
        return self.uncertainty.tolerance_band(self.value)

    def is_measured(self) -> bool:
        """True only when the source is MEASURED."""
        return self.source == MeasurementSource.MEASURED

    def is_estimated(self) -> bool:
        """True when the source is ESTIMATED, CONFIGURED, or ASSUMED."""
        return self.source in (
            MeasurementSource.ESTIMATED,
            MeasurementSource.CONFIGURED,
            MeasurementSource.ASSUMED,
        )
