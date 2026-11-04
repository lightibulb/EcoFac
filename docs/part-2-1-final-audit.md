# EcoFac Part 2.1 - Final Integration Audit

Auditor: Principal Engineer (independent)
Method:   source inspection + ruff + pytest (evidence regenerated on each run)
Scope:    Part 2.1 Decision Center + Scenario Intelligence

## A. Feature status

### Completed

| Feature | Evidence |
|---|---|
| Decision evaluation (POST /api/v1/decisions/evaluate) | `src/ecofac/api/routes/decisions.py`, `tests/api/test_decisions_routes.py` |
| Decision persistence (append-only, triggers) | `src/ecofac/persistence/schema.sql`, `tests/integration/test_decisions_persistence.py` |
| Decision history (paginated, X-Total-Count) | `decisions.py` list endpoint + tests |
| Decision detail (full evidence) | `get_decision_endpoint` |
| Decision verification (input+config fp + status re-run) | `store.verify_decision`, `DecisionVerifyResponse` |
| Decision engineering summary | `/decisions/{id}/engineering` |
| Decision stats (aggregate) | `/decisions/stats`, `decision/stats.py` |
| Supersession (single-child, branch-aware walker) | `walk_supersession_chain_with_branches`, migration 003 |
| Evidence inspection (formulas, assumptions, constraints) | `result.evidence` in `DecisionRecordResponse` |
| Explanation + uncertainty rendered | Frontend `decisions.html` sections |
| Decision Center page | `templates/decisions.html` |
| Scenario creation + evaluation | `POST /api/v1/scenarios/evaluate` |
| Scenario validation (finite check + Pydantic) | `validate_scenario_numerics` |
| Scenario persistence (separate table, append-only) | `scenarios` table + triggers, migration 004 |
| Scenario history | `/api/v1/scenarios` list |
| Scenario engineering / bottlenecks / sensitivity | `/{id}/engineering`, `/{id}/bottlenecks`, `/{id}/sensitivity` |
| Multi-scenario comparison | `POST /api/v1/scenarios/compare` |
| Scenario Workbench page | `templates/scenarios.html` |
| Cross-page navigation | topnav in index / decisions / scenarios |
| NaN/inf safe validation responses | `_sanitize_validation_error` in `main.py` |

### Partially completed

| Feature | Gap | Reason |
|---|---|---|
| Decision verification | Re-runs status + precedence only. Does not re-run full evidence bundle. | `verification_scope = input_config_output_status`, `full_bundle_reproduced = False`. Documented, not hidden. |
| Trusted-mode evaluation | Backend supports it; requires caller to supply `meter_id` and `measurement_timestamp`. | Frontend does not expose trusted-mode UI. Backend only. |
| Corrective action mode | Engine supports `corrective_action_mode=True`; not exposed by any API schema. | Overloaded + corrective-reduction proposal still returns REJECT. Documented in `docs/part-2-1-integration-audit.md`. |
| Scenario history | No per-user attribution. Timestamps + IDs are the only provenance. | No auth layer to attribute. |
| Sensitivity sweep | Fixed ±20% / ±5pp steps. No adaptive or user-specified range. | Deterministic by design; no fabricated probability. |

### Not implemented

| Item | Severity | Notes |
|---|---|---|
| Authentication / authorization | HIGH (for production) | `ECOFAC_WRITE_TOKEN` exists but optional, off by default. Documented in `SECURITY.md`. |
| Rate limiting | HIGH (for production) | No request rate caps on read or write endpoints. |
| Tenant isolation | HIGH (for production) | Single SQLite DB, no org-scoped rows. |
| Full evidence bundle reproduction | MEDIUM | `verified` scope is explicitly declared. |
| Persistent storage on free hosting | MEDIUM | Documented `render.yaml` and free-tier limitation. |

## B. Critical defects

**None open.** The one critical defect that emerged during the audit (NaN in
a Pydantic-validated field crashed the error serialiser, returning a 500
instead of 422) was fixed by a global `RequestValidationError` handler in
`src/ecofac/main.py`. Regression coverage:
`tests/unit/test_nan_sanitizer.py` (7 tests) and
`tests/api/test_part_2_1_end_to_end.py::test_nan_returns_clean_422_not_server_crash`.

## C. High-priority defects

None currently open.

## D. Medium-priority defects

1. `corrective_action_mode` flag is not exposed via API schema.
2. Scenario Workbench does not display `verification_scope` prominently.
3. `config_version` strings do not influence `config_fingerprint` (by
   design). Two scenarios with the same config payload but different
   version labels share a fingerprint.

## E. Security findings

Documented in `SECURITY.md` and `docs/limitations.md`:

