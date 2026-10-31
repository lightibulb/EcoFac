# Evaluation Methodology

EcoFac includes a reproducible evaluation framework (`evaluation/`) to measure accuracy.

## Synthetic Data

The `simulator/` generates 21,600 readings (5 buildings, 2 meters, 90 days) with:

- 40+ injected anomalies (spikes). Ground truth is reconciled AFTER the 1%
  missing-data drop, so every label is detectable in principle.
- Missing data scenarios (1% dropped).
- Duplicate data scenarios (1% duplicated).
- Stale data scenarios (3 days of halted updates).

The simulator also emits `quality_ground_truth.json` (injected defect counts)
so ingestion capture rates are measurable.

## Detection Metrics

- **Precision:** True Positives / (True Positives + False Positives)
- **Recall:** True Positives / (True Positives + True Negatives-free denominator)
- **F1 Score:** Harmonic mean of Precision and Recall.
- **PR-AUC:** Area under the Precision-Recall curve, computed by ranking all
  predicted anomalies by |z-score| (stored in `detection_parameters`) and
  sweeping the decision threshold (trapezoidal integration).
- **False Alerts / Bldg / Week:** Normalized noise metric.

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
