# Copyright 2026 Lightibulb
"""Schema validation for the decision API models."""

import pydantic
import pytest

from ecofac.api.schemas import (
    DecisionEvaluateRequest,
    DecisionHistoryItem,
    DecisionRecordResponse,
    DecisionVerifyResponse,
    SupersessionChainResponse,
)
from ecofac.decision.transparent import TransparentDecisionRequest


def _payload(**overrides):
    base = {
        "decision": {
            "config": {
                "electrical": {"capacity": 1000.0, "reserve_pct": 0.10},
                "cooling": {"capacity": 800.0, "reserve_pct": 0.10},
            },
            "config_version": "cfg-1",
            "it_load": {
                "name": "it_load",
                "value": 400.0,
                "unit": "kW",
                "source": "MEASURED",
            },
            "cooling_load": {
                "name": "cooling_load",
                "value": 300.0,
                "unit": "kW",
                "source": "MEASURED",
            },
            "proposed_it_delta": 0.0,
            "proposed_cooling_delta": 0.0,
            "quality_eligible": True,
            "quality_stale": False,
            "quality_flags": [],
            "uncertainty_flags": [],
            "conflicting_inputs": [],
        },
    }
    base.update(overrides)
    return base


def test_decision_evaluate_request_accepts_valid():
    req = DecisionEvaluateRequest.model_validate(_payload())
    assert isinstance(req.decision, TransparentDecisionRequest)
    assert req.supersedes is None
    assert req.supersession_reason is None


def test_decision_evaluate_request_accepts_supersedes():
    req = DecisionEvaluateRequest.model_validate(
        _payload(supersedes="abc-123", supersession_reason="fixing")
    )
    assert req.supersedes == "abc-123"
    assert req.supersession_reason == "fixing"


def test_decision_evaluate_request_rejects_empty():
    with pytest.raises(pydantic.ValidationError):
        DecisionEvaluateRequest.model_validate({})


def test_decision_evaluate_request_rejects_negative_capacity():
    bad = _payload()
    bad["decision"]["config"]["electrical"]["capacity"] = -1.0
    with pytest.raises(pydantic.ValidationError):
        DecisionEvaluateRequest.model_validate(bad)


def test_decision_evaluate_request_rejects_reserve_at_one():
    bad = _payload()
    bad["decision"]["config"]["electrical"]["reserve_pct"] = 1.0
    with pytest.raises(pydantic.ValidationError):
        DecisionEvaluateRequest.model_validate(bad)


def test_decision_evaluate_request_rejects_oversized_reason():
    bad = _payload(supersedes="x", supersession_reason="a" * 2000)
    with pytest.raises(pydantic.ValidationError):
        DecisionEvaluateRequest.model_validate(bad)


def test_decision_history_item_ignores_extra_fields():
    item = DecisionHistoryItem(
        id="d1",
        status="APPROVE",
        precedence="clean",
        engine_version="1.0",
        config_version="cfg-1",
        decision_timestamp="2026-01-01T00:00:00Z",
        created_at="2026-01-01T00:00:01Z",
        supersedes=None,
        supersession_reason=None,
        request_json="{}",
        result_json="{}",
    )
    assert item.id == "d1"


def test_decision_record_response_includes_payloads():
    record = DecisionRecordResponse(
        id="d1",
        status="REJECT",
        precedence="reserve_violation",
        engine_version="1.0",
        config_version="cfg-1",
        decision_timestamp="2026-01-01T00:00:00Z",
        created_at="2026-01-01T00:00:01Z",
        supersedes=None,
        supersession_reason=None,
        input_fingerprint="abc",
        config_fingerprint="def",
        request={"it_load": {"value": 400.0}},
        result={"status": "REJECT"},
    )
    assert record.request["it_load"]["value"] == 400.0
    assert record.result["status"] == "REJECT"


def test_decision_verify_response_shape():
    resp = DecisionVerifyResponse(
        verified=True,
        stored_input_fingerprint="a",
        computed_input_fingerprint="a",
        stored_config_fingerprint="b",
        computed_config_fingerprint="b",
    )
    assert resp.verified is True


def test_supersession_chain_response_shape():
    resp = SupersessionChainResponse(decision_id="d1", chain=[])
    assert resp.chain == []
