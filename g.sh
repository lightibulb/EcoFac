#!/usr/bin/env bash
# =============================================================================
# g.sh — EcoFac last-moment bug-fix bundle (LMF-01..09) — v2.2
# Usage:   bash g.sh [repo-root]
# Safe:    idempotent; timestamped backups; loud failures.
# =============================================================================
set -euo pipefail

log()  { printf '\033[1;32m[LMF]\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[LMF][warn]\033[0m %s\n' "$*"; }
die()  { printf '\033[1;31m[LMF][FAIL]\033[0m %s\n' "$*" >&2; exit 1; }

REPO_ROOT="${1:-$(pwd)}"
cd "$REPO_ROOT"
[[ -d src/ecofac ]] || die "src/ecofac not found — run from the EcoFac repo root"
command -v python3 >/dev/null 2>&1 || die "python3 is required"
export PYTHONPATH="$REPO_ROOT/src${PYTHONPATH:+:$PYTHONPATH}"

# ---------------------------------------------------------------------------
# 0. Locate modules + templates + ruff
# ---------------------------------------------------------------------------
log "Locating route modules ..."
WORLD_ROUTES="$(python3 - <<'PY'
import pathlib, sys
cands = [str(p) for p in sorted(pathlib.Path("src/ecofac/api/routes").glob("*.py"))
         if "max_severity" in p.read_text(encoding="utf-8")
         and "buildings" in p.read_text(encoding="utf-8")
         and "APIRouter" in p.read_text(encoding="utf-8")]
if len(cands) != 1:
    sys.exit(f"world routes module ambiguous or missing: {cands}")
print(cands[0])
PY
)" || die "cannot locate the world routes module"

DASH_ROUTES="$(python3 - <<'PY'
import pathlib, sys
cands = [str(p) for p in sorted(pathlib.Path("src/ecofac/api/routes").glob("*.py"))
         if "generate-demo" in p.read_text(encoding="utf-8")]
if len(cands) != 1:
    sys.exit(f"dashboard routes module ambiguous or missing: {cands}")
print(cands[0])
PY
)" || die "cannot locate the dashboard routes module"

if [[ -d src/ecofac/templates ]]; then
  TEMPLATES_DIR="src/ecofac/templates"
elif [[ -d templates ]]; then
  TEMPLATES_DIR="templates"
else
  die "no templates dir found (checked src/ecofac/templates and templates)"
fi

HAVE_RUFF=0
RUFF=(ruff)
if command -v ruff >/dev/null 2>&1; then
  HAVE_RUFF=1
elif python3 -m ruff --version >/dev/null 2>&1; then
  RUFF=(python3 -m ruff)
  HAVE_RUFF=1
fi

log "  world routes    : $WORLD_ROUTES"
log "  dashboard routes: $DASH_ROUTES"
log "  templates dir   : $TEMPLATES_DIR"
log "  ruff            : $([[ $HAVE_RUFF -eq 1 ]] && echo "${RUFF[*]}" || echo 'NOT FOUND')"

FILES_TO_TOUCH=(
  src/ecofac/api/routes/incidents.py
  "$WORLD_ROUTES"
  "$DASH_ROUTES"
  "$TEMPLATES_DIR/index.html"
  "$TEMPLATES_DIR/world.html"
  "$TEMPLATES_DIR/decisions.html"
  "$TEMPLATES_DIR/scenarios.html"
  .github/workflows/ci.yml
)
for f in "${FILES_TO_TOUCH[@]}"; do
  [[ -f "$f" ]] || die "expected file missing: $f — share it so the patch can be targeted"
done

if [[ -f tests/integration/test_incidents.py ]]; then
  warn "tests/integration/test_incidents.py exists. If it asserts the OLD"
  warn "empty-body resolve behaviour, it will fail by design at Gate 4"
  warn "(required reason is the new contract). Update that test if so."
fi

# ---------------------------------------------------------------------------
# Backup + lint baselines (baseline = oldest backup = true pre-fix state)
# ---------------------------------------------------------------------------
BACKUP_DIR=".lmf-backup-$(date +%Y%m%d-%H%M%S)"
for f in "${FILES_TO_TOUCH[@]}"; do
  mkdir -p "$BACKUP_DIR/$(dirname "$f")"
  cp "$f" "$BACKUP_DIR/$f"
done
log "Backup written to $BACKUP_DIR"

OLDEST_BACKUP="$(ls -dt .lmf-backup-* 2>/dev/null | tail -n 1 || true)"
[[ -n "$OLDEST_BACKUP" ]] || OLDEST_BACKUP="$BACKUP_DIR"
log "Pre-fix baseline source: $OLDEST_BACKUP"

LMF_TMP="$(mktemp -d .lmf-tmp-XXXXXX)"
export LMF_TMP
RULE_SNAP_DIR="$LMF_TMP/rules"
mkdir -p "$RULE_SNAP_DIR"

if [[ "$HAVE_RUFF" -eq 1 ]]; then
  baseline_source() {  # $1 = relative path -> pre-fix copy if available
    if [[ -f "$OLDEST_BACKUP/$1" ]]; then
      printf '%s' "$OLDEST_BACKUP/$1"
    else
      printf '%s' "$1"
    fi
  }
  snap_rules() {  # $1 = relative file, $2 = tag
    local src_file
    src_file="$(baseline_source "$1")"
    "${RUFF[@]}" check --output-format=concise "$src_file" 2>/dev/null |
      sed -nE 's/^[^:]+:[0-9]+:[0-9]+: ([A-Z]+[0-9]+).*$/\1/p' |
      sort | uniq -c | awk '{print $2" "$1}' | sort > "$RULE_SNAP_DIR/$2.txt" || true
  }
  snap_rules src/ecofac/api/routes/incidents.py base_incidents
  snap_rules "$WORLD_ROUTES" base_world
  snap_rules "$DASH_ROUTES" base_dashboard
  : > "$RULE_SNAP_DIR/base_newtest.txt"   # new test file must be fully clean
fi

cat > "$LMF_TMP/compare_rules.py" <<'CMP'
import pathlib, sys

def load(p):
    counts = {}
    for line in pathlib.Path(p).read_text(encoding="utf-8").splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[1].isdigit():
            counts[parts[0]] = int(parts[1])
    return counts

before, after = load(sys.argv[1]), load(sys.argv[2])
regressions = {
    k: (before.get(k, 0), v)
    for k, v in after.items() if v > before.get(k, 0)
}
if regressions:
    print(f"NEW lint violations (rule: before->after): {regressions}",
          file=sys.stderr)
    sys.exit(1)
CMP

# ---------------------------------------------------------------------------
# Shared patch helpers
# ---------------------------------------------------------------------------
cat > "$LMF_TMP/patchutil.py" <<'PYUTIL'
"""Shared helpers for LMF patch blocks (AST-safe endpoint surgery)."""
from __future__ import annotations

import ast
import pathlib
import sys


def read(path):
    return pathlib.Path(path).read_text(encoding="utf-8")


def write(path, text):
    pathlib.Path(path).write_text(text, encoding="utf-8")


def die(msg):
    print(f"[LMF][PATCH] ABORT: {msg}", file=sys.stderr)
    raise SystemExit(1)


def find_endpoint(src, method, path_pred):
    """Locate a handler by decorator router.<method>("<path>").

    Returns (first_lineno, end_lineno, func_name, route_path) or None.
    """
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for dec in node.decorator_list:
            if not isinstance(dec, ast.Call):
                continue
            fn = dec.func
            if not (isinstance(fn, ast.Attribute) and fn.attr == method):
                continue
            if not dec.args:
                continue
            a0 = dec.args[0]
            if (isinstance(a0, ast.Constant)
                    and isinstance(a0.value, str)
                    and path_pred(a0.value)):
                first = min(
                    [d.lineno for d in node.decorator_list] + [node.lineno]
                )
                return first, node.end_lineno, node.name, a0.value
    return None


