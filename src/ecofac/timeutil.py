# Copyright 2026 Lightibulb
"""Canonical timestamp handling for EcoFac.

All timestamps are stored as UTC ISO-8601 strings with a trailing ``Z``
(e.g. ``2026-01-01T12:00:00Z``). SQLite compares TEXT lexicographically, so
every module MUST use exactly one canonical form. These helpers are the
single source of truth for parsing and formatting.
"""
import datetime as dt


def parse_utc(value: str) -> dt.datetime:
    """Parse an ISO-8601 string into an aware UTC datetime.

    Naive timestamps (no timezone) are rejected: interpreting them is
    ambiguous and previously depended on the server's local timezone.
    """
    parsed = dt.datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        msg = f"Naive timestamp (missing timezone info): {value!r}"
        raise ValueError(msg)
    return parsed.astimezone(dt.UTC)


def to_z(value: dt.datetime) -> str:
    """Format an aware datetime into canonical storage form (UTC, 'Z')."""
    if value.tzinfo is None:
        msg = "Cannot store a naive timestamp; attach a timezone first."
        raise ValueError(msg)
    return value.astimezone(dt.UTC).isoformat().replace("+00:00", "Z")


def now_z() -> str:
    """Current UTC time in canonical storage form."""
    return to_z(dt.datetime.now(dt.UTC))
