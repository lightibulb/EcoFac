# Copyright 2026 Lightibulb
"""Metrics v2 correctness tests (FPR, FNR, candidate counting)."""

import datetime as dt
import json

import pytest

from ecofac.domain.models import EnergyReading
from ecofac.persistence.database import get_connection, init_db
from ecofac.persistence.repository import insert_reading
from ecofac.timeutil import to_z
from evaluation.metrics import compare_configs, evaluate_detection_v2

BASE = dt.datetime(2026, 1, 1, tzinfo=dt.UTC)


@pytest.fixture
def db_conn():
    conn = get_connection(":memory:")
    init_db(conn)
    now = "2026-01-01T00:00:00Z"
    conn.execute(
        "INSERT INTO organizations (id, name, created_at) VALUES ('o1', 'Org', ?)",
        (now,),
    )
    conn.execute(
        "INSERT INTO buildings (id, org_id, name, created_at) VALUES ('b1', 'o1', 'B', ?)",
        (now,),
    )
    conn.execute(
        "INSERT INTO meters (id, building_id, fuel_type, unit, created_at) "
        "VALUES ('m1', 'b1', 'elec', 'kWh', ?)",
        (now,),
    )
    conn.execute(
        "INSERT INTO data_sources (id, name, source_type, created_at) "
        "VALUES ('s1', 'Src', 'csv', ?)",
        (now,),
    )
    conn.execute(
        "INSERT INTO import_batches (id, source_id, status, imported_at) "
        "VALUES ('ib1', 's1', 'started', ?)",
        (now,),
    )
    yield conn
    conn.close()


def _insert_reading(conn, rid, ts, val):
    insert_reading(
        conn,
        EnergyReading(
            id=rid,
            meter_id="m1",
            timestamp_utc=ts,
            value=val,
            unit="kWh",
            source_id="s1",
            batch_id="ib1",
            is_valid=True,
            created_at="2026-01-01T00:00:00Z",
        ),
    )


def _insert_anomaly(conn, aid, ts, z):
    conn.execute(
        "INSERT INTO anomalies (id, building_id, meter_id, timestamp_utc, "
        "observed_consumption, expected_consumption, absolute_deviation, "
        "percentage_deviation, detector_name, severity, is_data_quality_eligible, "
        "detection_timestamp, detection_parameters) "
        "VALUES (?, 'b1', 'm1', ?, 100, 10, 90, 900, 'rolling_baseline', 'high', 1, "
        "'2026-01-01T00:00:00Z', ?)",
        (aid, ts, json.dumps({"z_score": z})),
    )


def _truth_file(tmp_path, timestamps):
    path = tmp_path / "gt.json"
    path.write_text(
        json.dumps([{"meter_id": "m1", "timestamp": ts} for ts in timestamps]),
        encoding="utf-8",
    )
    return str(path)


def test_perfect_case_yields_all_ones(db_conn, tmp_path):
    """30 readings, 2 anomalies matching truth -> precision=recall=1, FPR=0."""
    for i in range(30):
        _insert_reading(db_conn, f"r{i}", to_z(BASE + dt.timedelta(hours=i)), 10.0)
    gt_ts = [to_z(BASE + dt.timedelta(hours=h)) for h in (20, 25)]
    _insert_anomaly(db_conn, "a20", gt_ts[0], 5.0)
    _insert_anomaly(db_conn, "a25", gt_ts[1], 5.0)
    metrics = evaluate_detection_v2(db_conn, _truth_file(tmp_path, gt_ts), min_samples=20)
    assert metrics["precision"] == 1.0
    assert metrics["recall"] == 1.0
    assert metrics["f1"] == 1.0
    assert metrics["false_positive_rate"] == 0.0
    assert metrics["false_negative_rate"] == 0.0
    assert metrics["true_positives"] == 2
    assert metrics["true_negatives"] == 8
    assert metrics["candidates_evaluated"] == 10


