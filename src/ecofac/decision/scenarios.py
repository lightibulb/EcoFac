# Copyright 2026 Lightibulb
"""Scenario Intelligence — hypothetical decision exploration.

A scenario wraps a caller-supplied set of engineering inputs, runs the
canonical decision engine (``decide_transparently``), and persists the
result. Scenarios live in their own table so operational decision history
is not polluted by hypothetical explorations unless the caller explicitly
sets ``persist_as_decision=True``.

Invariants:
  - Every evaluation goes through ``decide_transparently``. There is no
    parallel decision path here.
  - NaN/infinity are rejected at the scenario boundary (Pydantic does not
    reliably reject them via ``Field(ge=0)``).
  - Negative predicted physical loads are classified by the engine
    (``REJECT / invalid_proposal``), not silently clamped.
  - All measurement sources default to ``ASSUMED`` because the caller is
    supplying hypothetical values, not readings from a real meter.
"""

import json
import math
import sqlite3
import uuid

from ecofac.decision.limits import ConstraintConfig
from ecofac.decision.measurements import (
    MeasurementSource,
    RichMeasurement,
)
from ecofac.decision.numeric import validate_pue_value
from ecofac.decision.reproducibility import compute_fingerprint
from ecofac.decision.state_machine import (
    ENGINE_VERSION as CURRENT_ENGINE_VERSION,
)
from ecofac.decision.store import persist_decision
from ecofac.decision.transparent import (
    DecisionMode,
    TransparentDecisionRequest,
    TransparentDecisionResult,
    build_config_payload,
    build_input_payload,
    decide_transparently,
)
from ecofac.persistence.tx import persist_atomically
from ecofac.timeutil import now_z


def default_config_version(config_dump: dict) -> str:
    """Derive a stable short config_version from the config payload."""
    fingerprint = compute_fingerprint(config_dump)
    return f"cfg-{fingerprint[:12]}"


def validate_scenario_numerics(
    *,
    it_load_kw: float,
    cooling_load_kw: float,
    pue: float,
    proposed_it_delta_kw: float,
    proposed_cooling_delta_kw: float,
    config_dump: dict,
) -> None:
    """Reject NaN / infinity before they reach the engine.

    Pydantic ``Field(ge=0)`` does NOT reject NaN or infinity reliably, so
    they are checked explicitly here.
    """
    checks: list[tuple[str, object]] = [
        ("it_load_kw", it_load_kw),
        ("cooling_load_kw", cooling_load_kw),
        ("pue", pue),
        ("proposed_it_delta_kw", proposed_it_delta_kw),
        ("proposed_cooling_delta_kw", proposed_cooling_delta_kw),
    ]
    for section, key in (
        ("electrical", "capacity"),
        ("cooling", "capacity"),
        ("electrical", "reserve_pct"),
        ("cooling", "reserve_pct"),
        ("electrical", "warning_threshold_pct"),
        ("cooling", "warning_threshold_pct"),
        ("electrical", "min_headroom"),
        ("cooling", "min_headroom"),
    ):
        checks.append((f"config.{section}.{key}", config_dump[section][key]))
    for path, value in checks:
        if isinstance(value, bool) or not isinstance(value, int | float):
            msg = f"Scenario {path} must be a number (got {value!r})"
            # Deliberately ValueError: the API layer converts it to HTTP 400.
            raise ValueError(msg)  # noqa: TRY004
        if not math.isfinite(value):
            msg = f"Scenario {path} must be finite (got {value})"
            raise ValueError(msg)
    validate_pue_value(pue, "Scenario pue")


def build_transparent_request(
    *,
    it_load_kw: float,
    cooling_load_kw: float,
    pue: float,
    proposed_it_delta_kw: float,
    proposed_cooling_delta_kw: float,
    config: ConstraintConfig,
    config_version: str,
    it_source: MeasurementSource = MeasurementSource.ASSUMED,
    cooling_source: MeasurementSource = MeasurementSource.ASSUMED,
    pue_source: MeasurementSource = MeasurementSource.ASSUMED,
) -> TransparentDecisionRequest:
    """Build the canonical engine request from scenario inputs."""
    return TransparentDecisionRequest(
        config=config,
        config_version=config_version,
        it_load=RichMeasurement(
            name="it_load",
            value=it_load_kw,
            unit="kW",
            source=it_source,
        ),
        cooling_load=RichMeasurement(
            name="cooling_load",
            value=cooling_load_kw,
            unit="kW",
            source=cooling_source,
        ),
        pue=RichMeasurement(
            name="pue",
            value=pue,
            unit="dimensionless",
            source=pue_source,
        ),
        proposed_it_delta=proposed_it_delta_kw,
        proposed_cooling_delta=proposed_cooling_delta_kw,
        quality_eligible=True,
        quality_stale=False,
        mode=DecisionMode.HYPOTHETICAL,
    )


def _log_audit(
    conn: sqlite3.Connection,
    entity_id: str,
    action: str,
    details: str,
) -> None:
    """Append an audit event for a scenario operation."""
    conn.execute(
        "INSERT INTO audit_events "
        "(id, entity_type, entity_id, action, details, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (
            str(uuid.uuid4()),
            "SCENARIO",
            entity_id,
            action,
            details,
            now_z(),
        ),
    )


