# Evaluation Methodology

EcoFac includes a reproducible evaluation framework (`evaluation/`) to measure accuracy.

## Synthetic Data
The `simulator/` generates 21,600 readings (5 buildings, 2 meters, 90 days) with:
- 40+ injected anomalies (spikes).
- Missing data scenarios (1% dropped).
- Duplicate data scenarios (1% duplicated).
- Stale data scenarios (3 days of halted updates).

## Detection Metrics
- **Precision:** True Positives / (True Positives + False Positives)
- **Recall:** True Positives / (True Positives + False Negatives)
- **F1 Score:** Harmonic mean of Precision and Recall.
- **PR-AUC:** Area under the Precision-Recall curve.
- **False Alerts / Bldg / Week:** Normalized noise metric.

## Verification Outcomes
Interventions are classified as:
- **SUCCESSFUL:** Statistically significant drop (> 5%).
- **FAILED:** Statistically significant increase (> 5%).
- **PARTIAL_IMPROVEMENT:** Minor drop (< 5%).
- **RECURRENCE:** A new spike appears in the post-period.
- **INCONCLUSIVE:** Insufficient data or no change.
