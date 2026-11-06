#!/usr/bin/env bash
# =============================================================================
# EcoFac - P2-6 + P2-7 + P2-10 + P2-11
#
# No triple-quote syntax anywhere in this file. Generated files use #
# comments instead of docstrings; Python transformation strings use
# single-line fragments only.
#
# P2-6   AST drift test between pipeline.py codes and imports map
# P2-7   verify_scenario uses stored config_version, not "verify-tmp"
# P2-10  ephemeral warning moves to FastAPI lifespan handler
# P2-11  regen script + docs for ARCHITECTURE.json
#
# Idempotent. Backs up every touched file. Aborts on lint or test failure.
# =============================================================================
set -euo pipefail

RUFF="${RUFF:-ruff}"
PYTEST="${PYTEST:-pytest}"

REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
cd "$REPO_ROOT"

RED=$'\033[31m'; GREEN=$'\033[32m'; YELLOW=$'\033[33m'; BLUE=$'\033[34m'; RESET=$'\033[0m'
abort() { printf '%sABORT:%s %s\n' "$RED" "$RESET" "$*" >&2; exit 1; }
info()  { printf '%s==%s %s\n' "$GREEN" "$RESET" "$*"; }
warn()  { printf '%s!!%s %s\n' "$YELLOW" "$RESET" "$*"; }
step()  { printf '\n%s>>> %s%s\n' "$BLUE" "$*" "$RESET"; }

BACKUP_DIR="$(mktemp -d "${TMPDIR:-/tmp}/ecofac-p2-6-7-10-11-XXXXXX")"
on_exit() { printf '\nbackups: %s\n' "$BACKUP_DIR"; }
trap on_exit EXIT
backup() { [[ -f "$1" ]] && cp -a "$1" "$BACKUP_DIR/$(printf '%s' "$1" | tr '/' '_')" || true; }

step "Preflight"
for f in \
    src/ecofac/ingestion/pipeline.py \
    src/ecofac/api/routes/imports.py \
    src/ecofac/decision/scenarios.py \
    src/ecofac/main.py
do
  [[ -f "$f" ]] || abort "missing: $f"
done
mkdir -p tests/unit scripts docs
info "repo:   $REPO_ROOT"
info "python: $(python3 --version 2>&1)"

# =============================================================================
# P2-6
# =============================================================================
step "P2-6: write tests/unit/test_ingestion_error_codes.py"
backup tests/unit/test_ingestion_error_codes.py

cat > tests/unit/test_ingestion_error_codes.py <<'PYEOF'
# P2-6: ingestion error-code drift test.
#
# pipeline.py raises IngestionError with string codes. imports.py maps
# those codes to HTTP statuses via _INGESTION_STATUS; any unmapped code
# silently falls back to HTTP 400. If pipeline.py adds a code and
# imports.py is not updated, the new code degrades to 400 with no test
# failure. This test AST-parses both files and asserts:
#   1. every code raised in pipeline.py is mapped in _INGESTION_STATUS
#      or listed in DOCUMENTED_400_CODES
#   2. every entry in _INGESTION_STATUS can actually be raised
#   3. every entry in DOCUMENTED_400_CODES can actually be raised

import ast
from pathlib import Path

PIPELINE_PATH = Path("src/ecofac/ingestion/pipeline.py")
IMPORTS_PATH = Path("src/ecofac/api/routes/imports.py")

# Codes intentionally mapped to the 400 fallback. Adding here is a
# deliberate choice; document why before adding.
DOCUMENTED_400_CODES = frozenset({
    "malformed_csv",
    "missing_columns",
})


def _pipeline_codes() -> set[str]:
    src = PIPELINE_PATH.read_text(encoding="utf-8")
    tree = ast.parse(src)
    codes: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not (isinstance(func, ast.Name) and func.id == "IngestionError"):
            continue
        if not node.args:
            continue
        first = node.args[0]
        if isinstance(first, ast.Constant) and isinstance(first.value, str):
            codes.add(first.value)
    return codes


def _ingestion_status_map() -> dict[str, int]:
    src = IMPORTS_PATH.read_text(encoding="utf-8")
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        for tgt in node.targets:
            if isinstance(tgt, ast.Name) and tgt.id == "_INGESTION_STATUS":
                value = ast.literal_eval(node.value)
                if not isinstance(value, dict):
                    msg = (
                        f"_INGESTION_STATUS in {IMPORTS_PATH} is not a "
                        f"literal dict (got {type(value).__name__})"
                    )
                    raise AssertionError(msg)
                return value
    msg = f"_INGESTION_STATUS assignment not found in {IMPORTS_PATH}"
    raise AssertionError(msg)


