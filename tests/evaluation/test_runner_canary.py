# Copyright 2026 Lightibulb
"""Canary test: the end-to-end analysis path must detect planted anomalies.

If this test ever fails, the evaluation pipeline has gone silently blind
again (the exact failure mode of the original meter_id bug).
"""

import datetime as dt

from ecofac.domain.models import EnergyReading
from ecofac.persistence.database import get_connection, init_db
from ecofac.persistence.repository import insert_reading
from evaluation.runner import analyze_all, seed_metadata


def test_canary_spikes_are_detected():
    """Plant 3 spikes in flat data; the pipeline must find them."""
    conn = get_connection(":memory:")
    init_db(conn)
    seed_metadata(conn)

    base = dt.datetime(2026, 1, 1, tzinfo=dt.UTC)

    def add_reading_row(reading_id, hour, value):
        ts = base + dt.timedelta(hours=hour)
        insert_reading(
            conn,
            EnergyReading(
                id=reading_id,
                meter_id="m1-1",
                timestamp_utc=ts.isoformat().replace("+00:00", "Z"),
                value=value,
                unit="kWh",
                source_id="s1",
                batch_id="ib1",
                is_valid=True,
                created_at="2026-01-01T00:00:00Z",
            ),
        )

    for hour in range(48):
        add_reading_row(f"c{hour}", hour, 10.0)
    for i, hour in enumerate([48, 49, 50]):
        add_reading_row(f"spike{i}", hour, 100.0)

    detected = analyze_all(conn)

    anomalies = conn.execute("SELECT count(*) AS c FROM anomalies").fetchone()["c"]
    incidents = conn.execute("SELECT count(*) AS c FROM incidents").fetchone()["c"]
    assert detected >= 3, "Pipeline is silently blind to planted spikes"
    assert anomalies >= 3, "Anomaly rows were not persisted"
    assert incidents >= 3, "Eligible anomalies did not produce incidents"
    conn.close()
