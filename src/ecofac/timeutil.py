# Copyright 2026 Lightibulb

"""Time utility functions."""

import datetime as dt


def now_z() -> str:
    """Return the current UTC time in ISO 8601 format with a 'Z' suffix."""
    return dt.datetime.now(dt.UTC).isoformat().replace("+00:00", "Z")
