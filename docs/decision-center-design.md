# Part 2.1 — Decision Center: Architecture Design

**Status:** Design — not yet implemented.
**Scope:** Frontend experience + supporting read-only backend endpoints.
**Non-goals:** New decision logic, new engineering calculations, new DB tables.

---

## 1. Architecture Assessment

### 1.1 Canonical decision evaluation endpoint

POST /api/v1/decisions/evaluate — src/ecofac/api/routes/decisions.py.

Flow:
1. Validate DecisionEvaluateRequest (422 on malformed input).
2. _resolve_trusted() — when mode="trusted", replace caller-supplied
   measurement values with DB-validated readings and recompute quality
   flags server-side (400 on missing / invalid readings).
3. decide_transparently() — state machine + uncertainty + sensitivity +
   evidence + explanation + fingerprint.
4. persist_decision() — atomic write to decisions + decision_configs.
5. Return DecisionEvaluateResponse { decision_id, result }.

Auth: require_write_access (env-gated by ECOFAC_WRITE_TOKEN).

### 1.2 Persistence

- decisions — append-only (triggers block UPDATE/DELETE). Columns: id,
  status, precedence, engine_version, config_version, input_fingerprint,
  config_fingerprint, request_json, result_json, decision_timestamp,
  created_at, supersedes, supersession_reason.
- decision_configs — one row per unique config fingerprint.
- Both TransparentDecisionRequest and TransparentDecisionResult are stored
  verbatim in request_json / result_json.

Every field the Decision Center inspector needs is already persisted.

### 1.3 Available fields per decision

All in DecisionRecordResponse.result (parsed from result_json):

| Section | Fields |
|---|---|
| Status | status, precedence, decision_timestamp, engine_version, config_version |
| Inputs | evidence.input_references[] — name, value, unit, source, reference_id, timestamp |
| Proposed change | evidence.calculation_inputs.proposed_it_delta_kw, proposed_cooling_delta_kw, invalid_proposal_reasons |
| Current state | evidence.calculation_inputs.current_it_load_kw, current_facility_power_kw, current_cooling_load_kw, pue, pue_provenance |
| Predicted state | evidence.calculation_inputs.predicted_it_load_kw, predicted_facility_power_kw, predicted_cooling_load_kw |
| Constraints | state_machine_decision.evaluated_constraints[] + evidence.{passed,warning,failed}_constraints[] |
| Formulas / assumptions | evidence.formulas, evidence.assumptions[] |
| Constraints snapshot | evidence.constraints_snapshot |
| Uncertainty | uncertainty.{missing_measurements, stale_measurements, low_quality_measurements, estimated_parameters, assumptions, model_limitations, data_quality_level, model_uncertainty_level, rationale, tolerance_used, quality_flags} |
| Explanation | explanation.{summary, limiting_constraint, headroom_summary, passed, failed, warnings, uncertainty_notes, what_would_change_decision} |
| Sensitivity | sensitivity[].{measurement_name, baseline_status, lower_bound, upper_bound, status_at_lower, status_at_upper, could_change_verdict, reason} |
| Reproducibility | fingerprint.{input_fingerprint, config_fingerprint, decision_timestamp, engine_version, config_version, full_bundle} |

### 1.4 Evidence / explanation / uncertainty / constraints

- Evidence (DecisionEvidenceBundle): formulas, assumptions, three
  constraint lists, and calculation_inputs with every numeric value.
- Explanation (DecisionExplanation): summary, limiting_constraint,
  headroom_summary, passed[], failed[], warnings[], uncertainty_notes[],
  what_would_change_decision[].
- Uncertainty (UncertaintyReport): categorical only
  (OK/DEGRADED/UNUSABLE, LOW/MODERATE/HIGH/UNKNOWN). No calibrated
  percentage.
- Constraints (list[LimitEvaluation]): name, severity, observed, permitted,
  headroom_to_permitted, headroom_to_physical, margin_used_pct,
  is_limiting, reason.

