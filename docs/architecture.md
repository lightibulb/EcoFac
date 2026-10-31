# Architecture

EcoFac is a monolithic Python application designed for simplicity and auditability.

## Data Flow
1. **Ingestion:** Data is uploaded via CSV. The `pipeline.py` validates headers, normalizes units to kWh, converts timestamps to UTC, and writes to SQLite.
2. **Quality Assessment:** The `assessment.py` engine checks for missing, stale, duplicate, or invalid data. It outputs boolean flags and an `is_analysis_eligible` status.
3. **Detection:** The `anomaly.py` engine computes baselines (Rolling, MAD) and flags deviations. It generates an `Anomaly` record.
4. **Evidence & Incidents:** If an anomaly is found, the `lifecycle.py` manager creates an `Incident` and an `EvidenceRecord` containing the exact mathematical proof.
5. **Verification:** The `verification.py` engine compares pre/post consumption periods to validate interventions.

## Technology Stack
- **Language:** Python 3.11+
- **Web Framework:** FastAPI
- **Validation:** Pydantic v2
- **Database:** SQLite (via stdlib `sqlite3`)
- **Testing:** pytest
- **Linting:** Ruff
