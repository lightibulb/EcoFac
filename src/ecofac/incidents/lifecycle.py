# Copyright 2026 Lightibulb

"""Manages incident state transitions and audit logging."""

import datetime as dt
import sqlite3
import uuid

from ecofac.domain.models import Anomaly
from ecofac.evidence.builder import build_computation_log

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
        (
            str(uuid.uuid4()),
            "INCIDENT",
            entity_id,
            action,
            details,
            dt.datetime.now(dt.UTC).isoformat(),
        ),
    )


def create_incident(conn: sqlite3.Connection, anomaly: Anomaly) -> str:
    """Create an incident and its associated evidence record."""
    incident_id = str(uuid.uuid4())
    now = dt.datetime.now(dt.UTC).isoformat()

    # Build evidence first (validates reading existence)
    try:
        computation_log = build_computation_log(conn, anomaly)
    except ValueError as e:
        msg = f"Cannot create incident: {e}"
        raise ValueError(msg) from e

    conn.execute("BEGIN")
    try:
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

        _log_audit(
            conn,
            incident_id,
            "CREATED",
            f"Incident created from anomaly {anomaly.id} with {anomaly.detector_name}",
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
    """Advance the incident state, enforcing rules and logging audit events."""
    cursor = conn.execute("SELECT status FROM incidents WHERE id = ?", (incident_id,))
    row = cursor.fetchone()
    if not row:
        msg = f"Incident {incident_id} not found."
        raise ValueError(msg)

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

    now = dt.datetime.now(dt.UTC).isoformat()

    conn.execute("BEGIN")
    try:
        if new_status == "DISMISSED":
            conn.execute(
                "UPDATE incidents SET status = ?, dismissal_reason = ?, updated_at = ? WHERE id = ?",
                (new_status, reason, now, incident_id),
            )
        elif new_status in ("RESOLVED", "UNRESOLVED"):
            conn.execute(
                "UPDATE incidents SET status = ?, resolution_notes = ?, updated_at = ? WHERE id = ?",
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
