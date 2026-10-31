# EcoFac — Open Energy Intelligence for Facilities

Reproducible, explainable, evidence-driven energy intelligence for facilities.
Initial scope: **energy intelligence only**, using **synthetic data exclusively**.

## Status: Early Foundation (Part 2)

Repository skeleton, CI (lint + test), linting, and test harness exist.
**No energy intelligence logic has been implemented yet.**

## Scope (initial)

- Synthetic facilities, meters, and readings
- CSV ingestion, normalisation, data quality assessment
- Statistical baselines and anomaly detection
- Evidence-backed findings
- Incident lifecycle tracking
- Before/after energy verification
- FastAPI REST API
- Reproducible evaluation

## Explicitly out of scope

- Real BMS integrations
- Real equipment control
- Maintenance dispatch or work orders
- Financial claims of verified savings
- Paid AI dependencies
- Complex digital twins

## Limitations (read first)

1. **All data is synthetic.** No real building, meter, or BMS connection.
2. **Not production-ready.** Passing tests are not a production claim.
3. **No authentication or authorization** in this scope.
4. **SQLite is single-process**; concurrent writes unsupported.
5. **Statistical methods only**, not learned models.

## Development

Requires Python 3.11+.

~~~bash
python -m pip install -e ".[dev]"
ruff check src tests
ruff format src tests
pytest tests/ -v
~~~

## License

Apache License 2.0 — see [LICENSE](LICENSE).