def test_every_pipeline_code_is_mapped_or_documented() -> None:
    codes = _pipeline_codes()
    mapped = _ingestion_status_map()
    undocumented = codes - mapped.keys() - DOCUMENTED_400_CODES
    msg = (
        f"pipeline.py raises codes not mapped in _INGESTION_STATUS and not "
        f"in DOCUMENTED_400_CODES: {sorted(undocumented)}. "
        f"Add each to imports._INGESTION_STATUS (with an HTTP status) or "
        f"to DOCUMENTED_400_CODES (defaults to 400)."
    )
    assert not undocumented, msg


def test_no_dead_entries_in_ingestion_status_map() -> None:
    codes = _pipeline_codes()
    mapped = _ingestion_status_map()
    dead = mapped.keys() - codes
    msg = (
        f"_INGESTION_STATUS has entries pipeline.py never raises: "
        f"{sorted(dead)}. Remove them."
    )
    assert not dead, msg


def test_documented_400_codes_are_real() -> None:
    codes = _pipeline_codes()
    extra = DOCUMENTED_400_CODES - codes
    msg = (
        f"DOCUMENTED_400_CODES lists codes pipeline.py does not raise: "
        f"{sorted(extra)}. Remove them."
    )
    assert not extra, msg
PYEOF
info "wrote tests/unit/test_ingestion_error_codes.py"

# =============================================================================
# P2-7
# =============================================================================
step "P2-7: scenarios.py verify_scenario uses stored config_version"
backup src/ecofac/decision/scenarios.py

python3 - <<'PYEOF'
import pathlib
import sys

path = pathlib.Path("src/ecofac/decision/scenarios.py")
src = path.read_text()

if '"verify-tmp"' not in src:
    print("scenarios.py: 'verify-tmp' already gone; skipping")
    sys.exit(0)

OLD_SELECT = (
    "    row = conn.execute(\n"
    '        "SELECT input_json, config_json, result_json, "\n'
    '        "input_fingerprint, config_fingerprint, engine_version "\n'
    '        "FROM scenarios WHERE id = ?",\n'
    "        (scenario_id,),\n"
    "    ).fetchone()"
)
NEW_SELECT = (
    "    row = conn.execute(\n"
    '        "SELECT input_json, config_json, result_json, "\n'
    '        "input_fingerprint, config_fingerprint, engine_version, "\n'
    '        "config_version "\n'
    '        "FROM scenarios WHERE id = ?",\n'
    "        (scenario_id,),\n"
    "    ).fetchone()"
)
OLD_BODY = '            "config_version": "verify-tmp",'
NEW_BODY = '            "config_version": row["config_version"],'

if OLD_SELECT not in src:
    print("ERROR: verify_scenario SELECT anchor not found", file=sys.stderr)
    sys.exit(1)
if OLD_BODY not in src:
    print("ERROR: verify_scenario body anchor not found", file=sys.stderr)
    sys.exit(1)

src = src.replace(OLD_SELECT, NEW_SELECT, 1)
src = src.replace(OLD_BODY, NEW_BODY, 1)
path.write_text(src)
print("scenarios.py: SELECT + body updated")
PYEOF

step "P2-7: write tests/unit/test_verify_scenario_config_version.py"
backup tests/unit/test_verify_scenario_config_version.py

cat > tests/unit/test_verify_scenario_config_version.py <<'PYEOF'
# P2-7: verify_scenario uses the stored config_version, not a placeholder.
#
# Previously the verify path built a TransparentDecisionRequest with the
# literal "verify-tmp" config_version. It never affected the fingerprints
# or the status comparison (config_version does not feed either), but it
# was confusing to read and harder to audit. This test asserts the verify
# path passes the real stored config_version into the decision engine on
# replay.

import contextlib
import tempfile
from pathlib import Path

import pytest
from ecofac.decision import scenarios as scenarios_module
from ecofac.decision.limits import (
    ConstraintConfig,
    CoolingLimits,
    ElectricalLimits,
)
from ecofac.decision.scenarios import (
    build_transparent_request,
    evaluate_and_persist_scenario,
    verify_scenario,
)
from ecofac.persistence.database import get_connection, init_db


@pytest.fixture
def temp_conn():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    conn = get_connection(db_path)
    init_db(conn)
    try:
        yield conn
    finally:
        conn.close()
        with contextlib.suppress(OSError):
            Path(db_path).unlink()


