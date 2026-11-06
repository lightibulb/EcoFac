# Copyright 2026 Lightibulb

"""Reliability tests for CSV ingestion: malformed input, identifiers, duplicates, atomicity."""

import csv
import sqlite3

import pytest

from ecofac.ingestion import entities, pipeline
from ecofac.ingestion.pipeline import IngestionError, ingest_csv
from ecofac.persistence.database import get_connection, init_db

HEADER = "timestamp,building_id,meter_id,energy_value,unit\n"
NOW = "2026-01-01T00:00:00Z"


@pytest.fixture
def conn():
    """In-memory DB with one org/building/meter, one source and one batch."""
    c = get_connection(":memory:")
    init_db(c)
    c.execute("INSERT INTO organizations (id, name, created_at) VALUES ('o1', 'Org', ?)", (NOW,))
    c.execute(
        "INSERT INTO buildings (id, org_id, name, created_at) VALUES ('b1', 'o1', 'Bldg', ?)",
        (NOW,),
    )
    c.execute(
        "INSERT INTO meters (id, building_id, fuel_type, unit, created_at) "
        "VALUES ('m1', 'b1', 'electricity', 'kWh', ?)",
        (NOW,),
    )
    c.execute(
        "INSERT INTO data_sources (id, name, source_type, created_at) VALUES ('s1','S','csv',?)",
        (NOW,),
    )
    c.execute(
        "INSERT INTO import_batches (id, source_id, status, imported_at) VALUES ('ib1','s1','started',?)",
        (NOW,),
    )
    c.execute(
        "INSERT INTO import_batches (id, source_id, status, imported_at) VALUES ('ib2','s1','started',?)",
        (NOW,),
    )
    yield c
    c.close()


def _count(conn, table):
    return conn.execute(f"SELECT count(*) AS c FROM {table}").fetchone()["c"]


def _row(ts="2026-01-01T00:00:00Z", building="b1", meter="m1", value="1.0", unit="kWh"):
    return f"{ts},{building},{meter},{value},{unit}\n"


# ---------------------------------------------------------------- malformed input


def test_ingestion_error_is_still_a_value_error(conn):
    with pytest.raises(ValueError, match="Missing required columns") as exc:
        ingest_csv(conn, "a,b\n1,2\n", "s1", "ib1")
    assert isinstance(exc.value, IngestionError)
    assert exc.value.code == "missing_columns"
    assert exc.value.details["found"] == ["a", "b"]


def test_timestamp_outside_supported_range_is_a_rejected_row(conn):
    # The standard library raises OverflowError (not ValueError) for these.
    text = HEADER + _row(ts="0001-01-01T00:00:00+05:00") + _row(ts="9999-12-31T23:59:59-05:00")
    summary = ingest_csv(conn, text, "s1", "ib1")
    assert summary.total_rows == 2
    assert summary.accepted_rows == 0
    assert summary.invalid_timestamps == 2
    assert _count(conn, "energy_readings") == 0


def test_oversized_field_is_a_structured_error_and_stores_nothing(conn):
    huge = "9" * (csv.field_size_limit() + 10)
    text = HEADER + _row(ts="2026-01-01T00:00:00Z") + _row(ts="2026-01-01T01:00:00Z", value=huge)
    with pytest.raises(IngestionError) as exc:
        ingest_csv(conn, text, "s1", "ib1")
    assert exc.value.code == "malformed_csv"
    assert exc.value.details["line"] >= 1
    # The valid first row must not survive the failed import.
    assert _count(conn, "energy_readings") == 0
    assert _count(conn, "data_quality_issues") == 0
    assert not conn.in_transaction


def test_row_limit_error_is_coded_and_rolls_back(conn):
    text = HEADER + _row(ts="2026-01-01T00:00:00Z") + _row(ts="2026-01-01T01:00:00Z")
    with pytest.raises(IngestionError) as exc:
        ingest_csv(conn, text, "s1", "ib1", max_rows=1)
    assert exc.value.code == "row_limit_exceeded"
    assert exc.value.details == {"max_rows": 1}
    assert _count(conn, "energy_readings") == 0


