# Copyright 2026 Lightibulb
"""Server-side resolution of trusted operational measurements.

Pure stdlib + sqlite (no pydantic, no FastAPI) so the rules are unit-testable
in isolation. The API layer calls ``resolve_trusted_pair`` and converts the
returned ``TrustedReading`` objects into request measurements.

Rules enforced here (each raises ``TrustError``, a ``ValueError``):

* Energy is not power. Readings are stored as **kWh per reporting interval**.
  Average power is ``kWh * 60 / interval_minutes`` and is only computed when
  the meter reports kWh *and* has a valid interval. A reading is never
  relabelled kW without that conversion.
* The meter must be an electric kWh meter; its configured role (if any) must
  match the requested role; one meter cannot serve two roles.
* Both operational measurements need a trusted reference, with aligned
  timestamps, none of them too far in the future.
* Quality is assessed per measurement, against that meter's own interval.
* Tolerance comes from server-side meter metadata, never from the caller.
"""

import dataclasses
import datetime as dt
import os
import sqlite3

from ecofac.decision.numeric import require_finite
from ecofac.quality.assessment import assess_quality
from ecofac.timeutil import to_z

ROLE_IT_LOAD = "IT_LOAD"
ROLE_COOLING = "COOLING"
VALID_ROLES = (ROLE_IT_LOAD, ROLE_COOLING)

ENERGY_UNIT = "kWh"
POWER_UNIT = "kW"
ELECTRIC_FUEL_TYPES = frozenset({"elec", "electric", "electricity"})

MAX_INTERVAL_MINUTES = 1440
FUTURE_TOLERANCE_SECONDS = 300
ALIGNMENT_TOLERANCE_SECONDS = 0
DEFAULT_MAX_AGE_SECONDS = 7200
MAX_AGE_ENV = "ECOFAC_TRUSTED_MAX_AGE_SECONDS"

CAVEAT_ROLE_UNCLASSIFIED = "meter_role_unclassified"


class TrustError(ValueError):
    """A trusted measurement could not be resolved safely."""


@dataclasses.dataclass(frozen=True)
class TrustedReading:
    """One server-resolved operational measurement (power, with provenance)."""

    field: str
    meter_id: str
    timestamp_utc: str
    reading_id: str
    raw_value: float
    raw_unit: str
    interval_minutes: int
    value_kw: float
    relative_tolerance: float | None
    quality_flags: tuple[str, ...]
    quality_eligible: bool
    caveats: tuple[str, ...]


def energy_kwh_to_average_kw(energy_kwh: float, interval_minutes: object) -> float:
    """Convert energy over one interval (kWh) to average power (kW).

    ``kW_avg = kWh / (interval_minutes / 60)``. Raises ``TrustError`` when the
    interval is missing, non-integral, non-positive or implausibly long, since
    the conversion is meaningless without a valid interval.
    """
    try:
        energy = require_finite("energy_kwh", energy_kwh)
    except ValueError as exc:
        raise TrustError(str(exc)) from exc
    if energy < 0:
        msg = f"energy_kwh must be >= 0 (got {energy})"
        raise TrustError(msg)
    if (
        isinstance(interval_minutes, bool)
        or not isinstance(interval_minutes, int)
        or interval_minutes <= 0
        or interval_minutes > MAX_INTERVAL_MINUTES
    ):
        msg = (
            "Cannot convert energy to power: meter interval_minutes must be an "
            f"integer in 1..{MAX_INTERVAL_MINUTES} (got {interval_minutes!r})"
        )
        raise TrustError(msg)
    return energy * 60.0 / interval_minutes


def server_max_age_seconds() -> int:
    """Freshness policy for operational decisions. Controlled by the server.

    Read from ``ECOFAC_TRUSTED_MAX_AGE_SECONDS``; an unset, non-numeric or
    non-positive value falls back to ``DEFAULT_MAX_AGE_SECONDS``. Callers can
    never influence it.
    """
    raw = os.environ.get(MAX_AGE_ENV, "").strip()
    try:
        value = int(raw)
    except ValueError:
        return DEFAULT_MAX_AGE_SECONDS
    return value if value > 0 else DEFAULT_MAX_AGE_SECONDS


def parse_aware_utc(ts: object, name: str = "timestamp") -> dt.datetime:
    """Parse an ISO-8601 timestamp. Naive timestamps are rejected."""
    if not isinstance(ts, str) or not ts.strip():
        msg = f"{name} must be a non-empty ISO-8601 string"
        raise TrustError(msg)
    try:
        parsed = dt.datetime.fromisoformat(ts.strip())
    except ValueError as exc:
        msg = f"{name} is not a valid ISO-8601 timestamp: {ts!r}"
        raise TrustError(msg) from exc
    if parsed.tzinfo is None:
        msg = f"{name} must carry a timezone (naive timestamps are rejected): {ts!r}"
        raise TrustError(msg)
    return parsed.astimezone(dt.UTC)