def _persist(conn, config_version: str) -> str:
    config = ConstraintConfig(
        electrical=ElectricalLimits(capacity=1000.0, reserve_pct=0.1),
        cooling=CoolingLimits(capacity=800.0, reserve_pct=0.1),
    )
    req = build_transparent_request(
        it_load_kw=500.0,
        cooling_load_kw=500.0,
        pue=1.4,
        proposed_it_delta_kw=50.0,
        proposed_cooling_delta_kw=0.0,
        config=config,
        config_version=config_version,
    )
    scenario_id, _, _ = evaluate_and_persist_scenario(
        conn,
        name="verify-config-version-test",
        transparent_request=req,
        persist_as_decision=False,
    )
    return scenario_id


def test_verify_scenario_replays_with_stored_config_version(
    temp_conn, monkeypatch
) -> None:
    scenario_id = _persist(temp_conn, "custom-v1")

    captured: list[str] = []
    real = scenarios_module.decide_transparently

    def spy(request):
        captured.append(request.config_version)
        return real(request)

    monkeypatch.setattr(scenarios_module, "decide_transparently", spy)

    result = verify_scenario(temp_conn, scenario_id)

    assert result is not None
    assert result["verified"] is True
    assert captured, "verify_scenario did not call decide_transparently"
    assert captured[-1] == "custom-v1", (
        f"replay used {captured[-1]!r}, expected 'custom-v1'"
    )
    assert "verify-tmp" not in captured
PYEOF
info "wrote tests/unit/test_verify_scenario_config_version.py"

# =============================================================================
# P2-10
# =============================================================================
step "P2-10: main.py moves ephemeral warning to lifespan"
backup src/ecofac/main.py

python3 - <<'PYEOF'
import pathlib
import sys

path = pathlib.Path("src/ecofac/main.py")
src = path.read_text()

if "_lifespan" in src:
    print("main.py: lifespan already present; skipping")
    sys.exit(0)

OLD_IMP1 = "from collections.abc import Callable"
NEW_IMP1 = "from collections.abc import AsyncIterator, Callable"
if OLD_IMP1 not in src:
    print("ERROR: Callable import not found", file=sys.stderr)
    sys.exit(1)
src = src.replace(OLD_IMP1, NEW_IMP1, 1)

OLD_IMP2 = "import sqlite3\n"
NEW_IMP2 = "import sqlite3\nfrom contextlib import asynccontextmanager\n"
if OLD_IMP2 not in src:
    print("ERROR: sqlite3 import not found", file=sys.stderr)
    sys.exit(1)
src = src.replace(OLD_IMP2, NEW_IMP2, 1)

OLD_BLOCK = (
    "_ephemeral_warning = ephemeral_storage_warning()\n"
    "if _ephemeral_warning:\n"
    "    logger.warning(_ephemeral_warning)\n"
)
if OLD_BLOCK not in src:
    print("ERROR: import-time warning block not found", file=sys.stderr)
    sys.exit(1)
src = src.replace(OLD_BLOCK, "", 1)

ANCHOR = "BASE_DIR = pathlib.Path(__file__).parent"
LIFESPAN = (
    "@asynccontextmanager\n"
    "async def _lifespan(_app: FastAPI) -> AsyncIterator[None]:\n"
    "    # Log the ephemeral-storage warning on startup, not on import.\n"
    "    warning = ephemeral_storage_warning()\n"
    "    if warning:\n"
    "        logger.warning(warning)\n"
    "    yield\n"
    "\n"
    "\n"
)
if ANCHOR not in src:
    print("ERROR: BASE_DIR anchor not found", file=sys.stderr)
    sys.exit(1)
src = src.replace(ANCHOR, LIFESPAN + ANCHOR, 1)

OLD_APP = (
    "app = FastAPI(\n"
    '    title="EcoFac API",\n'
    '    version="0.1.0",\n'
    "    description=(\n"
    '        "Local demonstration API. Not production-secure. Requires auth '
    'for public deployment."\n'
    "    ),\n"
    ")"
)
NEW_APP = (
    "app = FastAPI(\n"
    '    title="EcoFac API",\n'
    '    version="0.1.0",\n'
    "    description=(\n"
    '        "Local demonstration API. Not production-secure. Requires auth '
    'for public deployment."\n'
    "    ),\n"
    "    lifespan=_lifespan,\n"
    ")"
)
if OLD_APP not in src:
    print("ERROR: FastAPI constructor anchor not found", file=sys.stderr)
    sys.exit(1)
