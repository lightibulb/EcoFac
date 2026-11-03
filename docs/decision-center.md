# Decision Center

The Decision Center exposes the decisions produced by the EcoFac Decision
Engine as an inspectable, evidence-backed interface. It is accessible from
the main dashboard's primary navigation.

## URL

`GET /decisions` — server-rendered HTML page (Jinja2 template `decisions.html`).
Client-side JS fetches data from the read-only Decision API; every engineering
value is rendered verbatim from the backend.

## Sections

1. **Decision Overview** — total / approved / rejected / review counts, latest
   decision, recent activity. Sourced from `GET /api/v1/decisions/stats`.
2. **Engineering Snapshot** — current vs predicted IT load, facility power,
   cooling load, PUE, plus headroom to permitted and physical bounds.
   Sourced from `GET /api/v1/decisions/{id}/engineering`.
3. **Decision Explanation** — status, precedence, limiting constraint,
   passed/warning/failed constraints, uncertainty levels, sensitivity table,
   and "what would change the outcome" hints.
   Sourced from `GET /api/v1/decisions/{id}`.
4. **Decision History** — paginated list with status filter and supersession
   indicators. Sourced from `GET /api/v1/decisions`.
5. **Integrity Verification** — per-decision "Verify integrity" button.
   Sourced from `GET /api/v1/decisions/{id}/verify`.

## Backend contract

| Endpoint | Used for |
|---|---|
| `GET /api/v1/decisions/stats` | Overview strip and recent-activity list |
| `GET /api/v1/decisions` | History list (paginated, filtered) |
| `GET /api/v1/decisions/{id}` | Inspector detail panel |
| `GET /api/v1/decisions/{id}/engineering` | Engineering Snapshot |
| `GET /api/v1/decisions/{id}/verify` | Verify integrity button |

The page **never** computes headroom, reserve margins, uncertainty, or
decision outcomes in JavaScript. All numeric values rendered in the inspector
are read verbatim from `evidence.calculation_inputs` and the extracted
`/engineering` summary.

## Verification guarantee

The verify button reports:
- Input fingerprint match
- Config fingerprint match
- Top-level (status, precedence) match after re-running the engine
- Whether the stored engine version equals the current engine version

`verification_scope` is always `input_config_output_status`.
`full_bundle_reproduced` is always `false` — the full evidence bundle is not
reproduced, only the top-level outcome.

## Security

- The Decision Center is read-only.
- All read endpoints are public (synthetic data).
- No new write paths are introduced by this page.
- The disclaimer banner makes it explicit that decisions are advisory only.

## Limits

- A decision record from before the PUE integration has no `current_facility_power_kw`
  in its evidence. `GET .../engineering` returns **422** with a clear message
  for such records rather than fabricating values.
- The page refreshes the overview and history every 30 s. The selected
  decision is only re-fetched on an explicit Refresh click so an inspection
  session is not interrupted.

## Decision Inspector

The inspector is the full detail view for a selected decision. It is opened by
clicking any row in the Decision history list, the Latest decision card, or an
item in Recent activity. Sections use the native `<details>` element so they
are keyboard-accessible, screen-reader-navigable, and can be collapsed
individually.

### Sections

1. **Decision identity** (always visible) — ID, status, precedence, created /
   decision timestamps, engine version, configuration version, mode.
2. **Summary** (always visible) — the engine's human-readable explanation,
   limiting constraint, remaining headroom.
3. **Engineering snapshot** (open by default) — current vs predicted IT load,
   facility power, cooling load, PUE plus its provenance, electrical and
   cooling headroom to both permitted and physical bounds. If the record
   predates the PUE integration, a clearly-marked "Engineering evidence
   unavailable" warning is shown instead of fabricated zeros.
4. **Proposed change** (collapsed) — submitted IT and cooling load deltas,
   plus the effective deltas the engine used (with a note when they differ),
   plus every `invalid_proposal_reason`.
5. **Input measurements** (collapsed) — per-measurement value, unit, source
   (provenance), reference ID, meter ID, timestamp, and tolerance metadata.
   Followed by quality eligibility / staleness / flags.
6. **Constraint inspection** (collapsed) — every evaluated constraint for
   both current and predicted state, including name, severity, observed,
   permitted limit, physical capacity, headroom to both, percent used, and
   the engine's reason string. The `is_limiting` constraint is marked.
7. **Evidence & reproducibility** (collapsed) — input and config fingerprints
   (truncated with full value in the tooltip), engine / config versions,
   input references, formulas used, assumptions, constraints snapshot, and
   the Verify button with its inline result.
8. **Uncertainty** (collapsed) — data-quality level, model uncertainty level,
   tags for missing / stale / low-quality / estimated / tolerances / conflicts,
   rationale, assumptions, and model limitations. Levels and tags come
   verbatim from the backend — no inference in the browser.
9. **Sensitivity analysis** (collapsed) — whether any measurement's tolerance
   band could flip the verdict.
10. **Why this decision?** (collapsed) — a precedence-driven plain-English
    reason, raw recorded reasons, unresolved conditions, passed/warning/failed
    constraints, actionable "what would change the outcome" hints, and an
    "Inputs influencing the outcome" note about PUE provenance and estimated
    parameters.

### Guarantees

- **No client-side engineering math.** Facility power, headroom, reserve
  margins, uncertainty levels, and outcomes come from the API. The JS never
  divides, subtracts, or classifies.
- **No fabricated explanations.** If a section's data is absent (e.g. an old
  record lacks engineering evidence), the section shows an explicit
  "unavailable" message — never an inferred or blank value.
- **No inferred certainty.** Confidence indicators are only shown when the
  backend supplies them (`data_quality_level`, `model_uncertainty_level`).
- **Verification is scoped honestly.** The button documents that it verifies
  `input_config_output_status` only; `full_bundle_reproduced` is always
  shown as `no`.

### Verification limitation

The Verify action recomputes the input fingerprint, the configuration
fingerprint, and re-runs the engine to confirm the top-level status matches.
It does **not** reproduce the full evidence bundle — assumptions, formulas,
and per-constraint margins are read from storage, not recomputed.
`verification_scope` is `input_config_output_status` and
`full_bundle_reproduced` is `false` for every decision.
