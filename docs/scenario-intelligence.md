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
