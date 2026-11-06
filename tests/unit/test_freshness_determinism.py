# Copyright 2026 Lightibulb
"""Freshness is judged at a recorded evaluation time and replays deterministically."""

import json

import pytest

from ecofac.decision import transparent
from ecofac.decision.limits import ConstraintConfig, CoolingLimits, ElectricalLimits
from ecofac.decision.measurements import MeasurementSource, RichMeasurement
from ecofac.decision.store import persist_decision, verify_decision
from ecofac.decision.transparent import (
    TransparentDecisionRequest,
    build_input_payload,
    decide_transparently,
)
from ecofac.persistence.database import get_connection, init_db

EVAL = "2026-03-01T12:00:00Z"


def m(name, value, ts):
    return RichMeasurement(
        name=name,
        value=value,
        unit="kW",
        source=MeasurementSource.MEASURED,
        measurement_timestamp=ts,
    )


def request(it_ts, cool_ts=None, **kw):
    base = {
        "config": ConstraintConfig(
            electrical=ElectricalLimits(capacity=1000.0, reserve_pct=0.1),
            cooling=CoolingLimits(capacity=800.0, reserve_pct=0.1),
        ),
        "config_version": "cfg-1",
        "it_load": m("it_load", 400.0, it_ts),
        "cooling_load": m("cooling_load", 300.0, cool_ts or it_ts),
        "quality_eligible": True,
        "max_measurement_age_seconds": 3600,
        "evaluation_time": EVAL,
    }
    base.update(kw)
    return TransparentDecisionRequest(**base)


@pytest.fixture
def conn():
    c = get_connection(":memory:")
    init_db(c)
    yield c
    c.close()


def test_fresh_reading_is_approved_and_evaluation_time_recorded():
    result = decide_transparently(request("2026-03-01T11:30:00Z"))
    assert result.status == "APPROVE"
    assert result.evaluation_time == EVAL
    assert result.evidence.calculation_inputs["evaluation_time"] == EVAL


def test_stale_reading_is_review():
    result = decide_transparently(request("2026-03-01T09:00:00Z"))
    assert result.status == "REVIEW"
    assert set(result.uncertainty.stale_measurements) == {"it_load", "cooling_load"}


def test_future_timestamp_is_flagged_and_blocks_approval():
    result = decide_transparently(request("2026-03-01T13:00:00Z"))
    assert result.status == "REVIEW"
    assert "future_timestamp:it_load" in result.uncertainty.quality_flags
    assert result.evidence.calculation_inputs["future_timestamp_measurements"]


def test_small_clock_skew_is_tolerated():
    result = decide_transparently(request("2026-03-01T12:01:00Z"))
    assert result.status == "APPROVE"


def test_naive_timestamp_is_treated_as_invalid_not_local_time():
    result = decide_transparently(request("2026-03-01T11:30:00"))
    assert result.status == "REVIEW"


def test_caller_stale_flag_cannot_hide_real_staleness():
    result = decide_transparently(request("2026-03-01T09:00:00Z", quality_stale=False))
    assert result.status == "REVIEW"


def test_caller_can_only_add_staleness_never_remove_it():
    fresh_but_flagged = decide_transparently(request("2026-03-01T11:30:00Z", quality_stale=True))
    assert fresh_but_flagged.status == "REVIEW"


def test_old_decision_is_fresh_at_its_own_evaluation_time():
    """Same timestamps, judged at their own time, are valid regardless of 'now'."""
    result = decide_transparently(
        request("2024-06-01T11:30:00Z", evaluation_time="2024-06-01T12:00:00Z")
    )
    assert result.status == "APPROVE"


def test_replay_is_unaffected_by_wall_clock_advancing(conn, monkeypatch):
    monkeypatch.setattr(transparent, "now_z", lambda: EVAL)
    req = request("2026-03-01T11:30:00Z", evaluation_time=None)
    result = decide_transparently(req)
    assert result.status == "APPROVE"
    decision_id = persist_decision(conn, req, result)

    stored = json.loads(
        conn.execute("SELECT request_json FROM decisions WHERE id=?", (decision_id,)).fetchone()[0]
    )
    assert stored["evaluation_time"] == EVAL  # pinned at persistence

    monkeypatch.setattr(transparent, "now_z", lambda: "2027-01-01T00:00:00Z")
    verification = verify_decision(conn, decision_id)
    assert verification["verified"] is True
    assert verification["recomputed_status"] == "APPROVE"
    assert verification["evaluation_time"] == EVAL


def test_equal_requests_with_equal_evaluation_time_have_equal_fingerprints():
    a = decide_transparently(request("2026-03-01T11:30:00Z"))
    b = decide_transparently(request("2026-03-01T11:30:00Z"))
    assert a.fingerprint.input_fingerprint == b.fingerprint.input_fingerprint


def test_payload_omits_new_keys_when_unset_so_legacy_fingerprints_survive():
    legacy_style = request(None, None, evaluation_time=None, max_measurement_age_seconds=None)
    payload = build_input_payload(legacy_style)
    assert "evaluation_time" not in payload
    assert "inputs_resolved_by_server" not in payload
    assert "trust_caveats" not in payload
    assert "raw_value" not in payload["it_load"]
