# Copyright 2026 Lightibulb

"""Builds reproducible evidence records for anomalies."""

import json
import sqlite3

from ecofac.domain.models import Anomaly


def _build_explanation(
    detector_name: str,
    observed: float,
    expected: float,
    config: dict,
    sample_count: int,
) -> str:
    """Compose a human-readable reason for the classification."""
    det = detector_name.replace("_", " ")
    z = config.get("z_score")
    thr = config.get("z_threshold")
    if isinstance(z, int | float) and isinstance(thr, int | float):
        score_part = f"z-score {float(z):.2f} vs threshold {float(thr):.2f}"
    elif isinstance(z, int | float):
        score_part = f"z-score {float(z):.2f}"
    else:
        score_part = "score not recorded"
    return (
        f"{det}: observed {observed:.2f} kWh vs expected "
        f"{expected:.2f} kWh; {score_part}; "
        f"{sample_count} historical reading(s)"
    )


def build_computation_log(conn: sqlite3.Connection, anomaly: Anomaly) -> str:
    """Construct a JSON evidence payload, validating all reading references."""
    # 1. Validate supporting observation IDs
    supporting_ids = anomaly.supporting_observation_ids
    if not supporting_ids:
        msg = "Anomaly has no supporting observations."
        raise ValueError(msg)

    placeholders = ",".join(["?"] * len(supporting_ids))
    query = (
        "SELECT id, timestamp_utc, value, raw_value, source_id "
        "FROM energy_readings "
        f"WHERE id IN ({placeholders}) "
        "ORDER BY timestamp_utc, id"
    )
    cursor = conn.execute(query, supporting_ids)
    rows = cursor.fetchall()

    if len(rows) != len(set(supporting_ids)):
        msg = "Supporting observation IDs reference nonexistent readings."
        raise ValueError(msg)

    # 2. Target reading
    cursor = conn.execute(
        "SELECT id, timestamp_utc, value, raw_value FROM energy_readings "
        "WHERE meter_id = ? AND timestamp_utc = ?",
        (anomaly.meter_id, anomaly.timestamp_utc),
    )
    target = cursor.fetchone()
    if not target:
        msg = "Target reading does not exist."
        raise ValueError(msg)

    # 3. Parse detector config (older records may lack newer keys)
    config = json.loads(anomaly.detection_parameters or "{}")
    version = config.get("detector_version", "unknown")
    sample_count = config.get("sample_count", len(rows))

    # 4. Explicit reading references (target first, then sorted history)
    reading_ids = [target["id"]] + [r["id"] for r in rows]

    # 5. Human-readable explanation
    explanation = _build_explanation(
        anomaly.detector_name,
        anomaly.observed_consumption,
        anomaly.expected_consumption,
        config,
        sample_count,
    )

    # 6. Deterministic payload (fixed ordering, so the same inputs always
    #    produce the same bytes — required for reproducibility)
    payload = {
        "anomaly_id": anomaly.id,
        "building_id": anomaly.building_id,
        "meter_id": anomaly.meter_id,
        "severity": anomaly.severity,
        "detector_name": anomaly.detector_name,
        "detector_version": version,
        "detector_configuration": config,
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
        "reading_ids": reading_ids,
        "historical_comparison_window": [
            {
                "id": r["id"],
                "timestamp": r["timestamp_utc"],
                "value": r["value"],
                "source_id": r["source_id"],
            }
            for r in rows
        ],
        "data_quality_eligible": anomaly.is_data_quality_eligible,
        "explanation": explanation,
    }

    return json.dumps(payload, indent=2, sort_keys=True)
