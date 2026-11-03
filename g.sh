#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

echo "==> Patching test helper _req: positional booleans -> keyword-only (FBT002)..."
python3 << 'PYEOF'
import pathlib

p = pathlib.Path("tests/api/test_decisions_routes.py")
text = p.read_text()

old = '''def _req(
    it_value=400.0,
    cool_value=300.0,
    it_tol=None,
    quality=True,
    quality_stale=False,
    quality_flags=None,
    it_source="MEASURED",
    config_version="cfg-1",
    supersedes=None,
    supersession_reason=None,
):
'''
new = '''def _req(
    *,
    it_value=400.0,
    cool_value=300.0,
    it_tol=None,
    quality=True,
    quality_stale=False,
    quality_flags=None,
    it_source="MEASURED",
    config_version="cfg-1",
    supersedes=None,
    supersession_reason=None,
):
'''
assert old in text, "_req signature not found"
text = text.replace(old, new, 1)
p.write_text(text)
print("  test helper patched")
PYEOF

echo ""
echo "==> Ruff..."
ruff check src tests simulator evaluation scripts

echo ""
echo "==> New tests..."
python -m pytest \
  tests/unit/test_decision_api_schemas.py \
  tests/integration/test_decisions_persistence.py \
  tests/api/test_decisions_routes.py -v

echo ""
echo "==> Full suite (same command CI runs)..."
python -m pytest tests/ -v

echo ""
echo "================================================================"
echo "  All green."
echo "================================================================"
