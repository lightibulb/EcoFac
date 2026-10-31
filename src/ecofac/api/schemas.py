# Copyright 2026 Lightibulb

"""Pydantic models for API requests and responses."""

import pydantic


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
    reason: str | None = None


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