def replace_lines(src, start, end, new_block):
    lines = src.splitlines(keepends=True)
    if not new_block.endswith("\n"):
        new_block += "\n"
    return "".join(lines[: start - 1]) + new_block + "".join(lines[end:])


def imported_names(src):
    tree = ast.parse(src)
    from_map = {}
    plain = set()
    last_import_end = 0
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            for a in node.names:
                from_map.setdefault(node.module, set()).add(a.asname or a.name)
        elif isinstance(node, ast.Import):
            for a in node.names:
                plain.add((a.asname or a.name).split(".")[0])
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            last_import_end = max(last_import_end, node.end_lineno)
    return from_map, plain, last_import_end


def ensure_from_imports(src, module, names):
    from_map, _, last = imported_names(src)
    have = from_map.get(module, set())
    missing = [n for n in names if n not in have]
    if not missing:
        return src
    line = f"from {module} import {', '.join(missing)}\n"
    lines = src.splitlines(keepends=True)
    if last == 0:
        return line + src
    lines.insert(last, line)
    return "".join(lines)


def ensure_plain_import(src, module):
    _, plain, last = imported_names(src)
    if module in plain:
        return src
    line = f"import {module}\n"
    lines = src.splitlines(keepends=True)
    if last == 0:
        return line + src
    lines.insert(last, line)
    return "".join(lines)


def insert_before(src, marker, block, idem):
    if idem in src:
        return src, False
    idx = src.find(marker)
    if idx == -1:
        return src, False
    return src[:idx] + block + src[idx:], True
PYUTIL
log "Patch helpers ready"

# ---------------------------------------------------------------------------
# LMF-01 + LMF-02 — incidents API
# ---------------------------------------------------------------------------
log "LMF-01/02: patching src/ecofac/api/routes/incidents.py ..."
python3 - <<'PY'
import ast
import os
import re
import sys

sys.path.insert(0, os.environ["LMF_TMP"])
from patchutil import (  # noqa: E402
    die, ensure_from_imports, ensure_plain_import, find_endpoint, read,
    replace_lines, write,
)

P = "src/ecofac/api/routes/incidents.py"
src = read(P)
if "def _transition(" not in src:
    die("incidents.py lacks the _transition helper — share this file")

LIST_TMPL = '''@router.get("__PATH__")  # LMF-01-APPLIED
def __NAME__(
    response: Response,
    conn: Annotated[sqlite3.Connection, Depends(get_db)],
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
    status: Annotated[
        str | None, Query(description="Lifecycle status filter")
    ] = None,
) -> list[dict]:
    """Paginated incident history with an optional status filter."""
    where = ""
    params: list[object] = []
    if status is not None and status.strip():
        where = " WHERE i.status = ?"
        params.append(status.strip().upper())
    total = conn.execute(
        "SELECT COUNT(*) AS n FROM incidents i" + where, params
    ).fetchone()["n"]
    rows = conn.execute(
        """
        SELECT i.id, i.anomaly_id, i.status, i.resolution_notes,
               i.dismissal_reason, i.created_at, i.updated_at,
               a.building_id, a.meter_id, a.severity, a.detector_name,
               a.timestamp_utc AS observation_timestamp
          FROM incidents i
          JOIN anomalies a ON a.id = i.anomaly_id
        """
        + where
        + " ORDER BY i.created_at DESC, i.id DESC LIMIT ? OFFSET ?",
        [*params, limit, offset],
    ).fetchall()
    response.headers["X-Total-Count"] = str(total)
    return [dict(row) for row in rows]
'''

if "LMF-01-APPLIED" in src:
    print("  list endpoint already patched — skipping")
else:
    span = find_endpoint(
        src, "get", lambda p: p in ("", "/", "/incidents", "incidents")
    )
    if span is None:
        die("could not locate the GET incident-list endpoint; "
            "share src/ecofac/api/routes/incidents.py")
    start, end, name, path = span
    print(f"  replacing list handler '{name}' @ '{path}' (lines {start}-{end})")
    src = replace_lines(
        src, start, end,
        LIST_TMPL.replace("__NAME__", name).replace("__PATH__", path),
    )

# v2.2 resolve: payload is passed to _transition as the Pydantic model
# (attribute access inside _transition), and blank reasons are rejected.
RESOLVE_TMPL = '''class LMFResolveBody(BaseModel):
    """Payload for resolving an incident; the reason is mandatory."""

    reason: str = Field(min_length=1, max_length=2000)


@router.post("__PATH__")  # LMF-02-APPLIED-V2
def __NAME__(
    incident_id: str,
    conn: Annotated[sqlite3.Connection, Depends(get_db)],
    _auth: Annotated[None, Depends(require_write_access)],
    body: LMFResolveBody,
) -> dict:
    """Move an incident to RESOLVED; a non-empty reason is required."""
    reason = body.reason.strip()
    if not reason:
        raise HTTPException(
            status_code=422, detail="A non-blank reason is required"
        )
    try:
        result = _transition(conn, incident_id, "RESOLVED", body)
    except IncidentNotFoundError as exc:
        raise HTTPException(
            status_code=404, detail="Incident not found"
        ) from exc
    conn.execute(
        "UPDATE incidents SET resolution_notes = ? WHERE id = ?",
        (reason, incident_id),
    )
    conn.commit()
    return result if isinstance(result, dict) else dict(result)
'''

if "LMF-02-APPLIED-V2" in src:
    print("  resolve endpoint already at v2 — skipping")
else:
    # Drop any v1 LMFResolveBody class so the re-replacement stays clean.
    src = re.sub(
        r"class LMFResolveBody\(BaseModel\):.*?(?=\n@router\.)",
        "", src, flags=re.DOTALL,
    )
    span = find_endpoint(src, "post", lambda p: p.endswith("/resolve"))
    if span is None:
        die("could not locate POST /{incident_id}/resolve; "
            "share src/ecofac/api/routes/incidents.py")
    start, end, name, path = span
    print(f"  replacing resolve handler '{name}' @ '{path}' "
          f"(lines {start}-{end})")
    src = replace_lines(
        src, start, end,
        RESOLVE_TMPL.replace("__NAME__", name).replace("__PATH__", path),
    )
    src = re.sub(r"\n{4,}", "\n\n\n", src)

src = ensure_from_imports(
    src, "fastapi", ["Query", "Response", "HTTPException", "Depends"]
)
src = ensure_from_imports(src, "typing", ["Annotated"])
src = ensure_from_imports(src, "pydantic", ["BaseModel", "Field"])
src = ensure_from_imports(
    src, "ecofac.incidents.lifecycle", ["IncidentNotFoundError"]
)
src = ensure_from_imports(
    src, "ecofac.api.dependencies", ["get_db", "require_write_access"]
)
src = ensure_plain_import(src, "sqlite3")

ast.parse(src)
write(P, src)
print("  incidents.py patched OK")
PY

# ---------------------------------------------------------------------------
# LMF-04 — test-drive endpoint: guard, fallback append only if missing
# ---------------------------------------------------------------------------
log "LMF-04: checking test-drive route in $WORLD_ROUTES ..."
WORLD_ROUTES="$WORLD_ROUTES" python3 - <<'PY'
import ast
import inspect
import os
import pprint
import sys

sys.path.insert(0, os.environ["LMF_TMP"])
from patchutil import (  # noqa: E402
    die, ensure_from_imports, ensure_plain_import, find_endpoint, read, write,
)

