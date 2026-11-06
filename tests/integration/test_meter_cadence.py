# Copyright 2026 Lightibulb
"""Meter cadence: quality assessment and batch screening across reporting rates."""

import datetime as dt
import random

import pytest

from ecofac.detection.anomaly import (
    DEFAULT_MIN_SAMPLES,
    detect_robust_mad,
    detect_rolling_baseline,
)
from ecofac.detection.batch import analyze_meter_series
from ecofac.persistence.database import get_connection, init_db
from ecofac.quality.assessment import assess_quality, meter_interval_minutes

START = dt.datetime(2026, 2, 1, tzinfo=dt.UTC)


def z(ts: dt.datetime) -> str:
    return ts.isoformat().replace("+00:00", "Z")


def new_conn():
    c = get_connection(":memory:")
    init_db(c)
    c.execute("INSERT INTO organizations VALUES ('o1','Org','2026-01-01T00:00:00Z')")
    c.execute("INSERT INTO buildings VALUES ('b1','o1','B','2026-01-01T00:00:00Z')")
    c.execute("INSERT INTO data_sources VALUES ('s1','S','csv','2026-01-01T00:00:00Z')")
    c.execute(
        "INSERT INTO import_batches (id, source_id, status, imported_at) "
        "VALUES ('ib1','s1','done','2026-01-01T00:00:00Z')"
    )
    return c


def add_meter(conn, meter_id, interval):
    conn.execute(
        "INSERT INTO meters (id, building_id, fuel_type, unit, interval_minutes, created_at) "
        "VALUES (?, 'b1', 'elec', 'kWh', ?, '2026-01-01T00:00:00Z')",
        (meter_id, interval),
    )


def add_series(conn, meter_id, stamps, values):
    conn.executemany(
        "INSERT INTO energy_readings (id, meter_id, timestamp_utc, value, unit, "
        "source_id, batch_id, is_valid, created_at) "
        "VALUES (?, ?, ?, ?, 'kWh', 's1', 'ib1', 1, ?)",
        [
            (f"{meter_id}-{i}", meter_id, z(t), v, z(t))
            for i, (t, v) in enumerate(zip(stamps, values, strict=True))
        ],
    )


def regular(interval_min, count):
    return [START + dt.timedelta(minutes=interval_min * i) for i in range(count)]


@pytest.fixture
def conn():
    c = new_conn()
    yield c
    c.close()


# ---- quality assessment uses the meter's own interval ----------------------


@pytest.mark.parametrize("interval", [60, 30, 15])
def test_complete_series_is_eligible_at_every_cadence(conn, interval):
    add_meter(conn, "m", interval)
    stamps = regular(interval, 9)
    add_series(conn, "m", stamps, [10.0] * 9)
    report = assess_quality(conn, "m", stamps[0], stamps[-1])
    assert report.interval_minutes == interval
    assert report.expected_readings == 9
    assert report.missing_readings == 0
    assert report.is_analysis_eligible


def test_hourly_assumption_would_have_misjudged_a_15_minute_meter(conn):
    add_meter(conn, "m", 15)
    stamps = regular(15, 5)  # one hour of 15-minute data
    add_series(conn, "m", stamps, [10.0] * 5)
    explicit_hourly = assess_quality(
        conn, "m", stamps[0], stamps[-1], expected_frequency_minutes=60
    )
    actual = assess_quality(conn, "m", stamps[0], stamps[-1])
    assert explicit_hourly.expected_readings == 2  # the old, wrong expectation
    assert actual.expected_readings == 5
    assert actual.missing_readings == 0


def test_missing_reading_is_detected_relative_to_cadence(conn):
    add_meter(conn, "m", 30)
    stamps = regular(30, 7)
    del stamps[3]
    add_series(conn, "m", stamps, [10.0] * 6)
    report = assess_quality(conn, "m", START, START + dt.timedelta(minutes=180))
    assert report.missing_readings == 1
    assert "missing_data" in report.flags
    assert not report.is_analysis_eligible


def test_irregular_series_is_flagged(conn):
    add_meter(conn, "m", 15)
    offsets = [0, 15, 30, 55, 60]  # one late sample, one missing slot
    stamps = [START + dt.timedelta(minutes=o) for o in offsets]
    add_series(conn, "m", stamps, [10.0] * 5)
    report = assess_quality(conn, "m", START, START + dt.timedelta(minutes=60))
    assert report.expected_readings == 5
    assert report.actual_readings == 5  # counts rows, but...
    # ...gap between 30 and 55 minutes exceeds the cadence: a window ending
    # at the late sample must not look healthy for the missing slot.
    window = assess_quality(
        conn, "m", START + dt.timedelta(minutes=30), START + dt.timedelta(minutes=45)
    )
    assert window.missing_readings >= 1


def test_stale_series_is_flagged_by_interval(conn):
    add_meter(conn, "m", 15)
    stamps = regular(15, 4)  # last reading at +45 min
    add_series(conn, "m", stamps, [10.0] * 4)
    end = START + dt.timedelta(minutes=45 + 31)  # > 2 intervals of silence
    report = assess_quality(conn, "m", START, end)
    assert report.is_stale
    assert "stale_data" in report.flags


