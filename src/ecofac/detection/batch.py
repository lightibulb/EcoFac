# Copyright 2026 Lightibulb
"""Bulk anomaly analysis.

Running both detectors against every reading costs several SQL queries per
reading. This module screens each meter's series in memory first (same
formulas, slightly looser thresholds, so screening can only over-select) and
then hands the few candidates to the authoritative detectors. The detectors
remain the single source of truth for what counts as an anomaly.
"""
import dataclasses
import logging
import math
import sqlite3

from ecofac.detection.anomaly import (
    DEFAULT_MIN_SAMPLES,
    MAD_Z_THRESHOLD,
    ROLLING_Z_THRESHOLD,
    ZERO_MAD_EPSILON,
    detect_robust_mad,
    detect_rolling_baseline,
)
from ecofac.incidents.lifecycle import create_incident

logger = logging.getLogger(__name__)

# Slice size for the in-memory screen (previous N readings).
SCREEN_WINDOW = 24
SCREEN_MARGIN = 0.98
MAD_SCALE = 1.4826


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
    """Analyze every valid reading of one meter. Returns (summary, incident ids).

    The loop starts at DEFAULT_MIN_SAMPLES (not SCREEN_WINDOW) so that bulk
    analysis covers the same candidate space as a per-reading pass. For
    irregular or gappy data, the slice is capped at index 0 so ``history``
    never wraps negatively.
    """
    summary = AnalysisSummary()
    incident_ids: set[str] = set()
    rows = conn.execute(
        "SELECT timestamp_utc, value FROM energy_readings "
        "WHERE meter_id = ? AND is_valid = 1 ORDER BY timestamp_utc",
        (meter_id,),
    ).fetchall()
    values = [row["value"] for row in rows]

    for i in range(DEFAULT_MIN_SAMPLES, len(rows)):
        history = values[max(0, i - SCREEN_WINDOW):i]
        if len(history) < 2:
            continue
        if not is_candidate(history, values[i]):
            continue
        timestamp = rows[i]["timestamp_utc"]
        for detect in (detect_rolling_baseline, detect_robust_mad):
            anomaly = detect(conn, meter_id, timestamp)
            if anomaly is None:
                continue
            summary.anomalies_detected += 1
            if not anomaly.is_data_quality_eligible:
                summary.anomalies_gated += 1
                continue
            if not create_incidents:
                continue
            try:
                incident_ids.add(create_incident(conn, anomaly))
            except ValueError:
                logger.warning(
                    "Skipped incident for anomaly %s", anomaly.id, exc_info=True
                )
                summary.skipped += 1
    return summary, incident_ids


def analyze_all(
    conn: sqlite3.Connection,
    *,
    meter_ids: list[str] | None = None,
    create_incidents: bool = True,
) -> AnalysisSummary:
    """Run both detectors over every valid reading of the given (or all) meters.

    Anomalies already recorded are skipped, so this is safe to call repeatedly.
    """
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
