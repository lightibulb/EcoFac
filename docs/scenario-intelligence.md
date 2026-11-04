# Scenario Intelligence (backend)

Scenario Intelligence lets callers explore hypothetical operational
changes using the **same canonical decision engine** that produces
operational decisions. Scenarios live in their own table and do not
pollute operational decision history unless the caller opts in.

## Persistence

| Table | Purpose |
|---|---|
| `scenarios` | Every evaluated scenario, append-only |
| `decisions` | Operational decisions (unchanged) |

`scenarios.decision_id` is populated **only** when the caller sets
`persist_as_decision=true`. Default behavior keeps the two streams
separate. Append-only triggers prevent silent modification.

## API

| Method | Path | Auth | Purpose |
|---|---|---|---|
| POST | `/api/v1/scenarios/evaluate` | `require_write_access` | Evaluate + persist a scenario |
| GET | `/api/v1/scenarios` | public | List scenarios (paginated, filtered) |
| GET | `/api/v1/scenarios/{id}` | public | Get a single scenario |
| GET | `/api/v1/scenarios/{id}/verify` | public | Re-run engine on stored inputs |

### Request

```json
{
  "name": "Add 2 servers, cap 500",
  "config": {
    "electrical": {"capacity": 500.0, "reserve_pct": 0.10},
    "cooling": {"capacity": 800.0, "reserve_pct": 0.10}
  },
  "config_version": "cfg-lab-2026",
  "it_load_kw": 400.0,
  "cooling_load_kw": 300.0,
  "pue": 1.4,
  "proposed_it_delta_kw": 0.0,
  "proposed_cooling_delta_kw": 0.0,
  "it_source": "ASSUMED",
  "cooling_source": "ASSUMED",
  "pue_source": "ASSUMED",
  "persist_as_decision": false
}
```

Response

```json
{
  "scenario_id": "uuid",
  "decision_id": "uuid or null",
  "result": { /* full TransparentDecisionResult */ }
}
```

Validation

Input Rejected when
it_load_kw negative (422), NaN/inf (400)
cooling_load_kw negative (422), NaN/inf (400)
pue ≤ 0 (422), NaN/inf (400)
proposed_it_delta_kw NaN/inf (400)
proposed_cooling_delta_kw NaN/inf (400)
config.*.capacity ≤ 0 (422), NaN/inf (400)
config.*.reserve_pct outside [0, 1) (422), NaN/inf (400)

NaN/infinity are checked explicitly because Pydantic Field(ge=0) does
not reliably reject them. Negative predicted loads flow through the
canonical engine and are classified as REJECT / invalid_proposal.

Canonical engine

evaluate_and_persist_scenario calls decide_transparently directly.
There is no parallel decision path. The engine applies
facility_power = IT × PUE for the electrical constraint, respects
reserve_pct and warning_threshold_pct, and preserves uncertainty
semantics (sources default to ASSUMED).

Verification

GET /api/v1/scenarios/{id}/verify recomputes fingerprints and re-runs
the engine on stored inputs. Scope is input_config_output_status;
full_bundle_reproduced is always false.

## Analysis endpoints

Four read-only endpoints expose derived analysis on any stored
scenario. None of them persist new rows or re-write the decision
engine — every number is produced by decide_transparently or
extracted from the stored result payload.

### GET /api/v1/scenarios/{id}/engineering

Returns the same EngineeringSummary shape used by the Decision
Center (current / predicted IT load, facility power, cooling
load, PUE and its provenance, electrical + cooling headroom to
both permitted and physical bounds). Returns 422 for records
that predate the PUE integration.

### GET /api/v1/scenarios/{id}/bottlenecks

Returns ScenarioBottleneckReport:

- most_restrictive_constraint — predicted constraint with the
  smallest headroom-to-permitted.
- findings — one entry per constraint per state where the
  classification is non-trivial. Categories:
  - {electrical|cooling}_over_physical (CRITICAL)
  - {electrical|cooling}_over_permitted (CRITICAL)
  - {electrical|cooling}_approaching_physical (WARNING)
  - {electrical|cooling}_approaching_permitted (WARNING)
  - {electrical|cooling}_insufficient_margin (INFO, APPROVE-only)
- has_enough_information — False for invalid-proposal records
  where the engine did not produce constraint evaluations.

Policy constants:

- APPROACHING_PHYSICAL_RATIO = 0.10
- APPROACHING_PERMITTED_RATIO = 0.10
- INSUFFICIENT_MARGIN_RATIO = 0.20

These are policy choices, not calibrated confidence scores.

### GET /api/v1/scenarios/{id}/sensitivity

Returns SensitivityAnalysisResponse with baseline_status,
baseline_precedence, and per-parameter steps.

Sweeps are deterministic:

- Multiplicative (loads, PUE, capacities): x0.8, x0.9, x1.0,
  x1.1, x1.2
- Additive (reserve percentages): -0.05, -0.02, 0, +0.02, +0.05

Invalid steps (negative loads, PUE < 0.1, reserve outside
[0, 1)) are skipped. No probability distributions or confidence
intervals are fabricated.

### POST /api/v1/scenarios/compare

Body: {"scenario_ids": ["uuid1", "uuid2", "uuid3"]}

Returns ScenarioComparisonResponse:

- scenarios[] — per-scenario engineering snapshot plus
  most_restrictive_constraint and uncertainty levels
- input_differences[] — per-field values and differs flag
- mixed_config_versions / config_versions — surfaces when
  scenarios were evaluated under different config versions
- outcome_explanation — deterministic narrative

Validation: 2-10 scenario IDs; unknown IDs return 404.
