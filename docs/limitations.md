# Known Limitations

1. **Synthetic Data Only:** EcoFac is not connected to real equipment.
2. **Not Production-Ready:** Does not include authentication, rate limiting, or horizontal scaling.
3. **SQLite Concurrency:** Single-process only. Concurrent writes will fail with "database is locked".
4. **Statistical Detection:** Detects mathematical deviations, not confirmed operational faults.
5. **No Weather Normalization:** The baseline does not account for outdoor temperature or occupancy.
6. **Analysis Cost:** Bulk analysis screens in memory, then runs the detectors only on candidates (about 1 second for 21k readings). Millions of readings per request would still be slow.
7. **Routine Matching Uses UTC:** The comparable-period check (same hour, same weekday/weekend type, last 5 matching days) uses UTC hours and Saturday/Sunday as the weekend. Sites in other time zones, or with other work calendars, get a shifted routine. Holidays are not modelled.
8. **Ephemeral Storage:** On Render's free plan the SQLite file is lost on redeploy or spin-down. A persistent disk requires a paid plan.
