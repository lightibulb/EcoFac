# P2-7: verify_scenario uses the stored config_version, not a placeholder.
#
# Previously the verify path built a TransparentDecisionRequest with the
# literal "verify-tmp" config_version. It never affected the fingerprints
# or the status comparison (config_version does not feed either), but it
# was confusing to read and harder to audit. This test asserts the verify
# path passes the real stored config_version into the decision engine on
# replay.

import contextlib
import tempfile
from pathlib import Path

import pytest

from ecofac.decision import scenarios as scenarios_module
from ecofac.decision.limits import (
    ConstraintConfig,
    CoolingLimits,
    ElectricalLimits,
)
from ecofac.decision.scenarios import (
    build_transparent_request,
    evaluate_and_persist_scenario,
    verify_scenario,
)
from ecofac.persistence.database import get_connection, init_db


@pytest.fixture
def temp_conn():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    conn = get_connection(db_path)
    init_db(conn)
    try:
        yield conn
    finally:
        conn.close()
        with contextlib.suppress(OSError):
            Path(db_path).unlink()


def _persist(conn, config_version: str) -> str:
    config = ConstraintConfig(
        electrical=ElectricalLimits(capacity=1000.0, reserve_pct=0.1),
        cooling=CoolingLimits(capacity=800.0, reserve_pct=0.1),
    )
    req = build_transparent_request(
        it_load_kw=500.0,
        cooling_load_kw=500.0,
        pue=1.4,
        proposed_it_delta_kw=50.0,
        proposed_cooling_delta_kw=0.0,
        config=config,
        config_version=config_version,
    )
    scenario_id, _, _ = evaluate_and_persist_scenario(
        conn,
        name="verify-config-version-test",
        transparent_request=req,
        persist_as_decision=False,
    )
    return scenario_id


def test_verify_scenario_replays_with_stored_config_version(temp_conn, monkeypatch) -> None:
    scenario_id = _persist(temp_conn, "custom-v1")

    captured: list[str] = []
    real = scenarios_module.decide_transparently

    def spy(request):
        captured.append(request.config_version)
        return real(request)

    monkeypatch.setattr(scenarios_module, "decide_transparently", spy)

    result = verify_scenario(temp_conn, scenario_id)

    assert result is not None
    assert result["verified"] is True
    assert captured, "verify_scenario did not call decide_transparently"
    assert captured[-1] == "custom-v1", f"replay used {captured[-1]!r}, expected 'custom-v1'"
    assert "verify-tmp" not in captured
