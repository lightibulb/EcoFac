#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

echo "==> Patching explain.py (Resolve data-quality -> Resolve quality)..."
python3 << 'PYEOF'
import pathlib

p = pathlib.Path("src/ecofac/decision/explain.py")
text = p.read_text()

old1 = '"Resolve data-quality flags: "'
new1 = '"Resolve quality flags: "'
assert old1 in text, "first string not found"
text = text.replace(old1, new1, 1)

old2 = '"Resolve data-quality issues before this decision "'
new2 = '"Resolve quality issues before this decision "'
assert old2 in text, "second string not found"
text = text.replace(old2, new2, 1)

p.write_text(text)
print("  explain.py patched")
PYEOF

echo ""
echo "==> Ruff..."
ruff check src tests simulator evaluation scripts

echo ""
echo "==> Failing test + neighbors..."
python -m pytest \
  tests/unit/test_transparent_decision.py \
  tests/integration/test_transparent_decision_flow.py -v

echo ""
echo "==> Full suite (same command CI runs)..."
python -m pytest tests/ -v

echo ""
echo "=================================================="
echo "  All green."
echo "=================================================="
