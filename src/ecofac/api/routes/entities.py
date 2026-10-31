# Copyright 2026 Lightibulb

"""Endpoints for buildings and meters."""

import sqlite3
from fastapi import APIRouter, Depends, HTTPException, Query

from ecofac.api.dependencies import get_db
from ecofac.api.schemas import BuildingResponse, MeterResponse

router = APIRouter()

@router.get("/buildings", response_model=list[BuildingResponse], tags=["Entities"])
def list_buildings(
    limit: int = Query(10, ge=1, le=100),
    offset: int = Query(0, ge=0),
    conn: sqlite3.Connection = Depends(get_db),
) -> list[BuildingResponse]:
    """List buildings with pagination."""
    cursor = conn.execute("SELECT * FROM buildings LIMIT ? OFFSET ?", (limit, offset))
    return [BuildingResponse(**dict(row)) for row in cursor.fetchall()]

@router.get("/buildings/{id}", response_model=BuildingResponse, tags=["Entities"])
def get_building(id: str, conn: sqlite3.Connection = Depends(get_db)) -> BuildingResponse:
    """Get a building by ID."""
    cursor = conn.execute("SELECT * FROM buildings WHERE id = ?", (id,))
    row = cursor.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Building not found")
    return BuildingResponse(**dict(row))

@router.get("/meters", response_model=list[MeterResponse], tags=["Entities"])
def list_meters(
    limit: int = Query(10, ge=1, le=100),
    offset: int = Query(0, ge=0),
    conn: sqlite3.Connection = Depends(get_db),
) -> list[MeterResponse]:
    """List meters with pagination."""
    cursor = conn.execute("SELECT * FROM meters LIMIT ? OFFSET ?", (limit, offset))
    return [MeterResponse(**dict(row)) for row in cursor.fetchall()]

@router.get("/meters/{id}", response_model=MeterResponse, tags=["Entities"])
def get_meter(id: str, conn: sqlite3.Connection = Depends(get_db)) -> MeterResponse:
    """Get a meter by ID."""
    cursor = conn.execute("SELECT * FROM meters WHERE id = ?", (id,))
    row = cursor.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Meter not found")
    return MeterResponse(**dict(row))
