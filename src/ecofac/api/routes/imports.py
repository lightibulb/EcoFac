# Copyright 2026 Lightibulb

"""Endpoints for CSV imports."""

import dataclasses
import json
import logging
import sqlite3
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile

from ecofac.api.dependencies import get_db, require_write_access
from ecofac.api.errors import api_error
from ecofac.api.schemas import ImportBatchResponse, ImportSummaryResponse
from ecofac.constants import MAX_UPLOAD_BYTES
from ecofac.detection.batch import AnalysisSummary, analyze_all
from ecofac.ingestion.entities import ID_PATTERN, MAX_ID_LENGTH
from ecofac.ingestion.pipeline import IngestionError, IngestionSummary, ingest_csv
from ecofac.persistence.tx import persist_atomically
from ecofac.timeutil import now_z

logger = logging.getLogger(__name__)

router = APIRouter()

MAX_FILE_SIZE = MAX_UPLOAD_BYTES
IdField = Annotated[str, Form(min_length=1, max_length=MAX_ID_LENGTH, pattern=ID_PATTERN)]

# HTTP status per whole-file ingestion error code. Anything not listed is 400.
_INGESTION_STATUS = {
    "file_too_large": 413,
    "row_limit_exceeded": 413,
    "entity_limit_exceeded": 422,
}


def _read_upload(file: UploadFile) -> bytes:
    """Read the uploaded file, enforcing the size limit.

    By the time this runs the multipart body has already been received and
    spooled (memory up to 1 MiB, a temporary file beyond that); that is how
    Starlette parses multipart. The real bound on what gets buffered is
    ``BodySizeLimitMiddleware``. This read is the second, exact check on the
    file part itself (the middleware allows a small multipart envelope on top
    of the file limit).
    """
    content = file.file.read(MAX_FILE_SIZE + 1)
    if len(content) > MAX_FILE_SIZE:
        raise api_error(
            413,
            "file_too_large",
            f"File exceeds the {MAX_FILE_SIZE // (1024 * 1024)} MB limit",
            limit_bytes=MAX_FILE_SIZE,
        )
    return content


def _set_batch_status(
    conn: sqlite3.Connection, batch_id: str, status: str, summary_json: str | None = None
) -> None:
    conn.execute(
        "UPDATE import_batches SET status = ?, summary_json = COALESCE(?, summary_json) "
        "WHERE id = ?",
        (status, summary_json, batch_id),
    )


def _import_in_transaction(
    conn: sqlite3.Connection, text: str, source_id: str, batch_id: str
) -> IngestionSummary:
    """Create source + batch, ingest rows and entities, mark completed.

    Runs inside ``persist_atomically``: if anything here raises, the source,
    batch, entities, readings and quality issues are all rolled back together.
    """
    existing = conn.execute(
        "SELECT source_id, status FROM import_batches WHERE id = ?", (batch_id,)
    ).fetchone()
    if existing and existing["source_id"] != source_id:
        raise api_error(
            409,
            "batch_source_conflict",
            "batch_id already belongs to a different source",
            rolled_back=True,
        )
    if existing and existing["status"] == "completed":
        raise api_error(
            409,
            "batch_already_imported",
            "This batch_id was already imported; use a new batch_id",
            rolled_back=True,
        )

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
    summary = ingest_csv(conn, text, source_id, batch_id, auto_create_entities=True)
    _set_batch_status(conn, batch_id, "completed", json.dumps(dataclasses.asdict(summary)))
    return summary


def _run_post_import_analysis(
    conn: sqlite3.Connection,
    batch_id: str,
    meters: list[str],
) -> tuple[AnalysisSummary, bool]:
    # Run analysis in its own transaction. On failure, mark the batch
    # analysis_failed so operators can retry via POST /analyze; the
    # import itself was already committed and is preserved. On success
    # the batch stays 'completed' (backward-compatible default).
    if not meters:
        _set_batch_status(conn, batch_id, "completed")
        return AnalysisSummary(), True
    try:
        analysis = analyze_all(conn, meter_ids=meters)
    except sqlite3.Error:
        logger.exception("Post-import analysis failed")
        _set_batch_status(conn, batch_id, "analysis_failed")
        return AnalysisSummary(), False
    _set_batch_status(conn, batch_id, "completed")
    return analysis, True


