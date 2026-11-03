# Copyright 2026 Lightibulb
"""Unit tests for the rich measurement model."""

import pydantic
import pytest

from ecofac.decision.measurements import (
    MeasurementSource,
    RichMeasurement,
    UncertaintyMetadata,
)


def test_measured_with_absolute_tolerance():
    m = RichMeasurement(
        name="it_load",
        value=500.0,
        unit="kW",
        source=MeasurementSource.MEASURED,
        uncertainty=UncertaintyMetadata(absolute_tolerance=10.0),
    )
    assert m.is_present()
    assert m.is_measured()
    assert not m.is_estimated()
    assert m.band() == (490.0, 510.0)


def test_measured_with_relative_tolerance():
    m = RichMeasurement(
        name="it_load",
        value=200.0,
        unit="kW",
        source=MeasurementSource.MEASURED,
        uncertainty=UncertaintyMetadata(relative_tolerance=0.05),
    )
    assert m.band() == (190.0, 210.0)


def test_absolute_overrides_relative():
    m = RichMeasurement(
        name="x",
        value=100.0,
        unit="kW",
        source=MeasurementSource.MEASURED,
        uncertainty=UncertaintyMetadata(
            absolute_tolerance=5.0, relative_tolerance=0.5
        ),
    )
    assert m.band() == (95.0, 105.0)


def test_no_uncertainty_returns_none_band():
    m = RichMeasurement(
        name="x", value=100.0, unit="kW", source=MeasurementSource.MEASURED
    )
    assert m.band() is None


def test_estimated_source():
    m = RichMeasurement(
        name="pue",
        value=1.4,
        unit="dimensionless",
        source=MeasurementSource.ESTIMATED,
    )
    assert m.is_present()
    assert m.is_estimated()
    assert not m.is_measured()


def test_assumed_source():
    m = RichMeasurement(
        name="pue",
        value=1.5,
        unit="dimensionless",
        source=MeasurementSource.ASSUMED,
    )
    assert m.is_estimated()


def test_missing_source():
    m = RichMeasurement(
        name="it_load", value=None, unit="kW", source=MeasurementSource.MISSING
    )
    assert not m.is_present()
    assert m.band() is None


def test_negative_tolerance_rejected():
    with pytest.raises(pydantic.ValidationError):
        UncertaintyMetadata(absolute_tolerance=-1.0)


def test_relative_tolerance_at_one_rejected():
    with pytest.raises(pydantic.ValidationError):
        UncertaintyMetadata(relative_tolerance=1.0)


def test_frozen_model():
    m = RichMeasurement(
        name="x", value=1.0, unit="kW", source=MeasurementSource.MEASURED
    )
    with pytest.raises(pydantic.ValidationError):
        m.value = 2.0