### 1.5 Verification semantics — does it recompute?

Yes (decision/store.py::verify_decision):
1. Re-validates request_json into TransparentDecisionRequest.
2. Recomputes input/config fingerprints, compares to stored.
3. Re-runs decide_transparently(request) and compares stored status +
   precedence to recomputed values.
4. verified = input_verified AND config_verified AND output_verified.

Current gaps (P2):
- Does not compare engine_version — a v0.9 record re-run under v1.0 may
  differ and return verified: false without saying why.
- Does not compare full evidence bundle.
- Does not distinguish "verify FALSE" from "cannot verify".

### 1.6 Reusable frontend components

From templates/index.html:
- CSS design tokens (--paper, --ink, --accent, --alert, --ok, dark-mode
  support).
- .panel, .strip, .stat, .num, .lbl patterns.
- Chart.js with reveal / marks / crosshair plugins.
- api() fetch wrapper, toast notifications, visibility-aware polling.
- timeAgo, countTo, esc utilities.

Recommendation: new templates/decisions.html plus static/decisions.{js,css}.
Add nav bar linking / ↔ /decisions.

### 1.7 Missing backend capabilities

| Gap | Reason |
|---|---|
| GET /api/v1/decisions/stats | Overview needs server-side aggregate counts + latest + recent activity |
| GET /api/v1/decisions/{id}/engineering | Engineering Overview needs structured current/predicted values; evaluated_constraints mixes current and predicted without labels |
| is_superseded + superseded_by on DecisionHistoryItem | Per-row indicator without N queries |
| X-Total-Count on GET /decisions | Pager needs total |
| (P2) Extended DecisionVerifyResponse | engine_version_match, full_bundle_verified |

### 1.8 Security restrictions

| Endpoint | Current | Recommendation |
|---|---|---|
| POST /decisions/evaluate | require_write_access | Keep |
| GET /decisions, /decisions/{id}, /{id}/verify, /{id}/supersession-chain | public | Keep |
| GET /decisions/stats (proposed) | — | Public |
| GET /decisions/{id}/engineering (proposed) | — | Public |

Trusted mode: caller can reference only readings that exist and are
is_valid=1; cannot manufacture values.

---

## 2. Proposed Component Structure

src/ecofac/
  api/routes/decisions.py            # EXTEND — /stats, /{id}/engineering
  api/schemas.py                     # EXTEND — new schemas
  decision/stats.py                  # NEW — aggregate metrics
  decision/engineering.py            # NEW — extract from stored evidence
  templates/index.html               # MODIFY — nav link
  templates/decisions.html           # NEW
  templates/static/decisions.js      # NEW
  templates/static/decisions.css     # NEW

tests/
  unit/test_decision_stats.py        # NEW
  unit/test_decision_engineering.py  # NEW
  integration/test_decisions_engineering.py  # NEW
  api/test_decisions_routes.py       # EXTEND

---

## 3. API Integration Plan

### Reused (no change)

- GET /api/v1/decisions?limit&offset&status&config_version
- GET /api/v1/decisions/{id}
- GET /api/v1/decisions/{id}/supersession-chain
- GET /api/v1/decisions/{id}/verify

### New

#### GET /api/v1/decisions/stats

Returns total, approved, rejected, review counts, latest_decision, and
recent_activity (last 10 DecisionHistoryItem).

#### GET /api/v1/decisions/{id}/engineering

Pure extraction from result.evidence. No state-machine work. Returns pue,
pue_provenance, current/predicted it_load / facility_power / cooling_load,
and per-side (current_electrical, predicted_electrical, current_cooling,
predicted_cooling) headroom objects.

### Extended

- DecisionHistoryItem gains is_superseded: bool = False,
  superseded_by: str | None = None.
- GET /api/v1/decisions gains response header X-Total-Count.
- DecisionVerifyResponse (P2) gains full_bundle_verified,
  engine_version_match, stored_engine_version, current_engine_version.

---

## 4. Missing Endpoint/Schema Requirements

