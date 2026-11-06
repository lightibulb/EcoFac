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

from ecofac.constants import MAX_VALID_READING_KWH
from ecofac.ingestion import entities
from ecofac.persistence.tx import persist_atomically
from ecofac.timeutil import now_z, parse_utc


class IngestionError(ValueError):
    """A fatal, whole-file import problem, as opposed to one rejected row.

    Subclasses ``ValueError`` so callers that already catch ``ValueError``
    keep working. ``code`` is stable and machine-readable; ``details`` carries
    extra context (limits, line numbers). Raising it always means the import
    was rolled back: no reading, issue or entity from the file was kept.
    """

    def __init__(self, code: str, message: str, **details: object) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details


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
    invalid_ids: int = 0
    buildings_created: int = 0
    meters_created: int = 0


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


def _existing_pairs(conn: sqlite3.Connection, meter_ids: list[str]) -> set[tuple[str, str]]:
    """Fetch (meter_id, timestamp_utc) pairs already stored, chunked."""
    unique_meters = sorted(set(meter_ids))
    existing: set[tuple[str, str]] = set()
    chunk_size = 500
    for i in range(0, len(unique_meters), chunk_size):
        chunk = unique_meters[i : i + chunk_size]
        placeholders = ",".join("?" * len(chunk))
        cursor = conn.execute(
            f"SELECT meter_id, timestamp_utc FROM energy_readings "
            f"WHERE meter_id IN ({placeholders})",
            chunk,
        )
        existing.update((row["meter_id"], row["timestamp_utc"]) for row in cursor.fetchall())
    return existing


def normalize_headers(reader: csv.DictReader) -> None:
    """Strip and lower-case header names so ' Timestamp ' still matches."""
    if reader.fieldnames:
        reader.fieldnames = [(name or "").strip().lower() for name in reader.fieldnames]


def _parse_timestamp(ts_raw: str) -> str:
    """Return canonical UTC 'Z' form (whole seconds) or raise ValueError.

    Naive timestamps are rejected. Sub-second precision is dropped so that
    string ordering of stored timestamps always matches time ordering.
    Timestamps whose UTC equivalent falls outside year 1..9999 (for example
    ``0001-01-01T00:00:00+05:00``) raise ``OverflowError`` in the standard
    library; that is reported as an invalid timestamp, never as a crash.
    """
    try:
        dt_val = dt.datetime.fromisoformat(ts_raw)
        if dt_val.tzinfo is None:
            msg = f"Naive timestamp: {ts_raw}"
            raise ValueError(msg)
        dt_utc = dt_val.astimezone(dt.UTC).replace(microsecond=0)
    except OverflowError as exc:
        msg = f"Timestamp out of supported range: {ts_raw}"
        raise ValueError(msg) from exc
    return dt_utc.isoformat().replace("+00:00", "Z")


def ingest_csv(
    conn: sqlite3.Connection,
    csv_content: str,
    source_id: str,
    batch_id: str,
    *,
    max_rows: int = 50000,
    max_size_mb: float = 5.0,
    auto_create_entities: bool = False,
) -> IngestionSummary:
    """Parse, validate, and store readings from a CSV string.

    Row-level problems (bad timestamp, unit, value, duplicate, unknown meter)
    reject that row and are counted in the summary. Whole-file problems raise
    ``IngestionError`` (a ``ValueError``) and leave the database untouched.

    Transactions: the work runs through ``persist_atomically``. Called on its
    own it is one ``BEGIN IMMEDIATE`` transaction; called inside a caller's
    transaction (the import route does this so the source, batch, entities,
    readings and final batch status commit together) it uses a savepoint and
    never commits by itself.

    ``auto_create_entities``: when true, a meter that does not exist yet is
    created together with its building, but only if its identifiers are valid
    and at least one row for it is otherwise storable. When false (default,
    library behaviour) an unknown meter rejects the row.

    Also: out-of-order rows are flagged (informational), gaps over 2x the
    meter interval are flagged, and readings above MAX_VALID_READING_KWH are
    stored with is_valid=0 and a ``suspicious_value`` issue.
    """
    limit_bytes = int(max_size_mb * 1024 * 1024)
    if len(csv_content) > limit_bytes:
        raise IngestionError("file_too_large", "File size exceeds limit", limit_bytes=limit_bytes)

    reader = csv.DictReader(io.StringIO(csv_content))
    try:
        normalize_headers(reader)
        fieldnames = reader.fieldnames
    except csv.Error as exc:
        raise IngestionError(
            "malformed_csv", f"Malformed CSV header: {exc}", line=reader.line_num
        ) from exc
    if not fieldnames or not REQUIRED_COLUMNS.issubset(fieldnames):
        raise IngestionError(
            "missing_columns",
            f"Missing required columns. Expected: {sorted(REQUIRED_COLUMNS)}",
            required=sorted(REQUIRED_COLUMNS),
            found=[name for name in (fieldnames or []) if name],
        )

    def _run() -> IngestionSummary:
        return _ingest_rows(
            conn,
            reader,
            source_id,
            batch_id,
            max_rows=max_rows,
            auto_create_entities=auto_create_entities,
        )

    return persist_atomically(conn, _run)


