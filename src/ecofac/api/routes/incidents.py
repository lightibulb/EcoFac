# Copyright 2026 Lightibulb
"""Endpoints for incident management."""
import sqlite3
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query

from ecofac.api.dependencies import get_db
from ecofac.api.schemas import EvidenceResponse, IncidentActionRequest, IncidentResponse
from ecofac.incidents.lifecycle import InvalidStateTransitionError, transition_incident

router = APIRouter()


def _transition_and_fetch(
    conn: sqlite3.Connection,
    incident_id: str,
    new_status: str,
    reason: str | None = None,
) -> IncidentResponse:
    """Apply a lifecycle transition, mapping domain errors to HTTP codes."""
    try:
        transition_incident(conn, incident_id, new_status, reason=reason)
    except InvalidStateTransitionError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    cursor = conn.execute("SELECT * FROM incidents WHERE id = ?", (incident_id,))
    return IncidentResponse(**dict(cursor.fetchone()))


@router.get("/incidents", tags=["Incidents"])
def list_incidents(
    conn: Annotated[sqlite3.Connection, Depends(get_db)],
    limit: Annotated[int, Query(ge=1, le=100)] = 10,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[IncidentResponse]:
    """List incidents."""
    cursor = conn.execute("SELECT * FROM incidents LIMIT ? OFFSET ?", (limit, offset))
    return [IncidentResponse(**dict(row)) for row in cursor.fetchall()]


@router.get("/incidents/{incident_id}", tags=["Incidents"])
def get_incident(
    incident_id: str,
    conn: Annotated[sqlite3.Connection, Depends(get_db)],
) -> IncidentResponse:
    """Get an incident by ID."""
    cursor = conn.execute("SELECT * FROM incidents WHERE id = ?", (incident_id,))
    row = cursor.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Incident not found")
    return IncidentResponse(**dict(row))


@router.post("/incidents/{incident_id}/review", tags=["Incidents"])
def review_incident(
    incident_id: str,
    conn: Annotated[sqlite3.Connection, Depends(get_db)],
) -> IncidentResponse:
    """Transition an incident to UNDER_REVIEW (requires DETECTED)."""
    return _transition_and_fetch(conn, incident_id, "UNDER_REVIEW")


@router.post("/incidents/{incident_id}/acknowledge", tags=["Incidents"])
def acknowledge_incident(
    incident_id: str,
    payload: IncidentActionRequest,
    conn: Annotated[sqlite3.Connection, Depends(get_db)],
) -> IncidentResponse:
    """Transition an incident to ACKNOWLEDGED (requires UNDER_REVIEW)."""
    return _transition_and_fetch(conn, incident_id, "ACKNOWLEDGED", reason=payload.reason)


@router.post("/incidents/{incident_id}/resolve", tags=["Incidents"])
def resolve_incident(
    incident_id: str,
    payload: IncidentActionRequest,
    conn: Annotated[sqlite3.Connection, Depends(get_db)],
) -> IncidentResponse:
    """Transition an incident to RESOLVED (requires ACKNOWLEDGED, reason required)."""
    return _transition_and_fetch(conn, incident_id, "RESOLVED", reason=payload.reason)


@router.post("/incidents/{incident_id}/dismiss", tags=["Incidents"])
def dismiss_incident(
    incident_id: str,
    payload: IncidentActionRequest,
    conn: Annotated[sqlite3.Connection, Depends(get_db)],
) -> IncidentResponse:
    """Transition an incident to DISMISSED (requires UNDER_REVIEW, reason required)."""
    return _transition_and_fetch(conn, incident_id, "DISMISSED", reason=payload.reason)


@router.get("/incidents/{incident_id}/evidence", tags=["Incidents"])
def get_evidence(
    incident_id: str,
    conn: Annotated[sqlite3.Connection, Depends(get_db)],
) -> list[EvidenceResponse]:
    """Get evidence records for an incident."""
    cursor = conn.execute(
        "SELECT * FROM evidence_records WHERE incident_id = ?", (incident_id,)
    )
    rows = cursor.fetchall()
    return [EvidenceResponse(**dict(row)) for row in rows]
