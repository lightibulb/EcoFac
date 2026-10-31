# EcoFac Fix Log — 20261031-165629

Applied by ecofac_fix_all.sh. Originals preserved in .ecofac-fix-backup-20261031-165629/.

## P0 (silent correctness)
1. evaluation/runner.py — detectors now receive meter_id (was reading UUID);
   analysis loop extracted as analyze_all(); exit guard on zero TPs.
2. evaluation/metrics.py — real PR-AUC via |z|-ranked threshold sweep.
3. quality/assessment.py + schema.sql — duplicates scoped by meter+window via
   typed columns (was unscoped LIKE on prose).

## P1 (logic/robustness)
4. timeutil.py (NEW) — canonical parse_utc/to_z/now_z; ingestion rejects naive
   timestamps instead of reading them as server-local time.
5. imports route — invalid source_id now returns 400 (rowcount check).
6. incidents routes — added /review and /resolve (full state machine exposed).
7. intelligence routes — naive timestamps return 400 instead of 500.
8. verification.py — post window now exactly post_days; Z-normalized bounds.

## P2 (methodology)
9. simulator — ground truth reconciled after missing-drop; spikes only on
   positive values; quality_ground_truth.json emitted.
10. Eligibility gate enforced (runner + /analyze) — no incidents on bad data.
11. Runner runs a synthetic intervention and persists verification_runs.
12. anomalies UNIQUE(meter_id, timestamp_utc, detector_name) + pre-check.
13. Detectors ignore invalid target readings.
14. Report labels corrected (rejected vs missing).

## P3 (hygiene)
15. Incident dataclass synced with schema (anomaly_id).
16. Indexes on energy_readings(meter_id, timestamp_utc) and data_quality_issues.
17. init_db cached per DB path.
18. tree.txt / LICENSE / README / methodology doc corrected.

## Tests added/updated
- tests/evaluation/test_runner_canary.py (NEW)
- tests/api/test_regressions.py (NEW)
- tests/integration/test_regressions.py (NEW)
- tests/integration/test_quality.py (typed duplicate columns + scoping test)
