# Copyright 2026 Lightibulb
"""Virtual Facility visualization endpoint.

Returns a single compact snapshot of the demo facility so the /world page
can render a data-driven visualization. Every value comes from the live
database; nothing is synthesized.

Query budget: ~9 indexed SELECTs, ~20 ms on the demo dataset.
No decision-engine calls. No LLM. No fabricated confidence.
"""

import sqlite3
from typing import Annotated

from fastapi import APIRouter, Depends

from ecofac.api.dependencies import get_db
from ecofac.timeutil import now_z

router = APIRouter(prefix="/world", tags=["Virtual Facility"])

OPEN_INCIDENT_STATUSES = ("DETECTED", "UNDER_REVIEW", "ACKNOWLEDGED")
RECENT_LIMIT = 15


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
    decision_counts = _decision_counts(conn)
    scenario_count = _scenario_count(conn)
    recent_events = _recent_events(conn)

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
        "SELECT id, status, precedence, created_at "
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
            "timestamp": row["created_at"],
        }
        for row in decisions
    ]

    events = incident_events + decision_events
    events.sort(key=lambda e: e["timestamp"], reverse=True)
    return events[:RECENT_LIMIT]
