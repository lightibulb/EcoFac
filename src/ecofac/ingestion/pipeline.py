# Copyright 2026 Lightibulb

"""CSV ingestion and normalization pipeline."""

import csv
import dataclasses
import datetime as dt
import io
import sqlite3
import uuid


@dataclasses.dataclass
class IngestionSummary:
    """Summary of a CSV ingestion run."""

    total_rows: int = 0
    accepted_rows: int = 0
    rejected_rows: int = 0
    duplicate_rows: int = 0
    invalid_timestamps: int = 0
    invalid_units: int = 0
    unknown_meter_ids: int = 0
    negative_consumption: int = 0


UNITS = {"wh": 0.001, "kwh": 1.0, "mwh": 1000.0}


def _log_issue(
    conn: sqlite3.Connection,
    *,
    batch_id: str,
    issue_type: str,
    description: str,
    reading_id: str | None = None,
    meter_id: str | None = None,
    reading_timestamp_utc: str | None = None,
) -> None:
    conn.execute(
        "INSERT INTO data_quality_issues "
        "(id, reading_id, batch_id, issue_type, description, created_at, "
        "meter_id, reading_timestamp_utc) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            str(uuid.uuid4()),
            reading_id,
            batch_id,
            issue_type,
            description,
            dt.datetime.now(dt.UTC).isoformat(),
            meter_id,
            reading_timestamp_utc,
        ),
    )


def ingest_csv(
    conn: sqlite3.Connection,
    csv_content: str,
    source_id: str,
    batch_id: str,
    *,
    max_rows: int = 50000,
    max_size_mb: float = 5.0,
) -> IngestionSummary:
    """Parse, validate, and store readings from a CSV string."""
    if len(csv_content) > max_size_mb * 1024 * 1024:
        msg = "File size exceeds limit"
        raise ValueError(msg)

    reader = csv.DictReader(io.StringIO(csv_content))
    required = {"timestamp", "building_id", "meter_id", "energy_value", "unit"}
    if not reader.fieldnames or not required.issubset(reader.fieldnames):
        msg = f"Missing required columns. Expected: {required}"
        raise ValueError(msg)

    summary = IngestionSummary()

    cursor = conn.execute("SELECT id, building_id FROM meters")
    meter_map = {row["id"]: row["building_id"] for row in cursor.fetchall()}

    seen: set[tuple[str, str]] = set()

    conn.execute("BEGIN")
    try:
        for row in reader:
            summary.total_rows += 1
            if summary.total_rows > max_rows:
                msg = "Row count exceeds limit"
                raise ValueError(msg)

            meter_id = (row.get("meter_id") or "").strip()
            building_id = (row.get("building_id") or "").strip()

            # 1. Validate meter and building relationship
            if meter_id not in meter_map:
                summary.rejected_rows += 1
                summary.unknown_meter_ids += 1
                _log_issue(
                    conn,
                    batch_id=batch_id,
                    issue_type="unknown_meter",
                    description=f"Unknown meter: {meter_id}",
                )
                continue

            if meter_map[meter_id] != building_id:
                summary.rejected_rows += 1
                _log_issue(
                    conn,
                    batch_id=batch_id,
                    issue_type="building_mismatch",
                    description=f"Meter {meter_id} does not belong to building {building_id}",
                )
                continue

            # 2. Normalize Timestamp to UTC (and reject naive timestamps)
            ts_raw = (row.get("timestamp") or "").strip()
            try:
                dt_val = dt.datetime.fromisoformat(ts_raw)
                if dt_val.tzinfo is None:
                    msg = f"Naive timestamp: {ts_raw}"
                    raise ValueError(msg)
                dt_utc = dt_val.astimezone(dt.UTC)
                ts_utc = dt_utc.isoformat().replace("+00:00", "Z")
            except ValueError:
                summary.rejected_rows += 1
                summary.invalid_timestamps += 1
                _log_issue(
                    conn,
                    batch_id=batch_id,
                    issue_type="invalid_timestamp",
                    description=f"Invalid timestamp: {ts_raw}",
                )
                continue

            # 3. Duplicate check (within CSV)
            key = (meter_id, ts_utc)
            if key in seen:
                summary.rejected_rows += 1
                summary.duplicate_rows += 1
                _log_issue(
                    conn,
                    batch_id=batch_id,
                    issue_type="duplicate",
                    description=f"Duplicate in CSV: {meter_id} @ {ts_utc}",
                    meter_id=meter_id,
                    reading_timestamp_utc=ts_utc,
                )
                continue
            seen.add(key)

            # 4. Unit normalization
            unit_raw = (row.get("unit") or "").strip().lower()
            if unit_raw not in UNITS:
                summary.rejected_rows += 1
                summary.invalid_units += 1
                _log_issue(
                    conn,
                    batch_id=batch_id,
                    issue_type="invalid_unit",
                    description=f"Unsupported unit: {unit_raw}",
                )
                continue

            # 5. Value parsing
            val_raw = (row.get("energy_value") or "").strip()
            try:
                val = float(val_raw)
            except ValueError:
                summary.rejected_rows += 1
                _log_issue(
                    conn,
                    batch_id=batch_id,
                    issue_type="malformed_value",
                    description=f"Invalid value: {val_raw}",
                )
                continue

            val_kwh = val * UNITS[unit_raw]
            reading_id = str(uuid.uuid4())
            is_valid = True
            issue_to_log = None

            # 6. Negative consumption flagging
            if val_kwh < 0:
                is_valid = False
                summary.negative_consumption += 1
                issue_to_log = (
                    batch_id,
                    "negative_consumption",
                    f"Negative reading: {val_kwh} kWh",
                    reading_id,
                )

            # 7. Insert normalized record
            try:
                conn.execute(
                    """INSERT INTO energy_readings
                    (id, meter_id, timestamp_utc, value, unit, source_id, batch_id,
                     is_valid, created_at, raw_value)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        reading_id,
                        meter_id,
                        ts_utc,
                        val_kwh,
                        "kWh",
                        source_id,
                        batch_id,
                        int(is_valid),
                        dt.datetime.now(dt.UTC).isoformat(),
                        val_raw,
                    ),
                )
                summary.accepted_rows += 1

                # Log issue AFTER inserting reading to satisfy FK constraint
                if issue_to_log:
                    _log_issue(
                        conn,
                        batch_id=issue_to_log[0],
                        issue_type=issue_to_log[1],
                        description=issue_to_log[2],
                        reading_id=issue_to_log[3],
                    )

            except sqlite3.IntegrityError:
                # Catches duplicates that exist in the DB already
                summary.rejected_rows += 1
                summary.duplicate_rows += 1
                _log_issue(
                    conn,
                    batch_id=batch_id,
                    issue_type="duplicate",
                    description=f"Duplicate in DB: {meter_id} @ {ts_utc}",
                    meter_id=meter_id,
                    reading_timestamp_utc=ts_utc,
                )

        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise

    return summary
