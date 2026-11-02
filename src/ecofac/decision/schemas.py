# Copyright 2026 Lightibulb
"""Pydantic schemas for the decision engine interface.

All operational limits come from the caller via OperationalConstraints.
The engine never hard-codes a threshold.
"""

from enum import StrEnum

import pydantic


class DecisionStatus(StrEnum):
    """Aggregate outcome of a decision evaluation."""

    APPROVE = "APPROVE"
    REJECT = "REJECT"
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"


class ConstraintStatus(StrEnum):
    """Per-constraint evaluation outcome."""

    PASS = "PASS"  # noqa: S105
    FAIL = "FAIL"
    UNKNOWN = "UNKNOWN"


class OperationalConstraints(pydantic.BaseModel):
    """Caller-supplied operational limits. Never hard-coded in the engine."""

    electrical_capacity_kw: float = pydantic.Field(gt=0)
    cooling_capacity_kw: float = pydantic.Field(gt=0)
    max_pue: float = pydantic.Field(gt=0)
    safety_margin_pct: float = pydantic.Field(ge=0.0, lt=1.0)


class FacilityMeasurements(pydantic.BaseModel):
    """Current readings from the intelligence layer.

    Any field may be None, which means the measurement is missing and the
    engine must refuse to guess it.
    """

    it_load_kw: float | None = None
    cooling_load_kw: float | None = None
    pue: float | None = None


class DataQualitySnapshot(pydantic.BaseModel):
    """Quality snapshot produced by the intelligence layer.

    ``eligible`` must be True and ``stale`` must be False for the engine to
    proceed. Both flags come from the quality assessment, not from the caller.
    """

    eligible: bool
    flags: list[str] = pydantic.Field(default_factory=list)
    stale: bool = False


class ProposedChange(pydantic.BaseModel):
    """Delta applied to the current state."""

    it_load_delta_kw: float = 0.0
    cooling_load_delta_kw: float = 0.0
    description: str | None = None


class ConstraintEvaluation(pydantic.BaseModel):
    """Evaluation of a single constraint."""

    name: str
    status: ConstraintStatus
    observed: float | None = None
    limit: float
    margin: float | None = None
    reason: str


class PredictedConditions(pydantic.BaseModel):
    """Projected operational state after the proposed change."""

    it_load_kw: float
    cooling_load_kw: float
    total_facility_kw: float
    pue: float


class Decision(pydantic.BaseModel):
    """Final decision output."""

    status: DecisionStatus
    predicted: PredictedConditions | None = None
    constraints: list[ConstraintEvaluation]
    warnings: list[str] = pydantic.Field(default_factory=list)
    evidence: dict
    explanation: str
