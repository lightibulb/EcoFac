# Copyright 2026 Lightibulb

"""HTTP-level reliability tests for CSV imports: structured errors and all-or-nothing writes."""

import csv
import json
import sqlite3

import pytest

from ecofac.api.routes import imports as imports_route
from ecofac.constants import MAX_UPLOAD_BYTES, MULTIPART_OVERHEAD_BYTES
from ecofac.ingestion import entities

HEADER = "timestamp,building_id,meter_id,energy_value,unit\n"
TABLES = (
    "data_sources",
    "import_batches",
    "organizations",
    "buildings",
    "meters",
    "energy_readings",
    "data_quality_issues",
)


def _row(ts="2026-01-01T00:00:00Z", building="b1", meter="m1", value="1.0", unit="kWh"):
    return f"{ts},{building},{meter},{value},{unit}\n"


def _post(client, content, batch="ib-x", source="src-x", **params):
    if isinstance(content, str):
        content = content.encode("utf-8")
    return client.post(
        "/api/v1/imports",
        files={"file": ("data.csv", content, "text/csv")},
        data={"source_id": source, "batch_id": batch},
        params=params,
    )


def _snapshot(db):
    return {
        table: db.execute(f"SELECT count(*) AS c FROM {table}").fetchone()["c"] for table in TABLES
    }


def _error(resp):
    detail = resp.json()["detail"]
    assert isinstance(detail, dict), detail
    assert {"code", "message"} <= set(detail)
    return detail


# ---------------------------------------------------------------- success path


def test_successful_import_is_recorded_atomically_and_reports_entities(client, test_db):
    resp = _post(client, HEADER + _row(building="b-new", meter="m-new"))
    assert resp.status_code == 200
    body = resp.json()
    assert (body["accepted_rows"], body["buildings_created"], body["meters_created"]) == (1, 1, 1)

    batch = test_db.execute("SELECT * FROM import_batches WHERE id = 'ib-x'").fetchone()
    assert batch["status"] == "completed"
    assert json.loads(batch["summary_json"])["accepted_rows"] == 1
    assert client.get("/api/v1/imports/ib-x").json()["status"] == "completed"


def test_existing_supported_behaviour_is_unchanged(client):
    # Unknown source, existing building/meter, BOM, offsets: all still accepted.
    text = "\ufeff" + HEADER + _row(ts="2026-01-01T12:00:00+02:00")
    resp = _post(client, text, source="brand-new-source")
    assert resp.status_code == 200
    assert resp.json()["accepted_rows"] == 1


# ---------------------------------------------------------------- failures leave no trace


def test_missing_columns_is_a_structured_400_and_leaves_no_trace(client, test_db):
    before = _snapshot(test_db)
    resp = _post(client, "timestamp,building_id\n2026-01-01T00:00:00Z,b-new\n")
    assert resp.status_code == 400
    detail = _error(resp)
    assert detail["code"] == "missing_columns"
    assert detail["rolled_back"] is True
    assert "meter_id" in detail["details"]["required"]
    assert _snapshot(test_db) == before
    assert client.get("/api/v1/imports/ib-x").status_code == 404


def test_failed_batch_id_can_be_retried(client):
    assert _post(client, "bad,header\n1,2\n").status_code == 400
    assert _post(client, HEADER + _row()).status_code == 200


def test_invalid_utf8_is_a_structured_400_and_leaves_no_trace(client, test_db):
    before = _snapshot(test_db)
    resp = _post(client, b"\xff\xfe\x00bad")
    assert resp.status_code == 400
    assert _error(resp)["code"] == "invalid_encoding"
    assert _snapshot(test_db) == before


def test_malformed_csv_field_is_a_400_not_a_500(client, test_db):
    before = _snapshot(test_db)
    huge = "9" * (csv.field_size_limit() + 10)
    resp = _post(client, HEADER + _row() + _row(ts="2026-01-01T01:00:00Z", value=huge))
    assert resp.status_code == 400
    detail = _error(resp)
    assert detail["code"] == "malformed_csv"
    assert detail["rolled_back"] is True
    assert _snapshot(test_db) == before


def test_row_limit_is_a_structured_413_and_leaves_no_trace(client, test_db):
    before = _snapshot(test_db)
    resp = _post(client, HEADER + _row() * 50_001)
    assert resp.status_code == 413
    detail = _error(resp)
    assert detail["code"] == "row_limit_exceeded"
    assert detail["details"]["max_rows"] == 50_000
    assert _snapshot(test_db) == before


def test_too_many_new_meters_is_a_structured_422_and_leaves_no_trace(client, test_db, monkeypatch):
    monkeypatch.setattr(entities, "MAX_AUTO_ENTITIES", 1)
    before = _snapshot(test_db)
    text = HEADER + _row(building="b-a", meter="m-a") + _row(building="b-b", meter="m-b")
    resp = _post(client, text)
    assert resp.status_code == 422
    assert _error(resp)["code"] == "entity_limit_exceeded"
    assert _snapshot(test_db) == before


def test_out_of_range_timestamp_is_a_rejected_row_not_a_500(client):
    resp = _post(client, HEADER + _row(ts="0001-01-01T00:00:00+05:00") + _row())
    assert resp.status_code == 200
    body = resp.json()
    assert body["invalid_timestamps"] == 1
    assert body["accepted_rows"] == 1


