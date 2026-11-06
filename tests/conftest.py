"""Project-wide pytest configuration and cross-cutting guards.

Dual-package guard
------------------
``pyproject.toml`` sets ``pythonpath = ["src", "."]`` so both ``import
ecofac`` and ``import src.ecofac`` resolve at runtime. They are NOT the
same package: importing from ``src.ecofac.X`` loads a second copy of every
module, including every pydantic model. ``transparent.py`` declares its
fields against the ``ecofac.*`` class objects, so pydantic rejects
instances of the ``src.ecofac.*`` copy with a confusing error like
``Input should be a valid dictionary or instance of RichMeasurement`` even
though the input *is* a RichMeasurement.

Rule: always import from ``ecofac.*`` in tests, never ``src.ecofac.*``.
"""

import os
import sys

STRICT = os.environ.get("ECOFAC_STRICT_IMPORTS", "1") != "0"


def _bad_imports() -> list[str]:
    return sorted(m for m in sys.modules if m.startswith("src.ecofac"))


def pytest_collection_finish(session):  # noqa: ARG001
    """Abort the run if any test imported src.ecofac during collection.

    Runs after every test module has been imported but before any test
    executes, so the failure is early, clear, and cheap to diagnose.
    """
    bad = _bad_imports()
    if not bad:
        return
    msg = (
        "dual-package imports detected during collection:\n  "
        + "\n  ".join(bad)
        + "\n\nFix: import from 'ecofac.*', not 'src.ecofac.*'."
    )
    if STRICT:
        import pytest  # noqa: PLC0415

        pytest.exit(msg, returncode=2)
    else:
        print(f"WARN: {msg}")


def pytest_runtest_setup(item):
    """Catch runtime src.ecofac imports that escape the collection check."""
    bad = _bad_imports()
    if not bad:
        return
    msg = (
        "dual-package imports detected before test "
        f"{item.nodeid}: {bad}. Import from 'ecofac.*', not 'src.ecofac.*'."
    )
    if STRICT:
        import pytest  # noqa: PLC0415

        pytest.fail(msg)
    else:
        print(f"WARN: {msg}")
