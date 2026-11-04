# Part 2.1 Integration Audit

Status: verification pass, not a production-readiness claim.

This document records the integration and reliability audit conducted on
Part 2.1 (Decision Center and Scenario Intelligence). It documents what
was tested, what passed, what remains limited, and what would still need
to be addressed before production use.

## Scope

Audited areas:

1. Decision evaluation (POST /api/v1/decisions/evaluate)
2. Decision persistence (decisions, decision_configs tables)
3. Decision history (GET /api/v1/decisions)
4. Decision detail retrieval (GET /api/v1/decisions/{id})
5. Decision verification (GET /api/v1/decisions/{id}/verify)
6. Evidence inspection (fields in result.evidence)
7. Explanation rendering (result.explanation)
8. Uncertainty rendering (result.uncertainty)
9. Scenario creation (POST /api/v1/scenarios/evaluate)
10. Scenario evaluation (same endpoint, different persistence)
11. Multi-scenario comparison (POST /api/v1/scenarios/compare)
12. Sensitivity analysis (GET /api/v1/scenarios/{id}/sensitivity)
13. Bottleneck detection (GET /api/v1/scenarios/{id}/bottlenecks)
14. Dashboard navigation (three-page cross-links)
15. Frontend/backend schema compatibility

## Test artifacts

- tests/api/test_part_2_1_end_to_end.py
  - 20 tests covering Journeys A-E and engineering validations
- tests/api/test_decisions_routes.py (54 tests)
- tests/api/test_decisions_page.py (14 tests)
- tests/api/test_scenarios_api.py (26 tests)
- tests/api/test_scenarios_analysis_api.py (18 tests)
- tests/api/test_scenarios_page.py (14 tests)
- tests/integration/test_decision_inspector_states.py (11 tests)
- tests/integration/test_scenarios_api.py (26 tests)
- tests/unit/* (existing unit coverage)

## Journeys

### Journey A: Decision Center full path
PASS. Covered by test_journey_a_decision_center_full_path. List,
select, inspect engineering inputs, inspect constraints, inspect
evidence, verify. Every field the frontend reads is present in the
response. Verification scope is input_config_output_status.

### Journey B: Scenario Intelligence full path
PASS. Covered by test_journey_b_scenario_full_path. Evaluate, retrieve,
engineering summary, verify. History is paginated and accurate.

### Journey C: Multi-scenario comparison
PASS. Covered by test_journey_c_multi_scenario_comparison. Three
scenarios with different IT load and PUE compared. input_differences
flags the differing fields. outcome_explanation names the driver.

### Journey D: Invalid inputs
PASS. Covered by test_journey_d_invalid_inputs. Negative IT load, zero
PUE, negative capacity, reserve=1.0, empty name return 422. NaN and
infinity return 400 from the finite-check. No records persist.

### Journey E: Missing evidence and stale measurements
PASS. Covered by test_journey_e_missing_evidence_and_stale. Stale
measurement forces REVIEW. Quality flags force REVIEW. Old record
without PUE evidence returns 422 for /engineering while /{id} still
works.

## Engineering validations

| Check | Result |
|---|---|
| Facility power = IT load * PUE | PASS |
| Electrical constraint uses facility power (not IT load) | PASS |
| Cooling constraint independent of electrical | PASS |
| Reserve reduces permitted below physical | PASS |
| Negative predicted load -> REJECT / invalid_proposal | PASS |
| Non-kW PUE unit -> 400 | PASS |
| Stale measurement + max_age -> REVIEW | PASS |
| No measurement tolerance -> UNKNOWN uncertainty | PASS |

## Known limitation: corrective action mode is not exposed

The engine supports a `corrective_action_mode` flag on DecisionContext
that would evaluate only the predicted state, allowing a proposal to
repair an existing overload. This flag is NOT exposed via any API
endpoint, so every evaluation checks both current and predicted state
by default.

Consequence: if a caller is currently overloaded (for example IT load
above capacity) and proposes a reduction that brings predicted load
under capacity, the engine still returns REJECT because the CURRENT
state is overloaded. Verified by
test_engineering_existing_overload_with_corrective_action.

This is documented as a known limitation, not a bug. Exposing the flag
would require a schema change and a policy decision on when it is
appropriate.

## Frontend observations

- All three pages render with expected containers.
- Cross-page navigation works in both directions.
- No unrendered Jinja placeholders.
- Empty states are present when no records exist.
- Loading states use CSS skeletons and aria-busy.
- Results come exclusively from backend responses; the browser does not
  compute facility power, headroom, or constraint severity.
- Disclaimer banners on /decisions and /scenarios clarify that decisions
  are advisory and scenarios are hypothetical.

## Performance observations

- Pagination is bounded: limit max is 100; offset must be >= 0.
- Comparison is bounded: 2 to 10 scenarios per request.
- list endpoints use a single SELECT plus a single COUNT; X-Total-Count
  header avoids a second round-trip for pagination.
- Batch supersession uses a single IN(...) query; no N+1 on list views.
- Verification re-runs decide_transparently on the stored request. This
  is a per-request cost, not a background job; it is acceptable for
  dashboard use but would need memoization for high-traffic deployments.
- SQLite is single-writer. Two concurrent POST /evaluate calls will
  serialize on BEGIN IMMEDIATE; this is expected.

## Data integrity guarantees

- Both decisions and scenarios are append-only (triggers reject UPDATE
  and DELETE).
- Fingerprints are stored and recomputed on verify; tampering is
  detectable.
- Verification scope is explicitly reported (input_config_output_status)
  and full_bundle_reproduced is always false. There is no overclaim.
- persist_as_decision is off by default; scenarios do not pollute
  operational decision history.
- Mixed config versions in comparisons are flagged.

## Not production ready

This audit does NOT change the production-readiness assessment.
EcoFac is still a synthetic-data demonstration with:

- No authentication or authorization (ECOFAC_WRITE_TOKEN is optional and
  environment-gated)
- No rate limiting on either read or write endpoints
- No tenant isolation
- SQLite with single-writer semantics
- Ephemeral storage on the free hosting plan
- Statistical anomaly detection with no weather or occupancy
  normalization

These are documented in docs/limitations.md and docs/security notes.
Passing this audit means the Part 2.1 integration is coherent and
internally consistent, not that the deployment is suitable for real
facility operations.

## Remaining limitations (Part 2.1 specific)

1. Corrective action mode is not exposed via the API (see above).
2. The sensitivity sweep is deterministic and bounded; it does not
   produce calibrated probability estimates or confidence intervals.
3. Bottleneck thresholds (10% approaching, 20% insufficient margin) are
   policy choices, not derived from operational data.
4. Scenario history is per-deployment; there is no multi-user separation.
5. Comparing scenarios with different config version labels is
   permitted but flagged. There is no rule preventing such comparisons
   beyond that flag.
6. The Scenario Workbench does not save an explicit user identity with
   each scenario; timestamps and IDs are the only attribution.

## Final assessment

Part 2.1 is internally consistent: every user journey documented in the
task has a passing end-to-end test, every engineering invariant is
verified with a numeric assertion, and the frontend consumes backend
values without local recomputation.

Before any production deployment, the outstanding items in
docs/limitations.md and the "Not production ready" section above would
need to be addressed. This audit does not claim otherwise.