def test_all_rows_rejected_creates_no_entities(client, test_db):
    before = _snapshot(test_db)
    resp = _post(client, HEADER + _row(ts="nope", building="b-z", meter="m-z"))
    assert resp.status_code == 200
    body = resp.json()
    assert (body["accepted_rows"], body["meters_created"], body["buildings_created"]) == (0, 0, 0)
    after = _snapshot(test_db)
    for table in ("organizations", "buildings", "meters", "energy_readings"):
        assert after[table] == before[table]


def test_invalid_identifier_rows_are_counted_and_not_created(client, test_db):
    text = HEADER + _row() + _row(building="bad id!", meter="m-q", ts="2026-01-01T01:00:00Z")
    resp = _post(client, text)
    assert resp.status_code == 200
    body = resp.json()
    assert body["accepted_rows"] == 1
    assert body["invalid_ids"] == 1
    assert test_db.execute("SELECT 1 FROM buildings WHERE id = 'bad id!'").fetchone() is None
    assert test_db.execute("SELECT 1 FROM meters WHERE id = 'm-q'").fetchone() is None


# ---------------------------------------------------------------- upload limits


def test_oversized_request_is_rejected_by_the_body_limit_before_parsing(client, test_db):
    before = _snapshot(test_db)
    big = "a" * (MAX_UPLOAD_BYTES + MULTIPART_OVERHEAD_BYTES + 1024)
    resp = _post(client, big)
    assert resp.status_code == 413
    assert _error(resp)["code"] == "request_too_large"
    assert _snapshot(test_db) == before


def test_file_just_over_the_limit_is_rejected_by_the_exact_file_check(client, test_db):
    before = _snapshot(test_db)
    resp = _post(client, "a" * (MAX_UPLOAD_BYTES + 1))
    assert resp.status_code == 413
    detail = _error(resp)
    assert detail["code"] == "file_too_large"
    assert detail["details"]["limit_bytes"] == MAX_UPLOAD_BYTES
    assert _snapshot(test_db) == before


# ---------------------------------------------------------------- duplicates and conflicts


def test_importing_the_same_rows_under_a_new_batch_adds_nothing(client, test_db):
    text = HEADER + _row() + _row(ts="2026-01-01T01:00:00Z")
    assert _post(client, text, batch="ib-1").json()["accepted_rows"] == 2
    readings = _snapshot(test_db)["energy_readings"]
    again = _post(client, text, batch="ib-2").json()
    assert (again["accepted_rows"], again["duplicate_rows"]) == (0, 2)
    assert _snapshot(test_db)["energy_readings"] == readings


def test_batch_conflicts_have_codes_and_change_nothing(client, test_db):
    assert _post(client, HEADER + _row(), batch="ib-1").status_code == 200
    before = _snapshot(test_db)
    same = _post(client, HEADER + _row(ts="2026-01-01T05:00:00Z"), batch="ib-1")
    assert same.status_code == 409
    assert _error(same)["code"] == "batch_already_imported"
    other = _post(client, HEADER + _row(ts="2026-01-01T05:00:00Z"), batch="ib-1", source="other")
    assert other.status_code == 409
    assert _error(other)["code"] == "batch_source_conflict"
    assert _snapshot(test_db) == before


# ---------------------------------------------------------------- rollback on internal failure


def _failing_after_real_ingest(exc):
    real = imports_route.ingest_csv

    def wrapper(*args, **kwargs):
        real(*args, **kwargs)  # rows, issues and entities are now written...
        raise exc  # ...then something fails before the import completes

    return wrapper


@pytest.mark.parametrize(
    ("exc", "status", "code"),
    [
        (sqlite3.OperationalError("disk I/O error"), 500, "database_error"),
        (sqlite3.OperationalError("database is locked"), 503, "database_busy"),
        (RuntimeError("boom"), 500, "internal_error"),
    ],
)
def test_internal_failure_rolls_back_source_batch_entities_and_readings(
    client, test_db, monkeypatch, exc, status, code
):
    before = _snapshot(test_db)
    monkeypatch.setattr(imports_route, "ingest_csv", _failing_after_real_ingest(exc))
    resp = _post(client, HEADER + _row(building="b-new", meter="m-new"), source="src-new")
    assert resp.status_code == status
    detail = _error(resp)
    assert detail["code"] == code
    assert detail["rolled_back"] is True
    assert _snapshot(test_db) == before
    assert client.get("/api/v1/imports/ib-x").status_code == 404


def test_analysis_failure_after_commit_does_not_undo_the_import(client, test_db, monkeypatch):
    def boom(*_args, **_kwargs):
        raise sqlite3.Error("analysis exploded")

    monkeypatch.setattr(imports_route, "analyze_all", boom)
    resp = _post(client, HEADER + _row())
    assert resp.status_code == 200
    assert resp.json()["accepted_rows"] == 1
    assert _snapshot(test_db)["energy_readings"] == 1
    # P2-2: analysis failure leaves the batch as "analysis_failed"
    # (visible for operator retry via POST /imports/{batch_id}/analyze).
    # The import itself committed successfully -- that is what this test
    # verifies (readings survive); only the batch status changes.
    assert client.get("/api/v1/imports/ib-x").json()["status"] == "analysis_failed"
