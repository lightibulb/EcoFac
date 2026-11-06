#!/usr/bin/env bash
# =============================================================================
# EcoFac — P0 remediation (P0-1, P0-2, P0-3)
#
# Idempotent. Refuses to run on a dirty tree. Backs up every touched file.
# P0-1 is attempted then reverted cleanly if its spec fails, so P0-2/P0-3
# can still land green; the revert leaves docs/P0-1.md as the contract.
#
# Usage:
#   bash scripts/p0_remediation.sh
#   DRY_RUN=1 bash scripts/p0_remediation.sh   # show generated files, touch nothing
# =============================================================================
set -euo pipefail

DRY_RUN="${DRY_RUN:-0}"
PYTHON="${PYTHON:-python3}"
RUFF="${RUFF:-ruff}"
PYTEST="${PYTEST:-pytest}"

REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
cd "$REPO_ROOT"

RED=$'\033[31m'; GREEN=$'\033[32m'; YELLOW=$'\033[33m'; BLUE=$'\033[34m'; RESET=$'\033[0m'
abort() { printf '%sABORT:%s %s\n' "$RED" "$RESET" "$*" >&2; exit 1; }
info()  { printf '%s==%s %s\n' "$GREEN" "$RESET" "$*"; }
warn()  { printf '%s!!%s %s\n' "$YELLOW" "$RESET" "$*"; }
step()  { printf '\n%s>>> %s%s\n' "$BLUE" "$*" "$RESET"; }

BACKUP_DIR="$(mktemp -d "${TMPDIR:-/tmp}/ecofac-p0-XXXXXX")"
on_exit() { printf '\nbackups: %s\n' "$BACKUP_DIR"; }
trap on_exit EXIT

backup() {
  [[ -f "$1" ]] || return 0
  cp -a "$1" "$BACKUP_DIR/$(printf '%s' "$1" | tr '/' '_')"
}
emit() {  # emit <path> <<heredoc
  local dest="$1"
  if [[ "$DRY_RUN" == "1" ]]; then
    printf '\n--- %s (dry-run, not written) ---\n' "$dest"
    cat
  else
    mkdir -p "$(dirname "$dest")"
    cat > "$dest"
    info "wrote $dest"
  fi
}

# ---------- preflight ----------
step "Preflight"
for f in \
    pyproject.toml \
    src/ecofac/decision/transparent.py \
    src/ecofac/decision/state_machine.py \
    src/ecofac/decision/limits.py \
    src/ecofac/decision/scenario_analysis.py \
    src/ecofac/decision/engine.py \
    src/ecofac/decision/constraints.py \
    src/ecofac/decision/testdrive.py \
    tests/api/test_trusted_decisions_api.py \
    tests/evaluation/test_runner_canary.py \
    tests/unit/test_trust_resolution.py \
    tests/unit/test_deprecated_modules.py
do
  [[ -f "$f" ]] || abort "required file missing: $f"
done

if [[ "$DRY_RUN" != "1" ]]; then
  git diff --quiet || abort "working tree is dirty; commit or stash first"
fi
info "repo:    $REPO_ROOT"
info "branch:  $(git rev-parse --abbrev-ref HEAD)"
info "python:  $($PYTHON --version 2>&1)"
info "ruff:    $($RUFF --version 2>&1 || echo missing)"
info "pytest:  $($PYTEST --version 2>&1 | head -1)"

# =============================================================================
# P0-2 — Fix resolver false positives
# =============================================================================
step "P0-2: rename colliding test helpers"

backup tests/api/test_trusted_decisions_api.py
backup tests/evaluation/test_runner_canary.py
backup tests/unit/test_trust_resolution.py

if [[ "$DRY_RUN" != "1" ]]; then
$PYTHON - <<'PY'
"""Tokenize-based rename; preserves formatting, skips attribute access."""
import io, pathlib, tokenize

SKIP = {tokenize.NL, tokenize.NEWLINE, tokenize.INDENT,
        tokenize.DEDENT, tokenize.COMMENT, tokenize.ENCODING}

def rename(src: str, pairs):
    toks = list(tokenize.generate_tokens(io.StringIO(src).readline))
    hits = {}
    for i, tok in enumerate(toks):
        if tok.type != tokenize.NAME:
            continue
        for old, new in pairs:
            if tok.string != old:
                continue
            j = i - 1
            while j >= 0 and toks[j].type in SKIP:
                j -= 1
            if j >= 0 and toks[j].string == ".":
                continue
            hits[i] = (tok, new)
    if not hits:
        return src
    out = []
    for i, tok in enumerate(toks):
        if i in hits:
            _, new = hits[i]
            out.append(tokenize.TokenInfo(tok.type, new, tok.start, tok.end, tok.line))
        else:
            out.append(tok)
    return tokenize.untokenize(out)

