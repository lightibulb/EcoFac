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
    interval_minutes INTEGER NOT NULL DEFAULT 60,
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
    is_valid INTEGER NOT NULL DEFAULT 1 CHECK (is_valid IN (0, 1)),
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
    meter_id TEXT,
    reading_timestamp_utc TEXT,
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
    severity TEXT NOT NULL CHECK (severity IN ('low', 'medium', 'high')),
    is_data_quality_eligible INTEGER NOT NULL CHECK (is_data_quality_eligible IN (0, 1)),
    detection_timestamp TEXT NOT NULL,
    detection_parameters TEXT,
    FOREIGN KEY (building_id) REFERENCES buildings(id),
    FOREIGN KEY (meter_id) REFERENCES meters(id),
    UNIQUE (meter_id, timestamp_utc, detector_name)
);

CREATE TABLE IF NOT EXISTS incidents (
    id TEXT PRIMARY KEY,
    anomaly_id TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN (
        'DETECTED', 'UNDER_REVIEW', 'ACKNOWLEDGED', 'DISMISSED', 'RESOLVED', 'UNRESOLVED'
    )),
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

-- Detection runs per-reading range queries on this pair; without the index
-- every query full-scans the table (this is what made Render crawl).
CREATE INDEX IF NOT EXISTS idx_readings_meter_ts
    ON energy_readings (meter_id, timestamp_utc);

-- Scoped quality-issue lookups (duplicate counts per meter per window).
CREATE INDEX IF NOT EXISTS idx_dqi_scope
    ON data_quality_issues (issue_type, meter_id, reading_timestamp_utc);

-- Incident lookups: newest-first listing, per-anomaly lookup, evidence per incident.
CREATE INDEX IF NOT EXISTS idx_incidents_created ON incidents (created_at);
CREATE INDEX IF NOT EXISTS idx_incidents_anomaly ON incidents (anomaly_id);
CREATE INDEX IF NOT EXISTS idx_evidence_incident ON evidence_records (incident_id);
CREATE INDEX IF NOT EXISTS idx_audit_created ON audit_events (created_at);

-- Evidence and audit rows are immutable: enforce it in the database, not just
-- in application code.
CREATE TRIGGER IF NOT EXISTS trg_evidence_no_update
BEFORE UPDATE ON evidence_records
BEGIN
    SELECT RAISE(ABORT, 'evidence_records is append-only');
END;

CREATE TRIGGER IF NOT EXISTS trg_evidence_no_delete
BEFORE DELETE ON evidence_records
BEGIN
    SELECT RAISE(ABORT, 'evidence_records is append-only');
END;

CREATE TRIGGER IF NOT EXISTS trg_audit_no_update
BEFORE UPDATE ON audit_events
BEGIN
    SELECT RAISE(ABORT, 'audit_events is append-only');
END;

CREATE TRIGGER IF NOT EXISTS trg_audit_no_delete
BEFORE DELETE ON audit_events
BEGIN
    SELECT RAISE(ABORT, 'audit_events is append-only');
END;

-- -----------------------------------------------------------------------------
-- Decision Engine persistence (immutable, append-only, auditable)
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS decisions (
    id TEXT PRIMARY KEY,
    status TEXT NOT NULL CHECK (status IN ('APPROVE', 'REJECT', 'REVIEW')),
    precedence TEXT NOT NULL,
    engine_version TEXT NOT NULL,
    config_version TEXT NOT NULL,
    input_fingerprint TEXT NOT NULL,
    config_fingerprint TEXT NOT NULL,
    request_json TEXT NOT NULL,
    result_json TEXT NOT NULL,
    decision_timestamp TEXT NOT NULL,
    created_at TEXT NOT NULL,
    supersedes TEXT,
    supersession_reason TEXT,
    FOREIGN KEY (supersedes) REFERENCES decisions(id)
);

CREATE INDEX IF NOT EXISTS idx_decisions_created
    ON decisions (created_at DESC, id);
CREATE INDEX IF NOT EXISTS idx_decisions_status
    ON decisions (status);
CREATE INDEX IF NOT EXISTS idx_decisions_config_version
    ON decisions (config_version);
CREATE INDEX IF NOT EXISTS idx_decisions_supersedes
    ON decisions (supersedes);

CREATE TRIGGER IF NOT EXISTS trg_decisions_no_update
BEFORE UPDATE ON decisions
BEGIN
    SELECT RAISE(ABORT, 'decisions is append-only');
END;

CREATE TRIGGER IF NOT EXISTS trg_decisions_no_delete
BEFORE DELETE ON decisions
BEGIN
    SELECT RAISE(ABORT, 'decisions is append-only');
END;

CREATE TABLE IF NOT EXISTS decision_configs (
    fingerprint TEXT PRIMARY KEY,
    config_version TEXT NOT NULL,
    config_json TEXT NOT NULL,
    first_seen_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_decision_configs_version
    ON decision_configs (config_version);
