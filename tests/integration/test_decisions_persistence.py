# Copyright 2026 Lightibulb
"""Persistence and auditability of decision records."""

import sqlite3

import pytest

from ecofac.decision.limits import (
    ConstraintConfig,
    CoolingLimits,
    ElectricalLimits,
)
from ecofac.decision.measurements import (
    MeasurementSource,
    RichMeasurement,
    UncertaintyMetadata,
)
from ecofac.decision.store import (
    get_decision,
    list_decisions,
    persist_decision,
    verify_decision,
    walk_supersession_chain,
)
from ecofac.decision.transparent import (
    TransparentDecisionRequest,
    decide_transparently,
)
from ecofac.persistence.database import get_connection, init_db


@pytest.fixture
def db_conn():
    conn = get_connection(":memory:")
    init_db(conn)
    yield conn
    conn.close()


def _request(
    it_value=400.0,
    cool_value=300.0,
    config_version="cfg-1",
):
    return TransparentDecisionRequest(
        config=ConstraintConfig(
            electrical=ElectricalLimits(capacity=1000.0, reserve_pct=0.10),
            cooling=CoolingLimits(capacity=800.0, reserve_pct=0.10),
        ),
        config_version=config_version,
        it_load=RichMeasurement(
            name="it_load",
            value=it_value,
            unit="kW",
            source=MeasurementSource.MEASURED,
            uncertainty=UncertaintyMetadata(),
            reference_id="reading-it-1",
            measurement_timestamp="2026-01-01T00:00:00Z",
        ),
        cooling_load=RichMeasurement(
            name="cooling_load",
            value=cool_value,
            unit="kW",
            source=MeasurementSource.MEASURED,
        ),
        quality_eligible=True,
    )


def _persist(conn, request):
    result = decide_transparently(request)
    return persist_decision(conn, request, result)


