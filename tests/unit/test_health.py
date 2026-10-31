# Copyright 2026 Lightibulb
"""Minimal health-check tests for CI bootstrapping."""

from ecofac import __version__


def test_package_imports() -> None:
    """The package must import and expose a non-empty version."""
    assert isinstance(__version__, str)
    assert __version__ != ""


def test_version_is_dotted_numeric() -> None:
    """Version must be at least two dotted numeric parts."""
    parts = __version__.split(".")
    assert len(parts) >= 2
    assert all(part.isdigit() for part in parts)
