#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

if [[ ! -f "LICENSE" ]]; then
  echo "ERROR: LICENSE not found. Run from EcoFac repo root." >&2
  exit 1
fi
if [[ -e "pyproject.toml" ]]; then
  echo "ERROR: pyproject.toml already exists. Refusing to overwrite." >&2
  exit 1
fi

echo "==> Creating directories..."
mkdir -p src/ecofac/api/routes src/ecofac/domain src/ecofac/ingestion \
         src/ecofac/quality src/ecofac/detection src/ecofac/evidence \
         src/ecofac/incidents src/ecofac/verification src/ecofac/persistence \
         simulator evaluation tests/unit tests/integration tests/fixtures \
         .github/workflows docs

echo "==> Writing package skeleton..."
printf '%s\n' \
  '"""EcoFac — Open Energy Intelligence for Facilities.' \
  '' \
  'Initial scope: energy intelligence only. All data is synthetic.' \
  '"""' \
  '' \
  '__version__ = "0.1.0"' \
  > src/ecofac/__init__.py

printf '%s\n' '"""HTTP API layer."""'            > src/ecofac/api/__init__.py
printf '%s\n' '"""API route modules."""'         > src/ecofac/api/routes/__init__.py
printf '%s\n' '"""Domain entities and enums."""' > src/ecofac/domain/__init__.py
printf '%s\n' '"""CSV ingestion and normalisation."""' > src/ecofac/ingestion/__init__.py
printf '%s\n' '"""Data quality assessment."""'    > src/ecofac/quality/__init__.py
printf '%s\n' '"""Baseline and anomaly detection."""' > src/ecofac/detection/__init__.py
printf '%s\n' '"""Deterministic evidence assembly."""' > src/ecofac/evidence/__init__.py
printf '%s\n' '"""Incident lifecycle tracking."""' > src/ecofac/incidents/__init__.py
printf '%s\n' '"""Before/after energy verification."""' > src/ecofac/verification/__init__.py
printf '%s\n' '"""SQLite persistence layer (stdlib sqlite3)."""' > src/ecofac/persistence/__init__.py

echo "==> Writing pyproject.toml..."
cat > pyproject.toml <<'TOML'
[build-system]
requires = ["setuptools>=82.0.1"]
build-backend = "setuptools.build_meta"

[project]
name = "ecofac"
version = "0.1.0"
description = "Open Energy Intelligence for Facilities (synthetic data only)"
readme = "README.md"
license = "Apache-2.0"
license-files = ["LICENSE"]
requires-python = ">=3.11"
dependencies = [
    "fastapi",
    "pydantic>=2",
    "uvicorn",
]

[project.optional-dependencies]
dev = [
    "pytest>=7",
    "httpx",
    "ruff>=0.8",
]

[tool.setuptools]
package-dir = { "" = "src" }

[tool.setuptools.packages.find]
where = ["src"]
exclude = ["tests*", "simulator*", "evaluation*"]

[tool.ruff]
line-length = 100
target-version = "py311"
src = ["src"]

[tool.ruff.lint]
select = ["ALL"]
ignore = [
    "D",
    "COM812",
    "ISC001",
    "ANN401",
]

[tool.ruff.lint.per-file-ignores]
"tests/**/*.py" = ["S101", "PLR2004", "ANN", "D"]
"__init__.py" = ["F401"]

[tool.ruff.format]
quote-style = "double"
indent-style = "space"

[tool.pytest.ini_options]
minversion = "7.0"
addopts = ["-ra", "--import-mode=importlib"]
testpaths = ["tests"]
pythonpath = ["src"]
markers = ["slow", "integration"]
TOML

echo "==> Writing .gitignore..."
cat > .gitignore <<'GIT'
__pycache__/
*.py[cod]
*.egg-info/
.eggs/
build/
dist/
*.whl
.venv/
venv/
env/
.pytest_cache/
.coverage
htmlcov/
.ruff_cache/
*.db
*.sqlite
*.sqlite3
.DS_Store
.idea/
.vscode/
GIT

echo "==> Writing tests..."
: > tests/__init__.py

cat > tests/unit/test_health.py <<'PY'
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
PY

echo "==> Writing CI workflow..."
cat > .github/workflows/ci.yml <<'YAML'
name: CI

on:
  push:
    branches: [main]
  pull_request:
    branches: [main]

concurrency:
  group: ci-${{ github.ref }}
  cancel-in-progress: true

jobs:
  lint:
    name: Lint (ruff)
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.11"
      - name: Install ruff
        run: python -m pip install --upgrade pip "ruff>=0.8"
      - name: Ruff check
        run: ruff check src tests simulator evaluation

  test:
    name: Test (pytest)
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.11"
          cache: pip
      - name: Install package with dev extras
        run: |
          python -m pip install --upgrade pip
          python -m pip install -e ".[dev]"
      - name: Run pytest
        run: pytest tests/ -v
YAML

echo "==> Writing README.md..."
cat > README.md <<'MD'
# EcoFac — Open Energy Intelligence for Facilities

Reproducible, explainable, evidence-driven energy intelligence for facilities.
Initial scope: **energy intelligence only**, using **synthetic data exclusively**.

## Status: Early Foundation (Part 2)

Repository skeleton, CI (lint + test), linting, and test harness exist.
**No energy intelligence logic has been implemented yet.**

## Scope (initial)

- Synthetic facilities, meters, and readings
- CSV ingestion, normalisation, data quality assessment
- Statistical baselines and anomaly detection
- Evidence-backed findings
- Incident lifecycle tracking
- Before/after energy verification
- FastAPI REST API
- Reproducible evaluation

## Explicitly out of scope

- Real BMS integrations
- Real equipment control
- Maintenance dispatch or work orders
- Financial claims of verified savings
- Paid AI dependencies
- Complex digital twins

## Limitations (read first)

1. **All data is synthetic.** No real building, meter, or BMS connection.
2. **Not production-ready.** Passing tests are not a production claim.
3. **No authentication or authorization** in this scope.
4. **SQLite is single-process**; concurrent writes unsupported.
5. **Statistical methods only**, not learned models.

## Development

Requires Python 3.11+.

~~~bash
python -m pip install -e ".[dev]"
ruff check src tests
ruff format src tests
pytest tests/ -v
~~~

## License

Apache License 2.0 — see [LICENSE](LICENSE).
MD

echo "==> Writing docs placeholders..."
cat > docs/architecture.md <<'MD'
# Architecture (Draft)

Placeholder for the approved Part 1 architecture and dependency map. Expanded
during Part 3 when domain models are implemented.
MD

cat > docs/limitations.md <<'MD'
# Known Limitations

See the Limitations section of the top-level README. Expanded as stages complete.
MD

echo ""
echo "==> Done. Files created (nothing committed)."
echo ""
echo "Review with: git status"
echo "Commit and push only when you explicitly approve."
echo "Then confirm the first GitHub Actions run actually passes."

rm --"$0"