P = os.environ["WORLD_ROUTES"]
src = read(P)

span = find_endpoint(
    src, "get", lambda p: "test-drive" in p and "{preset_id}" in p
)
if span is not None and span[3] == "/test-drive/{preset_id}":
    print(f"  GET /test-drive/{{preset_id}} already present "
          f"(handler '{span[2]}') — no backend change needed")
    raise SystemExit(0)
if span is not None:
    print(f"  found test-drive route at unexpected path '{span[3]}' — "
          "appending canonical route as fallback")
if "LMF-04-APPLIED" in src:
    print("  fallback already appended — skipping")
    raise SystemExit(0)

from ecofac.api.schemas import ScenarioCreateRequest  # noqa: E402
from ecofac.decision import scenarios as scmod  # noqa: E402

btr = getattr(scmod, "build_transparent_request", None)
if btr is None:
    die("build_transparent_request not found")
sig = inspect.signature(btr)
if len(sig.parameters) != 1:
    die(f"build_transparent_request unexpected signature {sig}")
p0 = next(iter(sig.parameters.values()))
use_model = p0.annotation is ScenarioCreateRequest or (
    "ScenarioCreateRequest" in str(p0.annotation)
)

fields = getattr(ScenarioCreateRequest, "model_fields", None)
if fields is None:
    fields = dict(getattr(ScenarioCreateRequest, "__fields__", {}))
declared = set(fields.keys())
for must in ("constraints", "it_load_kw", "cooling_load_kw", "pue",
             "proposed_it_delta_kw", "proposed_cooling_delta_kw"):
    if must not in declared:
        die(f"ScenarioCreateRequest lacks '{must}' — adapt presets manually")

BASE = {
    "name": "world-test-drive",
    "config_version": "lmf-test-drive",
    "it_load_kw": 600.0,
    "cooling_load_kw": 300.0,
    "pue": 1.4,
    "proposed_it_delta_kw": 60.0,
    "proposed_cooling_delta_kw": 30.0,
    "it_source": "ASSUMED",
    "cooling_source": "ASSUMED",
    "pue_source": "ASSUMED",
    "persist_as_decision": False,
    "constraints": {
        "electrical": {"capacity": 1000.0, "reserve_pct": 0.10},
        "cooling": {"capacity": 800.0, "reserve_pct": 0.10},
    },
}
required = [
    n for n, f in fields.items()
    if (f.is_required() if callable(getattr(f, "is_required", None))
        else getattr(f, "required", False))
]
missing = [r for r in required if r not in BASE]
if missing:
    die(f"ScenarioCreateRequest requires fields not in presets: {missing}")

plus10 = {k: v for k, v in BASE.items() if k in declared}
plus40 = {k: v for k, v in
          dict(BASE, proposed_it_delta_kw=240.0,
               proposed_cooling_delta_kw=120.0).items() if k in declared}
call_arg = "ScenarioCreateRequest(**payload)" if use_model else "payload"

WORLD_TMPL = '''

# --- LMF-04-APPLIED: fallback Test Drive endpoint (GET, no persistence) -----
_LMF_TEST_DRIVE_PRESETS: dict[str, dict[str, object]] = {
    "plus10": __PLUS10__,
    "plus40": __PLUS40__,
}


def _lmf_build_test_drive_request(
    payload: dict,
) -> TransparentDecisionRequest:
    return build_transparent_request(__CALL_ARG__)


@router.get("/test-drive/{preset_id}")
def world_test_drive_endpoint(preset_id: str) -> dict:
    """Evaluate a canned test-drive preset without persisting anything."""
    preset = _LMF_TEST_DRIVE_PRESETS.get(preset_id)
    if preset is None:
        raise HTTPException(
            status_code=404,
            detail=f"Unknown test-drive preset: {preset_id}",
        )
    request = _lmf_build_test_drive_request(dict(preset))
    result = decide_transparently(request)
    if hasattr(result, "model_dump"):
        data = result.model_dump(mode="json")
    elif hasattr(result, "dict"):
        data = result.dict()
    else:
        data = dict(result)
    return {"preset_id": preset_id, "persisted": False, "result": data}
'''
src += (
    WORLD_TMPL
    .replace("__PLUS10__", pprint.pformat(plus10, width=60, sort_dicts=False))
    .replace("__PLUS40__", pprint.pformat(plus40, width=60, sort_dicts=False))
    .replace("__CALL_ARG__", call_arg)
)
src = ensure_from_imports(src, "fastapi", ["HTTPException"])
src = ensure_from_imports(src, "ecofac.decision.transparent",
                          ["decide_transparently", "TransparentDecisionRequest"])
src = ensure_from_imports(src, "ecofac.decision.scenarios",
                          ["build_transparent_request"])
if use_model:
    src = ensure_from_imports(src, "ecofac.api.schemas",
                              ["ScenarioCreateRequest"])
src = ensure_plain_import(src, "sqlite3")

ast.parse(src)
write(P, src)
print("  fallback test-drive endpoint appended")
PY

# ---------------------------------------------------------------------------
# LMF-08 — Re-analyse + run manifest (dashboard routes) — v2: correct prefix
# ---------------------------------------------------------------------------
log "LMF-08: adding /reanalyze + /run-manifest to $DASH_ROUTES ..."
DASH_ROUTES="$DASH_ROUTES" python3 - <<'PY'
import ast
import os
import sys

sys.path.insert(0, os.environ["LMF_TMP"])
from patchutil import (  # noqa: E402
    die, ensure_from_imports, ensure_plain_import, read, write,
)

P = os.environ["DASH_ROUTES"]
src = read(P)
if "LMF-08-APPLIED-V2" in src:
    print("  already patched (v2) — skipping")
    raise SystemExit(0)

# Migrate the v1 block (it used the wrong route prefix -> 404s).
idx = src.find("\n\n# --- LMF-08-APPLIED")
if idx != -1:
    src = src[:idx] + "\n"
    print("  removed v1 block (wrong route prefix)")

# Detect the router's path convention from sibling routes: this codebase
# registers resource prefixes inside the path (e.g. "/incidents/...",
# "/dashboard/stats"), with routers mounted at /api/v1.
tree = ast.parse(src)
paths = []
for node in ast.walk(tree):
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        for dec in node.decorator_list:
            if (isinstance(dec, ast.Call)
                    and isinstance(dec.func, ast.Attribute)
                    and dec.func.attr in ("get", "post")
                    and dec.args
                    and isinstance(dec.args[0], ast.Constant)
                    and isinstance(dec.args[0].value, str)):
                paths.append(dec.args[0].value)
if not paths:
    die(f"no routes found in {P} — share this file")
prefix = "/dashboard" if any(p.startswith("/dashboard/") for p in paths) else ""
print(f"  sibling route paths: {paths}")
print(f"  using route prefix: '{prefix}'")

if "analyze_all" not in src:
    die(f"{P} does not import analyze_all — share this file")