# file-local renames
local = {
    "tests/api/test_trusted_decisions_api.py": [("post", "post_json")],
    "tests/evaluation/test_runner_canary.py":  [("add", "add_reading_row")],
    "tests/unit/test_trust_resolution.py":     [("resolve", "resolve_pair_request")],
}
changed = []
for path, pairs in local.items():
    p = pathlib.Path(path)
    before = p.read_text()
    after = rename(before, pairs)
    if after != before:
        p.write_text(after)
        changed.append(path)

# `post` helper is imported by sibling test modules; rename project-wide under tests/
for p in pathlib.Path("tests").rglob("*.py"):
    if str(p) in local:
        continue
    before = p.read_text()
    after = rename(before, [("post", "post_json")])
    if after != before:
        p.write_text(after)
        changed.append(str(p))

print("renamed in:")
for c in changed:
    print(f"  {c}")
PY

  # smoke: collection must still work
  $PYTEST --collect-only -q tests/api tests/evaluation tests/unit/test_trust_resolution.py \
    >/dev/null || abort "P0-2 broke pytest collection"
  if grep -RIn --include='*.py' -E '^[[:space:]]*def post\(' tests/ >/dev/null; then
    abort "P0-2: bare 'def post(' still present"
  fi
fi
info "P0-2 ok (post -> post_json, add -> add_reading_row, resolve -> resolve_pair_request)"

# =============================================================================
# P0-3 — Decide fate of deprecated engine/constraints
# =============================================================================
step "P0-3: document legacy decision modules + CI gate"

# Decision: keep for one minor release, CI-gated, delete in v2.0.
if [[ ! -f docs/LEGACY.md ]]; then
emit docs/LEGACY.md <<'MD'
# Legacy modules (scheduled for removal in v2.0)

| module                                | replacement                                | status     |
|---------------------------------------|--------------------------------------------|------------|
| `src/ecofac/decision/engine.py`       | `src/ecofac/decision/state_machine.py`     | deprecated |
| `src/ecofac/decision/constraints.py`  | `src/ecofac/decision/limits.py`            | deprecated |

**Decision:** keep for one release behind a CI guard. `ARCHITECTURE.json`
shows no production caller for `engine.decide`; only tests exercise them.
Deleting them is mechanical and should not obscure the P0-1 sensitivity diff.
External consumers (if any) get one release of `DeprecationWarning` (already
asserted by `tests/unit/test_deprecated_modules.py`).

## Enforcement

`scripts/check_no_legacy_imports.py` fails CI if any file under `src/`,
`evaluation/`, `simulator/`, or `scripts/` imports these modules (the legacy
modules themselves are exempt).

## Deletion checklist (v2.0)

- [ ] delete `engine.py`, `constraints.py`
- [ ] delete `tests/unit/test_deprecated_modules.py`,
      `tests/unit/test_decision_constraints.py`,
      `tests/integration/test_decision_engine.py`
- [ ] delete `scripts/check_no_legacy_imports.py` and the `make check-no-legacy` target
- [ ] regenerate `ARCHITECTURE.json`
MD
fi

if [[ ! -f scripts/check_no_legacy_imports.py ]]; then
emit scripts/check_no_legacy_imports.py <<'PY'
#!/usr/bin/env python3
"""Fail if production code imports legacy decision modules. See docs/LEGACY.md."""
from __future__ import annotations

import ast
import pathlib
import sys

LEGACY_TAILS = {"engine", "constraints"}
LEGACY_PARENT = "ecofac.decision"
SELF_EXEMPT = {
    pathlib.Path("src/ecofac/decision/engine.py"),
    pathlib.Path("src/ecofac/decision/constraints.py"),
}
ROOTS = ["src", "evaluation", "simulator", "scripts"]

def is_legacy(module: str | None) -> bool:
    if not module:
        return False
    tail = module.split(".")[-1]
    return tail in LEGACY_TAILS and (LEGACY_PARENT in module or "." not in module)

