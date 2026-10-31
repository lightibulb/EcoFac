# Data Schema

The database is initialized via `src/ecofac/persistence/schema.sql`.

## Core Tables
- **organizations:** Represents the top-level entity.
- **buildings:** Physical structures belonging to an organization.
- **meters:** Measurement devices associated with a building.
- **energy_readings:** Normalized, time-series observations. Includes a `raw_value` column to preserve the original input.
- **data_quality_issues:** Logs of anomalies in the data itself (e.g., duplicates, invalid units).
- **anomalies:** Statistical deviations detected by the engine.
- **incidents:** Lifecycle tracking for anomalies.
- **evidence_records:** Immutable JSON proofs supporting an incident.
- **audit_events:** Append-only log of state changes.

## Integrity Rules
- All timestamps are stored in UTC ISO-8601 format.
- `UNIQUE(meter_id, timestamp_utc)` prevents duplicate readings.
- Missing readings are not filled; they are handled by the quality engine.