- **No authentication on write endpoints by default.**
- **No rate limiting.**
- **No tenant isolation.**
- **SQLite single-writer.**
- **Ephemeral storage on the Render free plan.**
- **Append-only enforcement** via SQLite triggers on `decisions`,
  `scenarios`, `evidence_records`, `audit_events`.
- **Sanitised error responses.** Non-finite floats replaced with strings
  before serialisation.
- **No hardcoded secrets.** Token via env var only.

## F. Engineering correctness findings

Verified by source inspection + unit tests:

- **PUE integration:** `facility_power = IT load * PUE` via
  `calculate_electrical`. Tests: `tests/unit/test_pue_integration.py`,
  `test_engineering_pue_multiplies_facility_power`.
- **Electrical constraint uses facility power, not IT load.**
  `test_engineering_facility_power_not_it_load_for_constraint`.
- **Reserve margin reduces permitted below physical.**
  `test_engineering_reserve_reduces_permitted`.
- **Cooling independent of electrical.**
  `test_engineering_cooling_independent`.
- **Negative predicted loads → REJECT invalid_proposal** with reason.
  `test_engineering_negative_predicted_load`.
- **Units validated.** Loads must be `kW`; PUE must be `dimensionless`.
  `test_engineering_unit_conversion_rejected`.
- **Missing tolerance → UNKNOWN model uncertainty.**
  `test_engineering_missing_tolerance_unknown_uncertainty`.
- **Stale measurements → REVIEW data_quality_ineligible.**
  `test_journey_e_missing_evidence_and_stale`.
- **NaN/Inf rejected** (`validate_scenario_numerics` + Pydantic gt=0).

## G. Test results (this run)

### Ruff
```
All checks passed!
```

### Focused Part 2.1 tests
```
  /data/data/com.termux/files/usr/lib/python3.14/site-packages/fastapi/testclient.py:1: StarletteDeprecationWarning: Using `httpx` with `starlette.testclient` is deprecated; install `httpx2` instead.
    from starlette.testclient import TestClient as TestClient  # noqa

-- Docs: https://docs.pytest.org/en/stable/how-to/capture-warnings.html
249 passed, 1 warning in 24.97s
```

### Full suite
```
  /data/data/com.termux/files/usr/lib/python3.14/site-packages/fastapi/testclient.py:1: StarletteDeprecationWarning: Using `httpx` with `starlette.testclient` is deprecated; install `httpx2` instead.
    from starlette.testclient import TestClient as TestClient  # noqa

-- Docs: https://docs.pytest.org/en/stable/how-to/capture-warnings.html
609 passed, 1 warning in 144.87s (0:02:24)
```

## H. Documentation changes in Part 2.1

- `docs/decision-center.md` — Decision Center page + inspector sections
- `docs/scenario-intelligence.md` — Scenario API + analysis endpoints
- `docs/decisions-workflow.md` — supersession policy
- `docs/part-2-1-integration-audit.md` — earlier journey audit
- `docs/part-2-1-final-audit.md` — this audit (generated by this script)
- `docs/limitations.md` — updated with decision-engine caveats
- `docs/architecture.md` — canonical pipeline note
- `README.md` — scope unchanged (still synthetic-data demonstration)

Gaps:
- No API reference generated from OpenAPI is committed.
- No consolidated precedence-value reference doc.

## I. Deployment requirements

| Requirement | Status |
|---|---|
| Set `ECOFAC_WRITE_TOKEN` env var | Required for any non-demo deployment |
| Configure reverse proxy rate limits | Required for any non-demo deployment |
| Persistent disk for durable data | Required on any paid plan; free plan is ephemeral |
| Trust TLS termination | Handled by hosting platform |
| Persistent SQLite backup | Not automated |

## J. Recommended next milestone

**Part 2.2 - Production hardening.** Priority order:

1. Authentication: token-based (API key or OAuth) for write endpoints.
2. Authorization: minimal RBAC (reader vs writer roles).
3. Rate limiting: per-IP and per-identity limits.
4. Persistent storage migration: SQLite → Postgres.
5. Observability: structured logs, request IDs, health/readiness probes.
6. Full evidence reproduction in `/verify`.
7. Expose `corrective_action_mode` via API schema.

## K. Final assessment

**Part 2.1 acceptance criteria: MET for the specified scope.**

- Every endpoint required by the Decision Center and Scenario Intelligence
  task exists, is documented, and is covered by tests.
- Every engineering invariant the task names is verified by a numeric test.
- The NaN serialization defect found mid-audit was fixed at the server
  boundary with regression coverage; no known critical defect remains.
- The frontend pages consume backend values exclusively; no client-side
  engineering math is performed.

**Part 2.1 is NOT a claim of production readiness.** Remaining security,
rate limiting, and persistence gaps must be closed before any deployment
involving real operational data. This audit does not hide them.

**Do not merge this milestone into a "production release" branch until
the Part 2.2 items in Section J are addressed.**