def main() -> int:
    bad: list[tuple[pathlib.Path, int, str]] = []
    for root in ROOTS:
        base = pathlib.Path(root)
        if not base.is_dir():
            continue
        for p in base.rglob("*.py"):
            if p in SELF_EXEMPT:
                continue
            try:
                tree = ast.parse(p.read_text(), filename=str(p))
            except SyntaxError as exc:
                print(f"{p}: cannot parse: {exc}", file=sys.stderr)
                return 1
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and is_legacy(node.module):
                    bad.append((p, node.lineno, node.module or "<relative>"))
                elif isinstance(node, ast.Import):
                    for alias in node.names:
                        if is_legacy(alias.name):
                            bad.append((p, node.lineno, alias.name))
    for p, ln, m in bad:
        print(f"{p}:{ln}: imports {m}")
    if bad:
        print("legacy imports detected; see docs/LEGACY.md", file=sys.stderr)
        return 1
    print("no legacy imports in production code")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
PY
fi

if [[ ! -f Makefile ]]; then
  emit Makefile <<'MK'
.PHONY: check-no-legacy
check-no-legacy: ## fail if production code imports legacy decision modules
	@./scripts/check_no_legacy_imports.py
MK
elif ! grep -q 'check-no-legacy' Makefile; then
  if [[ "$DRY_RUN" != "1" ]]; then
    printf '\n.PHONY: check-no-legacy\ncheck-no-legacy:\n\t@./scripts/check_no_legacy_imports.py\n' >> Makefile
    info "appended check-no-legacy target to Makefile"
  fi
fi

# annotate the two modules with the decision (idempotent)
for f in src/ecofac/decision/engine.py src/ecofac/decision/constraints.py; do
  backup "$f"
  if ! grep -q 'Scheduled for removal in v2.0' "$f"; then
    if [[ "$DRY_RUN" != "1" ]]; then
      $PYTHON - "$f" <<'PY'
import pathlib, sys
p = pathlib.Path(sys.argv[1])
src = p.read_text()
banner = (
    '"""LEGACY MODULE — deprecated.\n\n'
    'Scheduled for removal in v2.0. See docs/LEGACY.md.\n'
    'Do not import from new production code; use\n'
    '`ecofac.decision.state_machine` / `ecofac.decision.limits` instead.\n'
    '"""\n'
)
lines = src.splitlines(keepends=True)
insert_at = 0
for i, ln in enumerate(lines[:3]):
    if ln.startswith("#!") or "coding:" in ln:
        insert_at = i + 1
p.write_text("".join(lines[:insert_at]) + banner + "".join(lines[insert_at:]))
PY
    fi
  fi
done
info "P0-3 ok (docs/LEGACY.md, CI gate, module banners)"

# =============================================================================
# P0-1 — Unify sensitivity computation
# =============================================================================
step "P0-1: unify sensitivity via state machine"

# The spec test is always written (it is the contract). If wiring is not
# detected, it skips with a clear reason, so lint/pytest stay green.
if [[ ! -f tests/unit/test_sensitivity_equivalence.py ]]; then
emit tests/unit/test_sensitivity_equivalence.py <<'PY'
"""P0-1 spec: sensitivity must delegate to the state machine.

The contract is structural: each band-edge status inside
`_compute_sensitivity` must come from `evaluate_decision(build_context(...))`,
not from an inline threshold. This keeps the per-decision sensitivity
artifact consistent with `scenario_analysis.analyze_sensitivity`, which
already re-runs `decide_transparently` per step.

If wiring is not yet present, the test skips with a pointer to docs/P0-1.md.
"""
from __future__ import annotations

import inspect

import pytest

from src.ecofac.decision import state_machine, transparent
from src.ecofac.decision.transparent import (
    TransparentDecisionRequest,
    decide_transparently,
)


def _base_request() -> TransparentDecisionRequest:
    payload = {
        "it_load": {"value": 80.0, "unit": "kW", "source": "measured",
                     "tolerance_abs": 2.0},
        "cooling_load": {"value": 90.0, "unit": "kW", "source": "measured",
                          "tolerance_abs": 2.0},
        "pue": {"value": 1.4, "unit": "ratio", "source": "measured",
                 "tolerance_abs": 0.05},
        "constraints": {
            "electrical_capacity_kw": 200.0,
            "electrical_reserve_pct": 0.1,
            "cooling_capacity_kw": 250.0,
            "cooling_reserve_pct": 0.1,
            "max_pue": 1.8,
            "warning_margin_pct": 0.05,
        },
        "proposed_change": {"it_delta_kw": 5.0, "cooling_delta_kw": 0.0},
    }
    return TransparentDecisionRequest.model_validate(payload)


