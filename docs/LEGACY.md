# Legacy modules (scheduled for removal in v2.0)

| module                                | replacement                                | status     |
|---------------------------------------|--------------------------------------------|------------|
| `src/ecofac/decision/engine.py`       | `src/ecofac/decision/state_machine.py`     | deprecated |
| `src/ecofac/decision/constraints.py`  | `src/ecofac/decision/limits.py`            | deprecated |

**Decision:** keep for one release behind a CI guard. `ARCHITECTURE.json`
shows no production caller for `engine.decide`; only tests exercise them.
Deleting them is mechanical and should not obscure the P0-1 sensitivity diff.
External consumers (if any) get one release of `DeprecationWarning` (already
asserted by `tests/unit/test_deprecated_modules.py`).

## Enforcement

`scripts/check_no_legacy_imports.py` fails CI if any file under `src/`,
`evaluation/`, `simulator/`, or `scripts/` imports these modules (the legacy
modules themselves are exempt).

## Deletion checklist (v2.0)

- [ ] delete `engine.py`, `constraints.py`
- [ ] delete `tests/unit/test_deprecated_modules.py`,
      `tests/unit/test_decision_constraints.py`,
      `tests/integration/test_decision_engine.py`
- [ ] delete `scripts/check_no_legacy_imports.py` and the `make check-no-legacy` target
- [ ] regenerate `ARCHITECTURE.json`
