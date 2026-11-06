"""Virtual Facility +40% test drive must reject."""

from ecofac.decision.testdrive import list_presets, run_test_drive


def _as_upper(value: object) -> str | None:
    if isinstance(value, str):
        return value.upper()
    if value is None:
        return None
    inner = getattr(value, "value", value)
    return str(inner).upper()


def _as_str(value: object) -> str | None:
    if isinstance(value, str):
        return value
    if value is None:
        return None
    inner = getattr(value, "value", value)
    return str(inner)


def _extract_status(result: object) -> str | None:
    if not isinstance(result, dict):
        return None

    for key in ("status", "final_status"):
        value = _as_upper(result.get(key))
        if value is not None:
            return value

    for nested_key in ("decision", "result", "decision_result"):
        nested = result.get(nested_key)
        if isinstance(nested, dict):
            for key in ("status", "final_status"):
                value = _as_upper(nested.get(key))
                if value is not None:
                    return value

    return None


def _extract_precedence(result: object) -> str | None:
    if not isinstance(result, dict):
        return None

    value = _as_str(result.get("precedence"))
    if value is not None:
        return value

    for nested_key in ("decision", "result", "decision_result"):
        nested = result.get(nested_key)
        if isinstance(nested, dict):
            value = _as_str(nested.get("precedence"))
            if value is not None:
                return value

    return None


def test_plus40_preset_exists() -> None:
    ids = {preset["id"] for preset in list_presets()}
    assert "plus_40pct" in ids


def test_plus40_load_is_rejected() -> None:
    result = run_test_drive("plus_40pct")

    assert _extract_status(result) == "REJECT"

    assert _extract_precedence(result) in {
        "hard_limit_violation",
        "reserve_violation",
    }
