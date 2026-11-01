# Copyright 2026 Lightibulb

"""Manages incident state transitions and audit logging."""

import sqlite3
import uuid

from ecofac.domain.models import Anomaly
from ecofac.evidence.builder import build_computation_log
from ecofac.timeutil import now_z

VALID_TRANSITIONS = {
    "DETECTED": ["UNDER_REVIEW"],
    "UNDER_REVIEW": ["ACKNOWLEDGED", "DISMISSED"],
    "ACKNOWLEDGED": ["RESOLVED", "UNRESOLVED"],
    "DISMISSED": [],       # Terminal
    "RESOLVED": [],        # Terminal
    "UNRESOLVED": [],      # Terminal
}


class InvalidStateTransitionError(Exception):
    """Raised when an invalid incident status transition is attempted."""


class IncidentNotFoundError(ValueError):
    """Raised when an incident id does not exist."""


def _log_audit(
    conn: sqlite3.Connection,
    entity_id: str,
    action: str,
    details: str,
) -> None:
    """Append an immutable audit event."""
    conn.execute(
        "INSERT INTO audit_events (id, entity_type, entity_id, action, details, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (str(uuid.uuid4()), "INCIDENT", entity_id, action, details, now_z()),
    )


def find_event_incident(conn: sqlite3.Connection, meter_id: str, timestamp_utc: str) -> str | None:
    """Return the id of the incident already tracking this (meter, timestamp) event."""
    row = conn.execute(
        "SELECT incidents.id FROM incidents "
        "JOIN anomalies ON anomalies.id = incidents.anomaly_id "
        "WHERE anomalies.meter_id = ? AND anomalies.timestamp_utc = ? "
        "ORDER BY incidents.created_at, incidents.id LIMIT 1",
        (meter_id, timestamp_utc),
    ).fetchone()
    return row["id"] if row else None


def create_incident(conn: sqlite3.Connection, anomaly: Anomaly) -> str:
    """Create an incident and its evidence record, idempotently.

    Several detectors can flag the same reading. They all describe one event,
    so the first anomaly opens the incident and later ones attach their
    evidence to it instead of opening duplicates. Calling this twice for the
    same anomaly returns the same incident.
    """
    # Build evidence first (validates reading existence)
    try:
        computation_log = build_computation_log(conn, anomaly)
    except ValueError as e:
        msg = f"Cannot create incident: {e}"
        raise ValueError(msg) from e

    now = now_z()
    conn.execute("BEGIN IMMEDIATE")
    try:
        same_anomaly = conn.execute(
            "SELECT id FROM incidents WHERE anomaly_id = ?", (anomaly.id,)
        ).fetchone()
        if same_anomaly:
            conn.execute("COMMIT")
            return same_anomaly["id"]

        event_incident = find_event_incident(conn, anomaly.meter_id, anomaly.timestamp_utc)
        incident_id = event_incident or str(uuid.uuid4())

        if event_incident is None:
            conn.execute(
                "INSERT INTO incidents (id, anomaly_id, status, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (incident_id, anomaly.id, "DETECTED", now, now),
            )

        conn.execute(
            "INSERT INTO evidence_records (id, incident_id, computation_log, created_at) "
            "VALUES (?, ?, ?, ?)",
            (str(uuid.uuid4()), incident_id, computation_log, now),
        )

        if event_incident is None:
            _log_audit(
                conn,
                incident_id,
                "CREATED",
                f"Incident created from anomaly {anomaly.id} with {anomaly.detector_name}",
            )
        else:
            _log_audit(
                conn,
                incident_id,
                "EVIDENCE_ADDED",
                f"Anomaly {anomaly.id} from {anomaly.detector_name} attached to incident",
            )
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise

    return incident_id


def transition_incident(
    conn: sqlite3.Connection,
    incident_id: str,
    new_status: str,
    reason: str | None = None,
) -> None:
    """Advance the incident state, enforcing rules and logging audit events.

    The status check and the update happen in one write transaction, so two
    concurrent requests cannot both pass the same transition.
    """
    reason = (reason or "").strip() or None

    conn.execute("BEGIN IMMEDIATE")
    try:
        row = conn.execute(
            "SELECT status FROM incidents WHERE id = ?", (incident_id,)
        ).fetchone()
        if not row:
            msg = f"Incident {incident_id} not found."
            raise IncidentNotFoundError(msg)

        current_status = row["status"]
        allowed = VALID_TRANSITIONS.get(current_status, [])

        if new_status not in allowed:
            msg = f"Invalid transition from {current_status} to {new_status}."
            raise InvalidStateTransitionError(msg)

        if new_status in ("RESOLVED", "UNRESOLVED") and not reason:
            msg = f"A reason is required to transition to {new_status}."
            raise ValueError(msg)

        if new_status == "DISMISSED" and not reason:
            msg = "A dismissal reason is required."
            raise ValueError(msg)

        now = now_z()
        if new_status == "DISMISSED":
            conn.execute(
                "UPDATE incidents SET status = ?, dismissal_reason = ?, "
                "updated_at = ? WHERE id = ?",
                (new_status, reason, now, incident_id),
            )
        elif new_status in ("RESOLVED", "UNRESOLVED"):
            conn.execute(
                "UPDATE incidents SET status = ?, resolution_notes = ?, "
                "updated_at = ? WHERE id = ?",
                (new_status, reason, now, incident_id),
            )
        else:
            conn.execute(
                "UPDATE incidents SET status = ?, updated_at = ? WHERE id = ?",
                (new_status, now, incident_id),
            )

        _log_audit(
            conn,
            incident_id,
            f"TRANSITION_{new_status}",
            f"Status changed to {new_status}. Reason: {reason or 'None'}",
        )
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
