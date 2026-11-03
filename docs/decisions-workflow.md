# Decision Workflow

Every decision produced by the Decision Engine is **immutable** once
persisted. This document defines how decisions are created, superseded,
and audited.

## Creation

`POST /api/v1/decisions/evaluate` accepts a `DecisionEvaluateRequest`
(wrapping a `TransparentDecisionRequest`), runs `decide_transparently`, and
persists the result via `decision.store.persist_decision` inside a single
transaction. The transaction also:

- Registers the `ConstraintConfig` fingerprint (once per unique config);
- Emits a `DECISION_CREATED` audit event;
- Emits a `CONFIG_REGISTERED` audit event on first sighting of a config.

## Supersession

A decision **cannot be updated or deleted** — SQLite triggers enforce this.
To correct or re-run a decision:

1. Submit a new evaluation with `supersedes=<old_decision_id>` and a
   non-empty `supersession_reason`.
2. The new decision row records `supersedes=<old_id>`; the old row is
   untouched.
3. Audit log records both `DECISION_SUPERSEDED` (on the old id) and
   `DECISION_CREATED` (on the new id).

The full chain is retrievable at
`GET /api/v1/decisions/{id}/supersession-chain`.

## Re-evaluation

Re-evaluation is supersession with the **same input payload**. The resulting
decision has an identical `input_fingerprint` and `config_fingerprint`, so the
difference in outcome (if any) is attributable only to a version bump of the
engine or config. This makes version-to-version comparison trivial.

## Verification

`GET /api/v1/decisions/{id}/verify` recomputes the SHA-256 fingerprint of the
stored `request_json` and compares it to the recorded `input_fingerprint` and
`config_fingerprint`. Any mismatch (tampering, corruption) returns
`verified: false` alongside both stored and computed values.

## Config change log

Unique `(config_fingerprint, config_version)` pairs are recorded in
`decision_configs` with their first-seen timestamp. The `CONFIG_REGISTERED`
audit event marks the moment a new configuration entered the system.
