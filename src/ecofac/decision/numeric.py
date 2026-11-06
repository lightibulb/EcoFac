# Copyright 2026 Lightibulb
"""Finite-number and PUE validation shared by every decision entry point.

Pure stdlib (no pydantic) so it can be used by API schemas, the trusted
resolver, the scenario validator and the engine alike, and so the same rule
is enforced everywhere instead of being re-implemented per module.

Policies (documented here, enforced in code):

* Every numeric input to a calculation must be a finite real number.
  ``NaN``, ``+inf`` and ``-inf`` are rejected, as are booleans (``True`` is
  an ``int`` in Python and must never be read as 1.0 kW).
* PUE (Power Usage Effectiveness) is total facility power divided by IT
  power, so under the standard definition it can never be below 1.0.
  ``MAX_PUE`` is a sanity ceiling that catches unit mistakes (e.g. a
  percentage typed as a ratio); it is not a physical law.
* Uncertainty bands for physically non-negative quantities are **clamped**
  at their physical floor (0 for loads, 1 for PUE) rather than rejected, and
  the clamping is reported to the caller by ``clamp_band``.
"""

import math

MIN_PUE = 1.0
MAX_PUE = 10.0


def require_finite(name: str, value: object) -> float:
    """Return ``value`` as a float, or raise ``ValueError`` if not finite."""
    if isinstance(value, bool) or not isinstance(value, int | float):
        msg = f"{name} must be a finite number (got {value!r})"
        # Deliberately ValueError (not TypeError): every caller wraps this
        # as a domain error (TrustError / HTTP 400). See trust.py.
        raise ValueError(msg)  # noqa: TRY004
    number = float(value)
    if not math.isfinite(number):
        msg = f"{name} must be finite (got {value})"
        raise ValueError(msg)
    return number


def validate_pue_value(value: object, name: str = "pue") -> float:
    """Validate a PUE value: finite and within ``[MIN_PUE, MAX_PUE]``."""
    number = require_finite(name, value)
    if number < MIN_PUE:
        msg = (
            f"{name} must be >= {MIN_PUE} under the standard PUE definition "
            f"(facility power / IT power); got {number}"
        )
        raise ValueError(msg)
    if number > MAX_PUE:
        msg = f"{name} {number} exceeds the sanity ceiling {MAX_PUE}; check units"
        raise ValueError(msg)
    return number


def clamp_band(lower: float, upper: float, *, floor: float = 0.0) -> tuple[float, float, bool]:
    """Clamp a tolerance band at a physical floor.

    Returns ``(lower, upper, was_clamped)``. Only the lower bound is clamped:
    an uncertainty band must never imply a negative physical load.
    """
    if lower < floor:
        return floor, max(upper, floor), True
    return lower, upper, False
