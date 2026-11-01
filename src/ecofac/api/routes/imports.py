# Copyright 2026 Lightibulb

"""Endpoints for CSV imports."""

import csv
import dataclasses
import datetime as dt
import io
import json
import sqlite3
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile

from ecofac.api.dependencies import get_db
from ecofac.api.schemas import ImportBatchResponse, ImportSummaryResponse
from ecofac.ingestion.pipeline import ingest_csv

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

    now = dt.datetime.now(dt.UTC).isoformat()

    # 1. Auto-create the data source
    conn.execute(
        "INSERT OR IGNORE INTO data_sources (id, name, source_type, created_at) "
        "VALUES (?, ?, 'csv', ?)",
        (source_id, source_id, now),
    )

    # 2. Auto-create the batch
    conn.execute(
        "INSERT OR IGNORE INTO import_batches (id, source_id, status, imported_at) "
        "VALUES (?, ?, 'started', ?)",
        (batch_id, source_id, now),
    )

    # 3. Extract buildings and meters from CSV and auto-create them
    try:
        reader = csv.DictReader(io.StringIO(text))
        pairs = {
            (row.get("building_id", "").strip(), row.get("meter_id", "").strip())
            for row in reader
        }

        # Create a default org
        conn.execute(
            "INSERT OR IGNORE INTO organizations (id, name, created_at) "
            "VALUES ('o-auto', 'Auto Org', ?)",
            (now,),
        )

        for b_id, m_id in pairs:
            if not b_id or not m_id:
                continue

            conn.execute(
                "INSERT OR IGNORE INTO buildings (id, org_id, name, created_at) "
                "VALUES (?, 'o-auto', ?, ?)",
                (b_id, b_id, now),
            )

            conn.execute(
                "INSERT OR IGNORE INTO meters "
                "(id, building_id, fuel_type, unit, created_at) "
                "VALUES (?, ?, 'elec', 'kWh', ?)",
                (m_id, b_id, now),
            )

    except (csv.Error, ValueError):
        # If we can't parse the CSV header, let ingest_csv handle the error properly
        pass

    # 4. Run ingestion
    try:
        summary = ingest_csv(conn, text, source_id, batch_id, max_size_mb=5.0)

        conn.execute(
            "UPDATE import_batches SET status = 'completed', summary_json = ? "
            "WHERE id = ?",
            (json.dumps(dataclasses.asdict(summary)), batch_id),
        )

        return ImportSummaryResponse(**dataclasses.asdict(summary))
    except ValueError as e:
        conn.execute(
            "UPDATE import_batches SET status = 'failed' WHERE id = ?", (batch_id,)
        )
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


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
