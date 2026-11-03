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
    """Lightweight view of one stored decision.

    ``is_superseded`` / ``superseded_by`` are populated for the list and
    supersession-chain endpoints; they default to False/None for callers
    that construct the model directly.
    """
    id: str
    status: str
    precedence: str
    engine_version: str
    config_version: str
    decision_timestamp: str
    created_at: str
    supersedes: str | None
    supersession_reason: str | None
    is_superseded: bool = False
    superseded_by: str | None = None


class DecisionRecordResponse(DecisionHistoryItem):
    """Full record for a single decision."""
    input_fingerprint: str
    config_fingerprint: str
    request: dict
    result: dict


class DecisionVerifyResponse(pydantic.BaseModel):
    """Structured verification result.

    ``verified`` is an aggregate over the scope named in ``verification_scope``
    — currently ``input_config_output_status``: input fingerprint, config
    fingerprint, and the top-level (status, precedence) pair. The full evidence
    bundle is not reproduced; ``full_bundle_reproduced`` is therefore False.
    ``engine_version_match`` signals whether the stored engine version equals
    the current engine version — when False, a passing ``output_verified``
    means the outcome matches *by coincidence* under different engine code,
    and the caller should treat the result with caution.
    """
    verified: bool
    stored_input_fingerprint: str
    computed_input_fingerprint: str
    stored_config_fingerprint: str
    computed_config_fingerprint: str
    output_verified: bool = True
    stored_status: str | None = None
    recomputed_status: str | None = None
    stored_precedence: str | None = None
    recomputed_precedence: str | None = None
    engine_version_match: bool = True
    stored_engine_version: str | None = None
    current_engine_version: str | None = None
    full_bundle_reproduced: bool = False
    verification_scope: str = "input_config_output_status"


class SupersessionChainResponse(pydantic.BaseModel):
    """The supersession chain containing a decision.

    ``chain`` is the primary linear sequence. ``branches`` is normally empty
    (single-child rule enforced at persist time); it is populated only for
    legacy data whose parent has multiple successors, so no branch is silently
    omitted from the response.
    """
    decision_id: str
    chain: list[DecisionHistoryItem]
    branches: list[DecisionHistoryItem] = pydantic.Field(default_factory=list)

class DecisionStats(pydantic.BaseModel):
    """Aggregate decision metrics for the Overview section."""
    total: int
    approved: int
    rejected: int
    review: int
    latest_decision: "DecisionHistoryItem | None"
    recent_activity: list["DecisionHistoryItem"]


class ConstraintHeadroom(pydantic.BaseModel):
    """Headroom picture for one observed value against one physical limit."""
    physical_capacity_kw: float
    permitted_kw: float
    observed_kw: float
    headroom_to_permitted_kw: float
    headroom_to_physical_kw: float
    reserve_pct: float


class EngineeringSummary(pydantic.BaseModel):
    """Structured engineering values extracted from a stored decision.

    All numbers are computed server-side from the decision's persisted
    evidence. The frontend renders them verbatim.
    """
    pue: float
    pue_provenance: str
    current_it_load_kw: float
    current_facility_power_kw: float
    current_cooling_load_kw: float
    predicted_it_load_kw: float
    predicted_facility_power_kw: float
    predicted_cooling_load_kw: float
    current_electrical: ConstraintHeadroom
    predicted_electrical: ConstraintHeadroom
    current_cooling: ConstraintHeadroom
    predicted_cooling: ConstraintHeadroom