src = src.replace(OLD_APP, NEW_APP, 1)

path.write_text(src)
print("main.py: ephemeral warning moved to lifespan")
PYEOF

# =============================================================================
# P2-11
# =============================================================================
step "P2-11: write scripts/regen_architecture.sh"
backup scripts/regen_architecture.sh

cat > scripts/regen_architecture.sh <<'SHEOF'
#!/usr/bin/env bash
# Regenerate ARCHITECTURE.json and verify false-positive reduction.
# Install: pip install py-code-visualizer
# Override the build command: ARCH_CMD="..." bash scripts/regen_architecture.sh

set -euo pipefail

REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
cd "$REPO_ROOT"

ARCH_CMD="${ARCH_CMD:-py-code-visualizer build}"
BACKUP="$(mktemp -d "${TMPDIR:-/tmp}/ecofac-arch-regen-XXXXXX")"

echo "=== preflight ==="
if ! command -v py-code-visualizer >/dev/null 2>&1; then
  echo "py-code-visualizer not found on PATH."
  echo "Install: pip install py-code-visualizer"
  exit 0
fi
echo "tool:  $(py-code-visualizer --version 2>&1 | head -1)"
echo "cmd:   $ARCH_CMD"

for f in ARCHITECTURE.json ARCHITECTURE.md; do
  if [[ -f "$f" ]]; then
    cp -a "$f" "$BACKUP/$(basename "$f")"
    echo "backed up $f"
  fi
done

echo
echo "=== running build ==="
$ARCH_CMD || { echo "ERROR: build command failed"; exit 1; }

echo
echo "=== stats diff ==="
python3 - "$BACKUP/ARCHITECTURE.json" ARCHITECTURE.json <<'PYEOF'
import json
import pathlib
import sys

old_path = pathlib.Path(sys.argv[1])
new_path = pathlib.Path(sys.argv[2])
if not old_path.exists() or not new_path.exists():
    print("skipping diff (missing old or new JSON)")
    sys.exit(0)

old = json.loads(old_path.read_text())
new = json.loads(new_path.read_text())

for k in ("nodes", "edges", "cycles", "ambiguous_edges"):
    before = int(old.get("stats", {}).get(k, 0) or 0)
    after = int(new.get("stats", {}).get(k, 0) or 0)
    delta = after - before
    sign = "+" if delta >= 0 else ""
    print(f"{k:<20} {before:>10} {after:>10} {sign}{delta:>9}")

def has_edge(graph, caller_suffix, callee_suffix):
    for e in graph.get("edges", []):
        if (
            e.get("caller", "").endswith(caller_suffix)
            and e.get("callee", "").endswith(callee_suffix)
        ):
            return True
    return False

checks = (
    ("decorator post false pos", "evaluate_decision", "test_trusted_decisions_api.post_json"),
    ("set.add false pos", "_walk_supersession_impl", "add_reading_row"),
    ("Path.resolve false pos", "_apply_migrations", "resolve_pair_request"),
    ("_sanitize_json recursion (correct)", "_sanitize_json", "_sanitize_json"),
)
print()
print("=== false-positive edge checks ===")
for label, caller, callee in checks:
    present = has_edge(new, caller, callee)
    print(f"  {label}: {'STILL PRESENT' if present else 'GONE'}")
PYEOF

echo
echo "backups: $BACKUP"
SHEOF
chmod +x scripts/regen_architecture.sh
info "wrote scripts/regen_architecture.sh"

step "P2-11: write docs/architecture-regen.md"
backup docs/architecture-regen.md

cat > docs/architecture-regen.md <<'MDEOF'
# Regenerating ARCHITECTURE.json

`ARCHITECTURE.json` is produced by `py-code-visualizer`. It is a snapshot,
not a live view; it goes stale whenever call-graph structure changes.

## When to regenerate

- After any change that adds, removes, or renames functions
- After renaming test helpers (P0-2)
- Before relying on god_nodes or high-risk hubs for a review

## How

    pip install py-code-visualizer
    bash scripts/regen_architecture.sh

The script:

1. Backs up the existing ARCHITECTURE.json and ARCHITECTURE.md
2. Runs the build command (default `py-code-visualizer build`; override
   with `ARCH_CMD=...`)
3. Prints a stats diff (nodes, edges, cycles, ambiguous_edges)
4. Checks for the specific false-positive edges identified in the P0
   audit and reports GONE or STILL PRESENT

