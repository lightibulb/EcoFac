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