def test_missed_anomaly_shows_in_recall(db_conn, tmp_path):
    """Two GT labels, one anomaly emitted -> recall 0.5, FNR 0.5."""
    for i in range(30):
        _insert_reading(db_conn, f"r{i}", to_z(BASE + dt.timedelta(hours=i)), 10.0)
    gt_ts = [to_z(BASE + dt.timedelta(hours=h)) for h in (20, 25)]
    _insert_anomaly(db_conn, "a20", gt_ts[0], 5.0)
    metrics = evaluate_detection_v2(db_conn, _truth_file(tmp_path, gt_ts), min_samples=20)
    assert metrics["recall"] == 0.5
    assert metrics["false_negative_rate"] == 0.5
    assert metrics["true_positives"] == 1
    assert metrics["false_negatives"] == 1


def test_empty_db_no_crash(db_conn, tmp_path):
    """No readings, no anomalies -> all metrics zero, delay is None."""
    metrics = evaluate_detection_v2(db_conn, _truth_file(tmp_path, []), min_samples=20)
    assert metrics["precision"] == 0.0
    assert metrics["recall"] == 0.0
    assert metrics["candidates_evaluated"] == 0
    assert metrics["detection_delay_sec_mean"] is None


def test_warmup_eligibility(db_conn, tmp_path):
    """Readings before the min_samples warm-up are not eligible candidates."""
    for i in range(25):
        _insert_reading(db_conn, f"r{i}", to_z(BASE + dt.timedelta(hours=i)), 10.0)
    metrics = evaluate_detection_v2(db_conn, _truth_file(tmp_path, []), min_samples=20)
    # 25 readings, first 20 are warm-up -> 5 eligible candidates, all TN.
    assert metrics["candidates_evaluated"] == 5
    assert metrics["true_negatives"] == 5


def test_threshold_ordering_monotonic(db_conn, tmp_path):
    """Stricter z-threshold -> fewer emitted, non-increasing recall."""
    for i in range(30):
        _insert_reading(db_conn, f"r{i}", to_z(BASE + dt.timedelta(hours=i)), 10.0)
    gt_ts = [to_z(BASE + dt.timedelta(hours=h)) for h in range(20, 25)]
    for i, z in enumerate([5.0, 4.0, 3.5, 3.0, 2.0]):
        _insert_anomaly(db_conn, f"a{i}", gt_ts[i], z)
    results = compare_configs(
        db_conn,
        _truth_file(tmp_path, gt_ts),
        [{"z_threshold": 2.0}, {"z_threshold": 3.0}, {"z_threshold": 4.0}],
    )
    emitted = [r["emitted"] for r in results]
    recalls = [r["recall"] for r in results]
    assert emitted == sorted(emitted, reverse=True)
    assert recalls == sorted(recalls, reverse=True)


def test_detection_delay_one_to_one_matching(db_conn, tmp_path):
    """A single ground-truth label must not contribute multiple delays.

    Regression for DEF-04: three predictions near one GT label used to each
    contribute their own delay, inflating the mean. One-to-one matching keeps
    only the closest pair.
    """
    for i in range(30):
        _insert_reading(db_conn, f"r{i}", to_z(BASE + dt.timedelta(hours=i)), 10.0)
    gt_ts = to_z(BASE + dt.timedelta(hours=25))
    _insert_anomaly(db_conn, "a24_50", to_z(BASE + dt.timedelta(hours=24, minutes=50)), 5.0)
    _insert_anomaly(db_conn, "a25_00", gt_ts, 5.0)
    _insert_anomaly(db_conn, "a25_10", to_z(BASE + dt.timedelta(hours=25, minutes=10)), 5.0)
    metrics = evaluate_detection_v2(
        db_conn, _truth_file(tmp_path, [gt_ts]), min_samples=20
    )
    # Only the closest pair (delta = 0) contributes.
    # Pre-fix behaviour (all three contribute) would report mean = 400 s.
    assert metrics["detection_delay_sec_mean"] == 0.0
