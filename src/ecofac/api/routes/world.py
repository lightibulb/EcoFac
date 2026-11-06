# Copyright 2026 Lightibulb
"""Virtual Facility visualization endpoint.

Returns a single compact snapshot of the demo facility so the /world page
can render a data-driven visualization. Every value comes from the live
database; nothing is synthesized.

Query budget: ~9 indexed SELECTs, ~20 ms on the demo dataset.
No decision-engine calls. No LLM. No fabricated confidence.
"""

import json
import sqlite3
from typing import Annotated

from fastapi import APIRouter, Depends

from ecofac.api.dependencies import get_db
from ecofac.api.errors import api_error
from ecofac.decision.testdrive import (
    TEST_DRIVE_VERSION,
    UnknownPresetError,
    list_presets,
    run_test_drive,
)
from ecofac.timeutil import now_z

router = APIRouter(prefix="/world", tags=["Virtual Facility"])

OPEN_INCIDENT_STATUSES = ("DETECTED", "UNDER_REVIEW", "ACKNOWLEDGED")
RECENT_LIMIT = 15


@router.get("/test-drive/presets")
def test_drive_presets() -> dict:
    """List the hypothetical test-drive presets and their expected outcomes.

    Static: no database, no engine run. Nothing here is operational data.
    """
    return {
        "version": TEST_DRIVE_VERSION,
        "mode": "HYPOTHETICAL",
        "persisted": False,
        "presets": list_presets(),
    }


@router.get("/test-drive/{preset_id}")
def test_drive(preset_id: str) -> dict:
    """Run one hypothetical test drive through the canonical decision engine.

    Read-only by construction: this handler takes no database dependency, so
    it cannot create a decision, scenario or audit event. The response lists
    the exact inputs used, labels each as hypothetical, and includes the
    ``POST /scenarios/evaluate`` body that reproduces the result.
    """
    try:
        return run_test_drive(preset_id)
    except UnknownPresetError as exc:
        raise api_error(
            404,
            "unknown_test_drive_preset",
            f"Unknown test-drive preset: {preset_id}",
            valid_presets=[p["id"] for p in list_presets()],
        ) from exc


@router.get("/state")
def world_state(
    conn: Annotated[sqlite3.Connection, Depends(get_db)],
) -> dict:
    """Snapshot of facility state for the visualization.

    Shape:

    {
      "generated_at": "...Z",
      "buildings": [
        {"id": "b1", "name": "Bldg",
         "meters": [
           {"id": "m1-1", "fuel_type": "elec", "unit": "kWh",
            "interval_minutes": 60,
            "latest_reading": {"timestamp_utc": "...Z",
                               "value": 45.2, "is_valid": 1} | None,
            "open_incidents": 0,
            "total_incidents": 3}
         ]}
      ],
      "totals": {
        "buildings": 5, "meters": 10,
        "open_incidents": 12, "total_incidents": 54,
        "decisions": {"APPROVE": 40, "REJECT": 10, "REVIEW": 4},
        "scenarios": 8
      },
      "recent_events": [
        {"type": "incident", "id": "...", "meter_id": "m1-1",
         "status": "DETECTED", "severity": "high",
         "detector_name": "rolling_baseline", "timestamp": "...Z"},
        {"type": "decision", "id": "...", "status": "REJECT",
         "precedence": "hard_limit_violation", "timestamp": "...Z"}
      ]
    }
    """
    buildings = conn.execute(
        "SELECT id, name FROM buildings ORDER BY id"
    ).fetchall()

    meters = conn.execute(
        "SELECT id, building_id, fuel_type, unit, interval_minutes "
        "FROM meters ORDER BY id"
    ).fetchall()

    latest_by_meter = _latest_reading_per_meter(conn)
    open_inc = _count_per_meter(conn, "open_incidents")
    total_inc = _count_per_meter(conn, "total_incidents")
    _max_severity_per_meter(conn)
    decision_counts = _decision_counts(conn)
    scenario_count = _scenario_count(conn)
    recent_events = _recent_events(conn)
    max_sev = _max_severity_per_meter(conn)

    buildings_out = []
    for b in buildings:
        b_meters = [m for m in meters if m["building_id"] == b["id"]]
        buildings_out.append({
            "id": b["id"],
            "name": b["name"],
            "meters": [
                {
                    "id": m["id"],
                    "fuel_type": m["fuel_type"],
                    "unit": m["unit"],
                    "interval_minutes": m["interval_minutes"],
                    "latest_reading": latest_by_meter.get(m["id"]),
                    "open_incidents": open_inc.get(m["id"], 0),
                    "total_incidents": total_inc.get(m["id"], 0),
                        "max_severity": max_sev.get(m["id"], 0),
                }
                for m in b_meters
            ],
        })

    return {
        "generated_at": now_z(),
        "buildings": buildings_out,
        "totals": {
            "buildings": len(buildings),
            "meters": len(meters),
            "open_incidents": sum(open_inc.values()),
            "total_incidents": sum(total_inc.values()),
            "decisions": decision_counts,
            "scenarios": scenario_count,
        },
        "recent_events": recent_events,
    }


