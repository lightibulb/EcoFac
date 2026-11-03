#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

echo "==> Fixing ARG001 in test_decision_engineering.py..."
python3 << 'PYEOF'
import pathlib

p = pathlib.Path("tests/unit/test_decision_engineering.py")
text = p.read_text()

old1 = '''def test_missing_evidence_raises(db_conn):
    """A record without the PUE-integrated evidence cannot be summarised."""
'''
new1 = '''def test_missing_evidence_raises():
    """A record without the PUE-integrated evidence cannot be summarised."""
'''
assert old1 in text, "test_missing_evidence_raises not found"
text = text.replace(old1, new1, 1)

old2 = '''def test_missing_result_raises(db_conn):
    with pytest.raises(ValueError, match="predates"):
        build_engineering_summary({"id": "old-1", "result": {}})
'''
new2 = '''def test_missing_result_raises():
    with pytest.raises(ValueError, match="predates"):
        build_engineering_summary({"id": "old-1", "result": {}})
'''
assert old2 in text, "test_missing_result_raises not found"
text = text.replace(old2, new2, 1)

p.write_text(text)
print("  removed 2 unused fixture args")
PYEOF

echo ""
echo "==> Ruff..."
ruff check src tests simulator evaluation scripts

echo ""
echo "==> Sanity — the two tests still pass in isolation..."
PYTHONPATH="src:." python3 << 'PYEOF'
import pytest as _pytest_unused  # noqa: F401
from ecofac.decision.engineering import build_engineering_summary

# Mirror test_missing_evidence_raises
try:
    build_engineering_summary(
        {"id": "old-1", "result": {"evidence": {"calculation_inputs": {}}}}
    )
    raise AssertionError("expected ValueError")
except ValueError as e:
    assert "predates the PUE integration" in str(e)

# Mirror test_missing_result_raises
try:
    build_engineering_summary({"id": "old-1", "result": {}})
    raise AssertionError("expected ValueError")
except ValueError as e:
    assert "predates" in str(e)

print("  OK: both assertions still hold")
PYEOF

echo ""
if python3 -m pytest --version >/dev/null 2>&1; then
    echo "==> Targeted tests..."
    python3 -m pytest \
        tests/unit/test_decision_engineering.py \
        tests/unit/test_decision_stats.py \
        tests/api/test_decisions_page.py \
        tests/api/test_decisions_routes.py -q

    echo ""
    echo "==> Full suite (same command CI runs)..."
    python3 -m pytest tests/ -q
else
    echo "  pytest not installed locally — CI will run the full suite."
fi

echo ""
echo "================================================================"
echo "  All green."
echo "================================================================"
