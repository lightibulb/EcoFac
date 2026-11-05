# Copyright 2026 Lightibulb
"""Rich measurement model with optional uncertainty metadata.

Every measurement carries provenance (measured / estimated / configured /
assumed / missing), an optional explicit tolerance, and an optional source
reference so it can be traced back to the original reading.

The system never invents uncertainty values: if neither ``absolute_tolerance``
nor ``relative_tolerance`` is provided, the measurement has *unknown*
uncertainty and downstream code must treat it as such.

Negative values are rejected at the model boundary because a physical
measurement (load, power, capacity) cannot be negative. Callers who want to
propose *reductions* use ``proposed_it_delta`` on the request — not a
negative ``value``.

Every float is finite-only (``allow_inf_nan=False``): NaN and +/-infinity are
rejected at the boundary and can never reach a calculation or JSON output.

Uncertainty-band policy: the lower bound of a band around a physically
non-negative quantity is **clamped** at the physical floor (0 by default, 1.0
for PUE) rather than allowed to go negative. ``band_clamped`` reports when the
clamp changed the band so evidence can say so.
"""

from enum import StrEnum

import pydantic

from ecofac.decision.numeric import clamp_band


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

    absolute_tolerance: float | None = pydantic.Field(
        default=None, ge=0.0, allow_inf_nan=False
    )
    relative_tolerance: float | None = pydantic.Field(
        default=None, ge=0.0, lt=1.0, allow_inf_nan=False
    )
    notes: str | None = None

    def tolerance_band(
        self, value: float, *, floor: float = 0.0
    ) -> tuple[float, float] | None:
        """Return (lower, upper) bounds around ``value``, or None if unknown.

        ``absolute_tolerance`` takes precedence when both are supplied. The
        lower bound is clamped at ``floor`` so a tolerance wider than the
        value can never describe a negative physical load.
        """
        if self.absolute_tolerance is not None:
            delta = self.absolute_tolerance
        elif self.relative_tolerance is not None:
            delta = abs(value) * self.relative_tolerance
        else:
            return None
        lower, upper, _ = clamp_band(value - delta, value + delta, floor=floor)
        return (lower, upper)

    def is_clamped(self, value: float, *, floor: float = 0.0) -> bool:
        """True if the raw band around ``value`` dips below ``floor``."""
        if self.absolute_tolerance is not None:
            delta = self.absolute_tolerance
        elif self.relative_tolerance is not None:
            delta = abs(value) * self.relative_tolerance
        else:
            return False
        return (value - delta) < floor


class RichMeasurement(pydantic.BaseModel):
    """A measurement value with provenance and optional uncertainty."""

    model_config = pydantic.ConfigDict(frozen=True)

    name: str
    value: float | None = pydantic.Field(default=None, ge=0.0, allow_inf_nan=False)
    unit: str
    source: MeasurementSource
    uncertainty: UncertaintyMetadata | None = None
    measurement_timestamp: str | None = None
    reference_id: str | None = None
    meter_id: str | None = None
    # Original stored reading, preserved when ``value`` was derived from it
    # (e.g. kWh over an interval converted to average kW). ``None`` for
    # measurements that were not converted.
    raw_value: float | None = pydantic.Field(
        default=None, ge=0.0, allow_inf_nan=False
    )
    raw_unit: str | None = None
    interval_minutes: int | None = pydantic.Field(default=None, gt=0)

    def is_present(self) -> bool:
        """True if a value is available and the source is not MISSING."""
        return self.value is not None and self.source != MeasurementSource.MISSING

    def band(self, *, floor: float = 0.0) -> tuple[float, float] | None:
        """Return the tolerance band for this measurement, or None.

        The lower bound is clamped at ``floor`` (0 for loads, 1.0 for PUE).
        """
        if self.value is None or self.uncertainty is None:
            return None
        return self.uncertainty.tolerance_band(self.value, floor=floor)

    def band_clamped(self, *, floor: float = 0.0) -> bool:
        """True if the tolerance band was clamped at ``floor``."""
        if self.value is None or self.uncertainty is None:
            return False
        return self.uncertainty.is_clamped(self.value, floor=floor)

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

    def validate_unit(self, expected: str) -> None:
        """Raise ``ValueError`` if ``self.unit`` differs from ``expected``."""
        if self.unit != expected:
            msg = (
                f"Measurement '{self.name}' has unit '{self.unit}', "
                f"expected '{expected}'"
            )
            raise ValueError(msg)
