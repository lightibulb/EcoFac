-- Adds Decision Engine persistence tables (append-only).
-- The canonical schema lives in src/ecofac/persistence/schema.sql; this file
-- documents the delta for deployments that are upgraded in place.

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
