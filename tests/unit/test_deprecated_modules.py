"""The deprecated decision modules must warn on import."""

import importlib
import sys
import warnings


def test_engine_emits_deprecation_warning() -> None:
    sys.modules.pop("ecofac.decision.engine", None)
    sys.modules.pop("ecofac.decision.constraints", None)
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        importlib.import_module("ecofac.decision.engine")
    assert any(issubclass(w.category, DeprecationWarning) for w in caught)


def test_constraints_emits_deprecation_warning() -> None:
    sys.modules.pop("ecofac.decision.constraints", None)
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        importlib.import_module("ecofac.decision.constraints")
    assert any(issubclass(w.category, DeprecationWarning) for w in caught)
