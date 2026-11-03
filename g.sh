#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

# -----------------------------------------------------------------------------
# Fix 1 + 2: PERF401 in explain.py (two for-append blocks -> list.extend)
# -----------------------------------------------------------------------------
echo "==> Patching explain.py (PERF401 x2)..."
python3 << 'PYEOF'
import pathlib

p = pathlib.Path("src/ecofac/decision/explain.py")
text = p.read_text()

old1 = '''    for s in sensitivity:
        if s.could_change_verdict:
            hints.append(
                f"Measurement '{s.measurement_name}' tolerance could flip the "
                f"verdict: {s.reason}"
            )
    return hints
'''
new1 = '''    hints.extend(
        f"Measurement '{s.measurement_name}' tolerance could flip the "
        f"verdict: {s.reason}"
        for s in sensitivity
        if s.could_change_verdict
    )
    return hints
'''
assert old1 in text, "explain.py: block 1 not found"
text = text.replace(old1, new1, 1)

old2 = '''    for s in sensitivity:
        if s.could_change_verdict:
            uncertainty_notes.append(
                f"sensitivity: {s.measurement_name} tolerance could flip verdict"
            )
'''
new2 = '''    uncertainty_notes.extend(
        f"sensitivity: {s.measurement_name} tolerance could flip verdict"
        for s in sensitivity
        if s.could_change_verdict
    )
'''
assert old2 in text, "explain.py: block 2 not found"
text = text.replace(old2, new2, 1)

p.write_text(text)
print("  explain.py patched")
PYEOF

# -----------------------------------------------------------------------------
# Fix 3: FBT001 in uncertainty.py (make quality_stale keyword-only + update call)
# -----------------------------------------------------------------------------
echo "==> Patching uncertainty.py (FBT001)..."
python3 << 'PYEOF'
import pathlib

p = pathlib.Path("src/ecofac/decision/uncertainty.py")
text = p.read_text()

old_sig = '''def _assess_data_quality(
    measurements: list[RichMeasurement],
    quality_flags: list[str],
    quality_stale: bool,
) -> tuple[DataQualityLevel, list[str]]:
'''
new_sig = '''def _assess_data_quality(
    measurements: list[RichMeasurement],
    quality_flags: list[str],
    *,
    quality_stale: bool,
) -> tuple[DataQualityLevel, list[str]]:
'''
assert old_sig in text, "uncertainty.py: signature not found"
text = text.replace(old_sig, new_sig, 1)

old_call = '''    data_level, data_rationale = _assess_data_quality(
        measurements, quality_flags, quality_stale
    )
'''
new_call = '''    data_level, data_rationale = _assess_data_quality(
        measurements, quality_flags, quality_stale=quality_stale
    )
'''
assert old_call in text, "uncertainty.py: call site not found"
text = text.replace(old_call, new_call, 1)

p.write_text(text)
print("  uncertainty.py patched")
PYEOF

# -----------------------------------------------------------------------------
# Fix 4: FBT001 in transparent.py (make changed keyword-only + update 2 callers)
# -----------------------------------------------------------------------------
echo "==> Patching transparent.py (FBT001)..."
python3 << 'PYEOF'
import pathlib

p = pathlib.Path("src/ecofac/decision/transparent.py")
text = p.read_text()

old_sig = '''def _sensitivity_reason(
    name: str,
    low: float,
    high: float,
    status_low: str,
    status_high: str,
    baseline_status: str,
    changed: bool,
) -> str:
'''
new_sig = '''def _sensitivity_reason(
    name: str,
    low: float,
    high: float,
    status_low: str,
    status_high: str,
    baseline_status: str,
    *,
    changed: bool,
) -> str:
'''
assert old_sig in text, "transparent.py: signature not found"
text = text.replace(old_sig, new_sig, 1)

old_call_it = '''                reason=_sensitivity_reason(
                    it_load.name,
                    low,
                    high,
                    status_low,
                    status_high,
                    baseline_status,
                    changed,
                ),
'''
new_call_it = '''                reason=_sensitivity_reason(
                    it_load.name,
                    low,
                    high,
                    status_low,
                    status_high,
                    baseline_status,
                    changed=changed,
                ),
'''
assert old_call_it in text, "transparent.py: it_load caller not found"
text = text.replace(old_call_it, new_call_it, 1)

old_call_cool = '''                reason=_sensitivity_reason(
                    cooling_load.name,
                    low,
                    high,
                    status_low,
                    status_high,
                    baseline_status,
                    changed,
                ),
'''
new_call_cool = '''                reason=_sensitivity_reason(
                    cooling_load.name,
                    low,
                    high,
                    status_low,
                    status_high,
                    baseline_status,
                    changed=changed,
                ),
'''
assert old_call_cool in text, "transparent.py: cooling_load caller not found"
text = text.replace(old_call_cool, new_call_cool, 1)

p.write_text(text)
print("  transparent.py patched")
PYEOF

# -----------------------------------------------------------------------------
# Verify
# -----------------------------------------------------------------------------
echo ""
echo "==> Repo-wide ruff..."
ruff check src tests simulator evaluation scripts

echo ""
echo "==> New-module targeted tests..."
pytest tests/unit/test_measurements.py \
       tests/unit/test_uncertainty.py \
       tests/unit/test_decision_evidence_bundle.py \
       tests/unit/test_transparent_decision.py \
       tests/integration/test_transparent_decision_flow.py -v

echo ""
echo "==> Full suite (regression guard)..."
pytest tests/ -v

echo ""
echo "=================================================="
echo "  Transparent Decision System — fully green."
echo "=================================================="