## Expected changes after P0/P1/P2

| False positive | Cause | Fix landed in |
|---|---|---|
| evaluate_decision -> test_trusted_decisions_api.post | @router.post decorator line misattributed to unique helper `post` | P0-2 |
| _walk_supersession_impl -> test_runner_canary...add | set.add misattributed to unique helper `add` | P0-2 |
| _apply_migrations -> test_trust_resolution.resolve | Path.resolve misattributed to unique helper `resolve` | P0-2 |
| test_trusted_decisions_api.post self-cycle | client.post() inside a helper defining post | P0-2 |
| _sanitize_json self-cycle | intentional recursion, actually correct | not a bug |

Additional expected changes:

- edge count decreases (false positives removed)
- god_nodes list loses the test helpers P0-2 renamed
- health score may improve (less hub_risk from test helpers)
- _log_audit duplicates consolidated (P1-1): store.py, scenarios.py,
  lifecycle.py now all call src.ecofac.audit.log_audit
- testdrive wrapper block removed (P1-2)

## Not fixed by regeneration

The tool still:

- does not trace calls from inside nested closures (why P0-1 looked
  suspicious but was verified as correct)
- does not know FastAPI DI, ASGI middleware dispatch, or property
  accessors (orphan count stays high)
- does not distinguish src.ecofac.X from ecofac.X (P1-0 guard prevents
  new tests creating dual imports, but old edges may remain)

## After regeneration

Commit if false positives are GONE and edge count dropped. If a check
still shows STILL PRESENT, run:

    grep -rn 'def post\b' tests/ 2>/dev/null
    grep -rn 'def add\b' tests/ 2>/dev/null
    grep -rn 'def resolve\b' tests/ 2>/dev/null

Empty output confirms the rename was complete.
MDEOF
info "wrote docs/architecture-regen.md"

# =============================================================================
# Verification
# =============================================================================
step "Auto-format touched files"
FORMAT_ARGS=()
for f in \
    src/ecofac/decision/scenarios.py \
    src/ecofac/main.py \
    tests/unit/test_ingestion_error_codes.py \
    tests/unit/test_verify_scenario_config_version.py
do
  [[ -f "$f" ]] && FORMAT_ARGS+=("$f")
done
if [[ ${#FORMAT_ARGS[@]} -gt 0 ]]; then
  $RUFF format "${FORMAT_ARGS[@]}" || abort "ruff format failed"
fi

step "Lint (ruff check)"
$RUFF check src tests simulator evaluation scripts --fix|| abort "ruff check failed"

step "Sanity: main imports cleanly and P1-2 hardening still applied"
python3 - <<'PYEOF'
import importlib
import sys

sys.modules.pop("ecofac.main", None)
importlib.import_module("ecofac.main")

import ecofac.api.rate_limit as rl
assert getattr(rl.RateLimiter, "ecofac_secure_wrapped", False) is True, (
    "P1-2 hardening was lost"
)
print("OK: main.py imports cleanly; P1-2 hardening still applied")
PYEOF

step "Focused tests"
FOCUS=(
  tests/unit/test_ingestion_error_codes.py
  tests/integration/test_import_reliability.py
  tests/api/test_import_api_reliability.py
  tests/integration/test_ingestion.py
  tests/unit/test_verify_scenario_config_version.py
  tests/api/test_scenarios_api.py
  tests/api/test_scenarios_analysis_api.py
  tests/unit/test_ephemeral_storage_warning.py
  tests/api/test_api_smoke.py
  tests/api/test_middleware.py
  tests/unit/test_sensitivity_equivalence.py
  tests/unit/test_rounding_policy.py
  tests/unit/test_testdrive_cache.py
  tests/unit/test_sensitivity_cache.py
)
$PYTEST --tb=short -q "${FOCUS[@]}" || abort "focused tests failed"

step "Summary"
cat <<'OUT'
P2-6   tests/unit/test_ingestion_error_codes.py (3 tests)
P2-7   scenarios.py:verify_scenario uses row["config_version"]
       tests/unit/test_verify_scenario_config_version.py (1 test)
P2-10  main.py: ephemeral warning moved to lifespan handler
P2-11  scripts/regen_architecture.sh + docs/architecture-regen.md

Deferred:
  P2-1  decide_transparently split   - separate session
  P2-2  _ingest_rows split + retry   - separate session
  P2-8  structured logging           - needs middleware.py
  P2-9  rate-limit default           - needs deployment decision
OUT
git diff --stat || true