def test_file_size_error_is_coded(conn):
    with pytest.raises(IngestionError) as exc:
        ingest_csv(conn, HEADER + _row(), "s1", "ib1", max_size_mb=0.00001)
    assert exc.value.code == "file_too_large"


# ---------------------------------------------------------------- transactions


def test_ingest_inside_outer_transaction_never_commits_by_itself(conn):
    conn.execute("BEGIN IMMEDIATE")
    summary = ingest_csv(conn, HEADER + _row(), "s1", "ib1")
    assert summary.accepted_rows == 1
    conn.execute("ROLLBACK")
    assert _count(conn, "energy_readings") == 0


def test_failed_ingest_inside_outer_transaction_keeps_outer_work(conn):
    conn.execute("BEGIN IMMEDIATE")
    conn.execute(
        "INSERT INTO data_sources (id, name, source_type, created_at) VALUES ('mark','M','csv',?)",
        (NOW,),
    )
    text = HEADER + _row(ts="2026-01-01T00:00:00Z") + _row(ts="2026-01-01T01:00:00Z")
    with pytest.raises(IngestionError):
        ingest_csv(conn, text, "s1", "ib1", max_rows=1)
    conn.execute("COMMIT")
    assert _count(conn, "energy_readings") == 0
    assert conn.execute("SELECT 1 FROM data_sources WHERE id = 'mark'").fetchone() is not None


def test_database_failure_after_entity_creation_rolls_entities_back(conn, monkeypatch):
    def boom(*_args, **_kwargs):
        raise sqlite3.OperationalError("disk I/O error")

    monkeypatch.setattr(pipeline, "_existing_pairs", boom)
    text = HEADER + _row(building="b-new", meter="m-new")
    with pytest.raises(sqlite3.OperationalError):
        ingest_csv(conn, text, "s1", "ib1", auto_create_entities=True)
    assert _count(conn, "meters") == 1
    assert _count(conn, "buildings") == 1
    assert conn.execute("SELECT 1 FROM organizations WHERE id = 'o-auto'").fetchone() is None
    assert not conn.in_transaction


# ---------------------------------------------------------------- entities


def test_auto_create_makes_meter_and_building_for_a_storable_row(conn):
    text = HEADER + _row(building="b-new", meter="m-new")
    summary = ingest_csv(conn, text, "s1", "ib1", auto_create_entities=True)
    assert (summary.accepted_rows, summary.buildings_created, summary.meters_created) == (1, 1, 1)
    building = conn.execute("SELECT org_id FROM buildings WHERE id = 'b-new'").fetchone()
    assert building["org_id"] == "o-auto"
    assert (
        conn.execute("SELECT building_id FROM meters WHERE id = 'm-new'").fetchone()[0] == "b-new"
    )


def test_default_library_behaviour_still_rejects_unknown_meters(conn):
    summary = ingest_csv(conn, HEADER + _row(meter="m-new"), "s1", "ib1")
    assert summary.unknown_meter_ids == 1
    assert summary.accepted_rows == 0
    assert _count(conn, "meters") == 1


def test_new_meter_under_existing_building_does_not_create_a_building(conn):
    summary = ingest_csv(conn, HEADER + _row(meter="m2"), "s1", "ib1", auto_create_entities=True)
    assert (summary.buildings_created, summary.meters_created) == (0, 1)
    assert _count(conn, "buildings") == 1


def test_rows_rejected_before_storage_create_no_entities(conn):
    text = (
        HEADER
        + _row(ts="not-a-date", building="b-x", meter="m-x")
        + _row(unit="BTU", building="b-x", meter="m-x")
        + _row(value="abc", building="b-x", meter="m-x")
        + _row(value="nan", building="b-x", meter="m-x")
    )
    summary = ingest_csv(conn, text, "s1", "ib1", auto_create_entities=True)
    assert summary.accepted_rows == 0
    assert summary.rejected_rows == 4
    assert (summary.buildings_created, summary.meters_created) == (0, 0)
    assert _count(conn, "meters") == 1
    assert _count(conn, "buildings") == 1
    assert conn.execute("SELECT 1 FROM organizations WHERE id = 'o-auto'").fetchone() is None