DASH_TMPL = '''

# --- LMF-08-APPLIED-V2: separate Re-analyse action + run manifest -----------
@router.post(
    "__PREFIX__/reanalyze", dependencies=[Depends(require_write_access)]
)
def reanalyze_endpoint(
    conn: Annotated[sqlite3.Connection, Depends(get_db)],
) -> dict:
    """Re-run anomaly analysis over existing readings (no new data)."""
    result = analyze_all(conn)
    summary = result[0] if isinstance(result, tuple) and result else result
    conn.commit()
    return {
        "status": "reanalyzed",
        "anomalies_detected": getattr(summary, "anomalies_detected", None),
        "anomalies_gated": getattr(summary, "anomalies_gated", None),
        "skipped": getattr(summary, "skipped", None),
        "incidents_created": getattr(summary, "incidents_created", None),
    }


@router.get("__PREFIX__/run-manifest")
def run_manifest_endpoint(
    conn: Annotated[sqlite3.Connection, Depends(get_db)],
) -> dict:
    """Live manifest of the current run: what is in the database now."""
    counts = {
        "buildings": conn.execute(
            "SELECT COUNT(*) AS n FROM buildings"
        ).fetchone()["n"],
        "meters": conn.execute(
            "SELECT COUNT(*) AS n FROM meters"
        ).fetchone()["n"],
        "readings": conn.execute(
            "SELECT COUNT(*) AS n FROM energy_readings"
        ).fetchone()["n"],
        "data_quality_issues": conn.execute(
            "SELECT COUNT(*) AS n FROM data_quality_issues"
        ).fetchone()["n"],
        "anomalies": conn.execute(
            "SELECT COUNT(*) AS n FROM anomalies"
        ).fetchone()["n"],
        "incidents": conn.execute(
            "SELECT COUNT(*) AS n FROM incidents"
        ).fetchone()["n"],
        "decisions": conn.execute(
            "SELECT COUNT(*) AS n FROM decisions"
        ).fetchone()["n"],
        "scenarios": conn.execute(
            "SELECT COUNT(*) AS n FROM scenarios"
        ).fetchone()["n"],
    }
    latest_reading = conn.execute(
        "SELECT MAX(timestamp_utc) AS ts FROM energy_readings"
    ).fetchone()["ts"]
    latest_detection = conn.execute(
        "SELECT MAX(detection_timestamp) AS ts FROM anomalies"
    ).fetchone()["ts"]
    latest_incident = conn.execute(
        "SELECT MAX(created_at) AS ts FROM incidents"
    ).fetchone()["ts"]
    return {
        "generated_at": now_z(),
        "counts": counts,
        "latest_reading_timestamp": latest_reading,
        "latest_detection_timestamp": latest_detection,
        "latest_incident_at": latest_incident,
    }
'''

src += DASH_TMPL.replace("__PREFIX__", prefix)
src = ensure_from_imports(src, "ecofac.detection.batch", ["analyze_all"])
src = ensure_from_imports(src, "ecofac.timeutil", ["now_z"])
src = ensure_from_imports(src, "ecofac.api.dependencies",
                          ["get_db", "require_write_access"])
src = ensure_from_imports(src, "fastapi", ["Depends"])
src = ensure_from_imports(src, "typing", ["Annotated"])
src = ensure_plain_import(src, "sqlite3")

ast.parse(src)
write(P, src)
print("  dashboard routes patched OK (uses existing analyze_all)")
PY

# ---------------------------------------------------------------------------
# Frontend — dashboard ($TEMPLATES_DIR/index.html)
# ---------------------------------------------------------------------------
log "LMF frontend: patching $TEMPLATES_DIR/index.html ..."
TEMPLATES_DIR="$TEMPLATES_DIR" python3 - <<'PY'
import os
import sys

sys.path.insert(0, os.environ["LMF_TMP"])
from patchutil import die, insert_before, read, write  # noqa: E402

P = os.path.join(os.environ["TEMPLATES_DIR"], "index.html")
html = read(P)
if "<!-- LMF-INDEX-ADDITIONS -->" in html:
    print("  already patched — skipping")
    raise SystemExit(0)

