# Copyright 2026 Lightibulb

"""Endpoints for incident management."""

import sqlite3
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, Field

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


@router.get("/incidents")  # LMF-01-APPLIED
def list_incidents(
    response: Response,
    conn: Annotated[sqlite3.Connection, Depends(get_db)],
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
    status: Annotated[
        str | None, Query(description="Lifecycle status filter")
    ] = None,
) -> list[dict]:
    """Paginated incident history with an optional status filter."""
    where = ""
    params: list[object] = []
    if status is not None and status.strip():
        where = " WHERE i.status = ?"
        params.append(status.strip().upper())
    total = conn.execute(
        "SELECT COUNT(*) AS n FROM incidents i" + where, params
    ).fetchone()["n"]
    rows = conn.execute(
        """
        SELECT i.id, i.anomaly_id, i.status, i.resolution_notes,
               i.dismissal_reason, i.created_at, i.updated_at,
               a.building_id, a.meter_id, a.severity, a.detector_name,
               a.timestamp_utc AS observation_timestamp
          FROM incidents i
          JOIN anomalies a ON a.id = i.anomaly_id
        """
        + where
        + " ORDER BY i.created_at DESC, i.id DESC LIMIT ? OFFSET ?",
        [*params, limit, offset],
    ).fetchall()
    response.headers["X-Total-Count"] = str(total)
    return [dict(row) for row in rows]


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


class LMFResolveBody(BaseModel):
    """Payload for resolving an incident; the reason is mandatory."""

    reason: str = Field(min_length=1, max_length=2000)


@router.post("/incidents/{incident_id}/resolve")  # LMF-02-APPLIED-V2
def resolve_incident(
    incident_id: str,
    conn: Annotated[sqlite3.Connection, Depends(get_db)],
    _auth: Annotated[None, Depends(require_write_access)],
    body: LMFResolveBody,
) -> dict:
    """Move an incident to RESOLVED; a non-empty reason is required."""
    reason = body.reason.strip()
    if not reason:
        raise HTTPException(
            status_code=422, detail="A non-blank reason is required"
        )
    try:
        result = _transition(conn, incident_id, "RESOLVED", body)
    except IncidentNotFoundError as exc:
        raise HTTPException(
            status_code=404, detail="Incident not found"
        ) from exc
    conn.execute(
        "UPDATE incidents SET resolution_notes = ? WHERE id = ?",
        (reason, incident_id),
    )
    conn.commit()
    return result if isinstance(result, dict) else dict(result)


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
