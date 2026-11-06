# Copyright 2026 Lightibulb

"""Endpoints for incident management."""

import sqlite3
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query

from ecofac.api.dependencies import get_db, require_write_access
from ecofac.api.schemas import EvidenceResponse, IncidentActionRequest, IncidentResponse
from ecofac.incidents.lifecycle import (
    IncidentNotFoundError,
    InvalidStateTransitionError,
    transition_incident,
)

router = APIRouter()


def _get_incident_row(conn: sqlite3.Connection, incident_id: str) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM incidents WHERE id = ?", (incident_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Incident not found")
    return row


def _transition(
    conn: sqlite3.Connection,
    incident_id: str,
    new_status: str,
    payload: IncidentActionRequest | None,
) -> IncidentResponse:
    """Apply one lifecycle transition and return the updated incident."""
    reason = payload.reason if payload else None
    try:
        transition_incident(conn, incident_id, new_status, reason=reason)
    except IncidentNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except InvalidStateTransitionError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return IncidentResponse(**dict(_get_incident_row(conn, incident_id)))


@router.get("/incidents", tags=["Incidents"])
def list_incidents(
    conn: Annotated[sqlite3.Connection, Depends(get_db)],
    limit: Annotated[int, Query(ge=1, le=100)] = 10,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[IncidentResponse]:
    """List incidents, newest first."""
    cursor = conn.execute(
        "SELECT * FROM incidents ORDER BY created_at DESC, id LIMIT ? OFFSET ?",
        (limit, offset),
    )
    return [IncidentResponse(**dict(row)) for row in cursor.fetchall()]


@router.get("/incidents/{incident_id}", tags=["Incidents"])
def get_incident(
    incident_id: str,
    conn: Annotated[sqlite3.Connection, Depends(get_db)],
) -> IncidentResponse:
    """Get an incident by ID."""
    return IncidentResponse(**dict(_get_incident_row(conn, incident_id)))


@router.post("/incidents/{incident_id}/review", tags=["Incidents"])
def review_incident(
    incident_id: str,
    conn: Annotated[sqlite3.Connection, Depends(get_db)],
    _auth: Annotated[None, Depends(require_write_access)],
    payload: IncidentActionRequest | None = None,
) -> IncidentResponse:
    """Transition an incident to UNDER_REVIEW."""
    return _transition(conn, incident_id, "UNDER_REVIEW", payload)


@router.post("/incidents/{incident_id}/acknowledge", tags=["Incidents"])
def acknowledge_incident(
    incident_id: str,
    conn: Annotated[sqlite3.Connection, Depends(get_db)],
    _auth: Annotated[None, Depends(require_write_access)],
    payload: IncidentActionRequest | None = None,
) -> IncidentResponse:
    """Transition an incident to ACKNOWLEDGED."""
    return _transition(conn, incident_id, "ACKNOWLEDGED", payload)


@router.post("/incidents/{incident_id}/dismiss", tags=["Incidents"])
def dismiss_incident(
    incident_id: str,
    conn: Annotated[sqlite3.Connection, Depends(get_db)],
    _auth: Annotated[None, Depends(require_write_access)],
    payload: IncidentActionRequest | None = None,
) -> IncidentResponse:
    """Transition an incident to DISMISSED (reason required)."""
    return _transition(conn, incident_id, "DISMISSED", payload)


@router.post("/incidents/{incident_id}/resolve", tags=["Incidents"])
def resolve_incident(
    incident_id: str,
    conn: Annotated[sqlite3.Connection, Depends(get_db)],
    _auth: Annotated[None, Depends(require_write_access)],
    payload: IncidentActionRequest | None = None,
) -> IncidentResponse:
    """Transition an incident to RESOLVED (reason required)."""
    return _transition(conn, incident_id, "RESOLVED", payload)


@router.post("/incidents/{incident_id}/unresolve", tags=["Incidents"])
def unresolve_incident(
    incident_id: str,
    conn: Annotated[sqlite3.Connection, Depends(get_db)],
    _auth: Annotated[None, Depends(require_write_access)],
    payload: IncidentActionRequest | None = None,
) -> IncidentResponse:
    """Transition an incident to UNRESOLVED (reason required)."""
    return _transition(conn, incident_id, "UNRESOLVED", payload)


@router.get("/incidents/{incident_id}/evidence", tags=["Incidents"])
def get_evidence(
    incident_id: str,
    conn: Annotated[sqlite3.Connection, Depends(get_db)],
) -> list[EvidenceResponse]:
    """Get evidence records for an incident (one per detector that flagged it)."""
    _get_incident_row(conn, incident_id)
    cursor = conn.execute(
        "SELECT * FROM evidence_records WHERE incident_id = ? ORDER BY created_at, id",
        (incident_id,),
    )
    return [EvidenceResponse(**dict(row)) for row in cursor.fetchall()]
