# Architecture

EcoFac is a monolithic Python application designed for simplicity, auditability,
and reproducibility. Every detection result carries its own evidence, and every
incident is traceable back to the readings that produced it.

## Data Flow

Raw CSV
│
▼
ingestion.pipeline.ingest_csv          ← validation, normalization, dedup, gap/order checks
│
▼
energy_readings (is_valid, raw_value) + data_quality_issues
│
▼
quality.assessment.assess_quality       ← completeness, freshness, duplicates, invalid
│
▼
detection.service.analyze_at            ← rolling_baseline + robust_mad, savepoint per detector
│
▼
anomalies (detector_version, detection_parameters, is_data_quality_eligible)
│
▼
incidents.lifecycle.create_incident     ← idempotent per (meter, ts) event
│
▼
evidence_records (reading_ids, detector_config, quality_flags, explanation)
│
▼
audit_events (append-only, trigger-enforced)

```

Anomalies flagged on an ineligible quality window are recorded but never
escalated to incidents. `analyze_at` wraps each detector in a savepoint so a
failed incident creation rolls back its anomaly insert, eliminating orphans.

## Modules

| Path | Responsibility |
|---|---|
| `src/ecofac/api/routes/` | FastAPI route handlers (thin; delegate to services) |
| `src/ecofac/api/schemas.py` | Pydantic response models (frozen contracts) |
| `src/ecofac/api/dependencies.py` | DB connection lifecycle with WAL + first-use init |
| `src/ecofac/domain/models.py` | Dataclasses for entities |
| `src/ecofac/ingestion/pipeline.py` | CSV parse → validate → normalize → bulk insert |
| `src/ecofac/ingestion/entities.py` | Auto-create buildings/meters from CSV headers |
| `src/ecofac/quality/assessment.py` | Quality report (missing, stale, dup, invalid) |
| `src/ecofac/detection/anomaly.py` | Detectors: rolling baseline, robust MAD |
| `src/ecofac/detection/service.py` | Orchestration: detect → gate → incident |
| `src/ecofac/detection/batch.py` | In-memory screening + bulk run |
| `src/ecofac/evidence/builder.py` | Deterministic JSON evidence payloads |
| `src/ecofac/incidents/lifecycle.py` | State machine, audit logging, idempotency |
| `src/ecofac/persistence/` | Schema, migrations, tx helper, repository |
| `src/ecofac/constants.py` | Detection thresholds, window sizes, bounds |
| `src/ecofac/timeutil.py` | UTC parsing/formatting helpers |
| `simulator/generate.py` | Multi-scenario synthetic data generator |
| `evaluation/metrics.py` | Precision, recall, F1, PR-AUC, FPR, FNR, delay |
| `evaluation/verification.py` | Before/after intervention classification |
| `evaluation/runner.py` | Full end-to-end evaluation |
| `evaluation/scenario_eval.py` | Per-scenario harness with report output |

## Deployment

The application is a single FastAPI process backed by SQLite. Deployment is
configured via `render.yaml` and verified in CI:

1. `ruff check` — lint.
2. `pytest` — unit, integration, API, evaluation, scenario tests. Includes
   `test_health` (TestClient startup) and `test_dashboard_root` (template
   rendering), which together prove the app boots.
3. `python -m evaluation.runner` — full engine runs end-to-end.
4. `python scripts/generate_openapi.py` — OpenAPI schema generates (proves
   the FastAPI app builds).
5. `python scripts/preflight.py` — optional manual check for local
   verification before a deploy; runs the same assertions plus a fresh
   file-DB initialization.

Environment variable `ECOFAC_DB_PATH` overrides the SQLite file path (default
`ecofac_api.db`). On Render's free plan the file is ephemeral; a persistent
disk requires a paid plan.

## Migrations

`schema.sql` is idempotent (`CREATE TABLE IF NOT EXISTS`) and is applied on
every first-use connection. For evolving deployments, numbered SQL files in
`migrations/` describe incremental changes to apply to existing databases.
Currently:

- `001_initial.sql` — baseline tables and indexes.
- `002_add_meter_interval.sql` — adds `meters.interval_minutes` (default 60).

## Technology Stack

- **Language:** Python 3.11+
- **Web Framework:** FastAPI
- **Validation:** Pydantic v2
- **Database:** SQLite (stdlib `sqlite3`)
- **Testing:** pytest
- **Linting:** Ruff (with a deliberately permissive ignore list to accommodate
  domain-specific branching in the pipeline and detector code)

## Decision Engine (additive)

`src/ecofac/decision/` is a pure, side-effect-free module that consumes
validated measurements from the intelligence layer and returns a deterministic
decision. It does not touch the database, the API, or the dashboard.

| Path | Responsibility |
|---|---|
| `decision/schemas.py` | Pydantic input and output schemas |
| `decision/constraints.py` | Pure math: predicted state, per-constraint evaluation |
| `decision/engine.py` | Orchestration: quality gate -> math -> decision |

**Interface summary:**
- Input: `OperationalConstraints`, `FacilityMeasurements`, `ProposedChange`,
  `DataQualitySnapshot`.
- Output: `Decision` (status, predicted state, per-constraint evaluations,
  warnings, evidence, explanation).

**Guarantees:**
- No operational threshold is hard-coded in the engine; all limits come from
  the caller's `OperationalConstraints`.
- Missing IT or cooling load, ineligible quality, or stale measurements all
  produce `INSUFFICIENT_DATA` -- the engine never treats missing values as 0.
- The engine performs no physical actions and makes no claims about equipment
  condition.
- Deterministic for identical inputs (verified by test).

## Engineering Calculation Layer (additive)

`src/ecofac/decision/calculations.py` provides the mathematical and
engineering primitives the decision engine is built on. It is a pure
function module -- no I/O, no database, no API.

**Two PUE models coexist:**

- `decision/constraints.py` derives PUE from IT + cooling measurements (real-
  time monitoring model).
- `decision/calculations.py` treats PUE as a supplied value (measured,
  configured, or assumed) and computes facility power as `IT * PUE` (capacity
  planning model).

The two are independent and intentional. Neither imports the other.

**Exposed calculations:**

| Function | Purpose |
|---|---|
| `calculate_electrical` | facility power, physical headroom, safety-adjusted headroom |
| `calculate_cooling` | heat load (IT-electrical approximation), physical headroom, safety-adjusted headroom |
| `compute_current_state` | combined electrical + cooling snapshot |
| `compute_predicted_state` | same, after one or more `LoadChange` deltas |
| `build_report` | full `CalculationReport` with formula log and assumptions |

**Modeling note:**

The `heat_conversion_factor` on `CoolingCapacity` defaults to `1.0`, encoding
the approximation that IT electrical load equals IT heat output. A future
airflow or thermal-efficiency model can override it per call without changing
this module's interface. This approximation is documented in every
`CalculationReport.assumptions`, so consumers cannot use the numbers without
seeing the caveat.
