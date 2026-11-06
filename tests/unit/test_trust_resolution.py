# Copyright 2026 Lightibulb
"""Trusted-measurement resolution: energy/power semantics, roles, alignment."""

import datetime as dt

import pytest

from ecofac.decision.numeric import (
    MAX_PUE,
    clamp_band,
    require_finite,
    validate_pue_value,
)
from ecofac.decision.trust import (
    CAVEAT_ROLE_UNCLASSIFIED,
    ROLE_COOLING,
    ROLE_IT_LOAD,
    TrustError,
    energy_kwh_to_average_kw,
    resolve_trusted_pair,
    resolve_trusted_reading,
    server_max_age_seconds,
)
from ecofac.persistence.database import get_connection, init_db

NOW = dt.datetime(2026, 3, 1, 12, 0, tzinfo=dt.UTC)
TS = "2026-03-01T11:00:00Z"
PREV_HOUR = "2026-03-01T10:00:00Z"


@pytest.fixture
def conn():
    c = get_connection(":memory:")
    init_db(c)
    c.execute("INSERT INTO organizations VALUES ('o1','Org','2026-01-01T00:00:00Z')")
    c.execute("INSERT INTO buildings VALUES ('b1','o1','B','2026-01-01T00:00:00Z')")
    c.execute("INSERT INTO data_sources VALUES ('s1','S','csv','2026-01-01T00:00:00Z')")
    c.execute(
        "INSERT INTO import_batches (id, source_id, status, imported_at) "
        "VALUES ('ib1','s1','done','2026-01-01T00:00:00Z')"
    )
    yield c
    c.close()


def add_meter(conn, meter_id, *, role=None, interval=60, unit="kWh", fuel="elec",
              tolerance=None):
    conn.execute(
        "INSERT INTO meters (id, building_id, fuel_type, unit, interval_minutes, "
        "role, relative_tolerance, created_at) "
        "VALUES (?, 'b1', ?, ?, ?, ?, ?, '2026-01-01T00:00:00Z')",
        (meter_id, fuel, unit, interval, role, tolerance),
    )


def add_reading(conn, meter_id, ts, value, *, valid=1, unit="kWh"):
    conn.execute(
        "INSERT INTO energy_readings (id, meter_id, timestamp_utc, value, unit, "
        "source_id, batch_id, is_valid, created_at) "
        "VALUES (?, ?, ?, ?, ?, 's1', 'ib1', ?, ?)",
        (f"{meter_id}@{ts}", meter_id, ts, value, unit, valid, ts),
    )


def setup_pair(conn, *, it_interval=60, cool_interval=60):
    add_meter(conn, "it", role=ROLE_IT_LOAD, interval=it_interval, tolerance=0.02)
    add_meter(conn, "cool", role=ROLE_COOLING, interval=cool_interval, tolerance=0.03)
    for m, interval in (("it", it_interval), ("cool", cool_interval)):
        prev = dt.datetime(2026, 3, 1, 11, tzinfo=dt.UTC) - dt.timedelta(minutes=interval)
        add_reading(conn, m, prev.isoformat().replace("+00:00", "Z"), 10.0)
        add_reading(conn, m, TS, 10.0)


def resolve(conn, **kw):
    args = {
        "it_ref": ("it", TS),
        "cooling_ref": ("cool", TS),
        "evaluation_time": NOW,
    }
    args.update(kw)
    return resolve_trusted_pair(conn, **args)


# ---- energy vs power --------------------------------------------------------

@pytest.mark.parametrize(
    ("kwh", "interval", "expected_kw"),
    [(10.0, 60, 10.0), (5.0, 30, 10.0), (2.5, 15, 10.0), (20.0, 120, 10.0)],
)
def test_energy_to_average_power_uses_interval(kwh, interval, expected_kw):
    assert energy_kwh_to_average_kw(kwh, interval) == pytest.approx(expected_kw)


@pytest.mark.parametrize("bad", [None, 0, -15, 1441, True, 15.5, "60"])
def test_conversion_rejects_missing_or_invalid_interval(bad):
    with pytest.raises(TrustError):
        energy_kwh_to_average_kw(10.0, bad)


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), -1.0])
def test_conversion_rejects_non_physical_energy(bad):
    with pytest.raises(TrustError):
        energy_kwh_to_average_kw(bad, 60)


