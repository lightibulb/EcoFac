# Copyright 2026 Lightibulb
"""CSV ingestion and normalization pipeline."""
import csv
import dataclasses
import datetime as dt
import io
import math
import sqlite3
import typing
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


class _Reading(typing.NamedTuple):
    """One validated reading, in the column order of the energy_readings insert."""

    id: str
    meter_id: str
    timestamp_utc: str
    value: float
    unit: str
    source_id: str
    batch_id: str
    is_valid: int
    created_at: str
    raw_value: str


UNITS = {"wh": 0.001, "kwh": 1.0, "mwh": 1000.0}
REQUIRED_COLUMNS = frozenset({"timestamp", "building_id", "meter_id", "energy_value", "unit"})


def _issue_row(
    batch_id: str,
    issue_type: str,
    description: str,
    created_at: str,
    *,
    reading_id: str | None = None,
    meter_id: str | None = None,
    reading_timestamp_utc: str | None = None,
) -> tuple:
    """Build one data_quality_issues row."""
    return (
        str(uuid.uuid4()),
        reading_id,
        batch_id,
        issue_type,
        description,
        created_at,
        meter_id,
        reading_timestamp_utc,
    )


def _existing_pairs(
    conn: sqlite3.Connection, meter_ids: list[str]
) -> set[tuple[str, str]]:
    """Fetch (meter_id, timestamp_utc) pairs already stored, chunked."""
    unique_meters = sorted(set(meter_ids))
    existing: set[tuple[str, str]] = set()
    chunk_size = 500
    for i in range(0, len(unique_meters), chunk_size):
        chunk = unique_meters[i:i + chunk_size]
        placeholders = ",".join("?" * len(chunk))
        cursor = conn.execute(
            f"SELECT meter_id, timestamp_utc FROM energy_readings "
            f"WHERE meter_id IN ({placeholders})",
            chunk,
        )
        existing.update(
            (row["meter_id"], row["timestamp_utc"]) for row in cursor.fetchall()
        )
    return existing


def normalize_headers(reader: csv.DictReader) -> None:
    """Strip and lower-case header names so ' Timestamp ' still matches."""
    if reader.fieldnames:
        reader.fieldnames = [(name or "").strip().lower() for name in reader.fieldnames]


def _parse_timestamp(ts_raw: str) -> str:
    """Return the canonical UTC 'Z' form (whole seconds) or raise ValueError.

    Naive timestamps are rejected. Sub-second precision is dropped so that
    string ordering of stored timestamps always matches time ordering.
    """
    dt_val = dt.datetime.fromisoformat(ts_raw)
    if dt_val.tzinfo is None:
        msg = f"Naive timestamp: {ts_raw}"
        raise ValueError(msg)
    dt_utc = dt_val.astimezone(dt.UTC).replace(microsecond=0)
    return dt_utc.isoformat().replace("+00:00", "Z")


