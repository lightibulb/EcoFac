# Part 1 remediation: handoff for Part 2

Scope: trusted measurement integrity, energy/power semantics, PUE safety,
uncertainty, numeric validation, freshness/determinism, meter cadence, DB
initialisation, canonical calculation path. Synthetic data only. No equipment
control.

## Verification status (read this first)

The authoring sandbox had **no network**, so `pytest`, `ruff`, `fastapi` and
`pydantic` could not be installed. What was and was not run:

| Check | Result |
|---|---|
| `python -m py_compile` on every changed/new `.py` | passed |
| Stdlib-only tests (`tests/unit/test_trust_resolution.py`, `tests/integration/test_database_init.py`, `tests/integration/test_meter_cadence.py`) via a throwaway pytest stand-in | 74 passed, 0 failed |
| Same cadence tests against the OLD `batch.py` | 3 failed (the bug is reproduced), then pass on the fix |
| Pydantic/FastAPI tests (`test_operational_decision_safety`, `test_numeric_adversarial`, `test_freshness_determinism`, `test_trusted_decisions_api`, edited existing tests) | **NOT RUN** |
| Existing suite (`pytest tests/ -v`) | **NOT RUN** |
| `ruff check` | **NOT RUN** (line lengths and import order were checked by hand) |

First action for Part 2: run `ruff check src tests simulator evaluation scripts`
and `pytest tests/ -v`, and fix anything that surfaces. Expect a few
assertion/format fixes in the unrun tests; do not weaken them.

## Changes

| Area | Files | Correction |
|---|---|---|
| DB init | `persistence/database.py`, `api/dependencies.py`, `main.py`, `schema.sql`, `migrations/005_*.sql` | Locked, once-per-process `ensure_initialized`; connection always closed; failed init not cached; additive column upgrade; `check_schema`; `/ready` verifies schema |
| Cadence | `quality/assessment.py`, `detection/anomaly.py`, `detection/batch.py` | `assess_quality` defaults to the meter's `interval_minutes`; detector quality window = one interval; batch screen uses the detectors' 24 h **time** window, not 24 rows |
| Trust | `decision/trust.py` (new), `api/routes/decisions.py` | Both measurements need trusted refs; kWh/interval to kW; role/unit/fuel/interval checks; per-measurement quality; alignment; future rejection; server tolerance, max age and evaluation time |
| Numerics | `decision/numeric.py` (new), `measurements.py`, `limits.py`, `calculations.py`, `scenarios.py`, `api/schemas.py`, `transparent.py` | `allow_inf_nan=False` everywhere; PUE in [1, 10]; tolerance lower bound clamped (0 for loads, 1 for PUE) |
| Engine | `decision/transparent.py`, `evidence.py`, `store.py` | Derived uncertainty flags, PUE basis, PUE sensitivity, evaluation-time pinning, deterministic replay |
| Canonical calc | `calculations.py` | `FORMULAS`, `facility_power_kw`, `predicted_load_kw` are the single source; `_compute_facility_power` removed |
| UI | `templates/world.html`, `index.html` | New Building and Data Center SVG icons (tabs, zone/tower headers, stats) |

## Key decisions

* **Mode now means something.** `TRUSTED` is operational: derived uncertainty
  flags are *applied* (REVIEW). `HYPOTHETICAL` records the same facts as
  `advisory (not applied)` in evidence and keeps its old verdicts.
* **Two-pass evaluation.** Pass 1 = physical/quality verdict (+ caller flags).
  Sensitivity runs on pass 1. Pass 2 adds derived flags (operational only).
  `calculation_outputs.physical_status_before_uncertainty` shows pass 1.
* **Determinism by pinning, not by freezing the clock.** `evaluation_time` is
  stored in the request; fingerprint payload keys added by this work appear
  only when set, so decisions stored before it still verify.
* **Role = NULL is tolerated but caveated** (`meter_role_unclassified` forces
  REVIEW in operational mode). Rejecting it outright would break every
  auto-created meter. Part 2 may tighten this.
* **No trusted PUE source exists.** Trusted mode accepts an explicitly
  `CONFIGURED` PUE; claiming `MEASURED` is a 400; missing/assumed gives REVIEW.