def test_fifteen_minute_meter_is_not_read_as_kw(conn):
    """2.5 kWh in 15 min is 10 kW average - never relabelled 2.5 kW."""
    setup_pair(conn, it_interval=15)
    conn.execute(
        "UPDATE energy_readings SET value = 2.5 WHERE meter_id='it' AND timestamp_utc=?",
        (TS,),
    )
    it, _ = resolve(conn)
    assert it.raw_value == 2.5
    assert it.raw_unit == "kWh"
    assert it.interval_minutes == 15
    assert it.value_kw == pytest.approx(10.0)


def test_meter_with_missing_interval_is_rejected(conn):
    setup_pair(conn)
    conn.execute("UPDATE meters SET interval_minutes = 0 WHERE id = 'it'")
    with pytest.raises(TrustError, match="interval"):
        resolve(conn)


def test_power_unit_meter_is_incompatible(conn):
    setup_pair(conn)
    conn.execute("UPDATE meters SET unit = 'kW' WHERE id = 'it'")
    with pytest.raises(TrustError, match="energy"):
        resolve(conn)


def test_non_electric_meter_is_incompatible(conn):
    setup_pair(conn)
    conn.execute("UPDATE meters SET fuel_type = 'gas' WHERE id = 'cool'")
    with pytest.raises(TrustError, match="electric"):
        resolve(conn)


# ---- roles ------------------------------------------------------------------

def test_wrong_role_is_rejected(conn):
    setup_pair(conn)
    conn.execute("UPDATE meters SET role = 'COOLING' WHERE id = 'it'")
    with pytest.raises(TrustError, match="role"):
        resolve(conn)


def test_same_meter_cannot_serve_both_roles(conn):
    setup_pair(conn)
    with pytest.raises(TrustError, match="same meter"):
        resolve(conn, cooling_ref=("it", TS))


def test_unclassified_role_is_a_visible_caveat_not_silent(conn):
    setup_pair(conn)
    conn.execute("UPDATE meters SET role = NULL WHERE id = 'it'")
    it, cooling = resolve(conn)
    assert CAVEAT_ROLE_UNCLASSIFIED in it.caveats
    assert cooling.caveats == ()


# ---- references, alignment, freshness --------------------------------------

@pytest.mark.parametrize(
    ("it_ref", "cool_ref", "missing"),
    [
        (("it", TS), None, "cooling_load"),
        (None, ("cool", TS), "it_load"),
        (("it", TS), ("cool", None), "cooling_load"),
        (None, None, "it_load"),
    ],
)
def test_partial_or_missing_references_are_rejected(conn, it_ref, cool_ref, missing):
    setup_pair(conn)
    with pytest.raises(TrustError, match=missing):
        resolve(conn, it_ref=it_ref, cooling_ref=cool_ref)


def test_mismatched_timestamps_are_rejected(conn):
    setup_pair(conn)
    conn.execute(
        "INSERT OR IGNORE INTO energy_readings (id, meter_id, timestamp_utc, value, "
        "unit, source_id, batch_id, is_valid, created_at) VALUES "
        "('c-prev','cool',?,10,'kWh','s1','ib1',1,?)", (PREV_HOUR, PREV_HOUR),
    )
    with pytest.raises(TrustError, match="not aligned"):
        resolve(conn, cooling_ref=("cool", PREV_HOUR))


def test_future_timestamp_is_rejected(conn):
    setup_pair(conn)
    with pytest.raises(TrustError, match="future"):
        resolve(conn, evaluation_time=NOW - dt.timedelta(hours=3))


def test_naive_timestamp_is_rejected(conn):
    setup_pair(conn)
    with pytest.raises(TrustError, match="timezone"):
        resolve(conn, it_ref=("it", "2026-03-01T11:00:00"))


def test_offset_spelling_of_same_instant_resolves(conn):
    setup_pair(conn)
    it, cooling = resolve(
        conn,
        it_ref=("it", "2026-03-01T11:00:00+00:00"),
        cooling_ref=("cool", "2026-03-01T13:00:00+02:00"),
    )
    assert it.timestamp_utc == cooling.timestamp_utc == TS