@router.post("/imports", tags=["Imports"])
def upload_csv(
    file: Annotated[UploadFile, File()],
    source_id: IdField,
    batch_id: IdField,
    conn: Annotated[sqlite3.Connection, Depends(get_db)],
    _auth: Annotated[None, Depends(require_write_access)],
    *,
    analyze: Annotated[bool, Query()] = True,
) -> ImportSummaryResponse:
    """Upload a CSV file, ingest it, and (by default) run anomaly analysis on it.

    The import is all-or-nothing. The data source, import batch, auto-created
    buildings/meters, readings and quality issues are written in one
    transaction. Any failure rolls all of it back, so a failed import leaves
    no records behind and the same ``batch_id`` can be retried. Errors are
    structured: ``{"detail": {"code", "message", "rolled_back", "details"}}``.
    """
    content = _read_upload(file)
    try:
        text = content.decode("utf-8-sig")  # tolerate the BOM that Excel adds
    except UnicodeDecodeError as e:
        raise api_error(
            400,
            "invalid_encoding",
            "File must be UTF-8 encoded",
            rolled_back=True,
            byte_offset=e.start,
        ) from e

    try:
        summary = persist_atomically(
            conn, lambda: _import_in_transaction(conn, text, source_id, batch_id)
        )
    except HTTPException:
        raise
    except IngestionError as e:
        raise api_error(
            _INGESTION_STATUS.get(e.code, 400),
            e.code,
            e.message,
            rolled_back=True,
            **e.details,
        ) from e
    except sqlite3.Error as e:
        logger.exception("CSV import failed in the database")
        message = str(e).lower()
        if isinstance(e, sqlite3.OperationalError) and ("locked" in message or "busy" in message):
            raise api_error(
                503,
                "database_busy",
                "The database is busy; nothing was imported. Retry shortly.",
                rolled_back=True,
            ) from e
        raise api_error(
            500,
            "database_error",
            "Import failed in the database; nothing was imported.",
            rolled_back=True,
        ) from e
    except Exception as e:
        logger.exception("CSV import failed")
        raise api_error(
            500,
            "internal_error",
            "Import failed due to an internal error; nothing was imported.",
            rolled_back=True,
        ) from e

    analysis = AnalysisSummary()
    if analyze and summary.accepted_rows:
        meters = [
            row["meter_id"]
            for row in conn.execute(
                "SELECT DISTINCT meter_id FROM energy_readings "
                "WHERE batch_id = ? ORDER BY meter_id",
                (batch_id,),
            )
        ]
        analysis, _ = _run_post_import_analysis(conn, batch_id, meters)

    return ImportSummaryResponse(
        **dataclasses.asdict(summary),
        anomalies_detected=analysis.anomalies_detected,
        incidents_created=analysis.incidents_created,
    )


@router.post("/imports/{batch_id}/analyze", tags=["Imports"])
def analyze_import(
    batch_id: str,
    conn: Annotated[sqlite3.Connection, Depends(get_db)],
    _auth: Annotated[None, Depends(require_write_access)],
) -> dict:
    # Re-run analysis for one batch. Idempotent: analyze_all skips
    # anomalies already recorded. Safe to call after a failed analysis.
    row = conn.execute("SELECT status FROM import_batches WHERE id = ?", (batch_id,)).fetchone()
    if row is None:
        raise api_error(404, "batch_not_found", f"Import batch {batch_id} not found")
    if row["status"] == "started":
        raise api_error(
            409,
            "batch_in_progress",
            "Batch upload is still in progress; retry once it completes",
        )
    meters = [
        r["meter_id"]
        for r in conn.execute(
            "SELECT DISTINCT meter_id FROM energy_readings WHERE batch_id = ? ORDER BY meter_id",
            (batch_id,),
        )
    ]
    analysis, ok = _run_post_import_analysis(conn, batch_id, meters)
    return {
        "batch_id": batch_id,
        "status": "completed" if ok else "analysis_failed",
        "anomalies_detected": analysis.anomalies_detected,
        "incidents_created": analysis.incidents_created,
    }


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
