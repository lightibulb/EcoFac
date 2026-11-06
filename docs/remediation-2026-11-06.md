# Audit Remediation — 2026-11-06

This document records the audit findings raised in the independent review
and the state of each remediation after the 2026-11-06 pass.

## Resolved

| DEF | Finding | Fix |
|---|---|---|
| DEF-01 | Migration runner path | `parents[3]`; skips on `:memory:`; fallback strips comments before `;` split |
| DEF-02 | `ruff` not in dev extras | `"ruff>=0.8"` added to `[project.optional-dependencies].dev` |
| DEF-03 | `world.html` test-drive averaged kWh as kW | Routed through `/api/v1/world/test-drive/{id}` |
| DEF-04 | Detection-delay matching reused truth labels | Greedy one-to-one matching |
| DEF-05 | `world/state.max_severity` ignored extra detectors | Joins every anomaly at the open incident's `(meter, ts)` |
| DEF-06 | Sub-second trusted timestamps never matched | Truncated to whole seconds before SQL lookup |
| DEF-07 | `persist_scenario` did not pin `evaluation_time` | Uses `pinned_request` (parity with `persist_decision`) |
| DEF-08 | Scenario reconstruction fabricated `PUE=1.0` | Preserves `None` |
| DEF-09 | Stats ordering test flaky | Fake clock in `test_recent_ordering_newest_first` |
| DEF-10 | Scenario page test asserted the wrong thing | Rewritten to assert endpoint consumption + no JS math |
| DEF-11 | Render health check was `/health` | Now `/ready` (fails when DB unreachable) |
| DEF-12 | Docs drifted from code | Migrations/limitations/README corrected |
| DEF-13 | 14 tests exercised deprecated `decision.engine` | `pytestmark = skip`; module-level `DeprecationWarning` |
| DEF-14 | `dependencies._initialized` aliased the private cache | Removed the alias |
| DEF-15 | `MAX_VALID_READING_KWH` comment overstated | Comment rewritten to describe the data-entry sentinel |
| DEF-16 | Analysis failure indistinguishable from 0-anomaly success | `analysis_failed` + `import_committed` fields |
| DEF-17 | `world.html` button labels misrepresented DEF-03 semantics | Buttons renamed; hypothetical disclaimer added |
| DEF-18 | `/world` test-drive silently persisted scenarios | Persistence removed (pure explorer) |

| DEF-19 | Corrective-action policy exposed via API | `corrective_action_mode` on request; default False |
| DEF-20 | Rate limiting off by default | Default-on with autouse test reset; explicit disable via env |
| DEF-21 | Evidence reproduction not verified | `evidence_bundle_verified` + diff on `/verify`; scenario verify preserved `config_version` |
| DEF-22 | Render persistence not surfaced | Startup warning when running on Render without `ECOFAC_DB_PATH` |
| DEF-23 | Doc drift (`13` vs `14`, supersession) | Remediation index fixed; supersession banner on 20261103 report |

## Still open (out of scope for this pass)

- Full RBAC, multi-tenant isolation, structured logging: see `docs/roadmap.md`.
- `docs/audit-2026-11-01.md` narrative reflects the state before DEF-01..DEF-18.