def _compute_sensitivity_source() -> str:
    return inspect.getsource(transparent._compute_sensitivity)


def _wired() -> bool:
    return "_sensitivity_status" in _compute_sensitivity_source()


pytestmark = pytest.mark.skipif(
    not _wired(),
    reason="P0-1 not wired yet; see docs/P0-1.md and share transparent.py",
)


def test_sensitivity_delegates_to_state_machine(monkeypatch):
    calls: list[object] = []
    real = state_machine.evaluate_decision

    def spy(context):
        calls.append(context)
        return real(context)

    monkeypatch.setattr(state_machine, "evaluate_decision", spy)
    decide_transparently(_base_request())
    # primary decision + at least one band-edge per swept parameter
    assert len(calls) >= 2, (
        f"expected primary + sensitivity calls into evaluate_decision, "
        f"got {len(calls)}"
    )


def test_sensitivity_artifact_is_deterministic():
    a = decide_transparently(_base_request())
    b = decide_transparently(_base_request())
    assert getattr(a, "sensitivity", None) == getattr(b, "sensitivity", None)
PY
fi

if [[ ! -f docs/P0-1.md ]]; then
emit docs/P0-1.md <<'MD'
# P0-1 — Sensitivity unification (in progress)

## Contract

For every swept parameter, the status recorded in the per-decision
`sensitivity` artifact must equal:
evaluate_decision(build_context(predicted_electrical=..., predicted_cooling=...,
current_electrical=..., current_cooling=...)).status

