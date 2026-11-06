# Known Limitations

EcoFac is a demonstration system built on synthetic data. It is **not
production-ready**. This file documents what it does not do, and what it does
not do well.

## Data and Detection

1. **Synthetic data only.** EcoFac is not connected to any real meter, BMS,
   or building. Do not treat its outputs as observations of physical systems.
2. **Statistical anomalies, not equipment faults.** Both detectors flag
   readings whose distance from baseline exceeds a threshold. A flagged
   reading is evidence to investigate, not proof of malfunction. The system
   makes no attempt to distinguish sensor failure from equipment failure from
   legitimate unusual operation.
3. **Detector thresholds are module-level constants.** Values like
   `ROLLING_Z_THRESHOLD = 3.0` in `constants.py` can be overridden per call
   via the detector function's keyword arguments, but the API surface does not
   expose threshold tuning. Per-meter tuning requires a code change.
4. **Comparable-period check uses UTC only.** The routine-match (same hour,
   same weekday/weekend, last 5 matching days) is computed against UTC hours.
   Sites in other time zones or with non-Saturday/Sunday work calendars get a
   shifted routine. Holidays are not modelled.
5. **RECURRENCE classification is asymmetric.** The verification engine flags
   a post-period spike as RECURRENCE when any post reading exceeds
   `baseline_mean + max(3·baseline_stdev, 20%·baseline_mean)`. Flat baselines
   (stdev ≈ 0) make RECURRENCE easy to trigger; noisy baselines make it nearly
   impossible. A better formula is deferred pending re-baselining of evaluation
   metrics.
6. **No weather or occupancy normalization.** Baselines do not account for
   outdoor temperature, humidity, or occupancy. On real data, most
   "anomalies" would be weather responses.

## Data Quality

7. **Quality windows are hard-coded to 60-minute cadence.** Both detection
   and quality assessment assume hourly metering. Sub-hourly meters get a
   misleading `expected_readings` count and an artificially strict eligibility
   gate. The schema supports a per-meter `interval_minutes` column, but
   `assess_quality` does not yet read it.
8. **Gap threshold is `2× interval_minutes`.** Single-missing-reading gaps are
   tolerated; larger gaps are logged as `irregular_sampling` but readings are
   still ingested. Gaps do not lower quality eligibility in the current
   detector integration; only missing / duplicate / invalid / stale readings do.
9. **Out-of-order detection is informational.** Readings arriving out of
   chronological order in a CSV are flagged but still stored. This is
   correct for backfill scenarios; it is not a substitute for a real
   streaming-order guarantee.

## Persistence

10. **SQLite is single-writer.** Concurrent imports from multiple processes
    will lock the database. `BEGIN IMMEDIATE` + `timeout=30` mitigates within
    one process, but multi-process deployments should migrate to Postgres.
11. **Ephemeral storage on Render free tier.** The SQLite file is lost on
    redeploy or spin-down. Persistent disk requires a paid plan.
12. **Migrations require manual application.** `schema.sql` is idempotent and
    auto-applied; numbered files in `migrations/` are not. A deployed
    database must be upgraded by running the migration SQL manually.

## Security

13. **No authentication or authorization.** Every API endpoint is public. Do
    not expose to an untrusted network.
14. **No rate limiting.** A client can flood upload or analyze endpoints.
    File-size (5 MB) and row-count (50k) limits exist but do not prevent
    request flooding.
15. **No tenant isolation.** All data lives in one database with no ownership
    enforcement. Multi-tenant operation would require schema changes.

## Evaluation

16. **PR-AUC uses detector z-scores as the ranking signal.** Predictions that
    lack a `z_score` field (older records) are ranked at score 0, biasing
    curves downward. New records always include `z_score` and
    `detector_version`.
17. **Metrics are computed against the reconciled ground truth.** Labels
    dropped by simulation missingness or lacking 24 readings of history are
    removed before evaluation. This is fair to the detector but means the
    reported recall is a lower-bound on the detector's ability against
    un-reconciled labels.
