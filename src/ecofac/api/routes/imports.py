# Copyright 2026 Lightibulb
"""Endpoints for CSV imports."""
import dataclasses
import json
import sqlite3
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile

from ecofac.api.dependencies import get_db
from ecofac.api.schemas import ImportBatchResponse, ImportSummaryResponse
from ecofac.ingestion.pipeline import ingest_csv
from ecofac.timeutil import now_z

router = APIRouter()

MAX_FILE_SIZE = 5 * 1024 * 1024  # 5 MB


@router.post("/imports", tags=["Imports"])
async def upload_csv(
    file: Annotated[UploadFile, File()],
    source_id: Annotated[str, Form()],
    batch_id: Annotated[str, Form()],
    conn: Annotated[sqlite3.Connection, Depends(get_db)],
) -> ImportSummaryResponse:
    """Upload a CSV file and ingest it."""
    content = await file.read()
    if len(content) > MAX_FILE_SIZE:
        raise HTTPException(status_code=413, detail="File too large")
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError as e:
        raise HTTPException(status_code=400, detail="File must be UTF-8 encoded") from e

    # Ensure the batch exists before ingestion to satisfy foreign keys.
    # NOTE: INSERT OR IGNORE does NOT swallow FK violations, so the source
    # must be validated BEFORE the insert or an unknown source_id crashes
    # with an unhandled IntegrityError (500) instead of a clean 400.
    known_source = conn.execute(
        "SELECT 1 FROM data_sources WHERE id = ?", (source_id,)
    ).fetchone()
    if known_source is None:
        raise HTTPException(status_code=400, detail="Invalid source_id")
    conn.execute(
        "INSERT OR IGNORE INTO import_batches (id, source_id, status, imported_at) "
        "VALUES (?, ?, ?, ?)",
        (batch_id, source_id, "started", now_z()),
    )

    try:
        summary = ingest_csv(conn, text, source_id, batch_id, max_size_mb=5.0)
        conn.execute(
            "UPDATE import_batches SET status = 'completed', summary_json = ? WHERE id = ?",
            (json.dumps(dataclasses.asdict(summary)), batch_id),
        )
        return ImportSummaryResponse(**dataclasses.asdict(summary))
    except ValueError as e:
        conn.execute(
            "UPDATE import_batches SET status = 'failed' WHERE id = ?", (batch_id,)
        )
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.get("/imports/{batch_id}", tags=["Imports"])
def get_import(
    batch_id: str,
    conn: Annotated[sqlite3.Connection, Depends(get_db)],
) -> ImportBatchResponse:
    """Get the status of an import."""
    cursor = conn.execute("SELECT * FROM import_batches WHERE id = ?", (batch_id,))
    row = cursor.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Import batch not found")
    return ImportBatchResponse(**dict(row))