def _ingest_rows(
    conn: sqlite3.Connection,
    reader: csv.DictReader,
    source_id: str,
    batch_id: str,
    *,
    max_rows: int,
    auto_create_entities: bool,
) -> IngestionSummary:
    """Validate and insert every row. Runs inside a transaction (see ingest_csv)."""
    summary = IngestionSummary()
    created_at = now_z()

    seen: set[tuple[str, str]] = set()
    last_ts_per_meter: dict[str, str] = {}
    pending: list[_Reading] = []
    issues: list[tuple] = []

    cursor = conn.execute("SELECT id, building_id, interval_minutes FROM meters")
    meter_map = {
        row["id"]: (row["building_id"], row["interval_minutes"]) for row in cursor.fetchall()
    }

    try:
        for row in reader:
            summary.total_rows += 1
            if summary.total_rows > max_rows:
                raise IngestionError(
                    "row_limit_exceeded", "Row count exceeds limit", max_rows=max_rows
                )

            meter_id = (row.get("meter_id") or "").strip()
            building_id = (row.get("building_id") or "").strip()

            # 1. Identifiers. A meter is either known (its building must match)
            # or, when auto-creation is on, new (its identifiers must be valid).
            is_new_meter = False
            if meter_id not in meter_map:
                if not auto_create_entities:
                    summary.rejected_rows += 1
                    summary.unknown_meter_ids += 1
                    issues.append(
                        _issue_row(
                            batch_id,
                            "unknown_meter",
                            f"Unknown meter: {meter_id}",
                            created_at,
                        )
                    )
                    continue
                reason = entities.invalid_identifier_reason(building_id, meter_id)
                if reason is not None:
                    summary.rejected_rows += 1
                    summary.invalid_ids += 1
                    issues.append(
                        _issue_row(
                            batch_id,
                            "invalid_identifier",
                            f"Invalid identifier on line {reader.line_num}: {reason}",
                            created_at,
                        )
                    )
                    continue
                is_new_meter = True
            else:
                expected_building, _ = meter_map[meter_id]
                if expected_building != building_id:
                    summary.rejected_rows += 1
                    issues.append(
                        _issue_row(
                            batch_id,
                            "building_mismatch",
                            f"Meter {meter_id} does not belong to building {building_id}",
                            created_at,
                        )
                    )
                    continue

            # 2. Timestamp
            ts_raw = (row.get("timestamp") or "").strip()
            try:
                ts_utc = _parse_timestamp(ts_raw)
            except ValueError:
                summary.rejected_rows += 1
                summary.invalid_timestamps += 1
                issues.append(
                    _issue_row(
                        batch_id,
                        "invalid_timestamp",
                        f"Invalid timestamp: {ts_raw}",
                        created_at,
                    )
                )
                continue

            # 3. Unit
            unit_raw = (row.get("unit") or "").strip().lower()
            if unit_raw not in UNITS:
                summary.rejected_rows += 1
                summary.invalid_units += 1
                issues.append(
                    _issue_row(
                        batch_id,
                        "invalid_unit",
                        f"Unsupported unit: {unit_raw}",
                        created_at,
                    )
                )
                continue

            # 4. Value
            val_raw = (row.get("energy_value") or "").strip()
            try:
                val_kwh = float(val_raw) * UNITS[unit_raw]
                if not math.isfinite(val_kwh):
                    msg = "Non-finite value"
                    raise ValueError(msg)
            except ValueError:
                summary.rejected_rows += 1
                issues.append(
                    _issue_row(
                        batch_id,
                        "malformed_value",
                        f"Invalid value: {val_raw}",
                        created_at,
                    )
                )
                continue

            # 5. Duplicate (within CSV)
            key = (meter_id, ts_utc)
            if key in seen:
                summary.rejected_rows += 1
                summary.duplicate_rows += 1
                issues.append(
                    _issue_row(
                        batch_id,
                        "duplicate",
                        f"Duplicate in CSV: {meter_id} @ {ts_utc}",
                        created_at,
                        meter_id=meter_id,
                        reading_timestamp_utc=ts_utc,
                    )
                )
                continue
            seen.add(key)

            # 5a. The row is storable: only now create a missing meter/building.
            if is_new_meter:
                if summary.meters_created >= entities.MAX_AUTO_ENTITIES:
                    raise IngestionError(
                        "entity_limit_exceeded",
                        f"CSV references more than {entities.MAX_AUTO_ENTITIES} new meters",
                        max_new_meters=entities.MAX_AUTO_ENTITIES,
                    )
                building_created, meter_created = entities.ensure_building_and_meter(
                    conn, building_id, meter_id, created_at
                )
                summary.buildings_created += int(building_created)
                summary.meters_created += int(meter_created)
                meter_map[meter_id] = (building_id, entities.DEFAULT_INTERVAL_MINUTES)

            # 5b. Out-of-order (informational only)
            prev_high = last_ts_per_meter.get(meter_id)
            if prev_high is not None and ts_utc < prev_high:
                issues.append(
                    _issue_row(
                        batch_id,
                        "out_of_order",
                        f"Out-of-order: {meter_id} @ {ts_utc} arrived after {prev_high}",
                        created_at,
                        meter_id=meter_id,
                        reading_timestamp_utc=ts_utc,
                    )
                )
            if prev_high is None or ts_utc > prev_high:
                last_ts_per_meter[meter_id] = ts_utc

            # 5c. Bounds
            is_valid_int = 1 if 0 <= val_kwh <= MAX_VALID_READING_KWH else 0

            pending.append(
                _Reading(
                    str(uuid.uuid4()),
                    meter_id,
                    ts_utc,
                    val_kwh,
                    "kWh",
                    source_id,
                    batch_id,
                    is_valid_int,
                    created_at,
                    val_raw,
                )
            )
    except csv.Error as exc:
        # e.g. "field larger than field limit": the reader cannot continue.
        raise IngestionError(
            "malformed_csv",
            f"Malformed CSV near line {reader.line_num}: {exc}",
            line=reader.line_num,
        ) from exc

    # 6. Bulk DB duplicate filter
    existing = _existing_pairs(conn, [r.meter_id for r in pending])
    final: list[_Reading] = []
    for rec in pending:
        if (rec.meter_id, rec.timestamp_utc) in existing:
            summary.rejected_rows += 1
            summary.duplicate_rows += 1
            issues.append(
                _issue_row(
                    batch_id,
                    "duplicate",
                    f"Duplicate in DB: {rec.meter_id} @ {rec.timestamp_utc}",
                    created_at,
                    meter_id=rec.meter_id,
                    reading_timestamp_utc=rec.timestamp_utc,
                )
            )
        else:
            final.append(rec)
    summary.accepted_rows = len(final)

    # 6b. Gap detection on final (post-dedup), sorted per meter
    pending_by_meter: dict[str, list[_Reading]] = {}
    for rec in final:
        pending_by_meter.setdefault(rec.meter_id, []).append(rec)
    for gap_meter, group in pending_by_meter.items():
        group.sort(key=lambda r: r.timestamp_utc)
        _, interval = meter_map[gap_meter]
        for i in range(1, len(group)):
            prev_dt = parse_utc(group[i - 1].timestamp_utc)
            curr_dt = parse_utc(group[i].timestamp_utc)
            gap_min = (curr_dt - prev_dt).total_seconds() / 60.0
            if gap_min > 2 * interval:
                issues.append(
                    _issue_row(
                        batch_id,
                        "irregular_sampling",
                        f"Gap {gap_min:.0f}min > 2x{interval}min for {gap_meter}",
                        created_at,
                        meter_id=gap_meter,
                        reading_timestamp_utc=group[i].timestamp_utc,
                    )
                )

    # 7. Flag invalid stored rows (negative OR suspicious)
    for rec in final:
        if rec.is_valid == 0:
            if rec.value < 0:
                summary.negative_consumption += 1
                issues.append(
                    _issue_row(
                        batch_id,
                        "negative_consumption",
                        f"Negative reading: {rec.value} kWh",
                        created_at,
                        reading_id=rec.id,
                        meter_id=rec.meter_id,
                        reading_timestamp_utc=rec.timestamp_utc,
                    )
                )
            else:
                issues.append(
                    _issue_row(
                        batch_id,
                        "suspicious_value",
                        f"Suspicious reading: {rec.value} kWh exceeds {MAX_VALID_READING_KWH}",
                        created_at,
                        reading_id=rec.id,
                        meter_id=rec.meter_id,
                        reading_timestamp_utc=rec.timestamp_utc,
                    )
                )

    # 8. Insert readings first, then issues (FK order)
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
    return summary