def ingest_csv(
    conn: sqlite3.Connection,
    csv_content: str,
    source_id: str,
    batch_id: str,
    *,
    max_rows: int = 50000,
    max_size_mb: float = 5.0,
) -> IngestionSummary:
    """Parse, validate, and store readings from a CSV string.

    Valid readings and quality issues are bulk-inserted with executemany so
    large imports stay fast on slow hosts (e.g. Render free tier). The whole
    import is one write transaction: either every accepted row lands or none.
    """
    if len(csv_content) > max_size_mb * 1024 * 1024:
        msg = "File size exceeds limit"
        raise ValueError(msg)

    reader = csv.DictReader(io.StringIO(csv_content))
    normalize_headers(reader)
    if not reader.fieldnames or not REQUIRED_COLUMNS.issubset(reader.fieldnames):
        msg = f"Missing required columns. Expected: {sorted(REQUIRED_COLUMNS)}"
        raise ValueError(msg)

    summary = IngestionSummary()
    created_at = now_z()

    seen: set[tuple[str, str]] = set()
    pending: list[_Reading] = []
    issues: list[tuple] = []

    # IMMEDIATE takes the write lock up front, so the duplicate check below and
    # the inserts cannot race with a concurrent import.
    conn.execute("BEGIN IMMEDIATE")
    try:
        cursor = conn.execute("SELECT id, building_id FROM meters")
        meter_map = {row["id"]: row["building_id"] for row in cursor.fetchall()}

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
                issues.append(_issue_row(
                    batch_id, "unknown_meter", f"Unknown meter: {meter_id}", created_at,
                ))
                continue
            if meter_map[meter_id] != building_id:
                summary.rejected_rows += 1
                issues.append(_issue_row(
                    batch_id,
                    "building_mismatch",
                    f"Meter {meter_id} does not belong to building {building_id}",
                    created_at,
                ))
                continue

            # 2. Normalize timestamp to UTC (naive timestamps are rejected)
            ts_raw = (row.get("timestamp") or "").strip()
            try:
                ts_utc = _parse_timestamp(ts_raw)
            except ValueError:
                summary.rejected_rows += 1
                summary.invalid_timestamps += 1
                issues.append(_issue_row(
                    batch_id, "invalid_timestamp", f"Invalid timestamp: {ts_raw}", created_at,
                ))
                continue

            # 3. Unit normalization
            unit_raw = (row.get("unit") or "").strip().lower()
            if unit_raw not in UNITS:
                summary.rejected_rows += 1
                summary.invalid_units += 1
                issues.append(_issue_row(
                    batch_id, "invalid_unit", f"Unsupported unit: {unit_raw}", created_at,
                ))
                continue

            # 4. Value parsing (NaN / infinity would poison every statistic)
            val_raw = (row.get("energy_value") or "").strip()
            try:
                val_kwh = float(val_raw) * UNITS[unit_raw]
                if not math.isfinite(val_kwh):
                    msg = "Non-finite value"
                    raise ValueError(msg)
            except ValueError:
                summary.rejected_rows += 1
                issues.append(_issue_row(
                    batch_id, "malformed_value", f"Invalid value: {val_raw}", created_at,
                ))
                continue

            # 5. Duplicate check (within CSV). Only fully valid rows claim a key,
            #    so a rejected row can never shadow a good one.
            key = (meter_id, ts_utc)
            if key in seen:
                summary.rejected_rows += 1
                summary.duplicate_rows += 1
                issues.append(_issue_row(
                    batch_id, "duplicate",
                    f"Duplicate in CSV: {meter_id} @ {ts_utc}", created_at,
                    meter_id=meter_id, reading_timestamp_utc=ts_utc,
                ))
                continue
            seen.add(key)

            pending.append(_Reading(
                str(uuid.uuid4()), meter_id, ts_utc, val_kwh, "kWh", source_id,
                batch_id, int(val_kwh >= 0), created_at, val_raw,
            ))

        # 6. Filter rows that already exist in the DB (bulk duplicate check)
        existing = _existing_pairs(conn, [r.meter_id for r in pending])
        final: list[_Reading] = []
        for rec in pending:
            if (rec.meter_id, rec.timestamp_utc) in existing:
                summary.rejected_rows += 1
                summary.duplicate_rows += 1
                issues.append(_issue_row(
                    batch_id, "duplicate",
                    f"Duplicate in DB: {rec.meter_id} @ {rec.timestamp_utc}", created_at,
                    meter_id=rec.meter_id, reading_timestamp_utc=rec.timestamp_utc,
                ))
            else:
                final.append(rec)
        summary.accepted_rows = len(final)

        # 7. Flag negative consumption only for rows that are really stored
        #    (an issue pointing at a skipped row would violate the FK).
        for rec in final:
            if rec.is_valid == 0:
                summary.negative_consumption += 1
                issues.append(_issue_row(
                    batch_id, "negative_consumption", f"Negative reading: {rec.value} kWh",
                    created_at, reading_id=rec.id, meter_id=rec.meter_id,
                    reading_timestamp_utc=rec.timestamp_utc,
                ))

        # 8. Bulk insert readings first, then issues (issues may reference
        #    reading ids, so the FK requires this order)
        if final:
            conn.executemany(
                """INSERT INTO energy_readings
                (id, meter_id, timestamp_utc, value, unit, source_id, batch_id,
                is_valid, created_at, raw_value)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                final,
            )
        if issues:
            conn.executemany(
                """INSERT INTO data_quality_issues
                (id, reading_id, batch_id, issue_type, description, created_at,
                meter_id, reading_timestamp_utc)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                issues,
            )

        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise

    return summary
