# Copyright 2026 Lightibulb
"""Persistence and retrieval for decision records.

Decisions are immutable: once written, a decision row cannot be updated or
deleted (enforced by SQLite triggers). Supersession records a new decision
that references the old one, leaving the original intact. Each decision may
have at most one direct successor enforced at insert time; legacy data with
multiple children is surfaced through ``walk_supersession_chain_with_branches``
rather than silently dropped.
"""

import json
import sqlite3
import uuid

from ecofac.decision.reproducibility import compute_fingerprint
from ecofac.decision.state_machine import ENGINE_VERSION as CURRENT_ENGINE_VERSION
from ecofac.decision.transparent import (
    TransparentDecisionRequest,
    TransparentDecisionResult,
    build_config_payload,
    build_input_payload,
    decide_transparently,
    is_time_dependent,
    pinned_request,
)
from ecofac.persistence.tx import persist_atomically
from ecofac.timeutil import now_z

VERIFICATION_SCOPE = "input_config_output_status"


def _log_audit(
    conn: sqlite3.Connection,
    entity_type: str,
    entity_id: str,
    action: str,
    details: str,
) -> None:
    """Append one immutable audit event."""
    conn.execute(
        "INSERT INTO audit_events "
        "(id, entity_type, entity_id, action, details, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (str(uuid.uuid4()), entity_type, entity_id, action, details, now_z()),
    )


def _register_config(
    conn: sqlite3.Connection,
    request: TransparentDecisionRequest,
    config_fingerprint: str,
) -> None:
    """Register the config fingerprint once; emit audit only on first sighting."""
    existing = conn.execute(
        "SELECT 1 FROM decision_configs WHERE fingerprint = ?",
        (config_fingerprint,),
    ).fetchone()
    if existing is not None:
        return
    config_json = json.dumps(build_config_payload(request), sort_keys=True)
    conn.execute(
        "INSERT INTO decision_configs "
        "(fingerprint, config_version, config_json, first_seen_at) "
        "VALUES (?, ?, ?, ?)",
        (config_fingerprint, request.config_version, config_json, now_z()),
    )
    _log_audit(
        conn,
        "DECISION_CONFIG",
        config_fingerprint,
        "CONFIG_REGISTERED",
        f"Config version {request.config_version} registered",
    )


def persist_decision(
    conn: sqlite3.Connection,
    request: TransparentDecisionRequest,
    result: TransparentDecisionResult,
    *,
    supersedes: str | None = None,
    supersession_reason: str | None = None,
) -> str:
    """Persist one decision atomically. Returns the new decision id.

    Raises ``ValueError`` when supersession is invalid or the parent already
    has a successor.
    """
    if supersedes is not None and not (supersession_reason or "").strip():
        msg = "supersession_reason is required when supersedes is set"
        raise ValueError(msg)

    decision_id = str(uuid.uuid4())
    # Store the request with its evaluation time pinned (when the verdict is
    # time-dependent) so verify_decision replays at the ORIGINAL instant.
    request = pinned_request(request, result)
    request_json = json.dumps(request.model_dump(mode="json"), sort_keys=True)
    result_json = json.dumps(result.model_dump(mode="json"), sort_keys=True)
    config_fp = result.fingerprint.config_fingerprint

    def _do() -> str:
        if supersedes is not None:
            parent = conn.execute(
                "SELECT 1 FROM decisions WHERE id = ?", (supersedes,)
            ).fetchone()
            if parent is None:
                msg = f"Superseded decision {supersedes} does not exist"
                raise ValueError(msg)
            existing_child = conn.execute(
                "SELECT id FROM decisions WHERE supersedes = ? LIMIT 1",
                (supersedes,),
            ).fetchone()
            if existing_child is not None:
                msg = (
                    f"Decision {supersedes} is already superseded by "
                    f"{existing_child['id']}"
                )
                raise ValueError(msg)
        _register_config(conn, request, config_fp)
        conn.execute(
            "INSERT INTO decisions "
            "(id, status, precedence, engine_version, config_version, "
            "input_fingerprint, config_fingerprint, request_json, "
            "result_json, decision_timestamp, created_at, supersedes, "
            "supersession_reason) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                decision_id, result.status, result.precedence,
                result.engine_version, result.config_version,
                result.fingerprint.input_fingerprint,
                result.fingerprint.config_fingerprint,
                request_json, result_json, result.decision_timestamp,
                now_z(), supersedes, supersession_reason,
            ),
        )
        _log_audit(
            conn, "DECISION", decision_id, "DECISION_CREATED",
            f"status={result.status} precedence={result.precedence} "
            f"config_version={result.config_version} "
            f"supersedes={supersedes or 'none'}",
        )
        if supersedes is not None:
            _log_audit(
                conn, "DECISION", supersedes, "DECISION_SUPERSEDED",
                f"superseded_by={decision_id} "
                f"reason={supersession_reason or 'none'}",
            )
        return decision_id

    return persist_atomically(conn, _do)


