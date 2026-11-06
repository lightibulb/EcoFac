# Copyright 2026 Lightibulb
"""Scenario Intelligence API routes."""

import sqlite3
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Response

from ecofac.api.dependencies import get_db, require_write_access
from ecofac.api.schemas import (
    EngineeringSummary,
    ScenarioComparisonRequest,
    ScenarioCreateRequest,
    ScenarioEvaluateResponse,
    ScenarioHistoryItem,
    ScenarioRecordResponse,
    ScenarioVerifyResponse,
)
from ecofac.decision.engineering import build_engineering_summary
from ecofac.decision.scenario_analysis import (
    ScenarioBottleneckReport,
    ScenarioComparisonResponse,
    SensitivityAnalysisResponse,
    analyze_sensitivity,
    compare_scenarios,
    detect_bottlenecks,
)
from ecofac.decision.scenarios import (
    build_transparent_request,
    count_scenarios,
    default_config_version,
    evaluate_and_persist_scenario,
    get_scenario,
    list_scenarios,
    validate_scenario_numerics,
    verify_scenario,
)

router = APIRouter(prefix="/scenarios", tags=["Scenario Intelligence"])


def _resolve_config_version(req: ScenarioCreateRequest) -> str:
    if req.config_version:
        return req.config_version
    return default_config_version(req.config.model_dump(mode="json"))


def _enrich(row: sqlite3.Row) -> ScenarioHistoryItem:
    return ScenarioHistoryItem(
        id=row["id"],
        name=row["name"],
        created_at=row["created_at"],
        status=row["status"],
        precedence=row["precedence"],
        engine_version=row["engine_version"],
        config_version=row["config_version"],
        decision_id=row["decision_id"],
    )


@router.post("/evaluate")
def evaluate_scenario(
    payload: ScenarioCreateRequest,
    conn: Annotated[sqlite3.Connection, Depends(get_db)],
    _auth: Annotated[None, Depends(require_write_access)],
) -> ScenarioEvaluateResponse:
    """Evaluate a hypothetical scenario through the canonical engine."""
    config_dump = payload.config.model_dump(mode="json")
    try:
        validate_scenario_numerics(
            it_load_kw=payload.it_load_kw,
            cooling_load_kw=payload.cooling_load_kw,
            pue=payload.pue,
            proposed_it_delta_kw=payload.proposed_it_delta_kw,
            proposed_cooling_delta_kw=payload.proposed_cooling_delta_kw,
            config_dump=config_dump,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    config_version = _resolve_config_version(payload)
    try:
        transparent_request = build_transparent_request(
            it_load_kw=payload.it_load_kw,
            cooling_load_kw=payload.cooling_load_kw,
            pue=payload.pue,
            proposed_it_delta_kw=payload.proposed_it_delta_kw,
            proposed_cooling_delta_kw=payload.proposed_cooling_delta_kw,
            config=payload.config,
            config_version=config_version,
            it_source=payload.it_source,
            cooling_source=payload.cooling_source,
            pue_source=payload.pue_source,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    try:
        scenario_id, decision_id, result = evaluate_and_persist_scenario(
            conn,
            name=payload.name,
            transparent_request=transparent_request,
            persist_as_decision=payload.persist_as_decision,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    return ScenarioEvaluateResponse(
        scenario_id=scenario_id,
        decision_id=decision_id,
        result=result,
    )


@router.post("/compare")
def compare_scenarios_endpoint(
    payload: ScenarioComparisonRequest,
    conn: Annotated[sqlite3.Connection, Depends(get_db)],
) -> ScenarioComparisonResponse:
    """Compare 2-10 stored scenarios."""
    records: list[dict] = []
    missing: list[str] = []
    for sid in payload.scenario_ids:
        record = get_scenario(conn, sid)
        if record is None:
            missing.append(sid)
        else:
            records.append(record)
    if missing:
        raise HTTPException(
            status_code=404,
            detail=f"Scenarios not found: {', '.join(missing)}",
        )
    try:
        return compare_scenarios(records)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.get("")
def list_scenarios_endpoint(
    response: Response,
    conn: Annotated[sqlite3.Connection, Depends(get_db)],
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
    status: Annotated[str | None, Query()] = None,
    config_version: Annotated[str | None, Query()] = None,
) -> list[ScenarioHistoryItem]:
    """List stored scenarios, newest first.

    Response header X-Total-Count carries the total matching the
    active filters.
    """
    total = count_scenarios(
        conn, status=status, config_version=config_version
    )
    response.headers["X-Total-Count"] = str(total)
    rows = list_scenarios(
        conn, limit=limit, offset=offset,
        status=status, config_version=config_version,
    )
    return [_enrich(row) for row in rows]


@router.get("/{scenario_id}")
def get_scenario_endpoint(
    scenario_id: str,
    conn: Annotated[sqlite3.Connection, Depends(get_db)],
) -> ScenarioRecordResponse:
    """Get the full record for one scenario."""
    record = get_scenario(conn, scenario_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Scenario not found")
    return ScenarioRecordResponse(**record)


@router.get("/{scenario_id}/engineering")
def scenario_engineering_endpoint(
    scenario_id: str,
    conn: Annotated[sqlite3.Connection, Depends(get_db)],
) -> EngineeringSummary:
    """Structured engineering summary extracted from the stored scenario."""
    record = get_scenario(conn, scenario_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Scenario not found")
    try:
        data = build_engineering_summary(record)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    return EngineeringSummary(**data)


@router.get("/{scenario_id}/bottlenecks")
def scenario_bottlenecks_endpoint(
    scenario_id: str,
    conn: Annotated[sqlite3.Connection, Depends(get_db)],
) -> ScenarioBottleneckReport:
    """Bottleneck analysis derived from stored constraint evaluations."""
    record = get_scenario(conn, scenario_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Scenario not found")
    return detect_bottlenecks(record)


@router.get("/{scenario_id}/sensitivity")
def scenario_sensitivity_endpoint(
    scenario_id: str,
    conn: Annotated[sqlite3.Connection, Depends(get_db)],
) -> SensitivityAnalysisResponse:
    """Deterministic multi-parameter sweep over supported inputs."""
    record = get_scenario(conn, scenario_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Scenario not found")
    try:
        return analyze_sensitivity(record)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.get("/{scenario_id}/verify")
def verify_scenario_endpoint(
    scenario_id: str,
    conn: Annotated[sqlite3.Connection, Depends(get_db)],
) -> ScenarioVerifyResponse:
    """Recompute fingerprints and re-run the canonical engine."""
    verification = verify_scenario(conn, scenario_id)
    if verification is None:
        raise HTTPException(status_code=404, detail="Scenario not found")
    return ScenarioVerifyResponse(**verification)
