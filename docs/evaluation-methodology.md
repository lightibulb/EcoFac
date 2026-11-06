# Evaluation Methodology

EcoFac includes a reproducible evaluation framework (`evaluation/`) to measure accuracy.

## Synthetic Data

The `simulator/` generates 21,600 readings (5 buildings, 2 meters, 90 days) with:

- ~230 injected anomalies (spikes). Ground truth is reconciled AFTER the 1%
  missing-data drop, and labels without a full 24-reading history are
  removed, so every label is detectable in principle.
- Missing data scenarios (1% dropped).
- Duplicate data scenarios (1% duplicated).
- Stale data scenarios (3 days of halted updates).

The simulator also emits `quality_ground_truth.json` (injected defect counts)
so ingestion capture rates are measurable.

## Detection Metrics

- **Precision:** True Positives / (True Positives + False Positives)
- **Recall:** True Positives / (True Positives + False Negatives)
- **F1 Score:** Harmonic mean of Precision and Recall.
- **PR-AUC:** Area under the Precision-Recall curve, computed by ranking all
  predicted anomalies by |z-score| (stored in `detection_parameters`) and
  sweeping the decision threshold (trapezoidal integration).
- **False Alerts / Bldg / Week:** Normalized noise metric.

## Detection Method

Both detectors first compare a reading with the trailing 24 valid readings
(mean/stdev and median/MAD). A reading that passes is then confirmed against
the same hour on the 5 most recent days of the same type (weekday or
weekend); if it matches that routine (|z| < 3.5) it is not reported. With fewer
than 3 comparable readings the trailing verdict stands. Evaluation seed 42:
precision 0.805, recall 0.987, F1 0.887 (before confirmation: 0.265 / 0.996 / 0.419).

## Verification Outcomes

Interventions are classified as SUCCESSFUL, FAILED, PARTIAL_IMPROVEMENT,
RECURRENCE, or INCONCLUSIVE. Baseline and post windows use canonical `Z`
timestamp bounds and exact day counts.

## Reproducibility Guardrails

- The runner refuses to publish (non-zero exit) if zero ground-truth
  anomalies are detected — silent blindness is structurally impossible.
- `tests/evaluation/test_runner_canary.py` plants known spikes and asserts
  the full analysis path finds them.
- Incidents are only created for anomalies whose data-quality window is
  eligible; ineligible findings are reported as gated.

## Time-Aware Evaluation
- Candidate eligibility is now based on a 24-hour elapsed time window rather than a simple row count.
- Detection delay matching uses exact one-to-one timestamp matching to prevent ground-truth reuse.