def _latest_reading_per_meter(
    conn: sqlite3.Connection,
) -> dict[str, dict]:
    """Return {meter_id: latest_reading_dict} in a single query."""
    rows = conn.execute(
        "SELECT r.meter_id, r.timestamp_utc, r.value, r.is_valid "
        "FROM energy_readings r "
        "INNER JOIN ("
        "  SELECT meter_id, MAX(timestamp_utc) AS max_ts "
        "  FROM energy_readings GROUP BY meter_id"
        ") m ON r.meter_id = m.meter_id "
        "AND r.timestamp_utc = m.max_ts"
    ).fetchall()
    return {
        row["meter_id"]: {
            "timestamp_utc": row["timestamp_utc"],
            "value": row["value"],
            "is_valid": row["is_valid"],
        }
        for row in rows
    }


def _max_severity_per_meter(conn: sqlite3.Connection) -> dict[str, int]:
    rows = conn.execute(
        "SELECT a.meter_id, MAX("
        "  CASE a.severity"
        "    WHEN 'high' THEN 3"
        "    WHEN 'medium' THEN 2"
        "    WHEN 'low' THEN 1"
        "    ELSE 0"
        "  END"
        ") as max_sev"
        " FROM incidents i"
        " JOIN anomalies a ON a.id = i.anomaly_id"
        " WHERE i.status IN ('DETECTED', 'UNDER_REVIEW', 'ACKNOWLEDGED')"
        " GROUP BY a.meter_id"
    ).fetchall()
    return {row["meter_id"]: row["max_sev"] for row in rows}

def _count_per_meter(
    conn: sqlite3.Connection, kind: str
) -> dict[str, int]:
    """Return {meter_id: incident_count}. ``kind`` selects open vs total."""
    if kind == "open_incidents":
        placeholders = ",".join("?" * len(OPEN_INCIDENT_STATUSES))
        rows = conn.execute(
            "SELECT a.meter_id, COUNT(*) AS c "
            "FROM incidents i "
            "JOIN anomalies a ON a.id = i.anomaly_id "
            f"WHERE i.status IN ({placeholders}) "
            "GROUP BY a.meter_id",
            OPEN_INCIDENT_STATUSES,
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT a.meter_id, COUNT(*) AS c "
            "FROM incidents i "
            "JOIN anomalies a ON a.id = i.anomaly_id "
            "GROUP BY a.meter_id"
        ).fetchall()
    return {row["meter_id"]: int(row["c"]) for row in rows}


def _decision_counts(conn: sqlite3.Connection) -> dict[str, int]:
    rows = conn.execute(
        "SELECT status, COUNT(*) AS c FROM decisions GROUP BY status"
    ).fetchall()
    return {row["status"]: int(row["c"]) for row in rows}


def _decision_mode(request_json: str | None) -> str | None:
    """Return ``"trusted"`` / ``"hypothetical"`` from a stored request, else None.

    Decisions persisted from hypothetical scenarios share the ``decisions``
    table with operational ones; the UI uses this to label them.
    """
    try:
        mode = json.loads(request_json or "{}").get("mode")
    except (ValueError, AttributeError):
        return None
    return mode if isinstance(mode, str) else None


def _scenario_count(conn: sqlite3.Connection) -> int:
    row = conn.execute("SELECT COUNT(*) AS c FROM scenarios").fetchone()
    return int(row["c"])


def _recent_events(conn: sqlite3.Connection) -> list[dict]:
    """Return last 15 events, newest first, mixing incidents and decisions."""
    incidents = conn.execute(
        "SELECT i.id, i.status, i.created_at, "
        "       a.meter_id, a.severity, a.detector_name "
        "FROM incidents i "
        "JOIN anomalies a ON a.id = i.anomaly_id "
        "ORDER BY i.created_at DESC, i.id DESC LIMIT ?",
        (RECENT_LIMIT,),
    ).fetchall()
    decisions = conn.execute(
        "SELECT id, status, precedence, created_at, request_json "
        "FROM decisions "
        "ORDER BY created_at DESC, id DESC LIMIT ?",
        (RECENT_LIMIT,),
    ).fetchall()

    incident_events = [
        {
            "type": "incident",
            "id": row["id"],
            "meter_id": row["meter_id"],
            "status": row["status"],
            "severity": row["severity"],
            "detector_name": row["detector_name"],
            "timestamp": row["created_at"],
        }
        for row in incidents
    ]
    decision_events = [
        {
            "type": "decision",
            "id": row["id"],
            "status": row["status"],
            "precedence": row["precedence"],
            "mode": _decision_mode(row["request_json"]),
            "timestamp": row["created_at"],
        }
        for row in decisions
    ]

    events = incident_events + decision_events
    events.sort(key=lambda e: e["timestamp"], reverse=True)
    return events[:RECENT_LIMIT]