def persist_scenario(
    conn: sqlite3.Connection,
    *,
    name: str,
    transparent_request: TransparentDecisionRequest,
    result: TransparentDecisionResult,
    decision_id: str | None,
) -> str:
    """Persist one scenario atomically. Returns the new scenario id."""
    scenario_id = str(uuid.uuid4())
    input_payload = build_input_payload(transparent_request)
    config_payload = build_config_payload(transparent_request)
    input_json = json.dumps(input_payload, sort_keys=True)
    config_json = json.dumps(config_payload, sort_keys=True)
    result_json = json.dumps(result.model_dump(mode="json"), sort_keys=True)

    def _do() -> str:
        conn.execute(
            "INSERT INTO scenarios "
            "(id, name, created_at, status, precedence, engine_version, "
            "config_version, input_fingerprint, config_fingerprint, "
            "input_json, config_json, result_json, decision_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                scenario_id,
                name,
                now_z(),
                result.status,
                result.precedence,
                result.engine_version,
                result.config_version,
                result.fingerprint.input_fingerprint,
                result.fingerprint.config_fingerprint,
                input_json,
                config_json,
                result_json,
                decision_id,
            ),
        )
        _log_audit(
            conn,
            scenario_id,
            "SCENARIO_CREATED",
            f"status={result.status} precedence={result.precedence} "
            f"decision_id={decision_id or 'none'}",
        )
        return scenario_id

    return persist_atomically(conn, _do)


def evaluate_and_persist_scenario(
    conn: sqlite3.Connection,
    *,
    name: str,
    transparent_request: TransparentDecisionRequest,
    persist_as_decision: bool,
) -> tuple[str, str | None, TransparentDecisionResult]:
    """Run the canonical engine and persist the scenario.

    Returns ``(scenario_id, decision_id_or_none, result)``.
    """
    result = decide_transparently(transparent_request)

    decision_id: str | None = None
    if persist_as_decision:
        decision_id = persist_decision(conn, transparent_request, result)

    scenario_id = persist_scenario(
        conn,
        name=name,
        transparent_request=transparent_request,
        result=result,
        decision_id=decision_id,
    )
    return scenario_id, decision_id, result


def get_scenario(conn: sqlite3.Connection, scenario_id: str) -> dict | None:
    """Return the full record for one scenario, or None."""
    row = conn.execute("SELECT * FROM scenarios WHERE id = ?", (scenario_id,)).fetchone()
    if row is None:
        return None
    return {
        "id": row["id"],
        "name": row["name"],
        "created_at": row["created_at"],
        "status": row["status"],
        "precedence": row["precedence"],
        "engine_version": row["engine_version"],
        "config_version": row["config_version"],
        "decision_id": row["decision_id"],
        "input_fingerprint": row["input_fingerprint"],
        "config_fingerprint": row["config_fingerprint"],
        "inputs": json.loads(row["input_json"]),
        "config": json.loads(row["config_json"]),
        "result": json.loads(row["result_json"]),
    }


def _scenario_filter(status: str | None, config_version: str | None) -> tuple[str, list[object]]:
    sql = " WHERE 1=1"
    params: list[object] = []
    if status is not None:
        sql += " AND status = ?"
        params.append(status)
    if config_version is not None:
        sql += " AND config_version = ?"
        params.append(config_version)
    return sql, params


def count_scenarios(
    conn: sqlite3.Connection,
    *,
    status: str | None = None,
    config_version: str | None = None,
) -> int:
    """Count scenarios matching the filters."""
    clause, params = _scenario_filter(status, config_version)
    row = conn.execute(f"SELECT count(*) AS c FROM scenarios{clause}", params).fetchone()
    return int(row["c"])


def list_scenarios(
    conn: sqlite3.Connection,
    *,
    limit: int = 20,
    offset: int = 0,
    status: str | None = None,
    config_version: str | None = None,
) -> list[sqlite3.Row]:
    """List scenarios newest first, with optional filters."""
    clause, params = _scenario_filter(status, config_version)
    sql = f"SELECT * FROM scenarios{clause} ORDER BY created_at DESC, id LIMIT ? OFFSET ?"
    params.extend([limit, offset])
    return conn.execute(sql, params).fetchall()


def verify_scenario(conn: sqlite3.Connection, scenario_id: str) -> dict | None:
    """Recompute fingerprints and re-run the engine on stored inputs."""
    row = conn.execute(
        "SELECT input_json, config_json, result_json, "
        "input_fingerprint, config_fingerprint, engine_version "
        "FROM scenarios WHERE id = ?",
        (scenario_id,),
    ).fetchone()
    if row is None:
        return None

    request = TransparentDecisionRequest.model_validate(
        {
            **json.loads(row["input_json"]),
            "config": json.loads(row["config_json"]),
            "config_version": "verify-tmp",
        }
    )
    computed_input = compute_fingerprint(build_input_payload(request))
    computed_config = compute_fingerprint(build_config_payload(request))

    input_verified = computed_input == row["input_fingerprint"]
    config_verified = computed_config == row["config_fingerprint"]

    recomputed = decide_transparently(request)
    stored_result = json.loads(row["result_json"])
    output_verified = (
        stored_result.get("status") == recomputed.status
        and stored_result.get("precedence") == recomputed.precedence
    )

    engine_version_match = row["engine_version"] == CURRENT_ENGINE_VERSION

    return {
        "verified": (input_verified and config_verified and output_verified),
        "output_verified": output_verified,
        "stored_input_fingerprint": row["input_fingerprint"],
        "computed_input_fingerprint": computed_input,
        "stored_config_fingerprint": row["config_fingerprint"],
        "computed_config_fingerprint": computed_config,
        "engine_version_match": engine_version_match,
        "stored_engine_version": row["engine_version"],
        "current_engine_version": CURRENT_ENGINE_VERSION,
        "verification_scope": "input_config_output_status",
        "full_bundle_reproduced": False,
    }
