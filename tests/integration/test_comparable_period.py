# Copyright 2026 Lightibulb
"""A reading that matches its own daily routine is not an anomaly."""
import datetime as dt

from ecofac.detection.anomaly import detect_robust_mad, detect_rolling_baseline
from ecofac.ingestion.pipeline import ingest_csv
from ecofac.persistence.database import get_connection, init_db
from evaluation.runner import seed_metadata

TARGET = "2026-01-13T08:00:00Z"  # a Tuesday, first hour of the working-day load step


def _db(spike_at_target=None):
    """Nine days of hourly data: weekdays run +5 kWh from 08:00 to 17:00."""
    lines = ["timestamp,building_id,meter_id,energy_value,unit"]
    start = dt.datetime(2026, 1, 5, tzinfo=dt.UTC)  # a Monday
    for i in range(9 * 24):
        ts = start + dt.timedelta(hours=i)
        value = 10.0 + (5.0 if ts.weekday() < 5 and 8 <= ts.hour <= 17 else 0.0)
        stamp = ts.isoformat().replace("+00:00", "Z")
        if spike_at_target is not None and stamp == TARGET:
            value = spike_at_target
        lines.append(f"{stamp},b1,m1-1,{value},kWh")
    conn = get_connection(":memory:")
    init_db(conn)
    seed_metadata(conn)
    ingest_csv(conn, "\n".join(lines) + "\n", "s1", "ib1")
    return conn


def test_daily_load_step_is_not_flagged():
    conn = _db()
    assert detect_robust_mad(conn, "m1-1", TARGET) is None
    assert detect_rolling_baseline(conn, "m1-1", TARGET) is None


def test_real_spike_in_the_same_slot_is_still_flagged():
    conn = _db(spike_at_target=40.0)
    anomaly = detect_robust_mad(conn, "m1-1", TARGET)
    assert anomaly is not None
    assert '"comparable_period"' in anomaly.detection_parameters
