# Copyright 2026 Lightibulb
"""CSV ingestion and normalization pipeline."""
import csv
import dataclasses
import datetime as dt
import io
import sqlite3
import uuid
from ecofac.timeutil import now_z

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
    now = now_z()
    
    valid_rows = []
    issues_to_log = []

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
            issues_to_log.append((str(uuid.uuid4()), None, batch_id, "unknown_meter", f"Unknown meter: {meter_id}", now, None, None))
            continue

        if meter_map[meter_id] != building_id:
            summary.rejected_rows += 1
            issues_to_log.append((str(uuid.uuid4()), None, batch_id, "building_mismatch", f"Meter {meter_id} does not belong to building {building_id}", now, meter_id, None))
            continue

        # 2. Normalize Timestamp to UTC
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
            issues_to_log.append((str(uuid.uuid4()), None, batch_id, "invalid_timestamp", f"Invalid timestamp: {ts_raw}", now, meter_id, None))
            continue

        # 3. Duplicate check (within CSV)
        key = (meter_id, ts_utc)
        if key in seen:
            summary.rejected_rows += 1
            summary.duplicate_rows += 1
            issues_to_log.append((str(uuid.uuid4()), None, batch_id, "duplicate", f"Duplicate in CSV: {meter_id} @ {ts_utc}", now, meter_id, ts_utc))
            continue
        seen.add(key)

        # 4. Unit normalization
        unit_raw = (row.get("unit") or "").strip().lower()
        if unit_raw not in UNITS:
            summary.rejected_rows += 1
            summary.invalid_units += 1
            issues_to_log.append((str(uuid.uuid4()), None, batch_id, "invalid_unit", f"Unsupported unit: {unit_raw}", now, meter_id, ts_utc))
            continue

        # 5. Value parsing
        val_raw = (row.get("energy_value") or "").strip()
        try:
            val = float(val_raw)
        except ValueError:
            summary.rejected_rows += 1
            issues_to_log.append((str(uuid.uuid4()), None, batch_id, "malformed_value", f"Invalid value: {val_raw}", now, meter_id, ts_utc))
            continue

        val_kwh = val * UNITS[unit_raw]
        reading_id = str(uuid.uuid4())
        is_valid = 1
        issue_to_log = None

        # 6. Negative consumption flagging
        if val_kwh < 0:
            is_valid = 0
            summary.negative_consumption += 1
            issue_to_log = (str(uuid.uuid4()), reading_id, batch_id, "negative_consumption", f"Negative reading: {val_kwh} kWh", now, meter_id, ts_utc)

        valid_rows.append((
            reading_id, meter_id, ts_utc, val_kwh, "kWh", source_id, batch_id, is_valid, now, val_raw
        ))
        if issue_to_log:
            issues_to_log.append(issue_to_log)

    # Check for DB duplicates efficiently in a single query instead of N try/except blocks
    meter_ids = list({r[1] for r in valid_rows})
    existing_pairs = set()
    if meter_ids:
        chunk_size = 900  # SQLite limits on IN clause variables
        for i in range(0, len(meter_ids), chunk_size):
            chunk = meter_ids[i:i+chunk_size]
            placeholders = ",".join("?" * len(chunk))
            cursor = conn.execute(
                f"SELECT meter_id, timestamp_utc FROM energy_readings WHERE meter_id IN ({placeholders})",
                chunk
            )
            existing_pairs.update((row["meter_id"], row["timestamp_utc"]) for row in cursor.fetchall())

    final_readings = []
    for r in valid_rows:
        reading_id, meter_id, ts_utc = r[0], r[1], r[2]
        if (meter_id, ts_utc) in existing_pairs:
            summary.rejected_rows += 1
            summary.duplicate_rows += 1
            issues_to_log.append((str(uuid.uuid4()), None, batch_id, "duplicate", f"Duplicate in DB: {meter_id} @ {ts_utc}", now, meter_id, ts_utc))
        else:
            final_readings.append(r)

    summary.accepted_rows = len(final_readings)

    # Bulk Insert
    conn.execute("BEGIN")
    try:
        if final_readings:
            conn.executemany(
                """INSERT INTO energy_readings
                (id, meter_id, timestamp_utc, value, unit, source_id, batch_id,
                is_valid, created_at, raw_value)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                final_readings
            )

        if issues_to_log:
            conn.executemany(
                """INSERT INTO data_quality_issues
                (id, reading_id, batch_id, issue_type, description, created_at, meter_id, reading_timestamp_utc)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                issues_to_log
            )

        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise

    return summary