def test_missing_and_invalid_readings_are_rejected(conn):
    setup_pair(conn)
    with pytest.raises(TrustError, match="not found"):
        resolve(conn, it_ref=("it", "2026-03-01T09:00:00Z"))
    conn.execute("UPDATE energy_readings SET is_valid = 0 WHERE meter_id='it' AND timestamp_utc=?", (TS,))
    with pytest.raises(TrustError, match="invalid"):
        resolve(conn)


def test_unknown_meter_is_rejected(conn):
    setup_pair(conn)
    with pytest.raises(TrustError, match="does not exist"):
        resolve(conn, it_ref=("ghost", TS))


# ---- quality + tolerance are per measurement and server-derived ------------

def test_quality_is_assessed_for_every_measurement(conn):
    setup_pair(conn)
    prev = "2026-03-01T10:00:00Z"
    conn.execute("DELETE FROM energy_readings WHERE meter_id='cool' AND timestamp_utc=?", (prev,))
    it, cooling = resolve(conn)
    assert it.quality_eligible is True
    assert cooling.quality_eligible is False
    assert "missing_data" in cooling.quality_flags


def test_quality_window_uses_each_meters_own_interval(conn):
    setup_pair(conn, it_interval=15, cool_interval=60)
    it, cooling = resolve(conn)
    assert it.quality_eligible is True
    assert cooling.quality_eligible is True


def test_tolerance_comes_from_meter_metadata(conn):
    setup_pair(conn)
    it, cooling = resolve(conn)
    assert (it.relative_tolerance, cooling.relative_tolerance) == (0.02, 0.03)
    conn.execute("UPDATE meters SET relative_tolerance = NULL WHERE id = 'it'")
    it, _ = resolve(conn)
    assert it.relative_tolerance is None


@pytest.mark.parametrize("bad", [0.0, 1.0, 1.5, -0.1])
def test_invalid_server_tolerance_is_rejected(conn, bad):
    setup_pair(conn)
    conn.execute("UPDATE meters SET relative_tolerance = ? WHERE id = 'it'", (bad,))
    with pytest.raises(TrustError, match="relative_tolerance"):
        resolve(conn)


def test_single_reading_resolver_rejects_unknown_role(conn):
    setup_pair(conn)
    with pytest.raises(TrustError, match="role"):
        resolve_trusted_reading(
            conn, field="x", role="PUE", meter_id="it", timestamp=TS,
            evaluation_time=NOW,
        )


# ---- server-controlled freshness policy ------------------------------------

def test_server_max_age_policy(monkeypatch):
    monkeypatch.delenv("ECOFAC_TRUSTED_MAX_AGE_SECONDS", raising=False)
    assert server_max_age_seconds() == 7200
    monkeypatch.setenv("ECOFAC_TRUSTED_MAX_AGE_SECONDS", "900")
    assert server_max_age_seconds() == 900
    for bad in ("abc", "0", "-5", ""):
        monkeypatch.setenv("ECOFAC_TRUSTED_MAX_AGE_SECONDS", bad)
        assert server_max_age_seconds() == 7200


# ---- numeric helpers --------------------------------------------------------

@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf"), True, "1", None])
def test_require_finite_rejects(bad):
    with pytest.raises(ValueError, match=r"finite|number"):
        require_finite("x", bad)


@pytest.mark.parametrize("bad", [0.0, 0.99, -1.0, float("nan"), float("inf"), MAX_PUE + 1])
def test_pue_validation_rejects(bad):
    with pytest.raises(ValueError, match="pue"):
        validate_pue_value(bad)


def test_pue_validation_accepts_physical_values():
    assert validate_pue_value(1.0) == 1.0
    assert validate_pue_value(1.45) == 1.45


def test_clamp_band_never_returns_negative_lower_bound():
    assert clamp_band(-3.0, 7.0) == (0.0, 7.0, True)
    assert clamp_band(2.0, 4.0) == (2.0, 4.0, False)
    assert clamp_band(0.8, 1.6, floor=1.0) == (1.0, 1.6, True)
