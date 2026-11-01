# Copyright 2026 Lightibulb

"""Time utility functions."""

import datetime as dt


def now_z() -> str:
    """Return the current UTC time as ISO 8601 (microseconds, 'Z' suffix).

    Microseconds are always present so that string ordering matches time ordering.
    """
    return dt.datetime.now(dt.UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def parse_utc(ts: str) -> dt.datetime:
    """Parse an ISO 8601 timestamp into an aware UTC datetime.

    Naive timestamps are rejected instead of being guessed as local time.
    """
    parsed = dt.datetime.fromisoformat(ts.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        msg = f"Naive timestamp not allowed: {ts}"
        raise ValueError(msg)
    return parsed.astimezone(dt.UTC)


def to_z(dt_obj: dt.datetime) -> str:
    """Convert an aware datetime to a canonical ISO 8601 UTC string with a 'Z' suffix."""
    if dt_obj.tzinfo is None:
        msg = "Naive datetime not allowed"
        raise ValueError(msg)
    return dt_obj.astimezone(dt.UTC).isoformat().replace("+00:00", "Z")