ADDITIONS = '''
<!-- LMF-INDEX-ADDITIONS -->
<!-- LMF-03/06: facility severity from API max_severity + label map -->
<script id="lmf-severity-labels">
(function () {
  "use strict";
  var SEV_LABELS = ["OK", "LOW", "MEDIUM", "HIGH"];
  window.lmfLabel = window.lmfLabel || function (key) {
    var map = {
      it_load: "IT Load", cooling_load: "Cooling Load", pue: "PUE",
      electrical: "Electrical", cooling: "Cooling",
      facility_power_kw: "Facility Power",
      predicted_it_load_kw: "Predicted IT Load",
      hard_limit_violation: "Hard limit violated",
      reserve_violation: "Reserve margin violated",
      data_quality_ineligible: "Data quality gate",
      rolling_baseline: "Rolling baseline", robust_mad: "Robust MAD",
      APPROVE: "Approved", REJECT: "Rejected", REVIEW: "Needs review",
      DETECTED: "Detected", UNDER_REVIEW: "Under review",
      ACKNOWLEDGED: "Acknowledged", DISMISSED: "Dismissed",
      RESOLVED: "Resolved", UNRESOLVED: "Unresolved",
      low: "Low", medium: "Medium", high: "High"
    };
    return Object.prototype.hasOwnProperty.call(map, key) ? map[key] : key;
  };
  fetch("/api/v1/world/state")
    .then(function (r) { return r.ok ? r.json() : null; })
    .then(function (world) {
      if (!world) { return; }
      var maxSev = 0;
      (world.buildings || []).forEach(function (b) {
        (b.meters || []).forEach(function (m) {
          maxSev = Math.max(maxSev, m.max_severity || 0);
        });
      });
      window.lmfFacilityMaxSeverity = maxSev;
      var label = SEV_LABELS[maxSev] || "UNKNOWN";
      ["facility-severity", "facilitySeverity", "severity-badge",
       "overview-severity", "lmf-facility-severity"].forEach(function (id) {
        var el = document.getElementById(id);
        if (!el) { return; }
        try {
          el.textContent = label;
          el.setAttribute("data-severity", String(maxSev));
          el.className = String(el.className || "")
            .replace(/sev-\\w+/g, "").trim() + " sev-" + maxSev;
        } catch (e) { /* odd element types: ignore */ }
      });
    })
    .catch(function () { /* keep existing rendering */ });
})();
</script>

<!-- LMF-01/02: full incident history with pagination + status filter -->
<section id="lmf-incident-history" aria-label="Incident history">
  <h2>Incident History
    <span id="lmf-facility-severity" class="badge" data-severity=""></span>
  </h2>
  <div class="lmf-toolbar">
    <label>Status
      <select id="lmf-inc-status">
        <option value="">All</option>
        <option>DETECTED</option><option>UNDER_REVIEW</option>
        <option>ACKNOWLEDGED</option><option>DISMISSED</option>
        <option>RESOLVED</option><option>UNRESOLVED</option>
      </select>
    </label>
    <label>Page size
      <select id="lmf-inc-limit">
        <option>10</option><option selected>25</option>
        <option>50</option><option>100</option>
      </select>
    </label>
    <span id="lmf-inc-total" class="muted"></span>
  </div>
  <table>
    <thead><tr>
      <th>Created</th><th>Incident</th><th>Meter</th><th>Detector</th>
      <th>Severity</th><th>Status</th><th>Notes</th><th>Actions</th>
    </tr></thead>
    <tbody id="lmf-inc-body"><tr><td colspan="8">Loading…</td></tr></tbody>
  </table>
  <div class="lmf-pager">
    <button id="lmf-inc-prev" type="button">Previous</button>
    <span id="lmf-inc-page" class="muted"></span>
    <button id="lmf-inc-next" type="button">Next</button>
  </div>
</section>
<script id="lmf-incident-history-js">
(function () {
  "use strict";
  var offset = 0;
  function el(id) { return document.getElementById(id); }
  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;",
               '"': "&quot;", "'": "&#39;" }[c];
    });
  }
  function label(k) { return window.lmfLabel ? window.lmfLabel(k) : k; }
  async function load() {
    var limit = parseInt(el("lmf-inc-limit").value, 10) || 25;
    var status = el("lmf-inc-status").value;
    var url = "/api/v1/incidents?limit=" + limit + "&offset=" + offset +
      (status ? "&status=" + encodeURIComponent(status) : "");
    try {
      var r = await fetch(url);
      var data = await r.json();
      if (!r.ok) { throw new Error(String(r.status)); }
      var total = parseInt(
        r.headers.get("X-Total-Count") || String(data.length), 10);
      el("lmf-inc-total").textContent = total + " incident(s)";
      el("lmf-inc-page").textContent = "Showing " +
        (data.length ? offset + 1 : 0) + "–" + (offset + data.length) +
        " of " + total;
      el("lmf-inc-prev").disabled = offset <= 0;
      el("lmf-inc-next").disabled = offset + data.length >= total;
      var body = el("lmf-inc-body");
      if (!data.length) {
        body.innerHTML = "<tr><td colspan='8'>No incidents.</td></tr>";
        return;
      }
      body.innerHTML = data.map(function (i) {
        var actions = "";
        if (i.status === "ACKNOWLEDGED") {
          actions = "<button class='lmf-resolve' data-id='" + esc(i.id) +
            "' type='button'>Resolve…</button>";
        } else if (i.status === "DETECTED") {
          actions = "<button class='lmf-review' data-id='" + esc(i.id) +
            "' type='button'>Review</button>";
        } else if (i.status === "UNDER_REVIEW") {
          actions = "<button class='lmf-ack' data-id='" + esc(i.id) +
            "' type='button'>Acknowledge</button>";
        }
        return "<tr><td>" + esc(i.created_at) + "</td><td>" + esc(i.id) +
          "</td><td>" + esc(i.meter_id) + "</td><td>" +
          esc(label(i.detector_name)) + "</td><td>" + esc(label(i.severity)) +
          "</td><td>" + esc(label(i.status)) + "</td><td>" +
          esc(i.resolution_notes || i.dismissal_reason || "") +
          "</td><td>" + actions + "</td></tr>";
      }).join("");
    } catch (err) {
      el("lmf-inc-body").innerHTML =
        "<tr><td colspan='8'>Failed to load: " + esc(err.message) +
        "</td></tr>";
    }
  }
  async function act(id, verb, withReason) {
    var payload = {};
    if (withReason) {
      var reason = window.prompt("Resolution reason (required):");
      if (reason === null) { return; }
      reason = reason.trim();
      if (!reason) {
        window.alert("A reason is required to resolve an incident.");
        return;
      }
      payload.reason = reason;
    }
    try {
      var r = await fetch(
        "/api/v1/incidents/" + encodeURIComponent(id) + "/" + verb, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload),
        });
      var data = await r.json().catch(function () { return {}; });
      if (!r.ok) {
        window.alert(verb + " failed: " +
          (data.detail ? JSON.stringify(data.detail) : String(r.status)));
        return;
      }
      load();
    } catch (err) {
      window.alert(verb + " failed: " + err.message);
    }
  }
  el("lmf-inc-body").addEventListener("click", function (e) {
    var t = e.target;
    if (!t || !t.dataset) { return; }
    if (t.classList.contains("lmf-resolve")) {
      act(t.dataset.id, "resolve", true);
    } else if (t.classList.contains("lmf-review")) {
      act(t.dataset.id, "review", false);
    } else if (t.classList.contains("lmf-ack")) {
      act(t.dataset.id, "acknowledge", false);
    }
  });
  el("lmf-inc-prev").addEventListener("click", function () {
    var limit = parseInt(el("lmf-inc-limit").value, 10) || 25;
    offset = Math.max(0, offset - limit);
    load();
  });
  el("lmf-inc-next").addEventListener("click", function () {
    var limit = parseInt(el("lmf-inc-limit").value, 10) || 25;
    offset += limit;
    load();
  });
  el("lmf-inc-status").addEventListener("change",
    function () { offset = 0; load(); });
  el("lmf-inc-limit").addEventListener("change",
    function () { offset = 0; load(); });
  window.lmfRefreshIncidents = load;
  load();
})();
</script>

<!-- LMF-07: Current Readings panel -->
<section id="lmf-current-readings" aria-label="Current readings">
  <h2>Current Readings</h2>
  <p class="muted">Latest reading per meter. kWh readings are converted to
    average kW over the meter interval.</p>
  <table>
    <thead><tr>
      <th>Meter</th><th>Building</th><th>Reading</th>
      <th>Timestamp</th><th>Interval</th><th>Average kW</th>
    </tr></thead>
    <tbody id="lmf-current-readings-body">
      <tr><td colspan="6">Loading…</td></tr>
    </tbody>
  </table>
</section>
<script id="lmf-current-readings-js">
(function () {
  "use strict";
  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;",
               '"': "&quot;", "'": "&#39;" }[c];
    });
  }
  function fmt(n, d) { return Number(n).toFixed(d === undefined ? 1 : d); }
  fetch("/api/v1/world/state")
    .then(function (r) { return r.ok ? r.json() : null; })
    .then(function (world) {
      var tbody = document.getElementById("lmf-current-readings-body");
      if (!tbody) { return; }
      if (!world) {
        tbody.innerHTML = "<tr><td colspan='6'>Unavailable</td></tr>";
        return;
      }
      var rows = [];
      (world.buildings || []).forEach(function (b) {
        (b.meters || []).forEach(function (m) {
          var lr = m.latest_reading;
          var reading = "No reading", ts = "—", avg = "—";
          if (lr && lr.value !== null && lr.value !== undefined) {
            reading = fmt(lr.value) + " " + esc(m.unit || "");
            if (lr.is_valid === 0) { reading += " (invalid)"; }
            ts = esc(lr.timestamp_utc || "—");
            var unit = String(m.unit || "").toLowerCase();
            if (unit === "kwh" && m.interval_minutes > 0) {
              avg = fmt(lr.value / (m.interval_minutes / 60.0), 2) + " kW";
            } else if (unit === "kw") {
              avg = fmt(lr.value, 2) + " kW";
            }
          }
          rows.push("<tr><td>" + esc(m.id) + "</td><td>" +
            esc(b.name || b.id) + "</td><td>" + reading + "</td><td>" +
            ts + "</td><td>" +
            (m.interval_minutes != null
              ? esc(m.interval_minutes) + " min" : "—") +
            "</td><td>" + avg + "</td></tr>");
        });
      });
      tbody.innerHTML = rows.length ? rows.join("") :
        "<tr><td colspan='6'>No meters</td></tr>";
    })
    .catch(function () {
      var tbody = document.getElementById("lmf-current-readings-body");
      if (tbody) {
        tbody.innerHTML = "<tr><td colspan='6'>Unavailable</td></tr>";
      }
    });
})();
</script>

<!-- LMF-08: separate Generate New Run / Re-analyse + run manifest -->
<section id="lmf-demo-controls" aria-label="Demo data controls">
  <h2>Demo Data</h2>
  <button id="lmf-btn-generate" type="button">Generate New Run</button>
  <button id="lmf-btn-reanalyze" type="button">Re-analyse</button>
  <span id="lmf-demo-status" class="muted" role="status"></span>
  <pre id="lmf-run-manifest" class="muted">Manifest not loaded.</pre>
</section>
<script id="lmf-demo-controls-js">
(function () {
  "use strict";
  function el(id) { return document.getElementById(id); }
  function say(msg) { el("lmf-demo-status").textContent = msg; }
  async function loadManifest() {
    try {
      var r = await fetch("/api/v1/dashboard/run-manifest");
      if (!r.ok) { throw new Error(String(r.status)); }
      var m = await r.json();
      el("lmf-run-manifest").textContent = JSON.stringify(m, null, 2);
    } catch (err) {
      el("lmf-run-manifest").textContent =
        "Manifest unavailable: " + err.message;
    }
  }
  async function run(kind, url) {
    var btn = el(kind === "generate"
      ? "lmf-btn-generate" : "lmf-btn-reanalyze");
    var original = btn.textContent;
    btn.disabled = true;
    btn.textContent = "Running…";
    say("Running " + kind + "…");
    try {
      var r = await fetch(url, { method: "POST" });
      var data = await r.json().catch(function () { return {}; });
      if (!r.ok) {
        throw new Error(data.detail ? JSON.stringify(data.detail)
                                    : String(r.status));
      }
      say(kind + " finished.");
      await loadManifest();
      if (window.lmfRefreshIncidents) { window.lmfRefreshIncidents(); }
    } catch (err) {
      say(kind + " failed: " + err.message);
    } finally {
      btn.disabled = false;
      btn.textContent = original;
    }
  }
  el("lmf-btn-generate").addEventListener("click", function () {
    run("generate", "/api/v1/dashboard/generate-demo");
  });
  el("lmf-btn-reanalyze").addEventListener("click", function () {
    run("reanalyze", "/api/v1/dashboard/reanalyze");
  });
  loadManifest();
})();
</script>
'''

