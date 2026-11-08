# Copyright 2026 Lightibulb

"""Endpoints for the web dashboard."""

import json
import pathlib
import sqlite3
import tempfile
import threading
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query

from ecofac.api.dependencies import get_db, require_write_access
from ecofac.detection.batch import analyze_all
from ecofac.ingestion.pipeline import ingest_csv
from ecofac.timeutil import now_z
from simulator.generate import generate_data

router = APIRouter()

DEMO_SOURCE_ID = "s1"
DEMO_BATCH_ID = "demo-batch"
DEMO_BUILDINGS = 5
DEMO_METERS_PER_BUILDING = 2
_demo_lock = threading.Lock()


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
    meter_id: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 24,
) -> dict:
    """Get the latest valid readings for one meter.

    Defaults to the first meter (by id) that has valid readings, so the chart
    does not jump between meters as new data arrives. Invalid readings
    (e.g. negative consumption) are never plotted.
    """
    if meter_id is None:
        meter = conn.execute(
            "SELECT m.id FROM meters m WHERE EXISTS ("
            "SELECT 1 FROM energy_readings r WHERE r.meter_id = m.id AND r.is_valid = 1"
            ") ORDER BY m.id LIMIT 1"
        ).fetchone()
        if not meter:
            return {"meter_id": None, "labels": [], "data": []}
        meter_id = meter["id"]
    elif not conn.execute("SELECT 1 FROM meters WHERE id = ?", (meter_id,)).fetchone():
        raise HTTPException(status_code=404, detail="Meter not found")

    rows = conn.execute(
        "SELECT timestamp_utc, value FROM energy_readings "
        "WHERE meter_id = ? AND is_valid = 1 ORDER BY timestamp_utc DESC LIMIT ?",
        (meter_id, limit),
    ).fetchall()[::-1]

    # Show the date too once the window spans more than one day.
    multi_day = bool(rows) and rows[0]["timestamp_utc"][:10] != rows[-1]["timestamp_utc"][:10]
    start = 5 if multi_day else 11
    return {
        "meter_id": meter_id,
        "labels": [row["timestamp_utc"][start:16].replace("T", " ") for row in rows],
        "data": [row["value"] for row in rows],
    }


def _seed_demo_metadata(conn: sqlite3.Connection, now: str) -> None:
    conn.execute(
        "INSERT OR IGNORE INTO organizations (id, name, created_at) VALUES ('o1', 'Org', ?)",
        (now,),
    )
    for b in range(1, DEMO_BUILDINGS + 1):
        conn.execute(
            "INSERT OR IGNORE INTO buildings (id, org_id, name, created_at) "
            "VALUES (?, 'o1', 'Bldg', ?)",
            (f"b{b}", now),
        )
        for m in range(1, DEMO_METERS_PER_BUILDING + 1):
            role = "IT_LOAD" if m == 1 else ("COOLING" if m == 2 else "HVAC")
            tol = 0.02 if m == 1 else (0.03 if m == 2 else 0.05)
            conn.execute(
                "INSERT OR IGNORE INTO meters "
                "(id, building_id, fuel_type, unit, role, relative_tolerance, created_at) "
                "VALUES (?, ?, 'elec', 'kWh', ?, ?, ?)",
                (f"m{b}-{m}", f"b{b}", role, tol, now),
            )
    conn.execute(
        "INSERT OR IGNORE INTO data_sources (id, name, source_type, created_at) "
        "VALUES (?, 'Src', 'csv', ?)",
        (DEMO_SOURCE_ID, now),
    )
    conn.execute(
        "INSERT OR IGNORE INTO import_batches (id, source_id, status, imported_at) "
        "VALUES (?, ?, 'started', ?)",
        (DEMO_BATCH_ID, DEMO_SOURCE_ID, now),
    )


