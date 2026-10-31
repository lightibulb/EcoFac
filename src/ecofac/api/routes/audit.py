# Copyright 2026 Lightibulb

"""Endpoints for audit logs."""

import sqlite3
from typing import Annotated

from fastapi import APIRouter, Depends, Query

from ecofac.api.dependencies import get_db
from ecofac.api.schemas import AuditEventResponse

router = APIRouter()

@router.get("/audit", tags=["Audit"])
def list_audit(
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
    conn: Annotated[sqlite3.Connection, Depends(get_db)] = None,
) -> list[AuditEventResponse]:
    """List audit events."""
    cursor = conn.execute(
        "SELECT * FROM audit_events ORDER BY created_at DESC LIMIT ? OFFSET ?",
        (limit, offset),
    )
    return [AuditEventResponse(**dict(row)) for row in cursor.fetchall()]
