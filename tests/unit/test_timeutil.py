# Copyright 2026 Lightibulb
"""Time helpers must reject naive input instead of guessing local time."""

import datetime as dt

import pytest

from ecofac.timeutil import now_z, parse_utc, to_z


def test_to_z_rejects_naive_datetime():
    with pytest.raises(ValueError, match="Naive"):
        to_z(dt.datetime(2026, 1, 1, 12, 0))  # noqa: DTZ001


def test_parse_utc_rejects_naive_string():
    with pytest.raises(ValueError, match="Naive"):
        parse_utc("2026-01-01T12:00:00")


def test_parse_utc_normalizes_offsets():
    assert to_z(parse_utc("2026-01-01T12:00:00+02:00")) == "2026-01-01T10:00:00Z"


def test_now_z_always_has_microseconds_so_strings_sort_by_time():
    stamp = now_z()
    assert stamp.endswith("Z")
    assert "." in stamp
