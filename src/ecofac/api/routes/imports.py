# Copyright 2026 Lightibulb

"""Endpoints for CSV imports."""

import dataclasses
import datetime as dt
import json
import sqlite3

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile

from ecofac.api.dependencies import get_db
from ecofac.api.schemas import ImportBatchResponse, ImportSummaryResponse
from ecofac.ingestion.pipeline import ingest_csv

router = APIRouter()

MAX_FILE_SIZE = 5 * 1024 * 1024  # 5 MB

@router.post("/imports", response_model=ImportSummaryResponse, tags=["Imports"])
async def upload_csv(
    file: UploadFile = File(...),
    source_id: str = Form(...),
    batch_id: str = Form(...),
    conn: sqlite3.Connection = Depends(get_db),
) -> ImportSummaryResponse:
    """Upload a CSV file and ingest it."""
    content = await file.read()
    if len(content) > MAX_FILE_SIZE:
        raise HTTPException(status_code=413, detail="File too large")

    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError as e:
        raise HTTPException(status_code=400, detail="File must be UTF-8 encoded") from e

    try:
        summary = ingest_csv(conn, text, source_id, batch_id, max_size_mb=5.0)

        # Save batch status
        now = dt.datetime.now(dt.UTC).isoformat()
        conn.execute(
            "INSERT INTO import_batches (id, source_id, status, summary_json, imported_at) "
            "VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(id) DO UPDATE SET status=excluded.status, summary_json=excluded.summary_json",
            (batch_id, source_id, "completed", json.dumps(dataclasses.asdict(summary)), now)
        )

        return ImportSummaryResponse(**dataclasses.asdict(summary))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

@router.get("/imports/{id}", response_model=ImportBatchResponse, tags=["Imports"])
def get_import(id: str, conn: sqlite3.Connection = Depends(get_db)) -> ImportBatchResponse:
    """Get the status of an import."""
    cursor = conn.execute("SELECT * FROM import_batches WHERE id = ?", (id,))
    row = cursor.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Import batch not found")
    return ImportBatchResponse(**dict(row))
