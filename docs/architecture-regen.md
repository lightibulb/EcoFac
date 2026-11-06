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