def get_decision(
    conn: sqlite3.Connection, decision_id: str
) -> dict | None:
    """Return the full record for one decision, or None."""
    row = conn.execute(
        "SELECT * FROM decisions WHERE id = ?", (decision_id,)
    ).fetchone()
    if row is None:
        return None
    return {
        "id": row["id"],
        "status": row["status"],
        "precedence": row["precedence"],
        "engine_version": row["engine_version"],
        "config_version": row["config_version"],
        "decision_timestamp": row["decision_timestamp"],
        "created_at": row["created_at"],
        "supersedes": row["supersedes"],
        "supersession_reason": row["supersession_reason"],
        "input_fingerprint": row["input_fingerprint"],
        "config_fingerprint": row["config_fingerprint"],
        "request": json.loads(row["request_json"]),
        "result": json.loads(row["result_json"]),
    }


def _filter_clause(
    status: str | None, config_version: str | None
) -> tuple[str, list[object]]:
    """Return (WHERE-clause-fragment, params) for the two optional filters."""
    sql = " WHERE 1=1"
    params: list[object] = []
    if status is not None:
        sql += " AND status = ?"
        params.append(status)
    if config_version is not None:
        sql += " AND config_version = ?"
        params.append(config_version)
    return sql, params


def count_decisions(
    conn: sqlite3.Connection,
    *,
    status: str | None = None,
    config_version: str | None = None,
) -> int:
    """Count decisions matching the given filters. Bounded single query."""
    clause, params = _filter_clause(status, config_version)
    row = conn.execute(
        f"SELECT count(*) AS c FROM decisions{clause}", params
    ).fetchone()
    return int(row["c"])


def list_decisions(
    conn: sqlite3.Connection,
    *,
    limit: int = 20,
    offset: int = 0,
    status: str | None = None,
    config_version: str | None = None,
) -> list[sqlite3.Row]:
    """Return stored decisions, newest first, with optional filters."""
    clause, params = _filter_clause(status, config_version)
    sql = (
        f"SELECT * FROM decisions{clause} "
        "ORDER BY created_at DESC, id LIMIT ? OFFSET ?"
    )
    params.extend([limit, offset])
    return conn.execute(sql, params).fetchall()


def batch_supersession_info(
    conn: sqlite3.Connection, decision_ids: list[str]
) -> dict[str, str]:
    """Return {parent_id: successor_id} for parents that have a successor.

    If legacy data has multiple successors for one parent, the earliest wins;
    the rest are surfaced through ``walk_supersession_chain_with_branches``.
    """
    if not decision_ids:
        return {}
    placeholders = ",".join("?" * len(decision_ids))
    rows = conn.execute(
        f"SELECT supersedes, id FROM decisions "
        f"WHERE supersedes IN ({placeholders}) "
        f"ORDER BY created_at, id",
        decision_ids,
    ).fetchall()
    result: dict[str, str] = {}
    for row in rows:
        parent = row["supersedes"]
        if parent is not None and parent not in result:
            result[parent] = row["id"]
    return result