@pytest.mark.parametrize(
    ("building", "meter"),
    [
        ("bad id!", "m-x"),
        ("b-x", "bad id!"),
        ("b-x", "semi;colon"),
        ("b-x", "x" * 101),
        ("x" * 101, "m-x"),
        ("b-x", ""),
    ],
)
def test_invalid_identifiers_are_rejected_and_never_created(conn, building, meter):
    summary = ingest_csv(
        conn, HEADER + _row(building=building, meter=meter), "s1", "ib1", auto_create_entities=True
    )
    assert summary.invalid_ids == 1
    assert summary.rejected_rows == 1
    assert summary.accepted_rows == 0
    assert _count(conn, "meters") == 1
    assert _count(conn, "buildings") == 1
    issue = conn.execute("SELECT issue_type FROM data_quality_issues").fetchone()
    assert issue["issue_type"] == "invalid_identifier"


def test_existing_meter_with_wrong_building_is_a_mismatch_and_creates_nothing(conn):
    summary = ingest_csv(
        conn, HEADER + _row(building="b-other", meter="m1"), "s1", "ib1", auto_create_entities=True
    )
    assert summary.rejected_rows == 1
    assert summary.accepted_rows == 0
    assert _count(conn, "buildings") == 1
    issue = conn.execute("SELECT issue_type FROM data_quality_issues").fetchone()
    assert issue["issue_type"] == "building_mismatch"


def test_new_meter_cannot_be_split_across_two_buildings_in_one_file(conn):
    text = (
        HEADER
        + _row(ts="2026-01-01T00:00:00Z", building="b-a", meter="m-x")
        + _row(ts="2026-01-01T01:00:00Z", building="b-b", meter="m-x")
    )
    summary = ingest_csv(conn, text, "s1", "ib1", auto_create_entities=True)
    assert summary.accepted_rows == 1
    assert summary.rejected_rows == 1
    assert conn.execute("SELECT 1 FROM buildings WHERE id = 'b-b'").fetchone() is None


def test_entity_limit_rolls_back_everything(conn, monkeypatch):
    monkeypatch.setattr(entities, "MAX_AUTO_ENTITIES", 2)
    text = HEADER + "".join(
        _row(ts=f"2026-01-01T0{i}:00:00Z", building=f"b-n{i}", meter=f"m-n{i}") for i in range(3)
    )
    with pytest.raises(IngestionError) as exc:
        ingest_csv(conn, text, "s1", "ib1", auto_create_entities=True)
    assert exc.value.code == "entity_limit_exceeded"
    assert _count(conn, "meters") == 1
    assert _count(conn, "buildings") == 1
    assert _count(conn, "energy_readings") == 0


# ---------------------------------------------------------------- duplicates


def test_duplicate_rows_in_file_and_in_database_are_counted_once_each(conn):
    first = HEADER + _row(ts="2026-01-01T00:00:00Z") + _row(ts="2026-01-01T00:00:00Z", value="9")
    one = ingest_csv(conn, first, "s1", "ib1", auto_create_entities=True)
    assert (one.accepted_rows, one.duplicate_rows) == (1, 1)

    again = HEADER + _row(ts="2026-01-01T00:00:00Z")
    two = ingest_csv(conn, again, "s1", "ib2", auto_create_entities=True)
    assert (two.accepted_rows, two.duplicate_rows) == (0, 1)
    assert _count(conn, "energy_readings") == 1
    value = conn.execute("SELECT value FROM energy_readings").fetchone()["value"]
    assert value == 1.0  # the first value wins; a later duplicate never overwrites it


def test_identifier_validation_is_one_definition():
    # Form fields use ID_PATTERN; CSV rows use invalid_identifier_reason.
    assert entities.invalid_identifier_reason("b1", "m1") is None
    assert entities.invalid_identifier_reason("b.1:a-b_c", "M-1") is None
    assert entities.invalid_identifier_reason("b1", "m 1") is not None
    assert rf"^{entities.ID_CHARS}+$" == entities.ID_PATTERN
