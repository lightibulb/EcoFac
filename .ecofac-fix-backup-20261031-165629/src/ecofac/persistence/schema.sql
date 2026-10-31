CREATE TABLE IF NOT EXISTS organizations (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS buildings (
    id TEXT PRIMARY KEY,
    org_id TEXT NOT NULL,
    name TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY (org_id) REFERENCES organizations(id)
);

CREATE TABLE IF NOT EXISTS meters (
    id TEXT PRIMARY KEY,
    building_id TEXT NOT NULL,
    fuel_type TEXT NOT NULL,
    unit TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY (building_id) REFERENCES buildings(id)
);

CREATE TABLE IF NOT EXISTS data_sources (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    source_type TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS import_batches (
    id TEXT PRIMARY KEY,
    source_id TEXT NOT NULL,
    status TEXT NOT NULL,
    summary_json TEXT,
    imported_at TEXT NOT NULL,
    FOREIGN KEY (source_id) REFERENCES data_sources(id)
);

CREATE TABLE IF NOT EXISTS energy_readings (
    id TEXT PRIMARY KEY,
    meter_id TEXT NOT NULL,
    timestamp_utc TEXT NOT NULL,
    value REAL NOT NULL,
    unit TEXT NOT NULL,
    source_id TEXT NOT NULL,
    batch_id TEXT NOT NULL,
    is_valid INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    raw_value TEXT,
    FOREIGN KEY (meter_id) REFERENCES meters(id),
    FOREIGN KEY (source_id) REFERENCES data_sources(id),
    FOREIGN KEY (batch_id) REFERENCES import_batches(id),
    UNIQUE (meter_id, timestamp_utc)
);

CREATE TABLE IF NOT EXISTS data_quality_issues (
    id TEXT PRIMARY KEY,
    reading_id TEXT,
    batch_id TEXT NOT NULL,
    issue_type TEXT NOT NULL,
    description TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY (reading_id) REFERENCES energy_readings(id),
    FOREIGN KEY (batch_id) REFERENCES import_batches(id)
);

CREATE TABLE IF NOT EXISTS anomalies (
    id TEXT PRIMARY KEY,
    building_id TEXT NOT NULL,
    meter_id TEXT NOT NULL,
    timestamp_utc TEXT NOT NULL,
    observed_consumption REAL NOT NULL,
    expected_consumption REAL NOT NULL,
    absolute_deviation REAL NOT NULL,
    percentage_deviation REAL,
    detector_name TEXT NOT NULL,
    severity TEXT NOT NULL,
    is_data_quality_eligible INTEGER NOT NULL,
    detection_timestamp TEXT NOT NULL,
    FOREIGN KEY (building_id) REFERENCES buildings(id),
    FOREIGN KEY (meter_id) REFERENCES meters(id)
);

CREATE TABLE IF NOT EXISTS incidents (
    id TEXT PRIMARY KEY,
    anomaly_id TEXT NOT NULL,
    status TEXT NOT NULL,
    resolution_notes TEXT,
    dismissal_reason TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (anomaly_id) REFERENCES anomalies(id)
);

CREATE TABLE IF NOT EXISTS evidence_records (
    id TEXT PRIMARY KEY,
    incident_id TEXT NOT NULL,
    computation_log TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY (incident_id) REFERENCES incidents(id)
);

CREATE TABLE IF NOT EXISTS verification_runs (
    id TEXT PRIMARY KEY,
    incident_id TEXT NOT NULL,
    result TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY (incident_id) REFERENCES incidents(id)
);

CREATE TABLE IF NOT EXISTS audit_events (
    id TEXT PRIMARY KEY,
    entity_type TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    action TEXT NOT NULL,
    details TEXT NOT NULL,
    created_at TEXT NOT NULL
);
