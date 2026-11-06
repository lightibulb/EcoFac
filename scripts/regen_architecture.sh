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
