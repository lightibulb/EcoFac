# Copyright 2026 Lightibulb

"""Endpoints for CSV imports."""

import csv
import dataclasses
import io
import json
import logging
import sqlite3
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile

from ecofac.api.dependencies import get_db
from ecofac.api.schemas import ImportBatchResponse, ImportSummaryResponse
from ecofac.detection.batch import AnalysisSummary, analyze_all
from ecofac.ingestion.pipeline import ingest_csv, normalize_headers
from ecofac.timeutil import now_z

logger = logging.getLogger(__name__)

router = APIRouter()

MAX_FILE_SIZE = 5 * 1024 * 1024  # 5 MB
MAX_AUTO_ENTITIES = 500
MAX_ID_LENGTH = 100
ID_PATTERN = r"^[A-Za-z0-9._:-]+$"
IdField = Annotated[str, Form(min_length=1, max_length=MAX_ID_LENGTH, pattern=ID_PATTERN)]


def _entity_pairs(text: str) -> set[tuple[str, str]]:
    """Collect well-formed (building_id, meter_id) pairs from the CSV."""
    pairs: set[tuple[str, str]] = set()
    try:
        reader = csv.DictReader(io.StringIO(text))
        normalize_headers(reader)
        for row in reader:
            building = (row.get("building_id") or "").strip()
            meter = (row.get("meter_id") or "").strip()
            if building and meter and max(len(building), len(meter)) <= MAX_ID_LENGTH:
                pairs.add((building, meter))
            if len(pairs) > MAX_AUTO_ENTITIES:
                raise HTTPException(
                    status_code=400,
                    detail=f"CSV references more than {MAX_AUTO_ENTITIES} meters",
                )
    except csv.Error:
        # Malformed CSV: ingest_csv reports the precise problem.
        return set()
    return pairs


def _create_entities(conn: sqlite3.Connection, pairs: set[tuple[str, str]], now: str) -> None:
    """Auto-create the organization, buildings and meters a CSV refers to."""
    conn.execute(
        "INSERT OR IGNORE INTO organizations (id, name, created_at) "
        "VALUES ('o-auto', 'Auto Org', ?)",
        (now,),
    )
    for building_id, meter_id in sorted(pairs):
        conn.execute(
            "INSERT OR IGNORE INTO buildings (id, org_id, name, created_at) "
            "VALUES (?, 'o-auto', ?, ?)",
            (building_id, building_id, now),
        )
        conn.execute(
            "INSERT OR IGNORE INTO meters (id, building_id, fuel_type, unit, created_at) "
            "VALUES (?, ?, 'elec', 'kWh', ?)",
            (meter_id, building_id, now),
        )


def _set_batch_status(
    conn: sqlite3.Connection, batch_id: str, status: str, summary_json: str | None = None
) -> None:
    conn.execute(
        "UPDATE import_batches SET status = ?, summary_json = COALESCE(?, summary_json) "
        "WHERE id = ?",
        (status, summary_json, batch_id),
    )


@router.post("/imports", tags=["Imports"])
def upload_csv(
    file: Annotated[UploadFile, File()],
    source_id: IdField,
    batch_id: IdField,
    conn: Annotated[sqlite3.Connection, Depends(get_db)],
    *,
    analyze: Annotated[bool, Query()] = True,
) -> ImportSummaryResponse:
    """Upload a CSV file, ingest it, and (by default) run anomaly analysis on it."""
    # Read at most one byte past the limit so an oversized upload is never fully buffered.
    content = file.file.read(MAX_FILE_SIZE + 1)
    if len(content) > MAX_FILE_SIZE:
        raise HTTPException(status_code=413, detail="File too large")

    try:
        text = content.decode("utf-8-sig")  # tolerate the BOM that Excel adds
    except UnicodeDecodeError as e:
        raise HTTPException(status_code=400, detail="File must be UTF-8 encoded") from e

    existing = conn.execute(
        "SELECT source_id, status FROM import_batches WHERE id = ?", (batch_id,)
    ).fetchone()
    if existing and existing["source_id"] != source_id:
        raise HTTPException(
            status_code=409, detail="batch_id already belongs to a different source"
        )
    if existing and existing["status"] == "completed":
        raise HTTPException(
            status_code=409, detail="This batch_id was already imported; use a new batch_id"
        )

    pairs = _entity_pairs(text)
    now = now_z()
    conn.execute(
        "INSERT OR IGNORE INTO data_sources (id, name, source_type, created_at) "
        "VALUES (?, ?, 'csv', ?)",
        (source_id, source_id, now),
    )
    conn.execute(
        "INSERT OR IGNORE INTO import_batches (id, source_id, status, imported_at) "
        "VALUES (?, ?, 'started', ?)",
        (batch_id, source_id, now),
    )
    _create_entities(conn, pairs, now)

    try:
        summary = ingest_csv(conn, text, source_id, batch_id, max_size_mb=5.0)
    except ValueError as e:
        _set_batch_status(conn, batch_id, "failed")
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:
        logger.exception("CSV import failed")
        _set_batch_status(conn, batch_id, "failed")
        raise HTTPException(
            status_code=500, detail="Import failed due to an internal error"
        ) from e

    _set_batch_status(conn, batch_id, "completed", json.dumps(dataclasses.asdict(summary)))

    analysis = AnalysisSummary()
    if analyze and summary.accepted_rows and pairs:
        try:
            analysis = analyze_all(conn, meter_ids=sorted({meter for _, meter in pairs}))
        except sqlite3.Error:
            logger.exception("Post-import analysis failed; the import itself succeeded")

    return ImportSummaryResponse(
        **dataclasses.asdict(summary),
        anomalies_detected=analysis.anomalies_detected,
        incidents_created=analysis.incidents_created,
    )


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