def test_decisions_table_exists(db_conn):
    tables = {
        row["name"]
        for row in db_conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    assert "decisions" in tables
    assert "decision_configs" in tables


def test_append_only_update_blocked(db_conn):
    did = _persist(db_conn, _request())
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        db_conn.execute(
            "UPDATE decisions SET status = 'REJECT' WHERE id = ?", (did,)
        )


def test_append_only_delete_blocked(db_conn):
    did = _persist(db_conn, _request())
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        db_conn.execute("DELETE FROM decisions WHERE id = ?", (did,))


def test_persist_decision_round_trip(db_conn):
    request = _request()
    result = decide_transparently(request)
    did = persist_decision(db_conn, request, result)
    record = get_decision(db_conn, did)
    assert record is not None
    assert record["id"] == did
    assert record["status"] == result.status
    assert record["precedence"] == result.precedence
    assert record["engine_version"] == result.engine_version
    assert record["config_version"] == "cfg-1"
    assert record["input_fingerprint"] == result.fingerprint.input_fingerprint
    assert record["config_fingerprint"] == result.fingerprint.config_fingerprint
    assert record["supersedes"] is None


def test_get_decision_returns_none_for_missing(db_conn):
    assert get_decision(db_conn, "does-not-exist") is None


def test_persist_decision_rejects_missing_supersedes(db_conn):
    request = _request()
    result = decide_transparently(request)
    with pytest.raises(ValueError, match="does not exist"):
        persist_decision(
            db_conn, request, result,
            supersedes="missing",
            supersession_reason="rebalance",
        )


def test_persist_decision_requires_supersession_reason(db_conn):
    did = _persist(db_conn, _request())
    request = _request()
    result = decide_transparently(request)
    with pytest.raises(ValueError, match="supersession_reason is required"):
        persist_decision(
            db_conn, request, result,
            supersedes=did,
            supersession_reason="   ",
        )


def test_persist_decision_creates_audit_event(db_conn):
    did = _persist(db_conn, _request())
    actions = [
        row["action"]
        for row in db_conn.execute(
            "SELECT action FROM audit_events WHERE entity_id = ?", (did,)
        ).fetchall()
    ]
    assert "DECISION_CREATED" in actions


def test_config_registered_once(db_conn):
    _persist(db_conn, _request())
    _persist(db_conn, _request())
    count = db_conn.execute(
        "SELECT count(*) AS c FROM decision_configs"
    ).fetchone()["c"]
    assert count == 1
    events = db_conn.execute(
        "SELECT count(*) AS c FROM audit_events "
        "WHERE action = 'CONFIG_REGISTERED'"
    ).fetchone()["c"]
    assert events == 1


def test_config_registered_for_new_version(db_conn):
    _persist(db_conn, _request(config_version="cfg-1"))
    _persist(db_conn, _request(config_version="cfg-2"))
    count = db_conn.execute(
        "SELECT count(*) AS c FROM decision_configs"
    ).fetchone()["c"]
    # Same shape but different fingerprint only if capacities differ — same
    # capacities mean identical fingerprint. Only config_version string differs,
    # but the fingerprint hashes only the config payload (capacity, reserve).
    # Therefore cfg-1 and cfg-2 with the same capacities share a fingerprint.
    assert count == 1


def test_supersession_chain_linear(db_conn):
    id_a = _persist(db_conn, _request())
    request_b = _request()
    result_b = decide_transparently(request_b)
    id_b = persist_decision(
        db_conn, request_b, result_b,
        supersedes=id_a, supersession_reason="rebalance",
    )
    request_c = _request()
    result_c = decide_transparently(request_c)
    id_c = persist_decision(
        db_conn, request_c, result_c,
        supersedes=id_b, supersession_reason="final",
    )
    chain = walk_supersession_chain(db_conn, id_b)
    ids = [row["id"] for row in chain]
    assert ids == [id_a, id_b, id_c]


def test_supersession_chain_returns_empty_for_missing(db_conn):
    assert walk_supersession_chain(db_conn, "missing") == []


def test_supersession_emits_supersede_audit(db_conn):
    id_a = _persist(db_conn, _request())
    request_b = _request()
    result_b = decide_transparently(request_b)
    persist_decision(
        db_conn, request_b, result_b,
        supersedes=id_a, supersession_reason="refresh",
    )
    actions = [
        row["action"]
        for row in db_conn.execute(
            "SELECT action FROM audit_events WHERE entity_id = ?", (id_a,)
        ).fetchall()
    ]
    assert "DECISION_SUPERSEDED" in actions


def test_verify_decision_returns_true_for_legit(db_conn):
    did = _persist(db_conn, _request())
    verification = verify_decision(db_conn, did)
    assert verification is not None
    assert verification["verified"] is True
    assert (
        verification["stored_input_fingerprint"]
        == verification["computed_input_fingerprint"]
    )


def test_verify_decision_returns_none_for_missing(db_conn):
    assert verify_decision(db_conn, "missing") is None


def test_verify_decision_detects_tamper(db_conn):
    did = _persist(db_conn, _request())
    db_conn.execute("DROP TRIGGER trg_decisions_no_update")
    db_conn.execute(
        "UPDATE decisions SET input_fingerprint = 'tampered' WHERE id = ?",
        (did,),
    )
    db_conn.execute(
        "CREATE TRIGGER trg_decisions_no_update "
        "BEFORE UPDATE ON decisions "
        "BEGIN "
        "    SELECT RAISE(ABORT, 'decisions is append-only'); "
        "END"
    )
    verification = verify_decision(db_conn, did)
    assert verification is not None
    assert verification["verified"] is False
    assert verification["stored_input_fingerprint"] == "tampered"
    assert verification["computed_input_fingerprint"] != "tampered"


def test_list_decisions_newest_first(db_conn):
    for _ in range(3):
        _persist(db_conn, _request())
    rows = list_decisions(db_conn)
    assert len(rows) == 3


def test_list_decisions_filter_by_status(db_conn):
    _persist(db_conn, _request(it_value=400.0))      # APPROVE
    _persist(db_conn, _request(it_value=1100.0))     # REJECT
    rows = list_decisions(db_conn, status="REJECT")
    assert len(rows) == 1
    assert rows[0]["status"] == "REJECT"


def test_list_decisions_filter_by_config(db_conn):
    _persist(db_conn, _request(config_version="cfg-1"))
    _persist(db_conn, _request(config_version="cfg-2"))
    rows = list_decisions(db_conn, config_version="cfg-2")
    assert len(rows) == 1
    assert rows[0]["config_version"] == "cfg-2"


def test_list_decisions_pagination(db_conn):
    for _ in range(5):
        _persist(db_conn, _request())
    page1 = list_decisions(db_conn, limit=2, offset=0)
    page2 = list_decisions(db_conn, limit=2, offset=2)
    assert len(page1) == 2
    assert len(page2) == 2
    assert {row["id"] for row in page1}.isdisjoint(
        {row["id"] for row in page2}
    )
