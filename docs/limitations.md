# Known Limitations

1. **Synthetic Data Only:** EcoFac is not connected to real equipment.
2. **Not Production-Ready:** Does not include authentication, rate limiting, or horizontal scaling.
3. **SQLite Concurrency:** Single-process only. Concurrent writes will fail with "database is locked".
4. **Statistical Detection:** Detects mathematical deviations, not confirmed operational faults.
5. **No Weather Normalization:** The baseline does not account for outdoor temperature or occupancy.
6. **Evaluation Runner Performance:** The evaluation script runs detection on every single reading sequentially. This takes a few seconds for 21k readings but would be slow for millions.
