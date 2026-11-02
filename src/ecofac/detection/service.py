# Copyright 2026 Lightibulb

"""Orchestration of detection, quality gating, and incident creation."""

import dataclasses
import sqlite3
import uuid

from ecofac.detection.anomaly import detect_robust_mad, detect_rolling_baseline
from ecofac.incidents.lifecycle import create_incident, find_event_incident


@dataclasses.dataclass
class Finding:
    """One anomaly finding, whether fresh or already recorded."""

    anomaly_id: str
    incident_id: str | None
    detector: str
    severity: str
    gated_by_quality: bool
    already_recorded: bool


def analyze_at(
    conn: sqlite3.Connection,
    meter_id: str,
    target_ts: str,
    *,
    create_incidents: bool = True,
) -> list[Finding]:
    """Run both detectors on one (meter, ts). Returns fresh and recorded findings.

    Each detector runs inside its own savepoint so that a failure during
    incident creation cannot leave an orphaned anomaly row.
    """
    findings: list[Finding] = []
    fresh_detectors: set[str] = set()

    for detect in (detect_rolling_baseline, detect_robust_mad):
        sp = f"sp_at_{uuid.uuid4().hex[:8]}"
        conn.execute(f"SAVEPOINT {sp}")
        try:
            anomaly = detect(conn, meter_id, target_ts)
            if anomaly is None:
                conn.execute(f"RELEASE SAVEPOINT {sp}")
                continue
            fresh_detectors.add(anomaly.detector_name)
            incident_id: str | None = None
            if anomaly.is_data_quality_eligible and create_incidents:
                try:
                    incident_id = create_incident(conn, anomaly)
                except (ValueError, sqlite3.IntegrityError):
                    conn.execute(f"ROLLBACK TO SAVEPOINT {sp}")
                    conn.execute(f"RELEASE SAVEPOINT {sp}")
                    continue
            conn.execute(f"RELEASE SAVEPOINT {sp}")
            findings.append(
                Finding(
                    anomaly_id=anomaly.id,
                    incident_id=incident_id,
                    detector=anomaly.detector_name,
                    severity=anomaly.severity,
                    gated_by_quality=not anomaly.is_data_quality_eligible,
                    already_recorded=False,
                )
            )
        except Exception:
            conn.execute(f"ROLLBACK TO SAVEPOINT {sp}")
            conn.execute(f"RELEASE SAVEPOINT {sp}")
            raise

    recorded = conn.execute(
        "SELECT id, detector_name, severity, is_data_quality_eligible FROM anomalies "
        "WHERE meter_id = ? AND timestamp_utc = ? ORDER BY detector_name",
        (meter_id, target_ts),
    ).fetchall()
    event_incident = find_event_incident(conn, meter_id, target_ts)
    for row in recorded:
        if row["detector_name"] in fresh_detectors:
            continue
        findings.append(
            Finding(
                anomaly_id=row["id"],
                incident_id=event_incident if row["is_data_quality_eligible"] else None,
                detector=row["detector_name"],
                severity=row["severity"],
                gated_by_quality=not row["is_data_quality_eligible"],
                already_recorded=True,
            )
        )

    return findings