18. **Scenario evaluation runs one seed at a time in CI.** The scenario
    harness supports multiple seeds (`run_scenario_evaluation(seeds=(42, 43))`),
    but only seed 42 runs in the default `evaluation.runner` path.

## Operational

19. **No structured logging or request IDs.** Errors surface as HTTP 500s
    with generic detail strings. Production deployment would need structured
    logs, tracing, and health/readiness probes beyond `/health`.
20. **No backup or restore drills.** SQLite file is the single source of
    truth; no backup automation exists.


## Decision Engine (updated)

The canonical decision path is `decision.transparent`. It computes
**facility power = IT load × PUE** before evaluating the electrical
constraint. PUE provenance (MEASURED/CONFIGURED/ASSUMED) is recorded
in every decision. When PUE is not supplied it defaults to 1.0 with an
explicit ASSUMED provenance note in the evidence bundle.

Negative proposed loads (predicted IT or cooling < 0) are rejected
with precedence `invalid_proposal`. Negative measurement values are
rejected at the schema boundary.

Measurement units are validated: loads must be `kW`, PUE must be
`dimensionless`. Freshness can be enforced server-side via
`max_measurement_age_seconds`.

Uncertainty classification returns `UNKNOWN` (not `LOW`) when no
tolerance metadata is supplied, because measured is not the same as
accurate.

Trusted mode (`mode="trusted"`) resolves measurement values and
quality flags from the database instead of trusting the caller.


## Part 2.2 operational foundations

Implemented in this milestone (single-process, no extra infrastructure):

- **`X-Request-ID`** on every request/response. Client-supplied IDs are
  echoed so distributed traces can correlate.
- **Defensive headers** (`X-Content-Type-Options: nosniff`,
  `X-Frame-Options: DENY`, `Referrer-Policy: no-referrer`) on every
  response. No CSP is set — the HTML pages use inline scripts/styles.
- **`/ready`** endpoint distinct from `/health`. `/health` is a liveness
  probe; `/ready` queries the DB and returns 503 when unreachable.
- **In-process rate limiting** — opt-in via
  `ECOFAC_RATE_LIMIT_ENABLED=true`. Defaults: 600 reads/min, 60
  writes/min per client identity (`X-EcoFac-Token` if present, else
  `X-Forwarded-For`, else client IP). Per-process state; a
  multi-process deployment would need shared state.

Still missing before production:

- Authentication remains env-gated (`ECOFAC_WRITE_TOKEN`) and off by
  default.
- Authorization (RBAC) is not implemented.
- Rate-limit state is not shared across processes.
- No structured logging / request-ID propagation into log records yet.
- No Content-Security-Policy (would require refactoring inline scripts).


## Virtual Facility visualization

The `/world` page renders a data-driven view of the demo facility. Known
limitations:

- **Polling, not streaming.** Refreshes every 5 s while the tab is
  visible. Not a WebSocket stream.
- **Single-process assumption.** Polling works from any client; state is
  per-request, no server-side push.
- **No thermal/airflow modeling.** Rack colors reflect incident counts
  and reading validity, not temperature or airflow (neither is modeled
  by the backend).
- **Two views, one dataset.** Data Center (floor plan) and Building
  (skyline) are different layouts of the same `/api/v1/world/state`
  response.
- **Test drive actions require auth only if the deployment sets
  `ECOFAC_WRITE_TOKEN`.** On a public read-only deployment, the
  scenario-evaluation button will return 401 unless the deployment is
  configured to allow writes. This is consistent with the rest of the
  app.

## Added by the Part 1 remediation
7. **Operational decisions are conservative by design.** In `trusted` mode an unknown tolerance, assumed PUE, unclassified meter role or a tolerance band that could change the verdict yields REVIEW, not APPROVE. See `docs/part1-handoff.md`.
8. **No trusted PUE source.** Trusted mode accepts only an explicitly configured PUE.
9. **Meter metadata is set via SQL.** `interval_minutes`, `role` and `relative_tolerance` have no API yet.


## Database Migrations
- A versioned migration runner is now integrated. Migrations in the `migrations/` folder are applied automatically and tracked in the `_schema_version` table.