* **Cooling** is a measured (or stated) load plus an explicit delta, never
  derived from IT load. Evidence records `cooling_load_basis` and
  `cooling_demand_modelled: false`. `calculate_cooling` (modelled demand) is
  only used by the legacy report path.
* Legacy `decision/constraints.py` and `decision/engine.py` are untouched and
  unused at runtime; `limits.py` + `state_machine.py` is the live path.

## Canonical interfaces

* `decision.numeric`: `require_finite`, `validate_pue_value`, `clamp_band`, `MIN_PUE=1.0`, `MAX_PUE=10.0`
* `decision.trust`: `resolve_trusted_pair`, `resolve_trusted_reading`, `energy_kwh_to_average_kw` (`kW = kWh * 60 / interval_minutes`), `server_max_age_seconds()` (env `ECOFAC_TRUSTED_MAX_AGE_SECONDS`, default 7200), `TrustError`
* `decision.calculations`: `FORMULAS`, `facility_power_kw`, `predicted_load_kw`
* `decision.transparent`: `decide_transparently`, `pinned_request`, `is_time_dependent`
* `quality.assessment`: `assess_quality(..., expected_frequency_minutes=None)` uses the meter interval; `meter_interval_minutes`
* `persistence.database`: `ensure_initialized`, `check_schema`, `init_db` (idempotent, additive)

## API / schema changes (all additive)

* `TransparentDecisionRequest`: `evaluation_time`, `inputs_resolved_by_server`, `trust_caveats`. **Server-owned in trusted mode: any caller value is overwritten.**
* `RichMeasurement` / evidence `InputReference`: `raw_value`, `raw_unit`, `interval_minutes`.
* `TransparentDecisionResult.evaluation_time`; evidence `calculation_inputs` gains `pue_trust_basis`, `pue_assumed`, `evaluation_time`, `max_measurement_age_seconds`, `stale_measurements`, `future_timestamp_measurements`, `derived_/applied_uncertainty_flags`, `cooling_load_basis`; `calculation_outputs` gains `physical_status_before_uncertainty`, `decision_reasons`.
* Verify response dict gains `evaluation_time`.
* **Behaviour changes (intended):** trusted mode needs BOTH refs (was: either); trusted values are kW converted from kWh/interval (was: kWh read as kW); trusted + unknown tolerance / assumed PUE is REVIEW (was: APPROVE); PUE < 1 or > 10 is rejected (was: any > 0); `/ready` returns 503 for an unusable schema.
* Existing tests that encoded the old behaviour were rewritten, not loosened: `test_trusted_mode_is_pure_in_decide_transparently`, `test_trusted_mode_resolves_valid_reading` (partial trust), the weak PUE-uncertainty test, and `_check_db_ready` monkeypatch in `test_middleware`.

## Database

* `meters` gains `role TEXT` (`IT_LOAD`/`COOLING`/NULL) and `relative_tolerance REAL` (fraction in (0,1), NULL = unknown). `interval_minutes` already existed (migration 002).
* Applied automatically at startup (`ADDITIVE_COLUMNS`) and shipped as `migrations/005`. `migrations/001` was not edited.
* `interval_minutes` is `NOT NULL DEFAULT 60`, so a meter whose interval was never configured is indistinguishable from a real hourly meter.

## Tests Part 2 must preserve

`test_trust_resolution`, `test_database_init`, `test_meter_cadence`,
`test_operational_decision_safety`, `test_numeric_adversarial`,
`test_freshness_determinism`, `test_trusted_decisions_api`, and the edited
`test_pue_integration`, `test_trusted_mode`, `test_decisions_routes`.

## Unresolved / limitations

* Nothing above that is marked NOT RUN has been executed.
* No API/UI to set a meter's role, tolerance or interval; set via SQL for now.
* Legacy time-dependent decisions stored without `evaluation_time` replay at their stored `decision_timestamp`.
* The 24 h detection window and alignment tolerance (0 s) are constants, not config.
* Part 2 should dedupe `build_report`'s `formula_log` strings against `FORMULAS`.
* Detection screening assumes detectors only ever *suppress* relative to `is_candidate`; keep that invariant.
