# Copyright 2026 Lightibulb

"""Endpoints for the web dashboard."""

import os
import sqlite3
import tempfile
from typing import Annotated

from fastapi import APIRouter, Depends

from ecofac.api.dependencies import get_db
from ecofac.ingestion.pipeline import ingest_csv
from simulator.generate import generate_data

router = APIRouter()


@router.get("/dashboard/stats")
def get_dashboard_stats(
    conn: Annotated[sqlite3.Connection, Depends(get_db)],
) -> dict:
    """Get aggregated stats for the dashboard cards."""
    buildings = conn.execute("SELECT count(*) as c FROM buildings").fetchone()["c"]
    meters = conn.execute("SELECT count(*) as c FROM meters").fetchone()["c"]
    incidents = conn.execute("SELECT count(*) as c FROM incidents").fetchone()["c"]
    open_incidents = conn.execute(
        "SELECT count(*) as c FROM incidents "
        "WHERE status IN ('DETECTED', 'UNDER_REVIEW', 'ACKNOWLEDGED')"
    ).fetchone()["c"]

    return {
        "buildings": buildings,
        "meters": meters,
        "total_incidents": incidents,
        "open_incidents": open_incidents,
    }


@router.get("/dashboard/chart-data")
def get_chart_data(
    conn: Annotated[sqlite3.Connection, Depends(get_db)],
) -> dict:
    """Get the last 24 readings for the first available meter."""
    meter = conn.execute("SELECT id FROM meters LIMIT 1").fetchone()
    if not meter:
        return {"labels": [], "data": []}

    meter_id = meter["id"]
    cursor = conn.execute(
        "SELECT timestamp_utc, value FROM energy_readings "
        "WHERE meter_id = ? ORDER BY timestamp_utc DESC LIMIT 24",
        (meter_id,)
    )
    rows = cursor.fetchall()[::-1]

    return {
        "meter_id": meter_id,
        "labels": [row["timestamp_utc"][11:16] for row in rows],
        "data": [row["value"] for row in rows],
    }


@router.post("/dashboard/generate-demo")
def generate_demo_data(
    conn: Annotated[sqlite3.Connection, Depends(get_db)]
) -> dict:
    """Trigger the synthetic data generator, seed metadata, and ingest."""

    # 1. Generate data using a safe temporary directory
    with tempfile.TemporaryDirectory() as tmpdir:
        filepath = os.path.join(tmpdir, "synthetic_data.csv")
        generate_data(filename=filepath)

        with open(filepath, encoding="utf-8") as f:
            csv_content = f.read()

    # 2. Seed metadata
    now = "2026-01-01T00:00:00Z"
    conn.execute(
        "INSERT OR IGNORE INTO organizations (id, name, created_at) "
        "VALUES ('o1', 'Org', ?)",
        (now,)
    )
    for b in range(1, 6):
        conn.execute(
            "INSERT OR IGNORE INTO buildings (id, org_id, name, created_at) "
            "VALUES (?, 'o1', 'Bldg', ?)",
            (f"b{b}", now)
        )
        for m in range(1, 3):
            conn.execute(
                "INSERT OR IGNORE INTO meters "
                "(id, building_id, fuel_type, unit, created_at) "
                "VALUES (?, ?, 'elec', 'kWh', ?)",
                (f"m{b}-{m}", f"b{b}", now)
            )
    conn.execute(
        "INSERT OR IGNORE INTO data_sources (id, name, source_type, created_at) "
        "VALUES ('s1', 'Src', 'csv', ?)",
        (now,)
    )

    batch_id = f"batch-{now}"
    conn.execute(
        "INSERT OR IGNORE INTO import_batches "
        "(id, source_id, status, imported_at) VALUES (?, 's1', 'started', ?)",
        (batch_id, now)
    )

    # 3. Ingest
    summary = ingest_csv(conn, csv_content, "s1", batch_id)

    return {
        "status": "success",
        "message": f"Generated and ingested {summary.accepted_rows} rows."
    }
