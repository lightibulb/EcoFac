#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

# ---------------------------------------------------------------------------
# Fix 1: F401 — remove unused ConstraintSeverity import from transparent.py
# ---------------------------------------------------------------------------
echo "==> Fixing F401 in transparent.py..."
python3 << 'PYEOF'
import pathlib

p = pathlib.Path("src/ecofac/decision/transparent.py")
text = p.read_text()

old = "from ecofac.decision.limits import ConstraintConfig, ConstraintSeverity\n"
new = "from ecofac.decision.limits import ConstraintConfig\n"

if old in text:
    text = text.replace(old, new, 1)
    p.write_text(text)
    print("  removed unused ConstraintSeverity import")
elif new in text:
    print("  already clean (no change needed)")
else:
    raise SystemExit("ERROR: expected import line not found")
PYEOF

# ---------------------------------------------------------------------------
# Fix 2: ISC004 — parenthesize implicit string concat in MODEL_LIMITATIONS
# ---------------------------------------------------------------------------
echo "==> Fixing ISC004 in uncertainty.py..."
python3 << 'PYEOF'
import pathlib

p = pathlib.Path("src/ecofac/decision/uncertainty.py")
text = p.read_text()

old = '''MODEL_LIMITATIONS: list[str] = [
    "IT electrical load is not treated as facility power directly; "
    "facility power = IT load * PUE.",
    "PUE provenance may be MEASURED, CONFIGURED, or ASSUMED; results depend on it.",
'''
new = '''MODEL_LIMITATIONS: list[str] = [
    (
        "IT electrical load is not treated as facility power directly; "
        "facility power = IT load * PUE."
    ),
    "PUE provenance may be MEASURED, CONFIGURED, or ASSUMED; results depend on it.",
'''

if old in text:
    text = text.replace(old, new, 1)
    p.write_text(text)
    print("  wrapped implicit string concat in parens")
elif new in text:
    print("  already clean (no change needed)")
else:
    raise SystemExit("ERROR: MODEL_LIMITATIONS block not found")
PYEOF

# ---------------------------------------------------------------------------
# Fix 3: PT017 — rewrite test_pue_wrong_unit_rejected with pytest.raises
# (also removes dead code from the previous version)
# ---------------------------------------------------------------------------
echo "==> Fixing PT017 in test_pue_integration.py..."
python3 << 'PYEOF'
import pathlib

p = pathlib.Path("tests/unit/test_pue_integration.py")
text = p.read_text()

# 3a. Ensure pytest is imported
if "\nimport pytest\n" not in text:
    if "import pytest" not in text:
        # Insert after the first block of imports (after measurements import)
        anchor = "from ecofac.decision.measurements import (\n    MeasurementSource,\n    RichMeasurement,\n    UncertaintyMetadata,\n)\n"
        if anchor in text:
            text = text.replace(anchor, anchor + "\nimport pytest\n", 1)
        else:
            # Fallback: prepend
            text = "import pytest\n\n" + text

# 3b. Replace the whole test_pue_wrong_unit_rejected function
old_fn = '''def test_pue_wrong_unit_rejected():
    bad_pue = RichMeasurement(
        name="pue", value=1.4, unit="kW", source=MeasurementSource.MEASURED
    )
    result = decide_transparently.__wrapped__ if hasattr(
        decide_transparently, "__wrapped__"
    ) else None
    _ = result
    try:
        decide_transparently(
            TransparentDecisionRequest(
                config=_config(cap=1000.0),
                config_version="cfg-1",
                it_load=RichMeasurement(
                    name="it_load", value=400.0, unit="kW",
                    source=MeasurementSource.MEASURED,
                ),
                cooling_load=RichMeasurement(
                    name="cooling_load", value=100.0, unit="kW",
                    source=MeasurementSource.MEASURED,
                ),
                pue=bad_pue,
                quality_eligible=True,
            )
        )
        raise AssertionError("Expected ValueError for wrong PUE unit")
    except ValueError as e:
        assert "dimensionless" in str(e)
'''