@router.post("/dashboard/generate-demo")
def generate_demo_data(
    conn: Annotated[sqlite3.Connection, Depends(get_db)],
    _auth: Annotated[None, Depends(require_write_access)],
    seed: Annotated[int | None, Query()] = None,
) -> dict:
    """Load the synthetic demo data once, then run anomaly analysis on it.

    Safe to click repeatedly: demo readings are never re-imported (that used
    to flood the quality log with duplicate issues), and analysis skips
    anomalies that are already recorded.
    """
    if not _demo_lock.acquire(blocking=False):
        raise HTTPException(status_code=409, detail="Demo generation is already running")
    try:
        accepted = 0
        already_loaded = conn.execute(
            "SELECT 1 FROM energy_readings WHERE source_id = ? AND batch_id = ? LIMIT 1",
            (DEMO_SOURCE_ID, DEMO_BATCH_ID),
        ).fetchone()
        if not already_loaded:
            with tempfile.TemporaryDirectory() as tmpdir:
                filepath = pathlib.Path(tmpdir) / "synthetic_data.csv"
                manifest = generate_data(filename=str(filepath), seed=seed)
                csv_content = filepath.read_text(encoding="utf-8")
            _seed_demo_metadata(conn, now_z())
            summary = ingest_csv(conn, csv_content, DEMO_SOURCE_ID, DEMO_BATCH_ID)
            accepted = summary.accepted_rows
            conn.execute(
                "UPDATE import_batches SET status = 'completed', summary_json = ? WHERE id = ?",
                (json.dumps({"manifest": manifest}), DEMO_BATCH_ID),
            )

        analysis = analyze_all(conn)
    finally:
        _demo_lock.release()

    if already_loaded:
        message = (
            "Demo data was already loaded. Analysis refreshed: "
            f"{analysis.anomalies_detected} new anomalies, "
            f"{analysis.incidents_created} incidents."
        )
    else:
        message = (
            f"Generated and ingested {accepted} rows. Found "
            f"{analysis.anomalies_detected} anomalies, "
            f"{analysis.incidents_created} incidents."
        )
    return {
        "status": "success",
        "message": message,
        "accepted_rows": accepted,
        "anomalies_detected": analysis.anomalies_detected,
        "incidents_created": analysis.incidents_created,
    }



# --- LMF-08-APPLIED-V2: separate Re-analyse action + run manifest -----------
@router.post(
    "/dashboard/reanalyze", dependencies=[Depends(require_write_access)]
)
def reanalyze_endpoint(
    conn: Annotated[sqlite3.Connection, Depends(get_db)],
) -> dict:
    """Re-run anomaly analysis over existing readings (no new data)."""
    result = analyze_all(conn)
    summary = result[0] if isinstance(result, tuple) and result else result
    conn.commit()
    return {
        "status": "reanalyzed",
        "anomalies_detected": getattr(summary, "anomalies_detected", None),
        "anomalies_gated": getattr(summary, "anomalies_gated", None),
        "skipped": getattr(summary, "skipped", None),
        "incidents_created": getattr(summary, "incidents_created", None),
    }


@router.get("/dashboard/run-manifest")
def run_manifest_endpoint(
    conn: Annotated[sqlite3.Connection, Depends(get_db)],
) -> dict:
    """Live manifest of the current run: what is in the database now."""
    counts = {
        "buildings": conn.execute(
            "SELECT COUNT(*) AS n FROM buildings"
        ).fetchone()["n"],
        "meters": conn.execute(
            "SELECT COUNT(*) AS n FROM meters"
        ).fetchone()["n"],
        "readings": conn.execute(
            "SELECT COUNT(*) AS n FROM energy_readings"
        ).fetchone()["n"],
        "data_quality_issues": conn.execute(
            "SELECT COUNT(*) AS n FROM data_quality_issues"
        ).fetchone()["n"],
        "anomalies": conn.execute(
            "SELECT COUNT(*) AS n FROM anomalies"
        ).fetchone()["n"],
        "incidents": conn.execute(
            "SELECT COUNT(*) AS n FROM incidents"
        ).fetchone()["n"],
        "decisions": conn.execute(
            "SELECT COUNT(*) AS n FROM decisions"
        ).fetchone()["n"],
        "scenarios": conn.execute(
            "SELECT COUNT(*) AS n FROM scenarios"
        ).fetchone()["n"],
    }
    latest_reading = conn.execute(
        "SELECT MAX(timestamp_utc) AS ts FROM energy_readings"
    ).fetchone()["ts"]
    latest_detection = conn.execute(
        "SELECT MAX(detection_timestamp) AS ts FROM anomalies"
    ).fetchone()["ts"]
    latest_incident = conn.execute(
        "SELECT MAX(created_at) AS ts FROM incidents"
    ).fetchone()["ts"]
    return {
        "generated_at": now_z(),
        "counts": counts,
        "latest_reading_timestamp": latest_reading,
        "latest_detection_timestamp": latest_detection,
        "latest_incident_at": latest_incident,
    }
