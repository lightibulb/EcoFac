"""Tests for the test-drive computation cache."""

import pytest

from ecofac.decision.testdrive import (
    UnknownPresetError,
    reset_test_drive_cache,
    run_test_drive,
    test_drive_cache_info,
)


def _strip_ts(payload: dict) -> dict:
    """Return a copy of the payload with the fresh timestamp removed."""
    return {k: v for k, v in payload.items() if k != "evaluated_at"}


def test_cache_hits_after_first_call() -> None:
    reset_test_drive_cache()
    a = run_test_drive("plus_40pct")
    b = run_test_drive("plus_40pct")
    info = test_drive_cache_info()
    assert info["hits"] >= 1, f"expected >=1 cache hit, got {info}"
    assert _strip_ts(a) == _strip_ts(b)


def test_alias_shares_cache_entry() -> None:
    reset_test_drive_cache()
    a = run_test_drive("plus40")
    b = run_test_drive("plus_40pct")
    info = test_drive_cache_info()
    assert info["hits"] >= 1, f"expected alias to share cache, got {info}"
    assert _strip_ts(a) == _strip_ts(b)


def test_evaluated_at_is_fresh_per_call() -> None:
    reset_test_drive_cache()
    payload = run_test_drive("baseline")
    assert payload["evaluated_at"], "evaluated_at must not be empty"
    assert "T" in payload["evaluated_at"], "evaluated_at must be ISO-8601"


def test_unknown_preset_still_raises() -> None:
    reset_test_drive_cache()
    with pytest.raises(UnknownPresetError):
        run_test_drive("definitely_not_a_real_preset_xyz")
    # Repeat call must also raise — lru_cache does not cache exceptions.
    with pytest.raises(UnknownPresetError):
        run_test_drive("definitely_not_a_real_preset_xyz")