new_fn = '''def test_pue_wrong_unit_rejected():
    bad_pue = RichMeasurement(
        name="pue", value=1.4, unit="kW", source=MeasurementSource.MEASURED
    )
    request = TransparentDecisionRequest(
        config=_config(cap=1000.0),
        config_version="cfg-1",
        it_load=RichMeasurement(
            name="it_load", value=400.0, unit="kW",
            source=MeasurementSource.MEASURED,
        ),
        cooling_load=RichMeasurement(
            name="cooling_load", value=100.0, unit="kW",
            source=MeasurementSource.MEASURED,
        ),
        pue=bad_pue,
        quality_eligible=True,
    )
    with pytest.raises(ValueError, match="dimensionless"):
        decide_transparently(request)
'''

if old_fn in text:
    text = text.replace(old_fn, new_fn, 1)
    p.write_text(text)
    print("  test rewritten with pytest.raises")
elif new_fn in text:
    print("  already clean (no change needed)")
else:
    raise SystemExit("ERROR: test_pue_wrong_unit_rejected not found in expected form")
PYEOF

# ---------------------------------------------------------------------------
# Verify
# ---------------------------------------------------------------------------
echo ""
echo "==> Ruff (full repo)..."
ruff check src tests simulator evaluation scripts

echo ""
echo "==> Targeted new-test sanity (stdlib, no pytest needed)..."
PYTHONPATH="src:." python3 << 'PYEOF'
# Confirm the three fixes did not break runtime behaviour.
from ecofac.decision.limits import (
    ConstraintConfig,
    CoolingLimits,
    ElectricalLimits,
)
from ecofac.decision.measurements import (
    MeasurementSource,
    RichMeasurement,
)
from ecofac.decision.transparent import (
    TransparentDecisionRequest,
    decide_transparently,
)
from ecofac.decision.uncertainty import MODEL_LIMITATIONS

CFG = ConstraintConfig(
    electrical=ElectricalLimits(capacity=1000.0, reserve_pct=0.10),
    cooling=CoolingLimits(capacity=800.0, reserve_pct=0.10),
)


def _m(name, value, unit, source=MeasurementSource.MEASURED):
    return RichMeasurement(name=name, value=value, unit=unit, source=source)


# 1. transparent.py still imports and decides correctly (F401 fix intact)
r = decide_transparently(TransparentDecisionRequest(
    config=CFG, config_version="cfg-1",
    it_load=_m("it_load", 400.0, "kW"),
    cooling_load=_m("cooling_load", 300.0, "kW"),
    quality_eligible=True,
))
assert r.status == "APPROVE"
print("  OK: transparent.py works (F401 fixed)")

# 2. MODEL_LIMITATIONS is still a 6-element list of str, first entry is the
#    full concatenated string
assert len(MODEL_LIMITATIONS) == 6
assert all(isinstance(x, str) for x in MODEL_LIMITATIONS)
assert "facility power = IT load * PUE" in MODEL_LIMITATIONS[0]
assert MODEL_LIMITATIONS[0].endswith("PUE.")
print("  OK: MODEL_LIMITATIONS unchanged (ISC004 fixed)")

# 3. Wrong PUE unit still raises ValueError (test rewrite preserves behaviour)
try:
    decide_transparently(TransparentDecisionRequest(
        config=CFG, config_version="cfg-1",
        it_load=_m("it_load", 400.0, "kW"),
        cooling_load=_m("cooling_load", 300.0, "kW"),
        pue=_m("pue", 1.4, "kW"),
        quality_eligible=True,
    ))
    raise SystemExit("FAIL: wrong PUE unit accepted")
except ValueError as e:
    assert "dimensionless" in str(e), f"unexpected message: {e}"
print("  OK: wrong PUE unit rejected with 'dimensionless' (PT017 fix intact)")

print("  ALL 3 sanity checks passed")
PYEOF

# ---------------------------------------------------------------------------
# pytest (if available)
# ---------------------------------------------------------------------------
if python3 -m pytest --version >/dev/null 2>&1; then
    echo ""
    echo "==> Targeted tests..."
    python3 -m pytest \
        tests/unit/test_pue_integration.py \
        tests/unit/test_uncertainty.py \
        tests/integration/test_transparent_decision_flow.py -v

    echo ""
    echo "==> Full suite (same command CI runs)..."
    python3 -m pytest tests/ -v
else
    echo ""
    echo "  pytest not installed locally — skipping test run."
    echo "  Stdlib verification above covers all three fixes."
fi

echo ""
echo "================================================================"
echo "  All three ruff errors fixed and verified."
echo "================================================================"
