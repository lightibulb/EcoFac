# Copyright 2026 Lightibulb

"""Endpoints for audit logs."""

import sqlite3
from fastapi import APIRouter, Depends, Query

from ecofac.api.dependencies import get_db
from ecofac.api.schemas import AuditEventResponse

router = APIRouter()

@router.get("/audit", response_model=list[AuditEventResponse], tags=["Audit"])
def list_audit(
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    conn: sqlite3.Connection = Depends(get_db),
) -> list[AuditEventResponse]:
    """List audit events."""
    cursor = conn.execute("SELECT * FROM audit_events ORDER BY created_at DESC LIMIT ? OFFSET ?", (limit, offset))
    return [AuditEventResponse(**dict(row)) for row in cursor.fetchall()]
