# Copyright 2026 Lightibulb

"""Pydantic models for API requests and responses."""

import pydantic

from ecofac.decision.transparent import (
    TransparentDecisionRequest,
    TransparentDecisionResult,
)


class HealthResponse(pydantic.BaseModel):
    """Health check response."""
    status: str


class PaginatedResponse(pydantic.BaseModel):
    """Base paginated response."""
    total: int
    limit: int
    offset: int


class BuildingResponse(pydantic.BaseModel):
    """Building data."""
    id: str
    org_id: str
    name: str
    created_at: str


class MeterResponse(pydantic.BaseModel):
    """Meter data."""
    id: str
    building_id: str
    fuel_type: str
    unit: str
    interval_minutes: int = 60
    created_at: str


class IncidentResponse(pydantic.BaseModel):
    """Incident data."""
    id: str
    anomaly_id: str
    status: str
    resolution_notes: str | None
    dismissal_reason: str | None
    created_at: str
    updated_at: str


class IncidentActionRequest(pydantic.BaseModel):
    """Payload for acknowledging or dismissing an incident."""
    reason: str | None = pydantic.Field(default=None, max_length=1000)


class EvidenceResponse(pydantic.BaseModel):
    """Evidence record data."""
    id: str
    incident_id: str
    computation_log: str
    created_at: str


class AuditEventResponse(pydantic.BaseModel):
    """Audit event data."""
    id: str
    entity_type: str
    entity_id: str
    action: str
    details: str
    created_at: str


class ImportSummaryResponse(pydantic.BaseModel):
    """Result of a CSV import."""
    total_rows: int
    accepted_rows: int
    rejected_rows: int
    duplicate_rows: int
    invalid_timestamps: int
    invalid_units: int
    unknown_meter_ids: int
    negative_consumption: int
    anomalies_detected: int = 0
    incidents_created: int = 0


class ImportBatchResponse(pydantic.BaseModel):
    """Import batch status."""
    id: str
    source_id: str
    status: str
    summary_json: str | None
    imported_at: str


class QualityResponse(pydantic.BaseModel):
    """Data quality assessment."""
    meter_id: str
    period_start: str
    period_end: str
    expected_readings: int
    actual_readings: int
    missing_readings: int
    duplicate_readings: int
    invalid_readings: int
    latest_observation: str | None
    is_stale: bool
    is_analysis_eligible: bool
    flags: list[str]


class DecisionEvaluateRequest(pydantic.BaseModel):
    """Request body for the decision evaluation endpoint."""
    decision: TransparentDecisionRequest
    supersedes: str | None = None
    supersession_reason: str | None = pydantic.Field(default=None, max_length=1000)


class DecisionEvaluateResponse(pydantic.BaseModel):
    """Response from the decision evaluation endpoint."""
    decision_id: str
    result: TransparentDecisionResult


class DecisionHistoryItem(pydantic.BaseModel):
    """Lightweight view of one stored decision."""
    id: str
    status: str
    precedence: str
    engine_version: str
    config_version: str
    decision_timestamp: str
    created_at: str
    supersedes: str | None
    supersession_reason: str | None


class DecisionRecordResponse(DecisionHistoryItem):
    """Full record for a single decision."""
    input_fingerprint: str
    config_fingerprint: str
    request: dict
    result: dict


class DecisionVerifyResponse(pydantic.BaseModel):
    """Result of a fingerprint re-verification."""
    verified: bool
    stored_input_fingerprint: str
    computed_input_fingerprint: str
    stored_config_fingerprint: str
    computed_config_fingerprint: str


class SupersessionChainResponse(pydantic.BaseModel):
    """The full supersession chain containing a decision."""
    decision_id: str
    chain: list[DecisionHistoryItem]
