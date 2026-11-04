-- Adds Scenario Intelligence persistence (append-only, separate from decisions).
CREATE TABLE IF NOT EXISTS scenarios (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    created_at TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('APPROVE', 'REJECT', 'REVIEW')),
    precedence TEXT NOT NULL,
    engine_version TEXT NOT NULL,
    config_version TEXT NOT NULL,
    input_fingerprint TEXT NOT NULL,
    config_fingerprint TEXT NOT NULL,
    input_json TEXT NOT NULL,
    config_json TEXT NOT NULL,
    result_json TEXT NOT NULL,
    decision_id TEXT,
    FOREIGN KEY (decision_id) REFERENCES decisions(id)
);

CREATE INDEX IF NOT EXISTS idx_scenarios_created
    ON scenarios (created_at DESC, id);
CREATE INDEX IF NOT EXISTS idx_scenarios_status
    ON scenarios (status);
CREATE INDEX IF NOT EXISTS idx_scenarios_config_version
    ON scenarios (config_version);

CREATE TRIGGER IF NOT EXISTS trg_scenarios_no_update
BEFORE UPDATE ON scenarios
BEGIN
    SELECT RAISE(ABORT, 'scenarios is append-only');
END;

CREATE TRIGGER IF NOT EXISTS trg_scenarios_no_delete
BEFORE DELETE ON scenarios
BEGIN
    SELECT RAISE(ABORT, 'scenarios is append-only');
END;