def test_unknown_meter_falls_back_to_hourly_and_bad_interval_raises(conn):
    assert meter_interval_minutes(conn, "ghost") == 60
    add_meter(conn, "bad", 15)
    conn.execute("UPDATE meters SET interval_minutes = -5 WHERE id='bad'")
    with pytest.raises(ValueError, match="non-positive"):
        meter_interval_minutes(conn, "bad")


# ---- batch screening is time-window based and exhaustive --------------------


def noisy(interval_min, hours, *, seed, base=50.0):
    rng = random.Random(seed)
    n = int(hours * 60 / interval_min)
    return regular(interval_min, n), [base + rng.gauss(0, 1.0) for _ in range(n)]


def detector_set(conn, meter_id):
    return {
        (r["timestamp_utc"], r["detector_name"])
        for r in conn.execute(
            "SELECT timestamp_utc, detector_name FROM anomalies WHERE meter_id = ?",
            (meter_id,),
        )
    }


def exhaustive(conn, meter_id):
    rows = conn.execute(
        "SELECT timestamp_utc FROM energy_readings WHERE meter_id = ? "
        "AND is_valid = 1 ORDER BY timestamp_utc",
        (meter_id,),
    ).fetchall()
    for r in rows:
        detect_rolling_baseline(conn, meter_id, r["timestamp_utc"])
        detect_robust_mad(conn, meter_id, r["timestamp_utc"])
    return detector_set(conn, meter_id)


def build_pair(interval, values_fn, *, hours=72, seed=7):
    stamps, values = noisy(interval, hours, seed=seed)
    values = values_fn(stamps, values)
    a, b = new_conn(), new_conn()
    for c in (a, b):
        add_meter(c, "m", interval)
        add_series(c, "m", stamps, values)
    return a, b, stamps


def spike(idxs, factor=3.0):
    def apply(_stamps, values):
        out = list(values)
        for i in idxs:
            out[i] *= factor
        return out

    return apply


@pytest.mark.parametrize("interval", [60, 30, 15])
def test_screening_equals_exhaustive_detection_at_every_cadence(interval):
    per_hour = 60 // interval
    spikes = [30 * per_hour + 3, 40 * per_hour, 55 * per_hour + 1]
    screened_db, exhaustive_db, _ = build_pair(interval, spike(spikes))
    analyze_meter_series(screened_db, "m", create_incidents=False)
    got = detector_set(screened_db, "m")
    want = exhaustive(exhaustive_db, "m")
    assert got == want
    assert len(got) >= len(spikes)  # the planted spikes are found, not skipped
    screened_db.close()
    exhaustive_db.close()


def test_fifteen_minute_spike_is_not_hidden_by_a_row_count_window():
    """Regression: a 24-row window covered only 6 h of 15-minute data."""

    def shape(stamps, values):
        out = list(values)
        # noisy first 18 h, then a quiet 6 h so a 24-row window has tiny variance
        for i in range(len(out)):
            hours_in = (stamps[i] - START).total_seconds() / 3600
            if 18 <= hours_in < 30:
                out[i] = 50.0 + (i % 2) * 0.01
        out[40 * 4] *= 1.4
        return out

    screened_db, exhaustive_db, _ = build_pair(15, shape)
    analyze_meter_series(screened_db, "m", create_incidents=False)
    assert detector_set(screened_db, "m") == exhaustive(exhaustive_db, "m")
    screened_db.close()
    exhaustive_db.close()


def test_regime_shift_matches_exhaustive_detection():
    def shift(_stamps, values):
        return [v + (25.0 if i >= len(values) // 2 else 0.0) for i, v in enumerate(values)]

    screened_db, exhaustive_db, _ = build_pair(30, shift)
    analyze_meter_series(screened_db, "m", create_incidents=False)
    got = detector_set(screened_db, "m")
    assert got == exhaustive(exhaustive_db, "m")
    assert got, "the level shift must register at the transition"
    screened_db.close()
    exhaustive_db.close()


def test_gappy_irregular_series_matches_exhaustive_detection():
    rng = random.Random(11)
    stamps, values = noisy(15, 72, seed=3)
    keep = [i for i in range(len(stamps)) if rng.random() > 0.2]
    stamps = [stamps[i] for i in keep]
    values = [values[i] for i in keep]
    values[len(values) // 2] *= 3.0
    a, b = new_conn(), new_conn()
    for c in (a, b):
        add_meter(c, "m", 15)
        add_series(c, "m", stamps, values)
    analyze_meter_series(a, "m", create_incidents=False)
    assert detector_set(a, "m") == exhaustive(b, "m")
    a.close()
    b.close()


def test_too_little_history_yields_no_detections():
    stamps = regular(15, DEFAULT_MIN_SAMPLES - 1)
    c = new_conn()
    add_meter(c, "m", 15)
    add_series(c, "m", stamps, [10.0] * (len(stamps) - 1) + [500.0])
    summary, _ = analyze_meter_series(c, "m", create_incidents=False)
    assert summary.anomalies_detected == 0
    c.close()
