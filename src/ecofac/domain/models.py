# Copyright 2026 Lightibulb

"""Domain models representing facilities and energy observations."""

import dataclasses
import datetime as dt


@dataclasses.dataclass
class Organization:
    """Represents an entity that owns buildings."""

    id: str
    name: str
    created_at: dt.datetime


@dataclasses.dataclass
class Building:
    """A physical building associated with an organization."""

    id: str
    org_id: str
    name: str
    created_at: dt.datetime


@dataclasses.dataclass
class Meter:
    """A measurement device associated with a building."""

    id: str
    building_id: str
    fuel_type: str
    unit: str
    created_at: dt.datetime


@dataclasses.dataclass
class DataSource:
    """Origin of imported data (e.g., manual upload, API)."""

    id: str
    name: str
    source_type: str
    created_at: dt.datetime


@dataclasses.dataclass
class ImportBatch:
    """A specific import event from a data source."""

    id: str
    source_id: str
    status: str
    imported_at: dt.datetime


@dataclasses.dataclass
class EnergyReading:
    """A normalized energy measurement at a specific UTC timestamp."""

    id: str
    meter_id: str
    timestamp_utc: str
    value: float
    unit: str
    source_id: str
    batch_id: str
    is_valid: bool
    created_at: str


@dataclasses.dataclass
class DataQualityIssue:
    """An issue discovered during data validation."""

    id: str
    reading_id: str | None
    batch_id: str
    issue_type: str
    description: str
    created_at: str


@dataclasses.dataclass
class Incident:
    """Tracks the lifecycle of an anomaly or quality issue."""

    id: str
    meter_id: str
    status: str
    created_at: str


@dataclasses.dataclass
class EvidenceRecord:
    """Immutable proof supporting a finding or incident."""

    id: str
    incident_id: str
    computation_log: str
    created_at: str


@dataclasses.dataclass
class VerificationRun:
    """Before/after verification of an incident resolution."""

    id: str
    incident_id: str
    result: str
    created_at: str


@dataclasses.dataclass
class AuditEvent:
    """System-level audit log for state changes."""

    id: str
    entity_type: str
    entity_id: str
    action: str
    details: str
    created_at: str
