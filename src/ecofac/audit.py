"""Append-only audit event logging shared by all write paths.

Every write path (decisions, scenarios, incidents, configs) records its
event here, so the audit_events INSERT shape lives in exactly one place.
If the table ever gains a column, one function changes, not three.
"""

import sqlite3
import uuid

from ecofac.timeutil import now_z


def log_audit(
    conn: sqlite3.Connection,
    entity_type: str,
    entity_id: str,
    action: str,
    details: str,
) -> None:
    """Append one immutable audit event.

    The caller is responsible for having the correct entity_type string
    ("DECISION", "SCENARIO", "INCIDENT", "DECISION_CONFIG", ...).
    """
    conn.execute(
        "INSERT INTO audit_events "
        "(id, entity_type, entity_id, action, details, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (str(uuid.uuid4()), entity_type, entity_id, action, details, now_z()),
    )
