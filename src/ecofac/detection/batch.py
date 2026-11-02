# Copyright 2026 Lightibulb

"""Bulk anomaly analysis using in-memory screening."""

import dataclasses
import logging
import math
import sqlite3

from ecofac.constants import (
    DETECTION_WINDOW_SIZE,
    MAD_SCALE,
    MAD_Z_THRESHOLD,
    ROLLING_Z_THRESHOLD,
    SCREEN_MARGIN,
    ZERO_MAD_EPSILON,
)
from ecofac.detection.service import analyze_at

logger = logging.getLogger(__name__)

WINDOW_SIZE = DETECTION_WINDOW_SIZE


@dataclasses.dataclass
class AnalysisSummary:
    """Outcome of a bulk analysis run."""

    anomalies_detected: int = 0
    anomalies_gated: int = 0
    incidents_created: int = 0
    skipped: int = 0


def _median(sorted_values: list[float]) -> float:
    mid = len(sorted_values) // 2
    if len(sorted_values) % 2:
        return sorted_values[mid]
    return (sorted_values[mid - 1] + sorted_values[mid]) / 2


def is_candidate(history: list[float], observed: float) -> bool:
    """Cheap pre-check: could either detector flag this observation?"""
    mean = sum(history) / len(history)
    variance = sum((v - mean) ** 2 for v in history) / (len(history) - 1)
    stdev = math.sqrt(variance) or ZERO_MAD_EPSILON
    if abs(observed - mean) / stdev >= ROLLING_Z_THRESHOLD * SCREEN_MARGIN:
        return True
    median = _median(sorted(history))
    mad = _median(sorted(abs(v - median) for v in history))
    scaled_mad = MAD_SCALE * mad or ZERO_MAD_EPSILON
    return abs(observed - median) / scaled_mad >= MAD_Z_THRESHOLD * SCREEN_MARGIN


def analyze_meter_series(
    conn: sqlite3.Connection,
    meter_id: str,
    *,
    create_incidents: bool = True,
) -> tuple[AnalysisSummary, set[str]]:
    """Analyze every valid reading of one meter. Returns (summary, incident ids)."""
    summary = AnalysisSummary()
    incident_ids: set[str] = set()
    rows = conn.execute(
        "SELECT timestamp_utc, value FROM energy_readings "
        "WHERE meter_id = ? AND is_valid = 1 ORDER BY timestamp_utc",
        (meter_id,),
    ).fetchall()
    values = [row["value"] for row in rows]

    for i in range(WINDOW_SIZE, len(rows)):
        if not is_candidate(values[i - WINDOW_SIZE:i], values[i]):
            continue
        timestamp = rows[i]["timestamp_utc"]
        try:
            findings = analyze_at(
                conn, meter_id, timestamp, create_incidents=create_incidents
            )
        except Exception:
            logger.exception("Analysis failed for %s @ %s", meter_id, timestamp)
            summary.skipped += 1
            continue
        for f in findings:
            if f.already_recorded:
                continue
            summary.anomalies_detected += 1
            if f.gated_by_quality:
                summary.anomalies_gated += 1
            if f.incident_id:
                incident_ids.add(f.incident_id)
    return summary, incident_ids


def analyze_all(
    conn: sqlite3.Connection,
    *,
    meter_ids: list[str] | None = None,
    create_incidents: bool = True,
) -> AnalysisSummary:
    """Run both detectors over every valid reading of the given (or all) meters."""
    if meter_ids is None:
        meter_ids = [
            row["meter_id"]
            for row in conn.execute(
                "SELECT DISTINCT meter_id FROM energy_readings ORDER BY meter_id"
            ).fetchall()
        ]
    total = AnalysisSummary()
    all_incidents: set[str] = set()
    for meter_id in sorted(set(meter_ids)):
        part, incident_ids = analyze_meter_series(
            conn, meter_id, create_incidents=create_incidents
        )
        total.anomalies_detected += part.anomalies_detected
        total.anomalies_gated += part.anomalies_gated
        total.skipped += part.skipped
        all_incidents |= incident_ids
    total.incidents_created = len(all_incidents)
    return total
