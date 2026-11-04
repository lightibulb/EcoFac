# Copyright 2026 Lightibulb
"""Regression coverage for the NaN-safe validation error handler."""

import json

from ecofac.main import _sanitize_json


def test_sanitize_replaces_nan():
    assert _sanitize_json(float("nan")) == "nan"


def test_sanitize_replaces_positive_infinity():
    assert _sanitize_json(float("inf")) == "inf"


def test_sanitize_replaces_negative_infinity():
    assert _sanitize_json(float("-inf")) == "-inf"


def test_sanitize_preserves_finite_floats():
    assert _sanitize_json(1.5) == 1.5
    assert _sanitize_json(0.0) == 0.0
    assert _sanitize_json(-2.25) == -2.25


def test_sanitize_recurses_into_dicts_and_lists():
    payload = {
        "detail": [
            {
                "loc": ["body", "pue"],
                "input": float("nan"),
                "ctx": {"gt": 0.0},
            },
            {
                "loc": ["body", "it_load_kw"],
                "input": float("inf"),
            },
        ],
    }
    sanitized = _sanitize_json(payload)
    assert sanitized["detail"][0]["input"] == "nan"
    assert sanitized["detail"][1]["input"] == "inf"
    # Finite values pass through unchanged
    assert sanitized["detail"][0]["ctx"]["gt"] == 0.0


def test_sanitize_result_is_json_serialisable():
    """The whole point: json.dumps must succeed after sanitisation."""
    payload = {
        "detail": [
            {"input": float("nan")},
            {"input": float("inf")},
            {"input": float("-inf")},
            {"input": 1.0},
        ],
    }
    sanitized = _sanitize_json(payload)
    # allow_nan=False is what JSONResponse uses internally
    out = json.dumps(sanitized, allow_nan=False)
    assert "nan" in out
    assert "inf" in out


def test_sanitize_leaves_strings_and_ints_alone():
    assert _sanitize_json("hello") == "hello"
    assert _sanitize_json(42) == 42
    assert _sanitize_json(obj=True) is True
    assert _sanitize_json(None) is None