new_html, inserted = insert_before(
    html, "</main>", ADDITIONS, "<!-- LMF-INDEX-ADDITIONS -->")
if not inserted:
    new_html, inserted = insert_before(
        html, "</body>", ADDITIONS, "<!-- LMF-INDEX-ADDITIONS -->")
if not inserted:
    die(f"{P} has neither </main> nor </body> — share the file")
write(P, new_html)
print("  index.html patched OK")
PY

# ---------------------------------------------------------------------------
# LMF-04/05 frontend — world.html
# ---------------------------------------------------------------------------
log "LMF-04/05: patching $TEMPLATES_DIR/world.html ..."
TEMPLATES_DIR="$TEMPLATES_DIR" python3 - <<'PY'
import os
import sys

sys.path.insert(0, os.environ["LMF_TMP"])
from patchutil import die, insert_before, read, write  # noqa: E402

P = os.path.join(os.environ["TEMPLATES_DIR"], "world.html")
html = read(P)

if 'id="lmf-record-decision"' not in html:
    idx = html.find('id="btn-eval-plus10"')
    if idx == -1:
        die(f'btn-eval-plus10 not found in {P} — share the file')
    tag_start = html.rfind("<", 0, idx)
    checkbox = (
        '<label for="lmf-record-decision" '
        'style="display:block;margin:0.5rem 0;">\n'
        '  <input type="checkbox" id="lmf-record-decision">\n'
        "  Also record in Decision Center\n"
        "</label>\n"
    )
    html = html[:tag_start] + checkbox + html[tag_start:]
    print("  checkbox inserted before btn-eval-plus10")
else:
    print("  checkbox already present — skipping")

SCRIPT = '''
<!-- LMF-WORLD-ADDITIONS -->
<script id="lmf-testdrive">
(function () {
  "use strict";
  var PRESETS = {
    plus10: { it: 600, cool: 300, pue: 1.4, dit: 60, dcool: 30 },
    plus40: { it: 600, cool: 300, pue: 1.4, dit: 240, dcool: 120 }
  };
  var CONSTRAINTS = {
    electrical: { capacity: 1000, reserve_pct: 0.10 },
    cooling: { capacity: 800, reserve_pct: 0.10 }
  };
  function el(id) { return document.getElementById(id); }
  function say(msg) {
    var m = el("action-msg");
    if (m) { m.textContent = msg; }
  }
  function scenarioBody(presetId) {
    var p = PRESETS[presetId];
    return {
      name: "test-drive-" + presetId,
      it_load_kw: p.it, cooling_load_kw: p.cool, pue: p.pue,
      proposed_it_delta_kw: p.dit, proposed_cooling_delta_kw: p.dcool,
      constraints: CONSTRAINTS,
      config_version: "lmf-test-drive",
      it_source: "ASSUMED", cooling_source: "ASSUMED", pue_source: "ASSUMED",
      persist_as_decision: false
    };
  }
  window.lmfRunTestDrive = async function (presetId) {
    if (!PRESETS[presetId]) { say("Unknown preset " + presetId); return; }
    say("Evaluating " + presetId + "…");
    var record = el("lmf-record-decision");
    try {
      var resp, data;
      if (record && record.checked) {
        var body = scenarioBody(presetId);
        body.persist_as_decision = true;
        resp = await fetch("/api/v1/scenarios/evaluate", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body),
        });
        data = await resp.json();
        if (!resp.ok) {
          throw new Error(data.detail ? JSON.stringify(data.detail)
                                      : String(resp.status));
        }
        say("Recorded in Decision Center: " +
            (data.decision_id || data.scenario_id || "ok"));
      } else {
        resp = await fetch(
          "/api/v1/world/test-drive/" + encodeURIComponent(presetId));
        data = await resp.json();
        if (!resp.ok) {
          throw new Error(data.detail ? JSON.stringify(data.detail)
                                      : String(resp.status));
        }
        var r = data.result || data;
        var status = r.final_status || r.status || "EVALUATED";
        say(presetId + ": " + status + " (via server test-drive endpoint)");
      }
      if (window.lmfRefreshEvents) { window.lmfRefreshEvents(); }
    } catch (err) {
      say("Test drive failed: " + err.message);
    }
  };
  // Replace buttons with clones: drops ALL previous listeners, including
  // any legacy client-side meter-reading averaging logic.
  function hijack(id, preset) {
    var b = el(id);
    if (!b) { return; }
    var nb = b.cloneNode(true);
    b.parentNode.replaceChild(nb, b);
    nb.onclick = function (e) {
      e.preventDefault();
      window.lmfRunTestDrive(preset);
    };
  }
  hijack("btn-eval-plus10", "plus10");
  hijack("btn-eval-plus40", "plus40");
})();
</script>
'''
html, inserted = insert_before(
    html, "</body>", SCRIPT, "<!-- LMF-WORLD-ADDITIONS -->")
if not inserted:
    print("  test-drive script already present — skipping")
write(P, html)
print("  world.html patched OK")
PY

# ---------------------------------------------------------------------------
# LMF-06 — label overlay on decisions + scenarios pages
# ---------------------------------------------------------------------------
log "LMF-06: adding label overlay to decisions.html + scenarios.html ..."
TEMPLATES_DIR="$TEMPLATES_DIR" python3 - <<'PY'
import os
import sys

sys.path.insert(0, os.environ["LMF_TMP"])
from patchutil import die, insert_before, read, write  # noqa: E402

