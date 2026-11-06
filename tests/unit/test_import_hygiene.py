"""P1-0: dual-package import guard.

Asserts that no test imports models from ``src.ecofac.*``. Doing so loads
a second copy of each pydantic model, and pydantic compares classes by
identity, so ``transparent.py`` would reject valid instances of the
"wrong copy" with a confusing validation error.

The conftest hooks in ``tests/conftest.py`` make this a hard failure
during collection and before every test. This test is a redundant check
that documents the invariant.
"""

import sys


def test_no_dual_package_imports() -> None:
    bad = sorted(m for m in sys.modules if m.startswith("src.ecofac"))
    msg = (
        "dual-package imports detected: "
        f"{bad}. "
        "Fix: import from 'ecofac.*', not 'src.ecofac.*'. "
        "The conftest hooks enforce this at collection time; see "
        "tests/conftest.py."
    )
    assert not bad, msg


def test_ecofac_is_importable_from_canonical_path() -> None:
    # Sanity check: the canonical import path resolves and exposes the
    # modules the tests rely on. If this fails, pythonpath is misconfigured.
    import ecofac.decision.limits  # noqa: PLC0415
    import ecofac.decision.state_machine  # noqa: PLC0415
    import ecofac.decision.transparent  # noqa: F401, PLC0415