class DecisionStats: total, approved, rejected, review,
  latest_decision: DecisionHistoryItem | None,
  recent_activity: list[DecisionHistoryItem]

class ConstraintHeadroom: physical_capacity_kw, permitted_kw, observed_kw,
  headroom_to_permitted_kw, headroom_to_physical_kw, reserve_pct

class EngineeringSummary: pue, pue_provenance, current_it_load_kw,
  current_facility_power_kw, current_cooling_load_kw, predicted_it_load_kw,
  predicted_facility_power_kw, predicted_cooling_load_kw,
  current_electrical, predicted_electrical, current_cooling,
  predicted_cooling: ConstraintHeadroom

decision/stats.py:
  def aggregate_decisions(conn, *, recent_limit=10) -> dict

decision/engineering.py:
  def build_engineering_summary(record: dict) -> EngineeringSummary

---

## 5. Database Changes

None required. Everything derives from existing columns.

Optional P2: decisions.last_verified_at and
decisions.last_verification_result_json to cache verify results.

---

## 6. Security Considerations

1. Write path (/evaluate) remains gated by require_write_access.
2. All read endpoints stay public (synthetic data).
3. /verify re-runs the engine — document cost in docs/limitations.md;
   consider future memoization keyed by (decision_id, engine_version).
4. Trusted-mode DB reference validation unchanged.
5. No new auth required for v1.

---

## 7. Experience Design

### A. Decision Overview
GET /stats → 4 stat cards + latest decision card + recent activity list.

### B. Decision Inspector
GET /decisions/{id} + GET /decisions/{id}/engineering → single-page panel
with sections: header, inputs, proposed change, engineering overview,
constraints (three lists), uncertainty, explanation, sensitivity,
reproducibility, supersession.

### C. Decision History
GET /decisions?limit=20&offset=N&status=X → paginated list with status and
config-version filters, superseded indicator, verify badge. X-Total-Count
drives the pager.

### D. Engineering Overview
Rendered inside the Inspector. Sourced entirely from /engineering. Bars
show headroom vs permitted and physical bounds. No client-side
engineering math.

---

## 8. Implementation Sequence

1. decision/stats.py::aggregate_decisions() + tests
2. GET /decisions/stats + tests
3. Extend DecisionHistoryItem + batch supersession query + tests
4. Add X-Total-Count header + tests
5. decision/engineering.py::build_engineering_summary() + tests
6. GET /decisions/{id}/engineering + tests
7. (P2) Extend DecisionVerifyResponse + tests
8. templates/decisions.html + static/decisions.js
9. Nav link in index.html
10. Integration test (evaluate → list → inspect → verify)
11. Docs update

Each step independently testable and backward compatible.

---

## 9. Test Strategy

| Layer | Purpose | Approx. |
|---|---|---|
| test_decision_stats.py | Aggregate counts, latest, recent activity | ~8 |
| test_decision_engineering.py | Extraction, corrective mode, missing fields | ~10 |
| test_decisions_engineering.py | End-to-end via TestClient | ~4 |
| test_decisions_routes.py (extend) | /stats, supersession indicator, X-Total-Count, /{id}/engineering, verify extension | ~15 |
| Frontend smoke | GET /decisions renders HTML | ~1 |

Regression gate: all existing 429 tests must pass. CI runs ruff, pytest,
evaluation runner, OpenAPI schema.

---

## 10. Risks and Non-Goals

Non-goals:
- No new decision logic
- No new engineering calculations
- No new DB tables
- No authentication changes
- No move of the canonical pipeline away from decision.transparent

Risks:
- Engineering extraction must stay purely presentational; single source
  of truth is result.evidence.calculation_inputs.
- Verify cost grows with record count — memoize in a follow-up if needed.

Invariants:
1. Frontend never calculates engineering outcomes.
2. Decision Center never modifies stored decisions.
3. POST /decisions/evaluate is the only write path; the Decision Center
   uses GET exclusively.
