# Copyright 2026 Lightibulb

"""Endpoints for buildings and meters."""

import sqlite3
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query

from ecofac.api.dependencies import get_db
from ecofac.api.schemas import BuildingResponse, MeterResponse

router = APIRouter()

@router.get("/buildings", tags=["Entities"])
def list_buildings(
    conn: Annotated[sqlite3.Connection, Depends(get_db)],
    limit: Annotated[int, Query(ge=1, le=100)] = 10,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[BuildingResponse]:
    """List buildings with pagination."""
    cursor = conn.execute("SELECT * FROM buildings LIMIT ? OFFSET ?", (limit, offset))
    return [BuildingResponse(**dict(row)) for row in cursor.fetchall()]

@router.get("/buildings/{building_id}", tags=["Entities"])
def get_building(
    building_id: str,
    conn: Annotated[sqlite3.Connection, Depends(get_db)],
) -> BuildingResponse:
    """Get a building by ID."""
    cursor = conn.execute("SELECT * FROM buildings WHERE id = ?", (building_id,))
    row = cursor.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Building not found")
    return BuildingResponse(**dict(row))

@router.get("/meters", tags=["Entities"])
def list_meters(
    conn: Annotated[sqlite3.Connection, Depends(get_db)],
    limit: Annotated[int, Query(ge=1, le=100)] = 10,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[MeterResponse]:
    """List meters with pagination."""
    cursor = conn.execute("SELECT * FROM meters LIMIT ? OFFSET ?", (limit, offset))
    return [MeterResponse(**dict(row)) for row in cursor.fetchall()]

@router.get("/meters/{meter_id}", tags=["Entities"])
def get_meter(
    meter_id: str,
    conn: Annotated[sqlite3.Connection, Depends(get_db)],
) -> MeterResponse:
    """Get a meter by ID."""
    cursor = conn.execute("SELECT * FROM meters WHERE id = ?", (meter_id,))
    row = cursor.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Meter not found")
    return MeterResponse(**dict(row))
