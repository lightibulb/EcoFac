# Copyright 2026 Lightibulb
"""Bulk analysis must find exactly what the per-reading detectors find."""

import math
import random

import pytest

from ecofac.detection.anomaly import detect_robust_mad, detect_rolling_baseline
from ecofac.detection.batch import analyze_all
from ecofac.ingestion.pipeline import ingest_csv
from ecofac.persistence.database import get_connection, init_db
from evaluation.runner import seed_metadata


def _series_csv(rng):
    lines = ["timestamp,building_id,meter_id,energy_value,unit"]
    for i in range(500):
        value = 20 + 5 * math.sin(i / 7) + rng.gauss(0, 1)
        if 150 <= i < 190:
            value = 12.0  # perfectly flat stretch -> zero MAD
        if i >= 380:
            value += 8  # level shift
        if rng.random() < 0.03:
            value *= rng.uniform(1.5, 3.0)
        day, hour = divmod(i, 24)
        lines.append(f"2026-01-{day + 1:02d}T{hour:02d}:00:00Z,b1,m1-1,{value:.4f},kWh")
    return "\n".join(lines) + "\n"


def _fresh(csv_text):
    conn = get_connection(":memory:")
    init_db(conn)
    seed_metadata(conn)
    ingest_csv(conn, csv_text, "s1", "ib1")
    return conn


def _keys(conn):
    return {
        (r["meter_id"], r["timestamp_utc"], r["detector_name"])
        for r in conn.execute("SELECT meter_id, timestamp_utc, detector_name FROM anomalies")
    }


@pytest.fixture
def csv_text():
    return _series_csv(random.Random(7))


def test_bulk_analysis_matches_exhaustive_detection(csv_text):
    fast = _fresh(csv_text)
    analyze_all(fast, create_incidents=False)

    slow = _fresh(csv_text)
    rows = slow.execute(
        "SELECT meter_id, timestamp_utc FROM energy_readings WHERE is_valid = 1 "
        "ORDER BY timestamp_utc"
    ).fetchall()
    for r in rows:
        detect_rolling_baseline(slow, r["meter_id"], r["timestamp_utc"])
        detect_robust_mad(slow, r["meter_id"], r["timestamp_utc"])

    assert len(_keys(slow)) > 10, "test data should contain anomalies"
    assert _keys(fast) == _keys(slow)


def test_analysis_is_idempotent(csv_text):
    conn = _fresh(csv_text)
    first = analyze_all(conn)
    again = analyze_all(conn)
    assert first.anomalies_detected > 0
    assert again.anomalies_detected == 0
    assert again.incidents_created == 0
