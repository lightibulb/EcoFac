# Copyright 2026 Lightibulb
"""Persistence and retrieval for decision records.

Decisions are stored immutably: once written, a decision row cannot be
updated or deleted (enforced by SQLite triggers). Supersession is recorded
as a new decision that references the old one via ``supersedes``, leaving
the original row untouched.
"""

import json
import sqlite3
import uuid

from ecofac.decision.reproducibility import compute_fingerprint
from ecofac.decision.transparent import (
    TransparentDecisionRequest,
    TransparentDecisionResult,
    build_config_payload,
    build_input_payload,
)
from ecofac.persistence.tx import persist_atomically
from ecofac.timeutil import now_z


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

    Raises ``ValueError`` when ``supersedes`` points to a missing decision
    or when ``supersession_reason`` is empty while superseding.
    """
    if supersedes is not None and not (supersession_reason or "").strip():
        msg = "supersession_reason is required when supersedes is set"
        raise ValueError(msg)

    decision_id = str(uuid.uuid4())
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
        _register_config(conn, request, config_fp)
        conn.execute(
            "INSERT INTO decisions "
            "(id, status, precedence, engine_version, config_version, "
            "input_fingerprint, config_fingerprint, request_json, "
            "result_json, decision_timestamp, created_at, supersedes, "
            "supersession_reason) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                decision_id,
                result.status,
                result.precedence,
                result.engine_version,
                result.config_version,
                result.fingerprint.input_fingerprint,
                result.fingerprint.config_fingerprint,
                request_json,
                result_json,
                result.decision_timestamp,
                now_z(),
                supersedes,
                supersession_reason,
            ),
        )
        _log_audit(
            conn,
            "DECISION",
            decision_id,
            "DECISION_CREATED",
            f"status={result.status} precedence={result.precedence} "
            f"config_version={result.config_version} "
            f"supersedes={supersedes or 'none'}",
        )
        if supersedes is not None:
            _log_audit(
                conn,
                "DECISION",
                supersedes,
                "DECISION_SUPERSEDED",
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


def list_decisions(
    conn: sqlite3.Connection,
    *,
    limit: int = 20,
    offset: int = 0,
    status: str | None = None,
    config_version: str | None = None,
) -> list[sqlite3.Row]:
    """Return stored decisions, newest first, with optional filters."""
    sql = "SELECT * FROM decisions WHERE 1=1"
    params: list[object] = []
    if status is not None:
        sql += " AND status = ?"
        params.append(status)
    if config_version is not None:
        sql += " AND config_version = ?"
        params.append(config_version)
    sql += " ORDER BY created_at DESC, id LIMIT ? OFFSET ?"
    params.extend([limit, offset])
    return conn.execute(sql, params).fetchall()


def verify_decision(
    conn: sqlite3.Connection, decision_id: str
) -> dict | None:
    """Recompute the input and config fingerprints for a stored decision."""
    row = conn.execute(
        "SELECT request_json, input_fingerprint, config_fingerprint "
        "FROM decisions WHERE id = ?",
        (decision_id,),
    ).fetchone()
    if row is None:
        return None
    stored_request = json.loads(row["request_json"])
    request = TransparentDecisionRequest.model_validate(stored_request)
    computed_input = compute_fingerprint(build_input_payload(request))
    computed_config = compute_fingerprint(build_config_payload(request))
    return {
        "verified": (
            computed_input == row["input_fingerprint"]
            and computed_config == row["config_fingerprint"]
        ),
        "stored_input_fingerprint": row["input_fingerprint"],
        "computed_input_fingerprint": computed_input,
        "stored_config_fingerprint": row["config_fingerprint"],
        "computed_config_fingerprint": computed_config,
    }


def walk_supersession_chain(
    conn: sqlite3.Connection, decision_id: str
) -> list[sqlite3.Row]:
    """Return the complete branching supersession history containing an ID.

    The result starts at the root ancestor and includes every descendant,
    rather than silently following only the first child.
    """
    exists = conn.execute(
        "SELECT 1 FROM decisions WHERE id = ?", (decision_id,)
    ).fetchone()
    if exists is None:
        return []

    # Find the root ancestor.
    root = decision_id
    visited_ancestors = {root}
    while True:
        row = conn.execute(
            "SELECT supersedes FROM decisions WHERE id = ?", (root,)
        ).fetchone()
        if row is None or not row["supersedes"]:
            break
        parent = row["supersedes"]
        if parent in visited_ancestors:
            break
        visited_ancestors.add(parent)
        root = parent

    # Walk all branches deterministically.
    ordered_ids: list[str] = []
    pending = [root]
    visited: set[str] = set()

    while pending:
        current = pending.pop(0)
        if current in visited:
            continue
        visited.add(current)
        ordered_ids.append(current)

        children = conn.execute(
            "SELECT id FROM decisions WHERE supersedes = ? "
            "ORDER BY created_at, id",
            (current,),
        ).fetchall()
        pending.extend(row["id"] for row in children)

    if decision_id not in visited:
        return []

    placeholders = ",".join("?" for _ in ordered_ids)
    rows = conn.execute(
        f"SELECT * FROM decisions WHERE id IN ({placeholders}) "
        "ORDER BY created_at, id",
        ordered_ids,
    ).fetchall()
    return list(rows)
