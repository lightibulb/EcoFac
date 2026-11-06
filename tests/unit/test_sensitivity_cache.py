"""Tests for the sensitivity cache in scenario_analysis.

The sensitivity endpoint runs analyze_sensitivity, which invokes
decide_transparently up to 35 times per HTTP request. Because a scenario
record is append-only, its canonical JSON never changes; the cached
variant keys on that JSON, so repeated requests reuse the result.
"""

import contextlib
import tempfile
from pathlib import Path

from ecofac.decision.limits import (
    ConstraintConfig,
    CoolingLimits,
    ElectricalLimits,
)
from ecofac.decision.scenario_analysis import (
    analyze_sensitivity,
    analyze_sensitivity_cached,
    reset_sensitivity_cache,
    sensitivity_cache_info,
)
from ecofac.decision.scenarios import (
    build_transparent_request,
    evaluate_and_persist_scenario,
    get_scenario,
)
from ecofac.persistence.database import get_connection, init_db


def _make_record() -> dict:
    """Build a real persisted scenario record in a throwaway database."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name

    conn = get_connection(db_path)
    try:
        init_db(conn)
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
            config_version="cache-test",
        )
        scenario_id, _, _ = evaluate_and_persist_scenario(
            conn,
            name="cache-test",
            transparent_request=req,
            persist_as_decision=False,
        )
        record = get_scenario(conn, scenario_id)
    finally:
        conn.close()
        with contextlib.suppress(OSError):
            Path(db_path).unlink()

    assert record is not None, "scenario record was not persisted"
    return record


def test_cache_returns_identical_result_on_second_call() -> None:
    reset_sensitivity_cache()
    record = _make_record()
    a = analyze_sensitivity_cached(record)
    b = analyze_sensitivity_cached(record)
    info = sensitivity_cache_info()
    assert info["hits"] >= 1, f"expected >=1 cache hit, got {info}"
    assert a.model_dump() == b.model_dump()


def test_cache_matches_uncached_output() -> None:
    reset_sensitivity_cache()
    record = _make_record()
    cached = analyze_sensitivity_cached(record)
    uncached = analyze_sensitivity(record)
    assert cached.model_dump() == uncached.model_dump()


def test_cache_miss_on_different_record() -> None:
    reset_sensitivity_cache()
    record_a = _make_record()
    record_b = _make_record()
    assert record_a["id"] != record_b["id"]
    analyze_sensitivity_cached(record_a)
    analyze_sensitivity_cached(record_b)
    info = sensitivity_cache_info()
    assert info["misses"] >= 2, f"expected >=2 misses, got {info}"
