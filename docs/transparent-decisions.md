# Transparent Decisions

The `decision.transparent` module wraps the deterministic state machine with
uncertainty assessment, sensitivity analysis, evidence assembly,
explanation generation, and reproducibility capture.

## Guarantees

- **No fabricated confidence.** Confidence is categorical
  (`OK`/`DEGRADED`/`UNUSABLE`, `LOW`/`MODERATE`/`HIGH`/`UNKNOWN`).
- **No invented tolerances.** If no `UncertaintyMetadata` is supplied, the
  band is `None` and sensitivity analysis abstains.
- **Missing measurements force REVIEW.** A missing `it_load` or
  `cooling_load` sets `quality_eligible=False` and adds a `missing_*` flag.
- **Every decision is reproducible.** `ReproductionFingerprint` stores
  SHA-256 hashes of canonical input + config plus the full evidence bundle.
- **No anomaly field.** The decision context cannot be influenced by
  statistical detection outputs.

## Reading the Result

`TransparentDecisionResult.explanation["what_would_change_decision"]` is
populated **only for REVIEW decisions** and lists the concrete actions or
information that could resolve the review.

`TransparentDecisionResult.sensitivity` is populated **only for measurements
with explicit tolerance metadata**. If a tolerance band contains a value that
flips the verdict, `could_change_verdict` is `True`.
