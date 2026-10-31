# Copyright 2026 Lightibulb

"""Builds reproducible evidence records for anomalies."""

import json
import sqlite3

from ecofac.domain.models import Anomaly


def build_computation_log(conn: sqlite3.Connection, anomaly: Anomaly) -> str:
    """Construct a JSON evidence payload, validating all reading references."""
    # 1. Validate that all supporting observation IDs actually exist
    supporting_ids = anomaly.supporting_observation_ids
    if not supporting_ids:
        msg = "Anomaly has no supporting observations."
        raise ValueError(msg)

    placeholders = ",".join(["?"] * len(supporting_ids))
    query = (
        "SELECT id, timestamp_utc, value, raw_value, source_id "
        "FROM energy_readings "
        f"WHERE id IN ({placeholders})"
    )

    cursor = conn.execute(query, supporting_ids)
    rows = cursor.fetchall()

    if len(rows) != len(supporting_ids):
        msg = "Supporting observation IDs reference nonexistent readings."
        raise ValueError(msg)

    # 2. Identify the target reading
    cursor = conn.execute(
        "SELECT id, timestamp_utc, value, raw_value FROM energy_readings "
        "WHERE meter_id = ? AND timestamp_utc = ?",
        (anomaly.meter_id, anomaly.timestamp_utc),
    )
    target = cursor.fetchone()
    if not target:
        msg = "Target reading does not exist."
        raise ValueError(msg)

    # 3. Assemble the reproducible payload
    payload = {
        "detector_name": anomaly.detector_name,
        "detector_configuration": json.loads(anomaly.detection_parameters),
        "observation_timestamp": anomaly.timestamp_utc,
        "target_measurement": {
            "id": target["id"],
            "value": target["value"],
            "raw_value": target["raw_value"],
        },
        "baseline_calculation": {
            "expected_value": anomaly.expected_consumption,
            "absolute_deviation": anomaly.absolute_deviation,
            "percentage_deviation": anomaly.percentage_deviation,
        },
        "historical_comparison_window": [
            {
                "id": r["id"],
                "timestamp": r["timestamp_utc"],
                "value": r["value"],
                "source_id": r["source_id"],
            } for r in rows
        ],
        "data_quality_eligible": anomaly.is_data_quality_eligible,
    }

    return json.dumps(payload, indent=2)