OVERLAY = '''
<!-- LMF-LABEL-OVERLAY -->
<script id="lmf-label-overlay">
(function () {
  "use strict";
  window.lmfLabel = window.lmfLabel || function (key) {
    var map = {
      it_load: "IT Load", cooling_load: "Cooling Load", pue: "PUE",
      electrical: "Electrical", cooling: "Cooling",
      facility_power_kw: "Facility Power",
      current_it_load_kw: "Current IT Load",
      predicted_it_load_kw: "Predicted IT Load",
      current_facility_power_kw: "Current Facility Power",
      predicted_facility_power_kw: "Predicted Facility Power",
      headroom_to_permitted_kw: "Headroom to Permitted",
      headroom_to_physical_kw: "Headroom to Physical",
      hard_limit_violation: "Hard limit violated",
      reserve_violation: "Reserve margin violated",
      data_quality_ineligible: "Data quality gate",
      APPROVE: "Approved", REJECT: "Rejected", REVIEW: "Needs review",
      MEASURED: "Measured", ASSUMED: "Assumed", DERIVED: "Derived",
      low: "Low", medium: "Medium", high: "High"
    };
    return Object.prototype.hasOwnProperty.call(map, key) ? map[key] : key;
  };
  var SKIP = { SCRIPT: 1, STYLE: 1, TEXTAREA: 1, INPUT: 1, SELECT: 1,
               OPTION: 1, CODE: 1, PRE: 1 };
  function fixNode(node) {
    var t = (node.nodeValue || "").trim();
    if (!t) { return; }
    var pretty = window.lmfLabel(t);
    if (pretty !== t) { node.nodeValue = node.nodeValue.replace(t, pretty); }
  }
  function walk(root) {
    var walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, {
      acceptNode: function (n) {
        var p = n.parentElement;
        if (!p || SKIP[p.tagName]) { return NodeFilter.FILTER_REJECT; }
        return NodeFilter.FILTER_ACCEPT;
      }
    });
    var n;
    while ((n = walker.nextNode())) { fixNode(n); }
  }
  function start() {
    walk(document.body);
    var mo = new MutationObserver(function (muts) {
      muts.forEach(function (m) {
        if (m.type === "childList") {
          m.addedNodes.forEach(function (added) {
            if (added.nodeType === 1) { walk(added); }
          });
        } else if (m.type === "characterData") {
          fixNode(m.target);
        }
      });
    });
    mo.observe(document.body, { childList: true, subtree: true,
                                characterData: true });
  }
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", start);
  } else {
    start();
  }
})();
</script>
'''

tdir = os.environ["TEMPLATES_DIR"]
for name in ("decisions.html", "scenarios.html"):
    P = os.path.join(tdir, name)
    html = read(P)
    new_html, inserted = insert_before(
        html, "</body>", OVERLAY, "<!-- LMF-LABEL-OVERLAY -->")
    if not inserted:
        if "<!-- LMF-LABEL-OVERLAY -->" in html:
            print(f"  {name}: already patched — skipping")
            continue
        die(f"{P} has no </body> — share the file")
    write(P, new_html)
    print(f"  {name}: overlay added")
PY

# ---------------------------------------------------------------------------
# LMF-09 — CI permissions
# ---------------------------------------------------------------------------
log "LMF-09: patching .github/workflows/ci.yml ..."
python3 - <<'PY'
import pathlib
import re

P = pathlib.Path(".github/workflows/ci.yml")
src = P.read_text(encoding="utf-8")
BLOCK = "permissions:\n  contents: write\n  pull-requests: write\n"

if re.search(r"^permissions:\s*$", src, re.M):
    new = re.sub(r"^permissions:\n(?:[ \t]+.*\n?)*", BLOCK, src,
                 count=1, flags=re.M)
    action = "replaced existing permissions block"
else:
    lines = src.splitlines(keepends=True)
    idx = next((j for j, line in enumerate(lines)
                if re.match(r"^jobs:", line)), None)
    if idx is None:
        raise SystemExit("ci.yml: could not find 'jobs:' to insert permissions")
    lines.insert(idx, BLOCK)
    new = "".join(lines)
    action = "inserted permissions block before jobs:"

if new != src:
    P.write_text(new, encoding="utf-8")
print(f"  ci.yml: {action}")
PY

# ---------------------------------------------------------------------------
# Housekeeping — keep backups and temp dirs out of git
# ---------------------------------------------------------------------------
log "Ensuring .gitignore covers LMF scratch dirs ..."
touch .gitignore
for pat in ".lmf-backup-*/" ".lmf-tmp-*/"; do
  if ! grep -qxF "$pat" .gitignore; then
    printf '%s\n' "$pat" >> .gitignore
    log "  added $pat to .gitignore"
  else
    log "  $pat already ignored"
  fi
done

# ---------------------------------------------------------------------------
# Regression tests
# ---------------------------------------------------------------------------
log "Writing tests/api/test_last_moment_fixes.py ..."
mkdir -p tests/api
cat > tests/api/test_last_moment_fixes.py <<'TESTEOF'
# Copyright 2026 Lightibulb
"""Regression tests for the last-moment fix bundle (LMF-01 .. LMF-08)."""

INC = "/api/v1/incidents"
WORLD_TD = "/api/v1/world/test-drive"
DASH = "/api/v1/dashboard"


def _mk_incident(test_db, idx, status="DETECTED"):
    ts = f"2026-02-01T{idx:02d}:00:00Z"
    aid = f"lmf-an-{idx}"
    iid = f"lmf-inc-{idx}"
    test_db.execute(
        "INSERT INTO anomalies (id, building_id, meter_id, timestamp_utc, "
        "observed_consumption, expected_consumption, absolute_deviation, "
        "percentage_deviation, detector_name, severity, "
        "is_data_quality_eligible, detection_timestamp) "
        "VALUES (?, 'b1', 'm1', ?, 100, 10, 90, 900, "
        "'robust_mad', 'high', 1, ?)",
        (aid, ts, ts),
    )
    test_db.execute(
        "INSERT INTO incidents (id, anomaly_id, status, created_at, "
        "updated_at) VALUES (?, ?, ?, ?, ?)",
        (iid, aid, status, ts, ts),
    )
    return iid


def _first_preset_id(client):
    """Discover a real test-drive preset id from the presets endpoint."""
    resp = client.get(f"{WORLD_TD}/presets")
    assert resp.status_code == 200, resp.text
    data = resp.json()
    if isinstance(data, dict):
        data = data.get("presets", data)
    if isinstance(data, list) and data:
        first = data[0]
        if isinstance(first, dict):
            for key in ("id", "preset_id", "name"):
                if key in first:
                    return first[key]
        else:
            return str(first)
    return "plus10"


# --- LMF-01: pagination + status filter -------------------------------------
def test_incident_list_paginates_and_reports_total(client, test_db):
    for i in range(1, 8):
        _mk_incident(test_db, i)
    test_db.commit()
    resp = client.get(INC, params={"limit": 3, "offset": 0})
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 3
    assert int(resp.headers["X-Total-Count"]) >= 7
    page2 = client.get(INC, params={"limit": 3, "offset": 3}).json()
    assert [r["id"] for r in page2] != [r["id"] for r in body]


def test_incident_list_status_filter(client, test_db):
    _mk_incident(test_db, 11, status="RESOLVED")
    test_db.commit()
    body = client.get(INC, params={"status": "RESOLVED", "limit": 100}).json()
    assert body
    assert "lmf-inc-11" in [r["id"] for r in body]
    assert all(r["status"] == "RESOLVED" for r in body)


# --- LMF-02: resolve requires a reason ---------------------------------------
def _to_acknowledged(client, test_db, idx):
    iid = _mk_incident(test_db, idx)
    test_db.commit()
    assert client.post(f"{INC}/{iid}/review", json={}).status_code == 200
    assert client.post(
        f"{INC}/{iid}/acknowledge", json={}).status_code == 200
    return iid


def test_resolve_requires_reason(client, test_db):
    iid = _to_acknowledged(client, test_db, 21)
    assert client.post(f"{INC}/{iid}/resolve", json={}).status_code == 422
    assert client.post(f"{INC}/{iid}/resolve").status_code == 422
    assert client.post(
        f"{INC}/{iid}/resolve", json={"reason": "   "}).status_code == 422
    row = test_db.execute(
        "SELECT status FROM incidents WHERE id = ?", (iid,)
    ).fetchone()
    assert row["status"] == "ACKNOWLEDGED"


