# Copyright 2026 Lightibulb
"""Facility Operating Policy API routes."""

import sqlite3
import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from ecofac.api.dependencies import get_db, require_write_access
from ecofac.policy.manager import FacilityPolicy, PolicyManager
from ecofac.timeutil import now_z

router = APIRouter(prefix="/policies", tags=["Facility Policies"])


class PolicyCreateRequest(BaseModel):
    """Payload for creating a versioned operating policy."""

    version: str = Field(min_length=1, max_length=50)
    electrical_capacity_kw: float = Field(gt=0, le=100000)
    electrical_reserve_pct: float = Field(ge=0, lt=1)
    electrical_warning_threshold_pct: float = Field(ge=0, lt=1)
    electrical_min_headroom_kw: float = Field(ge=0, le=100000)
    cooling_capacity_kw: float = Field(gt=0, le=100000)
    cooling_reserve_pct: float = Field(ge=0, lt=1)
    cooling_warning_threshold_pct: float = Field(ge=0, lt=1)
    cooling_min_headroom_kw: float = Field(ge=0, le=100000)
    pue_min: float = Field(default=1.0, ge=1.0, le=10.0)
    pue_max: float = Field(default=10.0, ge=1.0, le=10.0)
    freshness_max_age_seconds: float = Field(default=7200.0, gt=0, le=604800)
    notes: str = Field(default="", max_length=500)
    activate: bool = Field(default=False)


def _payload(policy: FacilityPolicy) -> dict[str, Any]:
    snapshot = policy.config_snapshot()
    return {
        **policy.to_dict(),
        "fingerprint": policy.fingerprint,
        "constraints": {
            "electrical": snapshot["electrical"],
            "cooling": snapshot["cooling"],
        },
    }


@router.get("")
def list_policies(
    conn: Annotated[sqlite3.Connection, Depends(get_db)],
) -> list[dict]:
    """List all operating policies, newest first."""
    return [_payload(p) for p in PolicyManager(conn).list_policies()]


@router.get("/active")
def get_active_policy_endpoint(
    conn: Annotated[sqlite3.Connection, Depends(get_db)],
) -> dict:
    """Return the active operating policy (auto-creates the default)."""
    policy = PolicyManager(conn).get_active_policy()
    if policy is None:
        raise HTTPException(status_code=404, detail="No active policy found")
    return _payload(policy)


@router.get("/{policy_id}")
def get_policy_endpoint(
    policy_id: str,
    conn: Annotated[sqlite3.Connection, Depends(get_db)],
) -> dict:
    """Return one operating policy by id."""
    policy = PolicyManager(conn).get_policy(policy_id)
    if policy is None:
        raise HTTPException(status_code=404, detail="Policy not found")
    return _payload(policy)


@router.post("", status_code=201)
def create_policy_endpoint(
    request: PolicyCreateRequest,
    conn: Annotated[sqlite3.Connection, Depends(get_db)],
    _auth: Annotated[None, Depends(require_write_access)],
) -> dict:
    """Create a new versioned operating policy."""
    if request.pue_min > request.pue_max:
        raise HTTPException(status_code=422, detail="pue_min must be <= pue_max")
    policy = FacilityPolicy(
        id=str(uuid.uuid4()),
        version=request.version.strip(),
        created_at=now_z(),
        is_active=request.activate,
        electrical_capacity_kw=request.electrical_capacity_kw,
        electrical_reserve_pct=request.electrical_reserve_pct,
        electrical_warning_threshold_pct=request.electrical_warning_threshold_pct,
        electrical_min_headroom_kw=request.electrical_min_headroom_kw,
        cooling_capacity_kw=request.cooling_capacity_kw,
        cooling_reserve_pct=request.cooling_reserve_pct,
        cooling_warning_threshold_pct=request.cooling_warning_threshold_pct,
        cooling_min_headroom_kw=request.cooling_min_headroom_kw,
        pue_min=request.pue_min,
        pue_max=request.pue_max,
        freshness_max_age_seconds=request.freshness_max_age_seconds,
        notes=request.notes,
    )
    manager = PolicyManager(conn)
    try:
        manager.create_policy(policy)
    except sqlite3.IntegrityError as exc:
        raise HTTPException(
            status_code=409,
            detail=f"Policy version already exists: {policy.version}",
        ) from exc
    if request.activate:
        manager.activate_policy(policy.id)
    return _payload(policy)


@router.post("/{policy_id}/activate")
def activate_policy_endpoint(
    policy_id: str,
    conn: Annotated[sqlite3.Connection, Depends(get_db)],
    _auth: Annotated[None, Depends(require_write_access)],
) -> dict:
    """Make one policy active (deactivates all others)."""
    manager = PolicyManager(conn)
    policy = manager.get_policy(policy_id)
    if policy is None:
        raise HTTPException(status_code=404, detail="Policy not found")
    manager.activate_policy(policy_id)
    policy.is_active = True
    return _payload(policy)
