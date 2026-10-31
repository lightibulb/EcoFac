# Copyright 2026 Lightibulb
"""CSV ingestion and normalization pipeline."""
import csv
import dataclasses
import io
import sqlite3
import uuid

from ecofac.timeutil import now_z, parse_utc, to_z


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
    batch_id: str,
    issue_type: str,
    description: str,
    reading_id: str | None = None,
    meter_id: str | None = None,
    reading_timestamp_utc: str | None = None,
) -> None:
    conn.execute(
        "INSERT INTO data_quality_issues "
        "(id, reading_id, batch_id, issue_type, description, meter_id, "
        "reading_timestamp_utc, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            str(uuid.uuid4()),
            reading_id,
            batch_id,
            issue_type,
            description,
            meter_id,
            reading_timestamp_utc,
            now_z(),
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
                _log_issue(conn, batch_id, "unknown_meter", f"Unknown meter: {meter_id}")
                continue
            if meter_map[meter_id] != building_id:
                summary.rejected_rows += 1
                _log_issue(
                    conn,
                    batch_id,
                    "building_mismatch",
                    f"Meter {meter_id} does not belong to building {building_id}",
                    meter_id=meter_id,
                )
                continue

            # 2. Normalize timestamp to UTC. Naive timestamps are REJECTED:
            #    previously they were interpreted as server-local time.
            ts_raw = (row.get("timestamp") or "").strip()
            try:
                ts_utc = to_z(parse_utc(ts_raw))
            except ValueError:
                summary.rejected_rows += 1
                summary.invalid_timestamps += 1
                _log_issue(
                    conn,
                    batch_id,
                    "invalid_timestamp",
                    f"Invalid or naive timestamp: {ts_raw}",
                    meter_id=meter_id,
                )
                continue

            # 3. Duplicate check (within CSV)
            key = (meter_id, ts_utc)
            if key in seen:
                summary.rejected_rows += 1
                summary.duplicate_rows += 1
                _log_issue(
                    conn,
                    batch_id,
                    "duplicate",
                    f"Duplicate in CSV: {meter_id} @ {ts_utc}",
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
                    batch_id,
                    "invalid_unit",
                    f"Unsupported unit: {unit_raw}",
                    meter_id=meter_id,
                    reading_timestamp_utc=ts_utc,
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
                    batch_id,
                    "malformed_value",
                    f"Invalid value: {val_raw}",
                    meter_id=meter_id,
                    reading_timestamp_utc=ts_utc,
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
                    meter_id,
                    ts_utc,
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
                        now_z(),
                        val_raw,
                    ),
                )
                summary.accepted_rows += 1
                # Log issue AFTER inserting reading to satisfy FK constraint
                if issue_to_log:
                    _log_issue(conn, *issue_to_log)
            except sqlite3.IntegrityError:
                # Duplicate that already exists in the DB
                summary.rejected_rows += 1
                summary.duplicate_rows += 1
                _log_issue(
                    conn,
                    batch_id,
                    "duplicate",
                    f"Duplicate in DB: {meter_id} @ {ts_utc}",
                    meter_id=meter_id,
                    reading_timestamp_utc=ts_utc,
                )

        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise

    return summary