def test_resolve_with_reason_persists_notes(client, test_db):
    iid = _to_acknowledged(client, test_db, 22)
    resp = client.post(
        f"{INC}/{iid}/resolve", json={"reason": "Repaired faulty sensor"}
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "RESOLVED"
    row = test_db.execute(
        "SELECT resolution_notes FROM incidents WHERE id = ?", (iid,)
    ).fetchone()
    assert row["resolution_notes"] == "Repaired faulty sensor"


def test_resolve_unknown_incident_404(client):
    resp = client.post(f"{INC}/does-not-exist/resolve", json={"reason": "x"})
    assert resp.status_code == 404


# --- LMF-04: test-drive GET endpoint -----------------------------------------
def test_test_drive_get_preset_available(client):
    preset_id = _first_preset_id(client)
    resp = client.get(f"{WORLD_TD}/{preset_id}")
    assert resp.status_code == 200, resp.text
    assert isinstance(resp.json(), dict)


def test_test_drive_unknown_preset_is_client_error(client):
    resp = client.get(f"{WORLD_TD}/definitely-not-a-preset")
    assert resp.status_code in (400, 404, 422)


def test_test_drive_does_not_record_decision(client, test_db):
    preset_id = _first_preset_id(client)
    before = test_db.execute(
        "SELECT COUNT(*) n FROM decisions").fetchone()["n"]
    assert client.get(f"{WORLD_TD}/{preset_id}").status_code == 200
    after = test_db.execute(
        "SELECT COUNT(*) n FROM decisions").fetchone()["n"]
    assert before == after


# --- LMF-08: re-analyse + run manifest ---------------------------------------
def test_run_manifest_shape(client):
    resp = client.get(f"{DASH}/run-manifest")
    assert resp.status_code == 200
    body = resp.json()
    for key in ("buildings", "meters", "readings", "anomalies", "incidents"):
        assert key in body["counts"]
    assert "generated_at" in body


def test_reanalyze_endpoint(client):
    resp = client.post(f"{DASH}/reanalyze")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "reanalyzed"
    for key in ("anomalies_detected", "anomalies_gated", "skipped"):
        assert key in body


# --- frontend contracts -------------------------------------------------------
def test_world_page_has_record_checkbox(client):
    html = client.get("/world").text
    assert 'id="lmf-record-decision"' in html
    assert "Also record in Decision Center" in html
    assert "/api/v1/world/test-drive/" in html


def test_world_page_test_drive_buttons_intact(client):
    html = client.get("/world").text
    for element_id in ("btn-eval-plus10", "btn-eval-plus40"):
        assert f'id="{element_id}"' in html


def test_dashboard_has_lmf_panels(client):
    html = client.get("/").text
    for element_id in (
        "lmf-incident-history",
        "lmf-current-readings",
        "lmf-demo-controls",
    ):
        assert f'id="{element_id}"' in html
    assert "X-Total-Count" in html
    assert "/api/v1/dashboard/run-manifest" in html


def test_dashboard_severity_uses_max_severity(client):
    html = client.get("/").text
    assert "/api/v1/world/state" in html
    assert "max_severity" in html


def test_label_overlay_on_decision_pages(client):
    for page in ("/decisions", "/scenarios"):
        html = client.get(page).text
        assert 'id="lmf-label-overlay"' in html, page
TESTEOF

# ---------------------------------------------------------------------------
# Verification gates
# ---------------------------------------------------------------------------
echo
log "=== Verification gates ==="

PY_FILES=(
  src/ecofac/api/routes/incidents.py
  "$WORLD_ROUTES"
  "$DASH_ROUTES"
  tests/api/test_last_moment_fixes.py
)

log "Gate 1: python syntax"
python3 -m py_compile "${PY_FILES[@]}" \
  || die "syntax error in a patched module (backup in $BACKUP_DIR)"

if [[ "$HAVE_RUFF" -eq 1 ]]; then
  log "Normalizing patched modules (safe ruff autofixes, e.g. import order)"
  "${RUFF[@]}" check --fix "${PY_FILES[@]}" >/dev/null 2>&1 || true
  python3 -m py_compile "${PY_FILES[@]}" || die "autofix broke syntax"

  log "Gate 2: ruff — no NEW violations vs pre-fix baseline (select=ALL)"
  lint_ok=0
  for attempt in 1 2 3; do
    fail=0
    snap_rules src/ecofac/api/routes/incidents.py after_incidents
    snap_rules "$WORLD_ROUTES" after_world
    snap_rules "$DASH_ROUTES" after_dashboard
    snap_rules tests/api/test_last_moment_fixes.py after_newtest
    for tag in incidents world dashboard newtest; do
      if ! python3 "$LMF_TMP/compare_rules.py" \
            "$RULE_SNAP_DIR/base_$tag.txt" "$RULE_SNAP_DIR/after_$tag.txt"; then
        fail=1
      fi
    done
    if [[ $fail -eq 0 ]]; then lint_ok=1; break; fi
    warn "ruff pass $attempt: new violations — running ruff --fix"
    "${RUFF[@]}" check --fix "${PY_FILES[@]}" >/dev/null 2>&1 || true
    python3 -m py_compile "${PY_FILES[@]}" || die "autofix broke syntax"
  done
  [[ $lint_ok -eq 1 ]] || die "ruff still shows NEW violations — see above"
  log "ruff gate clean"
else
  warn "Gate 2 skipped (ruff unavailable) — run 'ruff check' manually"
fi

log "Gate 3: targeted regression tests"
if ! python3 -m pytest tests/api/test_last_moment_fixes.py -q --tb=short; then
  warn "Targeted tests failed. Likely causes:"
  warn "  - tests assert the pre-fix resolve/list contract (update them —"
  warn "    the new contract is intentional)"
  warn "  - world/test-drive presets endpoint shape differs from expectation"
  die "targeted regression tests failed"
fi
log "targeted tests green"

log "Gate 4: full suite"
if ! python3 -m pytest -q --tb=short; then
  warn "Full suite failed. If tests/integration/test_incidents.py or"
  warn "tests/api/test_audit_regressions.py assert the OLD empty-body resolve"
  warn "behaviour, update them to the new contract (required reason) —"
  warn "that change is intentional per the fix list."
  die "full test suite failed"
fi
log "full suite green"

# ---------------------------------------------------------------------------
echo
log "=== All fixes applied and verified ==="
cat <<'SUMMARY'
  LMF-01  GET /incidents: limit/offset/status + X-Total-Count;
          full-history widget on the dashboard
  LMF-02  POST /incidents/{id}/resolve: 422 unless a non-blank reason is
          provided (Pydantic + blank guard); reason persisted to
          resolution_notes; payload passed to _transition as the model
  LMF-03  Facility severity badge driven by world-state max_severity
  LMF-04  GET /api/v1/world/test-drive/{preset_id} confirmed/restored;
          legacy button handlers replaced (client-side averaging removed)
  LMF-05  "Also record in Decision Center" checkbox (default OFF) records via
          POST /api/v1/scenarios/evaluate with persist_as_decision=true
  LMF-06  Human-readable label map + overlay on decisions/scenarios/dashboard
  LMF-07  Current Readings panel (meter, building, reading, timestamp,
          interval; kWh converted to average kW)
  LMF-08  Generate New Run / Re-analyse split + GET
          /api/v1/dashboard/run-manifest (prefix auto-detected)
  LMF-09  ci.yml permissions: contents write + pull-requests write
SUMMARY

echo
warn "MANUAL STEP (cannot be scripted): GitHub → Settings → Integrations —"
warn "reconnect the App/token for this repo with 'Contents: Read and write'"
warn "and 'Pull requests: Read and write'."
warn ""
warn "Review before merge:"
warn "  - legacy incident panel in index.html is superseded by Incident History"
warn "  - retire any legacy single 'Generate demo' button if present"
log "Backup of pre-fix files: $BACKUP_DIR (git-ignored)"
log "Done. 🍁"
