# Copyright 2026 Lightibulb

"""Structured API errors.

Routes that need machine-readable errors raise ``api_error``. The response body
keeps FastAPI's ``detail`` key (so existing clients keep working) but ``detail``
is an object::

    {"detail": {"code": "missing_columns",
                "message": "Missing required columns. Expected: [...]",
                "rolled_back": true,
                "details": {...}}}

``code`` is stable and meant for programs; ``message`` is meant for people.
"""

from typing import Any

from fastapi import HTTPException


def api_error(
    status_code: int,
    code: str,
    message: str,
    *,
    rolled_back: bool | None = None,
    **details: Any,
) -> HTTPException:
    """Build an ``HTTPException`` whose ``detail`` is a structured object."""
    detail: dict[str, Any] = {"code": code, "message": message}
    if rolled_back is not None:
        detail["rolled_back"] = rolled_back
    if details:
        detail["details"] = details
    return HTTPException(status_code=status_code, detail=detail)