def verify_decision(
    conn: sqlite3.Connection, decision_id: str
) -> dict | None:
    """Recompute and verify both the fingerprints AND the top-level outcome.

    Scope is explicitly ``VERIFICATION_SCOPE`` — inputs, config, and the
    (status, precedence) pair. The full evidence bundle is NOT reproduced.
    Engine-version equality is reported separately.
    """
    row = conn.execute(
        "SELECT request_json, result_json, input_fingerprint, "
        "config_fingerprint, engine_version FROM decisions WHERE id = ?",
        (decision_id,),
    ).fetchone()
    if row is None:
        return None

    stored_request = json.loads(row["request_json"])
    request = TransparentDecisionRequest.model_validate(stored_request)
    computed_input = compute_fingerprint(build_input_payload(request))
    computed_config = compute_fingerprint(build_config_payload(request))

    input_verified = computed_input == row["input_fingerprint"]
    config_verified = computed_config == row["config_fingerprint"]

    stored_result = json.loads(row["result_json"])
    # Replay at the ORIGINAL evaluation instant. Requests stored with a pinned
    # ``evaluation_time`` already carry it; for older time-dependent decisions
    # the stored result's own timestamp is the best available record. Either
    # way the wall clock never decides whether a historical decision verifies.
    replay_request = request
    if request.evaluation_time is None and is_time_dependent(request):
        pinned = stored_result.get("evaluation_time") or stored_result.get(
            "decision_timestamp"
        )
        if pinned:
            replay_request = request.model_copy(update={"evaluation_time": pinned})
    recomputed = decide_transparently(replay_request)
    output_verified = (
        stored_result.get("status") == recomputed.status
        and stored_result.get("precedence") == recomputed.precedence
    )

    stored_engine = row["engine_version"]
    engine_version_match = stored_engine == CURRENT_ENGINE_VERSION

    return {
        "verified": input_verified and config_verified and output_verified,
        "stored_input_fingerprint": row["input_fingerprint"],
        "computed_input_fingerprint": computed_input,
        "stored_config_fingerprint": row["config_fingerprint"],
        "computed_config_fingerprint": computed_config,
        "output_verified": output_verified,
        "stored_status": stored_result.get("status"),
        "recomputed_status": recomputed.status,
        "stored_precedence": stored_result.get("precedence"),
        "recomputed_precedence": recomputed.precedence,
        "engine_version_match": engine_version_match,
        "stored_engine_version": stored_engine,
        "current_engine_version": CURRENT_ENGINE_VERSION,
        "evaluation_time": replay_request.evaluation_time,
        "full_bundle_reproduced": False,
        "verification_scope": VERIFICATION_SCOPE,
    }


def _walk_supersession_impl(
    conn: sqlite3.Connection, decision_id: str
) -> tuple[list[sqlite3.Row], list[sqlite3.Row]]:
    """Return (primary_chain, branches).

    ``branches`` contains any non-primary children of a node in the chain —
    normally empty because ``persist_decision`` enforces a single-child
    supersession rule. Non-empty branches signal legacy data.
    """
    present = conn.execute(
        "SELECT 1 FROM decisions WHERE id = ?", (decision_id,)
    ).fetchone()
    if present is None:
        return [], []

    root = decision_id
    visited = {decision_id}
    while True:
        row = conn.execute(
            "SELECT supersedes FROM decisions WHERE id = ?", (root,)
        ).fetchone()
        if row is None or not row["supersedes"]:
            break
        root = row["supersedes"]
        if root in visited:
            break
        visited.add(root)

    chain: list[sqlite3.Row] = []
    branches: list[sqlite3.Row] = []
    current: str | None = root
    while current is not None:
        row = conn.execute(
            "SELECT * FROM decisions WHERE id = ?", (current,)
        ).fetchone()
        if row is None:
            break
        chain.append(row)
        children = conn.execute(
            "SELECT * FROM decisions WHERE supersedes = ? "
            "ORDER BY created_at, id",
            (current,),
        ).fetchall()
        if not children:
            current = None
        else:
            current = children[0]["id"]
            branches.extend(children[1:])
    return chain, branches


def walk_supersession_chain(
    conn: sqlite3.Connection, decision_id: str
) -> list[sqlite3.Row]:
    """Return the primary supersession chain containing ``decision_id``."""
    chain, _ = _walk_supersession_impl(conn, decision_id)
    return chain


def walk_supersession_chain_with_branches(
    conn: sqlite3.Connection, decision_id: str
) -> tuple[list[sqlite3.Row], list[sqlite3.Row]]:
    """Return (primary_chain, branches) — branches surfaces legacy multi-child data."""
    return _walk_supersession_impl(conn, decision_id)
