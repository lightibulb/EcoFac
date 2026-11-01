# EcoFac Priority 1-4 Remediation Notes

## Priority 1: Evaluation integrity

- Detection labels are reconciled against readings that survive simulated
  missingness. A removed reading can no longer remain a false-negative label.
- The simulator emits `quality_ground_truth.json` for injected missing and
  duplicate defects.
- PR-AUC uses threshold groups for tied scores and explicit curve endpoints.
- Regression coverage lives in `tests/evaluation/test_priority_remediation.py`.

## Priority 2: Synthetic demo reproducibility and transparency

- The default generator seed is random. Set `ECOFAC_SEED=1234` to reproduce a
  scenario, or call `generate_data(seed=1234)` directly.
- Every generation writes `scenario_manifest.json` with the seed, scenario
  configuration, counts, exact injected anomaly timestamps, baseline values,
  observed values, and injection multipliers.
- `ground_truth.json` and `quality_ground_truth.json` are generated beside the
  CSV file. Treat these files as synthetic evaluation artifacts, not telemetry.

## Priority 3: Input and detection reliability

- CSV ingestion rejects NaN and infinity as malformed numeric values.
- A timestamp is marked as seen only after unit and numeric validation, so an
  invalid first row does not suppress a later valid row for that meter/time.
- Negative consumption remains an explicitly invalid reading, not silently
  converted into a normal reading.

## Priority 4: Production hardening gate

The following are deployment blockers, not features this patch can safely
invent in isolation. Complete and test them before exposing a real deployment:

1. Authentication: select an identity provider and implement token/session
   validation for every protected route. Health endpoints must be deliberately
   classified as public or protected.
2. Authorization/RBAC: define organization, building, meter, and incident
   permissions; enforce authorization server-side on every query and mutation.
3. Tenant isolation: add tenant ownership to persistence constraints and test
   cross-tenant read/write denial. Do not rely on frontend filtering.
4. Rate limiting and abuse controls: apply per-identity and per-IP limits,
   request-size limits, import quotas, and audit logging at the API boundary.
5. Database: migrate production workloads from single-process SQLite to a
   managed PostgreSQL deployment; add migrations, backups, restore drills,
   connection pooling, and concurrency tests.
6. Secrets and transport: use managed secrets, TLS, restrictive CORS, secure
   headers, dependency scanning, and secret scanning in CI.
7. Observability: structured logs, request IDs, error reporting, health/readiness
   probes, metrics, alert thresholds, and retention policies.
8. Data governance: retention/deletion policy, export controls, tenant-scoped
   audit trail, and documented handling of building operational data.
9. Operational validation: weather/occupancy normalization, comparable-period
   baselines, load testing, and independent review of anomaly thresholds.

Until these are implemented, keep the app in synthetic/demo mode and do not
represent statistical anomalies as confirmed faults or simulated reductions as
verified financial savings.
