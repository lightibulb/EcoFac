#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

# ---------------------------------------------------------------------------
# Fix 1: uncertainty.py — add quality_flags field + populate in assess_uncertainty
# ---------------------------------------------------------------------------
echo "==> Patching uncertainty.py..."
python3 << 'PYEOF'
import pathlib

p = pathlib.Path("src/ecofac/decision/uncertainty.py")
text = p.read_text()

old_fields = '''    rationale: list[str]
    tolerance_used: list[str]
'''
new_fields = '''    rationale: list[str]
    tolerance_used: list[str]
    quality_flags: list[str] = pydantic.Field(default_factory=list)
'''
assert old_fields in text, "uncertainty.py: UncertaintyReport fields block not found"
text = text.replace(old_fields, new_fields, 1)

old_ret = '''        rationale=[*data_rationale, *model_rationale],
        tolerance_used=[m.name for m in measurements if m.band() is not None],
    )
'''
new_ret = '''        rationale=[*data_rationale, *model_rationale],
        tolerance_used=[m.name for m in measurements if m.band() is not None],
        quality_flags=list(quality_flags),
    )
'''
assert old_ret in text, "uncertainty.py: assess_uncertainty return block not found"
text = text.replace(old_ret, new_ret, 1)

p.write_text(text)
print("  uncertainty.py patched")
PYEOF

# ---------------------------------------------------------------------------
# Fix 2: explain.py — guarantee at least one hint for data_quality_ineligible
# ---------------------------------------------------------------------------
echo "==> Patching explain.py..."
python3 << 'PYEOF'
import pathlib

p = pathlib.Path("src/ecofac/decision/explain.py")
text = p.read_text()

old_block = '''    if precedence == "data_quality_ineligible":
        if uncertainty.missing_measurements:
            hints.append(
                "Provide values for: "
                + ", ".join(uncertainty.missing_measurements)
            )
        if uncertainty.stale_measurements:
            hints.append(
                "Provide fresh readings for: "
                + ", ".join(uncertainty.stale_measurements)
            )
        if uncertainty.low_quality_measurements:
            hints.append(
                "Resolve quality flags for: "
                + ", ".join(uncertainty.low_quality_measurements)
            )
    elif precedence == "near_threshold":
'''
new_block = '''    if precedence == "data_quality_ineligible":
        if uncertainty.missing_measurements:
            hints.append(
                "Provide values for: "
                + ", ".join(uncertainty.missing_measurements)
            )
        if uncertainty.stale_measurements:
            hints.append(
                "Provide fresh readings for: "
                + ", ".join(uncertainty.stale_measurements)
            )
        if uncertainty.low_quality_measurements:
            hints.append(
                "Resolve quality flags for: "
                + ", ".join(uncertainty.low_quality_measurements)
            )
        if not hints and uncertainty.quality_flags:
            hints.append(
                "Resolve data-quality flags: "
                + ", ".join(uncertainty.quality_flags)
            )
        if not hints:
            hints.append(
                "Resolve data-quality issues before this decision "
                "can be approved."
            )
    elif precedence == "near_threshold":
'''
assert old_block in text, "explain.py: _review_hints data_quality block not found"
text = text.replace(old_block, new_block, 1)

p.write_text(text)
print("  explain.py patched")
PYEOF

# ---------------------------------------------------------------------------
# Verify
# ---------------------------------------------------------------------------
echo ""
echo "==> Ruff..."
ruff check src tests simulator evaluation scripts

echo ""
echo "==> Targeted regression (the failing test + its neighbors)..."
python -m pytest \
  tests/unit/test_transparent_decision.py \
  tests/unit/test_uncertainty.py \
  tests/unit/test_decision_evidence_bundle.py \
  tests/integration/test_transparent_decision_flow.py -v

echo ""
echo "==> Full suite (same command your CI runs)..."
python -m pytest tests/ -v

echo ""
echo "=================================================="
echo "  All green. CI should now pass."
echo "=================================================="