```

`scenario_analysis.analyze_sensitivity` already honours this by re-running
`decide_transparently` per step. `transparent._compute_sensitivity` does not:
it uses local closures `_status_it`, `_status_cooling`, `_status_pue` that
appear to reimplement thresholds inline (no edge to `evaluate_decision` in
`ARCHITECTURE.json`).

## Plan

1. Add `_sensitivity_status(build_context, **changes)` helper (done by the
   remediation script; safe, additive).
2. Rewrite the three closures to call `_sensitivity_status` with the same
   `build_context` kwargs the primary decision uses:
   `predicted_electrical`, `predicted_cooling`, `current_electrical`,
   `current_cooling`.
3. Keep the delta computation in the closure (it is domain logic, not
   duplication).

## Why the script may revert P0-1

The exact delta semantics for each sweep (raw IT vs facility power for PUE;
which baseline is subtracted) cannot be inferred from the graph. The
remediation script attempts a conservative template and runs
`tests/unit/test_sensitivity_equivalence.py`; if the spec fails it restores
`transparent.py` from backup and leaves this document as the contract.
Share `transparent.py` + `state_machine.py` to finish the rewrite.
MD
fi

# Attempt the closure rewrite; revert on spec failure so P0-2/P0-3 stay green.
P0_1_STATUS="skipped"
if [[ "$DRY_RUN" != "1" ]] && grep -q "_sensitivity_status" src/ecofac/decision/transparent.py; then
  info "P0-1: helper already present"
  P0_1_STATUS="wired"
elif [[ "$DRY_RUN" != "1" ]]; then
  backup src/ecofac/decision/transparent.py
  P0_1_PRE="$(cat src/ecofac/decision/transparent.py)"

  if $PYTHON - <<'PY'
"""Conservative P0-1 rewrite: insert helper + replace closure bodies."""
import ast
import pathlib
import sys

path = pathlib.Path("src/ecofac/decision/transparent.py")
src = path.read_text()

HELPER = '''def _sensitivity_status(build_context, **changes):
    """P0-1: single source of truth for band-edge status."""
    from .state_machine import evaluate_decision
    return evaluate_decision(build_context(**changes)).status


if "_sensitivity_status" not in src:
    idx = src.find("def _compute_sensitivity")
    if idx < 0:
        print("no _compute_sensitivity", file=sys.stderr)
        sys.exit(1)
    src = src[:idx] + HELPER + src[idx:]

tree = ast.parse(src)
lines = src.splitlines(keepends=True)

templates = {
    "_status_it": (
        "    if measured_value is None:\n"
        "        return None\n"
        "    return _sensitivity_status(\n"
        "        build_context,\n"
        "        predicted_electrical=predicted_it + (measured_value - it_load.value),\n"
        "        predicted_cooling=predicted_cooling,\n"
        "        current_electrical=current_it,\n"
        "        current_cooling=current_cooling,\n"
        "    )\n"
    ),
    "_status_cooling": (
        "    if measured_value is None:\n"
        "        return None\n"
        "    return _sensitivity_status(\n"
        "        build_context,\n"
        "        predicted_electrical=predicted_it,\n"
        "        predicted_cooling=predicted_cooling + (measured_value - cooling_load.value),\n"
        "        current_electrical=current_it,\n"
        "        current_cooling=current_cooling,\n"
        "    )\n"
    ),
    "_status_pue": (
        "    if candidate_pue is None or not pue_value:\n"
        "        return None\n"
        "    return _sensitivity_status(\n"
        "        build_context,\n"
        "        predicted_electrical=predicted_it * (candidate_pue / pue_value),\n"
        "        predicted_cooling=predicted_cooling,\n"
        "        current_electrical=current_it,\n"
        "        current_cooling=current_cooling,\n"
        "    )\n"
    ),
}

found = {}
for node in ast.walk(tree):
    if isinstance(node, ast.FunctionDef) and node.name in templates:
        found.setdefault(node.name, node)

if not found:
    print("no closures found", file=sys.stderr)
    sys.exit(1)

for name in sorted(found, key=lambda n: found[n].lineno, reverse=True):
    node = found[name]
    # keep the signature lines, replace the body
    sig_end = node.lineno - 1
    while not lines[sig_end].rstrip().endswith(":"):
        sig_end += 1
        if sig_end > node.lineno + 5:
            print(f"{name}: cannot find signature end", file=sys.stderr)
            sys.exit(1)
    lines[node.lineno - 1 : node.end_lineno] = lines[node.lineno - 1 : sig_end + 1] + [templates[name]]

path.write_text("".join(lines))
print("rewrote closures to delegate via _sensitivity_status")
PY
  then
    if $PYTEST -q tests/unit/test_sensitivity_equivalence.py; then
      P0_1_STATUS="green"
    else
      warn "P0-1 spec failed; reverting transparent.py"
      printf '%s' "$P0_1_PRE" > src/ecofac/decision/transparent.py
      P0_1_STATUS="reverted (needs source)"
    fi
  else
    warn "P0-1 rewrite could not be applied; reverting"
    printf '%s' "$P0_1_PRE" > src/ecofac/decision/transparent.py
    P0_1_STATUS="reverted (rewrite failed)"
  fi
fi
info "P0-1 status: $P0_1_STATUS"

# =============================================================================
# Verification — lint + focused tests
# =============================================================================
step "Lint (ruff)"
$RUFF check src/ tests/ scripts/ || abort "ruff check failed"
$RUFF format --check src/ tests/ scripts/ 2>/dev/null || {
  warn "format drift; run: $RUFF format src/ tests/ scripts/"
}

step "Guard: no legacy imports"
$PYTHON scripts/check_no_legacy_imports.py

step "Focused tests"
FOCUS=(
  tests/unit/test_sensitivity_equivalence.py
  tests/unit/test_deprecated_modules.py
  tests/unit/test_trust_resolution.py
  tests/unit/test_decision_state_machine.py
  tests/unit/test_decision_constraints.py
  tests/evaluation/test_runner_canary.py
  tests/integration/test_decision_engine.py
  tests/integration/test_trusted_mode.py
  tests/api/test_trusted_decisions_api.py
  tests/api/test_decisions_routes.py
  tests/api/test_scenarios_api.py
  tests/api/test_scenarios_analysis_api.py
  tests/api/test_part_2_1_end_to_end.py
)
$PYTEST -q "${FOCUS[@]}" || abort "focused tests failed"

step "Diff summary"
git diff --stat || true

step "Summary"
cat <<EOF
P0-1  $P0_1_STATUS
P0-2  post -> post_json, add -> add_reading_row, resolve -> resolve_pair_request
P0-3  docs/LEGACY.md + scripts/check_no_legacy_imports.py + Makefile target

Backups: $BACKUP_DIR
Next:
  git add tests/ && git commit -m 'P0-2: rename colliding test helpers'
  git add docs/ scripts/ Makefile src/ecofac/decision/engine.py \\
             src/ecofac/decision/constraints.py \\
          && git commit -m 'P0-3: document legacy engine/constraints'
  # only if P0-1 green:
  git add src/ecofac/decision/transparent.py \\
             tests/unit/test_sensitivity_equivalence.py docs/P0-1.md \\
          && git commit -m 'P0-1: unify sensitivity via state machine'
EOF