def resolve_trusted_reading(
    conn: sqlite3.Connection,
    *,
    field: str,
    role: str,
    meter_id: object,
    timestamp: object,
    evaluation_time: dt.datetime,
) -> TrustedReading:
    """Resolve one stored reading into a validated average-power measurement."""
    if role not in VALID_ROLES:
        msg = f"unknown measurement role {role!r}"
        raise TrustError(msg)
    if not isinstance(meter_id, str) or not meter_id.strip():
        msg = f"{field}: meter_id is required for a trusted measurement"
        raise TrustError(msg)
    at = parse_aware_utc(timestamp, f"{field}.measurement_timestamp")
    if (at - evaluation_time).total_seconds() > FUTURE_TOLERANCE_SECONDS:
        msg = (
            f"{field}: timestamp {to_z(at)} is in the future relative to the "
            f"evaluation time {to_z(evaluation_time)}"
        )
        raise TrustError(msg)
    ts_z = to_z(at)

    meter = conn.execute(
        "SELECT id, fuel_type, unit, interval_minutes, role, relative_tolerance "
        "FROM meters WHERE id = ?",
        (meter_id,),
    ).fetchone()
    if meter is None:
        msg = f"{field}: meter {meter_id!r} does not exist"
        raise TrustError(msg)
    if str(meter["fuel_type"]).strip().lower() not in ELECTRIC_FUEL_TYPES:
        msg = (
            f"{field}: meter {meter_id} measures fuel type "
            f"{meter['fuel_type']!r}; an electric meter is required"
        )
        raise TrustError(msg)
    if str(meter["unit"]).strip().lower() != ENERGY_UNIT.lower():
        msg = (
            f"{field}: meter {meter_id} reports unit {meter['unit']!r}; "
            f"trusted load resolution requires an energy ({ENERGY_UNIT}) meter. "
            "Values are never relabelled as power without an interval conversion."
        )
        raise TrustError(msg)
    meter_role = meter["role"]
    caveats: list[str] = []
    if meter_role is None:
        caveats.append(CAVEAT_ROLE_UNCLASSIFIED)
    elif meter_role != role:
        msg = (
            f"{field}: meter {meter_id} has role {meter_role!r} but {role!r} "
            "is required"
        )
        raise TrustError(msg)

    interval = meter["interval_minutes"]
    tolerance = meter["relative_tolerance"]
    if tolerance is not None:
        try:
            tolerance = require_finite("relative_tolerance", tolerance)
        except ValueError as exc:
            msg = f"{field}: {exc}"
            raise TrustError(msg) from exc
        if not 0.0 < tolerance < 1.0:
            msg = (
                f"{field}: meter {meter_id} relative_tolerance must be in (0, 1); "
                "a zero tolerance would claim a perfect meter"
            )
            raise TrustError(msg)

    row = conn.execute(
        "SELECT id, value, unit, is_valid FROM energy_readings "
        "WHERE meter_id = ? AND timestamp_utc = ?",
        (meter_id, ts_z),
    ).fetchone()
    if row is None:
        msg = f"{field}: reading not found (meter={meter_id}, ts={ts_z})"
        raise TrustError(msg)
    if row["is_valid"] == 0:
        msg = f"{field}: reading for meter {meter_id} at {ts_z} is marked invalid"
        raise TrustError(msg)
    if str(row["unit"]).strip().lower() != ENERGY_UNIT.lower():
        msg = f"{field}: stored reading unit {row['unit']!r} is not {ENERGY_UNIT}"
        raise TrustError(msg)

    value_kw = energy_kwh_to_average_kw(row["value"], interval)
    quality = assess_quality(
        conn,
        meter_id,
        at - dt.timedelta(minutes=interval),
        at,
        expected_frequency_minutes=interval,
    )
    return TrustedReading(
        field=field,
        meter_id=meter_id,
        timestamp_utc=ts_z,
        reading_id=row["id"],
        raw_value=float(row["value"]),
        raw_unit=ENERGY_UNIT,
        interval_minutes=int(interval),
        value_kw=value_kw,
        relative_tolerance=tolerance,
        quality_flags=tuple(quality.flags),
        quality_eligible=quality.is_analysis_eligible,
        caveats=tuple(caveats),
    )


def resolve_trusted_pair(
    conn: sqlite3.Connection,
    *,
    it_ref: tuple[object, object] | None,
    cooling_ref: tuple[object, object] | None,
    evaluation_time: dt.datetime,
) -> tuple[TrustedReading, TrustedReading]:
    """Resolve both operational measurements or fail; never a partial pair."""
    missing = [
        name
        for name, ref in (("it_load", it_ref), ("cooling_load", cooling_ref))
        if ref is None or ref[0] is None or ref[1] is None
    ]
    if missing or it_ref is None or cooling_ref is None:
        msg = (
            "Trusted mode requires a trusted reference (meter_id + "
            f"measurement_timestamp) for every operational measurement; missing: "
            f"{', '.join(missing)}"
        )
        raise TrustError(msg)
    if it_ref[0] == cooling_ref[0]:
        msg = (
            "it_load and cooling_load reference the same meter "
            f"({it_ref[0]!r}); one meter cannot serve two roles"
        )
        raise TrustError(msg)
    it = resolve_trusted_reading(
        conn, field="it_load", role=ROLE_IT_LOAD,
        meter_id=it_ref[0], timestamp=it_ref[1], evaluation_time=evaluation_time,
    )
    cooling = resolve_trusted_reading(
        conn, field="cooling_load", role=ROLE_COOLING,
        meter_id=cooling_ref[0], timestamp=cooling_ref[1],
        evaluation_time=evaluation_time,
    )
    skew = abs(
        (parse_aware_utc(it.timestamp_utc) - parse_aware_utc(cooling.timestamp_utc))
        .total_seconds()
    )
    if skew > ALIGNMENT_TOLERANCE_SECONDS:
        msg = (
            "Measurement timestamps are not aligned: it_load "
            f"{it.timestamp_utc} vs cooling_load {cooling.timestamp_utc} "
            f"({skew:.0f}s apart)"
        )
        raise TrustError(msg)
    return it, cooling
