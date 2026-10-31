# Copyright 2026 Lightibulb

"""Time utility functions."""

import datetime as dt


def now_z() -> str:
    """Return the current UTC time in ISO 8601 format with a 'Z' suffix."""
    return dt.datetime.now(dt.UTC).isoformat().replace("+00:00", "Z")


def parse_utc(ts: str) -> dt.datetime:
    """Parse an ISO 8601 timestamp string and return a timezone-aware UTC datetime."""
    # Python 3.11's fromisoformat handles 'Z' natively, but we normalize it for safety.
    clean_ts = ts.replace("Z", "+00:00")
    return dt.datetime.fromisoformat(clean_ts).astimezone(dt.UTC)


def to_z(dt_obj: dt.datetime) -> str:
    """Convert a datetime to an ISO 8601 string with a 'Z' suffix."""
    return dt_obj.astimezone(dt.UTC).isoformat().replace("+00:00", "Z")
