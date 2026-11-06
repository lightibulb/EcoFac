# EcoFac — Open Energy Intelligence for Facilities

**Tagline:** Open Energy Intelligence for Facilities

EcoFac is a reproducible, explainable, evidence-driven energy intelligence engine for facilities. It ingests synthetic meter data, assesses data quality, computes historical baselines, detects anomalies, attaches deterministic evidence, and tracks incidents through a defined lifecycle.

## ⚠️ Important Disclaimers
- **Synthetic Data Only:** EcoFac is not connected to any real building, meter, or BMS.
- **Not Production-Ready:** A successful test run or demonstration does not constitute production readiness.
- **No Authentication:** This is a local demonstration API. Do not expose it to untrusted networks.
- **No Equipment Control:** This system cannot and does not control physical equipment.

## Scope (Initial)
- Synthetic building and meter records
- CSV-based energy data ingestion
- Data normalization and quality assessment
- Historical energy baselines and anomaly detection
- Evidence-backed findings and incident tracking (one incident per event, one evidence record per detector)
- Automatic anomaly analysis after every CSV import and demo-data load
- Before/after energy verification
- FastAPI REST API
- Reproducible automated evaluation

## Explicitly Out of Scope
- Real BMS integrations
- Real equipment control
- Automatic maintenance dispatch
- Work-order management
- Financial claims of verified savings
- Paid AI dependencies
- Complex digital twins

## Architecture
EcoFac is built with Python 3.11+, FastAPI, and SQLite. It follows a strict modular architecture:
- `src/ecofac/api`: FastAPI routes and Pydantic schemas.
- `src/ecofac/domain`: Core dataclasses.
- `src/ecofac/ingestion`: CSV parsing and normalization.
- `src/ecofac/quality`: Data quality assessment.
- `src/ecofac/detection`: Statistical anomaly detection.
- `src/ecofac/incidents`: Incident lifecycle and evidence.
- `simulator`: Synthetic data generation.
- `evaluation`: Metrics and verification engines.

## Setup Instructions
Requires Python 3.11+.
```bash
python -m pip install -e ".[dev]"
```

## Test Instructions
```bash
# Run linting
ruff check src tests simulator evaluation scripts

# Run all tests
pytest tests/ -v

# Run the evaluation framework
python -m evaluation.runner
```

## API Documentation
Once the server is running (`uvicorn ecofac.main:app --reload`), visit `http://127.0.0.1:8000/docs` for the interactive OpenAPI documentation.

## Limitations
See [docs/limitations.md](docs/limitations.md) for a complete list. Key limitations include:
- SQLite is single-process.
- Statistical methods only (no deep learning).
- Anomalies are statistical, not confirmed operational faults.
